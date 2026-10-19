"""Shared test setup.

- Saved model answers go to a temporary folder, never to work/.
- The real Tesseract is not run on synthetic test pages (slow, and its answer
  on fake stripes means nothing); orientation tests use a stand-in instead.
"""

from __future__ import annotations

from dataclasses import replace

import pytest


@pytest.fixture(autouse=True)
def _isolated_read_cache(tmp_path, monkeypatch):
    monkeypatch.setattr("app.pipeline.READ_CACHE_DIR", tmp_path / "read_cache")


@pytest.fixture(autouse=True)
def _no_real_tesseract(monkeypatch):
    from app import pages

    monkeypatch.setattr(pages, "SETTINGS", replace(pages.SETTINGS, auto_rotate=False))


@pytest.fixture(autouse=True)
def _isolated_database(tmp_path, monkeypatch):
    """Approved forms go to a temporary database, never to work/forms.db."""
    monkeypatch.setattr("app.db.DB_PATH", tmp_path / "forms.db")
    monkeypatch.setattr("app.db.PAGES_DIR", tmp_path / "approved_pages")
