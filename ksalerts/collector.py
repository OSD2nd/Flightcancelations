"""Daily flight log from OpenSky Network (free account).

Once a day, for each airport in research/watchlist.json, ask OpenSky which
aircraft arrived and departed the previous day and append them to
data/collect/flights.csv. Over weeks this shows each airport's normal daily
flights, so a usual flight missing on a bad-weather day is almost certainly a
cancellation. Weather for any day can be added later from the free METAR archive.

Needs repo secrets OPENSKY_CLIENT_ID and OPENSKY_CLIENT_SECRET; without them
it just says so and stops.

Cost: a one-day window costs ~30 credits per call, so ~60 per airport per day.
A free account gets 4,000 credits a day per endpoint group.

  python -m ksalerts.collector            # yesterday
  python -m ksalerts.collector --days 3   # the last 3 days (backfill)
  python -m ksalerts.collector --airports CYCG,CAD4 --from 2025-11-01 --to 2026-03-31
                                          # a past period for chosen airports, newest first;
                                          # stops at the credit budget and resumes next run
"""
import argparse, csv, json, os, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import date, datetime, timedelta, timezone

TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
API = "https://opensky-network.org/api"
WATCH = "research/watchlist.json"
OUT = "data/collect/flights.csv"
DONE = "data/collect/done.json"
COLS = ["date", "airport", "direction", "callsign", "icao24", "other_airport",
        "first_seen_utc", "last_seen_utc", "local_time"]


class OpenSky:
    def __init__(self, cid, secret):
        self.cid, self.secret, self.token, self.expires = cid, secret, None, 0
        self.remaining = None

    def _token(self):
        if self.token and time.time() < self.expires - 30:
            return self.token
        body = urllib.parse.urlencode({"grant_type": "client_credentials",
                                       "client_id": self.cid, "client_secret": self.secret}).encode()
        req = urllib.request.Request(TOKEN_URL, data=body,
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=30) as r:
            j = json.load(r)
        self.token, self.expires = j["access_token"], time.time() + j.get("expires_in", 1800)
        return self.token

    def get(self, path, **q):
        for attempt in range(3):
            req = urllib.request.Request(f"{API}{path}?{urllib.parse.urlencode(q)}",
                                         headers={"Authorization": f"Bearer {self._token()}"})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    self.remaining = r.headers.get("X-Rate-Limit-Remaining", self.remaining)
                    body = r.read()
                return json.loads(body) if body.strip() else []
            except urllib.error.HTTPError as e:
                if e.code == 404:          # no flights in that window
                    return []
                if e.code == 401:          # token expired
                    self.token = None
                    continue
                if e.code == 429:
                    wait = int(e.headers.get("X-Rate-Limit-Retry-After-Seconds", "0") or 0)
                    raise RuntimeError(f"out of OpenSky credits for today (retry in {wait // 3600} h)")
                if e.code >= 500 and attempt < 2:
                    time.sleep(5 * (attempt + 1))
                    continue
                raise
        raise RuntimeError("OpenSky kept refusing the access token")


def load_done():
    try:
        with open(DONE) as f:
            return set(json.load(f))
    except Exception:
        return set()


def save(rows, done):
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    new = not os.path.exists(OUT)
    with open(OUT, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        if new:
            w.writeheader()
        w.writerows(rows)
    with open(DONE, "w") as f:
        json.dump(sorted(done), f, indent=0)


def collect_day(api, ap, day, offset):
    """Arrivals and departures for one local day at one airport."""
    start = datetime(day.year, day.month, day.day, tzinfo=timezone(timedelta(hours=offset)))
    b, e = int(start.timestamp()), int((start + timedelta(days=1)).timestamp()) - 1
    rows = []
    for direction, path, here_key, other_key, t_key in (
            ("arr", "/flights/arrival", "estArrivalAirport", "estDepartureAirport", "lastSeen"),
            ("dep", "/flights/departure", "estDepartureAirport", "estArrivalAirport", "firstSeen")):
        for f in api.get(path, airport=ap, begin=b, end=e) or []:
            t = f.get(t_key)
            local = (datetime.fromtimestamp(t, timezone.utc) + timedelta(hours=offset)).strftime("%H:%M") if t else ""
            rows.append({"date": day.isoformat(), "airport": ap, "direction": direction,
                         "callsign": (f.get("callsign") or "").strip(), "icao24": f.get("icao24", ""),
                         "other_airport": f.get(other_key) or "",
                         "first_seen_utc": f.get("firstSeen", ""), "last_seen_utc": f.get("lastSeen", ""),
                         "local_time": local})
    return rows


def main():
    ap_ = argparse.ArgumentParser()
    ap_.add_argument("--days", type=int, default=1, help="how many past days to collect")
    ap_.add_argument("--budget", type=int, default=3600, help="stop before using more credits than this")
    ap_.add_argument("--backfill", action="store_true",
                     help="after yesterday, spend leftover credits on the watchlist's 'backfill' period")
    ap_.add_argument("--airports", default="", help="comma-separated ICAO codes (default: whole watchlist)")
    ap_.add_argument("--from", dest="start", default="", help="first date YYYY-MM-DD (with --to)")
    ap_.add_argument("--to", dest="end", default="", help="last date YYYY-MM-DD")
    a = ap_.parse_args()

    cid, secret = os.environ.get("OPENSKY_CLIENT_ID"), os.environ.get("OPENSKY_CLIENT_SECRET")
    if not cid or not secret:
        print("OpenSky secrets not set (OPENSKY_CLIENT_ID / OPENSKY_CLIENT_SECRET) - nothing collected.")
        return
    with open(WATCH) as f:
        watch = json.load(f)
    offset = watch.get("utc_offset", -7)
    api = OpenSky(cid, secret)
    done = load_done()
    today = (datetime.now(timezone.utc) + timedelta(hours=offset)).date()
    airports = watch["airports"]
    if a.airports:
        want = [x.strip().upper() for x in a.airports.split(",") if x.strip()]
        known = {ap["icao"]: ap for ap in airports}
        airports = [known.get(c, {"icao": c, "name": c}) for c in want]
    if a.start and a.end:
        d0, d1 = date.fromisoformat(a.start), date.fromisoformat(a.end)
        span = [d1 - timedelta(days=i) for i in range((d1 - d0).days + 1)]
    else:
        span = [today - timedelta(days=back) for back in range(1, a.days + 1)]
    jobs = [(day, ap) for day in span for ap in airports]
    bf = watch.get("backfill")
    if a.backfill and bf and not (a.start or a.airports):
        known = {ap["icao"]: ap for ap in watch["airports"]}
        d0, d1 = date.fromisoformat(bf["from"]), date.fromisoformat(bf["to"])
        for i in range((d1 - d0).days + 1):
            for c in bf["airports"]:
                jobs.append((d1 - timedelta(days=i), known.get(c, {"icao": c, "name": c})))
        left = sum(1 for d, ap in jobs[len(span) * len(airports):] if f"{d.isoformat()}|{ap['icao']}" not in done)
        print(f"backfill {bf['from']}..{bf['to']}: {left} airport-days still to collect")
    rows, spent, calls = [], 0, 0
    try:
        for day, ap in jobs:
            key = f"{day.isoformat()}|{ap['icao']}"
            if key in done:
                continue
            if spent + 60 > a.budget:
                raise RuntimeError(f"stopping at the {a.budget}-credit budget (continues next run)")
            if api.remaining is not None and int(api.remaining) < 200:
                raise RuntimeError(f"only {api.remaining} OpenSky credits left today (continues next run)")
            got = collect_day(api, ap["icao"], day, ap.get("utc_offset", offset))
            rows += got
            done.add(key)
            spent += 60
            calls += 2
            print(f"{day} {ap['icao']:5} {ap['name']:16} {sum(r['direction']=='arr' for r in got):3} arr "
                  f"{sum(r['direction']=='dep' for r in got):3} dep")
    except Exception as e:
        print(f"[warn] {e}")
    save(rows, done)
    print(f"saved {len(rows)} flights from {calls} requests; credits left today: {api.remaining}")


if __name__ == "__main__":
    sys.exit(main())
