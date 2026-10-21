"""A form that was already approved comes back with its saved values, not the reading.

Noa's decision (20/10/2026): when the secretary uploads a form that is already in the
system, every field arrives green with the values she approved before, and the form
arrives approved. The page and its close-ups come from the new file. Changing a value
makes "אישור הטופס" required again (the API takes care of that).

Two ways to know it is the same form:
- the very same page (its pixels' fingerprint) — found before the model is called, free;
- the same commitment number and a matching ID, from another file (PDF / photo) — found
  after the reading, which is still needed to learn the number.
"""

from __future__ import annotations

import traceback
from pathlib import Path

from app import db, rules
from app.models import STATE_APPROVED, FormRecord


def saved_for_page(image_path: Path) -> dict | None:
    """The approved form saved for exactly this page, or None. Never fails the reading."""
    try:
        return db.saved_form(db.form_key(image_path))
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return None


def from_saved(record: FormRecord, saved: dict, same_page: bool) -> None:
    """Put the saved values in place of the reading. Boxes for the close-ups: the saved
    ones when it is the same page, otherwise the new reading's (they fit the new image)."""
    old = db.record_from_json(saved["record"]) if same_page and saved.get("record") else None
    for key, field in record.fields.items():
        values = list(saved["values"].get(key, []))
        if old is not None and key in old.fields:
            field.region = old.fields[key].region
            field.value_regions = list(old.fields[key].value_regions)
            field.region_note = old.fields[key].region_note
        elif len(values) != len(field.values):
            field.value_regions = []
        field.values = values
        field.state = STATE_APPROVED
        field.reasons = []
        field.model_note = ""
        field.history = []
    if old is not None:
        record.context = dict(old.context)
        record.template = old.template
    record.model_note = ""
    record.verified = True
    record.from_store = saved.get("approved_at") or ""
    rules.reopen_broken(record)  # אושר לפני הכללים ואינו עומד בהם — השדות האלה כתומים, הטופס לאישור מחדש


def apply_known(records: list[FormRecord]) -> int:
    """After the reading (and after joining halves): forms already in the system get
    their saved values. Returns how many."""
    found = 0
    for record in records:
        # version > 0: המזכירה כבר תיקנה את הטופס בזמן שהשאר נקראו — לא דורסים את התיקון
        if record.error or record.from_store or record.verified or record.version > 0:
            continue
        try:
            saved = db.same_form(record.fields["commitment_numbers"].values, record.fields["national_id"].values)
        except Exception:  # noqa: BLE001 - בדיקה מול המאגר לא מפילה את הקריאה
            traceback.print_exc()
            continue
        if saved is not None:
            from_saved(record, saved, same_page=False)
            found += 1
    return found
