# Which BC airports have the most flight-time bad weather?

`airport_weather_ranking.txt` counts, for each airport, the share of winter days
(Nov–Mar, Nov 2023 – Nov 2026) with poor weather between 9 am and 3 pm, from the
airports' hourly weather reports (Iowa Environmental Mesonet archive).

- **IFR day**: cloud base under 1,000 ft or visibility under 3 miles at least once
- **LIFR day**: cloud base under 500 ft or visibility under 1 mile
- **cloud<2000**: cloud base under 2,000 ft
- **wet+cloud<4000**: cloud base under 4,000 ft with rain, snow or mist (the
  pattern behind the Nov 2026 Castlegar cancellations)

This is weather only. Airports with good instrument approaches fly through
weather that grounds others (Castlegar's approach limits are high), so real
cancellation rates need flight outcome data.

To redo it: download each station's reports from
`https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py` (fields vsby,
skyc1-4, skyl1-4, wxcodes; UTC) into a folder named `STATION.csv`, then run
`python3 rank_airports.py <folder>`.
