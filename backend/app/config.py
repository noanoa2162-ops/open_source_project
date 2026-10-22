"""Runtime settings. Secrets live in .env and never leave the server."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str
    # הקורא: מודל אחד חזק שקורא כל טופס לבד (Claude Opus 5.5 מאז 15/10/2026).
    reader_model: str
    page_render_dpi: int
    min_image_long_side: int
    max_pages_per_batch: int
    max_files_per_batch: int
    max_upload_bytes: int
    max_cost_per_batch_usd: float
    max_parallel_calls: int = 6
    # עומק החשיבה של Claude: low | medium | high | xhigh | max. ב-Opus 5.5 החשיבה
    # תמיד פועלת; זה רק קובע כמה עמוק. נועה בחרה medium (22/10/2026, במקום high — מהירות).
    claude_effort: str = "medium"
    # חשיבה של הקורא: auto (כבויה איפה שאפשר: Haiku/Sonnet 5.5) | on | off. ב-Opus 5.5 תמיד פועלת.
    reader_thinking: str = "auto"
    # תוקף המטמון של ההוראות: 1h (ברירת מחדל — העלאות במעבדה מפוזרות לאורך היום) | 5m
    prompt_cache_ttl: str = "1h"
    # Gemini — רק אם READER_MODEL הוא gemini-* (נמדד ולא אומץ, SPEED_COST.md). המפתח נשאר בשרת.
    gemini_api_key: str = ""

    straighten_pages: bool = True  # ליישר מקומית דף עקום או על הצד, לפני הקריאה
    auto_rotate: bool = True  # לזהות דף מסובב (90°/180°/270°) עם Tesseract ולסובב לפני הקריאה
    # מיתוג המסך: moh = עיצוב משרד הבריאות (באישור נועה, 18/10/2026); neutral = הסמל שלנו
    brand: str = "moh"

    @property
    def model(self) -> str:
        return self.reader_model

    @property
    def has_key(self) -> bool:
        return bool(self.anthropic_api_key)


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name) or default)


def _flag(name: str, default: str = "1") -> bool:
    return (os.environ.get(name) or default).strip().lower() not in ("0", "false", "no")


def _first(*names: str, default: str) -> str:
    """The first of these .env names that is set — older names keep working."""
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return default


def load_settings() -> Settings:
    return Settings(
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", "").strip(),
        gemini_api_key=(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip(),
        reader_model=_first("READER_MODEL", "JUDGE_A_MODEL", default="claude-opus-5-5"),
        page_render_dpi=_int("PAGE_RENDER_DPI", 300),
        min_image_long_side=_int("MIN_IMAGE_LONG_SIDE", 1500),
        # תקרה לכל הגרירה יחד, לא לכל קובץ: כל עמוד הוא קריאה בתשלום למודל.
        max_pages_per_batch=_int(
            "MAX_PAGES_PER_BATCH", int(os.environ.get("MAX_PAGES_PER_UPLOAD") or 30)
        ),
        max_files_per_batch=_int("MAX_FILES_PER_BATCH", 25),
        max_upload_bytes=_int("MAX_UPLOAD_BYTES", 40 * 1024 * 1024),
        # רשת ביטחון נגד תקלה בלבד. 0 = בלי תקרה.
        max_cost_per_batch_usd=float(os.environ.get("MAX_COST_PER_BATCH_USD") or 10.0),
        # כמה טפסים מעובדים בו־זמנית. חשיבה עמוקה איטית — לכן כמה במקביל.
        max_parallel_calls=max(1, _int("MAX_PARALLEL_CALLS", 6)),
        claude_effort=_first(
            "READER_EFFORT", "JUDGE_EFFORT", "JUDGE_B_EFFORT", default="medium"
        ).lower(),
        reader_thinking=(os.environ.get("READER_THINKING") or "auto").strip().lower(),
        prompt_cache_ttl="5m" if (os.environ.get("PROMPT_CACHE_TTL") or "1h").strip().lower() == "5m" else "1h",
        straighten_pages=_flag("STRAIGHTEN_PAGES"),
        auto_rotate=_flag("AUTO_ROTATE"),
        brand="neutral" if (os.environ.get("BRAND") or "moh").strip().lower() == "neutral" else "moh",
    )


SETTINGS = load_settings()

DATA_DIR = (
    Path(tempfile.gettempdir()) / "tofes-cloud"
    if os.environ.get("CLOUD_MODE") == "1"
    else Path(os.environ["DATA_DIR"]).expanduser().resolve() if os.environ.get("DATA_DIR") else PROJECT_ROOT
)
WORK_DIR = DATA_DIR / "work"
BATCHES_DIR = WORK_DIR / "batches"
GOLD_DIR = DATA_DIR / "evaluation" / "gold"
RUNS_DIR = DATA_DIR / "evaluation" / "runs"

# מחירים בדולר למיליון טוקנים (קלט, פלט), ממחירון ה-API של Anthropic. להצגת עלות בלבד.
# כל מודלי Claude מופיעים כאן: בסירוב, מודל גיבוי עשוי לענות במקום Opus.
PRICES_PER_MILLION: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    # Gemini — מהמחירון הרשמי (ai.google.dev/gemini-api/docs/pricing, נקרא 22/10/2026). חלק מהמחירים
    # עולים ב-1/12/2026 או ב-1/1/2027 — לבדוק שוב לפני שימוש אמיתי. מטמון: 10% ממחיר הקלט.
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.6-flash": (0.75, 3.75),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3-flash-preview": (0.50, 3.00),
    "gemini-3.1-pro-preview": (2.00, 12.00),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
}

# מטמון פרומפט: קריאה ~10% ממחיר הקלט, כתיבה 125% (תוקף 5 דקות) או 200% (תוקף שעה).
CACHE_READ_FACTOR = 0.10
CACHE_WRITE_FACTOR = 1.25
CACHE_WRITE_1H_FACTOR = 2.0
# מודלים שבהם מחיר הקריאה מהמטמון שונה מ-10% — דולר למיליון, מהמחירון הרשמי.
CACHE_READ_PRICE: dict[str, float] = {"claude-opus-5-5": 0.20}


def cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    cache_write_1h_tokens: int = 0,
) -> float | None:
    """Dollar cost of one call, or None when the model's price is unknown."""
    price = PRICES_PER_MILLION.get(model)
    if price is None:
        return None
    writes = cache_write_tokens * CACHE_WRITE_FACTOR + cache_write_1h_tokens * CACHE_WRITE_1H_FACTOR
    input_cost = (input_tokens + writes) * price[0]
    input_cost += cache_read_tokens * CACHE_READ_PRICE.get(model, price[0] * CACHE_READ_FACTOR)
    return (input_cost + output_tokens * price[1]) / 1_000_000
