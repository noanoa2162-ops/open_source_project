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


def test_in_the_cloud_the_help_table_is_created_when_missing(monkeypatch):
    """22/10/2026: in the cloud "🙋 עזרה" failed — the table is made only by a manual command after
    deploying, which a network blocking port 5432 cannot run. Now the first help call creates it."""

    class Conn:
        def __init__(self, exists):
            self.exists, self.sql = exists, []

        def execute(self, sql, params=None):
            self.sql.append(" ".join(sql.split()))
            row = {"t": "help_requests" if self.exists else None}
            return type("R", (), {"fetchone": lambda _: row})()

    monkeypatch.setattr(db, "cloud_enabled", lambda: True)
    monkeypatch.setattr(db, "_HELP_READY", False)

    missing = Conn(exists=False)
    monkeypatch.setattr(db, "_connect", lambda path=None: missing)
    db._help_conn()
    assert any(s.startswith("CREATE TABLE IF NOT EXISTS help_requests") for s in missing.sql)
    assert db._HELP_READY is False, "נבדק שוב בבקשה הבאה — אולי היצירה בוטלה"

    there = Conn(exists=True)
    monkeypatch.setattr(db, "_connect", lambda path=None: there)
    db._help_conn()
    db._help_conn()
    assert not any("CREATE" in s for s in there.sql) and db._HELP_READY is True
    assert len(there.sql) == 1, "אחרי שנמצאה — בלי בדיקה נוספת"


# ---------- 22/10/2026: "📨 שליחה עכשיו" — המייל יוצא מהשרת, בלי לפתוח את Gmail

class _FakeSMTP:
    sent: list = []
    fail_login = False

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        import smtplib
        if _FakeSMTP.fail_login:
            raise smtplib.SMTPAuthenticationError(535, b"bad")
        self.user = user

    def send_message(self, message):
        _FakeSMTP.sent.append(message)


@pytest.fixture
def smtp(monkeypatch):
    from app import mailer
    monkeypatch.setenv("SMTP_USER", "noa@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "app-password")
    monkeypatch.delenv("HELP_MAIL_ALLOWED", raising=False)
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", _FakeSMTP)
    monkeypatch.setattr(mailer, "_SENT", {})
    monkeypatch.setattr(mailer, "_RECENT", [])
    _FakeSMTP.sent, _FakeSMTP.fail_login = [], False
    return _FakeSMTP


def test_the_server_sends_the_help_mail_with_only_the_link(smtp):
    client, record = _client(), _record()
    token = _ask(client, record)
    res = client.post(f"/api/records/{record.record_id}/help/mail", json={"field": "national_id", "to": "boss@example.com"})
    assert res.status_code == 200, res.text
    message = smtp.sent[0]
    body = message.get_content()
    assert message["To"] == "boss@example.com" and "noa@example.com" in message["From"]
    assert "תעודת זהות" in message["Subject"] and f"/help/{token}" in body and "רחל" in body
    # בלי פרטי מטופל: לא שם, לא ת"ז, לא מזהה טופס
    assert "שרה" not in body and "כהן" not in body and "011223344" not in body and record.record_id not in body


def test_no_mail_without_an_open_request_or_to_a_bad_address(smtp):
    client, record = _client(), _record()
    assert client.post(f"/api/records/{record.record_id}/help/mail",
                       json={"field": "national_id", "to": "boss@example.com"}).status_code == 409
    _ask(client, record)
    assert client.post(f"/api/records/{record.record_id}/help/mail",
                       json={"field": "national_id", "to": "not an address"}).status_code == 502
    assert smtp.sent == []


def test_only_allowed_recipients_and_a_few_sends_per_request(smtp, monkeypatch):
    monkeypatch.setenv("HELP_MAIL_ALLOWED", "@lab.example, chief@other.example")
    client, record = _client(), _record()
    _ask(client, record)
    send = lambda to: client.post(f"/api/records/{record.record_id}/help/mail", json={"field": "national_id", "to": to})
    assert send("stranger@gmail.com").status_code == 502, "כתובת שלא ברשימה — לא נשלח"
    assert send("chief@other.example").status_code == 200 and send("a@lab.example").status_code == 200
    assert send("b@lab.example").status_code == 200
    assert send("c@lab.example").status_code == 502, "עד 3 שליחות לבקשה"
    assert len(smtp.sent) == 3


def test_a_wrong_password_says_what_to_do_and_does_not_count(smtp):
    smtp.fail_login = True
    client, record = _client(), _record()
    _ask(client, record)
    res = client.post(f"/api/records/{record.record_id}/help/mail", json={"field": "national_id", "to": "boss@example.com"})
    assert res.status_code == 502 and "App Password" in res.json()["detail"]
    from app import mailer
    assert not any(mailer._SENT.values())


def test_direct_sending_is_off_until_configured_and_on_a_public_site_needs_a_list(monkeypatch):
    from app import mailer
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    assert not mailer.available() and _client().get("/api/config").json()["mail_send"] is False
    monkeypatch.setenv("SMTP_USER", "noa@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "app-password")
    monkeypatch.setattr(mailer, "_public", lambda: True)
    monkeypatch.delenv("HELP_MAIL_ALLOWED", raising=False)
    assert not mailer.available(), "אתר ציבורי בלי רשימת נמענים — אסור: כל אחד היה שולח בשמך"
    monkeypatch.setenv("HELP_MAIL_ALLOWED", "@lab.example")
    assert mailer.available()


# ---------- 22/10/2026: שליחה דרך Brevo (בחירת נועה) — מפתח אחד וכתובת שולח מאומתת

@pytest.fixture
def brevo(monkeypatch):
    import io
    import json
    import urllib.error
    from app import mailer
    monkeypatch.setenv("BREVO_API_KEY", "xkeysib-test")
    monkeypatch.setenv("MAIL_FROM", "noa@example.com")
    monkeypatch.setenv("SMTP_USER", "smtp-not-used@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "not-used")
    monkeypatch.delenv("HELP_MAIL_ALLOWED", raising=False)
    monkeypatch.setattr(mailer, "_SENT", {})
    monkeypatch.setattr(mailer, "_RECENT", [])
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", lambda *a, **k: pytest.fail("Brevo מוגדר — לא שולחים ב-Gmail"))
    calls = {"sent": [], "fail": None}

    class Response:
        status = 201

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def urlopen(request, timeout=None):
        if calls["fail"]:
            code, text = calls["fail"]
            raise urllib.error.HTTPError(request.full_url, code, "err", {}, io.BytesIO(text.encode()))
        calls["sent"].append((request.full_url, dict(request.header_items()), json.loads(request.data)))
        return Response()

    monkeypatch.setattr(mailer.urllib.request, "urlopen", urlopen)
    return calls


def test_brevo_sends_the_help_mail_with_only_the_link(brevo):
    client, record = _client(), _record()
    token = _ask(client, record)
    res = client.post(f"/api/records/{record.record_id}/help/mail", json={"field": "national_id", "to": "boss@example.com"})
    assert res.status_code == 200, res.text
    url, headers, payload = brevo["sent"][0]
    assert url == "https://api.brevo.com/v3/smtp/email" and headers["Api-key"] == "xkeysib-test"
    assert payload["sender"]["email"] == "noa@example.com" and payload["to"] == [{"email": "boss@example.com"}]
    assert f"/help/{token}" in payload["textContent"] and "תעודת זהות" in payload["subject"]
    assert "שרה" not in payload["textContent"] and "011223344" not in payload["textContent"]


@pytest.mark.parametrize("code, text, says", [
    (400, '{"code":"invalid_parameter","message":"sender not valid"}', "MAIL_FROM"),
    (401, '{"code":"unauthorized","message":"Key not found"}', "BREVO_API_KEY"),
])
def test_brevo_errors_say_what_to_fix_and_do_not_count(brevo, code, text, says):
    brevo["fail"] = (code, text)
    client, record = _client(), _record()
    _ask(client, record)
    res = client.post(f"/api/records/{record.record_id}/help/mail", json={"field": "national_id", "to": "boss@example.com"})
    assert res.status_code == 502 and says in res.json()["detail"]
    from app import mailer
    assert not any(mailer._SENT.values())


def test_brevo_alone_turns_direct_sending_on(monkeypatch):
    from app import mailer
    for name in ("SMTP_USER", "SMTP_PASSWORD", "HELP_MAIL_ALLOWED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BREVO_API_KEY", "xkeysib-test")
    monkeypatch.delenv("MAIL_FROM", raising=False)
    assert not mailer.available(), "בלי כתובת שולח — כבוי"
    monkeypatch.setenv("MAIL_FROM", "noa@example.com")
    assert mailer.available()
