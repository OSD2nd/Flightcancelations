"""Score the coming week's flights, write the dashboard data, send email alerts.

Run times live in config.json "alert_times" (local time). The GitHub workflow
wakes every 30 minutes with --scheduled; this script skips unless one of those
times has come up and hasn't been handled yet today.
"""
import argparse, json, os, re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import alert, features as F, flights, history, weather

OUT = "docs/latest.json"
STATE = "data/state.json"
LIVE_DAYS = 2      # days that use the flight API (if a key is set); later days use config times
LATE_OK_HOURS = 3  # a scheduled check still runs if GitHub starts it up to this late


def demo_weather(code, days):
    """Fake weather for offline testing: valley fog + snow at Castlegar tomorrow."""
    out = {}
    for d in days:
        for h in range(24):
            t = datetime(d.year, d.month, d.day, h)
            r = dict(temperature_2m=1.0, precipitation=0.0, snowfall=0.0, weather_code=3,
                     visibility=20000, cloud_cover_low=40, wind_speed_10m=10, wind_gusts_10m=20)
            if code == "YCG" and d == days[1] and 10 <= h <= 14:
                r.update(weather_code=45, visibility=700, cloud_cover_low=100, snowfall=0.4)
            if code == "YZZ" and d == days[1] and 8 <= h <= 12:
                r.update(visibility=3500, cloud_cover_low=95, wind_gusts_10m=38)
            out[t] = r
    return out


def load_state():
    try:
        with open(STATE) as f:
            s = json.load(f)
    except Exception:
        s = {}
    if "alerts" not in s:  # older flat format
        s = {"alerts": {}, "runs": []}
    s.setdefault("runs", [])
    return s


def due_slot(now, times, done):
    """The alert time that is due now and not yet handled, as 'YYYY-MM-DD HH:MM', else None."""
    for t in times:
        h, m = map(int, t.split(":"))
        for back in (0, 1):  # a 23:30 slot may run just after midnight
            d = now.date() - timedelta(days=back)
            slot = datetime(d.year, d.month, d.day, h, m)
            key = f"{d.isoformat()} {t}"
            if slot <= now < slot + timedelta(hours=LATE_OK_HOURS) and key not in done:
                return key
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="fake weather, no network")
    ap.add_argument("--no-email", action="store_true")
    ap.add_argument("--scheduled", action="store_true",
                    help="only run if one of config alert_times is due")
    ap.add_argument("--now", help="pretend local time, e.g. 2026-11-10T19:07 (testing)")
    ap.add_argument("--train", action="store_true", help="retrain from data/history.csv first")
    ap.add_argument("--backfill", type=int, metavar="DAYS", help="pull past DAYS of flights into history")
    a = ap.parse_args()

    with open("config.json") as f:
        cfg = json.load(f)
    tz = cfg["timezone"]
    now = (datetime.fromisoformat(a.now) if a.now
           else datetime.now(ZoneInfo(tz)).replace(tzinfo=None, second=0, microsecond=0))
    today = now.date()
    state = load_state()

    slot = None
    if a.scheduled:
        slot = due_slot(now, cfg.get("alert_times", ["19:00", "06:30"]), state["runs"])
        if not slot:
            print(f"{now:%H:%M} local: no check due (times: {', '.join(cfg.get('alert_times', []))})")
            return

    if a.backfill:
        history.backfill(cfg, a.backfill)
    if a.train or a.backfill:
        history.train(cfg)

    model = history.load_model()
    weights = model["weights"] if model else None
    n_days = max(1, min(14, int(cfg.get("days_ahead", 7))))
    days = [today + timedelta(days=i) for i in range(n_days)]

    wx = {}
    for code, ap_cfg in list(cfg["airports"].items()) + [("HUB", cfg["hub"])]:
        wx[code] = (demo_weather(code, days) if a.demo else
                    weather.fetch_hourly(ap_cfg["lat"], ap_cfg["lon"], days[0], days[-1], False, tz))

    results = []
    for code, ap_cfg in cfg["airports"].items():
        for i, d in enumerate(days):
            if a.demo or i >= LIVE_DAYS:
                fl, source = flights.departures_static(code, d, cfg), "static"
            else:
                fl, source = flights.departures(code, d, cfg)
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
               "today": today.isoformat(), "demo": a.demo,
               "model": ("learned" if model else "starting-weights"),
               "model_info": {k: model[k] for k in ("flights", "cancellations", "trained")} if model else None,
               "settings": {k: cfg.get(k) for k in ("alert_times", "days_ahead", "alert_threshold", "email_to")},
               "flights": results, "history_stats": [] if a.demo else _safe_stats(cfg)}
    with open(OUT, "w") as f:
        json.dump(payload, f, indent=1)
    # also embed the data in the page so it works when opened as a plain file
    with open("docs/index.html") as f:
        page = f.read()
    seed = json.dumps(payload).replace("</", "<\\/")
    page = re.sub(r'(<script id="seed" type="application/json">).*?(</script>)',
                  lambda m: m.group(1) + seed + m.group(2), page, count=1, flags=re.S)
    with open("docs/index.html", "w") as f:
        f.write(page)

    # ---- alerts: flights leaving in the next alert_window_hours, once per flight per level ----
    horizon = now + timedelta(hours=cfg.get("alert_window_hours", 30))
    new = []
    for r in results:
        dep = datetime.fromisoformat(f"{r['date']}T{r['dep_local']}")
        if not (now <= dep <= horizon) or r["risk"] < cfg["alert_threshold"]:
            continue
        key = f"{r['date']}|{r['flight']}"
        if state["alerts"].get(key) != r["level"]:
            new.append(r)
            state["alerts"][key] = r["level"]
    if new:
        lines = [f"{r['airline']} {r['flight']} from {r['airport_name']} at {r['dep_local']} on "
                 f"{_day_name(r['date'], today)}: {round(r['risk']*100)}% cancellation risk ({r['level']}). "
                 f"{'; '.join(r['reasons']) or 'Elevated overall conditions'}." for r in new]
        body = ("Flight risk heads-up:\n\n" + "\n\n".join(lines) +
                "\n\nThis is an estimate from weather forecasts and past cancellations, not an airline notice. "
                "Check with the airline before you travel.")
        subject = f"Flight risk: {', '.join(r['flight'] for r in new)}"
        if a.no_email:
            print(subject, "\n", body)
        else:
            alert.send(subject, body, cfg["email_to"])

    if slot:
        state["runs"].append(slot)
    cutoff = (today - timedelta(days=2)).isoformat()
    state = {"alerts": {k: v for k, v in state["alerts"].items() if k.split("|")[0] >= today.isoformat()},
             "runs": [s for s in state["runs"] if s >= cutoff]}
    os.makedirs("data", exist_ok=True)
    with open(STATE, "w") as f:
        json.dump(state, f, indent=1)
    print(f"{now:%Y-%m-%d %H:%M} local{' (' + slot + ' check)' if slot else ''}: "
          f"scored {len(results)} flights over {n_days} days, {len(new)} new alert(s)")


def _day_name(d, today):
    d = date.fromisoformat(d)
    if d == today: return "today"
    if d == today + timedelta(days=1): return "tomorrow"
    return d.strftime("%a %b %d")


def _safe_stats(cfg):
    try:
        return history.condition_stats(cfg)
    except Exception as e:
        print(f"[warn] history stats skipped: {e}")
        return []


if __name__ == "__main__":
    main()
