"""Local in-memory batches; cloud mode uses PostgreSQL and a request cache."""

from __future__ import annotations

import shutil
import threading
import re
from pathlib import Path

from app.config import BATCHES_DIR
from app.models import Batch, FormRecord
from app.cloud import enabled as cloud_enabled

_LOCK = threading.Lock()
_BATCHES: dict[str, Batch] = {}
HYDRATE = False  # Only serialized cloud mutations restore complete batch files.


def put(batch: Batch) -> None:
    with _LOCK:
        _BATCHES[batch.batch_id] = batch


def get(batch_id: str) -> Batch | None:
    with _LOCK:
        if cloud_enabled() and batch_id not in _BATCHES:
            from app.cloud import state
            batch = state.get(batch_id)
            if batch is not None:
                _BATCHES[batch_id] = batch
                if HYDRATE:
                    from app.cloud.storage import restore_prefix
                    restore_prefix(f"work/batches/{batch_id}/")
        return _BATCHES.get(batch_id)


def all_batches() -> list[Batch]:
    if cloud_enabled():
        from app.cloud import state
        for batch in state.all_batches():
            with _LOCK:
                _BATCHES.setdefault(batch.batch_id, batch)
    with _LOCK:
        return list(_BATCHES.values())


def find_record(record_id: str) -> tuple[Batch, FormRecord] | None:
    if cloud_enabled():
        from app.cloud import state
        batch_id = state.batch_of(record_id)
        if batch_id:
            get(batch_id)
    with _LOCK:
        for batch in _BATCHES.values():
            for record in batch.records:
                if record.record_id == record_id:
                    return batch, record
    return None


def batch_dir(batch_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", batch_id):
        raise ValueError("Invalid batch identifier")
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
    if not re.fullmatch(r"[A-Za-z0-9_-]+", batch_id):
        raise ValueError("Invalid batch identifier")
    with _LOCK:
        existed = _BATCHES.pop(batch_id, None) is not None
    if cloud_enabled():
        from app.cloud import state, storage
        storage.remove_prefix(f"work/batches/{batch_id}/")
        existed = state.delete(batch_id) or existed
    shutil.rmtree(BATCHES_DIR / batch_id, ignore_errors=True)
    return existed
