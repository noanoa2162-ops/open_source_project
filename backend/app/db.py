"""Approved forms, kept for good — one row per form the secretary approved.

SQLite: a single file (work/forms.db) that Python reads and writes on its own —
no server, no password, no install. Open it with "DB Browser for SQLite". Only
this module touches the database, so moving to PostgreSQL later is a change here.

The page image is copied next to it (work/approved_pages/) and the row keeps the
path, so the row survives even after the batch itself is deleted. Approving the
same page again (after a fix) updates its row — never a duplicate.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import threading
from pathlib import Path

from PIL import Image

from app.config import WORK_DIR
from app.models import FormRecord, SourceFile, now

DB_PATH = WORK_DIR / "forms.db"
PAGES_DIR = WORK_DIR / "approved_pages"
_LOCK = threading.Lock()

TEXT_FIELDS = ("first_name", "last_name", "national_id", "sample_received_date", "sending_entity")
LIST_FIELDS = ("central_numbers", "commitment_numbers")  # כמה ערכים — נשמרים כרשימה (JSON)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS approved_forms (
    form_key             TEXT PRIMARY KEY,
    first_name           TEXT,
    last_name            TEXT,
    national_id          TEXT,
    central_numbers      TEXT,
    sample_received_date TEXT,
    sending_entity       TEXT,
    commitment_numbers   TEXT,
    source_file          TEXT,
    page_number          INTEGER,
    page_image           TEXT,
    batch_id             TEXT,
    record_id            TEXT,
    approved_at          TEXT,
    updated_at           TEXT
)
"""


def _connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(_SCHEMA)
    return conn


def form_key(page_png: Path) -> str:
    """The page's own fingerprint (its pixels): the same form is always the same row."""
    with Image.open(page_png) as page:
        digest = hashlib.sha256(f"{page.mode}:{page.size}".encode("ascii"))
        digest.update(page.tobytes())
    return digest.hexdigest()[:24]


def save_approved(record: FormRecord, source: SourceFile, page_png: Path,
                  path: Path | None = None, pages_dir: Path | None = None) -> str:
    """Insert or update the row for this approved form. Returns its key."""
    key = form_key(page_png)
    pages_dir = pages_dir or PAGES_DIR
    pages_dir.mkdir(parents=True, exist_ok=True)
    image = pages_dir / f"{key}.png"
    shutil.copyfile(page_png, image)

    values = {k: " | ".join(record.fields[k].values) for k in TEXT_FIELDS}
    values.update({k: json.dumps(record.fields[k].values, ensure_ascii=False) for k in LIST_FIELDS})
    stamp = now()
    row = {
        "form_key": key, **values,
        "source_file": source.filename, "page_number": record.page_number,
        "page_image": str(image), "batch_id": record.batch_id, "record_id": record.record_id,
        "approved_at": stamp, "updated_at": stamp,
    }
    columns = ", ".join(row)
    marks = ", ".join(f":{c}" for c in row)
    # אישור חוזר של אותו טופס: מעדכנים את השורה, ושומרים את מועד האישור הראשון
    updates = ", ".join(f"{c} = excluded.{c}" for c in row if c not in ("form_key", "approved_at"))
    with _LOCK, _connect(path) as conn:
        conn.execute(f"INSERT INTO approved_forms ({columns}) VALUES ({marks}) "
                     f"ON CONFLICT(form_key) DO UPDATE SET {updates}", row)
    return key


def list_approved(path: Path | None = None) -> list[dict]:
    """Every approved form, newest first; the list fields come back as lists."""
    with _LOCK, _connect(path) as conn:
        rows = conn.execute("SELECT * FROM approved_forms ORDER BY updated_at DESC").fetchall()
    out = []
    for row in rows:
        item = dict(row)
        for k in LIST_FIELDS:
            item[k] = json.loads(item[k] or "[]")
        out.append(item)
    return out
