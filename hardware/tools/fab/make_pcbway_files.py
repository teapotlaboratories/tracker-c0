"""Turn the raw kicad-cli BOM and position exports into the production files.

Called by export_production.sh with its work directory. Writes TeapotlabsTrackerC0_BOM.csv and _CPL.csv (fitted
parts only: a non-empty Manufacturer Part Number and Value != "DNP"), and z/PCBWay_bom.csv and
z/PCBWay_positions.csv for the Gerber zip. CPL coordinates are the footprint origins in the drill/place-file
origin frame, the same frame as the Gerbers.
"""
import csv,re,sys
O=sys.argv[1]
def nat(r): m=re.match(r'([A-Za-z#]+)(\d+)',r); return (m.group(1),int(m.group(2))) if m else (r,0)
rows=list(csv.DictReader(open(f'{O}/bom_raw.csv',encoding='utf-8')))
keep=[]
for r in rows:
    raw=[]
    for tok in r['Reference'].split(','):
        m=re.fullmatch(r'([A-Za-z]+)(\d+)-[A-Za-z]*(\d+)',tok.strip())
        if m: raw+= [f"{m.group(1)}{i}" for i in range(int(m.group(2)),int(m.group(3))+1)]
        else: raw.append(tok.strip())
    refs=sorted(raw,key=nat); r['Reference']=','.join(refs)
    mpn=r['Manufacturer Part Number'].strip()
    if not mpn or mpn=='-' or r['Value'].strip()=='DNP': continue
    keep.append(r)
keep.sort(key=lambda r: nat(r['Reference'].split(',')[0]))
cols=['Id','Reference','Qty','Manufacturer','Manufacturer Part Number','Value','Description','Datasheet','Footprint','Distributor','Distributor Part Number','Distributor Link']
with open(f'{O}/TeapotlabsTrackerC0_BOM.csv','w',newline='',encoding='utf-8') as f:
    w=csv.writer(f); w.writerow(cols)
    for i,r in enumerate(keep,1): w.writerow([i]+[r[c].strip() for c in cols[1:]])
bomrefs={x:r for r in keep for x in r['Reference'].split(',')}
smd={r['Ref'] for r in csv.DictReader(open(f'{O}/pos_smd.csv'))}
cpl=[];pw=[]
for r in csv.DictReader(open(f'{O}/pos_all.csv')):
    ref=r['Ref']
    if '*' in ref: continue
    x=round(float(r['PosX']),4); y=round(float(r['PosY']),4); rot=float(r['Rot'])%360
    side='bottom' if r['Side'].lower().startswith('b') else 'top'
    placed=ref in bomrefs
    mpn=bomrefs[ref]['Manufacturer Part Number'].strip() if placed else ''
    pr=rot if rot<=180 else rot-360
    pw.append((x,y,round(pr,1),side,ref,mpn,r['Package'],r['Val'],'smt' if ref in smd else 'tht',placed))
    if placed: cpl.append((ref,x,y,round(rot,1),side))
cpl.sort(key=lambda r: nat(r[0])); pw.sort(key=lambda r: nat(r[4]))
missing=set(bomrefs)-{r[0] for r in cpl}
assert not missing, missing
with open(f'{O}/TeapotlabsTrackerC0_CPL.csv','w',newline='',encoding='utf-8') as f:
    w=csv.writer(f); w.writerow(['Designator','Mid X','Mid Y','Rotation','Layer']); w.writerows(cpl)
with open(f'{O}/z/PCBWay_positions.csv','w',newline='',encoding='utf-8') as f:
    w=csv.writer(f); w.writerow(['pos_x','pos_y','rotation','side','designator','mpn','pack','value','mount_type','place']); w.writerows(pw)
grp={}
for x,y,rot,side,ref,mpn,pack,val,mt,placed in pw:
    if placed: grp.setdefault((val,pack,mpn,mt),[]).append(ref)
with open(f'{O}/z/PCBWay_bom.csv','w',newline='',encoding='utf-8') as f:
    w=csv.writer(f); w.writerow(['designator','quantity','value','pack','mpn','mount_type'])
    for (val,pack,mpn,mt),refs in sorted(grp.items(),key=lambda kv: nat(sorted(kv[1],key=nat)[0])):
        refs=sorted(refs,key=nat); w.writerow([', '.join(refs),len(refs),val,pack,mpn,mt])
print('BOM',len(keep),'parts',len(bomrefs),'CPL',len(cpl),'positions',len(pw),'pcbway bom',len(grp))
