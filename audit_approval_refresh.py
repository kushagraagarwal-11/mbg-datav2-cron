# -*- coding: utf-8 -*-
"""Hourly refresh of the 'CSPs Audit Approval' sheet (1iUiiX...) for the 1076 escape-velocity CSPs.
Columns: Partner ID, CSP Name, City, Owner Mobile, Admin Mobile, Audit done?, Audit approval?.
  Audit done?     = device scan complete in the audit tool (campaign 108a08d1, scan_complete_at).
  Audit approval? = partner agreed in the csp-approval portal (consents.agreed_at) — the PSF->SD consent.
Creds from env (GitHub Actions secrets); falls back to local files for laptop runs."""
import os, json, base64, csv, io, datetime, urllib.request as U
HERE = os.path.dirname(os.path.abspath(__file__))
SUPA = os.environ.get("SUPABASE_TOKEN") or open(os.path.join(HERE, "..", "mbg-cron", "supabase_token.txt")).read().strip()
MB_KEY = os.environ.get("MB_KEY") or "mb_1dsbxsJfyROPsVyNpifJ8hTTlIDG85+qNKRo91KDnb4="
AUDIT, APPROVAL = "gonqnxpdtvjydppbrnie", "oobaxfbsmqhdaligebmg"
MGCAMP = "108a08d1-749a-4236-a0e9-fd4f1d3c6a27"
SHEET = "1iUiiXEhyHqh-P5XgUQptnQM81wkbUcj45v43_kfNHEc"
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

def sb(q, ref):
    r = U.Request(f"https://api.supabase.com/v1/projects/{ref}/database/query",
        data=json.dumps({"query": q}).encode(),
        headers={"Authorization": f"Bearer {SUPA}", "Content-Type": "application/json", "User-Agent": "curl/8.4.0"})
    return json.loads(U.urlopen(r, timeout=60).read().decode())

def mb(q):
    r = U.Request("https://metabase.wiom.in/api/dataset",
        data=json.dumps({"database": 113, "type": "native", "native": {"query": q}}).encode(),
        headers={"x-api-key": MB_KEY, "Content-Type": "application/json"})
    j = json.loads(U.urlopen(r, timeout=180).read().decode())
    return j["data"]["rows"] if j.get("data") else []

# ---- 1) the 1076 cohort (from secret, or local CSV on laptop) ----
b64 = os.environ.get("CSP_1076_B64")
if b64:
    txt = base64.b64decode(b64).decode("utf-8")
else:
    txt = open(r"C:\Users\Palak Vardhan\escape_velocity\CSP_1076_active_loggedin_mobile.csv", encoding="utf-8").read()
rows = list(csv.DictReader(io.StringIO(txt)))
pids = [str(r["partner_account_id"]).strip() for r in rows]
pl = "','".join(set(pids))
print(f"cohort: {len(rows)} CSPs", flush=True)

# ---- 2) audit done (scan_complete) ----
done = set(str(r["pid"]) for r in sb(f"select distinct partner_id::text pid from campaign_partners where campaign_id='{MGCAMP}' and scan_complete_at is not null and partner_id::text in ('{pl}')", AUDIT))
# ---- 3) audit approval (consents) ----
appr = set(str(r["partner_id"]) for r in sb(f"select distinct partner_id from consents where agreed_at is not null and partner_id in ('{pl}')", APPROVAL))
# ---- 4) city (SUPPLY_MODEL) ----
city = {str(r[0]): (r[1] or "") for r in mb(f"select PARTNER_ACCOUNT_ID::text, ANY_VALUE(CITY) from PROD_DB.PUBLIC.SUPPLY_MODEL where PARTNER_ACCOUNT_ID::text in ('{pl}') group by 1")}
print(f"audit done={sum(1 for p in pids if p in done)} | approved={sum(1 for p in pids if p in appr)} | city={sum(1 for p in pids if city.get(p))}", flush=True)

# ---- 5) build + write ----
yn = lambda b: "Yes" if b else "No"
stamp = datetime.datetime.now(IST).strftime("%d-%b %H:%M IST")
hdr = ["Partner ID", "CSP Name", "City", "Owner Mobile", "Admin Mobile", "Audit done?", "Audit approval?", f"(updated {stamp})"]
grid = [hdr]
for r in rows:
    p = str(r["partner_account_id"]).strip()
    grid.append([p, r.get("csp_name", ""), city.get(p, ""), r.get("owner_mobile", ""), r.get("admin_mobile", ""),
                 yn(p in done), yn(p in appr), ""])

import gspread
from google.oauth2.service_account import Credentials
_SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
_sa = os.environ.get("GOOGLE_SA_JSON")
creds = (Credentials.from_service_account_info(json.loads(_sa), scopes=_SCOPES) if _sa
         else Credentials.from_service_account_file(r"C:\Users\Palak Vardhan\dashboard\wiom-sheets-writer.json", scopes=_SCOPES))
sh = gspread.authorize(creds).open_by_key(SHEET)
ws = sh.get_worksheet(0)
ws.clear(); ws.resize(rows=len(grid) + 5, cols=len(hdr))
ws.update(range_name="A1", values=grid, value_input_option="RAW")
ws.format("A1:H1", {"textFormat": {"bold": True}})
print(f"WROTE '{ws.title}': {len(grid)} rows @ {stamp}", flush=True)
