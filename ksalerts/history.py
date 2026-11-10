"""Learn from past cancellations.

backfill(): pull past departures + status into data/history.csv
train():    for each past flight, rebuild the weather around it, then fit the
            risk weights (pulled toward the starting guesses so a small
            record can't produce silly numbers).
"""
import csv, math, os
from collections import defaultdict
from datetime import date, datetime, timedelta

from . import features as F, flights, weather

HIST = "data/history.csv"
MODEL = "data/model.json"
COLS = ["date", "airport", "flight", "sched", "status"]


def load():
    if not os.path.exists(HIST):
        return []
    with open(HIST, newline="") as f:
        return list(csv.DictReader(f))


def _save(rows):
    os.makedirs("data", exist_ok=True)
    with open(HIST, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["date"], r["airport"], r["sched"])))


FINAL = ("departed", "arrived", "cancel", "diverted")


def record_outcomes(results, now):
    """Add today's flights with a known outcome (live status) to the history file."""
    rows = {(r["date"], r["airport"], r["flight"]): r for r in load()}
    added = 0
    for r in results:
        if r["schedule_source"] != "live" or not str(r["status"]).lower().startswith(FINAL):
            continue
        k = (r["date"], r["airport"], r["flight"])
        new = {"date": r["date"], "airport": r["airport"], "flight": r["flight"],
               "sched": r["dep_local"], "status": r["status"]}
        if rows.get(k) != new:
            rows[k] = new
            added += 1
    if added:
        _save(list(rows.values()))
        print(f"history: recorded {added} flight outcome(s)")


def is_cancelled(status):
    return str(status).lower().startswith("cancel")


def backfill(cfg, days):
    key = os.environ.get("AERODATABOX_KEY")
    if not key:
        raise SystemExit("Backfill needs AERODATABOX_KEY. Or fill data/history.csv by hand (date,airport,flight,sched,status).")
    rows = {(r["date"], r["airport"], r["flight"]): r for r in load()}
    today = date.today()
    for n in range(1, days + 1):
        d = today - timedelta(days=n)
        for code in cfg["airports"]:
            try:
                for f in flights.departures_api(code, d, cfg["hub"]["code"], key):
                    rows[(d.isoformat(), code, f["flight"])] = {
                        "date": d.isoformat(), "airport": code, "flight": f["flight"],
                        "sched": f["dep"].strftime("%H:%M"), "status": f["status"]}
            except Exception as e:
                print(f"[warn] {d} {code}: {e}")
    _save(list(rows.values()))
    print(f"history now has {len(rows)} flights")


def _build_dataset(cfg, rows):
    """Attach weather flags to each historical flight. Weather cached per airport per month."""
    tz = cfg["timezone"]
    cache = {}

    def wx(code, lat, lon, d):
        k = (code, d.year, d.month)
        if k not in cache:
            first = date(d.year, d.month, 1)
            last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
            cache[k] = weather.fetch_hourly(lat, lon, first, min(last, date.today()), True, tz)
        return cache[k]

    hub = cfg["hub"]
    X, y = [], []
    for r in rows:
        d = date.fromisoformat(r["date"])
        h, m = map(int, r["sched"].split(":"))
        dep = datetime(d.year, d.month, d.day, h, m)
        a = cfg["airports"][r["airport"]]
        try:
            o = weather.window(wx(r["airport"], a["lat"], a["lon"], d), dep, 1, 1)
            hb = weather.window(wx("HUB", hub["lat"], hub["lon"], d), dep + timedelta(hours=1), 1, 1)
        except Exception as e:
            print(f"[warn] skipping {r['date']} {r['flight']}: {e}")
            continue
        X.append(F.extract(o, hb))
        y.append(1 if is_cancelled(r["status"]) else 0)
    return X, y


def fit(X, y, prior=None, lam=2.0, lr=0.05, iters=2000):
    """Logistic regression pulled toward the prior weights (MAP estimate)."""
    prior = prior or F.PRIOR
    names = ["bias"] + F.FEATURES
    w = dict(prior)
    n = max(len(y), 1)
    for _ in range(iters):
        grad = defaultdict(float)
        for x, t in zip(X, y):
            p = F.probability(x, w)
            e = p - t
            grad["bias"] += e
            for k in F.FEATURES:
                if x[k]:
                    grad[k] += e
        for k in names:
            g = grad[k] / n + lam * (w[k] - prior[k]) / n
            w[k] -= lr * g * 10
    return w


def train(cfg):
    import json
    rows = load()
    if len(rows) < 30:
        print(f"Only {len(rows)} flights in history - need at least 30 to learn anything. Keeping starting weights.")
        return None
    X, y = _build_dataset(cfg, rows)
    cancels = sum(y)
    if cancels < 5:
        print(f"Only {cancels} cancellations in history - too few to learn from yet. Keeping starting weights.")
        return None
    w = fit(X, y)
    os.makedirs("data", exist_ok=True)
    with open(MODEL, "w") as f:
        json.dump({"weights": w, "flights": len(y), "cancellations": cancels,
                   "trained": datetime.now().isoformat(timespec="minutes")}, f, indent=2)
    print(f"trained on {len(y)} flights, {cancels} cancellations -> {MODEL}")
    return w


def load_model():
    import json
    if os.path.exists(MODEL):
        with open(MODEL) as f:
            return json.load(f)
    return None


def condition_stats(cfg):
    """Cancellation rate when each risk flag was present (for the dashboard). Needs weather fetch."""
    rows = load()
    if len(rows) < 30:
        return []
    X, y = _build_dataset(cfg, rows)
    out = []
    for k in F.FEATURES:
        idx = [i for i, x in enumerate(X) if x[k]]
        if len(idx) >= 3:
            out.append({"condition": F.LABELS[k], "flights": len(idx),
                        "cancelled": sum(y[i] for i in idx),
                        "rate": round(sum(y[i] for i in idx) / len(idx), 3)})
    return sorted(out, key=lambda r: -r["rate"])
