"""🙋 עזרה (Noa, 21/10/2026): the secretary asks a manager about one field. The message holds only
a link; the link shows only that field; the field is not approved while waiting; she decides."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import db, store
from app.models import STATE_APPROVED, STATE_REVIEW, Batch, FormRecord, SourceFile


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr("app.store.BATCHES_DIR", tmp_path / "batches")
    monkeypatch.setattr("app.config.BATCHES_DIR", tmp_path / "batches")
    monkeypatch.setattr("app.gold.save", lambda *a, **k: None)


def _record() -> FormRecord:
    """A form with two IDs read on it — the secretary doesn't know which is right."""
    batch = Batch(batch_id="bat_help", status="completed")
    source = SourceFile(index=1, filename="f.pdf", kind="pdf", page_count=1)
    batch.sources.append(source)
    page = store.source_dir("bat_help", 1) / "pages" / "page-001.png"
    page.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (200, 300), "white").save(page)
    record = FormRecord.empty("bat_help", source, 1)
    for key, values in {"first_name": ["שרה"], "last_name": ["כהן"], "national_id": ["011223344", "11223354"],
                        "central_numbers": ["948023"], "sample_received_date": ["24/08/2026"],
                        "sending_entity": ["מכבי"], "commitment_numbers": ["7001"]}.items():
        record.fields[key].values = values
        record.fields[key].state = STATE_APPROVED
    ids = record.fields["national_id"]
    ids.state, ids.reasons = STATE_REVIEW, ["multiple_values"]
    ids.value_regions = [(0.1, 0.1, 0.5, 0.2), (0.1, 0.5, 0.5, 0.6)]
    batch.records.append(record)
    store.put(batch)
    return record


def _client() -> TestClient:
    from app.api import app
    return TestClient(app)


def _ask(client, record) -> str:
    res = client.post(f"/api/records/{record.record_id}/help",
                      json={"field": "national_id", "question": "איזה נכון?", "asker": "רחל"})
    assert res.status_code == 200, res.text
    return res.json()["help_token"]


def test_the_link_shows_only_this_field():
    client, record = _client(), _record()
    token = _ask(client, record)
    data = client.get(f"/api/help/{token}").json()
    assert data["label"] == "תעודת זהות" and data["options"] == ["011223344", "11223354"]
    assert data["crops"] == 2 and data["status"] == "open" and data["asker"] == "רחל"
    # אין בו שם המטופל, שדות אחרים, או מזהה של הטופס/ההעלאה
    text = str(data)
    assert "שרה" not in text and "כהן" not in text and record.record_id not in text and "bat_help" not in text
    assert client.get(f"/api/help/{token}/crops/0").headers["content-type"] == "image/png"
    assert client.get(f"/api/help/{token}/crops/5").status_code == 404
    page = client.get(f"/help/{token}")
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store"


def test_the_manager_sees_the_crop_the_secretary_dragged_to():
    """She moved the zoom because the engine's place was off: the link shows exactly her area."""
    client, record = _client(), _record()
    res = client.post(f"/api/records/{record.record_id}/help",
                      json={"field": "national_id", "views": [[0.0, 0.5, 0.5, 1.0], "not a box"]})
    token = res.json()["help_token"]
    with Image.open(db.HELP_DIR / f"{token}-0.png") as moved, Image.open(db.HELP_DIR / f"{token}-1.png") as kept:
        assert moved.size == (200, 300)  # 100x150 בדיוק מהעמוד (200x300), מוגדל פי 2 — בלי שוליים
        assert kept.size != moved.size  # ערך שני: לא הוזז (קלט לא תקין) — התקריב הרגיל על השדה


def test_a_wrong_link_finds_nothing():
    client = _client()
    assert client.get("/api/help/not-a-real-token").status_code == 404
    assert client.get("/help/not-a-real-token").status_code == 404


def test_asking_twice_keeps_one_request():
    client, record = _client(), _record()
    assert _ask(client, record) == _ask(client, record)


def test_waiting_for_help_blocks_approval():
    client, record = _client(), _record()
    _ask(client, record)
    field = client.get("/api/batches/bat_help").json()["records"][0]["fields"]["national_id"]
    assert field["help"]["status"] == "open"
    res = client.patch(f"/api/records/{record.record_id}", json={"field": "national_id", "approve": True})
    assert res.status_code == 409
    # גם כשכל השאר תקין וירוק — הבקשה הממתינה לבדה חוסמת את אישור הטופס
    ids = record.fields["national_id"]
    ids.values, ids.state, ids.value_regions = ["011223344"], STATE_APPROVED, []
    res = client.post(f"/api/records/{record.record_id}/verify")
    assert res.status_code == 409 and "עזרה" in res.json()["detail"]
    assert record.verified is False


def test_the_answer_comes_back_and_the_secretary_decides():
    client, record = _client(), _record()
    token = _ask(client, record)
    res = client.post(f"/api/help/{token}/answer", json={"value": "011223344", "note": "לפי המדבקה", "name": "דנה"})
    assert res.status_code == 200 and res.json()["status"] == "answered"
    help_ = client.get("/api/batches/bat_help").json()["records"][0]["fields"]["national_id"]["help"]
    assert help_["answer_value"] == "011223344" and help_["answered_by"] == "דנה"
    assert record.fields["national_id"].values == ["011223344", "11223354"]  # התשובה לא משנה לבד
    # המזכירה משתמשת בערך — ואז הטופס מאושר כרגיל
    client.patch(f"/api/records/{record.record_id}", json={"field": "national_id", "values": ["011223344"]})
    assert client.post(f"/api/records/{record.record_id}/verify").status_code == 200
    assert db.help_get(token)["status"] == "closed"  # נשמר לתיעוד, לא נמחק


def test_an_answer_must_follow_the_rules_and_comes_once():
    client, record = _client(), _record()
    token = _ask(client, record)
    assert client.post(f"/api/help/{token}/answer", json={"value": "לא ברור"}).status_code == 422
    assert client.post(f"/api/help/{token}/answer", json={}).status_code == 400
    assert client.post(f"/api/help/{token}/answer", json={"note": "תתקשרי לקופה"}).status_code == 200
    assert client.post(f"/api/help/{token}/answer", json={"value": "011223344"}).status_code == 409


def test_cancel_frees_the_field():
    client, record = _client(), _record()
    token = _ask(client, record)
    client.post(f"/api/records/{record.record_id}/help/cancel", json={"field": "national_id"})
    assert client.get("/api/batches/bat_help").json()["records"][0]["fields"]["national_id"]["help"] is None
    assert db.help_get(token)["status"] == "canceled"
    assert client.post(f"/api/help/{token}/answer", json={"value": "011223344"}).status_code == 409


def test_fixing_it_herself_ends_the_request():
    client, record = _client(), _record()
    token = _ask(client, record)
    client.patch(f"/api/records/{record.record_id}", json={"field": "national_id", "values": ["011223344"]})
    assert db.help_get(token)["status"] == "closed"


def test_no_help_for_an_approved_field():
    client, record = _client(), _record()
    res = client.post(f"/api/records/{record.record_id}/help", json={"field": "first_name"})
    assert res.status_code == 409


def test_after_the_answer_the_link_shows_nothing_private():
    client, record = _client(), _record()
    token = _ask(client, record)
    client.post(f"/api/help/{token}/answer", json={"value": "011223344", "name": "דנה"})
    data = client.get(f"/api/help/{token}").json()
    assert data["status"] == "answered" and data["options"] == [] and data["crops"] == 0
    assert "011223344" not in str(data) and "11223354" not in str(data)
    assert client.get(f"/api/help/{token}/crops/0").status_code == 404
    # המזכירה עדיין רואה את התשובה אצלה במסך
    seen = client.get("/api/batches/bat_help").json()["records"][0]["fields"]["national_id"]["help"]
    assert seen["answer_value"] == "011223344"


def test_the_link_expires_after_48_hours(monkeypatch):
    from datetime import timedelta

    client, record = _client(), _record()
    token = _ask(client, record)
    later = db._utcnow() + timedelta(hours=db.HELP_TTL_HOURS, minutes=1)
    monkeypatch.setattr("app.db._utcnow", lambda: later)

    data = client.get(f"/api/help/{token}").json()
    assert data["status"] == "expired" and data["options"] == [] and data["crops"] == 0
    assert client.get(f"/api/help/{token}/crops/0").status_code == 404
    assert client.post(f"/api/help/{token}/answer", json={"value": "011223344"}).status_code == 410
    field = client.get("/api/batches/bat_help").json()["records"][0]["fields"]["national_id"]
    assert field["help"]["expired"] is True

    fresh = _ask(client, record)  # "שליחה שוב" — קישור חדש שעובד
    assert fresh != token
    assert db.help_get(token)["status"] == "expired"  # הישן נשאר בתיעוד, לא נמחק
    assert client.post(f"/api/help/{fresh}/answer", json={"value": "011223344"}).status_code == 200


def test_before_48_hours_the_link_still_works(monkeypatch):
    from datetime import timedelta

    client, record = _client(), _record()
    token = _ask(client, record)
    almost = db._utcnow() + timedelta(hours=db.HELP_TTL_HOURS - 1)
    monkeypatch.setattr("app.db._utcnow", lambda: almost)
    assert client.get(f"/api/help/{token}").json()["status"] == "open"
    assert client.post(f"/api/help/{token}/answer", json={"value": "011223344"}).status_code == 200
