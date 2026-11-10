# Kootenay flight risk alerts (Trail YZZ + Castlegar YCG)

Scores each departure to Vancouver for cancellation risk from weather forecasts
(Trail, Castlegar, Vancouver) plus what past cancellations looked like. Emails
you when tomorrow looks risky. `docs/index.html` is the dashboard;
`docs/latest.json` is the data other pages (your maps) can read.

## Try it (no accounts needed)
    python3 run.py --demo --no-email

## Go live
1. Put this folder in a GitHub repo. Turn on Pages (Settings → Pages → /docs).
2. Add repo secrets (Settings → Secrets → Actions):
   - `SMTP_USER` / `SMTP_PASS`: Gmail address + an App Password
   - `AERODATABOX_KEY`: RapidAPI key for AeroDataBox (gives real schedules, delays, cancellations)
3. Load history once, then retrain: `AERODATABOX_KEY=... python3 run.py --backfill 180`
   (or hand-fill `data/history.csv`: date,airport,flight,sched,status)
4. The workflow runs at ~7pm and ~6:30am Pacific. Edit `config.json` for thresholds/emails/schedule.

## Honest limits
- Until history is loaded it uses starting estimates, not learned ones.
- Only a few flights a day: the learning is slow and noisy. It needs ~30 flights and ~5 cancellations to start adjusting.
- Weather is forecast-model data, not the official aviation forecast. No live aircraft tracking yet.
- Fallback schedule in config.json comes from public listings; verify it.
