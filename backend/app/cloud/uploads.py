"""Short-lived, private direct uploads; finalization queues work atomically."""

from __future__ import annotations

import dataclasses
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from psycopg.types.json import Jsonb

from app.config import SETTINGS
from app.models import Batch, SourceFile, new_id
from . import storage
from .postgres import connect

router = APIRouter()
RESERVED_LIMIT = 800 * 1024 * 1024  # Conservative cap below the Free storage allowance.


class FileSpec(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0, le=SETTINGS.max_upload_bytes)


class Prepare(BaseModel):
    files: list[FileSpec] = Field(min_length=1, max_length=SETTINGS.max_files_per_batch)


class Complete(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=SETTINGS.max_files_per_batch)


@router.post("/api/uploads")
def prepare(payload: Prepare):
    with connect() as conn:
        used = conn.execute(
            "SELECT (SELECT coalesce(sum(size),0) FROM artifacts) + "
            "(SELECT coalesce(sum(size),0) FROM uploads) AS bytes"
        ).fetchone()["bytes"]
        if used + sum(f.size for f in payload.files) > RESERVED_LIMIT:
            raise HTTPException(413, "אין מספיק מקום פנוי במכסת האחסון של סביבת ההדגמה.")
        tickets = []
        for position, item in enumerate(payload.files, 1):
            upload_id = uuid.uuid4().hex
            key = "uploads/" + upload_id
            signed = storage.bucket().create_signed_upload_url(key)
            conn.execute(
                "INSERT INTO uploads(upload_id,object_key,filename,size,position) VALUES (%s,%s,%s,%s,%s)",
                (upload_id, key, item.name, item.size, position),
            )
            tickets.append({"id": upload_id, "url": signed["signed_url"]})
    return {"files": tickets}


@router.post("/api/uploads/complete")
def complete(payload: Complete):
    if len(set(payload.ids)) != len(payload.ids):
        raise HTTPException(400, "העלאה כפולה ברשימה.")
    with connect() as conn:
        rows = conn.execute("SELECT * FROM uploads WHERE upload_id=ANY(%s) FOR UPDATE", (payload.ids,)).fetchall()
        if len(rows) != len(payload.ids):
            raise HTTPException(404, "אישור ההעלאה לא נמצא.")
        assigned = {r["batch_id"] for r in rows}
        if assigned != {None}:
            if len(assigned) == 1 and None not in assigned:
                batch_id = next(iter(assigned))
                if not conn.execute("SELECT 1 FROM batches WHERE batch_id=%s", (batch_id,)).fetchone():
                    raise HTTPException(410, "ההעלאה הזו כבר נמחקה.")
                return {"batch_id": batch_id, "accepted": len(rows), "rejected": 0}
            raise HTTPException(409, "חלק מהקבצים כבר נשלחו לעיבוד.")
        expired = conn.execute(
            "SELECT 1 FROM uploads WHERE upload_id=ANY(%s) AND created_at < now() - interval '2 hours' LIMIT 1",
            (payload.ids,),
        ).fetchone()
        if expired:
            raise HTTPException(410, "תוקף ההעלאה פג. בחרו את הקבצים מחדש.")
        by_id = {r["upload_id"]: r for r in rows}
        batch = Batch(batch_id=new_id("bat"))
        for position, upload_id in enumerate(payload.ids, 1):
            row = by_id[upload_id]
            batch.sources.append(SourceFile(index=position, filename=row["filename"]))
            conn.execute("UPDATE uploads SET batch_id=%s,position=%s WHERE upload_id=%s",
                         (batch.batch_id, position, upload_id))
        conn.execute("INSERT INTO batches(batch_id,payload) VALUES (%s,%s)",
                     (batch.batch_id, Jsonb(dataclasses.asdict(batch))))
        conn.execute("INSERT INTO jobs(job_id,batch_id,kind) VALUES (%s,%s,'extract')",
                     (uuid.uuid4().hex, batch.batch_id))
    return {"batch_id": batch.batch_id, "accepted": len(rows), "rejected": 0}
