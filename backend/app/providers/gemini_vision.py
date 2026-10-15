"""Gemini vision judge — the only module in the project that imports the Google SDK.

Gemini reads the whole page at full resolution. Its "thinking" is off by
default: copying text off a form does not need it, and thinking tokens are
billed at the expensive output rate.

Gemini is the judge whose locations are trusted for the zoom panel: in the live
runs its boxes landed on the field, GPT's did not.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from google import genai
from google.genai import types

from app.config import SETTINGS, cost_usd
from app.pages import ink_enhanced
from app.providers.base import ExtractionError, RawExtraction, Region, Usage, clamp_region
from app.providers.reading import (
    FieldReading,
    PageReading,
    load_prompt,
    prompt_version,
    to_raw,
)

RATE_LIMIT_RETRIES = 3
RATE_LIMIT_BACKOFF_SECONDS = (2, 4, 8)

# Gemini אומן על תיבות בפורמט הזה — מבקשים אותו בדיוק, לא את הפורמט של OpenAI.
_COORDS = """
## מיקום כל שדה — `box`

לכל שדה שנמצא: `box` = `[ymin, xmin, ymax, xmax]` של **הערך עצמו** (לא של הכיתוב
שלידו), בסקאלה 0–1000 של התמונה. `tile` = null תמיד.
לשדה שלא נמצא: `box` = null.
"""


class GeminiExtractionProvider:
    name = "gemini"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        *,
        thinking_budget: int | None = None,
        structured: bool = True,
    ) -> None:
        key = api_key or SETTINGS.gemini_api_key
        if not key:
            raise ExtractionError("אין GEMINI_API_KEY בקובץ .env", fatal=True)
        self.model = model or SETTINGS.judge_a_model
        self.thinking_budget = (
            SETTINGS.gemini_thinking_budget if thinking_budget is None else thinking_budget
        )
        # מודלים פתוחים (Gemma) דרך אותו API לא תומכים בכל האפשרויות.
        self.structured = structured
        self._client = genai.Client(api_key=key)
        self.enhance_ink = SETTINGS.enhance_ink
        self._prompt = load_prompt(_COORDS, with_ink=self.enhance_ink)
        self.prompt_version = prompt_version(self._prompt)

    def extract(self, image_path: Path) -> RawExtraction:
        contents = [types.Part.from_bytes(data=image_path.read_bytes(), mime_type="image/png")]
        if self.enhance_ink:
            # אותו עמוד, בלי מרקרים ועם דיו מודגש — מקומי, בלי עלות לעיבוד.
            ink = ink_enhanced(image_path)
            contents.append(types.Part.from_bytes(data=ink.read_bytes(), mime_type="image/png"))
        contents.append(self._prompt)
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            try:
                response = self._client.models.generate_content(
                    model=self.model, contents=contents, config=self._config()
                )
                return self._to_domain(response)
            except Exception as exc:  # noqa: BLE001 - SDK raises many shapes
                if isinstance(exc, ExtractionError):
                    raise
                if _is_transient_rate_limit(exc) and attempt < RATE_LIMIT_RETRIES:
                    time.sleep(RATE_LIMIT_BACKOFF_SECONDS[attempt])
                    continue
                message, fatal = _classify(exc)
                raise ExtractionError(message, fatal=fatal) from exc

        raise ExtractionError("קריאה למודל נכשלה")  # לא אמור להגיע לכאן

    def _config(self) -> types.GenerateContentConfig:
        kwargs: dict = {"temperature": 0, "response_mime_type": "application/json"}
        if self.structured:
            kwargs["response_schema"] = PageReading
            if self.thinking_budget is not None and self.thinking_budget >= 0:
                kwargs["thinking_config"] = types.ThinkingConfig(
                    thinking_budget=self.thinking_budget
                )
        return types.GenerateContentConfig(**kwargs)

    def _to_domain(self, response: object) -> RawExtraction:
        parsed = getattr(response, "parsed", None) if self.structured else None
        if not isinstance(parsed, PageReading):
            text = (getattr(response, "text", "") or "").strip()
            if text.startswith("```"):
                text = text.strip("`").removeprefix("json").strip()
            if not text:
                raise ExtractionError("Gemini החזיר תשובה ריקה")
            try:
                parsed = PageReading(**json.loads(text))
            except Exception as exc:  # noqa: BLE001
                raise ExtractionError("Gemini החזיר JSON לא תקין") from exc
        return to_raw(parsed, self.model, _region_of, _usage(self.model, response))


def _region_of(field: FieldReading) -> Region | None:
    if field.box is None or len(field.box) != 4:
        return None
    y0, x0, y1, x1 = (v / 1000 for v in field.box)
    return clamp_region((x0, y0, x1, y1))


def _usage(model: str, response: object) -> Usage | None:
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return None
    tokens_in = int(getattr(meta, "prompt_token_count", 0) or 0)
    # טוקני חשיבה מחויבים כפלט.
    tokens_out = int(getattr(meta, "candidates_token_count", 0) or 0) + int(
        getattr(meta, "thoughts_token_count", 0) or 0
    )
    return Usage(tokens_in, tokens_out, cost_usd(model, tokens_in, tokens_out))


# ------------------------------------------------------------------ errors


def _status(exc: Exception) -> tuple[int | None, str]:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    message = str(getattr(exc, "message", "") or exc)
    return (code if isinstance(code, int) else None), message


_RETRY_IN = re.compile(r"retry in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", re.I)


def _out_of_credit(code: int | None, message: str) -> bool:
    return code == 402 or "prepayment credits are depleted" in message


def _long_quota(code: int | None, message: str) -> bool:
    """A 429 that will not clear in this batch: a daily or free-tier quota.

    Google sends these as RESOURCE_EXHAUSTED too, with "retry in 4h11m". Treating
    them as a passing overload would just burn retries.
    """
    if code != 429:
        return False
    if "free_tier" in message or "PerDay" in message or "per day" in message.lower():
        return True
    match = _RETRY_IN.search(message)
    if match and any(match.groups()):
        hours, minutes, seconds = (float(g or 0) for g in match.groups())
        return hours * 3600 + minutes * 60 + seconds > 120
    return False


def _is_transient_rate_limit(exc: Exception) -> bool:
    """Overload that clears by itself: rate limit, or the model 'experiencing high demand'."""
    code, message = _status(exc)
    if code in (500, 502, 503, 504):
        return True
    return code == 429 and not _out_of_credit(code, message) and not _long_quota(code, message)


def _classify(exc: Exception) -> tuple[str, bool]:
    code, message = _status(exc)
    if _out_of_credit(code, message):
        return "אין יתרת קרדיט בחשבון Gemini. היכנסו ל-ai.studio וטענו קרדיט.", True
    if _long_quota(code, message):
        tier = " (חשבון חינמי — 20 בקשות ביום)" if "free_tier" in message else ""
        return (
            f"נגמרה המכסה של Gemini{tier}. ודאו שהמפתח ב-.env שייך לפרויקט שבו הופעל "
            "החיוב ב-ai.studio."
        ), True
    if code in (429, 500, 502, 503, 504):
        return "Gemini עמוס כרגע. נסו שוב בעוד רגע.", False
    if (
        code in (401, 403)
        or "API_KEY_INVALID" in message
        or "API key not valid" in message
        or "API key expired" in message
    ):
        return "המפתח ב-.env נדחה על ידי Google. בדקו את GEMINI_API_KEY.", True
    if code == 404 or "NOT_FOUND" in message:
        return "מודל Gemini המוגדר אינו זמין בחשבון. בדקו את JUDGE_A_MODEL ב-.env.", True
    return f"קריאה ל-Gemini נכשלה ({type(exc).__name__}). פרטים בלוג השרת.", False
