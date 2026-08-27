# -*- coding: utf-8 -*-
"""750 opt-in tracker — 27-Aug campaign (live 27-Aug 10:30 AM), 92 CSPs.

Writes to the 750 opt-in sheet (1ap0K6GB...):
  - "Summary (27-Aug)"    : Opted / Declined / Abandoned / Not viewed + viewed + decision rate
  - "CSP Status (27-Aug)" : one row per CSP — status, viewed-at, decided-at, name, mobile, zone

Opt-ins = backend DOMINANCE_CONSENT (real-time, all channels) + banner Confirmed/Closed-new;
declines = Payout750_Declined/Closed-later; abandoned = viewed, no decision; scoped since go-live.

Env: CT_PASS, GOOGLE_SA_JSON (CI) or local SA. Idempotent.
"""
import os, sys, json, tempfile, urllib.request, datetime
sys.stdout.reconfigure(encoding="utf-8")
import gspread
from google.oauth2 import service_account

CT_ACC = "44Z-644-777Z"; CT_PASS = os.environ.get("CT_PASS") or os.environ.get("CLEVERTAP_PASSCODE")
CT = "https://eu1.api.clevertap.com"
HP = {"X-CleverTap-Account-Id": CT_ACC, "X-CleverTap-Passcode": CT_PASS, "Content-Type": "application/json; charset=utf-8"}
HG = {"X-CleverTap-Account-Id": CT_ACC, "X-CleverTap-Passcode": CT_PASS}
MB_KEY = os.environ.get("METABASE_KEY", "mb_1dsbxsJfyROPsVyNpifJ8hTTlIDG85+qNKRo91KDnb4=")
SHEET_ID = "1ap0K6GB6RijeLHRWPf9N84U0cl1XEJs67DSQBql7sGQ"
DESIGN_SHEET_ID = "1SfWil0SaN1lKPTqtTF86edoPk8lT1BxzNzB6vC0_4Yc"
START = os.environ.get("P750_AUG27_START", "20260827103000"); START_TS = "2026-08-27 10:30:00"
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
SUM_TAB = "Summary (27-Aug)"; CSP_TAB = "CSP Status (27-Aug)"

CSPS = ["a0c0e1","a0b9l1","a0b0n9","a0b9w5","a0b8v5","a0b8x4","a0a7b3","a0b6e0","a0b5s4","a0b7d7","a0a7d9",
        "a0b5m5","a0b8w9","a0b5x8","a0b9t2","a0b6l1","a0b6t8","a0b8q4","a0b6o0","a0a7c0","a0b9x3","a0b6r2",
        "a0b5m1","a0a6z0","a0a6y6","a0a6y4","a0b5m9","a0b9v8","a0a6z4","a0b9x7","a0b5n8","a0b9s6","a0b6o9",
        "a0b6q5","a0a7b0","a0a7a2","a0b0w0","a0b3m4","a0a7b8","a0a7b9","a0a7c1","a0a7c2","a0a7f4","a0a7f8",
        "a0b8r1","a0b8s5","a0b8s8","a0b8t5","a0b8v4","a0b8w3","a0b8w5","a0b8w7","a0b8x0","a0b8y9","a0b8z1",
        "a0b9a0","a0b9a2","a0b9b5","a0b9c0","a0b9c9","a0b9d9","a0b9e3","a0b9f6","a0b9g3","a0b9i3","a0b9j3",
        "a0b9k9","a0b9m7","a0b9p2","a0b9q7","a0b9r0","a0b9s2","a0b9t7","a0b6x4","a0b6y1","a0b6y8","a0b7c6",
        "a0b7d1","a0b7d8","a0b5z6","a0b6b4","a0b6d3","a0b6g7","a0b5q8","a0b5s1","a0b9v7","a0b9w7","a0b9y1",
        "a0b9y4","a0c0c1","a0b6i0","a0b6p2"]


def mb(sql):
    body = json.dumps({"database": 113, "type": "native", "native": {"query": sql}}).encode()
    d = json.loads(urllib.request.urlopen(urllib.request.Request(
        "https://metabase.wiom.in/api/dataset", data=body,
        headers={"x-api-key": MB_KEY, "Content-Type": "application/json"}), timeout=180).read().decode())
    if isinstance(d, dict) and d.get("error"): raise SystemExit("MB ERROR: " + str(d["error"])[:300])
    return d["data"]["rows"]


def export(ev):
    tom = int((datetime.datetime.now() + datetime.timedelta(days=1)).strftime("%Y%m%d"))
    body = json.dumps({"event_name": ev, "from": 20260827, "to": tom}).encode()
    try:
        d = json.loads(urllib.request.urlopen(urllib.request.Request(CT + "/1/events.json?batch_size=1000", data=body, headers=HP), timeout=90).read().decode())
    except Exception as e:
        print("  export failed", ev, str(e)[:80]); return []
    cur = d.get("cursor"); recs = []; p = 0
    while cur and p < 300:
        dd = json.loads(urllib.request.urlopen(urllib.request.Request(CT + "/1/events.json?cursor=" + cur, headers=HG), timeout=90).read().decode())
        recs += dd.get("records", []); cur = dd.get("next_cursor"); p += 1
        if not cur: break
    return recs


def ev_ts(ev, ch=None):
    m = {}; S = set(CSPS)
    for r in export(ev):
        ts = str(r.get("ts", ""))
        if ts < START: continue
        if ch and (r.get("event_props", {}) or {}).get("choice") != ch: continue
        c = (r.get("profile", {}).get("profileData", {}) or {}).get("cspid", "")
        if c in S and (c not in m or ts < m[c]): m[c] = ts
    return m


def fmt(ts):
    try: return datetime.datetime.strptime(ts, "%Y%m%d%H%M%S").strftime("%d-%b %H:%M")
    except Exception: return ""


def creds():
    raw = os.environ.get("GOOGLE_SA_JSON", "")
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    if raw:
        t = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False); t.write(raw); t.close()
        return service_account.Credentials.from_service_account_file(t.name, scopes=scopes)
    for p in (r"C:\Users\Palak Vardhan\Desktop\mbg\mbg-cron\wiom-sheets-writer.json",
              r"C:\Users\Palak Vardhan\dashboard\wiom-sheets-writer.json"):
        if os.path.exists(p): return service_account.Credentials.from_service_account_file(p, scopes=scopes)
    raise SystemExit("no SA creds")


def mob(p):
    p = (p or "").replace("+", "").strip()
    return p[2:] if p.startswith("91") and len(p) == 12 else p


def main():
    gc = gspread.authorize(creds())
    inlist = ",".join("'%s'" % c for c in CSPS)
    be = {r[0]: r[1] for r in mb(
        f"SELECT CSP_ID, TO_CHAR(CONVERT_TIMEZONE('Asia/Kolkata',CONSENT_TIMESTAMP),'DD-Mon HH24:MI') "
        f"FROM PROD_DB.CSP_RV_SERVICE_CSP_RV_SERVICE.DOMINANCE_CONSENT "
        f"WHERE CONSENT_CHOICE='OPTED_IN' AND COALESCE(_FIVETRAN_DELETED,FALSE)=FALSE AND CSP_ID IN ({inlist}) "
        f"AND CONSENT_TIMESTAMP >= '{START_TS} +05:30'::timestamp_tz")}
    viewed = ev_ts("Payout750_Viewed")
    conf = ev_ts("Payout750_Confirmed"); clo_new = ev_ts("Payout750_Closed", "new")
    dec = ev_ts("Payout750_Declined"); clo_later = ev_ts("Payout750_Closed", "later")
    opt_ts, dec_ts = {}, {}
    for c in CSPS:
        cands = [t for t in (be.get(c), conf.get(c) and fmt(conf[c]), clo_new.get(c) and fmt(clo_new[c])) if t]
        if cands: opt_ts[c] = cands[0]
        dcs = [fmt(t) for t in (dec.get(c), clo_later.get(c)) if t]
        if dcs: dec_ts[c] = dcs[0]
    opted = set(opt_ts); declined = set(dec_ts) - opted
    abandoned = set(viewed) - opted - declined
    not_viewed = set(CSPS) - opted - declined - set(viewed)

    d = gc.open_by_key(DESIGN_SHEET_ID).worksheet("Design").get_all_values()
    H = d[0]; ci = H.index("CSP ID"); ni = H.index("Partner Name")
    name = {r[ci].strip(): r[ni].strip() for r in d[1:] if len(r) > max(ci, ni) and r[ci].strip()}
    acct = {r[0]: (mob(str(r[1] or "")), (str(r[2] or "").split(",")[0].strip()))
            for r in mb(f"SELECT CSP_ID, MOBILE_NUMBER, LOGICAL_GROUP FROM PROD_DB.CSP_GATEWAY_SERVICE_CSP_GATEWAY_SERVICE.CSP_ACCOUNT WHERE CSP_ID IN ({inlist}) AND _FIVETRAN_ACTIVE=TRUE")}

    now = datetime.datetime.now(IST); ss = gc.open_by_key(SHEET_ID)
    nopt, ndec, nab, nnv, nv = len(opted), len(declined), len(abandoned), len(not_viewed), len(viewed)
    deciders = nopt + ndec
    def pct(x): return f"{round(100*x/len(CSPS))}%" if CSPS else "-"
    summ = [
        [f'750 opt-in — 27-Aug campaign  |  live 27-Aug 10:30 AM  |  {len(CSPS)} CSPs  |  updated {now:%d-%b %H:%M IST}'],
        [], ["STATUS (unique CSPs)", "count", "% of audience"],
        ["Opted in", nopt, pct(nopt)], ["Declined", ndec, pct(ndec)],
        ["Abandoned (viewed, no decision)", nab, pct(nab)], ["Not viewed yet", nnv, pct(nnv)],
        ["TOTAL", len(CSPS), "100%"], [],
        ["Viewed at least once", nv, pct(nv)], ["Deciders (opted + declined)", deciders, ""],
        ["Opt-in rate among deciders", f"{round(100*nopt/deciders)}%" if deciders else "-", ""],
    ]
    try: ws = ss.worksheet(SUM_TAB); ws.clear()
    except gspread.WorksheetNotFound: ws = ss.add_worksheet(SUM_TAB, rows=30, cols=4)
    ws.update(values=summ, range_name="A1", value_input_option="RAW")
    ws.format("A1:D1", {"textFormat": {"bold": True, "fontSize": 10}, "wrapStrategy": "WRAP"})
    ws.format("A3:C3", {"textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}}, "backgroundColor": {"red": .85, "green": 0, "blue": .55}})
    ws.format("A8:C8", {"textFormat": {"bold": True}})

    def row(c, status, viewed_at, decided_at, decision):
        m, z = acct.get(c, ("", "")); return [c, name.get(c, ""), m, z, status, viewed_at, decided_at, decision]
    body = []
    for c in sorted(opted):      body.append(row(c, "Opted in", fmt(viewed[c]) if c in viewed else "", opt_ts[c], "Opted in"))
    for c in sorted(declined):   body.append(row(c, "Declined", fmt(viewed[c]) if c in viewed else "", dec_ts[c], "Declined"))
    for c in sorted(abandoned):  body.append(row(c, "Abandoned", fmt(viewed[c]), "", "no decision yet"))
    for c in sorted(not_viewed): body.append(row(c, "Not viewed yet", "", "", ""))
    head = ["CSP ID", "Name", "Mobile", "Zone", "Status", "Viewed at (IST)", "Decided at (IST)", "Decision"]
    note = [f"Per-CSP status — 27-Aug campaign — {len(CSPS)} CSPs — updated {now:%d-%b %H:%M IST}  |  Opted {nopt} · Declined {ndec} · Abandoned {nab} · Not viewed {nnv}"]
    out = [note, [], head] + body
    try: ws2 = ss.worksheet(CSP_TAB); ws2.clear()
    except gspread.WorksheetNotFound: ws2 = ss.add_worksheet(CSP_TAB, rows=max(120, len(out) + 10), cols=8)
    ws2.update(values=out, range_name="A1", value_input_option="RAW")
    ws2.format("A1:H1", {"textFormat": {"bold": True, "fontSize": 10}, "wrapStrategy": "WRAP"})
    ws2.format("A3:H3", {"textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}}, "backgroundColor": {"red": .85, "green": 0, "blue": .55}})
    print(f"wrote '{SUM_TAB}' + '{CSP_TAB}': opted={nopt} declined={ndec} abandoned={nab} not_viewed={nnv} viewed={nv} | {now:%H:%M IST}", flush=True)


if __name__ == "__main__":
    main()
