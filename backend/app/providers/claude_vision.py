"""Claude vision reader — the only module in the project that imports the Anthropic SDK.

The default reader (Claude Opus 5.5) thinks before it answers; `effort` sets
how deeply (Noa chose "high"). Deep thinking means long answers, so the call
is streamed — the SDK refuses long non-streamed requests — and the structured
answer is taken from the final message. The long prompt goes in `system` with
a cache marker, so after the first form it is billed at a fraction of the input
price. The page is shrunk to the model's own limit (2576 px on the long side)
with a high-quality filter, so what is sent is predictable.

Its boxes drive the zoom panel. The full page above the zoom shows a frame
around exactly what was cut, so a box that missed is obvious, not misleading.
"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import anthropic
from PIL import Image

from app.config import SETTINGS, cost_usd
from app.providers.base import ExtractionError, RawExtraction, Region, Usage, clamp_region
from app.providers.reading import (
    NAMES_PROMPT,
    NAMES_VERSION,
    NameCheck,
    NameReading,
    PageReading,
    load_prompt,
    prompt_version,
    to_name_check,
    to_raw,
)

_ASK_NAMES = "קרא את שם המטופל שבתקריב, לפי ההוראות."

MAX_LONG_SIDE = 2576  # הגבול של Claude Sonnet 5.5 לתמונה ברזולוציה גבוהה
# Claude מקטין בעצמו כל תמונה מעל ~3.5 מיליון פיקסלים (נמדד ב-count_tokens: 4,780 טוקנים
# לכל תמונה גדולה מזה). אז המודל מודד מיקומים בתמונה קטנה ממה שאמרנו לו — והתיבות זזות.
# לכן שולחים כבר בגודל שהוא רואה: מה שכתוב בהודעה = מה שהוא רואה. אותה עלות, אותה איכות.
MAX_PIXELS = 3_400_000
# צילום מסך או תמונה קטנה (למשל 670×909) הגיעו למודל כמו שהם — פי 6 פחות פיקסלים מעמוד PDF.
# שם המודל מיקם שדות בסטייה של עד 16% מרוחב הדף (ת"ז נחתכה על השם, 21/10/2026).
# תמונה כזו מוגדלת עד שהצלע הארוכה שלה 2000 — כמו בבדיקת הכיוון ב-pages.py (OSD_MIN_SIDE).
# עמוד PDF (~3,500) לא מושפע.
MIN_LONG_SIDE = 2000


def fit_scale(width: int, height: int) -> float:
    """How much to resize so the model sees the image exactly as sent: shrink a large page,
    enlarge a small image up to MIN_LONG_SIDE, never past the limits."""
    limit = min(MAX_LONG_SIDE / max(width, height), (MAX_PIXELS / (width * height)) ** 0.5)
    return min(limit, max(1.0, MIN_LONG_SIDE / max(width, height)))
MAX_IMAGE_BYTES = 4_500_000  # מתחת לתקרת 5MB לתמונה, עם מרווח
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# מקום לחשיבה העמוקה ולתשובה. גבוה בכוונה — שהחשיבה לעולם לא תיקטע באמצע.
MAX_OUTPUT_TOKENS = 64_000
READ_TIMEOUT_SECONDS = 15 * 60  # לטופס אחד, כולל חשיבה
# סינון אינטרנט (NetFree) חוסם בקשה בקוד 418 כשרצף האותיות של התמונה המקודדת יוצר במקרה
# "מילה אסורה". אז שולחים את אותו דף בקידוד אחר — רצף אחר לגמרי. בקשה חסומה לא מגיעה
# ל-Anthropic ולא מחויבת. הסדר: PNG רגיל, JPEG צבעוני, JPEG אפור.
FILTER_BLOCKED = 418
ENCODINGS = ("png", "jpeg", "jpeg_gray")

_COORDS = """
## מיקום כל ערך — `boxes` ו-`box`

**מיקומים בפיקסלים של התמונה שקיבלת.** בהודעה כתוב רוחב התמונה וגובהה בפיקסלים.
x נמדד לאורך הרוחב (0 עד הרוחב), y לאורך הגובה (0 עד הגובה). אל תשתמש בסקאלה אחרת.

לכל שדה שנמצא:
- `boxes` — רשימה, **תיבה לכל ערך** באותו סדר כמו `values`. כל תיבה
  `[xmin, ymin, xmax, ymax]` סביב **הערך עצמו** (לא הכיתוב שלידו). שני מספרים מרכזיים
  = שתי תיבות, כל אחת סביב המספר שלה.
- `box` — תיבה אחת שמקיפה את כל הערכים של השדה.
- `tile` = 1.

לשדה שלא נמצא: `tile` = null, `box` = null, `boxes` = null.
"""

_ASK = "קרא את טופס ההתחייבות שבתמונה והחזר את השדות בדיוק לפי ההוראות."


def _ask(size: tuple[int, int]) -> str:
    width, height = size
    return f"גודל התמונה: רוחב {width} פיקסלים, גובה {height} פיקסלים.\n{_ASK}"


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
        self.model = model or SETTINGS.reader_model
        self.effort = effort or SETTINGS.claude_effort
        # ה-SDK מנסה שוב לבד על עומס (429) ושגיאות שרת (5xx). קריאה שנכשלה לא מחויבת.
        self._client = anthropic.Anthropic(
            api_key=key, max_retries=2, timeout=READ_TIMEOUT_SECONDS
        )
        self._prompt = load_prompt(_COORDS)
        self.prompt_version = prompt_version(self._prompt)
        self._names_prompt = NAMES_PROMPT
        self.names_version = NAMES_VERSION

    def extract(self, image_path: Path) -> RawExtraction:
        for encoding in ENCODINGS:
            try:
                return self._extract(image_path, encoding)
            except anthropic.APIStatusError as exc:
                if getattr(exc, "status_code", None) != FILTER_BLOCKED:
                    message, fatal = _classify(exc)
                    raise ExtractionError(message, fatal=fatal) from exc
                blocked = exc  # הסינון חסם את הקידוד הזה — מנסים את הבא
        raise ExtractionError(
            "הבקשה נחסמה על ידי סינון האינטרנט במחשב (NetFree) — לא הגיעה ל-Claude ולא חויבה. "
            "אפשר לבקש מ-NetFree לפתוח את api.anthropic.com."
        ) from blocked

    def _extract(self, image_path: Path, encoding: str) -> RawExtraction:
        block, size = _image_block_sized(image_path, encoding)
        images = [block]
        try:
            # streaming: חשיבה עמוקה = תשובה ארוכה, וה-SDK לא מאפשר אותה בלי זרימה.
            with self._client.beta.messages.stream(
                model=self.model,
                max_tokens=MAX_OUTPUT_TOKENS,
                # הפרומפט הקבוע ראשון ועם סימון מטמון — משולם במלואו פעם אחת בלבד.
                system=[{"type": "text", "text": self._prompt, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": [*images, {"type": "text", "text": _ask(size)}]}],
                output_format=PageReading,
                # עומק החשיבה. ב-Opus 5.5 החשיבה תמיד פועלת; זה קובע כמה עמוק.
                output_config={"effort": self.effort},
                # בסירוב — הבקשה עוברת אוטומטית למודל גיבוי במקום להיכשל.
                betas=[FALLBACK_BETA],
                fallbacks="default",
            ) as stream:
                response = stream.get_final_message()
        except anthropic.APITimeoutError as exc:
            raise ExtractionError("Claude לא סיים לקרוא את הטופס בזמן. נסו שוב.") from exc
        except anthropic.APIConnectionError as exc:
            raise ExtractionError("אין חיבור ל-Anthropic. נסו שוב בעוד רגע.") from exc

        return self._to_domain(response, size)

    def _to_domain(self, response: object, size: tuple[int, int] = (1000, 1000)) -> RawExtraction:
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
        return to_raw(parsed, served_by, lambda box, tile: _region_of(box, size), _usage(served_by, response))

    # ------------------------------------------------------------ שלב שני: שמות מתוך תקריב
    def read_names(self, crop_path: Path) -> NameCheck:
        """Read first and last name again, from a sharp close-up of where they are.

        A second, short request with its own instructions — only the close-up is
        sent. Same model, same depth. A network-filter block is retried in another
        encoding, like the page itself."""
        for encoding in ENCODINGS:
            try:
                return self._read_names(crop_path, encoding)
            except anthropic.APIStatusError as exc:
                if getattr(exc, "status_code", None) != FILTER_BLOCKED:
                    message, fatal = _classify(exc)
                    raise ExtractionError(message, fatal=fatal) from exc
                blocked = exc
        raise ExtractionError("הבקשה נחסמה על ידי סינון האינטרנט במחשב (NetFree).") from blocked

    def _read_names(self, crop_path: Path, encoding: str) -> NameCheck:
        block, _ = _image_block_sized(crop_path, encoding)
        try:
            with self._client.beta.messages.stream(
                model=self.model,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=[{"type": "text", "text": self._names_prompt, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": [block, {"type": "text", "text": _ASK_NAMES}]}],
                output_format=NameReading,
                output_config={"effort": self.effort},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            ) as stream:
                response = stream.get_final_message()
        except anthropic.APITimeoutError as exc:
            raise ExtractionError("Claude לא סיים לקרוא את השם בזמן.") from exc
        except anthropic.APIConnectionError as exc:
            raise ExtractionError("אין חיבור ל-Anthropic.") from exc
        if getattr(response, "stop_reason", None) in ("refusal", "max_tokens"):
            raise ExtractionError("Claude לא השלים את קריאת השם.")
        parsed = getattr(response, "parsed_output", None)
        if not isinstance(parsed, NameReading):
            text = "".join(
                getattr(b, "text", "") for b in getattr(response, "content", []) or []
                if getattr(b, "type", "") == "text"
            ).strip()
            try:
                parsed = NameReading(**json.loads(text))
            except Exception as exc:  # noqa: BLE001
                raise ExtractionError("Claude החזיר תשובה במבנה לא תקין") from exc
        served_by = getattr(response, "model", None) or self.model
        return to_name_check(parsed, served_by, _usage(served_by, response))


def _image_block(image_path: Path, encoding: str = "png") -> dict:
    return _image_block_sized(image_path, encoding)[0]


def _image_block_sized(image_path: Path, encoding: str = "png") -> tuple[dict, tuple[int, int]]:
    """The image block, and the exact pixel size of the image inside it — the
    reader's positions are in those pixels."""
    if encoding == "png":
        data, media_type = page_png_b64(image_path), "image/png"
    else:
        data, media_type = page_jpeg_b64(image_path, gray=encoding == "jpeg_gray"), "image/jpeg"
    with Image.open(io.BytesIO(base64.b64decode(data))) as sent:
        size = sent.size
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}, size


def encode_image(image: Image.Image, encoding: str = "png") -> tuple[str, str, tuple[int, int]]:
    """Base64 data, media type and the exact size sent — at most 2576 px on the long
    side and under 5MB. PNG normally; JPEG when a network filter blocked the PNG."""
    scale = fit_scale(*image.size)
    while True:
        size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        sized = image if size == image.size else image.resize(size, Image.Resampling.LANCZOS)
        if encoding == "jpeg_gray":
            sized = sized.convert("L")
        buffer = io.BytesIO()
        if encoding == "png":
            sized.save(buffer, format="PNG", optimize=True)
        else:
            sized.save(buffer, format="JPEG", quality=92)
        data = buffer.getvalue()
        if len(data) <= MAX_IMAGE_BYTES or scale < 0.3:
            media_type = "image/png" if encoding == "png" else "image/jpeg"
            return base64.standard_b64encode(data).decode("ascii"), media_type, sized.size
        scale *= 0.85


def page_jpeg_b64(image_path: Path, gray: bool = False) -> str:
    """The same page as JPEG (colour or gray), same size limit — a different byte
    sequence, for when a network filter blocked the PNG."""
    with Image.open(image_path) as page:
        page = page.convert("L" if gray else "RGB")
        scale = fit_scale(*page.size)
        size = (max(1, round(page.width * scale)), max(1, round(page.height * scale)))
        sized = page if size == page.size else page.resize(size, Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        sized.save(buffer, format="JPEG", quality=92)
        return base64.standard_b64encode(buffer.getvalue()).decode("ascii")


def page_png_b64(image_path: Path) -> str:
    """The page as base64 PNG, at most 2576 px on the long side and under 5MB."""
    with Image.open(image_path) as page:
        page = page.convert("RGB")
        scale = fit_scale(*page.size)
        while True:
            size = (max(1, round(page.width * scale)), max(1, round(page.height * scale)))
            sized = page if size == page.size else page.resize(size, Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            sized.save(buffer, format="PNG", optimize=True)
            data = buffer.getvalue()
            if len(data) <= MAX_IMAGE_BYTES or scale < 0.3:
                return base64.standard_b64encode(data).decode("ascii")
            scale *= 0.85


def _region_of(box: list[float] | None, size: tuple[int, int] = (1000, 1000)) -> Region | None:
    """One box in pixels of the image that was sent → a region of the page (0–1)."""
    if box is None or len(box) != 4:
        return None
    width, height = size
    try:
        x0, y0, x1, y1 = (float(v) for v in box)
    except (TypeError, ValueError):
        return None
    return clamp_region((x0 / width, y0 / height, x1 / width, y1 / height))


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
        return "מודל Claude המוגדר אינו זמין בחשבון. בדקו את READER_MODEL ב-.env.", True
    if isinstance(exc, anthropic.RateLimitError) or (status is not None and status >= 500):
        # ה-SDK כבר ניסה שוב — עומס שנמשך הוא בעיה של הטופס הזה, לא של החשבון.
        return "Anthropic עמוסה כרגע. נסו שוב בעוד רגע.", False
    return f"קריאה ל-Claude נכשלה ({type(exc).__name__}). פרטים בלוג השרת.", False
