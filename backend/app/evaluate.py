"""`uv run evaluate` — measure the reader against forms a person verified.

Re-reads every verified form (saved with "שמור כמאומת") and reports:

  * silent errors — a value shown without orange that differs from the verified
    one. This is the number that matters; the target is zero.
  * accepted & correct, orange rate, real cost and time per form.

Costs money (real model calls), so it prints the plan and an estimate and does
nothing until run again with --yes. Held-out forms are reported separately.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from app import gold
from app.config import PROJECT_ROOT, RUNS_DIR, SETTINGS
from app.consensus import normalize
from app.fields import FIELDS
from app.models import STATE_REVIEW, SourceFile
from app.validators import REASON_TEXT
from app.pipeline import Reader, read_form
from app.providers.base import ExtractionError, ExtractionProvider


@dataclass
class Config:
    code: str
    title: str
    make: Callable[[], ExtractionProvider]
    cents_per_form: float  # אומדן לתצוגה לפני הרצה בלבד


def _configured() -> ExtractionProvider:
    """Exactly what the server uses now (.env)."""
    from app.pipeline import build_reader

    reader, problem = build_reader()
    if reader is None:
        raise ExtractionError(problem, fatal=True)
    return reader


CONFIGS: dict[str, Config] = {
    "O": Config("O", f"{SETTINGS.model} · חשיבה {SETTINGS.claude_effort} — ההגדרה הנוכחית",
                _configured, 15.0),
}


@dataclass
class Score:
    forms: int = 0
    fields: int = 0
    correct: int = 0
    orange: int = 0
    silent_errors: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0
    failures: int = 0
    errors: list[dict] = field(default_factory=list)

    def line(self) -> str:
        if not self.fields:
            return "אין נתונים"
        per_form = (self.cost_usd / self.forms * 100) if self.forms else 0
        return (
            f"שגיאות שקטות {self.silent_errors:>3} | נכון ומאושר {self.correct / self.fields:6.1%} | "
            f"כתום {self.orange / self.fields:6.1%} | {per_form:5.2f}¢ לטופס | "
            f"{self.seconds / max(1, self.forms):5.1f} שנ׳ לטופס | כשלים {self.failures}"
        )


def _same(key: str, a: list[str], b: list[str]) -> bool:
    return sorted(normalize(key, v) for v in a) == sorted(normalize(key, v) for v in b)


def score_form(cfg: Config, form: gold.GoldForm, score: Score, details: list[dict]) -> None:
    try:
        reader = Reader(cfg.make())
    except ExtractionError as exc:
        score.failures += 1
        details.append({"form": form.key, "error": str(exc)})
        return

    source = SourceFile(index=1, filename=form.source_name)  # בלי hash: קריאה טרייה, בלי מטמון
    # use_saved=False: תמיד קריאה של המודל — אחרת היינו מקבלים בחזרה את הערכים שאושרו ומודדים אותם מול עצמם
    record = read_form(reader, form.page_png, "evaluate", source, form.page_number, use_saved=False)
    score.cost_usd += record.cost_usd or 0.0
    score.seconds += record.seconds
    _score_record(record, form, score, details)


def _score_record(record, form: gold.GoldForm, score: Score, details: list[dict]) -> None:
    score.forms += 1
    if record.error:
        score.failures += 1

    for spec in FIELDS:
        truth = form.truth.get(spec.key, [])
        got = record.fields[spec.key]
        score.fields += 1
        if got.state == STATE_REVIEW:
            score.orange += 1
            outcome = "orange"
        elif _same(spec.key, got.values, truth):
            score.correct += 1
            outcome = "correct"
        else:
            score.silent_errors += 1
            outcome = "SILENT_ERROR"
            score.errors.append(
                {"form": form.key, "field": spec.key, "truth": truth, "shown": got.values}
            )
        details.append(
            {"form": form.key, "field": spec.key, "outcome": outcome, "truth": truth,
             "values": got.values, "reasons": got.reasons}
        )


# ------------------------------------------------------------ מדידה בחינם, מקריאות שמורות
#
# `uv run evaluate --saved`: בלי שום קריאה למודל. לכל טופס מאושר — הקריאה שהמודל כבר עשה ונשמרה
# (work/read_cache), דרך אותם כללים מקומיים בדיוק (apply_reading), מול הערכים שהאדם אישר.

FIELD_LABELS = {spec.key: spec.label_he for spec in FIELDS}


def measure_saved(forms: list[gold.GoldForm]) -> dict:
    from app.models import FormRecord
    from app.pipeline import PROMPT_VERSION, apply_reading, saved_readings

    per_field = {spec.key: {"correct": 0, "orange_right": 0, "orange_wrong": 0, "silent": 0} for spec in FIELDS}
    dev, hold, details = Score(), Score(), []
    versions: dict[str, int] = {}
    missing: list[str] = []
    reasons: dict[str, dict[str, int]] = {}  # למה כתום — לכל שדה, לכל סיבה
    for form in forms:
        truth_file = form.page_png.parent / "truth.json"
        sha = json.loads(truth_file.read_text(encoding="utf-8")).get("sha256", "") if truth_file.is_file() else ""
        readings = saved_readings(form.page_png, sha, form.page_number)
        if not readings:
            missing.append(form.key)
            continue
        version, reading = readings[0]
        versions[version] = versions.get(version, 0) + 1
        record = FormRecord.empty("evaluate", SourceFile(index=1, filename=form.source_name), form.page_number)
        # עותק זמני: הבדיקות המקומיות כותבות קובץ עזר ליד התמונה — תיקיית טבלת האמת לא נוגעים בה
        with tempfile.TemporaryDirectory() as scratch:
            page = Path(scratch) / "page.png"
            shutil.copyfile(form.page_png, page)
            apply_reading(record, reading, page)
        before = len(details)
        _score_record(record, form, hold if form.holdout else dev, details)
        for item in details[before:]:
            got = record.fields[item["field"]]
            bucket = per_field[item["field"]]
            if item["outcome"] == "correct":
                bucket["correct"] += 1
            elif item["outcome"] == "orange":
                right = _same(item["field"], got.values, item["truth"])
                bucket["orange_right" if right else "orange_wrong"] += 1
                for reason in item["reasons"]:
                    reasons.setdefault(item["field"], {}).setdefault(reason, 0)
                    reasons[item["field"]][reason] += 1
            else:
                bucket["silent"] += 1
    return {"dev": dev, "hold": hold, "details": details, "per_field": per_field, "reasons": reasons,
            "versions": versions, "missing": missing, "current_version": PROMPT_VERSION}


def write_report(result: dict, total_forms: int) -> str:
    dev, hold = result["dev"], result["hold"]
    measured = dev.forms + hold.forms
    rows = []
    for key, b in result["per_field"].items():
        n = sum(b.values()) or 1
        rows.append(f"| {FIELD_LABELS[key]} | {b['correct']}/{n} ({b['correct'] / n:.0%}) | {b['orange_wrong']} | "
                    f"{b['orange_right']} | **{b['silent']}** |")
    all_fields = sum(sum(b.values()) for b in result["per_field"].values()) or 1
    correct = sum(b["correct"] for b in result["per_field"].values())
    caught = sum(b["orange_wrong"] for b in result["per_field"].values())
    needless = sum(b["orange_right"] for b in result["per_field"].values())
    silent = sum(b["silent"] for b in result["per_field"].values())
    right_any = correct + needless
    hardest = sorted(result["per_field"].items(), key=lambda kv: kv[1]["correct"] / (sum(kv[1].values()) or 1))[:2]
    versions = ", ".join(f"`{v}` ({n} טפסים)" for v, n in result["versions"].items())
    silent_lines = [f"- {e['form']} · {FIELD_LABELS[e['field']]}: הוצג {e['shown']}, נכון {e['truth']}"
                    for e in dev.errors + hold.errors] or ["- אין."]
    return "\n".join([
        "# דוח דיוק: הקורא מול טפסים שאדם בדק ואישר",
        "",
        f"נוצר: {datetime.now():%d/%m/%Y %H:%M} · `uv run evaluate --saved` · **בלי קריאות למודל ובלי עלות**.",
        "",
        "## איך נמדד",
        f"- **טבלת האמת:** {total_forms} טפסים מקובצי הדוגמה שהמזכירה בדקה, תיקנה ואישרה במסך.",
        "  לא נכתבה טבלת אמת ביד ולא שימש מודל לבדוק את עצמו.",
        f"- **הקריאה הנמדדת:** הקריאה של Claude Opus 5.5 שכבר נעשתה ונשמרה, דרך אותם כללים מקומיים כמו במסך "
        f"(בדיקות ת\"ז, תאריכים, רשימה סגורה). נמדדו {measured} טפסים; ל-{len(result['missing'])} לא נמצאה קריאה שמורה.",
        f"- **גרסאות ההוראות שנמדדו:** {versions}. הגרסה הנוכחית: `{result['current_version']}`.",
        "- **שדה נחשב:** ✓ נכון = הוצג רגיל (לא כתום) וזהה לערך המאושר.",
        "  כתום = המערכת ביקשה בדיקה אנושית. שגיאה שקטה = הוצג רגיל ושגוי, וזה המספר שהכי חשוב שיהיה אפס.",
        "",
        "## תוצאות לפי שדה",
        "| שדה | ✓ נכון בלי בדיקה | כתום — באמת היה שגוי (נתפס) | כתום — היה נכון (בדיקה מיותרת) | שגיאה שקטה |",
        "|---|---|---|---|---|",
        *rows,
        "",
        "## בשורה התחתונה",
        f"- **{correct}/{all_fields} שדות ({correct / all_fields:.0%})** יצאו נכונים בלי צורך בבדיקה.",
        f"- **{right_any}/{all_fields} ({right_any / all_fields:.0%})** מהערכים שהקורא הציע היו נכונים, כולל כתומים.",
        f"- **{caught}** טעויות נתפסו וסומנו בכתום. **{silent}** עברו בשקט.",
        f"- **{needless}** שדות כתומים היו בעצם נכונים. זה המחיר של הזהירות: כמה קליקים של אישור.",
        f"- **הכי קשים:** {', '.join(FIELD_LABELS[k] for k, _ in hardest)}.",
        f"- **פיתוח ({dev.forms} טפסים):** {dev.line()}",
        f"- **טפסים שמורים לבדיקה סופית ({hold.forms}):** {hold.line()}",
        "",
        "## למה שדות סומנו בכתום",
        *[f"- **{FIELD_LABELS[k]}:** " + ", ".join(f"{REASON_TEXT.get(r, r)} ({n})" for r, n in
                                                   sorted(v.items(), key=lambda kv: -kv[1]))
          for k, v in result["reasons"].items()],
        "",
        "**הערה חשובה על נתוני הדוגמה:** מארגני האתגר שינו את הפרטים האישיים בטפסים. מספרי תעודת הזהות שאושרו",
        "בדוגמאות אינם באורך 9 ספרות, ולכן הבדיקה המקומית מסמנת אותם, בצדק, לבדיקה. על השמות המודל דיווח",
        "\"קריאה חלקית\". ייתכן שבטפסים אמיתיים, עם ת\"ז תקינות, יהיה פחות כתום בשדות האלה. זה עוד לא נמדד.",
        "",
        "## שגיאות שקטות",
        *silent_lines,
        "",
        "## מגבלות כנות",
        "- טבלת האמת קטנה, וכוללת רק טפסים מקובצי הדוגמה שאושרו. זה לא מדגם מייצג של כל הטפסים במעבדה.",
        "- חלק מהקריאות נעשו בגרסת הוראות קודמת. הרצה טרייה בגרסה הנוכחית: `uv run evaluate --yes` (עולה כסף, כ-15¢ לטופס).",
        "- \"כתום\" אינו הסתברות מכוילת. הוא החלטה שמרנית של המודל ושל הבדיקות המקומיות.",
        "",
    ])


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(prog="evaluate", description="מדידת הקורא מול טפסים מאומתים.")
    parser.add_argument("--configs", default="O", help="ברירת מחדל O — ההגדרה הנוכחית")
    parser.add_argument("--yes", action="store_true", help="להריץ בפועל (עולה כסף)")
    parser.add_argument("--saved", action="store_true",
                        help="בחינם: מקריאות שכבר שמורות, בלי שום קריאה למודל; כותב EVALUATION.md")
    args = parser.parse_args(argv)

    if args.saved:
        forms = gold.load_all()
        if not forms:
            print("אין עדיין טפסים מאומתים.")
            return 1
        result = measure_saved(forms)
        report = write_report(result, len(forms))
        # בשורש הפרויקט (ב-Git, להגשה): רק מספרים — בלי תמונות ובלי טבלת האמת עצמה
        out = PROJECT_ROOT / "EVALUATION.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8")
        print(report)
        print(f"נשמר: {out}  · אף קריאה למודל, עלות 0.")
        return 0

    codes = [c.strip().upper() for c in args.configs.split(",") if c.strip()]
    unknown = [c for c in codes if c not in CONFIGS]
    if unknown:
        print(f"הגדרות לא מוכרות: {', '.join(unknown)}. קיימות: {', '.join(CONFIGS)}")
        return 2

    forms = gold.load_all()
    if not forms:
        print("אין עדיין טפסים מאומתים. בדקו טפסים במסך ולחצו 'שמור כמאומת'.")
        return 1

    held = sum(f.holdout for f in forms)
    estimate = sum(CONFIGS[c].cents_per_form for c in codes) * len(forms)
    print(f"{len(forms)} טפסים מאומתים ({held} שמורים לבדיקה סופית בלבד).")
    for c in codes:
        print(f"  {c}: {CONFIGS[c].title}")
    print(f"עלות משוערת: ~{estimate:.0f}¢ (~${estimate / 100:.2f}).")
    if not args.yes:
        print("לא בוצעה אף קריאה. להרצה בפועל: uv run evaluate --yes")
        return 0

    report: dict = {"when": datetime.now().isoformat(timespec="seconds"), "forms": len(forms), "configs": {}}
    started = time.perf_counter()
    for code in codes:
        cfg = CONFIGS[code]
        dev, hold, details = Score(), Score(), []
        for form in forms:
            score_form(cfg, form, hold if form.holdout else dev, details)
        report["configs"][code] = {
            "title": cfg.title,
            "dev": dev.__dict__,
            "holdout": hold.__dict__,
            "details": details,
        }
        print(f"\n{code} — {cfg.title}")
        print(f"  פיתוח  ({dev.forms}): {dev.line()}")
        print(f"  שמורים ({hold.forms}): {hold.line()}")
        for err in (dev.errors + hold.errors)[:10]:
            print(f"    ✗ {err['form']} · {err['field']}: הוצג {err['shown']} — נכון {err['truth']}")

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    out = RUNS_DIR / f"run-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nזמן כולל {time.perf_counter() - started:.0f} שנ׳. פירוט מלא: {out}")
    print("סדר הבחירה: הכי מעט שגיאות שקטות → אחר כך עלות → אחר כך זמן.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
