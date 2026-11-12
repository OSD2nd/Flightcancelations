"""Score the coming week's flights, write the dashboard data, send email alerts.

The GitHub workflow runs this every 30 minutes with --scheduled: the dashboard
data is refreshed every time, but emails only go out at the times in
config.json "alert_times" (local time).

Email recipients come from the private ALERT_EMAILS secret (comma separated),
falling back to the sending address SMTP_USER. They are never written to the repo.
"""
import argparse, json, os, re
from datetime import date, datetime, timedelta, timezone

from . import alert, aviation, clock, events, tracking, features as F, flights, history, weather, webcam

OUT = "docs/latest.json"
STATE = "data/state.json"
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
    s.setdefault("status_runs", [])
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
                    help="refresh data; only email if one of config alert_times is due")
    ap.add_argument("--now", help="pretend local time, e.g. 2026-11-10T19:07 (testing)")
    ap.add_argument("--train", action="store_true", help="retrain from data/history.csv first")
    ap.add_argument("--backfill", type=int, metavar="DAYS", help="pull past DAYS of flights into history")
    a = ap.parse_args()

    with open("config.json") as f:
        cfg = json.load(f)
    tz = None
    clock.setup(cfg)
    now = (datetime.fromisoformat(a.now) if a.now
           else clock.now())
    today = now.date()
    state = load_state()

    slot = None
    if a.scheduled:
        slot = due_slot(now, cfg.get("alert_times", ["19:00", "06:30"]), state["runs"])
    may_email = slot is not None or not a.scheduled

    # live flight status: only at config "status_checks" (free API plan is ~400 units/month)
    yesterday_status = None
    key = os.environ.get("AERODATABOX_KEY")
    if key and not a.demo:
        checks = cfg.get("status_checks") or [{"time": t} for t in cfg.get("status_times", ["06:30", "10:00", "16:45"])]
        sslot = due_slot(now, [c["time"] for c in checks], state["status_runs"])
        if sslot:
            chk = next(c for c in checks if c["time"] == sslot.split()[1])
            flights.fetch_status(cfg, today, key, now, chk.get("airports"))
            if chk.get("yesterday"):
                yesterday_status = flights.fetch_yesterday(cfg, today - timedelta(days=1), key, chk["yesterday"])
            state["status_runs"].append(sslot)
            print(f"checked live flight status ({sslot}: {', '.join(chk.get('airports') or cfg['airports'])})")
    live = {} if a.demo else flights.load_status()

    if a.backfill:
        history.backfill(cfg, a.backfill)
    if a.train or a.backfill:
        history.train(cfg)

    model = history.load_model()
    weights = model["weights"] if model else None
    n_days = max(1, min(14, int(cfg.get("days_ahead", 7))))
    days = [today + timedelta(days=i) for i in range(n_days)]

    wx, notes = {}, []
    for code, ap_cfg in list(cfg["airports"].items()) + [("HUB", cfg["hub"])]:
        try:
            wx[code] = (demo_weather(code, days) if a.demo else
                        weather.fetch_hourly(ap_cfg["lat"], ap_cfg["lon"], days[0], days[-1], False, tz))
        except Exception as e:
            print(f"[warn] weather model forecast failed for {code}: {e}")
            wx[code] = {}
            notes.append(f"Weather-model forecast unavailable for {ap_cfg['name']} this run.")

    # wildfire smoke forecast (fine particles), merged into the same hourly rows
    if not a.demo:
        for code, ap_cfg in list(cfg["airports"].items()) + [("HUB", cfg["hub"])]:
            try:
                for t, v in weather.fetch_smoke(ap_cfg["lat"], ap_cfg["lon"], days[0], days[-1]).items():
                    if t in wx[code]:
                        wx[code][t]["pm2_5"] = v
            except Exception as e:
                print(f"[warn] smoke forecast failed for {code}: {e}")

    # official airport reports + forecasts (Trail has none, so it borrows Castlegar's)
    stations = sorted({c.get("wx_station") for c in list(cfg["airports"].values()) + [cfg["hub"]]} - {None})
    mets, tafs = {}, {}
    if stations and not a.demo:
        try:
            mets = aviation.metars(stations, hours=24)
        except Exception as e:
            print(f"[warn] airport reports failed: {e}; trying the backup archive")
            try:
                mets = aviation.metars_backup(stations)
                names = {c.get("wx_station"): c["name"] for c in list(cfg["airports"].values()) + [cfg["hub"]]}
                for st, reports in mets.items():
                    for m in reports:
                        m["name"] = names.get(st, st)
                notes.append(f"Airport reports came from the backup archive ({e}).")
            except Exception as e2:
                print(f"[warn] backup airport reports failed: {e2}")
                notes.append(f"Official airport reports unavailable this run ({e}).")
        try:
            tafs = aviation.tafs(stations)
        except Exception as e:
            print(f"[warn] airport forecasts failed: {e}")
            notes.append(f"Pilots' forecast (TAF) unavailable this run ({e}).")
    to_utc = clock.to_utc
    hub_st = cfg["hub"].get("wx_station")

    def score(f, code, ap_cfg, d, arriving, source):
        """Risk for one flight. `here` = time at the Kootenay airport, `there` = time at Vancouver."""
        block = timedelta(minutes=f.get("block_min", 75))
        here = f["dep"]
        there = here - block if arriving else here + block
        st = ap_cfg.get("wx_station")
        o_av = aviation.conditions(mets.get(st), tafs.get(st), to_utc(here)) if st else None
        h_av = aviation.conditions(mets.get(hub_st), tafs.get(hub_st), to_utc(there)) if hub_st else None
        feats = F.extract(weather.window(wx[code], here, 1, 1), weather.window(wx["HUB"], there, 1, 1), o_av, h_av)
        p = F.probability(feats, weights)
        why = F.reasons(feats, weights)
        forecast_p, forecast_why = p, why
        if history.is_cancelled(f["status"]):
            p, why = 1.0, ["Airline already shows this flight as cancelled"]
        elif "divert" in str(f["status"]).lower():
            p, why = 1.0, ["Airline shows this flight as diverted" +
                           (f" to {f['diverted_to']}" if f.get("diverted_to") else "")]
        hub = cfg["hub"]["code"]
        return {
            "airport": code, "airport_name": ap_cfg["name"], "airline": f["airline"],
            "flight": f["flight"], "date": d.isoformat(), "direction": "arr" if arriving else "dep",
            "from": hub if arriving else code, "to": code if arriving else hub,
            "dep_local": here.strftime("%H:%M"),  # time at the Kootenay airport (arrival time for arrivals)
            "expected_local": f["expected"].strftime("%H:%M") if f.get("expected") and f["expected"] != here else None,
            "status": f["status"], "diverted_to": f.get("diverted_to"),
            "risk": round(p, 3), "own_risk": round(p, 3),
            "level": F.level(p, cfg["watch_threshold"], cfg["alert_threshold"]),
            "reasons": why, "schedule_source": source,
            "airport_wx": (o_av["source"] + (f" ({st})" if st != code and st else "")) if o_av else None,
            "ceiling_ft": o_av["ceiling_ft"] if o_av else None,
            "forecast_risk": round(forecast_p, 3), "forecast_reasons": forecast_why,
            "flags": [k for k, v in feats.items() if v]}

    def link_aircraft(rs):
        """The plane that flies in from Vancouver is the one that flies back out.
        A departure is at least as risky as the arrival it depends on."""
        for dep in [r for r in rs if r["direction"] == "dep"]:
            t = datetime.fromisoformat(f"{dep['date']}T{dep['dep_local']}")
            ins = [r for r in rs if r["direction"] == "arr" and r["airport"] == dep["airport"]
                   and r["date"] == dep["date"] and r["airline"] == dep["airline"]
                   and timedelta(0) <= t - datetime.fromisoformat(f"{r['date']}T{r['dep_local']}") <= timedelta(hours=2)]
            if not ins:
                continue
            inb = max(ins, key=lambda r: r["dep_local"])
            dep["inbound"] = inb["flight"]
            dep["forecast_risk"] = max(dep["forecast_risk"], inb["forecast_risk"])
            if inb["risk"] > dep["risk"]:
                dep["risk"] = inb["risk"]
                dep["level"] = F.level(dep["risk"], cfg["watch_threshold"], cfg["alert_threshold"])
                lead = ("Incoming plane " + inb["flight"] + " is already cancelled"
                        if history.is_cancelled(inb["status"]) else
                        f"Incoming plane {inb['flight']} is at {round(inb['risk']*100)}% risk")
                extra = [x for x in inb["reasons"] if x not in dep["reasons"]][:2]
                dep["reasons"] = ([lead] if inb["risk"] >= cfg["watch_threshold"] else []) + extra + dep["reasons"]

    results = []
    for code, ap_cfg in cfg["airports"].items():
        for i, d in enumerate(days):
            for arriving in (False, True):
                fl, source = flights.schedule(code, d, cfg, arriving, live)
                for f in fl:
                    results.append(score(f, code, ap_cfg, d, arriving, source))
    link_aircraft(results)
    if cfg.get("record_history") and live.get("date") == today.isoformat():
        history.record_outcomes(results, now)
    if live.get("date") == today.isoformat() and not a.demo:
        try:
            events.record([r for r in results if r["date"] == today.isoformat()], cfg, now, mets, tafs, to_utc)
        except Exception as e:
            print(f"[warn] history log failed: {e}")
    events.publish()
    if not a.demo:
        try:
            tracking.record(results, now, mets,
                            {c: a_.get("wx_station") for c, a_ in cfg["airports"].items()}, to_utc)
        except Exception as e:
            print(f"[warn] forecast tracking failed: {e}")
    if yesterday_status:
        try:
            rows = yesterday_rows(yesterday_status, cfg, today - timedelta(days=1))
            tracking.apply_outcomes(rows)
            events.record(rows, cfg, now, mets, tafs, to_utc)
            events.publish()
            print(f"yesterday's final status: {[(r['flight'], r['status']) for r in rows]}")
        except Exception as e:
            print(f"[warn] yesterday's outcomes failed: {e}")
    tracking.publish(today, now=now)
    cams = []
    if cfg.get("webcams") and not a.demo:
        try:
            cams = webcam.frames(cfg["webcams"])
        except Exception as e:
            print(f"[warn] webcams failed: {e}")

    os.makedirs("docs", exist_ok=True)
    payload = {"generated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
               "today": today.isoformat(), "demo": a.demo,
               "model": ("learned" if model else "starting-weights"),
               "model_info": {k: model[k] for k in ("flights", "cancellations", "trained")} if model else None,
               "settings": {k: cfg.get(k) for k in ("alert_times", "days_ahead", "alert_threshold")},
               "airport_wx": {s: aviation.summary(mets.get(s), tafs.get(s)) for s in stations
                              if mets.get(s) or tafs.get(s)},
               "flight_status_checked": live.get("checked") if live.get("date") == today.isoformat() else None,
               "utc_offset": clock.OFFSET, "day_wx": day_forecast(wx, days, cfg),
               "notes": notes, "webcams": cams, "airport_cams": cfg.get("airport_cams", []),
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
    for r in (results if may_email else []):
        dep = datetime.fromisoformat(f"{r['date']}T{r['dep_local']}")
        if not (now <= dep <= horizon) or r["risk"] < cfg["alert_threshold"]:
            continue
        key = f"{r['date']}|{r['flight']}"
        if state["alerts"].get(key) != r["level"]:
            new.append(r)
            state["alerts"][key] = r["level"]
    if new:
        lines = [f"{r['airline']} {r['flight']} {r['from']}→{r['to']} "
                 f"{'arriving' if r['direction'] == 'arr' else 'leaving'} {r['airport_name']} at {r['dep_local']} on "
                 f"{_day_name(r['date'], today)}: {round(r['risk']*100)}% cancellation risk ({r['level']}). "
                 f"{'; '.join(r['reasons']) or 'Elevated overall conditions'}." for r in new]
        body = ("Flight risk heads-up:\n\n" + "\n\n".join(lines) +
                "\n\nThis is an estimate from weather forecasts and past cancellations, not an airline notice. "
                "Check with the airline before you travel.")
        subject = f"Flight risk: {', '.join(r['flight'] for r in new)}"
        if a.no_email:
            print(subject, "\n", body)
        else:
            err = alert.send(subject, body, recipients())
            if err:
                print(f"[warn] email not sent: {err}")
                for r in new:  # try again next email check
                    state["alerts"].pop(f"{r['date']}|{r['flight']}", None)
                payload["notes"].append(f"Email alert could not be sent: {err}. Check the SMTP_USER and "
                                        "SMTP_PASS secrets in the repo settings.")
                with open(OUT, "w") as f:
                    json.dump(payload, f, indent=1)

    if slot:
        state["runs"].append(slot)
    cutoff = (today - timedelta(days=2)).isoformat()
    state = {"alerts": {k: v for k, v in state["alerts"].items() if k.split("|")[0] >= today.isoformat()},
             "runs": [s for s in state["runs"] if s >= cutoff],
             "status_runs": [s for s in state["status_runs"] if s >= cutoff]}
    os.makedirs("data", exist_ok=True)
    with open(STATE, "w") as f:
        json.dump(state, f, indent=1)
    print(f"{now:%Y-%m-%d %H:%M} local{' (' + slot + ' email check)' if slot else ''}: "
          f"scored {len(results)} flights over {n_days} days, "
          + (f"{len(new)} new alert(s)" if may_email else "no email check due"))


def recipients():
    raw = os.environ.get("ALERT_EMAILS") or os.environ.get("SMTP_USER") or ""
    return [e.strip() for e in raw.replace("\n", ",").split(",") if e.strip()]


def yesterday_rows(status, cfg, day):
    """Turn the airline's answers about yesterday into result-like rows for the history logs."""
    hub = cfg["hub"]["code"]
    preds = tracking._load(f"{tracking.DIR}/{day:%Y-%m}.json")
    rows = []
    for code, dirs in status.items():
        name = cfg["airports"][code]["name"]
        for direction, lst in dirs.items():
            for f in lst:
                p = preds.get(f"{day.isoformat()}|{code}|{f['flight']}", {}).get("p") or [[None, None]]
                rows.append({"schedule_source": "live", "status": f["status"], "dep_local": f["time"],
                             "expected_local": f.get("expected"), "date": day.isoformat(), "flight": f["flight"],
                             "airport": code, "airport_name": name, "airline": f["airline"],
                             "direction": direction, "from": hub if direction == "arr" else code,
                             "to": code if direction == "arr" else hub, "diverted_to": f.get("diverted_to"),
                             "forecast_risk": (p[-1][1] / 100) if p[-1][1] is not None else None,
                             "forecast_reasons": []})
    return rows


def day_forecast(wx, days, cfg, hours=range(7, 20)):
    """Hourly forecast per day for the dashboard's day view: {date: {place: [row, ...]}}."""
    places = {code: a["name"] for code, a in cfg["airports"].items()}
    places["HUB"] = cfg["hub"]["name"]
    rnd = lambda v, n=0: None if v is None else round(v, n)
    out = {}
    for d in days:
        out[d.isoformat()] = day = {}
        for code in places:
            rows = []
            for h in hours:
                r = (wx.get(code) or {}).get(datetime(d.year, d.month, d.day, h))
                if not r:
                    continue
                lv = [r.get(k) for k in ("cloud_cover_925hPa", "cloud_cover_900hPa") if r.get(k) is not None]
                rows.append({"h": f"{h:02d}:00", "low": rnd(r.get("cloud_cover_low")),
                             "valley": rnd(max(lv)) if lv else None, "ridge": rnd(r.get("cloud_cover_850hPa")),
                             "vis": rnd((r.get("visibility") or 0) / 1000, 1) if r.get("visibility") is not None else None,
                             "rain": rnd(r.get("precipitation"), 1), "snow": rnd(r.get("snowfall"), 1),
                             "gust": rnd(r.get("wind_gusts_10m")), "temp": rnd(r.get("temperature_2m")),
                             "smoke": rnd(r.get("pm2_5")),
                             "code": r.get("weather_code")})
            if rows:
                day[code] = {"name": places[code], "rows": rows}
    return out


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
