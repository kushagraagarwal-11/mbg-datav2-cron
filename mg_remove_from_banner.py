# -*- coding: utf-8 -*-
"""MG pilot — daily 11pm removal.

Reads the "Remove from MG" tab (CSPs flagged on the Enforcement Hub for MG removal)
and DELETES those CSPs' rows from the two banner-audience tabs so they stop being shown
the MG banner:
  - "Show Banner - Install MG"   (col A = CSP ID)
  - "Show Banner - Sehat MG"     (col A = CSP ID)

Matches on CSP ID (col A). Idempotent: a CSP already gone is a no-op. Header row is never
touched. The remove-tab is found by name prefix "Remove from MG" so a date suffix can change.

Env: GOOGLE_SA_JSON (service-account json string) in CI, or a local SA file fallback.
"""
import os, re, sys, json, tempfile
sys.stdout.reconfigure(encoding="utf-8")
import gspread
from google.oauth2 import service_account

SHEET_ID = "1XHqjybQYKyfCpgdraPiL32GBm2R2wf-CguxlHalmu8M"
REMOVE_TAB_PREFIX = "Remove from MG"
BANNER_TABS = ["Show Banner - Install MG", "Show Banner - Sehat MG"]
SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
CSP_RE = re.compile(r"^a0[a-z0-9]{4}$", re.I)


def creds():
    raw = os.environ.get("GOOGLE_SA_JSON", "")
    if raw:
        t = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False); t.write(raw); t.close()
        return service_account.Credentials.from_service_account_file(t.name, scopes=SCOPES)
    for p in (r"C:\Users\Palak Vardhan\Desktop\mbg\mbg-cron\wiom-sheets-writer.json",
              r"C:\Users\Palak Vardhan\dashboard\wiom-sheets-writer.json"):
        if os.path.exists(p):
            return service_account.Credentials.from_service_account_file(p, scopes=SCOPES)
    raise SystemExit("no SA creds (set GOOGLE_SA_JSON)")


def main():
    gc = gspread.authorize(creds())
    ss = gc.open_by_key(SHEET_ID)

    # 1) remove-set = every valid CSP ID in the "Remove from MG" tab (col A)
    rt = next((w for w in ss.worksheets() if w.title.startswith(REMOVE_TAB_PREFIX)), None)
    if rt is None:
        raise SystemExit(f"no tab starting with '{REMOVE_TAB_PREFIX}' found")
    remove = {row[0].strip() for row in rt.get_all_values() if row and CSP_RE.match(row[0].strip())}
    print(f"remove-set from '{rt.title}': {len(remove)} CSP IDs", flush=True)
    if not remove:
        print("nothing to remove — exiting"); return

    seen_anywhere = set()
    # 2) delete matching rows from each banner tab (bottom-up so indices stay valid)
    for tab in BANNER_TABS:
        try:
            ws = ss.worksheet(tab)
        except gspread.WorksheetNotFound:
            print(f"  !! tab '{tab}' not found — skipping", flush=True); continue
        colA = ws.col_values(1)  # 1-based; row 1 = header
        hits = [(i + 1, v.strip()) for i, v in enumerate(colA)
                if i > 0 and v.strip() in remove]           # i>0 => never the header
        seen_anywhere |= {v for _, v in hits}
        if not hits:
            print(f"  '{tab}': 0 to remove (already clean)", flush=True); continue
        reqs = [{"deleteDimension": {"range": {"sheetId": ws.id, "dimension": "ROWS",
                 "startIndex": r - 1, "endIndex": r}}}
                for r, _ in sorted(hits, key=lambda x: -x[0])]   # descending row order
        ss.batch_update({"requests": reqs})
        print(f"  '{tab}': removed {len(hits)} rows -> {sorted(v for _, v in hits)}", flush=True)

    missing = sorted(remove - seen_anywhere)
    if missing:
        print(f"note: {len(missing)} remove-CSPs not present in either banner tab "
              f"(already removed / never in audience): {missing}", flush=True)
    print("done.", flush=True)


if __name__ == "__main__":
    main()
