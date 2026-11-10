"""Airport flight-board logger.

Some airports publish their own arrivals/departures board as a data feed. We
read it about once an hour (their robots.txt allows ordinary readers; we say
who we are) and keep each flight's latest status, so cancellations and delays
build up into a history.

Boards are listed in research/watchlist.json under "boards". Results go to
data/collect/boards/<ICAO>-<YYYY-MM>.json, one record per flight per day:
  {sched, direction, airline, flight, via, status, shown_time, first_seen, last_seen,
   changes: [[local time, status, shown_time], ...]}

  python -m ksalerts.boards          # read any board not read in the last 55 min
  python -m ksalerts.boards --force  # read now
"""
import argparse, json, os, urllib.request
from datetime import datetime, timedelta, timezone

WATCH = "research/watchlist.json"
DIR = "data/collect/boards"
STATE = "data/collect/boards_state.json"
UA = "kootenay-flight-risk/1.0 (personal flight-weather research; reads once an hour)"
MIN_GAP_MIN = 55
DONE = ("arrived", "departed", "landed", "cancel", "divert")


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def read_ch_flight_data(url):
    """WordPress 'ch-flight-data' plugin feed (Kamloops): {arrivals:[...], departures:[...]}."""
    d = _get(url)
    out = []
    for key, direction in (("arrivals", "arr"), ("departures", "dep")):
        for f in d.get(key) or []:
            out.append({"direction": direction, "airline": (f.get("AirlineCode") or "").strip(),
                        "flight": str(f.get("FlightNumber") or "").strip(),
                        "via": (f.get("ViaAirportCity") or "").strip(),
                        "sched": (f.get("ScheduleTime") or "").strip(),
                        "shown_time": (f.get("DisplayTime") or "").strip(),
                        "status": (f.get("Status") or "").strip()})
    return out


READERS = {"ch-flight-data": read_ch_flight_data}


def flight_date(sched, status, now):
    """Date for the first flight on the board, given the time we're reading it."""
    h, m = map(int, sched.split(":"))
    t = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if t < now - timedelta(hours=6):
        t += timedelta(days=1)              # early-morning flight listed late in the evening
    elif t > now + timedelta(hours=18) and status.lower().startswith(DONE):
        t -= timedelta(days=1)              # last night's flight still shown after midnight
    return t.date()


def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def log_board(board, now):
    rows = READERS[board["kind"]](board["url"])
    stamp = now.strftime("%Y-%m-%dT%H:%M")
    files, changed = {}, 0
    # The board lists several days in time order with no dates: date the first
    # flight from the current time, then move to the next day whenever the time drops.
    day_of, prev = {}, {}
    for r in rows:
        if not r["sched"] or not r["flight"]:
            continue
        d = r["direction"]
        if d not in day_of:
            day_of[d] = flight_date(r["sched"], r["status"], now)
        elif r["sched"] < prev[d]:
            day_of[d] += timedelta(days=1)
        prev[d] = r["sched"]
        day = day_of[d]
        path = f"{DIR}/{board['icao']}-{day:%Y-%m}.json"
        recs = files.setdefault(path, _load(path))
        key = f"{day.isoformat()}|{r['direction']}|{r['airline']}|{r['flight']}"
        rec = recs.get(key)
        if rec is None:
            rec = recs[key] = {"date": day.isoformat(), **r, "first_seen": stamp, "last_seen": stamp,
                               "changes": [[stamp, r["status"], r["shown_time"]]]}
            changed += 1
            continue
        rec["last_seen"] = stamp
        if (rec["status"], rec["shown_time"]) != (r["status"], r["shown_time"]):
            rec.update(status=r["status"], shown_time=r["shown_time"])
            rec["changes"].append([stamp, r["status"], r["shown_time"]])
            changed += 1
    os.makedirs(DIR, exist_ok=True)
    for path, recs in files.items():
        with open(path, "w") as f:
            json.dump(dict(sorted(recs.items())), f, indent=1)
    return len(rows), changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    try:
        with open(WATCH) as f:
            watch = json.load(f)
    except Exception:
        return
    offset = watch.get("utc_offset", -7)
    now = (datetime.now(timezone.utc) + timedelta(hours=offset)).replace(tzinfo=None)
    state = _load(STATE)
    for b in watch.get("boards", []):
        last = state.get(b["icao"])
        if not a.force and last and now - datetime.fromisoformat(last) < timedelta(minutes=MIN_GAP_MIN):
            continue
        try:
            n, changed = log_board(b, now)
            print(f"[board] {b['name']}: {n} flights listed, {changed} new or changed")
        except Exception as e:
            print(f"[board] {b['name']}: could not read ({e})")
        state[b["icao"]] = now.isoformat(timespec="minutes")
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w") as f:
        json.dump(state, f, indent=1)


if __name__ == "__main__":
    main()
