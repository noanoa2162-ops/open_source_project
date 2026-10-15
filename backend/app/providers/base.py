"""The contract every extraction backend must satisfy.

Business code depends on this, never on a vendor SDK, so swapping a judge for
an internal model later is a change in one adapter and one config line.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

# כמה בטוח הקורא שקרא נכון. זו הצהרה של המודל, לא הסתברות מכוילת —
# משמשת רק כסיבה נוספת לסמן כתום, לעולם לא כסיבה לסמן "נכון".
LEGIBILITY_CLEAR = "clear"
LEGIBILITY_PARTIAL = "partial"
LEGIBILITY_ILLEGIBLE = "illegible"
LEGIBILITY_ABSENT = "absent"
LEGIBILITY_VALUES = (
    LEGIBILITY_CLEAR,
    LEGIBILITY_PARTIAL,
    LEGIBILITY_ILLEGIBLE,
    LEGIBILITY_ABSENT,
)

# אזור על העמוד: (x0, y0, x1, y1), כל ערך בין 0 ל-1 מרוחב/גובה העמוד המלא.
Region = tuple[float, float, float, float]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None


@dataclass
class RawExtraction:
    """What one judge returns for one page, before comparison and validation."""

    values: dict[str, list[str]] = field(default_factory=dict)
    note: str = ""
    model: str = ""
    legibility: dict[str, str] = field(default_factory=dict)
    regions: dict[str, Region | None] = field(default_factory=dict)
    # שדות הקשר שאינם מיוצאים, אבל מאפשרים לבדוק את תאריך הקבלה מול המסמך.
    context: dict[str, str] = field(default_factory=dict)
    template: str = ""
    usage: Usage | None = None
    # האם אפשר לסמוך על המיקומים להגדלה. GPT מחזיר תיבות, אבל בבדיקות החיות הן
    # נחתו במקום הלא נכון — ולכן ההגדלה לוקחת מיקום רק משופט שזה True אצלו.
    regions_reliable: bool = True


class ExtractionProvider(Protocol):
    name: str
    model: str

    def extract(self, image_path: Path) -> RawExtraction:
        """Read one page image and return the seven fields."""
        ...


class ExtractionError(RuntimeError):
    """Raised when a page could not be extracted.

    `fatal` marks an account-level problem — no credit, bad key, missing model —
    where retrying the remaining pages would only burn quota and time. A
    non-fatal error is specific to one page and the batch continues.
    """

    def __init__(self, message: str, *, fatal: bool = False) -> None:
        super().__init__(message)
        self.fatal = fatal


def clamp_region(values: object) -> Region | None:
    """Turn whatever a model sent into a valid region, or None.

    Model coordinates are not facts: anything out of range, inverted or
    degenerate is dropped rather than repaired, so the UI never shows a
    confident crop of the wrong place.
    """
    if not isinstance(values, (list, tuple)) or len(values) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in values)
    except (TypeError, ValueError):
        return None
    if not all(-0.02 <= v <= 1.02 for v in (x0, y0, x1, y1)):
        return None
    x0, y0, x1, y1 = (min(max(v, 0.0), 1.0) for v in (x0, y0, x1, y1))
    if x1 - x0 < 0.005 or y1 - y0 < 0.003:
        return None
    return (x0, y0, x1, y1)
