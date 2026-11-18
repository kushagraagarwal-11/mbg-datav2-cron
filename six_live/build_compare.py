# -*- coding: utf-8 -*-
r"""All live windows on one before / after compare map, ranked.

Reads the six-live pulls (bookings.json = paid bookings pinned inside each window's atom since 1 Aug, with the routing
candidates per booking; metrics.json for names, pitch and go-live; splitters.json; static/geo.json outlines) and writes
out/area/live-compare.html: two synced maps under a swipe divider (left = before go-live, right = since go-live), every
window ranked by the fall in unrecoverable leads per 30 days or by the SE gain. The page computes windows / ranks live
from the embedded pins, so the before / after toggles and the rank switch need no rebuild."""
import json, io, os, re, datetime as dt
HERE = os.path.dirname(os.path.abspath(__file__)); os.chdir(HERE)
J = lambda n: json.load(io.open(n, encoding="utf-8"))
geo, spl, M, bk = J("static/geo.json"), J("splitters.json"), J("metrics.json"), J("bookings.json")
STAMP = io.open("stamp.txt").read().strip(); NOW = dt.datetime.strptime(STAMP, "%d %b %Y %H:%M")
P = lambda s: dt.datetime.strptime(s[:16], "%Y-%m-%d %H:%M")
CLOSED = {"CONNECTION_ACTIVE", "CANCELLED_BY_UPSTREAM", "CANCELLED_BY_CUSTOMER", "DECLINED", "INSTALLATION_REPORTED_FAILED", "CANCELLED", "EXPIRED"}
def inring(lat, lng, ring):
    c = False
    for i in range(len(ring)):
        a, b = ring[i], ring[i - 1]
        if (a[1] > lng) != (b[1] > lng) and lat < (b[0] - a[0]) * (lng - a[1]) / (b[1] - a[1]) + a[0]: c = not c
    return c
inatom = lambda cid, lat, lng: any(inring(lat, lng, r) for r in geo[cid]["rings"])
def short(n): return re.sub(r"\s*\(.*$", "", n or "")
def pin(cid, r):
    """[lat, lng, booked, who, installed, installed by this window, bucket]; who 0 = routed to him last, 1 = to an old CSP, 2 = paid but no lead found;
    bucket 0 installed, 1 open (task still running, or booked in the last 48 h with nothing final), 2 dropped by the customer, 3 nobody installed (unrecoverable)."""
    cands = [c.split(":") for c in (r["CANDS"] or "").split("|") if c]
    inst = any(c[-1] == "I" or c[1] == "CONNECTION_ACTIVE" for c in cands); byhim = any(c[0] == cid and (c[-1] == "I" or c[1] == "CONNECTION_ACTIVE") for c in cands)
    who = 2 if not cands else (0 if cands[-1][0] == cid else 1)
    last = cands[-1][1] if cands else None; age_h = (NOW - P(r["BOOKED"])).total_seconds() / 3600
    if inst: b = 0
    elif last == "CANCELLED_BY_CUSTOMER": b = 2
    elif cands and last not in CLOSED: b = 1
    elif age_h < 48: b = 1
    else: b = 3
    return [r["LAT"], r["LNG"], r["BOOKED"], who, int(inst), int(byhim), b]
wins = []
for cid, m in M.items():
    g = geo[cid]
    rows = [r for r in bk if r["ATOM"] == cid and inatom(cid, r["LAT"], r["LNG"])]
    # one row per customer per day: keep the installed one, else the latest
    byk = {}
    for r in rows:
        k = (r["MH"], r["BOOKED"][:10]); p = pin(cid, r); cur = byk.get(k)
        if cur is None or (p[4] and not cur[4]) or (p[4] == cur[4] and p[2] > cur[2]): byk[k] = p
    pts = sorted(byk.values(), key=lambda p: p[2])
    wins.append(dict(key=g["key"], cid=cid, name=m["name"], sname=short(m["name"]), parent=m["parent"], golive=m["golive"], pitch=m["pitch"], kind=g["kind"],
                     rings=g["rings"], spl=spl.get(cid, {}).get("pts", []), spl_synced=spl.get(cid, {}).get("synced"), cov=m.get("cov_pct"), pts=pts))
    n = len(pts); gl = P(m["golive"])
    print("%-8s %-28s pins %4d | after go-live %3d | installed %3d (by him %2d) | unrec %3d" % (cid, short(m["name"])[:28], n, sum(1 for p in pts if P(p[2]) >= gl),
          sum(p[4] for p in pts if P(p[2]) >= gl), sum(p[5] for p in pts if P(p[2]) >= gl), sum(1 for p in pts if P(p[2]) >= gl and p[6] == 3)))
D = dict(stamp=STAMP, now=NOW.strftime("%Y-%m-%d %H:%M"), base_from="2026-08-01", wins=wins)
tpl = io.open("static/compare_template.html", encoding="utf-8").read()
html = tpl.replace("/*DATA*/null", json.dumps(D, separators=(",", ":"))).replace("{{STAMP}}", STAMP)
OUT = "out/area/live-compare.html"; os.makedirs(os.path.dirname(OUT), exist_ok=True)
io.open(OUT, "w", encoding="utf-8", newline="").write(html)
print("wrote", OUT, len(html), "bytes,", len(wins), "windows")
