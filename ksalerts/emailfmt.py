"""The alert email: a short subject that says it all, then one clear block per flight.

Sent as HTML (coloured risk, big times) with a plain-text copy for mail apps that
don't show HTML.
"""
from datetime import date, timedelta
from html import escape

PLACE = {"YZZ": "Trail", "YCG": "Castlegar", "YVR": "Vancouver"}
COLOUR = {"SEVERE": "#b91c1c", "HIGH": "#ea580c", "WATCH": "#ca8a04", "LOW": "#15803d"}


def _day(d, today):
    d = date.fromisoformat(d)
    if d == today:
        return "Today"
    if d == today + timedelta(days=1):
        return "Tomorrow"
    return d.strftime("%a %b %-d")


def _time(hhmm):
    h, m = map(int, hhmm.split(":"))
    return f"{(h - 1) % 12 + 1}:{m:02d} {'am' if h < 12 else 'pm'}"


def _cancelled(r):
    return str(r.get("status", "")).lower().startswith("cancel")


def _route(r):
    here = PLACE.get(r["airport"], r["airport"])
    return f"Vancouver → {here}" if r["direction"] == "arr" else f"{here} → Vancouver"


def build(flights, today, site):
    """flights: scored flight dicts. Returns (subject, text, html)."""
    flights = sorted(flights, key=lambda r: (not _cancelled(r), r["date"], r["dep_local"]))
    canc = [r for r in flights if _cancelled(r)]

    def short(r):
        return f"{PLACE.get(r['airport'], r['airport'])} {_time(r['dep_local'])} {_day(r['date'], today).lower()}"
    if canc:
        subject = "CANCELLED: " + ", ".join(f"{_route(r)} {_time(r['dep_local'])} {_day(r['date'], today).lower()}"
                                            for r in canc)
    else:
        top = max(flights, key=lambda r: r["risk"])
        subject = f"{round(top['risk'] * 100)}% risk: {short(top)}"
    rest = len(flights) - (len(canc) if canc else 1)
    if rest > 0:
        subject += f" (+{rest} more)"

    text, blocks = [], []
    for r in flights:
        pct = round(r["risk"] * 100)
        day, t, route = _day(r["date"], today), _time(r["dep_local"]), _route(r)
        verb = "arriving" if r["direction"] == "arr" else "departing"
        reasons = [x for x in r.get("reasons") or [] if x and "already shows" not in x][:4]
        if _cancelled(r):
            headline, colour = "CANCELLED by the airline", COLOUR["SEVERE"]
        else:
            headline, colour = f"{pct}% cancel risk · {r['level']}", COLOUR.get(r["level"], "#444")
        text.append(f"{day} {t} · {route}\n{r['airline']} {r['flight']} ({verb} {r['airport_name']})\n"
                    f"{headline}\n" + "".join(f"  - {x}\n" for x in reasons))
        blocks.append(f"""
<tr><td style="padding:0 0 14px">
 <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e5e7eb;border-left:6px solid {colour};border-radius:10px">
  <tr><td style="padding:14px 16px">
   <div style="font-size:13px;color:#6b7280;text-transform:uppercase;letter-spacing:.04em">{escape(day)}</div>
   <div style="font-size:24px;font-weight:700;color:#111827;margin:2px 0">{escape(t)} &nbsp;{escape(route)}</div>
   <div style="font-size:14px;color:#6b7280">{escape(r['airline'])} {escape(r['flight'])} · {verb} {escape(r['airport_name'])}</div>
   <div style="margin:10px 0 6px"><span style="display:inline-block;background:{colour};color:#fff;font-weight:700;font-size:16px;padding:6px 12px;border-radius:999px">{escape(headline)}</span></div>
   {''.join(f'<div style="font-size:15px;color:#374151;margin:4px 0">• {escape(x)}</div>' for x in reasons)}
  </td></tr>
 </table>
</td></tr>""")

    foot_txt = ("This is an estimate from weather forecasts and past cancellations, not an airline notice. "
                "Check with the airline before you travel.")
    text_body = ("\n".join(text) + f"\nDashboard: {site}\n\n{foot_txt}\n\n--\nKootenay Flight Risk alerts. "
                 f"You're getting this because you signed up.\nUnsubscribe: {site}#unsubscribe")
    html = f"""<!doctype html><html><body style="margin:0;background:#f3f4f6;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6"><tr><td align="center" style="padding:16px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#fff;border-radius:12px">
 <tr><td style="padding:18px 16px 8px"><div style="font-size:18px;font-weight:700;color:#111827">Kootenay flight risk</div></td></tr>
 <tr><td style="padding:6px 16px 0"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">{''.join(blocks)}</table></td></tr>
 <tr><td style="padding:0 16px 16px"><a href="{site}" style="display:block;text-align:center;background:#1d4ed8;color:#fff;text-decoration:none;font-weight:700;font-size:16px;padding:12px;border-radius:8px">Open the dashboard</a></td></tr>
 <tr><td style="padding:0 16px 18px;font-size:12px;color:#6b7280;line-height:1.5">{escape(foot_txt)}<br>
  You're getting this because you signed up. <a href="{site}#unsubscribe" style="color:#6b7280">Unsubscribe</a></td></tr>
</table></td></tr></table></body></html>"""
    return subject, text_body, html
