"""Sends email. With no SMTP settings it saves each message into the 'outbox' folder (and prints it),
so password-reset works on a laptop with no mail server."""
import os
import re
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage

from common import BASE_DIR

OUTBOX = os.path.join(BASE_DIR, "outbox")


def send_mail(to, subject, body):
    """Returns 'sent' or 'saved'."""
    host = os.environ.get("SMTP_HOST")
    if host:
        msg = EmailMessage()
        msg["Subject"], msg["To"] = subject, to
        msg["From"] = os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER") or "no-reply@localhost"
        msg.set_content(body)
        port = int(os.environ.get("SMTP_PORT", "587"))
        if os.environ.get("SMTP_SSL") == "1":
            server = smtplib.SMTP_SSL(host, port, timeout=15, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(host, port, timeout=15)
        with server:
            if os.environ.get("SMTP_SSL") != "1" and os.environ.get("SMTP_TLS", "1") == "1":
                server.starttls(context=ssl.create_default_context())
            if os.environ.get("SMTP_USER"):
                server.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
            server.send_message(msg)
        return "sent"

    os.makedirs(OUTBOX, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    path = os.path.join(OUTBOX, f"{stamp}-{re.sub(r'[^a-z0-9]+', '-', to.lower())}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"To: {to}\nSubject: {subject}\n\n{body}\n")
    print(f"\n[email not sent: no SMTP set up] Saved to {path}\n{body}\n")
    return "saved"
