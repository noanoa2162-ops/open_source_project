"""Render an uploaded document into one PNG per page.

The vision model reads the original page image directly — there is no separate
OCR step — so this is the only thing standing between the upload and the model.
"""

from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageOps

from app.config import SETTINGS
from app.files import KIND_PDF, sniff_path

MAX_PIXELS = 60_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


def render_pages(source: Path, out_dir: Path, limit: int | None = None) -> tuple[list[Path], int]:
    """Write page-001.png, page-002.png … into `out_dir`.

    Returns `(written_paths, total_pages_in_document)` so the caller can tell
    the user when a long document was cut short.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    cap = limit if limit is not None else SETTINGS.max_pages_per_batch

    if sniff_path(source) == KIND_PDF:
        return _render_pdf(source, out_dir, cap)
    return _render_image(source, out_dir), 1


def _render_pdf(source: Path, out_dir: Path, cap: int) -> tuple[list[Path], int]:
    pdf = pdfium.PdfDocument(source)
    try:
        total = len(pdf)
        scale = SETTINGS.page_render_dpi / 72.0
        written: list[Path] = []
        for index in range(min(total, cap)):
            target = out_dir / f"page-{index + 1:03d}.png"
            page = pdf[index]
            image = page.render(scale=scale).to_pil().convert("RGB")
            _guard(image)
            image = _straightened(image, target)
            image.save(target, format="PNG")
            image.close()
            page.close()
            written.append(target)
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
    if not SETTINGS.straighten_pages:
        return image
    fixed, note = straighten(image)
    if note:
        turn_note_path(target).write_text(note, encoding="utf-8")
    return fixed


def straighten(image: Image.Image) -> tuple[Image.Image, str]:
    """A page turned on its side, or photographed slightly askew, made level.

    Returns the (possibly new) image and a short Hebrew note of what was done.
    """
    probe = _ink_mask(image)
    notes: list[str] = []

    # דף A4 שהגיע לרוחב, ושהשורות שלו עומדות — סובב על הצד.
    if image.width > image.height * 1.1:
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
