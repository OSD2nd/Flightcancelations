"""Road reports around Trail and Castlegar from DriveBC's open data (Open511 API).

Closures, incidents, construction and winter road conditions ("compact snow", "slippery
sections") on the highways to the airports. Open Government Licence - British Columbia.
"""
import json, urllib.request

UA = "kootenay-flight-risk/1.0 github.com/OSD2nd/Flightcancelations"
URL = ("https://api.open511.gov.bc.ca/events?format=json&status=ACTIVE&limit=200"
       "&bbox={w},{s},{e},{n}")
BOX = (-118.35, 48.98, -117.05, 49.62)     # Grand Forks/Paulson to Salmo/Nelson, US border to Slocan


def _points(geo):
    """GeoJSON geometry -> list of [lat, lon] (lines are thinned to keep the file small)."""
    if not geo:
        return []
    t, c = geo.get("type"), geo.get("coordinates") or []
    if t == "Point":
        return [[c[1], c[0]]]
    if t == "LineString":
        step = max(1, len(c) // 40)
        return [[p[1], p[0]] for p in c[::step]] + ([[c[-1][1], c[-1][0]]] if c else [])
    if t in ("MultiLineString", "MultiPoint"):
        out = []
        for part in c:
            out += _points({"type": "LineString" if t == "MultiLineString" else "Point",
                            "coordinates": part})
        return out
    return []


def fetch():
    w, s, e, n = BOX
    req = urllib.request.Request(URL.format(w=w, s=s, e=e, n=n), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=45) as r:
        data = json.load(r)
    out = []
    for ev in data.get("events") or []:
        roads = ev.get("roads") or []
        out.append({"id": ev.get("id"), "type": ev.get("event_type"), "subtype": (ev.get("event_subtypes") or [None])[0],
                    "severity": ev.get("severity"), "headline": ev.get("headline"),
                    "description": ev.get("description"), "updated": ev.get("updated"),
                    "road": ", ".join(r.get("name", "") for r in roads if r.get("name")),
                    "where": "; ".join(" to ".join(x for x in (r.get("from"), r.get("to")) if x) for r in roads),
                    "points": _points(ev.get("geography"))})
    return out
