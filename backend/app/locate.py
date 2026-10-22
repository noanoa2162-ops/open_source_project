"""Check the reader's boxes against the page itself — locally, for free.

The reader's values are good, but its boxes are sometimes off: one field in the
wrong place, or (rarely) every box on the page stretched by the same factor. A
wrong box gives a zoom that shows the wrong thing.

Printed numbers can be found on the page with Tesseract. For each digit field
whose value is found, the box is confirmed or moved onto it. When at least two
fields were moved and all moved the same way, the same correction is applied to
the other boxes on that page (names included). Only boxes move — a value that was
read is never changed. Without Tesseract, or when nothing matches, every box stays
as the reader gave it.
"""

from __future__ import annotations

import json
import os
import re
import statistics
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from app.models import FormRecord, Region
from app.providers.reading import union
from app.pages import tesseract_command

# שדות עם מספרים מודפסים, שאפשר למצוא בעמוד בלי מודל. בלי תאריך: אותו תאריך מופיע
# בעמוד בכמה מקומות (תאריך הדפסה, חותמת) — הוא היה מזיז את התיבה למקום הלא נכון.
DIGIT_FIELDS = ("central_numbers", "national_id", "commitment_numbers")
MIN_DIGITS = 5  # מספר קצר יותר מופיע בעמוד בהרבה מקומות — לא עוגן אמין
OCR_TIMEOUT_SECONDS = 60
BLACK_WHITE = 150  # סף לעותק השחור-לבן: מנקה רקע אפור וכתמי סריקה
EDGE = 0.012  # סובלנות סביב תיבה, בחלק מהעמוד
AGREE = 0.08  # כמה היחס בין שני עוגנים רשאי להיות שונה ועדיין "אותה הזזה"
MIN_CHANGE = 0.10  # תיקון כללי רק כשההזזה ממשית (יותר מ-10%)
# עם עוגן אחד: הציר השני רשאי לזוז עד 20% — תיבה סביב שני ערכים יחד אינה ממורכזת על אחד מהם
SIDE_TOLERANCE = 0.20
MODEL_PIXELS = 3_580_000  # כמה פיקסלים המודל רואה לכל היותר (נמדד ב-count_tokens: 4,780 טוקנים)

# מה ידוע על מיקום כל שדה — מוצג בתקריב
CHECKED = "verified"  # הערך נמצא בעמוד בתוך התיבה
MOVED = "fixed"  # התיבה הועברה למקום שבו הערך נמצא, או תוקנה יחד עם שאר הדף
READER = "reader"  # מיקום לפי המנוע בלבד

_NOT_DIGIT = re.compile(r"\D")


@dataclass(frozen=True)
class Word:
    digits: str
    box: Region


def check_regions(record: FormRecord, image_path: Path) -> None:
    """Confirm, move or leave every field's box. Mutates `record` in place."""
    for field in record.fields.values():
        field.region_note = READER if field.region else ""
    if not any(record.fields[k].region and record.fields[k].values for k in DIGIT_FIELDS):
        return
    words = page_words(image_path)
    if not words:
        return

    anchored: set[str] = set()
    moves: list[tuple[Region, Region]] = []  # (התיבה של המנוע, המקום האמיתי)
    stayed = 0
    for key in DIGIT_FIELDS:
        field = record.fields[key]
        if not field.values or not (field.region or any(field.value_regions)):
            continue
        # כל ערך בנפרד — לכל מספר מרכזי התיבה שלו
        places = list(field.value_regions) + [None] * (len(field.values) - len(field.value_regions))
        held = moved = False
        for i, value in enumerate(field.values):
            target = _digits(value)
            given = places[i] or field.region
            if not given or len(target) < MIN_DIGITS or "?" in value:
                continue
            found = _closest([w for w in words if target in w.digits], given)
            if found is None:
                continue
            if _holds(given, found.box):
                held = True
                stayed += 1
                if len(field.values) > 1 and places[i] is None:
                    places[i] = _around(found.box)
            else:
                moves.append((given, found.box))
                places[i] = _around(found.box)
                moved = True
        if not (held or moved):
            continue
        anchored.add(key)
        field.region_note = MOVED if moved else CHECKED
        if len(field.values) > 1:
            field.value_regions = places
        if moved:
            field.region = union(places) if len(field.values) > 1 else places[0]

    # כל הדף הוזז ואף שדה לא נמצא במקומו: כמה שדות זזו באותו אופן, או שדה אחד זז בדיוק
    # ביחס הגובה/רוחב של הדף (המנוע מדד את הגובה ביחידות של הרוחב) — מתקנים גם את השאר
    if moves and not stayed:
        fix = _common_fix(moves) if len(moves) >= 2 else _aspect_fix(moves[0], image_path)
        if fix is not None:
            for key, field in record.fields.items():
                if key == "sending_entity" and field.region:
                    # לוגו/כותרת: בוחרים בין שתי התיבות את זו שבאמת יושבת על דפוס
                    moved_box = fix(field.region)
                    if _print_share(image_path, moved_box) > _print_share(image_path, field.region):
                        field.region, field.region_note = moved_box, MOVED
                    continue
                if key in anchored:
                    # ערכים שלא נמצאו בעמוד בשדה שחלק מערכיו כן נמצא — זזים יחד עם הדף
                    if field.value_regions and len(field.value_regions) == len(field.values):
                        field.value_regions = [
                            r if r is None or _holds_any(r, words, value) else fix(r)
                            for r, value in zip(field.value_regions, field.values)
                        ]
                        field.region = union(field.value_regions) or field.region
                    continue
                if field.region:
                    field.region = fix(field.region)
                    field.value_regions = [fix(r) if r else None for r in field.value_regions]
                    field.region_note = MOVED


def _holds_any(region: Region, words: list[Word], value: str) -> bool:
    target = _digits(value)
    return any(target and target in w.digits and _holds(region, w.box) for w in words)


def _print_share(image_path: Path, region: Region) -> float:
    """How much of the box is dark print (0–1) — a logo or a printed title is dense."""
    try:
        with Image.open(image_path) as page:
            width, height = page.size
            box = (int(region[0] * width), int(region[1] * height), int(region[2] * width), int(region[3] * height))
            if box[2] - box[0] < 2 or box[3] - box[1] < 2:
                return 0.0
            gray = page.crop(box).convert("L").resize((60, 60))
    except OSError:
        return 0.0
    dark = sum(1 for v in gray.getdata() if v < 110)
    return dark / 3600


def page_words(image_path: Path) -> list[Word]:
    """Numbers printed on the page and where they are, or [] if Tesseract is unavailable.

    Two passes — the page as is, and a black-and-white copy — because on noisy
    scans each finds numbers the other misses. Saved next to the page (and deleted
    with it), so a page that was already checked is not scanned again.
    """
    saved = image_path.with_name(image_path.stem + ".words.json")
    if saved.is_file():
        try:
            return [Word(w["d"], tuple(w["b"])) for w in json.loads(saved.read_text(encoding="utf-8"))]
        except (OSError, ValueError, KeyError, TypeError):
            pass
    command = tesseract_command()
    if command is None:
        return []
    try:
        with Image.open(image_path) as page:
            gray = page.convert("L")
    except OSError:
        return []
    words: list[Word] = []
    for image in (gray, gray.point(lambda v: 255 if v > BLACK_WHITE else 0)):
        tsv = _tesseract(command, image)
        if tsv is None:
            return []
        words += parse_tsv(tsv, *gray.size)
    try:
        saved.write_text(json.dumps([{"d": w.digits, "b": list(w.box)} for w in words]), encoding="utf-8")
    except OSError:
        pass
    return words


def _tesseract(command: str, image: Image.Image) -> str | None:
    handle, name = tempfile.mkstemp(suffix=".png")
    os.close(handle)
    try:
        image.save(name)
        result = subprocess.run(
            [command, name, "stdout", "--psm", "11", "tsv"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=OCR_TIMEOUT_SECONDS,
        )
        return result.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        Path(name).unlink(missing_ok=True)


def parse_tsv(tsv: str, width: int, height: int) -> list[Word]:
    """Numbers from Tesseract's TSV. A number split in two ("2648 00956") is also
    kept joined, when the parts sit side by side on the same line."""
    parts: list[Word] = []
    for line in tsv.splitlines()[1:]:
        cells = line.split("\t")
        if len(cells) != 12:
            continue
        digits = _digits(cells[11])
        if len(digits) < 2:
            continue
        try:
            left, top, w, h = (int(c) for c in cells[6:10])
        except ValueError:
            continue
        parts.append(Word(digits, (left / width, top / height, (left + w) / width, (top + h) / height)))

    parts.sort(key=lambda p: p.box[0])
    words = [p for p in parts if len(p.digits) >= MIN_DIGITS]
    for i, first in enumerate(parts):
        for second in parts[i + 1:]:
            if _side_by_side(first.box, second.box):
                joined = first.digits + second.digits
                if len(joined) >= MIN_DIGITS:
                    box = (first.box[0], min(first.box[1], second.box[1]),
                           second.box[2], max(first.box[3], second.box[3]))
                    words.append(Word(joined, box))
                break
    return words


def _side_by_side(a: Region, b: Region) -> bool:
    height = max(a[3] - a[1], b[3] - b[1])
    overlap = min(a[3], b[3]) - max(a[1], b[1])
    gap = b[0] - a[2]
    return overlap > 0.5 * height and 0 <= gap < 1.5 * height


def _digits(text: str) -> str:
    return _NOT_DIGIT.sub("", text)


def _center(box: Region) -> tuple[float, float]:
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def _holds(region: Region, box: Region) -> bool:
    x, y = _center(box)
    return region[0] - EDGE <= x <= region[2] + EDGE and region[1] - EDGE <= y <= region[3] + EDGE


def _closest(words: list[Word], region: Region) -> Word | None:
    if not words:
        return None
    rx, ry = _center(region)
    return min(words, key=lambda w: (_center(w.box)[0] - rx) ** 2 + (_center(w.box)[1] - ry) ** 2)


def _around(box: Region) -> Region:
    """The found word, with a little room so the zoom shows its surroundings."""
    pad_x, pad_y = (box[2] - box[0]) * 0.15, (box[3] - box[1]) * 0.3
    return (max(0.0, box[0] - pad_x), max(0.0, box[1] - pad_y),
            min(1.0, box[2] + pad_x), min(1.0, box[3] + pad_y))


def _common_fix(moves: list[tuple[Region, Region]]):
    """One correction that explains every move, or None.

    Per axis: either nothing changed, or the reader's position is the true one
    multiplied by the same factor (the page's sides mixed up) — the case seen in
    practice. Anything less consistent is left alone.
    """
    scales = []
    for axis in (0, 1):
        ratios = []
        for given, true in moves:
            got, real = _center(given)[axis], _center(true)[axis]
            if real < 0.02:
                return None
            ratios.append(got / real)
        middle = statistics.median(ratios)
        if any(abs(r - middle) > AGREE * middle for r in ratios):
            return None
        scales.append(1.0 if abs(middle - 1) < MIN_CHANGE else middle)
    if scales == [1.0, 1.0]:
        return None
    return _scaled(*scales)


def _aspect_fix(move: tuple[Region, Region], image_path: Path):
    """With a single anchor: correct the page only for the one known mistake —
    the height measured in units of the width (positions × height/width)."""
    try:
        with Image.open(image_path) as page:
            width, height = page.size
    except OSError:
        return None
    given, true = move
    (gx, gy), (tx, ty) = _center(given), _center(true)
    if tx < 0.02 or ty < 0.02:
        return None
    rx, ry = gx / tx, gy / ty

    def near(ratio: float, aspect: float) -> bool:
        return abs(aspect - 1) >= MIN_CHANGE and abs(ratio - aspect) <= AGREE * aspect

    if near(ry, height / width) and abs(rx - 1) < SIDE_TOLERANCE:
        return _scaled(1.0, height / width)
    if near(rx, width / height) and abs(ry - 1) < SIDE_TOLERANCE:
        return _scaled(width / height, 1.0)
    # המודל מדד בתמונה שהוקטנה אצלו (מעל ~3.5 מיליון פיקסלים): כל הדף מוזז באותו יחס בשני
    # הצירים — היחס בין מה שהוא ראה למה שנשלח. מתקנים רק כשהעוגן מראה בדיוק את היחס הזה.
    seen = _model_shrink(width, height)
    if seen < 1 - MIN_CHANGE / 2 and abs(rx - seen) <= 0.04 and abs(ry - seen) <= 0.04:
        return _scaled(seen, seen)
    return None


def _model_shrink(width: int, height: int) -> float:
    """How much smaller the model saw an image we used to send at up to 2576 px."""
    sent = min(1.0, 2576 / max(width, height))
    pixels = width * sent * height * sent
    return min(1.0, (MODEL_PIXELS / pixels) ** 0.5)


def _scaled(sx: float, sy: float):
    def fix(region: Region) -> Region:
        x0, y0, x1, y1 = region
        return (min(1.0, x0 / sx), min(1.0, y0 / sy), min(1.0, x1 / sx), min(1.0, y1 / sy))

    return fix


# ------------------------------------------------------------ הצמדת תיבה לדיו של כתב היד
# המודל ממקם כתב יד בקירוב: לפעמים על הכיתוב המודפס שליד השם, ולא על השם עצמו. כתב היד
# בטפסים הוא בעט כחול, ולכן אפשר למצוא אותו מקומית ולהצמיד אליו את התיבה. בלי מודל, בלי עלות.
INK_FIELDS = ("first_name", "last_name", "national_id", "sample_received_date")
INK_STEP = 2  # דוגמים כל פיקסל שני — מהיר, ומספיק לעט
INKED = "inked"  # התיבה הוצמדה לדיו שבטופס


def is_pen(r: int, g: int, b: int) -> bool:
    """Blue or blue-green pen. Not the pale turquoise highlighter, not black print,
    not a gray stamp, not yellow highlighter."""
    return b - r > 50 and r < 140 and (r + g + b) < 600


def snap_to_ink(record: FormRecord, image_path: Path) -> None:
    """Pull each handwritten field's box onto the pen strokes around it. A box with
    no pen ink nearby (printed values, stamps) is left exactly as it was."""
    targets = [k for k in INK_FIELDS if record.fields[k].values and record.fields[k].region
               and record.fields[k].region_note == READER]
    if not targets:
        return
    try:
        with Image.open(image_path) as page:
            page = page.convert("RGB")
            for key in targets:
                field = record.fields[key]
                snapped = ink_region(page, field.region, nearby=key in INK_GROUPS)
                if snapped is not None and _on_other_field(record, key, snapped):
                    snapped = None  # הדיו הקרוב הוא של שדה אחר (ת"ז ליד השם) — התיבה נשארת של המנוע
                if snapped is not None:
                    field.region = snapped
                    field.value_regions = [ink_region(page, r, nearby=key in INK_GROUPS) or r if r else None for r in field.value_regions]
                    field.region_note = INKED
    except OSError:
        return


# שדות שכתובים יחד (שם פרטי ושם משפחה) רשאים לחלוק דיו; ת"ז ותאריך — לא עם אף שדה אחר
INK_GROUPS = {"first_name": "name", "last_name": "name"}
OTHER_INK_SHARE = 0.4  # חלק מהתיבה החדשה שנמצא על שדה אחר — מעבר לזה, זה הדיו שלו


def _on_other_field(record: FormRecord, key: str, box: Region) -> bool:
    """Whether a snapped box landed mostly on the pen strokes of a different field —
    on a small image, an ID box the reader placed beside the name was pulled onto the name."""
    area = (box[2] - box[0]) * (box[3] - box[1])
    group = INK_GROUPS.get(key, key)
    for other in INK_FIELDS:
        region = record.fields[other].region
        if other == key or INK_GROUPS.get(other, other) == group or not region or not record.fields[other].values:
            continue
        w = min(box[2], region[2]) - max(box[0], region[0])
        h = min(box[3], region[3]) - max(box[1], region[1])
        if area > 0 and w > 0 and h > 0 and w * h / area > OTHER_INK_SHARE:
            return True
    return False


def ink_region(page: Image.Image, region: Region, nearby: bool = True) -> Region | None:
    """`nearby=False`: only ink that overlaps the box. Without it an ID box beside the name
    (small image, imprecise reader box) jumped to the closest ink — the name."""
    width, height = page.size
    x0, y0, x1, y1 = region[0] * width, region[1] * height, region[2] * width, region[3] * height
    bw, bh = max(x1 - x0, 20), max(y1 - y0, 20)
    left, right = max(0, int(x0 - 0.7 * bw)), min(width, int(x1 + 0.7 * bw))
    top, bottom = max(0, int(y0 - 0.6 * bh)), min(height, int(y1 + 0.6 * bh))
    if right - left < 4 or bottom - top < 4:
        return None
    window = page.crop((left, top, right, bottom))
    cols = (right - left) // INK_STEP
    rows = (bottom - top) // INK_STEP
    small = window.resize((cols, rows), Image.Resampling.NEAREST)
    pixels = small.load()
    ink = [[is_pen(*pixels[c, r]) for c in range(cols)] for r in range(rows)]

    # עמודות עם דיו, בשורות של התיבה (ועוד קצת) — כדי לא להיגרר לשורה אחרת
    band = range(max(0, int((y0 - 0.3 * bh - top) / INK_STEP)), min(rows, int((y1 + 0.3 * bh - top) / INK_STEP) + 1))
    col_ink = [sum(ink[r][c] for r in band) for c in range(cols)]
    gap = max(3, int(0.45 * bh / INK_STEP))
    runs = _runs([c for c in range(cols) if col_ink[c] >= 2], gap)
    if not runs:
        return None
    bx0, bx1 = (x0 - left) / INK_STEP, (x1 - left) / INK_STEP
    chosen = [run for run in runs
              if min(run[1], bx1) - max(run[0], bx0) >= 0.3 * min(run[1] - run[0] + 1, bx1 - bx0)]
    if not chosen and not nearby:
        return None
    if not chosen:
        center = (bx0 + bx1) / 2
        near = min(runs, key=lambda run: abs((run[0] + run[1]) / 2 - center))
        if abs((near[0] + near[1]) / 2 - center) > 1.2 * (bx1 - bx0):
            return None
        chosen = [near]
    c0, c1 = min(r[0] for r in chosen), max(r[1] for r in chosen)

    # שורות: רק בתוך הפס של התיבה, כדי שהתיבה לא תימתח לשורה שמעל או מתחת (ת"ז, כתובת)
    row_ink = {r: sum(ink[r][c] for c in range(c0, c1 + 1)) for r in band}
    row_runs = _runs([r for r in band if row_ink[r] >= 2], max(1, int(0.15 * bh / INK_STEP)))
    if not row_runs:
        return None
    middle = ((y0 + y1) / 2 - top) / INK_STEP
    r0, r1 = min(row_runs, key=lambda run: 0 if run[0] <= middle <= run[1] else min(abs(run[0] - middle), abs(run[1] - middle)))

    nx0, nx1 = left + c0 * INK_STEP, left + (c1 + 1) * INK_STEP
    ny0, ny1 = top + r0 * INK_STEP, top + (r1 + 1) * INK_STEP
    # בטיחות: תיבה שהתנפחה תפסה כנראה גם שורה סמוכה (ת"ז מתחת לשם, כתב יד אלכסוני).
    # אז לא נוגעים — התיבה של המודל נשארת.
    pad_x, pad_y = 0.06 * (nx1 - nx0) + 4, 0.12 * (ny1 - ny0) + 4
    fx0, fy0, fx1, fy1 = nx0 - pad_x, ny0 - pad_y, nx1 + pad_x, ny1 + pad_y
    if (fy1 - fy0) > 1.4 * (y1 - y0) + 10 or (fx1 - fx0) > 2.0 * (x1 - x0) + 10 or (fx1 - fx0) > 0.5 * width:
        return None
    return (max(0.0, fx0 / width), max(0.0, fy0 / height), min(1.0, fx1 / width), min(1.0, fy1 / height))


def _runs(indexes: list[int], gap: int) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    for i in indexes:
        if runs and i - runs[-1][1] <= gap:
            runs[-1] = (runs[-1][0], i)
        else:
            runs.append((i, i))
    return runs
