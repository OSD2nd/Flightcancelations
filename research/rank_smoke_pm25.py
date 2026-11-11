"""Summer wildfire smoke at each airport from modelled fine particles (PM2.5).

Airport reports only note smoke where a person does the observing, so this
uses the same source for every airport: Open-Meteo's air-quality history
(CAMS model), hourly PM2.5 in µg/m³, Jun-Sep, 9 am - 6 pm local (UTC-7).

  Smoky day:       PM2.5 reaches 35 (unhealthy for sensitive people; haze)
  Heavy smoke day: PM2.5 reaches 100 (thick smoke; visibility often drops)

The model is coarse (~40 km) and tends to underestimate smoke in narrow
valleys, so treat these as a fair comparison between airports rather than
exact counts.

  python3 rank_smoke_pm25.py [cache folder]   # downloads, prints the table
"""
import json, os, sys, time, urllib.request
from collections import defaultdict
from datetime import datetime, timedelta

AIRPORTS = {  # name, lat, lon
    "CYCG": ("Castlegar", 49.296, -117.632), "CAD4": ("Trail", 49.056, -117.609),
    "CYXC": ("Cranbrook", 49.610, -115.782), "CYYF": ("Penticton", 49.463, -119.602),
    "CYLW": ("Kelowna", 49.956, -119.378), "CYKA": ("Kamloops", 50.702, -120.444),
    "CYXT": ("Terrace", 54.469, -128.576), "CYPR": ("Prince Rupert", 54.286, -130.445),
    "CYYD": ("Smithers", 54.825, -127.183), "CYWL": ("Williams Lake", 52.183, -122.054),
    "CYQZ": ("Quesnel", 53.026, -122.510), "CYXJ": ("Fort St. John", 56.238, -120.740),
    "CYDQ": ("Dawson Creek", 55.742, -120.183), "CYZP": ("Sandspit", 53.254, -131.814),
    "CZMT": ("Masset", 54.028, -132.125), "CYBL": ("Campbell River", 49.951, -125.271),
    "CYQQ": ("Comox", 49.711, -124.887), "CYPW": ("Powell River", 49.834, -124.500),
    "CYZT": ("Port Hardy", 50.681, -127.367), "CYCD": ("Nanaimo", 49.052, -123.870),
    "CYXS": ("Prince George", 53.889, -122.679), "CYYE": ("Fort Nelson", 58.836, -122.597),
    "CYAZ": ("Tofino", 49.082, -125.773), "CYBD": ("Bella Coola", 52.388, -126.596),
    "CBBC": ("Bella Bella", 52.185, -128.157), "CYVR": ("Vancouver", 49.195, -123.184),
    "CYYJ": ("Victoria", 48.647, -123.426), "CYGE": ("Golden", 51.299, -116.982),
}
SUMMERS = [2024, 2025, 2026]
URL = ("https://air-quality-api.open-meteo.com/v1/air-quality?latitude={lat}&longitude={lon}"
       "&hourly=pm2_5&timezone=GMT&start_date={a}&end_date={b}")


CACHE = sys.argv[1] if len(sys.argv) > 1 else None  # optional folder to keep downloads (lets a rerun resume)


def fetch(lat, lon, year):
    path = f"{CACHE}/{lat}_{lon}_{year}.json" if CACHE else None
    if path and os.path.exists(path):
        with open(path) as f:
            h = json.load(f)
    else:
        for attempt in range(5):
            try:
                with urllib.request.urlopen(URL.format(lat=lat, lon=lon, a=f"{year}-06-01", b=f"{year}-09-30"),
                                            timeout=90) as r:
                    h = json.load(r)["hourly"]
                break
            except Exception as e:
                if attempt == 4:
                    raise
                print(f"  retry ({e})", file=sys.stderr)
                time.sleep(10 * (attempt + 1))
        if path:
            os.makedirs(CACHE, exist_ok=True)
            with open(path, "w") as f:
                json.dump(h, f)
    return zip(h["time"], h["pm2_5"])


rows = []
for code, (name, lat, lon) in AIRPORTS.items():
    smoky, heavy, worst = defaultdict(int), defaultdict(int), 0
    for y in SUMMERS:
        daymax = defaultdict(float)
        for t, v in fetch(lat, lon, y):
            if v is None:
                continue
            lt = datetime.fromisoformat(t) - timedelta(hours=7)
            if 9 <= lt.hour < 18:
                daymax[lt.date()] = max(daymax[lt.date()], v)
        smoky[y] = sum(v >= 35 for v in daymax.values())
        heavy[y] = sum(v >= 100 for v in daymax.values())
        worst = max([worst] + list(daymax.values()))
        time.sleep(2)
    rows.append((code, name, smoky, heavy, worst))
    print(f"  fetched {name}", file=sys.stderr)

rows.sort(key=lambda r: -sum(r[2].values()))
print(f"{'Airport':16} {'code':5} {'smoky days':>10} {'heavy':>6} {'worst':>6}   smoky days by summer")
for code, name, s, h, w in rows:
    print(f"{name:16} {code:5} {sum(s.values()):10} {sum(h.values()):6} {round(w):6}   " +
          "  ".join(f"{y}:{s[y]}" for y in SUMMERS))
print("\nsmoky = PM2.5 reached 35 µg/m³ during 9am-6pm; heavy = reached 100; worst = highest hourly value. Jun-Sep "
      + ", ".join(map(str, SUMMERS)) + ".")
