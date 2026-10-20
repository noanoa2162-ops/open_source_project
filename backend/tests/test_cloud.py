"""Cloud boundaries with real PostgreSQL in CI and a private in-memory bucket.

No cloud account, real document, provider API key or paid model is used.
"""

from __future__ import annotations

import io
import os
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app import api, db, gold, pipeline, store
from app.cloud import gateway, postgres, state, storage, uploads, worker
from app.models import Batch, FormRecord, SourceFile


@pytest.fixture
def cloud_settings(monkeypatch):
    values = {
        "DATABASE_URL": "postgresql://test:test@localhost:5432/tofes_test?sslmode=require",
        "SUPABASE_URL": "https://test.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "test-only",
        "APP_ORIGIN": "https://test.example",
        "ACCESS_USER": "demo",
        "ACCESS_PASSWORD": "test-password-with-more-than-24-characters",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return values


def test_environment_fails_closed(cloud_settings, monkeypatch):
    gateway.validate_environment()
    monkeypatch.setenv("ACCESS_PASSWORD", "short")
    with pytest.raises(RuntimeError, match="24"):
        gateway.validate_environment()


@pytest.mark.parametrize("url", [
    "postgresql://test:test@localhost:6543/tofes_test?sslmode=require",
    "postgresql://test:test@localhost:5432/tofes_test?sslmode=disable",
])
def test_cloud_rejects_unsafe_database_modes(cloud_settings, monkeypatch, url):
    monkeypatch.setenv("DATABASE_URL", url)
    with pytest.raises(RuntimeError):
        gateway.validate_environment()


def test_auth_and_cross_site_requests(cloud_settings):
    app = FastAPI()
    app.add_middleware(gateway.CloudGateway)
    client = TestClient(app)
    assert client.get("/").status_code == 401
    assert client.get("/api/pending").status_code == 401
    assert client.get("/static/app.js", auth=("demo", "wrong")).status_code == 401
    credentials = ("demo", cloud_settings["ACCESS_PASSWORD"])
    assert client.post("/api/test", auth=credentials, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/test", auth=credentials).status_code == 403
    assert client.get("/", auth=credentials).status_code == 404  # Authenticated, no route.


def test_parameters_do_not_rewrite_literals_or_values():
    assert postgres.parameters("SELECT '?' AS text, ':name', value WHERE x=? AND y=:foo") == (
        "SELECT '?' AS text, ':name', value WHERE x=%s AND y=%(foo)s"
    )


@pytest.mark.parametrize("key", ["../secret", "work/../../secret", "/etc/passwd", "work/a/../b"])
def test_artifacts_cannot_escape_scratch(tmp_path, monkeypatch, key):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    with pytest.raises(ValueError):
        storage.local(key)


def test_batch_paths_cannot_escape_workspace():
    with pytest.raises(ValueError):
        store.delete("../outside")
    with pytest.raises(ValueError):
        store.batch_dir("../outside")


class Bucket:
    """The storage boundary is mocked; SQL, API and document logic are real."""

    def __init__(self):
        self.objects = {}

    def upload(self, key, data, options):
        self.objects[key] = bytes(data)

    def download(self, key):
        return self.objects[key]

    def remove(self, keys):
        for key in keys:
            self.objects.pop(key, None)

    def create_signed_upload_url(self, key):
        return {"signed_url": "https://storage.example/upload/" + key}

    def create_signed_url(self, key, seconds):
        assert seconds == 60
        return {"signedURL": "https://storage.example/private/" + key}

    def list(self, prefix, options):
        names = sorted(k[len(prefix) + 1:] for k in self.objects if k.startswith(prefix + "/"))
        return [{"name": name} for name in names[options["offset"]:options["offset"] + options["limit"]]]


@pytest.fixture
def cloud(tmp_path, monkeypatch, cloud_settings):
    database = os.environ.get("TEST_DATABASE_URL")
    if not database:
        pytest.skip("Real PostgreSQL integration runs in CI (TEST_DATABASE_URL)")
    url = urlsplit(database)
    if url.hostname not in ("localhost", "127.0.0.1") or url.path != "/tofes_test":
        pytest.fail("Integration tests may only reset the local tofes_test database")
    monkeypatch.setenv("DATABASE_URL", database)
    monkeypatch.setenv("CLOUD_MODE", "1")
    # TLS is required for actual cloud settings, covered independently above.
    monkeypatch.setattr(gateway, "validate_environment", lambda: None)
    root = tmp_path / "tofes-cloud"
    monkeypatch.setattr(storage, "DATA_DIR", root)
    monkeypatch.setattr(store, "BATCHES_DIR", root / "work" / "batches")
    monkeypatch.setattr(pipeline, "BATCHES_DIR", root / "work" / "batches")
    monkeypatch.setattr(pipeline, "READ_CACHE_DIR", root / "work" / "read_cache")
    monkeypatch.setattr(db, "PAGES_DIR", root / "work" / "approved_pages")
    monkeypatch.setattr(gold, "GOLD_DIR", root / "evaluation" / "gold")
    bucket = Bucket()
    monkeypatch.setattr(storage, "bucket", lambda: bucket)
    with postgres.connect() as conn:
        conn.execute("DROP SCHEMA IF EXISTS app_private CASCADE")
    postgres.initialize()
    app = FastAPI()
    app.router.routes.extend(api.app.router.routes)
    app.include_router(uploads.router)
    app.add_middleware(gateway.CloudGateway)
    client = TestClient(app, headers={"Origin": cloud_settings["APP_ORIGIN"]})
    client.auth = ("demo", cloud_settings["ACCESS_PASSWORD"])
    store._BATCHES.clear()
    storage._BASELINE.clear()
    monkeypatch.setattr(storage, "_LAST_SWEEP", 0.0)
    pipeline._READ_CACHE.clear()
    yield client, bucket
    store._BATCHES.clear()
    storage.reset_scratch()


def seeded():
    batch = Batch(batch_id="bat_test", status="completed")
    source = SourceFile(index=1, filename="synthetic.png", kind="png", page_count=1, sha256="abc123")
    batch.sources.append(source)
    record = FormRecord.empty(batch.batch_id, source, 1)
    for key, field in record.fields.items():
        field.values = ["123"] if key != "first_name" else ["שם בדיקה"]
        field.state = "approved"
    batch.records.append(record)
    batch.pages.append({"source_index": 1, "page_number": 1, "record_id": record.record_id, "status": "done"})
    page = store.source_dir(batch.batch_id, 1) / "pages" / "page-001.png"
    page.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (100, 200), "white").save(page)
    with postgres.writer_lock():
        state.checkpoint(batch)
    storage.reset_scratch()
    return batch, record


def test_cold_restart_edits_approval_and_export(cloud):
    client, bucket = cloud
    batch, record = seeded()
    url = f"/api/records/{record.record_id}"
    assert client.get(f"/api/batches/{batch.batch_id}").json()["records"][0]["fields"]["first_name"]["values"] == ["שם בדיקה"]
    result = client.patch(url, json={"field": "first_name", "values": ["עוד בדיקה"], "version": 0})
    assert result.status_code == 200, result.text
    assert client.patch(url, json={"field": "first_name", "values": ["stale"], "version": 0}).status_code == 409
    result = client.post(url + "/verify", json={"version": 1})
    assert result.status_code == 200, result.text
    assert state.get(batch.batch_id).records[0].verified
    rows = db.list_rows(start="2000-01-01", end="2100-01-01")["rows"]
    assert len(rows) == 1 and rows[0]["first_name"] == "עוד בדיקה"
    result = client.post(f"/api/batches/{batch.batch_id}/export")
    assert result.status_code == 200 and result.content.startswith(b"PK")
    result = client.get(f"/api/batches/{batch.batch_id}/sources/1/pages/1", follow_redirects=False)
    assert result.status_code == 307
    assert result.headers["location"].startswith("https://storage.example/private/")


def test_failed_approval_rolls_back_sql_and_snapshot(cloud, monkeypatch):
    client, bucket = cloud
    batch, record = seeded()
    original = db.save_approved

    def fails_after_insert(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("simulated persistence failure")

    monkeypatch.setattr(db, "save_approved", fails_after_insert)
    response = client.post(f"/api/records/{record.record_id}/verify", json={"version": 0})
    assert response.status_code == 503
    assert not state.get(batch.batch_id).records[0].verified
    assert db.list_approved() == []


def test_manual_split_survives_cold_restart_and_undo(cloud):
    client, bucket = cloud
    batch, record = seeded()
    result = client.post(f"/api/records/{record.record_id}/split-page", json={"axis": "y", "cut": 0.5})
    assert result.status_code == 200, result.text
    assert len(result.json()["records"]) == 2
    part = result.json()["records"][0]["record_id"]
    assert state.get(batch.batch_id).page_splits
    result = client.post(f"/api/records/{part}/unsplit-page")
    assert result.status_code == 200, result.text
    assert len(result.json()["records"]) == 1


def test_delete_is_persistent_and_deferred_objects_are_collected(cloud):
    client, bucket = cloud
    batch, record = seeded()
    result = client.delete(f"/api/batches/{batch.batch_id}")
    assert result.status_code == 200, result.text
    assert state.get(batch.batch_id) is None
    with postgres.writer_lock():
        storage.collect_garbage()
    assert bucket.objects == {}
    assert client.get(f"/api/batches/{batch.batch_id}").status_code == 404


def test_upload_finalization_is_idempotent_and_worker_runs_without_http(cloud, monkeypatch):
    client, bucket = cloud
    from app.providers.base import RawExtraction

    class Reader:
        model = "stub"
        name = "stub"
        calls = 0

        def extract(self, path):
            self.calls += 1
            return RawExtraction(values={"first_name": ["בדיקה"]}, model="stub")

    reader = Reader()
    monkeypatch.setattr(pipeline, "build_reader", lambda: (reader, ""))
    output = io.BytesIO()
    Image.new("RGB", (100, 200), "white").save(output, "PNG")
    data = output.getvalue()
    result = client.post("/api/uploads", json={"files": [{"name": "synthetic.png", "size": len(data)}]})
    assert result.status_code == 200, result.text
    ticket = result.json()["files"][0]
    bucket.objects["uploads/" + ticket["id"]] = data
    payload = {"ids": [ticket["id"]]}
    first = client.post("/api/uploads/complete", json=payload)
    second = client.post("/api/uploads/complete", json=payload)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert reader.calls == 0
    assert worker.run_once()
    assert reader.calls == 1
    batch = state.get(first.json()["batch_id"])
    assert len(batch.records) == 1
    assert batch.status == "completed"
    assert db.pending_count() == 1
    assert not worker.run_once()
    assert reader.calls == 1


def test_interrupted_job_never_retries_the_paid_read(cloud, monkeypatch):
    batch, record = seeded()
    batch.status = "processing"
    with postgres.writer_lock():
        state.enqueue(batch)
        with postgres.connect() as conn:
            conn.execute("UPDATE jobs SET status='running'")
    monkeypatch.setattr(pipeline, "_run", lambda *_: pytest.fail("Must not retry an interrupted job"))
    assert not worker.run_once()
    assert state.get(batch.batch_id).status == "partial"
    with postgres.connect() as conn:
        assert conn.execute("SELECT status FROM jobs").fetchone()["status"] == "interrupted"


def test_workspace_lock_rejects_other_writer_and_rolls_back(cloud):
    # A separate DB session represents another web instance/worker.
    with postgres._open() as other:
        other.execute("SELECT pg_advisory_lock(%s)", (postgres.WORKSPACE_LOCK,))
        other.commit()
        with postgres.writer_lock() as acquired:
            assert not acquired
        result = cloud[0].post("/api/uploads", json={"files": [{"name": "x", "size": 10}]})
        assert result.status_code == 409
    with pytest.raises(RuntimeError):
        with postgres.writer_lock(transaction=True):
            state.save(Batch(batch_id="rolled_back"))
            raise RuntimeError("rollback")
    assert state.get("rolled_back") is None


def test_lost_lock_session_cannot_save_using_a_new_connection(cloud):
    with pytest.raises(Exception):
        with postgres.writer_lock():
            postgres._OWNER.close()
            state.save(Batch(batch_id="stale_worker"))
    assert state.get("stale_worker") is None
