"""Flight schedule + status.

With an AERODATABOX_KEY (RapidAPI) we pull real departures and their status
(Scheduled / Delayed / Canceled). Without one we fall back to the static
schedule in config.json, so weather risk still works.
"""
import json, os, urllib.request
from datetime import datetime, timedelta


def _adb(path, key):
    req = urllib.request.Request(
        "https://aerodatabox.p.rapidapi.com" + path,
        headers={"X-RapidAPI-Key": key, "X-RapidAPI-Host": "aerodatabox.p.rapidapi.com"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _local(s):
    # "2026-11-11 10:55-08:00" -> naive local datetime
    return datetime.fromisoformat(s.replace("Z", "+00:00")[:16].replace(" ", "T"))


def departures_api(code, day, hub_code, key):
    """Departures from `code` to the hub on `day`, status included. Two 12h windows (API limit)."""
    out, seen = [], set()
    for start_h in (0, 12):
        a = datetime(day.year, day.month, day.day, start_h)
        b = a + timedelta(hours=11, minutes=59)
        path = (f"/flights/airports/iata/{code}/{a:%Y-%m-%dT%H:%M}/{b:%Y-%m-%dT%H:%M}"
                "?direction=Departure&withCancelled=true&withCodeshared=false&withCargo=false&withPrivate=false")
        for f in _adb(path, key).get("departures", []):
            dest = (f.get("arrival", {}).get("airport", {}) or {}).get("iata")
            if dest != hub_code:
                continue
            num = f.get("number", "").replace(" ", "")
            if num in seen:
                continue
            seen.add(num)
            sched = (f.get("departure", {}).get("scheduledTime", {}) or {}).get("local")
            if not sched:
                continue
            out.append({"flight": num, "dep": _local(sched),
                        "status": f.get("status", "Unknown"),
                        "airline": (f.get("airline") or {}).get("name", "")})
    return sorted(out, key=lambda x: x["dep"])


def departures_static(code, day, cfg):
    out = []
    for s in cfg["static_schedule"].get(code, []):
        h, m = map(int, s["dep"].split(":"))
        out.append({"flight": s["flight"], "dep": datetime(day.year, day.month, day.day, h, m),
                    "status": "Scheduled", "airline": cfg["airports"][code]["airline"],
                    "block_min": s.get("block_min", 75)})
    return out


def departures(code, day, cfg):
    key = os.environ.get("AERODATABOX_KEY")
    if key:
        try:
            fl = departures_api(code, day, cfg["hub"]["code"], key)
            if fl:
                return fl, "live"
        except Exception as e:
            print(f"[warn] flight API failed for {code}: {e}; using static schedule")
    return departures_static(code, day, cfg), "static"
