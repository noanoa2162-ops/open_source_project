"""Check the persistence boundary without using real documents or model calls."""

import json
import os
import subprocess
import sys

from fastapi.testclient import TestClient

from app.api import app


def test_health_endpoint():
    response = TestClient(app).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_cloud_database_and_page_survive_a_new_process(tmp_path):
    env = {**os.environ, "DATA_DIR": str(tmp_path)}
    write = '''
from PIL import Image
from app import db
from app.config import WORK_DIR, GOLD_DIR, RUNS_DIR
from app.models import FormRecord, SourceFile
WORK_DIR.mkdir(parents=True)
page = WORK_DIR / "test.png"
Image.new("RGB", (10, 10), "white").save(page)
source = SourceFile(index=1, filename="synthetic.png", kind="image")
record = FormRecord.empty("test", source, 1)
record.fields["national_id"].values = ["001234567"]
db.save_approved(record, source, page)
assert GOLD_DIR.parent.parent == WORK_DIR.parent
assert RUNS_DIR.parent.parent == WORK_DIR.parent
'''
    subprocess.run([sys.executable, "-c", write], env=env, check=True, capture_output=True)
    read = '''
import json
from pathlib import Path
from app import db
rows = db.list_approved()
assert len(rows) == 1
assert Path(rows[0]["page_image"]).is_file()
print(json.dumps(rows[0]["national_id"]))
'''
    result = subprocess.run(
        [sys.executable, "-c", read], env=env, check=True, capture_output=True, text=True,
    )
    assert json.loads(result.stdout) == "001234567"
    assert (tmp_path / "work" / "forms.db").is_file()
