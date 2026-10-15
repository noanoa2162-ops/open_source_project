"""The seven fields the challenge asks for — defined once, used everywhere.

The extraction prompt, the validators and the Excel columns are all generated
from this list, so adding or renaming a field happens in exactly one place.
"""

from __future__ import annotations

from dataclasses import dataclass

MULTI_SEPARATOR = " | "


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label_he: str
    multi: bool
    kind: str  # text | digits | date
    hint_he: str


FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("first_name", "שם פרטי", False, "text", "שם המבוטח/ת, לרוב בכתב יד"),
    FieldSpec("last_name", "שם משפחה", False, "text", "שם משפחה של המבוטח/ת"),
    FieldSpec("national_id", "תעודת זהות", False, "digits", "9 ספרות, כולל אפסים מובילים"),
    FieldSpec("central_numbers", "מספר מרכזי", True, "digits", "ליד הכיתוב ״מרכזי״, במדבקה או בכתב יד"),
    FieldSpec("sample_received_date", "תאריך קבלת הדגימה", False, "date", "מחותמת ״התקבל״ בלבד"),
    FieldSpec("sending_entity", "גורם שולח", False, "text", "הקופה/המוסד ששלח, לא נותן השירות"),
    FieldSpec("commitment_numbers", "מספר התחייבות", True, "digits", "גם ״דרישה״ או ״דגימה״"),
)

FIELDS_BY_KEY = {f.key: f for f in FIELDS}
