"""Turn the reading(s) of one page into field values and review flags.

With one reader (the default since 15/10/2026: Claude Opus with deep thinking)
a value it is sure of keeps its normal colour and any doubt it reports turns
the field orange. With two judges configured, the rules below apply.

The rule the whole system rests on: a value is filled in only when both
judges read the same thing. Disagreement, one judge finding nothing, an
unreadable character or a judge calling the field illegible becomes orange
"לא ברור" with what each judge read, for the secretary to decide against the
original. Agreement is never shown as "correct"; only a person makes it green.

Two softer cases keep the agreed value and its normal colour, with a small
remark instead of orange: both read the same value but one called it only
partly legible ("ספק קטן"), and one judge found everything the other found plus
one more value (the shared value is kept, the extra one is offered).

Everything here is local: no model is asked again to settle anything.
"""

from __future__ import annotations

import difflib
import re
import unicodedata

from app.crops import padded
from app.entities import canonical_sender
from app.fields import FIELDS, FIELDS_BY_KEY, MULTI_SEPARATOR
from app.models import ExtractedField, FormRecord
from app.providers.base import LEGIBILITY_ILLEGIBLE, LEGIBILITY_PARTIAL, RawExtraction, Region

UNREADABLE = "?"

# שדות שבדרך כלל כתובים ביד. ברזולוציה נמוכה הם מסומנים גם כשהשופטים הסכימו.
HANDWRITING_PRONE = ("first_name", "last_name", "national_id", "sample_received_date")

_DOUBT = (LEGIBILITY_PARTIAL, LEGIBILITY_ILLEGIBLE)
# שתי קריאות "דומות" — הפרש של עד 2 תווים, או 70% תווים משותפים — הן כנראה אותו
# כתב יד שנקרא קצת אחרת, ותקריב אחד מספיק. שונות מאוד → תקריב לכל שופט.
SIMILAR_MAX_EDITS = 2
SIMILAR_MIN_RATIO = 0.7
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
        _place(field, spec.key, readings)
        reads = [_normalized(spec.key, r) for r in readings]
        levels = [r.legibility.get(spec.key, "") for r in readings]
        doubt = any(level in _DOUBT for level in levels)
        field.readings = [
            {"model": r.model, "values": values, "legibility": r.legibility.get(spec.key, "")}
            for r, values in zip(readings, reads)
        ]

        if len(reads) == 1:
            # קורא אחד: בטוח (clear) → צבע רגיל; כל ספק (חלקי, `?`, לא קריא) → כתום,
            # כי אין קורא שני שיאשר. הבדיקות המקומיות רצות אחר כך כרגיל.
            field.values = reads[0]
            if flag_single_reader:
                field.flag("single_reader")  # הוגדרו שני שופטים ואחד נפל
            if doubt or _has_unreadable(reads[0]):
                field.flag("illegible")
            elif not reads[0] and not flag_single_reader:
                field.flag("not_on_form")  # הקורא חיפש ולא מצא — "לא קיים בטופס"
            continue

        first, second = reads
        if sorted(first) == sorted(second):
            field.values = first  # הסדר של הקורא הראשון; ערכים מרובים לא ממוינים בייצוא
            if not first and not doubt:
                # שניהם חיפשו ולא מצאו, ואף אחד לא אמר "לא קריא": השדה לא קיים בטופס.
                field.flag("not_on_form")
            _mark_doubt(field, first, levels)
            continue

        shorter, longer = sorted((first, second), key=len)
        if shorter and set(shorter) < set(longer):
            # אחד מצא את כל מה שהשני מצא, ועוד ערך. המשותף נכנס; הנוסף מוצע בלחיצה.
            field.values = shorter
            field.extra_values = [v for v in longer if v not in shorter]
            field.note("extra_value")
            _mark_doubt(field, shorter, levels)
            continue

        # אין הסכמה: לא ממלאים ערך של אף אחד מהם. המזכירה מכריעה מול המקור.
        field.values = []
        field.candidates = [MULTI_SEPARATOR.join(v) for v in reads if v]
        field.flag("readers_disagree" if first and second else "single_value")
        if doubt:
            field.flag("illegible")
        if first and second and len(field.regions_by_model) >= 2:
            boxes = list(field.regions_by_model.values())
            field.two_crops = not _similar(first, second) or _apart(boxes[0], boxes[1])

    record.upside_down = all(r.upside_down for r in readings)

    if record.low_resolution:
        for key in HANDWRITING_PRONE:
            record.fields[key].flag("low_resolution")


def _mark_doubt(field: ExtractedField, values: list[str], levels: list[str]) -> None:
    """Orange for an unreadable character or an "illegible"; a small remark for "partial"."""
    if _has_unreadable(values) or LEGIBILITY_ILLEGIBLE in levels:
        field.flag("illegible")
    elif LEGIBILITY_PARTIAL in levels:
        field.note("minor_doubt")


def _place(field: ExtractedField, key: str, readings: list[RawExtraction]) -> None:
    """Where on the page the field is, per judge, and which location to start from.

    A verified judge's location comes first (Gemini). Without one, a judge whose
    locations may be shown as an unverified fallback is used (Claude), and the
    screen says so. A judge whose boxes landed in the wrong place in live runs
    (GPT) is never used: better the full page than a confident wrong crop.
    """
    for r in readings:
        region = r.regions.get(key)
        if region and (r.regions_reliable or r.regions_fallback):
            field.regions_by_model.setdefault(r.model, region)
    trusted = next((r for r in readings if r.regions_reliable and r.regions.get(key)), None)
    chosen = trusted or next((r for r in readings if r.regions_fallback and r.regions.get(key)), None)
    if chosen is not None:
        field.region = chosen.regions[key]
        field.region_by = chosen.model
        field.region_verified = trusted is not None


def _similar(first: list[str], second: list[str]) -> bool:
    a, b = MULTI_SEPARATOR.join(first), MULTI_SEPARATOR.join(second)
    if _edits(a, b) <= SIMILAR_MAX_EDITS:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= SIMILAR_MIN_RATIO


def _edits(a: str, b: str) -> int:
    """Levenshtein distance: how many characters to change, add or remove."""
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current = [i]
        for j, char_b in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (char_a != char_b)))
        previous = current
    return previous[-1]


def _apart(one: Region, other: Region) -> bool:
    """The two judges pointed at different places: their cut-outs do not even touch."""
    a, b = padded(one), padded(other)
    return a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1]


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
