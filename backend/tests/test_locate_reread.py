"""Box checking against the page, the combined name zoom, and reading a failed
form again only on request. No model is called: fake readers stand in."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import locate, pipeline, store
from app.api import full_name_region
from app.locate import MOVED, CHECKED, READER, Word, check_regions, parse_tsv
from app.models import PAGE_DONE, PAGE_FAILED, PAGE_READING, Batch, FormRecord, SourceFile
from app.providers.base import ExtractionError, RawExtraction

SOURCE = SourceFile(index=1, filename="form.png", kind="png")  # בלי sha256: בלי תשובות שמורות


def _page(path: Path, size=(1000, 1414)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, "white").save(path)
    return path


def _record(**fields) -> FormRecord:
    record = FormRecord.empty("b", SOURCE, 1)
    for key, (values, region) in fields.items():
        record.fields[key].values = values
        record.fields[key].region = region
    return record


# ---------------------------------------------------------------- Tesseract output


def test_tsv_keeps_long_numbers_and_joins_split_ones():
    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    rows = [
        "5\t1\t1\t1\t1\t1\t100\t200\t80\t30\t90\t2648",
        "5\t1\t1\t1\t1\t2\t190\t202\t100\t30\t90\t00956",
        "5\t1\t1\t1\t1\t3\t600\t900\t40\t30\t90\t12",
        "5\t1\t1\t1\t1\t4\t300\t500\t120\t30\t90\t945986",
    ]
    words = parse_tsv("\n".join([header, *rows]), 1000, 1000)
    digits = {w.digits for w in words}
    assert "945986" in digits and "00956" in digits
    assert "264800956" in digits, "מספר שנחתך לשניים באותה שורה מחובר"
    assert "12" not in digits


# ---------------------------------------------------------------- boxes


def test_box_holding_the_value_is_confirmed(monkeypatch, tmp_path):
    monkeypatch.setattr(locate, "page_words", lambda _: [Word("945986", (0.40, 0.20, 0.50, 0.22))])
    record = _record(central_numbers=(["945986"], (0.38, 0.18, 0.52, 0.24)))
    check_regions(record, _page(tmp_path / "p.png"))
    assert record.fields["central_numbers"].region_note == CHECKED
    assert record.fields["central_numbers"].region == (0.38, 0.18, 0.52, 0.24)


def test_box_elsewhere_moves_onto_the_value_and_the_value_never_changes(monkeypatch, tmp_path):
    monkeypatch.setattr(locate, "page_words", lambda _: [Word("945986", (0.40, 0.20, 0.50, 0.22))])
    record = _record(central_numbers=(["945986"], (0.10, 0.70, 0.20, 0.72)),
                     first_name=(["ישראל"], (0.6, 0.3, 0.7, 0.32)))
    check_regions(record, _page(tmp_path / "p.png"))
    field = record.fields["central_numbers"]
    assert field.region_note == MOVED
    x0, y0, x1, y1 = field.region
    assert x0 <= 0.45 <= x1 and y0 <= 0.21 <= y1
    assert field.values == ["945986"]
    # עוגן אחד שאינו ביחס הגובה/רוחב: שאר התיבות לא נוגעות
    assert record.fields["first_name"].region == (0.6, 0.3, 0.7, 0.32)
    assert record.fields["first_name"].region_note == READER


def test_whole_page_stretch_is_corrected_from_two_agreeing_anchors(monkeypatch, tmp_path):
    words = [Word("945986", (0.15, 0.17, 0.23, 0.19)), Word("264800956", (0.40, 0.31, 0.55, 0.33))]
    monkeypatch.setattr(locate, "page_words", lambda _: words)
    k = 1.4
    record = _record(
        central_numbers=(["945986"], (0.15, 0.17 * k, 0.23, 0.19 * k)),
        commitment_numbers=(["264800956"], (0.40, 0.31 * k, 0.55, 0.33 * k)),
        first_name=(["ישראל"], (0.06, 0.22 * k, 0.21, 0.24 * k)),
    )
    check_regions(record, _page(tmp_path / "p.png"))
    name = record.fields["first_name"]
    assert name.region_note == MOVED
    assert name.region[1] == pytest.approx(0.22, abs=0.01) and name.region[3] == pytest.approx(0.24, abs=0.01)


def test_single_anchor_fixes_the_page_only_for_the_height_width_mixup(monkeypatch, tmp_path):
    aspect = 1414 / 1000
    monkeypatch.setattr(locate, "page_words", lambda _: [Word("945986", (0.15, 0.17, 0.23, 0.19))])
    record = _record(
        central_numbers=(["945986"], (0.15, 0.17 * aspect, 0.23, 0.19 * aspect)),
        last_name=(["ישראלי"], (0.04, 0.22 * aspect, 0.10, 0.24 * aspect)),
    )
    check_regions(record, _page(tmp_path / "p.png"))
    assert record.fields["last_name"].region_note == MOVED
    assert record.fields["last_name"].region[1] == pytest.approx(0.22, abs=0.01)


def test_nothing_moves_without_tesseract_or_without_a_match(monkeypatch, tmp_path):
    region = (0.10, 0.70, 0.20, 0.72)
    for words in ([], [Word("111111", (0.4, 0.2, 0.5, 0.22))]):
        monkeypatch.setattr(locate, "page_words", lambda _, w=words: w)
        record = _record(central_numbers=(["945986"], region))
        check_regions(record, _page(tmp_path / "p.png"))
        assert record.fields["central_numbers"].region == region
        assert record.fields["central_numbers"].region_note == READER


def test_unsure_digits_are_not_used_as_anchors(monkeypatch, tmp_path):
    monkeypatch.setattr(locate, "page_words", lambda _: [Word("945986", (0.4, 0.2, 0.5, 0.22))])
    region = (0.10, 0.70, 0.20, 0.72)
    record = _record(central_numbers=(["9459?6"], region))
    check_regions(record, _page(tmp_path / "p.png"))
    assert record.fields["central_numbers"].region == region


# ---------------------------------------------------------------- names together


def test_full_name_zoom_covers_both_names_on_the_same_line():
    record = _record(first_name=(["ישראל"], (0.6, 0.30, 0.7, 0.32)), last_name=(["ישראלי"], (0.4, 0.30, 0.5, 0.33)))
    assert full_name_region(record) == (0.4, 0.30, 0.7, 0.33)


def test_full_name_zoom_is_not_stretched_across_the_page():
    record = _record(first_name=(["ישראל"], (0.6, 0.10, 0.7, 0.12)), last_name=(["ישראלי"], (0.4, 0.60, 0.5, 0.62)))
    assert full_name_region(record) is None


# ---------------------------------------------------------------- reading again, only on request


class FlakyReader:
    """Fails the first `fail` calls, then answers. Counts every call."""

    model = "stub"

    def __init__(self, fail: int) -> None:
        self.fail, self.calls = fail, 0

    def extract(self, image_path: Path) -> RawExtraction:
        self.calls += 1
        if self.calls <= self.fail:
            raise ExtractionError("Anthropic עמוסה כרגע. נסו שוב בעוד רגע.")
        return RawExtraction(values={"central_numbers": ["945986"]}, model="stub")


@pytest.fixture
def failed_batch(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.BATCHES_DIR", tmp_path / "batches")
    monkeypatch.setattr("app.store.BATCHES_DIR", tmp_path / "batches")
    monkeypatch.setattr("app.pipeline.BATCHES_DIR", tmp_path / "batches")
    monkeypatch.setattr(locate, "page_words", lambda _: [])
    batch = Batch(batch_id="bat_rr", status="partial")
    batch.sources.append(SOURCE)
    image = _page(pipeline.page_image_path("bat_rr", 1, 1))
    reader = FlakyReader(fail=1)
    record = pipeline.read_form(pipeline.Reader(reader), image, "bat_rr", SOURCE, 1)
    assert record.error
    batch.records.append(record)
    batch.pages = [{"source_index": 1, "page_number": 1, "status": PAGE_FAILED, "record_id": record.record_id}]
    store.put(batch)
    monkeypatch.setattr(pipeline, "build_reader", lambda: (reader, ""))
    yield batch, record, reader
    store.delete("bat_rr")


def _wait(page: dict) -> None:
    for _ in range(200):
        if page["status"] != PAGE_READING:
            return
        time.sleep(0.02)


def test_a_failed_form_is_not_read_again_on_its_own(failed_batch):
    batch, record, reader = failed_batch
    time.sleep(0.1)
    assert reader.calls == 1 and batch.records[0].error


def test_failure_is_logged_without_form_content(failed_batch):
    log = (pipeline.BATCHES_DIR / "bat_rr" / "errors.log").read_text(encoding="utf-8")
    assert "עמוד 1" in log and "עמוסה" in log
    assert "945986" not in log


def test_reread_replaces_the_failed_form_in_place(failed_batch):
    from app.api import app

    batch, record, reader = failed_batch
    client = TestClient(app)
    res = client.post(f"/api/records/{record.record_id}/reread")
    assert res.status_code == 200
    _wait(batch.pages[0])
    assert reader.calls == 2, "בדיוק קריאה אחת נוספת"
    fresh = batch.records[0]
    assert fresh.record_id == record.record_id and not fresh.error
    assert fresh.fields["central_numbers"].values == ["945986"]
    assert batch.pages[0]["status"] == PAGE_DONE
    # טופס שכבר נקרא — לא שולחים שוב
    assert client.post(f"/api/records/{record.record_id}/reread").status_code == 409
    assert reader.calls == 2


def test_double_click_does_not_send_twice(failed_batch):
    batch, record, reader = failed_batch
    batch.pages[0]["status"] = PAGE_READING  # קריאה חוזרת כבר רצה
    with pytest.raises(pipeline.RereadRefused):
        pipeline.reread("bat_rr", record.record_id)
    assert reader.calls == 1


def test_no_reread_while_the_batch_is_still_reading(failed_batch):
    batch, record, reader = failed_batch
    batch.status = "processing"
    with pytest.raises(pipeline.RereadRefused):
        pipeline.reread("bat_rr", record.record_id)
    assert reader.calls == 1
