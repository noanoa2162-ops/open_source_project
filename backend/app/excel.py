"""Excel export — one row per commitment number.

A form with several commitment numbers becomes several rows (the project
requirement, 16/10/2026): every row repeats the form's other fields, and only
the commitment number changes. Several central numbers stay together in one
cell, so rows never multiply into a cross product.

Everything is written as text. Identifiers keep their leading zeros, dates are
not re-parsed into Excel serial numbers, and a value starting with a formula
character can never execute when the lab opens the file.
"""

from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from app.fields import FIELDS, MULTI_SEPARATOR
from app.models import STATE_APPROVED, STATE_REVIEW, Batch, FormRecord
from app.validators import REASON_TEXT

# נוסחאות: ערך שמתחיל באחד מאלה ייכתב כטקסט מנוטרל ולא ירוץ כנוסחה.
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

NAVY = "1B3A5C"
AMBER_FILL = "FFF1DC"
AMBER_TEXT = "8A4B10"
LINE = "D8E0E8"

EXTRA_COLUMNS = ("קובץ מקור", "עמוד בקובץ", "שדות לבדיקה", "סיבות")


def neutralize(value: str) -> str:
    """Stop a spreadsheet from treating a scanned value as a formula."""
    if value and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


COMMITMENT_KEY = "commitment_numbers"


def record_rows(record: FormRecord) -> list[list[str]]:
    """One row per commitment number; one row when there are none or one."""
    base = record_row(record)
    commitments = record.fields[COMMITMENT_KEY].values
    if len(commitments) < 2:
        return [base]
    column = next(i for i, f in enumerate(FIELDS) if f.key == COMMITMENT_KEY)
    return [[*base[:column], number, *base[column + 1:]] for number in commitments]


def record_row(record: FormRecord) -> list[str]:
    cells = [MULTI_SEPARATOR.join(record.fields[f.key].values) for f in FIELDS]
    flagged = [f for f in record.fields.values() if f.state == STATE_REVIEW]
    reasons = sorted({REASON_TEXT.get(r, r) for f in flagged for r in f.reasons})
    # שדה במחלוקת נשאר ריק בתא — אף קורא לא מיוצא כעובדה. האפשרויות מופיעות כאן.
    for f in flagged:
        if f.candidates:
            label = next(s.label_he for s in FIELDS if s.key == f.key)
            reasons.append(f"{label}: " + " / ".join(f.candidates))
    # הערות רכות: התא לא כתום ולא נספר לבדיקה, אבל ההערה גלויה גם באקסל.
    for spec in FIELDS:
        f = record.fields[spec.key]
        if f.state == STATE_APPROVED:
            continue
        for code in f.notes:
            if code == "extra_value" and f.extra_values:
                reasons.append(f"{spec.label_he}: מודל אחד מצא גם " + " / ".join(f.extra_values))
            else:
                reasons.append(f"{spec.label_he}: {REASON_TEXT.get(code, code)}")
    return [
        *cells,
        record.source_name,
        str(record.page_number),
        str(len(flagged)),
        "; ".join(reasons),
    ]


def build_workbook(batch: Batch) -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = "התחייבויות"
    ws.sheet_view.rightToLeft = True

    headers = [f.label_he for f in FIELDS] + list(EXTRA_COLUMNS)
    widths = [14, 16, 16, 18, 20, 22, 20, 30, 12, 12, 46]
    thin = Side(style="thin", color=LINE)

    for index, (label, width) in enumerate(zip(headers, widths), start=1):
        cell = ws.cell(row=1, column=index, value=label)
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.font = Font(bold=True, color="FFFFFF", size=11)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(index)].width = width
        ws.column_dimensions[get_column_letter(index)].number_format = "@"
    ws.row_dimensions[1].height = 30
    ws.freeze_panes = "A2"

    row_index = 1
    for record in batch.records:
        for values in record_rows(record):
            row_index += 1
            for column_index, value in enumerate(values, start=1):
                cell = ws.cell(row=row_index, column=column_index, value=neutralize(value))
                cell.number_format = "@"  # טקסט: אפסים מובילים ותאריכים נשמרים כפי שהם
                cell.alignment = Alignment(horizontal="right", vertical="top", wrap_text=True)
                cell.border = Border(bottom=thin)

                spec = FIELDS[column_index - 1] if column_index <= len(FIELDS) else None
                if spec and record.fields[spec.key].state == STATE_REVIEW:
                    # כתום = נדרשת בדיקה. אותה משמעות כמו במסך — בכל שורות הטופס.
                    cell.fill = PatternFill("solid", fgColor=AMBER_FILL)
                    cell.font = Font(color=AMBER_TEXT)

    _add_notice(ws, row_index + 2, batch, rows=row_index - 1)
    return wb


def _add_notice(ws, row: int, batch: Batch, *, rows: int) -> None:
    sources = len(batch.accepted_sources)
    lines = [
        "דמו להאקתון — מיועד לטפסי הדוגמה בלבד. אין להסתמך על הקובץ ללא בדיקה.",
        f"מקור: {batch.display_name} · {sources} קבצים · {len(batch.records)} טפסים · "
        f"{rows} שורות (שורה לכל מספר התחייבות) · "
        f"{batch.review_total} שדות מסומנים לבדיקה",
        "תאים כתומים דורשים בדיקה אנושית, כולל שדות ריקים. אין כאן טענת נכונות.",
    ]
    if batch.rejected_sources:
        names = ", ".join(f"{s.filename} ({s.error})" for s in batch.rejected_sources)
        lines.append(f"קבצים שלא עובדו: {names}")
    for offset, text in enumerate(lines):
        cell = ws.cell(row=row + offset, column=1, value=text)
        cell.font = Font(size=10, color="5A6B7B", italic=offset > 0)
        cell.alignment = Alignment(horizontal="right")


def workbook_bytes(batch: Batch) -> bytes:
    buffer = BytesIO()
    build_workbook(batch).save(buffer)
    return buffer.getvalue()
