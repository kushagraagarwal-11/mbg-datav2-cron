"""Six live CSPs: atom vs splitter overlap, bookings in the atom (baseline / since go-live / L30D, new vs old vs no lead), task funnel."""
import json, io, math, datetime as dt
from collections import Counter, defaultdict
J = lambda n: json.load(io.open(n, encoding="utf-8"))
geo, spl, tasks, ev, tas, la, bk = J("static/geo.json"), J("splitters.json"), J("tasks.json"), J("events.json"), J("tas.json"), J("launches.json"), J("bookings.json")
bka = J("bookings_all.json")   # every booking attempt, for the unpaid count
APR = J("appr_points.json")   # pitch-time unrecoverable leads per window, fetched from the private sheet tab six_live_inputs by refresh.py   # tracker lead map mirror (data to 21 Oct)
            # BD pocket map unrecoverable leads [pocket, lat, lng, date, never_seen_by_N]
briefs = J("static/briefs_live.json")["pockets"]
import os
UI = {r["EC"]: r["SEC_UI_AFTER"] for r in J("ui30.json")} if os.path.exists("ui30.json") else {}
W = {"a0a7h7": dict(name="Ashu Broadband", pitch="2026-09-14", golive="2026-09-24 18:50", parent="Broadband Solution (a0b9z3)", pockets=["6387_17152"]),
     "a0a7h8": dict(name="BB Internet", pitch="2026-09-15", golive="2026-09-24 18:50", parent="ex Stream Broadband (a0a7b9)", pockets=["6374_17171", "6375_17171"]),
     "a0a7h9": dict(name="Mahadev Cable", pitch="2026-09-18", golive="2026-09-29 19:41", parent="Daiwik Broadband (a0c0b8)", pockets=["6359_17119"]),
     "a0a6z8": dict(name="Webcity Broadband (expansion)", pitch="2026-09-15", golive="2026-09-29 19:51", parent="same CSP, 2 new pockets", pockets=["6367_17176", "6367_17175"]),
     "a0a7i4": dict(name="Google Broadband", pitch="2026-09-17", golive="2026-10-21 12:24", parent="Entertainment Broadband (a0b6h4)", pockets=["6344_17176"]),
     "a0a7i8": dict(name="AS Internet Service 2", pitch="2026-10-12", golive="2026-10-23 15:20", parent="AS Internet Services (a0b9i3)", pockets=["6332_17182"]),
     "a0a7i6": dict(name="Pavika Broadband", pitch="2026-10-22", golive="2026-10-23 13:24", parent="Daiwik Broadband (a0c0b8), 2nd window", pockets=[])}
NOW = dt.datetime.strptime(io.open("stamp.txt").read().strip(), "%d %b %Y %H:%M"); L30 = (NOW - dt.timedelta(days=30)).replace(hour=0, minute=0)
P = lambda s: dt.datetime.strptime(s[:16], "%Y-%m-%d %H:%M")
IST = lambda s: P(s) + dt.timedelta(hours=5, minutes=30)      # FACT / TAS timestamps print in UTC
def hav(a, b, c, d):
    p = math.pi / 180; x = math.sin((c - a) * p / 2) ** 2 + math.cos(a * p) * math.cos(c * p) * math.sin((d - b) * p / 2) ** 2
    return 12742000 * math.asin(math.sqrt(x))
def inring(lat, lng, ring):
    c = False
    for i in range(len(ring)):
        a, b = ring[i], ring[i - 1]
        if (a[1] > lng) != (b[1] > lng) and lat < (b[0] - a[0]) * (lng - a[1]) / (b[1] - a[1]) + a[0]: c = not c
    return c
inatom = lambda cid, lat, lng: any(inring(lat, lng, r) for r in geo[cid]["rings"])
def dist_to_atom(cid, lat, lng):
    return min(hav(lat, lng, p[0], p[1]) for r in geo[cid]["rings"] for p in r)
M = {}
evby = defaultdict(dict)
for r in ev: evby[r["EC"]][(r["EVENT_NAME"], r["ROLE"])] = r
tasby = {r["EC"]: r for r in tas}
laby = defaultdict(set)
for r in la:
    if r["ROLE"] in ("OWNER", "MANAGER"): laby[r["CSPID"]].add(r["TS"])
print("owner-open entry sources:", Counter(s for r in ev if r["EVENT_NAME"] == "install_candidate_opened" and r["ROLE"] == "" for s in (r["SRCS"] or "").split("|")).most_common())
for cid, w in W.items():
    g = geo[cid]; pts = spl[cid]["pts"]; m = dict(csp=cid, **w, atom_kind=g["kind"], synced=spl[cid]["synced"], n_pts=len(pts))
    m["pts_inside"] = sum(1 for p in pts if inatom(cid, p[0], p[1]))
    m["pts_near50"] = sum(1 for p in pts if not inatom(cid, p[0], p[1]) and dist_to_atom(cid, p[0], p[1]) <= 50)
    b = g["bbox"]; step = 0.00011; tot = cov = 0; la0 = b[0]; unc = []; i = 0
    while la0 <= b[1]:
        ln = b[2]; j = 0
        while ln <= b[3]:
            if inatom(cid, la0, ln):
                tot += 1
                if any(hav(la0, ln, p[0], p[1]) <= 50 for p in pts): cov += 1
                elif i % 2 == 0 and j % 2 == 0: unc.append([round(la0, 6), round(ln, 6)])   # 24 m cells with no point within 50 m, for the map
            ln += step / math.cos(math.radians(la0)); j += 1
        la0 += step; i += 1
    m["cov_pct"] = round(100 * cov / tot) if tot else None; m["grid_cells"] = tot; m["uncov_pct"] = 100 - m["cov_pct"]; m["uncov"] = unc
    allp = [p for r in g["rings"] for p in r]; c0 = (sum(p[0] for p in allp) / len(allp), sum(p[1] for p in allp) / len(allp))
    if unc:
        u0 = (sum(p[0] for p in unc) / len(unc), sum(p[1] for p in unc) / len(unc))
        d = hav(c0[0], c0[1], u0[0], u0[1]); rad = max(hav(c0[0], c0[1], p[0], p[1]) for p in allp)
        brg = (math.degrees(math.atan2((u0[1] - c0[1]) * math.cos(math.radians(c0[0])), u0[0] - c0[0])) + 360) % 360
        m["uncov_dir"] = "spread across the atom" if d < 0.18 * rad else "mostly the " + ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"][int((brg + 22.5) // 45) % 8] + " part"
    else: m["uncov_dir"] = ""
    m["brief"] = {pk: briefs.get(pk) for pk in w["pockets"]}
    # unrecoverable leads in the atom when we approached him (1 Aug - 14 Sep): tracker lead map (in_pocket, period before, status unrec); AS2 is not in the tracker -> BD pocket map leads
    ap = [list(p) for p in APR.get(cid, {}).get("pts", [])]
    if APR.get(cid, {}).get("src") == "tracker": m["appr_src"] = "tracker lead map (Shreyansh), unrecoverable leads inside the outline before go-live"
    elif w["pockets"]: m["appr_src"] = "BD pocket map (14 Sep brief), unrecoverable leads of the pocket cell"
    else: m["appr_src"] = "no pocket cell is tied to this window (area from Shreyansh's page), so no pitch-time lead list"
    for x in ap: x.append(any(hav(x[0], x[1], p[0], p[1]) <= 50 for p in pts))   # now within 50 m of one of his points
    m["appr"] = ap; m["appr_n"] = len(ap); m["appr_cov"] = sum(1 for x in ap if x[4])
    # bookings: rows in the exact atom, unique customers per window
    rows = [r for r in bk if r["ATOM"] == cid and inatom(cid, r["LAT"], r["LNG"])]
    pitch_end = dt.datetime(2026, 9, 14, 23, 59); gl = P(w["golive"])   # one baseline window for all six: 1 Aug - 14 Sep (Palak, 28 Oct)
    def win(name, lo, hi):
        rs = [r for r in rows if lo <= P(r["BOOKED"]) <= hi]
        cust = {}
        for r in rs: cust.setdefault(r["MH"], []).append(r)
        o = dict(window=name, from_=lo.strftime("%d %b"), to=hi.strftime("%d %b"), days=round((hi - lo).total_seconds() / 86400, 1), bookings=len(rs), customers=len(cust))
        o["unpaid"] = len({r["MH"] for r in bka if r["ATOM"] == cid and inatom(cid, r["LAT"], r["LNG"]) and lo <= P(r["BOOKED"]) <= hi and r["PST"] in (0, None) and r["BST"] is None and not r["CONN"]})
        new = old = nolead = inst_new = inst_old = any_new = 0
        for mh, rr in cust.items():
            cands = [c for r in rr if r["CANDS"] for c in r["CANDS"].split("|")]
            if not cands: nolead += 1; continue
            last = cands[-1].split(":"); cs = [c.split(":") for c in cands]
            if any(c[0] == cid for c in cs): any_new += 1
            if last[0] == cid: new += 1
            else: old += 1
            if any(c[0] == cid and c[-1] == "I" for c in cs): inst_new += 1
            elif any(c[-1] == "I" for c in cs): inst_old += 1
        o.update(new=new, old=old, nolead=nolead, offered_new_any=any_new, inst_new=inst_new, inst_old=inst_old, per30=round(len(cust) * 30 / max(o["days"], 1), 1), perday=round(len(cust) / max(o["days"], 1), 2))
        return o
    m["windows"] = [win("baseline", dt.datetime(2026, 8, 1), pitch_end), win("since go-live", gl, NOW), win("L30D", L30, NOW)]
    m["pins"] = [[r["LAT"], r["LNG"], r["BOOKED"][:10], ("new" if (r["CANDS"] or "").split("|")[-1].split(":")[0] == cid else "old") if r["CANDS"] else "nolead", "I" in [c.split(":")[-1] for c in (r["CANDS"] or "").split("|") if c], P(r["BOOKED"]) >= gl, P(r["BOOKED"]) <= pitch_end] for r in rows]
    # funnel on tasks created since go-live (IST)
    T = [t for t in tasks if t["CSP_ID"] == cid and IST(t["CREATED"]) >= gl]
    f = Counter(); lat_open = []; lat_resp = []
    for t in T:
        e = evby.get(t["EC"], {}); s = tasby.get(t["EC"], {}); f["created"] += 1
        d = e.get(("pn_delivered", "")) or e.get(("fpn_delivered", ""))
        if not d: continue
        f["delivered"] += 1; dts = P(d["FIRST_TS"])
        o = e.get(("install_candidate_opened", ""))
        if o: f["opened"] += 1; lat_open.append((P(o["FIRST_TS"]) - dts).total_seconds() / 60)
        if s.get("CSP_FIRST_AT"): f["responded"] += 1; lat_resp.append((IST(s["CSP_FIRST_AT"]) - dts).total_seconds() / 60)
        if s.get("ACCEPTED_AT"): f["accepted"] += 1
        if s.get("DECLINED_AT"): f["declined"] += 1
        if s.get("TIMEOUT_AT"): f["timeout"] += 1
        if s.get("ASSIGNED_AT"): f["assigned"] += 1
        if s.get("ARRIVED_AT"): f["arrived"] += 1
        if s.get("ACTIVE_AT") or t.get("INST_AT"): f["installed"] += 1
        mins = {(dts + dt.timedelta(minutes=k)).strftime("%Y-%m-%d %H:%M") for k in range(0, 31)}
        if mins & laby[cid]: f["app30"] += 1
        if UI.get(t["EC"]) is not None: f["ui30"] += 1
        if o and (P(o["FIRST_TS"]) - dts).total_seconds() <= 1800: f["open30"] += 1
        if o and (o["SRCS"] or "").find("fpn") >= 0 or e.get(("pn_clicked", "")): f["open_from_push"] += 1
        if not s.get("CSP_FIRST_AT") and not s.get("TIMEOUT_AT") and t["TS"] == "OPEN": f["still_open"] += 1
        if e.get(("pn_clicked", "")): f["pn_clicked"] += 1
    med = lambda xs: round(sorted(xs)[len(xs) // 2]) if xs else None
    m["funnel"] = dict(f); m["funnel"]["med_min_open"] = med(lat_open); m["funnel"]["med_min_resp"] = med(lat_resp)
    m["tasks_all_since_20sep"] = sum(1 for t in tasks if t["CSP_ID"] == cid)
    M[cid] = m
    print(f"\n{cid} {w['name']} | {g['kind']} | pts {len(pts)} inside {m['pts_inside']} near50 {m['pts_near50']} cov {m['cov_pct']}%")
    for o in m["windows"]: print("  ", o)
    print("   funnel", m["funnel"], "| brief", {k: (v or {}).get("unrec") for k, v in m["brief"].items()})
json.dump(M, io.open("metrics.json", "w", encoding="utf-8"), indent=1)
