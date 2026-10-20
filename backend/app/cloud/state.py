"""Durable batch snapshots and jobs, independent of a particular web instance."""

from __future__ import annotations

import dataclasses
import json
import uuid

from psycopg.types.json import Jsonb

from app.models import Batch, SourceFile
from .postgres import connect


def decode(data: dict) -> Batch:
    from app.db import record_from_json

    data = dict(data)
    data["sources"] = [SourceFile(**s) for s in data["sources"]]
    record = lambda r: record_from_json(json.dumps(r))
    data["records"] = [record(r) for r in data["records"]]
    data["joined"] = {k: [record(r) for r in v] for k, v in data.get("joined", {}).items()}
    data["page_splits"] = {k: record(v) for k, v in data.get("page_splits", {}).items()}
    return Batch(**data)


def save(batch: Batch):
    with connect() as conn:
        conn.execute(
            "INSERT INTO batches(batch_id,payload) VALUES (%s,%s) "
            "ON CONFLICT(batch_id) DO UPDATE SET payload=excluded.payload,updated_at=now()",
            (batch.batch_id, Jsonb(dataclasses.asdict(batch))),
        )


def get(batch_id: str):
    with connect() as conn:
        row = conn.execute("SELECT payload FROM batches WHERE batch_id=%s", (batch_id,)).fetchone()
    return decode(row["payload"]) if row else None


def all_batches():
    with connect() as conn:
        rows = conn.execute("SELECT payload FROM batches ORDER BY updated_at").fetchall()
    return [decode(r["payload"]) for r in rows]


def batch_of(record_id: str):
    with connect() as conn:
        row = conn.execute(
            "SELECT batch_id FROM batches WHERE payload->'records' @> %s LIMIT 1",
            (Jsonb([{"record_id": record_id}]),),
        ).fetchone()
    return row["batch_id"] if row else None


def delete(batch_id: str):
    with connect() as conn:
        conn.execute("DELETE FROM pending_forms WHERE batch_id=%s", (batch_id,))
        conn.execute("DELETE FROM jobs WHERE batch_id=%s", (batch_id,))
        return conn.execute("DELETE FROM batches WHERE batch_id=%s", (batch_id,)).rowcount > 0


def enqueue(batch: Batch, kind="extract", record_id=None):
    # Snapshot and queue entry commit together. Only the worker can call the model.
    with connect() as conn:
        conn.execute(
            "INSERT INTO batches(batch_id,payload) VALUES (%s,%s) "
            "ON CONFLICT(batch_id) DO UPDATE SET payload=excluded.payload,updated_at=now()",
            (batch.batch_id, Jsonb(dataclasses.asdict(batch))),
        )
        conn.execute(
            "INSERT INTO jobs(job_id,batch_id,kind,record_id) VALUES (%s,%s,%s,%s)",
            (uuid.uuid4().hex, batch.batch_id, kind, record_id),
        )


def checkpoint(batch: Batch):
    # Artifacts must be durable before the snapshot advertises that a page is ready.
    from . import storage
    storage.flush()
    save(batch)


def interrupt_running():
    """Called only under the workspace lock: no other worker can still own a job.

    Never automatically repeat a potentially paid request after an uncertain crash.
    Partial results remain; users can explicitly start another reading.
    """
    with connect() as conn:
        jobs = conn.execute("SELECT * FROM jobs WHERE status='running'").fetchall()
        for job in jobs:
            batch = get(job["batch_id"])
            if batch:
                batch.status = "partial" if batch.records else "failed"
                batch.error = "השרת נעצר במהלך הקריאה. התוצאות שנשמרו זמינות; לא בוצעה קריאה חוזרת בתשלום."
                for page in batch.pages:
                    if page.get("status") in ("reading", "queued"):
                        page["status"] = "failed"
                for record in batch.records:
                    if record.error == "קורא שוב…":
                        record.error = "הקריאה הופסקה. אפשר לבקש קריאה חוזרת."
                save(batch)
                from app import pipeline
                pipeline.park_batch(batch)
        conn.execute(
            "UPDATE jobs SET status='interrupted',finished_at=now(),error='Worker interrupted' WHERE status='running'"
        )
