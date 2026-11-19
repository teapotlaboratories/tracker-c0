#!/usr/bin/env python3
"""Build the BWLC1A interactive BOM: one self-contained HTML file.

Reads the board with KiCad's pcbnew module (KiCad 10) and the production BOM and CPL CSVs, checks that
they agree, and writes template.html with the data embedded.

Usage (from the repo root, with KiCad's Python):
    python3 hardware/tools/ibom/make_ibom.py \
        --board hardware/kicad/bwlc1a.kicad_pcb \
        --bom hardware/kicad/production/TeapotlabsBWLC1A_BOM.csv \
        --cpl hardware/kicad/production/TeapotlabsBWLC1A_CPL.csv \
        --out hardware/kicad/production/TeapotlabsBWLC1A_InteractiveBOM.html

--fragment writes the page without the <!doctype>/<html>/<head>/<body> wrapper (for hosts that add it).
"""
import argparse
import csv
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
MAX_ERR = pcbnew.FromMM(0.005)


def mm(v):
    return round(v / 1e6, 3)


def nat(ref):
    m = re.match(r'([A-Za-z#]+)(\d+)', ref)
    return (m.group(1), int(m.group(2))) if m else (ref, 0)


def poly_path(ps, nd=3):
    """SHAPE_POLY_SET -> SVG path data (outlines and holes; draw with fill-rule evenodd)."""
    out = []
    for i in range(ps.OutlineCount()):
        chains = [ps.Outline(i)] + [ps.Hole(i, j) for j in range(ps.HoleCount(i))]
        for ch in chains:
            n = ch.PointCount()
            if n < 2:
                continue
            pts = [ch.CPoint(k) for k in range(n)]
            out.append('M' + ' '.join(f'{p.x / 1e6:.{nd}f} {p.y / 1e6:.{nd}f}' for p in pts) + 'Z')
    return ''.join(out)


def shape_path(item, layer):
    ps = pcbnew.SHAPE_POLY_SET()
    item.TransformShapeToPolygon(ps, layer, 0, MAX_ERR, pcbnew.ERROR_INSIDE)
    ps.Simplify()
    return poly_path(ps)


def silk(board, layer):
    """Silkscreen as KiCad's own SVG plotter draws it (stroke text, knockout text, dashed lines), as SVG markup in
    board millimetres. Colours are replaced by currentColor so the page can theme them."""
    with tempfile.TemporaryDirectory() as tmp:
        pc = pcbnew.PLOT_CONTROLLER(board)
        po = pc.GetPlotOptions()
        po.SetOutputDirectory(tmp)
        po.SetPlotFrameRef(False)
        po.SetMirror(False)
        po.SetUseAuxOrigin(False)
        po.SetSubtractMaskFromSilk(False)
        pc.SetLayer(layer)
        pc.OpenPlotfile('silk', pcbnew.PLOT_FORMAT_SVG, 'silk')
        pc.PlotLayer()
        pc.ClosePlot()
        path = [os.path.join(tmp, f) for f in os.listdir(tmp) if f.endswith('.svg')][0]
        svg = open(path, encoding='utf-8').read()
    body = svg[svg.index('</desc>') + len('</desc>'):svg.rindex('</svg>')]
    body = re.sub(r'#[0-9A-Fa-f]{6}', 'currentColor', body)
    body = re.sub(r'(\d+\.\d{3})\d+', r'\1', body)          # 0.001 mm is plenty
    body = re.sub(r'\s*\n\s*', ' ', body).replace('> <', '><').strip()
    return body


def zones(board, layer):
    """Filled copper pours on one layer, merged (as KiCad plots them)."""
    ps = pcbnew.SHAPE_POLY_SET()
    for z in board.Zones():
        if z.GetIsRuleArea() or not z.IsOnLayer(layer):
            continue
        fill = z.GetFilledPolysList(layer)
        if fill:
            ps.BooleanAdd(fill)
    ps.Simplify()
    return poly_path(ps, 2)


def tracks(board, layer):
    segs, arcs, vias = [], [], []
    for t in board.GetTracks():
        if isinstance(t, pcbnew.PCB_VIA):
            if t.IsOnLayer(layer):
                p = t.GetPosition()
                vias.append([mm(p.x), mm(p.y), mm(t.GetWidth(layer)), mm(t.GetDrillValue())])
        elif t.GetLayer() == layer:
            if isinstance(t, pcbnew.PCB_ARC):
                s, m, e = t.GetStart(), t.GetMid(), t.GetEnd()
                arcs.append([mm(s.x), mm(s.y), mm(m.x), mm(m.y), mm(e.x), mm(e.y), mm(t.GetWidth())])
            else:
                s, e = t.GetStart(), t.GetEnd()
                segs.append([mm(s.x), mm(s.y), mm(e.x), mm(e.y), mm(t.GetWidth())])
    return {'segs': segs, 'arcs': arcs, 'vias': vias}


def footprint(fp, aux):
    side = 'B' if fp.GetLayer() == pcbnew.B_Cu else 'F'
    cu = pcbnew.B_Cu if side == 'B' else pcbnew.F_Cu
    pads, pin1, holes = [], [], []
    for pad in fp.Pads():
        if not (pad.IsOnLayer(cu) or pad.IsOnLayer(pcbnew.F_Cu) or pad.IsOnLayer(pcbnew.B_Cu)):
            continue
        for L, key in ((pcbnew.F_Cu, 'F'), (pcbnew.B_Cu, 'B')):
            if pad.IsOnLayer(L):
                d = shape_path(pad, L)
                if d:
                    pads.append([key, d])
                    if pad.GetNumber() in ('1', 'A1'):
                        pin1.append([key, d])
        if pad.HasHole():
            ps = pcbnew.SHAPE_POLY_SET()
            pad.TransformHoleToPolygon(ps, 0, MAX_ERR, pcbnew.ERROR_INSIDE)
            holes.append(poly_path(ps))
    crt = fp.GetCourtyard(pcbnew.B_CrtYd if side == 'B' else pcbnew.F_CrtYd)
    outline = poly_path(crt) if crt.OutlineCount() else ''
    bb = fp.GetBoundingBox(False)
    if not outline:
        x0, y0, x1, y1 = mm(bb.GetLeft()), mm(bb.GetTop()), mm(bb.GetRight()), mm(bb.GetBottom())
        outline = f'M{x0} {y0} {x1} {y0} {x1} {y1} {x0} {y1}Z'
    pos = fp.GetPosition()
    attr = fp.GetAttributes()
    return {
        'ref': fp.GetReference(),
        'val': fp.GetValue(),
        'fp': str(fp.GetFPID().GetLibItemName()),
        'side': side,
        'x': round((pos.x - aux.x) / 1e6, 4),           # CPL frame: drill/place origin, Y up
        'y': round((aux.y - pos.y) / 1e6, 4),
        'rot': round(fp.GetOrientationDegrees() % 360, 1),
        'cx': mm(bb.GetCenter().x), 'cy': mm(bb.GetCenter().y),
        'smd': bool(attr & pcbnew.FP_SMD), 'tht': bool(attr & pcbnew.FP_THROUGH_HOLE),
        'nobom': fp.IsExcludedFromBOM(), 'dnp': fp.IsDNP() or fp.GetValue().strip().upper() == 'DNP',
        'outline': outline, 'pads': pads, 'pin1': pin1, 'holes': holes,
    }


def git_rev(path):
    try:
        rev = subprocess.run(['git', '-C', path, 'rev-parse', '--short', 'HEAD'], capture_output=True,
                             text=True, check=True).stdout.strip()
        dirty = subprocess.run(['git', '-C', path, 'status', '--porcelain', '--', '.'], capture_output=True,
                               text=True, check=True).stdout.strip()
        return rev + (' + uncommitted changes' if dirty else '')
    except Exception:
        return ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--board', required=True)
    ap.add_argument('--bom', required=True)
    ap.add_argument('--cpl', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--title', default='BWLC1A Rev 4 Interactive BOM')
    ap.add_argument('--fragment', action='store_true')
    a = ap.parse_args()

    board = pcbnew.LoadBoard(a.board)
    aux = board.GetDesignSettings().GetAuxOrigin()
    edge = pcbnew.SHAPE_POLY_SET()
    board.GetBoardPolygonOutlines(edge, True)
    bb = board.GetBoardEdgesBoundingBox()

    fps = {}
    for fp in board.GetFootprints():
        ref = fp.GetReference()
        if not ref or '*' in ref:
            ref = f'{ref or "?"}@{mm(fp.GetPosition().x)},{mm(fp.GetPosition().y)}'
        fps[ref] = footprint(fp, aux)
        fps[ref]['ref'] = ref

    lines = []
    for r in csv.DictReader(open(a.bom, encoding='utf-8')):
        refs = sorted([x.strip() for x in r['Reference'].split(',') if x.strip()], key=nat)
        lines.append({
            'id': int(r['Id']), 'refs': refs, 'qty': int(r['Qty']), 'val': r['Value'], 'mfr': r['Manufacturer'],
            'mpn': r['Manufacturer Part Number'], 'desc': r['Description'], 'ds': r['Datasheet'],
            'fp': r['Footprint'], 'dist': r['Distributor'], 'dpn': r['Distributor Part Number'],
            'link': r['Distributor Link'],
        })

    # Cross-checks: BOM <-> board <-> CPL.
    errors = []
    bomrefs = {}
    for ln in lines:
        if ln['qty'] != len(ln['refs']):
            errors.append(f"line {ln['id']}: Qty {ln['qty']} but {len(ln['refs'])} refs")
        for ref in ln['refs']:
            if ref in bomrefs:
                errors.append(f'{ref} on two BOM lines')
            bomrefs[ref] = ln['id']
            if ref not in fps:
                errors.append(f'{ref} in the BOM but not on the board')
    cpl = {r['Designator']: r for r in csv.DictReader(open(a.cpl, encoding='utf-8'))}
    for ref in bomrefs:
        c, f = cpl.get(ref), fps.get(ref)
        if not c:
            errors.append(f'{ref} in the BOM but not in the CPL')
            continue
        if not f:
            continue
        side = 'B' if c['Layer'] == 'bottom' else 'F'
        if side != f['side']:
            errors.append(f"{ref}: CPL side {c['Layer']}, board side {f['side']}")
        if abs(float(c['Mid X']) - f['x']) > 0.01 or abs(float(c['Mid Y']) - f['y']) > 0.01:
            errors.append(f"{ref}: CPL ({c['Mid X']}, {c['Mid Y']}) vs board ({f['x']}, {f['y']})")
        if abs((float(c['Rotation']) - f['rot'] + 180) % 360 - 180) > 0.1:
            errors.append(f"{ref}: CPL rotation {c['Rotation']} vs board {f['rot']}")
    for ref in cpl:
        if ref not in bomrefs:
            errors.append(f'{ref} in the CPL but not in the BOM')
    for ref, f in fps.items():
        f['line'] = bomrefs.get(ref)
        if f['line'] is None and not (f['nobom'] or f['dnp']) and not ref.startswith(('TP', 'G', 'H', 'NT', '#')):
            print(f'note: {ref} ({f["val"]}) is on the board but not in the BOM', file=sys.stderr)
    if errors:
        print('BOM/board/CPL mismatch:', *errors, sep='\n  ', file=sys.stderr)
        sys.exit(1)

    data = {
        'title': a.title,
        'generated': datetime.date.today().isoformat(),
        'source': {
            'board': os.path.basename(a.board), 'bom': os.path.basename(a.bom), 'cpl': os.path.basename(a.cpl),
            'rev': git_rev(os.path.dirname(os.path.abspath(a.board))),
        },
        'bbox': [mm(bb.GetLeft()), mm(bb.GetTop()), mm(bb.GetWidth()), mm(bb.GetHeight())],
        'edge': poly_path(edge),
        'silk': {'F': silk(board, pcbnew.F_SilkS), 'B': silk(board, pcbnew.B_SilkS)},
        'tracks': {'F': tracks(board, pcbnew.F_Cu), 'B': tracks(board, pcbnew.B_Cu)},
        'zones': {'F': zones(board, pcbnew.F_Cu), 'B': zones(board, pcbnew.B_Cu)},
        'lines': lines,
        'fps': sorted(fps.values(), key=lambda f: nat(f['ref'])),
    }

    tpl = open(os.path.join(HERE, 'template.html'), encoding='utf-8').read()
    blob = json.dumps(data, separators=(',', ':'), ensure_ascii=False).replace('</', '<\\/')
    page = tpl.replace('/*IBOM_DATA*/null', blob).replace('{{TITLE}}', a.title)
    if a.fragment:
        page = page.split('<!--FRAG-START-->', 1)[1].split('<!--FRAG-END-->', 1)[0]
        page = re.sub(r'(?s)<!--CUT-->.*?<!--/CUT-->', '', page).strip() + '\n'
    else:
        for marker in ('<!--FRAG-START-->', '<!--FRAG-END-->', '<!--CUT-->', '<!--/CUT-->'):
            page = page.replace(marker, '')
    with open(a.out, 'w', encoding='utf-8') as f:
        f.write(page)
    nparts = sum(len(l['refs']) for l in lines)
    print(f'{a.out}: {len(lines)} lines, {nparts} parts, {len(fps)} footprints, {len(page) // 1024} KiB')


if __name__ == '__main__':
    main()
