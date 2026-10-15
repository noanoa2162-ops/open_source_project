"""OpenAI vision judge — the only module in the project that imports the OpenAI SDK.

`gpt-4o-mini` shrinks every image so its short side is 768 px. A whole page
would arrive at roughly the size of the screenshot that failed, so by default
the page is sent as four overlapping quarters in one call: each quarter keeps
nearly all its detail. The quarters overlap so a field on a cut line is whole
in at least one of them.
"""

from __future__ import annotations

import base64
import io
import json
import time
from pathlib import Path

from openai import OpenAI
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

# 429 רגעי (עומס) שווה ניסיון חוזר. 429 של "נגמר הכסף" לא.
RATE_LIMIT_RETRIES = 2
RATE_LIMIT_BACKOFF_SECONDS = (2, 4)

# כל רבע מכסה 55% מהרוחב והגובה, כך שיש 10% חפיפה במרכז.
TILE_SPAN = 0.55
# רבעים בסדר שבו הם נשלחים: (x0, y0) כחלק מהעמוד. "שמאל/ימין" — כפי שרואים בתמונה.
TILE_ORIGINS: tuple[tuple[float, float], ...] = (
    (0.0, 0.0),
    (1 - TILE_SPAN, 0.0),
    (0.0, 1 - TILE_SPAN),
    (1 - TILE_SPAN, 1 - TILE_SPAN),
)
SHORT_SIDE = 768  # מה ש-gpt-4o-mini משאיר ממילא; מקטינים בעצמנו באיכות גבוהה
LONG_SIDE_CAP = 2048

_COORDS_TILED = """
## מיקום כל שדה — `tile` ו-`box`

קיבלת **4 תמונות שהן רבעים חופפים של אותו עמוד אחד**, בסדר הזה:
1. רבע **שמאלי-עליון**  2. רבע **ימני-עליון**  3. רבע **שמאלי-תחתון**  4. רבע **ימני-תחתון**
("שמאל" ו"ימין" — כפי שרואים בתמונה, לא לפי כיוון הקריאה.)

הרבעים חופפים, ולכן אותו שדה עשוי להופיע ביותר מרבע אחד. **דווח על כל שדה פעם
אחת בלבד**, מהרבע שבו הוא נראה הכי שלם וברור.

לכל שדה שנמצא: `tile` = מספר הרבע (1–4), ו-`box` = `[xmin, ymin, xmax, ymax]`
של **הערך עצמו** (לא של הכיתוב שלידו), בסקאלה 0–1000 **של אותו רבע**.
לשדה שלא נמצא: `tile` = null, `box` = null.
"""

_COORDS_SINGLE = """
## מיקום כל שדה — `box`

לכל שדה שנמצא: `tile` = 1, ו-`box` = `[xmin, ymin, xmax, ymax]` של **הערך עצמו**
(לא של הכיתוב שלידו), בסקאלה 0–1000 של התמונה.
לשדה שלא נמצא: `tile` = null, `box` = null.
"""


class OpenAIExtractionProvider:
    name = "openai"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        *,
        split: int | None = None,
        detail: str | None = "auto-pick",
    ) -> None:
        key = api_key or SETTINGS.openai_api_key
        if not key:
            raise ExtractionError("אין OPENAI_API_KEY בקובץ .env", fatal=True)
        self.model = model or SETTINGS.judge_b_model
        chosen = split or SETTINGS.gpt_image_split
        self.split = 4 if chosen >= 4 else 1
        # רבעים נשלחים ב-high (gpt-4o*). עמוד שלם — בלי פרמטר, כך שדגמים חדשים
        # משתמשים בברירת המחדל שלהם, שהיא הרזולוציה המקורית.
        self.detail = ("high" if self.split == 4 else None) if detail == "auto-pick" else detail
        self._client = OpenAI(api_key=key)
        # ברבעים — בלי גרסה מודגשת (כבר 4 תמונות; פי 2 עלות בלי בדיקה שמצדיקה).
        self.enhance_ink = SETTINGS.enhance_ink and self.split == 1
        self._prompt = load_prompt(
            _COORDS_TILED if self.split == 4 else _COORDS_SINGLE, with_ink=self.enhance_ink
        )
        self.prompt_version = prompt_version(self._prompt)

    def extract(self, image_path: Path) -> RawExtraction:
        images = page_images(image_path, self.split)
        if self.enhance_ink:
            images += page_images(ink_enhanced(image_path), 1)
        content: list[dict] = [{"type": "text", "text": self._prompt}]  # טקסט קבוע ראשון → מטמון
        for data_url in images:
            image_part: dict = {"url": data_url}
            if self.detail:
                image_part["detail"] = self.detail
            content.append({"type": "image_url", "image_url": image_part})
        messages = [{"role": "user", "content": content}]

        for attempt in range(RATE_LIMIT_RETRIES + 1):
            try:
                completion = self._client.chat.completions.parse(
                    model=self.model, messages=messages, response_format=PageReading
                )
                return self._to_domain(completion)
            except Exception as exc:  # noqa: BLE001 - SDK raises many shapes
                if _is_transient_rate_limit(exc) and attempt < RATE_LIMIT_RETRIES:
                    time.sleep(RATE_LIMIT_BACKOFF_SECONDS[attempt])
                    continue
                if isinstance(exc, ExtractionError):
                    raise
                message, fatal = _classify(exc)
                raise ExtractionError(message, fatal=fatal) from exc

        raise ExtractionError("קריאה למודל נכשלה")  # לא אמור להגיע לכאן

    def _to_domain(self, completion: object) -> RawExtraction:
        choices = getattr(completion, "choices", None) or []
        if not choices:
            raise ExtractionError("המודל החזיר תשובה ריקה")
        message = choices[0].message
        if getattr(message, "refusal", None):
            raise ExtractionError("המודל סירב לקרוא את הטופס")

        parsed = getattr(message, "parsed", None)
        if parsed is None:
            text = (getattr(message, "content", "") or "").strip()
            if not text:
                raise ExtractionError("המודל החזיר תשובה ריקה")
            try:
                parsed = PageReading(**json.loads(text))
            except Exception as exc:  # noqa: BLE001
                raise ExtractionError("המודל החזיר JSON לא תקין") from exc

        raw = to_raw(parsed, self.model, self._region_of, _usage(self.model, completion))
        # OpenAI עצמה כותבת שהמודל "struggles with precise spatial localization",
        # ובבדיקה החיה התיבות שלו נחתו על שורות ריקות ועל כיתובים אחרים.
        raw.regions_reliable = False
        return raw

    def _region_of(self, field: FieldReading) -> Region | None:
        """Map a box given inside one quarter back onto the whole page."""
        if field.box is None or len(field.box) != 4:
            return None
        x0, y0, x1, y1 = (v / 1000 for v in field.box)
        if self.split == 1:
            return clamp_region((x0, y0, x1, y1))
        tile = field.tile
        if tile is None or not 1 <= tile <= 4:
            return None
        ox, oy = TILE_ORIGINS[tile - 1]
        return clamp_region(
            (ox + x0 * TILE_SPAN, oy + y0 * TILE_SPAN, ox + x1 * TILE_SPAN, oy + y1 * TILE_SPAN)
        )


def page_images(image_path: Path, split: int) -> list[str]:
    """The page as data URLs: one whole image, or four overlapping quarters."""
    with Image.open(image_path) as page:
        page = page.convert("RGB")
        if split == 1:
            # עמוד שלם לדגמים שקוראים ברזולוציה מקורית — בלי להקטין.
            return [_data_url(page)]
        width, height = page.size
        quarters = []
        for ox, oy in TILE_ORIGINS:
            box = (
                round(ox * width),
                round(oy * height),
                round((ox + TILE_SPAN) * width),
                round((oy + TILE_SPAN) * height),
            )
            quarters.append(_data_url(_fit(page.crop(box))))
        return quarters


def _fit(image: Image.Image) -> Image.Image:
    """Shrink quarters to what gpt-4o-mini keeps anyway, with a high-quality filter."""
    width, height = image.size
    scale = min(1.0, SHORT_SIDE / min(width, height), LONG_SIDE_CAP / max(width, height))
    if scale >= 1.0:
        return image
    return image.resize((round(width * scale), round(height * scale)), Image.Resampling.LANCZOS)


def _data_url(image: Image.Image) -> str:
    # PNG ללא דחיסה הרסנית: JPEG נבדק ושינה קריאת ספרות בכתב יד.
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _usage(model: str, completion: object) -> Usage | None:
    raw = getattr(completion, "usage", None)
    if raw is None:
        return None
    tokens_in = int(getattr(raw, "prompt_tokens", 0) or 0)
    tokens_out = int(getattr(raw, "completion_tokens", 0) or 0)
    return Usage(tokens_in, tokens_out, cost_usd(model, tokens_in, tokens_out))


def _out_of_quota(message: str) -> bool:
    return "insufficient_quota" in message or "exceeded your current quota" in message


def _is_transient_rate_limit(exc: Exception) -> bool:
    """Overload that clears by itself: rate limit or a temporary server error."""
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    message = str(getattr(exc, "message", "") or exc)
    if status in (500, 502, 503, 504):
        return True
    return status == 429 and not _out_of_quota(message)


def _classify(exc: Exception) -> tuple[str, bool]:
    """Turn an SDK exception into (message a non-developer can act on, is_fatal)."""
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    message = str(getattr(exc, "message", "") or exc)

    if _out_of_quota(message):
        return "אין יתרת קרדיט בחשבון OpenAI. בדקו ב-platform.openai.com/billing.", True
    if status in (429, 500, 502, 503, 504):
        return "OpenAI עמוסה כרגע. נסו שוב בעוד רגע.", False
    if status in (401, 403) or "Incorrect API key" in message or "invalid_api_key" in message:
        return "המפתח ב-.env נדחה על ידי OpenAI. בדקו את OPENAI_API_KEY.", True
    if status == 404 or "does not exist" in message or "model_not_found" in message:
        return "מודל OpenAI המוגדר אינו זמין בחשבון. בדקו את JUDGE_B_MODEL ב-.env.", True
    if status == 400 and "image" in message.lower():
        return "מודל OpenAI שנבחר אינו תומך בקריאת תמונות. בדקו את JUDGE_B_MODEL.", True
    return f"קריאה ל-OpenAI נכשלה ({type(exc).__name__}). פרטים בלוג השרת.", False
