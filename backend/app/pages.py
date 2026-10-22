"""Render an uploaded document into one PNG per page.

The vision model reads the original page image directly — there is no separate
OCR step — so this is the only thing standing between the upload and the model.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageOps

from app.config import SETTINGS
from app.files import KIND_PDF, sniff_path

MAX_PIXELS = 60_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS
# כמה עמודים מוכנים במקביל (Tesseract, יישור, שמירה). נמדד 21/10/2026: ~2 שניות לעמוד בחוט אחד.
PAGE_WORKERS = 8


def count_pages(source: Path) -> int:
    """How many pages the file has — instant, nothing is rendered. 0 if unreadable."""
    try:
        if sniff_path(source) == KIND_PDF:
            pdf = pdfium.PdfDocument(source)
            try:
                return len(pdf)
            finally:
                pdf.close()
        return 1
    except Exception:  # noqa: BLE001 - קובץ פגום ייתפס בשלב הרינדור עצמו
        return 0


def render_pages(
    source: Path, out_dir: Path, limit: int | None = None, on_page=None,
) -> tuple[list[Path], int]:
    """Write page-001.png, page-002.png … into `out_dir`.

    Returns `(written_paths, total_pages_in_document)` so the caller can tell
    the user when a long document was cut short. `on_page(number, path)` is
    called after each page is ready — so the screen can count forms as they
    are found, not only at the end.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    cap = limit if limit is not None else SETTINGS.max_pages_per_batch

    if sniff_path(source) == KIND_PDF:
        return _render_pdf(source, out_dir, cap, on_page)
    written = _render_image(source, out_dir)
    if on_page:
        on_page(1, written[0])
    return written, 1


def _render_pdf(source: Path, out_dir: Path, cap: int, on_page=None) -> tuple[list[Path], int]:
    """The PDF itself is rendered page after page (pypdfium2 is not safe for threads on one
    document, and it is the cheap part). The heavy part — Tesseract, straightening, saving the
    PNG — runs for several pages at once. Every page goes through exactly the same steps as
    before, so its pixels (and the saved answers keyed on them) do not change. `on_page` is
    still called in page order."""
    pdf = pdfium.PdfDocument(source)
    try:
        total = len(pdf)
        count = min(total, cap)
        scale = SETTINGS.page_render_dpi / 72.0
        written = [out_dir / f"page-{index + 1:03d}.png" for index in range(count)]
        done = [False] * count
        reported = [0]
        report_lock = threading.Lock()
        # כמה עמודים מוכנים לעבודה בבת אחת — שלא יתמלא הזיכרון בעמודים שמחכים לתור
        room = threading.BoundedSemaphore(PAGE_WORKERS * 2)

        def finish(index: int, image: Image.Image) -> None:
            try:
                target = written[index]
                image = _straightened(image, target)
                image.save(target, format="PNG")
                image.close()
            finally:
                room.release()
            with report_lock:
                done[index] = True
                # מדווחים לפי הסדר: עמוד 3 נספר רק אחרי 1 ו-2, גם אם הוא מוכן לפניהם
                while reported[0] < count and done[reported[0]]:
                    reported[0] += 1
                    if on_page:
                        on_page(reported[0], written[reported[0] - 1])

        with ThreadPoolExecutor(max_workers=PAGE_WORKERS) as pool:
            futures = []
            for index in range(count):
                room.acquire()
                try:
                    page = pdf[index]
                    image = page.render(scale=scale).to_pil().convert("RGB")
                    page.close()
                    _guard(image)
                except BaseException:
                    room.release()
                    raise
                futures.append(pool.submit(finish, index, image))
            for future in futures:
                future.result()  # עמוד שנכשל — אותה שגיאה כמו קודם, לפי הסדר
        return written, total
    finally:
        pdf.close()


def _render_image(source: Path, out_dir: Path) -> list[Path]:
    target = out_dir / "page-001.png"
    with Image.open(source) as src:
        # מצלמות מסובבות תמונות דרך EXIF; בלי זה הטופס מגיע הפוך למודל.
        fixed = ImageOps.exif_transpose(src).convert("RGB")
        _guard(fixed)
        fixed = _straightened(fixed, target)
        fixed.save(target, format="PNG")
        fixed.close()
    return [target]


# "דיו מודגש": גרסה של העמוד בלי מרקרים, שבה כתב היד שחור ובולט.
# נמדד על טופס מאוחדת (15/10/2026), בערוץ האדום: דיו כחול ~74, מרקר טורקיז ~168,
# מרקר צהוב ~245, נייר ~251, דפוס שחור ~34. כל מה שבהיר מ-INK_WHITE נעשה לבן
# (המרקרים נעלמים), כל מה שכהה מ-INK_BLACK נעשה שחור (הדיו והדפוס).
# הערוץ הירוק נבדק קודם ונפסל: העט בטופס טורקיז-כחול (G≈187) ונעלם יחד עם המרקר.
# מגבלה ידועה: עט אדום נעלם בגרסה הזו — ולכן היא נשלחת תמיד לצד המקורית, לא במקומה.
INK_BLACK = 50
INK_WHITE = 150


def ink_path_for(page_png: Path) -> Path:
    return page_png.with_name(page_png.stem + ".ink.png")


def ink_enhanced(page_png: Path) -> Path:
    """Write (once) and return the highlighter-free, bold-ink version of a page."""
    target = ink_path_for(page_png)
    if target.is_file():
        return target
    span = INK_WHITE - INK_BLACK
    table = [
        0 if v <= INK_BLACK else 255 if v >= INK_WHITE else round((v - INK_BLACK) * 255 / span)
        for v in range(256)
    ]
    with Image.open(page_png) as page:
        page.convert("RGB").getchannel("R").point(table).save(target, format="PNG")
    return target


# ------------------------------------------------------------ straightening
#
# מקומי ובחינם, לפני שהעמוד נשלח למודלים. הרעיון: בדף ישר, שורות הטקסט יוצרות
# "פסים" — שורה עם דיו, שורה ריקה, שורה עם דיו. מסובבים עותק קטן של הדף בזוויות
# שונות ובוחרים את הזווית שבה הפסים הכי חדים. דף הפוך (180°) נראה בדיוק כמו דף
# ישר מבחינת הפסים — ולכן אותו מזהים המודלים, לא הקוד הזה.

PROBE_SIDE = 800  # הבדיקה רצה על עותק קטן — מהיר, והתוצאה זהה
INK_LEVEL = 140  # כהה מזה = דיו
MAX_SKEW = 10.0  # מעלות, לכל כיוון
MIN_SKEW = 2.0  # הטיה קטנה מזה לא מפריעה לקריאה — לא נוגעים בדף
# כמה הפסים צריכים להיות חדים יותר כדי שנסובב. סף שמרני: דף ישר נשאר כמו שהוא.
SKEW_MIN_GAIN = 1.15
SIDEWAYS_MIN_GAIN = 1.3


def turn_note_path(page_png: Path) -> Path:
    return page_png.with_name(page_png.stem + ".turn.txt")


def turn_note(page_png: Path) -> str:
    """What was straightened on this page before reading, or "" if nothing."""
    path = turn_note_path(page_png)
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _straightened(image: Image.Image, target: Path) -> Image.Image:
    notes: list[str] = []
    oriented = False
    if SETTINGS.auto_rotate:
        image, turned, oriented = auto_orient(image)
        if turned:
            notes.append(f"הדף סובב ב-{turned}° (זוהה אוטומטית)")
    if SETTINGS.straighten_pages:
        # כשהכיוון כבר נקבע על ידי Tesseract — רק תיקון הטיה קטנה, בלי ניחוש "על הצד".
        image, note = straighten(image, check_sideways=not oriented)
        if note:
            notes.append(note)
    if notes:
        turn_note_path(target).write_text(" · ".join(notes), encoding="utf-8")
    return image


# ------------------------------------------------------------ orientation
#
# Tesseract OSD (Orientation and Script Detection): כלי קוד פתוח, מקומי וחינמי, שמזהה
# אם דף מסובב ב-90°, 180° או 270°. משמש רק לכיוון הדף — לא לקריאת הטקסט.
# נמדד על 48 עמודי הדוגמה (16/10/2026): 11 מתוך 11 הדפים שעל הצד סובבו נכון, ואף אחד
# מ-37 הדפים הישרים לא סובב. הביטחון הנמוך ביותר בדף מסובב היה 9.5 — הסף 5 משאיר מרווח.

OSD_MIN_CONFIDENCE = 5.0
# תמונה שהצלע הארוכה שלה קצרה מזה מוגדלת לפני הבדיקה. נמדד 20/10/2026 על טופס לאומית
# 531×777 שהגיע על הצד: בגודל המקורי ביטחון 1.86 (לא סובב), מוגדל פי 2 — 11.61, ומסובב נכון.
OSD_MIN_SIDE = 1600
OSD_TIMEOUT_SECONDS = 30
_OSD_ROTATE = re.compile(r"Rotate:\s*(\d+)")
_OSD_CONFIDENCE = re.compile(r"Orientation confidence:\s*([\d.]+)")
_WINDOWS_DEFAULT = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")


def tesseract_command() -> str | None:
    """Where Tesseract is, or None when it is not installed (the step is then skipped)."""
    configured = os.environ.get("TESSERACT_CMD", "").strip()
    if configured:
        return configured if Path(configured).is_file() else None
    found = shutil.which("tesseract")
    if found:
        return found
    return str(_WINDOWS_DEFAULT) if _WINDOWS_DEFAULT.is_file() else None


def detect_orientation(image: Image.Image) -> tuple[int, float] | None:
    """(degrees clockwise to turn the page upright, confidence), or None if unknown."""
    command = tesseract_command()
    if command is None:
        return None
    probe = image.convert("L")
    if max(probe.size) < OSD_MIN_SIDE:
        # תמונה קטנה (צילום מסך, תמונה מוקטנת): Tesseract לא בטוח באותיות הקטנות.
        # מגדילים רק את העותק לבדיקה — העמוד עצמו לא משתנה. עמודי PDF (~3,500) לא מגיעים לכאן.
        scale = OSD_MIN_SIDE / max(probe.size)
        probe = probe.resize(
            (round(probe.width * scale), round(probe.height * scale)), Image.Resampling.LANCZOS
        )
    handle, name = tempfile.mkstemp(suffix=".png")
    os.close(handle)
    try:
        probe.save(name)
        result = subprocess.run(
            [command, name, "stdout", "--psm", "0"],
            capture_output=True, text=True, timeout=OSD_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        Path(name).unlink(missing_ok=True)
    output = (result.stdout or "") + (result.stderr or "")
    rotate, confidence = _OSD_ROTATE.search(output), _OSD_CONFIDENCE.search(output)
    if not rotate or not confidence:
        return None
    return int(rotate.group(1)) % 360, float(confidence.group(1))


def auto_orient(image: Image.Image) -> tuple[Image.Image, int, bool]:
    """Turn a sideways or upside-down page upright before any model sees it.

    Returns (image, degrees turned, whether the orientation was decided).
    Anything uncertain or unavailable leaves the page exactly as it was.
    """
    found = detect_orientation(image)
    if found is None:
        return image, 0, False
    degrees, confidence = found
    if confidence < OSD_MIN_CONFIDENCE:
        return image, 0, False
    if degrees not in (90, 180, 270):
        return image, 0, True
    # Tesseract נותן מעלות בכיוון השעון; Pillow מסובב נגד כיוון השעון.
    return image.rotate(-degrees, expand=True), degrees, True


def straighten(image: Image.Image, *, check_sideways: bool = True) -> tuple[Image.Image, str]:
    """A page turned on its side, or photographed slightly askew, made level.

    Returns the (possibly new) image and a short Hebrew note of what was done.
    """
    probe = _ink_mask(image)
    notes: list[str] = []

    # דף A4 שהגיע לרוחב, ושהשורות שלו עומדות — סובב על הצד.
    if check_sideways and image.width > image.height * 1.1:
        upright = probe.rotate(90, expand=True)
        if _stripes(upright) > _stripes(probe) * SIDEWAYS_MIN_GAIN:
            image = image.rotate(90, expand=True)
            probe = upright
            notes.append("הדף הגיע על הצד — סובב ב-90°")

    angle = _skew_angle(probe)
    if angle:
        image = image.rotate(
            angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=(255, 255, 255)
        )
        notes.append(f"הדף היה עקום — יושר ב-{abs(angle):.1f}°")
    return image, " · ".join(notes)


def _ink_mask(image: Image.Image) -> Image.Image:
    small = image.convert("L")
    small.thumbnail((PROBE_SIDE, PROBE_SIDE))
    return small.point(lambda v: 255 if v < INK_LEVEL else 0)


def _stripes(mask: Image.Image) -> float:
    """How sharply ink alternates from row to row. High = level text lines."""
    rows = mask.resize((1, mask.height), Image.Resampling.BOX).tobytes()
    if len(rows) < 2:
        return 0.0
    return sum((b - a) ** 2 for a, b in zip(rows, rows[1:])) / (len(rows) - 1)


def _skew_angle(mask: Image.Image) -> float:
    def score(angle: float) -> float:
        if not angle:
            return _stripes(mask)
        return _stripes(mask.rotate(angle, resample=Image.Resampling.BILINEAR))

    level = score(0.0)
    best, best_score = 0.0, level
    for whole in range(-int(MAX_SKEW), int(MAX_SKEW) + 1):
        current = score(float(whole))
        if current > best_score:
            best, best_score = float(whole), current
    for step in range(-5, 6):
        angle = round(best + step * 0.2, 1)
        if abs(angle) <= MAX_SKEW:
            current = score(angle)
            if current > best_score:
                best, best_score = angle, current
    if abs(best) < MIN_SKEW or best_score < level * SKEW_MIN_GAIN:
        return 0.0
    return best


def turn_upside_down(page_png: Path) -> None:
    """Rotate a stored page 180°, for display and crops. The ink copy is redone."""
    with Image.open(page_png) as page:
        turned = page.rotate(180)
    turned.save(page_png, format="PNG")
    ink_path_for(page_png).unlink(missing_ok=True)


def _guard(image: Image.Image) -> None:
    if image.width * image.height > MAX_PIXELS:
        raise ValueError("העמוד גדול מדי לעיבוד. נסו DPI נמוך יותר.")
