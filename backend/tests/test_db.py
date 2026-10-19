"""Approved forms are kept in SQLite: one row per form, updated on re-approval."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from app import db, pipeline, store
from app.models import Batch, FormRecord, SourceFile

SOURCE = SourceFile(index=1, filename="8-11 מחובר2.pdf", kind="pdf", sha256="a" * 64)


def _record(batch_id: str) -> FormRecord:
    record = FormRecord.empty(batch_id, SOURCE, 2)
    values = {
        "first_name": ["ישראל"], "last_name": ["ישראלי"], "national_id": ["011223344"],
        "central_numbers": ["944963", "944964"], "sample_received_date": ["05/08/2026"],
        "sending_entity": ["פוריה"], "commitment_numbers": ["0012345"],
    }
    for key, vals in values.items():
        record.fields[key].values = vals
        record.fields[key].state = "approved"
    return record


def _page(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (600, 840), "white").save(path)
    return path


def test_approved_form_is_saved_with_all_seven_fields_and_its_page(tmp_path):
    page = _page(tmp_path / "page.png")
    db.save_approved(_record("b1"), SOURCE, page)
    rows = db.list_approved()
    assert len(rows) == 1
    row = rows[0]
    assert row["first_name"] == "ישראל" and row["last_name"] == "ישראלי"
    assert row["national_id"] == "011223344", "אפס מוביל נשמר"
    assert row["central_numbers"] == ["944963", "944964"], "כמה ערכים — רשימה"
    assert row["commitment_numbers"] == ["0012345"]
    assert row["sample_received_date"] == "05/08/2026" and row["sending_entity"] == "פוריה"
    assert row["source_file"] == "8-11 מחובר2.pdf" and row["page_number"] == 2
    assert Path(row["page_image"]).is_file(), "תמונת הטופס נשמרת"


def test_approving_the_same_form_again_updates_its_row(tmp_path):
    page = _page(tmp_path / "page.png")
    record = _record("b1")
    db.save_approved(record, SOURCE, page)
    first = db.list_approved()[0]
    record.fields["last_name"].values = ["כהן"]
    db.save_approved(record, SOURCE, page)
    rows = db.list_approved()
    assert len(rows) == 1, "אין שורה כפולה"
    assert rows[0]["last_name"] == "כהן"
    assert rows[0]["approved_at"] == first["approved_at"], "מועד האישור הראשון נשמר"


def test_verify_saves_to_the_database_and_a_db_failure_does_not_break_it(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.BATCHES_DIR", tmp_path / "batches")
    monkeypatch.setattr("app.store.BATCHES_DIR", tmp_path / "batches")
    monkeypatch.setattr("app.gold.GOLD_DIR", tmp_path / "gold")
    from app.api import app

    batch = Batch(batch_id="bat_db", status="completed")
    batch.sources.append(SOURCE)
    record = _record("bat_db")
    batch.records.append(record)
    _page(pipeline.page_image_path("bat_db", 1, 2))
    store.put(batch)
    client = TestClient(app)
    assert client.post(f"/api/records/{record.record_id}/verify").status_code == 200
    assert [r["record_id"] for r in client.get("/api/approved").json()] == [record.record_id]

    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("app.db.save_approved", broken)
    record.verified = False
    assert client.post(f"/api/records/{record.record_id}/verify").status_code == 200
    store.delete("bat_db")
