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
    try:
        with smtplib.SMTP_SSL(os.environ.get("SMTP_HOST", "smtp.gmail.com"), 465, timeout=30) as s:
            s.login(user, pw)
            s.send_message(msg)
        return None
    except smtplib.SMTPAuthenticationError:
        return "Gmail rejected the login"
    except smtplib.SMTPServerDisconnected:
        return "Gmail closed the connection during login (usually a wrong or revoked app password)"
    except Exception as e:
        return f"{type(e).__name__}: {e}"
