"""Forms waiting for approval are kept only when needed, once each, and leave when approved."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import db, pipeline, store
from app.models import Batch, FormRecord, SourceFile
from app.providers.base import ExtractionError


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr("app.store.BATCHES_DIR", tmp_path / "batches")
    monkeypatch.setattr("app.config.BATCHES_DIR", tmp_path / "batches")
    monkeypatch.setattr("app.gold.save", lambda *a, **k: None)


def _batch(batch_id: str, *names: str, shade: int = 0) -> Batch:
    batch = Batch(batch_id=batch_id, status="completed")
    source = SourceFile(index=1, filename=f"{batch_id}.pdf", kind="pdf", page_count=len(names))
    batch.sources.append(source)
    for number, name in enumerate(names, start=1):
        page = store.source_dir(batch_id, 1) / "pages" / f"page-{number:03d}.png"
        page.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (60, 80), (255, 255 - number, 255 - shade)).save(page)
        record = FormRecord.empty(batch_id, source, number)
        record.fields["first_name"].values = [name]
        record.fields["first_name"].flag("illegible")
        batch.records.append(record)
    store.put(batch)
    return batch


def _client() -> TestClient:
    from app.api import app
    return TestClient(app)


def _names() -> list[str]:
    return [r["first_name"] for r in db.list_pending()["rows"]]


def test_nothing_is_kept_until_it_is_needed():
    _batch("bat_a", "שרה", "דוד")
    assert _names() == []  # רק קריאה — לא נשמר כלום


def test_closing_the_page_keeps_the_unapproved_forms():
    _batch("bat_a", "שרה", "דוד")
    assert _client().post("/api/batches/bat_a/park").json() == {"waiting": 2}
    assert sorted(_names()) == ["דוד", "שרה"]


def test_a_new_upload_keeps_the_previous_unfinished_forms(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "start", lambda batch_id: None)  # בלי קריאה — רק ההעלאה עצמה
    _batch("bat_a", "שרה")
    page = tmp_path / "new.png"
    Image.new("RGB", (40, 56), "white").save(page)
    with page.open("rb") as fh:
        assert _client().post("/api/batches", files=[("files", ("new.png", fh, "image/png"))]).status_code == 200
    assert _names() == ["שרה"]


def test_keeping_twice_updates_and_never_duplicates():
    batch = _batch("bat_a", "שרה")
    pipeline.park_batch(batch)
    batch.records[0].fields["first_name"].values = ["שרה מתוקנת"]
    pipeline.park_batch(batch)
    assert _names() == ["שרה מתוקנת"]
    again = _batch("bat_b", "שרה")  # אותו עמוד בהעלאה נוספת — מחליף, לא מוסיף
    pipeline.park_batch(again)
    assert db.pending_count() == 1


def test_approving_takes_the_form_out_of_waiting():
    batch = _batch("bat_a", "שרה", "דוד")
    pipeline.park_batch(batch)
    record = batch.records[0]
    client = _client()
    client.patch(f"/api/records/{record.record_id}", json={"field": "first_name", "approve": True})
    for key in record.fields:
        client.patch(f"/api/records/{record.record_id}", json={"field": key, "approve": True})
    assert client.post(f"/api/records/{record.record_id}/verify").status_code == 200
    assert _names() == ["דוד"]


def test_a_waiting_form_opens_after_a_restart_without_reading():
    batch = _batch("bat_a", "שרה", "דוד")
    pipeline.park_batch(batch)
    wanted = batch.records[1].record_id
    store._BATCHES.clear()  # כמו הפעלה מחדש: הזיכרון ריק, הקבצים בתיקיית העבודה נשארים

    opened = _client().post(f"/api/pending/{wanted}/open")

    assert opened.status_code == 200
    body = opened.json()
    assert body["focus"] == wanted and [r["fields"]["first_name"]["values"] for r in body["records"]] == [["שרה"], ["דוד"]]


def test_server_shutdown_keeps_everything_waiting():
    _batch("bat_a", "שרה")
    _batch("bat_b", "דוד", shade=7)
    from app.api import park_everything
    park_everything()
    assert sorted(_names()) == ["דוד", "שרה"]


def test_tests_can_never_reach_the_paid_model(tmp_path):
    """The safety net in conftest: a real reader built in a test cannot reach Anthropic."""
    import time

    from app.providers.claude_vision import ClaudeExtractionProvider

    page = tmp_path / "p.png"
    Image.new("RGB", (40, 56), "white").save(page)
    reader = ClaudeExtractionProvider(api_key="sk-test")
    assert "127.0.0.1" in str(reader._client.base_url)
    started = time.perf_counter()
    with pytest.raises(Exception):
        reader.extract(page)
    assert time.perf_counter() - started < 20


def test_measurement_always_asks_the_reader_not_the_saved_values(tmp_path, monkeypatch):
    """The bug Noa asked about: measuring approved pages must not return the approved values."""
    batch = _batch("bat_m", "שרה")
    record = batch.records[0]
    client = _client()
    for key in record.fields:
        client.patch(f"/api/records/{record.record_id}", json={"field": key, "approve": True})
    client.post(f"/api/records/{record.record_id}/verify")
    page = pipeline.page_image_path("bat_m", 1, 1)

    class Reader:
        model = "stub"
        calls = 0

        def extract(self, image_path: Path):
            Reader.calls += 1
            raise ExtractionError("קריאה מדומה")

    source = SourceFile(index=1, filename="x.pdf")
    from_system = pipeline.read_form(pipeline.Reader(Reader()), page, "b", source, 1)
    assert Reader.calls == 0 and from_system.from_store  # למזכירה: מהמערכת, בלי מודל
    measured = pipeline.read_form(pipeline.Reader(Reader()), page, "b", source, 1, use_saved=False)
    assert Reader.calls == 1 and not measured.from_store  # למדידה: תמיד הקורא עצמו
