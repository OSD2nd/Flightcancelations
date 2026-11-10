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


def fetch_hourly(lat, lon, start, end, historical=False, tz="America/Vancouver"):
    """Return {naive local datetime (hour): {var: value}} for start..end dates inclusive."""
    base = ("https://historical-forecast-api.open-meteo.com/v1/forecast" if historical
            else "https://api.open-meteo.com/v1/forecast")
    q = dict(latitude=lat, longitude=lon, hourly=HOURLY, timezone=tz,
             start_date=start.isoformat(), end_date=end.isoformat())
    try:  # with cloud at valley/ridge height; fall back if the server refuses those
        data = _get(base + "?" + urllib.parse.urlencode({**q, "hourly": HOURLY + "," + LEVELS}))
    except RuntimeError:
        data = _get(base + "?" + urllib.parse.urlencode(q))
    h = data["hourly"]
    keys = [k for k in (HOURLY + "," + LEVELS).split(",") if k in h]
    out = {}
    for i, t in enumerate(h["time"]):
        out[datetime.fromisoformat(t)] = {k: h[k][i] for k in keys}
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
