"""Forecast track record.

Every check, note what we forecast for each flight (before any airline status
override). Over the week leading up to a flight this builds a line - 7 days
out, 3 days out, the night before, the morning of - so the History page can
show whether the forecast homed in on cancellations or was all over the shop.

data/predictions/YYYY-MM.json (month of the flight):
  {"2026-11-11|YZZ|8P452": {"sched": "10:50", "p": [["2026-11-05T19:07", 4], ...],
                            "outcome": "Canceled"}}
A new point is saved at most every 3 hours unless the forecast moves 5+ points.
"""
import glob, json, os, re
from datetime import datetime, timedelta

DIR = "data/predictions"
PUBLIC = "docs/predictions.json"
FINAL = ("arrived", "departed", "landed", "cancel", "divert")
IN_PROGRESS = {"boarding", "gateclosed", "enroute", "approaching"}


def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _wx_at(reports, t_utc):
    """Airport report closest to the flight time (within 45 min), as raw text + key numbers."""
    from . import aviation
    near = [m for m in reports or [] if abs(m.get("obsTime", 0) - t_utc) <= 45 * 60]
    if not near:
        return None
    m = min(near, key=lambda m: abs(m["obsTime"] - t_utc))
    return {"raw": m.get("rawOb"), "ceiling_ft": aviation.ceiling(m.get("clouds"), m.get("vertVis")),
            "vis_sm": aviation.vis_sm(m.get("visib")), "wx": m.get("wxString")}


def record(results, now, mets=None, stations=None, to_utc=None):
    """stations: {airport code: weather station}; mets: {station: [reports]} (to save the
    weather at each flight's time, flown or not)."""
    stamp = now.strftime("%Y-%m-%dT%H:%M")
    files = {}
    for r in results:
        path = f"{DIR}/{r['date'][:7]}.json"
        recs = files.setdefault(path, _load(path))
        key = f"{r['date']}|{r['airport']}|{r['flight']}"
        rec = recs.setdefault(key, {"sched": r["dep_local"], "direction": r["direction"], "p": []})
        pct = round(100 * r.get("forecast_risk", r["risk"]))
        flight_time = datetime.fromisoformat(f"{r['date']}T{r['dep_local']}")
        if now <= flight_time + timedelta(minutes=30) and not r.get("no_forecast"):
            last = rec["p"][-1] if rec["p"] else None
            if (last is None or abs(pct - last[1]) >= 5
                    or now - datetime.fromisoformat(last[0]) >= timedelta(hours=3)):
                rec["p"].append([stamp, pct])
        if r.get("schedule_source") == "live":
            rec["last_status"], rec["last_status_at"] = r["status"], stamp
            if str(r.get("status", "")).lower().startswith(FINAL) and rec.get("outcome_by") != "owner":
                rec["outcome"] = r["status"]
        st = (stations or {}).get(r["airport"])
        if "wx" not in rec and mets and st and to_utc and now >= flight_time:
            w = _wx_at(mets.get(st), to_utc(flight_time).timestamp())
            if w:
                rec["wx"] = w
    os.makedirs(DIR, exist_ok=True)
    for path, recs in files.items():
        with open(path, "w") as f:
            json.dump(recs, f, separators=(",", ":"))


OPENSKY_LOG = "data/collect/flights.csv"
ICAO = {"YZZ": "CAD4", "YCG": "CYCG"}


def opensky_index():
    """{(date, ICAO, 'arr'/'dep'): [minutes after midnight, ...]} from the nightly OpenSky log."""
    import csv
    idx = {}
    try:
        with open(OPENSKY_LOG) as f:
            for r in csv.DictReader(f):
                if r.get("local_time"):
                    h, m = map(int, r["local_time"].split(":"))
                    idx.setdefault((r["date"], r["airport"], r["direction"]), []).append(h * 60 + m)
    except FileNotFoundError:
        pass
    return idx


def apply_outcomes(rows):
    """Final airline status for past flights (e.g. yesterday afternoon, asked the next morning)."""
    files = {}
    for r in rows:
        if not str(r.get("status", "")).lower().startswith(FINAL):
            continue
        path = f"{DIR}/{r['date'][:7]}.json"
        recs = files.setdefault(path, _load(path))
        rec = recs.setdefault(f"{r['date']}|{r['airport']}|{r['flight']}",
                              {"sched": r["dep_local"], "direction": r["direction"], "p": []})
        if rec.get("outcome_by") != "owner":
            rec["outcome"] = r["status"]
    for path, recs in files.items():
        with open(path, "w") as f:
            json.dump(recs, f, separators=(",", ":"))


def settle(rec, date, now, airport=None, seen=None):
    """Final outcome. If the airline never posted 'Departed/Arrived':
    - the nightly OpenSky log saw a plane leave/arrive within 90 min of the time -> 'Flew (seen by OpenSky)'
    - last seen boarding / gate closed / in the air, now well past -> 'Flew (probably)'.
    A plain 'Expected' is NOT proof it flew: Pacific Coastal sometimes never posts a
    cancellation, so the flight stays 'not confirmed' instead."""
    if rec.get("outcome"):
        return rec["outcome"]
    t = datetime.fromisoformat(f"{date}T{rec['sched']}")
    if seen is not None and airport in ICAO and now > t:
        mins = t.hour * 60 + t.minute
        if any(abs(x - mins) <= 90 for x in seen.get((date, ICAO[airport], rec.get("direction", "dep")), [])):
            return "Flew (seen by OpenSky)"
    at = rec.get("last_status_at")
    if (at and now > t + timedelta(hours=3) and t - datetime.fromisoformat(at) <= timedelta(hours=3)
            and str(rec.get("last_status", "")).lower() in IN_PROGRESS):
        return "Flew (probably)"
    return None


OWNER = "data/owner_outcomes.json"


def owner_outcomes():
    """Outcomes the owner entered by hand - these beat anything the airline feed said."""
    return {k: v for k, v in _load(OWNER).items() if not k.startswith("_")}


def publish(today, days_back=45, now=None):
    cutoff = (today - timedelta(days=days_back)).isoformat()
    out = {}
    owner = owner_outcomes()
    seen = opensky_index()
    for path in sorted(glob.glob(f"{DIR}/*.json")):
        for k, v in _load(path).items():
            if k.split("|")[0] >= cutoff:
                if k in owner:
                    v = {**v, "outcome": owner[k], "outcome_by": "owner"}
                if now is not None:
                    o = settle(v, k.split("|")[0], now, k.split("|")[1], seen)
                    if o and not v.get("outcome"):
                        v = {**v, "outcome": o}
                out[k] = v
    # the plane that flies in is the one that flies back out: if the departure left,
    # its inbound arrival must have landed (the feed often never updates arrivals)
    for k, v in list(out.items()):
        if v.get("outcome") or v.get("direction") != "arr":
            continue
        date, ap, fl = k.split("|")
        t = datetime.fromisoformat(f"{date}T{v['sched']}")
        for k2, d in out.items():
            d_date, d_ap, d_fl = k2.split("|")
            if (d_date, d_ap) == (date, ap) and d.get("direction") == "dep" and d_fl[:2] == fl[:2] \
                    and timedelta(0) <= datetime.fromisoformat(f"{date}T{d['sched']}") - t <= timedelta(hours=2) \
                    and re.match(r"(departed|flew)", str(d.get("outcome", "")), re.I):
                out[k] = {**v, "outcome": f"Arrived (its plane flew out on {d_fl})"}
                break
    with open(PUBLIC, "w") as f:
        json.dump(out, f, separators=(",", ":"))
