"""Upload validation: what a file really is, decided by its bytes."""

from __future__ import annotations

from pathlib import Path

KIND_PDF = "pdf"
KIND_JPEG = "jpeg"
KIND_PNG = "png"

ACCEPTED_LABEL = "PDF, JPG או PNG"


def sniff_kind(data: bytes) -> str | None:
    """Return the real file kind, or None when it is not one we accept.

    The extension and the browser-supplied MIME type are user input; only the
    leading bytes decide.
    """
    if data.startswith(b"%PDF-"):
        return KIND_PDF
    if data.startswith(b"\xff\xd8\xff"):
        return KIND_JPEG
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return KIND_PNG
    return None


def sniff_path(path: Path) -> str | None:
    with path.open("rb") as fh:
        return sniff_kind(fh.read(12))
