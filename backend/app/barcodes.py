"""Barcodes on the page — a second, exact and free reader for the numbers.

The stickers on these forms carry barcodes: the "מרכזי" sticker encodes the
central number, and many hospital and kupa forms encode the commitment number.
A barcode is read by a local decoder (zxing-cpp), digit for digit, with a
built-in checksum — no model, no cost, about half a second per page.

What a barcode may change, and what it may not:
- **Catch a misread.** A value that differs from the barcode right next to it by
  one or two digits, or has a `?` the barcode fills → orange, with the barcode's
  value offered for one click. This turns a silent digit error into a visible one.
- **Vouch for the digits, never for the field.** A value equal to a barcode on the
  page shows "✓ ברקוד": the digits are exact, so the secretary only checks that the
  number belongs to this field. It never removes an orange flag. Measured 22/10/2026
  on the 48 sample pages: a reader put the commitment number (a barcode) in the
  central-number field; the digits matched, the field did not. Clearing the flag
  there would have been a silent error.
- **Never write a value.** Only a person picks a suggestion. Barcode text is data,
  never an instruction.
"""

from __future__ import annotations

import json
import re
import threading
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image

from app.models import FormRecord, Region

# שדות של מספרים שמדבקת ברקוד יכולה לאמת
NUMBER_KEYS = ("central_numbers", "commitment_numbers", "national_id")
# שדות שמותר להציע להם ברקוד כשהם ריקים (ת"ז — לא: מדבקת מטופל מכילה מספרים רבים)
SUGGEST_WHEN_EMPTY = ("central_numbers", "commitment_numbers")
# מרחק מותר בין הברקוד לערך: המספר המודפס יושב מתחת לברקוד או לצדו
NEAR_MARGIN = 0.05
MIN_LENGTH = 4  # ברקוד קצר מזה הוא לא מספר התחייבות/מרכזי (בדוגמאות: מ-4 ספרות)
MAX_SUFFIX = 3  # מכבי: הברקוד = מספר הדרישה + 2–3 ספרות מבחנה ("50364186" → "50364186700")
MAX_SUGGESTIONS = 3

_SPACES = re.compile(r"\s+")
_USABLE = re.compile(r"^[0-9A-Za-z\-]+$")
_CACHE: dict[str, list["Barcode"]] = {}
_PENDING: dict[str, Future] = {}
_LOCK = threading.Lock()
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="barcodes")
MAX_CACHED_PAGES = 2000


@dataclass(frozen=True)
class Barcode:
    text: str  # התוכן, בלי רווחים
    kind: str  # Code128, Code39, ITF…
    region: Region  # איפה בעמוד (0–1)


def sidecar(page_png: Path) -> Path:
    return page_png.with_name(page_png.stem + ".barcodes.json")


def prefetch(page_png: Path) -> None:
    """Start decoding now, in the background, while the reader is busy with the same
    page — so the barcodes are ready by the time its answer arrives."""
    key = str(page_png)
    with _LOCK:
        if key in _CACHE or key in _PENDING:
            return
        _PENDING[key] = _POOL.submit(_read_now, page_png)


def read(page_png: Path) -> list[Barcode]:
    """Every barcode on the page, decoded locally. Saved next to the page, so it is
    decoded once. Never raises: a page without a readable barcode is just []."""
    key = str(page_png)
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key]
        pending = _PENDING.get(key)
    if pending is not None:
        try:
            return pending.result()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return []
    return _read_now(page_png)


def _read_now(page_png: Path) -> list[Barcode]:
    key = str(page_png)
    try:
        found = _load(page_png)
        if found is None:
            found = _decode(page_png)
            _store(page_png, found)
    finally:
        with _LOCK:
            _PENDING.pop(key, None)
    with _LOCK:
        if len(_CACHE) >= MAX_CACHED_PAGES:
            _CACHE.clear()
        _CACHE[key] = found
    return found


def forget(page_png: Path) -> None:
    """The page image changed (turned, cut): its barcodes must be read again."""
    with _LOCK:
        _CACHE.pop(str(page_png), None)
    sidecar(page_png).unlink(missing_ok=True)


def _decode(page_png: Path) -> list[Barcode]:
    try:
        import zxingcpp
    except ImportError:  # הספרייה חסרה — המערכת עובדת כמו קודם, בלי אימות ברקוד
        return []
    try:
        with Image.open(page_png) as page:
            gray = page.convert("L")
        width, height = gray.size
        results = zxingcpp.read_barcodes(gray)
    except Exception:  # noqa: BLE001 - תקלה בפענוח לא מפילה את קריאת הטופס
        traceback.print_exc()
        return []
    found: list[Barcode] = []
    for result in results:
        if not getattr(result, "valid", True):
            continue
        text = _SPACES.sub("", result.text or "")
        if len(text) < MIN_LENGTH or not _USABLE.match(text):
            continue
        p = result.position
        xs = [p.top_left.x, p.top_right.x, p.bottom_left.x, p.bottom_right.x]
        ys = [p.top_left.y, p.top_right.y, p.bottom_left.y, p.bottom_right.y]
        region = (
            max(0.0, min(xs) / width), max(0.0, min(ys) / height),
            min(1.0, max(xs) / width), min(1.0, max(ys) / height),
        )
        barcode = Barcode(text=text, kind=str(result.format.name), region=region)
        if barcode not in found:
            found.append(barcode)
    return found


def _load(page_png: Path) -> list[Barcode] | None:
    path = sidecar(page_png)
    if not path.is_file():
        return None
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
        return [Barcode(text=r["text"], kind=r["kind"], region=tuple(r["region"])) for r in rows]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _store(page_png: Path, found: list[Barcode]) -> None:
    try:
        sidecar(page_png).write_text(
            json.dumps([asdict(b) for b in found], ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass


# ------------------------------------------------------------------ התאמה לשדות
def _near(barcode: Region, value: Region | None) -> bool:
    """Is the value (as the reader located it) on or right next to this barcode?"""
    if value is None:
        return False
    x0, y0, x1, y1 = barcode
    margin_x = max(NEAR_MARGIN, (x1 - x0) * 0.5)
    margin_y = max(NEAR_MARGIN, (y1 - y0) * 0.8)
    v0, w0, v1, w1 = value
    return not (v1 < x0 - margin_x or v0 > x1 + margin_x or w1 < y0 - margin_y or w0 > y1 + margin_y)


def _matches(value: str, barcode: str) -> bool:
    """The digits of the value are the barcode's — exactly, or the barcode is the value
    plus a short tube suffix (Maccabi). Never a fuzzy match."""
    if value == barcode:
        return True
    return len(value) >= 5 and barcode.startswith(value) and 0 < len(barcode) - len(value) <= MAX_SUFFIX


def _close(value: str, barcode: str) -> bool:
    """Same length and at most two different characters, or a `?` the barcode fills:
    almost certainly the same printed number, misread."""
    if len(value) != len(barcode) or value == barcode:
        return False
    different = [i for i, (a, b) in enumerate(zip(value, barcode)) if a != b]
    if all(value[i] == "?" for i in different):
        return True
    return len(different) <= 2 and len(value) >= 5


def _region_of_value(field, index: int) -> Region | None:
    if len(field.value_regions) == len(field.values) and field.value_regions[index]:
        return field.value_regions[index]
    return field.region if len(field.values) == 1 else None


def apply(record: FormRecord, barcodes: list[Barcode]) -> None:
    """Confirm, question or suggest number fields from the page's barcodes.
    Mutates the record. Call after validation, so every other flag is already set."""
    record.barcodes = [asdict(b) for b in barcodes]
    if not barcodes:
        return
    used: set[str] = set()
    for key in NUMBER_KEYS:
        field = record.fields[key]
        field.confirmed_by = []
        field.suggestions = []
        if not field.values:
            continue
        confirmed = 0
        suggestions: list[str] = []
        for index, raw in enumerate(field.values):
            value = _SPACES.sub("", raw)
            place = _region_of_value(field, index)
            exact = [b for b in barcodes if _matches(value, b.text)]
            if exact:
                used.update(b.text for b in exact)
                confirmed += 1  # הספרות זהות לברקוד — לא השיוך לשדה
                continue
            for barcode in barcodes:
                if _close(value, barcode.text) and _near(barcode.region, place):
                    if barcode.text not in suggestions:
                        suggestions.append(barcode.text)
                    used.add(barcode.text)
        if suggestions:
            field.suggestions = suggestions[:MAX_SUGGESTIONS]
            field.flag("barcode_differs")
        if confirmed == len(field.values) and not suggestions:
            # סימון בלבד: הספרות מדויקות. הכתום (אם יש) נשאר — השיוך לשדה עדיין בידי אדם.
            field.confirmed_by = ["barcode"]

    # שדה ריק — ברקודים שלא שויכו לשום שדה, להשלמה בלחיצה (השדה נשאר כתום)
    spare = [b.text for b in barcodes if b.text not in used and b.text.isdigit()]
    for key in SUGGEST_WHEN_EMPTY:
        field = record.fields[key]
        if not field.values and spare:
            field.suggestions = spare[:MAX_SUGGESTIONS]
            field.flag("barcode_candidates")


def after_person(record: FormRecord, key: str) -> None:
    """A person set this field's values: suggestions are spent. The "✓ ברקוד" mark stays
    only if every value they kept is exactly a barcode on the page."""
    field = record.fields[key]
    field.suggestions = []
    texts = {b.get("text", "") for b in record.barcodes}
    values = [_SPACES.sub("", v) for v in field.values]
    field.confirmed_by = (
        ["barcode"] if key in NUMBER_KEYS and values and all(
            any(_matches(v, t) for t in texts) for v in values) else []
    )


def summary(records: list[FormRecord]) -> dict:
    """How the barcodes helped this batch, for the screen."""
    confirmed = sum(1 for r in records for f in r.fields.values() if "barcode" in f.confirmed_by)
    questioned = sum(1 for r in records for f in r.fields.values() if "barcode_differs" in f.reasons)
    pages = sum(1 for r in records if r.barcodes)
    return {"pages_with_barcodes": pages, "confirmed_fields": confirmed, "questioned_fields": questioned}
