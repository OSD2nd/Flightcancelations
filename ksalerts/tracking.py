"""Forecast track record.

Every check, note what we forecast for each flight (before any airline status
override). Over the week leading up to a flight this builds a line - 7 days
out, 3 days out, the night before, the morning of - so the History page can
show whether the forecast homed in on cancellations or was all over the shop.

data/predictions/YYYY-MM.json (month of the flight):
  {"2026-11-11|YZZ|8P452": {"sched": "10:50", "p": [["2026-11-05T19:07", 4], ...],
                            "outcome": "Canceled"}}
A new point is saved at most every 3 hours unless the forecast moves 5+ points.
"""
import glob, json, os
from datetime import datetime, timedelta

DIR = "data/predictions"
PUBLIC = "docs/predictions.json"
FINAL = ("arrived", "departed", "landed", "cancel", "divert")


def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def record(results, now):
    stamp = now.strftime("%Y-%m-%dT%H:%M")
    files = {}
    for r in results:
        path = f"{DIR}/{r['date'][:7]}.json"
        recs = files.setdefault(path, _load(path))
        key = f"{r['date']}|{r['airport']}|{r['flight']}"
        rec = recs.setdefault(key, {"sched": r["dep_local"], "direction": r["direction"], "p": []})
        pct = round(100 * r.get("forecast_risk", r["risk"]))
        flight_time = datetime.fromisoformat(f"{r['date']}T{r['dep_local']}")
        if now <= flight_time + timedelta(minutes=30):
            last = rec["p"][-1] if rec["p"] else None
            if (last is None or abs(pct - last[1]) >= 5
                    or now - datetime.fromisoformat(last[0]) >= timedelta(hours=3)):
                rec["p"].append([stamp, pct])
        if r.get("schedule_source") == "live" and str(r.get("status", "")).lower().startswith(FINAL):
            rec["outcome"] = r["status"]
    os.makedirs(DIR, exist_ok=True)
    for path, recs in files.items():
        with open(path, "w") as f:
            json.dump(recs, f, separators=(",", ":"))


def publish(today, days_back=45):
    cutoff = (today - timedelta(days=days_back)).isoformat()
    out = {}
    for path in sorted(glob.glob(f"{DIR}/*.json")):
        for k, v in _load(path).items():
            if k.split("|")[0] >= cutoff:
                out[k] = v
    with open(PUBLIC, "w") as f:
        json.dump(out, f, separators=(",", ":"))
