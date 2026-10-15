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


def _guard(image: Image.Image) -> None:
    if image.width * image.height > MAX_PIXELS:
        raise ValueError("העמוד גדול מדי לעיבוד. נסו DPI נמוך יותר.")
