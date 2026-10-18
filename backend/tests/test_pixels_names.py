"""Positions in pixels, a box for every value, and the close-up second reading of
the name. No model is called: fake readers and clients stand in."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import locate, pipeline, store
from app.consensus import combine, merge_names
from app.locate import Word, check_regions
from app.models import STATE_APPROVED, STATE_OK, STATE_REVIEW, Batch, FormRecord, SourceFile
from app.providers import claude_vision as cv
from app.providers.base import ExtractionError, RawExtraction, Usage
from app.providers.reading import FieldReading, NameCheck, PageReading, to_raw

SOURCE = SourceFile(index=1, filename="form.png", kind="png")


def _page(path: Path, size=(1000, 1414)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, "white").save(path)
    return path


def _reading(**fields) -> PageReading:
    empty = FieldReading(values=[], legibility="absent")
    data = {k: fields.get(k, empty) for k in (
        "first_name", "last_name", "national_id", "central_numbers",
        "sample_received_date", "sending_entity", "commitment_numbers")}
    return PageReading(template="t", **data)


# ---------------------------------------------------------------- pixels


def test_positions_are_read_in_pixels_of_the_sent_image():
    assert cv._region_of([200, 560, 400, 700], (2000, 2800)) == pytest.approx((0.1, 0.2, 0.2, 0.25))
    # גובה ורוחב לא מתבלבלים: אותה תיבה בתמונה לרוחב נותנת אזור אחר
    assert cv._region_of([200, 560, 400, 700], (2800, 2000))[1] == pytest.approx(0.28)
    assert cv._region_of([200, 5600, 400, 7000], (2000, 2800)) is None, "מחוץ לתמונה — לא ממציאים"


def test_the_message_tells_the_reader_the_image_size(tmp_path):
    sent = {}

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get_final_message(self):
            return SimpleNamespace(stop_reason="end_turn", parsed_output=_reading(), model="m", usage=None)

    def stream(**kwargs):
        sent.update(kwargs)
        return Stream()

    judge = cv.ClaudeExtractionProvider(api_key="test")
    judge._client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))
    judge.extract(_page(tmp_path / "p.png", size=(1000, 1414)))
    texts = [c["text"] for c in sent["messages"][0]["content"] if c["type"] == "text"]
    assert "רוחב 1000" in texts[0] and "גובה 1414" in texts[0], "גודל העמוד המלא"
    assert all("פיקסלים" in t for t in texts[:5]), "גודל כל רבע"
    assert "בפיקסלים" in judge._prompt and "0–1000" not in judge._prompt


def test_prompt_changed_so_old_answers_are_not_reused():
    judge = cv.ClaudeExtractionProvider(api_key="test")
    assert not judge.prompt_version.endswith("325df645")


# ---------------------------------------------------------------- a box for every value


def test_each_value_gets_its_own_region_and_the_field_their_union():
    reading = _reading(central_numbers=FieldReading(
        values=["947468", "947469"], legibility="clear",
        boxes=[[0, 70, 470, 160, 520], [0, 70, 530, 160, 575]]))
    raw = to_raw(reading, "m", lambda b, t: cv._region_of(b, (1000, 1000)), None)
    one, two = raw.value_regions["central_numbers"]
    assert one == pytest.approx((0.07, 0.47, 0.16, 0.52)) and two == pytest.approx((0.07, 0.53, 0.16, 0.575))
    assert raw.regions["central_numbers"] == pytest.approx((0.07, 0.47, 0.16, 0.575))


def test_an_old_answer_with_one_box_still_works():
    reading = _reading(national_id=FieldReading(values=["11223344"], legibility="partial", box=[100, 200, 300, 220]))
    raw = to_raw(reading, "m", lambda b, t: cv._region_of(b, (1000, 1000)), None)
    assert raw.regions["national_id"] == pytest.approx((0.1, 0.2, 0.3, 0.22))
    assert raw.value_regions["national_id"] == []


def test_value_positions_stay_aligned_after_repeats_are_removed():
    raw = RawExtraction(values={"central_numbers": ["947468", "947468", "947469"]},
                        value_regions={"central_numbers": [(0.1, 0.1, 0.2, 0.12), (0.5, 0.5, 0.6, 0.52), (0.1, 0.3, 0.2, 0.32)]},
                        legibility={"central_numbers": "clear"})
    record = FormRecord.empty("b", SOURCE, 1)
    combine(record, raw)
    field = record.fields["central_numbers"]
    assert field.values == ["947468", "947469"]
    assert field.value_regions == [(0.1, 0.1, 0.2, 0.12), (0.1, 0.3, 0.2, 0.32)]


def test_box_around_two_numbers_still_reveals_a_whole_page_shift(monkeypatch, tmp_path):
    # כמו בעמוד של וולפסון: תיבה אחת סביב שני מספרים, מוזזת ביחס הגובה/רוחב
    monkeypatch.setattr(locate, "page_words", lambda _: [Word("947468", (0.07, 0.47, 0.155, 0.52))])
    k = 1414 / 1000
    record = FormRecord.empty("b", SOURCE, 1)
    record.fields["central_numbers"].values = ["947468", "947469"]
    record.fields["central_numbers"].region = (0.07, 0.47 * k, 0.16, 0.575 * k)
    record.fields["first_name"].values = ["יוסי"]
    record.fields["first_name"].region = (0.45, 0.31 * k, 0.52, 0.325 * k)
    check_regions(record, _page(tmp_path / "p.png"))
    assert record.fields["first_name"].region[1] == pytest.approx(0.31, abs=0.01)


def test_api_serves_a_close_up_for_each_value(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.BATCHES_DIR", tmp_path / "batches")
    monkeypatch.setattr("app.store.BATCHES_DIR", tmp_path / "batches")
    from app.api import app

    batch = Batch(batch_id="bat_pv", status="completed")
    batch.sources.append(SOURCE)
    _page(pipeline.page_image_path("bat_pv", 1, 1))
    record = FormRecord.empty("bat_pv", SOURCE, 1)
    field = record.fields["central_numbers"]
    field.values = ["947468", "947469"]
    field.value_regions = [(0.07, 0.47, 0.16, 0.52), (0.07, 0.53, 0.16, 0.575)]
    field.region = (0.07, 0.47, 0.16, 0.575)
    batch.records.append(record)
    store.put(batch)
    client = TestClient(app)
    served = client.get("/api/batches/bat_pv").json()["records"][0]["fields"]["central_numbers"]
    assert len(served["crop_boxes"]) == 2
    base = "/api/batches/bat_pv/sources/1/pages/1/crops/central_numbers"
    first, second = client.get(base + "?i=0"), client.get(base + "?i=1")
    assert first.status_code == second.status_code == 200
    assert client.get(base + "?i=5").status_code == 404
    # תיקון שמשנה את מספר הערכים — המיקומים לפי ערך נמחקים, לא מוצגים בטעות
    client.patch(f"/api/records/{record.record_id}", json={"field": "central_numbers", "values": ["947468"]})
    assert record.fields["central_numbers"].value_regions == []
    store.delete("bat_pv")


# ---------------------------------------------------------------- names: second, close-up reading


def _names_record(state=STATE_REVIEW, first=("ישראל",), last=("ישראלי",)) -> FormRecord:
    record = FormRecord.empty("b", SOURCE, 1)
    for key, values in (("first_name", first), ("last_name", last)):
        record.fields[key].values = list(values)
        record.fields[key].region = (0.40, 0.30, 0.55, 0.32) if key == "first_name" else (0.20, 0.30, 0.35, 0.32)
        record.fields[key].state = state
    return record


def test_same_reading_twice_stays_orange_with_a_reason():
    record = _names_record()
    assert merge_names(record, NameCheck(found=True, first_name=["ישראל"], last_name=["ישראלי"]))
    for key in ("first_name", "last_name"):
        assert record.fields[key].state == STATE_REVIEW
        assert "names_read_twice_same" in record.fields[key].reasons


def test_different_reading_shows_both_close_up_first():
    record = _names_record(first=("ספי",))
    merge_names(record, NameCheck(found=True, first_name=["ישראל"], last_name=["ישראלי"]))
    field = record.fields["first_name"]
    assert field.values == ["ישראל", "ספי"]
    assert field.state == STATE_REVIEW and "names_two_readings" in field.reasons


def test_close_up_without_a_name_changes_nothing():
    record = _names_record(first=("ספי",))
    assert not merge_names(record, NameCheck(found=False, first_name=["ישראל"]))
    assert record.fields["first_name"].values == ["ספי"]


def test_a_certain_name_never_turns_green_and_is_not_marked():
    record = _names_record(state=STATE_OK)
    merge_names(record, NameCheck(found=True, first_name=["ישראל"], last_name=["ישראלי"]))
    assert record.fields["first_name"].state == STATE_OK
    assert record.fields["first_name"].reasons == []


class NameReader:
    model = "stub"
    names_version = "names-test"

    def __init__(self, fail=False):
        self.calls, self.fail = 0, fail

    def read_names(self, crop: Path) -> NameCheck:
        self.calls += 1
        if self.fail:
            raise ExtractionError("נחסם")
        with Image.open(crop) as image:
            assert image.width > 100, "התקריב נחתך מהעמוד המלא"
        return NameCheck(found=True, first_name=["ישראל"], last_name=["ישראלי"],
                         usage=Usage(cost_usd=0.04))


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.BATCHES_DIR", tmp_path / "batches")
    monkeypatch.setattr("app.store.BATCHES_DIR", tmp_path / "batches")
    monkeypatch.setattr(pipeline, "READ_CACHE_DIR", tmp_path / "cache")
    return _page(pipeline.page_image_path("b", 1, 1), size=(2480, 3508))


def test_second_look_runs_only_for_orange_names_and_is_paid_once(isolated):
    reader = NameReader()
    record = _names_record(first=("ספי",))
    record.cost_usd = 0.14
    pipeline.look_again_at_names(reader, record, isolated, "b", SOURCE, 1)
    assert reader.calls == 1
    assert record.fields["first_name"].values[0] == "ישראל"
    assert record.cost_usd == pytest.approx(0.18), "העלות של השלב השני נספרת"
    # אותו תקריב שוב — מהתשובות השמורות, בלי לשלם
    again = _names_record(first=("ספי",))
    pipeline.look_again_at_names(reader, again, isolated, "b", SOURCE, 1)
    assert reader.calls == 1 and again.fields["first_name"].values[0] == "ישראל"


def test_no_second_look_when_the_name_is_certain_or_has_no_place(isolated):
    reader = NameReader()
    pipeline.look_again_at_names(reader, _names_record(state=STATE_OK), isolated, "b", SOURCE, 1)
    pipeline.look_again_at_names(reader, _names_record(state=STATE_APPROVED), isolated, "b", SOURCE, 1)
    nowhere = _names_record()
    for key in ("first_name", "last_name"):
        nowhere.fields[key].region = None
    pipeline.look_again_at_names(reader, nowhere, isolated, "b", SOURCE, 1)
    assert reader.calls == 0


def test_a_failed_second_look_keeps_the_first_reading(isolated):
    record = _names_record(first=("ספי",))
    pipeline.look_again_at_names(NameReader(fail=True), record, isolated, "b", SOURCE, 1)
    assert record.fields["first_name"].values == ["ספי"]


def test_a_reader_without_the_second_step_is_simply_skipped(isolated):
    record = _names_record(first=("ספי",))
    pipeline.look_again_at_names(SimpleNamespace(model="x"), record, isolated, "b", SOURCE, 1)
    assert record.fields["first_name"].values == ["ספי"]


def test_reading_a_form_does_not_send_the_name_again(isolated, monkeypatch):
    """הקריאה השנייה של השם כבויה (החלטת נועה, 18/10): טופס נקרא פעם אחת בלבד."""
    reader = NameReader()
    reader.extract = lambda image_path: RawExtraction(
        values={"first_name": ["ספי"], "last_name": ["ישראלי"]},
        legibility={"first_name": "partial", "last_name": "partial"},
        regions={"first_name": (0.4, 0.3, 0.55, 0.32), "last_name": (0.2, 0.3, 0.35, 0.32)}, model="stub")
    monkeypatch.setattr("app.pipeline.check_regions", lambda record, path: None)
    record = pipeline.read_form(pipeline.Reader(reader), isolated, "b", SOURCE, 1)
    assert reader.calls == 0
    assert record.fields["first_name"].values == ["ספי"]


def test_name_instructions_ask_for_a_deep_reading():
    text = cv.ClaudeExtractionProvider(api_key="test")._prompt
    assert "שלושה מעברים" in text and "כל המקומות בדף שבהם מופיע שם המטופל" in text


# ---------------------------------------------------------------- sharp quarters


def test_a_box_measured_in_a_quarter_lands_in_the_right_place_on_the_page():
    content, layout = cv.page_content(_page_path_for_tiles())
    width, height = layout.page
    # רבע 4 = למטה משמאל: התחלתו בגובה (1-0.57) מהעמוד, ברזולוציה מלאה
    part = next(t for t in layout.tiles if t.number == 4)
    left, top, right, bottom = part.rect
    assert left == 0 and right < width and top > 0 and bottom == height
    assert part.sent == (right - left, bottom - top), "רבע נשלח בלי הקטנה"
    region = layout.region([0, 0, part.sent[0] / 2, part.sent[1] / 2], 4)
    assert region[0] == pytest.approx(0) and region[1] == pytest.approx(top / height)
    assert region[3] == pytest.approx((top + (bottom - top) / 2) / height, abs=1e-3)
    assert layout.region([10, 10, 20, 20], 7) is None, "מספר תמונה לא קיים — לא ממציאים"
    assert layout.region([0, 0, part.sent[0] * 2, 10], 4) is None, "מחוץ לרבע"


def test_each_value_box_carries_its_own_quarter():
    reading = _reading(central_numbers=FieldReading(
        values=["947468", "947469"], legibility="clear", boxes=[[1, 0, 0, 100, 50], [3, 0, 0, 100, 50]]))
    seen = []
    to_raw(reading, "m", lambda b, t: seen.append(t) or (0.1, 0.1, 0.2, 0.2), None)
    assert [t for t in seen if t is not None] == [1, 3]


def _page_path_for_tiles():
    import tempfile
    path = Path(tempfile.mkdtemp()) / "page.png"
    Image.new("RGB", (2480, 3508), "white").save(path)
    return path


# ---------------------------------------------------------------- snapping to pen ink


def _ink_page(tmp_path):
    page = Image.new("RGB", (2000, 2800), "white")
    from PIL import ImageDraw
    draw = ImageDraw.Draw(page)
    draw.rectangle((900, 700, 1200, 760), fill=(40, 90, 210))     # שם בעט כחול
    draw.rectangle((500, 700, 800, 760), fill=(20, 20, 20))       # כיתוב מודפס שחור לידו
    draw.rectangle((900, 900, 1300, 960), fill=(40, 90, 210))     # ת"ז בכתב יד, שורה אחרת
    path = tmp_path / "ink.png"
    page.save(path)
    return path


def test_box_next_to_the_handwriting_snaps_onto_the_pen_ink(tmp_path):
    from app.locate import INKED, snap_to_ink
    record = FormRecord.empty("b", SOURCE, 1)
    field = record.fields["first_name"]
    field.values = ["ישראל"]
    field.region = (750 / 2000, 698 / 2800, 1000 / 2000, 762 / 2800)   # חצי על המודפס, חצי על הכתב
    field.region_note = "reader"
    snap_to_ink(record, _ink_page(tmp_path))
    x0, y0, x1, y1 = field.region
    assert field.region_note == INKED
    assert x0 * 2000 >= 870 and x1 * 2000 <= 1230, "צמוד לדיו הכחול, לא למודפס"
    assert y1 * 2800 < 900, "לא נמתח לשורה של הת\"ז"


def test_no_pen_ink_leaves_the_box_alone(tmp_path):
    from app.locate import snap_to_ink
    record = FormRecord.empty("b", SOURCE, 1)
    field = record.fields["last_name"]
    field.values = ["כהן"]
    field.region = (100 / 2000, 2000 / 2800, 300 / 2000, 2060 / 2800)
    field.region_note = "reader"
    snap_to_ink(record, _ink_page(tmp_path))
    assert field.region == (100 / 2000, 2000 / 2800, 300 / 2000, 2060 / 2800)
