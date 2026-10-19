"""A page is not always one form: two forms on a page become two records, and a
page that continues the previous form joins it — both flagged, never silent."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from app import pipeline
from app.models import Batch, FormRecord, SourceFile
from app.pipeline import Reader
from app.providers.base import RawExtraction

SOURCE = SourceFile(index=1, filename="two.pdf", kind="pdf", sha256="")


def _png(path: Path, size=(1200, 800)) -> Path:
    Image.new("RGB", size, "white").save(path)
    return path


class TwoForms:
    """Whole page: two forms, side by side. Each cut-out: its own patient."""

    name = model = "test"
    prompt_version = "split-test"

    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    def extract(self, image_path: Path) -> RawExtraction:
        with Image.open(image_path) as image:
            size = image.size
        self.calls.append(size)
        if size == (1200, 800):  # העמוד כולו
            return RawExtraction(values={"national_id": ["111111118"]}, model="test",
                                 legibility={"national_id": "clear"}, forms_on_page=2,
                                 form_regions=[(0.5, 0.0, 1.0, 1.0), (0.0, 0.0, 0.5, 1.0)])
        right = len(self.calls) == 2
        return RawExtraction(
            values={"national_id": ["111111118" if right else "222222226"],
                    "commitment_numbers": ["5001" if right else "5002"]},
            model="test", legibility={"national_id": "clear", "commitment_numbers": "clear"},
            regions={"national_id": (0.1, 0.1, 0.5, 0.2)},
        )


def test_two_forms_on_one_page_become_two_flagged_records_with_page_boxes(tmp_path):
    page = _png(tmp_path / "page-001.png")
    reader = TwoForms()
    records = pipeline.read_page(Reader(reader), page, "b", SOURCE, 1)

    assert len(records) == 2, "שני מטופלים — שתי רשומות, לא מיזוג"
    assert [r.fields["national_id"].values for r in records] == [["111111118"], ["222222226"]]
    assert [(r.sub_form, r.sub_forms) for r in records] == [(1, 2), (2, 2)]
    for r in records:
        assert "split_page" in r.fields["commitment_numbers"].reasons, "הפרדה אוטומטית — לבדיקה"
    x0, y0, x1, y1 = records[0].fields["national_id"].region
    assert 0.5 < x0 < x1 <= 1.0, "התיבה הומרה לקואורדינטות של העמוד המלא (החצי הימני)"
    assert len(reader.calls) == 3, "קריאה אחת לעמוד ועוד אחת לכל טופס"


def test_several_forms_without_usable_boxes_stay_one_flagged_record(tmp_path):
    class NoBoxes(TwoForms):
        def extract(self, image_path):
            return RawExtraction(values={"national_id": ["111111118"]}, model="test",
                                 legibility={"national_id": "clear"}, forms_on_page=2, form_regions=[])

    records = pipeline.read_page(Reader(NoBoxes()), _png(tmp_path / "p.png"), "b", SOURCE, 1)
    assert len(records) == 1 and "split_page" in records[0].fields["commitment_numbers"].reasons


def _record(page: int, continues: bool = False, **values) -> FormRecord:
    record = FormRecord.empty("b", SOURCE, page)
    record.continues_previous = continues
    for key, vals in values.items():
        record.fields[key].values = vals
    return record


def test_a_continuation_page_joins_the_previous_form_and_flags_what_it_filled():
    first = _record(3, national_id=["011223344"])
    first.fields["commitment_numbers"].flag("not_on_form")
    second = _record(4, continues=True, commitment_numbers=["730578626"], national_id=["999999998"])
    other = _record(5, national_id=["222222226"])
    batch = Batch(batch_id="b", records=[first, second, other],
                  pages=[{"record_id": r.record_id} for r in (first, second, other)])

    pipeline.join_continuations(batch)

    assert batch.records == [first, other], "עמוד ההמשך אינו רשומה ריקה משלו"
    assert first.extra_pages == [4]
    assert first.fields["commitment_numbers"].values == ["730578626"]
    assert first.fields["commitment_numbers"].reasons == ["joined_pages"], "מולא מעמוד אחר — לבדיקה"
    assert first.fields["national_id"].values == ["011223344"], "ערך קיים לא נדרס"
    assert batch.pages[1]["record_id"] == first.record_id


def test_a_continuation_never_joins_across_files_or_gaps():
    first = _record(3)
    gap = _record(5, continues=True, commitment_numbers=["1"])
    batch = Batch(batch_id="b", records=[first, gap])
    pipeline.join_continuations(batch)
    assert len(batch.records) == 2
