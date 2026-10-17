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

from app.config import SETTINGS, WORK_DIR
from app.consensus import combine
from app.models import (
    PAGE_DONE,
    PAGE_FAILED,
    PAGE_QUEUED,
    PAGE_READING,
    PAGE_SKIPPED,
    Batch,
    FormRecord,
    Region,
    SourceFile,
)
from app.pages import render_pages, turn_note, turn_upside_down
from app.providers.base import ExtractionError, ExtractionProvider, RawExtraction
from app.providers.reading import PROMPT_VERSION
from app.store import get, source_dir
from app.validators import validate_record

Page = tuple[SourceFile, int, Path]

# אותו דף, אותו מודל, אותו פרומפט → אותה תשובה בלי לשלם שוב.
# בזיכרון לגישה מהירה, ובקובץ ב-work/read_cache כדי שיישמר גם אחרי הפעלה מחדש.
_READ_CACHE: dict[tuple, RawExtraction] = {}
_CACHE_LOCK = threading.Lock()
READ_CACHE_DIR = WORK_DIR / "read_cache"


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
    reader: Reader, image_path: Path, batch_id: str, source: SourceFile, page_number: int
) -> FormRecord:
    """One page → one record: the reader's answer, then the local rules."""
    started = time.perf_counter()
    record = FormRecord.empty(batch_id, source, page_number)
    record.low_resolution = _is_low_resolution(image_path)
    record.straightened = turn_note(image_path)

    try:
        reading = _read(reader.provider, image_path, source, page_number)
    except ExtractionError as exc:
        if exc.fatal:
            reader.retire(str(exc))
        record.seconds = round(time.perf_counter() - started, 1)
        record.error = f"{reader.model}: {exc}"
        for field in record.fields.values():
            field.flag("missing")
        return record

    record.seconds = round(time.perf_counter() - started, 1)
    usage = reading.usage
    record.cost_usd = usage.cost_usd if usage and usage.cost_usd is not None else 0.0
    combine(record, reading)
    validate_record(record)
    return record


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

    for source in batch.sources:
        if not source.accepted:
            continue
        if budget <= 0:
            source.error = "לא עובד — נחצתה תקרת העמודים להעלאה"
            continue
        try:
            written, in_file = render_pages(
                source_dir(batch_id, source.index) / "source",
                source_dir(batch_id, source.index) / "pages",
                limit=budget,
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


def page_image_path(batch_id: str, source_index: int, page_number: int) -> Path:
    return source_dir(batch_id, source_index) / "pages" / f"page-{page_number:03d}.png"


def crop_path(batch_id: str, source_index: int, page_number: int, field_key: str) -> Path:
    return source_dir(batch_id, source_index) / "crops" / f"{page_number:03d}-{field_key}.png"
