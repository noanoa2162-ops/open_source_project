"""A form already in the system comes back with its saved values (Noa, 20/10/2026)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import db, pipeline, store
from app.models import STATE_APPROVED, STATE_REVIEW, Batch, SourceFile
from app.providers.base import RawExtraction

TODAY = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
RANGE = {"start": TODAY.isoformat(), "end": (TODAY + timedelta(days=1)).isoformat()}
SARA = {"first_name": ["שרה"], "last_name": ["כהן"], "national_id": ["012345674"],
        "commitment_numbers": ["7001"], "sending_entity": ["מכבי"]}


class PerFile:
    """Stands in for the reader: the values for each uploaded file, by its position."""

    model = "stub"
    name = "stub"

    def __init__(self, by_file: dict[int, dict]) -> None:
        self.by_file = by_file
        self.calls = 0

    def extract(self, image_path: Path) -> RawExtraction:
        self.calls += 1
        return RawExtraction(values=self.by_file[int(image_path.parent.parent.name)], note="", model="stub")


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr("app.store.BATCHES_DIR", tmp_path / "batches")
    monkeypatch.setattr("app.config.BATCHES_DIR", tmp_path / "batches")
    monkeypatch.setattr("app.gold.save", lambda *a, **k: None)


def _png(tmp_path: Path, name: str, shade: int, size=(60, 80)) -> Path:
    path = tmp_path / name
    Image.new("RGB", size, (255, 255 - shade, 255)).save(path)
    return path


def _upload(monkeypatch, batch_id: str, files: list[Path], provider) -> Batch:
    monkeypatch.setattr(pipeline, "build_reader", lambda: (provider, ""))
    batch = Batch(batch_id=batch_id)
    for position, path in enumerate(files, start=1):
        batch.sources.append(SourceFile(index=position, filename=path.name, kind="png"))
        (store.source_dir(batch_id, position) / "source").write_bytes(path.read_bytes())
    store.put(batch)
    pipeline._process(batch_id)
    return batch


def _client() -> TestClient:
    from app.api import app
    return TestClient(app)


def _approve_all_and_verify(record) -> None:
    client = _client()
    for key in record.fields:
        client.patch(f"/api/records/{record.record_id}", json={"field": key, "approve": True})
    assert client.post(f"/api/records/{record.record_id}/verify").status_code == 200


def _first_approval(tmp_path, monkeypatch):
    """Sara's form read, her first name fixed by the secretary, and approved."""
    batch = _upload(monkeypatch, "bat_one", [_png(tmp_path, "sara.png", 1)], PerFile({1: SARA}))
    record = batch.records[0]
    _client().patch(f"/api/records/{record.record_id}", json={"field": "first_name", "values": ["שרה רבקה"]})
    _approve_all_and_verify(record)
    return record


def test_same_page_again_comes_from_the_system_without_the_model(tmp_path, monkeypatch):
    _first_approval(tmp_path, monkeypatch)
    reader = PerFile({})  # כל קריאה למודל הייתה נכשלת

    again = _upload(monkeypatch, "bat_two", [_png(tmp_path, "sara-again.png", 1)], reader).records[0]

    assert reader.calls == 0 and again.cost_usd == 0
    assert again.fields["first_name"].values == ["שרה רבקה"]  # הערך שהמזכירה תיקנה, לא הקריאה
    assert all(f.state == STATE_APPROVED and not f.reasons for f in again.fields.values())
    assert again.verified and again.from_store


def test_same_form_from_another_file_gets_the_saved_values(tmp_path, monkeypatch):
    _first_approval(tmp_path, monkeypatch)
    misread = {**SARA, "first_name": ["שרח"], "national_id": ["12345674"]}  # בלי האפס המוביל

    again = _upload(monkeypatch, "bat_two", [_png(tmp_path, "sara-photo.png", 40)], PerFile({1: misread})).records[0]

    assert again.fields["first_name"].values == ["שרה רבקה"]
    assert again.fields["national_id"].values == ["012345674"]
    assert again.verified and again.from_store
    assert all(f.state == STATE_APPROVED for f in again.fields.values())


def test_same_commitment_with_another_id_is_not_the_same_form(tmp_path, monkeypatch):
    _first_approval(tmp_path, monkeypatch)
    other = {**SARA, "first_name": ["דוד"], "national_id": ["000000018"]}

    again = _upload(monkeypatch, "bat_two", [_png(tmp_path, "david.png", 40)], PerFile({1: other})).records[0]

    assert again.fields["first_name"].values == ["דוד"]
    assert not again.verified and not again.from_store


def test_approving_the_new_version_keeps_one_form(tmp_path, monkeypatch):
    _first_approval(tmp_path, monkeypatch)
    again = _upload(monkeypatch, "bat_two", [_png(tmp_path, "sara-photo.png", 40)], PerFile({1: SARA})).records[0]
    client = _client()
    client.patch(f"/api/records/{again.record_id}", json={"field": "last_name", "values": ["כהן-לוי"]})
    assert client.post(f"/api/records/{again.record_id}/verify").status_code == 200

    rows = db.list_rows(**RANGE)["rows"]
    assert len(rows) == 1  # רשומה אחת — האחרונה
    assert rows[0]["last_name"] == "כהן-לוי" and rows[0]["source_name"] == "sara-photo.png"


def test_changing_a_value_requires_approving_the_form_again(tmp_path, monkeypatch):
    _first_approval(tmp_path, monkeypatch)
    again = _upload(monkeypatch, "bat_two", [_png(tmp_path, "sara-again.png", 1)], PerFile({})).records[0]
    client = _client()

    client.patch(f"/api/records/{again.record_id}", json={"field": "first_name", "approve": True})
    assert again.verified  # אישור בלי שינוי — עדיין מאושר
    client.patch(f"/api/records/{again.record_id}", json={"field": "first_name", "values": ["שרה רבקה"]})
    assert again.verified  # אותו ערך בדיוק — אין שינוי
    client.patch(f"/api/records/{again.record_id}", json={"field": "first_name", "values": ["שרית"]})
    assert not again.verified  # שינוי — חובה לאשר שוב


def test_halves_join_first_then_come_from_the_system(tmp_path, monkeypatch):
    _first_approval(tmp_path, monkeypatch)
    top = {"first_name": ["שרה"], "last_name": ["כהן"], "national_id": ["012345674"], "sending_entity": ["מכבי"]}
    bottom = {"commitment_numbers": ["7001"], "sending_entity": ["מכבי"]}

    batch = _upload(monkeypatch, "bat_two", [_png(tmp_path, "bottom.png", 60), _png(tmp_path, "top.png", 70)],
                    PerFile({1: bottom, 2: top}))

    assert len(batch.records) == 1  # החצאים חוברו קודם
    joined = batch.records[0]
    assert joined.record_id in batch.joined
    assert joined.from_store and joined.fields["first_name"].values == ["שרה רבקה"]


def test_a_form_never_approved_is_read_as_usual(tmp_path, monkeypatch):
    batch = _upload(monkeypatch, "bat_new", [_png(tmp_path, "new.png", 5)], PerFile({1: SARA}))
    record = batch.records[0]
    assert not record.from_store and not record.verified
    assert record.fields["first_name"].values == ["שרה"]
    assert any(f.state == STATE_REVIEW for f in record.fields.values())  # שדות חסרים — כתומים כרגיל
