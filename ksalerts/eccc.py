"""Environment and Climate Change Canada's official public forecast (weather.gc.ca city pages).

Free, from the Government of Canada's GeoMet service. For each place:
  - hourly conditions for the next 24 hours, in words ("Fog patches", "Rain"),
    with chance of precipitation and wind
  - the regular forecast periods (Today / Tonight / Friday ...) for ~6 days
  - any weather warnings

It has no cloud heights or visibility numbers, so it adds to the model
forecast rather than replacing it: its wording counts toward the risk
(fog, low cloud, freezing rain, snow, warnings).
"""
import json, re, urllib.request
from datetime import datetime, timedelta, timezone

URL = "https://api.weather.gc.ca/collections/citypageweather-realtime/items/{id}?f=json&lang=en"
UA = "kootenay-flight-risk/1.0 github.com/OSD2nd/Flightcancelations"

FOG = re.compile(r"\bfog|mist|low cloud|drizzle", re.I)
WINTRY = re.compile(r"freezing|ice pellets|blowing snow|heavy snow", re.I)
SNOW = re.compile(r"\bsnow|flurr", re.I)


def _en(x):
    return (x or {}).get("en") if isinstance(x, dict) else x


def fetch(city_id):
    req = urllib.request.Request(URL.format(id=city_id), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as r:
        p = json.load(r)["properties"]
    hours = []
    for h in (p.get("hourlyForecastGroup") or {}).get("hourlyForecasts") or []:
        hours.append({"utc": h.get("timestamp"), "condition": _en(h.get("condition")),
                      "pop": _en((h.get("lop") or {}).get("value")),
                      "temp": _en((h.get("temperature") or {}).get("value")),
                      "wind": _en(((h.get("wind") or {}).get("speed") or {}).get("value"))})
    periods = []
    for f in (p.get("forecastGroup") or {}).get("forecasts") or []:
        periods.append({"name": _en((f.get("period") or {}).get("textForecastName")),
                        "day": _en((f.get("period") or {}).get("value")),
                        "summary": _en(f.get("textSummary")),
                        "short": _en(((f.get("abbreviatedForecast") or {}).get("textSummary")))})
    warnings = []
    for w in p.get("warnings") or []:
        for e in (w.get("events") if isinstance(w, dict) and w.get("events") else [w]):
            t = _en(e.get("description")) or _en(e.get("type")) or ""
            if t:
                warnings.append(t.strip())
    return {"name": _en(p.get("name")), "updated": p.get("lastUpdated"), "url": _en(p.get("url")),
            "hours": hours, "periods": periods, "warnings": warnings}


def period_dates(periods, today):
    """Give each forecast period a date and day/night. The first is today (or tonight)."""
    out, d = [], today
    for i, p in enumerate(periods):
        night = "night" in (p["name"] or "").lower() or (p["name"] or "").lower() == "tonight"
        if i > 0 and not night:
            d = d + timedelta(days=1)
        out.append({**p, "date": d.isoformat(), "night": night})
    return out


# "fog patches dissipating in the morning" only matters for flights before ~11
UNTIL = [(r"dissipating (early )?in the morning|clearing in the morning", 11), (r"dissipating near noon|clearing near noon", 13),
         (r"dissipating (in the|this) afternoon|clearing (in the|this) afternoon", 17)]
LATER = r"developing (this |in the )?(evening|overnight|after midnight|late)"


def fog_at(text, hour):
    """Does the forecast wording put fog/low cloud at this hour of the day?"""
    for sentence in re.split(r"(?<=\.)\s+", text or ""):
        if not FOG.search(sentence):
            continue
        if re.search(LATER, sentence, re.I):
            continue                      # fog arriving tonight, not during a daytime flight
        until = next((h for pat, h in UNTIL if re.search(pat, sentence, re.I)), None)
        if until is None or hour < until:
            return True
    return False


def signals(fc, when_local, to_utc, today):
    """What the official forecast says around a flight time: {'fog': bool, 'wintry': bool, 'snow': bool, 'text': str}."""
    if not fc:
        return None
    t = to_utc(when_local)
    # the hourly wording near the flight time (next 24 h) AND that day's written forecast:
    # the hourly list often just says "Mainly cloudy" while the text says "fog patches
    # dissipating in the morning" - either one counts
    near = [h for h in fc["hours"] if h.get("utc") and
            abs(datetime.fromisoformat(h["utc"].replace("Z", "+00:00")) - t) <= timedelta(minutes=90)]
    hourly = " / ".join(sorted({h["condition"] or "" for h in near}))
    day = when_local.date().isoformat()
    per = [p for p in period_dates(fc["periods"], today) if p["date"] == day and not p["night"]]
    period = per[0]["summary"] if per else ""
    # fog said to develop the night before and dissipate "in the morning" belongs to that morning too
    eve = [p for p in period_dates(fc["periods"], today)
           if p["night"] and p["date"] == (when_local.date() - timedelta(days=1)).isoformat()]
    if not near and not per:
        return None
    text = " ".join(x for x in (hourly + "." if hourly else "", period) if x)
    if eve and when_local.hour < 11 and re.search(r"fog", eve[0]["summary"] or "", re.I) and FOG.search(period or ""):
        text += " Fog patches."
    return {"fog": fog_at(text, when_local.hour), "wintry": bool(WINTRY.search(text)),
            "snow": bool(SNOW.search(text)), "text": text, "warnings": fc.get("warnings") or []}
