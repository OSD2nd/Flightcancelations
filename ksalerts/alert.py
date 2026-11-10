"""Email alerts over SMTP. Works with a Gmail app password (SMTP_USER / SMTP_PASS)."""
import os, smtplib
from email.message import EmailMessage


def send(subject, body, to):
    """Returns None if sent (or no login set up), otherwise a short reason it failed."""
    user = (os.environ.get("SMTP_USER") or "").strip()
    # Google shows app passwords as "abcd efgh ijkl mnop"; the spaces aren't part of it
    pw = "".join((os.environ.get("SMTP_PASS") or "").split())
    if not user or not pw:
        print("[alert] SMTP_USER / SMTP_PASS not set - printing instead:\n", subject, "\n", body)
        return None
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = user, ", ".join(to or [user]), subject
    msg.set_content(body)
    # safe diagnostic for the run log: no secret values, just their shape
    name, _, domain = user.partition("@")
    print(f"[alert] login as {name[:2]}***@{domain or '(no @ in SMTP_USER!)'}, app password length {len(pw)}"
          f"{' (expected 16)' if len(pw) != 16 else ''}, letters only: {pw.isalpha()}")
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    last = None
    for port in (465, 587):  # SSL first, then STARTTLS
        try:
            if port == 465:
                s = smtplib.SMTP_SSL(host, 465, timeout=30)
            else:
                s = smtplib.SMTP(host, 587, timeout=30)
                s.starttls()
            with s:
                s.login(user, pw)
                s.send_message(msg)
            return None
        except smtplib.SMTPAuthenticationError as e:
            last = f"Gmail rejected the login ({e.smtp_code})"
            break  # same answer on the other port
        except smtplib.SMTPServerDisconnected:
            last = "Gmail closed the connection during login (usually a wrong or revoked app password)"
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
        print(f"[alert] port {port}: {last}")
    return last
