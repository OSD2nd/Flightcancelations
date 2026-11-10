"""Official airport weather from aviationweather.gov (free, no key).

METAR = what the airport is reporting now (about hourly).
TAF   = the official aviation forecast for the airport (Castlegar's covers ~8 daytime hours).

These see what actually grounds Kootenay flights - how low the cloud base is -
which the general weather-model forecast does not report.
"""
import json, urllib.request
from datetime import datetime, timezone

API = "https://aviationweather.gov/api/data"


def _get(path):
    with urllib.request.urlopen(API + path, timeout=30) as r:
        body = r.read()
    return json.loads(body) if body.strip() else []


def ceiling(clouds, vert_vis=None):
    """Lowest broken/overcast layer in feet above the airport, or None if no ceiling."""
    bases = [c.get("base") for c in (clouds or [])
             if c.get("cover") in ("BKN", "OVC", "OVX") and c.get("base") is not None]
    if vert_vis:
        bases.append(vert_vis)
    return min(bases) if bases else None


def vis_sm(v):
    """'6+' -> 6.0 statute miles; numbers pass through; None stays None."""
    if v is None:
        return None
    try:
        return float(str(v).rstrip("+"))
    except ValueError:
        return None


def metars(ids, hours=3):
    """{icao: [reports, newest first]}"""
    out = {}
    for m in _get(f"/metar?ids={','.join(ids)}&format=json&hours={hours}"):
        out.setdefault(m["icaoId"], []).append(m)
    for v in out.values():
        v.sort(key=lambda m: m.get("obsTime", 0), reverse=True)
    return out


def tafs(ids):
    """{icao: taf}"""
    return {t["icaoId"]: t for t in _get(f"/taf?ids={','.join(ids)}&format=json")}


def _merge(groups, source):
    """Worst case across report/forecast groups."""
    ceils = [c for c in (ceiling(g.get("clouds"), g.get("vertVis")) for g in groups) if c is not None]
    viss = [v for v in (vis_sm(g.get("visib")) for g in groups) if v is not None]
    gusts = [g.get("wgst") or g.get("wspd") for g in groups if isinstance(g.get("wgst") or g.get("wspd"), (int, float))]
    wx = " ".join(g.get("wxString") or "" for g in groups).split()
    return {"ceiling_ft": min(ceils) if ceils else None, "vis_sm": min(viss) if viss else None,
            "gust_kt": max(gusts) if gusts else None, "wx": wx, "source": source}


def taf_at(taf, when_utc):
    """Forecast conditions at a moment: the main forecast plus any TEMPO/PROB
    'could briefly be worse' groups that cover it. None if the TAF doesn't cover it."""
    if not taf:
        return None
    t = when_utc.timestamp()
    if not (taf.get("validTimeFrom", 0) <= t < taf.get("validTimeTo", 0)):
        return None
    main, temp = None, []
    for g in taf.get("fcsts", []):
        if not (g["timeFrom"] <= t < g["timeTo"]):
            continue
        if g.get("fcstChange") in ("TEMPO", "PROB"):
            temp.append(g)
        else:
            main = g  # later FM/BECMG groups replace earlier ones
    groups = ([main] if main else []) + temp
    return _merge(groups, "airport forecast") if groups else None


def metar_now(reports, when_utc, max_age_min=90):
    """Latest report, if it is recent and the flight is within ~2 hours."""
    if not reports:
        return None
    m = reports[0]
    obs = datetime.fromtimestamp(m["obsTime"], timezone.utc)
    now = datetime.now(timezone.utc)
    if (now - obs).total_seconds() > max_age_min * 60 or abs((when_utc - now).total_seconds()) > 2 * 3600:
        return None
    return _merge([m], "airport report")


def conditions(reports, taf, when_utc):
    """Best available official conditions for a departure/arrival time (worst of report + forecast)."""
    parts = [p for p in (metar_now(reports, when_utc), taf_at(taf, when_utc)) if p]
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    a, b = parts
    pick = lambda x, y: min(v for v in (x, y) if v is not None) if (x is not None or y is not None) else None
    return {"ceiling_ft": pick(a["ceiling_ft"], b["ceiling_ft"]), "vis_sm": pick(a["vis_sm"], b["vis_sm"]),
            "gust_kt": max([v for v in (a["gust_kt"], b["gust_kt"]) if v is not None], default=None),
            "wx": a["wx"] + b["wx"], "source": "airport report + forecast"}


def summary(reports, taf):
    """Small decoded block for the dashboard."""
    if not reports and not taf:
        return None
    m = reports[0] if reports else {}
    wind = None
    if m.get("wspd") is not None:
        wind = ("calm" if not m["wspd"] else
                f"{m['wdir']}° {m['wspd']} kt" + (f" gusting {m['wgst']}" if m.get("wgst") else ""))
    return {"name": m.get("name") or (taf or {}).get("name"),
            "obs_time": (datetime.fromtimestamp(m["obsTime"], timezone.utc).isoformat(timespec="minutes")
                         if m.get("obsTime") else None),
            "category": m.get("fltCat"), "ceiling_ft": ceiling(m.get("clouds"), m.get("vertVis")),
            "vis_sm": vis_sm(m.get("visib")), "wind": wind, "wx": m.get("wxString"),
            "raw": m.get("rawOb"), "taf": (taf or {}).get("rawTAF")}
