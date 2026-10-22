"""A form photographed in two halves → one record. Automatic, local and free.

Every image is read on its own, as always. Afterwards, the content decides:
a bottom half has no patient at all (no name, ID or central number) but brings
a commitment number or a date; a top half has the patient but lacks what the
bottom half brings, and both name the same sender. A whole form never comes
without any patient details, so such a pair is joined: the two pictures are
stacked into one page and the fields are combined — no new reading, no cost.

A pixel test ("is the edge cut?") was measured first (20/10/2026) and rejected:
on the sample PDFs, many whole pages also have ink touching the edge.

PDF pages join too (Noa, 20/10/2026), with one more safeguard: a PDF page only
joins a page right next to it in upload order (page 3 with page 4, or the last page
of one file with the first of the next) — a PDF often holds many forms.

The joined form stays orange until a person checks it, and "split" brings the
two original records back.
"""

from __future__ import annotations

import copy
import hashlib
import io

from PIL import Image

from app.files import KIND_JPEG, KIND_PDF, KIND_PNG
from app.models import Batch, ExtractedField, FormRecord, Region, SourceFile
from app.store import source_dir
from app.validators import validate_record

IDENTITY = ("first_name", "last_name", "national_id", "central_numbers")
# מה שהחצי התחתון מביא: מדבקת ההתחייבות ותאריך הקבלה נמצאים לעתים בתחתית הטופס
TAIL = ("commitment_numbers", "sample_received_date")
# שדה יחיד: ערך שונה בשני החלקים = שני טפסים שונים, לא מחברים
SINGLE = ("first_name", "last_name", "national_id", "sending_entity")

PART_NOTE = "לא נמצאו בטופס פרטי מטופל — ייתכן שזה רק חלק מהטופס"
EMPTY_NOTE = "עמוד בלי נתונים — ייתכן שהוא המשך של הטופס הקודם (אפשר לחבר ידנית)"


class SplitRefused(Exception):
    """The joined form was not split; the message says why, in Hebrew."""


def join_halves(batch: Batch) -> int:
    """Join every clear top/bottom pair in the batch. Returns how many were joined."""
    joined = 0
    # טופס שהמזכירה כבר תיקנה (בזמן שהשאר עוד נקראו) — לא מחברים לבד, כדי לא לדרוס את התיקון.
    # חיבור ידני ("חבר עם חלק אחר") עדיין אפשרי.
    untouched = [r for r in batch.records if r.version == 0]
    for bottom in [r for r in untouched if _is_bottom(batch, r)]:
        if bottom not in batch.records:
            continue
        tops = [t for t in batch.records if t is not bottom and t.version == 0 and _fits(batch, t, bottom)]
        if _is_pdf(batch, bottom):
            tops = [t for t in tops if _next_to(batch, t, bottom)]  # עמוד PDF — רק עם השכן הצמוד
        else:
            tops = [t for t in tops if not _is_pdf(batch, t) or _next_to(batch, t, bottom)]
        if len(tops) > 1:
            # כמה מועמדים: רק השכן הצמוד בסדר ההעלאה, ורק אם הוא יחיד
            at = batch.records.index(bottom)
            tops = [t for t in tops if abs(batch.records.index(t) - at) == 1]
        if len(tops) != 1:
            _add_note(bottom, PART_NOTE)
            continue
        _join(batch, tops[0], bottom)
        joined += 1
    for record in batch.records:
        # עמוד שאין בו שום ערך — לא מחברים לבד (יכול להיות דף כריכה), רק מציעים
        if _usable(batch, record) and all(f.is_empty for f in record.fields.values()):
            _add_note(record, EMPTY_NOTE)
    return joined


def split_joined(batch: Batch, record_id: str) -> None:
    """Undo a join: the two original records come back, as they were — and remember
    the pair, so "join back" can undo the split."""
    originals = batch.joined.get(record_id)
    index = next((i for i, r in enumerate(batch.records) if r.record_id == record_id), None)
    if originals is None or index is None:
        raise SplitRefused("הטופס הזה לא חובר משני קבצים.")
    joined = batch.records[index]
    if joined.verified:
        raise SplitRefused("הטופס כבר אושר — אי אפשר להפריד אותו.")
    del batch.joined[record_id]
    batch.records[index:index + 1] = originals
    for page in batch.pages:
        if page.get("record_id") == record_id:
            match = next((r for r in originals if (r.source_index, r.page_number)
                          == (page["source_index"], page["page_number"])), None)
            page["record_id"] = match.record_id if match else ""
    top, bottom = originals
    pair = {"top": top.record_id, "bottom": bottom.record_id, "source_index": joined.source_index}
    batch.split_pairs[top.record_id] = pair
    batch.split_pairs[bottom.record_id] = pair


def rejoin(batch: Batch, record_id: str) -> FormRecord:
    """Join back two halves that were split — as they are now, so a fix made after
    the split is kept. Reuses the joined picture; no reading, no cost."""
    pair = batch.split_pairs.get(record_id)
    if pair is None:
        raise SplitRefused("הטופס הזה לא הופרד מטופס מחובר.")
    top, bottom = (_find(batch, pair[k]) for k in ("top", "bottom"))
    if top is None or bottom is None:
        raise SplitRefused("אחד החלקים כבר לא קיים בנפרד.")
    if top.verified or bottom.verified:
        raise SplitRefused("אחד החלקים כבר אושר — אי אפשר לחבר אותו בחזרה.")
    return _join(batch, top, bottom, how="חובר בחזרה", index=pair["source_index"])


def is_partial(batch: Batch, record: FormRecord) -> bool:
    """Only part of a form: an uploaded page (image or PDF) missing the patient or
    the commitment side entirely. Only these can be joined by hand."""
    return _usable(batch, record) and (
        all(record.fields[k].is_empty for k in IDENTITY)
        or all(record.fields[k].is_empty for k in TAIL)
    )


def join_by_hand(batch: Batch, record_id: str, other_id: str, top_id: str) -> FormRecord:
    """A person chose two parts of the same form, and which one goes on top."""
    first, other = _find(batch, record_id), _find(batch, other_id)
    if first is None or other is None or first is other:
        raise SplitRefused("צריך לבחור שני חלקים שונים מאותה העלאה.")
    if not (is_partial(batch, first) and is_partial(batch, other)):
        raise SplitRefused("אפשר לחבר רק חלקים של טופס חתוך, לא טופס שלם.")
    if top_id not in (first.record_id, other.record_id):
        raise SplitRefused("לא נבחר איזה חלק למעלה.")
    top, bottom = (first, other) if top_id == first.record_id else (other, first)
    return _join(batch, top, bottom, how="חובר ידנית")


def _find(batch: Batch, record_id: str) -> FormRecord | None:
    return next((r for r in batch.records if r.record_id == record_id), None)


# ------------------------------------------------------------ מי חצי ומי מתאים

def _source(batch: Batch, record: FormRecord) -> SourceFile | None:
    return next((s for s in batch.sources if s.index == record.source_index), None)


def _joinable(batch: Batch, record: FormRecord) -> bool:
    """A page that was uploaded (not a picture this module made): an image or a PDF page."""
    source = _source(batch, record)
    return bool(source and not source.joined and source.kind in (KIND_JPEG, KIND_PNG, KIND_PDF))


def _is_pdf(batch: Batch, record: FormRecord) -> bool:
    source = _source(batch, record)
    return bool(source and source.kind == KIND_PDF)


def _part(batch: Batch, record: FormRecord) -> str:
    """How to name one part in the note: the file, and the page when it is a PDF page."""
    return f"{record.source_name} עמ׳ {record.page_number}" if _is_pdf(batch, record) else record.source_name


def _next_to(batch: Batch, one: FormRecord, other: FormRecord) -> bool:
    """Right next to each other in upload order (also across the end of one file)."""
    return abs(batch.records.index(one) - batch.records.index(other)) == 1


def _usable(batch: Batch, record: FormRecord) -> bool:
    return _joinable(batch, record) and not record.error and not record.verified \
        and record.record_id not in batch.joined


def _is_bottom(batch: Batch, record: FormRecord) -> bool:
    return (
        _usable(batch, record)
        and all(record.fields[k].is_empty for k in IDENTITY)
        and any(not record.fields[k].is_empty for k in TAIL)
    )


def _fits(batch: Batch, top: FormRecord, bottom: FormRecord) -> bool:
    if not _usable(batch, top) or _is_bottom(batch, top):
        return False
    if all(top.fields[k].is_empty for k in IDENTITY):
        return False
    # משלימים: לחלק העליון חסר לפחות אחד מהשדות שהתחתון מביא
    if not any(top.fields[k].is_empty and not bottom.fields[k].is_empty for k in TAIL):
        return False
    for key in SINGLE:
        a, b = top.fields[key], bottom.fields[key]
        if not a.is_empty and not b.is_empty and _norm(a.values) != _norm(b.values):
            return False
    return True


def _norm(values: list[str]) -> set[str]:
    return {v.strip() for v in values if v.strip()}


# ------------------------------------------------------------ החיבור עצמו

def _join(
    batch: Batch, top: FormRecord, bottom: FormRecord,
    how: str = "חובר אוטומטית", index: int | None = None,
) -> FormRecord:
    source, top_share = _stack(batch, top, bottom, index)
    record = FormRecord.empty(batch.batch_id, source, 1)

    def on_top(region: Region | None) -> Region | None:
        return _moved(region, 0.0, top_share)

    def on_bottom(region: Region | None) -> Region | None:
        return _moved(region, top_share, 1.0 - top_share)

    for key in record.fields:
        record.fields[key] = _combined(top.fields[key], bottom.fields[key], on_top, on_bottom)

    record.context = {**bottom.context, **top.context}  # תאריך ההפקה/הלידה — מהחלק העליון
    record.readers = list(dict.fromkeys(top.readers + bottom.readers))
    record.low_resolution = top.low_resolution or bottom.low_resolution
    record.cost_usd = (top.cost_usd or 0.0) + (bottom.cost_usd or 0.0)
    record.seconds = round(top.seconds + bottom.seconds, 1)
    record.template = top.template or bottom.template
    record.model_note = " · ".join(filter(None, [top.model_note, bottom.model_note]))
    note = f"{how} משני חלקים: {_part(batch, top)} (למעלה) + {_part(batch, bottom)} (למטה) — לבדוק"
    earlier = [n for r in (top, bottom) for n in r.straightened.split(" · ") if n and n != PART_NOTE]
    record.straightened = " · ".join([note, *earlier])
    validate_record(record)

    # חלק שהופרד קודם מזוג אחר — הזוג הישן כבר לא רלוונטי
    for part in (top, bottom):
        old = batch.split_pairs.pop(part.record_id, None)
        if old:
            for key in (old["top"], old["bottom"]):
                batch.split_pairs.pop(key, None)

    at = batch.records.index(top)
    batch.records[at] = record
    batch.records.remove(bottom)
    batch.joined[record.record_id] = [top, bottom]
    for page in batch.pages:
        if page.get("record_id") in (top.record_id, bottom.record_id):
            page["record_id"] = record.record_id
    return record


def _combined(top: ExtractedField, bottom: ExtractedField, on_top, on_bottom) -> ExtractedField:
    if bottom.is_empty:
        field = _copy(top, on_top)
    elif top.is_empty:
        field = _copy(bottom, on_bottom)
    else:
        field = _copy(top, on_top)
        aligned = len(field.value_regions) == len(field.values)
        extra_regions = bottom.value_regions if len(bottom.value_regions) == len(bottom.values) else []
        for i, value in enumerate(bottom.values):
            if value.strip() in _norm(field.values):
                continue
            field.values.append(value)
            if aligned:
                field.value_regions.append(on_bottom(extra_regions[i]) if extra_regions else None)
        for reason in bottom.reasons:
            field.flag(reason)
        if _norm(top.values) != _norm(bottom.values):
            field.flag("joined_conflict")
    if not field.is_empty:
        field.flag("joined_halves")
    return field


def _copy(field: ExtractedField, move) -> ExtractedField:
    fresh = copy.deepcopy(field)  # הרשומות המקוריות נשמרות כמו שהן, לכפתור "הפרד"
    fresh.region = move(fresh.region)
    fresh.value_regions = [move(r) for r in fresh.value_regions]
    fresh.history = []
    return fresh


def _moved(region: Region | None, offset: float, share: float) -> Region | None:
    """A box on one half → the same box on the stacked page (x stays, y shrinks)."""
    if region is None:
        return None
    x0, y0, x1, y1 = region
    return (x0, offset + y0 * share, x1, offset + y1 * share)


def _stack(
    batch: Batch, top: FormRecord, bottom: FormRecord, index: int | None = None,
) -> tuple[SourceFile, float]:
    """Both pictures one above the other, at the same width, as a new source in the
    batch — so the page view, the close-ups, approval and Excel work unchanged.
    `index` reuses the source of an earlier join of the same pair (join back)."""
    with Image.open(_page(batch, top)) as a, Image.open(_page(batch, bottom)) as b:
        width = max(a.width, b.width)
        upper = _to_width(a.convert("RGB"), width)
        lower = _to_width(b.convert("RGB"), width)
    page = Image.new("RGB", (width, upper.height + lower.height), "white")
    page.paste(upper, (0, 0))
    page.paste(lower, (0, upper.height))

    reused = next((s for s in batch.sources if s.joined and s.index == index), None)
    index = reused.index if reused else max(s.index for s in batch.sources) + 1
    target = source_dir(batch.batch_id, index) / "pages" / "page-001.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    page.save(buffer, format="PNG")
    target.write_bytes(buffer.getvalue())
    if reused:
        # אותו זוג, אותה תמונה. התקריבים הישנים נמחקים, כי המיקומים מחושבים מחדש.
        for crop in (target.parent.parent / "crops").glob("*.png"):
            crop.unlink(missing_ok=True)
        return reused, upper.height / page.height

    source = SourceFile(
        index=index,
        filename=f"{_part(batch, top)} + {_part(batch, bottom)}",
        kind=KIND_PNG,
        page_count=1,
        sha256=hashlib.sha256(buffer.getvalue()).hexdigest(),
        joined=True,
    )
    batch.sources.append(source)
    return source, upper.height / page.height


def _to_width(image: Image.Image, width: int) -> Image.Image:
    if image.width == width:
        return image
    height = round(image.height * width / image.width)
    return image.resize((width, height), Image.Resampling.LANCZOS)


def _page(batch: Batch, record: FormRecord):
    return source_dir(batch.batch_id, record.source_index) / "pages" / f"page-{record.page_number:03d}.png"


def _add_note(record: FormRecord, note: str) -> None:
    if note not in record.straightened:
        record.straightened = " · ".join(filter(None, [record.straightened, note]))
