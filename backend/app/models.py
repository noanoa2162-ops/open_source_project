"""Domain objects shared by the pipeline, the API and the Excel exporter."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.fields import FIELDS

# מצב שדה. כתום = נדרשת בדיקה, כולל ערך חסר. ירוק רק אחרי אישור אנושי.
STATE_OK = "ok"
STATE_REVIEW = "needs_review"
STATE_APPROVED = "approved"

# אזור על העמוד: (x0, y0, x1, y1), כל ערך בין 0 ל-1.
Region = tuple[float, float, float, float]

UNDO_DEPTH = 10  # כמה פעולות אחורה "בטל" יכול להחזיר, לכל שדה

# מצב כל דף בזמן העיבוד — לתמונות הממוזערות במסך ההתקדמות.
PAGE_QUEUED = "queued"
PAGE_READING = "reading"
PAGE_DONE = "done"
PAGE_FAILED = "failed"
PAGE_SKIPPED = "skipped"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class ExtractedField:
    key: str
    values: list[str] = field(default_factory=list)
    state: str = STATE_OK
    reasons: list[str] = field(default_factory=list)
    model_note: str = ""
    region: Region | None = None  # איפה הערך בעמוד — לתקריב ולריבוע על הטופס המלא
    # מה ידוע על המיקום: verified (נמצא בעמוד) | fixed (תוקן מקומית) | reader (לפי המנוע)
    region_note: str = ""
    # מיקום לכל ערך בנפרד, באותו סדר כמו values — תקריב לכל מספר כשיש כמה
    value_regions: list[Region | None] = field(default_factory=list)
    # המצבים הקודמים של השדה, לכפתור "בטל" במסך: {values, state, reasons}
    history: list[dict] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not any(v.strip() for v in self.values)

    def flag(self, reason: str) -> None:
        if reason not in self.reasons:
            self.reasons.append(reason)
        if self.state != STATE_APPROVED:
            self.state = STATE_REVIEW

    def settle(self) -> None:
        """A person decided this field: automatic flags no longer apply."""
        self.reasons = []

    def remember(self) -> None:
        """Keep the current state, so a person's change can be undone."""
        self.history.append(
            {"values": list(self.values), "state": self.state, "reasons": list(self.reasons)}
        )
        del self.history[:-UNDO_DEPTH]

    def undo(self) -> bool:
        if not self.history:
            return False
        previous = self.history.pop()
        self.values = previous["values"]
        self.state = previous["state"]
        self.reasons = previous["reasons"]
        return True


@dataclass
class SourceFile:
    """One file the user dropped. A batch holds as many as they dropped."""

    index: int  # 1-based, בסדר שבו נגררו
    filename: str
    kind: str = ""  # pdf | jpeg | png
    page_count: int = 0
    error: str = ""  # קובץ פסול — נרשם כאן ואינו מפיל את השאר
    sha256: str = ""  # טביעת אצבע: אותו קובץ פעמיים לא נשלח שוב למודלים
    joined: bool = False  # לא הועלה — תמונה שהמערכת הדביקה משני חצאים של טופס

    @property
    def accepted(self) -> bool:
        return not self.error


@dataclass
class FormRecord:
    """One logical commitment form — one row in the table and in Excel."""

    record_id: str
    batch_id: str
    source_index: int
    source_name: str
    page_number: int  # מספר העמוד בתוך אותו קובץ מקור
    fields: dict[str, ExtractedField] = field(default_factory=dict)
    error: str = ""
    model_note: str = ""  # ספק שהמודל הביע. לא אחוז ביטחון מכויל.
    template: str = ""
    context: dict[str, str] = field(default_factory=dict)  # תאריך הפקה/לידה לבדיקות
    readers: list[str] = field(default_factory=list)  # איזה מודל קרא בפועל
    low_resolution: bool = False
    cost_usd: float | None = None
    seconds: float = 0.0
    verified: bool = False  # המזכירה בדקה ושמרה כמאומת
    upside_down: bool = False  # המודל אמר שהדף הגיע הפוך; סובב לתצוגה בלבד
    straightened: str = ""  # מה יושר מקומית לפני הקריאה, למשל "סובב ב-90°"
    version: int = 0  # עולה בכל שינוי של אדם — עריכה ממסך ישן נדחית ולא דורסת
    # הטופס כבר אושר בעבר: הערכים הגיעו מהמערכת ולא מהקריאה. מועד האישור השמור (UTC), או ריק
    from_store: str = ""

    @property
    def review_count(self) -> int:
        return sum(1 for f in self.fields.values() if f.state == STATE_REVIEW)

    @classmethod
    def empty(cls, batch_id: str, source: SourceFile, page_number: int) -> "FormRecord":
        return cls(
            record_id=new_id("rec"),
            batch_id=batch_id,
            source_index=source.index,
            source_name=source.filename,
            page_number=page_number,
            fields={f.key: ExtractedField(key=f.key) for f in FIELDS},
        )


@dataclass
class Batch:
    batch_id: str
    sources: list[SourceFile] = field(default_factory=list)
    status: str = "queued"  # queued | processing | completed | partial | failed
    total_pages: int = 0  # סכום העמודים מכל הקבצים שהתקבלו
    processed_pages: int = 0  # נספר רוחבית על כל הקבצים
    records: list[FormRecord] = field(default_factory=list)
    error: str = ""
    created_at: str = field(default_factory=now)
    skipped_pages: int = 0  # עמודים שהושמטו בגלל תקרת העמודים
    found_pages: int = 0  # כמה טפסים זוהו עד עכשיו — עולה תוך כדי הכנת העמודים
    expected_pages: int = 0  # כמה עמודים צפויים בסך הכול (ספירה מיידית לפני הזיהוי) — לאחוזים
    budget_skipped_pages: int = 0  # עמודים שלא עובדו בגלל תקרת התקציב
    warnings: list[str] = field(default_factory=list)  # הודעות גלויות ברמת ההעלאה
    seconds: float = 0.0
    # לכל דף, לפי הסדר: {source_index, page_number, status, record_id}
    pages: list[dict] = field(default_factory=list)
    # טופס שחובר אוטומטית משני חצאים → שתי הרשומות המקוריות, לכפתור "הפרד"
    joined: dict[str, list[FormRecord]] = field(default_factory=dict)
    # אחרי "הפרד": לכל אחד משני החלקים → {top, bottom, source_index}, לכפתור "חבר בחזרה"
    split_pairs: dict[str, dict] = field(default_factory=dict)
    # עמוד שפוצל לשני טפסים: כל חלק → הרשומה המקורית של העמוד, לכפתור "בטל פיצול"
    page_splits: dict[str, "FormRecord"] = field(default_factory=dict)

    @property
    def review_total(self) -> int:
        return sum(r.review_count for r in self.records)

    @property
    def cost_usd(self) -> float | None:
        costs = [r.cost_usd for r in self.records if r.cost_usd is not None]
        return sum(costs) if costs else None

    @property
    def accepted_sources(self) -> list[SourceFile]:
        """The files that were uploaded and accepted (not an image joined from two halves)."""
        return [s for s in self.sources if s.accepted and not s.joined]

    @property
    def rejected_sources(self) -> list[SourceFile]:
        return [s for s in self.sources if not s.accepted]

    @property
    def display_name(self) -> str:
        """What to call this batch in one line, e.g. in the Excel notice."""
        accepted = self.accepted_sources
        if not accepted:
            return "ללא קבצים"
        if len(accepted) == 1:
            return accepted[0].filename
        return f"{accepted[0].filename} ועוד {len(accepted) - 1} קבצים"

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)
