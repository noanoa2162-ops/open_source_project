"""The answer shape the reader must fill, and the prompt it is given.

Kept separate from the vendor adapter, so a different reader (for example an
internal model) gets exactly the same instructions and schema. Only the
coordinate convention belongs to the adapter, because each model is most
accurate in the box format it was trained on.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from pydantic import BaseModel

from app.entities import senders_for_prompt
from app.fields import FIELDS
from app.providers.base import (
    LEGIBILITY_PARTIAL,
    LEGIBILITY_VALUES,
    RawExtraction,
    Region,
    Usage,
)

PROMPT_NAME = "extract_v4"  # v3 נשאר בתיקייה לצורך השוואה; v4 = v3 + גבולות הטופס בעמוד
PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / f"{PROMPT_NAME}.md"
# שם + טביעת אצבע של התוכן: כל עריכה בפרומפט פוסלת תשובות שמורות ישנות.
PROMPT_VERSION = (
    PROMPT_NAME + "-" + hashlib.sha256(PROMPT_PATH.read_bytes()).hexdigest()[:8]
)


class FieldReading(BaseModel):
    values: list[str]
    legibility: str
    tile: int | None = None
    box: list[float] | None = None  # תיבה אחת סביב כל הערכים (תשובות ישנות: רק זו)
    # תיבה לכל ערך, באותו סדר כמו values: [tile, xmin, ymin, xmax, ymax] (או 4 מספרים בלי tile)
    boxes: list[list[float] | None] | None = None


class PageReading(BaseModel):
    template: str
    first_name: FieldReading
    last_name: FieldReading
    national_id: FieldReading
    central_numbers: FieldReading
    sample_received_date: FieldReading
    sending_entity: FieldReading
    commitment_numbers: FieldReading
    issue_date: str | None = None
    birth_date: str | None = None
    upside_down: bool = False
    note: str = ""
    # איך נמצא מספר ההתחייבות: label (ליד כיתוב) | form_sticker (מדבקת הטופס, בלי כיתוב) | none
    commitment_found_by: str | None = None
    # גבולות הטופס: כמה טפסים בעמוד, תיבה לכל אחד, והאם העמוד ממשיך טופס מהעמוד הקודם
    forms_on_page: int = 1
    form_boxes: list[list[float]] | None = None
    continues_previous_page: bool = False


# ------------------------------------------------------------ שלב שני: שמות מתוך תקריב
NAMES_NAME = "names_v1"
NAMES_PATH = PROMPT_PATH.parent / f"{NAMES_NAME}.md"
NAMES_PROMPT = NAMES_PATH.read_text(encoding="utf-8")
NAMES_VERSION = NAMES_NAME + "-" + hashlib.sha256(NAMES_PROMPT.encode("utf-8")).hexdigest()[:8]


class NameReading(BaseModel):
    found: bool
    first_name: list[str]
    first_legibility: str
    last_name: list[str]
    last_legibility: str


@dataclass
class NameCheck:
    """The second, close-up reading of the patient's name."""

    found: bool = False
    first_name: list[str] = field(default_factory=list)
    last_name: list[str] = field(default_factory=list)
    legibility: dict[str, str] = field(default_factory=dict)
    model: str = ""
    usage: Usage | None = None


def to_name_check(reading: NameReading, model: str, usage: Usage | None) -> NameCheck:
    def level(raw: str) -> str:
        value = (raw or "").strip().lower()
        return value if value in LEGIBILITY_VALUES else LEGIBILITY_PARTIAL

    clean = lambda values: [v.strip() for v in values if v and v.strip()]  # noqa: E731
    return NameCheck(
        found=bool(reading.found),
        first_name=clean(reading.first_name),
        last_name=clean(reading.last_name),
        legibility={"first_name": level(reading.first_legibility), "last_name": level(reading.last_legibility)},
        model=model,
        usage=usage,
    )


def _one(region_of, entry) -> Region | None:
    """A box for one value: [tile, xmin, ymin, xmax, ymax], or just the four numbers."""
    if not entry:
        return None
    if len(entry) == 5:
        try:
            tile = int(entry[0])
        except (TypeError, ValueError):
            return None
        return region_of(list(entry[1:]), tile)
    return region_of(list(entry), None)


def union(regions: list[Region | None]) -> Region | None:
    """The smallest region holding all of them, or None when there are none."""
    found = [r for r in regions if r]
    if not found:
        return None
    return (min(r[0] for r in found), min(r[1] for r in found),
            max(r[2] for r in found), max(r[3] for r in found))


def prompt_version(text: str) -> str:
    """Name + fingerprint of the full text actually sent. Part of the cache key:
    an edited prompt is a different version even without a server restart."""
    return PROMPT_NAME + "-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


SENDERS_SECTION = """
## רשימת הגורמים השולחים המוכרים

ב-`sending_entity`: אם הגורם שבראש הדף הוא **בבירור** אחד מאלה — החזר את השם
**בדיוק כפי שהוא ברשימה**. אם אינו ברשימה, או שאינך בטוח שזה אותו מוסד — העתק
את השם בדיוק כפי שהוא מודפס. **אסור לבחור את "הכי דומה" מהרשימה.**

"""


def load_prompt(coordinates_section: str) -> str:
    text = PROMPT_PATH.read_text(encoding="utf-8")
    text += "\n---\n" + SENDERS_SECTION + senders_for_prompt() + "\n"
    text += "\n---\n\n" + coordinates_section.strip() + "\n"
    return text


def to_raw(
    reading: PageReading,
    model: str,
    region_of: Callable[[list[float] | None, int | None], Region | None],
    usage: Usage | None,
) -> RawExtraction:
    """`region_of(box, tile)` turns one box, in the units of the image it was
    measured in, into a region of the page (0–1), or None when it is unusable."""
    values: dict[str, list[str]] = {}
    legibility: dict[str, str] = {}
    regions: dict[str, Region | None] = {}
    value_regions: dict[str, list[Region | None]] = {}

    for spec in FIELDS:
        field: FieldReading = getattr(reading, spec.key)
        boxes = list(field.boxes or [])
        kept, kept_regions = [], []
        for i, raw in enumerate(field.values):
            if raw and raw.strip():
                kept.append(raw.strip())
                kept_regions.append(_one(region_of, boxes[i]) if i < len(boxes) else None)
        values[spec.key] = kept
        value_regions[spec.key] = kept_regions if any(kept_regions) else []
        level = (field.legibility or "").strip().lower()
        # ערך לא מוכר מתפרש כספק, לא כוודאות.
        legibility[spec.key] = level if level in LEGIBILITY_VALUES else LEGIBILITY_PARTIAL
        regions[spec.key] = union(kept_regions) or region_of(field.box, field.tile)

    context = {
        key: value.strip()
        for key, value in (("issue_date", reading.issue_date), ("birth_date", reading.birth_date))
        if value and value.strip()
    }
    return RawExtraction(
        values=values,
        note=(reading.note or "").strip(),
        model=model,
        legibility=legibility,
        regions=regions,
        value_regions=value_regions,
        context={**context, **({"commitment_found_by": reading.commitment_found_by.strip().lower()}
                               if reading.commitment_found_by else {})},
        template=(reading.template or "").strip(),
        usage=usage,
        upside_down=bool(reading.upside_down),
        forms_on_page=max(1, int(reading.forms_on_page or 1)),
        form_regions=[r for r in (_one(region_of, b) for b in (reading.form_boxes or [])) if r],
        continues_previous=bool(reading.continues_previous_page),
    )
