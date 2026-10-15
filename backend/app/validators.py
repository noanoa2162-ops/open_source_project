"""Domain rules that decide which fields a human must look at.

These never change a value — they only raise a flag and say why. A field that
passes every rule here is still only *unflagged*, never *verified*: green in the
UI means a person approved it, not that a validator liked it.
"""

from __future__ import annotations

import re
from datetime import date

from app.entities import canonical_sender
from app.fields import FIELDS_BY_KEY
from app.models import ExtractedField, FormRecord

DATE_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")

REASON_TEXT = {
    "missing": "השדה ריק — המודל לא מצא ערך",
    "not_digits": "הערך מכיל תווים שאינם ספרות",
    "id_length": "תעודת זהות שאינה באורך 9 ספרות",
    "id_checksum": "ספרת הביקורת אינה מתאימה",
    "bad_date": "התאריך אינו בפורמט DD/MM/YYYY",
    "impossible_date": "תאריך שאינו קיים בלוח השנה",
    "multiple_values": "נמצאו כמה ערכים — לוודא שכולם שייכים לטופס",
    "model_uncertain": "המודל ציין שהוא אינו בטוח",
    "readers_disagree": "שני הקוראים לא הסכימו — בחרו או תקנו",
    "single_value": "רק אחד הקוראים מצא ערך",
    "illegible": "חלק מהתווים לא קריאים",
    "single_reader": "נבדק על ידי קורא אחד בלבד",
    "low_resolution": "רזולוציה נמוכה — כתב יד עלול לא להיקרא",
    "unknown_entity": "גורם שולח שאינו ברשימה המוכרת",
    "date_before_issue": "תאריך הקבלה מוקדם מתאריך ההפקה",
    "date_in_future": "תאריך הקבלה בעתיד",
    "date_is_birth": "תאריך הקבלה זהה לתאריך הלידה",
    "not_on_form": "השדה לא נמצא בטופס — לאשר כריק או למלא",
    # הערות רכות: מוצגות ליד הערך, בלי כתום
    "minor_doubt": "ספק קטן — מומלץ לבדוק",
    "extra_value": "מודל אחד מצא ערך נוסף — מומלץ לבדוק",
}


def validate_record(
    record: FormRecord, model_note: str = "", *, today: date | None = None
) -> None:
    """Flag every field that needs a human. Mutates the record in place."""
    for key, field in record.fields.items():
        spec = FIELDS_BY_KEY[key]

        if field.is_empty:
            # שדה במחלוקת כבר מסומן עם האפשרויות שלו — "ריק" היה מטעה.
            # שדה ששני השופטים לא מצאו כבר מסומן "לא קיים בטופס".
            if not field.candidates and "not_on_form" not in field.reasons:
                field.flag("missing")
            continue

        if len(field.values) > 1:
            field.flag("multiple_values")

        for value in field.values:
            if spec.kind == "digits":
                _check_digits(field, value, key)
            elif spec.kind == "date":
                _check_date(field, value)

    _check_sender(record.fields["sending_entity"])
    _check_received_date(record, today or date.today())

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
    if not (1 <= month <= 12 and 1 <= day <= 31 and 1900 <= year <= 2100):
        field.flag("impossible_date")


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
        if issued and received < issued:
            field.flag("date_before_issue")
        if born and received == born:
            field.flag("date_is_birth")

