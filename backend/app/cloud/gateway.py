"""Authenticated, serialized HTTP boundary for one shared demo workspace."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import logging
import os
import secrets
from contextlib import nullcontext
from urllib.parse import urlsplit

from starlette.responses import JSONResponse

from app import store
from . import state, storage
from .postgres import connect, writer_lock

log = logging.getLogger(__name__)
MAX_REQUEST = 256 * 1024  # Documents travel directly to private Storage.
MAX_RESPONSE = 4_000_000  # Stay below the Function payload limit.


def validate_environment():
    required = ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "APP_ORIGIN")
    if any(not os.environ.get(name) for name in required):
        raise RuntimeError("Cloud configuration is incomplete; see .env.cloud.example")
    if not public_access() and len(os.environ.get("ACCESS_PASSWORD", "")) < 24:
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


def public_access() -> bool:
    return os.environ.get("PUBLIC_ACCESS", "").strip().lower() in ("1", "true", "yes")


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
        self.public = public_access()
        if self.public:
            self.credentials_hash = None
        else:
            expected = os.environ.get("ACCESS_USER", "demo") + ":" + os.environ["ACCESS_PASSWORD"]
            self.credentials_hash = hashlib.sha256(expected.encode()).digest()

    def authorized(self, headers):
        if self.public:
            return True
        try:
            scheme, encoded = headers.get(b"authorization", b"").split(b" ", 1)
            value = base64.b64decode(encoded, validate=True)
            return scheme.lower() == b"basic" and secrets.compare_digest(
                hashlib.sha256(value).digest(), self.credentials_hash
            )
        except (ValueError, binascii.Error):
            return False

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        if not self.authorized(headers):
            return await JSONResponse(
                {"detail": "נדרשת כניסה."}, status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Tofes", charset="UTF-8"', "Cache-Control": "no-store"},
            )(scope, receive, send)
        mutate = scope["method"] not in ("GET", "HEAD", "OPTIONS")
        if mutate and headers.get(b"origin", b"").decode() != self.origin:
            return await JSONResponse({"detail": "מקור הבקשה אינו מורשה."}, status_code=403)(scope, receive, send)
        if scope["path"] == "/api/batches" and mutate:
            return await JSONResponse(
                {"detail": "בענן מעלים קבצים דרך מסך ההעלאה לאחסון הפרטי."}, status_code=413
            )(scope, receive, send)
        if not scope["path"].startswith("/api/"):
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
