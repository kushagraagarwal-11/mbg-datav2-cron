r"""Six live CSPs page: per CSP a map (atom outline, uploaded splitter points + 50 m reach, booking pins coloured by who got
them) and the twelve asks as tiles; a summary table on top. Writes newcsp-companion/out/area/six-live.html + a Desktop copy."""
import json, io, os, datetime as dt
STAMP = io.open("stamp.txt").read().strip(); _NOW = dt.datetime.strptime(STAMP, "%d %b %Y %H:%M"); _L30 = (_NOW - dt.timedelta(days=30)); L30LBL = _L30.strftime("%d %b") + " to " + _NOW.strftime("%d %b")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = "out/area/six-live.html"
DESK = None
M = json.load(io.open(os.path.join(HERE, "metrics.json"), encoding="utf-8"))
geo = json.load(io.open(os.path.join(HERE, "static/geo.json"), encoding="utf-8"))
spl = json.load(io.open(os.path.join(HERE, "splitters.json"), encoding="utf-8"))
ORDER = ["a0a7h8", "a0a6z8", "a0a7h7", "a0a7h9", "a0a7i4", "a0a7i6", "a0a7i8"]
pct = lambda a, b: "&ndash;" if not b else f"{round(100 * a / b)}%"
def esc(s): return str(s).replace("&", "&amp;").replace("<", "&lt;")
def win(m, name): return next(w for w in m["windows"] if w["window"] == name)
rows = []; sections = []; DATA = {}
for i, cid in enumerate(ORDER, 1):
    m = M[cid]; f = m["funnel"]; b, g, l = win(m, "baseline"), win(m, "since go-live"), win(m, "L30D")
    led = g["new"] + g["old"] + g["nolead"]
    rows.append(f"""<tr><td><a href="#c_{cid}"><b>{esc(m['name'])}</b></a><br><span class="mut">{cid} &middot; live {dt.datetime.strptime(m['golive'], '%Y-%m-%d %H:%M').strftime('%d %b')}</span></td>
<td><a href="#c_{cid}">map &darr;</a></td><td>{m['appr_n']}<br><span class="mut">{m['appr_cov']} now within 50 m of his points</span></td><td><b>{m['cov_pct']}% covered</b><br><span class="o">{m['uncov_pct']}% not covered</span><br><span class="mut">{m['uncov_dir']}</span></td>
<td>{b['customers']}<br><span class="mut">{b['perday']}/day &middot; {b['per30']}/30 d</span></td><td><b>{g['customers']}</b><br><span class="mut">{g['perday']}/day &middot; {g['per30']}/30 d &middot; since {g['from_']}</span></td><td>{l['customers']}<br><span class="mut">{l['perday']}/day &middot; {l['per30']}/30 d</span></td><td><b>{m['routed_sum']['customers']}</b> ({m['routed_sum']['installed']})<br><span class="mut">{m['routed_sum']['offers']} offers &middot; {m['routed_sum']['outside']} outside the atom</span></td>
<td class="g"><b>{pct(g['new'], led)}</b><br><span class="mut">{g['new']} ({g['inst_new']} inst.)</span></td><td class="o">{pct(g['old'], led)}<br><span class="mut">{g['old']} ({g['inst_old']} inst.)</span></td><td class="mut">{pct(g['nolead'], led)}<br><span class="mut">{g['nolead']}</span></td>
<td>{pct(f.get('delivered', 0), f.get('created', 0))}<br><span class="mut">{f.get('delivered', 0)} of {f.get('created', 0)}</span></td><td>{pct(f.get('opened', 0), f.get('delivered', 0))}</td><td>{pct(f.get('responded', 0), f.get('delivered', 0))}<br><span class="mut">{f.get('timeout', 0)} timed out</span></td>
<td>{pct(f.get('assigned', 0), f.get('delivered', 0))}</td><td>{pct(f.get('arrived', 0), f.get('delivered', 0))}</td><td><b>{pct(f.get('installed', 0), f.get('delivered', 0))}</b><br><span class="mut">{f.get('installed', 0)} installed</span></td>
<td>{pct(f.get('ui30', 0), f.get('delivered', 0))}<br><span class="mut">card {pct(f.get('open30', 0), f.get('delivered', 0))}</span></td></tr>""")
    rt = m["routed_sum"]
    wt = "".join(f"<tr><td>{w['window']}<br><span class='mut'>{w['from_']}&ndash;{w['to']}, {round(w['days'])} d</span></td><td><b>{w['customers']}</b></td><td>{w['perday']}</td><td>{w['per30']}</td><td class='g'>{w['new']}<span class='mut'> ({w['inst_new']})</span></td><td class='o'>{w['old']}<span class='mut'> ({w['inst_old']})</span></td><td class='mut'>{w['nolead']}</td><td class='mut'>{w['unpaid']} unpaid attempts not counted</td></tr>" for w in m["windows"])
    brief = ", ".join(f"{k}: {v['unrec']} unrecoverable (rank {v['rank']})" if v else f"{k}: not in the 14 Sep brief (rank 165 list: 5 unrecoverable)" for k, v in m["brief"].items())
    fun = "" if not f.get("created") else f"""<table class="t"><tr><th>step</th><th>tasks</th><th>of delivered</th></tr>
<tr><td>tasks created for him (routing offers)</td><td>{f.get('created',0)}</td><td class="mut">{pct(f.get('created',0), f.get('delivered',0))}</td></tr>
<tr><td>push delivered to his phone</td><td>{f.get('delivered',0)}</td><td>{pct(f.get('delivered',0), f.get('created',0))} of created</td></tr>
<tr><td>task card opened by owner / manager</td><td>{f.get('opened',0)}</td><td>{pct(f.get('opened',0), f.get('delivered',0))}</td></tr>
<tr><td>responded (accepted with a slot, declined, or assigned a technician)</td><td>{f.get('responded',0)}</td><td>{pct(f.get('responded',0), f.get('delivered',0))}</td></tr>
<tr><td>technician assigned</td><td>{f.get('assigned',0)}</td><td>{pct(f.get('assigned',0), f.get('delivered',0))}</td></tr>
<tr><td>technician arrived at site</td><td>{f.get('arrived',0)}</td><td>{pct(f.get('arrived',0), f.get('delivered',0))}</td></tr>
<tr><td>installed (connection active)</td><td>{f.get('installed',0)}</td><td>{pct(f.get('installed',0), f.get('delivered',0))}</td></tr>
<tr><td>app open within 30 min of delivery (any owner / manager activity in the app)</td><td>{f.get('ui30',0)}</td><td>{pct(f.get('ui30',0), f.get('delivered',0))}</td></tr>
<tr><td>task card opened within 30 min of delivery</td><td>{f.get('open30',0)}</td><td>{pct(f.get('open30',0), f.get('delivered',0))}</td></tr>
<tr><td class="mut">no response, timed out (P41)</td><td class="mut">{f.get('timeout',0)}</td><td class="mut">{pct(f.get('timeout',0), f.get('delivered',0))}</td></tr>
<tr><td class="mut">declined by him</td><td class="mut">{f.get('declined',0)}</td><td class="mut">{pct(f.get('declined',0), f.get('delivered',0))}</td></tr></table>
<p class="mut">Median time from push to first response: {f.get('med_min_resp')} min. Tasks since go-live only; {m['tasks_all_since_20sep'] - f.get('created',0)} earlier tasks (20 Sep onwards) left out.</p>"""
    if not f.get("created"): fun = f"<p><b>No task has ever been created for {esc(m['name'])}</b>, so there is no funnel to measure. Routing has never listed him as a candidate: he has no Genie registration (T_CSP / T_SPLITTER), so his {m['n_pts']} uploaded points are invisible to the 50 m rule.</p>"
    sections.append(f"""<section class="card" id="c_{cid}"><h2>{i}. {esc(m['name'])} <span class="mut">{cid} &middot; parent {esc(m['parent'])} &middot; pitched {m['pitch'][8:]} {dt.datetime.strptime(m['pitch'], '%Y-%m-%d').strftime('%b')} &middot; live {dt.datetime.strptime(m['golive'], '%Y-%m-%d %H:%M').strftime('%d %b %H:%M')}</span></h2>
<div class="row"><div class="mapwrap"><div class="map" id="m_{cid}"></div><div class="legend"><i style="border:2px solid #D9008D;background:rgba(217,0,141,.1)"></i> atom ({esc(m['atom_kind'])}) &nbsp; <b>black ring</b> = installed<br>
<label><input type="checkbox" id="spl_{cid}" checked> <i style="background:#7B4A12"></i> his splitter mapping, {m['n_pts']} uploaded points (synced {m['synced']}) with 50 m reach</label> &nbsp;
<label><input type="checkbox" id="unc_{cid}" checked> <i style="background:#E8590C;border-radius:2px;opacity:.5"></i> part of the atom not covered (no point within 50 m)</label> &nbsp;
<label><input type="checkbox" id="appr_{cid}" checked> <i style="background:#fff;border:2px solid #D9008D"></i> unrecoverable leads when we approached him (1 Aug&ndash;14 Sep), {m['appr_n']}; filled brown = now within 50 m of his points ({m['appr_cov']})</label> &nbsp;
<label><input type="checkbox" id="now_{cid}" checked> <i style="background:#138A3E"></i> paid booking since go-live routed to him &nbsp; <i style="background:#E8590C"></i> to an old CSP &nbsp; <i style="background:#8A8599"></i> paid, no lead found</label> &nbsp;
<label><input type="checkbox" id="pre_{cid}"> <i style="background:#fff;border:2px solid #E8590C"></i> paid bookings 1 Aug&ndash;14 Sep (hollow)</label><br>
<label><input type="checkbox" id="rt_{cid}" checked> <i class="dia" style="background:#138A3E"></i> leads routed to him since go-live, all his zones ({rt['customers']} customers, {rt['offers']} offers): green = accepted / installed, amber = still open, red = declined by him, grey = timed out or visit failed</label></div>
<div class="facts">
<div class="tile"><b>{m['cov_pct']}%</b><span>of the atom shared with him is covered (within 50 m of one of his points, what routing needs)</span></div>
<div class="tile"><b class="o" style="color:#E8590C">{m['uncov_pct']}%</b><span>of the atom is still not covered, {esc(m['uncov_dir']) or 'none left'}; shaded orange on the map</span></div>
<div class="tile"><b>{m['pts_inside']} / {m['n_pts']}</b><span>of his points lie inside the atom; {m['pts_near50']} more within 50 m of its edge</span></div>
<div class="tile"><b>{m['appr_cov']} / {m['appr_n']}</b><span>of the unrecoverable leads we showed him at the pitch (1 Aug&ndash;14 Sep) now sit within 50 m of his points</span></div>
<h3>Confirmed bookings inside the atom <span class="mut">(paid bookings, unique customers; routed to = who finally held the lead; (n) = installed)</span></h3>
<table class="t"><tr><th>window</th><th>customers</th><th>per day</th><th>per 30 d</th><th class="g">to him</th><th class="o">to old CSPs</th><th>paid, lead not found</th></tr>{wt}</table>
<p class="mut">BD brief at the pitch, same pockets: {esc(brief)}. Of these bookings inside the atom, offered to him at any point since go-live: {g['offered_new_any']}.</p>
<h3>Leads routed to him since go-live <span class="mut">(every lead routing offered him, all his zones, not only the atom; one row per customer; diamonds on the map)</span></h3>
<table class="t"><tr><th>customers</th><th>offers</th><th>pin inside the atom</th><th>pin outside the atom</th><th class="g">accepted</th><th class="g">installed</th><th>still with him</th><th class="o">declined by him</th><th class="o">timed out</th><th>visit failed / other</th></tr>
<tr><td><b>{rt['customers']}</b></td><td>{rt['offers']}</td><td>{rt['inside']}</td><td>{rt['outside']}{(' (+%d with no pin)' % rt['nopin']) if rt['nopin'] else ''}</td><td class="g">{rt['accepted']}</td><td class="g"><b>{rt['installed']}</b></td><td>{rt['open']}</td><td class="o">{rt['declined']}</td><td class="o">{rt['timeout']}</td><td class="mut">{rt['failed']}</td></tr></table>
<h3>Task funnel since go-live <span class="mut">(every install task routed to him, all his zones, not only the atom)</span></h3>{fun}</div></div></section>""")
    DATA[cid] = dict(rings=geo[cid]["rings"], pts=spl[cid]["pts"], pins=m["pins"], routed=m["routed"], name=m["name"], unc=m["uncov"], pct=m["uncov_pct"], appr=m["appr"])

# ---- Go-live checklist (29 Oct scorecard from the "Expansion windows: go-live health checks" doc; coverage % from this page) ----
# each item: (state, small note). state: ok | weak | fail
CHECK = {
 "a0a7h8": dict(spl=("ok","115 pts"), genie=("ok","113"), expo=("ok","FULL, 36 of 100"), enf=("ok",""), route=("ok","won 79"), leads=("ok","66 leads, 39 installs"), verdict=("ok","Live, healthy")),
 "a0a6z8": dict(spl=("ok","469 pts"), genie=("ok","469"), expo=("weak","REDUCED, SLA at risk"), enf=("weak","pattern watch"), route=("ok","won 87"), leads=("ok","75 leads, 39 installs"), verdict=("weak","Live, SLA at risk")),
 "a0a7h7": dict(spl=("ok","33 pts"), genie=("ok","33"), expo=("ok","4 of 100"), enf=("ok",""), route=("ok","won 8"), leads=("weak","8 leads, 4 installs"), verdict=("weak","Live, thin")),
 "a0a7h9": dict(spl=("ok","41 pts"), genie=("ok","41"), expo=("ok","0 of 100"), enf=("ok",""), route=("ok","won 5"), leads=("weak","5 leads, 0 installs"), verdict=("weak","Live, not converting")),
 "a0a7i4": dict(spl=("ok","294 pts"), genie=("ok","294"), expo=("ok","0 of 100"), enf=("ok",""), route=("ok","won 2 of 19"), leads=("ok","2 leads, 1 accepted"), verdict=("ok","Live since 28 Oct")),
 "a0a7i6": dict(spl=("ok","424 pts, re-uploaded 27 Oct"), genie=("ok","424"), expo=("ok","0 of 50"), enf=("ok",""), route=("weak","in the list for 3 of 43 bookings since Genie (28 Oct), won 1"), leads=("weak","1 lead 28 Oct 21:13, timed out 29 Oct 15:00"), verdict=("weak","Live since 28 Oct, thin")),
 "a0a7i8": dict(spl=("ok","114 pts"), genie=("ok","114"), expo=("ok","0 of 50"), enf=("ok",""), route=("fail","no booking in its zone since 13 Oct"), leads=("fail","0"), verdict=("fail","Registered, no demand in its zone")),
}
_rt = {r["CSP_ID"]: r for r in json.load(io.open("routing.json", encoding="utf-8"))}
_tk = json.load(io.open("tasks.json", encoding="utf-8"))
for _c in CHECK:
    r = _rt.get(_c)
    if r and r.get("RECEIVED") is not None:
        st = CHECK[_c]["route"][0]
        if r["WON"] == 0 and r["RECEIVED"] == 0: st = "fail"
        elif r["WON"] > 0 and st == "fail": st = "weak"
        CHECK[_c]["route"] = (st, "in the list %d times, won %d%s" % (r["RECEIVED"], r["WON"], (", last " + r["LAST_WON"]) if r.get("LAST_WON") else ""))
    conns = {t["CONN"] for t in _tk if t["CSP_ID"] == _c}; inst = {t["CONN"] for t in _tk if t["CSP_ID"] == _c and (t["INST_AT"] or t["ST"] == "CONNECTION_ACTIVE")}
    ls = CHECK[_c]["leads"][0]
    if not conns: ls = "fail"
    elif ls == "fail": ls = "weak"
    CHECK[_c]["leads"] = (ls, "%d leads, %d installs since 20 Sep" % (len(conns), len(inst)))
def tick(state, note=""):
    g = {"ok": "&#10003;", "weak": "&#10003;", "fail": "&#10007;"}[state]
    return '<span class="ck %s">%s</span>' % (state, g) + ('<br><span class="mut">%s</span>' % esc(note) if note else "")
chk_rows = []
for cid in ORDER:
    m = M[cid]; c = CHECK[cid]; cov = m["cov_pct"]
    cov_state = "ok" if cov >= 50 else "fail"
    chk_rows.append('<tr><td><a href="#c_%s"><b>%s</b></a><br><span class="mut">%s</span></td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td><span class="vd %s">%s</span></td></tr>' % (
        cid, esc(m["name"]), cid, tick(cov_state, "%s%% covered" % cov), tick(*c["spl"]), tick(*c["genie"]), tick(*c["expo"]), tick(*c["enf"]), tick(*c["route"]), tick(*c["leads"]), c["verdict"][0], esc(c["verdict"][1])))
CHK_HTML = ('<section class="card"><h2>Go-live checklist</h2><div class="tw"><table id="chk"><tr><th>CSP</th>'
    '<th>1 Atom coverage<br><span class="mut">share of the atom within 50 m of his points</span></th><th>2 Splitter mapping uploaded</th><th>3 Genie</th>'
    '<th>4 Exposure<br><span class="mut">zone data set open</span></th><th>5 Enforcement</th><th>6 Routing sees it</th><th>7 Leads arrived</th><th>Verdict</th></tr>'
    + "".join(chk_rows) + '</table></div>'
    '<p class="mut">Tick = passes; amber tick = passes but weak (reduced exposure, pattern watch, thin or non-converting leads); cross = fails. Atom coverage ticks at 50% or more. Routing and leads counts refresh with the page; splitter, Genie, exposure and enforcement states are from the go-live health checks of 29 Oct.</p></section>')

html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Seven live CSPs - atom, splitters, bookings, task funnel</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"><script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
body{{margin:0;background:#F7F5FB;color:#161021;font:14px/1.5 'Segoe UI',system-ui,sans-serif}}header{{background:#fff;border-bottom:1px solid #E6E2EE;padding:14px 20px}}h1{{margin:0;font-size:20px}}h2{{margin:0 0 10px;font-size:17px}}h3{{margin:12px 0 4px;font-size:14px}}
.sub{{margin:4px 0 0;color:#665E75}}.mut{{color:#665E75;font-weight:400;font-size:12px}}.card{{background:#fff;border:1px solid #E6E2EE;border-radius:12px;margin:14px 20px;padding:14px 16px}}
.tw{{overflow:auto}}table{{border-collapse:collapse;width:100%}}#sum th,#sum td{{padding:6px 8px;border-bottom:1px solid #EEEAF4;text-align:left;vertical-align:top;font-size:13px;white-space:nowrap}}#sum th{{background:#F3F0F8;position:sticky;top:0;font-weight:600;font-size:12px;color:#514A63}}
.t th,.t td{{padding:4px 6px;border-bottom:1px solid #EEEAF4;text-align:left;font-size:13px}}.t th{{color:#514A63;font-weight:600;font-size:12px}}
.g{{color:#138A3E}}.o{{color:#B45309}}.row{{display:flex;gap:16px;flex-wrap:wrap}}.mapwrap{{flex:1 1 520px;min-width:300px}}.map{{height:460px;border-radius:10px;border:1px solid #D7D3E0}}.facts{{flex:1 1 420px;min-width:300px}}
.tile{{display:inline-block;background:#F3F0F8;border-radius:8px;padding:8px 12px;margin:0 8px 8px 0;min-width:140px;vertical-align:top}}.tile b{{display:block;font-size:22px;color:#D9008D}}.tile span{{font-size:12px;color:#514A63}}
.legend{{font-size:12px;color:#514A63;margin-top:6px}}.legend i{{display:inline-block;width:11px;height:11px;border-radius:50%;vertical-align:-1px;margin-right:3px}}a{{color:#D9008D}}#chk th,#chk td{{padding:8px 10px;border-bottom:1px solid #EEEAF4;text-align:center;vertical-align:top;font-size:13px}}#chk th{{background:#F3F0F8;font-weight:600;font-size:12px;color:#514A63}}#chk td:first-child,#chk th:first-child{{text-align:left}}.ck{{font-size:22px;font-weight:700;line-height:1}}.ck.ok{{color:#138A3E}}.ck.weak{{color:#B45309}}.ck.fail{{color:#C92A2A}}.vd{{font-weight:600}}.vd.ok{{color:#138A3E}}.vd.weak{{color:#B45309}}.vd.fail{{color:#C92A2A}}ul{{margin:0;padding-left:18px}}li{{margin:3px 0}}
.legend i.dia{{border-radius:2px;transform:rotate(45deg);width:9px;height:9px}}.rtd div{{width:12px;height:12px;border:2px solid #fff;box-shadow:0 0 0 1.5px #161021;transform:rotate(45deg)}}
@media(max-width:700px){{.card{{margin:10px 8px;padding:10px}}.map{{height:340px}}}}
</style></head><body>
<header><h1>Seven live expansion CSPs: atom vs splitter map, bookings in the atom, task funnel</h1><p class="sub">Atom = the pocket we asked each CSP to expand into (tracker outline). Baseline = confirmed bookings in the atom from 1 Aug to 14 Sep, the same window for all six. <b>Last refreshed {STAMP} IST</b> (auto-refresh every 2 hours) from Dynamo PARTNER_COORDINATES, the booking tables, FACT_INSTALL_CANDIDATES, the TAS state log and the CSP-app CleverTap events.</p></header>
{CHK_HTML}
<section class="card"><h2>At a glance</h2><div class="tw"><table id="sum"><tr><th>CSP</th><th>overlap map</th><th>unrecoverable leads at the pitch<br><span class="mut">in the atom, 1 Aug to 14 Sep</span></th><th>atom covered by his splitter mapping<br><span class="mut">within 50 m of one of his points</span></th><th>confirmed bookings: baseline<br><span class="mut">1 Aug to 14 Sep</span></th><th>since go-live</th><th>last 30 days</th><th>leads routed to him since go-live<br><span class="mut">all his zones; customers (installed)</span></th><th class="g">% to him</th><th class="o">% to old CSPs</th><th>% paid, lead not found</th><th>task delivery<br><span class="mut">tasks = all his zones, not only the atom</span></th><th>task open</th><th>response</th><th>assign</th><th>tech arrive</th><th>install</th><th>app open &le;30 min<br><span class="mut">card = opened that task's card</span></th></tr>{''.join(rows)}</table></div>
<p class="mut">A booking counts only once its payment confirmed it (payment status or booking state set; unpaid attempts are listed per CSP but not counted). Percent to him / to old CSPs / lead not found are over confirmed bookings since go-live and add up to 100%. Funnel rates are over tasks delivered since go-live. Google Broadband, Pavika Broadband and AS Internet Service 2 were registered in Genie only on 28 Oct (T_CSP rows at 20:40 IST), so routing could not see their splitters before that. Google got its first two tasks on 28 Oct 18:57 and 29 Oct 07:58; AS Internet Service 2 still has none because its only authorised zone, Shakti Vihar 569, has had no booking since 13 Oct. Data to {STAMP} IST.</p></section>
{''.join(sections)}
<section class="card"><h2>How these numbers are made</h2><ul>
<li><b>Atom:</b> the tracker outline for the pocket (Ashu, BB Internet, Mahadev polygons; Google one 300 m circle; Webcity two 300 m circles). AS Internet Service 2 has no tracker outline, so his 450 m BD planning cell 6332_17182 is used. Pavika Broadband (Daiwik's 2nd window) uses the three location outlines from Shreyansh's 'Daiwik 2 - area to be covered' page.</li>
<li><b>Unrecoverable leads at the pitch:</b> Shreyansh's tracker lead map (mirror of 21 Oct): leads inside the outline, created 1 Aug to 14 Sep, status unrecoverable at the time; AS Internet Service 2 is not in the tracker, so its BD pocket map leads (14 Sep brief) are used. "Now within 50 m" tests each of those leads against his current uploaded points.</li>
<li><b>Covered / not covered:</b> his latest Dynamo PARTNER_COORDINATES upload. Covered = share of the atom's area within 50 m of one of his points, sampled on a 12 m grid; the orange shading on the map is the rest, and the direction is where the centre of that uncovered area sits relative to the atom's centre; routing offers a booking to a CSP only when one of his Genie splitters is within 50 m.</li>
<li><b>Bookings:</b> BOOKING_SERVICE_JAVA + DYNAMODB_READ bookings with a pin inside the atom whose payment confirmed them (payment status not 0, or a booking state set), one per customer per window; unpaid attempts (status 0, no state, no connection) are shown as a count but not counted. Routed to = the CSP who held the lead last (FACT_INSTALL_CANDIDATES via the enriched booking table); lead not found = the booking was paid but no install lead could be linked to it through either the enriched booking table or TASKVANILLA (mobile to connection), for example refunded before a connection was created. Baseline = 1 Aug to 14 Sep for every CSP; since go-live = from the exposure / campaign go-live; last 30 days = {L30LBL}; per 30 d normalises the windows.</li>
<li><b>Leads routed to him:</b> FACT_INSTALL_CANDIDATES rows for the CSP since go-live, any zone, one row per customer (connection). The diamond on the map is the TASKVANILLA pin of that connection, so a lead shows even when it falls outside the atom outline; the table counts pins inside and outside the outline. Outcome = the last offer to him: installed, still with him (task open), declined by him, timed out (P41, no response), or visit failed; accepted = he assigned a technician at least once.</li>
<li><b>Task funnel:</b> tasks = FACT_INSTALL_CANDIDATES rows for the CSP since go-live. Delivered = CleverTap pn_delivered / fpn_delivered on his phone; opened = install_candidate_opened on an owner or manager device; responded / assigned / arrived / installed = TAS INSTALL_STATE_TRANSITION_LOG (CSP action, TECHNICIAN_ASSIGNED, ARRIVED_AT_SITE, CONNECTION_ACTIVE). App open &le;30 min = any owner / manager app activity within 30 min of delivery; the full-screen push itself wakes the app, so the stricter "card opened &le;30 min" is shown under it.</li>
</ul></section>
<script>
var D={json.dumps(DATA, separators=(',', ':'))};
var COL={{"new":"#138A3E","old":"#E8590C","nolead":"#8A8599"}};
var RC={{"installed":"#138A3E","open":"#B45309","declined":"#C92A2A","timeout":"#8A8599","failed":"#8A8599","cx_cancel":"#8A8599","other":"#8A8599"}};
var TXT={{"installed":"installed","open":"still with him","declined":"declined by him","timeout":"no response, timed out","failed":"visit failed","cx_cancel":"cancelled by the customer","other":"closed"}};
Object.keys(D).forEach(function(cid){{var d=D[cid];var map=L.map('m_'+cid,{{scrollWheelZoom:false}});
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{{z}}/{{y}}/{{x}}',{{maxZoom:19,attribution:'Tiles &copy; Esri'}}).addTo(map);
var atom=L.polygon(d.rings,{{color:'#D9008D',weight:3,fillColor:'#D9008D',fillOpacity:.08}}).bindTooltip('Atom offered to '+d.name,{{sticky:true}}).addTo(map);
var gS=L.layerGroup().addTo(map),gU=L.layerGroup().addTo(map),gP=L.layerGroup().addTo(map),gA=L.layerGroup().addTo(map),gB=L.layerGroup();var gR=gS;var gT=L.layerGroup().addTo(map);
var h=0.00011,w=0.00011/Math.cos(d.rings[0][0][0]*Math.PI/180);if(d.unc.length)L.polygon(d.unc.map(function(c){{return [[c[0]-h,c[1]-w],[c[0]-h,c[1]+w],[c[0]+h,c[1]+w],[c[0]+h,c[1]-w]];}}),{{stroke:false,fillColor:'#E8590C',fillOpacity:.32,interactive:false}}).addTo(gU);
d.appr.forEach(function(a){{L.circleMarker([a[0],a[1]],{{radius:5.5,color:'#D9008D',weight:2,fillColor:a[4]?'#C9A27A':'#fff',fillOpacity:a[4]?.9:.7}}).bindTooltip('Unrecoverable lead at the pitch, '+a[2]+(a[3]?' &middot; offered to '+a[3]:' &middot; offered to nobody')+(a[4]?' &middot; now within 50 m of his points':' &middot; still not within 50 m of his points')).addTo(gP);}});
d.pts.forEach(function(p){{L.circle(p,{{radius:50,color:'#7B4A12',weight:.5,opacity:.3,fillColor:'#7B4A12',fillOpacity:.07,interactive:false}}).addTo(gR);L.circleMarker(p,{{radius:3,color:'#fff',weight:.6,fillColor:'#7B4A12',fillOpacity:.95}}).bindTooltip('his splitter mapping (uploaded point)').addTo(gS);}});
d.pins.forEach(function(b){{if(!b[5]&&!b[6])return;var m=b[5]?L.circleMarker([b[0],b[1]],{{radius:5.5,color:b[4]?'#161021':'#fff',weight:b[4]?2:1,fillColor:COL[b[3]],fillOpacity:.95}}):L.circleMarker([b[0],b[1]],{{radius:5,color:COL[b[3]],weight:2,fill:true,fillColor:'#fff',fillOpacity:.6,dashArray:b[4]?null:'2,2'}});m.bindTooltip((b[5]?'Paid booking since go-live, ':'Paid booking when we approached him (1 Aug&ndash;14 Sep), ')+b[2]+' &middot; '+(b[3]==='new'?'routed to '+d.name:b[3]==='old'?'routed to an old CSP':'paid, no lead found')+(b[4]?' &middot; installed':''));m.addTo(b[5]?gA:gB);}});
d.routed.forEach(function(r){{if(r.lat==null)return;var col=RC[r.out]||'#8A8599';var mk=L.marker([r.lat,r.lng],{{icon:L.divIcon({{className:'rtd',html:'<div style="background:'+col+'"></div>',iconSize:[14,14],iconAnchor:[7,7]}})}});mk.bindTooltip('Lead routed to '+d.name+' '+r.first+(r.offers>1?' ('+r.offers+' offers, last '+r.last+')':'')+' &middot; '+(r.inside?'inside the atom':r.dist+' m outside the atom')+' &middot; '+TXT[r.out]+(r.out==='open'?' ('+r.state.toLowerCase().replace(/_/g,' ')+')':'')+(r.accepted&&r.out!=='installed'?' &middot; he assigned a technician':''));mk.addTo(gT);}});
[['spl_',gS],['unc_',gU],['appr_',gP],['now_',gA],['pre_',gB],['rt_',gT]].forEach(function(t){{document.getElementById(t[0]+cid).addEventListener('change',function(e){{if(e.target.checked)t[1].addTo(map);else map.removeLayer(t[1]);}});}});
var bb=atom.getBounds().pad(0.35);d.routed.forEach(function(r){{if(r.lat!=null&&r.dist<=1500)bb.extend([r.lat,r.lng]);}});map.fitBounds(bb);}});
</script></body></html>"""
os.makedirs(os.path.dirname(OUT), exist_ok=True)
io.open(OUT, "w", encoding="utf-8", newline="").write(html)
print("wrote", OUT, len(html), "bytes")
