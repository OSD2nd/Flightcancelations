"""Turn weather rows into yes/no risk flags, and hold the starting weights.

The PRIOR weights are educated guesses about what grounds small-airport
flights in mountain valleys. history.train() nudges them toward what the
real cancellation record says, so they get better as data builds up.
"""
import math

FEATURES = [
    "vis_lt_1_6", "vis_lt_4_8", "lowcloud_90", "gust_35", "gust_50",
    "snow_03", "snow_10", "freezing", "fog", "tstorm",
    "hub_vis_lt_1_6", "hub_gust_60",
]

PRIOR = {
    "bias": -3.6,          # ~3% baseline chance of cancellation on any flight
    "vis_lt_1_6": 2.0, "vis_lt_4_8": 0.8, "lowcloud_90": 0.7,
    "gust_35": 0.5, "gust_50": 1.2,
    "snow_03": 0.4, "snow_10": 1.0,
    "freezing": 2.0, "fog": 1.2, "tstorm": 1.5,
    "hub_vis_lt_1_6": 1.0, "hub_gust_60": 0.8,
}

LABELS = {
    "vis_lt_1_6": "Visibility under 1.6 km", "vis_lt_4_8": "Reduced visibility (under 4.8 km)",
    "lowcloud_90": "Heavy low cloud", "gust_35": "Gusts over 35 km/h", "gust_50": "Gusts over 50 km/h",
    "snow_03": "Snow falling", "snow_10": "Heavy snow (1+ cm/h)", "freezing": "Freezing rain/drizzle",
    "fog": "Fog", "tstorm": "Thunderstorm", "hub_vis_lt_1_6": "Low visibility at Vancouver",
    "hub_gust_60": "Strong wind at Vancouver",
}


def _vals(rows, key):
    return [r[key] for r in rows if r.get(key) is not None]


def extract(origin_rows, hub_rows):
    """origin_rows/hub_rows: hourly weather dicts around departure / arrival."""
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
    return f


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
