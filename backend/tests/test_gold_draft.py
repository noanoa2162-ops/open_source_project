"""The draft truth table becomes a review batch where every field must be confirmed,
and an approved form becomes a verified answer that keeps its ambiguous fields."""

from __future__ import annotations

import pypdfium2 as pdfium
from fastapi.testclient import TestClient
from PIL import Image

from app import api, gold, gold_draft, store


def _pdf(path, pages=2):
    pdf = pdfium.PdfDocument.new()
    for _ in range(pages):
        pdf.new_page(595, 842)
    pdf.save(str(path))
    return path


DRAFT = [
    {"file": "s.pdf", "page": 2, "kind": "קופה", "first_name": ["ישראל"], "last_name": ["ישראלי"],
     "national_id": ["011223344"], "central_numbers": ["939376"], "sample_received_date": ["18/06/2026"],
     "sending_entity": ["מאוחדת"], "commitment_numbers": ["1?001"], "ambiguous": ["commitment_numbers"]},
]


def test_draft_loads_every_field_orange_and_approval_saves_a_verified_answer(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.BATCHES_DIR", tmp_path / "batches")
    monkeypatch.setattr("app.store.BATCHES_DIR", tmp_path / "batches")
    monkeypatch.setattr("app.gold.GOLD_DIR", tmp_path / "gold")
    _pdf(tmp_path / "s.pdf")

    batch = gold_draft.build(DRAFT, samples=tmp_path)
    store.put(batch)
    record = batch.records[0]
    assert record.page_number == 2 and record.review_count == 7, "כל שדה כתום עד אישור"
    assert "draft_ambiguous" in record.fields["commitment_numbers"].reasons

    client = TestClient(api.app)
    assert client.post(f"/api/records/{record.record_id}/verify").status_code == 409, "אי אפשר לאשר בלי לבדוק"
    for key in record.fields:
        client.patch(f"/api/records/{record.record_id}", json={"field": key, "approve": True})
    assert client.post(f"/api/records/{record.record_id}/verify").status_code == 200

    saved = gold.load_all(tmp_path / "gold")
    assert len(saved) == 1 and saved[0].truth["national_id"] == ["011223344"]
    assert saved[0].ambiguous == ("commitment_numbers",)
    store.delete(batch.batch_id)
