#!/usr/bin/env python3
"""Cross-check the production files against the board and schematic before ordering.

Usage: python3 hardware/tools/fab/check_production.py
Needs KiCad 10 (pcbnew Python + kicad-cli). Checks, and exits non-zero on any issue:
  - every fitted part (schematic: MPN set, Value != DNP, not DNP/excluded) is in the BOM exactly once, and
    nothing else is; Qty matches the reference count; no empty Manufacturer / MPN / Value / Footprint /
    Distributor fields; every Distributor Link is that distributor's product page for the part (no search links);
  - BOM MPN, value and footprint agree with the schematic and the board;
  - the CPL has one row per BOM part, its position/rotation/side match the board in the drill/place-file
    origin frame (the Gerber frame), and every point lies inside the board outline;
  - PCBWay_bom.csv and PCBWay_positions.csv inside the Gerber zip agree with the BOM and CPL;
  - the zip contains only fabrication layers.
Prints, for information, parts whose footprint origin is > 0.1 mm from the centre of their pads (the CPL
uses the footprint origin; for J3 and U3 that is the body centre, by design).
"""
import collections, csv, math, os, re, subprocess, sys, tempfile, zipfile
import pcbnew

KICAD = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'kicad'))
PROD = os.path.join(KICAD, 'production', 'TeapotlabsTrackerC0')
FAB_LAYERS = {'F_Cu', 'In1_Cu', 'In2_Cu', 'B_Cu', 'F_Paste', 'B_Paste', 'F_SilkS', 'B_SilkS', 'F_Mask', 'B_Mask', 'Edge_Cuts'}
# Distributor Link must be a product page (one part), per distributor; anything search-like fails
PRODUCT_PAGE = {
    'lcsc': re.compile(r'^https://(www\.)?lcsc\.com/product-detail/[^?#]+$'),
    'mouser': re.compile(r'^https://(www\.)?mouser\.com/ProductDetail/[^/]+/[^/?#]+'),
    'digikey': re.compile(r'^https://(www\.)?digikey\.com/en/products/detail/[^/]+/[^/]+/\d+$'),
}
SEARCH_LINK = re.compile(r'(?i)/search|/result|[?&](q|k|keyword|keywords)=|/products/filter/|/c/')


def main():
    tmp = tempfile.mkdtemp()
    net = os.path.join(tmp, 'net.xml')
    subprocess.run(['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml', '-o', net,
                    os.path.join(KICAD, 'tracker-c0.kicad_sch')], check=True, capture_output=True)
    subprocess.run(['git', '-C', KICAD, 'checkout', '--', 'tracker-c0.kicad_prl'], capture_output=True)
    zf = zipfile.ZipFile(PROD + '_Gerber.zip')
    zf.extractall(os.path.join(tmp, 'zip'))
    Z = os.path.join(tmp, 'zip')
    issues = collections.defaultdict(list)
    info = []

    def I(cat, msg):
        issues[cat].append(msg)

    # zip contents
    for n in zf.namelist():
        m = re.fullmatch(r'tracker-c0-(.+)\.gbr', n)
        if m and m.group(1) not in FAB_LAYERS:
            I('Zip', 'non-fabrication layer in the zip: ' + n)
    info.append('zip: ' + ', '.join(sorted(zf.namelist())))

    b = pcbnew.LoadBoard(os.path.join(KICAD, 'tracker-c0.kicad_pcb'))
    ax = b.GetDesignSettings().GetAuxOrigin()
    mm = lambda v: v / 1e6
    s = open(net).read()
    sch = {}
    for m in re.finditer(r'<comp ref="([^"]+)">(.*?)</comp>', s, re.S):
        body = m.group(2)
        f = dict(re.findall(r'<field name="([^"]+)">([^<]*)</field>', body))
        f['Value'] = (re.search(r'<value>([^<]*)</value>', body) or [None, ''])[1]
        f['dnp'] = bool(re.search(r'<property name="dnp"', body))
        f['exclude_from_bom'] = bool(re.search(r'<property name="exclude_from_bom"', body))
        sch[m.group(1)] = f
    unesc = lambda t: t.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"')

    bom = list(csv.DictReader(open(PROD + '_BOM.csv', encoding='utf-8-sig')))
    cpl = list(csv.DictReader(open(PROD + '_CPL.csv', encoding='utf-8-sig')))
    pwb = list(csv.DictReader(open(os.path.join(Z, 'PCBWay_bom.csv'), encoding='utf-8-sig')))
    pwp = list(csv.DictReader(open(os.path.join(Z, 'PCBWay_positions.csv'), encoding='utf-8-sig')))
    fps = {f.GetReference(): f for f in b.GetFootprints() if not f.GetReference().endswith('*')}

    def fitted(ref):
        f = sch.get(ref, {})
        return bool(f) and f.get('Value', '') != 'DNP' and not f['dnp'] and not f['exclude_from_bom'] \
            and f.get('Manufacturer Part Number', '').strip() not in ('', '-')

    need = {r for r in fps if fitted(r)}
    bomref = {}
    for row in bom:
        refs = [x.strip() for x in row['Reference'].split(',')]
        if int(row['Qty']) != len(refs):
            I('BOM', 'line %s: Qty %s but %d references' % (row['Id'], row['Qty'], len(refs)))
        for x in refs:
            if x in bomref:
                I('BOM', '%s is on two lines' % x)
            bomref[x] = row
        for c in ('Manufacturer', 'Manufacturer Part Number', 'Value', 'Footprint', 'Distributor', 'Distributor Part Number'):
            if not row[c].strip():
                I('BOM', 'line %s (%s): empty %s' % (row['Id'], row['Reference'], c))
        # rule (owner, 2026-11-22): every Distributor Link is the part's product page, never a search or category page
        link, dist = row.get('Distributor Link', '').strip(), row['Distributor'].strip().lower().replace('-', '')
        page = PRODUCT_PAGE.get(dist)
        if not link:
            I('BOM links', 'line %s (%s): empty Distributor Link' % (row['Id'], row['Reference']))
        elif SEARCH_LINK.search(link):
            I('BOM links', 'line %s (%s): search link, not a product page: %s' % (row['Id'], row['Reference'], link))
        elif page is None:
            I('BOM links', 'line %s (%s): no product-page pattern for distributor %r' % (row['Id'], row['Reference'], row['Distributor']))
        elif not page.search(link):
            I('BOM links', 'line %s (%s): not a %s product page: %s' % (row['Id'], row['Reference'], row['Distributor'], link))
    info.append('board footprints %d, fitted %d, BOM lines %d / parts %d, CPL rows %d'
                % (len(fps), len(need), len(bom), len(bomref), len(cpl)))
    for x in sorted(need - set(bomref)):
        I('BOM', 'fitted part %s is missing from the BOM' % x)
    for x in sorted(set(bomref) - need):
        I('BOM', '%s is in the BOM but is not a fitted board part' % x)
    for x, row in bomref.items():
        fp = fps.get(x)
        if not fp:
            I('BOM', '%s is not on the board' % x)
            continue
        if row['Manufacturer Part Number'].strip() != unesc(sch.get(x, {}).get('Manufacturer Part Number', '')).strip():
            I('BOM', '%s: MPN differs between BOM and schematic' % x)
        if fp.HasField('Manufacturer Part Number') and fp.GetField('Manufacturer Part Number').GetText().strip() != row['Manufacturer Part Number'].strip():
            I('BOM', '%s: MPN differs between BOM and board' % x)
        if row['Value'].strip() != fp.GetValue().strip():
            I('BOM', '%s: value BOM %r vs board %r' % (x, row['Value'], fp.GetValue()))
        if row['Footprint'].split(':')[-1] != str(fp.GetFPID().GetLibItemName()):
            I('BOM', '%s: footprint BOM %r vs board %r' % (x, row['Footprint'], str(fp.GetFPID().GetLibItemName())))
        if fp.IsDNP() or fp.IsExcludedFromBOM() or fp.IsExcludedFromPosFiles():
            I('BOM', '%s is fitted but the board marks it DNP / excluded' % x)

    ec = [d for d in b.GetDrawings() if d.GetLayer() == pcbnew.Edge_Cuts]
    bb = ec[0].GetBoundingBox()
    for d in ec[1:]:
        bb.Merge(d.GetBoundingBox())
    w = mm(b.GetDesignSettings().GetLineThickness(pcbnew.Edge_Cuts))
    X0 = mm(bb.GetLeft() - ax.x) + w / 2; X1 = mm(bb.GetRight() - ax.x) - w / 2
    Y0 = -mm(bb.GetBottom() - ax.y) + w / 2; Y1 = -mm(bb.GetTop() - ax.y) - w / 2
    info.append('board outline %.2f x %.2f mm (centreline)' % (X1 - X0, Y1 - Y0))

    cplref = {}
    for r in cpl:
        if r['Designator'] in cplref:
            I('CPL', '%s appears twice' % r['Designator'])
        cplref[r['Designator']] = r
    for x in sorted(set(bomref) ^ set(cplref)):
        I('CPL', '%s is only in the %s' % (x, 'BOM' if x in bomref else 'CPL'))
    for x, r in cplref.items():
        fp = fps.get(x)
        if not fp:
            continue
        ex = mm(fp.GetPosition().x - ax.x); ey = -mm(fp.GetPosition().y - ax.y)
        if abs(float(r['Mid X']) - ex) > 0.001 or abs(float(r['Mid Y']) - ey) > 0.001:
            I('CPL', '%s position %s,%s vs board %.4f,%.4f' % (x, r['Mid X'], r['Mid Y'], ex, ey))
        dr = (float(r['Rotation']) - fp.GetOrientationDegrees()) % 360
        if min(dr, 360 - dr) > 0.05:
            I('CPL', '%s rotation %s vs board %.1f' % (x, r['Rotation'], fp.GetOrientationDegrees()))
        side = 'top' if fp.GetLayer() == pcbnew.F_Cu else 'bottom'
        if r['Layer'] != side:
            I('CPL', '%s layer %s vs board %s' % (x, r['Layer'], side))
        if not (X0 <= float(r['Mid X']) <= X1 and Y0 <= float(r['Mid Y']) <= Y1):
            I('CPL', '%s lies outside the board outline' % x)
        pads = [p for p in fp.Pads() if p.IsOnCopperLayer()]
        if pads:
            pb = pads[0].GetBoundingBox()
            for p in pads[1:]:
                pb.Merge(p.GetBoundingBox())
            c = pb.GetCenter()
            d = math.hypot(mm(c.x - fp.GetPosition().x), mm(c.y - fp.GetPosition().y))
            if d > 0.1:
                info.append('origin %.2f mm from pad centre: %s (%s)' % (d, x, fp.GetFPID().GetLibItemName()))

    pwref = {}
    for r in pwb:
        refs = [t.strip() for t in r['designator'].split(',')]
        if int(r['quantity']) != len(refs):
            I('PCBWay BOM', '%s: quantity mismatch' % r['designator'])
        for t in refs:
            pwref[t] = r
            if t in bomref and r['mpn'] != bomref[t]['Manufacturer Part Number']:
                I('PCBWay BOM', '%s: mpn %s vs BOM %s' % (t, r['mpn'], bomref[t]['Manufacturer Part Number']))
    for t in sorted(set(pwref) ^ set(bomref)):
        I('PCBWay BOM', '%s is only in the %s' % (t, 'PCBWay BOM' if t in pwref else 'BOM'))
    for r in pwp:
        t = r['designator']
        if r['place'] == 'True':
            c = cplref.get(t)
            if not c:
                I('PCBWay positions', '%s is placed but not in the CPL' % t)
                continue
            if abs(float(r['pos_x']) - float(c['Mid X'])) > 1e-3 or abs(float(r['pos_y']) - float(c['Mid Y'])) > 1e-3:
                I('PCBWay positions', '%s position differs from the CPL' % t)
            dr = (float(r['rotation']) - float(c['Rotation'])) % 360
            if min(dr, 360 - dr) > 0.05:
                I('PCBWay positions', '%s rotation differs from the CPL' % t)
        elif t in bomref:
            I('PCBWay positions', '%s is in the BOM but marked place=False' % t)

    for line in info:
        print('INFO', line)
    for cat, msgs in issues.items():
        print('==', cat, len(msgs))
        for m in msgs:
            print('  ', m)
    print('RESULT:', 'PASS' if not issues else 'FAIL (%d issues)' % sum(len(v) for v in issues.values()))
    return 1 if issues else 0


if __name__ == '__main__':
    sys.exit(main())
