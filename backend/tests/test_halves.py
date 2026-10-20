"""A form photographed in two halves → one record; and small images are turned upright."""

from __future__ import annotations

from dataclasses import replace

import pytest
from PIL import Image

from app import halves, pages
from app.models import STATE_REVIEW, Batch, FormRecord, SourceFile
from app.store import source_dir

TOP = {"first_name": ["טובה"], "last_name": ["גרין"], "national_id": ["011223344"],
       "central_numbers": ["948023"], "sending_entity": ["וולפסון"]}
BOTTOM = {"sending_entity": ["וולפסון"], "commitment_numbers": ["7254647"]}
WHOLE = {**TOP, "commitment_numbers": ["X132767"], "sending_entity": ["לאומית"],
         "sample_received_date": ["24/08/2026"]}


@pytest.fixture(autouse=True)
def _isolated_batches(tmp_path, monkeypatch):
    monkeypatch.setattr("app.store.BATCHES_DIR", tmp_path / "batches")


def _batch(*forms: tuple[str, dict], kind: str = "png") -> Batch:
    """One drop: (filename, values) per image, each with a page of its own size."""
    batch = Batch(batch_id="bat_halves", status="completed")
    for index, (name, values) in enumerate(forms, start=1):
        source = SourceFile(index=index, filename=name, kind=kind, page_count=1)
        batch.sources.append(source)
        page = source_dir(batch.batch_id, index) / "pages" / "page-001.png"
        page.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (200 * index, 100 * index), "white").save(page)
        record = FormRecord.empty(batch.batch_id, source, 1)
        for key, vals in values.items():
            record.fields[key].values = list(vals)
        record.fields["commitment_numbers"].region = (0.1, 0.5, 0.3, 0.7)
        batch.records.append(record)
        batch.pages.append({"source_index": index, "page_number": 1, "status": "done",
                            "record_id": record.record_id})
    return batch


def test_two_halves_become_one_form_top_first():
    # החלק התחתון הועלה ראשון — כמו אצל נועה
    batch = _batch(("bottom.png", BOTTOM), ("top.png", TOP))
    assert halves.join_halves(batch) == 1

    assert len(batch.records) == 1
    form = batch.records[0]
    assert form.fields["first_name"].values == ["טובה"]
    assert form.fields["national_id"].values == ["011223344"]  # אפס מוביל נשמר
    assert form.fields["commitment_numbers"].values == ["7254647"]
    assert form.fields["sending_entity"].values == ["וולפסון"]
    # הכול כתום עד שאדם בודק — החיבור עצמו אינו ודאי
    assert all(f.state == STATE_REVIEW for f in form.fields.values())
    assert "joined_halves" in form.fields["commitment_numbers"].reasons
    assert "חובר אוטומטית" in form.straightened
    # העמוד המחובר: התמונה העליונה (400×200) למעלה, התחתונה (200×100 → 400×200) מתחתיה
    page = source_dir(batch.batch_id, form.source_index) / "pages" / "page-001.png"
    with Image.open(page) as joined:
        assert joined.size == (400, 400)
    # התיבה של מספר ההתחייבות עברה לחצי התחתון של העמוד המחובר
    x0, y0, x1, y1 = form.fields["commitment_numbers"].region
    assert (x0, x1) == (0.1, 0.3) and y0 == pytest.approx(0.75) and y1 == pytest.approx(0.85)
    # התמונה המחוברת אינה קובץ שהועלה
    assert len(batch.accepted_sources) == 2
    assert {p["record_id"] for p in batch.pages} == {form.record_id}


def test_split_brings_both_originals_back():
    batch = _batch(("bottom.png", BOTTOM), ("top.png", TOP))
    originals = [r.record_id for r in batch.records]
    halves.join_halves(batch)
    joined = batch.records[0].record_id

    halves.split_joined(batch, joined)

    assert sorted(r.record_id for r in batch.records) == sorted(originals)
    assert {p["record_id"] for p in batch.pages} == set(originals)
    with pytest.raises(halves.SplitRefused):
        halves.split_joined(batch, joined)


def test_approved_joined_form_cannot_be_split():
    batch = _batch(("bottom.png", BOTTOM), ("top.png", TOP))
    halves.join_halves(batch)
    batch.records[0].verified = True
    with pytest.raises(halves.SplitRefused):
        halves.split_joined(batch, batch.records[0].record_id)


def test_whole_forms_are_never_joined():
    batch = _batch(("a.png", WHOLE), ("b.png", WHOLE), ("c.png", TOP))
    assert halves.join_halves(batch) == 0
    assert len(batch.records) == 3


def test_different_sender_is_not_the_same_form():
    batch = _batch(("bottom.png", {**BOTTOM, "sending_entity": ["לאומית"]}), ("top.png", TOP))
    assert halves.join_halves(batch) == 0
    assert len(batch.records) == 2
    assert halves.PART_NOTE in batch.records[0].straightened  # גלוי במסך, לא נעלם


def test_a_part_with_a_patient_is_not_a_half():
    batch = _batch(("bottom.png", {**BOTTOM, "first_name": ["דוד"]}), ("top.png", TOP))
    assert halves.join_halves(batch) == 0


def test_pdf_pages_next_to_each_other_are_joined():
    # עמוד 2 חצי עליון, עמוד 3 חצי תחתון — בין עמודים שלמים (החלטת נועה, 20/10/2026)
    batch = _batch(("p1.pdf", WHOLE), ("p2.pdf", TOP), ("p3.pdf", BOTTOM), ("p4.pdf", WHOLE), kind="pdf")
    assert halves.join_halves(batch) == 1
    assert len(batch.records) == 3
    joined = next(r for r in batch.records if r.record_id in batch.joined)
    assert joined.fields["first_name"].values == ["טובה"]
    assert joined.fields["commitment_numbers"].values == ["7254647"]


def test_pdf_halves_far_apart_are_not_joined():
    far = _batch(("p1.pdf", TOP), ("p2.pdf", WHOLE), ("p3.pdf", WHOLE), ("p4.pdf", BOTTOM), kind="pdf")
    assert halves.join_halves(far) == 0
    assert halves.PART_NOTE in far.records[3].straightened  # גלוי, עם חיבור ידני


def test_pdf_page_and_image_next_to_each_other_are_joined():
    batch = _batch(("end-of-file.pdf", TOP), kind="pdf")
    image = _batch(("bottom.png", BOTTOM))
    # שני קבצים באותה העלאה: סוף ה-PDF ואחריו התמונה
    source = image.sources[0]
    source.index = 2
    page = source_dir(batch.batch_id, 2) / "pages" / "page-001.png"
    page.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (200, 100), "white").save(page)
    image.records[0].source_index = 2
    batch.sources.append(source)
    batch.records.append(image.records[0])
    assert halves.join_halves(batch) == 1


def test_a_page_without_any_data_is_offered_not_joined():
    batch = _batch(("p1.pdf", WHOLE), ("p2.pdf", {}), kind="pdf")
    assert halves.join_halves(batch) == 0
    empty = batch.records[1]
    assert halves.EMPTY_NOTE in empty.straightened
    assert halves.is_partial(batch, empty)  # כפתור "חבר עם חלק אחר" זמין


def test_join_by_hand_works_for_pdf_pages():
    batch = _batch(("p1.pdf", TOP), ("p2.pdf", WHOLE), ("p3.pdf", BOTTOM), kind="pdf")
    halves.join_halves(batch)
    top, bottom = batch.records[0], batch.records[2]
    form = halves.join_by_hand(batch, bottom.record_id, top.record_id, top.record_id)
    assert form.fields["commitment_numbers"].values == ["7254647"]


def test_two_possible_tops_far_apart_are_left_alone():
    batch = _batch(("top1.png", TOP), ("whole.png", WHOLE), ("bottom.png", BOTTOM),
                   ("whole2.png", WHOLE), ("top2.png", TOP))
    assert halves.join_halves(batch) == 0
    assert len(batch.records) == 5


def test_two_possible_tops_the_neighbour_wins():
    batch = _batch(("top1.png", TOP), ("bottom.png", BOTTOM), ("whole.png", WHOLE),
                   ("top2.png", TOP))
    assert halves.join_halves(batch) == 1
    assert "top1.png" in batch.records[0].straightened


# ------------------------------------------------------------ "חבר בחזרה" וחיבור ידני

def test_split_then_join_back_gives_the_same_form():
    batch = _batch(("bottom.png", BOTTOM), ("top.png", TOP))
    halves.join_halves(batch)
    first = batch.records[0]
    sources_before = len(batch.sources)
    halves.split_joined(batch, first.record_id)
    assert len(batch.split_pairs) == 2

    again = halves.rejoin(batch, batch.records[0].record_id)

    assert batch.records == [again]
    assert {k: f.values for k, f in again.fields.items()} == {k: f.values for k, f in first.fields.items()}
    assert again.fields["commitment_numbers"].region == first.fields["commitment_numbers"].region
    assert again.source_index == first.source_index and len(batch.sources) == sources_before  # אותה תמונה
    assert "חובר בחזרה" in again.straightened
    assert not batch.split_pairs


def test_a_fix_made_after_the_split_is_kept_when_joining_back():
    batch = _batch(("bottom.png", BOTTOM), ("top.png", TOP))
    halves.join_halves(batch)
    halves.split_joined(batch, batch.records[0].record_id)
    top = next(r for r in batch.records if r.source_name == "top.png")
    top.fields["first_name"].values = ["טובה מתוקנת"]

    again = halves.rejoin(batch, top.record_id)

    assert again.fields["first_name"].values == ["טובה מתוקנת"]


def test_split_and_join_back_many_times():
    batch = _batch(("bottom.png", BOTTOM), ("top.png", TOP))
    halves.join_halves(batch)
    for _ in range(3):
        halves.split_joined(batch, batch.records[0].record_id)
        assert len(batch.records) == 2
        halves.rejoin(batch, batch.records[1].record_id)
        assert len(batch.records) == 1


def test_an_approved_part_cannot_be_joined_back():
    batch = _batch(("bottom.png", BOTTOM), ("top.png", TOP))
    halves.join_halves(batch)
    halves.split_joined(batch, batch.records[0].record_id)
    batch.records[0].verified = True
    with pytest.raises(halves.SplitRefused):
        halves.rejoin(batch, batch.records[1].record_id)


def test_join_by_hand_puts_the_chosen_part_on_top():
    # שני חלקים שהחיבור האוטומטי לא חיבר (גורם שולח שונה)
    batch = _batch(("bottom.png", {**BOTTOM, "sending_entity": ["לאומית"]}), ("top.png", TOP))
    assert halves.join_halves(batch) == 0
    bottom, top = batch.records

    form = halves.join_by_hand(batch, bottom.record_id, top.record_id, top.record_id)

    assert batch.records == [form]
    assert form.fields["first_name"].values == ["טובה"]
    assert form.fields["commitment_numbers"].region[1] > 0.5  # מהחלק התחתון — בחצי התחתון
    # הגורם השולח סותר — נשמרים שניהם, כתום, לבחירה
    assert form.fields["sending_entity"].values == ["וולפסון", "לאומית"]
    assert "joined_conflict" in form.fields["sending_entity"].reasons
    assert "חובר ידנית" in form.straightened
    assert halves.PART_NOTE not in form.straightened


def test_join_by_hand_other_order():
    batch = _batch(("bottom.png", {**BOTTOM, "sending_entity": ["לאומית"]}), ("top.png", TOP))
    halves.join_halves(batch)
    bottom, top = batch.records
    form = halves.join_by_hand(batch, top.record_id, bottom.record_id, bottom.record_id)
    assert form.fields["commitment_numbers"].region[1] < 0.5  # התחתון נבחר להיות למעלה


def test_whole_form_cannot_be_joined_by_hand():
    batch = _batch(("whole.png", WHOLE), ("bottom.png", {**BOTTOM, "sending_entity": ["X"]}))
    halves.join_halves(batch)
    whole, part = batch.records
    assert not halves.is_partial(batch, whole) and halves.is_partial(batch, part)
    with pytest.raises(halves.SplitRefused):
        halves.join_by_hand(batch, part.record_id, whole.record_id, whole.record_id)


def test_approved_part_cannot_be_joined_by_hand():
    batch = _batch(("bottom.png", {**BOTTOM, "sending_entity": ["X"]}), ("top.png", TOP))
    batch.records[1].verified = True
    with pytest.raises(halves.SplitRefused):
        halves.join_by_hand(batch, batch.records[0].record_id, batch.records[1].record_id,
                            batch.records[1].record_id)


def test_joining_a_split_part_elsewhere_forgets_the_old_pair():
    batch = _batch(("bottom.png", BOTTOM), ("top.png", TOP), ("bottom2.png", BOTTOM))
    halves.join_halves(batch)
    joined = next(r for r in batch.records if r.record_id in batch.joined)
    halves.split_joined(batch, joined.record_id)
    top = next(r for r in batch.records if r.source_name == "top.png")
    other = next(r for r in batch.records if r.source_name == "bottom2.png")

    halves.join_by_hand(batch, top.record_id, other.record_id, top.record_id)

    assert not batch.split_pairs  # "חבר בחזרה" כבר לא מוצע לחלק שנשאר


# ------------------------------------------------------------ סיבוב תמונה קטנה

def test_small_image_is_enlarged_only_for_the_orientation_check(monkeypatch):
    seen = {}

    def fake_run(command, **kwargs):
        with Image.open(command[1]) as probe:
            seen["size"] = probe.size

        class Done:
            stdout = "Rotate: 270\nOrientation confidence: 11.6\n"
            stderr = ""
        return Done()

    monkeypatch.setattr(pages, "tesseract_command", lambda: "tesseract")
    monkeypatch.setattr(pages.subprocess, "run", fake_run)

    small = Image.new("RGB", (531, 777), "white")
    assert pages.detect_orientation(small) == (270, 11.6)
    assert max(seen["size"]) == pages.OSD_MIN_SIDE
    assert small.size == (531, 777)  # העמוד עצמו לא השתנה

    big = Image.new("RGB", (2480, 3508), "white")  # עמוד PDF ב-300 DPI — נבדק כמו שהוא
    pages.detect_orientation(big)
    assert seen["size"] == (2480, 3508)


def test_small_sideways_image_is_turned(monkeypatch):
    monkeypatch.setattr(pages, "SETTINGS", replace(pages.SETTINGS, auto_rotate=True))
    monkeypatch.setattr(pages, "detect_orientation", lambda image: (270, 11.6))
    turned, degrees, decided = pages.auto_orient(Image.new("RGB", (531, 777), "white"))
    assert (degrees, decided, turned.size) == (270, True, (777, 531))
