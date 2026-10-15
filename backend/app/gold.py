"""Forms a person checked and saved as verified — the answer key for measurement.

Nobody types a truth table. The secretary reviews a form in the normal screen,
fixes what is wrong, and presses "שמור כמאומת"; the values she ended up with
are stored here together with a copy of the page, so `uv run evaluate` can
re-run any configuration against them later. Never filled by a model.

About a third of the forms are held out by a fixed rule, so prompt tuning
never sees them and their score stays honest.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from app.config import GOLD_DIR
from app.models import FormRecord, SourceFile, now


@dataclass
class GoldForm:
    key: str
    page_png: Path
    source_name: str
    page_number: int
    template: str
    truth: dict[str, list[str]]
    holdout: bool


def is_holdout(sha256: str, page_number: int) -> bool:
    digest = hashlib.sha256(f"{sha256}:{page_number}".encode()).hexdigest()
    return int(digest[:8], 16) % 3 == 0


def save(record: FormRecord, source: SourceFile, page_png: Path, root: Path | None = None) -> Path:
    key = f"{(source.sha256 or record.record_id)[:16]}-p{record.page_number:03d}"
    folder = (root or GOLD_DIR) / key
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(page_png, folder / "page.png")
    payload = {
        "source_name": source.filename,
        "sha256": source.sha256,
        "page_number": record.page_number,
        "template": record.template,
        "verified_at": now(),
        "holdout": is_holdout(source.sha256 or record.record_id, record.page_number),
        "fields": {key_: f.values for key_, f in record.fields.items()},
    }
    (folder / "truth.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return folder


def load_all(root: Path | None = None) -> list[GoldForm]:
    folder = root or GOLD_DIR
    forms: list[GoldForm] = []
    if not folder.is_dir():
        return forms
    for entry in sorted(folder.iterdir()):
        truth_file, page = entry / "truth.json", entry / "page.png"
        if not (truth_file.is_file() and page.is_file()):
            continue
        data = json.loads(truth_file.read_text(encoding="utf-8"))
        forms.append(
            GoldForm(
                key=entry.name,
                page_png=page,
                source_name=data.get("source_name", ""),
                page_number=data.get("page_number", 0),
                template=data.get("template", ""),
                truth={k: list(v) for k, v in data.get("fields", {}).items()},
                holdout=bool(data.get("holdout")),
            )
        )
    return forms
