"""A value with "?" (a character nobody read) can't be approved — only fixed (Noa, 20/10/2026)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import store
from app.models import STATE_APPROVED, STATE_REVIEW, Batch, FormRecord, SourceFile
from app.validators import validate_record


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr("app.store.BATCHES_DIR", tmp_path / "batches")
    monkeypatch.setattr("app.gold.save", lambda *a, **k: None)


def _form(**values) -> FormRecord:
    batch = Batch(batch_id="bat_q", status="completed")
    source = SourceFile(index=1, filename="q.pdf", kind="pdf", page_count=1)
    batch.sources.append(source)
    page = store.source_dir("bat_q", 1) / "pages" / "page-001.png"
    page.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 56), "white").save(page)
    record = FormRecord.empty("bat_q", source, 1)
    for key, vals in values.items():
        record.fields[key].values = vals
    validate_record(record)
    batch.records.append(record)
    store.put(batch)
    return record


def _client() -> TestClient:
    from app.api import app
    return TestClient(app)


def test_a_value_with_a_question_mark_starts_orange():
    record = _form(commitment_numbers=["1?007"])
    field = record.fields["commitment_numbers"]
    assert field.state == STATE_REVIEW and "unknown_char" in field.reasons


def test_approving_a_field_with_a_question_mark_is_refused():
    record = _form(commitment_numbers=["1?007"])
    answer = _client().patch(f"/api/records/{record.record_id}", json={"field": "commitment_numbers", "approve": True})
    assert answer.status_code == 409 and "?" in answer.json()["detail"]
    assert record.fields["commitment_numbers"].state == STATE_REVIEW


def test_typing_a_value_that_still_has_a_question_mark_stays_orange():
    record = _form(first_name=["ש?ל"])
    _client().patch(f"/api/records/{record.record_id}", json={"field": "first_name", "values": ["ש?לום"]})
    assert record.fields["first_name"].state == STATE_REVIEW


def test_fixing_the_value_allows_approval():
    record = _form(commitment_numbers=["1?007"])
    client = _client()
    client.patch(f"/api/records/{record.record_id}", json={"field": "commitment_numbers", "values": ["11001"]})
    assert record.fields["commitment_numbers"].state == STATE_APPROVED


def test_the_form_cannot_be_approved_while_a_question_mark_remains():
    record = _form(first_name=["שרה"], commitment_numbers=["1?007"])
    for field in record.fields.values():  # גם אם כל השדות סומנו אחרת — השרת עדיין מסרב
        field.state = STATE_APPROVED
    answer = _client().post(f"/api/records/{record.record_id}/verify")
    assert answer.status_code == 409 and "?" in answer.json()["detail"]
    assert not record.verified
