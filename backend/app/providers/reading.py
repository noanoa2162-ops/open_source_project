"""The answer shape every judge must fill, shared by all adapters.

Both judges get the same prompt and the same schema, so their answers can be
compared field by field. Only the coordinate convention differs per vendor,
because each model is most accurate in the format it was trained on.
"""

from __future__ import annotations

import hashlib
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

PROMPT_NAME = "extract_v3"  # v2 נשאר בתיקייה לצורך השוואה
PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / f"{PROMPT_NAME}.md"
# שם + טביעת אצבע של התוכן: כל עריכה בפרומפט פוסלת תשובות שמורות ישנות.
PROMPT_VERSION = (
    PROMPT_NAME + "-" + hashlib.sha256(PROMPT_PATH.read_bytes()).hexdigest()[:8]
)


class FieldReading(BaseModel):
    values: list[str]
    legibility: str
    tile: int | None = None
    box: list[float] | None = None


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


def prompt_version(text: str) -> str:
    """Name + fingerprint of the full text actually sent. Part of the cache key:
    an edited prompt is a different version even without a server restart."""
    return PROMPT_NAME + "-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


INK_SECTION = """
## שתי תמונות של אותו עמוד

קיבלת **שתי תמונות של אותו עמוד בדיוק**, באותו גודל:
1. **המקורית** — עם הצבעים, החותמות והמרקרים.
2. **"דיו מודגש"** — אותו עמוד אחרי הסרת המרקרים (צהוב, טורקיז) והדגשת הדיו לשחור.

- **לקריאת כתב יד — במיוחד שם ות"ז — היעזר בתמונה השנייה.** בה הקווים לא נבלעים במרקר.
- **צבעים, חותמות ורמזי מרקר — מהתמונה הראשונה.** בשנייה הם הוסרו בכוונה.
- **המיקומים (`box`) — לפי התמונה הראשונה.** הגודל זהה, אז זה אותו מקום.
- **אם שתי התמונות נראות סותרות לגבי תו מסוים — `?` במקומו.**
- דיו אדום עלול להיעלם בתמונה השנייה — אם משהו כתוב באדום, קרא אותו מהראשונה.
"""


SENDERS_SECTION = """
## רשימת הגורמים השולחים המוכרים

ב-`sending_entity`: אם הגורם שבראש הדף הוא **בבירור** אחד מאלה — החזר את השם
**בדיוק כפי שהוא ברשימה**. אם אינו ברשימה, או שאינך בטוח שזה אותו מוסד — העתק
את השם בדיוק כפי שהוא מודפס. **אסור לבחור את "הכי דומה" מהרשימה.**

"""


def load_prompt(coordinates_section: str, *, with_ink: bool = False) -> str:
    text = PROMPT_PATH.read_text(encoding="utf-8")
    text += "\n---\n" + SENDERS_SECTION + senders_for_prompt() + "\n"
    text += "\n---\n\n" + coordinates_section.strip() + "\n"
    if with_ink:
        text += "\n---\n" + INK_SECTION
    return text


def to_raw(
    reading: PageReading,
    model: str,
    region_of: Callable[[FieldReading], Region | None],
    usage: Usage | None,
) -> RawExtraction:
    values: dict[str, list[str]] = {}
    legibility: dict[str, str] = {}
    regions: dict[str, Region | None] = {}

    for spec in FIELDS:
        field: FieldReading = getattr(reading, spec.key)
        values[spec.key] = [v.strip() for v in field.values if v and v.strip()]
        level = (field.legibility or "").strip().lower()
        # ערך לא מוכר מתפרש כספק, לא כוודאות.
        legibility[spec.key] = level if level in LEGIBILITY_VALUES else LEGIBILITY_PARTIAL
        regions[spec.key] = region_of(field)

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
        context=context,
        template=(reading.template or "").strip(),
        usage=usage,
        upside_down=bool(reading.upside_down),
    )
