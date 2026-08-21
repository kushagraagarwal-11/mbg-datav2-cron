# -*- coding: utf-8 -*-
"""Payout750 ops append — keep two calling tabs current, idempotently.

  - "Audit/Consent Pending Opt In"   <- new F2/F3 opt-ins  (cols A-I; Type = Consent/Audit Pending)
  - "Did NOT Opt in - Ask why?"      <- new declines       (cols A-H)

Source = the "Opt-ins & Declines — who & when" tab (kept fresh by the eventlog cron). Dedup by
cspid, so re-runs only APPEND genuinely new decisions. Mobile = owner phone; Zone from the rollout
sheet where present (blank otherwise). Caller / Call-Done left blank for manual assignment.

Env: GOOGLE_SA_JSON (or local SA), METABASE_KEY.
"""
import os, sys, json, tempfile, urllib.request
sys.stdout.reconfigure(encoding="utf-8")
import gspread
from google.oauth2 import service_account

MB_KEY = os.environ.get("METABASE_KEY", "mb_1dsbxsJfyROPsVyNpifJ8hTTlIDG85+qNKRo91KDnb4=")
WW_SHEET = "1ap0K6GB6RijeLHRWPf9N84U0cl1XEJs67DSQBql7sGQ"
WW_TAB   = os.environ.get("P750_WHO_TAB", "Opt-ins & Declines — who & when (19 Aug)")
OPS_SHEET = "1Q7g74kNLR_JHJNwb16IK_mmttzQNXuopK0ul1CkB1o0"
AUDIT_TAB = "Audit/Consent Pending Opt In"
DECL_TAB  = "Did NOT Opt in - Ask why?"
ROLLOUT_SHEET = "1W8W-sig92Bs9R4Uq5EYqfr2SMVY0wFYCX_K1uhJC0oM"
TYPE = {"F2": "Consent Pending", "F3": "Audit Pending"}


def mb(sql):
    body = json.dumps({"database": 113, "type": "native", "native": {"query": sql}}).encode()
    try:
        d = json.loads(urllib.request.urlopen(urllib.request.Request(
            "https://metabase.wiom.in/api/dataset", data=body,
            headers={"x-api-key": MB_KEY, "Content-Type": "application/json"}), timeout=120).read().decode())
        if isinstance(d, dict) and d.get("error"):
            print("  MB err", str(d["error"])[:120], flush=True); return []
        return d["data"]["rows"]
    except Exception as e:
        print("  MB failed", str(e)[:120], flush=True); return []


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


def mob(p):
    p = (p or "").replace("+", "").strip()
    return p[2:] if p.startswith("91") and len(p) == 12 else p   # strip the +91 PREFIX only


def main():
    gc = gspread.authorize(creds())
    ww = gc.open_by_key(WW_SHEET).worksheet(WW_TAB).get_all_values()
    opt23 = [r[:6] for r in ww[3:] if len(r) >= 6 and r[3].strip() == "Opted In" and r[2].strip() in ("F2", "F3")]
    dec   = [r[:6] for r in ww[3:] if len(r) >= 6 and r[3].strip() == "Declined"]

    ss = gc.open_by_key(OPS_SHEET)
    aud = ss.worksheet(AUDIT_TAB); dcl = ss.worksheet(DECL_TAB)
    aud_ids = set(r[0].strip() for r in aud.get_all_values()[1:] if r and r[0].strip())
    dcl_ids = set(r[0].strip() for r in dcl.get_all_values()[1:] if r and r[0].strip())
    new_opt = [r for r in opt23 if r[0] not in aud_ids]
    new_dec = [r for r in dec if r[0] not in dcl_ids]
    if not new_opt and not new_dec:
        print("no new opt-ins/declines — nothing to append", flush=True); return

    ids = [r[0] for r in new_opt] + [r[0] for r in new_dec]
    inlist = ",".join("'%s'" % c.replace("'", "") for c in ids)
    # Mobile + Zone from CSP_ACCOUNT. Zone = first token of LOGICAL_GROUP ("Zone,AM-name,ver");
    # LOGICAL_GROUP is only populated for some CSPs, so zone can be blank.
    acct = {r[0]: (mob(str(r[1] or "")), (r[2] or "").strip()) for r in mb(
        f"SELECT CSP_ID, MOBILE_NUMBER, SPLIT_PART(LOGICAL_GROUP, ',', 1) "
        f"FROM PROD_DB.CSP_GATEWAY_SERVICE_CSP_GATEWAY_SERVICE.CSP_ACCOUNT "
        f"WHERE CSP_ID IN ({inlist}) AND _FIVETRAN_ACTIVE=TRUE")}

    opt_rows = []
    for r in new_opt:
        c = r[0]; m, z = acct.get(c, ("", ""))
        opt_rows.append([c, r[1], r[2], "Opted In", r[4], r[5], TYPE.get(r[2], "Audit Pending"), m, z])
    dec_rows = []
    for r in new_dec:
        c = r[0]; m, z = acct.get(c, ("", ""))
        dec_rows.append([c, r[1], r[2], "Declined", r[4], r[5], m, z])

    if opt_rows: aud.append_rows(opt_rows, value_input_option="RAW")
    if dec_rows: dcl.append_rows(dec_rows, value_input_option="RAW")
    print(f"appended {len(opt_rows)} opt-ins (F2/F3) + {len(dec_rows)} declines", flush=True)


if __name__ == "__main__":
    main()
