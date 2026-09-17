# -*- coding: utf-8 -*-
"""OptOut 750 tab: fill Pre (F) / when-on-750 (G) / Post (H) with the booking funnel
for each CSP's three life-stages. Cohort by booking-offer date (IEC.CREATED_AT):
  Offered  = distinct bookings (connections) offered to the CSP in the period
  Off->Asg = of those, % that got a technician assigned (EXECUTOR_ID)
  Asg->Inst= of the assigned, % that installed (INSTALLATION_COMPLETED_AT)
Periods: pre = before opt-in; on = opt-in->withdrawal (col E); post = after withdrawal.
"""
import os, sys, json, re, urllib.request, datetime
sys.stdout.reconfigure(encoding="utf-8")
import gspread
from google.oauth2 import service_account
MB_KEY="mb_1dsbxsJfyROPsVyNpifJ8hTTlIDG85+qNKRo91KDnb4="
SCOPES=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
SHEET="1ap0K6GB6RijeLHRWPf9N84U0cl1XEJs67DSQBql7sGQ"; GID=825198366
MON={'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12}
MN=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
TODAY=datetime.date(2026,9,10)
def mb(sql):
    body=json.dumps({"database":113,"type":"native","native":{"query":sql}}).encode()
    d=json.loads(urllib.request.urlopen(urllib.request.Request("https://metabase.wiom.in/api/dataset",data=body,headers={"x-api-key":MB_KEY,"Content-Type":"application/json"}),timeout=180).read().decode())
    if isinstance(d,dict) and d.get("error"): raise SystemExit("ERR "+str(d["error"])[:300])
    return d["data"]["rows"]
for p in (r"C:\Users\Palak Vardhan\Desktop\mbg\mbg-cron\wiom-sheets-writer.json",
          r"C:\Users\Palak Vardhan\dashboard\wiom-sheets-writer.json"):
    if os.path.exists(p): cr=service_account.Credentials.from_service_account_file(p,scopes=SCOPES); break
gc=gspread.authorize(cr)
ss=gc.open_by_key(SHEET); w=next(x for x in ss.worksheets() if x.id==GID)
v=w.get_all_values()
def parse_first(t):
    ds=re.findall(r"(\d{1,2})\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)",(t or "").lower())
    return min([datetime.date(2026,MON[m],int(d)) for d,m in ds]) if ds else None
rows=[]  # (sheet_row, cspid, withdraw_date)
for i,r in enumerate(v):
    if i>=2 and len(r)>1 and r[1].strip().startswith("a0"):
        rows.append((i+1, r[1].strip(), parse_first(r[4] if len(r)>4 else "")))
csps=[c for _,c,_ in rows]; il=",".join("'%s'"%c for c in csps)

# opt-in date (backend consent)
optin={r[0]: r[1] for r in mb(f"SELECT CSP_ID, TO_CHAR(TO_DATE(DATEADD(minute,330,MIN(CONSENT_TIMESTAMP))),'YYYY-MM-DD') FROM PROD_DB.CSP_RV_SERVICE_CSP_RV_SERVICE.DOMINANCE_CONSENT WHERE CONSENT_CHOICE='OPTED_IN' AND _FIVETRAN_ACTIVE AND CSP_ID IN ({il}) GROUP BY 1")}
print("opt-in dates:")
for _,c,wd in rows: print(f"  {c}: optin={optin.get(c,'NONE')} withdraw={wd}")

# build bounds VALUES and funnel query
vals=[]
for _,c,wd in rows:
    oi=optin.get(c); 
    if not oi or not wd: continue
    vals.append(f"('{c}','{oi}','{wd:%Y-%m-%d}')")
boundsql=",".join(vals)
sql=f"""
WITH bounds AS (SELECT column1 CSP_ID, column2::date opt_in, column3::date withdraw FROM VALUES {boundsql}),
agg AS (
  SELECT iec.CSP_ID, iec.CONNECTION_ID,
    TO_DATE(DATEADD(minute,330,MIN(iec.CREATED_AT))) created_d,
    MAX(IFF(iec.EXECUTOR_ID IS NOT NULL,1,0)) assigned,
    MAX(IFF(iec.INSTALLATION_COMPLETED_AT IS NOT NULL,1,0)) installed
  FROM PROD_DB.CSP_TAS_SERVICE_CSP_TAS_SERVICE.INSTALL_EXECUTION_CANDIDATES iec
  WHERE iec._FIVETRAN_ACTIVE AND iec.CSP_ID IN ({il}) GROUP BY 1,2)
SELECT a.CSP_ID,
  CASE WHEN a.created_d < b.opt_in THEN 'pre' WHEN a.created_d < b.withdraw THEN 'on' ELSE 'post' END period,
  COUNT(*) offered, SUM(a.assigned) assigned, SUM(a.installed) installed,
  TO_CHAR(MIN(a.created_d),'YYYY-MM-DD') min_d
FROM agg a JOIN bounds b ON b.CSP_ID=a.CSP_ID GROUP BY 1,2
"""
data={}  # cspid -> period -> dict
for cid,per,off,asg,ins,mind in mb(sql):
    data.setdefault(cid,{})[per]={'off':off,'asg':asg,'ins':ins,'min':mind}

def d(s): 
    return datetime.date(*map(int,s.split('-'))) if s else None
def cell(cid, per):
    x=data.get(cid,{}).get(per)
    if not x or x['off']==0: return "—"
    off,asg,ins=x['off'],x['asg'],x['ins']
    # period length
    oi=d(optin.get(cid)); wd=next(w2 for r2,c2,w2 in rows if c2==cid)
    if per=='pre': start=d(x['min']); end=oi
    elif per=='on': start=oi; end=wd
    else: start=wd; end=TODAY
    days=max((end-start).days,1) if start and end else None
    permo=f"·~{off/days*30:.1f}/mo" if days else ""
    a_pct=f"{asg/off*100:.0f}%" if off else "-"
    i_pct=f"{ins/asg*100:.0f}%" if asg else "-"
    rng=f"({days}d {start:%d-%b}\u2192{end:%d-%b})" if start and end else ""
    return f"Offered {off} {permo}\nOff\u2192Asg {a_pct} ({asg}/{off})\nAsg\u2192Inst {i_pct} ({ins}/{asg})\n{rng}"

out=[]
for sr,cid,wd in rows:
    out.append((sr,[cell(cid,'pre'),cell(cid,'on'),cell(cid,'post')]))
# write F:H per row
for sr,triple in out:
    w.update(values=[triple], range_name=f"F{sr}:H{sr}", value_input_option="RAW")
w.update(values=[["Each cell: Offered (count·per-month) | Offered\u2192Assigned% | Assigned\u2192Install%. Cohort by booking-offer date (IEC). Pre=before opt-in, On=opt-in\u2192withdrawal(col E), Post=after. Src: IEC + DOMINANCE_CONSENT."]], range_name="J1", value_input_option="RAW")
print("\nwrote F:H for", len(out), "CSPs")
for sr,cid,wd in rows:
    x=data.get(cid,{})
    print(f"  {cid}: pre={x.get('pre',{}).get('off',0)} on={x.get('on',{}).get('off',0)} post={x.get('post',{}).get('off',0)}")
