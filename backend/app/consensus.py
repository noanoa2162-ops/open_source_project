"""Turn the reader's answer for one page into field values and review flags.

One strong model (Claude Opus, deep thinking) reads each page. A value it is
sure of keeps its normal colour; any doubt it reports — partly legible, an
unreadable `?`, illegible — turns the field orange, because there is no
second reader to confirm it. A field it looked for and did not find is
"לא קיים בטופס", also orange. Only a person makes a field green.

Everything here is local: no model is asked again to settle anything.
"""

from __future__ import annotations

import re
import unicodedata

from app.entities import canonical_sender
from app.fields import FIELDS, FIELDS_BY_KEY
from app.models import STATE_REVIEW, FormRecord
from app.providers.base import LEGIBILITY_ILLEGIBLE, LEGIBILITY_PARTIAL, RawExtraction

UNREADABLE = "?"

# שדות שבדרך כלל כתובים ביד. ברזולוציה נמוכה הם מסומנים גם כשהקורא בטוח.
HANDWRITING_PRONE = ("first_name", "last_name", "national_id", "sample_received_date")

_DOUBT = (LEGIBILITY_PARTIAL, LEGIBILITY_ILLEGIBLE)
_DATE = re.compile(r"^([\d?]{1,2})\s*[./\-\s]\s*([\d?]{1,2})\s*[./\-\s]\s*([\d?]{2,4})$")
# ניקוד וטעמים בלבד — בלי מקף עברי ובלי סימני פיסוק, שהם חלק מהשם.
_NIQQUD = re.compile("[֑-ׇֽֿׁׂׅׄ]")
_NOT_DIGIT = re.compile(r"[^\d?]")


def normalize(key: str, value: str) -> str:
    """Remove differences that are not differences: spacing, separators, quote marks.

    Never changes a character that was actually read — `0012` stays `0012`.
    """
    value = unicodedata.normalize("NFC", value).strip()
    kind = FIELDS_BY_KEY[key].kind

    if kind == "digits":
        stripped = value.replace(" ", "").replace("-", "").replace(".", "").replace("/", "")
        # אותיות בתוך מספר הן ממצא — משאירים אותן, הכללים יסמנו.
        return stripped if not _NOT_DIGIT.search(stripped) else " ".join(value.split())

    if kind == "date":
        match = _DATE.match(value)
        if not match:
            return " ".join(value.split())
        day, month, year = match.groups()
        if len(year) == 2:
            year = "20" + year
        return f"{day.zfill(2)}/{month.zfill(2)}/{year}"

    text = _NIQQUD.sub("", value).replace("״", '"').replace("׳", "'")
    text = " ".join(text.split())
    if key == "sending_entity":
        return canonical_sender(text) or text
    return text


def _normalized(key: str, reading: RawExtraction) -> tuple[list[str], list]:
    """Clean values, without repeats — and the position of each, in the same order."""
    seen: list[str] = []
    places: list = []
    given = reading.value_regions.get(key) or []
    for i, value in enumerate(reading.values.get(key, [])):
        clean = normalize(key, value)
        if clean and clean not in seen:
            seen.append(clean)
            places.append(given[i] if i < len(given) else None)
    return seen, places if any(places) else []


def combine(record: FormRecord, reading: RawExtraction) -> None:
    """Fill `record` from the reader's answer. Mutates in place."""
    record.readers = [reading.model]
    record.template = reading.template
    record.model_note = reading.note
    record.context = _context(reading)
    record.upside_down = reading.upside_down
    record.forms_on_page = reading.forms_on_page
    record.form_regions = list(reading.form_regions)
    record.continues_previous = reading.continues_previous

    for spec in FIELDS:
        field = record.fields[spec.key]
        field.region = reading.regions.get(spec.key)
        field.values, field.value_regions = _normalized(spec.key, reading)
        doubt = reading.legibility.get(spec.key) in _DOUBT
        if doubt or any(UNREADABLE in v for v in field.values):
            field.flag("illegible")
        elif not field.values:
            field.flag("not_on_form")  # הקורא חיפש ולא מצא — "לא קיים בטופס"

    # מספר התחייבות שנלקח ממדבקת הטופס, בלי כיתוב לידו — תמיד לבדיקה של אדם (הנחיית צוות האתגר)
    commitment = record.fields["commitment_numbers"]
    if commitment.values and reading.context.get("commitment_found_by") == "form_sticker":
        commitment.flag("commitment_from_sticker")

    if record.low_resolution:
        for key in HANDWRITING_PRONE:
            record.fields[key].flag("low_resolution")


NAME_KEYS = ("first_name", "last_name")


def merge_names(record: FormRecord, check) -> bool:
    """Fold the close-up re-reading of the name into the record. Returns whether it
    was used. Never makes a field green: agreement keeps it orange with a reason
    that it was read twice; disagreement shows both readings, the close-up first."""
    if check is None or not check.found:
        return False
    used = False
    for key in NAME_KEYS:
        field = record.fields[key]
        fresh: list[str] = []
        for value in getattr(check, key):
            clean = normalize(key, value)
            if clean and clean not in fresh:
                fresh.append(clean)
        if not fresh:
            continue
        used = True
        if fresh == field.values:
            if field.state == STATE_REVIEW:
                field.flag("names_read_twice_same")
            continue
        field.values = fresh + [v for v in field.values if v not in fresh]
        field.value_regions = []  # המיקומים היו של הקריאה הראשונה
        field.flag("names_two_readings")
    return used


def _context(reading: RawExtraction) -> dict[str, str]:
    """Issue and birth dates, used only to sanity-check the received date."""
    context: dict[str, str] = {}
    for key in ("issue_date", "birth_date"):
        value = reading.context.get(key)
        if value:
            clean = normalize("sample_received_date", value)
            if UNREADABLE not in clean:
                context[key] = clean
    return context
