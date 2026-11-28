"""Flight schedule + live status.

Timetable: config.json "static_schedule" (departures) and "static_arrivals".
Live status: AeroDataBox (RapidAPI, AERODATABOX_KEY). The free plan allows
~400 units/month, so we only ask at the times in config "status_times": one
airport request (2 units) returns that day's arrivals AND departures, so a
check of Trail + Castlegar costs 4 units. Results are kept in
data/flight_status.json and reused by the runs in between.
"""
import json, os, urllib.request
from datetime import datetime

STATUS_FILE = "data/flight_status.json"


QUOTA = {}   # RapidAPI's "how much of your plan is left" headers from the latest reply


def _adb(path, key):
    # a key from a direct AeroDataBox account (secret AERODATABOX_DIRECT_KEY) goes to their own
    # gateway; otherwise - or if the direct account refuses (no plan yet: 402, bad key: 401/403) -
    # the RapidAPI key (AERODATABOX_KEY). Same endpoints and answers either way.
    import urllib.error
    direct = (os.environ.get("AERODATABOX_DIRECT_KEY") or "").strip()
    rapid = (os.environ.get("AERODATABOX_KEY") or "").strip() or (key if key != direct else "")
    tries = []
    if direct:
        tries.append(("direct", urllib.request.Request("https://api.aerodatabox.com" + path, headers={"X-Api-Key": direct})))
    if rapid:
        tries.append(("RapidAPI", urllib.request.Request(
            "https://aerodatabox.p.rapidapi.com" + path,
            headers={"X-RapidAPI-Key": rapid, "X-RapidAPI-Host": "aerodatabox.p.rapidapi.com"})))
    last = None
    for name, req in tries:
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read()
                for k, v in r.headers.items():
                    if k.lower().startswith("x-ratelimit"):
                        QUOTA[k.lower()] = v
                QUOTA["gateway"] = name
            break
        except urllib.error.HTTPError as e:
            last = e
            if name == "direct" and e.code in (401, 402, 403) and len(tries) > 1:
                print(f"[warn] direct AeroDataBox account refused ({e.code}); using RapidAPI")
                continue
            raise
    else:
        raise last or RuntimeError("no AeroDataBox key")
    if QUOTA:
        print("AeroDataBox plan: " + ", ".join(f"{k.replace('x-ratelimit-', '')}={v}" for k, v in sorted(QUOTA.items())))
    return json.loads(body) if body.strip() else {}


def _local(s):
    # "2026-11-11 10:55-08:00" -> naive local datetime
    return datetime.fromisoformat(s.replace("Z", "+00:00")[:16].replace(" ", "T"))


def _diverted_to(f):
    """If the record names a diversion airport anywhere, return its code."""
    def walk(o, under=False):
        if isinstance(o, dict):
            for k, v in o.items():
                hit = under or "diver" in k.lower()  # diverted, diversion
                if hit and isinstance(v, dict) and (v.get("iata") or v.get("icao")):
                    return v.get("iata") or v.get("icao")
                r = walk(v, hit)
                if r:
                    return r
        elif isinstance(o, list):
            for v in o:
                r = walk(v, under)
                if r:
                    return r
        return None
    return walk(f)


def _num(n):
    return (n or "").replace(" ", "").upper()


def airport_day(code, day, hub_code, key, window=("06:00", "17:59")):
    """Today's flights between `code` and the hub, both directions, in ONE request.
    Returns {"dep": [...], "arr": [...]} with flight, time (at `code`), expected, status, airline."""
    a, b = (f"{day.isoformat()}T{t}" for t in window)
    # withCodeshared=true: with "false", AeroDataBox guesses which flights are real at airports like
    # Trail that send no code-share info ("false results are possible") - likely why flights vanished.
    # We drop the code-share copies ourselves. withLeg=true adds the Vancouver end of each flight.
    path = (f"/flights/airports/iata/{code}/{a}/{b}?direction=Both&withLeg=true&withCancelled=true"
            "&withCodeshared=true&withCargo=false&withPrivate=false&withLocation=false")
    data = _adb(path, key)
    out = {"dep": [], "arr": []}
    seen = set()
    for kind, near, far in (("departures", "departure", "arrival"), ("arrivals", "arrival", "departure")):
        for f in data.get(kind, []) or []:
            if "movement" in f:
                # airport board format: "movement" holds the OTHER airport and the time HERE
                here = f["movement"]
                other = (here.get("airport") or {}).get("iata")
            else:
                here = f.get(near) or {}
                other = ((f.get(far) or {}).get("airport") or {}).get("iata")
            if other != hub_code:
                continue
            if str(f.get("codeshareStatus", "")).lower() == "iscodeshared":
                continue                    # another airline's number for the same plane
            sched = (here.get("scheduledTime") or {}).get("local")
            if not sched:
                continue
            key_ = (kind, _num(f.get("number")), sched)
            if key_ in seen:
                continue
            seen.add(key_)
            best = (here.get("revisedTime") or here.get("runwayTime") or here.get("predictedTime") or {}).get("local")
            # the Vancouver end: for an arrival, when it (is expected to) leave Vancouver
            far_mv = f.get(far) or {}
            far_sched = (far_mv.get("scheduledTime") or {}).get("local")
            far_best = (far_mv.get("runwayTime") or far_mv.get("revisedTime") or far_mv.get("predictedTime") or {}).get("local")
            out["dep" if kind == "departures" else "arr"].append({
                "flight": _num(f.get("number")), "time": _local(sched).strftime("%H:%M"),
                "expected": _local(best).strftime("%H:%M") if best else None,
                "status": f.get("status") or "Unknown",
                "diverted_to": _diverted_to(f) if "divert" in str(f.get("status", "")).lower() else None,
                "airline": (f.get("airline") or {}).get("name", ""),
                "hub_sched": _local(far_sched).strftime("%H:%M") if far_sched else None,
                "hub_expected": _local(far_best).strftime("%H:%M") if far_best else None})
    return out


def load_status():
    try:
        with open(STATUS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_status(s):
    os.makedirs("data", exist_ok=True)
    with open(STATUS_FILE, "w") as f:
        json.dump(s, f, indent=1)


def fetch_status(cfg, day, key, now, airports=None):
    """Ask the API about these airports (default: all) for `day`; keep other airports'
    earlier answers from the same day."""
    old = load_status()
    s = old if old.get("date") == day.isoformat() else {"date": day.isoformat(), "airports": {}}
    s["checked"] = now.isoformat(timespec="minutes")
    window = tuple(cfg.get("status_window", ["06:00", "17:59"]))
    for code in airports or list(cfg["airports"]):
        try:
            s["airports"][code] = airport_day(code, day, cfg["hub"]["code"], key, window)
            s.setdefault("checked_by", {})[code] = s["checked"]
            # every flight the feed has listed today, so we notice one that later disappears
            seen = s.setdefault("seen", {}).setdefault(code, {})
            for d in ("dep", "arr"):
                for r in s["airports"][code].get(d) or []:
                    seen.setdefault(d, {})[r["flight"]] = r["time"]
        except Exception as e:
            print(f"[warn] flight status failed for {code}: {e}")
    if QUOTA:
        s["quota"] = dict(QUOTA, at=s["checked"])
    save_status(s)
    return s


def fetch_yesterday(cfg, day, key, airports):
    """Afternoon/evening of a past day (12:00-23:59): did the late flights go?"""
    out = {}
    for code in airports:
        try:
            out[code] = airport_day(code, day, cfg["hub"]["code"], key, ("12:00", "23:59"))
        except Exception as e:
            print(f"[warn] yesterday's flight status failed for {code}: {e}")
    return out


def schedule(code, day, cfg, arrivals=False, live=None):
    """Flights for one airport/day/direction. Timetable from config, overlaid with
    live status when we have it for that day. Returns (flights, source)."""
    table = cfg.get("static_arrivals", {}) if arrivals else cfg["static_schedule"]
    out = {}
    for s in table.get(code, []):
        t = s["arr"] if arrivals else s["dep"]
        h, m = map(int, t.split(":"))
        out[_num(s["flight"])] = {"flight": s["flight"], "dep": datetime(day.year, day.month, day.day, h, m),
                                  "status": "Scheduled", "airline": cfg["airports"][code]["airline"],
                                  "block_min": s.get("block_min", 75)}
    rows = ((live or {}).get("airports", {}).get(code) or {}).get("arr" if arrivals else "dep")
    if live and live.get("date") == day.isoformat() and rows is not None:
        for r in rows:
            h, m = map(int, r["time"].split(":"))
            base = out.get(r["flight"], {"block_min": 75, "airline": r["airline"]})
            out[r["flight"]] = {**base, "flight": r["flight"],
                                "dep": datetime(day.year, day.month, day.day, h, m),
                                "expected": (datetime(day.year, day.month, day.day, *map(int, r["expected"].split(":")))
                                             if r.get("expected") else None),
                                "status": r["status"], "diverted_to": r.get("diverted_to"),
                                "hub_sched": r.get("hub_sched"), "hub_expected": r.get("hub_expected"),
                                "airline": r["airline"] or base["airline"]}
        # listed by the feed earlier today, missing now: Pacific Coastal often just drops cancelled flights
        listed = {r["flight"] for r in rows}
        for fl, t in ((live.get("seen") or {}).get(code) or {}).get("arr" if arrivals else "dep", {}).items():
            if fl not in listed:
                base = out.get(fl, {"block_min": 75, "airline": cfg["airports"][code]["airline"], "flight": fl,
                                    "dep": datetime(day.year, day.month, day.day, *map(int, t.split(":")))})
                out[fl] = {**base, "dep": datetime(day.year, day.month, day.day, *map(int, t.split(":"))),
                           "status": "CanceledUncertain", "dropped": True}
        return sorted(out.values(), key=lambda f: f["dep"]), "live"
    return sorted(out.values(), key=lambda f: f["dep"]), "static"


# kept for history.backfill(): past days, one direction
def departures_api(code, day, hub_code, key, direction="Departure"):
    rows = airport_day(code, day, hub_code, key)["dep" if direction == "Departure" else "arr"]
    return [{"flight": r["flight"], "dep": datetime.fromisoformat(f"{day.isoformat()}T{r['time']}"),
             "status": r["status"], "airline": r["airline"]} for r in rows]


def departures_static(code, day, cfg, arrivals=False):
    return schedule(code, day, cfg, arrivals)[0]
