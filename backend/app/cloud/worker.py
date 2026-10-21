"""One durable worker for the shared demo workspace. No HTTP request runs the model."""

from __future__ import annotations

import logging
import signal
import threading

from app import pipeline, store
from . import enabled, state, storage
from .postgres import connect, writer_lock

STOP = threading.Event()
log = logging.getLogger(__name__)


def ingest(batch):
    from app.api import _store_source

    with connect() as conn:
        tickets = conn.execute(
            "SELECT * FROM uploads WHERE batch_id=%s ORDER BY position", (batch.batch_id,),
        ).fetchall()
    if not tickets:
        storage.restore_prefix(f"work/batches/{batch.batch_id}/")
        return
    sources = []
    for index, ticket in enumerate(tickets, 1):
        data = storage.bucket().download(ticket["object_key"])
        if len(data) != ticket["size"]:
            raise ValueError("Uploaded file size differs from the declared size")
        sources.append(_store_source(batch.batch_id, index, ticket["filename"], data))
    batch.sources = sources
    state.checkpoint(batch)
    # Inputs are durable in artifacts before the temporary upload objects are removed.
    storage.bucket().remove([t["object_key"] for t in tickets])
    # Retain tickets until the signed URL expires, for idempotent finalization
    # and cleanup of any upload replayed using a still-valid URL.


def run_once() -> bool:
    with writer_lock() as acquired:
        if not acquired:
            return False
        storage.reset_scratch()
        state.interrupt_running()
        storage.collect_garbage()
        with connect() as conn:
            job = conn.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED").fetchone()
            if not job:
                storage.reset_scratch()
                return False
            conn.execute("UPDATE jobs SET status='running' WHERE job_id=%s", (job["job_id"],))
        store._BATCHES.clear()
        batch = store.get(job["batch_id"])
        try:
            if batch is None:
                raise ValueError("Batch missing")
            if job["kind"] == "extract":
                ingest(batch)
                if not batch.accepted_sources:
                    raise ValueError("No supported files")
                pipeline._run(batch.batch_id)
            elif job["kind"] == "reread":
                storage.restore_prefix(f"work/batches/{batch.batch_id}/")
                index = next(i for i, r in enumerate(batch.records) if r.record_id == job["record_id"])
                old = batch.records[index]
                page = next((p for p in batch.pages if p.get("record_id") == old.record_id), None)
                pipeline._reread_run(batch, index, old, page)
            else:
                raise ValueError("Unknown job kind")
            pipeline.park_batch(batch)
            state.checkpoint(batch)
            with connect() as conn:
                conn.execute("UPDATE jobs SET status='done',finished_at=now() WHERE job_id=%s", (job["job_id"],))
        except Exception:
            # No document contents, keys, provider payloads or original filenames in logs.
            log.error("Cloud job failed: %s", job["job_id"])
            if batch:
                batch.status = "partial" if batch.records else "failed"
                batch.error = "העיבוד הופסק. התוצאות שנשמרו זמינות; לא בוצעה קריאה חוזרת אוטומטית."
                state.checkpoint(batch)
            with connect() as conn:
                conn.execute("UPDATE jobs SET status='failed',finished_at=now(),error='Processing failed' WHERE job_id=%s", (job["job_id"],))
        finally:
            store._BATCHES.clear()
            pipeline._READ_CACHE.clear()
            storage.reset_scratch()
        return True


def main():
    if not enabled():
        raise RuntimeError("Worker requires CLOUD_MODE=1")
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    signal.signal(signal.SIGINT, lambda *_: STOP.set())
    while not STOP.is_set():
        try:
            worked = run_once()
        except Exception as exc:
            # Report diagnostic codes only: exception messages can contain secrets.
            reason = "unspecified"
            message = str(exc).lower()
            for marker, code in (
                ("password authentication failed", "database_auth_failed"),
                ("tenant or user not found", "database_tenant_not_found"),
                ("connection timeout", "database_connection_timeout"),
                ("connection refused", "database_connection_refused"),
            ):
                if marker in message:
                    reason = code
                    break
            log.error("Cloud worker temporarily unavailable (%s, sqlstate=%s, reason=%s)",
                      type(exc).__name__, getattr(exc, "sqlstate", None), reason)
            worked = False
        if not worked:
            STOP.wait(5)


if __name__ == "__main__":
    main()
