"""Authenticated, serialized HTTP boundary for one shared demo workspace."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import logging
import os
import secrets
import time
from contextlib import nullcontext
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlsplit

from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

from app import store
from . import state, storage
from .postgres import connect, writer_lock

log = logging.getLogger(__name__)
MAX_REQUEST = 256 * 1024  # Documents travel directly to private Storage.
MAX_RESPONSE = 4_000_000  # Stay below the Function payload limit.
LOGIN_BODY_LIMIT = 8 * 1024
SESSION_SECONDS = 8 * 60 * 60
SESSION_COOKIE = "tofes_session"
PUBLIC_PREFIXES = ("/help/", "/api/help/", "/app/assets/", "/static/brand/")

LOGIN_PAGE = """<!doctype html>
<html lang="he" dir="rtl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>כניסה | טופס למידע</title>
<style>
:root { color-scheme:light;font-family:Arial,sans-serif } * { box-sizing:border-box }
body { margin:0;min-height:100vh;display:grid;place-items:center;padding:24px;background:linear-gradient(145deg,#f8f5ff,#eef5ff);color:#26223a }
main { width:min(100%,420px);background:#fff;border-radius:24px;padding:34px;box-shadow:0 20px 60px #372c6b22;border:1px solid #e8e2f2 }
img { display:block;max-width:150px;max-height:64px;margin:0 auto 24px;object-fit:contain }
h1 { margin:0 0 8px;text-align:center;font-size:28px } p { margin:0 0 24px;text-align:center;color:#645d78;line-height:1.5 }
label { display:block;margin:16px 0 7px;font-weight:700 }
input { width:100%;border:1px solid #cfc7dc;border-radius:12px;padding:13px 14px;font:inherit;direction:ltr }
input:focus { outline:3px solid #7857c726;border-color:#7857c7 }
button { width:100%;margin-top:24px;border:0;border-radius:12px;padding:14px;font:inherit;font-weight:700;color:#fff;background:#6842b6;cursor:pointer }
.error { color:#a32236;background:#fff0f2;border-radius:10px;padding:10px }
</style></head><body><main>
<img src="/static/brand/moh-office.png" alt="טופס למידע">
<h1>כניסה למערכת</h1><p>המערכת מוגנת כדי לשמור על המסמכים ועל מפתח ה־API.</p>
{error}
<form method="post" action="/auth/login">
<label for="username">שם משתמש</label><input id="username" name="username" autocomplete="username" required autofocus>
<label for="password">סיסמה</label><input id="password" name="password" type="password" autocomplete="current-password" required>
<button type="submit">כניסה</button></form>
</main></body></html>"""


def validate_environment():
    required = ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "APP_ORIGIN")
    if any(not os.environ.get(name) for name in required):
        raise RuntimeError("Cloud configuration is incomplete; see .env.cloud.example")
    if len(os.environ.get("ACCESS_PASSWORD", "")) < 24:
        raise RuntimeError("ACCESS_PASSWORD must contain at least 24 characters")
    for name in ("APP_ORIGIN", "SUPABASE_URL"):
        url = urlsplit(os.environ[name])
        if url.scheme != "https" or not url.hostname or url.path not in ("", "/") or url.query or url.fragment:
            raise RuntimeError(name + " must be an HTTPS origin")
    from psycopg.conninfo import conninfo_to_dict
    database = conninfo_to_dict(os.environ["DATABASE_URL"])
    if database.get("port") == "6543":
        raise RuntimeError("Use the Supabase session pooler (5432), not transaction mode")
    if database.get("sslmode") not in ("require", "verify-ca", "verify-full"):
        raise RuntimeError("DATABASE_URL must require TLS")

class _Rollback(Exception):
    pass


class _TooLarge(Exception):
    pass


class CloudGateway:
    def __init__(self, app):
        validate_environment()
        self.app = app
        self.lock = asyncio.Lock()
        self.origin = os.environ["APP_ORIGIN"].rstrip("/")
        self.username = os.environ.get("ACCESS_USER", "demo")
        self.password = os.environ["ACCESS_PASSWORD"]
        self.public_demo = os.environ.get("PUBLIC_DEMO", "").strip().lower() in ("1", "true", "yes")
        expected = self.username + ":" + self.password
        self.credentials_hash = hashlib.sha256(expected.encode()).digest()
        self.session_key = hashlib.sha256(("tofes-session:" + self.password).encode()).digest()

    def authorized(self, headers):
        if self._session_authorized(headers):
            return True
        try:
            scheme, encoded = headers.get(b"authorization", b"").split(b" ", 1)
            value = base64.b64decode(encoded, validate=True)
            return scheme.lower() == b"basic" and secrets.compare_digest(
                hashlib.sha256(value).digest(), self.credentials_hash
            )
        except (ValueError, binascii.Error):
            return False

    def _session_authorized(self, headers) -> bool:
        try:
            cookies = SimpleCookie()
            cookies.load(headers.get(b"cookie", b"").decode("latin-1"))
            value = cookies[SESSION_COOKIE].value
            expires_text, nonce, signature = value.split(".", 2)
            if int(expires_text) < int(time.time()):
                return False
            payload = f"{expires_text}.{nonce}".encode()
            expected = base64.urlsafe_b64encode(
                hmac.new(self.session_key, payload, hashlib.sha256).digest()
            ).rstrip(b"=").decode()
            return secrets.compare_digest(signature, expected)
        except (KeyError, ValueError, UnicodeError):
            return False

    def _new_session(self) -> str:
        expires = str(int(time.time()) + SESSION_SECONDS)
        payload = f"{expires}.{secrets.token_urlsafe(18)}"
        signature = base64.urlsafe_b64encode(
            hmac.new(self.session_key, payload.encode(), hashlib.sha256).digest()
        ).rstrip(b"=").decode()
        return f"{payload}.{signature}"

    @staticmethod
    def _is_public(path: str) -> bool:
        return path == "/login" or path.startswith(PUBLIC_PREFIXES)

    @staticmethod
    async def _form(receive) -> dict[str, str]:
        body = bytearray()
        while True:
            message = await receive()
            body.extend(message.get("body", b""))
            if len(body) > LOGIN_BODY_LIMIT:
                raise ValueError("Login form is too large")
            if not message.get("more_body"):
                break
        parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
        return {key: values[-1] for key, values in parsed.items()}

    @staticmethod
    def _login_page(error: bool = False):
        message = '<p class="error" role="alert">שם המשתמש או הסיסמה אינם נכונים.</p>' if error else ""
        return HTMLResponse(
            LOGIN_PAGE.replace("{error}", message),
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": (
                    "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
                    "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
                ),
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
            },
        )

    async def _login(self, receive):
        try:
            form = await self._form(receive)
        except (ValueError, UnicodeError):
            return JSONResponse({"detail": "בקשת הכניסה אינה תקינה."}, status_code=400)
        supplied = (form.get("username", "") + ":" + form.get("password", "")).encode()
        if not secrets.compare_digest(hashlib.sha256(supplied).digest(), self.credentials_hash):
            return self._login_page(error=True)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            SESSION_COOKIE, self._new_session(), max_age=SESSION_SECONDS,
            secure=True, httponly=True, samesite="strict", path="/",
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @staticmethod
    def _logout():
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(
            SESSION_COOKIE, secure=True, httponly=True, samesite="strict", path="/",
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        path = scope["path"]
        method = scope["method"]
        origin_ok = headers.get(b"origin", b"").decode("latin-1") == self.origin
        # Hackathon mode has no visible login. The first page view receives a
        # short-lived signed browser session; the server secret never enters
        # React or the repository. Direct API requests still fail closed.
        if self.public_demo and path == "/" and method in ("GET", "HEAD") and not self.authorized(headers):
            response = RedirectResponse("/", status_code=303)
            response.set_cookie(
                SESSION_COOKIE, self._new_session(), max_age=SESSION_SECONDS,
                secure=True, httponly=True, samesite="strict", path="/",
            )
            response.headers["Cache-Control"] = "no-store"
            return await response(scope, receive, send)
        if self.public_demo and path == "/login":
            return await RedirectResponse("/", status_code=303)(scope, receive, send)
        if self.public_demo and path in ("/auth/login", "/auth/logout"):
            return await JSONResponse({"detail": "מצב הדמו אינו משתמש במסך כניסה."}, status_code=404)(
                scope, receive, send
            )
        if path == "/login" and method in ("GET", "HEAD"):
            response = RedirectResponse("/", 303) if self.authorized(headers) else self._login_page()
            return await response(scope, receive, send)
        if path == "/auth/login" and method == "POST":
            response = await self._login(receive) if origin_ok else JSONResponse(
                {"detail": "מקור הבקשה אינו מורשה."}, status_code=403
            )
            return await response(scope, receive, send)
        if path == "/auth/logout" and method == "POST":
            response = self._logout() if origin_ok and self.authorized(headers) else JSONResponse(
                {"detail": "מקור הבקשה אינו מורשה."}, status_code=403
            )
            return await response(scope, receive, send)
        if not self._is_public(path) and not self.authorized(headers) and not path.startswith("/api/"):
            return await RedirectResponse("/login", status_code=303)(scope, receive, send)
        if not self._is_public(path) and not self.authorized(headers):
            return await JSONResponse(
                {"detail": "נדרשת כניסה."}, status_code=401,
                headers={"Cache-Control": "no-store"},
            )(scope, receive, send)
        mutate = scope["method"] not in ("GET", "HEAD", "OPTIONS")
        if mutate and not origin_ok:
            return await JSONResponse({"detail": "מקור הבקשה אינו מורשה."}, status_code=403)(scope, receive, send)
        if path == "/api/batches" and mutate:
            return await JSONResponse(
                {"detail": "בענן מעלים קבצים דרך מסך ההעלאה לאחסון הפרטי."}, status_code=413
            )(scope, receive, send)
        if not path.startswith("/api/"):
            return await self.app(scope, receive, send)

        # The existing image/review code uses a process-local cache. One operation
        # at a time per process; a PostgreSQL session lock serializes all writers.
        async with self.lock:
            messages, size, received = [], 0, 0

            async def buffered(message):
                nonlocal size
                size += len(message.get("body", b""))
                if size > MAX_RESPONSE:
                    raise _TooLarge()
                if message["type"] == "http.response.start":
                    message["headers"] = [
                        (key, value) for key, value in message["headers"]
                        if key.lower() not in (b"cache-control", b"referrer-policy")
                    ] + [(b"cache-control", b"no-store"), (b"referrer-policy", b"no-referrer"),
                         (b"x-content-type-options", b"nosniff")]
                messages.append(message)

            async def bounded_receive():
                nonlocal received
                message = await receive()
                received += len(message.get("body", b""))
                if received > MAX_REQUEST:
                    raise _TooLarge()
                return message

            response = None
            try:
                storage.reset_scratch()
                store._BATCHES.clear()
                store.HYDRATE = mutate
                with writer_lock(transaction=True) if mutate else nullcontext(True) as acquired:
                    if not acquired:
                        response = JSONResponse({"detail": "המערכת מעבדת טפסים כרגע. השינויים לא נשמרו; נסו בסיום הקריאה."}, 409)
                    else:
                        if mutate:
                            state.interrupt_running()
                            with connect() as conn:
                                queued = conn.execute("SELECT 1 FROM jobs WHERE status='queued' LIMIT 1").fetchone()
                            if queued and scope["path"] != "/api/uploads/complete":
                                response = JSONResponse({"detail": "ממתינה משימת קריאה. נסו שוב אחרי שתסתיים."}, 409)
                        if response is None:
                            await self.app(scope, bounded_receive, buffered)
                            status = next(m["status"] for m in messages if m["type"] == "http.response.start")
                            if status >= 400:
                                raise _Rollback()
                            if mutate:
                                storage.flush()
                                for batch in list(store._BATCHES.values()):
                                    state.save(batch)
            except _Rollback:
                pass  # Preserve the endpoint's error, rolling back its SQL.
            except _TooLarge:
                response = JSONResponse({"detail": "הבקשה או התוצאה גדולה מדי. צמצמו את מספר הטפסים או את טווח הייצוא."}, 413)
            except Exception as exc:
                log.error("Cloud request failed (%s)", type(exc).__name__)
                response = JSONResponse({"detail": "לא ניתן להשלים את השמירה כעת. רעננו ובדקו את מצב הטופס לפני ניסיון נוסף."}, 503)
            finally:
                store.HYDRATE = False
                store._BATCHES.clear()
                storage.reset_scratch()
            if response is not None:
                return await response(scope, receive, send)
            # The success response is sent only after the database commit.
            for message in messages:
                await send(message)
