"""Guess cancellations from the OpenSky log, strictly, and check them against the weather.

OpenSky misses a lot of flights (receiver gaps), so "we didn't see it" alone is mostly noise.
A regular flight only counts as a likely cancellation when:
  1. it normally runs on that weekday (seen on 60%+ of those weekdays),
  2. the airport was clearly being watched around that time: OpenSky saw other flights
     there within 3 hours before AND within 3 hours after its usual time, and
  3. its other leg (the arrival before a departure, or the departure after an arrival,
     same airline, within 2 h) is missing too - a cancelled rotation loses both legs.

Then: was the airport weather at the usual time worse for these than for flights that flew?
If yes, the guesses carry a real signal and can be used for training.

Usage: python research/opensky_cancellations.py METAR_DIR [AIRPORT ...]
METAR_DIR holds STATION.csv files from the Iowa State (IEM) archive.
"""
import collections, csv, json, re, sys
from datetime import date, datetime, timedelta

LOG = "data/collect/flights.csv"
DONE = "data/collect/done.json"
OUT = "research/opensky_cancellations.csv"
UTC_OFFSET = -8          # winter backfill (Jan-Mar): BC was still on PST then


def minutes(hhmm):
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def load_metars(path):
    out = collections.defaultdict(list)
    with open(path) as f:
        for r in csv.DictReader(f):
            if not r.get("metar"):
                continue
            t = datetime.fromisoformat(r["valid"]) + timedelta(hours=UTC_OFFSET)   # to local
            out[t.date().isoformat()].append((t.hour * 60 + t.minute, r["metar"]))
    return out


def weather_at(mets, day, mins):
    near = [m for m in mets.get(day, []) if abs(m[0] - mins) <= 60]
    if not near:
        return None
    raw = min(near, key=lambda m: abs(m[0] - mins))[1]
    cig = [int(h) * 100 for _, h in re.findall(r"\b(BKN|OVC|VV)(\d{3})\b", raw)]
    v = re.search(r"\s(?:(\d+)\s)?(\d+)(?:/(\d+))?SM\b", raw)
    vis = 10.0
    if v:
        whole, a, b = v.groups()
        vis = (int(whole) if whole else 0) + (int(a) / int(b) if b else int(a))
    low = (min(cig) < 1000 if cig else False) or vis < 3 or bool(re.search(r"\b(FG|FZ\w*|\+SN)\b", raw))
    return {"low": low, "ceiling": min(cig) if cig else None, "vis": vis, "raw": raw}


def analyse(ap, mets, rows, done):
    days = sorted({k.split("|")[0] for k in done if k.endswith("|" + ap)})
    xs = [r for r in rows if r["airport"] == ap and r["local_time"]]
    by_day = collections.defaultdict(list)
    seen = collections.defaultdict(dict)        # (callsign, dir) -> {date: minutes}
    for r in xs:
        by_day[r["date"]].append(minutes(r["local_time"]))
        seen[(r["callsign"].strip(), r["direction"])][r["date"]] = minutes(r["local_time"])
    wd = lambda d: date.fromisoformat(d).weekday()

    # regular flights per weekday, with their usual time
    regular = []          # (callsign, dir, weekday, usual minutes, [days it should run])
    for (cs, dr), v in seen.items():
        lo, hi = min(v), max(v)
        for w in range(7):
            span = [d for d in days if lo <= d <= hi and wd(d) == w]
            got = [d for d in span if d in v]
            if len(span) >= 4 and len(got) >= 0.6 * len(span):
                usual = sorted(v[d] for d in got)[len(got) // 2]
                regular.append((cs, dr, w, usual, span))

    def watched(day, mins, cs):
        others = [m for m in by_day.get(day, [])]
        mine = seen.get((cs, "dep"), {}).get(day), seen.get((cs, "arr"), {}).get(day)
        others = [m for m in others if m not in mine]
        return any(mins - 180 <= m < mins for m in others) and any(mins < m <= mins + 180 for m in others)

    def other_leg_missing(cs, dr, day, mins):
        airline = re.match(r"[A-Z]+", cs).group(0) if re.match(r"[A-Z]+", cs) else cs
        want = "arr" if dr == "dep" else "dep"
        legs = [(c, u) for c, d2, w, u, span in regular
                if d2 == want and c.startswith(airline) and day in span
                and (0 < mins - u <= 120 if dr == "dep" else 0 < u - mins <= 120)]
        if not legs:
            return None                         # no paired leg known: can't use rule 3
        return all(day not in seen[(c, want)] for c, _ in legs)

    out = []
    for cs, dr, w, usual, span in regular:
        for day in span:
            flew = day in seen[(cs, dr)]
            if flew:
                label = "flew"
            elif not watched(day, usual, cs):
                label = "unknown (not watched)"
            else:
                pair = other_leg_missing(cs, dr, day, usual)
                label = "likely cancelled" if pair else "unknown (other leg seen)" if pair is False else "unknown (no pair)"
            wx = weather_at(mets, day, usual)
            out.append({"airport": ap, "date": day, "callsign": cs, "direction": dr,
                        "usual_time": f"{usual // 60:02d}:{usual % 60:02d}", "label": label,
                        "low_weather": None if wx is None else int(wx["low"]),
                        "ceiling_ft": wx and wx["ceiling"], "vis_sm": wx and wx["vis"], "metar": wx and wx["raw"]})
    return out


def main():
    metar_dir, aps = sys.argv[1], sys.argv[2:] or ["CYXT", "CYQQ"]
    rows = list(csv.DictReader(open(LOG)))
    done = json.load(open(DONE))
    allrows = []
    for ap in aps:
        res = analyse(ap, load_metars(f"{metar_dir}/{ap}.csv"), [r for r in rows if r["date"] < "2026-04-01"], done)
        allrows += res
        c = collections.Counter(r["label"] for r in res)
        print(f"\n{ap}: {len(res)} expected flights")
        for lab, n in c.most_common():
            wx = [r["low_weather"] for r in res if r["label"] == lab and r["low_weather"] is not None]
            share = f"{100 * sum(wx) / len(wx):.0f}% in low cloud/fog/poor visibility" if wx else "no weather"
            print(f"  {lab:26} {n:5}  ({100 * n / len(res):.1f}%)  {share}")
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(allrows[0]))
        w.writeheader()
        w.writerows(allrows)
    print(f"\nsaved {len(allrows)} rows to {OUT}")


if __name__ == "__main__":
    main()
