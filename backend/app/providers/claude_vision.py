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
import threading
import time
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
# בלי חשיבה התשובה היא ה-JSON בלבד (נמדד: 400–1,400 טוקנים). מרווח גדול, בלי עלות נוספת.
MAX_OUTPUT_TOKENS_NO_THINKING = 16_000
READ_TIMEOUT_SECONDS = 15 * 60  # לטופס אחד, כולל חשיבה
# סינון אינטרנט (NetFree) חוסם בקשה בקוד 418 כשרצף האותיות של התמונה המקודדת יוצר במקרה
# "מילה אסורה". אז שולחים את אותו דף בקידוד אחר — רצף אחר לגמרי. בקשה חסומה לא מגיעה
# ל-Anthropic ולא מחויבת.
FILTER_BLOCKED = 418
# הסדר: JPEG צבעוני, JPEG אפור, PNG. נמדד 22/10/2026 על 12 עמודי הדוגמה: PNG שוקל 1.42MB
# בממוצע ולוקח 2–3.7 שניות לקודד; JPEG באיכות 90 — כרבע מהגודל ועשירית שנייה. המחיר זהה:
# המודל מחויב לפי פיקסלים, לא לפי בייטים. אותם פיקסלים, אותו גודל — רק העלאה מהירה יותר.
ENCODINGS = ("jpeg", "jpeg_gray", "jpeg_alt", "png")
JPEG_QUALITY = 90
JPEG_ALT_QUALITY = 84  # אותה תמונה, רצף בייטים אחר — עוד הזדמנות לעבור את הסינון
# כשכל הקידודים נחסמו: NetFree בודק תמונות ומשחרר אחרי כ-10 שניות (נצפה ע"י נועה, 22/10/2026).
# סבב אחד נוסף אחרי ההמתנה. בקשה חסומה לא מגיעה ל-Anthropic ולא מחויבת.
FILTER_RETRY_SECONDS = 10

# ---- מטמון ההוראות (כ-14,000 טוקנים) ----
# המטמון נהיה קריא רק כשהתשובה הראשונה מתחילה לזרום. עמודים שנשלחים יחד בהתחלה — כל אחד
# כותב את המטמון מחדש, במחיר מלא ועוד 25%. נמדד 22/10/2026: ב-Opus כל כתיבה כזו עולה כ-7¢,
# כך שהעלאה של 30 טפסים עם 10 קריאות במקביל שילמה כ-60¢ מיותרים (כ-40% מהעלות).
# לכן: העמוד הראשון יוצא לבד, והשאר משתחררים ברגע שהוא מתחיל לזרום (שניות ספורות).
FIRST_PAGE_WAIT_SECONDS = 90  # לא מחכים לנצח: אם הראשון נתקע, השאר יוצאים בכל זאת


class CacheGate:
    """Lets one request write the prompt cache before the others read it.

    `enter()` returns "go" (the cache is warm), "lead" (this request writes it) or
    "wait" (wait for the leader to start streaming, then go). Every request that starts
    streaming keeps the cache warm for another TTL."""

    def __init__(self, warm_seconds: float) -> None:
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._warm_until = 0.0
        self._leading = False
        self.warm_seconds = warm_seconds

    def enter(self) -> str:
        with self._lock:
            if time.monotonic() < self._warm_until:
                return "go"
            if not self._leading:
                self._leading = True
                self._ready.clear()
                return "lead"
            return "wait"

    def wait(self) -> None:
        self._ready.wait(FIRST_PAGE_WAIT_SECONDS)

    def started(self) -> None:
        """A response began streaming: the cache is readable and its timer restarted."""
        with self._lock:
            self._warm_until = time.monotonic() + self.warm_seconds
            self._ready.set()

    def release(self) -> None:
        """The leader finished (or failed): never leave the others waiting."""
        with self._lock:
            self._leading = False
            self._ready.set()

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


def thinking_for(model: str, effort: str, wanted: str = "auto") -> dict | None:
    """The `thinking` setting to send, or None to leave it out.

    Opus 5.5 always thinks: only `effort` sets how deep, and turning it off is a 400.
    Haiku 5.5 and Sonnet 5.5 can answer without thinking (at effort high or below).
    Reading a form is looking, not reasoning, and the answer comes several times faster.
    `wanted`: auto (= off where the model allows it) | on | off."""
    if wanted == "on" or effort in ("xhigh", "max"):
        return None
    if model.startswith("claude-haiku-5"):
        return {"type": "disabled"}
    if model.startswith("claude-sonnet-5-5"):
        return {"type": "between_tools"}  # ב-Sonnet 5.5 זו הדרך לענות בלי חשיבה
    return None


def has_fallback(model: str) -> bool:
    """Server-side refusal fallback exists for Opus/Sonnet 5.5, not for Haiku 5.5."""
    return not model.startswith("claude-haiku")


class ClaudeExtractionProvider:
    name = "claude"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        *,
        effort: str | None = None,
        thinking: str | None = None,
    ) -> None:
        key = api_key or SETTINGS.anthropic_api_key
        if not key:
            raise ExtractionError("אין ANTHROPIC_API_KEY בקובץ .env", fatal=True)
        self.model = model or SETTINGS.reader_model
        self.effort = effort or SETTINGS.claude_effort
        self.thinking = thinking_for(self.model, self.effort, thinking or SETTINGS.reader_thinking)
        # ה-SDK מנסה שוב לבד על עומס (429) ושגיאות שרת (5xx). קריאה שנכשלה לא מחויבת.
        self._client = anthropic.Anthropic(
            api_key=key, max_retries=2, timeout=READ_TIMEOUT_SECONDS
        )
        self._prompt = load_prompt(_COORDS)
        self.prompt_version = prompt_version(self._prompt)
        self._names_prompt = NAMES_PROMPT
        self.names_version = NAMES_VERSION
        self.cache_ttl = "1h" if SETTINGS.prompt_cache_ttl == "1h" else "5m"
        # מרווח ביטחון מתחת לתוקף האמיתי (שעה / 5 דקות)
        self._gate = CacheGate(55 * 60 if self.cache_ttl == "1h" else 4 * 60)

    def extract(self, image_path: Path) -> RawExtraction:
        role = self._gate.enter()
        if role == "wait":
            self._gate.wait()  # העמוד הראשון כותב את המטמון; השאר יקראו אותו בעשירית מהמחיר
        try:
            return self._extract_any_encoding(image_path)
        finally:
            if role == "lead":
                self._gate.release()

    def _extract_any_encoding(self, image_path: Path) -> RawExtraction:
        blocked: Exception | None = None
        for attempt in range(2):
            if attempt:
                time.sleep(FILTER_RETRY_SECONDS)  # NetFree משחרר לרוב אחרי בדיקה של כ-10 שניות
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

    def _request(self, system: str, content: list, output_format) -> dict:
        """The request fields shared by the page and the close-up readings."""
        request = dict(
            model=self.model,
            max_tokens=MAX_OUTPUT_TOKENS if self.thinking is None else MAX_OUTPUT_TOKENS_NO_THINKING,
            # הפרומפט הקבוע ראשון ועם סימון מטמון — משולם במלואו פעם אחת בלבד.
            system=[{"type": "text", "text": system, "cache_control": self._cache_control()}],
            messages=[{"role": "user", "content": content}],
            output_format=output_format,
            # עומק החשיבה. ב-Opus 5.5 החשיבה תמיד פועלת; זה קובע כמה עמוק.
            output_config={"effort": self.effort},
        )
        if self.thinking is not None:
            request["thinking"] = self.thinking  # Haiku/Sonnet: קריאה בלי חשיבה, מהירה פי כמה
        if has_fallback(self.model):
            # בסירוב — הבקשה עוברת אוטומטית למודל גיבוי במקום להיכשל.
            request.update(betas=[FALLBACK_BETA], fallbacks="default")
        return request

    def _cache_control(self) -> dict:
        # שעה: העלאות במעבדה מגיעות בהפרשים של יותר מ-5 דקות. כתיבה עולה פי 2 במקום פי 1.25,
        # וכל קריאה מהמטמון מאריכה אותו שוב — כך משלמים על ההוראות פעם אחת לשעה, לא לכל העלאה.
        return {"type": "ephemeral", "ttl": "1h"} if self.cache_ttl == "1h" else {"type": "ephemeral"}

    def _extract(self, image_path: Path, encoding: str) -> RawExtraction:
        block, size = _image_block_sized(image_path, encoding)
        try:
            # streaming: תשובה ארוכה (בחשיבה), וה-SDK לא מאפשר אותה בלי זרימה.
            with self._client.beta.messages.stream(
                **self._request(self._prompt, [block, {"type": "text", "text": _ask(size)}], PageReading)
            ) as stream:
                _first_event(stream)  # התשובה התחילה לזרום = המטמון קריא לשאר העמודים
                self._gate.started()
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
                **self._request(self._names_prompt, [block, {"type": "text", "text": _ASK_NAMES}], NameReading)
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


def _first_event(stream) -> None:
    """Wait for the first streamed event (the rest is read by get_final_message)."""
    try:
        events = iter(stream)
    except TypeError:  # תחליף בבדיקות שאינו זורם
        return
    next(events, None)


def _image_block(image_path: Path, encoding: str = "jpeg") -> dict:
    return _image_block_sized(image_path, encoding)[0]


def _image_block_sized(image_path: Path, encoding: str = "jpeg") -> tuple[dict, tuple[int, int]]:
    """The image block, and the exact pixel size of the image inside it. The reader's
    positions are in those pixels. The size comes from the encoder itself, not from
    decoding the result again."""
    with Image.open(image_path) as page:
        data, media_type, size = encode_image(page.convert("RGB"), encoding)
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}, size


def encode_image(image: Image.Image, encoding: str = "jpeg") -> tuple[str, str, tuple[int, int]]:
    """Base64 data, media type and the exact size sent: at most 2576 px on the long
    side and under 5MB. JPEG normally (a quarter of the bytes, the same pixels); PNG
    when a network filter blocked both JPEG versions."""
    scale = fit_scale(*image.size)
    while True:
        size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        sized = image if size == image.size else image.resize(size, Image.Resampling.LANCZOS)
        if encoding == "jpeg_gray":
            sized = sized.convert("L")
        elif encoding == "jpeg" and sized.mode not in ("RGB", "L"):
            sized = sized.convert("RGB")
        buffer = io.BytesIO()
        if encoding == "png":
            sized.save(buffer, format="PNG", optimize=True)
        else:
            sized.save(buffer, format="JPEG", quality=JPEG_ALT_QUALITY if encoding == "jpeg_alt" else JPEG_QUALITY)
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
        sized.save(buffer, format="JPEG", quality=JPEG_QUALITY)
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
    # כתיבה למטמון של שעה מחויבת פי 2 (ולא פי 1.25) — מופרדת כאן כדי שהעלות המוצגת תהיה נכונה
    split = getattr(raw, "cache_creation", None)
    write_1h = int(getattr(split, "ephemeral_1h_input_tokens", 0) or 0) if split is not None else 0
    write_1h = min(write_1h, cache_write)
    out = int(getattr(raw, "output_tokens", 0) or 0)
    return Usage(
        fresh + cache_read + cache_write,
        out,
        cost_usd(model, fresh, out, cache_read_tokens=cache_read,
                 cache_write_tokens=cache_write - write_1h, cache_write_1h_tokens=write_1h),
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
