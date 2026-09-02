# -*- coding: utf-8 -*-
"""Live-refresh ONLY columns K:N of "Delhi CSPs — Bonus & 750 Status".

K = 750 campaign delivered? (ever shown)   L = 750 opt-in status
M = Times seen (banner)                    N = Distinct days seen

Reads col A (CSP IDs, rows 4+) to keep the existing row order, recomputes the four
live 750 signals from the backend consent table + CleverTap events, and writes ONLY K4:N.
Does NOT touch A:J (bonus snapshot / reachability) or O:Q (audit/consent/eligibility).

Env: CT_PASS, GOOGLE_SA_JSON (CI) or local SA. Idempotent.
"""
import os, sys, json, tempfile, urllib.request, datetime
from collections import Counter, defaultdict
sys.stdout.reconfigure(encoding="utf-8")
import gspread
from google.oauth2 import service_account

CT_ACC = "44Z-644-777Z"; CT_PASS = os.environ.get("CT_PASS") or os.environ.get("CLEVERTAP_PASSCODE")
CT = "https://eu1.api.clevertap.com"
HP = {"X-CleverTap-Account-Id": CT_ACC, "X-CleverTap-Passcode": CT_PASS, "Content-Type": "application/json; charset=utf-8"}
HG = {"X-CleverTap-Account-Id": CT_ACC, "X-CleverTap-Passcode": CT_PASS}
MB_KEY = os.environ.get("METABASE_KEY", "mb_1dsbxsJfyROPsVyNpifJ8hTTlIDG85+qNKRo91KDnb4=")
SHEET_ID = "1ap0K6GB6RijeLHRWPf9N84U0cl1XEJs67DSQBql7sGQ"
TAB = "Delhi CSPs — Bonus & 750 Status"
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]


def mb(sql):
    body = json.dumps({"database": 113, "type": "native", "native": {"query": sql}}).encode()
    d = json.loads(urllib.request.urlopen(urllib.request.Request(
        "https://metabase.wiom.in/api/dataset", data=body,
        headers={"x-api-key": MB_KEY, "Content-Type": "application/json"}), timeout=120).read().decode())
    if isinstance(d, dict) and d.get("error"): raise SystemExit("MB ERR " + str(d["error"])[:200])
    return d["data"]["rows"]


def export(ev):
    body = json.dumps({"event_name": ev, "from": 20260818, "to": 20260903}).encode()
    d = json.loads(urllib.request.urlopen(urllib.request.Request(CT + "/1/events.json?batch_size=1000", data=body, headers=HP), timeout=90).read().decode())
    cur = d.get("cursor"); recs = []; p = 0
    while cur and p < 500:
        dd = json.loads(urllib.request.urlopen(urllib.request.Request(CT + "/1/events.json?cursor=" + cur, headers=HG), timeout=90).read().decode())
        recs += dd.get("records", []); cur = dd.get("next_cursor"); p += 1
        if not cur: break
    return recs


def cspset(ev, ch=None, scope=None):
    s = set()
    for r in export(ev):
        if ch and (r.get("event_props", {}) or {}).get("choice") != ch: continue
        c = (r.get("profile", {}).get("profileData", {}) or {}).get("cspid", "")
        if c and (scope is None or c in scope): s.add(c)
    return s


def creds():
    raw = os.environ.get("GOOGLE_SA_JSON", "")
    if raw:
        t = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False); t.write(raw); t.close()
        return service_account.Credentials.from_service_account_file(t.name, scopes=SCOPES)
    for p in (r"C:\Users\Palak Vardhan\Desktop\mbg\mbg-cron\wiom-sheets-writer.json",
              r"C:\Users\Palak Vardhan\dashboard\wiom-sheets-writer.json"):
        if os.path.exists(p): return service_account.Credentials.from_service_account_file(p, scopes=SCOPES)
    raise SystemExit("no SA creds")


def main():
    gc = gspread.authorize(creds())
    w = gc.open_by_key(SHEET_ID).worksheet(TAB)
    colA = w.col_values(1)                          # 1-based; header at row 3, data row 4+
    # rows 4..N hold CSP IDs
    csp_rows = [(i + 1, v.strip()) for i, v in enumerate(colA) if i >= 3 and v.strip().startswith("a0")]
    CSPS = {c for _, c in csp_rows}
    if not CSPS: raise SystemExit("no CSP rows found")
    il = ",".join("'%s'" % c for c in CSPS)

    opted = {r[0] for r in mb(f"SELECT DISTINCT CSP_ID FROM PROD_DB.CSP_RV_SERVICE_CSP_RV_SERVICE.DOMINANCE_CONSENT WHERE CONSENT_CHOICE='OPTED_IN' AND COALESCE(_FIVETRAN_DELETED,FALSE)=FALSE AND CSP_ID IN ({il})")}
    confirmed = (cspset("Payout750_Confirmed", scope=CSPS) | cspset("Payout750_Closed", "new", scope=CSPS))
    attempted = confirmed - opted
    declined = (cspset("Payout750_Declined", scope=CSPS) | cspset("Payout750_Closed", "later", scope=CSPS)) - opted - attempted
    # views (count + distinct days)
    vc = Counter(); vd = defaultdict(set)
    for r in export("Payout750_Viewed"):
        c = (r.get("profile", {}).get("profileData", {}) or {}).get("cspid", "")
        if c in CSPS: vc[c] += 1; vd[c].add(str(r.get("ts", ""))[:8])
    viewed = set(vc)
    reachable = {r[0] for r in mb(f"SELECT DISTINCT CSPID FROM PROD_DB.CLEVERTAP_CSP_API.PROFILE_DATA WHERE CSPID IN ({il}) AND COALESCE(_FIVETRAN_DELETED,FALSE)=FALSE AND IDENTITY IS NOT NULL AND LOWER(ROLE) IN ('owner','admin','manager','manager_plus')")}
    exposed = opted | attempted | declined | viewed

    def status(c):
        if c in opted: return "Opted in"
        if c in attempted: return "Attempted — consent NOT signed"
        if c in declined: return "Declined"
        if c in viewed: return "Abandoned (viewed, no decision)"
        if c not in reachable: return "Not reachable (no app profile)"
        return "Not viewed yet"

    def delivered(c):
        return "Yes" if c in exposed else ("No - not reachable" if c not in reachable else "No - never shown")

    # build K:N values in the EXACT row order of the sheet
    first = csp_rows[0][0]; last = csp_rows[-1][0]
    by_row = {row: c for row, c in csp_rows}
    values = []
    for row in range(first, last + 1):
        c = by_row.get(row)
        if c:
            values.append([delivered(c), status(c), (vc[c] if vc[c] else ""), (len(vd[c]) if vd[c] else "")])
        else:
            values.append(["", "", "", ""])         # non-CSP row (blank) — leave K:N blank
    now = datetime.datetime.now(IST)
    # write ONLY K:N — data block, plus a freshness marker in K2 (row 2 is blank; K2..N2 within K:N)
    w.update(values=values, range_name=f"K{first}:N{last}", value_input_option="RAW")
    w.update(values=[[f"↻ K–N live-updated {now:%d-%b %H:%M IST}", "", "", ""]], range_name="K2:N2", value_input_option="RAW")
    st = Counter(status(c) for c in CSPS)
    print(f"updated K{first}:N{last} ({len(csp_rows)} CSPs) | {dict(st)} | {now:%H:%M IST}", flush=True)


if __name__ == "__main__":
    main()
