# -*- coding: utf-8 -*-
"""Hourly refresh of 'All July Leads' into the 'Calling Sheet for MG' -> tab 'Data V2' (gid 499920107).
Self-contained (stdlib + gspread/google-auth). Reads creds from this folder. Launched hourly by loop.py.
Grain = one row per (connection, CSP): EVERY lead offered to the CSP (incl re-routed-in + OPEN), scope =
open OR terminal-in-July. STAGE + rate = the CSP's LIVE app values (CleverTap mbg_*). Per-lead 'counts as'
= the app's SETTLEMENT denom (customer must have confirmed a slot; pending EXCLUDED from rate)."""
import os, json, datetime, urllib.request as U
from collections import Counter
HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN = os.environ.get("SUPABASE_TOKEN") or open(os.path.join(HERE, "supabase_token.txt")).read().strip()
MB_KEY = os.environ["MB_KEY"]                     # GitHub Actions secret (Metabase API key)
CT_ACC, CT_PASS, CTB = "44Z-644-777Z", os.environ["CT_PASS"], "https://eu1.api.clevertap.com"  # passcode from secret
AUDIT, MG = "gonqnxpdtvjydppbrnie", "108a08d1-749a-4236-a0e9-fd4f1d3c6a27"
MS, GATE, CUT = "2026-07-01", 0.60, "2026-07-16"   # S4 denom: cx-confirmed if lead OFFERED <= CUT (16-Jul), else technician-assigned
SHEET_ID, TAB_GID = "132jlUraAFH02wjkXvcXLX6pY-nD4m6Xu5Jkjl7Awnqs", 499920107
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
IEC = "PROD_DB.CSP_TAS_SERVICE_CSP_TAS_SERVICE.INSTALL_EXECUTION_CANDIDATES"
TL  = "PROD_DB.CSP_TAS_SERVICE_CSP_TAS_SERVICE.INSTALL_STATE_TRANSITION_LOG"
TV  = "PROD_DB.DBT.TASKVANILLA_AUDIT"
CT  = "PROD_DB.CLEVERTAP_CSP_API"

def sb(q):
    req = U.Request(f"https://api.supabase.com/v1/projects/{AUDIT}/database/query",
        data=json.dumps({"query": q}).encode(),
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json", "User-Agent": "curl/8.4.0"})
    return json.loads(U.urlopen(req, timeout=60).read().decode())

def mb(q):
    req = U.Request("https://metabase.wiom.in/api/dataset",
        data=json.dumps({"database": 113, "type": "native", "native": {"query": q}}).encode(),
        headers={"x-api-key": MB_KEY, "Content-Type": "application/json"})
    j = json.loads(U.urlopen(req, timeout=200).read().decode())
    if not j.get("data"): raise SystemExit("MB ERR " + json.dumps(j)[:400])
    return j["data"]["rows"]

def fmt(ts):
    if not ts: return ""
    try:
        dt = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None: dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(IST).strftime("%d-%b %H:%M")
    except Exception: return str(ts)[:16]

def fmt_ct(ts):
    if not ts: return ""
    try:
        s = str(ts).replace("T", " ").split("+")[0].split(".")[0].strip()
        return datetime.datetime.fromisoformat(s).strftime("%d-%b %H:%M")
    except Exception: return str(ts)[:16]

def chunks(seq, n):
    seq = list(seq)
    for i in range(0, len(seq), n): yield seq[i:i+n]

def paddr(raw):
    if not raw: return ""
    if not str(raw).strip().startswith("{"): return str(raw)
    try:
        d = json.loads(raw)
        if isinstance(d, dict):
            p = []
            for k in ("home", "house", "flat", "building", "streetName", "street", "streetNumber", "road",
                      "landmark", "subLocality", "locality", "area", "city", "state", "pincode", "pin"):
                vv = d.get(k)
                if vv and str(vv).strip() and str(vv).strip().lower() not in ("none", "null") and str(vv).strip() not in p:
                    p.append(str(vv).strip())
            for kk, vv in d.items():
                if isinstance(vv, str) and vv.strip() and vv.strip() not in p and kk not in ("type", "landMarkType"):
                    p.append(vv.strip())
            return ", ".join(p)
    except Exception: pass
    return str(raw)

# ---------- 1) enrolled cohort ----------
_fcb64 = os.environ.get("FROZEN_COHORT_B64")   # partner cohort kept in a secret, not in the public repo
if _fcb64:
    import base64
    fc = json.loads(base64.b64decode(_fcb64).decode("utf-8"))
else:
    fc = json.load(open(os.path.join(HERE, "frozen_cohort.json"), encoding="utf-8"))
f1, f2 = set(fc["flow1"]), set(fc["flow2"])
opt = set(r["pid"] for r in sb("select partner_id::text pid from mg_optins where first_opted_at is not null and program='MG'"))
f2p = "','".join(f2)
done = set(r["pid"] for r in sb(f"select distinct partner_id::text pid from campaign_partners where partner_id in ('{f2p}') and campaign_id='{MG}' and scan_complete_at is not null"))
enrolled = (opt & f1) | (opt & f2 & done); pl = "','".join(enrolled)
apm = mb(f"SELECT DISTINCT csp_id, partner_id::text FROM PROD_DB.CSP_GATEWAY_SERVICE_CSP_GATEWAY_SERVICE.CSP_ACCOUNT WHERE _fivetran_active AND csp_id NOT IN ('a0a0b1','a0a6w1') AND partner_id::TEXT IN ('{pl}')")
universe = sorted({r[0] for r in apm})
print(f"enrolled {len(enrolled)} | csp universe {len(universe)}", flush=True)

# ---------- 2) contacts ----------
uinl = "','".join(universe)
cq = (f"WITH own AS (SELECT CSP_ID, MAX(PHONE_NUMBER) ph FROM PROD_DB.CSP_GATEWAY_SERVICE_CSP_GATEWAY_SERVICE.CSP_USER WHERE _fivetran_active AND STATUS='ACTIVE' AND ROLE='OWNER' GROUP BY 1),"
      f"adm AS (SELECT CSP_ID, MAX(PHONE_NUMBER) ph FROM PROD_DB.CSP_GATEWAY_SERVICE_CSP_GATEWAY_SERVICE.CSP_USER WHERE _fivetran_active AND STATUS='ACTIVE' AND ROLE IN ('MANAGER','MANAGER_PLUS') GROUP BY 1),"
      f"d AS (SELECT CSP_ID, ANY_VALUE(PARTNER_NAME) name FROM PROD_DB.DBT_CSP.DIM_CSP WHERE ETL_CURRENT AND CSP_ID IN ('{uinl}') GROUP BY 1) "
      f"SELECT d.CSP_ID, d.name, own.ph, adm.ph FROM d LEFT JOIN own ON d.CSP_ID=own.CSP_ID LEFT JOIN adm ON d.CSP_ID=adm.CSP_ID")
cname, cown, cadm = {}, {}, {}
for r in mb(cq):
    cname[r[0]] = r[1] or ""; cown[r[0]] = r[2] or ""; cadm[r[0]] = r[3] or ""

# ---------- 2b) LIVE app values (CleverTap) ----------
_HP = {"X-CleverTap-Account-Id": CT_ACC, "X-CleverTap-Passcode": CT_PASS, "Content-Type": "application/json; charset=utf-8"}
_HG = {"X-CleverTap-Account-Id": CT_ACC, "X-CleverTap-Passcode": CT_PASS}
def _cpost(p, b): return json.loads(U.urlopen(U.Request(CTB+p, data=json.dumps(b).encode(), headers=_HP), timeout=90).read().decode())
def _cget(p): return json.loads(U.urlopen(U.Request(CTB+p, headers=_HG), timeout=90).read().decode())
_today = datetime.datetime.now(IST).date()
import time as _time
_expbody = {"event_name": "App Launched",
    "from": int((_today - datetime.timedelta(days=25)).strftime("%Y%m%d")),
    "to": int((_today + datetime.timedelta(days=1)).strftime("%Y%m%d"))}
app = {}
def _i(pd, k):
    try: return int(pd.get(k) or 0)
    except Exception: return 0
for _attempt in range(6):   # the CleverTap export is intermittently empty -> retry
    app, _best = {}, {}
    try:
        cur = _cpost("/1/profiles.json?batch_size=1000", _expbody).get("cursor")
    except Exception as e:
        print(f"  export attempt {_attempt+1}: post err {str(e)[:80]}", flush=True); _time.sleep(6); continue
    pg = 0
    while cur and pg < 80:
        r = _cget("/1/profiles.json?cursor=" + cur); recs = r.get("records", []); pg += 1
        for rec in recs:
            pd = rec.get("profileData", {}); cid = pd.get("cspid")
            if not cid or "mbg_screen" not in pd: continue
            ls = max([p.get("ls", 0) for p in rec.get("platformInfo", [])] or [0])
            if cid not in _best or ls > _best[cid]:
                _best[cid] = ls
                app[cid] = {"screen": pd.get("mbg_screen_real") or pd.get("mbg_screen"), "installs": _i(pd, "mbg_installs"),
                            "leads": _i(pd, "mbg_leads"), "pending": _i(pd, "mbg_pending"), "pct": _i(pd, "mbg_pct")}
        cur = r.get("next_cursor")
        if not recs: break
    if len(app) > 100: break
    print(f"  CleverTap export attempt {_attempt+1}: {len(app)} CSPs — retrying", flush=True)
    _time.sleep(6)
print(f"CleverTap app values: {len(app)} CSPs", flush=True)

# ---------- 3) IEC base query per (conn,csp) ----------
BUCKET = ("CASE WHEN has_installed=1 THEN 'installed'"
  " WHEN last_state='DECLINED' THEN 'csp_denied'"
  " WHEN last_state='CANCELLED_BY_UPSTREAM' AND fr='TIMEOUT_P74' AND fsc='CSP_NO_SHOW' AND rc='ALLOCATION_ACCEPTED' THEN 'csp_no_show'"
  " WHEN last_state='CANCELLED_BY_UPSTREAM' AND fr='TIMEOUT_P74' AND fsc='CSP_NO_SHOW' THEN 'csp_abandoned'"
  " WHEN last_state='CANCELLED_BY_UPSTREAM' AND rc='TIMEOUT_P41' THEN 'csp_timeout'"
  " WHEN last_state='CANCELLED_BY_CUSTOMER' AND reached_slot=1 THEN 'cust_cancel_after_slot'"
  " WHEN last_state='CANCELLED_BY_CUSTOMER' AND reached_slot=0 THEN 'cust_cancel_before_slot'"
  " WHEN last_state='INSTALLATION_REPORTED_FAILED' THEN 'install_failed'"
  " WHEN last_state='CANCELLED_BY_UPSTREAM' AND rc='RETRY_EXHAUSTION' THEN 'csp_retryx'"   # CSP-fault: re-assigned until retry cap blew (~72% died from CSP no-show/timeout) — COUNTS in denom, matches poller csp_retryx (13-Jul fix)
  " WHEN last_state='CANCELLED_BY_UPSTREAM' THEN 'system_other'"
  " WHEN last_state='INSTALLATION_CANCELLED_ONSITE' THEN 'cancelled_onsite'"
  " WHEN last_state='INSTALLATION_EXPIRED' THEN 'install_expired'"   # S4 fix: expired install is TERMINAL, not open
  " ELSE 'open' END")

def base_sql(grp):
    vals = ",".join(f"('{c}')" for c in grp)
    return (f"WITH ap AS (SELECT column1 csp FROM VALUES {vals}),"
      f"cand AS (SELECT iec.EXECUTION_CANDIDATE_ID ecid, iec.CONNECTION_ID conn, iec.CSP_ID csp,"
      f" iec.CURRENT_STATE cs, iec.REASON_CODE rc, iec.FAILURE_REASON fr, iec.FAILURE_SUBREASON_CODE fsc,"
      f" iec.CREATED_AT created, iec.UPDATED_AT updated,"
      f" IFF(iec.OTP_VERIFIED=TRUE OR iec.INSTALLATION_COMPLETED_AT IS NOT NULL OR iec.COMPLETED_STEP>=7,1,0) inst,"
      f" IFF(iec.CONFIRMED_SLOT_AT IS NOT NULL,1,0) slotc, iec.PROPOSED_SLOT_DATE psd,"
      f" COALESCE(iec.CONFIRMED_SLOT_DATE,iec.CONFIRMED_SLOT_AT) csd, iec.CUSTOMER_NAME cust_name"
      f" FROM {IEC} iec JOIN ap ON iec.CSP_ID=ap.csp WHERE iec._FIVETRAN_ACTIVE),"
      f"cc AS (SELECT conn, csp, MAX_BY(cs,updated) last_state, MAX_BY(rc,updated) rc, MAX_BY(fr,updated) fr,"
      f" MAX_BY(fsc,updated) fsc, MAX(inst) has_installed, MAX(slotc) reached_slot, MAX_BY(slotc,updated) own_slot,"
      f" MAX_BY(created,updated) t_offered, TO_DATE(DATEADD(minute,330,MAX(updated))) last_date,"
      f" MAX_BY('#'||RIGHT(ecid,4),updated) install_id, MAX_BY(ecid,updated) rep_ecid, MAX_BY(cust_name,updated) cust_name,"
      f" TO_CHAR(MAX_BY(psd,updated),'DD-Mon') psd_fmt, TO_CHAR(MAX_BY(csd,updated),'DD-Mon') csd_fmt, ARRAY_AGG(DISTINCT ecid) ecids"
      f" FROM cand GROUP BY 1,2),"
      f"tl AS (SELECT cc.conn, cc.csp,"
      f" MIN(IFF(t.TO_STATE IN ('SLOT_SELECTED','AWAITING_CUSTOMER_SLOT_CONFIRMATION'),t.OCCURRED_AT,NULL)) t_slotprop,"
      f" MIN(IFF(t.TO_STATE IN ('SLOT_CONFIRMED_BY_CUSTOMER','AWAITING_TECHNICIAN_ASSIGNMENT','SLOT_AUTO_CONFIRMED'),t.OCCURRED_AT,NULL)) t_cxconf,"
      f" MIN(IFF(t.TO_STATE='TECHNICIAN_ASSIGNED',t.OCCURRED_AT,NULL)) t_tech,"
      f" MIN(IFF(t.TO_STATE='ARRIVED_AT_SITE',t.OCCURRED_AT,NULL)) t_arr"
      f" FROM cc JOIN {TL} t ON t.EXECUTION_CANDIDATE_ID=cc.rep_ecid GROUP BY 1,2),"
      f"ranked AS (SELECT conn, csp, ecid, cs, rc, fr, fsc, slotc, inst,"
      f" ROW_NUMBER() OVER (PARTITION BY conn, csp ORDER BY updated DESC) rk FROM cand),"
      f"prior AS (SELECT conn, csp, ecid pecid, cs pstate, rc prc, fr pfr, fsc pfsc, slotc pslot, inst pinst FROM ranked WHERE rk=2),"
      f"ptl AS (SELECT p.conn, p.csp,"
      f" MAX(IFF(t.TO_STATE IN ('SLOT_SELECTED','AWAITING_CUSTOMER_SLOT_CONFIRMATION'),1,0)) p_slotprop,"
      f" MAX(IFF(t.TO_STATE IN ('SLOT_CONFIRMED_BY_CUSTOMER','AWAITING_TECHNICIAN_ASSIGNMENT','SLOT_AUTO_CONFIRMED'),1,0)) p_cxconf,"
      f" MAX(IFF(t.TO_STATE='TECHNICIAN_ASSIGNED',1,0)) p_tech, MAX(IFF(t.TO_STATE='ARRIVED_AT_SITE',1,0)) p_arr"
      f" FROM prior p JOIN {TL} t ON t.EXECUTION_CANDIDATE_ID=p.pecid GROUP BY 1,2),"
      f"cls AS (SELECT cc.*, {BUCKET} bucket FROM cc) "
      f"SELECT cls.csp, cls.conn, cls.rep_ecid, cls.install_id, cls.cust_name, cls.last_state, cls.rc, cls.fr, cls.fsc,"
      f" cls.has_installed, cls.reached_slot, cls.bucket, cls.t_offered, cls.last_date, cls.psd_fmt, cls.csd_fmt,"
      f" tl.t_slotprop, tl.t_cxconf, tl.t_tech, tl.t_arr, cls.ecids, cls.own_slot,"
      f" pr.pstate, pr.prc, pr.pfr, pr.pfsc, pr.pslot, pr.pinst, ptl.p_slotprop, ptl.p_cxconf, ptl.p_tech, ptl.p_arr"
      f" FROM cls LEFT JOIN tl ON cls.conn=tl.conn AND cls.csp=tl.csp"
      f" LEFT JOIN prior pr ON cls.conn=pr.conn AND cls.csp=pr.csp LEFT JOIN ptl ON cls.conn=ptl.conn AND cls.csp=ptl.csp"
      f" WHERE cls.bucket='open' OR cls.last_date >= '{MS}'")

rows = []
for i, grp in enumerate(chunks(universe, 110)):
    rows += mb(base_sql(grp))
print(f"base rows: {len(rows)}", flush=True)

C = dict(csp=0, conn=1, rep_ecid=2, install_id=3, cust_name=4, last_state=5, rc=6, fr=7, fsc=8,
         has_installed=9, reached_slot=10, bucket=11, t_offered=12, last_date=13, psd=14, csd=15,
         t_slotprop=16, t_cxconf=17, t_tech=18, t_arr=19, ecids=20, own_slot=21,
         pstate=22, prc=23, pfr=24, pfsc=25, pslot=26, pinst=27, p_slotprop=28, p_cxconf=29, p_tech=30, p_arr=31)

def ecids_of(r):
    e = r[C["ecids"]]
    if isinstance(e, str):
        try: e = json.loads(e)
        except Exception: e = []
    return e or []

# ---------- 4) enrichment ----------
conns = sorted(set(r[C["conn"]] for r in rows))
rep_ecids = sorted(set(r[C["rep_ecid"]] for r in rows if r[C["rep_ecid"]]))
all_ecids = sorted({e for r in rows for e in ecids_of(r)})
reoff, firstoff = {}, {}
for grp in chunks(conns, 120):
    ci = "','".join(grp)
    for r in mb(f"select connection_id, count(distinct csp_id), MIN_BY(csp_id,created_at) FROM {IEC} where connection_id in ('{ci}') group by 1"):
        reoff[r[0]] = r[1]; firstoff[r[0]] = r[2]
mob, addr = {}, {}
for grp in chunks(rep_ecids, 120):
    fi = "','".join(grp)
    for r in mb(f"select EXECUTION_CANDIDATE_ID, MAX(MOBILE), MAX(ADDRESS) from {TV} where EXECUTION_CANDIDATE_ID in ('{fi}') group by 1"):
        mob[r[0]] = r[1] or ""; addr[r[0]] = r[2] or ""
fpn_e, tcall_e = {}, {}
for grp in chunks(all_ecids, 120):
    fi = "','".join(grp)
    for r in mb(f"select TRY_PARSE_JSON(PROPERTIES):execution_id::string, MIN(TIMESTAMP) from {CT}.EVENTS_DATA where EVENT_NAME='fpn_delivered' and TRY_PARSE_JSON(PROPERTIES):execution_id::string in ('{fi}') group by 1"):
        if r[0]: fpn_e[r[0]] = r[1]
    for r in mb(f"select TRY_PARSE_JSON(e.PROPERTIES):execution_id::string, MIN(e.TIMESTAMP) from {CT}.EVENTS_DATA e JOIN {CT}.PROFILE_DATA p ON e.CLEVERTAP_ID=p.CLEVERTAP_ID where e.EVENT_NAME='call_initiated' and UPPER(COALESCE(p.ROLE,''))='TECHNICIAN' and TRY_PARSE_JSON(e.PROPERTIES):execution_id::string in ('{fi}') group by 1"):
        if r[0]: tcall_e[r[0]] = r[1]

# ---------- 5) classification (current ticket + settlement) ----------
TERMINAL_STATES = {"DECLINED", "CANCELLED_BY_UPSTREAM", "CANCELLED_BY_CUSTOMER", "INSTALLATION_REPORTED_FAILED", "INSTALLATION_CANCELLED_ONSITE"}
def signals(r):
    inst = r[C["has_installed"]] == 1
    cxconf = r[C["own_slot"]] == 1 or bool(r[C["t_cxconf"]])
    techas = bool(r[C["t_tech"]])
    slotprop = bool(r[C["psd"]]) or bool(r[C["t_slotprop"]]) or cxconf
    return inst, cxconf, techas, slotprop
def cohort(r):
    inst, cxconf, techas, slotprop = signals(r)
    if inst: return "5. Installed"
    if techas: return "4. Tech Assigned, Not Installed"
    if cxconf: return "3. Cx Confirmed, Tech Not Assigned"
    if slotprop: return "2. Slot Proposed, Cx Not Confirmed"
    return "1. No Slot Proposed"
def status(r):
    if r[C["has_installed"]] == 1: return "Terminal"
    return "Terminal" if r[C["last_state"]] in TERMINAL_STATES else "Open"
def _off_date(r):
    ts = r[C["t_offered"]]
    if not ts: return ""
    try:
        dt = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None: dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(IST).date().isoformat()
    except Exception: return str(ts)[:10]
def gate_ok(r):
    # S4 denom gate: lead OFFERED (CSP) on/before 16-Jul must have a cx-confirmed slot; offered >=17-Jul must have a technician assigned.
    if _off_date(r) <= CUT: return r[C["reached_slot"]] == 1
    return bool(r[C["t_tech"]])
def treatment(r):
    b = r[C["bucket"]]; ld = (r[C["last_date"]] or "") >= MS
    if b == "open": return "Pending (open, not in rate)"
    if b == "system_other": return "Excluded — system/upstream cancel"
    if not ld: return "Excluded — closed before Jul"
    if not gate_ok(r): return "Excluded — S4 gate not met (cx≤16-Jul / tech≥17-Jul)"
    if b == "installed": return "Install (लगे)"
    return "मिले counted (denom)"
OPEN_TXT = {"AWAITING_SLOT_PROPOSAL": "Open — CSP yet to propose slot", "SLOT_SELECTED": "Open — awaiting cx to confirm slot",
    "AWAITING_CUSTOMER_SLOT_CONFIRMATION": "Open — awaiting cx to confirm slot", "SLOT_CONFIRMED_BY_CUSTOMER": "Open — awaiting technician",
    "AWAITING_TECHNICIAN_ASSIGNMENT": "Open — awaiting technician", "TECHNICIAN_ASSIGNED": "Open — tech assigned, not installed",
    "ARRIVED_AT_SITE": "Open — install in progress", "INSTALLATION_IN_PROGRESS_PRE_FEE": "Open — install in progress",
    "INSTALLATION_IN_PROGRESS_POST_FEE": "Open — install in progress", "FEE_COLLECTION_PENDING": "Open — install in progress",
    "AWAITING_CUSTOMER_OTP": "Open — awaiting customer OTP"}
def outcome(r):
    inst, cxconf, techas, slotprop = signals(r)
    if inst: return "Installed"
    if status(r) == "Open": return OPEN_TXT.get(r[C["last_state"]], "Open — " + str(r[C["last_state"]]))
    b = r[C["bucket"]]
    if b == "csp_denied": return "CSP declined the lead"
    if b in ("csp_no_show", "csp_abandoned"):
        if not slotprop: return "CSP didn't act → timed out (no-show, P74)"
        if not cxconf: return "Cx didn't confirm slot → timed out (P74)"
        return "CSP no-show (had confirmed slot)"
    if b == "csp_timeout": return "CSP didn't respond → timed out (P41)"
    if b == "cust_cancel_after_slot": return "Customer cancelled (after confirming slot)"
    if b == "cust_cancel_before_slot": return "Customer cancelled (before confirming slot)"
    if b == "install_failed": return "Install attempt reported failed"
    if b == "csp_retryx": return "CSP retries exhausted (no-show/timeout across attempts)"
    if b == "system_other": return "Cancelled upstream (system/ops)"
    if b == "cancelled_onsite": return "Cancelled on-site"
    return str(r[C["last_state"]])
def dreason(r):
    p = []
    for x in (r[C["rc"]], r[C["fr"]], r[C["fsc"]]):
        if x and x not in ("ALLOCATION_ACCEPTED", "ALLOCATION_ASSIGNED", "None") and str(x) not in p: p.append(str(x))
    return " / ".join(p)
def prior_reason(ps, prc, pfr, pfsc, pinst):
    if pinst: return "installed"
    if ps == "INSTALLATION_REPORTED_FAILED": return f"install failed ({pfsc or pfr or 'CSP'})"
    if ps == "DECLINED": return "CSP declined"
    if ps == "CANCELLED_BY_UPSTREAM" and pfr == "TIMEOUT_P74" and pfsc == "CSP_NO_SHOW": return "CSP no-show (P74)"
    if ps == "CANCELLED_BY_UPSTREAM" and prc == "TIMEOUT_P41": return "CSP no-response (P41)"
    if ps == "CANCELLED_BY_UPSTREAM" and prc == "RETRY_EXHAUSTION": return "retry exhausted"
    if ps == "CANCELLED_BY_UPSTREAM": return f"upstream cancel ({prc or pfr or 'system'})"
    if ps == "CANCELLED_BY_CUSTOMER": return "customer cancelled"
    if ps == "INSTALLATION_CANCELLED_ONSITE": return "cancelled on-site"
    return str(ps)
def prior_attempt(r):
    ps = r[C["pstate"]]
    if not ps: return ""
    pinst = r[C["pinst"]] == 1; pcx = r[C["pslot"]] == 1 or bool(r[C["p_cxconf"]])
    ptech = bool(r[C["p_tech"]]); parr = bool(r[C["p_arr"]]); pslotp = bool(r[C["p_slotprop"]]) or pcx
    far = ("Installed" if pinst else "Arrived home" if parr else "Tech Assigned" if ptech
           else "Cx Confirmed" if pcx else "Slot Proposed" if pslotp else "No Slot Proposed")
    return f"{far} → re-triggered: {prior_reason(ps, r[C['prc']], r[C['pfr']], r[C['pfsc']], pinst)}"

# ---------- 6) stage from live app (fallback settlement) ----------
SCLBL = {"secured": "Secured", "almost": "Almost", "keepgoing": "KeepGoing", "noleads": "No leads", "hold": "No leads"}
def route_settlement(inst, den, pend):
    total = den + pend
    if total == 0: return "No leads"
    if den > 0 and inst/den > GATE: return "Secured"
    if (inst + pend) > GATE*total: return "Almost"
    return "KeepGoing"
per = {}
for r in rows:
    m = per.setdefault(r[C["csp"]], {"inst": 0, "den": 0, "pend": 0}); t = treatment(r)
    if t.startswith("Install"): m["inst"] += 1; m["den"] += 1
    elif t.startswith("मिले"): m["den"] += 1
    elif t.startswith("Pending"): m["pend"] += 1
# Stage computed LIVE from the install pipeline with the app's OWN settlement rule (installs vs
# cx-confirmed-closed denom, pending excluded). This matches the CSP's in-app screen and is fresher
# than the CleverTap profile snapshot, which can lag a few minutes (app showed Almost while the
# profile still read Secured). app[] props are kept only for the A2 cross-check.
stage = {csp: route_settlement(per[csp]["inst"], per[csp]["den"], per[csp]["pend"]) for csp in per}
print("stage dist:", dict(Counter(stage.values())), flush=True)

# ---------- 7) detail + summaries ----------
COH = ["1. No Slot Proposed", "2. Slot Proposed, Cx Not Confirmed", "3. Cx Confirmed, Tech Not Assigned", "4. Tech Assigned, Not Installed", "5. Installed"]
STAGES = ["Secured", "Almost", "KeepGoing", "No leads"]
detail = []
for r in rows:
    csp = r[C["csp"]]; ec = r[C["rep_ecid"]]; nt = len(ecids_of(r))
    out = outcome(r)
    if nt > 1: out += f"  · re-offered ×{nt} tickets"
    detail.append([stage.get(csp, ""), cname.get(csp, ""), csp, cown.get(csp, ""), cadm.get(csp, ""),
        r[C["install_id"]] or "", str(nt), prior_attempt(r), cohort(r), status(r), treatment(r), out, dreason(r),
        (r[C["cust_name"]] or "").strip(), mob.get(ec, ""), paddr(addr.get(ec, "")),
        fmt(r[C["t_offered"]]), fmt_ct(fpn_e.get(ec)), fmt(r[C["t_slotprop"]]), r[C["psd"]] or "", fmt(r[C["t_cxconf"]]), r[C["csd"]] or "",
        fmt(r[C["t_tech"]]), fmt_ct(tcall_e.get(ec)), fmt(r[C["t_arr"]]),
        str(reoff.get(r[C["conn"]], 1)), "Yes" if firstoff.get(r[C["conn"]]) == csp else "No (re-routed in)"])
sord = {s: i for i, s in enumerate(STAGES)}
detail.sort(key=lambda d: (sord.get(d[0], 9), d[1], d[8]))

def tkey(r):
    t = treatment(r)
    return "inst" if t.startswith("Install") else "milecl" if t.startswith("मिले") else "pend" if t.startswith("Pending") else "excl"
A = {s: {"inst": 0, "milecl": 0, "pend": 0, "excl": 0} for s in STAGES}
for r in rows: A[stage.get(r[C["csp"]], "No leads")][tkey(r)] += 1
B = {s: {c: 0 for c in COH} for s in STAGES}
for r in rows: B[stage.get(r[C["csp"]], "No leads")][cohort(r)] += 1
OT = {s: {"Open": 0, "Terminal": 0} for s in STAGES}
for r in rows: OT[stage.get(r[C["csp"]], "No leads")][status(r)] += 1
APP = {s: {"installs": 0, "leads": 0, "pending": 0, "csps": 0} for s in STAGES}
for csp in per:
    if csp in app:
        s = stage[csp]; APP[s]["installs"] += app[csp]["installs"]; APP[s]["leads"] += app[csp]["leads"]
        APP[s]["pending"] += app[csp]["pending"]; APP[s]["csps"] += 1

# ---------- 8) build the grid ----------
DATED = _today.strftime("%d-%b")
vals, bold, fillh = [], [], []
def add(row=None): vals.append(row or [])
def mb_(): bold.append(len(vals)-1)
add([f"ALL July leads per CSP — EVERY lead offered to the CSP (incl re-routed-in & OPEN). STAGE computed LIVE from the install pipeline using the app's settlement rule (matches the CSP's in-app screen). Auto-refreshed hourly. All times IST. {len(rows)} leads · {DATED}."]); mb_(); add()
add(["SUMMARY A — how the LIVE app counts each lead. मिले (denom) = S4 settlement: lead OFFERED ≤16-Jul must have a cx-CONFIRMED slot, OFFERED ≥17-Jul must have a TECHNICIAN ASSIGNED; only leads TERMINAL in July count; OPEN/pending is NOT in the rate."]); mb_()
add(["Stage (live app)", "Installs (लगे)", "मिले other-closed", "मिले TOTAL (denom)", "Rate %", "Pending (open, not in rate)", "Excluded from rate", "Grand Total"]); mb_(); fillh.append(len(vals)-1)
def arow(a):
    inst, mc, pend, excl = a["inst"], a["milecl"], a["pend"], a["excl"]; den = inst+mc
    return inst, mc, den, (round(100*inst/den) if den else 0), pend, excl, inst+mc+pend+excl
tot = {"inst": 0, "milecl": 0, "pend": 0, "excl": 0}
for s in STAGES:
    for k in tot: tot[k] += A[s][k]
    add([s, *arow(A[s])])
add(["TOTAL", *arow(tot)]); mb_()
add(["↑ Stage & Rate computed LIVE from the install pipeline (installs ÷ cx-confirmed-closed denom; pending & cx-never-confirmed excluded) — the app's own rule. Summary A2 below is the CleverTap profile SNAPSHOT (what the poller last pushed) — it can lag these live numbers by a few min."]); add()
add(["SUMMARY A2 — CleverTap profile snapshot (mbg_installs / mbg_leads / mbg_pending) — the poller's last push; can lag the LIVE numbers above by a few min"]); mb_()
add(["Stage (live app)", "CSPs", "Installs (app)", "मिले denom (app)", "Pending (app)", "Rate %"]); mb_(); fillh.append(len(vals)-1)
ta = {"csps": 0, "installs": 0, "leads": 0, "pending": 0}
for s in STAGES:
    a = APP[s]
    for k in ta: ta[k] += a[k]
    add([s, a["csps"], a["installs"], a["leads"], a["pending"], round(100*a["installs"]/a["leads"]) if a["leads"] else 0])
add(["TOTAL", ta["csps"], ta["installs"], ta["leads"], ta["pending"], round(100*ta["installs"]/ta["leads"]) if ta["leads"] else 0]); mb_(); add()
add(["SUMMARY B — funnel: flow stage of the CURRENT ticket (counts OPEN + TERMINAL)"]); mb_()
add(["Stage", *COH, "Total"]); mb_(); fillh.append(len(vals)-1)
btot = {c: 0 for c in COH}
for s in STAGES:
    rr = [B[s][c] for c in COH]
    for c in COH: btot[c] += B[s][c]
    add([s, *rr, sum(rr)])
add(["TOTAL", *[btot[c] for c in COH], sum(btot.values())]); mb_(); add()
add(["SUMMARY C — open vs terminal"]); mb_()
add(["Stage", "Open", "Terminal", "Total"]); mb_(); fillh.append(len(vals)-1)
ct = {"Open": 0, "Terminal": 0}
for s in STAGES:
    ct["Open"] += OT[s]["Open"]; ct["Terminal"] += OT[s]["Terminal"]
    add([s, OT[s]["Open"], OT[s]["Terminal"], OT[s]["Open"]+OT[s]["Terminal"]])
add(["TOTAL", ct["Open"], ct["Terminal"], ct["Open"]+ct["Terminal"]]); mb_(); add()
add(["LEGEND"]); mb_()
add(["• Stage (LIVE) = computed from the live install data with the SAME settlement rule the app uses (installs ÷ cx-confirmed-closed denom; pending & cx-never-confirmed excluded). Matches the CSP's in-app screen and is fresher than the CleverTap profile snapshot (which can lag a few min — e.g. app shows Almost while the profile still reads Secured)."])
add(["• Grain = one row per (lead × CSP): every lead offered to the CSP, incl re-routed-in and still-open leads. Scope = OPEN, or reached a terminal state in July."])
add(["• 'Counts in the app as' (S4 SETTLEMENT — offered ≤16-Jul needs cx-confirmed slot; offered ≥17-Jul needs technician assigned): Install (लगे)=installed · मिले counted=gated lead that closed in July (in denom; includes RETRY_EXHAUSTION = CSP-fault retries-exhausted miss) · Pending (open)=still open, NOT in rate · Excluded=S4 gate not met, or genuine system/upstream cancel (NOT retry-exhaustion), or closed before July."])
add(["• RATE = installs ÷ denom (denom = installs + मिले-counted). Open/pending & cx-never-confirmed are NOT in the rate — a CSP with many open leads can still be 'Secured'."])
add(["• Re-offers: everything shown ('Flow stage', all timestamps, Outcome) is the CURRENT/latest ticket only. '# tickets'=attempts at THIS CSP; 'Prior attempt' = previous ticket's furthest stage → why re-triggered; '# CSPs offered to' = distinct CSPs the customer touched overall."]); add()
DHDR = ["Stage (LIVE)", "CSP Name", "CSP ID", "CSP Owner Mobile", "CSP Admin/Mgr Mobile", "Install ID (current)",
        "# tickets (this CSP)", "Prior attempt (why re-triggered)", "Flow stage (current ticket)", "Status", "Counts in the app as", "Outcome / Why",
        "Reason codes (raw)", "Cx Name", "Cx Mobile", "Cx Full Address", "Offered @", "Notif Pushed @ (FPN)",
        "CSP Proposed Slot @", "Proposed Slot Date", "Cx Confirmed Slot @", "Confirmed Slot Date", "Tech Assigned @",
        "Tech Called @", "Arrived Home @", "# CSPs offered to", "First offer to this CSP?"]
add(DHDR); mb_(); dhdr_row = len(vals)-1; fillh.append(dhdr_row)
for d in detail: add(d)
NC = len(DHDR)
grid = [(row + [""]*(NC-len(row))) if len(row) < NC else row for row in vals]

# ---------- 9) write ----------
import gspread
from google.oauth2.service_account import Credentials
_SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
_sa = os.environ.get("GOOGLE_SA_JSON")  # GitHub Actions secret; falls back to the local file (Railway/laptop)
if _sa:
    creds = Credentials.from_service_account_info(json.loads(_sa), scopes=_SCOPES)
else:
    creds = Credentials.from_service_account_file(os.path.join(HERE, "wiom-sheets-writer.json"), scopes=_SCOPES)
sh = gspread.authorize(creds).open_by_key(SHEET_ID)
ws = sh.get_worksheet_by_id(TAB_GID); sid = ws.id
ws.clear(); ws.resize(rows=len(grid)+5, cols=NC)
ws.update(range_name="A1", values=grid, value_input_option="RAW")
reqs = []
for rr in bold:
    reqs.append({"repeatCell": {"range": {"sheetId": sid, "startRowIndex": rr, "endRowIndex": rr+1}, "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}}, "fields": "userEnteredFormat.textFormat.bold"}})
for rr in fillh:
    reqs.append({"repeatCell": {"range": {"sheetId": sid, "startRowIndex": rr, "endRowIndex": rr+1, "startColumnIndex": 0, "endColumnIndex": NC}, "cell": {"userEnteredFormat": {"backgroundColor": {"red": 0.18, "green": 0.21, "blue": 0.29}, "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}}}}, "fields": "userEnteredFormat(backgroundColor,textFormat)"}})
reqs.append({"updateSheetProperties": {"properties": {"sheetId": sid, "gridProperties": {"frozenRowCount": dhdr_row+1}}, "fields": "gridProperties.frozenRowCount"}})
widths = [90, 200, 66, 130, 140, 90, 60, 250, 210, 78, 220, 250, 190, 150, 110, 340, 108, 130, 130, 110, 130, 110, 130, 130, 108, 66, 150]
for j, w in enumerate(widths):
    reqs.append({"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": j, "endIndex": j+1}, "properties": {"pixelSize": w}, "fields": "pixelSize"}})
sh.batch_update({"requests": reqs})
print(f"WROTE Data V2: {len(grid)} rows x {NC} cols ({len(rows)} leads) @ {datetime.datetime.now(IST).strftime('%d-%b %H:%M')} IST", flush=True)
