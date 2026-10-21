"""Hard rules (Noa, 21/10/2026): a value that breaks one can't be saved, approved or exported."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import rules, store
from app.models import STATE_APPROVED, STATE_REVIEW, Batch, FormRecord, SourceFile

TODAY = date(2026, 10, 21)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr("app.store.BATCHES_DIR", tmp_path / "batches")
    monkeypatch.setattr("app.config.BATCHES_DIR", tmp_path / "batches")
    monkeypatch.setattr("app.gold.save", lambda *a, **k: None)
    monkeypatch.setattr("app.db.save_approved", lambda *a, **k: None)  # לא נוגעים במסד


def _record(**values) -> FormRecord:
    batch = Batch(batch_id="bat_rules", status="completed")
    source = SourceFile(index=1, filename="f.pdf", kind="pdf", page_count=1)
    batch.sources.append(source)
    page = store.source_dir("bat_rules", 1) / "pages" / "page-001.png"
    page.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (60, 80), "white").save(page)
    record = FormRecord.empty("bat_rules", source, 1)
    for key, vals in values.items():
        record.fields[key].values = list(vals)
    batch.records.append(record)
    store.put(batch)
    return record


def _client() -> TestClient:
    from app.api import app
    return TestClient(app)


@pytest.mark.parametrize("key, values", [
    ("national_id", ["011223344", "11223344"]),       # שתי ת"ז לאדם אחד
    ("national_id", ["012345678"]),                    # ספרת ביקורת
    ("national_id", ["1100223344"]),                   # ארוכה מ-9
    ("national_id", ["11-223344"]),
    ("sample_received_date", ["לא מובן"]),
    ("sample_received_date", ["08/2026"]),
    ("sample_received_date", ["31/02/2026"]),
    ("sample_received_date", ["01/01/2027"]),          # בעתיד
    ("sample_received_date", ["25/08/????"]),
    ("first_name", ["לא ברור"]),
    ("first_name", ["שרה", "דוד"]),
    ("last_name", ["כהן2"]),
    ("commitment_numbers", ["לא ניתן לזהות"]),
    ("commitment_numbers", ["אין"]),
    ("commitment_numbers", ["ראה מצורף"]),             # בלי אף ספרה
    ("commitment_numbers", ["12?4"]),
    ("central_numbers", ["אין"]),
    ("sending_entity", ["-"]),
])
def test_breaks_a_rule(key, values):
    assert rules.problems(key, values, today=TODAY)


@pytest.mark.parametrize("key, values", [
    ("national_id", ["011223344"]),
    ("national_id", ["11223344"]),        # בלי אפס מוביל — תקינה
    ("national_id", ["0011223344"]),      # אפס מוביל נוסף — תקינה
    ("sample_received_date", ["24/08/2026"]),
    ("first_name", ["ט.מ. בילוד"]),
    ("last_name", ["בן-דוד"]),
    ("first_name", ["לאה"]),             # מתחיל ב"לא" אבל הוא שם
    ("commitment_numbers", ["X132767", "2657"]),  # כמה מספרים מותר
    # אין פורמט רשמי למספרים — כל תו מותר, רק שתהיה ספרה (החלטת נועה, 21/10/2026)
    ("commitment_numbers", ["12-34", "2026/1234", "AB12CD", "מ-12345"]),
    ("central_numbers", ["94-80 23"]),
    ("first_name", ["ישראל מאיר"]),       # שם של כמה מילים = ערך אחד
    ("last_name", ["ידלר גולדברג"]),
    ("central_numbers", ["948023", "947828"]),
    ("sending_entity", ['צה"ל']),          # לא ברשימה — נשאר כתום, לא חסום
    ("national_id", []),                   # ריק = "חסר", מאושר במפורש
])
def test_keeps_a_valid_value(key, values):
    assert rules.problems(key, values, today=TODAY) == []


def test_a_broken_value_is_not_saved():
    record = _record(national_id=["011223344"])
    res = _client().patch(f"/api/records/{record.record_id}",
                          json={"field": "sample_received_date", "values": ["לא מובן"]})
    assert res.status_code == 422 and "ריק" in res.json()["detail"]
    assert record.fields["sample_received_date"].values == []
    res = _client().patch(f"/api/records/{record.record_id}",
                          json={"field": "national_id", "values": ["011223344", "11223344"]})
    assert res.status_code == 422 and "ערך אחד" in res.json()["detail"]
    assert record.fields["national_id"].values == ["011223344"]


def test_a_broken_reading_cannot_be_approved():
    record = _record(national_id=["011223344", "11223344"])
    record.fields["national_id"].state = STATE_REVIEW
    res = _client().patch(f"/api/records/{record.record_id}", json={"field": "national_id", "approve": True})
    assert res.status_code == 409
    assert record.fields["national_id"].state == STATE_REVIEW
    blocked = _client().get("/api/batches/bat_rules").json()["records"][0]["fields"]["national_id"]["blocked"]
    assert blocked and "ערך אחד" in blocked[0]


def test_the_form_is_not_approved_while_a_field_breaks_a_rule():
    record = _record(national_id=["012345678"])
    for field in record.fields.values():
        field.state = STATE_APPROVED  # גם אם השדה ירוק — הכלל קובע
    res = _client().post(f"/api/records/{record.record_id}/verify")
    assert res.status_code == 409 and "ביקורת" in res.json()["detail"]
    assert record.verified is False


def test_empty_is_the_only_way_to_say_there_is_no_value():
    record = _record()
    res = _client().patch(f"/api/records/{record.record_id}", json={"field": "sample_received_date", "values": []})
    assert res.status_code == 200
    res = _client().patch(f"/api/records/{record.record_id}", json={"field": "sample_received_date", "approve": True})
    assert res.status_code == 200 and record.fields["sample_received_date"].state == STATE_APPROVED


def test_a_form_from_the_system_that_breaks_a_rule_comes_back_orange():
    record = _record(national_id=["011223344", "11223344"], first_name=["שרה"])
    for field in record.fields.values():
        field.state = STATE_APPROVED
    record.verified = True
    assert rules.reopen_broken(record) is True
    assert record.verified is False
    assert record.fields["national_id"].state == STATE_REVIEW and "breaks_rule" in record.fields["national_id"].reasons
    assert record.fields["first_name"].values == ["שרה"] and record.fields["first_name"].state == STATE_APPROVED
    assert record.fields["national_id"].values == ["011223344", "11223344"]  # לא משנים ערכים


def test_a_saved_row_is_marked_not_changed():
    assert rules.row_breaks_rules({"national_id": "011223344 | 11223344", "commitment_number": "7001"})
    assert not rules.row_breaks_rules({"national_id": "011223344", "central_numbers": "948023 | 947828",
                                       "commitment_number": "X132767", "sample_received_date": "24/08/2026"})


def test_a_placeholder_read_by_the_model_is_orange():
    from app.validators import validate_record
    record = _record(sample_received_date=["לא ברור"])
    validate_record(record, today=TODAY)
    assert "not_a_value" in record.fields["sample_received_date"].reasons
