"""Two forms on one page → two records — only when the secretary decides, and free.

Noa (20/10/2026): no automatic splitting and no second reading. The reader already
read the whole page and placed every value on it, so a split needs no model at all:
the picture is cut in two, and each value goes to the part where its box lies (its
box moved to that part's coordinates). A value with no box, or a box across the cut,
is not guessed — the field stays empty and orange. Every moved value is orange too,
until a person checks it. "בטל פיצול" brings the original page back, free.
"""

from __future__ import annotations

import copy
import threading
from pathlib import Path

from PIL import Image

from app.models import PAGE_DONE, PAGE_FAILED, Batch, ExtractedField, FormRecord, Region, SourceFile
from app.store import source_dir
from app.validators import validate_record

MARGIN = 0.02  # קצת חפיפה בחיתוך — כדי לא לחתוך אות באמצע
AXES = ("y", "x")  # y = אחד מעל השני, x = זה לצד זה
# סיבות מהקריאה שנשארות נכונות גם אחרי הפיצול (ספק של המודל בערך עצמו)
KEEP_REASONS = ("illegible", "model_uncertain", "commitment_from_sticker", "commitment_like_central")
_LOCK = threading.Lock()


class SplitPageRefused(Exception):
    """The page was not split; the message says why, in Hebrew."""


def suggest_cut(record: FormRecord) -> tuple[str, float]:
    """Where to cut: between the boxes of two values that belong to different forms
    (two IDs, two names, two commitment numbers). Default: across the middle."""
    for key in ("national_id", "first_name", "last_name", "commitment_numbers"):
        field = record.fields[key]
        boxes = [b for b in field.value_regions if b] if len(field.value_regions) == len(field.values) else []
        if len(boxes) >= 2:
            a, b = boxes[0], boxes[1]
            dx = abs((a[0] + a[2]) / 2 - (b[0] + b[2]) / 2)
            dy = abs((a[1] + a[3]) / 2 - (b[1] + b[3]) / 2)
            if dy >= dx:
                upper, lower = sorted((a, b), key=lambda r: r[1])
                return "y", _clamp((upper[3] + lower[1]) / 2)
            left, right = sorted((a, b), key=lambda r: r[0])
            return "x", _clamp((left[2] + right[0]) / 2)
    return "y", 0.5


def _clamp(cut: float) -> float:
    return round(min(0.8, max(0.2, cut)), 3)


# ------------------------------------------------------------ הפיצול — רק בלחיצה של המזכירה, בלי מודל

def split_page(batch: Batch, record_id: str, axis: str, cut: float | None = None) -> list[FormRecord]:
    """Cut the page in two and give each part the values that lie on it. No reading, no cost."""
    from app.pipeline import page_image_path

    if axis not in AXES:
        raise SplitPageRefused("לא נבחר איך הטפסים מסודרים בעמוד.")
    with _LOCK:
        if batch.status in ("queued", "processing"):
            raise SplitPageRefused("הקריאה של הקובץ עוד לא הסתיימה.")
        old = next((r for r in batch.records if r.record_id == record_id), None)
        if old is None:
            raise SplitPageRefused("הטופס לא נמצא.")
        if old.verified:
            raise SplitPageRefused("הטופס כבר אושר — אי אפשר לפצל אותו.")
        if old.error:
            raise SplitPageRefused("הקריאה של הטופס נכשלה — אין ערכים לחלק.")
        if record_id in batch.joined:
            raise SplitPageRefused("הטופס הזה חובר משני חלקים — אפשר להפריד אותו בכפתור \"הפרד\".")
        image = page_image_path(batch.batch_id, old.source_index, old.page_number)
        if not image.is_file():
            raise SplitPageRefused("תמונת העמוד לא נמצאה.")
        if cut is None:
            suggested_axis, suggested = suggest_cut(old)
            cut = suggested if suggested_axis == axis else 0.5
        cut = _clamp(float(cut))
        sources = _cut(batch, old, image, axis, cut)
        parts = [_part(batch, old, source, axis, cut, number) for number, source in enumerate(sources, start=1)]
        page = next((p for p in batch.pages if p.get("record_id") == record_id), None)
        _put_parts(batch, old, parts, page)
    return parts


def _bounds(axis: str, cut: float, number: int) -> tuple[float, float]:
    """The slice of the page a part covers, along the cut axis (with the small overlap)."""
    low, high = max(0.0, cut - MARGIN), min(1.0, cut + MARGIN)
    if axis == "y":
        return (0.0, high) if number == 1 else (low, 1.0)
    return (low, 1.0) if number == 1 else (0.0, high)  # זה לצד זה: הימני ראשון


def _on_part(region: Region, axis: str, cut: float, number: int) -> bool:
    """The whole box lies on this side of the cut (a box across the cut belongs to neither)."""
    start, end = (region[1], region[3]) if axis == "y" else (region[0], region[2])
    if number == 1:
        return (end <= cut + MARGIN) if axis == "y" else (start >= cut - MARGIN)
    return (start >= cut - MARGIN) if axis == "y" else (end <= cut + MARGIN)


def _moved(region: Region, axis: str, bounds: tuple[float, float]) -> Region:
    a, b = bounds
    span = (b - a) or 1.0
    x0, y0, x1, y1 = region
    if axis == "y":
        return (x0, (y0 - a) / span, x1, (y1 - a) / span)
    return ((x0 - a) / span, y0, (x1 - a) / span, y1)


def _part(batch: Batch, old: FormRecord, source: SourceFile, axis: str, cut: float, number: int) -> FormRecord:
    record = FormRecord.empty(batch.batch_id, source, 1)
    bounds = _bounds(axis, cut, number)
    for key, field in old.fields.items():
        values, boxes = _values_on(field, axis, cut, number)
        new = ExtractedField(key=key)
        new.values = values
        new.value_regions = [_moved(b, axis, bounds) for b in boxes] if len(boxes) == len(values) > 1 else []
        if boxes:
            new.region = _moved(_union(boxes), axis, bounds)
            new.region_note = field.region_note
        for reason in field.reasons:
            if reason in KEEP_REASONS and values:
                new.flag(reason)
        if values:
            new.flag("split_page")  # הערך עבר לחלק לפי המיקום שלו — אדם בודק
        record.fields[key] = new
    record.context = copy.deepcopy(old.context)
    record.template = old.template
    record.low_resolution = old.low_resolution
    record.straightened = f"פוצל ידנית מ{old.source_name} עמ׳ {old.page_number} — חלק {number} מתוך 2"
    validate_record(record)
    return record


def _values_on(field: ExtractedField, axis: str, cut: float, number: int) -> tuple[list[str], list[Region]]:
    """The values (and their boxes) that lie on this part. No box — not guessed."""
    if len(field.value_regions) == len(field.values) and field.values:
        pairs = [(v, b) for v, b in zip(field.values, field.value_regions) if b and _on_part(b, axis, cut, number)]
        return [v for v, _ in pairs], [b for _, b in pairs]
    if field.values and field.region and _on_part(field.region, axis, cut, number):
        return list(field.values), [field.region] if len(field.values) == 1 else []
    return [], []


def _union(boxes: list[Region]) -> Region:
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def _put_parts(batch: Batch, old: FormRecord, parts: list[FormRecord], page: dict | None) -> None:
    """The two parts take the page's place, in order; the original is kept for undo."""
    at = next((i for i, r in enumerate(batch.records) if r is old), None)
    if at is None:
        return
    batch.records[at:at + 1] = parts
    for part in parts:
        batch.page_splits[part.record_id] = old
    if page is not None:
        page.update(status=PAGE_FAILED if parts[0].error else PAGE_DONE, record_id=parts[0].record_id,
                    source_index=parts[0].source_index, page_number=1)
    batch.pages.append({"source_index": parts[1].source_index, "page_number": 1,
                        "status": PAGE_FAILED if parts[1].error else PAGE_DONE, "record_id": parts[1].record_id})


def _cut(batch: Batch, old: FormRecord, image: Path, axis: str, cut: float) -> list[SourceFile]:
    """Two pictures from the page. Side by side: the right one first (Hebrew order)."""
    with Image.open(image) as page:
        page = page.convert("RGB")
        w, h = page.size
        sources = []
        for number in (1, 2):
            a, b = _bounds(axis, cut, number)
            box = (0, round(h * a), w, round(h * b)) if axis == "y" else (round(w * a), 0, round(w * b), h)
            index = max(s.index for s in batch.sources) + 1
            source = SourceFile(index=index, filename=f"{old.source_name} עמ׳ {old.page_number} · חלק {number}",
                                kind="png", page_count=1, joined=True)
            batch.sources.append(source)
            target = _page_of(batch, source)
            target.parent.mkdir(parents=True, exist_ok=True)
            page.crop(box).save(target, format="PNG")
            sources.append(source)
    return sources


def _page_of(batch: Batch, source: SourceFile) -> Path:
    return source_dir(batch.batch_id, source.index) / "pages" / "page-001.png"


def unsplit_page(batch: Batch, record_id: str) -> FormRecord:
    """Undo a split: the original page comes back as one form. Free."""
    with _LOCK:
        original = batch.page_splits.get(record_id)
        if original is None:
            raise SplitPageRefused("הטופס הזה לא פוצל מעמוד.")
        parts = [r for r in batch.records if batch.page_splits.get(r.record_id) is original]
        if any(p.verified for p in parts):
            raise SplitPageRefused("אחד החלקים כבר אושר — אי אפשר לבטל את הפיצול.")
        at = min(batch.records.index(p) for p in parts)
        for part in parts:
            batch.records.remove(part)
            batch.page_splits.pop(part.record_id, None)
        batch.records.insert(at, original)
        ids = {p.record_id for p in parts}
        batch.pages = [p for p in batch.pages if p.get("record_id") not in ids]
        batch.pages.insert(min(at, len(batch.pages)), {"source_index": original.source_index,
                                                       "page_number": original.page_number,
                                                       "status": PAGE_DONE, "record_id": original.record_id})
    return original
