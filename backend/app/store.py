"""Batch store: in memory for speed, written through to SQLite so nothing is lost.

Every change — a new record, a fix, an approval — is saved at once (`persist`).
After a restart `restore` loads every batch back; a batch that was still being
read starts again, and the pages already read come back from the saved answers
for free.

Each batch belongs to the browser session that uploaded it. A session sees and
changes only its own batches; anything else is reported as "not found", so ids
of other sessions cannot even be probed.
"""

from __future__ import annotations

import shutil
import threading
import traceback
from pathlib import Path

from app import db
from app.config import BATCHES_DIR
from app.models import Batch, FormRecord, now

_LOCK = threading.Lock()
_BATCHES: dict[str, Batch] = {}


def put(batch: Batch) -> None:
    with _LOCK:
        _BATCHES[batch.batch_id] = batch


def get(batch_id: str, session_id: str | None = None) -> Batch | None:
    """The batch, or None. With `session_id`, only when that session owns it."""
    with _LOCK:
        batch = _BATCHES.get(batch_id)
    if batch is None or not owns(batch, session_id):
        return None
    return batch


def all_batches() -> list[Batch]:
    with _LOCK:
        return list(_BATCHES.values())


def cost_today() -> float:
    """Dollars spent on readings today (UTC), across every session — for the daily cap."""
    today = now()[:10]
    return sum(r.cost_usd or 0.0 for b in all_batches() for r in b.records if r.created_at[:10] == today)


def owns(batch: Batch, session_id: str | None) -> bool:
    # session_id=None — קוד פנימי (העובד ברקע). אצווה בלי בעלים — נוצרה בקוד, לא דרך ה-API.
    return session_id is None or not batch.session_id or batch.session_id == session_id


def find_record(record_id: str, session_id: str | None = None) -> tuple[Batch, FormRecord] | None:
    with _LOCK:
        batches = list(_BATCHES.values())
    for batch in batches:
        if not owns(batch, session_id):
            continue
        for record in batch.records:
            if record.record_id == record_id:
                return batch, record
    return None


def persist(batch: Batch) -> bool:
    """Save the batch and its records. A failure is logged and reported, never
    raised: the screen keeps working, and the next change saves again."""
    try:
        db.save_batch(batch)
        return True
    except Exception:  # noqa: BLE001 - שמירה שנכשלה לא מפילה את העבודה
        traceback.print_exc()
        return False


def restore() -> list[Batch]:
    """Load every saved batch into memory. Returns the ones that were still being
    read when the server stopped, so the caller can start them again."""
    unfinished = []
    try:
        batches = db.load_batches()
    except Exception:  # noqa: BLE001 - מסד פגום לא מונע מהשרת לעלות
        traceback.print_exc()
        return unfinished
    for batch in batches:
        put(batch)
        if batch.status in ("queued", "processing"):
            unfinished.append(batch)
    return unfinished


def batch_dir(batch_id: str) -> Path:
    path = BATCHES_DIR / batch_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def source_dir(batch_id: str, source_index: int) -> Path:
    """Each dropped file gets its own folder, so page-001.png of one file
    cannot collide with page-001.png of another."""
    path = batch_dir(batch_id) / "sources" / str(source_index)
    path.mkdir(parents=True, exist_ok=True)
    return path


def delete(batch_id: str) -> bool:
    """Remove a batch, its database rows and every derivative file it produced."""
    with _LOCK:
        existed = _BATCHES.pop(batch_id, None) is not None
    shutil.rmtree(BATCHES_DIR / batch_id, ignore_errors=True)
    try:
        db.delete_batch(batch_id)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
    return existed
