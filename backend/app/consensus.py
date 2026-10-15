"""Compare two independent judges, field by field.

The rule the whole system rests on: a value is filled in only when both
judges read the same thing. Anything else — disagreement, one judge finding
nothing, an unreadable character, a judge admitting doubt — becomes orange
"לא ברור" with what each judge read, for the secretary to decide against the
original. Agreement is never shown as "correct"; only a person makes it green.
"""

from __future__ import annotations

import re
import unicodedata

from app.entities import canonical_sender
from app.fields import FIELDS, FIELDS_BY_KEY, MULTI_SEPARATOR
from app.models import FormRecord
from app.providers.base import LEGIBILITY_ILLEGIBLE, LEGIBILITY_PARTIAL, RawExtraction

UNREADABLE = "?"

# שדות שבדרך כלל כתובים ביד. ברזולוציה נמוכה הם מסומנים גם כשהשופטים הסכימו.
HANDWRITING_PRONE = ("first_name", "last_name", "national_id", "sample_received_date")

_DOUBT = (LEGIBILITY_PARTIAL, LEGIBILITY_ILLEGIBLE)
_DATE = re.compile(r"^([\d?]{1,2})\s*[./\-\s]\s*([\d?]{1,2})\s*[./\-\s]\s*([\d?]{2,4})$")
# ניקוד וטעמים בלבד — בלי מקף עברי ובלי סימני פיסוק, שהם חלק מהשם.
_NIQQUD = re.compile("[֑-ׇֽֿׁׂׅׄ]")
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


def _normalized(key: str, reading: RawExtraction) -> list[str]:
    seen: list[str] = []
    for value in reading.values.get(key, []):
        clean = normalize(key, value)
        if clean and clean not in seen:
            seen.append(clean)
    return seen


def _has_unreadable(values: list[str]) -> bool:
    return any(UNREADABLE in v for v in values)


def combine(
    record: FormRecord, readings: list[RawExtraction], *, flag_single_reader: bool = True
) -> None:
    """Fill `record` from one or two judges' readings. Mutates in place.

    With one reading (the other judge failed, or a single-model comparison
    run) every field is flagged `single_reader` unless told otherwise.
    """
    if not readings:
        return
    record.readers = [r.model for r in readings]
    record.template = next((r.template for r in readings if r.template), "")
    record.model_note = " · ".join(dict.fromkeys(r.note for r in readings if r.note))
    record.context = _agreed_context(readings)

    for spec in FIELDS:
        field = record.fields[spec.key]
        # מיקום רק משופט שהמיקומים שלו אמינים. בלי כזה — אין הגדלה, והמסך מציג את
        # הטופס המלא. עדיף בלי תקריב מאשר תקריב של המקום הלא נכון.
        field.region = next(
            (
                r.regions.get(spec.key)
                for r in readings
                if r.regions_reliable and r.regions.get(spec.key)
            ),
            None,
        )
        reads = [_normalized(spec.key, r) for r in readings]
        doubt = any(r.legibility.get(spec.key) in _DOUBT for r in readings)
        field.readings = [
            {"model": r.model, "values": values, "legibility": r.legibility.get(spec.key, "")}
            for r, values in zip(readings, reads)
        ]

        if len(reads) == 1:
            field.values = reads[0]
            if flag_single_reader:
                field.flag("single_reader")
            if doubt or _has_unreadable(reads[0]):
                field.flag("illegible")
            continue

        first, second = reads
        if sorted(first) == sorted(second):
            field.values = first  # הסדר של הקורא הראשון; ערכים מרובים לא ממוינים בייצוא
            if doubt or _has_unreadable(first):
                field.flag("illegible")
            continue

        # אין הסכמה: לא ממלאים ערך של אף אחד מהם. המזכירה מכריעה מול המקור.
        field.values = []
        field.candidates = [MULTI_SEPARATOR.join(v) for v in reads if v]
        field.flag("readers_disagree" if first and second else "single_value")
        if doubt:
            field.flag("illegible")

    if record.low_resolution:
        for key in HANDWRITING_PRONE:
            record.fields[key].flag("low_resolution")


def _agreed_context(readings: list[RawExtraction]) -> dict[str, str]:
    """Issue and birth dates, used only to sanity-check the received date.

    With two readers a context date is used only if they agree: a wrong
    context date would raise a false alarm on a correct received date.
    """
    context: dict[str, str] = {}
    for key in ("issue_date", "birth_date"):
        seen = [normalize("sample_received_date", r.context[key]) for r in readings if r.context.get(key)]
        if not seen:
            continue
        if len(readings) == 1 or (len(seen) == len(readings) and len(set(seen)) == 1):
            if UNREADABLE not in seen[0]:
                context[key] = seen[0]
    return context
