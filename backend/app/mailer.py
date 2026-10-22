"""Sending the "🙋 עזרה" e-mail from the server, without opening Gmail (Noa, 22/10/2026).

Two ways, set once by the site owner on the server — never in the browser:
- Brevo (Noa's choice): BREVO_API_KEY and MAIL_FROM, a sender address verified in Brevo.
  Sent over HTTPS (port 443), so no mail port has to be open.
- Gmail: SMTP_USER and SMTP_PASSWORD (a Gmail App Password, not the account's password).
Brevo is used when both are set. Without either, direct sending is off and the screen keeps
the "open a ready e-mail in Gmail / Outlook" buttons.

The server writes the message itself (asker, field, question and the link — no patient
details), so nobody can use the site to send their own text. Recipients: when
HELP_MAIL_ALLOWED is set (addresses and/or @domains, comma-separated), only those. On a public
site (PUBLIC_DEMO, no password) direct sending stays off unless that list is set — otherwise
anyone could send mail in the owner's name.
"""

from __future__ import annotations

import os
import re
import smtplib
import json
import ssl
import threading
import time
import urllib.error
import urllib.request
from email.message import EmailMessage
from email.utils import formataddr

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TIMEOUT_SECONDS = 20
BREVO_URL = "https://api.brevo.com/v3/smtp/email"
DEFAULT_NAME = "מערכת קליטת טפסי התחייבות"
SENDS_PER_REQUEST = 3  # "שליחה שוב" — אבל לא בלי סוף
SENDS_PER_HOUR = 30  # תקרה לתהליך — הגנה מפני שליחה חוזרת בלולאה

_LOCK = threading.Lock()
_SENT: dict[str, int] = {}
_RECENT: list[float] = []


class MailRefused(Exception):
    """A reason to show the secretary, in Hebrew."""


def _setting(name: str) -> str:
    return os.environ.get(name, "").strip()


def _public() -> bool:
    from app.cloud import enabled as cloud_enabled

    return cloud_enabled() and _setting("PUBLIC_DEMO").lower() in ("1", "true", "yes")


def _allowed_list() -> list[str]:
    return [item.strip().lower() for item in _setting("HELP_MAIL_ALLOWED").split(",") if item.strip()]


def _brevo() -> bool:
    return bool(_setting("BREVO_API_KEY") and _setting("MAIL_FROM"))


def available() -> bool:
    """Whether the screen should offer "שליחה עכשיו"."""
    if not (_brevo() or (_setting("SMTP_USER") and _setting("SMTP_PASSWORD"))):
        return False
    return not _public() or bool(_allowed_list())


def allowed(address: str) -> bool:
    address = address.strip().lower()
    if not EMAIL_RE.match(address):
        return False
    rules = _allowed_list()
    if not rules:
        return True
    domain = "@" + address.split("@", 1)[1]
    return address in rules or domain in rules


def send(token: str, to: str, subject: str, body: str) -> None:
    """Send one help e-mail. Raises MailRefused with a reason the secretary can act on."""
    if not available():
        raise MailRefused("שליחה ישירה לא מוגדרת באתר הזה — פתחי מייל מוכן ב-Gmail או ב-Outlook.")
    if not allowed(to):
        raise MailRefused("אי אפשר לשלוח לכתובת הזו מהאתר. בדקי את הכתובת, או פתחי מייל מוכן ב-Gmail.")
    with _LOCK:
        now = time.monotonic()
        _RECENT[:] = [t for t in _RECENT if now - t < 3600]
        if _SENT.get(token, 0) >= SENDS_PER_REQUEST:
            raise MailRefused("הבקשה הזו כבר נשלחה כמה פעמים. אפשר להעתיק את הקישור ולשלוח אותו בעצמך.")
        if len(_RECENT) >= SENDS_PER_HOUR:
            raise MailRefused("נשלחו הרבה מיילים בשעה האחרונה. נסי שוב מאוחר יותר, או פתחי מייל מוכן ב-Gmail.")
        _SENT[token] = _SENT.get(token, 0) + 1
        _RECENT.append(now)

    try:
        if _brevo():
            _send_brevo(to.strip(), subject, body)
        else:
            _send_smtp(to.strip(), subject, body)
    except MailRefused:
        _undo(token)
        raise


def _send_brevo(to: str, subject: str, body: str) -> None:
    payload = {
        "sender": {"name": _setting("SMTP_FROM_NAME") or DEFAULT_NAME, "email": _setting("MAIL_FROM")},
        "to": [{"email": to}],
        "subject": subject,
        "textContent": body,
    }
    request = urllib.request.Request(
        BREVO_URL, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), method="POST",
        headers={"api-key": _setting("BREVO_API_KEY"), "content-type": "application/json", "accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            if response.status >= 300:
                raise MailRefused("שירות המייל (Brevo) לא קיבל את ההודעה. נסי שוב, או פתחי מייל מוכן ב-Gmail.")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace").lower()
        if exc.code in (401, 403) and "sender" not in detail:
            raise MailRefused("שירות המייל (Brevo) לא אישר את המפתח. בדקי את BREVO_API_KEY בהגדרות האתר.") from exc
        if "sender" in detail:
            raise MailRefused("כתובת השולח לא אומתה ב-Brevo. אמתי את MAIL_FROM ב-Brevo (Senders) ונסי שוב.") from exc
        raise MailRefused("שירות המייל (Brevo) דחה את ההודעה. נסי שוב, או פתחי מייל מוכן ב-Gmail.") from exc
    except OSError as exc:
        raise MailRefused("המייל לא נשלח — אין חיבור לשירות המייל כרגע. נסי שוב, או פתחי מייל מוכן ב-Gmail.") from exc


def _send_smtp(to: str, subject: str, body: str) -> None:
    sender = _setting("SMTP_USER")
    message = EmailMessage()
    message["From"] = formataddr((_setting("SMTP_FROM_NAME") or DEFAULT_NAME, sender))
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    host = _setting("SMTP_HOST") or "smtp.gmail.com"
    port = int(_setting("SMTP_PORT") or 465)
    try:
        with smtplib.SMTP_SSL(host, port, timeout=TIMEOUT_SECONDS, context=ssl.create_default_context()) as smtp:
            smtp.login(sender, _setting("SMTP_PASSWORD"))
            smtp.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        raise MailRefused("שירות המייל לא אישר את הכניסה. ב-Gmail צריך \"סיסמה לאפליקציה\" (App Password), "
                          "לא את הסיסמה הרגילה.") from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise MailRefused("המייל לא נשלח — אין חיבור לשירות המייל כרגע. נסי שוב, או פתחי מייל מוכן ב-Gmail.") from exc


def _undo(token: str) -> None:
    """A send that failed does not count against the request."""
    with _LOCK:
        if _SENT.get(token):
            _SENT[token] -= 1
        if _RECENT:
            _RECENT.pop()
