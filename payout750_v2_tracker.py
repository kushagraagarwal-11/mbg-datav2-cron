# -*- coding: utf-8 -*-
"""750 opt-in V2 tracker — campaign "750 opt in Flow 1 Positive - V2" (1787720389, live 26-Aug 10:29 AM).

Writes TWO tabs to the 750 opt-in sheet (1ap0K6GB...):
  - "Summary (V2)"    : Opted in / Declined / Abandoned / Not viewed + viewed + decision rate
  - "CSP Status (V2)" : one row per CSP — status, viewed-at, decided-at, name, mobile, zone

Scoped to V2's 42 CSPs and events since go-live. Opt-ins = backend DOMINANCE_CONSENT (real-time,
all channels) + banner Confirmed/Closed-new; declines = Payout750_Declined/Closed-later;
abandoned = viewed but no decision; not-viewed = never fired Payout750_Viewed.

Env: CT_PASS, GOOGLE_SA_JSON (CI) or local SA. Idempotent (clear + rewrite).
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
CAMP = "1787720389"; START = "20260826102900"; START_TS = "2026-08-26 10:29:00"
# abandoned re-pitch ("750 opt in Flow 1 Positive - abandoned") go-live — a view at/after this
# by one of the 16 = they saw the banner AGAIN.
REPITCH_FROM = os.environ.get("P750_REPITCH_FROM", "20260826141500")
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
SUM_TAB = "Summary (V2)"; CSP_TAB = "CSP Status (V2)"

CSPS = ["a0a7g1","a0b7h5","a0b5q7","a0b6c6","a0a7a3","a0a7e3","a0a7a6","a0a7a5","a0b5r9","a0b6u2",
        "a0b7i7","a0a7g6","a0b8u9","a0b5w4","a0b5n2","a0a7h0","a0b0a0","a0a7g0","a0b6x8","a0b6w1",
        "a0b9m6","a0b9x8","a0a6y9","a0b8r5","a0a6z1","a0c0i5","a0b8z0","a0b6t0","a0b8z7","a0b9v6",
        "a0b8y0","a0b5m3","a0b9b9","a0b9g8","a0b8s3","a0b6d8","a0c0f1","a0b9h5","a0b9g7","a0b8s0",
        "a0c0a5","a0b8r2"]

# the 16 that ABANDONED V2 (viewed, no decision) and are being re-pitched via the
# "750 opt in Flow 1 Positive - abandoned" campaign — tracked as a second summary block.
ABANDONED16 = {"a0a6y9","a0a7e3","a0a7g0","a0a7g6","a0a7h0","a0b5n2","a0b5q7","a0b5w4","a0b6d8",
               "a0b6x8","a0b8y0","a0b8z7","a0b9b9","a0b9v6","a0b9x8","a0c0a5"}


def mb(sql):
    body = json.dumps({"database": 113, "type": "native", "native": {"query": sql}}).encode()
    d = json.loads(urllib.request.urlopen(urllib.request.Request(
        "https://metabase.wiom.in/api/dataset", data=body,
        headers={"x-api-key": MB_KEY, "Content-Type": "application/json"}), timeout=180).read().decode())
    if isinstance(d, dict) and d.get("error"): raise SystemExit("MB ERROR: " + str(d["error"])[:300])
    return d["data"]["rows"]


def export(ev):
    tom = int((datetime.datetime.now() + datetime.timedelta(days=1)).strftime("%Y%m%d"))
    body = json.dumps({"event_name": ev, "from": 20260826, "to": tom}).encode()
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
    """{cspid: earliest ts >= START} for event ev (optional choice filter), CSP in scope."""
    m = {}
    S = set(CSPS)
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


def viewed_since(cutoff, scope):
    """cspids in `scope` that fired Payout750_Viewed at/after `cutoff`."""
    s = set()
    for r in export("Payout750_Viewed"):
        if str(r.get("ts", "")) < cutoff: continue
        c = (r.get("profile", {}).get("profileData", {}) or {}).get("cspid", "")
        if c in scope: s.add(c)
    return s


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

    # backend opt-ins (real-time) with IST time, since go-live
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

    # names (Design sheet) + mobile/zone (CSP_ACCOUNT)
    d = gc.open_by_key(DESIGN_SHEET_ID).worksheet("Design").get_all_values()
    H = d[0]; ci = H.index("CSP ID"); ni = H.index("Partner Name")
    name = {r[ci].strip(): r[ni].strip() for r in d[1:] if len(r) > max(ci, ni) and r[ci].strip()}
    acct = {r[0]: (mob(str(r[1] or "")), (str(r[2] or "").split(",")[0].strip()))
            for r in mb(f"SELECT CSP_ID, MOBILE_NUMBER, LOGICAL_GROUP FROM PROD_DB.CSP_GATEWAY_SERVICE_CSP_GATEWAY_SERVICE.CSP_ACCOUNT WHERE CSP_ID IN ({inlist}) AND _FIVETRAN_ACTIVE=TRUE")}

    now = datetime.datetime.now(IST)
    ss = gc.open_by_key(SHEET_ID)

    # ---------- Summary (V2) ----------
    nopt, ndec, nab, nnv, nv = len(opted), len(declined), len(abandoned), len(not_viewed), len(viewed)
    deciders = nopt + ndec
    def pct(x): return f"{round(100*x/len(CSPS))}%" if CSPS else "-"
    summ = [
        [f'750 opt in Flow 1 Positive - V2  |  campaign {CAMP}  |  live 26-Aug 10:29 AM  |  {len(CSPS)} CSPs  |  updated {now:%d-%b %H:%M IST}'],
        [],
        ["STATUS (unique CSPs)", "count", "% of audience"],
        ["Opted in", nopt, pct(nopt)],
        ["Declined", ndec, pct(ndec)],
        ["Abandoned (viewed, no decision)", nab, pct(nab)],
        ["Not viewed yet", nnv, pct(nnv)],
        ["TOTAL", len(CSPS), "100%"],
        [],
        ["Viewed at least once", nv, pct(nv)],
        ["Deciders (opted + declined)", deciders, ""],
        ["Opt-in rate among deciders", f"{round(100*nopt/deciders)}%" if deciders else "-", ""],
    ]
    # second block — the 16 abandoned CSPs re-pitched via the "abandoned" campaign
    ab_opt = len(opted & ABANDONED16); ab_dec = len(declined & ABANDONED16)
    ab_still = len(ABANDONED16) - ab_opt - ab_dec
    ab_reviewed = len(viewed_since(REPITCH_FROM, ABANDONED16))
    def pab(x): return f"{round(100*x/len(ABANDONED16))}%" if ABANDONED16 else "-"
    summ += [
        [],
        [f"ABANDONED RE-PITCH — {len(ABANDONED16)} CSPs (viewed V2, no decision) shown again", "count", "% of 16"],
        ["Viewed the re-pitch (again)", ab_reviewed, pab(ab_reviewed)],
        ["Opted in (converted)", ab_opt, pab(ab_opt)],
        ["Declined (now decided)", ab_dec, pab(ab_dec)],
        ["Still abandoned (no decision yet)", ab_still, pab(ab_still)],
    ]
    try: ws = ss.worksheet(SUM_TAB); ws.clear()
    except gspread.WorksheetNotFound: ws = ss.add_worksheet(SUM_TAB, rows=30, cols=4)
    ws.update(values=summ, range_name="A1", value_input_option="RAW")
    ws.format("A1:D1", {"textFormat": {"bold": True, "fontSize": 10}, "wrapStrategy": "WRAP"})
    pink = {"textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}}, "backgroundColor": {"red": .85, "green": 0, "blue": .55}}
    ws.format("A3:C3", pink)
    ws.format("A8:C8", {"textFormat": {"bold": True}})
    ab_hdr = next((i for i, r in enumerate(summ) if r and str(r[0]).startswith("ABANDONED RE-PITCH")), None)
    if ab_hdr is not None: ws.format(f"A{ab_hdr+1}:C{ab_hdr+1}", pink)

    # ---------- CSP Status (V2) ----------
    def row(c, status, viewed_at, decided_at, decision):
        m, z = acct.get(c, ("", ""))
        return [c, name.get(c, ""), m, z, status, viewed_at, decided_at, decision]
    body = []
    for c in sorted(opted):      body.append(row(c, "Opted in", fmt(viewed[c]) if c in viewed else "", opt_ts[c], "Opted in"))
    for c in sorted(declined):   body.append(row(c, "Declined", fmt(viewed[c]) if c in viewed else "", dec_ts[c], "Declined"))
    for c in sorted(abandoned):  body.append(row(c, "Abandoned", fmt(viewed[c]), "", "no decision yet"))
    for c in sorted(not_viewed): body.append(row(c, "Not viewed yet", "", "", ""))
    head = ["CSP ID", "Name", "Mobile", "Zone", "Status", "Viewed at (IST)", "Decided at (IST)", "Decision"]
    note = [f"Per-CSP status for V2 ({CAMP}) — {len(CSPS)} CSPs — updated {now:%d-%b %H:%M IST}  |  Opted {nopt} · Declined {ndec} · Abandoned {nab} · Not viewed {nnv}"]
    out = [note, [], head] + body
    try: ws2 = ss.worksheet(CSP_TAB); ws2.clear()
    except gspread.WorksheetNotFound: ws2 = ss.add_worksheet(CSP_TAB, rows=max(60, len(out) + 10), cols=8)
    ws2.update(values=out, range_name="A1", value_input_option="RAW")
    ws2.format("A1:H1", {"textFormat": {"bold": True, "fontSize": 10}, "wrapStrategy": "WRAP"})
    ws2.format("A3:H3", {"textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}}, "backgroundColor": {"red": .85, "green": 0, "blue": .55}})

    print(f"wrote '{SUM_TAB}' + '{CSP_TAB}': opted={nopt} declined={ndec} abandoned={nab} not_viewed={nnv} viewed={nv} | {now:%H:%M IST}", flush=True)


if __name__ == "__main__":
    main()
