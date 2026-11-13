"""Weather from Open-Meteo (free, no key). Forecast for tomorrow, historical-forecast for training."""
import json, time, urllib.parse, urllib.request
from datetime import datetime, timedelta

HOURLY = ("temperature_2m,precipitation,snowfall,weather_code,visibility,"
          "cloud_cover_low,wind_speed_10m,wind_gusts_10m")
LEVELS = "cloud_cover_925hPa,cloud_cover_900hPa,cloud_cover_850hPa"


def _get(url, tries=3):
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.load(r)
        except Exception as e:  # network hiccup, rate limit
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"weather fetch failed: {last}")


MODEL = None   # set from config "weather_model" (e.g. gem_seamless = Environment Canada)


def fetch_hourly(lat, lon, start, end, historical=False, tz=None):
    """Return {naive local datetime (hour): {var: value}} for start..end dates inclusive.
    Times come back in UTC and are shifted to local with clock.OFFSET (see clock.py)."""
    from . import clock
    base = ("https://historical-forecast-api.open-meteo.com/v1/forecast" if historical
            else "https://api.open-meteo.com/v1/forecast")
    q = dict(latitude=lat, longitude=lon, hourly=HOURLY, timezone="GMT",
             start_date=start.isoformat(), end_date=(end + timedelta(days=1)).isoformat())
    def get(extra):
        try:  # with cloud at valley/ridge height; fall back if the server refuses those
            return _get(base + "?" + urllib.parse.urlencode({**q, **extra, "hourly": HOURLY + "," + LEVELS}))
        except RuntimeError:
            return _get(base + "?" + urllib.parse.urlencode({**q, **extra}))
    names = (HOURLY + "," + LEVELS).split(",")
    models = [m.strip() for m in (MODEL or "").split(",") if m.strip()] if not historical else []
    data = get({"models": ",".join(models)} if models else {})
    h = data["hourly"]

    def series(model):
        """{var: [values]} for one model; with several models Open-Meteo suffixes each name."""
        if len(models) > 1:
            return {k: h.get(f"{k}_{model}") for k in names}
        return {k: h.get(k) for k in names}

    def usable(v):
        return v is not None and any(x is not None for x in v)

    per = [series(m) for m in models] if models else [series(None)]
    if len(per) > 1:
        # fill what the first model doesn't have (e.g. Environment Canada's has no visibility) from the second
        for k in names:
            if not usable(per[0][k]) and usable(per[1][k]):
                per[0][k] = per[1][k]
            if not usable(per[1][k]) and usable(per[0][k]):
                per[1][k] = per[0][k]
    elif models:
        missing = [k for k in names if not usable(per[0][k])]
        if missing:
            fill = _get(base + "?" + urllib.parse.urlencode({**q, "hourly": ",".join(missing)}))["hourly"]
            idx = {t: i for i, t in enumerate(fill["time"])}
            for k in missing:
                if k in fill:
                    per[0][k] = [fill[k][idx[t]] if t in idx else None for t in h["time"]]
    out = {}
    for i, t in enumerate(h["time"]):
        row = {k: v[i] for k, v in per[0].items() if usable(v)}
        if len(per) > 1:
            # the second model rides along so each flight can be scored with both
            row["alt"] = {k: v[i] for k, v in per[1].items() if usable(v)}
            row["model"], row["alt_model"] = models[0], models[1]
        out[datetime.fromisoformat(t) + timedelta(hours=clock.OFFSET)] = row
    return out


def window(hours, t, before=1, after=1):
    """Hourly rows from t-before to t+after (hours)."""
    base = t.replace(minute=0, second=0, microsecond=0)
    rows = []
    for d in range(-before, after + 1):
        r = hours.get(base + timedelta(hours=d))
        if r:
            rows.append(r)
    return rows


def fetch_smoke(lat, lon, start, end):
    """Wildfire smoke forecast: {naive local hour: pm2_5 µg/m³} (Open-Meteo air quality, ~5 days ahead)."""
    from . import clock
    base = "https://air-quality-api.open-meteo.com/v1/air-quality"
    # the smoke forecast only reaches ~5 days ahead; asking for more gets "400 Bad Request"
    q = dict(latitude=lat, longitude=lon, hourly="pm2_5", timezone="GMT", past_days=1, forecast_days=5)
    data = _get(base + "?" + urllib.parse.urlencode(q))
    h = data["hourly"]
    return {datetime.fromisoformat(t) + timedelta(hours=clock.OFFSET): v
            for t, v in zip(h["time"], h["pm2_5"]) if v is not None}


# ---- cache: fetch the model forecast every couple of hours and reuse it in between ----
CACHE_DIR = ".cache/weather"     # kept between GitHub runs with actions/cache (not committed)
FETCHED = {}                     # name -> when the data in use was actually fetched (epoch seconds)


def _cache_path(name):
    import os
    os.makedirs(CACHE_DIR, exist_ok=True)
    return f"{CACHE_DIR}/{name}.json"


def cached(name, fetch, fresh_hours=2, keep_hours=30):
    """Returns (data, note). Uses a saved copy younger than fresh_hours; otherwise fetches,
    and if that fails falls back to a saved copy up to keep_hours old (with a note)."""
    import json, os
    path = _cache_path(name)
    saved = None
    if os.path.exists(path):
        try:
            with open(path) as f:
                saved = json.load(f)
        except Exception:
            saved = None
    age = (time.time() - saved["at"]) / 3600 if saved else None
    def unpack(d):
        return {datetime.fromisoformat(k): v for k, v in d.items()}
    if saved and age < fresh_hours:
        FETCHED[name] = saved["at"]
        return unpack(saved["data"]), None
    try:
        data = fetch()
        with open(path, "w") as f:
            json.dump({"at": time.time(), "data": {k.isoformat(): v for k, v in data.items()}}, f)
        FETCHED[name] = time.time()
        return data, None
    except Exception as e:
        if saved and age < keep_hours:
            FETCHED[name] = saved["at"]
            return unpack(saved["data"]), f"using the forecast from {age:.0f} h ago ({e})"
        raise


# ---- backup forecast: MET Norway (free, worldwide; asks callers to identify themselves) ----
METNO = "https://api.met.no/weatherapi/locationforecast/2.0/complete"
METNO_UA = "kootenay-flight-risk/1.0 github.com/OSD2nd/Flightcancelations"


def _metno_code(sym):
    """MET Norway symbol -> the WMO-style weather codes the rest of the tool uses."""
    s = sym or ""
    if "thunder" in s: return 95
    if "sleet" in s: return 66
    if "snow" in s: return 85 if "showers" in s else 73
    if "rain" in s: return 80 if "showers" in s else (61 if "light" in s else 63)
    if "fog" in s: return 45
    if "cloudy" in s and "partly" not in s: return 3
    if "partlycloudy" in s or "fair" in s: return 2
    return 0 if "clearsky" in s else None


def fetch_metno(lat, lon, start=None, end=None):
    """Same shape as fetch_hourly: {naive local hour: {var: value}}. 6-hourly steps later in
    the week are spread over their 6 hours. No visibility or gusts: visibility is estimated
    from the fog share, gusts are left out."""
    from . import clock
    req = urllib.request.Request(f"{METNO}?lat={lat:.4f}&lon={lon:.4f}", headers={"User-Agent": METNO_UA})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.load(r)
            break
        except Exception as e:
            last = e
            time.sleep(4 * (attempt + 1))
    else:
        raise RuntimeError(f"MET Norway failed: {last}")
    out = {}
    for step in d["properties"]["timeseries"]:
        t = datetime.fromisoformat(step["time"].replace("Z", "+00:00")).replace(tzinfo=None) + timedelta(hours=clock.OFFSET)
        inst = step["data"]["instant"]["details"]
        nxt, span = step["data"].get("next_1_hours"), 1
        if not nxt:
            nxt, span = step["data"].get("next_6_hours"), 6
        sym = ((nxt or {}).get("summary") or {}).get("symbol_code")
        pcp = ((nxt or {}).get("details") or {}).get("precipitation_amount")
        per_h = pcp / span if pcp is not None else None
        fog = inst.get("fog_area_fraction")
        vis = None if fog is None else (500 if fog >= 50 else 3000 if fog >= 20 else 20000)
        snowy = bool(sym) and ("snow" in sym or "sleet" in sym)
        row = {"temperature_2m": inst.get("air_temperature"), "cloud_cover_low": inst.get("cloud_area_fraction_low"),
               "precipitation": per_h, "snowfall": (per_h if snowy else 0.0) if per_h is not None else None,
               "weather_code": _metno_code(sym), "visibility": vis,
               "wind_speed_10m": round(inst["wind_speed"] * 3.6, 1) if inst.get("wind_speed") is not None else None,
               "wind_gusts_10m": None, "source": "MET Norway"}
        for h in range(span):
            out.setdefault(t + timedelta(hours=h), dict(row))
    return out

