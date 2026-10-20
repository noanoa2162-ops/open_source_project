"""Private object storage with local scratch files for the existing image libraries.

Postgres maps logical paths to immutable objects. Local files are only a cache.
Only files whose content changed are uploaded. No directory is made public.
"""

from __future__ import annotations

import hashlib
import os
import threading
import shutil
import tempfile
import mimetypes
import time
from functools import lru_cache
from pathlib import Path

import httpx
from storage3 import SyncStorageClient

from app.config import DATA_DIR
from .postgres import connect

_BASELINE: dict[str, str] = {}
_LOCK = threading.RLock()
_LAST_SWEEP = 0.0


@lru_cache(maxsize=1)
def bucket():
    secret = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    client = SyncStorageClient(
        os.environ["SUPABASE_URL"].rstrip("/") + "/storage/v1/",
        {"apikey": secret, "Authorization": "Bearer " + secret},
        http_client=httpx.Client(timeout=60),
    )
    return client.from_(os.environ.get("SUPABASE_BUCKET", "documents"))


def logical(path: Path) -> str:
    relative = path.resolve().relative_to(DATA_DIR.resolve())
    if not relative.parts or relative.parts[0] not in ("work", "evaluation"):
        raise ValueError("Artifact is outside application data directories")
    return relative.as_posix()


def local(key: str) -> Path:
    path = DATA_DIR.joinpath(*key.split("/"))
    if logical(path) != key:
        raise ValueError("Invalid artifact path")
    return path


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def ensure(path: Path) -> Path:
    key = logical(path)
    with _LOCK:
        # A file modified by the active operation must not be replaced by its old remote copy.
        if path.is_file() and digest(path) != _BASELINE.get(key):
            return path
        with connect() as conn:
            item = conn.execute("SELECT * FROM artifacts WHERE path=%s", (key,)).fetchone()
        if item is None:
            if key in _BASELINE:
                path.unlink(missing_ok=True)
                _BASELINE.pop(key, None)
            return path
        if path.is_file() and digest(path) == item["sha256"]:
            return path
        data = bucket().download(item["object_key"])
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise OSError("Artifact checksum mismatch")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        _BASELINE[key] = item["sha256"]
    return path


def restore_prefix(prefix: str):
    with connect() as conn:
        rows = conn.execute("SELECT path FROM artifacts WHERE path LIKE %s", (prefix + "%",)).fetchall()
    for row in rows:
        ensure(local(row["path"]))


def put(path: Path):
    key = logical(path)
    # Hash the bytes actually uploaded, even if another page thread replaces a file.
    data = path.read_bytes()
    hashed = hashlib.sha256(data).hexdigest()
    if _BASELINE.get(key) == hashed:
        return
    object_key = "objects/" + hashed
    with connect() as conn:
        previous = conn.execute("SELECT sha256,object_key FROM artifacts WHERE path=%s", (key,)).fetchone()
        if not previous or previous["sha256"] != hashed:
            used = conn.execute("SELECT coalesce(sum(size),0) AS bytes FROM artifacts WHERE path<>%s", (key,)).fetchone()["bytes"]
            if used + len(data) > 800 * 1024 * 1024:
                raise OSError("Workspace storage limit reached")
            bucket().upload(object_key, data, {
                "content-type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                "upsert": "true",
            })
            conn.execute(
                "INSERT INTO artifacts(path,object_key,sha256,size) VALUES (%s,%s,%s,%s) "
                "ON CONFLICT(path) DO UPDATE SET object_key=excluded.object_key,sha256=excluded.sha256,"
                "size=excluded.size,updated_at=now()",
                (key, object_key, hashed, len(data)),
            )
            if previous and previous["object_key"] != object_key:
                conn.execute("INSERT INTO garbage(object_key) VALUES (%s) ON CONFLICT DO NOTHING",
                             (previous["object_key"],))
    _BASELINE[key] = hashed


def collect_garbage():
    """Run under the workspace lock, after request transactions have committed."""
    with connect() as conn:
        rows = conn.execute("SELECT object_key FROM garbage").fetchall()
        for row in rows:
            key = row["object_key"]
            referenced = conn.execute("SELECT 1 FROM artifacts WHERE object_key=%s LIMIT 1", (key,)).fetchone()
            if not referenced:
                bucket().remove([key])
            conn.execute("DELETE FROM garbage WHERE object_key=%s", (key,))
        # Upload URLs expire in two hours. Keep tickets longer so replayed uploads
        # are cleaned too, including inputs abandoned before finalization.
        expired = conn.execute("SELECT * FROM uploads WHERE created_at < now() - interval '3 hours'").fetchall()
        for row in expired:
            active = conn.execute("SELECT 1 FROM jobs WHERE batch_id=%s AND status IN ('queued','running')",
                                  (row["batch_id"],)).fetchone()
            if not active:
                bucket().remove([row["object_key"]])
                conn.execute("DELETE FROM uploads WHERE upload_id=%s", (row["upload_id"],))
    # Object storage and SQL cannot commit together. An upload followed by a
    # rolled-back SQL transaction can leave an unreferenced immutable object.
    # Reconcile under the same writer lock, when no other writer can publish it.
    global _LAST_SWEEP
    if time.monotonic() - _LAST_SWEEP >= 3600:
        offset, unused = 0, []
        while True:
            objects = bucket().list("objects", {"limit": 100, "offset": offset, "sortBy": {"column": "name", "order": "asc"}})
            with connect() as conn:
                for item in objects:
                    key = "objects/" + item["name"]
                    if not conn.execute("SELECT 1 FROM artifacts WHERE object_key=%s LIMIT 1", (key,)).fetchone():
                        unused.append(key)
            if len(objects) < 100:
                break
            offset += len(objects)
        for offset in range(0, len(unused), 100):
            bucket().remove(unused[offset:offset + 100])
        _LAST_SWEEP = time.monotonic()


def remove(path: Path):
    key = logical(path)
    with connect() as conn:
        row = conn.execute("DELETE FROM artifacts WHERE path=%s RETURNING object_key", (key,)).fetchone()
        if row:
            conn.execute("INSERT INTO garbage(object_key) VALUES (%s) ON CONFLICT DO NOTHING", (row["object_key"],))
    path.unlink(missing_ok=True)
    _BASELINE.pop(key, None)


def remove_prefix(prefix: str):
    with connect() as conn:
        rows = conn.execute("SELECT path FROM artifacts WHERE path LIKE %s", (prefix + "%",)).fetchall()
    for row in rows:
        remove(local(row["path"]))


def flush():
    with _LOCK:
        for key in list(_BASELINE):
            if not local(key).is_file():
                remove(local(key))
        for directory in (DATA_DIR / "work", DATA_DIR / "evaluation"):
            if directory.is_dir():
                for path in directory.rglob("*"):
                    if path.is_file() and path.suffix not in (".tmp", ".db", ".log"):
                        put(path)


def signed_download(path: Path) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT object_key FROM artifacts WHERE path=%s", (logical(path),)).fetchone()
    if row is None:
        return None
    return bucket().create_signed_url(row["object_key"], 60)["signedURL"]


def reset_scratch():
    """Used after each serialized API operation/job, including failures."""
    root = DATA_DIR.resolve()
    if not root.is_relative_to(Path(tempfile.gettempdir()).resolve()) or root.name != "tofes-cloud":
        raise RuntimeError("Cloud scratch cleanup requires the dedicated temporary directory")
    for name in ("work", "evaluation"):
        target = (root / name).resolve()
        if not target.is_relative_to(root):
            raise RuntimeError("Invalid scratch directory")
        if target.exists():
            shutil.rmtree(target)
    _BASELINE.clear()
