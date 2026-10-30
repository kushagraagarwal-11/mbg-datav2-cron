"""Seven live expansion CSPs page (csp-atoms-live.pages.dev/area/six-live): pull every input from Metabase (DB 113 =
Snowflake), run compute.py + build_page.py, write out/area/six-live.html with a "Last refreshed" stamp.
The GitHub Actions workflow then deploys out/ to the Cloudflare Pages project csp-atoms-live.

Secrets: MB_KEY. Static inputs live in static/ (atom outlines, tracker lead-map mirror, BD pocket leads, 14 Sep briefs).
"""
import io, json, os, subprocess, sys
from datetime import datetime, timedelta, timezone

import requests
import base64, tempfile

HERE = os.path.dirname(os.path.abspath(__file__)); os.chdir(HERE)
MB_URL = "https://metabase.wiom.in/api/dataset"; MB_KEY = os.environ.get("MB_KEY", ""); DB_ID = 113
if not MB_KEY: sys.exit("MB_KEY not set")
IST = timezone(timedelta(hours=5, minutes=30)); NOW = datetime.now(IST)
CSPS = ["a0a7h7", "a0a7h8", "a0a7h9", "a0a6z8", "a0a7i4", "a0a7i6", "a0a7i8"]
PIDS = {"a0a7i4": "281749855443180", "a0a7i8": "281749855453790", "a0a7h8": "281749855368355", "a0a7i6": "281749855453756"}   # DIM_CSP partner ids of the new windows (fallback join)
IN = ",".join("'%s'" % c for c in CSPS)
SINCE = "2026-09-20"   # first lead of the first window was 24 Sep


def mb(sql, name):
    r = requests.post(MB_URL, headers={"x-api-key": MB_KEY, "Content-Type": "application/json"},
                      json={"database": DB_ID, "type": "native", "native": {"query": sql}}, timeout=900)
    r.raise_for_status(); j = r.json()
    if j.get("status") == "failed" or "data" not in j: sys.exit("%s: Metabase error: %s" % (name, str(j.get("error", j))[:600]))
    cols = [c["name"] for c in j["data"]["cols"]]; rows = [dict(zip(cols, r)) for r in j["data"]["rows"]]
    for r in rows: r.pop("PAD", None)
    if len(rows) >= 2000: sys.exit("%s: Metabase row cap hit" % name)
    print("  %-13s %5d rows" % (name, len(rows))); return rows


def appr_points():
    """Pitch-time unrecoverable lead points per window, kept in the private call-list sheet (tab six_live_inputs), never in this public repo."""
    import gspread
    from google.oauth2.service_account import Credentials
    sa = os.environ.get("GOOGLE_SA_JSON", ""); path = os.environ.get("SA_JSON_PATH", "")
    if sa:
        raw = sa.strip(); raw = raw if raw.startswith("{") else base64.b64decode(raw).decode("utf-8")
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8"); f.write(raw); f.close(); path = f.name
    if not path: sys.exit("GOOGLE_SA_JSON not set")
    creds = Credentials.from_service_account_file(path, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    ws = gspread.authorize(creds).open_by_key("14CluM_LQi_qxI45z6LkvrJj2lwV23NvRFVmrYyFtAhU").worksheet("six_live_inputs")
    out = {}
    for row in ws.get_all_values()[1:]:
        if len(row) < 6 or not row[0]: continue
        out.setdefault(row[0], {"src": row[5], "pts": []})["pts"].append([float(row[1]), float(row[2]), row[3], row[4]])
    json.dump(out, io.open("appr_points.json", "w")); print("  appr_points   %5d rows" % sum(len(v["pts"]) for v in out.values()))


def main():
    appr_points()
    geo = json.load(io.open("static/geo.json", encoding="utf-8"))
    atom_case = " ".join("when lat between %s and %s and lng between %s and %s then '%s'" % (g["bbox"][0], g["bbox"][1], g["bbox"][2], g["bbox"][3], cid) for cid, g in geo.items())
    print("pulling", NOW.strftime("%d %b %H:%M IST"))
    # 1 splitters (latest Dynamo upload per window)
    spl = {}
    for r in mb(f"""select coalesce(d.csp_id, case p.partner_id::varchar {' '.join("when '%s' then '%s'" % (v, k) for k, v in PIDS.items())} end) csp_id,
  p.partner_id::varchar pid, to_char(p._fivetran_synced,'YYYY-MM-DD HH24:MI') synced, p.coordinates
from PROD_DB.DYNAMODB.PARTNER_COORDINATES p
left join PROD_DB.DBT_CSP.DIM_CSP d on d.partner_id::varchar = p.partner_id::varchar and d.etl_current = true and d.csp_id in ({IN})
where d.csp_id is not null or p.partner_id::varchar in ({",".join("'%s'" % v for v in PIDS.values())})
qualify row_number() over (partition by p.partner_id order by p._fivetran_synced desc) = 1""", "splitters"):
        arr = json.loads(r["COORDINATES"]) if isinstance(r["COORDINATES"], str) else r["COORDINATES"]; pts = []
        for c in arr or []:
            for q in (c.get("cluster") or [c.get("centroid")]):
                if q and len(q) == 2: pts.append([round(float(q[0]), 6), round(float(q[1]), 6)])
        spl[r["CSP_ID"]] = {"pid": r["PID"], "synced": r["SYNCED"], "pts": pts}
    json.dump(spl, io.open("splitters.json", "w"))
    # 2 app launches (owner / manager)
    json.dump(mb(f"""with pr as (select clevertap_id, cspid, upper(role) role from PROD_DB.CLEVERTAP_CSP_API.PROFILE_DATA where cspid in ({IN}) and not _fivetran_deleted and upper(role) in ('OWNER','MANAGER'))
select pr.cspid, pr.role, left(e."TIMESTAMP", 16) ts, count(*) n
from PROD_DB.CLEVERTAP_CSP_API.EVENTS_DATA e join pr on pr.clevertap_id = e.clevertap_id
where e._fivetran_synced >= '{SINCE}' and not e._fivetran_deleted and e.event_name = 'App Launched' and e."TIMESTAMP" >= '{SINCE}'
group by 1,2,3 order by 1,3""", "launches"), io.open("launches.json", "w"))
    # 3 tasks (FACT) + 4 TAS transition log
    json.dump(mb(f"""select csp_id, execution_candidate_id ec, connection_id conn, zone_id, to_char(created_at,'YYYY-MM-DD HH24:MI') created, current_state st, task_status ts, reason_code rc,
  case when executor_id is not null then 1 else 0 end asg, to_char(installation_completed_at,'YYYY-MM-DD HH24:MI') inst_at, to_char(confirmed_slot_at,'YYYY-MM-DD HH24:MI') slot_at, declined_reason, dismiss_count, call_attempts_count calls
from PROD_DB.DBT_CSP.FACT_INSTALL_CANDIDATES where etl_current and task_family = 'INSTALL' and csp_id in ({IN}) and created_at >= '{SINCE}' order by csp_id, created_at""", "tasks"), io.open("tasks.json", "w"))
    json.dump(mb(f"""with t as (select execution_candidate_id ec, csp_id from PROD_DB.DBT_CSP.FACT_INSTALL_CANDIDATES where etl_current = true and task_family = 'INSTALL' and csp_id in ({IN}) and created_at >= '{SINCE}')
select t.csp_id, t.ec,
 to_char(min(case when l.to_state='AWAITING_CSP_ACCEPTANCE' then l.occurred_at end),'YYYY-MM-DD HH24:MI:SS') hold_at,
 to_char(min(case when l.to_state='AWAITING_CUSTOMER_SCHEDULE' then l.occurred_at end),'YYYY-MM-DD HH24:MI:SS') cx_sched_at,
 to_char(min(case when l.actor_type='CSP' then l.occurred_at end),'YYYY-MM-DD HH24:MI:SS') csp_first_at,
 to_char(min(case when l.reason_code='ACCEPT_WITH_SLOT' then l.occurred_at end),'YYYY-MM-DD HH24:MI:SS') accepted_at,
 to_char(min(case when l.to_state='DECLINED' then l.occurred_at end),'YYYY-MM-DD HH24:MI:SS') declined_at,
 to_char(min(case when l.to_state='SLOT_CONFIRMED_BY_CUSTOMER' then l.occurred_at end),'YYYY-MM-DD HH24:MI:SS') cx_slot_at,
 to_char(min(case when l.to_state='TECHNICIAN_ASSIGNED' then l.occurred_at end),'YYYY-MM-DD HH24:MI:SS') assigned_at,
 to_char(min(case when l.to_state='ARRIVED_AT_SITE' then l.occurred_at end),'YYYY-MM-DD HH24:MI:SS') arrived_at,
 to_char(min(case when l.to_state='CONNECTION_ACTIVE' then l.occurred_at end),'YYYY-MM-DD HH24:MI:SS') active_at,
 to_char(min(case when l.reason_code='TIMEOUT_P41' then l.occurred_at end),'YYYY-MM-DD HH24:MI:SS') timeout_at,
 to_char(min(case when l.to_state='CANCELLED_BY_CUSTOMER' or l.reason_code in ('CSP_NO_SHOW','CANCEL_BY_CUSTOMER_BACKEND') then l.occurred_at end),'YYYY-MM-DD HH24:MI:SS') cancel_at,
 to_char(min(case when l.to_state='INSTALLATION_REPORTED_FAILED' then l.occurred_at end),'YYYY-MM-DD HH24:MI:SS') failed_at,
 count(l.id) n_log
from t left join PROD_DB.CSP_TAS_SERVICE_CSP_TAS_SERVICE.INSTALL_STATE_TRANSITION_LOG l on l.execution_candidate_id = t.ec
group by 1, 2""", "tas"), io.open("tas.json", "w"))
    # 5 bookings in each atom bbox (paid) + 6 every attempt (unpaid count)
    bk_head = f"""with bk0 as (
  select mobile::varchar mobile, added_time::timestamp_ntz added_time, try_to_double(lat::varchar) lat, try_to_double(lng::varchar) lng, 0 src, booking_state, status, payment_status
  from PROD_DB.BOOKING_SERVICE_JAVA_PUBLIC.BOOKING where _fivetran_active and lat is not null and added_time >= '2026-08-01'
  union all
  select mobile::varchar, added_time::timestamp_ntz, try_to_double(lat::varchar), try_to_double(lng::varchar), 1, booking_state, status, payment_status
  from PROD_DB.DYNAMODB_READ.BOOKING where lat is not null and added_time >= '2026-08-01'),
bk as (select *, right(regexp_replace(mobile,'[^0-9]',''),10) m10, case {atom_case} end atom
  from bk0 qualify row_number() over (partition by mobile, date_trunc('hour', added_time) order by src) = 1),"""
    json.dump(mb(bk_head + """
bx as (select * from bk where atom is not null and (coalesce(payment_status, 0) <> 0 or booking_state is not null)),
enr as (select connection_id, right(regexp_replace(mobile::varchar,'[^0-9]',''),10) m10, booking_confirm_time::timestamp_ntz bct from PROD_DB.PUBLIC.COMPANY_B_CONNECTION_BOOKING_ENRICHED qualify row_number() over (partition by connection_id order by run_date desc) = 1),
m1 as (select bx.mobile, bx.added_time, e.connection_id, row_number() over (partition by bx.mobile, bx.added_time order by abs(datediff('second', bx.added_time, e.bct))) rn
       from bx join enr e on e.m10 = bx.m10 and abs(datediff('hour', bx.added_time, e.bct)) <= 240),
tv as (select distinct right(regexp_replace(mobile,'[^0-9]',''),10) m10, connection_id from (select mobile, connection_id from PROD_DB.DBT.TASKVANILLA union all select mobile, connection_id from PROD_DB.DBT.TASKVANILLA_AUDIT) where mobile is not null and connection_id is not null),
fc as (select connection_id, min(created_at) first_lead, listagg(csp_id||':'||coalesce(current_state,'')||':'||to_char(created_at,'MM-DD HH24:MI')||':'||case when installation_completed_at is not null or current_state='CONNECTION_ACTIVE' then 'I' else '' end, '|') within group (order by created_at) cands
      from PROD_DB.DBT_CSP.FACT_INSTALL_CANDIDATES where etl_current = true and created_at >= '2026-07-25' group by 1),
m2 as (select bx.mobile, bx.added_time, tv.connection_id, row_number() over (partition by bx.mobile, bx.added_time order by abs(datediff('second', bx.added_time, fc.first_lead))) rn
       from bx join tv on tv.m10 = bx.m10 join fc on fc.connection_id = tv.connection_id and fc.first_lead between dateadd(day, -1, bx.added_time) and dateadd(day, 20, bx.added_time)),
j as (select bx.*, coalesce(a.connection_id, b.connection_id) connection_id, case when a.connection_id is not null then 'enr' when b.connection_id is not null then 'tv' end link
      from bx left join m1 a on a.mobile = bx.mobile and a.added_time = bx.added_time and a.rn = 1 left join m2 b on b.mobile = bx.mobile and b.added_time = bx.added_time and b.rn = 1)
select j.atom, to_char(j.added_time,'YYYY-MM-DD HH24:MI') booked, round(j.lat,6) lat, round(j.lng,6) lng, j.src, j.connection_id conn, j.link, c.cands, md5(j.mobile) mh, j.booking_state bst, j.status st, j.payment_status pst
from j left join fc c on c.connection_id = j.connection_id order by 1, 2""", "bookings"), io.open("bookings.json", "w"))
    json.dump(mb(bk_head + """
bx as (select * from bk where atom is not null),
enr as (select connection_id, mobile::varchar mobile, booking_confirm_time::timestamp_ntz bct from PROD_DB.PUBLIC.COMPANY_B_CONNECTION_BOOKING_ENRICHED qualify row_number() over (partition by connection_id order by run_date desc) = 1),
m as (select bx.*, e.connection_id, e.bct, row_number() over (partition by bx.mobile, bx.added_time order by abs(datediff('second', bx.added_time, e.bct))) rn
      from bx left join enr e on e.mobile = bx.mobile and abs(datediff('hour', bx.added_time, e.bct)) <= 96)
select m.atom, to_char(m.added_time,'YYYY-MM-DD HH24:MI') booked, round(m.lat,6) lat, round(m.lng,6) lng, m.src, m.connection_id conn, to_char(m.bct,'YYYY-MM-DD HH24:MI') confirm, null as cands, md5(m.mobile) mh, m.booking_state bst, m.status st, m.payment_status pst
from m where m.rn = 1 order by m.atom, m.added_time""", "bookings_all"), io.open("bookings_all.json", "w"))
    # 7 CleverTap task events per execution candidate (role '' = owner/manager device, 'technician' = technician device)
    json.dump(mb(f"""with t as (select execution_candidate_id ec, csp_id from PROD_DB.DBT_CSP.FACT_INSTALL_CANDIDATES where etl_current = true and task_family = 'INSTALL' and csp_id in ({IN}) and created_at >= '{SINCE}'),
pr as (select clevertap_id, cspid, case when lower(role) = 'technician' then 'technician' else '' end role from PROD_DB.CLEVERTAP_CSP_API.PROFILE_DATA where cspid in ({IN}) and not _fivetran_deleted)
select t.csp_id, t.ec, e.event_name, pr.role, min(e."TIMESTAMP") first_ts, max(e."TIMESTAMP") last_ts, count(*) n, count(distinct e.clevertap_id) devices,
  listagg(distinct try_parse_json(e.properties):entry_source::varchar, '|') srcs
from PROD_DB.CLEVERTAP_CSP_API.EVENTS_DATA e join pr on pr.clevertap_id = e.clevertap_id join t on t.ec = try_parse_json(e.properties):execution_id::varchar
where e._fivetran_synced >= '{SINCE}' and not e._fivetran_deleted and e."TIMESTAMP" >= '{SINCE}' and e.event_name in ('fpn_delivered','install_candidate_opened','pn_clicked','pn_delivered')
group by 1,2,3,4 order by 1,2,5""", "events"), io.open("events.json", "w"))
    # 8 seconds from push delivery to first app interaction (ui30)
    json.dump(mb(f"""with t as (select execution_candidate_id ec, csp_id from PROD_DB.DBT_CSP.FACT_INSTALL_CANDIDATES where etl_current = true and task_family = 'INSTALL' and csp_id in ({IN}) and created_at >= '{SINCE}'),
dl as (select t.csp_id, t.ec, min(try_to_timestamp(e."TIMESTAMP")) delivered_at from t join PROD_DB.CLEVERTAP_CSP_API.EVENTS_DATA e on try_parse_json(e.properties):execution_id::varchar = t.ec
  where e._fivetran_synced >= '{SINCE}' and not e._fivetran_deleted and e.event_name in ('pn_delivered','fpn_delivered') group by 1,2),
pr as (select clevertap_id, cspid from PROD_DB.CLEVERTAP_CSP_API.PROFILE_DATA where cspid in ({IN}) and not _fivetran_deleted and upper(role) in ('OWNER','MANAGER')),
ui as (select pr.cspid, e.event_name, try_to_timestamp(e."TIMESTAMP") ts from PROD_DB.CLEVERTAP_CSP_API.EVENTS_DATA e join pr on pr.clevertap_id = e.clevertap_id
  where e._fivetran_synced >= '{SINCE}' and not e._fivetran_deleted and e.event_name in ('home_feed_rendered','install_candidate_opened','candidate_closed','drawer_opened','sheet_opened','strip_chip_opened','home_refreshed','batch_drilldown_opened','drawer_item_opened','feed_refreshed','service_status_opened','overflow_menu_opened','nbrec_group_opened','recharge_customer_selected','ticket_accepted','technician_assigned','install_task_assigned','restore_candidate_opened','pickup_candidate_opened','pn_clicked','fpn_action_taken','call_initiated','deeplink_clicked','banner_opened','app_exited'))
select dl.csp_id, dl.ec, min(case when ui.ts >= dl.delivered_at then datediff('second', dl.delivered_at, ui.ts) end) sec_ui_after
from dl left join ui on ui.cspid = dl.csp_id and ui.ts between dl.delivered_at and dateadd(hour, 48, dl.delivered_at)
group by 1, 2""", "ui30"), io.open("ui30.json", "w"))
    # 9 routing: in the candidate list / eligible / won, per window, since 20 Sep (for the checklist)
    json.dump(mb(f"""select c.csp_id, count_if(a.candidate_csps_received ilike '%' || c.csp_id || '%') received, count_if(a.eligible_set ilike '%' || c.csp_id || '%') eligible, count_if(a.selected_csp_id = c.csp_id) won,
  to_char(convert_timezone('Asia/Kolkata', max(case when a.selected_csp_id = c.csp_id then a.created_at end)),'DD Mon HH24:MI') last_won
from (select column1 csp_id from values {",".join("('%s')" % c for c in CSPS)}) c
left join PROD_DB.CSP_DEMAND_ALLOCATION_SERVICE_CSP_DEMAND_ALLOCATION_SERVICE.ALLOCATION_AUDIT_LOG a on a.created_at >= '{SINCE}' and (a.candidate_csps_received ilike '%' || c.csp_id || '%' or a.selected_csp_id = c.csp_id)
group by 1""", "routing"), io.open("routing.json", "w"))
    stamp = NOW.strftime("%d %b %Y %H:%M")
    io.open("stamp.txt", "w").write(stamp)
    for step in ("compute.py", "build_page.py"):
        print("running", step); subprocess.run([sys.executable, step], check=True)
    print("done", stamp)


if __name__ == "__main__":
    main()
