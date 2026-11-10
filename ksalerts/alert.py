"""Email alerts over SMTP. Works with a Gmail app password (SMTP_USER / SMTP_PASS)."""
import os, smtplib
from email.message import EmailMessage


def send(subject, body, to):
    user, pw = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASS")
    if not user or not pw:
        print("[alert] SMTP_USER / SMTP_PASS not set - printing instead:\n", subject, "\n", body)
        return False
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = user, ", ".join(to), subject
    msg.set_content(body)
    with smtplib.SMTP_SSL(os.environ.get("SMTP_HOST", "smtp.gmail.com"), 465) as s:
        s.login(user, pw)
        s.send_message(msg)
    return True
