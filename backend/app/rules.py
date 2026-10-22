"""Hard rules: a value that breaks one of these can't be saved, approved or exported.

Unlike validators.py (which only raises an orange flag and lets a person decide), these
are basic data rules (Noa, 21/10/2026): one ID per person, a date is a real date, a name
is letters. When there's no value the only option is an empty field ("חסר") — never
"לא ברור" or other text in its place. Unsure? the secretary asks for help; she does not
approve.

Central and commitment numbers have no official format: any characters, as long as there
is at least one digit (Noa, 21/10/2026 — the samples are not a rule). An unknown sender
stays a soft flag: real senders (צה"ל, משרד הביטחון…) are not in the list.
"""

from __future__ import annotations

import re
from datetime import date

from app.fields import FIELDS_BY_KEY, MULTI_SEPARATOR
from app.models import STATE_REVIEW, FormRecord
from app.validators import PLACEHOLDER as _PLACEHOLDER, _israeli_id_valid, _parse

_NAME = re.compile(r"^[א-תA-Za-z\s\-־'׳״\".]+$")
_DATE = re.compile(r"^\d{2}/\d{2}/\d{4}$")


def problems(key: str, values: list[str], *, today: date | None = None) -> list[str]:
    """Why these values can't be saved in this field — empty list when they can.
    No values at all is fine: that is "חסר", approved explicitly as empty."""
    values = [v.strip() for v in values if v.strip()]
    if not values:
        return []
    spec = FIELDS_BY_KEY[key]
    found: list[str] = []
    if not spec.multi and len(values) > 1:
        found.append(f"ב{spec.label_he} מותר ערך אחד בלבד — יש למחוק את הערך שאינו נכון")
    for value in values:
        if "?" in value:
            found.append(f"ב\"{value}\" יש תו שלא נקרא (?) — יש לתקן")
        elif _PLACEHOLDER.match(value):
            found.append(f"\"{value}\" אינו ערך. אם אין ערך — משאירים את השדה ריק (חסר)")
        else:
            found += _value_problems(key, value, today or date.today())
    return list(dict.fromkeys(found))


def _value_problems(key: str, value: str, today: date) -> list[str]:
    if key == "national_id":
        if not value.isdigit():
            return ["תעודת זהות — ספרות בלבד"]
        if len(value.lstrip("0")) > 9:
            return ["תעודת זהות ארוכה מ-9 ספרות"]
        if not _israeli_id_valid(value.lstrip("0").zfill(9)):
            return [f"ספרת הביקורת של {value} אינה תקינה — כנראה ספרה לא נקראה נכון"]
        return []
    if key == "sample_received_date":
        if not _DATE.match(value):
            return [f"\"{value}\" — תאריך חייב להיות בפורמט DD/MM/YYYY"]
        parsed = _parse(value)
        if parsed is None or not 1900 <= parsed.year <= 2100:
            return [f"{value} — תאריך שאינו קיים בלוח השנה"]
        if parsed > today:
            return [f"{value} — תאריך בעתיד"]
        return []
    if key in ("first_name", "last_name"):
        if not _NAME.match(value) or not any(c.isalpha() for c in value):
            return [f"\"{value}\" — שם יכול להכיל אותיות בלבד"]
        return []
    if key in ("central_numbers", "commitment_numbers"):
        # אין פורמט רשמי — כל תו מותר, רק שיהיה מספר (החלטת נועה, 21/10/2026)
        if not any(c.isdigit() for c in value):
            return [f"\"{value}\" — {FIELDS_BY_KEY[key].label_he}: חייבת להיות בו לפחות ספרה אחת"]
        return []
    if key == "sending_entity" and not any(c.isalpha() for c in value):
        return [f"\"{value}\" — גורם שולח הוא שם, לא מספר"]
    return []


def record_problems(record: FormRecord) -> dict[str, list[str]]:
    """Every field of the form that breaks a rule, with why."""
    found = {key: problems(key, f.values) for key, f in record.fields.items()}
    return {key: why for key, why in found.items() if why}


def row_breaks_rules(row: dict) -> bool:
    """A row saved in the database (values joined by " | ") that breaks a rule —
    approved before the rules existed. Shown with a mark, never changed or deleted."""
    for key in FIELDS_BY_KEY:
        column = "commitment_number" if key == "commitment_numbers" else key
        text = row.get(column) or ""
        if problems(key, [v for v in text.split(MULTI_SEPARATOR.strip()) if v.strip()]):
            return True
    return False


def reopen_broken(record: FormRecord) -> bool:
    """A form that comes back approved (from the system) but breaks a rule: the broken
    fields turn orange and the form must be approved again. Values are not touched."""
    broken = record_problems(record)
    for key in broken:
        field = record.fields[key]
        field.state = STATE_REVIEW
        if "breaks_rule" not in field.reasons:
            field.reasons.append("breaks_rule")
    if broken:
        record.verified = False
    return bool(broken)
