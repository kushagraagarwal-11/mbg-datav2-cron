# -*- coding: utf-8 -*-
"""Sehat MG Live — daily refresh of optical-power / service-SLA for ENROLLED Sehat-MG CSPs.

Enrolled = fired the CleverTap `Sehat_OptIn` event (pulled live each run via the Events
Export API; CSP phone comes off that same profile). Cohort + intervention type from the
'Sehat MG' tab. Metric per CSP by intervention: Optical-Power -> T1_OOR_RATE (optical-OK %,
high=good); Service-SLA -> M3_TAT_PASS_RATE (4h-SLA on-time %). Both tracks earn Rs10,000
at >=80% (Sehat MG Quality Program v2.0), so "Target met" = current % >= 80. Baseline =
16-Jul snapshot (FIXED); Today = latest snapshot; Direction = today vs 16-Jul. Writes tab
'Sehat MG Live', sorted worst+declining first, red=worsened / green=improved, green=target met.

Env: MB_KEY, GOOGLE_SA_JSON (JSON string; else local SA file), CT_PASS (or
CLEVERTAP_PASSCODE). Optional: CLEVERTAP_ACCOUNT (default set), SEHAT_SHEET_ID.
"""
import os, sys, json, time, tempfile, urllib.request, datetime
sys.stdout.reconfigure(encoding="utf-8")
import gspread
from google.oauth2 import service_account

MB_KEY   = os.environ.get("MB_KEY") or os.environ.get("METABASE_KEY")
CT_ACC   = os.environ.get("CLEVERTAP_ACCOUNT", "44Z-644-777Z")
CT_PASS  = os.environ.get("CT_PASS") or os.environ.get("CLEVERTAP_PASSCODE")
REGION   = os.environ.get("CLEVERTAP_REGION", "eu1")
SHEET_ID = os.environ.get("SEHAT_SHEET_ID", "1XHqjybQYKyfCpgdraPiL32GBm2R2wf-CguxlHalmu8M")
BASELINE = "2026-07-16"
TARGET   = 80.0
SNAP = "PROD_DB.CSP_QUALITY_SERVICE_CSP_QUALITY_SERVICE.DAILY_METRIC_SNAPSHOTS"
CT = f"https://{REGION}.api.clevertap.com"
HP = {"X-CleverTap-Account-Id": CT_ACC, "X-CleverTap-Passcode": CT_PASS, "Content-Type": "application/json; charset=utf-8"}
HG = {"X-CleverTap-Account-Id": CT_ACC, "X-CleverTap-Passcode": CT_PASS}
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def mb(q, tries=4):
    for a in range(tries):
        try:
            r = urllib.request.Request("https://metabase.wiom.in/api/dataset",
                data=json.dumps({"database": 113, "type": "native", "native": {"query": q}}).encode(),
                headers={"x-api-key": MB_KEY, "Content-Type": "application/json"})
            j = json.loads(urllib.request.urlopen(r, timeout=300).read().decode())
            if j.get("data") and j["data"].get("rows") is not None:
                return j["data"]["rows"]
        except Exception as e:
            print("  mb retry", a + 1, e, flush=True); time.sleep(6)
    raise SystemExit("Metabase failed: " + q[:120])


def creds():
    raw = os.environ.get("GOOGLE_SA_JSON", "")
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    if raw:
        t = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False); t.write(raw); t.close()
        return service_account.Credentials.from_service_account_file(t.name, scopes=scopes)
    return service_account.Credentials.from_service_account_file(
        r"C:\Users\Palak Vardhan\dashboard\wiom-sheets-writer.json", scopes=scopes)


def fmt_phone(p):
    if p is None: return ""
    s = "".join(ch for ch in str(p) if ch.isdigit())
    if len(s) == 12 and s.startswith("91"): s = s[2:]
    return s


def sehat_optin_phones():
    """Live enrolled set = {cspid: phone} for cspids that fired Sehat_OptIn (last ~90d).
    phone is the CSP's CleverTap profile number (the app owner)."""
    to = datetime.datetime.now(IST); frm = to - datetime.timedelta(days=90)
    body = json.dumps({"event_name": "Sehat_OptIn",
                       "from": int(frm.strftime("%Y%m%d")), "to": int(to.strftime("%Y%m%d"))}).encode()
    d = json.loads(urllib.request.urlopen(urllib.request.Request(CT + "/1/events.json?batch_size=1000", data=body, headers=HP), timeout=90).read().decode())
    cur = d.get("cursor"); phones = {}; pages = 0
    while cur and pages < 200:
        dd = json.loads(urllib.request.urlopen(urllib.request.Request(CT + "/1/events.json?cursor=" + cur, headers=HG), timeout=90).read().decode())
        for rec in dd.get("records", []):
            prof = rec.get("profile", {}) or {}
            cid = (prof.get("profileData", {}) or {}).get("cspid")
            if not cid: continue
            cid = str(cid).strip().lower()
            ph = fmt_phone(prof.get("phone"))
            if cid not in phones or (ph and not phones[cid]):
                phones[cid] = ph
        cur = dd.get("next_cursor"); pages += 1
        if not cur: break
    return phones


def main():
    gc = gspread.authorize(creds())
    sh = gc.open_by_key(SHEET_ID)
    sv = sh.worksheet("Sehat MG").get_all_values(); h = sv[0]
    iP, iC, iN, iI = h.index("Partner ID"), h.index("CSP ID"), h.index("Name"), h.index("Sehat Intervention")
    cohort = {r[iC].strip().lower(): (r[iP].strip(), r[iN].strip(), r[iI].strip())
              for r in sv[1:] if len(r) > iI and r[iC].strip()}
    enrolled = sehat_optin_phones()
    if len(enrolled) < 20:
        raise SystemExit(f"Sehat_OptIn returned only {len(enrolled)} — refusing to write a half-empty set")
    keep = {c: v for c, v in cohort.items() if c in enrolled}
    print(f"cohort {len(cohort)} | Sehat_OptIn firers {len(enrolled)} | enrolled-in-cohort {len(keep)}", flush=True)

    allc = "','".join(keep)
    rows = mb(f"""
      with snap as (select CSP_ID, SNAPSHOT_DATE, T1_OOR_RATE t1, M3_TAT_PASS_RATE m3,
         max(SNAPSHOT_DATE) over () latest from {SNAP}
         where lower(CSP_ID) in ('{allc}') and SNAPSHOT_DATE >= DATE '{BASELINE}')
      select lower(CSP_ID),
        max(iff(SNAPSHOT_DATE=DATE '{BASELINE}',t1,null)), max(iff(SNAPSHOT_DATE=latest,t1,null)),
        max(iff(SNAPSHOT_DATE=DATE '{BASELINE}',m3,null)), max(iff(SNAPSHOT_DATE=latest,m3,null)),
        to_char(max(latest),'DD-Mon') from snap group by 1""")
    snap = {r[0]: r[1:] for r in rows}
    latest_lbl = rows[0][5] if rows else "today"

    out = []
    for c, (pid, name, interv) in keep.items():
        s = snap.get(c); optical = "ptical" in interv; phone = enrolled.get(c, "")
        base, now = (None, None)
        if s: base, now = (s[0], s[1]) if optical else (s[2], s[3])
        metric = "Optical-OK %" if optical else "SLA on-time %"
        itype  = "Optical Power" if optical else "Service SLA"
        if base is not None and now is not None:
            dd = round(now - base, 1)
            dirn = "↓ worsened" if dd <= -1 else ("↑ improved" if dd >= 1 else "→ flat")
            rank = 0 if dd <= -1 else (1 if abs(dd) < 1 else 2)
        else:
            dd = ""; dirn = "no data"; rank = 3
        target_met = ("Yes ✅" if now >= TARGET else "No") if now is not None else ""
        out.append([name, c, pid, phone, itype, metric,
                    (round(base, 1) if base is not None else ""), (round(now, 1) if now is not None else ""),
                    dd, dirn, target_met, rank, (now if now is not None else 999)])
    out.sort(key=lambda r: (r[11], r[12]))

    stamp = datetime.datetime.now(IST).strftime("%d-%b-%Y %H:%M IST")
    title = (f"Sehat MG — ENROLLED only (fired Sehat_OptIn) · Optical & Service SLA · target ≥80% · "
             f"16-Jul baseline vs {latest_lbl} · sorted worst+declining first · updated {stamp}")
    hdr = ["CSP Name", "CSP ID", "Partner ID", "CSP Phone", "Intervention", "Metric",
           "% on 16-Jul", "% Today", "Δ (pp)", "Direction", "Target met (≥80%)"]
    NC = len(hdr)
    grid = [[title] + [""] * (NC - 1), hdr] + [r[:NC] for r in out]
    try:
        ws = sh.worksheet("Sehat MG Live"); ws.clear(); ws.resize(rows=len(grid) + 5, cols=NC)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet("Sehat MG Live", rows=len(grid) + 5, cols=NC)
    ws.update(values=grid, range_name="A1", value_input_option="RAW")
    ws.update(values=[[r[1], r[2], r[3]] for r in out], range_name=f"B3:D{len(out)+2}", value_input_option="RAW")
    sid = ws.id; last = len(grid)
    ws.spreadsheet.batch_update({"requests": [
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1}, "cell": {"userEnteredFormat": {"textFormat": {"bold": True, "fontSize": 11}}}, "fields": "userEnteredFormat.textFormat"}},
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 1, "endRowIndex": 2}, "cell": {"userEnteredFormat": {"textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}}, "backgroundColor": {"red": .2, "green": .35, "blue": .55}, "wrapStrategy": "WRAP"}}, "fields": "userEnteredFormat(textFormat,backgroundColor,wrapStrategy)"}},
        {"updateSheetProperties": {"properties": {"sheetId": sid, "gridProperties": {"frozenRowCount": 2, "frozenColumnCount": 1}}, "fields": "gridProperties(frozenRowCount,frozenColumnCount)"}},
        {"setBasicFilter": {"filter": {"range": {"sheetId": sid, "startRowIndex": 1, "endRowIndex": last, "startColumnIndex": 0, "endColumnIndex": NC}}}},
        {"addConditionalFormatRule": {"rule": {"ranges": [{"sheetId": sid, "startRowIndex": 2, "endRowIndex": last, "startColumnIndex": 9, "endColumnIndex": 10}], "booleanRule": {"condition": {"type": "TEXT_CONTAINS", "values": [{"userEnteredValue": "worsened"}]}, "format": {"backgroundColor": {"red": .98, "green": .80, "blue": .80}}}}, "index": 0}},
        {"addConditionalFormatRule": {"rule": {"ranges": [{"sheetId": sid, "startRowIndex": 2, "endRowIndex": last, "startColumnIndex": 9, "endColumnIndex": 10}], "booleanRule": {"condition": {"type": "TEXT_CONTAINS", "values": [{"userEnteredValue": "improved"}]}, "format": {"backgroundColor": {"red": .80, "green": .94, "blue": .80}}}}, "index": 0}},
        {"addConditionalFormatRule": {"rule": {"ranges": [{"sheetId": sid, "startRowIndex": 2, "endRowIndex": last, "startColumnIndex": 10, "endColumnIndex": 11}], "booleanRule": {"condition": {"type": "TEXT_CONTAINS", "values": [{"userEnteredValue": "Yes"}]}, "format": {"backgroundColor": {"red": .80, "green": .94, "blue": .80}, "textFormat": {"bold": True}}}}, "index": 0}},
    ]})
    worse = sum(1 for r in out if "worsened" in r[9]); imp = sum(1 for r in out if "improved" in r[9])
    above = sum(1 for r in out if r[10].startswith("Yes")); noph = sum(1 for r in out if not r[3])
    print(f"wrote 'Sehat MG Live': {len(out)} enrolled | worsened {worse} improved {imp} | above 80% {above} | no-phone {noph} | vs {latest_lbl}", flush=True)


if __name__ == "__main__":
    main()
