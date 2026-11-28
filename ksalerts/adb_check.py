"""One-off: check the AeroDataBox key works and how well each airport is covered.

Uses the free "health" endpoints: whether AeroDataBox gets live flight updates for an airport,
only published schedules, or ADS-B. Run by the "AeroDataBox check" workflow (Actions tab).
"""
import json, os, sys
from datetime import datetime, timezone
from . import flights

AIRPORTS = {"CAD4": "Trail", "CYCG": "Castlegar", "CYYF": "Penticton", "CYLW": "Kelowna", "CYXS": "Prince George"}


def main():
    which = "direct (api.aerodatabox.com)" if (os.environ.get("AERODATABOX_DIRECT_KEY") or "").strip() else "RapidAPI"
    print(f"key in use: {which}")
    out = {"checked": datetime.now(timezone.utc).isoformat(timespec="minutes"), "gateway": which, "airports": {}}
    for icao, name in AIRPORTS.items():
        try:
            d = flights._adb(f"/health/services/airports/{icao}/feeds", os.environ.get("AERODATABOX_KEY"))
            row = {k: (d.get(k) or {}).get("status") for k in ("flightSchedulesFeed", "liveFlightUpdatesFeed", "adsbUpdatesFeed")}
            ga = d.get("generalAvailability") or {}
            row["data_from"], row["data_to"] = ga.get("minAvailableLocalDate"), ga.get("maxAvailableLocalDate")
        except Exception as e:
            row = {"error": f"{type(e).__name__}: {e}"}
        out["airports"][icao] = {"name": name, **row}
        print(f"{name:14} {row}")
    out["quota"] = dict(flights.QUOTA)
    os.makedirs("data", exist_ok=True)
    with open("data/adb_coverage.json", "w") as f:
        json.dump(out, f, indent=1)
    if all("error" in v for v in out["airports"].values()):
        sys.exit("every request failed - check the key")


if __name__ == "__main__":
    main()
