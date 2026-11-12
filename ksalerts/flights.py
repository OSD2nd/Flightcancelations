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


def _adb(path, key):
    req = urllib.request.Request(
        "https://aerodatabox.p.rapidapi.com" + path,
        headers={"X-RapidAPI-Key": key, "X-RapidAPI-Host": "aerodatabox.p.rapidapi.com"})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read()
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
    path = (f"/flights/airports/iata/{code}/{a}/{b}?direction=Both&withLeg=false&withCancelled=true"
            "&withCodeshared=false&withCargo=false&withPrivate=false&withLocation=false")
    data = _adb(path, key)
    out = {"dep": [], "arr": []}
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
            sched = (here.get("scheduledTime") or {}).get("local")
            if not sched:
                continue
            best = (here.get("revisedTime") or here.get("runwayTime") or {}).get("local")
            out["dep" if kind == "departures" else "arr"].append({
                "flight": _num(f.get("number")), "time": _local(sched).strftime("%H:%M"),
                "expected": _local(best).strftime("%H:%M") if best else None,
                "status": f.get("status") or "Unknown",
                "diverted_to": _diverted_to(f) if "divert" in str(f.get("status", "")).lower() else None,
                "airline": (f.get("airline") or {}).get("name", "")})
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
        except Exception as e:
            print(f"[warn] flight status failed for {code}: {e}")
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
                                "airline": r["airline"] or base["airline"]}
        return sorted(out.values(), key=lambda f: f["dep"]), "live"
    return sorted(out.values(), key=lambda f: f["dep"]), "static"


# kept for history.backfill(): past days, one direction
def departures_api(code, day, hub_code, key, direction="Departure"):
    rows = airport_day(code, day, hub_code, key)["dep" if direction == "Departure" else "arr"]
    return [{"flight": r["flight"], "dep": datetime.fromisoformat(f"{day.isoformat()}T{r['time']}"),
             "status": r["status"], "airline": r["airline"]} for r in rows]


def departures_static(code, day, cfg, arrivals=False):
    return schedule(code, day, cfg, arrivals)[0]
