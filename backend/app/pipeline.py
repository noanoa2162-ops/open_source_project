"""Uploads → pages → the reader → validated records.

A batch is one drop, however many files it contained. Every file is flattened
into pages and every page becomes one record. One strong model (Claude Opus,
deep thinking) reads each page on its own; whatever it is unsure of goes to
the secretary as orange.

Runs on a worker thread so the browser gets an immediate response and can poll
for progress. One page failing never stops the rest.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from PIL import Image

from app.config import BATCHES_DIR, SETTINGS, WORK_DIR
from app.consensus import NAME_KEYS, combine, merge_names
from app.crops import crop_field
from app.halves import join_halves
from app.known import apply_known, from_saved, saved_for_page
from app.locate import check_regions, snap_to_ink
from app.models import (
    PAGE_DONE,
    PAGE_FAILED,
    PAGE_QUEUED,
    PAGE_READING,
    PAGE_SKIPPED,
    STATE_REVIEW,
    Batch,
    FormRecord,
    Region,
    SourceFile,
)
from app.pages import count_pages, render_pages, turn_note, turn_upside_down
from app.providers.base import ExtractionError, ExtractionProvider, RawExtraction
from app.providers.reading import PROMPT_VERSION, NameCheck, union
from app.store import get, source_dir
from app.validators import validate_record

Page = tuple[SourceFile, int, Path]

# אותו דף, אותו מודל, אותו פרומפט → אותה תשובה בלי לשלם שוב.
# בזיכרון לגישה מהירה, ובקובץ ב-work/read_cache כדי שיישמר גם אחרי הפעלה מחדש.
_READ_CACHE: dict[tuple, RawExtraction] = {}
_CACHE_LOCK = threading.Lock()
READ_CACHE_DIR = WORK_DIR / "read_cache"
# קריאה חוזרת של טופס שנכשל (רק אחרי אישור במסך) — אחת בכל פעם לכל טופס
_REREAD_LOCK = threading.Lock()


def build_reader() -> tuple[ExtractionProvider | None, str]:
    """The reader configured in .env, or None and a visible reason why not."""
    from app.providers.claude_vision import ClaudeExtractionProvider

    try:
        return ClaudeExtractionProvider(model=SETTINGS.reader_model), ""
    except ExtractionError as exc:
        return None, str(exc)


class Reader:
    """The reader for one batch. After an account-level failure (no credit, bad
    key) it is retired, so the remaining pages stop burning calls."""

    def __init__(self, provider: ExtractionProvider) -> None:
        self.provider = provider
        self.retired = ""
        self._lock = threading.Lock()

    @property
    def model(self) -> str:
        return self.provider.model

    def retire(self, reason: str) -> None:
        with self._lock:
            self.retired = self.retired or reason

    @property
    def exhausted(self) -> bool:
        return bool(self.retired)


def start(batch_id: str) -> None:
    threading.Thread(target=_run, args=(batch_id,), daemon=True).start()


def _run(batch_id: str) -> None:
    batch = get(batch_id)
    if batch is None:
        return
    try:
        _process(batch_id)
    except Exception:  # noqa: BLE001 - a worker thread must never die silently
        batch.status = "failed"
        batch.error = "העיבוד נכשל. פרטים בלוג השרת."
        traceback.print_exc()


def _process(batch_id: str) -> None:
    batch = get(batch_id)
    assert batch is not None
    batch.status = "processing"
    started = time.perf_counter()

    # שלב 1: לרנדר הכול תחילה, כדי שמספר הטפסים יהיה ידוע לפני הקריאה הראשונה.
    pages = _render_all(batch_id, batch)
    batch.total_pages = len(pages)
    batch.pages = [
        {"source_index": s.index, "page_number": n, "status": PAGE_QUEUED, "record_id": ""}
        for s, n, _ in pages
    ]
    if not pages:
        batch.status = "failed"
        batch.error = _why_nothing_to_do(batch.sources)
        return

    provider, problem = build_reader()
    if provider is None:
        batch.status = "failed"
        batch.error = problem or "אין מודל זמין לקריאה."
        return

    # שלב 2: כמה טפסים במקביל.
    reader = Reader(provider)
    _extract_all(reader, batch, pages)
    batch.seconds = round(time.perf_counter() - started, 1)

    if reader.retired:
        batch.warn(f"{reader.model}: {reader.retired}")
        if len(batch.records) < len(pages):
            batch.status = "failed" if not batch.records else "partial"
            batch.error = reader.retired
            return

    # טופס שצולם בשני חצאים → טופס אחד. מקומי ובחינם; תקלה כאן לא מפילה את הקריאה.
    try:
        join_halves(batch)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
    # טופס שכבר אושר מקובץ אחר (אותו מספר התחייבות ות"ז) — הערכים מהמערכת. אחרי החיבור,
    # כדי שחצי תחתון לא יקבל שם ות"ז מהמאגר לפני שהתחבר.
    apply_known(batch.records)
    # עמוד עם שני טפסים — לא מפצלים לבד: המזכירה מחליטה, בכפתור, בחינם (twoforms.py, החלטת נועה 20/10/2026)

    _finish(batch, len(pages))


def _extract_all(reader: Reader, batch: Batch, pages: list[Page]) -> list[FormRecord | None]:
    """Run every page through the reader with a small pool of threads.

    Each record joins `batch.records` the moment it is ready, so the screen can
    show it while the rest are still being read; at the end they are put back in
    page order. Returns the records in page order too — a slot is None when that
    page was never attempted (the reader was retired, or the cost cap was reached).
    Only the calling thread adds records to `batch`; workers only mark their own
    page's status.
    """
    stop = threading.Event()
    spent = [0.0]
    spent_lock = threading.Lock()
    results: list[FormRecord | None] = [None] * len(pages)

    def work(index: int) -> tuple[int, FormRecord | None, str]:
        if stop.is_set() or reader.exhausted:
            return index, None, "stopped"
        with spent_lock:
            cap = SETTINGS.max_cost_per_batch_usd  # 0 = בלי תקרה
            if cap > 0 and spent[0] >= cap:
                return index, None, "budget"
        source, page_number, image_path = pages[index]
        if index < len(batch.pages):
            batch.pages[index]["status"] = PAGE_READING
        record = read_form(reader, image_path, batch.batch_id, source, page_number)
        if record.upside_down:
            show_upright(record, image_path)
        with spent_lock:
            spent[0] += record.cost_usd or 0.0
        if reader.exhausted:
            # חייב להיעשות כאן ולא בתהליך הראשי, אחרת עובד כבר תפס את העמוד הבא.
            stop.set()
        return index, record, ""

    with ThreadPoolExecutor(max_workers=SETTINGS.max_parallel_calls) as pool:
        futures = [pool.submit(work, i) for i in range(len(pages))]
        for future in as_completed(futures):
            index, record, why = future.result()
            status = batch.pages[index] if index < len(batch.pages) else {}
            if record is None:
                status["status"] = PAGE_SKIPPED
                if why == "budget":
                    batch.budget_skipped_pages += 1
                continue
            results[index] = record
            batch.records.append(record)  # מופיע במסך מיד, לא רק בסוף
            status.update(status=PAGE_FAILED if record.error else PAGE_DONE, record_id=record.record_id)
            batch.processed_pages += 1

    # בסוף — חזרה לסדר העמודים, כמו שהם הופיעו בקבצים
    order = {id(record): i for i, record in enumerate(results) if record is not None}
    batch.records.sort(key=lambda record: order.get(id(record), len(order)))

    if batch.budget_skipped_pages:
        batch.warn(
            f"נעצר בתקרת התקציב (${SETTINGS.max_cost_per_batch_usd:.2f}): "
            f"{batch.budget_skipped_pages} טפסים לא עובדו. ניתן לשנות את MAX_COST_PER_BATCH_USD."
        )
    return results


def read_form(
    reader: Reader, image_path: Path, batch_id: str, source: SourceFile, page_number: int,
    use_saved: bool = True,
) -> FormRecord:
    """One page → one record: the reader's answer, then the local rules.

    `use_saved=False` — always the reader, never the values a person approved before.
    The measurement needs that: it compares the reader against those very values."""
    started = time.perf_counter()
    record = FormRecord.empty(batch_id, source, page_number)
    record.low_resolution = _is_low_resolution(image_path)
    record.straightened = turn_note(image_path)

    # אותו עמוד כבר אושר בעבר: הערכים מהמערכת, בלי מודל ובלי עלות
    saved = saved_for_page(image_path) if use_saved else None
    if saved is not None:
        from_saved(record, saved, same_page=True)
        record.cost_usd = 0.0
        record.seconds = round(time.perf_counter() - started, 1)
        return record

    try:
        reading = _read(reader.provider, image_path, source, page_number)
    except ExtractionError as exc:
        if exc.fatal:
            reader.retire(str(exc))
        record.seconds = round(time.perf_counter() - started, 1)
        record.error = f"{reader.model}: {exc}"
        for field in record.fields.values():
            field.flag("missing")
        log_failure(batch_id, source, page_number, exc)
        return record

    record.seconds = round(time.perf_counter() - started, 1)
    usage = reading.usage
    record.cost_usd = usage.cost_usd if usage and usage.cost_usd is not None else 0.0
    apply_reading(record, reading, image_path)
    return record


def apply_reading(record: FormRecord, reading: RawExtraction, image_path: Path) -> None:
    """The reader's answer → the record, through every local rule. Shared with the
    free measurement, so it scores exactly what the secretary would see."""
    combine(record, reading)
    check_regions(record, image_path)  # מיקומי התיבות מול העמוד עצמו — מקומי, בלי עלות
    snap_to_ink(record, image_path)  # תיבות של כתב יד — נצמדות לדיו הכחול שבטופס
    # קריאה שנייה של השם מתקריב — כבויה (18/10/2026, החלטת נועה): עלתה 3–6¢ לטופס ובמדידה
    # על 3 טפסים שיפרה אחד, לא שינתה אחד והחמירה אחד. הפונקציה נשארת: look_again_at_names.
    validate_record(record)


def saved_readings(image_path: Path, sha256: str = "", page_number: int = 0) -> list[tuple[str, RawExtraction]]:
    """Every answer already paid for this page, from any version of the instructions:
    (prompt_version, reading), newest version first when it is the current one. Free."""
    digest = _page_digest(image_path)
    found: list[tuple[str, RawExtraction]] = []
    if not READ_CACHE_DIR.is_dir():
        return found
    for path in READ_CACHE_DIR.glob("*.json"):
        try:
            key = json.loads(path.read_text(encoding="utf-8")).get("key") or []
        except (OSError, ValueError):
            continue
        by_page = len(key) >= 5 and key[0] == "page" and key[1] == digest
        by_file = bool(sha256) and len(key) >= 5 and key[0] == sha256 and key[1] == page_number
        if not (by_page or by_file):
            continue
        reading = _load_saved(tuple(key))
        if reading is not None:
            found.append((str(key[4]), reading))  # במקום 4 בשני סוגי המפתח: גרסת ההוראות
    found.sort(key=lambda item: item[0] != PROMPT_VERSION)
    return found


# ------------------------------------------------------------ שלב שני: השם מתוך תקריב חד
NAME_TALL = 0.30  # תיבת שמות גבוהה מזה (השמות רחוקים זה מזה) — חותכים רק את השם הכתום


def name_region(record: FormRecord) -> Region | None:
    """Where to cut the close-up: around both names when they sit together,
    otherwise around the one that needs review."""
    first, last = (record.fields[k].region for k in NAME_KEYS)
    both = union([first, last])
    if both and both[3] - both[1] <= NAME_TALL:
        return both
    for key in NAME_KEYS:
        field = record.fields[key]
        if field.region and field.state == STATE_REVIEW:
            return field.region
    return None


def name_crop(page_png: Path, region: Region, target: Path) -> Path:
    """A sharp, generous close-up from the full-resolution page: the name, its
    label ("שם", "שם משפחה") and the sticker around it."""
    x0, y0, x1, y1 = region
    dx = max((x1 - x0) * 0.5, 0.06)
    dy = max((y1 - y0) * 1.5, 0.025)
    wide = (max(0.0, x0 - dx), max(0.0, y0 - dy), min(1.0, x1 + dx), min(1.0, y1 + dy))
    target.unlink(missing_ok=True)  # תקריב חדש לכל קריאה — המיקום עשוי להשתנות
    return crop_field(page_png, wide, target)


def look_again_at_names(
    provider: ExtractionProvider, record: FormRecord, image_path: Path,
    batch_id: str, source: SourceFile, page_number: int,
) -> None:
    """When a name is orange, read it again from a close-up — wherever on the page
    it is. Costs one short reading; a failure here never loses the first reading."""
    if not hasattr(provider, "read_names"):
        return
    if not any(record.fields[k].state == STATE_REVIEW for k in NAME_KEYS):
        return
    region = name_region(record)
    if region is None:
        return
    try:
        crop = name_crop(image_path, region, crop_path(batch_id, source.index, page_number, "names-check"))
        check = _read_names_saved(provider, crop)
    except ExtractionError as exc:
        log_failure(batch_id, source, page_number, exc)
        return
    except Exception:  # noqa: BLE001 - הקריאה הראשונה נשארת
        traceback.print_exc()
        return
    if check.usage and check.usage.cost_usd:
        record.cost_usd = (record.cost_usd or 0.0) + check.usage.cost_usd
    merge_names(record, check)


def _read_names_saved(provider: ExtractionProvider, crop: Path) -> NameCheck:
    """The close-up reading — from the saved answers when this exact close-up was
    already read with the same model and instructions."""
    key = ("names", _page_digest(crop), provider.model, getattr(provider, "names_version", ""))
    path = _saved_path(key)
    if path.is_file():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            if saved.get("key") == list(key):
                data = {k: v for k, v in saved["reading"].items() if k != "usage"}
                return NameCheck(**data)
        except (OSError, ValueError, TypeError, KeyError):
            pass
    check = provider.read_names(crop)
    try:
        data = dataclasses.asdict(check)
        data["usage"] = None
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f"{path.stem}.{threading.get_ident()}.tmp")
        temp.write_text(json.dumps({"key": list(key), "reading": data}, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, path)
    except (OSError, TypeError, ValueError):
        traceback.print_exc()
    return check


def log_failure(batch_id: str, source: SourceFile, page_number: int, exc: BaseException) -> None:
    """One line per failed form — when, which page, what went wrong. No form content."""
    cause = exc.__cause__
    detail = f"{type(cause).__name__}: {getattr(cause, 'status_code', '') or ''}" if cause else ""
    line = (f"{time.strftime('%Y-%m-%d %H:%M:%S')}\tקובץ {source.index} עמוד {page_number}\t"
            f"{exc}\t{detail}".rstrip() + "\n")
    print(f"[{batch_id}] הקריאה נכשלה — {line}", end="", flush=True)
    try:
        path = BATCHES_DIR / batch_id / "errors.log"
        if not path.parent.is_dir():
            return  # רק לתיקייה של העלאה אמיתית — לא יוצרים תיקיות חדשות
        with path.open("a", encoding="utf-8") as log:
            log.write(line)
    except OSError:
        pass


class RereadRefused(Exception):
    """A second reading was not started; the message says why, in Hebrew."""


def reread(batch_id: str, record_id: str) -> None:
    """Read one failed form again — only after the person confirmed it (it costs
    a reading). Runs in the background; the page shows "reading" until it ends."""
    batch = get(batch_id)
    if batch is None:
        raise RereadRefused("האצווה לא נמצאה.")
    with _REREAD_LOCK:
        if batch.status in ("queued", "processing"):
            raise RereadRefused("הקריאה של הקובץ עוד לא הסתיימה.")
        index = next((i for i, r in enumerate(batch.records) if r.record_id == record_id), None)
        if index is None:
            raise RereadRefused("הטופס לא נמצא.")
        old = batch.records[index]
        if not old.error:
            raise RereadRefused("הטופס הזה כבר נקרא.")
        page = next((p for p in batch.pages if p.get("record_id") == record_id), None)
        if page is not None and page.get("status") == PAGE_READING:
            raise RereadRefused("הטופס כבר נקרא שוב ברגע זה.")
        if page is not None:
            page["status"] = PAGE_READING
        old.error = "קורא שוב…"
    threading.Thread(target=_reread_run, args=(batch, index, old, page), daemon=True).start()


def _reread_run(batch: Batch, index: int, old: FormRecord, page: dict | None) -> None:
    record = None
    try:
        provider, problem = build_reader()
        source = next(s for s in batch.sources if s.index == old.source_index)
        if provider is None:
            raise ExtractionError(problem or "אין מודל זמין לקריאה.")
        image_path = page_image_path(batch.batch_id, old.source_index, old.page_number)
        record = read_form(Reader(provider), image_path, batch.batch_id, source, old.page_number)
        if record.upside_down:
            show_upright(record, image_path)
        apply_known([record])  # אם הטופס כבר אושר בעבר — הערכים מהמערכת
        record.record_id = old.record_id  # אותו טופס — המסך ממשיך לזהות אותו
    except Exception as exc:  # noqa: BLE001 - חוט רקע לא מת בשקט
        traceback.print_exc()
        old.error = f"הקריאה החוזרת נכשלה: {exc}"
    with _REREAD_LOCK:
        if record is not None:
            batch.records[index] = record
        if page is not None:
            failed = record is None or bool(record.error)
            page["status"] = PAGE_FAILED if failed else PAGE_DONE


def show_upright(record: FormRecord, image_path: Path) -> None:
    """The reader said the page is upside down: turn the stored page 180° so the
    secretary sees it the right way up, and move every box with it.

    A safety net behind the automatic turn before reading (Tesseract). The page
    is not sent again — that would double the cost; the row says what happened.
    """
    turn_upside_down(image_path)
    for field in record.fields.values():
        if field.region:
            field.region = _turned(field.region)
    note = "הדף הגיע הפוך — סובב לתצוגה. הקריאה נעשתה על הדף ההפוך, מומלץ לבדוק"
    record.straightened = " · ".join(filter(None, [record.straightened, note]))


def _turned(region: Region) -> Region:
    x0, y0, x1, y1 = region
    return (1 - x1, 1 - y1, 1 - x0, 1 - y0)


def _read(
    reader: ExtractionProvider, image_path: Path, source: SourceFile, page_number: int
) -> RawExtraction:
    """The reading of one page — from the saved answers when this exact page was
    already read, in any file and at any position, with the same model and prompt."""
    same_reader = (
        reader.model,
        None,  # מקום שמור (היה "חיתוך" לתמונה) — נשאר כדי שתשובות שמורות ימשיכו להימצא
        getattr(reader, "prompt_version", PROMPT_VERSION),
    )
    turned = turn_note(image_path)
    extra = (turned,) if turned else ()
    if not source.sha256:
        return reader.extract(image_path)

    # המפתח: טביעת האצבע של העמוד עצמו (הפיקסלים, אחרי סיבוב ויישור). אותו דף בקובץ
    # אחר או במקום אחר בקובץ — אותה תשובה, בלי לשלם שוב.
    key = ("page", _page_digest(image_path), *same_reader, *extra)
    # המפתח הקודם (קובץ + מספר עמוד): תשובות שנשמרו לפני 16/10/2026 לא הולכות לאיבוד.
    legacy = (source.sha256, page_number, *same_reader, *extra)

    for candidate in (key, legacy):
        with _CACHE_LOCK:
            cached = _READ_CACHE.get(candidate)
        if cached is None:
            cached = _load_saved(candidate)  # אחרי הפעלה מחדש: התשובה שנשמרה בקובץ
        if cached is not None:
            with _CACHE_LOCK:
                _READ_CACHE[key] = cached
            if candidate is legacy:
                _save(key, cached)
            # תשובה שכבר שולמה: מחזירים אותה בלי עלות חדשה.
            return RawExtraction(**{**cached.__dict__, "usage": None})

    reading = reader.extract(image_path)
    with _CACHE_LOCK:
        _READ_CACHE[key] = reading
    _save(key, reading)
    return reading


def _page_digest(image_path: Path) -> str:
    """Fingerprint of what the model actually sees: the page's pixels, not the file around it."""
    with Image.open(image_path) as page:
        digest = hashlib.sha256(f"{page.mode}:{page.size}".encode("ascii"))
        digest.update(page.tobytes())
    return digest.hexdigest()


def _saved_path(key: tuple) -> Path:
    digest = hashlib.sha256(json.dumps(key, ensure_ascii=False).encode("utf-8")).hexdigest()
    return READ_CACHE_DIR / f"{digest}.json"


def _save(key: tuple, reading: RawExtraction) -> None:
    """Keep a paid answer on disk. A failure here only costs a future re-read."""
    try:
        data = dataclasses.asdict(reading)
        data["usage"] = None  # תשובה שמורה תמיד מוחזרת בלי עלות
        target = _saved_path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f"{target.stem}.{threading.get_ident()}.tmp")
        temp.write_text(json.dumps({"key": list(key), "reading": data}, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, target)  # קריסה באמצע לא משאירה קובץ שבור
    except (OSError, TypeError, ValueError):
        traceback.print_exc()


def _load_saved(key: tuple) -> RawExtraction | None:
    """The answer saved for this key, or None — a damaged file is simply a miss."""
    path = _saved_path(key)
    if not path.is_file():
        return None
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved.get("key") != list(key):
            return None
        data = saved["reading"]
        known = {f.name for f in dataclasses.fields(RawExtraction)}
        data = {k: v for k, v in data.items() if k in known}
        data["usage"] = None
        data["regions"] = {
            k: (tuple(v) if v is not None else None) for k, v in (data.get("regions") or {}).items()
        }
        return RawExtraction(**data)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def _is_low_resolution(image_path: Path) -> bool:
    try:
        with Image.open(image_path) as image:
            return max(image.size) < SETTINGS.min_image_long_side
    except OSError:
        return False


def _render_all(batch_id: str, batch: Batch) -> list[Page]:
    """Flatten every accepted file into one ordered list of pages.

    The page cap applies to the whole drop, not to each file, because every
    page is a paid model call.
    """
    budget = SETTINGS.max_pages_per_batch
    flattened: list[Page] = []

    # כמה עמודים צפויים בסך הכול — ספירה מיידית, כדי שהמסך יראה אחוזים כבר בזמן הזיהוי
    expected = sum(count_pages(source_dir(batch_id, s.index) / "source") for s in batch.sources if s.accepted)
    batch.expected_pages = min(expected, budget)

    for source in batch.sources:
        if not source.accepted:
            continue
        if budget <= 0:
            source.error = "לא עובד — נחצתה תקרת העמודים להעלאה"
            continue
        try:
            def found(number: int, path: Path, source: SourceFile = source) -> None:
                # כל עמוד שמוכן נספר מיד — המסך סופר 1, 2, 3 כבר בזמן הזיהוי
                batch.pages.append({"source_index": source.index, "page_number": number,
                                    "status": PAGE_QUEUED, "record_id": ""})
                batch.found_pages += 1

            written, in_file = render_pages(
                source_dir(batch_id, source.index) / "source",
                source_dir(batch_id, source.index) / "pages",
                limit=budget,
                on_page=found,
            )
        except Exception as exc:  # noqa: BLE001 - חוסן: קובץ פגום לא מפיל את השאר
            source.error = f"לא ניתן לפתוח את הקובץ ({type(exc).__name__})"
            traceback.print_exc()
            continue

        source.page_count = len(written)
        if in_file > len(written):
            # הקובץ נקטע באמצע בגלל התקרה — חייב להיות גלוי, לא להיעלם בשקט.
            batch.skipped_pages += in_file - len(written)
        budget -= len(written)
        for page_number, image_path in enumerate(written, start=1):
            flattened.append((source, page_number, image_path))

    return flattened


def _why_nothing_to_do(sources: list[SourceFile]) -> str:
    rejected = [s for s in sources if not s.accepted]
    if rejected:
        return rejected[0].error or "לא נמצאו עמודים לעיבוד."
    return "לא נמצאו עמודים לעיבוד."


def _finish(batch: Batch, total: int) -> None:
    failures = sum(1 for record in batch.records if record.error)
    if batch.records and failures == len(batch.records):
        batch.status = "failed"
        batch.error = batch.records[0].error
        return

    notes = []
    if failures:
        notes.append(f"{failures} מתוך {total} טפסים נכשלו")
    if batch.rejected_sources:
        notes.append(f"{len(batch.rejected_sources)} קבצים נדחו")
    if batch.budget_skipped_pages:
        notes.append(f"{batch.budget_skipped_pages} טפסים לא עובדו בגלל תקרת התקציב")

    batch.status = "partial" if notes else "completed"
    batch.error = ". ".join(notes)


def park_batch(batch: Batch) -> int:
    """Keep the upload's unapproved forms as "waiting for approval" (see db.park). Never fails
    the caller — a storage problem is logged."""
    if batch.status in ("queued", "processing"):
        return 0
    try:
        from app import db

        return db.park(batch, lambda r: page_image_path(batch.batch_id, r.source_index, r.page_number))
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return 0


def page_image_path(batch_id: str, source_index: int, page_number: int) -> Path:
    return source_dir(batch_id, source_index) / "pages" / f"page-{page_number:03d}.png"


def crop_path(batch_id: str, source_index: int, page_number: int, field_key: str) -> Path:
    return source_dir(batch_id, source_index) / "crops" / f"{page_number:03d}-{field_key}.png"
