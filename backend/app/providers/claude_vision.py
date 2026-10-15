"""Claude vision judge — the only module in the project that imports the Anthropic SDK.

Claude gets exactly the same prompt and answer schema as the other judges. The
long prompt goes in `system` with a cache marker, so after the first form it
is billed at ~10% of the input price. The page is shrunk to the model's own
limit (2576 px on the long side) with a high-quality filter, so what is sent
is predictable rather than resized somewhere we cannot see.

Its boxes are requested but not trusted for the zoom panel: only Gemini's
locations were verified against real pages so far.
"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import anthropic
from PIL import Image

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

MAX_LONG_SIDE = 2576  # הגבול של Claude Sonnet 5.5 לתמונה ברזולוציה גבוהה
MAX_IMAGE_BYTES = 4_500_000  # מתחת לתקרת 5MB לתמונה, עם מרווח
FALLBACK_BETA = "server-side-fallback-2026-07-01"

_COORDS = """
## מיקום כל שדה — `box`

לכל שדה שנמצא: `tile` = 1, ו-`box` = `[xmin, ymin, xmax, ymax]` של **הערך עצמו**
(לא של הכיתוב שלידו), בסקאלה 0–1000 של התמונה.
לשדה שלא נמצא: `tile` = null, `box` = null.
"""

_ASK = "קרא את טופס ההתחייבות שבתמונה והחזר את השדות בדיוק לפי ההוראות."


class ClaudeExtractionProvider:
    name = "claude"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        *,
        effort: str | None = None,
    ) -> None:
        key = api_key or SETTINGS.anthropic_api_key
        if not key:
            raise ExtractionError("אין ANTHROPIC_API_KEY בקובץ .env", fatal=True)
        self.model = model or SETTINGS.judge_b_model
        self.effort = effort or SETTINGS.claude_effort
        # ה-SDK מנסה שוב לבד על עומס (429) ושגיאות שרת (5xx).
        self._client = anthropic.Anthropic(api_key=key, max_retries=3)
        self.enhance_ink = SETTINGS.enhance_ink
        self._prompt = load_prompt(_COORDS, with_ink=self.enhance_ink)
        self.prompt_version = prompt_version(self._prompt)

    def extract(self, image_path: Path) -> RawExtraction:
        images = [_image_block(image_path)]
        if self.enhance_ink:
            # אותו עמוד, בלי מרקרים ועם דיו מודגש — מוקטן לאותו גודל בדיוק.
            images.append(_image_block(ink_enhanced(image_path)))
        try:
            response = self._client.beta.messages.parse(
                model=self.model,
                max_tokens=16000,
                # הפרומפט הקבוע ראשון ועם סימון מטמון — משולם במלואו פעם אחת בלבד.
                system=[{"type": "text", "text": self._prompt, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": [*images, {"type": "text", "text": _ASK}]}],
                output_format=PageReading,
                output_config={"effort": self.effort},
                # בסירוב — הבקשה עוברת אוטומטית למודל גיבוי במקום להיכשל.
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.APIConnectionError as exc:
            raise ExtractionError("אין חיבור ל-Anthropic. נסו שוב בעוד רגע.") from exc
        except anthropic.APIStatusError as exc:
            message, fatal = _classify(exc)
            raise ExtractionError(message, fatal=fatal) from exc

        return self._to_domain(response)

    def _to_domain(self, response: object) -> RawExtraction:
        stop = getattr(response, "stop_reason", None)
        if stop == "refusal":
            raise ExtractionError("Claude סירב לקרוא את הטופס הזה.")
        if stop == "max_tokens":
            raise ExtractionError("התשובה של Claude נקטעה באמצע.")

        parsed = getattr(response, "parsed_output", None)
        if not isinstance(parsed, PageReading):
            text = "".join(
                getattr(b, "text", "") for b in getattr(response, "content", []) or []
                if getattr(b, "type", "") == "text"
            ).strip()
            try:
                parsed = PageReading(**json.loads(text))
            except Exception as exc:  # noqa: BLE001
                raise ExtractionError("Claude החזיר תשובה במבנה לא תקין") from exc

        # במקרה של מעבר למודל גיבוי — העלות והשם לפי המודל שבאמת ענה.
        served_by = getattr(response, "model", None) or self.model
        raw = to_raw(parsed, served_by, _region_of, _usage(served_by, response))
        # המיקומים של Claude עוד לא נבדקו מול עמודים אמיתיים — ההגדלה לוקחת מ-Gemini.
        raw.regions_reliable = False
        return raw


def _image_block(image_path: Path) -> dict:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": page_png_b64(image_path)},
    }


def page_png_b64(image_path: Path) -> str:
    """The page as base64 PNG, at most 2576 px on the long side and under 5MB."""
    with Image.open(image_path) as page:
        page = page.convert("RGB")
        scale = min(1.0, MAX_LONG_SIDE / max(page.size))
        while True:
            size = (max(1, round(page.width * scale)), max(1, round(page.height * scale)))
            sized = page if size == page.size else page.resize(size, Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            sized.save(buffer, format="PNG", optimize=True)
            data = buffer.getvalue()
            if len(data) <= MAX_IMAGE_BYTES or scale < 0.3:
                return base64.standard_b64encode(data).decode("ascii")
            scale *= 0.85


def _region_of(field: FieldReading) -> Region | None:
    if field.box is None or len(field.box) != 4:
        return None
    return clamp_region(tuple(v / 1000 for v in field.box))


def _usage(model: str, response: object) -> Usage | None:
    raw = getattr(response, "usage", None)
    if raw is None:
        return None
    fresh = int(getattr(raw, "input_tokens", 0) or 0)
    cache_read = int(getattr(raw, "cache_read_input_tokens", 0) or 0)
    cache_write = int(getattr(raw, "cache_creation_input_tokens", 0) or 0)
    out = int(getattr(raw, "output_tokens", 0) or 0)
    return Usage(
        fresh + cache_read + cache_write,
        out,
        cost_usd(model, fresh, out, cache_read_tokens=cache_read, cache_write_tokens=cache_write),
    )


def _classify(exc: anthropic.APIStatusError) -> tuple[str, bool]:
    """Turn an API error into (message a non-developer can act on, is_fatal)."""
    status = getattr(exc, "status_code", None)
    message = str(getattr(exc, "message", "") or exc)
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return "המפתח ב-.env נדחה על ידי Anthropic. בדקו את ANTHROPIC_API_KEY.", True
    if "credit balance" in message.lower() or "billing" in message.lower():
        return "אין יתרת קרדיט בחשבון Anthropic. בדקו ב-console.anthropic.com.", True
    if isinstance(exc, anthropic.NotFoundError):
        return "מודל Claude המוגדר אינו זמין בחשבון. בדקו את JUDGE_B_MODEL ב-.env.", True
    if isinstance(exc, anthropic.RateLimitError) or (status is not None and status >= 500):
        # ה-SDK כבר ניסה שוב — עומס שנמשך הוא בעיה של הטופס הזה, לא של החשבון.
        return "Anthropic עמוסה כרגע. נסו שוב בעוד רגע.", False
    return f"קריאה ל-Claude נכשלה ({type(exc).__name__}). פרטים בלוג השרת.", False
