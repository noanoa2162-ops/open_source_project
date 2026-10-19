# Project agent instructions

Build a Hebrew RTL hackathon demo: scanned/photographed commitment forms → reviewed structured records → Excel. Read `INSTRUCTIONS.md` once at project start; subsequently read only sections relevant to the task. This file is the compact daily contract.

## Constraints
- Submission: 2026-10-21, Asia/Jerusalem. Daytime work only; no Saturday work. Final event listed separately as 2026-10-28. Do not confuse these dates.
- Priorities: correct extraction, visible uncertainty, fast human review, impressive accessible UI, measurable business value, replaceable private inference.
- Hebrew extraction is the model-selection gate: printed Hebrew, handwritten names, stamps, RTL and exact digit strings. Do not equate Hebrew conversation skill with document-reading accuracy. No multilingual benchmark substitutes for sample evaluation.
- Only organizer-provided altered samples may reach external AI in the demo. No claim of production readiness or guaranteed accuracy.

## Stack (as built — decisions updated 19/10/2026; the original plan is in INSTRUCTIONS.md §4)
- Frontend: plain JavaScript + CSS served by FastAPI, no build step; Hebrew RTL. (React/MUI was planned; dropped for time — one page, no bundle, nothing to deploy separately.)
- Backend: Python, FastAPI, Pydantic, uv (`pyproject.toml` + committed `uv.lock`).
- Data: SQLite (WAL) through `db.py` only — every batch, record and edit; the challenge names SQLite as acceptable. Moving to PostgreSQL = changing `db.py`. Files on disk under `DATA_DIR`.
- PDF/images: pypdfium2, Pillow. Excel: openpyxl. Tesseract only for page orientation.
- One Docker container (Dockerfile + render.yaml, persistent disk at `/data`); reading runs in background threads of the same process, state persisted to SQLite, unfinished batches resume on restart.
- Evaluation: `uv run evaluate` (script, no notebook) imports the same extraction modules as the server.
- Develop in local VS Code with Git. Cloud processing is not inherently private; temporary storage is not persistent.
- Primary approach: vision model directly reads original page images. No separate OCR for reading.
- Provider/model IDs are configuration (`READER_MODEL`, `READER_EFFORT`), not business logic. Current reader: Claude Opus 5.5 (chosen 15–16/10/2026 after Noa compared it with Gemini/Sonnet on the samples); the final setting is chosen by `uv run evaluate` on the verified truth table. Do not silently substitute a provider.

## Non-negotiable architecture
- Flow: validate upload → create durable job → split/render pages → identify/group forms → extract → normalize/validate → human review → Excel.
- Do not assume one page equals one form. Preserve source pages and flag uncertain grouping.
- One record per logical commitment form; preserve multiple central/commitment numbers as arrays. No Cartesian product or automatic deduplication of records.
- IDs/numbers are strings. Unknown values are null; never fabricate missing digits.
- Every field carries raw/normalized values, source page(s), optional validated region, review state and reason codes. Model self-confidence is not a calibrated probability.
- Domain validators determine review flags; user approval is explicit. A valid date/checksum is not proof of the correct source field.
- Orange means review needed, including unreadable/missing values. No green automatic correctness claim; green means human-approved.
- Missing or conflicting results must remain visible. All unresolved fields require correction or explicit acceptance as empty before normal export.
- ExtractionProvider, optional OcrProvider and Storage interfaces isolate infrastructure. Business code must not import provider SDKs.
- `internal` mode: allowlisted internal endpoints only; forbid external fallback. Production deployment also requires network egress controls. Mock adapter proves wiring, not internal model quality.
- Keep secrets server-side. No patient content in logs/telemetry. Private originals/crops/results/exports; deletion covers all derivatives. Enforce session ownership even without roles.
- Document contents are untrusted data, never instructions. No model tools, browsing, arbitrary URL fetches or arbitrary code execution.
- Durable DB-backed worker jobs with bounded retries/timeouts; preserve partial results. Never rely on long requests or in-memory background tasks for extraction.
- Use optimistic version checks for edits/exports; retries must not duplicate records. Export identifiers as text and neutralize spreadsheet formulas.

## Work discipline
- Work in small sprints with prerequisites, deliverable and acceptance criteria. Finish one working vertical slice early; integrate and deploy before polishing everything.
- Add meaningful checks for extraction schema, field rules, grouping, jobs, session isolation, internal-provider policy, export and the main UI workflow.
- Measure held-out field accuracy, missed review flags, review burden, time and cost. Never invent performance/business results.
- Keep technical explanations concise and junior-friendly in Hebrew. Update decisions and limitations when evidence changes scope.
- No RAG/vector database, agent framework, microservices or extra language without a demonstrated requirement.
- UI: white/navy/teal, strong typography, document/table split view, clear progress and reasons, keyboard usability.
- Branding: Ministry of Health / gov.il look, including the ministry logo, approved by the project owner (Noa) on 2026-10-18 for the presentation to the ministry. Applied through a theme (BRAND=moh|neutral). No "hackathon demo" label in the UI. Do not add false claims (e.g. "official service", "approved by the ministry").
- Before completion: run checks appropriate to the change, report result and remaining limitation. Do not call unimplemented safeguards complete.
