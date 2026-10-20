"""In-memory batch store.

Deliberately not a database. This slice proves the path upload → model →
review → Excel; persistence across restarts is the next sprint's job, and
pretending otherwise would hide the gap. Everything disappears on restart.
"""

from __future__ import annotations

import shutil
import threading
from pathlib import Path

from app.config import BATCHES_DIR
from app.models import Batch, FormRecord

_LOCK = threading.Lock()
_BATCHES: dict[str, Batch] = {}


def put(batch: Batch) -> None:
    with _LOCK:
        _BATCHES[batch.batch_id] = batch


def get(batch_id: str) -> Batch | None:
    with _LOCK:
        return _BATCHES.get(batch_id)


def all_batches() -> list[Batch]:
    with _LOCK:
        return list(_BATCHES.values())


def find_record(record_id: str) -> tuple[Batch, FormRecord] | None:
    with _LOCK:
        for batch in _BATCHES.values():
            for record in batch.records:
                if record.record_id == record_id:
                    return batch, record
    return None


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
    """Remove a batch and every derivative file it produced."""
    with _LOCK:
        existed = _BATCHES.pop(batch_id, None) is not None
    shutil.rmtree(BATCHES_DIR / batch_id, ignore_errors=True)
    return existed
