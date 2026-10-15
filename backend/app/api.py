"""HTTP surface. Thin on purpose: validate, delegate, serialise."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response

from app import gold, pipeline, store
from app.config import SETTINGS
from app.crops import crop_field, padded
from app.pages import ink_enhanced
from app.excel import workbook_bytes
from app.fields import FIELDS, FIELDS_BY_KEY, MULTI_SEPARATOR
from app.files import ACCEPTED_LABEL, sniff_kind
from app.models import (
    STATE_APPROVED,
    STATE_OK,
    STATE_REVIEW,
    Batch,
    SourceFile,
    new_id,
)
from app.validators import REASON_TEXT

WEB_DIR = Path(__file__).resolve().parent / "web"

app = FastAPI(title="טופס למידע", docs_url=None, redoc_url=None)


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((WEB_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/api/config")
def config() -> dict:
    return {
        "model": SETTINGS.model,
        "judges": SETTINGS.judges,
        # עומק החשיבה מוצג רק כשקורא Claude פעיל
        "effort": SETTINGS.claude_effort if any(m.startswith("claude") for m in SETTINGS.judges) else "",
        "has_key": SETTINGS.has_key,
        "max_cost_usd": SETTINGS.max_cost_per_batch_usd,
        "max_pages": SETTINGS.max_pages_per_batch,
        "max_files": SETTINGS.max_files_per_batch,
        "fields": [
            {"key": f.key, "label": f.label_he, "multi": f.multi, "hint": f.hint_he}
            for f in FIELDS
        ],
    }


@app.post("/api/batches")
async def create_batch(files: list[UploadFile]) -> dict:
    """Accept one drop of any number of files, of any accepted type.

    Each file is checked on its own. A bad file is recorded and skipped; it
    never takes the good ones down with it. Only an entirely unusable drop is
    rejected outright.
    """
    if not files:
        raise HTTPException(400, "לא נבחרו קבצים.")
    if len(files) > SETTINGS.max_files_per_batch:
        raise HTTPException(
            413, f"יותר מדי קבצים בבת אחת. המקסימום הוא {SETTINGS.max_files_per_batch}."
        )

    batch = Batch(batch_id=new_id("bat"))
    for position, upload in enumerate(files, start=1):
        source = await _store_source(batch.batch_id, position, upload)
        batch.sources.append(source)

    if not batch.accepted_sources:
        store.delete(batch.batch_id)
        reasons = sorted({s.error for s in batch.sources if s.error})
        raise HTTPException(415, "אף קובץ לא התקבל. " + " ".join(reasons))

    store.put(batch)
    pipeline.start(batch.batch_id)
    return {
        "batch_id": batch.batch_id,
        "accepted": len(batch.accepted_sources),
        "rejected": len(batch.rejected_sources),
    }


async def _store_source(batch_id: str, position: int, upload: UploadFile) -> SourceFile:
    name = upload.filename or f"מסמך {position}"
    source = SourceFile(index=position, filename=name)

    data = await upload.read()
    if not data:
        source.error = "הקובץ ריק"
        return source
    if len(data) > SETTINGS.max_upload_bytes:
        limit = SETTINGS.max_upload_bytes // (1024 * 1024)
        source.error = f"גדול מ-{limit}MB"
        return source

    kind = sniff_kind(data[:12])
    if kind is None:
        source.error = f"סוג לא נתמך — מותר {ACCEPTED_LABEL}"
        return source

    source.kind = kind
    source.sha256 = hashlib.sha256(data).hexdigest()
    (store.source_dir(batch_id, position) / "source").write_bytes(data)
    return source


@app.get("/api/batches/{batch_id}")
def read_batch(batch_id: str) -> dict:
    return _serialise(_require(batch_id))


@app.get("/api/batches/{batch_id}/sources/{source_index}/pages/{page_number}")
def page_image(batch_id: str, source_index: int, page_number: int) -> FileResponse:
    _require(batch_id)
    path = pipeline.page_image_path(batch_id, source_index, page_number)
    if not path.is_file():
        raise HTTPException(404, "העמוד לא נמצא.")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/api/batches/{batch_id}/sources/{source_index}/pages/{page_number}/crops/{field_key}")
def field_crop(
    batch_id: str,
    source_index: int,
    page_number: int,
    field_key: str,
    ink: bool = False,
    by: str = "",
) -> FileResponse:
    """The enlarged cut-out of one field, for the lower half of the split panel.

    `by` asks for one judge's location (when the judges disagree, each gets its
    own cut-out); without it, the field's main location is used.
    """
    batch = _require(batch_id)
    if field_key not in FIELDS_BY_KEY:
        raise HTTPException(404, "שדה לא מוכר.")
    record = next(
        (r for r in batch.records if r.source_index == source_index and r.page_number == page_number),
        None,
    )
    field = record.fields[field_key] if record else None
    region = None
    if field is not None:
        region = field.regions_by_model.get(by) if by else field.region
    page = pipeline.page_image_path(batch_id, source_index, page_number)
    if region is None or not page.is_file():
        # אין מיקום אמין — לא ממציאים תקריב. המסך יציג את העמוד המלא בלבד.
        raise HTTPException(404, "אין מיקום אמין לשדה זה.")
    target = pipeline.crop_path(batch_id, source_index, page_number, field_key)
    if by:
        target = target.with_name(f"{target.stem}.{_SAFE.sub('_', by)}.png")
    if ink:
        # אותה הגדלה מגרסת "דיו מודגש": בלי מרקרים, כתב היד שחור. מקומי, בלי עלות.
        page = ink_enhanced(page)
        target = target.with_name(target.stem + ".ink.png")
    return FileResponse(
        crop_field(page, region, target), media_type="image/png", headers={"Cache-Control": "no-store"}
    )


@app.post("/api/records/{record_id}/verify")
def verify_record(record_id: str) -> dict:
    """The secretary checked every field of this form against the original.

    Every field becomes green, and the form is saved as an answer-key entry for
    measurement. Refused while a field still has an unresolved disagreement.
    """
    found = store.find_record(record_id)
    if found is None:
        raise HTTPException(404, "הרשומה לא נמצאה.")
    batch, record = found
    open_choices = [FIELDS_BY_KEY[k].label_he for k, f in record.fields.items() if f.candidates]
    if open_choices:
        raise HTTPException(409, "יש שדות שעדיין צריך לבחור בהם ערך: " + ", ".join(open_choices))

    for field in record.fields.values():
        field.state = STATE_APPROVED
        field.settle()
    record.verified = True

    source = next(s for s in batch.sources if s.index == record.source_index)
    page = pipeline.page_image_path(batch.batch_id, record.source_index, record.page_number)
    if page.is_file():
        gold.save(record, source, page)
    return _serialise(batch)


@app.patch("/api/records/{record_id}")
def update_record(record_id: str, payload: dict) -> dict:
    found = store.find_record(record_id)
    if found is None:
        raise HTTPException(404, "הרשומה לא נמצאה.")
    batch, record = found

    key = payload.get("field")
    if key not in record.fields:
        raise HTTPException(400, "שדה לא מוכר.")
    field = record.fields[key]

    if "values" in payload:
        values = [str(v).strip() for v in payload["values"] if str(v).strip()]
        field.values = values
        field.candidates = []  # הוכרע על ידי אדם
        # תיקון אנושי מנקה את הדגלים וההערות האוטומטיים — האדם ראה את המקור.
        field.settle()
        field.state = STATE_APPROVED if values else STATE_REVIEW
        if not values:
            field.reasons = ["missing"]
    elif payload.get("approve"):
        if field.candidates:
            raise HTTPException(409, "יש לבחור אחת מהאפשרויות או לתקן ידנית.")
        field.state = STATE_APPROVED
        field.settle()

    return _serialise(batch)


@app.post("/api/batches/{batch_id}/export")
def export_batch(batch_id: str) -> Response:
    batch = _require(batch_id)
    if not batch.records:
        raise HTTPException(409, "אין עדיין רשומות לייצוא.")
    filename = f"commitments-{batch_id}.xlsx"
    return Response(
        content=workbook_bytes(batch),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "Cache-Control": "no-store",
        },
    )


@app.delete("/api/batches/{batch_id}")
def delete_batch(batch_id: str) -> dict:
    if not store.delete(batch_id):
        raise HTTPException(404, "לא נמצא.")
    return {"deleted": True}


_SAFE = re.compile(r"[^A-Za-z0-9.-]")


def _require(batch_id: str) -> Batch:
    batch = store.get(batch_id)
    if batch is None:
        raise HTTPException(404, "האצווה לא נמצאה. ייתכן שהשרת הופעל מחדש.")
    return batch


def _serialise(batch: Batch) -> dict:
    return {
        "batch_id": batch.batch_id,
        "display_name": batch.display_name,
        "status": batch.status,
        "error": batch.error,
        "total_pages": batch.total_pages,
        "processed_pages": batch.processed_pages,
        "skipped_pages": batch.skipped_pages,
        "budget_skipped_pages": batch.budget_skipped_pages,
        "review_total": batch.review_total,
        "warnings": batch.warnings,
        "cost_usd": batch.cost_usd,
        "seconds": batch.seconds,
        "sources": [
            {
                "index": s.index,
                "filename": s.filename,
                "kind": s.kind,
                "page_count": s.page_count,
                "error": s.error,
            }
            for s in batch.sources
        ],
        "records": [
            {
                "record_id": r.record_id,
                "source_index": r.source_index,
                "source_name": r.source_name,
                "page_number": r.page_number,
                "error": r.error,
                "note": r.model_note,
                "review_count": r.review_count,
                "template": r.template,
                "readers": r.readers,
                "low_resolution": r.low_resolution,
                "cost_usd": r.cost_usd,
                "seconds": r.seconds,
                "verified": r.verified,
                "straightened": r.straightened,
                "fields": {
                    key: {
                        "values": f.values,
                        "state": f.state,
                        "reasons": [REASON_TEXT.get(c, c) for c in f.reasons],
                        "reason_codes": f.reasons,
                        # הערות רכות — ליד הערך, בלי כתום
                        "notes": [REASON_TEXT.get(c, c) for c in f.notes],
                        "note_codes": f.notes,
                        "extra_values": f.extra_values,
                        "candidates": [c.split(MULTI_SEPARATOR) for c in f.candidates],
                        "readings": f.readings,
                        # האזור שנחתך בפועל — מסומן בריבוע על העמוד המלא
                        "crop_box": list(padded(f.region)) if f.region else None,
                        "crop_by": f.region_by,
                        "crop_verified": f.region_verified,
                        "two_crops": f.two_crops,
                        "regions": [
                            {
                                "model": m,
                                "box": list(padded(r)),
                                # רק המיקום הראשי של שופט מאומת (Gemini) נחשב מאומת
                                "verified": f.region_verified and m == f.region_by,
                            }
                            for m, r in f.regions_by_model.items()
                        ],
                    }
                    for key, f in r.fields.items()
                },
            }
            for r in batch.records
        ],
        "states": {"ok": STATE_OK, "review": STATE_REVIEW, "approved": STATE_APPROVED},
    }
