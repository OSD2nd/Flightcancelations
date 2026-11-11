"""Rank BC airports by how often winter daytime weather is bad at flight times.

Uses hourly airport reports (METAR) from the Iowa Environmental Mesonet archive.
Winters = Nov-Mar, Nov 2023 - Nov 2026. Daytime window = 09:00-15:00 local,
when most regional flights operate. Local = UTC-8 (BC's winter time before the
2026 change; an hour either way doesn't change these counts much).
"""
import csv, glob, os, sys
from collections import defaultdict
from datetime import datetime, timedelta

NAMES = {"CYCG": "Castlegar", "CYXC": "Cranbrook", "CYYF": "Penticton", "CYLW": "Kelowna", "CYKA": "Kamloops",
         "CYXT": "Terrace", "CYPR": "Prince Rupert", "CYYD": "Smithers", "CYWL": "Williams Lake", "CYQZ": "Quesnel",
         "CYXJ": "Fort St. John", "CYDQ": "Dawson Creek", "CYZP": "Sandspit", "CZMT": "Masset",
         "CYBL": "Campbell River", "CYQQ": "Comox", "CYPW": "Powell River", "CYZT": "Port Hardy",
         "CYCD": "Nanaimo", "CYXS": "Prince George", "CYYE": "Fort Nelson", "CYAZ": "Tofino", "CYBD": "Bella Coola",
         "CBBC": "Bella Bella", "CYVR": "Vancouver", "CYYJ": "Victoria", "CYGE": "Golden"}
WET = ("RA", "DZ", "SN", "BR", "FG", "SG", "PL")


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main(D):
    rows = []
    for path in sorted(glob.glob(os.path.join(D, "*.csv"))):
        st = os.path.basename(path)[:-4]
        days = defaultdict(lambda: {"n": 0, "ifr": False, "lifr": False, "wetlow": False, "low": False})
        with open(path) as f:
            for r in csv.DictReader(f):
                try:
                    t = datetime.fromisoformat(r["valid"]) - timedelta(hours=8)
                except Exception:
                    continue
                if t.month not in (11, 12, 1, 2, 3) or not (9 <= t.hour < 15):
                    continue
                ceil = [num(r[f"skyl{i}"]) for i in range(1, 5) if r.get(f"skyc{i}") in ("BKN", "OVC", "VV")]
                ceil = min([c for c in ceil if c is not None], default=None)
                vis = num(r.get("vsby"))
                wx = r.get("wxcodes") or ""
                d = days[t.date()]
                d["n"] += 1
                if (ceil is not None and ceil < 1000) or (vis is not None and vis < 3):
                    d["ifr"] = True
                if (ceil is not None and ceil < 500) or (vis is not None and vis < 1):
                    d["lifr"] = True
                if ceil is not None and ceil < 4000 and any(w in wx for w in WET):
                    d["wetlow"] = True
                if ceil is not None and ceil < 2000:
                    d["low"] = True
        good = [d for d in days.values() if d["n"] >= 4]
        if len(good) < 60:
            rows.append((st, None, len(good)))
            continue
        pct = lambda k: round(100 * sum(d[k] for d in good) / len(good))
        rows.append((st, {"ifr": pct("ifr"), "lifr": pct("lifr"), "low": pct("low"), "wetlow": pct("wetlow")}, len(good)))

    ranked = sorted([r for r in rows if r[1]], key=lambda r: -(r[1]["ifr"] + r[1]["lifr"]))
    print(f"{'Airport':16} {'code':5} {'days':>5}  {'IFR day':>7} {'LIFR day':>8} {'cloud<2000':>10} {'wet+cloud<4000':>14}")
    for st, m, n in ranked:
        print(f"{NAMES.get(st, st):16} {st:5} {n:5}  {m['ifr']:6}% {m['lifr']:7}% {m['low']:9}% {m['wetlow']:13}%")
    for st, m, n in rows:
        if not m:
            print(f"{NAMES.get(st, st):16} {st:5} too few daytime reports ({n} days)")


if __name__ == "__main__":
    main(sys.argv[1])
