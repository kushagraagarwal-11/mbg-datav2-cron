"""Today: CSP tried, cx not reached  (tab in the Friends+Entertainment call-list sheet)

Every run: pull today's install offers (IST) for the Delhi CSP list from Metabase (DB 113 = Snowflake),
join the CSP phone call log, keep the leads that are still with the CSP where the CSP dialled the customer
at least once but nobody from the CSP (owner / manager / technician) has had a call connect for more than 10 s.
Upsert into the tab keyed by customer mobile + CSP id: new rows are appended, existing rows get their status /
call columns refreshed, team columns (Called?, Frivolous, Remarks, Recording) are never touched, and rows are
NEVER removed - a row that stops qualifying is flagged in "Still in list?" with the reason.

Secrets: MB_KEY (Metabase API key), GOOGLE_SA_JSON (service-account JSON, raw or base64).
"""
import base64, json, os, re, sys, tempfile
from datetime import datetime, timedelta, timezone

import gspread, requests
from google.oauth2.service_account import Credentials

SHEET_ID = "14CluM_LQi_qxI45z6LkvrJj2lwV23NvRFVmrYyFtAhU"
TAB_GID = 1065910498
MB_URL = "https://metabase.wiom.in/api/dataset"
MB_KEY = os.environ.get("MB_KEY", "")
if not MB_KEY:
    sys.exit("MB_KEY not set")
DB_ID = 113
IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime.now(IST)
TODAY = NOW.strftime("%Y-%m-%d")

CSP_IDS = """a0b9k9 a0b6f2 a0b6x8 a0b6x9 a0b6g1 a0b6j8 a0a7g5 a0a7e7 a0b6x2 a0b9t7 a0a7g1 a0b7c6 a0b6o2 a0b7e7 a0b8y0 a0a7b7 a0b6d4 a0b8y8 a0c0f6 a0b7i5
a0b5q7 a0b6b2 a0b9t8 a0b5s0 a0b0w0 a0b7a7 a0b9b9 a0b9d2 a0b9o4 a0b6e9 a0b6u2 a0b9c1 a0b5m1 a0b9z2 a0c0a9 a0b8s4 a0b6d0 a0c0b1 a0b5r9 a0b6a1 a0b5v9 a0b7a9
a0b9z9 a0b6n3 a0b9m8 a0c0b8 a0b9l1 a0c0f1 a0b9w6 a0b6o1 a0b5o2 a0b6b8 a0a7g7 a0b8r0 a0b9n2 a0b6f0 a0b6t9 a0b7h7 a0b8z0 a0b9m4 a0b6i4 a0b9y5 a0a7a5 a0b9y4
a0b6h4 a0b7h8 a0b9h2 a0b7d7 a0b5n4 a0b6i1 a0b6h8 a0b7b4 a0a6z1 a0b9x8 a0b9z3 a0b5o6 a0b6y5 a0b8w5 a0b7c7 a0b6b3 a0a7g6 a0c0a0 a0b6u8 a0b9j1 a0c0i5 a0b6w0
a0a7c1 a0b7b7 a0b6v6 a0b9s4 a0c0c8 a0b9v7 a0a6z8 a0b5y9 a0b9g3 a0b9b5 a0b8x3 a0b9d5 a0b5n2 a0b6y6 a0b8w3 a0b5m5 a0b5z6 a0b6t8 a0b8r9 a0b9k4 a0c0d2 a0b5n0
a0b5o1 a0c0b5 a0c0c7 a0b6l7 a0b6p4 a0b9r9 a0c0e1 a0b9c9 a0b6v4 a0b9r4 a0b9w7 a0b5u8 a0b5w2 a0b6l6 a0b6t0 a0b5n3 a0b5x8 a0b6g7 a0b8q2 a0b9a4 a0b6q8 a0b9f2
a0a7f5 a0b9u7 a0c0e6 a0b9a6 a0b5q0 a0b6i8 a0b6v7 a0b7h5 a0b7i0 a0b9a9 a0b9x3 a0b6a8 a0b6h5 a0b6n9 a0b6t3 a0b6x4 a0b8u2 a0b9y3 a0c0e4 a0b0n9 a0a7h1 a0b8o9
a0a6y6 a0b7a1 a0b7g4 a0b9q4 a0c0e2 a0c0e9 a0b7e5 a0b8p6 a0b9n6 a0c0b4 a0a7a8 a0a7g3 a0b6a3 a0b6g6 a0b6w7 a0b7i2 a0b6q2 a0b6y7 a0b6q3 a0b5l9 a0b6m3 a0b6r3
a0b7d8 a0b9r3 a0b6x3 a0b5t0 a0b5v5 a0b6w2 a0b5w1 a0b6l3 a0b6p3 a0c0a1 a0c0c1 a0b5r4 a0b6q1 a0b9a2 a0b9w8 a0b6p1 a0b6r8 a0b6v8 a0b9l5 a0c0c0 a0a7h7 a0b8u6
a0b9g6 a0b5o0 a0b6f8 a0b9j0 a0b6n2 a0b7f4 a0b7j4 a0b8o7 a0b6a6 a0b9i0 a0b9y8 a0a7h9 a0b6m4 a0b6o5 a0b9i5 a0b6h6 a0b6o9 a0b6p6 a0b6f1 a0a7i4 a0a9s3 a0b1u5
a0b5l8 a0b5p5 a0b5w8 a0b6h2 a0b7a6 a0b7b3 a0b7f3 a0b8x9 a0b9g8 a0b9i9""".split()
CSP_VALUES = ",".join("('%s')" % c for c in CSP_IDS)
OPEN_STATES = {"SLOT_CONFIRMED_BY_CUSTOMER", "TECHNICIAN_ASSIGNED", "ARRIVED_AT_SITE", "INSTALLATION_IN_PROGRESS_PRE_FEE", "FEE_COLLECTION_PENDING",
               "INSTALLATION_IN_PROGRESS_POST_FEE", "RATING_PENDING"}

# FACT_INSTALL_CANDIDATES.created_at is TIMESTAMP_TZ; DEVICE_CALL_LOG.start_time is TIMESTAMP_NTZ holding UTC.
# Both are compared in UTC explicitly. "Today" = IST calendar day.
SQL_LEADS = """
with csps as (select column1 csp_id from values %(csps)s),
f0 as (select f.connection_id, f.csp_id, min(convert_timezone('UTC', f.created_at)::timestamp_ntz) offered_utc, count(*) offers,
         max(case when f.executor_id is not null then 1 else 0 end) accepted,
         max_by(f.current_state, f.created_at) state, max_by(f.reason_code, f.created_at) reason, max_by(f.customer_name, f.created_at) customer_name,
         max_by(f.locality, f.created_at) locality, max_by(f.service_address, f.created_at) addr, max_by(f.confirmed_slot_at, f.created_at) slot_at,
         max(case when f.current_state = 'CONNECTION_ACTIVE' or f.installation_completed_at is not null then 1 else 0 end) installed
       from PROD_DB.DBT_CSP.FACT_INSTALL_CANDIDATES f
       where f.etl_current and f.task_family = 'INSTALL' and convert_timezone('Asia/Kolkata', f.created_at) >= '%(today)s 00:00' and f.csp_id in (select csp_id from csps)
       group by 1, 2),
enr as (select connection_id, right(regexp_replace(mobile::varchar,'[^0-9]',''),10) m10 from PROD_DB.PUBLIC.COMPANY_B_CONNECTION_BOOKING_ENRICHED
        where connection_id in (select connection_id from f0) qualify row_number() over (partition by connection_id order by run_date desc) = 1),
tv as (select connection_id, right(regexp_replace(mobile,'[^0-9]',''),10) m10 from (select mobile, connection_id from PROD_DB.DBT.TASKVANILLA union all select mobile, connection_id from PROD_DB.DBT.TASKVANILLA_AUDIT)
       where connection_id in (select connection_id from f0) and mobile is not null qualify row_number() over (partition by connection_id order by m10) = 1),
fm as (select f0.*, coalesce(enr.m10, tv.m10) m10 from f0 left join enr on enr.connection_id = f0.connection_id left join tv on tv.connection_id = f0.connection_id),
calls as (select partner_account_id csp_id, right(regexp_replace(counterparty_number,'[^0-9]',''),10) m10, start_time, role, direction, outcome, coalesce(duration_seconds,0) dur
          from PROD_DB.POSTGRES_RDS_PARTNER_CALL_LOG_IVR.DEVICE_CALL_LOG where start_time >= '%(utc_from)s' and partner_account_id in (select csp_id from csps)),
agg as (select fm.connection_id, fm.csp_id,
          count_if(c.direction='OUTGOING') n_out, count_if(c.direction='OUTGOING' and c.outcome='CONNECTED') n_out_conn, count_if(c.direction<>'OUTGOING' and c.outcome='CONNECTED') n_in_conn,
          max(iff(c.outcome='CONNECTED', c.dur, 0)) max_conn_dur,
          min_by(to_char(dateadd(minute,330,c.start_time),'HH24:MI') || ' ' || lower(c.role) || ' ' || lower(c.direction) || ' ' || lower(c.outcome) || ' ' || c.dur || 's', c.start_time) first_call,
          to_char(dateadd(minute,330,max(c.start_time)),'HH24:MI') last_call
        from fm join calls c on c.csp_id = fm.csp_id and c.m10 = fm.m10 and c.start_time >= fm.offered_utc group by 1, 2)
select fm.m10 mobile, fm.customer_name, fm.locality, fm.addr, fm.csp_id, d.partner_name csp_name, left(fm.connection_id, 8) conn,
  to_char(dateadd(minute, 330, fm.offered_utc),'HH24:MI') offered_ist, fm.offers, fm.accepted, fm.state, fm.reason, fm.installed,
  to_char(convert_timezone('Asia/Kolkata', fm.slot_at),'DD Mon HH24:MI') slot_ist,
  coalesce(a.n_out,0) n_out, coalesce(a.n_out_conn,0) n_out_conn, coalesce(a.n_in_conn,0) n_in_conn, coalesce(a.max_conn_dur,0) max_conn_dur, a.first_call, a.last_call
from fm left join agg a on a.connection_id = fm.connection_id and a.csp_id = fm.csp_id
left join PROD_DB.DBT_CSP.DIM_CSP d on d.csp_id = fm.csp_id and d.etl_current
order by fm.offered_utc
"""

SQL_CONTACTS = """
with csps as (select column1 csp_id from values %(csps)s),
d as (select csp_id, partner_id::string pid, partner_mobile_number owner_mobile from PROD_DB.DBT_CSP.DIM_CSP where etl_current and csp_id in (select csp_id from csps)),
pua as (select p.partner_id::string pid, p.role, p.status, p.name, right(regexp_replace(p.mobile_number,'[^0-9]',''),10) m10
        from PROD_DB.POSTGRES_RDS_PARTNER_SERVICE_DB_PUBLIC.PARTNER_USER_ACCOUNT p
        where (p._fivetran_deleted is null or p._fivetran_deleted = false) and p.partner_id::string in (select pid from d)),
ct as (select cspid, upper(role) role, right(regexp_replace(phone,'[^0-9]',''),10) m10 from PROD_DB.CLEVERTAP_CSP_API.PROFILE_DATA
       where cspid in (select csp_id from csps) and not _fivetran_deleted and phone is not null)
select d.csp_id, d.owner_mobile,
  (select listagg(distinct m10, ', ') from pua where pua.pid = d.pid and pua.role = 'OWNER') owner_acct,
  (select listagg(distinct coalesce(name,'') || ' ' || m10, ', ') from pua where pua.pid = d.pid and pua.role = 'ADMIN' and pua.status = 'ACTIVE') managers,
  (select listagg(distinct coalesce(name,'') || ' ' || m10, ', ') from pua where pua.pid = d.pid and pua.role = 'USER' and pua.status = 'ACTIVE') technicians,
  (select listagg(distinct role || ' ' || m10, ', ') from ct where ct.cspid = d.csp_id) app_profiles
from d
"""

COLS = ["Customer mobile", "Customer name", "Locality", "Address", "CSP holding the lead", "Owner number", "Manager number(s)", "Technician number(s)",
        "Offer date", "Offered at (IST)", "Lead status now", "Slot", "CSP calls so far (all CSP phones)", "First seen (IST)", "Last checked (IST)", "Still in list?",
        "Called?", "Frivolous/ CSP did not call", "Remarks", "Recording", "Connection ids"]
TEAM = {"Called?", "Frivolous/ CSP did not call", "Remarks", "Recording"}
DATA = {"Lead status now", "Slot", "CSP calls so far (all CSP phones)", "Last checked (IST)", "Still in list?", "Owner number", "Manager number(s)", "Technician number(s)"}


def mb_query(sql):
    r = requests.post(MB_URL, headers={"x-api-key": MB_KEY, "Content-Type": "application/json"},
                      json={"database": DB_ID, "type": "native", "native": {"query": sql}}, timeout=600)
    r.raise_for_status()
    j = r.json()
    if j.get("status") == "failed" or "data" not in j:
        sys.exit("Metabase error: " + str(j.get("error", j))[:500])
    cols = [c["name"] for c in j["data"]["cols"]]
    rows = [dict(zip(cols, r)) for r in j["data"]["rows"]]
    if len(rows) >= 2000:
        sys.exit("Metabase row cap hit (%d rows) - split the query" % len(rows))
    return rows


def sheet_tab():
    sa = os.environ.get("GOOGLE_SA_JSON", "")
    path = os.environ.get("SA_JSON_PATH", "")
    if sa:
        raw = sa.strip()
        if not raw.startswith("{"):
            raw = base64.b64decode(raw).decode("utf-8")
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8"); f.write(raw); f.close(); path = f.name
    if not path:
        sys.exit("GOOGLE_SA_JSON not set")
    creds = Credentials.from_service_account_file(path, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    return gspread.authorize(creds).open_by_key(SHEET_ID).get_worksheet_by_id(TAB_GID)


def clean_addr(s):
    if not s: return ""
    try: d = json.loads(s)
    except Exception: d = dict(re.findall(r'"(\w+)"\s*:\s*"([^"]*)"', s))
    return ", ".join(p for p in (str(d.get(k) or "").strip() for k in ("home", "streetNumber", "address", "locality")) if p)


def status(r):
    s, rc = r["STATE"], (r["REASON"] or "").lower().replace("_", " ")
    if r["INSTALLED"]: return "INSTALLED"
    if s == "CANCELLED_BY_UPSTREAM" and r["REASON"] == "TIMEOUT_P41": return "timed out (CSP never accepted)"
    if s == "CANCELLED_BY_UPSTREAM": return "taken back by system"
    if s == "DECLINED": return "CSP declined: " + rc
    if s == "INSTALLATION_REPORTED_FAILED": return "visit failed (" + rc + ")"
    if s == "CANCELLED_BY_CUSTOMER": return "customer cancelled"
    if not r["ACCEPTED"]: return "waiting for CSP to accept"
    return "accepted, " + s.lower().replace("_", " ")


def is_open(r):
    return not r["INSTALLED"] and (r["STATE"].startswith("AWAITING") or r["STATE"] in OPEN_STATES)


def calls_text(r):
    if r["N_OUT"] == 0 and r["N_IN_CONN"] == 0: return "no call yet"
    return "%d dialled, %d connected (longest %ds)%s; first %s; last %s" % (
        r["N_OUT"], r["N_OUT_CONN"] + r["N_IN_CONN"], r["MAX_CONN_DUR"], (", %d incoming answered" % r["N_IN_CONN"]) if r["N_IN_CONN"] else "", r["FIRST_CALL"] or "-", r["LAST_CALL"] or "-")


def contacts(C, cid):
    c = C.get(cid)
    if not c: return "", "", ""
    def nums(base, app_role):
        out = [x.strip() for x in (base or "").split(", ") if x.strip()]
        seen = {re.sub(r"\D", "", x)[-10:] for x in out}
        for p in (c["APP_PROFILES"] or "").split(", "):
            if p.startswith(app_role + " "):
                n = p.split()[-1]
                if n not in seen: out.append(n + " (app)"); seen.add(n)
        return ", ".join(out)
    owner = []
    for n in [c["OWNER_MOBILE"]] + (c["OWNER_ACCT"] or "").split(", "):
        n = re.sub(r"\D", "", n or "")[-10:]
        if n and n not in owner: owner.append(n)
    for p in (c["APP_PROFILES"] or "").split(", "):
        if p.startswith("OWNER ") and p.split()[-1] not in owner: owner.append(p.split()[-1] + " (app)")
    return ", ".join(owner), nums(c["MANAGERS"], "MANAGER"), nums(c["TECHNICIANS"], "TECHNICIAN")


def main():
    utc_from = (datetime.strptime(TODAY, "%Y-%m-%d") - timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
    leads = mb_query(SQL_LEADS % {"csps": CSP_VALUES, "today": TODAY, "utc_from": utc_from})
    C = {r["CSP_ID"]: r for r in mb_query(SQL_CONTACTS % {"csps": CSP_VALUES})}
    stamp = NOW.strftime("%d %b %H:%M")
    today_rows = {}
    for r in leads:
        key = (r["MOBILE"] or "conn:" + r["CONN"]) + "|" + r["CSP_ID"]
        qual = is_open(r) and r["N_OUT"] >= 1 and r["MAX_CONN_DUR"] <= 10
        if qual: still = "YES"
        elif r["MAX_CONN_DUR"] > 10: still = "no: CSP connected %ds" % r["MAX_CONN_DUR"]
        elif not is_open(r): still = "no: " + status(r)
        else: still = "no: CSP has not dialled yet"
        o, m, t = contacts(C, r["CSP_ID"])
        today_rows[key] = dict(qual=qual, data={"Customer mobile": r["MOBILE"] or "", "Customer name": r["CUSTOMER_NAME"] or "", "Locality": r["LOCALITY"] or "", "Address": clean_addr(r["ADDR"]),
            "CSP holding the lead": "%s (%s)" % (r["CSP_NAME"], r["CSP_ID"]), "Owner number": o, "Manager number(s)": m, "Technician number(s)": t,
            "Offer date": TODAY, "Offered at (IST)": r["OFFERED_IST"] + (" x%d" % r["OFFERS"] if r["OFFERS"] > 1 else ""), "Lead status now": status(r), "Slot": r["SLOT_IST"] or "",
            "CSP calls so far (all CSP phones)": calls_text(r), "First seen (IST)": stamp, "Last checked (IST)": stamp, "Still in list?": still, "Connection ids": r["CONN"]})

    ws = sheet_tab()
    vals = ws.get_all_values()
    hdr = vals[1] if len(vals) > 1 else []
    existing = []   # list of dicts in sheet order, by canonical column name
    for row in vals[2:]:
        if not any(x.strip() for x in row): continue
        row = row + [""] * (len(hdr) - len(row))
        d = {h: v for h, v in zip(hdr, row)}
        existing.append(d)
    # key for an existing row: mobile + csp id parsed from the CSP cell
    by_key = {}
    for d in existing:
        ids = re.findall(r"\((a0[a-z0-9]{4})\)", d.get("CSP holding the lead", ""))
        key = (d.get("Customer mobile", "") or "") + "|" + (ids[0] if ids else "")
        d.setdefault("Offer date", d.get("Offer date") or "2026-10-30")
        by_key[key] = d
    added = updated = 0
    for key, t in today_rows.items():
        if key in by_key:
            d = by_key[key]
            for c in DATA: d[c] = t["data"][c]
            updated += 1
        elif t["qual"]:
            d = {c: "" for c in COLS}; d.update(t["data"]); existing.append(d); by_key[key] = d; added += 1
    for key, d in by_key.items():
        if key not in today_rows and d.get("Still in list?", "").startswith(("YES", "")) and d.get("Offer date") != TODAY:
            if not d.get("Still in list?", "").startswith("no:"): d["Still in list?"] = "no: earlier day, lead no longer active"
            d["Last checked (IST)"] = stamp
    note = ("Auto-refreshed every 30 min (GitHub Actions). Rule: offered TODAY (IST) to one of the %d listed Delhi CSPs, lead still with the CSP, CSP dialled at least once after the offer, "
            "and nobody from the CSP (owner/manager/technician phone log) has had a call connect for more than 10 s. Rows are never removed: 'Still in list?' = YES while the rule holds, "
            "otherwise the reason it dropped. Team columns (Called?, Frivolous, Remarks, Recording) are never overwritten. Last run %s IST: %d rows, %d added, %d refreshed." % (len(CSP_IDS), stamp, len(existing), added, updated))
    out = [[note] + [""] * (len(COLS) - 1), COLS] + [[d.get(c, "") for c in COLS] for d in existing]
    ws.clear()
    ws.update(out, "A1", value_input_option="RAW")
    ws.freeze(rows=2)
    print("rows %d | added %d | refreshed %d | today's offers %d | qualifying now %d" % (len(existing), added, updated, len(leads), sum(1 for t in today_rows.values() if t["qual"])))


if __name__ == "__main__":
    main()
