# -*- coding: utf-8 -*-
"""750 opt-in tracker — V4 Flow-1 wave (campaign 1787766976, published 02-Sep), 260 CSPs.

Writes to the 750 opt-in sheet (1ap0K6GB...):
  - "Summary (V4)"    : Opted / Declined / Abandoned / Not viewed + viewed + decision rate
  - "CSP Status (V4)" : one row per CSP — status, viewed-at, decided-at, name, mobile, zone

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
START = os.environ.get("P750_V4_START", "20260902000000"); START_TS = "2026-09-02 00:00:00"
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
SUM_TAB = "Summary (V4)"; CSP_TAB = "CSP Status (V4)"

CSPS = [
        "a0a6y5", "a0a6z7", "a0a6z8", "a0a7a0", "a0a7a7", "a0a7a9", "a0a7b6", "a0a7b7", "a0a7c3", "a0a7c6", "a0a7d3",
        "a0a7d4", "a0a7d5", "a0a7d8", "a0a7e7", "a0a7e9", "a0a7g5", "a0a7h1", "a0a7h2", "a0a8a5", "a0b1b5", "a0b1u5",
        "a0b5m0", "a0b5n4", "a0b5n6", "a0b5o0", "a0b5o2", "a0b5o4", "a0b5o5", "a0b5o8", "a0b5p5", "a0b5p7", "a0b5q2",
        "a0b5r2", "a0b5s6", "a0b5t5", "a0b5u1", "a0b5u7", "a0b5u8", "a0b5v3", "a0b5v6", "a0b5v7", "a0b5v8", "a0b5w1",
        "a0b5w8", "a0b5x0", "a0b5x1", "a0b5x3", "a0b5y3", "a0b5y6", "a0b5z0", "a0b5z3", "a0b5z7", "a0b6a3", "a0b6a6",
        "a0b6a9", "a0b6b0", "a0b6b1", "a0b6b3", "a0b6b5", "a0b6b8", "a0b6c1", "a0b6c4", "a0b6c5", "a0b6d0", "a0b6d1",
        "a0b6e2", "a0b6e4", "a0b6e6", "a0b6e7", "a0b6e8", "a0b6e9", "a0b6f0", "a0b6f6", "a0b6f9", "a0b6g1", "a0b6g2",
        "a0b6h2", "a0b6h3", "a0b6h4", "a0b6h5", "a0b6h6", "a0b6h7", "a0b6h8", "a0b6h9", "a0b6i2", "a0b6i4", "a0b6j2",
        "a0b6j7", "a0b6j8", "a0b6k9", "a0b6l0", "a0b6l2", "a0b6l3", "a0b6m3", "a0b6m6", "a0b6n2", "a0b6n3", "a0b6o1",
        "a0b6o2", "a0b6p0", "a0b6p1", "a0b6p4", "a0b6p8", "a0b6p9", "a0b6q3", "a0b6q6", "a0b6q9", "a0b6r4", "a0b6r5",
        "a0b6r6", "a0b6r8", "a0b6s4", "a0b6s7", "a0b6t4", "a0b6t5", "a0b6t7", "a0b6t9", "a0b6u0", "a0b6u3", "a0b6u4",
        "a0b6u8", "a0b6v1", "a0b6v4", "a0b6v6", "a0b6v7", "a0b6w0", "a0b6w2", "a0b6w8", "a0b6x2", "a0b6x3", "a0b6x5",
        "a0b6x9", "a0b6y5", "a0b6y6", "a0b6y7", "a0b6z4", "a0b6z7", "a0b6z9", "a0b7a2", "a0b7a6", "a0b7a7", "a0b7b1",
        "a0b7b3", "a0b7b5", "a0b7b7", "a0b7c0", "a0b7c1", "a0b7c2", "a0b7c3", "a0b7c4", "a0b7d0", "a0b7e1", "a0b7e2",
        "a0b7f3", "a0b7f4", "a0b7g1", "a0b7g4", "a0b7g6", "a0b7h4", "a0b7h8", "a0b7i0", "a0b7i3", "a0b7i6", "a0b7j1",
        "a0b7j2", "a0b7j3", "a0b8o3", "a0b8o4", "a0b8o6", "a0b8o7", "a0b8o8", "a0b8p0", "a0b8p6", "a0b8p8", "a0b8q1",
        "a0b8r9", "a0b8v8", "a0b8w1", "a0b8w6", "a0b8w8", "a0b8x6", "a0b8y3", "a0b8y8", "a0b8z4", "a0b9a9", "a0b9c2",
        "a0b9d3", "a0b9d5", "a0b9d6", "a0b9f1", "a0b9g4", "a0b9h3", "a0b9h6", "a0b9h8", "a0b9h9", "a0b9i0", "a0b9i5",
        "a0b9i9", "a0b9j5", "a0b9j7", "a0b9k4", "a0b9l2", "a0b9l3", "a0b9l6", "a0b9l8", "a0b9m1", "a0b9m2", "a0b9m3",
        "a0b9m4", "a0b9n2", "a0b9n5", "a0b9n6", "a0b9n7", "a0b9n8", "a0b9p9", "a0b9q0", "a0b9r4", "a0b9r9", "a0b9s4",
        "a0b9u0", "a0b9v9", "a0b9w3", "a0b9x1", "a0b9y0", "a0b9y2", "a0b9y5", "a0b9y6", "a0b9y8", "a0b9z2", "a0b9z3",
        "a0b9z4", "a0b9z6", "a0b9z7", "a0c0a0", "a0c0a1", "a0c0a2", "a0c0a9", "a0c0b0", "a0c0b4", "a0c0b5", "a0c0b8",
        "a0c0c0", "a0c0c7", "a0c0c9", "a0c0d2", "a0c0e2", "a0c0e6", "a0c0e7", "a0c0e9", "a0c0f0", "a0c0f3", "a0c0f6",
        "a0c0f7", "a0c0g0", "a0c0g1", "a0c0g7", "a0c0g8", "a0c0h2", "a0c0h3"
]


def mb(sql):
    body = json.dumps({"database": 113, "type": "native", "native": {"query": sql}}).encode()
    d = json.loads(urllib.request.urlopen(urllib.request.Request(
        "https://metabase.wiom.in/api/dataset", data=body,
        headers={"x-api-key": MB_KEY, "Content-Type": "application/json"}), timeout=180).read().decode())
    if isinstance(d, dict) and d.get("error"): raise SystemExit("MB ERROR: " + str(d["error"])[:300])
    return d["data"]["rows"]


def export(ev):
    tom = int((datetime.datetime.now() + datetime.timedelta(days=1)).strftime("%Y%m%d"))
    body = json.dumps({"event_name": ev, "from": 20260902, "to": tom}).encode()
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
        [f'750 opt-in — V4 Flow-1 wave  |  campaign 1787766976, published 02-Sep  |  {len(CSPS)} CSPs  |  updated {now:%d-%b %H:%M IST}'],
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
    note = [f"Per-CSP status — V4 Flow-1 wave (campaign 1787766976) — {len(CSPS)} CSPs — updated {now:%d-%b %H:%M IST}  |  Opted {nopt} · Declined {ndec} · Abandoned {nab} · Not viewed {nnv}"]
    out = [note, [], head] + body
    try: ws2 = ss.worksheet(CSP_TAB); ws2.clear()
    except gspread.WorksheetNotFound: ws2 = ss.add_worksheet(CSP_TAB, rows=max(120, len(out) + 10), cols=8)
    ws2.update(values=out, range_name="A1", value_input_option="RAW")
    ws2.format("A1:H1", {"textFormat": {"bold": True, "fontSize": 10}, "wrapStrategy": "WRAP"})
    ws2.format("A3:H3", {"textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}}, "backgroundColor": {"red": .85, "green": 0, "blue": .55}})
    print(f"wrote '{SUM_TAB}' + '{CSP_TAB}': opted={nopt} declined={ndec} abandoned={nab} not_viewed={nnv} viewed={nv} | {now:%H:%M IST}", flush=True)


if __name__ == "__main__":
    main()
