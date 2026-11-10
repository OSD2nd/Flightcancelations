"""Nightly job: score tomorrow's (and today's) flights, write the dashboard data, send alerts."""
import argparse, json, os
from datetime import date, datetime, timedelta, timezone

from . import alert, features as F, flights, history, weather

OUT = "docs/latest.json"
STATE = "data/state.json"


def demo_weather(code, days):
    """Fake weather for offline testing: valley fog + snow at Castlegar tomorrow."""
    out = {}
    for d in days:
        for h in range(24):
            t = datetime(d.year, d.month, d.day, h)
            r = dict(temperature_2m=1.0, precipitation=0.0, snowfall=0.0, weather_code=3,
                     visibility=20000, cloud_cover_low=40, wind_speed_10m=10, wind_gusts_10m=20)
            if code == "YCG" and d == days[-1] and 6 <= h <= 13:
                r.update(weather_code=45, visibility=700, cloud_cover_low=100, snowfall=0.4)
            if code == "YZZ" and d == days[-1] and 8 <= h <= 12:
                r.update(visibility=3500, cloud_cover_low=95, wind_gusts_10m=38)
            out[t] = r
    return out


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except Exception:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="fake weather, no network")
    ap.add_argument("--no-email", action="store_true")
    ap.add_argument("--train", action="store_true", help="retrain from data/history.csv first")
    ap.add_argument("--backfill", type=int, metavar="DAYS", help="pull past DAYS of flights into history")
    a = ap.parse_args()

    with open("config.json") as f:
        cfg = json.load(f)
    if a.backfill:
        history.backfill(cfg, a.backfill)
    if a.train or a.backfill:
        history.train(cfg)

    model = history.load_model()
    weights = model["weights"] if model else None
    tz = cfg["timezone"]
    today = date.today()
    days = [today, today + timedelta(days=1)]

    wx = {}
    for code, ap_cfg in list(cfg["airports"].items()) + [("HUB", cfg["hub"])]:
        wx[code] = (demo_weather(code, days) if a.demo else
                    weather.fetch_hourly(ap_cfg["lat"], ap_cfg["lon"], days[0], days[-1], False, tz))

    results = []
    for code, ap_cfg in cfg["airports"].items():
        for d in days:
            fl, source = ([f for f in flights.departures_static(code, d, cfg)], "static") if a.demo \
                else flights.departures(code, d, cfg)
            for f in fl:
                dep = f["dep"]
                arr = dep + timedelta(minutes=f.get("block_min", 75))
                feats = F.extract(weather.window(wx[code], dep, 1, 1), weather.window(wx["HUB"], arr, 1, 1))
                p = F.probability(feats, weights)
                why = F.reasons(feats, weights)
                if history.is_cancelled(f["status"]):
                    p, why = 1.0, ["Airline already shows this flight as cancelled"]
                results.append({
                    "airport": code, "airport_name": ap_cfg["name"], "airline": f["airline"],
                    "flight": f["flight"], "date": d.isoformat(), "dep_local": dep.strftime("%H:%M"),
                    "status": f["status"], "risk": round(p, 3),
                    "level": F.level(p, cfg["watch_threshold"], cfg["alert_threshold"]),
                    "reasons": why, "schedule_source": source,
                    "flags": [k for k, v in feats.items() if v]})

    os.makedirs("docs", exist_ok=True)
    payload = {"generated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
               "demo": a.demo, "model": ("learned" if model else "starting-weights"),
               "model_info": {k: model[k] for k in ("flights", "cancellations", "trained")} if model else None,
               "flights": results, "history_stats": [] if a.demo else _safe_stats(cfg)}
    with open(OUT, "w") as f:
        json.dump(payload, f, indent=1)
    # also embed the data in the page so it works when opened as a plain file
    import re
    with open("docs/index.html") as f:
        page = f.read()
    seed = json.dumps(payload).replace("</", "<\\/")
    page = re.sub(r'(<script id="seed" type="application/json">).*?(</script>)',
                  lambda m: m.group(1) + seed + m.group(2), page, count=1, flags=re.S)
    with open("docs/index.html", "w") as f:
        f.write(page)

    # ---- alerts: only for tomorrow (a heads-up), once per flight per level ----
    tomorrow = days[-1].isoformat()
    state = load_state()
    new = []
    for r in results:
        if r["date"] != tomorrow or r["risk"] < cfg["alert_threshold"]:
            continue
        key = f"{r['date']}|{r['flight']}"
        if state.get(key) != r["level"]:
            new.append(r)
            state[key] = r["level"]
    if new:
        lines = [f"{r['airline']} {r['flight']} from {r['airport_name']} at {r['dep_local']} on {r['date']}: "
                 f"{round(r['risk']*100)}% cancellation risk ({r['level']}). "
                 f"{'; '.join(r['reasons']) or 'Elevated overall conditions'}." for r in new]
        body = ("Heads-up for tomorrow:\n\n" + "\n\n".join(lines) +
                "\n\nThis is an estimate from weather forecasts and past cancellations, not an airline notice. "
                "Check with the airline before you travel.")
        subject = f"Flight risk tomorrow: {', '.join(r['flight'] for r in new)}"
        if a.no_email:
            print(subject, "\n", body)
        else:
            alert.send(subject, body, cfg["email_to"])
    os.makedirs("data", exist_ok=True)
    with open(STATE, "w") as f:
        json.dump({k: v for k, v in state.items() if k.split("|")[0] >= today.isoformat()}, f)
    print(f"scored {len(results)} flights, {len(new)} new alert(s)")


def _safe_stats(cfg):
    try:
        return history.condition_stats(cfg)
    except Exception as e:
        print(f"[warn] history stats skipped: {e}")
        return []


if __name__ == "__main__":
    main()
