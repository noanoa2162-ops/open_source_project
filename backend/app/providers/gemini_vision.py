"""Gemini vision reader — the only module that imports the Google GenAI SDK.

Same instructions and the same answer shape as the Claude reader (reading.py); only the
box convention is Gemini's own: [ymin, xmin, ymax, xmax] on a 0–1000 scale, the format
Gemini was trained on. Model IDs and prices are configuration (.env, config.py), never
business logic. Chosen only after measurement on the sample pages (EVALUATION.md).
"""

from __future__ import annotations

import base64
import time
from pathlib import Path

from app.config import SETTINGS, cost_usd
from app.providers.base import ExtractionError, RawExtraction, Region, Usage, clamp_region
from app.providers.claude_vision import ENCODINGS, FILTER_BLOCKED, _image_block_sized
from app.providers.reading import PageReading, load_prompt, prompt_version, to_raw

_COORDS = """
## מיקום כל ערך — `boxes` ו-`box`

**כל תיבה בפורמט [ymin, xmin, ymax, xmax], בסקאלה 0–1000 של התמונה שקיבלת**
(0 = קצה עליון/שמאלי, 1000 = קצה תחתון/ימני).

לכל שדה שנמצא:
- `boxes` — רשימה, **תיבה לכל ערך** באותו סדר כמו `values`, סביב **הערך עצמו** (לא הכיתוב
  שלידו). שני מספרים מרכזיים = שתי תיבות.
- `box` — תיבה אחת שמקיפה את כל הערכים של השדה.
- `tile` = 1.

לשדה שלא נמצא: `tile` = null, `box` = null, `boxes` = null.
"""

_ASK = "קרא את טופס ההתחייבות שבתמונה והחזר את השדות בדיוק לפי ההוראות."
RETRIES = 2  # עומס (429) או שגיאת שרת — עוד ניסיון קצר


def _region_of(box: list[float] | None) -> Region | None:
    """[ymin, xmin, ymax, xmax] on 0–1000 → a region of the page (0–1)."""
    if box is None or len(box) != 4:
        return None
    try:
        y0, x0, y1, x1 = (float(v) / 1000 for v in box)
    except (TypeError, ValueError):
        return None
    return clamp_region((x0, y0, x1, y1))


class GeminiExtractionProvider:
    name = "gemini"

    def __init__(self, api_key: str | None = None, model: str | None = None, *, effort: str | None = None) -> None:
        key = api_key or SETTINGS.gemini_api_key
        if not key:
            raise ExtractionError("אין GEMINI_API_KEY בקובץ .env", fatal=True)
        from google import genai  # רק כאן — שאר הקוד לא תלוי בספק

        self.model = model or "gemini-3.8-flash"
        self.effort = (effort or "low").lower()
        self._client = genai.Client(api_key=key)
        self._prompt = load_prompt(_COORDS)
        self.prompt_version = prompt_version(self._prompt)

    def _config(self):
        from google.genai import types

        if self.model.startswith("gemini-2"):
            thinking = types.ThinkingConfig(thinking_budget=0 if self.effort in ("minimal", "low") else -1)
        else:
            level = {"minimal": "MINIMAL", "low": "LOW", "medium": "MEDIUM", "high": "HIGH"}.get(self.effort, "LOW")
            thinking = types.ThinkingConfig(thinking_level=level)
        return types.GenerateContentConfig(
            system_instruction=self._prompt,
            response_mime_type="application/json",
            response_schema=PageReading,
            thinking_config=thinking,
            media_resolution=types.MediaResolution.MEDIA_RESOLUTION_HIGH,  # מסמך: כל פרט קטן חשוב
        )

    def extract(self, image_path: Path) -> RawExtraction:
        from google.genai import errors, types

        blocked = None
        for encoding in ENCODINGS:
            block, _ = _image_block_sized(image_path, encoding)
            source = block["source"]
            part = types.Part.from_bytes(data=base64.b64decode(source["data"]), mime_type=source["media_type"])
            for attempt in range(RETRIES + 1):
                try:
                    response = self._client.models.generate_content(
                        model=self.model, contents=[part, _ASK], config=self._config())
                    return self._to_domain(response)
                except errors.APIError as exc:
                    code = getattr(exc, "code", None)
                    if code == FILTER_BLOCKED:
                        blocked = exc
                        break  # הסינון חסם את הקידוד הזה — מנסים את הבא
                    if code in (401, 403):
                        raise ExtractionError("המפתח של Gemini נדחה. בדקו את GEMINI_API_KEY.", fatal=True) from exc
                    if code == 404:
                        raise ExtractionError(f"המודל {self.model} אינו זמין בחשבון Gemini.", fatal=True) from exc
                    if code in (429, 500, 503) and attempt < RETRIES:
                        time.sleep(2 + 3 * attempt)
                        continue
                    raise ExtractionError(f"קריאה ל-Gemini נכשלה ({code}).") from exc
        raise ExtractionError("הבקשה ל-Gemini נחסמה על ידי סינון האינטרנט במחשב (NetFree).") from blocked

    def _to_domain(self, response) -> RawExtraction:
        parsed = getattr(response, "parsed", None)
        if not isinstance(parsed, PageReading):
            try:
                parsed = PageReading.model_validate_json(response.text or "")
            except Exception as exc:  # noqa: BLE001
                raise ExtractionError("Gemini החזיר תשובה במבנה לא תקין") from exc
        return to_raw(parsed, self.model, lambda box, tile: _region_of(box), _usage(self.model, response))


def _usage(model: str, response) -> Usage | None:
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return None
    prompt = int(meta.prompt_token_count or 0)
    cached = int(meta.cached_content_token_count or 0)
    out = int(meta.candidates_token_count or 0) + int(meta.thoughts_token_count or 0)
    return Usage(prompt, out, cost_usd(model, prompt - cached, out, cache_read_tokens=cached))
