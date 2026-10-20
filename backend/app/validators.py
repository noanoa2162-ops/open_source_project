"""Domain rules that decide which fields a human must look at.

These never change a value — they only raise a flag and say why. A field that
passes every rule here is still only *unflagged*, never *verified*: green in the
UI means a person approved it, not that a validator liked it.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from app.entities import canonical_sender
from app.fields import FIELDS_BY_KEY
from app.models import ExtractedField, FormRecord

DATE_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
# שם: אותיות עבריות או לטיניות, רווח, מקף (גם מקף עברי), גרש/גרשיים ונקודה (ראשי תיבות).
# `?` מותר כאן — יש לו סימון משלו ("חלק מהתווים לא קריאים").
NAME_RE = re.compile(r"^[א-תA-Za-z\s\-־'׳״\".?]+$")
CENTRAL_LENGTH = (5, 7)  # בכל הדוגמאות: 6 ספרות. רק טווח סביר — לא חוק
MIN_COMMITMENT_LENGTH = 4  # בדוגמאות: מ-4 ספרות (2657) עד 12 — לכן רק "קצר מדי"
MAX_RECEIVED_AGE_DAYS = 365

REASON_TEXT = {
    "missing": "השדה ריק — המודל לא מצא ערך",
    "not_digits": "הערך מכיל תווים שאינם ספרות",
    "id_length": "תעודת זהות שאינה באורך 9 ספרות",
    "id_checksum": "ספרת הביקורת אינה מתאימה",
    "bad_date": "התאריך אינו בפורמט DD/MM/YYYY",
    "impossible_date": "תאריך שאינו קיים בלוח השנה",
    "multiple_values": "נמצאו כמה ערכים — לוודא שכולם שייכים לטופס",
    "model_uncertain": "המודל ציין שהוא אינו בטוח",
    "illegible": "חלק מהתווים לא קריאים",
    "low_resolution": "רזולוציה נמוכה — כתב יד עלול לא להיקרא",
    "unknown_entity": "גורם שולח שאינו ברשימה המוכרת",
    "date_before_issue": "תאריך הקבלה מוקדם מתאריך ההפקה",
    "date_in_future": "תאריך הקבלה בעתיד",
    "date_is_birth": "תאריך הקבלה זהה לתאריך הלידה",
    "date_too_old": "תאריך הקבלה ישן מאוד — לוודא",
    "name_not_letters": "השם מכיל ספרות או סימנים",
    "name_too_short": "שם קצר באופן חריג",
    "central_length": "מספר מרכזי באורך חריג (בדרך כלל 6 ספרות)",
    "commitment_too_short": "מספר התחייבות קצר באופן חריג",
    "central_is_commitment": "אותו מספר גם במרכזי וגם בהתחייבות — לוודא",
    "id_is_other_number": "הת\"ז זהה למספר אחר בטופס — לוודא",
    "not_on_form": "השדה לא נמצא בטופס — לאשר כריק או למלא",
    "commitment_from_sticker": "נמצא לפי מדבקת הטופס, בלי כיתוב לידו — לוודא",
    "commitment_like_central": "נראה כמו מספר מרכזי (6 ספרות, 93xxxx–94xxxx) — לוודא",
    "names_read_twice_same": "השם נקרא פעמיים (גם מתקריב) — אותה קריאה, עדיין לוודא",
    "names_two_readings": "שתי קריאות שונות של השם (הדף והתקריב) — לבחור",
    "joined_halves": "הטופס חובר משני קבצים — לוודא שהערך שייך לטופס",
    "unknown_char": "בערך יש תו שלא נקרא (?) — יש לתקן לפני אישור",
    "split_page": "העמוד פוצל — לוודא שהערך שייך לטופס הזה",
    "joined_conflict": "שני חלקי הטופס נתנו ערכים שונים — לבחור",
}


def validate_record(
    record: FormRecord, model_note: str = "", *, today: date | None = None
) -> None:
    """Flag every field that needs a human. Mutates the record in place."""
    for key, field in record.fields.items():
        spec = FIELDS_BY_KEY[key]

        if field.is_empty:
            # שדה שהקורא חיפש ולא מצא כבר מסומן "לא קיים בטופס".
            if "not_on_form" not in field.reasons:
                field.flag("missing")
            continue

        if len(field.values) > 1:
            field.flag("multiple_values")

        for value in field.values:
            if "?" in value:
                field.flag("unknown_char")  # תו שלא נקרא — אי אפשר לאשר עד שמתקנים
            if spec.kind == "digits":
                _check_digits(field, value, key)
            elif spec.kind == "date":
                _check_date(field, value)
            elif key in ("first_name", "last_name"):
                _check_name(field, value)

    _check_sender(record.fields["sending_entity"])
    _check_received_date(record, today or date.today())
    _check_numbers_across_fields(record)

    if model_note:
        record.model_note = model_note
        # מסמנים רק את השדות שההערה באמת נוקבת בשמם. אם היא כללית, היא נשארת
        # ברמת השורה — לצבוע שבעה שדות בגלל משפט מעורפל זה רעש, לא מידע.
        for key in _fields_named_in(model_note):
            record.fields[key].flag("model_uncertain")


# תחיליות עבריות נפוצות שנדבקות למילה ומונעות התאמה פשוטה.
_PREFIXES = "הבלושכמ"


def _normalise(word: str) -> set[str]:
    word = word.strip(".,;:()\"'״׳")
    forms = {word}
    for count in (1, 2):
        if len(word) > count + 1 and all(c in _PREFIXES for c in word[:count]):
            forms.add(word[count:])
    return forms


def _fields_named_in(note: str) -> list[str]:
    """Which of the seven fields does this free-text note actually name?"""
    tokens: set[str] = set()
    for raw in note.split():
        tokens |= _normalise(raw)

    named = []
    for key, spec in FIELDS_BY_KEY.items():
        words = spec.label_he.split()
        if words and all(word in tokens for word in words):
            named.append(key)
    return named


def _check_digits(field: ExtractedField, value: str, key: str) -> None:
    if not value.isdigit():
        field.flag("not_digits")
        return
    if key == "central_numbers" and not CENTRAL_LENGTH[0] <= len(value) <= CENTRAL_LENGTH[1]:
        field.flag("central_length")
    if key == "commitment_numbers" and len(value) < MIN_COMMITMENT_LENGTH:
        field.flag("commitment_too_short")
    if key == "commitment_numbers" and len(value) == 6 and value[:2] in ("93", "94"):
        field.flag("commitment_like_central")  # נראה כמו מדבקת "מרכזי"
    if key == "national_id":
        if len(value) != 9:
            field.flag("id_length")
        elif not _israeli_id_valid(value):
            # כשל ביקורת מסמן בדיקה; אינו הוכחה שהקריאה שגויה.
            field.flag("id_checksum")


def _check_date(field: ExtractedField, value: str) -> None:
    match = DATE_RE.match(value)
    if not match:
        field.flag("bad_date")
        return
    day, month, year = (int(g) for g in match.groups())
    if not 1900 <= year <= 2100 or _parse(value) is None:
        # כולל 31/02 או 30/02 — תאריך שאינו קיים בלוח השנה
        field.flag("impossible_date")


def _check_name(field: ExtractedField, value: str) -> None:
    if not NAME_RE.match(value):
        field.flag("name_not_letters")
        return
    letters = [c for c in value if c.isalpha()]
    if len(letters) < 2 and "?" not in value:
        field.flag("name_too_short")


def _check_numbers_across_fields(record: FormRecord) -> None:
    """The same number in two different fields usually means one of them was misread."""
    central = record.fields["central_numbers"]
    commitment = record.fields["commitment_numbers"]
    national_id = record.fields["national_id"]
    shared = set(central.values) & set(commitment.values)
    if shared:
        central.flag("central_is_commitment")
        commitment.flag("central_is_commitment")
    for other in (central, commitment):
        if set(national_id.values) & set(other.values):
            national_id.flag("id_is_other_number")
            other.flag("id_is_other_number")


def _israeli_id_valid(value: str) -> bool:
    total = 0
    for index, char in enumerate(value):
        digit = int(char) * (1 if index % 2 == 0 else 2)
        total += digit if digit < 10 else digit - 9
    return total % 10 == 0


def _check_sender(field: ExtractedField) -> None:
    for value in field.values:
        if canonical_sender(value) is None:
            field.flag("unknown_entity")


def _parse(value: str) -> date | None:
    match = DATE_RE.match(value or "")
    if not match:
        return None
    day, month, year = (int(g) for g in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _check_received_date(record: FormRecord, today: date) -> None:
    """A valid-looking date can still be the wrong date on the form."""
    field = record.fields["sample_received_date"]
    issued = _parse(record.context.get("issue_date", ""))
    born = _parse(record.context.get("birth_date", ""))
    for value in field.values:
        received = _parse(value)
        if received is None:
            continue
        if received > today:
            field.flag("date_in_future")
        elif received < today - timedelta(days=MAX_RECEIVED_AGE_DAYS):
            field.flag("date_too_old")
        if issued and received < issued:
            field.flag("date_before_issue")
        if born and received == born:
            field.flag("date_is_birth")

