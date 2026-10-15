"""Runtime settings. Secrets live in .env and never leave the server."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    gemini_api_key: str
    anthropic_api_key: str
    # הקורא הראשי. JUDGE_B_MODEL ריק = קורא אחד בלבד (ברירת המחדל מאז 15/10/2026).
    # שופט שני (מחברה אחרת) אפשרי בשורה אחת ב-.env — הקוד שלו נשאר ועובד.
    judge_a_model: str
    judge_b_model: str
    gpt_image_split: int  # 4 = העמוד נשלח ל-GPT בארבעה רבעים; 1 = עמוד שלם
    gemini_thinking_budget: int
    page_render_dpi: int
    min_image_long_side: int
    max_pages_per_batch: int
    max_files_per_batch: int
    max_upload_bytes: int
    max_cost_per_batch_usd: float
    max_parallel_calls: int = 4
    # עומק החשיבה של Claude: low | medium | high | xhigh | max. ב-Opus 5.5 החשיבה
    # תמיד פועלת; זה רק קובע כמה עמוק. נועה בחרה high.
    claude_effort: str = "high"
    enhance_ink: bool = False  # לשלוח למודל גם גרסת "דיו מודגש" (תמונה שנייה, עולה כסף)
    straighten_pages: bool = True  # ליישר מקומית דף עקום או על הצד, לפני הקריאה

    # תאימות לאחור: הקוד והבדיקות הישנים מכירים את השמות האלה.
    @property
    def api_key(self) -> str:
        return self.openai_api_key

    @property
    def judges(self) -> list[str]:
        return [m for m in (self.judge_a_model, self.judge_b_model) if m]

    @property
    def model(self) -> str:
        return " + ".join(self.judges)

    @property
    def has_key(self) -> bool:
        return bool(self.openai_api_key or self.gemini_api_key or self.anthropic_api_key)


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name) or default)


def _flag(name: str, default: str = "1") -> bool:
    return (os.environ.get(name) or default).strip().lower() not in ("0", "false", "no")


def _default_split(judge_b: str) -> int:
    # gpt-4o* מקטין כל תמונה לצד קצר של 768px — לכן רבעים. דגמי gpt-5.4 ומעלה
    # קוראים ברזולוציה המקורית, ועמוד שלם עדיף להם (וזול פי 4).
    return 4 if judge_b.startswith("gpt-4o") else 1


def load_settings() -> Settings:
    judge_b = os.environ.get("JUDGE_B_MODEL", "").strip()
    return Settings(
        openai_api_key=os.environ.get("OPENAI_API_KEY", "").strip(),
        gemini_api_key=os.environ.get("GEMINI_API_KEY", "").strip(),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", "").strip(),
        judge_a_model=(os.environ.get("JUDGE_A_MODEL") or "claude-opus-5-5").strip(),
        judge_b_model=judge_b,
        gpt_image_split=4 if _int("GPT_IMAGE_SPLIT", _default_split(judge_b)) >= 4 else 1,
        # -1 = Gemini מחליט לבד כמה לחשוב, בלי תקרה (רלוונטי רק אם יופעל כשופט).
        gemini_thinking_budget=_int("GEMINI_THINKING_BUDGET", -1),
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
        # כמה טפסים מעובדים בו־זמנית. חשיבה עמוקה איטית — לכן יותר במקביל.
        max_parallel_calls=max(1, _int("MAX_PARALLEL_CALLS", 6)),
        claude_effort=(
            os.environ.get("JUDGE_EFFORT") or os.environ.get("JUDGE_B_EFFORT") or "high"
        ).strip().lower(),
        enhance_ink=_flag("ENHANCE_INK", "0"),
        straighten_pages=_flag("STRAIGHTEN_PAGES"),
    )


SETTINGS = load_settings()

WORK_DIR = PROJECT_ROOT / "work"
BATCHES_DIR = WORK_DIR / "batches"
GOLD_DIR = PROJECT_ROOT / "evaluation" / "gold"
RUNS_DIR = PROJECT_ROOT / "evaluation" / "runs"

# מחירים בדולר למיליון טוקנים (קלט, פלט). משמשים להצגת עלות בלבד, לא לחיוב.
# מקורות: עמודי המודלים של OpenAI; Gemini מאתרי השוואה — לאמת מול Google.
PRICES_PER_MILLION: dict[str, tuple[float, float]] = {
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-3.8-flash": (0.75, 3.75),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-5.4-mini": (0.75, 4.50),
    "gpt-5.4-nano": (0.20, 1.25),
    "gpt-6-astra": (10.00, 50.00),
    # Anthropic — מחירון ה-API הרשמי
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-opus-5-5": (4.00, 20.00),
}

# מטמון פרומפט: קריאה ~10% ממחיר הקלט, כתיבה ~125% (תוקף 5 דקות).
CACHE_READ_FACTOR = 0.10
CACHE_WRITE_FACTOR = 1.25
# מודלים שבהם מחיר הקריאה מהמטמון שונה מ-10% — דולר למיליון, מהמחירון הרשמי.
CACHE_READ_PRICE: dict[str, float] = {"claude-opus-5-5": 0.20}


def cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float | None:
    """Dollar cost of one call, or None when the model's price is unknown."""
    price = PRICES_PER_MILLION.get(model)
    if price is None:
        return None
    input_cost = (input_tokens + cache_write_tokens * CACHE_WRITE_FACTOR) * price[0]
    input_cost += cache_read_tokens * CACHE_READ_PRICE.get(model, price[0] * CACHE_READ_FACTOR)
    return (input_cost + output_tokens * price[1]) / 1_000_000
