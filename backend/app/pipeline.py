"""Uploads → pages → the reader (or two judges) → validated records.

A batch is one drop, however many files it contained. Every file is flattened
into pages and every page becomes one record. By default one strong model
(Claude Opus, deep thinking) reads each page on its own; whatever it is unsure
of goes to the secretary as orange. With a second judge configured in .env,
both read every page and `consensus.combine` fills only what they agree on.

Runs on a worker thread so the browser gets an immediate response and can poll
for progress. One page failing never stops the rest; one judge failing never
stops the form — it is read by the other judge and visibly marked as such.
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
from app.models import Batch, FormRecord, Region, SourceFile
from app.pages import render_pages, turn_note, turn_upside_down
from app.providers.base import ExtractionError, ExtractionProvider, RawExtraction
from app.providers.reading import PROMPT_VERSION
from app.store import get, source_dir
from app.validators import validate_record

Page = tuple[SourceFile, int, Path]

# אותו קובץ, אותו עמוד, אותו שופט, אותו פרומפט → אותה תשובה בלי לשלם שוב.
# בזיכרון לגישה מהירה, ובקובץ ב-work/read_cache כדי שיישמר גם אחרי הפעלה מחדש.
_READ_CACHE: dict[tuple, RawExtraction] = {}
_CACHE_LOCK = threading.Lock()
READ_CACHE_DIR = WORK_DIR / "read_cache"


def build_judges() -> tuple[list[ExtractionProvider], list[str]]:
    """The readers configured in .env, and a visible reason for any that is not available."""
    judges: list[ExtractionProvider] = []
    problems: list[str] = []
    for model in SETTINGS.judges:
        try:
            judges.append(judge_class(model)(model=model))
        except ExtractionError as exc:
            problems.append(str(exc))
    return judges, problems


def judge_class(model: str):
    """The adapter for a model, chosen by its name — so switching models is one line in .env."""
    name = model.lower()
    if name.startswith("claude"):
        from app.providers.claude_vision import ClaudeExtractionProvider

        return ClaudeExtractionProvider
    if name.startswith(("gemini", "gemma")):
        from app.providers.gemini_vision import GeminiExtractionProvider

        return GeminiExtractionProvider
    from app.providers.openai_vision import OpenAIExtractionProvider

    return OpenAIExtractionProvider


def judge_b_class(model: str | None = None):
    """Kept for the evaluation tool: the adapter of the second judge slot."""
    return judge_class(model or SETTINGS.judge_b_model or SETTINGS.judge_a_model)


class Panel:
    """The judges for one batch. A judge with an account-level failure (no
    credit, bad key) is retired for the rest of the batch, so it stops burning
    calls, and the remaining judge carries on."""

    def __init__(self, judges: list[ExtractionProvider], expected: int | None = None) -> None:
        self.judges = judges
        # כמה קוראים הוגדרו. קורא אחד במכוון אינו תקלה; אחד משניים — כן.
        self.expected = len(judges) if expected is None else expected
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
    expected = len(SETTINGS.judges)
    if expected > 1 and len(judges) == 1:
        batch.warn("רק קורא אחד זמין — כל השדות יסומנו לבדיקה.")

    # שלב 2: כמה טפסים במקביל (ואם הוגדרו שני שופטים — שניהם במקביל בכל טופס).
    panel = Panel(judges, expected=expected)
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
            cap = SETTINGS.max_cost_per_batch_usd  # 0 = בלי תקרה
            if cap > 0 and spent[0] >= cap:
                return index, None, "budget"
        source, page_number, image_path = pages[index]
        record = read_form(
            panel, image_path, batch.batch_id, source, page_number,
            flag_single_reader=panel.expected > 1,
        )
        if record.upside_down:
            show_upright(record, image_path)
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
    record.straightened = turn_note(image_path)

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


def show_upright(record: FormRecord, image_path: Path) -> None:
    """Both judges said the page is upside down: turn the stored page 180° so
    the secretary sees it the right way up, and move every box with it.

    The page is not sent again — that would double the cost. What the judges
    read stays as it is, with its own doubts, and the row says what happened.
    """
    turn_upside_down(image_path)
    for field in record.fields.values():
        if field.region:
            field.region = _turned(field.region)
        field.regions_by_model = {m: _turned(r) for m, r in field.regions_by_model.items()}
    note = "הדף הגיע הפוך — סובב לתצוגה. הקריאה נעשתה על הדף ההפוך, מומלץ לבדוק"
    record.straightened = " · ".join(filter(None, [record.straightened, note]))


def _turned(region: Region) -> Region:
    x0, y0, x1, y1 = region
    return (1 - x1, 1 - y1, 1 - x0, 1 - y0)


def _read(
    judge: ExtractionProvider, image_path: Path, source: SourceFile, page_number: int
) -> RawExtraction:
    """One judge's reading of one page — from the saved answers when this exact page was
    already read, in any file and at any position, with the same model and prompt."""
    same_reader = (
        judge.model,
        getattr(judge, "split", None),
        getattr(judge, "prompt_version", PROMPT_VERSION),
    )
    turned = turn_note(image_path)
    extra = (turned,) if turned else ()
    if not source.sha256:
        return judge.extract(image_path)

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

    reading = judge.extract(image_path)
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
