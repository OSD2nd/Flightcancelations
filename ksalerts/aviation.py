"""Official airport weather from aviationweather.gov (free, no key).

METAR = what the airport is reporting now (about hourly).
TAF   = the official aviation forecast for the airport (Castlegar's covers ~8 daytime hours).

These see what actually grounds Kootenay flights - how low the cloud base is -
which the general weather-model forecast does not report.
"""
import csv, io, json, re, time, urllib.error, urllib.request
from datetime import datetime, timedelta, timezone

API = "https://aviationweather.gov/api/data"
IEM = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
UA = "kootenay-flight-risk/1.0 (personal flight-weather dashboard)"


def _get(path):
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(API + path, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read()
            return json.loads(body) if body.strip() else []
        except urllib.error.HTTPError as e:
            last = f"aviationweather.gov answered {e.code}"
            if e.code < 500 and e.code != 429:
                break
        except Exception as e:
            last = f"aviationweather.gov: {type(e).__name__} {e}"
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(last)


# ---- backup: the same airport reports from the Iowa Environmental Mesonet archive ----
def _parse_metar(raw):
    """Just the parts we use, from the raw report text."""
    clouds = [{"cover": c, "base": int(h) * 100}
              for c, h in re.findall(r"\b(FEW|SCT|BKN|OVC|VV)(\d{3})\b", raw)]
    vv = next((c["base"] for c in clouds if c["cover"] == "VV"), None)
    clouds = [c for c in clouds if c["cover"] != "VV"]
    m = re.search(r"\s(M?)(?:(\d+)\s)?(\d+)(?:/(\d+))?SM\b", raw)
    vis = None
    if m:
        whole = int(m.group(2) or 0)
        vis = whole + (int(m.group(3)) / int(m.group(4)) if m.group(4) else int(m.group(3)))
    if "P6SM" in raw:
        vis = 6
    w = re.search(r"\b(\d{3}|VRB)(\d{2,3})(?:G(\d{2,3}))?KT\b", raw)
    wx = [t for t in raw.split() if re.fullmatch(r"[-+]?(VC)?(MI|BC|SH|FZ|BL|TS)?(RA|SN|DZ|BR|FG|HZ|FU|GR|GS|PL|SG|UP)+", t)]
    cat = None
    ceil = min([c["base"] for c in clouds if c["cover"] in ("BKN", "OVC")] + ([vv] if vv else []), default=None)
    if vis is not None or ceil is not None:
        v, c = (vis if vis is not None else 99), (ceil if ceil is not None else 99999)
        cat = "LIFR" if v < 1 or c < 500 else "IFR" if v < 3 or c < 1000 else "MVFR" if v <= 5 or c <= 3000 else "VFR"
    return {"clouds": clouds, "vertVis": vv, "visib": vis,
            "wdir": (0 if w and w.group(1) == "VRB" else int(w.group(1))) if w else None,
            "wspd": int(w.group(2)) if w else None, "wgst": int(w.group(3)) if w and w.group(3) else None,
            "wxString": " ".join(wx) or None, "fltCat": cat, "rawOb": raw}


def metars_backup(ids, hours=24):
    end = datetime.now(timezone.utc) + timedelta(hours=1)
    start = end - timedelta(hours=hours + 1)
    q = "&".join(f"station={i}" for i in ids)
    url = (f"{IEM}?{q}&data=metar&tz=Etc/UTC&format=onlycomma&"
           f"year1={start:%Y}&month1={start.month}&day1={start.day}&hour1={start.hour}&"
           f"year2={end:%Y}&month2={end.month}&day2={end.day}&hour2={end.hour}")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        text = r.read().decode("utf-8", "ignore")
    out = {}
    for row in csv.DictReader(io.StringIO(text)):
        raw = (row.get("metar") or "").strip()
        if not raw:
            continue
        t = datetime.fromisoformat(row["valid"]).replace(tzinfo=timezone.utc)
        m = _parse_metar(raw)
        m.update(icaoId=row["station"], obsTime=int(t.timestamp()), name=row["station"])
        out.setdefault(row["station"], []).append(m)
    for v in out.values():
        v.sort(key=lambda m: m["obsTime"], reverse=True)
    return out


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
    # lowest cloud of ANY amount (FEW/SCT patches too) - low patches in the valley stop
    # visual approaches even when there's no solid ceiling (Trail, 11 Nov 2026)
    lows = [c.get("base") for g in groups for c in (g.get("clouds") or []) if c.get("base") is not None]
    lows += [g.get("vertVis") for g in groups if g.get("vertVis")]
    low_remark = any("LO CLD" in (g.get("rawOb") or "") for g in groups)
    return {"ceiling_ft": min(ceils) if ceils else None, "vis_sm": min(viss) if viss else None,
            "gust_kt": max(gusts) if gusts else None, "wx": wx, "source": source,
            "lowest_ft": min(lows) if lows else None, "low_remark": low_remark}


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
            "lowest_ft": pick(a.get("lowest_ft"), b.get("lowest_ft")),
            "low_remark": a.get("low_remark") or b.get("low_remark"),
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
            "lowest_ft": min([c.get("base") for c in (m.get("clouds") or []) if c.get("base") is not None]
                             + ([m["vertVis"]] if m.get("vertVis") else []), default=None),
            "low_remark": "LO CLD" in (m.get("rawOb") or ""),
            "vis_sm": vis_sm(m.get("visib")), "wind": wind, "wx": m.get("wxString"),
            "raw": m.get("rawOb"), "taf": (taf or {}).get("rawTAF")}
