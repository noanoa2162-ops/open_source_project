"""Faster uploads (21/10/2026): pages are prepared 8 at a time, and each page is read the moment it is
ready. Order, the page cap and the cost cap must not change; one failing page must not stop the rest;
a fix made while the rest are still read must survive the joining at the end. A stand-in reader — free."""

from __future__ import annotations

import re
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app import pipeline, store
from app.models import Batch, SourceFile
from app.providers.base import RawExtraction, Usage


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.BATCHES_DIR", tmp_path / "batches")
    monkeypatch.setattr("app.store.BATCHES_DIR", tmp_path / "batches")
    monkeypatch.setattr("app.gold.save", lambda *a, **k: None)


def _pdf(path: Path, pages: int) -> Path:
    """A PDF whose pages all differ (so no two pages share a saved answer)."""
    mark = uuid.uuid4().int
    images = []
    for n in range(pages):
        image = Image.new("RGB", (300, 420), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((10 + n * 8, 10, 14 + n * 8, 14), fill="black")
        draw.rectangle((10, 30 + mark % 300, 16, 36 + mark % 300), fill="black")
        images.append(image)
    images[0].save(path, "PDF", save_all=True, append_images=images[1:], resolution=72)
    return path


def _page_of(image_path: Path) -> int:
    return int(re.search(r"page-(\d+)", image_path.name).group(1))


class Slow:
    """Sleeps like a real reader; every page gets its own name (to check the order)."""

    name = model = "stub"

    def __init__(self, sleep=0.3, fail_on=(), cost=0.0, values=None, hold=None) -> None:
        self.sleep, self.fail_on, self.cost, self.values, self.hold = sleep, set(fail_on), cost, values, hold
        self.calls = 0
        self.found_at_first_call = None
        self.lock = threading.Lock()

    def extract(self, image_path: Path) -> RawExtraction:
        n = _page_of(image_path)
        with self.lock:
            self.calls += 1
            if self.found_at_first_call is None:
                self.found_at_first_call = store.get("bat_speed").found_pages
        if self.hold and n in self.hold:
            self.hold[n].wait(10)
        time.sleep(self.sleep)
        if n in self.fail_on:
            raise RuntimeError("boom")  # לא ExtractionError — תקלה לא צפויה
        values = self.values(n) if self.values else {"first_name": [f"עמוד{n}"]}
        return RawExtraction(values=values, model="stub", usage=Usage(cost_usd=self.cost))


def _start(tmp_path, monkeypatch, reader, pages=30, **settings) -> Batch:
    monkeypatch.setattr(pipeline, "build_reader", lambda: (reader, ""))
    monkeypatch.setattr(pipeline, "SETTINGS", replace(pipeline.SETTINGS, **settings))
    batch = Batch(batch_id="bat_speed")
    batch.sources.append(SourceFile(index=1, filename="thirty.pdf", kind="pdf"))
    (store.source_dir("bat_speed", 1) / "source").write_bytes(_pdf(tmp_path / "thirty.pdf", pages).read_bytes())
    store.put(batch)
    return batch


def test_thirty_pages_keep_their_order_and_reading_starts_before_all_are_ready(tmp_path, monkeypatch):
    reader = Slow(sleep=0.3)
    batch = _start(tmp_path, monkeypatch, reader, max_parallel_calls=10)
    started = time.perf_counter()
    pipeline._process("bat_speed")
    took = time.perf_counter() - started

    assert batch.status == "completed"
    assert [r.page_number for r in batch.records] == list(range(1, 31))
    assert [r.fields["first_name"].values for r in batch.records] == [[f"עמוד{n}"] for n in range(1, 31)]
    assert [p["page_number"] for p in batch.pages] == list(range(1, 31))
    assert all(p["status"] == "done" for p in batch.pages)
    assert batch.found_pages == batch.total_pages == batch.processed_pages == 30
    assert reader.calls == 30
    assert reader.found_at_first_call < 30, "הקריאה מתחילה לפני שכל העמודים מוכנים"
    assert took < 30 * 0.3, "קריאה במקביל, לא אחד-אחד"


def test_page_cap_is_kept(tmp_path, monkeypatch):
    reader = Slow(sleep=0.0)
    batch = _start(tmp_path, monkeypatch, reader, max_pages_per_batch=10)
    pipeline._process("bat_speed")
    assert [r.page_number for r in batch.records] == list(range(1, 11))
    assert reader.calls == 10 and batch.skipped_pages == 20


def test_cost_cap_is_kept(tmp_path, monkeypatch):
    reader = Slow(sleep=0.0, cost=1.0)
    batch = _start(tmp_path, monkeypatch, reader, max_cost_per_batch_usd=3.0, max_parallel_calls=1)
    pipeline._process("bat_speed")
    assert reader.calls == 3, "אחרי $3 — עוצרים"
    assert len(batch.records) == 3 and batch.budget_skipped_pages == 27
    assert batch.status == "partial" and "תקציב" in batch.error


def test_one_failing_page_does_not_stop_the_rest(tmp_path, monkeypatch):
    reader = Slow(sleep=0.05, fail_on={7})
    batch = _start(tmp_path, monkeypatch, reader, max_parallel_calls=10)
    pipeline._process("bat_speed")
    assert len(batch.records) == 30
    failed = [r for r in batch.records if r.error]
    assert [r.page_number for r in failed] == [7] and "RuntimeError" in failed[0].error
    assert batch.status == "partial"
    assert [r.page_number for r in batch.records] == list(range(1, 31))


def _halves(n: int) -> dict:
    """Page 1 — the top of a form (patient details); page 2 — its bottom (commitment and date)."""
    if n == 1:
        return {"first_name": ["שרה"], "last_name": ["כהן"], "national_id": ["011223344"]}
    return {"commitment_numbers": ["7001"], "sample_received_date": ["18/06/2026"]}


def test_a_bottom_half_read_first_is_not_lost(tmp_path, monkeypatch):
    first = threading.Event()
    reader = Slow(sleep=0.0, values=_halves, hold={1: first})
    batch = _start(tmp_path, monkeypatch, reader, pages=2, max_parallel_calls=2)
    worker = threading.Thread(target=pipeline._process, args=("bat_speed",))
    worker.start()
    for _ in range(100):  # החצי התחתון נקרא ראשון
        if batch.records:
            break
        time.sleep(0.05)
    assert [r.page_number for r in batch.records] == [2]
    first.set()
    worker.join(10)
    assert len(batch.records) == 1 and batch.joined, "שני החלקים חוברו לטופס אחד"
    joined = batch.records[0]
    assert joined.fields["first_name"].values == ["שרה"] and joined.fields["commitment_numbers"].values == ["7001"]


def test_a_fix_made_while_reading_survives_the_joining_and_approval_waits(tmp_path, monkeypatch):
    bottom = threading.Event()
    reader = Slow(sleep=0.0, values=_halves, hold={2: bottom})
    batch = _start(tmp_path, monkeypatch, reader, pages=2, max_parallel_calls=2)
    worker = threading.Thread(target=pipeline._process, args=("bat_speed",))
    worker.start()
    for _ in range(100):
        if batch.records:
            break
        time.sleep(0.05)
    top = batch.records[0]
    assert top.page_number == 1

    from app.api import app
    client = TestClient(app)
    # תיקון בזמן שהעמוד השני עוד נקרא
    res = client.patch(f"/api/records/{top.record_id}", json={"field": "first_name", "values": ["שרה מתוקנת"]})
    assert res.status_code == 200
    # "אישור הטופס" מחכה לסוף (חיבור החצאים וזיהוי טפסים קיימים עוד לא רצו)
    res = client.post(f"/api/records/{top.record_id}/verify")
    assert res.status_code == 409 and "נמשכת" in res.json()["detail"]

    bottom.set()
    worker.join(10)
    assert batch.status != "processing"
    assert top in batch.records and top.fields["first_name"].values == ["שרה מתוקנת"], "התיקון לא נדרס"
    assert not batch.joined, "טופס שתוקן לא מחובר לבד — אפשר לחבר ידנית"
    assert len(batch.records) == 2


def test_the_reader_is_built_once_for_all_uploads(monkeypatch):
    """Loading the model's library took ~10 seconds on the first upload, and a new connection ~1.6 on
    every upload. The reader keeps no state between calls, so one serves them all — until a setting changes."""
    from app.config import SETTINGS

    monkeypatch.setattr(pipeline, "_READERS", {})
    monkeypatch.setattr(pipeline, "SETTINGS", replace(SETTINGS, anthropic_api_key="k1", reader_model="claude-opus-5-5"))
    first, problem = pipeline.build_reader()
    assert problem == "" and pipeline.build_reader()[0] is first
    monkeypatch.setattr(pipeline, "SETTINGS", replace(SETTINGS, anthropic_api_key="k2", reader_model="claude-opus-5-5"))
    assert pipeline.build_reader()[0] is not first
