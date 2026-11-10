"""Turn weather rows into yes/no risk flags, and hold the starting weights.

The PRIOR weights are educated guesses about what grounds small-airport
flights in mountain valleys. history.train() nudges them toward what the
real cancellation record says, so they get better as data builds up.
"""
import math

FEATURES = [
    "vis_lt_1_6", "vis_lt_4_8", "lowcloud_90", "gust_35", "gust_50",
    "snow_03", "snow_10", "freezing", "fog", "tstorm",
    "hub_vis_lt_1_6", "hub_gust_60", "ceil_lt_1000", "ceil_lt_3000",
]

PRIOR = {
    "bias": -3.6,          # ~3% baseline chance of cancellation on any flight
    "vis_lt_1_6": 2.0, "vis_lt_4_8": 0.8, "lowcloud_90": 0.7,
    "gust_35": 0.5, "gust_50": 1.2,
    "snow_03": 0.4, "snow_10": 1.0,
    "freezing": 2.0, "fog": 1.2, "tstorm": 1.5,
    "hub_vis_lt_1_6": 1.0, "hub_gust_60": 0.8,
    # Cloud base from the official airport report/forecast. Castlegar sits in a
    # deep valley, so a low ceiling matters more than cloud percentage.
    "ceil_lt_1000": 1.6, "ceil_lt_3000": 1.2,
}

LABELS = {
    "vis_lt_1_6": "Visibility under 1.6 km", "vis_lt_4_8": "Reduced visibility (under 4.8 km)",
    "lowcloud_90": "Heavy low cloud", "gust_35": "Gusts over 35 km/h", "gust_50": "Gusts over 50 km/h",
    "snow_03": "Snow falling", "snow_10": "Heavy snow (1+ cm/h)", "freezing": "Freezing rain/drizzle",
    "fog": "Fog", "tstorm": "Thunderstorm", "hub_vis_lt_1_6": "Low visibility at Vancouver",
    "hub_gust_60": "Strong wind at Vancouver",
    "ceil_lt_1000": "Cloud base under 1,000 ft", "ceil_lt_3000": "Cloud base under 3,000 ft",
}


def _vals(rows, key):
    return [r[key] for r in rows if r.get(key) is not None]


def extract(origin_rows, hub_rows, origin_av=None, hub_av=None):
    """origin_rows/hub_rows: hourly weather-model dicts around departure / arrival.
    origin_av/hub_av: official airport conditions from aviation.conditions(), if any."""
    f = {k: 0 for k in FEATURES}
    if origin_rows:
        vis = _vals(origin_rows, "visibility")
        if vis:
            f["vis_lt_1_6"] = int(min(vis) < 1600)
            f["vis_lt_4_8"] = int(min(vis) < 4800)
        low = _vals(origin_rows, "cloud_cover_low")
        f["lowcloud_90"] = int(bool(low) and max(low) >= 90)
        gust = _vals(origin_rows, "wind_gusts_10m")
        if gust:
            f["gust_35"] = int(max(gust) >= 35)
            f["gust_50"] = int(max(gust) >= 50)
        snow = _vals(origin_rows, "snowfall")
        if snow:
            f["snow_03"] = int(max(snow) >= 0.3)
            f["snow_10"] = int(max(snow) >= 1.0)
        codes = set(_vals(origin_rows, "weather_code"))
        f["freezing"] = int(bool(codes & {56, 57, 66, 67}))
        f["fog"] = int(bool(codes & {45, 48}))
        f["tstorm"] = int(bool(codes & {95, 96, 99}))
    if hub_rows:
        v = _vals(hub_rows, "visibility")
        g = _vals(hub_rows, "wind_gusts_10m")
        f["hub_vis_lt_1_6"] = int(bool(v) and min(v) < 1600)
        f["hub_gust_60"] = int(bool(g) and max(g) >= 60)
    if origin_av:
        _apply_airport(f, origin_av)
    if hub_av:
        if hub_av["vis_sm"] is not None and hub_av["vis_sm"] < 1:
            f["hub_vis_lt_1_6"] = 1
        if hub_av["gust_kt"] is not None and hub_av["gust_kt"] * 1.852 >= 60:
            f["hub_gust_60"] = 1
    return f


def _apply_airport(f, av):
    """Official report/forecast can only add warnings, never clear model ones."""
    c, v, g, wx = av["ceiling_ft"], av["vis_sm"], av["gust_kt"], av["wx"]
    if c is not None:
        f["ceil_lt_1000"] |= int(c < 1000)
        f["ceil_lt_3000"] |= int(c < 3000)
    if v is not None:
        f["vis_lt_1_6"] |= int(v < 1)      # 1 statute mile = 1.6 km
        f["vis_lt_4_8"] |= int(v < 3)
    if g is not None:
        f["gust_35"] |= int(g * 1.852 >= 35)
        f["gust_50"] |= int(g * 1.852 >= 50)
    for w in wx:
        if "FZ" in w: f["freezing"] = 1
        if "FG" in w and "FZ" not in w: f["fog"] = 1
        if "TS" in w: f["tstorm"] = 1
        if "SN" in w:
            f["snow_03"] = 1
            if w.startswith("+"): f["snow_10"] = 1


def probability(feats, weights=None):
    w = weights or PRIOR
    z = w["bias"] + sum(w.get(k, 0) * v for k, v in feats.items())
    return 1 / (1 + math.exp(-z))


def reasons(feats, weights=None):
    w = weights or PRIOR
    hits = [(w.get(k, 0), k) for k, v in feats.items() if v and w.get(k, 0) > 0.3]
    return [LABELS[k] for _, k in sorted(hits, reverse=True)]


def level(p, watch=0.15, alert=0.30):
    if p >= 0.60: return "SEVERE"
    if p >= alert: return "HIGH"
    if p >= watch: return "WATCH"
    return "LOW"
