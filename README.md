# Kootenay flight risk alerts (Trail YZZ + Castlegar YCG)

Scores each departure to Vancouver for cancellation risk and emails you when a
flight in the next 30 hours looks risky. Dashboard: https://osd2nd.github.io/Flightcancelations/

## Where the data comes from
- **Official airport weather** (aviationweather.gov, free): Castlegar (CYCG) and
  Vancouver (CYVR) reports and pilots' forecasts. Gives cloud-base height, the
  thing that most often grounds Castlegar. Trail has no reports, so it uses Castlegar's.
- **Weather-model forecast** (Open-Meteo, free): the 7-day outlook.
- **Weather map**: embedded from Windy.com.
- **Flights**: times in `config.json`, or live status if `AERODATABOX_KEY` is set.
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
