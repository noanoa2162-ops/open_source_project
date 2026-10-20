"""Two forms on one page: never split by itself; the secretary splits — free, no reading."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from app import pipeline, store, twoforms
from app.models import STATE_REVIEW, Batch, FormRecord, SourceFile
from app.providers.base import RawExtraction


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr("app.store.BATCHES_DIR", tmp_path / "batches")
    monkeypatch.setattr("app.config.BATCHES_DIR", tmp_path / "batches")


class Counting:
    """Stands in for the reader and counts every call."""

    model = "stub"
    name = "stub"

    def __init__(self, values: dict, note: str = "") -> None:
        self.values, self.note, self.calls = values, note, 0

    def extract(self, image_path: Path) -> RawExtraction:
        self.calls += 1
        return RawExtraction(values=self.values, note=self.note, model="stub")


def _upload(monkeypatch, reader) -> Batch:
    monkeypatch.setattr(pipeline, "build_reader", lambda: (reader, ""))
    batch = Batch(batch_id="bat_two")
    batch.sources.append(SourceFile(index=1, filename="page.png", kind="png"))
    Image.new("RGB", (100, 200), "white").save(store.source_dir("bat_two", 1) / "source", format="PNG")
    store.put(batch)
    pipeline._process("bat_two")
    return batch


def _two_on_page() -> FormRecord:
    """A page read as one: two names and two IDs, each with its box (top form, bottom form)."""
    batch = Batch(batch_id="bat_man", status="completed")
    source = SourceFile(index=1, filename="f.pdf", kind="pdf", page_count=1)
    batch.sources.append(source)
    page = store.source_dir("bat_man", 1) / "pages" / "page-001.png"
    page.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (100, 200), "white").save(page)
    record = FormRecord.empty("bat_man", source, 1)
    record.fields["first_name"].values = ["שרה", "דוד"]
    record.fields["first_name"].value_regions = [(0.6, 0.10, 0.8, 0.14), (0.6, 0.60, 0.8, 0.64)]
    record.fields["national_id"].values = ["012345674", "000000018"]
    record.fields["national_id"].value_regions = [(0.2, 0.12, 0.4, 0.16), (0.2, 0.62, 0.4, 0.66)]
    record.fields["commitment_numbers"].values = ["7001"]
    record.fields["commitment_numbers"].region = (0.1, 0.40, 0.3, 0.44)  # רק בטופס העליון
    record.fields["sending_entity"].values = ["מכבי"]  # בלי מיקום — לא מנחשים
    batch.records.append(record)
    store.put(batch)
    return record


def test_never_split_by_itself(monkeypatch):
    reader = Counting({"first_name": ["שרה", "דוד"]}, note="בעמוד יש שני טפסים")
    batch = _upload(monkeypatch, reader)
    assert reader.calls == 1 and len(batch.records) == 1  # נשאר טופס אחד, קריאה אחת


def test_the_secretary_splits_for_free_by_position(monkeypatch):
    reader = Counting({})
    monkeypatch.setattr(pipeline, "build_reader", lambda: (reader, ""))
    record = _two_on_page()
    batch = store.get("bat_man")

    top, bottom = twoforms.split_page(batch, record.record_id, "y", 0.5)

    assert reader.calls == 0  # בלי מודל ובלי עלות
    assert batch.records == [top, bottom]
    assert top.fields["first_name"].values == ["שרה"] and bottom.fields["first_name"].values == ["דוד"]
    assert top.fields["national_id"].values == ["012345674"] and bottom.fields["national_id"].values == ["000000018"]
    assert top.fields["commitment_numbers"].values == ["7001"] and bottom.fields["commitment_numbers"].values == []
    # התיבה עברה לקואורדינטות של החלק: 0.10 בעמוד → 0.10/0.52 בחלק העליון
    assert top.fields["first_name"].region[1] == pytest.approx(0.10 / 0.52)
    assert bottom.fields["first_name"].region[1] == pytest.approx((0.60 - 0.48) / 0.52)
    # בלי מיקום — לא מנוחש: ריק וכתום בשני החלקים
    assert top.fields["sending_entity"].values == [] and bottom.fields["sending_entity"].values == []
    assert all(f.state == STATE_REVIEW for part in (top, bottom) for f in part.fields.values())
    assert "split_page" in top.fields["first_name"].reasons


def test_undo_brings_the_page_back(monkeypatch):
    record = _two_on_page()
    batch = store.get("bat_man")
    top, _ = twoforms.split_page(batch, record.record_id, "y", 0.5)
    back = twoforms.unsplit_page(batch, top.record_id)
    assert batch.records == [back] and back is record


def test_side_by_side_right_form_first():
    record = _two_on_page()
    record.fields["first_name"].value_regions = [(0.70, 0.3, 0.9, 0.34), (0.10, 0.3, 0.3, 0.34)]
    record.fields["national_id"].value_regions = [(0.60, 0.4, 0.8, 0.44), (0.05, 0.4, 0.25, 0.44)]
    batch = store.get("bat_man")
    right, left = twoforms.split_page(batch, record.record_id, "x", 0.5)
    assert right.fields["first_name"].values == ["שרה"] and left.fields["first_name"].values == ["דוד"]


def test_cut_line_between_the_two_forms():
    record = _two_on_page()
    assert twoforms.suggest_cut(record) == ("y", pytest.approx(0.39))  # בין ת"ז עליונה (0.16) לתחתונה (0.62)


def test_an_approved_form_cannot_be_split():
    record = _two_on_page()
    record.verified = True
    with pytest.raises(twoforms.SplitPageRefused):
        twoforms.split_page(store.get("bat_man"), record.record_id, "y")
