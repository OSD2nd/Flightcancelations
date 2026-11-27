"""Airport limits and forecast accuracy check.

1. Each airport's weather limits: the actual airport report at each flight's time, for flights
   that went vs. flights that (probably) didn't. Lowest cloud base / visibility each airport still
   operates in, and the cancellation rate by cloud base and visibility.
2. Our risk scoring run on the ACTUAL weather at flight time (airport report only): does it separate
   the flights that went from the ones that didn't, airport by airport?
3. Our real forecasts for Trail and Castlegar vs. what happened: accuracy at 3 days / night before /
   morning of, and how much the forecast jumped around.

Sources
  Comox, Terrace: OpenSky log, strict "likely cancelled" rule (research/opensky_cancellations.py)
  Kamloops:       the airport's own departures board (real Cancelled / Late statuses)
  Trail, Castlegar: our forecast record + outcomes (docs/predictions.json from the live site)
  Weather:        Iowa State (IEM) archive of airport reports, research/metar/*.csv

Usage: python research/airport_limits.py [path to published predictions.json]
"""
import collections, csv, glob, json, os, re, sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ksalerts import aviation as A, features as F          # noqa: E402
import importlib.util                                       # noqa: E402

OFFSET = -7          # the collector's local times use UTC-7 throughout
METAR_DIR = "research/metar"


# ---------- weather ----------
def load_metars(station):
    """{utc epoch: parsed report} from every research/metar/<station>*.csv."""
    out = {}
    for path in glob.glob(f"{METAR_DIR}/{station}*.csv"):
        with open(path) as f:
            for r in csv.DictReader(f):
                if r.get("metar"):
                    t = datetime.fromisoformat(r["valid"]).replace(tzinfo=timezone.utc).timestamp()
                    out[t] = r["metar"]
    return dict(sorted(out.items()))


def wx_at(mets, t_utc, window=60):
    near = [t for t in mets if abs(t - t_utc) <= window * 60]
    if not near:
        return None
    raw = mets[min(near, key=lambda t: abs(t - t_utc))]
    ob = A._merge([A._parse_metar(raw)], "airport report")
    ob["raw"] = raw
    return ob


def local_to_utc(day, hhmm, offset=OFFSET):
    h, m = map(int, hhmm.split(":"))
    return (datetime(*map(int, day.split("-")), h, m, tzinfo=timezone(timedelta(hours=offset)))).timestamp()


# ---------- labelled flights ----------
def opensky_flights(ap):
    """[(date, usual local time, cancelled?)] from the strict OpenSky rule (flew / likely cancelled only)."""
    spec = importlib.util.spec_from_file_location("osc", "research/opensky_cancellations.py")
    osc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(osc)
    rows = list(csv.DictReader(open(osc.LOG)))
    done = json.load(open(osc.DONE))
    res = osc.analyse(ap, {}, rows, done)                  # weather looked up here instead
    return [(r["date"], r["usual_time"], r["label"] == "likely cancelled")
            for r in res if r["label"] in ("flew", "likely cancelled")]


def kamloops_flights():
    out = []
    for path in glob.glob("data/collect/boards/CYKA-*.json"):
        b = json.load(open(path))
        for r in (b.values() if isinstance(b, dict) else b):
            if r["date"] >= date.today().isoformat():
                continue
            s = r["status"].lower()
            if "cancel" in s:
                out.append((r["date"], r["sched"], True))
            elif re.search(r"departed|arrived|on time|late|delayed|early", s):
                out.append((r["date"], r["sched"], False))
    return out


def our_flights(preds):
    """Trail + Castlegar: (airport, date, time, cancelled?, forecast record)."""
    out = []
    for k, v in preds.items():
        o = v.get("outcome") or ""
        if not o or "not weather" in o.lower() or "uncertain" in o.lower():
            continue
        d, ap, fl = k.split("|")
        out.append((ap, d, v["sched"], bool(re.match(r"cancel|divert", o, re.I)), v))
    return out


# ---------- stats ----------
def auc(pairs):
    """Chance a random cancelled flight scored higher than a random flight that went (0.5 = no skill)."""
    pos = [p for p, y in pairs if y]
    neg = [p for p, y in pairs if not y]
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def brier(pairs):
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs) if pairs else None


CEIL_BINS = [("under 1,000 ft", 0, 1000), ("1,000-2,000", 1000, 2000), ("2,000-3,000", 2000, 3000),
             ("3,000-5,000", 3000, 5000), ("5,000+ or none", 5000, 10 ** 9)]
VIS_BINS = [("under 1 mi", 0, 1), ("1-3 mi", 1, 3), ("3-5 mi", 3, 5), ("5+ mi", 5, 999)]


def limits(name, labelled):
    """labelled = [(ob, cancelled?)]"""
    flew = [ob for ob, c in labelled if not c]
    canc = [ob for ob, c in labelled if c]
    def ceil(ob): return ob["ceiling_ft"] if ob["ceiling_ft"] is not None else 10 ** 9
    def vis(ob): return ob["vis_sm"] if ob["vis_sm"] is not None else 99
    def pct(xs, q):
        xs = sorted(xs)
        return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None
    print(f"\n=== {name}: {len(labelled)} flights with weather, {len(canc)} cancelled ({100 * len(canc) / max(1, len(labelled)):.1f}%)")
    if flew:
        lc = [ceil(o) for o in flew if o["ceiling_ft"] is not None]
        lv = [vis(o) for o in flew if o["vis_sm"] is not None]
        print(f"  flew with a cloud base as low as {min(lc) if lc else '-'} ft (5% of cloud-deck flights under {pct(lc, .05)} ft); "
              f"visibility as low as {min(lv) if lv else '-'} mi (5% under {pct(lv, .05)} mi)")
    for label, bins, fn in (("cloud base", CEIL_BINS, ceil), ("visibility", VIS_BINS, vis)):
        cells = []
        for b, lo, hi in bins:
            n = [c for ob, c in labelled if lo <= fn(ob) < hi]
            if n:
                cells.append(f"{b}: {sum(n)}/{len(n)} cancelled ({100 * sum(n) / len(n):.0f}%)")
        print(f"  by {label}: " + " | ".join(cells))
    wet = [c for ob, c in labelled if any(any(p in w for p in A.WET + A.FOGGY) for w in ob["wx"])]
    dry = [c for ob, c in labelled if not any(any(p in w for p in A.WET + A.FOGGY) for w in ob["wx"])]
    if wet and dry:
        print(f"  with rain/snow/mist reported: {100 * sum(wet) / len(wet):.0f}% cancelled ({len(wet)} flights); "
              f"without: {100 * sum(dry) / len(dry):.0f}% ({len(dry)})")


def nowcast(name, labelled):
    """Our scoring on the actual report at flight time only (no forecast models, no EC wording)."""
    pairs = []
    for ob, c in labelled:
        f = F.extract(None, None, ob, None, None)
        pairs.append((F.probability(f), c))
    canc = [p for p, c in pairs if c]
    flew = [p for p, c in pairs if not c]
    flag = lambda xs: sum(p >= 0.6 for p in xs)
    a = auc(pairs)
    print(f"  our scoring on the actual weather: cancelled avg {100 * sum(canc) / max(1, len(canc)):.0f}%, "
          f"went avg {100 * sum(flew) / max(1, len(flew)):.0f}% | separation (AUC) {a if a is None else round(a, 2)} | "
          f"60%+ catches {flag(canc)}/{len(canc)} cancellations, false alarms {flag(flew)}/{len(flew)}")
    return pairs


def label_with_weather(station, flights, offset=OFFSET):
    mets = load_metars(station)
    out = []
    for d, hhmm, c in flights:
        ob = wx_at(mets, local_to_utc(d, hhmm, offset))
        if ob:
            out.append((ob, c))
    return out


# ---------- our forecast record ----------
def last_before(v, d, hours):
    lim = datetime.fromisoformat(f"{d}T{v['sched']}") - timedelta(hours=hours)
    pts = [p for p in v.get("p", []) if datetime.fromisoformat(p[0]) <= lim]
    return pts[-1][1] / 100 if pts else None


def forecast_record(flights):
    print("\n=== Our real forecasts, Trail + Castlegar (settled flights since tracking began) ===")
    for label, h in (("3 days out", 72), ("night before", 12), ("morning of", 2)):
        pairs = [(last_before(v, d, h), c) for ap, d, t, c, v in flights]
        pairs = [(p, c) for p, c in pairs if p is not None]
        if not pairs:
            continue
        canc = [p for p, c in pairs if c]
        flew = [p for p, c in pairs if not c]
        print(f"  {label:12}: {len(pairs):3} flights | Brier {brier(pairs):.3f} | AUC {auc(pairs) if auc(pairs) is None else round(auc(pairs), 2)} | "
              f"60%+ caught {sum(p >= .6 for p in canc)}/{len(canc)}, false alarms {sum(p >= .6 for p in flew)}/{len(flew)}")
    # consistency: jumps between saved points in the last 5 days before each flight
    for title, lo, hi in (("before smoothing (to Nov 19)", "0000", "2026-11-20"),
                          ("smoothing + soft signals (Nov 20-24)", "2026-11-20", "2026-11-25"),
                          ("+ clear-report rule (from Nov 25)", "2026-11-25", "9999")):
        jumps, big, n = [], 0, 0
        for ap, d, t, c, v in flights:
            pts = [p for p in v.get("p", []) if lo <= p[0][:10] < hi]
            if len(pts) < 2:
                continue
            n += 1
            js = [abs(b[1] - a[1]) for a, b in zip(pts, pts[1:])]
            jumps += js
            big += sum(j >= 30 for j in js)
        if jumps:
            print(f"  jumpiness {title}: {n} flights, average move between saved points {sum(jumps) / len(jumps):.1f} pts, "
                  f"moves of 30+ pts: {big} ({100 * big / len(jumps):.1f}% of moves)")


def main():
    preds = json.load(open(sys.argv[1])) if len(sys.argv) > 1 else {}
    print("Weather limits and scoring check -", date.today().isoformat())
    results = {}
    for ap, name in (("CYQQ", "Comox (OpenSky, strict rule)"), ("CYXT", "Terrace (OpenSky, strict rule)")):
        lab = label_with_weather(ap, opensky_flights(ap))
        limits(name, lab)
        results[ap] = nowcast(name, lab)
    lab = label_with_weather("CYKA", kamloops_flights())
    limits("Kamloops (airport board)", lab)
    results["CYKA"] = nowcast("Kamloops", lab)
    if preds:
        ours = our_flights(preds)
        mets = load_metars("CYCG")
        for ap, name in (("YZZ", "Trail (our record; weather = Castlegar report)"), ("YCG", "Castlegar (our record)")):
            lab = []
            for a, d, t, c, v in ours:
                if a == ap:
                    ob = wx_at(mets, local_to_utc(d, t))
                    if ob:
                        lab.append((ob, c))
            limits(name, lab)
            results[ap] = nowcast(name, lab)
        forecast_record(ours)


if __name__ == "__main__":
    main()
