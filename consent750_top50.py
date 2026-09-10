# -*- coding: utf-8 -*-
"""Real-time refresh of the "Audit & consent pending -> Rs750 offer" sheet (top 50).
P = 750 opt-in status (from the EVENT Payout750_Confirmed/Closed[new] — the consent
    API was removed, so a fired confirm is the opt-in signal, NOT a backend write).
Q = Opt-in date (timestamp of that event, IST).
R = Consent completion (Yes/No)   } from the source consent sheet, matched by CSP ID.
S = Consent completed on (date)   }
Only the top-50 rows (rank 1..50) are touched. Env: CT_PASS, GOOGLE_SA_JSON (or local SA).
"""
import os, sys, json, re, tempfile, urllib.request, datetime
sys.stdout.reconfigure(encoding="utf-8")
import gspread
from google.oauth2 import service_account

CT_ACC="44Z-644-777Z"; CT_PASS=os.environ.get("CT_PASS") or os.environ.get("CLEVERTAP_PASSCODE")
CT="https://eu1.api.clevertap.com"
HP={"X-CleverTap-Account-Id":CT_ACC,"X-CleverTap-Passcode":CT_PASS,"Content-Type":"application/json; charset=utf-8"}
HG={"X-CleverTap-Account-Id":CT_ACC,"X-CleverTap-Passcode":CT_PASS}
TARGET="1_3M0jaBSXMjqPCOP6Ddoi8Y9D0PyiFPEFIQkEXZvlr8"; TARGET_TAB="Sheet1"
SOURCE="1iUiiXEhyHqh-P5XgUQptnQM81wkbUcj45v43_kfNHEc"; SOURCE_TAB="Sheet1"
IST=datetime.timezone(datetime.timedelta(hours=5,minutes=30))
SCOPES=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
MONTHS=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']

def export(ev):
    to=int((datetime.datetime.now(IST)+datetime.timedelta(days=1)).strftime("%Y%m%d"))
    body=json.dumps({"event_name":ev,"from":20260818,"to":to}).encode()
    d=json.loads(urllib.request.urlopen(urllib.request.Request(CT+"/1/events.json?batch_size=1000",data=body,headers=HP),timeout=120).read().decode())
    cur=d.get("cursor"); recs=[]; p=0
    while cur and p<500:
        dd=json.loads(urllib.request.urlopen(urllib.request.Request(CT+"/1/events.json?cursor="+cur,headers=HG),timeout=120).read().decode())
        recs+=dd.get("records",[]); cur=dd.get("next_cursor"); p+=1
        if not cur: break
    return recs

def fmt_ts(ts):
    s=str(ts)
    try:
        dt=datetime.datetime.strptime(s[:14],"%Y%m%d%H%M%S")
        return f"{dt.day:02d}-{MONTHS[dt.month-1]}-{dt.year} {dt.hour:02d}:{dt.minute:02d}"
    except Exception: return ""

MB_KEY=os.environ.get("METABASE_KEY","mb_1dsbxsJfyROPsVyNpifJ8hTTlIDG85+qNKRo91KDnb4=")
def mb(sql):
    body=json.dumps({"database":113,"type":"native","native":{"query":sql}}).encode()
    return json.loads(urllib.request.urlopen(urllib.request.Request("https://metabase.wiom.in/api/dataset",
        data=body,headers={"x-api-key":MB_KEY,"Content-Type":"application/json"}),timeout=120).read().decode())["data"]["rows"]

def creds():
    raw=os.environ.get("GOOGLE_SA_JSON","")
    if raw:
        t=tempfile.NamedTemporaryFile("w",suffix=".json",delete=False); t.write(raw); t.close()
        return service_account.Credentials.from_service_account_file(t.name,scopes=SCOPES)
    for p in (r"C:\Users\Palak Vardhan\Desktop\mbg\mbg-cron\wiom-sheets-writer.json",
              r"C:\Users\Palak Vardhan\dashboard\wiom-sheets-writer.json"):
        if os.path.exists(p): return service_account.Credentials.from_service_account_file(p,scopes=SCOPES)
    raise SystemExit("no SA creds")

def main():
    gc=gspread.authorize(creds())
    tw=gc.open_by_key(TARGET).worksheet(TARGET_TAB)
    grid=tw.get_all_values()
    HDR=grid[2]                                   # row 3 = headers
    # top-50 rows: rank (col A) 1..50, data from row 4
    rows=[]                                        # (sheet_row, cspid)
    for i in range(3,len(grid)):
        r=grid[i]
        if len(r)<2: continue
        rank=(r[0] or "").strip(); cid=(r[1] or "").strip()
        if cid.startswith("a0") and rank.isdigit() and int(rank)<=50:
            rows.append((i+1,cid))
    CSPS={c for _,c in rows}
    print(f"top-50 rows: {len(rows)} ({rows[0][1]}..{rows[-1][1]})",flush=True)

    # --- opt-in from EVENTS ---
    confirmed={}; declined=set(); viewed=set()
    for r in export("Payout750_Confirmed"):
        c=(r.get("profile",{}).get("profileData",{}) or {}).get("cspid","")
        if c in CSPS:
            ts=int(str(r.get("ts",0))[:14] or 0)
            if c not in confirmed or ts<confirmed[c]: confirmed[c]=ts
    for r in export("Payout750_Closed"):
        c=(r.get("profile",{}).get("profileData",{}) or {}).get("cspid","")
        if c not in CSPS: continue
        ch=(r.get("event_props",{}) or {}).get("choice","")
        if ch=="new":
            ts=int(str(r.get("ts",0))[:14] or 0)
            if c not in confirmed or ts<confirmed[c]: confirmed[c]=ts
        elif ch=="later": declined.add(c)
    for r in export("Payout750_Declined"):
        c=(r.get("profile",{}).get("profileData",{}) or {}).get("cspid","")
        if c in CSPS: declined.add(c)
    for r in export("Payout750_Viewed"):
        c=(r.get("profile",{}).get("profileData",{}) or {}).get("cspid","")
        if c in CSPS: viewed.add(c)

    # --- consent from SOURCE sheet ---
    sw=gc.open_by_key(SOURCE).worksheet(SOURCE_TAB)
    sgrid=sw.get_all_values(); sh=sgrid[0]
    def col(name): 
        for j,h in enumerate(sh):
            if (h or "").strip().lower()==name: return j
        return -1
    jID=col("partner id"); jForm=col("consent form"); jOn=col("consented on")
    # CSP ID is the last populated col (K in the dump); find by 'a0' pattern
    jCsp=None
    for j in range(len(sh)-1,-1,-1):
        if any(len(r)>j and (r[j] or "").strip().startswith("a0") for r in sgrid[1:50]): jCsp=j; break
    consent={}
    for r in sgrid[1:]:
        if jCsp is None or len(r)<=jCsp: continue
        cid=(r[jCsp] or "").strip()
        if not cid.startswith("a0"): continue
        form=(r[jForm] or "").strip() if jForm>=0 and len(r)>jForm else ""
        on=(r[jOn] or "").strip() if jOn>=0 and len(r)>jOn else ""
        consent[cid]=(form,on)
    print(f"source consent map: {len(consent)} CSPs | cols form={jForm} on={jOn} csp={jCsp}",flush=True)

    # reachability (does the CSP have an owner/admin app profile at all?)
    il=",".join("'%s'"%c for c in CSPS)
    reachable={r[0] for r in mb(f"SELECT DISTINCT CSPID FROM PROD_DB.CLEVERTAP_CSP_API.PROFILE_DATA WHERE CSPID IN ({il}) AND COALESCE(_FIVETRAN_DELETED,FALSE)=FALSE AND IDENTITY IS NOT NULL AND LOWER(ROLE) IN ('owner','admin','manager','manager_plus')")}
    exposed=set(confirmed)|declined|viewed
    print(f"reachable={len(reachable)} exposed(any Payout750 event)={len(exposed&CSPS)}",flush=True)

    def status(c):
        if c in confirmed: return "Opted in (event)"
        if c in declined: return "Declined"
        if c in viewed: return "Abandoned (viewed, no decision)"
        if c not in reachable: return "Not reachable (no app profile)"
        return "Not viewed yet"
    # Flow 2 is live on the whole top-50 cohort (confirmed by ops 09-Sep), so a
    # not-yet-viewed CSP is "live, awaiting app-open" — NOT "never shown".
    LIVE_CAMPAIGN = os.environ.get("LIVE_CAMPAIGN", "Flow 2")
    def shown(c):                                   # column O — LIVE now
        if c in exposed: return "Yes - seen"                     # actually opened the banner
        if c not in reachable: return "No - not reachable"
        return f"{LIVE_CAMPAIGN} live - not seen yet"            # campaign delivered, awaiting view

    first=rows[0][0]; last=rows[-1][0]
    by={row:c for row,c in rows}
    block=[]                                        # O:S per row
    optn=0; consy=0; shownn=0
    for row in range(first,last+1):
        c=by.get(row)
        if not c: block.append(["","","","",""]); continue
        st=status(c); od=fmt_ts(confirmed[c]) if c in confirmed else ""
        form,on=consent.get(c,("",""))
        if c in confirmed: optn+=1
        if form.lower()=="yes": consy+=1
        if c in exposed: shownn+=1
        block.append([shown(c),st,od,form,on])      # O,P,Q,R,S
    tw.update(values=[["Consent completed on"]], range_name="S3", value_input_option="RAW")   # new header
    tw.update(values=block, range_name=f"O{first}:S{last}", value_input_option="RAW")
    now=datetime.datetime.now(IST)
    # refresh the banner timestamp in A1
    try:
        a1=grid[0][0]
        a1n=re.sub(r"refreshed .*$", f"refreshed {now:%d-%b-%Y %H:%M IST}", a1)
        if a1n!=a1: tw.update(values=[[a1n]], range_name="A1", value_input_option="RAW")
    except Exception as e: print("A1 note skip:",e)
    print(f"wrote O{first}:S{last} | shown={shownn} opted(event)={optn} declined={len(declined&CSPS)} | consent Yes={consy} | {now:%d-%b %H:%M IST}",flush=True)

if __name__=="__main__": main()
