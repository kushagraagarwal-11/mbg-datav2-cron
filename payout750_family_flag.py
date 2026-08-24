# -*- coding: utf-8 -*-
"""Payout750 FAMILY-LEVEL suppression flag.

CleverTap's "Have Not Done" suppression is per-IDENTITY, so when one member of a CSP
(e.g. the admin) opts in, the OTHER members (e.g. the owner) — different userIds — keep
seeing the banner. This closes that gap:

  once ANY member of a cspid does ANY action — opts in (DOMINANCE_CONSENT = OPTED_IN, real-time)
  OR declines (Payout750_Declined / Closed choice=later, CleverTap) — write p750_decided = "true"
  to ALL CleverTap identities of that cspid. So admin accept/reject suppresses the owner, and vice versa.

Then add ONE rule to each flow's "Who":  p750_decided  does not equal  "true".
Profile writes fire NOTHING (safe to run every 5 min; idempotent).

Env: CT_PASS (CleverTap passcode) [, CLEVERTAP_ACCOUNT, CLEVERTAP_REGION, METABASE_KEY].
"""
import os, sys, json, time, datetime, urllib.request
sys.stdout.reconfigure(encoding="utf-8")

CT_ACC  = os.environ.get("CLEVERTAP_ACCOUNT", "44Z-644-777Z")
CT_PASS = os.environ.get("CT_PASS") or os.environ.get("CLEVERTAP_PASSCODE")
REGION  = os.environ.get("CLEVERTAP_REGION", "eu1")
CT      = f"https://{REGION}.api.clevertap.com"
HCT     = {"X-CleverTap-Account-Id": CT_ACC, "X-CleverTap-Passcode": CT_PASS, "Content-Type": "application/json; charset=utf-8"}
HG      = {"X-CleverTap-Account-Id": CT_ACC, "X-CleverTap-Passcode": CT_PASS}
MB_KEY  = os.environ.get("METABASE_KEY", "mb_1dsbxsJfyROPsVyNpifJ8hTTlIDG85+qNKRo91KDnb4=")
FLAG    = os.environ.get("P750_FLAG_PROP", "p750_decided")
FROM    = int(os.environ.get("P750_FROM", "20260818"))


def mb(sql):
    body = json.dumps({"database": 113, "type": "native", "native": {"query": sql}}).encode()
    d = json.loads(urllib.request.urlopen(urllib.request.Request(
        "https://metabase.wiom.in/api/dataset", data=body,
        headers={"x-api-key": MB_KEY, "Content-Type": "application/json"}), timeout=180).read().decode())
    if isinstance(d, dict) and d.get("error"):
        raise SystemExit("MB ERROR: " + str(d["error"])[:300])
    return d["data"]["rows"]


def export(ev, frm, to):
    body = json.dumps({"event_name": ev, "from": frm, "to": to}).encode()
    try:
        d = json.loads(urllib.request.urlopen(urllib.request.Request(CT + "/1/events.json?batch_size=1000", data=body, headers=HCT), timeout=90).read().decode())
        cur = d.get("cursor"); recs = []; p = 0
        while cur and p < 300:
            dd = json.loads(urllib.request.urlopen(urllib.request.Request(CT + "/1/events.json?cursor=" + cur, headers=HG), timeout=90).read().decode())
            recs += dd.get("records", []); cur = dd.get("next_cursor"); p += 1
            if not cur: break
        return recs
    except Exception as e:
        print("  export failed", ev, str(e)[:80], flush=True); return []


def declined_cspids():
    """cspids that DECLINED via the banner (Payout750_Declined, or Closed with choice=later)."""
    s = set()
    tom = int((datetime.datetime.now() + datetime.timedelta(days=1)).strftime("%Y%m%d"))
    for ev in ("Payout750_Declined", "Payout750_Closed"):
        for r in export(ev, FROM, tom):
            ep = r.get("event_props", {}) or {}
            if ev.endswith("Declined") or (ep.get("choice") == "later"):
                c = (r.get("profile", {}).get("profileData", {}) or {}).get("cspid", "")
                if c: s.add(c)
    return s


def main():
    if not CT_PASS:
        raise SystemExit("CT_PASS / CLEVERTAP_PASSCODE not set")
    # 1) every cspid where ANYONE did ANY action — opted in (backend, real-time) OR declined
    #    (CleverTap banner event). Either accept or reject by any family member flags the CSP.
    opted = set(str(r[0]) for r in mb(
        "SELECT DISTINCT CSP_ID FROM PROD_DB.CSP_RV_SERVICE_CSP_RV_SERVICE.DOMINANCE_CONSENT "
        "WHERE CONSENT_CHOICE='OPTED_IN' AND COALESCE(_FIVETRAN_DELETED,FALSE)=FALSE AND CSP_ID IS NOT NULL"))
    declined = declined_cspids()
    decided = sorted(opted | declined)
    print(f"decided cspids: {len(decided)}  (opted {len(opted)} + declined {len(declined)})", flush=True)
    if not decided:
        print("no decided cspids — nothing to flag"); return
    inlist = ",".join("'%s'" % c.replace("'", "") for c in decided)
    # 2) ALL CleverTap identities of those cspids (owner + admin + any) from the profile mirror.
    #    NOTE: PROFILE_DATA's column is CSPID (no underscore), unlike DOMINANCE_CONSENT's CSP_ID.
    idents = sorted({str(r[0]) for r in mb(
        f"SELECT DISTINCT IDENTITY FROM PROD_DB.CLEVERTAP_CSP_API.PROFILE_DATA "
        f"WHERE CSPID IN ({inlist}) AND COALESCE(_FIVETRAN_DELETED,FALSE)=FALSE AND IDENTITY IS NOT NULL") if r[0]})
    print(f"identities to flag={len(idents)}", flush=True)
    # 3) write FLAG='true' to every one of them — profile upload fires no campaign
    B, ok, unproc = 500, 0, 0
    for i in range(0, len(idents), B):
        batch = idents[i:i + B]
        body = json.dumps({"d": [{"identity": x, "type": "profile", "profileData": {FLAG: "true"}} for x in batch]}).encode()
        for attempt in range(3):
            try:
                r = json.loads(urllib.request.urlopen(urllib.request.Request(CT + "/1/upload", data=body, headers=HCT), timeout=90).read().decode())
                ok += r.get("processed", 0); unproc += len(r.get("unprocessed", []) or [])
                break
            except Exception as e:
                print("  retry upload", str(e)[:80], flush=True); time.sleep(4)
    print(f"flagged {ok} profiles {FLAG}=true  (unprocessed {unproc})", flush=True)


if __name__ == "__main__":
    main()
