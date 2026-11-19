#!/usr/bin/env bash
# Rebuild the production files in hardware/kicad/production/ from the board and schematic.
# Usage (from anywhere): hardware/tools/fab/export_production.sh
# Needs KiCad 10 (kicad-cli + pcbnew Python). Writes BOM, CPL, Gerber zip (Gerbers, drill, IPC-D-356,
# PCBWay BOM/positions) and the interactive BOM. ManufacturingInfo.txt is maintained by hand.
set -euo pipefail
TOOLS=$(cd "$(dirname "$0")" && pwd)
K=$(cd "$TOOLS/../../kicad" && pwd)
O=$(mktemp -d)
trap 'rm -rf "$O"' EXIT
mkdir -p "$O/z"
cd "$K"
P=tracker-c0.kicad_pcb; SCH=tracker-c0.kicad_sch
kicad-cli sch export bom -o "$O/bom_raw.csv" \
  --fields 'Reference,${QUANTITY},Manufacturer,Manufacturer Part Number,Value,Description,Datasheet,Footprint,Distributor,Distributor Part Number,Distributor Link' \
  --labels 'Reference,Qty,Manufacturer,Manufacturer Part Number,Value,Description,Datasheet,Footprint,Distributor,Distributor Part Number,Distributor Link' \
  --group-by 'Manufacturer Part Number,Value,Footprint' --exclude-dnp "$SCH" >/dev/null
kicad-cli pcb export pos --format csv --units mm --use-drill-file-origin --side both -o "$O/pos_all.csv" "$P" >/dev/null
kicad-cli pcb export pos --format csv --units mm --use-drill-file-origin --side both --smd-only -o "$O/pos_smd.csv" "$P" >/dev/null
kicad-cli pcb export gerbers --layers F.Cu,In1.Cu,In2.Cu,B.Cu,F.Paste,B.Paste,F.Silkscreen,B.Silkscreen,F.Mask,B.Mask,Edge.Cuts \
  --use-drill-file-origin --no-protel-ext -o "$O/z/" "$P" >/dev/null
kicad-cli pcb export drill --format excellon --drill-origin plot --excellon-separate-th --excellon-units mm -o "$O/z/" "$P" >/dev/null
kicad-cli pcb export ipcd356 -o "$O/z/PCBWay_netlist.ipc" "$P" >/dev/null
mv "$O/z/tracker-c0-F_Silkscreen.gbr" "$O/z/tracker-c0-F_SilkS.gbr"; mv "$O/z/tracker-c0-B_Silkscreen.gbr" "$O/z/tracker-c0-B_SilkS.gbr"
rm -f "$O"/z/*.gbrjob
python3 -I "$TOOLS/make_pcbway_files.py" "$O"
python3 -I -c "import zipfile,os,sys;o=sys.argv[1];z=zipfile.ZipFile(o+'/TeapotlabsTrackerC0_Gerber.zip','w',zipfile.ZIP_DEFLATED);[z.write(os.path.join(o,'z',f),f) for f in sorted(os.listdir(o+'/z'))];z.close()" "$O"
cp "$O/TeapotlabsTrackerC0_BOM.csv" "$O/TeapotlabsTrackerC0_CPL.csv" "$O/TeapotlabsTrackerC0_Gerber.zip" production/
python3 "$TOOLS/../ibom/make_ibom.py" --board "$P" --bom production/TeapotlabsTrackerC0_BOM.csv --cpl production/TeapotlabsTrackerC0_CPL.csv \
  --out production/TeapotlabsTrackerC0_InteractiveBOM.html --title "Tracker C0 Rev 4 Interactive BOM"
# kicad-cli rewrites the project's local settings file; keep the committed copy.
git checkout -- tracker-c0.kicad_prl 2>/dev/null || true
rm -f '~tracker-c0.kicad_pro.lck'
echo "production files written to $K/production"
