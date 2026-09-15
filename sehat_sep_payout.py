# -*- coding: utf-8 -*-
"""Sehat MG Payout — Month 2 (16 Aug -> 15 Sep 2026). Single idempotent builder:
rebuilds the "Sehat MG Payout (16Aug-15Sep)" tab, EXCLUDES confirmed-FPV CSPs
(RESOLVED_CONFIRMED in FPV_INVESTIGATION), and applies formatting. Cloud-ready
(GOOGLE_SA_JSON env). Window/dates are FIXED — refreshing only re-pulls the
as-of-15-Sep metrics (which still drift as the SLA ledger settles) + the FPV set
(which grows as investigations resolve).
"""
import os, sys, json, tempfile, urllib.request, datetime
sys.stdout.reconfigure(encoding="utf-8")
import gspread
from google.oauth2 import service_account
MB_KEY=os.environ.get("METABASE_KEY","mb_1dsbxsJfyROPsVyNpifJ8hTTlIDG85+qNKRo91KDnb4=")
SCOPES=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
SHEET="1XHqjybQYKyfCpgdraPiL32GBm2R2wf-CguxlHalmu8M"; AUG_GID=1356800065
NEW_TAB="Sehat MG Payout (16Aug-15Sep)"; REMOVE_TAB="Remove from MG (15-Sep)"
ASOF="2026-09-15"; ASOF_NEXT="2026-09-16"
START=datetime.date(2026,8,16); END=datetime.date(2026,9,15); DAYS=(END-START).days; DAILY=10000/DAYS
def mb(sql):
    body=json.dumps({"database":113,"type":"native","native":{"query":sql}}).encode()
    d=json.loads(urllib.request.urlopen(urllib.request.Request("https://metabase.wiom.in/api/dataset",data=body,headers={"x-api-key":MB_KEY,"Content-Type":"application/json"}),timeout=180).read().decode())
    if isinstance(d,dict) and d.get("error"): raise SystemExit("ERR "+str(d["error"])[:300])
    return d["data"]["rows"]
def creds():
    raw=os.environ.get("GOOGLE_SA_JSON","")
    if raw:
        t=tempfile.NamedTemporaryFile("w",suffix=".json",delete=False); t.write(raw); t.close()
        return service_account.Credentials.from_service_account_file(t.name,scopes=SCOPES)
    for p in (r"C:\Users\Palak Vardhan\Desktop\mbg\mbg-cron\wiom-sheets-writer.json",
              r"C:\Users\Palak Vardhan\dashboard\wiom-sheets-writer.json"):
        if os.path.exists(p): return service_account.Credentials.from_service_account_file(p,scopes=SCOPES)
    raise SystemExit("no SA creds")
def pdate(s):
    for f in ("%d-%b-%Y","%d-%b-%y","%d-%b"):
        try:
            d=datetime.datetime.strptime((s or "").strip(),f).date()
            return d.replace(year=2026) if d.year==1900 else d
        except Exception: pass
    return None

gc=gspread.authorize(creds()); ss=gc.open_by_key(SHEET)
aug=next(x for x in ss.worksheets() if x.id==AUG_GID); v=aug.get_all_values()
coh=[]
for r in v[2:]:
    if len(r)>11 and r[1].strip().startswith("a0"):
        coh.append({"name":r[0],"cspid":r[1].strip(),"pid":r[2],"base":r[3],"phone":r[4],
                    "track":r[5].strip(),"grade":r[6],"aug_pct":r[8],"enrol":r[11].strip()})
opt=[c["cspid"] for c in coh if "Optical" in c["track"]]; sla=[c["cspid"] for c in coh if "SLA" in c["track"]]
ilo=",".join("'%s'"%c for c in opt); ils=",".join("'%s'"%c for c in sla); ila=",".join("'%s'"%c["cspid"] for c in coh)

optcur={r[0]:r[1] for r in mb(f"SELECT CSP_ID,ROUND(T1_OOR_RATE,1) FROM PROD_DB.CSP_QUALITY_SERVICE_CSP_QUALITY_SERVICE.DAILY_METRIC_SNAPSHOTS WHERE SNAPSHOT_DATE::date='{ASOF}' AND CSP_ID IN ({ilo})")}
active={r[0]:r[1] for r in mb(f"SELECT CSP_ID,ACTIVE_CONNECTION_COUNT FROM PROD_DB.CSP_QUALITY_SERVICE_CSP_QUALITY_SERVICE.DAILY_METRIC_SNAPSHOTS WHERE SNAPSHOT_DATE::date='{ASOF}' AND CSP_ID IN ({ila})")}
slacur={r[0]:r[1] for r in mb(f"""SELECT CSP_ID, ROUND(100.0*SUM(IFF(RESOLVED_WITHIN_TAT,1,0))/NULLIF(COUNT(*),0),1)
FROM PROD_DB.CSP_QUALITY_SERVICE_CSP_QUALITY_SERVICE.COMPLAINT_RESOLUTION_LEDGER
WHERE _FIVETRAN_ACTIVE=TRUE AND COALESCE(EXCLUDED_FROM_SCORING,FALSE)=FALSE AND CSP_ID IN ({ils})
  AND OPENED_AT >= DATEADD(day,-60, CONVERT_TIMEZONE('Asia/Kolkata','UTC',TIMESTAMP '{ASOF_NEXT} 00:00:00'))
  AND OPENED_AT <  CONVERT_TIMEZONE('Asia/Kolkata','UTC',TIMESTAMP '{ASOF_NEXT} 00:00:00') GROUP BY 1""")}
# FPV confirmed + reasons
fpv={r[0] for r in mb(f"SELECT DISTINCT CSP_ID FROM PROD_DB.CSP_ENFORCEMENT_SERVICE_CSP_ENFORCEMENT_SERVICE.FPV_INVESTIGATION WHERE STATUS='RESOLVED_CONFIRMED' AND SUPERSEDED_BY IS NULL AND _FIVETRAN_ACTIVE AND CSP_ID IN ({ila})")}
reason={}
try:
    rv=ss.worksheet(REMOVE_TAB).get_all_values()
    hi=next(i for i,row in enumerate(rv) if "CSP ID" in row and any("Violation" in (c or "") for c in row))
    HH=rv[hi]; idc=HH.index("CSP ID"); ivc=next(j for j,c in enumerate(HH) if "Violation" in (c or ""))
    for row in rv[hi+1:]:
        if len(row)>max(idc,ivc) and row[idc].strip() in fpv: reason[row[idc].strip()]=row[ivc]
except Exception as e: print("remove-tab reasons skip:",e)
print(f"optical {len(optcur)}/{len(opt)} | SLA {len(slacur)}/{len(sla)} | FPV in cohort {len(fpv)}")

out=[]; met_metric=0; paid=0; total=0; fpv_rows=[]
for c in coh:
    cid=c["cspid"]
    cur = optcur.get(cid) if "Optical" in c["track"] else slacur.get(cid)
    curv = "" if cur is None else cur
    metb = (cur is not None and float(cur)>=80)
    if metb: met_metric+=1
    enrol=pdate(c["enrol"]); eff=max(enrol,START) if enrol else START
    elig=max(0,min(DAYS,(END-eff).days)); pro=round(elig*DAILY)
    is_fpv = cid in fpv
    if is_fpv:
        status="FPV — removed"; pay=0; note=f"FPV RESOLVED_CONFIRMED · {reason.get(cid,'')}"; fpv_rows.append(len(out)+3)
    else:
        status=("Yes ✅" if metb else "No"); pay=(pro if metb else 0); note=""
        if pay>0: paid+=1
    total+=pay
    out.append([c["name"],cid,c["pid"],active.get(cid,c["base"]),c["phone"],c["track"],c["grade"],
                c["aug_pct"], curv, "≥80%", status, c["enrol"], elig, pro, 10000, pay, note])
banner=(f"Sehat MG PAYOUT — 16 Aug → 15 Sep 2026 (Month 2 of ₹20k/2mo · ₹10,000 if track ≥80% as of 15-Sep) · "
        f"Optical = T1_OOR_RATE 15-Sep snapshot (HIGH=good) · Service SLA = M3 TAT RECOMPUTED from COMPLAINT_RESOLUTION_LEDGER "
        f"(trailing 60d ending 15-Sep; M3 snapshot still NULL in Sep) · {len(fpv)} FPV cases (RESOLVED_CONFIRMED) REMOVED → ₹0 · "
        f"{paid} paid · total Rs{total:,} · auto-refreshed nightly 00:00 IST · {datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5,minutes=30))):%d-%b %H:%M IST}")
hdr=["CSP Name","CSP ID","Partner ID","Active base","Phone","Track","Graded metric","As of 16-Aug %","Current % (15-Sep)","Target","Target met?","Enrolment date","Eligible days (/30)","Pro-rata (Rs)","Guarantee (Rs)","Payout (Rs)","Removal note"]
try: nw=ss.worksheet(NEW_TAB); nw.clear()
except gspread.WorksheetNotFound: nw=ss.add_worksheet(NEW_TAB, rows=len(out)+10, cols=20)
nw.update(values=[[banner]], range_name="A1", value_input_option="RAW")
nw.update(values=[hdr], range_name="A2:Q2", value_input_option="RAW")
nw.update(values=out, range_name=f"A3:Q{len(out)+2}", value_input_option="RAW")
tr=len(out)+3
nw.update(values=[["TOTAL","","",sum(int(o[3]) for o in out if str(o[3]).isdigit()),"","","","","","",f"paid: {paid}","","","","",total,f"{len(fpv)} FPV removed"]], range_name=f"A{tr}:Q{tr}", value_input_option="RAW")

def bg(r,g,b): return {"backgroundColor":{"red":r,"green":g,"blue":b}}
fmts=[{"range":f"A3:Q{tr-1}","format":{**bg(1,1,1),"textFormat":{"bold":False,"strikethrough":False}}}]  # reset stale fmt
fmts.append({"range":"A2:Q2","format":{**bg(.85,.85,.85),"textFormat":{"bold":True},"horizontalAlignment":"CENTER","wrapStrategy":"WRAP"}})
for i,o in enumerate(out):
    sr=i+3
    if o[1] in fpv:
        fmts.append({"range":f"A{sr}:Q{sr}","format":{**bg(.85,.85,.85),"textFormat":{"strikethrough":True}}}); continue
    met=str(o[10]).startswith("Yes")
    fmts.append({"range":f"K{sr}","format":{**(bg(.72,.88,.80) if met else bg(.96,.78,.76)),"horizontalAlignment":"CENTER"}})
    try:
        cur=float(o[8]); h=bg(.72,.88,.80) if cur>=80 else (bg(.99,.91,.70) if cur>=70 else bg(.96,.78,.76))
        fmts.append({"range":f"I{sr}","format":{**h,"horizontalAlignment":"CENTER"}})
    except Exception: pass
fmts.append({"range":f"P3:P{tr-1}","format":{"textFormat":{"bold":True},"horizontalAlignment":"CENTER"}})
fmts.append({"range":f"A{tr}:Q{tr}","format":{"textFormat":{"bold":True},**bg(.9,.9,.98)}})
nw.batch_format(fmts)
nw.freeze(rows=2, cols=2)
print(f"WROTE '{NEW_TAB}' | {len(out)} CSPs | met-metric {met_metric} | paid {paid} | FPV removed {len(fpv)} | total Rs{total:,}")
