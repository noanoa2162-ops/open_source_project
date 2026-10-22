"""Sending the "🙋 עזרה" e-mail from the server, without opening Gmail (Noa, 22/10/2026).

The site sends from the account in SMTP_USER, with a Gmail App Password in SMTP_PASSWORD —
never the account's real password, and never in the browser. Without both, direct sending is
off and the screen keeps the "open a ready e-mail in Gmail / Outlook" buttons.

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
import ssl
import threading
import time
from email.message import EmailMessage
from email.utils import formataddr

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TIMEOUT_SECONDS = 20
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


def available() -> bool:
    """Whether the screen should offer "שליחה עכשיו"."""
    if not (_setting("SMTP_USER") and _setting("SMTP_PASSWORD")):
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

    sender = _setting("SMTP_USER")
    message = EmailMessage()
    message["From"] = formataddr((_setting("SMTP_FROM_NAME") or "מערכת קליטת טפסי התחייבות", sender))
    message["To"] = to.strip()
    message["Subject"] = subject
    message.set_content(body)
    host = _setting("SMTP_HOST") or "smtp.gmail.com"
    port = int(_setting("SMTP_PORT") or 465)
    try:
        with smtplib.SMTP_SSL(host, port, timeout=TIMEOUT_SECONDS, context=ssl.create_default_context()) as smtp:
            smtp.login(sender, _setting("SMTP_PASSWORD"))
            smtp.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        _undo(token)
        raise MailRefused("שירות המייל לא אישר את הכניסה. ב-Gmail צריך \"סיסמה לאפליקציה\" (App Password), "
                          "לא את הסיסמה הרגילה.") from exc
    except (smtplib.SMTPException, OSError) as exc:
        _undo(token)
        raise MailRefused("המייל לא נשלח — אין חיבור לשירות המייל כרגע. נסי שוב, או פתחי מייל מוכן ב-Gmail.") from exc


def _undo(token: str) -> None:
    """A send that failed does not count against the request."""
    with _LOCK:
        if _SENT.get(token):
            _SENT[token] -= 1
        if _RECENT:
            _RECENT.pop()
