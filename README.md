# Kootenay flight risk alerts (Trail YZZ + Castlegar YCG)

Scores each departure to Vancouver for cancellation risk and emails you when a
flight in the next 30 hours looks risky. Dashboard: https://osd2nd.github.io/Flightcancelations/

## Where the data comes from
- **Official airport weather** (aviationweather.gov, free): Castlegar (CYCG) and
  Vancouver (CYVR) reports and pilots' forecasts. Gives cloud-base height, the
  thing that most often grounds Castlegar. Trail has no reports, so it uses Castlegar's.
- **Weather-model forecast**: Environment Canada's GEM models (via Open-Meteo, `weather_model`), fetched
  every 2 h. Backups in order: MET Norway, the last good forecast (up to 7 days), Trail borrows Castlegar's.
  Days with no forecast at all show "—".
- **Environment Canada's official forecast** (weather.gc.ca city pages, `eccc_city`): hourly wording for 24 h,
  daily periods and warnings, shown on the dashboard; fog/low cloud, freezing rain/heavy snow and warnings
  near a flight time count toward its risk.
- **Weather map**: embedded from Windy.com.
- **Flights**: timetable in `config.json` (`static_schedule`, `static_arrivals`). With an
  `AERODATABOX_KEY` secret, real status (cancelled, delayed, new times) is checked at
  `status_checks` (06:30 both airports + Trail yesterday afternoon, 10:00 both, 16:45 Trail only). Each airport request costs 2 API units
  (Trail + Castlegar, arrivals and departures together), about 372 units a month,
  under the free plan's 400. Credit to AeroDataBox is shown on the dashboard (required).
  `record_history` (off by default) saves each day's real outcomes to `data/history.csv`;
  check AeroDataBox's data-retention terms before turning it on.
- **History**: `data/history.csv` (date,airport,flight,sched,status). The risk
  weights start as estimates and are retrained once there are ~30 flights and
  ~5 cancellations (`python3 run.py --train`).

## How it runs
GitHub Actions runs every 30 minutes, refreshes the dashboard, and deploys it to
Pages. Emails only go out at the times in `config.json` `alert_times`.
Change times, days shown and alert level from **Settings** on the dashboard.

## Repo settings
- Settings → Pages → Source: **GitHub Actions**
- Settings → Actions → General → Workflow permissions: **Read and write**
- Secrets (Settings → Secrets and variables → Actions):
  - `SMTP_USER` / `SMTP_PASS`: Gmail address + app password (sends the alerts)
  - `ALERT_EMAILS`: who gets alerts, comma separated (private; defaults to `SMTP_USER`).
    Can also be set from dashboard Settings.
  - `AERODATABOX_KEY`: optional, live flight status

## Try it locally
    python3 run.py --demo --no-email

## Limits
- Risk numbers are estimates until real cancellation history is loaded.
- Castlegar's official forecast only covers ~8 daytime hours; beyond that it uses the weather model.
- Not an airline notice. Always confirm with the airline.
