# -*- coding: utf-8 -*-
"""Payout750 — per-CSP OPT-IN / DECLINE / ABANDONED with who (role) + when.

One row per CSP for the run: decision (opt-in wins), the role of the identity that made the
winning decision (owner-precedence, single role, CURRENT role), and the time. Abandoned = the
CSP viewed but never chose new/later; its "when" is the last activity time.

Role = CURRENT role from PROFILE_DATA, taking the HIGHEST role per identity — PROFILE_DATA keeps
one row PER APP VERSION, so an old row can still say 'technician' after a promotion to admin/owner;
highest-role picks the real current one (technician is only shown when it's the ONLY role).

Env: CT_PASS, GOOGLE_SA_JSON (or local SA). Optional: P750_RUN_FROM_TS, P750_FROM, P750_WHO_TAB,
     P750_LOG_SHEET_ID, P750_DESIGN_SHEET_ID, METABASE_KEY.
"""
import os, sys, json, time, tempfile, urllib.request, datetime
sys.stdout.reconfigure(encoding="utf-8")
import gspread
from google.oauth2 import service_account
from collections import Counter

CT_ACC  = os.environ.get("CLEVERTAP_ACCOUNT", "44Z-644-777Z")
CT_PASS = os.environ.get("CT_PASS") or os.environ.get("CLEVERTAP_PASSCODE")
REGION  = os.environ.get("CLEVERTAP_REGION", "eu1")
CT      = f"https://{REGION}.api.clevertap.com"
HP = {"X-CleverTap-Account-Id": CT_ACC, "X-CleverTap-Passcode": CT_PASS, "Content-Type": "application/json; charset=utf-8"}
HG = {"X-CleverTap-Account-Id": CT_ACC, "X-CleverTap-Passcode": CT_PASS}
MB_KEY  = os.environ.get("METABASE_KEY", "mb_1dsbxsJfyROPsVyNpifJ8hTTlIDG85+qNKRo91KDnb4=")
SHEET_ID = os.environ.get("P750_LOG_SHEET_ID", "1ap0K6GB6RijeLHRWPf9N84U0cl1XEJs67DSQBql7sGQ")
DESIGN_SHEET_ID = os.environ.get("P750_DESIGN_SHEET_ID", "1SfWil0SaN1lKPTqtTF86edoPk8lT1BxzNzB6vC0_4Yc")
RUN_FROM_TS = os.environ.get("P750_RUN_FROM_TS", "20260819183000")
FROM = int(os.environ.get("P750_FROM", "20260819"))
TAB  = os.environ.get("P750_WHO_TAB", "Opt-ins & Declines — who & when (19 Aug)")
IST  = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
ORDER = {"Owner": 0, "Admin": 1, "Technician": 2}
BEST  = {"OWNER": 3, "MANAGER_PLUS": 2, "MANAGER": 2, "TECHNICIAN": 0}


def export(ev, frm, to):
    body = json.dumps({"event_name": ev, "from": frm, "to": to}).encode()
    for _ in range(3):
        try:
            d = json.loads(urllib.request.urlopen(urllib.request.Request(CT + "/1/events.json?batch_size=1000", data=body, headers=HP), timeout=90).read().decode())
            cur = d.get("cursor"); recs = []; p = 0
            while cur and p < 300:
                dd = json.loads(urllib.request.urlopen(urllib.request.Request(CT + "/1/events.json?cursor=" + cur, headers=HG), timeout=90).read().decode())
                recs += dd.get("records", []); cur = dd.get("next_cursor"); p += 1
                if not cur: break
            return recs
        except Exception as e:
            print("  retry", ev, str(e)[:60], flush=True); time.sleep(4)
    return []


def mb(sql):
    b = json.dumps({"database": 113, "type": "native", "native": {"query": sql}}).encode()
    return json.loads(urllib.request.urlopen(urllib.request.Request("https://metabase.wiom.in/api/dataset", data=b, headers={"x-api-key": MB_KEY, "Content-Type": "application/json"}), timeout=180).read().decode())["data"]["rows"]


def creds():
    raw = os.environ.get("GOOGLE_SA_JSON", "")
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    if raw:
        t = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False); t.write(raw); t.close()
        return service_account.Credentials.from_service_account_file(t.name, scopes=scopes)
    for p in (r"C:\Users\Palak Vardhan\Desktop\mbg\mbg-cron\wiom-sheets-writer.json",
              r"C:\Users\Palak Vardhan\dashboard\wiom-sheets-writer.json"):
        if os.path.exists(p):
            return service_account.Credentials.from_service_account_file(p, scopes=scopes)
    raise SystemExit("no SA creds")


def fmt(ts):
    try: return datetime.datetime.strptime(str(ts), "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M")
    except Exception: return str(ts)


def rlab(r):
    r = (r or "").upper()
    return "Owner" if r == "OWNER" else "Admin" if r in ("MANAGER", "MANAGER_PLUS") else "Technician" if r == "TECHNICIAN" else (r or "?")


def main():
    gc = gspread.authorize(creds())
    tom = int((datetime.datetime.now() + datetime.timedelta(days=1)).strftime("%Y%m%d"))
    evs = []
    for ev in ["Payout750_Viewed", "Payout750_Progress", "Payout750_Confirmed", "Payout750_Declined", "Payout750_Closed"]:
        short = ev.replace("Payout750_", "")
        for r in export(ev, FROM, tom):
            prof = r.get("profile", {}) or {}; pd = prof.get("profileData", {}) or {}
            cspid = (pd.get("cspid") or "").strip(); ident = str(prof.get("identity", "")); ts = str(r.get("ts", ""))
            if not ts or (RUN_FROM_TS and ts < RUN_FROM_TS): continue
            ch = (r.get("event_props", {}) or {}).get("choice", "")
            evs.append((cspid, ident, ts, short, ch, pd.get("role", "")))

    # identity -> CURRENT role: highest role per identity from PROFILE_DATA
    idents = sorted({e[1] for e in evs if e[1]})
    id2role = {}
    if idents:
        inlist = ",".join("'%s'" % i.replace("'", "") for i in idents)
        for r in mb(f"SELECT IDENTITY, ROLE FROM PROD_DB.CLEVERTAP_CSP_API.PROFILE_DATA WHERE IDENTITY IN ({inlist})"):
            if not r[0]: continue
            k = str(r[0])
            if k not in id2role or BEST.get((r[1] or "").upper(), 1) > BEST.get((id2role[k] or "").upper(), 1):
                id2role[k] = r[1]

    d = gc.open_by_key(DESIGN_SHEET_ID).worksheet("Design").get_all_values()
    H = d[0]; ci = H.index("CSP ID"); ni = H.index("Partner Name"); fi = H.index("Flow")
    name = {r[ci].strip(): r[ni].strip() for r in d[1:] if len(r) > max(ci, ni) and r[ci].strip()}
    flow = {r[ci].strip(): r[fi].strip() for r in d[1:] if len(r) > fi and r[ci].strip()}

    per = {}
    for cspid, ident, ts, short, ch, rpd in evs:
        if not cspid: continue
        role = id2role.get(ident) or rpd
        p = per.setdefault(cspid, {"opt": [], "dec": [], "all": []})
        p["all"].append((ts, ident, role))
        if short == "Confirmed" or (short == "Closed" and ch == "new"): p["opt"].append((ts, ident, role))
        elif short == "Declined" or (short == "Closed" and ch == "later"): p["dec"].append((ts, ident, role))

    def pick_decision(a): s = sorted(a, key=lambda x: (ORDER.get(rlab(x[2]), 3), x[0])); return s[0][0], rlab(s[0][2])
    def pick_abandon(a):  s = sorted(a, key=lambda x: ORDER.get(rlab(x[2]), 3)); return max(x[0] for x in a), rlab(s[0][2])

    rows = []
    for cspid, p in per.items():
        if p["opt"]:   ts, who = pick_decision(p["opt"]); dec, rank = "Opted In", 0
        elif p["dec"]: ts, who = pick_decision(p["dec"]); dec, rank = "Declined", 1
        else:          ts, who = pick_abandon(p["all"]);  dec, rank = "Abandoned", 2
        rows.append([cspid, name.get(cspid, ""), (flow.get(cspid, "") or "").replace("Flow ", "F"), dec, who, fmt(ts), rank, ts])
    rows.sort(key=lambda x: (x[6], x[7]))

    c = Counter(r[3] for r in rows); byrole = Counter(r[4] for r in rows)
    now = datetime.datetime.now(IST)
    note = [f"PAYOUT750 — OPT-INS / DECLINES / ABANDONED · who & when (19-Aug new run) | "
            f"opted-in={c['Opted In']}  declined={c['Declined']}  abandoned={c['Abandoned']} | "
            f"'Acted by' = role of the deciding identity (owner-precedence, single, CURRENT role). "
            f"'When' = decision time for opted/declined, LAST activity for abandoned (viewed, no choice). | "
            f"updated {now:%Y-%m-%d %H:%M IST} (CleverTap export lags ~1 hr)"]
    head = ["CSP ID", "Business Name", "Flow", "Decision", "Acted by (role)", "When (IST)"]
    out = [note, [], head] + [r[:6] for r in rows]

    ss = gc.open_by_key(SHEET_ID)
    try: ws = ss.worksheet(TAB); ws.clear()
    except gspread.WorksheetNotFound: ws = ss.add_worksheet(TAB, rows=len(out) + 10, cols=6)
    ws.update(values=out, range_name="A1", value_input_option="RAW")
    ws.format("A1:F1", {"textFormat": {"bold": True, "fontSize": 10}, "wrapStrategy": "WRAP"})
    ws.format("A3:F3", {"textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}}, "backgroundColor": {"red": .85, "green": 0, "blue": .55}})
    try: ws.freeze(rows=3)
    except Exception: pass
    print(f"wrote '{TAB}': opted={c['Opted In']} declined={c['Declined']} abandoned={c['Abandoned']} | roles {dict(byrole)} | {now:%H:%M IST}", flush=True)


if __name__ == "__main__":
    main()
