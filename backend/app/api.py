"""HTTP surface. Thin on purpose: validate, delegate, serialise.

Every browser gets a secret session cookie; every batch, record, page and crop
is served only to the session that uploaded it — anything else is "not found".
With ACCESS_CODE set (public deployment) nothing but the login page and the
health check answers before the code is given.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import secrets
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image

from app import db, gold, pipeline, store
from app.config import PROJECT_ROOT, SETTINGS
from app.crops import crop_field, padded
from app.excel import neutralize, page_label, preview as excel_preview, records_workbook_bytes, workbook_bytes
from app.fields import FIELDS, FIELDS_BY_KEY
from app.files import ACCEPTED_LABEL, sniff_kind
from app.models import (
    STATE_APPROVED,
    STATE_OK,
    STATE_REVIEW,
    Batch,
    SourceFile,
    new_id,
)
from app.pages import ink_enhanced
from app.validators import REASON_TEXT

WEB_DIR = Path(__file__).resolve().parent / "web"
# טופס לדוגמה לכפתור "נסו עם טופס דוגמה". מחוץ ל-Git (samples/ מוחרגת); אם אין — אין כפתור.
SAMPLE_PATH = PROJECT_ROOT / "samples" / "demo-form.pdf"
THUMB_WIDTH = 240
SESSION_COOKIE = "tl_session"
ACCESS_COOKIE = "tl_access"
SESSION_DAYS = 30


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # אחרי הפעלה מחדש: כל האצוות חוזרות מהמסד, ואלה שנקראו באמצע — ממשיכות.
    for batch in store.restore():
        pipeline.resume(batch)
    yield


app = FastAPI(
    title="טופס למידע — API",
    description="קליטת טפסי התחייבות: העלאה, רשומות, תיקון ואישור, ייצוא. "
    "כל משאב שייך ל-session של הדפדפן שיצר אותו.",
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


# ------------------------------------------------------------ session, קוד גישה, Origin
def _access_token() -> str:
    return hashlib.sha256(f"tl-access:{SETTINGS.access_code}".encode()).hexdigest()


def _has_access(request: Request) -> bool:
    if not SETTINGS.access_code:
        return True
    return hmac.compare_digest(request.cookies.get(ACCESS_COOKIE, ""), _access_token())


def _secure(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return proto == "https"


OPEN_PATHS = ("/healthz", "/login")


@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    # שינוי מצב מאתר אחר (CSRF) — נחסם. Origin חסר (כלי שורת פקודה) מותר.
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host", ""):
            return JSONResponse({"detail": "בקשה ממקור לא מורשה."}, status_code=403)

    if not path.startswith(OPEN_PATHS) and not path.startswith("/static/") and not _has_access(request):
        if path.startswith("/api/"):
            return JSONResponse({"detail": "נדרש קוד גישה."}, status_code=401)
        return RedirectResponse("/login", status_code=303)

    sid = request.cookies.get(SESSION_COOKIE, "")
    fresh = len(sid) < 32
    if fresh:
        sid = secrets.token_urlsafe(32)
    request.state.sid = sid
    response = await call_next(request)
    if fresh:
        response.set_cookie(SESSION_COOKIE, sid, max_age=SESSION_DAYS * 86400, httponly=True,
                            secure=_secure(request), samesite="lax", path="/")
    return response


def _sid(request: Request) -> str:
    return getattr(request.state, "sid", "")


LOGIN_PAGE = """<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>כניסה</title>
<link rel="stylesheet" href="/static/app.css"></head><body class="loginpage">
<main class="loginbox card"><h1>קליטת טפסי התחייבות</h1>
<p>המערכת מיועדת לטפסי הדוגמה של האתגר. נא להזין את קוד הגישה.</p>
<form method="post" action="/login"><label for="code">קוד גישה</label>
<input id="code" name="code" type="password" autocomplete="current-password" required autofocus>
{error}<button class="primary big" type="submit">כניסה</button></form></main></body></html>"""


@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_page() -> HTMLResponse:
    return HTMLResponse(LOGIN_PAGE.replace("{error}", ""), headers={"Cache-Control": "no-store"})


@app.post("/login", include_in_schema=False)
async def login(request: Request) -> Response:
    form = parse_qs((await request.body()).decode("utf-8", "replace"))
    code = (form.get("code") or [""])[0]
    if SETTINGS.access_code and hmac.compare_digest(code.strip(), SETTINGS.access_code):
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(ACCESS_COOKIE, _access_token(), max_age=SESSION_DAYS * 86400, httponly=True,
                            secure=_secure(request), samesite="lax", path="/")
        return response
    page = LOGIN_PAGE.replace("{error}", '<p class="loginerr" role="alert">הקוד שגוי.</p>')
    return HTMLResponse(page, status_code=401)


# ------------------------------------------------------------ דפים
@app.get("/healthz", include_in_schema=False)
def health() -> dict:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index() -> HTMLResponse:
    # גרסה לפי זמן השינוי של הקבצים — הדפדפן לא יציג CSS/JS ישנים אחרי עדכון.
    version = str(max(int(p.stat().st_mtime) for p in WEB_DIR.glob("*.*") if p.suffix in (".js", ".css")))
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8").replace("{{v}}", version)
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
        "brand": SETTINGS.brand,
        "usd_ils": SETTINGS.usd_ils_rate,
        "manual_seconds_per_form": SETTINGS.manual_seconds_per_form,
        "fields": [
            {"key": f.key, "label": f.label_he, "multi": f.multi, "hint": f.hint_he}
            for f in FIELDS
        ],
    }


# ------------------------------------------------------------ העלאה ועיבוד
@app.post("/api/batches")
async def create_batch(request: Request, files: list[UploadFile]) -> dict:
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
    _check_daily_budget()
    received = [(upload.filename or "", await upload.read()) for upload in files]
    return _start(received, _sid(request))


@app.post("/api/batches/sample")
def create_sample_batch(request: Request) -> dict:
    """The demo form — already read once, so it comes back from the saved answers, free."""
    if not SAMPLE_PATH.is_file():
        raise HTTPException(404, "אין טופס דוגמה במערכת הזו.")
    return _start([("טופס דוגמה.pdf", SAMPLE_PATH.read_bytes())], _sid(request))


def _check_daily_budget() -> None:
    cap = SETTINGS.max_daily_cost_usd
    if cap > 0 and store.cost_today() >= cap:
        raise HTTPException(429, f"הושגה תקרת העלות היומית של השרת (${cap:.2f}). אפשר לנסות שוב מחר.")


def _start(received: list[tuple[str, bytes]], session_id: str = "") -> dict:
    batch = Batch(batch_id=new_id("bat"), session_id=session_id)
    for position, (name, data) in enumerate(received, start=1):
        batch.sources.append(_store_source(batch.batch_id, position, name, data))

    if not batch.accepted_sources:
        store.delete(batch.batch_id)
        reasons = sorted({s.error for s in batch.sources if s.error})
        raise HTTPException(415, "אף קובץ לא התקבל. " + " ".join(reasons))

    store.put(batch)
    store.persist(batch)
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


@app.get("/api/batches")
def list_batches(request: Request) -> list[dict]:
    """This session's uploads, newest first — to return to any of them."""
    sid = _sid(request)
    batches = [b for b in store.all_batches() if b.session_id == sid]
    batches.sort(key=lambda b: b.created_at, reverse=True)
    return [
        {"batch_id": b.batch_id, "display_name": b.display_name, "status": b.status,
         "created_at": b.created_at, "forms": len(b.records), "review_total": b.review_total,
         "unverified": sum(1 for r in b.records if not r.verified)}
        for b in batches
    ]


@app.get("/api/batches/{batch_id}")
def read_batch(request: Request, batch_id: str) -> dict:
    return _serialise(_require(request, batch_id))


@app.get("/api/batches/{batch_id}/sources/{source_index}/pages/{page_number}")
def page_image(request: Request, batch_id: str, source_index: int, page_number: int,
               ink: bool = False) -> FileResponse:
    _require(request, batch_id)
    path = pipeline.page_image_path(batch_id, source_index, page_number)
    if not path.is_file():
        raise HTTPException(404, "העמוד לא נמצא.")
    if ink:
        path = ink_enhanced(path)  # גרסת "דיו" של העמוד המלא — לתקריב שגוררים. מקומי, בלי עלות
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/api/batches/{batch_id}/sources/{source_index}/pages/{page_number}/thumb")
def page_thumb(request: Request, batch_id: str, source_index: int, page_number: int) -> FileResponse:
    """A small copy of the page, for the live progress tiles. Made once, locally."""
    _require(request, batch_id)
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
    request: Request,
    batch_id: str,
    source_index: int,
    page_number: int,
    field_key: str,
    ink: bool = False,
    i: int | None = None,
    rec: str = "",
) -> FileResponse:
    """The enlarged cut-out of one field, for the lower half of the split panel.
    `rec` picks the record when one page held several forms."""
    batch = _require(request, batch_id)
    if field_key not in FIELDS_BY_KEY and field_key != FULL_NAME:
        raise HTTPException(404, "שדה לא מוכר.")
    on_page = [r for r in batch.records if r.source_index == source_index and r.page_number == page_number]
    record = next((r for r in on_page if r.record_id == rec), on_page[0] if on_page else None)
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
                                (field_key if i is None else f"{field_key}-{i}")
                                + (f"-f{record.sub_form}" if record.sub_form else ""))
    # המיקום בשם הקובץ: אחרי קריאה חוזרת או תיקון מיקום — תקריב חדש, לא הישן
    place = hashlib.sha256(repr(tuple(round(v, 4) for v in region)).encode()).hexdigest()[:8]
    target = target.with_name(f"{target.stem}-{place}.png")
    if ink:
        # אותה הגדלה מגרסת "דיו מודגש": בלי מרקרים, כתב היד שחור. מקומי, בלי עלות.
        page = ink_enhanced(page)
        target = target.with_name(target.stem + ".ink.png")
    return FileResponse(
        crop_field(page, region, target), media_type="image/png", headers={"Cache-Control": "no-store"}
    )


# ------------------------------------------------------------ תיקון ואישור
@app.post("/api/records/{record_id}/reread")
def reread_record(request: Request, record_id: str) -> dict:
    """Read one failed form again. Costs one reading — the screen asks the person
    first; nothing calls this on its own."""
    batch, _ = _require_record(request, record_id)
    _check_daily_budget()
    try:
        pipeline.reread(batch.batch_id, record_id)
    except pipeline.RereadRefused as exc:
        raise HTTPException(409, str(exc)) from exc
    return _serialise(batch)


@app.post("/api/records/{record_id}/verify")
def verify_record(request: Request, record_id: str) -> dict:
    """The secretary checked every field of this form against the original.

    Every field becomes green, and the form is saved as an answer-key entry for
    measurement. Refused while any field is still orange: each one needs a fix
    or an explicit approval first.
    """
    batch, record = _require_record(request, record_id)
    still_open = [FIELDS_BY_KEY[k].label_he for k, f in record.fields.items() if f.state == STATE_REVIEW]
    if still_open:
        raise HTTPException(409, "יש שדות שעוד צריך לבדוק: " + ", ".join(still_open))

    for field in record.fields.values():
        field.state = STATE_APPROVED
        field.settle()
    record.verified = True
    record.touch()
    store.persist(batch)

    source = next(s for s in batch.sources if s.index == record.source_index)
    page = pipeline.page_image_path(batch.batch_id, record.source_index, record.page_number)
    if page.is_file():
        gold.save(record, source, page)
        try:
            db.save_approved(record, source, page)  # נשמר לתמיד במסד הנתונים (work/forms.db)
        except Exception:  # noqa: BLE001 - האישור במסך לא נופל בגלל שמירה
            traceback.print_exc()
    return _serialise(batch)


@app.get("/api/approved", include_in_schema=False)
def approved_forms() -> list[dict]:
    """The approved forms saved in the database — read only (local tools)."""
    return db.list_approved()


@app.patch("/api/records/{record_id}")
def update_record(request: Request, record_id: str, payload: dict) -> dict:
    """Fix or approve one field. With `revision`, a stale screen gets 409 instead
    of silently overwriting a newer change."""
    batch, record = _require_record(request, record_id)
    expected = payload.get("revision")
    if expected is not None and int(expected) != record.revision:
        raise HTTPException(409, "הטופס השתנה בינתיים (אולי בחלון אחר). המסך רוענן — נסו שוב.")

    key = payload.get("field")
    if key not in record.fields:
        raise HTTPException(400, "שדה לא מוכר.")
    field = record.fields[key]

    if "values" in payload:
        field.remember()
        values = [str(v).strip() for v in payload["values"] if str(v).strip()]
        if len(values) != len(field.values):
            field.value_regions = []  # המיקומים לפי ערך כבר לא תואמים
        field.values = values
        # תיקון אנושי מנקה את הדגלים האוטומטיים — האדם ראה את המקור.
        field.settle()
        field.state = STATE_APPROVED if values else STATE_REVIEW
        if not values:
            field.reasons = ["missing"]
    elif payload.get("approve"):
        field.remember()
        field.state = STATE_APPROVED
        field.settle()
    else:
        raise HTTPException(400, "חסר תיקון או אישור.")

    record.touch()
    store.persist(batch)
    return _serialise(batch)


@app.post("/api/records/{record_id}/undo")
def undo_field(request: Request, record_id: str, payload: dict) -> dict:
    """Put one field back the way it was before the last fix or approval."""
    batch, record = _require_record(request, record_id)
    field = record.fields.get(payload.get("field"))
    if field is None:
        raise HTTPException(400, "שדה לא מוכר.")
    if not field.undo():
        raise HTTPException(409, "אין מה לבטל בשדה הזה.")
    record.verified = False
    record.touch()
    store.persist(batch)
    return _serialise(batch)


# ------------------------------------------------------------ רשומות (מסך "רשומות" ו-API)
@app.get("/api/records")
def list_records(request: Request, q: str = "", status: str = "", sender: str = "",
                 date_from: str = "", date_to: str = "", limit: int = 500) -> dict:
    """Every record this session has in the system — search, filter, approved vs
    waiting. One row per logical form; several numbers stay a list."""
    sid = _sid(request)
    rows = db.search_records(sid, q=q, status=status, sender=sender,
                             date_from=date_from, date_to=date_to, limit=limit)
    return {
        "records": [_public_row(r) for r in rows],
        "counts": db.record_counts(sid),
        "senders": sorted({r["sending_entity"] for r in db.search_records(sid, limit=5000)
                           if r["sending_entity"]}),
    }


@app.get("/api/records/export")
def export_records(request: Request, format: str = "xlsx", q: str = "", status: str = "",
                   sender: str = "", date_from: str = "", date_to: str = "") -> Response:
    """The filtered records as Excel, CSV or JSON. Identifiers stay text; formula
    characters are neutralised in Excel and CSV."""
    rows = db.search_records(_sid(request), q=q, status=status, sender=sender,
                             date_from=date_from, date_to=date_to, limit=5000)
    rows = [_public_row(r) for r in rows]
    if format == "json":
        body = json.dumps(rows, ensure_ascii=False, indent=1).encode("utf-8")
        return _download(body, "records.json", "application/json; charset=utf-8")
    if format == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([f.label_he for f in FIELDS] + ["מצב", "קובץ מקור", "עמוד"])
        for row in rows:
            # כמו באקסל: שורה לכל מספר התחייבות; מספרים מרכזיים נשארים יחד בתא אחד
            for number in row["commitment_numbers"] or [""]:
                cells = [number if f.key == "commitment_numbers" else
                         (" | ".join(row[f.key]) if f.multi else row[f.key]) for f in FIELDS]
                writer.writerow([neutralize(c) for c in cells]
                                + [STATUS_TEXT.get(row["status"], row["status"]),
                                   neutralize(row["source_name"]), row["page_number"]])
        # BOM — כדי שאקסל יפתח עברית נכון
        return _download(("﻿" + buffer.getvalue()).encode("utf-8"), "records.csv", "text/csv; charset=utf-8")
    return _download(records_workbook_bytes(rows), "records.xlsx",
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


STATUS_TEXT = {"approved": "מאושר", "ready": "ממתין לאישור", "review": "לבדיקה", "failed": "הקריאה נכשלה"}


def _public_row(row: dict) -> dict:
    """A records-screen row: the seven fields (lists for the multi ones), status, source."""
    out = {f.key: row[f.key] for f in FIELDS}
    for f in FIELDS:
        if f.multi:
            out[f.key] = list(row[f.key])
        else:
            out[f.key] = row[f.key] or ""
    out.update({k: row[k] for k in ("record_id", "batch_id", "status", "review_count", "source_name",
                                    "page_number", "revision", "created_at", "updated_at")})
    return out


def _download(body: bytes, filename: str, media_type: str) -> Response:
    return Response(content=body, media_type=media_type, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        "Cache-Control": "no-store",
    })


# ------------------------------------------------------------ ייצוא אצווה
@app.get("/api/batches/{batch_id}/export/preview")
def export_preview(request: Request, batch_id: str) -> dict:
    """The rows the Excel file will contain — shown on screen before the download."""
    return excel_preview(_require(request, batch_id))


@app.post("/api/batches/{batch_id}/export")
def export_batch(request: Request, batch_id: str) -> Response:
    batch = _require(request, batch_id)
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
    return _download(workbook_bytes(batch), f"commitments-{batch_id}.xlsx",
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.delete("/api/batches/{batch_id}")
def delete_batch(request: Request, batch_id: str) -> dict:
    """Delete the upload and everything made from it: pages, crops, records."""
    _require(request, batch_id)
    store.delete(batch_id)
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


def _require(request: Request, batch_id: str) -> Batch:
    batch = store.get(batch_id, _sid(request))
    if batch is None:
        raise HTTPException(404, "האצווה לא נמצאה.")
    return batch


def _require_record(request: Request, record_id: str):
    found = store.find_record(record_id, _sid(request))
    if found is None:
        raise HTTPException(404, "הרשומה לא נמצאה.")
    return found


def _duplicates(batch: Batch, record) -> list[dict]:
    if not batch.session_id:
        return []
    try:
        return db.find_duplicates(record, batch.session_id)
    except Exception:  # noqa: BLE001 - בדיקת כפילות לא מפילה את המסך
        traceback.print_exc()
        return []


def _serialise(batch: Batch) -> dict:
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
        "model": SETTINGS.model,
        "effort": SETTINGS.claude_effort,
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
                "status": r.status,
                "revision": r.revision,
                "straightened": r.straightened,
                "sub_form": r.sub_form,
                "sub_forms": r.sub_forms,
                "extra_pages": r.extra_pages,
                "page_label": page_label(r),
                "duplicates": _duplicates(batch, r),
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
                    }
                    for key, f in r.fields.items()
                },
            }
            for r in batch.records
        ],
        "states": {"ok": STATE_OK, "review": STATE_REVIEW, "approved": STATE_APPROVED},
    }
