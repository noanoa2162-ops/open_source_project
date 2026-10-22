"""Speed and cost (cache warm-up, thinking per model, 1-hour cache price) and the barcode reader.

No network: the model is a stand-in. Every number these tests protect was measured on the
organizers' 48 sample pages on 22/10/2026 (see EVALUATION.md)."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest
from PIL import Image

from app import barcodes
from app.config import cost_usd
from app.models import STATE_OK, STATE_REVIEW, FormRecord, SourceFile
from app.providers import claude_vision as cv


# ------------------------------------------------------------------ thinking per model
def test_thinking_off_where_the_model_allows_it():
    assert cv.thinking_for("claude-haiku-5-5", "low") == {"type": "disabled"}
    assert cv.thinking_for("claude-sonnet-5-5", "medium") == {"type": "between_tools"}
    assert cv.thinking_for("claude-opus-5-5", "medium") is None, "ב-Opus 5.5 אי אפשר לכבות חשיבה — 400"
    assert cv.thinking_for("claude-haiku-5-5", "xhigh") is None, "disabled נדחה מעל high"
    assert cv.thinking_for("claude-sonnet-5-5", "low", "on") is None


def _reading():
    from app.providers.reading import FieldReading, PageReading

    empty = FieldReading(values=[], legibility="absent")
    return PageReading(template="t", first_name=empty, last_name=empty, national_id=empty,
                       central_numbers=empty, sample_received_date=empty, sending_entity=empty,
                       commitment_numbers=empty)


class _Stream:
    """A streamed answer whose first event arrives after `delay` seconds."""

    def __init__(self, delay=0.0):
        self.delay = delay

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        time.sleep(self.delay)
        yield SimpleNamespace(type="message_start")

    def get_final_message(self):
        return SimpleNamespace(stop_reason="end_turn", parsed_output=_reading(), model="claude-opus-5-5", usage=None)


def _page(tmp_path, name="p.png"):
    path = tmp_path / name
    Image.new("RGB", (1200, 1700), "white").save(path)
    return path


def test_haiku_request_has_no_server_fallback_and_no_thinking(tmp_path):
    sent = {}

    def stream(**kwargs):
        sent.update(kwargs)
        return _Stream()

    reader = cv.ClaudeExtractionProvider(api_key="test", model="claude-haiku-5-5", effort="low")
    reader._client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))
    reader.extract(_page(tmp_path))
    assert sent["thinking"] == {"type": "disabled"}
    assert "fallbacks" not in sent and "betas" not in sent, "ל-Haiku 5.5 אין גיבוי בצד השרת"
    assert sent["max_tokens"] == cv.MAX_OUTPUT_TOKENS_NO_THINKING


def test_prompt_cache_lives_an_hour_by_default(tmp_path):
    sent = {}

    def stream(**kwargs):
        sent.update(kwargs)
        return _Stream()

    reader = cv.ClaudeExtractionProvider(api_key="test")
    reader._client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))
    reader.extract(_page(tmp_path))
    assert sent["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


# ------------------------------------------------------------------ cache warm-up
def test_first_page_writes_the_cache_alone_then_the_rest_go_together(tmp_path):
    """10 pages at once used to write the 14k-token prompt 10 times (≈7¢ each on Opus).
    Now one goes first; the others leave the moment its answer starts streaming."""
    started: list[float] = []
    lock = threading.Lock()

    def stream(**kwargs):
        with lock:
            started.append(time.monotonic())
        return _Stream(delay=0.4)

    reader = cv.ClaudeExtractionProvider(api_key="test")
    reader._client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))
    pages = [_page(tmp_path, f"p{i}.png") for i in range(5)]
    threads = [threading.Thread(target=reader.extract, args=(p,)) for p in pages]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    started.sort()
    assert len(started) == 5
    assert started[1] - started[0] >= 0.35, "השאר מחכים עד שהראשון מתחיל לזרום"
    assert started[-1] - started[1] < 0.35, "ואז יוצאים יחד, במקביל"


def test_a_failing_first_page_never_blocks_the_others(tmp_path):
    import anthropic

    calls = []

    def stream(**kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise anthropic.APIConnectionError(request=SimpleNamespace(method="POST", url="x"))
        return _Stream()

    reader = cv.ClaudeExtractionProvider(api_key="test")
    reader._client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))
    first = threading.Thread(target=lambda: pytest.raises(Exception, reader.extract, _page(tmp_path, "a.png")))
    first.start()
    time.sleep(0.05)
    t0 = time.monotonic()
    reader.extract(_page(tmp_path, "b.png"))
    first.join(5)
    assert time.monotonic() - t0 < 5, "כשל של הראשון משחרר מיד את השאר"


def test_gate_states():
    gate = cv.CacheGate(warm_seconds=60)
    assert gate.enter() == "lead"
    assert gate.enter() == "wait"
    gate.started()
    assert gate.enter() == "go", "המטמון חם — בלי המתנה"
    gate.release()
    expired = cv.CacheGate(warm_seconds=0)
    assert expired.enter() == "lead"
    expired.started()
    expired.release()
    assert expired.enter() == "lead", "אחרי שהתוקף עבר — שוב עמוד ראשון לבד"


# ------------------------------------------------------------------ price of the 1-hour cache
def test_one_hour_cache_write_costs_twice_the_input_price():
    five = cost_usd("claude-opus-5-5", 0, 0, cache_write_tokens=1_000_000)
    hour = cost_usd("claude-opus-5-5", 0, 0, cache_write_1h_tokens=1_000_000)
    assert five == pytest.approx(5.0) and hour == pytest.approx(8.0)


def test_usage_splits_one_hour_writes():
    usage = SimpleNamespace(input_tokens=4500, output_tokens=1000, cache_read_input_tokens=0,
                            cache_creation_input_tokens=14_000,
                            cache_creation=SimpleNamespace(ephemeral_1h_input_tokens=14_000,
                                                           ephemeral_5m_input_tokens=0))
    got = cv._usage("claude-opus-5-5", SimpleNamespace(usage=usage))
    expected = (4500 * 4 + 14_000 * 8 + 1000 * 20) / 1_000_000
    assert got.cost_usd == pytest.approx(expected)


# ------------------------------------------------------------------ barcodes
def _record(values: dict[str, list[str]], regions: dict | None = None, review: dict | None = None) -> FormRecord:
    record = FormRecord.empty("b", SourceFile(index=1, filename="f"), 1)
    for key, vals in values.items():
        field = record.fields[key]
        field.values = vals
        if regions and key in regions:
            field.region = regions[key]
            field.value_regions = [regions[key]] * len(vals) if len(vals) > 1 else []
        if review and key in review:
            field.flag(review[key])
    return record


STICKER = barcodes.Barcode(text="939376", kind="Code128", region=(0.20, 0.03, 0.30, 0.05))
NEAR = (0.20, 0.05, 0.30, 0.07)  # המספר המודפס מתחת לברקוד
FAR = (0.70, 0.80, 0.90, 0.83)


def test_barcode_vouches_for_the_digits_but_never_clears_orange():
    record = _record({"central_numbers": ["939376"]}, {"central_numbers": NEAR}, {"central_numbers": "illegible"})
    barcodes.apply(record, [STICKER])
    field = record.fields["central_numbers"]
    assert field.confirmed_by == ["barcode"]
    assert field.state == STATE_REVIEW and "illegible" in field.reasons, (
        "ספרות נכונות ≠ שדה נכון: נמדד מספר התחייבות (ברקוד) שנקרא כמרכזי")


def test_barcode_catches_a_misread_digit_next_to_it():
    record = _record({"central_numbers": ["939378"]}, {"central_numbers": NEAR})
    assert record.fields["central_numbers"].state == STATE_OK
    barcodes.apply(record, [STICKER])
    field = record.fields["central_numbers"]
    assert field.state == STATE_REVIEW and "barcode_differs" in field.reasons
    assert field.suggestions == ["939376"]
    assert field.values == ["939378"], "הברקוד לא כותב ערך — רק מציע"
    assert field.confirmed_by == []


def test_barcode_fills_an_unread_digit_as_a_suggestion():
    record = _record({"central_numbers": ["93?376"]}, {"central_numbers": NEAR}, {"central_numbers": "unknown_char"})
    barcodes.apply(record, [STICKER])
    assert record.fields["central_numbers"].suggestions == ["939376"]


def test_a_barcode_far_from_the_value_suggests_nothing():
    record = _record({"commitment_numbers": ["939378"]}, {"commitment_numbers": FAR})
    barcodes.apply(record, [STICKER])
    assert record.fields["commitment_numbers"].suggestions == []
    assert record.fields["commitment_numbers"].state == STATE_OK


def test_empty_number_field_is_offered_the_unassigned_barcodes():
    record = _record({"central_numbers": [], "commitment_numbers": ["730578626"]})
    record.fields["central_numbers"].flag("not_on_form")
    commitment = barcodes.Barcode(text="730578626", kind="Code39", region=(0.1, 0.2, 0.3, 0.22))
    barcodes.apply(record, [STICKER, commitment])
    central = record.fields["central_numbers"]
    assert central.suggestions == ["939376"], "רק ברקוד שלא שויך לשדה אחר"
    assert "barcode_candidates" in central.reasons and central.state == STATE_REVIEW


def test_maccabi_tube_suffix_still_matches_the_demand_number():
    record = _record({"commitment_numbers": ["50364186"]})
    barcodes.apply(record, [barcodes.Barcode("50364186700", "Code128", (0.1, 0.1, 0.2, 0.12))])
    assert record.fields["commitment_numbers"].confirmed_by == ["barcode"]


def test_picking_a_suggestion_keeps_the_mark_and_spends_the_suggestions():
    record = _record({"central_numbers": ["939378"]}, {"central_numbers": NEAR})
    barcodes.apply(record, [STICKER])
    record.fields["central_numbers"].values = ["939376"]
    barcodes.after_person(record, "central_numbers")
    field = record.fields["central_numbers"]
    assert field.suggestions == [] and field.confirmed_by == ["barcode"]


def test_real_barcode_is_decoded_from_the_page(tmp_path):
    zxing = pytest.importorskip("zxingcpp")
    code = Image.fromarray(zxing.create_barcode("945504", zxing.BarcodeFormat.Code128).to_image(scale=3))
    page = Image.new("L", (2000, 2800), 255)
    page.paste(code, (400, 300))
    path = tmp_path / "page-001.png"
    page.save(path)
    found = barcodes.read(path)
    assert [b.text for b in found] == ["945504"]
    x0, y0, x1, y1 = found[0].region
    assert 0.19 < x0 < 0.23 and 0.10 < y0 < 0.12  # אחרי "אזור השקט" של הברקוד
    assert barcodes.sidecar(path).is_file(), "נשמר ליד העמוד — מפוענח פעם אחת"


def test_barcode_problems_never_break_the_reading(tmp_path):
    path = tmp_path / "broken.png"
    path.write_bytes(b"not an image")
    assert barcodes.read(path) == []


def test_saved_answers_are_kept_apart_by_thinking_depth(tmp_path, monkeypatch):
    from app import pipeline
    from app.providers.base import RawExtraction

    monkeypatch.setattr(pipeline, "READ_CACHE_DIR", tmp_path / "cache")
    pipeline._READ_CACHE.clear()
    page = _page(tmp_path)
    source = SourceFile(index=1, filename="f", sha256="a" * 64)
    calls = []

    class Reader:
        model = "claude-opus-5-5"
        prompt_version = "v"

        def __init__(self, effort):
            self.effort = effort

        def extract(self, path):
            calls.append(self.effort)
            return RawExtraction(model=self.model)

    pipeline._read(Reader("medium"), page, source, 1)
    pipeline._read(Reader("medium"), page, source, 1)
    pipeline._read(Reader("low"), page, source, 1)
    assert calls == ["medium", "low"], "אותה רמה — מהשמור; רמה אחרת — קריאה משלה"


# ------------------------------------------------------------------ sender vs. the form's own type
def test_kupa_as_sender_on_a_hospital_form_goes_orange():
    from app.validators import validate_record

    record = _record({"sending_entity": ["כללית"]})
    record.template = "טופס כללית - בקשה לבדיקת מעבדה מחוץ לבית החולים"
    validate_record(record)
    field = record.fields["sending_entity"]
    assert "kupa_on_hospital_form" in field.reasons and field.state == STATE_REVIEW
    assert field.values == ["כללית"], "רק מסמן — לא מחליף"


def test_kupa_form_and_screen_capture_are_not_questioned():
    from app.validators import validate_record

    for template in ("טופס התחייבות מכבי", "צילום מסך מערכת אשפוז (SAP)", "טופס 17 של כללית"):
        record = _record({"sending_entity": ["כללית"]})
        record.template = template
        validate_record(record)
        assert "kupa_on_hospital_form" not in record.fields["sending_entity"].reasons, template
    hospital = _record({"sending_entity": ["מאיר"]})
    hospital.template = "בקשה לבדיקה מחוץ לבית החולים - מרכז רפואי מאיר"
    validate_record(hospital)
    assert "kupa_on_hospital_form" not in hospital.fields["sending_entity"].reasons


# ------------------------------------------------------------------ Gemini adapter (no network)
def test_gemini_boxes_are_ymin_xmin_on_a_thousand_scale():
    from app.providers import gemini_vision as g

    assert g._region_of([100, 200, 150, 400]) == pytest.approx((0.2, 0.1, 0.4, 0.15))
    assert g._region_of(None) is None and g._region_of([1, 2, 3]) is None


