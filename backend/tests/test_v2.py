"""Acceptance checks for version 2: two judges, the secretary decides.

Every test here maps to a line in SPECIFICATION.md §14. No network: judges are
stand-ins that return exactly what each test needs.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from PIL import Image

from app import gold, pipeline, store
from app.consensus import combine, normalize
from app.crops import crop_field, padded
from app.entities import canonical_sender
from app.excel import workbook_bytes
from app.fields import FIELDS
from app.models import STATE_APPROVED, STATE_OK, STATE_REVIEW, Batch, FormRecord, SourceFile
from app.pipeline import Reader, read_form
from app.providers.base import ExtractionError, RawExtraction, clamp_region
from app.validators import validate_record

SOURCE = SourceFile(index=1, filename="form.pdf", kind="pdf", sha256="")
ALL_CLEAR = {f.key: "clear" for f in FIELDS}


def reading(model="a", legibility=None, regions=None, context=None, **values) -> RawExtraction:
    return RawExtraction(
        values={f.key: values.get(f.key, []) for f in FIELDS},
        model=model,
        legibility={**ALL_CLEAR, **(legibility or {})},
        regions=regions or {},
        context=context or {},
    )


def record_from(*readings: RawExtraction, low_res=False, today=date(2026, 10, 14)) -> FormRecord:
    record = FormRecord.empty("b", SOURCE, 1)
    record.low_resolution = low_res
    combine(record, readings[0])  # קורא אחד; קריאות נוספות בבדיקות ישנות לא בשימוש
    validate_record(record, today=today)
    return record


class Judge:
    """A stand-in judge: fixed answer, counts its calls, can fail on demand."""

    def __init__(self, model, answer=None, error=None):
        self.name = self.model = model
        self.answer = answer
        self.error = error
        self.calls = 0

    def extract(self, image_path):
        self.calls += 1
        if self.error:
            raise self.error
        return self.answer


def png(path: Path, size=(1700, 2400)) -> Path:
    Image.new("RGB", size, "white").save(path, format="PNG")
    return path


# ------------------------------------------------------------- agreement


def test_agreement_gives_a_neutral_value_never_green():
    r = record_from(
        reading("a", commitment_numbers=["730578626"]),
        reading("b", commitment_numbers=["730578626"]),
    )
    field = r.fields["commitment_numbers"]
    assert field.values == ["730578626"]
    assert field.state == STATE_OK, "הסכמה = ניטרלי"
    assert field.state != STATE_APPROVED, "ירוק רק באישור אנושי"


def test_both_empty_is_orange_not_on_form():
    # שניהם חיפשו ולא מצאו — "לא קיים בטופס". עדיין כתום: שדה חובה, אדם מאשר כריק.
    r = record_from(reading("a"), reading("b"))
    field = r.fields["last_name"]
    assert field.state == STATE_REVIEW
    assert field.reasons == ["not_on_form"]


def test_both_empty_with_illegible_is_not_called_absent():
    r = record_from(reading("a", legibility={"last_name": "illegible"}), reading("b"))
    reasons = r.fields["last_name"].reasons
    assert "not_on_form" not in reasons, "מודל אחד ראה דיו — השדה כן קיים"
    assert "illegible" in reasons and "missing" in reasons


def test_question_mark_is_orange_even_when_both_agree():
    r = record_from(
        reading("a", national_id=["0012?3456"]),
        reading("b", national_id=["0012?3456"]),
    )
    field = r.fields["national_id"]
    assert field.values == ["0012?3456"], "ה-? נשאר גלוי"
    assert "illegible" in field.reasons


def test_agreement_with_illegible_stays_orange():
    r = record_from(
        reading("a", legibility={"first_name": "illegible"}, first_name=["שמואל"]),
        reading("b", first_name=["שמואל"]),
    )
    assert r.fields["first_name"].state == STATE_REVIEW
    assert "illegible" in r.fields["first_name"].reasons


def test_multi_values_agree_regardless_of_order_and_keep_first_order():
    r = record_from(
        reading("a", commitment_numbers=["111", "222"]),
        reading("b", commitment_numbers=["222", "111"]),
    )
    assert r.fields["commitment_numbers"].values == ["111", "222"]


# ---------------------------------------------------------- normalization


@pytest.mark.parametrize(
    ("key", "a", "b"),
    [
        ("sample_received_date", "18/06/2026", "18-06-2026"),
        ("sample_received_date", "18.6.26", "18/06/2026"),
        ("national_id", "001 122 334", "001-122-334"),
        ("sending_entity", "קופת חולים מאוחדת", "מאוחדת"),
        ("sending_entity", "המרכז הרפואי שמיר (אסף הרופא)", "אסף הרופא"),
        ("first_name", "שָׁלוֹם", "שלום"),
    ],
)
def test_differences_that_are_not_differences_still_agree(key, a, b):
    assert normalize(key, a) == normalize(key, b)


def test_normalization_never_changes_a_digit():
    assert normalize("national_id", "0012") == "0012"
    assert normalize("national_id", "011223344") != normalize("national_id", "001122334")


def test_sender_is_shown_by_its_canonical_name():
    r = record_from(
        reading("a", sending_entity=["קופת חולים מאוחדת"]),
        reading("b", sending_entity=["מאוחדת"]),
    )
    assert r.fields["sending_entity"].values == ["מאוחדת"]
    assert r.fields["sending_entity"].state == STATE_OK


def test_patient_sticker_kupah_is_not_confused_with_sender_list_lookup():
    # "מכבי" is a known kupah; if both judges wrongly took it, the list can't save us —
    # but an unknown string is always flagged, never guessed into the list.
    assert canonical_sender("מכבי") == "מכבי"
    assert canonical_sender("מעבדה פרטית כלשהי") is None


@pytest.mark.parametrize("text", ["מרפאת זיו", "ד\"ר מאיר כהן", "קליניקת כרמל"])
def test_ordinary_words_do_not_become_hospitals(text):
    assert canonical_sender(text) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [("מאוחדת רמה אחרת", "מאוחדת"), ("המרכז הרפואי שמיר (אסף הרופא) מוסמך", "שמיר (אסף הרופא)"),
     ("בית החולים הציבורי אסותא אשדוד", "אסותא אשדוד"), ("זיו", "זיו")],
)
def test_known_senders_inside_longer_printed_names(text, expected):
    assert canonical_sender(text) == expected


def test_unknown_sender_is_kept_and_flagged():
    r = record_from(
        reading("a", sending_entity=["מרפאת הדר"]), reading("b", sending_entity=["מרפאת הדר"])
    )
    field = r.fields["sending_entity"]
    assert field.values == ["מרפאת הדר"]
    assert "unknown_entity" in field.reasons


# ------------------------------------------------------------- date rules


def test_received_before_issue_is_flagged():
    ctx = {"issue_date": "15/06/2026"}
    r = record_from(
        reading("a", context=ctx, sample_received_date=["10/06/2026"]),
        reading("b", context=ctx, sample_received_date=["10/06/2026"]),
    )
    assert "date_before_issue" in r.fields["sample_received_date"].reasons


def test_received_in_the_future_is_flagged():
    r = record_from(
        reading("a", sample_received_date=["01/01/2030"]),
        reading("b", sample_received_date=["01/01/2030"]),
    )
    assert "date_in_future" in r.fields["sample_received_date"].reasons


def test_received_equal_to_birth_date_is_flagged():
    ctx = {"birth_date": "04/12/2009"}
    r = record_from(
        reading("a", context=ctx, sample_received_date=["04/12/2009"]),
        reading("b", context=ctx, sample_received_date=["04/12/2009"]),
    )
    assert "date_is_birth" in r.fields["sample_received_date"].reasons


def test_valid_received_date_passes():
    ctx = {"issue_date": "15/06/2026", "birth_date": "04/12/2009"}
    r = record_from(
        reading("a", context=ctx, sample_received_date=["18/06/2026"]),
        reading("b", context=ctx, sample_received_date=["18/06/2026"]),
    )
    assert r.fields["sample_received_date"].state == STATE_OK


# ------------------------------------------------------ input quality gate


def test_low_resolution_flags_handwriting_fields_even_when_judges_agree():
    r = record_from(
        reading("a", first_name=["שרה"], commitment_numbers=["730578626"]),
        reading("b", first_name=["שרה"], commitment_numbers=["730578626"]),
        low_res=True,
    )
    assert "low_resolution" in r.fields["first_name"].reasons
    assert "low_resolution" not in r.fields["commitment_numbers"].reasons


def test_screenshot_sized_image_is_detected_as_low_resolution(tmp_path):
    small = png(tmp_path / "shot.png", size=(631, 896))
    record = read_form(Reader(Judge("a", reading("a"))), small, "b", SOURCE, 1)
    assert record.low_resolution


# ------------------------------------------------------------ the panel


def test_account_failure_retires_the_reader(tmp_path):
    a = Judge("a", error=ExtractionError("אין קרדיט", fatal=True))
    reader = Reader(a)
    record = read_form(reader, png(tmp_path / "p.png"), "b", SOURCE, 1)
    assert reader.exhausted, "לא שורפים עוד קריאות על חשבון בלי קרדיט"
    assert "קרדיט" in record.error


def test_same_file_twice_costs_nothing_the_second_time(tmp_path):
    pipeline._READ_CACHE.clear()
    source = SourceFile(index=1, filename="f.pdf", sha256="abc123")
    answer = reading("a", commitment_numbers=["1"])
    answer.usage = __import__("app.providers.base", fromlist=["Usage"]).Usage(100, 10, 0.01)
    a = Judge("a", answer)
    image = png(tmp_path / "p.png")
    first = read_form(Reader(a), image, "b", source, 1)
    second = read_form(Reader(a), image, "b", source, 1)
    assert a.calls == 1
    assert first.cost_usd == pytest.approx(0.01)
    assert second.cost_usd == 0.0
    pipeline._READ_CACHE.clear()


def test_budget_cap_stops_before_the_next_call(tmp_path, monkeypatch):
    from app.providers.base import Usage

    monkeypatch.setattr("app.config.BATCHES_DIR", tmp_path / "batches")
    monkeypatch.setattr("app.store.BATCHES_DIR", tmp_path / "batches")
    monkeypatch.setattr(
        pipeline, "SETTINGS",
        replace(pipeline.SETTINGS, max_cost_per_batch_usd=0.05, max_parallel_calls=1),
    )
    expensive = reading("a")
    expensive.usage = Usage(1, 1, 0.03)
    judge = Judge("a", expensive)
    monkeypatch.setattr(pipeline, "build_reader", lambda: (judge, ""))

    batch = Batch(batch_id="bat_budget")
    for i in range(1, 6):
        batch.sources.append(SourceFile(index=i, filename=f"{i}.png", kind="png"))
        (store.source_dir("bat_budget", i) / "source").write_bytes(
            png(tmp_path / f"{i}.png", size=(300, 400)).read_bytes()
        )
    store.put(batch)
    pipeline._process("bat_budget")

    assert judge.calls == 2, "0.03 + 0.03 עובר את 0.05 — הקריאה השלישית לא נשלחת"
    assert batch.budget_skipped_pages == 3
    assert batch.status == "partial"
    assert any("תקרת התקציב" in w for w in batch.warnings)


# ---------------------------------------------------------- regions & crops


@pytest.mark.parametrize("bad", [None, [1, 2], ["x", 0, 1, 1], [0.5, 0.5, 0.4, 0.6], [0, 0, 5, 5]])
def test_garbage_coordinates_are_dropped_not_repaired(bad):
    assert clamp_region(bad) is None


def test_crop_margins_stay_inside_the_page():
    for region in [(0.0, 0.0, 0.05, 0.02), (0.95, 0.97, 1.0, 1.0), (0.4, 0.4, 0.6, 0.45)]:
        x0, y0, x1, y1 = padded(region)
        assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1
        assert x0 <= region[0] and x1 >= region[2], "השוליים מכילים את השדה"


def test_crop_is_enlarged_and_cached(tmp_path):
    page = png(tmp_path / "page.png", size=(1000, 1400))
    target = tmp_path / "crops" / "001-national_id.png"
    out = crop_field(page, (0.4, 0.4, 0.6, 0.45), target)
    with Image.open(out) as im:
        assert im.width > 0.2 * 1000, "מוגדל, לא רק נחתך"
    stamp = out.stat().st_mtime_ns
    crop_field(page, (0.4, 0.4, 0.6, 0.45), target)
    assert out.stat().st_mtime_ns == stamp


# --------------------------------------------------------------- the API


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.BATCHES_DIR", tmp_path / "batches")
    monkeypatch.setattr("app.store.BATCHES_DIR", tmp_path / "batches")
    monkeypatch.setattr("app.gold.GOLD_DIR", tmp_path / "gold")
    from app.api import app

    batch = Batch(batch_id="bat_api")
    source = SourceFile(index=1, filename="form.png", kind="png", sha256="f" * 64)
    batch.sources.append(source)
    page = pipeline.page_image_path("bat_api", 1, 1)
    page.parent.mkdir(parents=True, exist_ok=True)
    png(page, size=(1000, 1400))
    record = record_from(
        reading("a", regions={"national_id": (0.4, 0.4, 0.6, 0.45)}, national_id=["011223344"],
                commitment_numbers=["730578626"]),
        reading("b", national_id=["00112?344"], commitment_numbers=["730578626"]),
    )
    record.batch_id, record.source_index, record.page_number = "bat_api", 1, 1
    batch.records.append(record)
    batch.status = "completed"
    store.put(batch)
    yield TestClient(app), record
    store.delete("bat_api")


def test_api_serves_values_and_crop_box(api):
    client, record = api
    data = client.get("/api/batches/bat_api").json()
    field = data["records"][0]["fields"]["national_id"]
    assert field["values"] == ["011223344"]
    assert field["crop_box"] is not None
    assert set(field) == {"values", "state", "reasons", "reason_codes", "crop_box", "crop_boxes", "region_note", "can_undo", "blocked", "help"}


def test_api_crop_endpoint(api):
    client, _ = api
    res = client.get("/api/batches/bat_api/sources/1/pages/1/crops/national_id")
    assert res.status_code == 200 and res.headers["content-type"] == "image/png"
    assert res.headers["cache-control"] == "no-store"


def test_api_no_crop_without_a_reliable_region(api):
    client, _ = api
    res = client.get("/api/batches/bat_api/sources/1/pages/1/crops/first_name")
    assert res.status_code == 404, "לא ממציאים תקריב"


def _approve_open_fields(client, record):
    for key, field in record.fields.items():
        if field.state == STATE_REVIEW:
            client.patch(f"/api/records/{record.record_id}", json={"field": key, "approve": True})


def test_verify_is_refused_while_a_field_is_orange(api):
    client, record = api
    assert any(f.state == STATE_REVIEW for f in record.fields.values())
    res = client.post(f"/api/records/{record.record_id}/verify")
    assert res.status_code == 409, "שדה כתום צריך תיקון או אישור מפורש לפני אישור הטופס"
    assert record.verified is False


def test_verify_saves_an_answer_key_entry(api, tmp_path):
    client, record = api
    client.patch(f"/api/records/{record.record_id}", json={"field": "national_id", "values": ["011223344"]})
    _approve_open_fields(client, record)
    res = client.post(f"/api/records/{record.record_id}/verify")
    assert res.status_code == 200
    assert res.json()["records"][0]["verified"] is True

    forms = gold.load_all(tmp_path / "gold")
    assert len(forms) == 1
    assert forms[0].truth["national_id"] == ["011223344"]
    assert forms[0].page_png.is_file()


def test_export_is_locked_until_no_field_is_orange(api):
    client, record = api
    res = client.post("/api/batches/bat_api/export")
    assert res.status_code == 409, "אין ייצוא לאקסל כל עוד יש שדה כתום"
    _approve_open_fields(client, record)
    client.post(f"/api/records/{record.record_id}/verify")
    res = client.post("/api/batches/bat_api/export")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/vnd.openxmlformats")


def test_export_waits_for_the_last_form_to_be_approved(api):
    """בלי שדות כתומים, אבל הטופס לא אושר ב"אישור הטופס" — עדיין אין ייצוא."""
    client, record = api
    _approve_open_fields(client, record)
    res = client.post("/api/batches/bat_api/export")
    assert res.status_code == 409 and "לאישור" in res.json()["detail"]
    assert client.get("/api/batches/bat_api").json()["unverified"] == 1
    client.post(f"/api/records/{record.record_id}/verify")
    assert client.get("/api/batches/bat_api").json()["unverified"] == 0
    assert client.post("/api/batches/bat_api/export").status_code == 200


def test_export_is_locked_while_still_reading(api):
    client, record = api
    _approve_open_fields(client, record)
    store.get("bat_api").status = "processing"
    assert client.post("/api/batches/bat_api/export").status_code == 409


def test_excel_preview_matches_the_file_exactly(api):
    """What the screen shows before download is exactly what the file contains."""
    from io import BytesIO
    from openpyxl import load_workbook

    client, record = api
    record.fields["commitment_numbers"].values = ["730578626", "=SUM(A1)"]
    data = client.get("/api/batches/bat_api/export/preview").json()
    assert data["forms"] == 1 and data["row_count"] == 2, "שורה לכל מספר התחייבות"
    assert [r["form"] for r in data["rows"]] == [1, 1]

    batch = store.get("bat_api")
    sheet = load_workbook(BytesIO(workbook_bytes(batch))).active
    file_rows = [[c.value or "" for c in row] for row in sheet.iter_rows(min_row=1, max_row=1 + data["row_count"])]
    assert file_rows[0] == data["headers"]
    assert file_rows[1:] == [r["cells"] for r in data["rows"]]
    assert data["rows"][1]["cells"][data["commitment_column"]] == "'=SUM(A1)", "נוסחה מנוטרלת גם בתצוגה"


def test_holdout_is_deterministic_and_about_a_third():
    picks = [gold.is_holdout(f"{i:064x}", 1) for i in range(300)]
    assert picks == [gold.is_holdout(f"{i:064x}", 1) for i in range(300)]
    assert 70 < sum(picks) < 130


# ----------------------------------------------------------------- Excel


# ------------------------------------------------------------- evaluate


def test_evaluation_counts_a_silent_error(tmp_path):
    from app.evaluate import Config, Score, score_form

    form = gold.GoldForm("k", png(tmp_path / "p.png"), "f.pdf", 1, "", {"commitment_numbers": ["730578626"]}, False)
    wrong = Judge("a", reading("a", commitment_numbers=["87250"]))
    cfg = Config("X", "test", lambda: wrong, 0)
    score = Score()
    score_form(cfg, form, score, [])
    assert score.silent_errors == 1, "ערך שגוי בלי כתום — זה בדיוק מה שנמדד"
    assert score.errors[0]["field"] == "commitment_numbers"


# --------------------------------------------------------- Gemini quotas


FREE_TIER = (
    "You exceeded your current quota, please check your plan and billing details. "
    "* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, "
    "limit: 20, model: gemini-3.8-flash Please retry in 4h11m2.9s."
)


# ---------------------------------------------- zoom location: trusted judge only


# ------------------------------------------------- editing the prompt takes effect


def test_edited_prompt_is_not_served_from_the_cache(tmp_path):
    pipeline._READ_CACHE.clear()
    source = SourceFile(index=1, filename="f.pdf", sha256="same-file")
    judge = Judge("a", reading("a", commitment_numbers=["1"]))
    image = png(tmp_path / "p.png")

    judge.prompt_version = "extract_v2-aaaa1111"
    read_form(Reader(judge), image, "b", source, 1)
    read_form(Reader(judge), image, "b", source, 1)
    assert judge.calls == 1, "אותו פרומפט — מהמטמון"

    judge.prompt_version = "extract_v2-bbbb2222"  # נועה ערכה את הפרומפט
    read_form(Reader(judge), image, "b", source, 1)
    assert judge.calls == 2, "פרומפט חדש — קריאה חדשה, לא תשובה ישנה"
    pipeline._READ_CACHE.clear()


def test_prompt_version_follows_the_text():
    from app.providers.reading import prompt_version

    assert prompt_version("א") == prompt_version("א")
    assert prompt_version("א") != prompt_version("ב")


# ------------------------------------------------- model comparison per field


def test_partial_name_from_both_judges_is_visible_but_orange():
    r = record_from(
        reading("a", legibility={"first_name": "partial"}, first_name=["שמו?ל"]),
        reading("b", legibility={"first_name": "partial"}, first_name=["שמו?ל"]),
    )
    field = r.fields["first_name"]
    assert field.values == ["שמו?ל"], "המזכירה רואה את מה שנקרא"
    assert field.state == STATE_REVIEW, "אבל זה לא מאושר — כתום"


# ----------------------------------------------------------- Claude as judge B


def _claude_response(parsed=None, stop="end_turn", model="claude-sonnet-5-5", usage=None):
    from types import SimpleNamespace

    return SimpleNamespace(parsed_output=parsed, stop_reason=stop, model=model, content=[], usage=usage)


def _page_reading(**fields):
    from app.providers.reading import FieldReading, PageReading

    empty = FieldReading(values=[], legibility="absent")
    reading = PageReading(template="kupah_form", **{
        k: empty for k in PageReading.model_fields
        if k not in ("template", "issue_date", "birth_date", "upside_down", "note", "commitment_found_by")
    })
    for key, value in fields.items():
        setattr(reading, key, value)
    return reading


def test_claude_answer_becomes_a_reading_with_its_locations():
    from types import SimpleNamespace

    from app.providers.claude_vision import ClaudeExtractionProvider
    from app.providers.reading import FieldReading

    judge = ClaudeExtractionProvider(api_key="test", model="claude-sonnet-5-5")
    parsed = _page_reading(
        national_id=FieldReading(values=["0011223344"], legibility="clear", tile=0, box=[100, 300, 400, 340]),
    )
    usage = SimpleNamespace(input_tokens=5000, output_tokens=800,
                            cache_read_input_tokens=6000, cache_creation_input_tokens=0)
    raw = judge._to_domain(_claude_response(parsed, usage=usage))

    assert raw.values["national_id"] == ["0011223344"]
    assert raw.regions["national_id"] == pytest.approx((0.1, 0.3, 0.4, 0.34)), "המיקום משמש לתקריב"
    assert raw.usage.cost_usd == pytest.approx((5000 * 2 + 6000 * 0.2 + 800 * 10) / 1_000_000)


def test_claude_refusal_does_not_stop_the_batch():
    from app.providers.claude_vision import ClaudeExtractionProvider

    judge = ClaudeExtractionProvider(api_key="test")
    with pytest.raises(ExtractionError) as caught:
        judge._to_domain(_claude_response(stop="refusal"))
    assert caught.value.fatal is False


def test_claude_fallback_model_is_named_and_priced():
    from app.providers.claude_vision import ClaudeExtractionProvider

    judge = ClaudeExtractionProvider(api_key="test", model="claude-sonnet-5-5")
    raw = judge._to_domain(_claude_response(_page_reading(), model="claude-opus-5-5"))
    assert raw.model == "claude-opus-5-5", "אם עבר למודל גיבוי — רואים מי ענה בפועל"


def _api_error(cls, message, status):
    import httpx2

    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls(message, response=httpx2.Response(status, request=request), body=None)


def test_claude_account_problems_are_fatal_and_overload_is_not():
    import anthropic

    from app.providers.claude_vision import _classify

    assert _classify(_api_error(anthropic.AuthenticationError, "invalid x-api-key", 401))[1] is True
    assert _classify(_api_error(anthropic.BadRequestError, "Your credit balance is too low", 400))[1] is True
    assert _classify(_api_error(anthropic.NotFoundError, "model not found", 404))[1] is True
    assert _classify(_api_error(anthropic.RateLimitError, "rate limited", 429))[1] is False
    assert _classify(_api_error(anthropic.InternalServerError, "overloaded", 529))[1] is False


def test_page_sent_to_claude_fits_its_limit(tmp_path):
    import base64
    import io

    from app.providers.claude_vision import MAX_LONG_SIDE, MAX_PIXELS, page_png_b64

    page = png(tmp_path / "p.png", size=(2481, 3509))
    data = base64.b64decode(page_png_b64(page))
    with Image.open(io.BytesIO(data)) as sent:
        assert max(sent.size) <= MAX_LONG_SIDE and sent.size[0] * sent.size[1] <= MAX_PIXELS
        assert sent.size[0] / sent.size[1] == pytest.approx(2481 / 3509, rel=0.01)


# ------------------------------------------------------------- "bold ink" version

INK = (74, 187, 237)  # נמדד על טופס מאוחדת
TURQUOISE = (168, 247, 247)
YELLOW = (245, 253, 171)
PAPER = (251, 253, 251)
PRINT = (34, 31, 36)


def _swatches(path: Path) -> Path:
    image = Image.new("RGB", (500, 100), PAPER)
    for i, colour in enumerate((INK, TURQUOISE, YELLOW, PAPER, PRINT)):
        image.paste(colour, (i * 100, 0, i * 100 + 100, 100))
    image.save(path)
    return path


def test_ink_version_removes_highlighters_and_darkens_ink(tmp_path):
    from app.pages import ink_enhanced

    out = ink_enhanced(_swatches(tmp_path / "page-001.png"))
    with Image.open(out) as ink:
        values = [ink.getpixel((i * 100 + 50, 50)) for i in range(5)]
    blue, turquoise, yellow, paper, printed = values
    assert blue < 70, "עט כחול נעשה כהה"
    assert turquoise == 255 and yellow == 255, "המרקרים נעלמים"
    assert paper == 255
    assert printed == 0, "דפוס שחור נשאר שחור"


def test_ink_version_keeps_the_page_size_and_is_made_once(tmp_path):
    from app.pages import ink_enhanced

    page = png(tmp_path / "page-001.png", size=(1234, 1700))
    out = ink_enhanced(page)
    assert out.name == "page-001.ink.png"
    with Image.open(out) as ink:
        assert ink.size == (1234, 1700), "אותו גודל — המיקומים לא זזים"
    stamp = out.stat().st_mtime_ns
    ink_enhanced(page)
    assert out.stat().st_mtime_ns == stamp


def _settings(**changes):
    from app.config import SETTINGS

    return replace(SETTINGS, **changes)


class _FakeStream:
    def __init__(self, response):
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.response


def _streaming_client(sent, response=None):
    """A stand-in Anthropic client: records what was sent, answers like `messages.stream`."""
    from types import SimpleNamespace

    def stream(**kwargs):
        sent.update(kwargs)
        sent["content"] = kwargs["messages"][0]["content"]
        return _FakeStream(response or _claude_response(_page_reading()))

    return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))


def test_opus_reads_with_deep_thinking_streamed(tmp_path, monkeypatch):
    from app.config import SETTINGS
    from app.providers import claude_vision as cv

    sent = {}
    judge = cv.ClaudeExtractionProvider(api_key="test", model="claude-opus-5-5")
    judge._client = _streaming_client(sent, _claude_response(_page_reading(), model="claude-opus-5-5"))
    raw = judge.extract(_swatches(tmp_path / "page-001.png"))
    assert sent["model"] == "claude-opus-5-5"
    assert sent["output_config"] == {"effort": SETTINGS.claude_effort}, "הרמה שנבחרה ב-.env נשלחת כמו שהיא"
    assert sent["max_tokens"] >= 64_000, "החשיבה לא נקטעת באמצע"
    assert "thinking" not in sent, "ב-Opus 5.5 החשיבה תמיד פועלת; העומק נקבע ב-effort"
    assert raw.model == "claude-opus-5-5"


def test_claude_sends_the_page_once(tmp_path):
    from app.providers import claude_vision as cv

    sent = {}
    judge = cv.ClaudeExtractionProvider(api_key="test")
    judge._client = _streaming_client(sent)
    page = _swatches(tmp_path / "page-001.png")
    judge.extract(page)

    images = [c for c in sent["content"] if c["type"] == "image"]
    assert len(images) == 1, "תמונה אחת — העמוד עצמו (בלי חלוקה לרבעים)"
    assert images[0]["source"]["data"] == cv.page_png_b64(page)
    assert sent["content"][-1]["type"] == "text"

def _blocking_client(sent_types, block, status=418):
    """Blocks (like NetFree's word filter) the first `block` requests, then answers."""
    import anthropic
    from types import SimpleNamespace

    def stream(**kwargs):
        image = next(c for c in kwargs["messages"][0]["content"] if c["type"] == "image")
        sent_types.append(image["source"]["media_type"])
        if len(sent_types) <= block:
            raise _api_error(anthropic.APIStatusError, "blocked", status)
        return _FakeStream(_claude_response(_page_reading()))

    return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))


def test_filter_block_resends_the_page_in_another_encoding(tmp_path):
    from app.providers import claude_vision as cv

    sent = []
    judge = cv.ClaudeExtractionProvider(api_key="test")
    judge._client = _blocking_client(sent, block=1)
    raw = judge.extract(_swatches(tmp_path / "page-001.png"))
    assert sent == ["image/png", "image/jpeg"], "PNG נחסם → אותו דף כ-JPEG"
    assert raw.values


def test_filter_blocking_every_encoding_says_so_and_is_not_fatal(tmp_path):
    from app.providers import claude_vision as cv
    from app.providers.base import ExtractionError

    sent = []
    judge = cv.ClaudeExtractionProvider(api_key="test")
    judge._client = _blocking_client(sent, block=99)
    with pytest.raises(ExtractionError) as caught:
        judge.extract(_swatches(tmp_path / "page-001.png"))
    assert len(sent) == len(cv.ENCODINGS) == 3, "שלושה קידודים ואז עוצרים"
    assert "NetFree" in str(caught.value) and "לא חויבה" in str(caught.value)
    assert caught.value.fatal is False, "שאר הטפסים ממשיכים"


def test_other_errors_do_not_trigger_another_encoding(tmp_path):
    import anthropic

    from app.providers import claude_vision as cv
    from app.providers.base import ExtractionError

    sent = []
    judge = cv.ClaudeExtractionProvider(api_key="test")
    judge._client = _blocking_client(sent, block=99, status=500)
    with pytest.raises(ExtractionError):
        judge.extract(_swatches(tmp_path / "page-001.png"))
    assert sent == ["image/png"], "שגיאת שרת — לא שולחים שוב בקידוד אחר"
    del anthropic


def test_jpeg_versions_keep_the_page_size(tmp_path):
    import base64
    import io

    from app.providers.claude_vision import MAX_LONG_SIDE, MAX_PIXELS, page_jpeg_b64

    page = png(tmp_path / "p.png", size=(2481, 3509))
    for gray in (False, True):
        with Image.open(io.BytesIO(base64.b64decode(page_jpeg_b64(page, gray=gray)))) as sent:
            assert sent.format == "JPEG" and max(sent.size) <= MAX_LONG_SIDE
            assert sent.size[0] * sent.size[1] <= MAX_PIXELS
            assert sent.mode == ("L" if gray else "RGB")


def test_api_crop_in_ink_view(api):
    client, _ = api
    # עמוד עם מרקר טורקיז — בגרסה המודגשת הוא אמור להיעלם
    Image.new("RGB", (1000, 1400), TURQUOISE).save(pipeline.page_image_path("bat_api", 1, 1))
    plain = client.get("/api/batches/bat_api/sources/1/pages/1/crops/national_id")
    ink = client.get("/api/batches/bat_api/sources/1/pages/1/crops/national_id?ink=1")
    assert plain.status_code == ink.status_code == 200
    assert ink.headers["content-type"] == "image/png"
    assert ink.content != plain.content, "גרסה נפרדת, לא אותה תמונה"


# ------------------------------------------- 15/10/2026: refinements, zero extra cost


def test_question_mark_in_agreement_is_still_orange_even_with_partial():
    r = record_from(
        reading("a", legibility={"national_id": "partial"}, national_id=["0011?2334"]),
        reading("b", legibility={"national_id": "partial"}, national_id=["0011?2334"]),
    )
    assert r.fields["national_id"].state == STATE_REVIEW


def test_upside_down_comes_from_the_reader():
    a = reading("a")
    assert record_from(a).upside_down is False
    a.upside_down = True
    assert record_from(a).upside_down is True


def test_show_upright_turns_the_page_and_every_box(tmp_path):
    page = tmp_path / "page-001.png"
    image = Image.new("RGB", (100, 200), "white")
    image.putpixel((0, 0), (255, 0, 0))
    image.save(page)
    record = record_from(
        reading("gemini", regions={"national_id": (0.1, 0.2, 0.3, 0.4)}, national_id=["1"]),
        reading("b", national_id=["1"]),
    )
    pipeline.show_upright(record, page)
    with Image.open(page) as turned:
        assert turned.getpixel((99, 199)) == (255, 0, 0)
    region = record.fields["national_id"].region
    assert region == pytest.approx((0.7, 0.6, 0.9, 0.8))
    assert "הפוך" in record.straightened


@pytest.mark.parametrize("printed, expected", [
    ("מרכז רפואי מאיר", "מאיר"),
    ("המרכז הרפואי האוניברסיטאי סורוקה", "סורוקה"),
    ("מרכז רפואי צאנז", "לניאדו"),
    ("בית חולים אלישע", "אלישע"),
    ('קופ"ח לאומית', "לאומית"),
    ("שמיר (אסף הרופא)", "שמיר (אסף הרופא)"),
])
def test_closed_list_maps_printed_names(printed, expected):
    assert canonical_sender(printed) == expected


@pytest.mark.parametrize("ambiguous", ["האיטלקי", "אסותא", "מרפאת השרון"])
def test_ambiguous_names_stay_unknown(ambiguous):
    assert canonical_sender(ambiguous) is None, "שם של כמה מוסדות — נשאר כתום, לא מנוחש"


def test_prompt_carries_the_closed_list_and_upside_down():
    from app.providers.reading import load_prompt

    text = load_prompt("x")
    assert "רשימת הגורמים השולחים המוכרים" in text
    assert "- שערי צדק" in text
    assert "upside_down" in text


def _lines_page(size=(1240, 1754)) -> Image.Image:
    from PIL import ImageDraw

    page = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(page)
    for y in range(120, size[1] - 100, 46):
        x = 100
        while x < size[0] - 140:
            width = 40 + (x * 7 + y) % 90
            draw.rectangle([x, y, min(x + width, size[0] - 100), y + 14], fill=(20, 20, 20))
            x += width + 18
    return page


def test_straight_page_is_left_alone():
    from app.pages import straighten

    page = _lines_page()
    fixed, note = straighten(page)
    assert note == "" and fixed is page


def test_tilted_page_is_levelled():
    from app.pages import _ink_mask, _skew_angle, straighten

    fixed, note = straighten(_lines_page().rotate(5, expand=True, fillcolor="white"))
    assert "יושר" in note
    assert _skew_angle(_ink_mask(fixed)) == 0.0, "אחרי היישור לא נשארת הטיה"


def test_small_tilt_is_not_worth_touching():
    from app.pages import straighten

    _, note = straighten(_lines_page().rotate(1, expand=True, fillcolor="white"))
    assert note == ""


def test_page_on_its_side_is_turned_upright():
    from app.pages import straighten

    fixed, note = straighten(_lines_page().rotate(90, expand=True))
    assert "90" in note
    assert fixed.height > fixed.width


def test_render_writes_the_turn_note(tmp_path):
    from app.pages import render_pages, turn_note

    source = tmp_path / "photo.png"
    _lines_page().rotate(6, expand=True, fillcolor="white").save(source)
    written, _ = render_pages(source, tmp_path / "pages")
    assert "יושר" in turn_note(written[0])


def test_api_human_fix_and_approve_empty(api):
    client, record = api
    field = record.fields["commitment_numbers"]
    field.values, field.reasons = ["111"], ["illegible"]
    body = client.patch(f"/api/records/{record.record_id}",
                        json={"field": "commitment_numbers", "values": ["111", "222"]}).json()
    served = body["records"][0]["fields"]["commitment_numbers"]
    assert served["values"] == ["111", "222"] and served["state"] == STATE_APPROVED
    assert served["reasons"] == [], "תיקון אנושי מנקה את הסימונים"

    empty = record.fields["central_numbers"]
    assert empty.reasons == ["not_on_form"]
    body = client.patch(f"/api/records/{record.record_id}",
                        json={"field": "central_numbers", "approve": True}).json()
    served = body["records"][0]["fields"]["central_numbers"]
    assert served["state"] == STATE_APPROVED and served["values"] == []


# ------------------------------------- 15/10/2026: one strong reader (Claude Opus 5.5)


def one_reader(*readings_):
    record = FormRecord.empty("b", SOURCE, 1)
    combine(record, readings_[0])
    validate_record(record, today=date(2026, 10, 14))
    return record


def test_single_reader_sure_value_keeps_its_normal_colour():
    r = one_reader(reading("claude-opus-5-5", commitment_numbers=["730578626"]))
    field = r.fields["commitment_numbers"]
    assert field.values == ["730578626"] and field.state == STATE_OK


@pytest.mark.parametrize("level, value", [("partial", "730578626"), ("clear", "7305?8626")])
def test_single_reader_any_doubt_is_orange(level, value):
    r = one_reader(reading("claude-opus-5-5", legibility={"commitment_numbers": level},
                           commitment_numbers=[value]))
    field = r.fields["commitment_numbers"]
    assert field.values == [value], "מה שנקרא נשאר גלוי"
    assert field.state == STATE_REVIEW


def test_single_reader_illegible_is_orange_and_empty():
    r = one_reader(reading("claude-opus-5-5", legibility={"first_name": "illegible"}))
    assert r.fields["first_name"].state == STATE_REVIEW
    assert "not_on_form" not in r.fields["first_name"].reasons


def test_single_reader_absent_is_not_on_form():
    r = one_reader(reading("claude-opus-5-5", legibility={"central_numbers": "absent"}))
    assert r.fields["central_numbers"].reasons == ["not_on_form"]
    assert r.fields["central_numbers"].state == STATE_REVIEW


def test_single_reader_keeps_every_value_and_local_rules_still_run():
    r = one_reader(reading("claude-opus-5-5", commitment_numbers=["111", "222"],
                           national_id=["0011223344"]))
    assert r.fields["commitment_numbers"].values == ["111", "222"]
    assert "id_length" in r.fields["national_id"].reasons, "הבדיקות המקומיות רצות גם עם קורא אחד"


def test_default_setup_is_one_opus_reader(monkeypatch):
    from app.config import load_settings

    for name in ("READER_MODEL", "JUDGE_A_MODEL", "READER_EFFORT", "JUDGE_EFFORT",
                 "JUDGE_B_EFFORT", "MAX_COST_PER_BATCH_USD"):
        monkeypatch.delenv(name, raising=False)
    s = load_settings()
    assert s.reader_model == "claude-opus-5-5"
    assert s.claude_effort == "medium"  # נועה, 22/10/2026 (היה high)
    assert s.max_cost_per_batch_usd == 10.0


def test_old_env_names_still_work(monkeypatch):
    from app.config import load_settings

    monkeypatch.delenv("READER_MODEL", raising=False)
    monkeypatch.delenv("READER_EFFORT", raising=False)
    monkeypatch.setenv("JUDGE_A_MODEL", "claude-sonnet-5-5")
    monkeypatch.setenv("JUDGE_EFFORT", "MEDIUM")
    s = load_settings()
    assert (s.reader_model, s.claude_effort) == ("claude-sonnet-5-5", "medium")


def test_build_reader_is_the_configured_claude_model(monkeypatch):
    from app.providers.claude_vision import ClaudeExtractionProvider

    monkeypatch.setattr(pipeline, "SETTINGS", _settings(reader_model="claude-opus-5-5", anthropic_api_key="k"))
    reader, problem = pipeline.build_reader()
    assert problem == "" and isinstance(reader, ClaudeExtractionProvider)
    assert reader.model == "claude-opus-5-5"

    monkeypatch.setattr(pipeline, "SETTINGS", _settings(anthropic_api_key=""))
    monkeypatch.setattr("app.providers.claude_vision.SETTINGS", _settings(anthropic_api_key=""))
    reader, problem = pipeline.build_reader()
    assert reader is None and "ANTHROPIC_API_KEY" in problem

def test_batch_with_one_reader_keeps_sure_values_in_normal_colour(tmp_path, monkeypatch):
    monkeypatch.setattr("app.store.BATCHES_DIR", tmp_path / "batches")
    judge = Judge("claude-opus-5-5", reading("claude-opus-5-5", commitment_numbers=["730578626"]))
    monkeypatch.setattr(pipeline, "build_reader", lambda: (judge, ""))
    batch = Batch(batch_id="bat_one")
    source = SourceFile(index=1, filename="f.png", kind="png", sha256="")
    batch.sources.append(source)
    store.put(batch)
    png(store.source_dir("bat_one", 1) / "source", size=(1000, 1400))
    pipeline._process("bat_one")
    record = batch.records[0]
    assert record.fields["commitment_numbers"].state == STATE_OK
    assert not batch.warnings
    store.delete("bat_one")


def test_prompt_v3_carries_the_challenge_rules():
    from app.providers.reading import PROMPT_NAME, load_prompt

    text = load_prompt("x")
    assert PROMPT_NAME == "extract_v3"
    for rule in ("שדה שלא נמצא נשאר ריק — לא מנוחש", "שומרים את כולם", '"דגימה"',
                 '"דרישה"', "התקבל", "רשימת הגורמים השולחים המוכרים", "העמוד הזה עומד לבד"):
        assert rule in text, rule


def test_opus_cache_reads_are_priced_at_its_own_rate():
    from app.config import cost_usd

    # 1M מהמטמון ב-Opus 5.5 = $0.20 (לא 10% מ-$4)
    assert cost_usd("claude-opus-5-5", 0, 0, cache_read_tokens=1_000_000) == pytest.approx(0.20)
    assert cost_usd("claude-sonnet-5-5", 0, 0, cache_read_tokens=1_000_000) == pytest.approx(0.20)
    assert cost_usd("claude-opus-5-5", 1_000_000, 1_000_000) == pytest.approx(24.0)


def test_zero_budget_cap_means_no_cap(tmp_path, monkeypatch):
    monkeypatch.setattr("app.store.BATCHES_DIR", tmp_path / "batches")
    monkeypatch.setattr(pipeline, "SETTINGS", _settings(max_cost_per_batch_usd=0.0))
    judge = Judge("claude-opus-5-5", reading("claude-opus-5-5"))
    batch = Batch(batch_id="bat_cap")
    pages = []
    for page in (1, 2):
        source = SourceFile(index=page, filename=f"{page}.png", kind="png", sha256="")
        batch.sources.append(source)
        pages.append((source, 1, png(tmp_path / f"p{page}.png")))
    results = pipeline._extract_all(Reader(judge), batch, pages)
    assert all(r is not None for r in results) and batch.budget_skipped_pages == 0



def test_prompt_lets_the_reader_recognise_cursive_names_but_flags_them():
    # 15/10/2026: "אל תזהה צורה של מילה" חסם את קריאת השם הרהוט. מותר לזהות מילה — וזה כתום.
    from app.providers.reading import load_prompt

    text = load_prompt("x")
    assert "אל תזהה \"צורה של מילה\"" not in text
    assert "אסור להשלים שם \"סביר\"" not in text
    assert "מזהים את המילה כולה" in text
    assert "כתב רהוט כמעט תמיד = `partial`" in text
    # ספרות נשארות קשוחות
    assert "ספרה שאינך קורא בוודאות → `?` במקומה" in text
    assert "ספרות שמתבלבלות בכתב יד: 1/7" in text


# --------------------------- 15/10/2026: saved answers survive a server restart


def _paid_answer(**values):
    from app.providers.base import Usage

    answer = reading("claude-opus-5-5", regions={"national_id": (0.1, 0.2, 0.3, 0.4)}, **values)
    answer.usage = Usage(100, 10, 0.05)
    return answer


def test_saved_answer_survives_a_restart_and_costs_nothing(tmp_path):
    pipeline._READ_CACHE.clear()
    source = SourceFile(index=1, filename="f.pdf", sha256="d" * 64)
    judge = Judge("claude-opus-5-5", _paid_answer(national_id=["001122334"]))
    image = png(tmp_path / "p.png")
    first = read_form(Reader(judge), image, "b", source, 1)

    pipeline._READ_CACHE.clear()  # כמו הפעלה מחדש של השרת
    second = read_form(Reader(judge), image, "b", source, 1)

    assert judge.calls == 1, "אחרי הפעלה מחדש — לא נשלח שוב למודל"
    assert first.cost_usd == pytest.approx(0.05) and second.cost_usd == 0.0
    assert second.fields["national_id"].values == ["001122334"]
    assert second.fields["national_id"].region == (0.1, 0.2, 0.3, 0.4)
    assert list(pipeline.READ_CACHE_DIR.glob("*.json")), "נשמר בקובץ"
    pipeline._READ_CACHE.clear()


def test_new_prompt_or_model_is_read_again_after_restart(tmp_path):
    pipeline._READ_CACHE.clear()
    source = SourceFile(index=1, filename="f.pdf", sha256="e" * 64)
    image = png(tmp_path / "p.png")
    old = Judge("claude-opus-5-5", _paid_answer())
    old.prompt_version = "extract_v3-aaaa"
    read_form(Reader(old), image, "b", source, 1)

    pipeline._READ_CACHE.clear()
    new = Judge("claude-opus-5-5", _paid_answer())
    new.prompt_version = "extract_v3-bbbb"
    read_form(Reader(new), image, "b", source, 1)
    assert new.calls == 1, "הוראות חדשות — קריאה חדשה, לא תשובה ישנה"
    pipeline._READ_CACHE.clear()


def test_damaged_saved_answer_is_just_read_again(tmp_path):
    pipeline._READ_CACHE.clear()
    source = SourceFile(index=1, filename="f.pdf", sha256="f0" * 32)
    image = png(tmp_path / "p.png")
    judge = Judge("claude-opus-5-5", _paid_answer())
    read_form(Reader(judge), image, "b", source, 1)
    for saved in pipeline.READ_CACHE_DIR.glob("*.json"):
        saved.write_text("{ שבור", encoding="utf-8")

    pipeline._READ_CACHE.clear()
    record = read_form(Reader(judge), image, "b", source, 1)
    assert judge.calls == 2 and not record.error
    pipeline._READ_CACHE.clear()


def test_file_without_fingerprint_is_not_saved(tmp_path):
    pipeline._READ_CACHE.clear()
    judge = Judge("claude-opus-5-5", _paid_answer())
    read_form(Reader(judge), png(tmp_path / "p.png"), "b", SOURCE, 1)
    assert not pipeline.READ_CACHE_DIR.exists() or not list(pipeline.READ_CACHE_DIR.glob("*.json"))


# ------------------- 16/10/2026: Tesseract OSD turns a sideways page before reading


def _with_auto_rotate(monkeypatch, found):
    from app import pages

    monkeypatch.setattr(pages, "SETTINGS", _settings(auto_rotate=True, straighten_pages=True))
    monkeypatch.setattr(pages, "detect_orientation", lambda image: found)


def test_sideways_page_is_turned_upright_before_reading(tmp_path, monkeypatch):
    from app.pages import render_pages, turn_note

    _with_auto_rotate(monkeypatch, (90, 11.7))
    source = tmp_path / "page.png"
    Image.new("RGB", (1000, 1400), "white").save(source)
    written, _ = render_pages(source, tmp_path / "pages")
    with Image.open(written[0]) as page:
        assert page.size == (1400, 1000), "סובב לרוחב — הטקסט ישר"
    assert turn_note(written[0]) == "הדף סובב ב-90° (זוהה אוטומטית)"


def test_turn_direction_follows_tesseract(monkeypatch):
    from app import pages

    page = Image.new("RGB", (100, 200), "white")
    page.putpixel((0, 0), (255, 0, 0))  # פינה שמאלית-עליונה
    monkeypatch.setattr(pages, "detect_orientation", lambda image: (90, 12.0))
    turned, degrees, decided = pages.auto_orient(page)
    # Tesseract: 90 = לסובב 90° בכיוון השעון. הפינה השמאלית-עליונה עוברת לימנית-עליונה.
    assert (degrees, decided) == (90, True)
    assert turned.size == (200, 100) and turned.getpixel((199, 0)) == (255, 0, 0)


@pytest.mark.parametrize("found", [(90, 2.0), None])
def test_unsure_or_missing_tesseract_leaves_the_page_alone(monkeypatch, found):
    from app import pages

    page = Image.new("RGB", (100, 200), "white")
    monkeypatch.setattr(pages, "detect_orientation", lambda image: found)
    turned, degrees, decided = pages.auto_orient(page)
    assert turned is page and degrees == 0 and decided is False


def test_upright_page_skips_the_old_sideways_guess(tmp_path, monkeypatch):
    """When Tesseract says "upright", the stripe heuristic must not turn a wide page."""
    from app.pages import render_pages, turn_note

    _with_auto_rotate(monkeypatch, (0, 20.0))
    source = tmp_path / "wide.png"
    _lines_page().rotate(90, expand=True).save(source)  # רחב, והשורות עומדות
    written, _ = render_pages(source, tmp_path / "pages")
    with Image.open(written[0]) as page:
        assert page.width > page.height, "Tesseract החליט — לא מנחשים מעליו"
    assert "90" not in turn_note(written[0])


def test_missing_tesseract_is_none_not_a_crash(monkeypatch):
    from app import pages

    monkeypatch.setattr(pages, "tesseract_command", lambda: None)
    assert pages.detect_orientation(Image.new("RGB", (50, 50), "white")) is None


def test_turned_page_does_not_reuse_an_answer_from_before_it_was_turned(tmp_path):
    from app.pages import turn_note_path

    pipeline._READ_CACHE.clear()
    source = SourceFile(index=1, filename="25.pdf", sha256="2" * 64)
    image = png(tmp_path / "page-001.png")
    old = Judge("claude-opus-5-5", _paid_answer())
    read_form(Reader(old), image, "b", source, 1)  # נקרא כשהדף היה על הצד

    turn_note_path(image).write_text("הדף סובב ב-90° (זוהה אוטומטית)", encoding="utf-8")
    pipeline._READ_CACHE.clear()
    new = Judge("claude-opus-5-5", _paid_answer())
    record = read_form(Reader(new), image, "b", source, 1)
    assert new.calls == 1, "דף ישר = תמונה חדשה; התשובה הישנה עם המיקומים השגויים לא חוזרת"
    assert "סובב" in record.straightened

    # דף שלא נגעו בו שומר על אותו מפתח — ולא עולה שוב
    pipeline._READ_CACHE.clear()
    again = Judge("claude-opus-5-5", _paid_answer())
    read_form(Reader(again), image, "b", source, 1)
    assert again.calls == 0
    pipeline._READ_CACHE.clear()


# ---------------- 16/10/2026: Excel — one row per commitment number (project requirement)


def _sheet(*records):
    import io

    batch = Batch(batch_id="x", sources=[SOURCE], records=list(records))
    ws = load_workbook(io.BytesIO(workbook_bytes(batch))).active
    headers = [c.value for c in ws[1]]
    return ws, headers


def test_three_commitments_become_three_identical_rows():
    record = one_reader(reading(
        "claude-opus-5-5", first_name=["ישראל"], last_name=["ישראלי"], national_id=["001122334"],
        central_numbers=["948062", "948063"], sample_received_date=["30/08/2026"],
        sending_entity=["מכבי"],
        commitment_numbers=["260005102632", "260005102611", "260005102644"],
    ))
    ws, headers = _sheet(record)
    col = {h: i + 1 for i, h in enumerate(headers)}
    rows = [r for r in range(2, ws.max_row + 1) if ws.cell(r, col["קובץ מקור"]).value]
    assert len(rows) == 3
    assert [ws.cell(r, col["מספר התחייבות"]).value for r in rows] == [
        "260005102632", "260005102611", "260005102644"]
    for name in ("שם פרטי", "שם משפחה", "תעודת זהות", "תאריך קבלת הדגימה", "גורם שולח",
                 "קובץ מקור", "עמוד בקובץ"):
        assert len({ws.cell(r, col[name]).value for r in rows}) == 1, name
    # כמה מספרים מרכזיים — נשארים יחד בתא אחד, בלי מכפלה של שורות
    assert {ws.cell(r, col["מספר מרכזי"]).value for r in rows} == {"948062 | 948063"}


def test_orange_repeats_on_every_row_of_the_form():
    record = one_reader(reading(
        "claude-opus-5-5", legibility={"first_name": "partial"}, first_name=["ישראל"],
        commitment_numbers=["111", "222"],
    ))
    ws, headers = _sheet(record)
    col = headers.index("שם פרטי") + 1
    assert all(ws.cell(r, col).fill.fill_type == "solid" for r in (2, 3))


def test_form_without_or_with_one_commitment_is_one_row():
    none = one_reader(reading("claude-opus-5-5", first_name=["א"]))
    one = one_reader(reading("claude-opus-5-5", first_name=["ב"], commitment_numbers=["730578626"]))
    ws, headers = _sheet(none, one)
    col = headers.index("שם פרטי") + 1
    assert [ws.cell(r, col).value for r in (2, 3)] == ["א", "ב"]
    assert ws.cell(4, col).value in (None, "")


def test_the_sheet_is_only_the_table():
    """No notes under the table (Noa, 22/10/2026): a header row and one row per commitment number."""
    record = one_reader(reading("claude-opus-5-5", commitment_numbers=["1", "2", "3"]))
    ws, _ = _sheet(record)
    assert ws.max_row == 1 + 3


# ---------- 16/10/2026: a page already read is free again, whatever file it comes in


def test_same_page_in_a_bigger_file_is_not_read_again(tmp_path):
    pipeline._READ_CACHE.clear()
    single = SourceFile(index=1, filename="25.pdf", sha256="a1" * 32)
    bundle = SourceFile(index=1, filename="all-26.pdf", sha256="b2" * 32)
    (tmp_path / "alone").mkdir()
    alone = png(tmp_path / "alone" / "page-001.png")
    inside = tmp_path / "bundle" / "page-007.png"
    inside.parent.mkdir()
    inside.write_bytes(alone.read_bytes())  # אותו עמוד, פיקסל לפיקסל

    judge = Judge("claude-opus-5-5", _paid_answer(national_id=["001122334"]))
    first = read_form(Reader(judge), alone, "b1", single, 1)
    pipeline._READ_CACHE.clear()  # גם אחרי הפעלה מחדש
    second = read_form(Reader(judge), inside, "b2", bundle, 7)

    assert judge.calls == 1, "הטופס כבר נקרא בנפרד — בקובץ הגדול הוא חוזר בחינם"
    assert first.cost_usd == pytest.approx(0.05) and second.cost_usd == 0.0
    assert second.fields["national_id"].values == ["001122334"]
    pipeline._READ_CACHE.clear()


def test_a_different_page_image_is_read(tmp_path):
    pipeline._READ_CACHE.clear()
    source = SourceFile(index=1, filename="f.pdf", sha256="c3" * 32)
    a = png(tmp_path / "a.png")
    b = tmp_path / "b.png"
    with Image.open(a) as page:
        page = page.copy()
    page.putpixel((5, 5), (0, 0, 0))  # פיקסל אחד שונה = סריקה אחרת
    page.save(b)
    judge = Judge("claude-opus-5-5", _paid_answer())
    read_form(Reader(judge), a, "b", source, 1)
    read_form(Reader(judge), b, "b", SourceFile(index=2, filename="g.pdf", sha256="d4" * 32), 1)
    assert judge.calls == 2
    pipeline._READ_CACHE.clear()


def test_answer_saved_under_the_old_key_is_still_free(tmp_path):
    pipeline._READ_CACHE.clear()
    source = SourceFile(index=1, filename="old.pdf", sha256="e5" * 32)
    image = png(tmp_path / "page-001.png")
    judge = Judge("claude-opus-5-5", _paid_answer(national_id=["001122334"]))
    from app.providers.reading import PROMPT_VERSION

    old_key = (source.sha256, 1, judge.model, None, PROMPT_VERSION)  # המפתח לפני השינוי
    pipeline._save(old_key, _paid_answer(national_id=["001122334"]))

    record = read_form(Reader(judge), image, "b", source, 1)
    assert judge.calls == 0 and record.cost_usd == 0.0
    assert record.fields["national_id"].values == ["001122334"]
    pipeline._READ_CACHE.clear()


# ------------- 16/10/2026: the full closed list (Wikipedia), prompt list frozen


@pytest.mark.parametrize("printed, expected", [
    ("בית החולים שמואל הרופא", "שמואל הרופא"),
    ("מרכז רפואי שמואל הרופא", "שמואל הרופא"),
    ("מרכז לבריאות הנפש גהה", "גהה"),
    ("בית החולים אסותא חיפה", "אסותא חיפה"),
    ("המרכז הרפואי אסותא אשדוד-כלניות", "אסותא אשדוד-כלניות"),
    ("אסותא אשדוד", "אסותא אשדוד"),
    ("בית החולים ספרא לילדים", "שיבא"),
    ("בית החולים רות רפפורט לילדים", 'רמב"ם'),
    ("המרכז הרפואי לשיקום לוינשטיין", "לוינשטיין"),
    ("בית החולים רפאל", "רפאל"),
    ("המרכז הרפואי לבריאות הנפש אברבנאל", "אברבנאל"),
    ("המרכז הרפואי לבריאות הנפש מעלה הכרמל", "מעלה הכרמל"),
    ("לב השרון", "לב השרון"),
    # מרפאה של כללית בשכונה — כללית, לא בית החולים הגריאטרי "קריית שלמה"
    ("קופת חולים כללית קריית שלמה ת\"א", "כללית"),
])
def test_full_list_maps_more_hospitals(printed, expected):
    assert canonical_sender(printed) == expected


@pytest.mark.parametrize("ordinary", [
    "באר שבע", "מרפאת רעות", "רפאל מערכות", "מרפאת אברבנאל", "מועצה אזורית לב השרון",
    "מרפאת מעלה הכרמל", "שער מנשה ד.נ. חפר",
    "צה\"ל", "משרד הביטחון", "הרשות הפלסטינית",
])
def test_ordinary_words_and_non_hospitals_stay_unknown(ordinary):
    assert canonical_sender(ordinary) is None, "לא בית חולים מהרשימה — נשאר כתום, לא מנוחש"


def test_prompt_list_is_frozen_so_saved_answers_stay_valid():
    from app.entities import PROMPT_SENDERS, SENDERS, senders_for_prompt

    assert len(PROMPT_SENDERS) == 39 and set(PROMPT_SENDERS) <= set(SENDERS)
    assert len(SENDERS) > len(PROMPT_SENDERS)
    assert "שמואל הרופא" not in senders_for_prompt(), "הרשימה המורחבת בקוד בלבד"


# -------------------- 16/10/2026: more field checks (local, never change a value)


def _checked(today=date(2026, 10, 16), **values):
    record = FormRecord.empty("b", SOURCE, 1)
    for key, value in values.items():
        record.fields[key].values = value if isinstance(value, list) else [value]
    validate_record(record, today=today)
    return record


@pytest.mark.parametrize("value, flagged", [
    ("31/02/2026", True), ("30/02/2026", True), ("28/02/2026", False), ("29/02/2028", False),
])
def test_received_date_must_exist_in_the_calendar(value, flagged):
    reasons = _checked(sample_received_date=value).fields["sample_received_date"].reasons
    assert ("impossible_date" in reasons) is flagged


@pytest.mark.parametrize("value, flagged", [("01/01/2015", True), ("09/10/2026", False)])
def test_very_old_received_date_is_flagged(value, flagged):
    reasons = _checked(sample_received_date=value).fields["sample_received_date"].reasons
    assert ("date_too_old" in reasons) is flagged


@pytest.mark.parametrize("value, reason", [
    ("ישראל3", "name_not_letters"), ("ישראל@", "name_not_letters"), ("A", "name_too_short"),
    ("ש", "name_too_short"),
])
def test_odd_names_are_flagged(value, reason):
    assert reason in _checked(first_name=value).fields["first_name"].reasons


@pytest.mark.parametrize("value", ["ישראל", "בן-דוד", "ג'ורג'", "שמו?ל", "אבו חצירא", "Israel"])
def test_ordinary_names_pass(value):
    reasons = _checked(last_name=value).fields["last_name"].reasons
    assert "name_not_letters" not in reasons and "name_too_short" not in reasons


@pytest.mark.parametrize("value, flagged", [("12", True), ("12345678", True), ("939376", False)])
def test_central_number_length(value, flagged):
    assert ("central_length" in _checked(central_numbers=value).fields["central_numbers"].reasons) is flagged


@pytest.mark.parametrize("value, flagged", [("5", True), ("2657", False), ("260005102632", False)])
def test_commitment_number_only_too_short_is_flagged(value, flagged):
    reasons = _checked(commitment_numbers=value).fields["commitment_numbers"].reasons
    assert ("commitment_too_short" in reasons) is flagged


def test_same_number_in_central_and_commitment_is_flagged_on_both():
    r = _checked(central_numbers="939376", commitment_numbers=["730578626", "939376"])
    assert "central_is_commitment" in r.fields["central_numbers"].reasons
    assert "central_is_commitment" in r.fields["commitment_numbers"].reasons


def test_id_equal_to_another_number_is_flagged():
    r = _checked(national_id="730578626", commitment_numbers="730578626")
    assert "id_is_other_number" in r.fields["national_id"].reasons
    assert "id_is_other_number" in r.fields["commitment_numbers"].reasons


def test_checks_never_change_a_value():
    r = _checked(national_id="1234567", sample_received_date="31/02/2026", first_name="ישראל3")
    assert r.fields["national_id"].values == ["1234567"], "לא משלימים אפסים"
    assert r.fields["sample_received_date"].values == ["31/02/2026"]
    assert r.fields["first_name"].values == ["ישראל3"]
    assert "id_length" in r.fields["national_id"].reasons



# ------------------------------------- 18/10/2026: UI upgrades — server side


def test_undo_restores_value_state_and_reasons(api):
    client, record = api
    field = record.fields["commitment_numbers"]
    field.values, field.state, field.reasons = ["111"], STATE_REVIEW, ["illegible"]
    client.patch(f"/api/records/{record.record_id}", json={"field": "commitment_numbers", "values": ["222"]})
    assert field.values == ["222"] and field.state == STATE_APPROVED

    body = client.post(f"/api/records/{record.record_id}/undo", json={"field": "commitment_numbers"}).json()
    served = body["records"][0]["fields"]["commitment_numbers"]
    assert served["values"] == ["111"] and served["state"] == STATE_REVIEW
    assert served["reason_codes"] == ["illegible"]
    assert client.post(f"/api/records/{record.record_id}/undo",
                       json={"field": "commitment_numbers"}).status_code == 409, "אין עוד מה לבטל"


def test_undo_of_an_approval(api):
    client, record = api
    field = record.fields["central_numbers"]
    field.state, field.reasons = STATE_REVIEW, ["not_on_form"]
    client.patch(f"/api/records/{record.record_id}", json={"field": "central_numbers", "approve": True})
    assert field.state == STATE_APPROVED
    client.post(f"/api/records/{record.record_id}/undo", json={"field": "central_numbers"})
    assert field.state == STATE_REVIEW and field.reasons == ["not_on_form"]


def test_thumbnail_is_small_and_made_once(api):
    client, record = api
    url = "/api/batches/bat_api/sources/1/pages/1/thumb"
    res = client.get(url)
    assert res.status_code == 200 and res.headers["content-type"] == "image/jpeg"
    import io

    with Image.open(io.BytesIO(res.content)) as thumb:
        assert thumb.width <= 240
    made = pipeline.page_image_path("bat_api", 1, 1).with_name("page-001.thumb.jpg")
    first = made.stat().st_mtime_ns
    client.get(url)
    assert made.stat().st_mtime_ns == first, "נוצרת פעם אחת ונשמרת"
    assert client.get("/api/batches/bat_api/sources/1/pages/9/thumb").status_code == 404


def test_records_appear_while_the_rest_are_read_and_end_in_page_order(tmp_path):
    import threading

    gate = threading.Event()

    class SlowFirst:
        model = "claude-opus-5-5"

        def __init__(self):
            self.seen = []

        def extract(self, image_path):
            if "p1" in image_path.name:
                gate.wait(5)  # העמוד הראשון מתעכב
            return reading("claude-opus-5-5", commitment_numbers=[image_path.stem])

    batch = Batch(batch_id="bat_live")
    pages = []
    for i in (1, 2, 3):
        source = SourceFile(index=i, filename=f"{i}.png", kind="png", sha256="")
        batch.sources.append(source)
        pages.append((source, 1, png(tmp_path / f"p{i}.png")))
    batch.pages = [{"source_index": i, "page_number": 1, "status": "queued", "record_id": ""} for i in (1, 2, 3)]

    worker = threading.Thread(target=pipeline._extract_all, args=(Reader(SlowFirst()), batch, pages))
    worker.start()
    deadline = __import__("time").time() + 5
    while len(batch.records) < 2 and __import__("time").time() < deadline:
        __import__("time").sleep(0.02)
    assert len(batch.records) == 2, "שני טפסים מוכנים — מופיעים לפני שהראשון נגמר"
    assert batch.pages[0]["status"] == "reading"
    gate.set()
    worker.join(5)
    assert [r.source_index for r in batch.records] == [1, 2, 3], "בסוף — לפי סדר העמודים"
    assert [p["status"] for p in batch.pages] == ["done"] * 3
    assert all(p["record_id"] for p in batch.pages)


def test_sample_form_comes_from_saved_answers_without_a_paid_call(tmp_path, monkeypatch):
    from app import api as api_module

    monkeypatch.setattr("app.config.BATCHES_DIR", tmp_path / "batches")
    monkeypatch.setattr("app.store.BATCHES_DIR", tmp_path / "batches")
    sample = tmp_path / "demo-form.png"
    png(sample, size=(1700, 2400))
    monkeypatch.setattr(api_module, "SAMPLE_PATH", sample)

    class NoMoney:
        model = "claude-opus-5-5"
        prompt_version = "extract_v3-test"
        calls = 0

        def extract(self, image_path):
            NoMoney.calls += 1
            return reading("claude-opus-5-5", commitment_numbers=["730578626"])

    monkeypatch.setattr(pipeline, "build_reader", lambda: (NoMoney(), ""))
    monkeypatch.setattr(pipeline, "start", lambda batch_id: pipeline._process(batch_id))
    client = TestClient(api_module.app)
    assert client.get("/api/config").json()["sample"] is True

    first = client.post("/api/batches/sample").json()["batch_id"]
    second = client.post("/api/batches/sample").json()["batch_id"]
    assert NoMoney.calls == 1, "הפעם השנייה — מהתשובה השמורה, בלי קריאה למודל"
    for batch_id in (first, second):
        data = client.get(f"/api/batches/{batch_id}").json()
        assert data["records"][0]["fields"]["commitment_numbers"]["values"] == ["730578626"]
        assert data["pages"][0]["status"] == "done"
        store.delete(batch_id)


def test_no_sample_button_without_a_sample_file(monkeypatch, tmp_path):
    from app import api as api_module

    monkeypatch.setattr(api_module, "SAMPLE_PATH", tmp_path / "missing.pdf")
    client = TestClient(api_module.app)
    assert client.get("/api/config").json()["sample"] is False
    assert client.post("/api/batches/sample").status_code == 404


def test_index_serves_split_static_files():
    import re

    from app import api as api_module

    client = TestClient(api_module.app)
    html = client.get("/").text
    # המסך ב-React: אותו קובץ עיצוב (/static/app.css), והקוד מהבנייה (/app/assets/...)
    assert "/static/app.css?v=" in html and "{{v}}" not in html
    for asset in re.findall(r'(/app/assets/[^"]+\.js)', html):
        assert client.get(asset).status_code == 200
    assert client.get("/static/app.css").status_code == 200
    # המסך הקודם (JavaScript רגיל) כבר לא מוגש — רק React
    assert client.get("/classic").status_code == 404
    assert "/static/app.js" not in html


# ---------------- 18/10/2026: Ministry of Health branding through a theme


def test_brand_defaults_to_ministry_and_can_be_neutral(monkeypatch):
    from app.config import load_settings

    monkeypatch.delenv("BRAND", raising=False)
    assert load_settings().brand == "moh"
    monkeypatch.setenv("BRAND", "neutral")
    assert load_settings().brand == "neutral"
    monkeypatch.setenv("BRAND", "something-else")
    assert load_settings().brand == "moh"


def test_page_shows_ministry_branding_without_a_demo_label():
    from app import api as api_module

    client = TestClient(api_module.app)
    import re

    html = client.get("/").text
    # React מצייר את המסך בדפדפן — המיתוג נמצא בקוד שהדף טוען
    code = html + "".join(client.get(a).text for a in re.findall(r'(/app/assets/[^"]+\.js)', html))
    assert "/static/brand/moh-office.png" in code and "משרד הבריאות" in code
    assert "דמו להאקתון" not in code
    assert client.get("/static/brand/moh-office.png").status_code == 200
    # the gov.il typeface ships with the app, so the page looks right without internet access
    assert client.get("/static/fonts/rubik-hebrew.woff2").status_code == 200
    assert client.get("/api/config").json()["brand"] in ("moh", "neutral")


def test_the_default_thinking_level_is_medium(monkeypatch):
    """Noa, 22/10/2026: medium instead of high — faster. .env can still choose another level."""
    from app import config

    for name in ("READER_EFFORT", "JUDGE_EFFORT", "JUDGE_B_EFFORT"):
        monkeypatch.delenv(name, raising=False)
    assert config.load_settings().claude_effort == "medium"
    assert config.Settings.__dataclass_fields__["claude_effort"].default == "medium"
