# -*- coding: utf-8 -*-
"""Payout750 FAMILY-LEVEL suppression flag.

CleverTap's "Have Not Done" suppression is per-IDENTITY, so when one member of a CSP
(e.g. the admin) opts in, the OTHER members (e.g. the owner) — different userIds — keep
seeing the banner. This closes that gap:

  once ANY member of a cspid opts in (DOMINANCE_CONSENT = OPTED_IN, real-time),
  write  p750_decided = "true"  to ALL CleverTap identities of that cspid.

Then add ONE rule to each flow's "Who":  p750_decided  does not equal  "true".
Profile writes fire NOTHING (safe to run every 5 min; idempotent).

Env: CT_PASS (CleverTap passcode) [, CLEVERTAP_ACCOUNT, CLEVERTAP_REGION, METABASE_KEY].
"""
import os, sys, json, time, urllib.request
sys.stdout.reconfigure(encoding="utf-8")

CT_ACC  = os.environ.get("CLEVERTAP_ACCOUNT", "44Z-644-777Z")
CT_PASS = os.environ.get("CT_PASS") or os.environ.get("CLEVERTAP_PASSCODE")
REGION  = os.environ.get("CLEVERTAP_REGION", "eu1")
CT      = f"https://{REGION}.api.clevertap.com"
HCT     = {"X-CleverTap-Account-Id": CT_ACC, "X-CleverTap-Passcode": CT_PASS, "Content-Type": "application/json; charset=utf-8"}
MB_KEY  = os.environ.get("METABASE_KEY", "mb_1dsbxsJfyROPsVyNpifJ8hTTlIDG85+qNKRo91KDnb4=")
FLAG    = os.environ.get("P750_FLAG_PROP", "p750_decided")


def mb(sql):
    body = json.dumps({"database": 113, "type": "native", "native": {"query": sql}}).encode()
    d = json.loads(urllib.request.urlopen(urllib.request.Request(
        "https://metabase.wiom.in/api/dataset", data=body,
        headers={"x-api-key": MB_KEY, "Content-Type": "application/json"}), timeout=180).read().decode())
    if isinstance(d, dict) and d.get("error"):
        raise SystemExit("MB ERROR: " + str(d["error"])[:300])
    return d["data"]["rows"]


def main():
    if not CT_PASS:
        raise SystemExit("CT_PASS / CLEVERTAP_PASSCODE not set")
    # 1) every cspid where ANYONE opted in (real-time backend, authoritative for enrolment)
    decided = [str(r[0]) for r in mb(
        "SELECT DISTINCT CSP_ID FROM PROD_DB.CSP_RV_SERVICE_CSP_RV_SERVICE.DOMINANCE_CONSENT "
        "WHERE CONSENT_CHOICE='OPTED_IN' AND COALESCE(_FIVETRAN_DELETED,FALSE)=FALSE AND CSP_ID IS NOT NULL")]
    if not decided:
        print("no opted-in cspids — nothing to flag"); return
    inlist = ",".join("'%s'" % c.replace("'", "") for c in decided)
    # 2) ALL CleverTap identities of those cspids (owner + admin + any) from the profile mirror.
    #    NOTE: PROFILE_DATA's column is CSPID (no underscore), unlike DOMINANCE_CONSENT's CSP_ID.
    idents = sorted({str(r[0]) for r in mb(
        f"SELECT DISTINCT IDENTITY FROM PROD_DB.CLEVERTAP_CSP_API.PROFILE_DATA "
        f"WHERE CSPID IN ({inlist}) AND COALESCE(_FIVETRAN_DELETED,FALSE)=FALSE AND IDENTITY IS NOT NULL") if r[0]})
    print(f"opted-in cspids={len(decided)}  identities to flag={len(idents)}", flush=True)
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
