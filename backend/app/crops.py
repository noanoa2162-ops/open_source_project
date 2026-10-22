"""Cut one field out of the page and enlarge it, for the split preview panel.

Done locally, on demand, at no model cost. Model coordinates are imprecise
(vision models say so themselves), so the crop gets generous margins; the full page
above it shows a box around exactly the area that was cut, so a crop that
missed is obvious rather than misleading.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from app.models import Region

MARGIN = 0.18  # שוליים יחסית לגודל השדה, מכל צד
MIN_MARGIN = 0.02  # ולפחות 2% מהעמוד — שדה זעיר עדיין מקבל הקשר
ZOOM = 2
MAX_WIDTH = 1600


def padded(region: Region) -> Region:
    """The area actually cut: the field plus margins, kept inside the page."""
    x0, y0, x1, y1 = region
    dx = max((x1 - x0) * MARGIN, MIN_MARGIN)
    dy = max((y1 - y0) * MARGIN, MIN_MARGIN)
    return (max(0.0, x0 - dx), max(0.0, y0 - dy), min(1.0, x1 + dx), min(1.0, y1 + dy))


def crop_field(page_png: Path, region: Region, target: Path, pad: bool = True) -> Path:
    """pad=False cuts exactly the region — an area a person already chose (dragged zoom)."""
    if target.is_file():
        return target
    x0, y0, x1, y1 = padded(region) if pad else region
    with Image.open(page_png) as page:
        width, height = page.size
        box = (
            int(x0 * width),
            int(y0 * height),
            max(int(x0 * width) + 1, int(x1 * width)),
            max(int(y0 * height) + 1, int(y1 * height)),
        )
        piece = page.crop(box).convert("RGB")
    scale = min(ZOOM, MAX_WIDTH / max(1, piece.width))
    if scale > 1:
        piece = piece.resize(
            (round(piece.width * scale), round(piece.height * scale)), Image.Resampling.LANCZOS
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    piece.save(target, format="PNG")
    return target
