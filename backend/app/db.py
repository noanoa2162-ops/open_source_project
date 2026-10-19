"""Approved forms, kept for good — one row per form the secretary approved.

SQLite: a single file (work/forms.db) that Python reads and writes on its own —
no server, no password, no install. Open it with "DB Browser for SQLite". Only
this module touches the database, so moving to PostgreSQL later is a change here.

The page image is copied next to it (work/approved_pages/) and the row keeps the
path, so the row survives even after the batch itself is deleted. Approving the
same page again (after a fix) updates its row — never a duplicate.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import shutil
import sqlite3
import threading
from pathlib import Path

from PIL import Image

from app.config import WORK_DIR
from app.models import Batch, ExtractedField, FormRecord, SourceFile, now

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


# כל ההעלאות וכל הרשומות — לא רק המאושרות. האצווה כולה נשמרת כ-JSON (מקור האמת לטעינה
# מחדש), והרשומות גם כשורות עם עמודות לחיפוש ולסינון במסך "רשומות".
_SCHEMA_RECORDS = (
    """CREATE TABLE IF NOT EXISTS batches (
        batch_id     TEXT PRIMARY KEY,
        session_id   TEXT NOT NULL DEFAULT '',
        status       TEXT,
        display_name TEXT,
        created_at   TEXT,
        updated_at   TEXT,
        data         TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS records (
        record_id            TEXT PRIMARY KEY,
        batch_id             TEXT NOT NULL,
        session_id           TEXT NOT NULL DEFAULT '',
        status               TEXT,
        first_name           TEXT,
        last_name            TEXT,
        national_id          TEXT,
        central_numbers      TEXT,
        sample_received_date TEXT,
        received_iso         TEXT,
        sending_entity       TEXT,
        commitment_numbers   TEXT,
        review_count         INTEGER,
        source_name          TEXT,
        page_number          INTEGER,
        revision             INTEGER,
        created_at           TEXT,
        updated_at           TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS records_session ON records(session_id, created_at)",
    "CREATE INDEX IF NOT EXISTS records_batch ON records(batch_id)",
)


def _connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_SCHEMA)
    for statement in _SCHEMA_RECORDS:
        conn.execute(statement)
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


# ------------------------------------------------------------ כל ההעלאות והרשומות
def _batch_to_json(batch: Batch) -> str:
    return json.dumps(dataclasses.asdict(batch), ensure_ascii=False)


def _region(value) -> tuple | None:
    return tuple(value) if value else None


def _batch_from_json(text: str) -> Batch:
    data = json.loads(text)
    known = {f.name for f in dataclasses.fields(Batch)}
    record_fields = {f.name for f in dataclasses.fields(FormRecord)}
    field_fields = {f.name for f in dataclasses.fields(ExtractedField)}
    sources = [SourceFile(**s) for s in data.pop("sources", [])]
    records = []
    for raw in data.pop("records", []):
        fields = {}
        for key, f in (raw.pop("fields", {}) or {}).items():
            f = {k: v for k, v in f.items() if k in field_fields}
            f["region"] = _region(f.get("region"))
            f["value_regions"] = [_region(r) for r in f.get("value_regions") or []]
            fields[key] = ExtractedField(**f)
        records.append(FormRecord(**{k: v for k, v in raw.items() if k in record_fields}, fields=fields))
    return Batch(**{k: v for k, v in data.items() if k in known}, sources=sources, records=records)


def _received_iso(values: list[str]) -> str:
    """DD/MM/YYYY → YYYY-MM-DD, for date-range filtering. Empty when unclear."""
    for value in values:
        parts = value.split("/")
        if len(parts) == 3 and all(p.isdigit() for p in parts):
            return f"{parts[2]}-{parts[1]}-{parts[0]}"
    return ""


def _record_row(record: FormRecord, session_id: str) -> dict:
    values = {k: f.values for k, f in record.fields.items()}
    return {
        "record_id": record.record_id,
        "batch_id": record.batch_id,
        "session_id": session_id,
        "status": record.status,
        **{k: " | ".join(values.get(k, [])) for k in TEXT_FIELDS},
        **{k: json.dumps(values.get(k, []), ensure_ascii=False) for k in LIST_FIELDS},
        "received_iso": _received_iso(values.get("sample_received_date", [])),
        "review_count": record.review_count,
        "source_name": record.source_name,
        "page_number": record.page_number,
        "revision": record.revision,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def save_batch(batch: Batch, path: Path | None = None) -> None:
    """Write the whole batch and every one of its records. Idempotent: running it
    again replaces the rows — a retry never duplicates a record."""
    rows = [_record_row(r, batch.session_id) for r in batch.records]
    with _LOCK, _connect(path) as conn:
        conn.execute(
            "INSERT INTO batches (batch_id, session_id, status, display_name, created_at, updated_at, data) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(batch_id) DO UPDATE SET "
            "status = excluded.status, display_name = excluded.display_name, "
            "updated_at = excluded.updated_at, data = excluded.data",
            (batch.batch_id, batch.session_id, batch.status, batch.display_name,
             batch.created_at, now(), _batch_to_json(batch)),
        )
        keep = [r["record_id"] for r in rows]
        if keep:
            marks = ",".join("?" * len(keep))
            conn.execute(f"DELETE FROM records WHERE batch_id = ? AND record_id NOT IN ({marks})",
                         (batch.batch_id, *keep))
        else:
            conn.execute("DELETE FROM records WHERE batch_id = ?", (batch.batch_id,))
        for row in rows:
            columns = ", ".join(row)
            marks = ", ".join(":" + c for c in row)
            updates = ", ".join(f"{c} = excluded.{c}" for c in row if c not in ("record_id", "created_at"))
            conn.execute(f"INSERT INTO records ({columns}) VALUES ({marks}) "
                         f"ON CONFLICT(record_id) DO UPDATE SET {updates}", row)


def load_batches(path: Path | None = None) -> list[Batch]:
    """Every saved batch, oldest first. A damaged row is skipped, never fatal."""
    with _LOCK, _connect(path) as conn:
        rows = conn.execute("SELECT data FROM batches ORDER BY created_at").fetchall()
    batches = []
    for row in rows:
        try:
            batches.append(_batch_from_json(row["data"]))
        except (ValueError, TypeError, KeyError):
            continue
    return batches


def delete_batch(batch_id: str, path: Path | None = None) -> None:
    with _LOCK, _connect(path) as conn:
        conn.execute("DELETE FROM records WHERE batch_id = ?", (batch_id,))
        conn.execute("DELETE FROM batches WHERE batch_id = ?", (batch_id,))


def search_records(
    session_id: str | None,
    q: str = "",
    status: str = "",
    sender: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 500,
    path: Path | None = None,
) -> list[dict]:
    """Records for the records screen, newest first. `session_id=None` = every
    session — for local tools only, never for the public API."""
    where, args = [], []
    if session_id is not None:
        where.append("session_id = ?")
        args.append(session_id)
    if status:
        where.append("status = ?")
        args.append(status)
    if sender:
        where.append("sending_entity = ?")
        args.append(sender)
    if date_from:
        where.append("received_iso != '' AND received_iso >= ?")
        args.append(date_from)
    if date_to:
        where.append("received_iso != '' AND received_iso <= ?")
        args.append(date_to)
    for word in q.split():
        like = f"%{word}%"
        where.append(
            "(first_name LIKE ? OR last_name LIKE ? OR national_id LIKE ? OR central_numbers LIKE ? "
            "OR commitment_numbers LIKE ? OR sending_entity LIKE ? OR source_name LIKE ? "
            "OR sample_received_date LIKE ?)"
        )
        args.extend([like] * 8)
    sql = "SELECT * FROM records"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC, page_number LIMIT ?"
    args.append(max(1, min(limit, 5000)))
    with _LOCK, _connect(path) as conn:
        rows = conn.execute(sql, args).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        for k in LIST_FIELDS:
            item[k] = json.loads(item[k] or "[]")
        out.append(item)
    return out


def record_counts(session_id: str | None, path: Path | None = None) -> dict[str, int]:
    sql, args = "SELECT status, COUNT(*) AS n FROM records", ()
    if session_id is not None:
        sql, args = sql + " WHERE session_id = ?", (session_id,)
    with _LOCK, _connect(path) as conn:
        rows = conn.execute(sql + " GROUP BY status", args).fetchall()
    counts = {row["status"]: row["n"] for row in rows}
    counts["all"] = sum(counts.values())
    return counts


def find_duplicates(record: FormRecord, session_id: str, path: Path | None = None) -> list[dict]:
    """Other records of the same session that share a commitment or central number —
    the same form scanned twice, or a misread. Shown for review; never merged."""
    numbers = [(k, v) for k in LIST_FIELDS for v in record.fields[k].values if v and "?" not in v]
    found: dict[str, dict] = {}
    if not numbers:
        return []
    with _LOCK, _connect(path) as conn:
        for key, value in numbers:
            rows = conn.execute(
                f"SELECT record_id, batch_id, source_name, page_number FROM records "
                f"WHERE session_id = ? AND record_id != ? AND {key} LIKE ?",
                (session_id, record.record_id, f'%"{value}"%'),
            ).fetchall()
            for row in rows:
                hit = found.setdefault(row["record_id"], {**dict(row), "matches": []})
                hit["matches"].append(value)
    return list(found.values())
