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
    "rain", "valley_cloud", "ridge_cloud", "wet_low_cloud", "ceil_lt_4000",
    "smoke", "smoke_heavy", "low_patches", "ec_fog", "ec_wintry", "ec_warning", "below_limit",
]

PRIOR = {
    "bias": -3.3,          # ~3.5% baseline chance of cancellation on a clear day
    "vis_lt_1_6": 2.0, "vis_lt_4_8": 0.8, "lowcloud_90": 0.9,
    "gust_35": 0.5, "gust_50": 1.2,
    "snow_03": 0.4, "snow_10": 1.0,
    "freezing": 2.0, "fog": 1.2, "tstorm": 1.5,
    "hub_vis_lt_1_6": 1.0, "hub_gust_60": 0.8,
    # Cloud base from the official airport report/forecast. Castlegar sits in a
    # deep valley, so a low ceiling matters more than cloud percentage.
    "ceil_lt_1000": 1.6, "ceil_lt_3000": 1.2,
    # Nov 2026 Castlegar record: the cancelled mornings (Nov 6, Nov 10) had cloud
    # near 3,000 ft WITH rain/mist; a dry 2,400 ft ceiling (Nov 4) still flew.
    # So the combination carries most of the weight. Estimates until history is fitted.
    "rain": 0.5, "valley_cloud": 0.8, "ridge_cloud": 0.5, "wet_low_cloud": 2.0,
    "ceil_lt_4000": 0.3,
    # Wildfire smoke (summer): fine particles from the air-quality forecast, or smoke
    # in the airport report. Thick smoke cuts visibility like fog does.
    "smoke": 0.7, "smoke_heavy": 1.6,
    # Patches of cloud/fog under 1,000 ft (FEW/SCT, not a solid layer). On 11 Nov 2026 this
    # alone cancelled Trail's morning flights while we said 4%.
    "low_patches": 2.0,
    # Environment Canada's official forecast wording around the flight time
    # fog wording raised after 11 Nov (Trail cancelled in fog patches while we said 4%)
    "ec_fog": 1.8, "ec_wintry": 1.2, "ec_warning": 0.6,
    # cloud base under THIS airport's practical limit (config "ceiling_limit_ft"). Nov 11-27, 2026:
    # Trail was cancelled every day Castlegar reported a deck at 3,300-4,000 ft and flew with 4,300 ft
    # and up, rain or not. A deck 700 ft under the limit alone gives ~43%, with rain or snow 85%+.
    "below_limit": 3.0,
}

LABELS = {
    "vis_lt_1_6": "Visibility under 1.6 km", "vis_lt_4_8": "Reduced visibility (under 4.8 km)",
    "lowcloud_90": "Heavy low cloud", "gust_35": "Gusts over 35 km/h", "gust_50": "Gusts over 50 km/h",
    "snow_03": "Snow falling", "snow_10": "Heavy snow (1+ cm/h)", "freezing": "Freezing rain/drizzle",
    "fog": "Fog", "tstorm": "Thunderstorm", "hub_vis_lt_1_6": "Low visibility at Vancouver",
    "hub_gust_60": "Strong wind at Vancouver",
    "ceil_lt_1000": "Cloud base under 1,000 ft", "ceil_lt_3000": "Cloud base under 3,000 ft",
    "rain": "Rain or drizzle", "valley_cloud": "Cloud down in the valley", "ridge_cloud": "Cloud on the ridges",
    "wet_low_cloud": "Low cloud with rain (the usual cancellation pattern)",
    "ceil_lt_4000": "Cloud base under 4,000 ft",
    "smoke": "Wildfire smoke", "smoke_heavy": "Thick wildfire smoke",
    "low_patches": "Low cloud or fog patches under 1,000 ft",
    "ec_fog": "Environment Canada forecasts fog or low cloud", "ec_wintry": "Environment Canada forecasts freezing rain or heavy snow",
    "ec_warning": "Environment Canada weather warning in effect",
    "below_limit": "Cloud base below this airport's limit",
}


def _vals(rows, key):
    return [r[key] for r in rows if r.get(key) is not None]


def ramp(x, lo, hi):
    """0 at lo, 1 at hi, straight line between (works for falling scales too, e.g. visibility).
    Soft edges instead of yes/no cut-offs: low cloud going 88% -> 91% used to flip a whole
    flag and swing the risk by tens of points; now it moves it a little."""
    if x is None:
        return 0.0
    t = (x - lo) / (hi - lo)
    return round(max(0.0, min(1.0, t)), 3)


def _typical(rows, key, worst):
    """The hour at flight time counts double; the hours either side count once. Then a
    blend of that average and the worst hour, so one odd hour can't decide it alone."""
    vals = [(r.get(key), 2 if i == len(rows) // 2 else 1) for i, r in enumerate(rows)]
    vals = [(v, w) for v, w in vals if v is not None]
    if not vals:
        return None
    avg = sum(v * w for v, w in vals) / sum(w for _, w in vals)
    return (avg + worst(v for v, _ in vals)) / 2


def extract(origin_rows, hub_rows, origin_av=None, hub_av=None, ec=None, limit_ft=None, borrowed=False):
    """origin_rows/hub_rows: hourly weather-model dicts around departure / arrival.
    origin_av/hub_av: official airport conditions from aviation.conditions(), if any.
    Model-based features are 0..1 (how strongly), report/forecast wording ones are 0 or 1."""
    f = {k: 0 for k in FEATURES}
    if origin_rows:
        vis = _typical(origin_rows, "visibility", min)
        if vis is not None:
            f["vis_lt_1_6"] = ramp(vis, 3000, 1000)
            f["vis_lt_4_8"] = ramp(vis, 8000, 3500)
        f["lowcloud_90"] = ramp(_typical(origin_rows, "cloud_cover_low", max), 70, 100)
        gust = _typical(origin_rows, "wind_gusts_10m", max)
        f["gust_35"] = ramp(gust, 30, 45)
        f["gust_50"] = ramp(gust, 45, 60)
        snow = _typical(origin_rows, "snowfall", max)
        f["snow_03"] = ramp(snow, 0.1, 0.5)
        f["snow_10"] = ramp(snow, 0.7, 1.5)
        pm = _typical(origin_rows, "pm2_5", max)
        f["smoke"] = ramp(pm, 60, 140)
        f["smoke_heavy"] = ramp(pm, 180, 300)
        f["rain"] = ramp(_typical(origin_rows, "precipitation", max), 0.05, 0.4)
        # cloud at pressure levels: ~925/900 hPa is valley height around Castlegar/Trail,
        # ~850 hPa is roughly the ridge tops the approach threads between
        vc = [x for x in (_typical(origin_rows, "cloud_cover_925hPa", max),
                          _typical(origin_rows, "cloud_cover_900hPa", max)) if x is not None]
        f["valley_cloud"] = ramp(max(vc), 60, 100) if vc else 0
        f["ridge_cloud"] = ramp(_typical(origin_rows, "cloud_cover_850hPa", max), 60, 100)
        codes = [r.get("weather_code") for r in origin_rows]
        share = lambda cs: sum(1 for c in codes if c in cs) / len(codes)
        f["freezing"] = round(share({56, 57, 66, 67}), 3)
        f["fog"] = round(share({45, 48}), 3)
        f["tstorm"] = round(share({95, 96, 99}), 3)
    if hub_rows:
        f["hub_vis_lt_1_6"] = ramp(_typical(hub_rows, "visibility", min), 3000, 1000)
        f["hub_gust_60"] = ramp(_typical(hub_rows, "wind_gusts_10m", max), 50, 70)
    if ec:
        f["ec_fog"] = strength(ec.get("fog_level", 5 if ec.get("fog") else 0))
        f["ec_wintry"] = int(ec.get("wintry", False))
        f["ec_warning"] = int(bool(ec.get("warnings")))
        if ec.get("snow"):
            f["snow_03"] = max(f["snow_03"], 0.5)
    if origin_rows and limit_ft and not origin_av:
        # no airport report/forecast reaches this far ahead: the models' own cloud deck stands in -
        # heavy low cloud AND cloud at ridge height (~850 hPa, about 3,000 ft above Castlegar), at 60%
        f["below_limit"] = round(0.6 * min(f["lowcloud_90"], f["ridge_cloud"]), 3)
    if origin_av:
        # a plainly clear airport report close to the flight outweighs forecasts made hours ago
        # (Nov 20-23: 55-94% on 15-mile mornings that flew). Phased in: none 3 h out, full within 1 h.
        w = origin_av.get("obs_weight", 1.0) if origin_av.get("obs_clear") else 0.0
        for k in OBS_DAMPED:
            f[k] = round(f[k] * (1 - 0.5 * w), 3)
        both = {k: 0 for k in FEATURES}
        _apply_airport(both, origin_av, limit_ft, borrowed)
        obs = {k: 0 for k in FEATURES}
        if w:
            _apply_airport(obs, origin_av.get("obs") or origin_av, limit_ft, borrowed)
        tmp = {k: 0 for k in FEATURES}
        if origin_av.get("tempo"):
            # TEMPO/PROB groups = "could briefly be worse": half weight (none once the report is clear)
            _apply_airport(tmp, origin_av["tempo"], limit_ft, borrowed)
        for k in FEATURES:
            v = max((1 - w) * both[k] + w * obs[k], 0.5 * (1 - w) * tmp[k])
            f[k] = max(f[k], round(v, 3))
    # the usual cancellation pattern is low cloud AND rain/snow together: how much of both
    # (cancellations so far had cloud bases of 3,000-3,500 ft with rain, so "low" means under 4,000 ft)
    low_cloud = max(f["lowcloud_90"], f["ceil_lt_4000"], f["valley_cloud"], f["low_patches"], f["ec_fog"],
                    f["below_limit"])
    wet = max(f["rain"], f["snow_03"], f["freezing"], f["fog"])
    f["wet_low_cloud"] = round(low_cloud * wet, 3)
    if hub_av:
        if hub_av["vis_sm"] is not None and hub_av["vis_sm"] < 1:
            f["hub_vis_lt_1_6"] = 1
        if hub_av["gust_kt"] is not None and hub_av["gust_kt"] * 1.852 >= 60:
            f["hub_gust_60"] = 1
    return f


# forecast-based signals that a clear airport report close to the flight cuts in half
OBS_DAMPED = ("lowcloud_90", "valley_cloud", "ridge_cloud", "rain", "snow_03", "snow_10", "vis_lt_1_6",
              "vis_lt_4_8", "fog", "ec_fog")


def strength(n):
    """1-5 strength -> the 0-1 value the model uses (1 = 0.2 ... 5 = 1.0)."""
    return round(max(0, min(5, n)) / 5, 2)


def patch_level(lowest_ft, low_remark, misty, cover=None, wet=False):
    """Low cloud/fog patches in the airport report, 1-5. From the record so far:
    Nov 14 (FEW004 with rain) and Nov 15 (FEW002/FEW003 with mist) cancelled, but Nov 21 (FEW003) and
    Nov 23 (FEW007) flew on dry 15-mile mornings. So a FEW layer counts fully only with mist, rain or snow.
      under 300 ft = 5, under 500 = 4, under 700 = 3, under 1,000 = 2;
      only the 'low cloud around' remark (LO CLD) = 2; mist or fog with it = one step up;
      just a few dry patches (FEW, no mist/rain/snow, no LO CLD remark) = two steps down (at least 1)."""
    if lowest_ft is not None and lowest_ft < 1000:
        n = 5 if lowest_ft < 300 else 4 if lowest_ft < 500 else 3 if lowest_ft < 700 else 2
    elif low_remark:
        n = 2
    else:
        return 0
    if cover == "FEW" and not (misty or wet or low_remark):
        return max(1, n - 2)
    return min(5, n + (1 if misty else 0))


def _apply_airport(f, av, limit_ft=None, borrowed=False):
    """Official report/forecast can only add warnings, never clear model ones.
    borrowed = the report is from another airport's station (Trail uses Castlegar's)."""
    c, v, g, wx = av["ceiling_ft"], av["vis_sm"], av["gust_kt"], av["wx"]
    fog_here = any(("BR" in w or "FG" in w) for w in wx)
    precip = any(any(p in w for p in ("RA", "SN", "DZ", "SH", "PL")) for w in wx)
    # fog at the borrowed station (Castlegar) with no rain/snow: often local to it - on Nov 21 Castlegar
    # had freezing fog at 200 ft while Trail flew. Its fog signals count half for the other airport.
    local_fog = borrowed and fog_here and not precip and ((c is not None and c < 1500) or (v is not None and v < 3))
    before = dict(f) if local_fog else None
    if c is not None and limit_ft:
        # nothing 300 ft above the limit, full 700 ft below it (Trail, limit 4,000: 4,300 -> 3,300 ft)
        b = ramp(c, limit_ft + 300, limit_ft - 700)
        if borrowed and c < 1500 and any(("BR" in w or "FG" in w) for w in wx):
            # very low fog at the borrowed station is local to it (Castlegar freezing fog on Nov 21 while
            # Trail flew); the 3,000-4,000 ft deck that cancels Trail is regional
            b = 0
        f["below_limit"] = max(f["below_limit"], b)
    misty = any(("BR" in w or "FG" in w) for w in wx)
    wet = any(any(p in w for p in ("RA", "SN", "DZ", "SH", "PL")) for w in wx)
    f["low_patches"] = max(f["low_patches"], strength(patch_level(av.get("lowest_ft"), av.get("low_remark"), misty,
                                                                   av.get("lowest_cover"), wet)))
    if c is not None:
        # graded: a 3,900 ft cloud base barely counts toward "under 4,000", 3,000 ft fully
        f["ceil_lt_1000"] = max(f["ceil_lt_1000"], ramp(c, 1500, 800))
        f["ceil_lt_3000"] = max(f["ceil_lt_3000"], ramp(c, 3200, 2200))
        f["ceil_lt_4000"] = max(f["ceil_lt_4000"], ramp(c, 4200, 3000))
    if v is not None:
        f["vis_lt_1_6"] = max(f["vis_lt_1_6"], ramp(v, 1.5, 0.5))      # statute miles (1 mi = 1.6 km)
        f["vis_lt_4_8"] = max(f["vis_lt_4_8"], ramp(v, 5, 2))
    if g is not None:
        f["gust_35"] = max(f["gust_35"], int(g * 1.852 >= 35))
        f["gust_50"] = max(f["gust_50"], int(g * 1.852 >= 50))
    for w in wx:
        if "FU" in w:  # smoke reported at the airport
            f["smoke"] = 1
            if v is not None and v < 3:
                f["smoke_heavy"] = 1
        if any(p in w for p in ("RA", "DZ", "BR")): f["rain"] = 1
        if "FZ" in w: f["freezing"] = 1
        if "FG" in w and "FZ" not in w: f["fog"] = 1
        if "TS" in w: f["tstorm"] = 1
        if "SN" in w:
            f["snow_03"] = 1
            if w.startswith("+"): f["snow_10"] = 1
    if local_fog:
        for k in ("low_patches", "ceil_lt_1000", "ceil_lt_3000", "ceil_lt_4000", "vis_lt_1_6", "vis_lt_4_8",
                  "fog", "freezing", "rain"):
            f[k] = max(before[k], round(0.5 * f[k], 3))


def probability(feats, weights=None):
    w = weights or PRIOR
    z = w["bias"] + sum(w.get(k, 0) * v for k, v in feats.items())
    return 1 / (1 + math.exp(-z))


def reasons(feats, weights=None):
    w = weights or PRIOR
    hits = [(w.get(k, 0) * v, k) for k, v in feats.items() if v >= 0.4 and w.get(k, 0) > 0.3]
    return [LABELS[k] for _, k in sorted(hits, reverse=True)]


def signals(feats, weights=None):
    """What's pushing the risk up, strongest first, each with a 1-5 strength for the cards."""
    w = weights or PRIOR
    hits = [(w.get(k, 0) * v, k, v) for k, v in feats.items() if v >= 0.2 and w.get(k, 0) > 0.3]
    return [{"label": LABELS[k], "level": max(1, min(5, round(v * 5)))} for _, k, v in sorted(hits, reverse=True)]


def lead_shrink(p, hours_ahead, weights=None):
    """Days out, a weather model can't know a flight is 98% doomed: pull far-off forecasts toward
    the everyday rate. Full strength within 2 days, 90% at 3 days, easing to 60% at 6+ days."""
    w = weights or PRIOR
    k = 1.0 if hours_ahead <= 48 else max(0.6, 1.0 - 0.4 * (hours_ahead - 48) / 96)
    z = math.log(p / (1 - p))
    return 1 / (1 + math.exp(-(w["bias"] + (z - w["bias"]) * k)))


def level(p, watch=0.15, alert=0.30):
    if p >= 0.60: return "SEVERE"
    if p >= alert: return "HIGH"
    if p >= watch: return "WATCH"
    return "LOW"
