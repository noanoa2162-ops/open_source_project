"""Uploads → pages → two independent judges → comparison → validated records.

A batch is one drop, however many files it contained. Every file is flattened
into pages and every page becomes one record. Each page is read by both judges
at the same time; `consensus.combine` fills only what they agree on, and
everything else goes to the secretary as orange "לא ברור" with both readings.

Runs on a worker thread so the browser gets an immediate response and can poll
for progress. One page failing never stops the rest; one judge failing never
stops the form — it is read by the other judge and visibly marked as such.
"""

from __future__ import annotations

import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from PIL import Image

from app.config import SETTINGS
from app.consensus import combine
from app.models import Batch, FormRecord, SourceFile
from app.pages import render_pages
from app.providers.base import ExtractionError, ExtractionProvider, RawExtraction
from app.providers.reading import PROMPT_VERSION
from app.store import get, source_dir
from app.validators import validate_record

Page = tuple[SourceFile, int, Path]

# אותו קובץ, אותו עמוד, אותו שופט, אותו פרומפט → אותה תשובה בלי לשלם שוב.
_READ_CACHE: dict[tuple, RawExtraction] = {}
_CACHE_LOCK = threading.Lock()


def build_judges() -> tuple[list[ExtractionProvider], list[str]]:
    """The judges available right now, and a visible reason for any that is not."""
    from app.providers.gemini_vision import GeminiExtractionProvider

    judges: list[ExtractionProvider] = []
    problems: list[str] = []
    for make in (GeminiExtractionProvider, judge_b_class()):
        try:
            judges.append(make())
        except ExtractionError as exc:
            problems.append(str(exc))
    return judges, problems


def judge_b_class(model: str | None = None):
    """The second judge's adapter, chosen by model name — so switching between
    Claude and GPT is one line in .env (JUDGE_B_MODEL)."""
    name = (model or SETTINGS.judge_b_model).lower()
    if name.startswith("claude"):
        from app.providers.claude_vision import ClaudeExtractionProvider

        return ClaudeExtractionProvider
    from app.providers.openai_vision import OpenAIExtractionProvider

    return OpenAIExtractionProvider


class Panel:
    """The judges for one batch. A judge with an account-level failure (no
    credit, bad key) is retired for the rest of the batch, so it stops burning
    calls, and the remaining judge carries on."""

    def __init__(self, judges: list[ExtractionProvider]) -> None:
        self.judges = judges
        self.retired: dict[str, str] = {}
        self._lock = threading.Lock()

    def active(self) -> list[ExtractionProvider]:
        with self._lock:
            return [j for j in self.judges if j.model not in self.retired]

    def retire(self, judge: ExtractionProvider, reason: str) -> None:
        with self._lock:
            self.retired.setdefault(judge.model, reason)

    @property
    def exhausted(self) -> bool:
        return not self.active()


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
    if not pages:
        batch.status = "failed"
        batch.error = _why_nothing_to_do(batch.sources)
        return

    judges, problems = build_judges()
    for problem in problems:
        batch.warn(problem)
    if not judges:
        batch.status = "failed"
        batch.error = " ".join(problems) or "אין שופט זמין."
        return
    if len(judges) == 1:
        batch.warn("רק קורא אחד זמין — כל השדות יסומנו לבדיקה.")

    # שלב 2: כמה טפסים במקביל, ובכל טופס שני השופטים במקביל.
    panel = Panel(judges)
    results = _extract_all(panel, batch, pages)
    batch.records.extend(record for record in results if record is not None)
    batch.seconds = round(time.perf_counter() - started, 1)

    for model, reason in panel.retired.items():
        batch.warn(f"{model}: {reason}")
    if panel.exhausted and len(batch.records) < len(pages):
        batch.status = "failed" if not batch.records else "partial"
        batch.error = " · ".join(panel.retired.values())
        return

    _finish(batch, len(pages))


def _extract_all(panel: Panel, batch: Batch, pages: list[Page]) -> list[FormRecord | None]:
    """Run every page through the panel with a small pool of threads.

    Returns records in page order. A slot is None when that page was never
    attempted: every judge retired, or the batch's cost cap was reached.
    Only the calling thread touches `batch`, so no locking is needed there.
    """
    stop = threading.Event()
    spent = [0.0]
    spent_lock = threading.Lock()
    results: list[FormRecord | None] = [None] * len(pages)

    def work(index: int) -> tuple[int, FormRecord | None, str]:
        if stop.is_set() or panel.exhausted:
            return index, None, "stopped"
        with spent_lock:
            if spent[0] >= SETTINGS.max_cost_per_batch_usd:
                return index, None, "budget"
        source, page_number, image_path = pages[index]
        record = read_form(panel, image_path, batch.batch_id, source, page_number)
        with spent_lock:
            spent[0] += record.cost_usd or 0.0
        if panel.exhausted:
            # חייב להיעשות כאן ולא בתהליך הראשי, אחרת עובד כבר תפס את העמוד הבא.
            stop.set()
        return index, record, ""

    with ThreadPoolExecutor(max_workers=SETTINGS.max_parallel_calls) as pool:
        futures = [pool.submit(work, i) for i in range(len(pages))]
        for future in as_completed(futures):
            index, record, why = future.result()
            if record is None:
                if why == "budget":
                    batch.budget_skipped_pages += 1
                continue
            results[index] = record
            batch.processed_pages += 1

    if batch.budget_skipped_pages:
        batch.warn(
            f"נעצר בתקרת התקציב (${SETTINGS.max_cost_per_batch_usd:.2f}): "
            f"{batch.budget_skipped_pages} טפסים לא עובדו. ניתן לשנות את MAX_COST_PER_BATCH_USD."
        )
    return results


def read_form(
    panel: Panel,
    image_path: Path,
    batch_id: str,
    source: SourceFile,
    page_number: int,
    *,
    flag_single_reader: bool = True,
) -> FormRecord:
    """One page → one record: both judges in parallel, then comparison and rules."""
    started = time.perf_counter()
    record = FormRecord.empty(batch_id, source, page_number)
    record.low_resolution = _is_low_resolution(image_path)

    judges = panel.active()
    readings: list[RawExtraction | None] = [None] * len(judges)
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, len(judges))) as pool:
        futures = {
            pool.submit(_read, judge, image_path, source, page_number): i
            for i, judge in enumerate(judges)
        }
        for future in as_completed(futures):
            i = futures[future]
            try:
                readings[i] = future.result()
            except ExtractionError as exc:
                failures.append(f"{judges[i].model}: {exc}")
                if exc.fatal:
                    panel.retire(judges[i], str(exc))

    done = [r for r in readings if r is not None]  # בסדר השופטים, לא בסדר הסיום
    record.seconds = round(time.perf_counter() - started, 1)
    costs = [r.usage.cost_usd for r in done if r.usage and r.usage.cost_usd is not None]
    record.cost_usd = sum(costs) if costs else (0.0 if done else None)

    if not done:
        record.error = " · ".join(failures) or "אף קורא לא החזיר תשובה."
        for field in record.fields.values():
            field.flag("missing")
        return record

    combine(record, done, flag_single_reader=flag_single_reader)
    if failures:
        record.model_note = " · ".join(filter(None, [record.model_note, *failures]))
    validate_record(record)
    return record


def _read(
    judge: ExtractionProvider, image_path: Path, source: SourceFile, page_number: int
) -> RawExtraction:
    key = (
        source.sha256,
        page_number,
        judge.model,
        getattr(judge, "split", None),
        getattr(judge, "prompt_version", PROMPT_VERSION),
    )
    if source.sha256:
        with _CACHE_LOCK:
            cached = _READ_CACHE.get(key)
        if cached is not None:
            # תשובה שכבר שולמה: מחזירים אותה בלי עלות חדשה.
            return RawExtraction(**{**cached.__dict__, "usage": None})
    reading = judge.extract(image_path)
    if source.sha256:
        with _CACHE_LOCK:
            _READ_CACHE[key] = reading
    return reading


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
