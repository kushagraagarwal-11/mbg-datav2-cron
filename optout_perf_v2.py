# -*- coding: utf-8 -*-
import os, sys, json, re, urllib.request, datetime
sys.stdout.reconfigure(encoding="utf-8")
import gspread
from google.oauth2 import service_account
MB_KEY="mb_1dsbxsJfyROPsVyNpifJ8hTTlIDG85+qNKRo91KDnb4="
SCOPES=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
SHEET="1ap0K6GB6RijeLHRWPf9N84U0cl1XEJs67DSQBql7sGQ"; GID=825198366
MON={'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12}
TODAY=datetime.date(2026,9,10)
def mb(sql):
    body=json.dumps({"database":113,"type":"native","native":{"query":sql}}).encode()
    d=json.loads(urllib.request.urlopen(urllib.request.Request("https://metabase.wiom.in/api/dataset",data=body,headers={"x-api-key":MB_KEY,"Content-Type":"application/json"}),timeout=180).read().decode())
    if isinstance(d,dict) and d.get("error"): raise SystemExit("ERR "+str(d["error"])[:300])
    return d["data"]["rows"]
for p in (r"C:\Users\Palak Vardhan\Desktop\mbg\mbg-cron\wiom-sheets-writer.json",
          r"C:\Users\Palak Vardhan\dashboard\wiom-sheets-writer.json"):
    if os.path.exists(p): cr=service_account.Credentials.from_service_account_file(p,scopes=SCOPES); break
gc=gspread.authorize(cr); ss=gc.open_by_key(SHEET); w=next(x for x in ss.worksheets() if x.id==GID)
v=w.get_all_values()
def pf(t):
    ds=re.findall(r"(\d{1,2})\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)",(t or "").lower())
    return min([datetime.date(2026,MON[m],int(d)) for d,m in ds]) if ds else None
rows=[(i+1,v[i][1].strip(),pf(v[i][4] if len(v[i])>4 else "")) for i in range(2,len(v)) if len(v[i])>1 and v[i][1].strip().startswith("a0")]
csps=[c for _,c,_ in rows]; il=",".join("'%s'"%c for c in csps)
optin={r[0]:r[1] for r in mb(f"SELECT CSP_ID,TO_CHAR(TO_DATE(DATEADD(minute,330,MIN(CONSENT_TIMESTAMP))),'YYYY-MM-DD') FROM PROD_DB.CSP_RV_SERVICE_CSP_RV_SERVICE.DOMINANCE_CONSENT WHERE CONSENT_CHOICE='OPTED_IN' AND _FIVETRAN_ACTIVE AND CSP_ID IN ({il}) GROUP BY 1")}
bl=",".join(f"('{c}','{optin[c]}','{wd:%Y-%m-%d}')" for _,c,wd in rows if optin.get(c) and wd)
# PRE = matched window: ON-750 + POST days combined (= days from opt-in to today),
# taken immediately before opt-in. So PRE length == the whole span since opt-in.
sql=f"""WITH bounds AS (SELECT column1 CSP_ID,column2::date opt_in,column3::date withdraw,
    DATEADD('day', -GREATEST(DATEDIFF('day',column2::date,'2026-09-10'::date),1), column2::date) pre_start FROM VALUES {bl}),
agg AS (SELECT iec.CSP_ID,iec.CONNECTION_ID,TO_DATE(DATEADD(minute,330,MIN(iec.CREATED_AT))) cd,
  MAX(IFF(iec.EXECUTOR_ID IS NOT NULL,1,0)) a,MAX(IFF(iec.INSTALLATION_COMPLETED_AT IS NOT NULL,1,0)) i
  FROM PROD_DB.CSP_TAS_SERVICE_CSP_TAS_SERVICE.INSTALL_EXECUTION_CANDIDATES iec WHERE iec._FIVETRAN_ACTIVE AND iec.CSP_ID IN ({il}) GROUP BY 1,2)
SELECT a.CSP_ID,CASE
    WHEN a.cd>=b.pre_start AND a.cd<b.opt_in THEN 'pre'
    WHEN a.cd>=b.opt_in AND a.cd<b.withdraw THEN 'on'
    WHEN a.cd>=b.withdraw THEN 'post' ELSE 'excl' END per,
  COUNT(*) off,SUM(a.a) asg,SUM(a.i) ins FROM agg a JOIN bounds b ON b.CSP_ID=a.CSP_ID GROUP BY 1,2"""
D={}
for cid,per,off,asg,ins in mb(sql):
    if per=='excl': continue                       # bookings older than the matched PRE window
    D.setdefault(cid,{})[per]={'o':off,'a':asg,'i':ins}

def fmtd(s):
    y,m,dd=map(int,s.split('-')); return datetime.date(y,m,dd).strftime("%d-%b")
def pdate(s):
    try: y,m,dd=map(int,s.split('-')); return datetime.date(y,m,dd)
    except Exception: return None
AR="\u2192"; DASH="\u2014"
# Layout A:S — each period block leads with a Days column.
hdr1=["CSP & withdrawal context","","","","","Key dates","",
      "PRE  (= ON+POST days, before opt-in)","","","",
      "ON 750  (opt-in "+AR+" withdraw)","","","",
      "POST  (after withdraw)","","",""]
hdr2=["CSP","CSP ID","Cohort","Reason in detail","Bug-front status","Opt-in","Withdrew",
      "Days","Offered","Off"+AR+"Asg","Asg"+AR+"Inst",
      "Days","Offered","Off"+AR+"Asg","Asg"+AR+"Inst",
      "Days","Offered","Off"+AR+"Asg","Asg"+AR+"Inst"]
def pct(n,d): return round(100*n/d) if d else None
def trip(cid,per):
    x=D.get(cid,{}).get(per)
    if not x or x['o']==0: return ["0",DASH,DASH],[None,None]
    ap=pct(x['a'],x['o']); ip=pct(x['i'],x['a'])
    return [x['o'], (f"{ap}%" if ap is not None else DASH), (f"{ip}%" if ip is not None else DASH)],[ap,ip]
rowvals=[]; pctcells=[]
for sr,cid,wd in rows:
    pre,pp=trip(cid,'pre'); on,op=trip(cid,'on'); post,ptp=trip(cid,'post')
    oi=pdate(optin.get(cid,""))
    on_days   = (wd-oi).days    if (oi and wd) else ""
    post_days = (TODAY-wd).days if wd else ""
    pre_days  = (TODAY-oi).days if oi else ""          # == on_days + post_days
    fp=[fmtd(optin[cid]) if optin.get(cid) else DASH, wd.strftime("%d-%b") if wd else DASH]
    rowvals.append((sr, fp + [pre_days]+pre + [on_days]+on + [post_days]+post))
    for col,val in zip(("J","K","N","O","R","S"), pp+op+ptp): pctcells.append((sr,col,val))
w.update(values=[hdr1], range_name="A1:S1", value_input_option="RAW")
w.update(values=[hdr2], range_name="A2:S2", value_input_option="RAW")
w.update(values=[rv for _,rv in rowvals], range_name=f"F3:S{rows[-1][0]}", value_input_option="RAW")
last=rows[-1][0]
legend=("Days = length of the window · Offered = bookings offered (IEC, cohort by offer date) · "
        "Off"+AR+"Asg = % that got a technician assigned · Asg"+AR+"Inst = % of assigned that installed. "
        "Green \u2265 80%, amber 50-79%, red <50%.  PRE = as many days before opt-in as ON-750 + POST "
        "combined (matches the full span since opt-in). ON = opt-in"+AR+"withdrawal(col E). POST = after withdrawal.")
w.update(values=[[legend]], range_name=f"A{last+2}", value_input_option="RAW")
print("values written")

# ---- FORMATTING ----
def bg(r,g,b): return {"backgroundColor":{"red":r,"green":g,"blue":b}}
def wtxt(): return {"textFormat":{"bold":True,"foregroundColor":{"red":1,"green":1,"blue":1}}}
def dark(): return {"textFormat":{"bold":True}}
fmts=[]
fmts.append({"range":"A1:E1","format":{**bg(.27,.19,.32),**wtxt(),"horizontalAlignment":"LEFT"}})
fmts.append({"range":"F1:G1","format":{**bg(.4,.4,.4),**wtxt(),"horizontalAlignment":"CENTER"}})
fmts.append({"range":"H1:K1","format":{**bg(.29,.53,.91),**wtxt(),"horizontalAlignment":"CENTER"}})
fmts.append({"range":"L1:O1","format":{**bg(.85,0,.55),**wtxt(),"horizontalAlignment":"CENTER"}})
fmts.append({"range":"P1:S1","format":{**bg(.91,.58,.11),**wtxt(),"horizontalAlignment":"CENTER"}})
fmts.append({"range":"A2:S2","format":{**bg(.93,.93,.93),**dark(),"horizontalAlignment":"CENTER"}})
fmts.append({"range":"A2:E2","format":{**bg(.93,.93,.93),**dark(),"horizontalAlignment":"LEFT"}})
fmts.append({"range":f"F3:G{last}","format":{"horizontalAlignment":"CENTER","backgroundColor":{"red":.96,"green":.96,"blue":.96}}})
fmts.append({"range":f"H3:I{last}","format":{"horizontalAlignment":"CENTER","backgroundColor":{"red":.91,"green":.94,"blue":.996}}})
fmts.append({"range":f"L3:M{last}","format":{"horizontalAlignment":"CENTER","backgroundColor":{"red":.99,"green":.91,"blue":.96}}})
fmts.append({"range":f"P3:Q{last}","format":{"horizontalAlignment":"CENTER","backgroundColor":{"red":.996,"green":.94,"blue":.88}}})
fmts.append({"range":f"J3:K{last}","format":{"horizontalAlignment":"CENTER"}})
fmts.append({"range":f"N3:O{last}","format":{"horizontalAlignment":"CENTER"}})
fmts.append({"range":f"R3:S{last}","format":{"horizontalAlignment":"CENTER"}})
def heat(val):
    if val is None: return None
    if val>=80: return bg(.72,.88,.80)
    if val>=50: return bg(.99,.91,.70)
    return bg(.96,.78,.76)
for sr,col,val in pctcells:
    h=heat(val)
    if h: fmts.append({"range":f"{col}{sr}","format":{**h,"horizontalAlignment":"CENTER"}})
w.batch_format(fmts)
try: w.freeze(rows=2, cols=2)
except Exception as e: print("freeze:",e)
for rng in ("A1:E1","F1:G1","H1:K1","L1:O1","P1:S1"):
    try: w.merge_cells(rng)
    except Exception: pass
print("formatting done | rows", len(rowvals))
