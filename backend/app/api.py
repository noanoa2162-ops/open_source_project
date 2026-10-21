"""HTTP surface. Thin on purpose: validate, delegate, serialise."""

from __future__ import annotations

import hashlib
import io
import json
import secrets
import shutil
import threading
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response, RedirectResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image

from app import db, gold, halves, pipeline, rules, store, twoforms
from app.config import PROJECT_ROOT, SETTINGS
from app.cloud import enabled as cloud_enabled
from app.crops import crop_field, padded
from app.pages import ink_enhanced
from app.excel import preview as excel_preview, saved_csv_bytes, saved_json, saved_workbook_bytes, workbook_bytes
from app.fields import FIELDS, FIELDS_BY_KEY
from app.files import ACCEPTED_LABEL, sniff_kind
from app.models import (
    STATE_APPROVED,
    STATE_OK,
    STATE_REVIEW,
    Batch,
    SourceFile,
    new_id,
    now,
)
from app.validators import REASON_TEXT

WEB_DIR = Path(__file__).resolve().parent / "web"
# טופס לדוגמה לכפתור "נסו עם טופס דוגמה". מחוץ ל-Git (samples/ מוחרגת); אם אין — אין כפתור.
SAMPLE_PATH = PROJECT_ROOT / "samples" / "demo-form.pdf"
THUMB_WIDTH = 240

# המסך ב-React (frontend/, נבנה עם "npm run build" אל web/dist). העיצוב נשאר /static/app.css.
DIST_DIR = WEB_DIR / "dist"

app = FastAPI(title="טופס למידע", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
if cloud_enabled():
    from app.cloud.gateway import CloudGateway
    from app.cloud.uploads import router as upload_router
    app.include_router(upload_router)
    app.add_middleware(CloudGateway)


def _image_response(path: Path, media_type: str, **kwargs):
    if not cloud_enabled() or media_type == "image/jpeg":
        return FileResponse(path, media_type=media_type, **kwargs)
    # Ink/crop derivatives are generated on demand and never become persistent
    # records. Bound their response size; the original PNG remains in Storage.
    with Image.open(path) as image:
        image = image.convert("RGB")
        image.thumbnail((2400, 2400))
        output = io.BytesIO()
        image.save(output, "JPEG", quality=85)
    return Response(output.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})

if (DIST_DIR / "assets").is_dir():
    app.mount("/app/assets", StaticFiles(directory=DIST_DIR / "assets"), name="app-assets")


def _version() -> str:
    # גרסה לפי זמן השינוי של הקבצים — הדפדפן לא יציג CSS/JS ישנים אחרי עדכון.
    return str(max(int(p.stat().st_mtime) for p in WEB_DIR.glob("app.*")))


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    """The screen: React (frontend/, built into web/dist; the design is /static/app.css)."""
    return react_index()


def react_index() -> HTMLResponse:
    if not (DIST_DIR / "index.html").is_file():
        raise HTTPException(503, "המסך לא נבנה — npm run build בתיקייה frontend.")
    html = (DIST_DIR / "index.html").read_text(encoding="utf-8").replace("{{v}}", _version())
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/api/config")
def config() -> dict:
    return {
        "model": SETTINGS.model,
        "effort": SETTINGS.claude_effort,
        "has_key": SETTINGS.has_key,
        "max_cost_usd": SETTINGS.max_cost_per_batch_usd,
        "max_pages": SETTINGS.max_pages_per_batch,
        "max_files": SETTINGS.max_files_per_batch,
        "sample": SAMPLE_PATH.is_file(),
        "cloud_uploads": cloud_enabled(),
        "brand": SETTINGS.brand,
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

    received = [(upload.filename or "", await upload.read()) for upload in files]
    return _start(received)


@app.post("/api/batches/sample")
def create_sample_batch() -> dict:
    """The demo form — already read once, so it comes back from the saved answers, free."""
    if not SAMPLE_PATH.is_file():
        raise HTTPException(404, "אין טופס דוגמה במערכת הזו.")
    return _start([("טופס דוגמה.pdf", SAMPLE_PATH.read_bytes())])


def _start(received: list[tuple[str, bytes]]) -> dict:
    batch = Batch(batch_id=new_id("bat"))
    for position, (name, data) in enumerate(received, start=1):
        batch.sources.append(_store_source(batch.batch_id, position, name, data))

    if not batch.accepted_sources:
        store.delete(batch.batch_id)
        reasons = sorted({s.error for s in batch.sources if s.error})
        raise HTTPException(415, "אף קובץ לא התקבל. " + " ".join(reasons))

    # העלאה חדשה בלי שסיימו את הקודמות: הטפסים שלא אושרו נשמרים כ"ממתינים לאישור"
    for other in store.all_batches():
        pipeline.park_batch(other)
    store.put(batch)
    pipeline.start(batch.batch_id)
    return {
        "batch_id": batch.batch_id,
        "accepted": len(batch.accepted_sources),
        "rejected": len(batch.rejected_sources),
    }


def _store_source(batch_id: str, position: int, name: str, data: bytes) -> SourceFile:
    source = SourceFile(index=position, filename=name or f"מסמך {position}")
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
def page_image(batch_id: str, source_index: int, page_number: int, ink: bool = False) -> Response:
    _require(batch_id)
    if cloud_enabled() and not ink:
        from app.cloud.storage import signed_download
        path = store.source_dir(batch_id, source_index) / "pages" / f"page-{page_number:03d}.png"
        url = signed_download(path)
        if not url:
            raise HTTPException(404, "העמוד לא נמצא.")
        return RedirectResponse(url, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
    path = pipeline.page_image_path(batch_id, source_index, page_number)
    if not path.is_file():
        raise HTTPException(404, "העמוד לא נמצא.")
    if ink:
        path = ink_enhanced(path)  # גרסת "דיו" של העמוד המלא — לתקריב שגוררים. מקומי, בלי עלות
    return _image_response(path, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/api/batches/{batch_id}/sources/{source_index}/pages/{page_number}/thumb")
def page_thumb(batch_id: str, source_index: int, page_number: int) -> FileResponse:
    """A small copy of the page, for the live progress tiles. Made once, locally."""
    _require(batch_id)
    page = pipeline.page_image_path(batch_id, source_index, page_number)
    if not page.is_file():
        raise HTTPException(404, "העמוד עוד לא מוכן.")
    thumb = page.with_name(page.stem + ".thumb.jpg")
    if not thumb.is_file() or thumb.stat().st_mtime < page.stat().st_mtime:
        with Image.open(page) as image:
            small = image.convert("RGB")
            small.thumbnail((THUMB_WIDTH, THUMB_WIDTH * 2))
            small.save(thumb, format="JPEG", quality=80)
    return FileResponse(thumb, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/batches/{batch_id}/sources/{source_index}/pages/{page_number}/crops/{field_key}")
def field_crop(
    batch_id: str,
    source_index: int,
    page_number: int,
    field_key: str,
    ink: bool = False,
    i: int | None = None,
) -> Response:
    """The enlarged cut-out of one field, for the lower half of the split panel."""
    batch = _require(batch_id)
    if field_key not in FIELDS_BY_KEY and field_key != FULL_NAME:
        raise HTTPException(404, "שדה לא מוכר.")
    record = next(
        (r for r in batch.records if r.source_index == source_index and r.page_number == page_number),
        None,
    )
    if not record:
        region = None
    elif field_key == FULL_NAME:
        region = full_name_region(record)
    else:
        field = record.fields[field_key]
        region = field.region
        if i is not None:
            # תקריב של ערך אחד, כשיש כמה (למשל שני מספרים מרכזיים)
            region = field.value_regions[i] if 0 <= i < len(field.value_regions) else None
    page = pipeline.page_image_path(batch_id, source_index, page_number)
    if region is None or not page.is_file():
        # אין מיקום — לא ממציאים תקריב. המסך יציג את העמוד המלא בלבד.
        raise HTTPException(404, "אין מיקום לשדה זה.")
    target = pipeline.crop_path(batch_id, source_index, page_number,
                                field_key if i is None else f"{field_key}-{i}")
    # המיקום בשם הקובץ: אחרי קריאה חוזרת או תיקון מיקום — תקריב חדש, לא הישן
    place = hashlib.sha256(repr(tuple(round(v, 4) for v in region)).encode()).hexdigest()[:8]
    target = target.with_name(f"{target.stem}-{place}.png")
    if ink:
        # אותה הגדלה מגרסת "דיו מודגש": בלי מרקרים, כתב היד שחור. מקומי, בלי עלות.
        page = ink_enhanced(page)
        target = target.with_name(target.stem + ".ink.png")
    return _image_response(
        crop_field(page, region, target), media_type="image/png", headers={"Cache-Control": "no-store"}
    )


@app.post("/api/records/{record_id}/reread")
def reread_record(record_id: str) -> dict:
    """Read one failed form again. Costs one reading — the screen asks the person
    first; nothing calls this on its own."""
    found = store.find_record(record_id)
    if found is None:
        raise HTTPException(404, "הרשומה לא נמצאה.")
    batch, _ = found
    try:
        pipeline.reread(batch.batch_id, record_id)
    except pipeline.RereadRefused as exc:
        raise HTTPException(409, str(exc)) from exc
    return _serialise(batch)


@app.post("/api/records/{record_id}/split")
def split_record(record_id: str) -> dict:
    """Undo an automatic join of two halves: both original forms come back. Free."""
    batch = _finished_batch_of(record_id)
    top = (batch.joined.get(record_id) or [None])[0]
    try:
        halves.split_joined(batch, record_id)
    except halves.SplitRefused as exc:
        raise HTTPException(409, str(exc)) from exc
    return {**_serialise(batch), "focus": top.record_id if top else ""}


@app.post("/api/records/{record_id}/rejoin")
def rejoin_record(record_id: str) -> dict:
    """Join back two halves that were split. Free — the joined picture already exists."""
    batch = _finished_batch_of(record_id)
    try:
        joined = halves.rejoin(batch, record_id)
    except halves.SplitRefused as exc:
        raise HTTPException(409, str(exc)) from exc
    return {**_serialise(batch), "focus": joined.record_id}


@app.post("/api/records/{record_id}/join")
def join_record(record_id: str, payload: dict) -> dict:
    """A person joins two parts of a cut form, and says which goes on top. Free."""
    batch = _finished_batch_of(record_id)
    try:
        joined = halves.join_by_hand(
            batch, record_id, str(payload.get("other", "")), str(payload.get("top", ""))
        )
    except halves.SplitRefused as exc:
        raise HTTPException(409, str(exc)) from exc
    return {**_serialise(batch), "focus": joined.record_id}


@app.post("/api/records/{record_id}/split-page")
def split_page(record_id: str, payload: dict) -> dict:
    """Two forms on one page — the secretary decided to split it. Free: no reading, the
    values already read go to the part where they lie."""
    batch = _finished_batch_of(record_id)
    try:
        parts = twoforms.split_page(batch, record_id, str(payload.get("axis", "")), payload.get("cut"))
    except twoforms.SplitPageRefused as exc:
        raise HTTPException(409, str(exc)) from exc
    return {**_serialise(batch), "focus": parts[0].record_id}


@app.post("/api/records/{record_id}/unsplit-page")
def unsplit_page(record_id: str) -> dict:
    """Undo a page split: the original page comes back as one form. Free."""
    batch = _finished_batch_of(record_id)
    try:
        original = twoforms.unsplit_page(batch, record_id)
    except twoforms.SplitPageRefused as exc:
        raise HTTPException(409, str(exc)) from exc
    return {**_serialise(batch), "focus": original.record_id}


# ------------------------------------------------------------ "🙋 עזרה" — שאלה לממונה, בלי פרטי מטופל בהודעה
# המזכירה מתלבטת בשדה כתום → נוצר קישור עם מפתח אקראי. ההודעה (וואטסאפ/מייל) מכילה רק את הקישור;
# מי שפותח אותו רואה את התקריב של השדה הזה בלבד ועונה. ההחלטה נשארת אצל המזכירה, והשדה לא
# מאושר כל עוד הבקשה ממתינה (החלטת נועה, 21/10/2026). בחינם — בלי מודל.

@app.post("/api/records/{record_id}/help")
def ask_help(record_id: str, payload: dict) -> dict:
    batch = _finished_batch_of(record_id)
    record = next(r for r in batch.records if r.record_id == record_id)
    key = payload.get("field")
    if key not in record.fields:
        raise HTTPException(400, "שדה לא מוכר.")
    field = record.fields[key]
    if record.verified or field.state == STATE_APPROVED:
        raise HTTPException(409, "השדה כבר אושר — אין צורך בעזרה.")
    active = db.help_active([record_id]).get((record_id, key))
    if active is not None and active["expired"]:
        db.help_expire(active["token"])  # הקישור הישן פג — נשאר בתיעוד, ונוצר קישור חדש
        active = None
    if active is None:
        token = secrets.token_urlsafe(18)
        # התקריב כפי שהמזכירה הזיזה אותו נשלח כמו שהוא; בלי הזזה — התקריב על השדה (עם שוליים)
        views = payload.get("views") if isinstance(payload.get("views"), list) else []
        if len(field.value_regions) == len(field.values) > 1:
            places = [(r, _view(views, i)) for i, r in enumerate(field.value_regions) if r]
        else:
            places = [(field.region, _view(views, 0))] if field.region else []
        page = pipeline.page_image_path(batch.batch_id, record.source_index, record.page_number)
        crops = 0
        if page.is_file():
            for region, view in places:
                target = db.HELP_DIR / f"{token}-{crops}.png"
                if view:
                    crop_field(page, view, target, pad=False)
                else:
                    crop_field(page, region, target)
                crops += 1
        active = db.help_create(token, record_id, batch.batch_id, key,
                                str(payload.get("question", "")).strip()[:500],
                                str(payload.get("asker", "")).strip()[:60], list(field.values), crops)
    _changed(batch, record)
    return {**_serialise(batch), "help_token": active["token"]}


def _view(views: list, index: int) -> tuple[float, float, float, float] | None:
    """One area the secretary dragged to, or None. Anything malformed falls back to the field's crop."""
    view = views[index] if 0 <= index < len(views) else None
    if not isinstance(view, list) or len(view) != 4:
        return None
    try:
        x0, y0, x1, y1 = (min(1.0, max(0.0, float(v))) for v in view)
    except (TypeError, ValueError):
        return None
    if x1 - x0 < 0.005 or y1 - y0 < 0.005:  # אזור ריק או הפוך
        return None
    return (x0, y0, x1, y1)


@app.post("/api/records/{record_id}/help/cancel")
def cancel_help(record_id: str, payload: dict) -> dict:
    batch = _finished_batch_of(record_id)
    record = next(r for r in batch.records if r.record_id == record_id)
    db.help_close(record_id, str(payload.get("field", "")), "canceled")
    _changed(batch, record)
    return _serialise(batch)


@app.get("/help/{token}", response_class=HTMLResponse)
def help_page(token: str) -> HTMLResponse:
    """The page the manager opens from the link — only this one field, nothing else."""
    if db.help_get(token) is None:
        raise HTTPException(404, "הבקשה לא נמצאה.")
    built = DIST_DIR / "help.html"
    if not built.is_file():
        raise HTTPException(503, "המסך לא נבנה — npm run build בתיקייה frontend.")
    html = built.read_text(encoding="utf-8")
    return HTMLResponse(html, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                                       "X-Robots-Tag": "noindex"})


@app.get("/api/help/{token}")
def help_request(token: str) -> dict:
    found = db.help_get(token)
    if found is None:
        raise HTTPException(404, "הבקשה לא נמצאה.")
    live = found["status"] == "open" and not found["expired"]
    # רק בקשה פתוחה מציגה ערכים ותקריב. אחרי תשובה / ביטול / 48 שעות — רק המצב, גם אם הקישור הועבר הלאה
    return {
        "field": found["field"], "label": FIELDS_BY_KEY[found["field"]].label_he,
        "question": found["question"] if live else "", "asker": found["asker"],
        "options": found["options"] if live else [], "crops": found["crops"] if live else 0,
        "status": "expired" if found["expired"] else found["status"], "created_at": found["created_at"],
        "answered_by": found["answered_by"], "answered_at": found["answered_at"],
    }


@app.get("/api/help/{token}/crops/{index}")
def help_crop(token: str, index: int) -> FileResponse:
    found = db.help_get(token)
    target = db.HELP_DIR / f"{token}-{index}.png"
    if found is not None and cloud_enabled() and 0 <= index < found["crops"]:
        from app.cloud.storage import ensure
        ensure(target)  # בענן: התקריב באחסון הפרטי (נשמר אוטומטית בסוף בקשת העזרה)
    if (found is None or found["status"] != "open" or found["expired"]
            or not 0 <= index < found["crops"] or not target.is_file()):
        raise HTTPException(404, "אין תקריב.")
    return FileResponse(target, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.post("/api/help/{token}/answer")
def answer_help(token: str, payload: dict) -> dict:
    found = db.help_get(token)
    if found is None:
        raise HTTPException(404, "הבקשה לא נמצאה.")
    if found["status"] != "open":
        raise HTTPException(409, "כבר ענו על הבקשה הזו, או שהיא בוטלה.")
    if found["expired"]:
        raise HTTPException(410, "פג תוקף הקישור — בקשי מהמזכירה לשלוח שוב.")
    value = str(payload.get("value", "")).strip()
    note = str(payload.get("note", "")).strip()[:500]
    if not value and not note:
        raise HTTPException(400, "יש לבחור ערך או לכתוב הערה.")
    if value:
        broken = rules.problems(found["field"], [value])
        if broken:
            raise HTTPException(422, " · ".join(broken))
    db.help_answer(token, value, note, str(payload.get("name", "")).strip()[:60])
    return help_request(token)


def _help_of(batch: Batch) -> dict[tuple[str, str], dict]:
    """Active help requests of this upload. A database problem never breaks the screen."""
    try:
        return db.help_active([r.record_id for r in batch.records])
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return {}


def _help_view(found: dict | None) -> dict | None:
    """What the review screen needs about a field's request — the link key, state and answer."""
    if found is None:
        return None
    return {key: found[key] for key in ("token", "status", "question", "asker", "created_at", "expired",
                                         "answer_value", "answer_note", "answered_by", "answered_at", "crops")}


def _waiting_help(record_id: str, key: str | None = None) -> list[str]:
    """Fields of this form still waiting for an answer — they can't be approved yet."""
    try:
        active = db.help_active([record_id])
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return []
    return [FIELDS_BY_KEY[f].label_he for (_, f), h in active.items()
            if h["status"] == "open" and (key is None or f == key)]


def _decided(record_id: str, key: str) -> None:
    """The secretary fixed or approved the field herself: its request is done (an answer that
    arrives later is refused — she already decided). Approving is refused earlier while waiting."""
    try:
        if db.help_active([record_id]).get((record_id, key)):
            db.help_close(record_id, key, "closed")
    except Exception:  # noqa: BLE001
        traceback.print_exc()


def _finished_batch_of(record_id: str) -> Batch:
    found = store.find_record(record_id)
    if found is None:
        raise HTTPException(404, "הרשומה לא נמצאה.")
    batch, _ = found
    if batch.status in ("queued", "processing"):
        raise HTTPException(409, "הקריאה עוד לא הסתיימה.")
    return batch


@app.post("/api/records/{record_id}/verify")
def verify_record(record_id: str, payload: dict | None = None) -> dict:
    """The secretary checked every field of this form against the original.

    Every field becomes green, and the form is saved as an answer-key entry for
    measurement. Refused while any field is still orange: each one needs a fix
    or an explicit approval first.
    """
    found = store.find_record(record_id)
    if found is None:
        raise HTTPException(404, "הרשומה לא נמצאה.")
    batch, record = found
    if batch.status in ("queued", "processing"):
        # טפסים מוצגים כבר בזמן הקריאה (אפשר לעיין ולתקן), אבל חיבור החצאים וזיהוי טפסים מהמערכת
        # רצים רק בסוף — לכן מאשרים טופס רק אחרי שהם הסתיימו
        raise HTTPException(409, "הקריאה עוד נמשכת — אפשר לעיין ולתקן, ואת הטופס מאשרים בסיום.")
    _check_version(record, payload)
    still_open = [FIELDS_BY_KEY[k].label_he for k, f in record.fields.items() if f.state == STATE_REVIEW]
    if still_open:
        raise HTTPException(409, "יש שדות שעוד צריך לבדוק: " + ", ".join(still_open))
    unknown = [FIELDS_BY_KEY[k].label_he for k, f in record.fields.items() if _has_unknown(f.values)]
    if unknown:
        raise HTTPException(409, "יש תו שלא נקרא (?) ב: " + ", ".join(unknown) + " — יש לתקן לפני אישור הטופס.")
    broken = rules.record_problems(record)
    if broken:
        raise HTTPException(409, "אי אפשר לאשר את הטופס: " + " · ".join(w for why in broken.values() for w in why))
    waiting = _waiting_help(record_id)
    if waiting:
        raise HTTPException(409, "יש שדות שנשלחו לעזרה וממתינים לתשובה: " + ", ".join(waiting))

    for field in record.fields.values():
        field.state = STATE_APPROVED
        field.settle()
    record.verified = True
    if record.from_store:
        record.from_store = now()  # "אושר ב-" מציג את האישור האחרון

    source = next(s for s in batch.sources if s.index == record.source_index)
    page = pipeline.page_image_path(batch.batch_id, record.source_index, record.page_number)
    if page.is_file():
        gold.save(record, source, page)
        try:
            db.save_approved(record, source, page)  # נשמר לתמיד במסד הנתונים (work/forms.db)
            db.unpark(record.record_id, page)  # אושר — כבר לא ממתין
        except Exception:  # noqa: BLE001 - האישור במסך לא נופל בגלל שמירה
            if cloud_enabled():
                raise  # CloudGateway rolls back and never returns a false success.
            traceback.print_exc()
    elif cloud_enabled():
        raise HTTPException(410, "תמונת הטופס חסרה. לא ניתן לשמור אישור.")
    _changed(batch, record)
    return _serialise(batch)


# ------------------------------------------------------------ ממתינים לאישור
# נשמרים רק כשצריך: הדפדפן נסגר (הדף שולח /park), העלאה חדשה (_start), או שהשרת נסגר (shutdown).

@app.post("/api/batches/{batch_id}/park")
def park_batch(batch_id: str) -> dict:
    """Keep this upload's unapproved forms as waiting. Called when the page closes."""
    batch = store.get(batch_id)
    return {"waiting": pipeline.park_batch(batch) if batch is not None else 0}


@app.on_event("startup")
def warm_reader() -> None:
    """Load the model's library in the background — the first upload doesn't wait ~10 seconds for it."""
    if not cloud_enabled():
        threading.Thread(target=pipeline.warm_up, daemon=True).start()


@app.on_event("shutdown")
def park_everything() -> None:
    """The server is closing (the computer shuts down): nothing unapproved is lost."""
    if cloud_enabled():
        return  # Snapshots and pending forms are committed by the durable worker.
    for batch in store.all_batches():
        pipeline.park_batch(batch)


@app.get("/api/pending")
def pending_forms(q: str = "", sender: str = "") -> dict:
    """Every form waiting for approval, oldest first."""
    return db.list_pending(q=q, sender=sender)


@app.get("/api/pending/count")
def pending_count() -> dict:
    return {"count": db.pending_count()}


@app.post("/api/pending/{record_id}/open")
def open_pending_form(record_id: str) -> dict:
    """Open a waiting form in the review screen — also after a restart. No reading."""
    found = store.find_record(record_id)
    if found is not None:
        return {**_serialise(found[0]), "focus": record_id}
    batch_id = db.pending_batch_of(record_id)
    if batch_id is None:
        raise HTTPException(404, "הטופס כבר לא ממתין (ייתכן שאושר).")
    if store.get(batch_id) is not None:
        raise HTTPException(409, "הטופס כבר לא נמצא בהעלאה הפתוחה.")
    batch = db.load_pending_batch(batch_id)
    if batch is None:
        raise HTTPException(404, "הטופס לא נמצא.")
    missing = [r for r in batch.records
               if not pipeline.page_image_path(batch_id, r.source_index, r.page_number).is_file()]
    if missing:
        raise HTTPException(410, "תמונת הטופס לא נמצאה בתיקיית העבודה.")
    store.put(batch)
    return {**_serialise(batch), "focus": record_id}


def _has_unknown(values: list[str]) -> bool:
    """A "?" marks a character nobody read. Such a value can't be approved (Noa, 20/10/2026)."""
    return any("?" in v for v in values)


def _check_version(record, payload: dict | None) -> None:
    """Optimistic check: an edit made on an older copy of the form is refused,
    so two open screens never silently overwrite each other."""
    sent = (payload or {}).get("version")
    if sent is not None and int(sent) != record.version:
        raise HTTPException(409, "הטופס השתנה בינתיים (אולי בחלון אחר). המסך רוענן — נסו שוב.")


def _changed(batch: Batch, record) -> None:
    record.version += 1


# ------------------------------------------------------------ "רשומות במערכת"
# רק טפסים שאושרו, שורה לכל מספר התחייבות. המסך שולח תמיד טווח של תאריכי אישור
# (ברירת מחדל: היום, לפי השעון של הדפדפן) — לא טוענים את כל המאגר.

def _filters(start: str, end: str, q: str, sender: str, received_from: str, received_to: str) -> dict:
    if not start or not end:
        raise HTTPException(400, "חסר טווח תאריכים.")
    return {"start": start, "end": end, "q": q, "sender": sender,
            "received_from": received_from, "received_to": received_to}


@app.get("/api/saved")
def saved_rows(
    start: str = "", end: str = "", q: str = "", sender: str = "",
    received_from: str = "", received_to: str = "", limit: int = db.LIST_LIMIT,
) -> dict:
    """The approved rows of the chosen days, filtered."""
    filters = _filters(start, end, q, sender, received_from, received_to)
    try:
        found = db.list_rows(**filters, limit=max(0, min(limit, db.LIST_LIMIT)))
    except ValueError as exc:
        raise HTTPException(400, "תאריך לא תקין.") from exc
    for row in found["rows"]:
        row["breaks_rules"] = rules.row_breaks_rules(row)  # אושר לפני הכללים — מסומן, לא נמחק ולא משתנה
    return found


@app.get("/api/saved/export")
def export_saved_rows(
    format: str = "xlsx", start: str = "", end: str = "", q: str = "", sender: str = "",
    received_from: str = "", received_to: str = "", offset: int = 0,
) -> Response:
    """Everything filtered on screen, as Excel, CSV or JSON. `offset` is the
    browser's minutes from UTC, so approval times read as local time."""
    filters = _filters(start, end, q, sender, received_from, received_to)
    try:
        rows = db.rows_for_export(**filters)
    except ValueError as exc:
        raise HTTPException(400, "תאריך לא תקין.") from exc
    for row in rows:
        row["approved_local"] = _local_time(row["approved_at"], offset)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    if format == "json":
        body = json.dumps(saved_json(rows), ensure_ascii=False, indent=1).encode("utf-8")
        media, suffix = "application/json", "json"
    elif format == "csv":
        body, media, suffix = saved_csv_bytes(rows), "text/csv; charset=utf-8", "csv"
    else:
        forms = len({r["form_key"] for r in rows})
        title = f"{forms} טפסים מאושרים · {len(rows)} שורות · ייצוא {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        body = saved_workbook_bytes(rows, title)
        media, suffix = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
    filename = f"approved-{stamp}.{suffix}"
    return Response(content=body, media_type=media, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        "Cache-Control": "no-store",
    })


def _local_time(stamp: str, offset: int) -> str:
    """UTC time → the browser's local time, DD/MM/YYYY HH:MM."""
    try:
        moment = datetime.fromisoformat(stamp) - timedelta(minutes=offset)
        return moment.strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError):
        return stamp or ""


@app.post("/api/saved/{form_key}/open")
def open_saved_form(form_key: str) -> dict:
    """Open an approved form in the review screen — also after the server restarted.
    The approved forms of its upload come back with it (so "next form" works). No reading."""
    saved = db.form_of(form_key)
    if saved is None:
        raise HTTPException(404, "הטופס לא נמצא.")
    if not saved.get("record"):
        raise HTTPException(409, "הטופס הזה אושר לפני שנוספה השמירה המלאה — אפשר לראות ולייצא אותו, לא לפתוח.")
    record_id = saved["record_id"]
    found = store.find_record(record_id)
    if found is not None:
        return {**_serialise(found[0]), "focus": record_id}
    if store.get(saved["batch_id"]) is not None:
        raise HTTPException(409, "הטופס כבר לא נמצא בהעלאה הפתוחה.")
    loaded = db.load_batch(saved["batch_id"])
    if loaded is None:
        raise HTTPException(404, "הטופס לא נמצא.")
    batch, images = loaded
    for record in batch.records:
        rules.reopen_broken(record)  # אושר לפני הכללים ואינו עומד בהם — חוזר לבדיקה, הערכים לא משתנים
        page = pipeline.page_image_path(batch.batch_id, record.source_index, record.page_number)
        kept = images.get(record.record_id)
        if not page.is_file() and kept and kept.is_file():
            page.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(kept, page)
    store.put(batch)
    return {**_serialise(batch), "focus": record_id}


@app.delete("/api/saved/{form_key}")
def delete_saved_form(form_key: str) -> dict:
    """Delete one approved form for good: its rows, its approved copy and page image,
    and — when its upload is still here — the page and every close-up cut from it."""
    gone = db.delete_form(form_key)
    if gone is None:
        raise HTTPException(404, "הטופס לא נמצא.")
    found = store.find_record(gone["record_id"]) if gone.get("record_id") else None
    if found is not None:
        batch, record = found
        parts = [record, *batch.joined.pop(record.record_id, [])]  # טופס מחובר: גם שני החלקים
        batch.records = [r for r in batch.records if r.record_id != record.record_id]
        batch.pages = [p for p in batch.pages if p.get("record_id") != record.record_id]
        for part in parts:
            _remove_page_files(batch, part)
        return {"deleted": True, "batch": _serialise(batch)}
    if gone.get("source"):
        source = json.loads(gone["source"])
        folder = store.BATCHES_DIR / str(gone["batch_id"]) / "sources" / str(source["index"])
        if folder.is_dir():
            for path in (folder / "pages").glob(f"page-{gone['page_number']:03d}*"):
                path.unlink(missing_ok=True)
            for path in (folder / "crops").glob(f"{gone['page_number']:03d}-*"):
                path.unlink(missing_ok=True)
    return {"deleted": True, "batch": None}


def _remove_page_files(batch: Batch, record) -> None:
    """The page image and everything cut from it; the uploaded file too, once no
    other form of the batch still comes from it."""
    folder = store.source_dir(batch.batch_id, record.source_index)
    for path in (folder / "pages").glob(f"page-{record.page_number:03d}*"):
        path.unlink(missing_ok=True)
    for path in (folder / "crops").glob(f"{record.page_number:03d}-*"):
        path.unlink(missing_ok=True)
    others = [r for r in batch.records if r.source_index == record.source_index]
    others += [p for pair in batch.joined.values() for p in pair if p.source_index == record.source_index]
    if not others:
        shutil.rmtree(folder, ignore_errors=True)


@app.get("/api/approved")
def approved_forms() -> list[dict]:
    """The approved forms saved in the database — read only."""
    return db.list_approved()


@app.patch("/api/records/{record_id}")
def update_record(record_id: str, payload: dict) -> dict:
    found = store.find_record(record_id)
    if found is None:
        raise HTTPException(404, "הרשומה לא נמצאה.")
    batch, record = found
    _check_version(record, payload)

    key = payload.get("field")
    if key not in record.fields:
        raise HTTPException(400, "שדה לא מוכר.")
    field = record.fields[key]

    if "values" in payload:
        values = [str(v).strip() for v in payload["values"] if str(v).strip()]
        broken = rules.problems(key, values)
        if broken:
            # ערך שאינו עומד בכללים לא נשמר בכלל (החלטת נועה, 21/10/2026)
            raise HTTPException(422, " · ".join(broken))
        field.remember()
        if values != field.values and record.from_store:
            # טופס שהגיע מהמערכת ושונה — חייב "אישור הטופס" מחדש (החלטת נועה, 20/10/2026)
            record.verified = False
        if len(values) != len(field.values):
            field.value_regions = []  # המיקומים לפי ערך כבר לא תואמים
        field.values = values
        # תיקון אנושי מנקה את הדגלים האוטומטיים — האדם ראה את המקור.
        field.settle()
        field.state = STATE_APPROVED if values else STATE_REVIEW
        if not values:
            field.reasons = ["missing"]
        elif _has_unknown(values):
            # עדיין יש "?" — לא נחשב תיקון גמור (החלטת נועה, 20/10/2026)
            field.state = STATE_REVIEW
            field.reasons = ["unknown_char"]
    elif payload.get("approve"):
        if _has_unknown(field.values):
            raise HTTPException(409, f"ב{FIELDS_BY_KEY[key].label_he} יש תו שלא נקרא (?) — יש לתקן אותו לפני אישור.")
        broken = rules.problems(key, field.values)
        if broken:
            raise HTTPException(409, "אי אפשר לאשר: " + " · ".join(broken))
        if _waiting_help(record_id, key):
            raise HTTPException(409, "השדה נשלח לעזרה וממתין לתשובה — אפשר לבטל את הבקשה ואז לאשר.")
        field.remember()
        field.state = STATE_APPROVED
        field.settle()

    _decided(record_id, key)
    _changed(batch, record)
    return _serialise(batch)


@app.post("/api/records/{record_id}/undo")
def undo_field(record_id: str, payload: dict) -> dict:
    """Put one field back the way it was before the last fix or approval."""
    found = store.find_record(record_id)
    if found is None:
        raise HTTPException(404, "הרשומה לא נמצאה.")
    batch, record = found
    _check_version(record, payload)
    field = record.fields.get(payload.get("field"))
    if field is None:
        raise HTTPException(400, "שדה לא מוכר.")
    if not field.undo():
        raise HTTPException(409, "אין מה לבטל בשדה הזה.")
    record.verified = False
    _changed(batch, record)
    return _serialise(batch)


@app.get("/api/batches/{batch_id}/export/preview")
def export_preview(batch_id: str) -> dict:
    """The rows the Excel file will contain — shown on screen before the download."""
    return excel_preview(_require(batch_id))


@app.post("/api/batches/{batch_id}/export")
def export_batch(batch_id: str) -> Response:
    batch = _require(batch_id)
    if not batch.records:
        raise HTTPException(409, "אין עדיין רשומות לייצוא.")
    # הייצוא רק אחרי שהקריאה הסתיימה וכל שדה כתום תוקן או אושר במפורש.
    if batch.status in ("queued", "processing"):
        raise HTTPException(409, "הקריאה עוד לא הסתיימה.")
    if batch.review_total:
        raise HTTPException(409, f"יש עדיין {batch.review_total} שדות לבדיקה.")
    # וגם כל טופס אושר במפורש ("אישור הטופס") — תיקון שדות לבדו אינו אישור
    unverified = sum(1 for r in batch.records if not r.verified)
    if unverified:
        raise HTTPException(409, f"נשארו {unverified} טפסים לאישור.")
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


FULL_NAME = "full_name"
SAME_LINE = 0.08  # שתי תיבות השם "באותו אזור" — עד 8% מגובה הדף בין המרכזים


def full_name_region(record) -> tuple | None:
    """One box around first and last name together, when both sit in the same area
    — the reader sometimes swaps them, so the zoom shows both side by side."""
    first, last = record.fields["first_name"].region, record.fields["last_name"].region
    if not first or not last:
        return None
    if abs((first[1] + first[3]) / 2 - (last[1] + last[3]) / 2) > SAME_LINE:
        return None
    return (min(first[0], last[0]), min(first[1], last[1]), max(first[2], last[2]), max(first[3], last[3]))


def _require(batch_id: str) -> Batch:
    batch = store.get(batch_id)
    if batch is None:
        raise HTTPException(404, "האצווה לא נמצאה. ייתכן שהשרת הופעל מחדש.")
    return batch


def _serialise(batch: Batch) -> dict:
    helps = _help_of(batch)
    return {
        "batch_id": batch.batch_id,
        "display_name": batch.display_name,
        "status": batch.status,
        "error": batch.error,
        "total_pages": batch.total_pages,
        "processed_pages": batch.processed_pages,
        "skipped_pages": batch.skipped_pages,
        "found_pages": batch.found_pages,
        "expected_pages": batch.expected_pages,
        "budget_skipped_pages": batch.budget_skipped_pages,
        "review_total": batch.review_total,
        "unverified": sum(1 for r in batch.records if not r.verified),
        "warnings": batch.warnings,
        "cost_usd": batch.cost_usd,
        "seconds": batch.seconds,
        "pages": batch.pages,
        "sources": [
            {
                "index": s.index,
                "filename": s.filename,
                "kind": s.kind,
                "page_count": s.page_count,
                "error": s.error,
            }
            for s in batch.sources
            if not s.joined  # התמונה המחוברת אינה קובץ שהועלה
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
                "joined": r.record_id in batch.joined,  # חובר משני חצאים — אפשר להפריד
                # החלקים שמהם נוצר — מי שצפתה בחלק בזמן הקריאה עוברת לטופס המחובר, לא לטופס אחר
                "joined_from": [part.record_id for part in batch.joined.get(r.record_id, [])],
                "split_from_joined": r.record_id in batch.split_pairs,  # אפשר "חבר בחזרה"
                "can_join": halves.is_partial(batch, r),  # חלק של טופס חתוך — אפשר לחבר ידנית
                "version": r.version,
                "from_store": r.from_store,  # הערכים מהמערכת (אושר בעבר) — מועד האישור השמור
                "split_from_page": r.record_id in batch.page_splits,  # חלק מעמוד שפוצל — "בטל פיצול"
                "full_name_crop": full_name_region(r) is not None,
                "fields": {
                    key: {
                        "values": f.values,
                        "state": f.state,
                        "reasons": [REASON_TEXT.get(c, c) for c in f.reasons],
                        "reason_codes": f.reasons,
                        # האזור שנחתך בפועל — מסומן בריבוע על העמוד המלא
                        "crop_box": list(padded(f.region)) if f.region else None,
                        "region_note": f.region_note,
                        # תיבה לכל ערך (ריקה כשאין מיקום נפרד לכל ערך)
                        "crop_boxes": [list(padded(r)) if r else None for r in f.value_regions]
                        if len(f.value_regions) == len(f.values) and len(f.values) > 1 else [],
                        "can_undo": bool(f.history),
                        # למה אי אפשר לאשר את הערך הזה (rules.py) — ריק כשאפשר
                        "blocked": rules.problems(key, f.values),
                        "help": _help_view(helps.get((r.record_id, key))),  # בקשת עזרה פעילה לשדה
                    }
                    for key, f in r.fields.items()
                },
            }
            for r in batch.records
        ],
        "states": {"ok": STATE_OK, "review": STATE_REVIEW, "approved": STATE_APPROVED},
    }
