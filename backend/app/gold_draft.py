"""`uv run gold-draft` — load a draft truth table into the normal review screen.

The draft (evaluation/truth_draft.json) was typed by hand from the sample
images; it is NOT an answer key until a person checks it. This command turns
it into one upload of the sample pages where every field is orange with the
reason "draft — confirm against the source". The person fixes what is wrong
and presses "אישור הטופס"; only then is the form saved as a verified answer
(evaluation/gold) for `uv run evaluate`. No model is called; it costs nothing.

Fields the draft marks as ambiguous (a half-printed stamp, two different IDs)
are kept with the verified form, so measurement can count "flagged orange" as
the right outcome there instead of demanding one exact value.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from app import store
from app.config import PROJECT_ROOT
from app.models import Batch, FormRecord, SourceFile, new_id
from app.pages import render_pages

DRAFT = PROJECT_ROOT / "evaluation" / "truth_draft.json"
SAMPLES = PROJECT_ROOT / "samples"


def load_draft(path: Path = DRAFT) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["forms"]


def build(forms: list[dict], samples: Path = SAMPLES, session_id: str = "") -> Batch:
    """One batch holding exactly the draft's pages, every field orange."""
    batch = Batch(batch_id=new_id("gold"), session_id=session_id, status="completed")
    files = sorted({f["file"] for f in forms})
    for index, name in enumerate(files, start=1):
        data = (samples / name).read_bytes()
        source = SourceFile(index=index, filename=name, kind="pdf", sha256=hashlib.sha256(data).hexdigest())
        folder = store.source_dir(batch.batch_id, index)
        (folder / "source").write_bytes(data)
        wanted = max(f["page"] for f in forms if f["file"] == name)
        written, _ = render_pages(folder / "source", folder / "pages", limit=wanted)
        source.page_count = len(written)
        batch.sources.append(source)
        for form in sorted((f for f in forms if f["file"] == name), key=lambda f: f["page"]):
            record = FormRecord.empty(batch.batch_id, source, form["page"])
            record.template = form.get("kind", "")
            record.model_note = form.get("notes", "")
            ambiguous = form.get("ambiguous", [])
            record.context["ambiguous"] = ",".join(ambiguous)
            for key, field in record.fields.items():
                field.values = list(form.get(key, []))
                field.flag("draft_truth")
                if key in ambiguous:
                    field.flag("draft_ambiguous")
            batch.records.append(record)
    batch.total_pages = batch.processed_pages = len(batch.records)
    batch.pages = [{"source_index": r.source_index, "page_number": r.page_number,
                    "status": "done", "record_id": r.record_id} for r in batch.records]
    return batch


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(prog="gold-draft", description="טיוטת טבלת אמת → מסך הבדיקה.")
    parser.add_argument("--draft", default=str(DRAFT))
    args = parser.parse_args(argv)

    forms = load_draft(Path(args.draft))
    missing = sorted({f["file"] for f in forms if not (SAMPLES / f["file"]).is_file()})
    if missing:
        print("חסרים קבצי דוגמה ב-samples/: " + ", ".join(missing))
        return 1
    batch = build(forms)
    store.put(batch)
    if not store.persist(batch):
        print("השמירה במסד נכשלה.")
        return 1
    print(f"{len(batch.records)} טפסים נטענו לבדיקה. כל שדה כתום עד שתאשרי אותו מול התמונה.")
    print("הפעילי את השרת (uv run serve) ופתחי:")
    print(f"  http://127.0.0.1:8000/?batch={batch.batch_id}")
    print("כל טופס שתאשרי ב'אישור הטופס' נשמר כתשובה מאומתת למדידה (evaluation/gold).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
