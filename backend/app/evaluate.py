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
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from app import gold
from app.config import RUNS_DIR, SETTINGS
from app.consensus import normalize
from app.fields import FIELDS
from app.models import STATE_REVIEW, SourceFile
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
    record = read_form(reader, form.page_png, "evaluate", source, form.page_number)
    score.forms += 1
    score.cost_usd += record.cost_usd or 0.0
    score.seconds += record.seconds
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


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(prog="evaluate", description="מדידת הקורא מול טפסים מאומתים.")
    parser.add_argument("--configs", default="O", help="ברירת מחדל O — ההגדרה הנוכחית")
    parser.add_argument("--yes", action="store_true", help="להריץ בפועל (עולה כסף)")
    args = parser.parse_args(argv)

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
