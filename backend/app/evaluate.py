"""`uv run evaluate` — measure readers against forms a person verified.

Re-reads every verified form (saved with "אישור הטופס", or loaded with
`uv run gold-draft` and approved) and reports, per configuration:

  * silent errors — a value shown WITHOUT orange that differs from the verified
    one. This is the number that matters; the target is zero.
  * accuracy per field, orange (review) rate, real cost and time per form.

Configurations are model × thinking depth, e.g. `opus-high,sonnet-medium`.
Costs money (real model calls), so it prints the plan and an estimate and does
nothing until run again with --yes. Held-out forms are reported separately:
they were never used to tune the prompt, so their score is the honest one.

Fields the form itself leaves ambiguous (a half-printed stamp, two different
IDs written on one form) are scored separately: orange there is the right
answer, and an unflagged value is "unflagged ambiguity", not a silent error.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app import gold
from app.config import PROJECT_ROOT, RUNS_DIR, SETTINGS
from app.consensus import normalize
from app.fields import FIELDS
from app.models import STATE_REVIEW, SourceFile
from app.pipeline import Reader, read_form
from app.providers.base import ExtractionError, ExtractionProvider

MODELS = {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-5-5"}
EFFORTS = ("low", "medium", "high", "xhigh", "max")
# אומדן לתצוגה לפני הרצה בלבד (סנטים לטופס). המספר האמיתי נמדד ומוצג אחרי ההרצה.
CENTS_ESTIMATE = {
    ("opus", "high"): 14.0, ("opus", "medium"): 8.0, ("opus", "low"): 4.5,
    ("sonnet", "high"): 6.0, ("sonnet", "medium"): 3.5, ("sonnet", "low"): 2.0,
    ("haiku", "high"): 0.4, ("haiku", "medium"): 0.25, ("haiku", "low"): 0.15,
}
REPORT = PROJECT_ROOT / "docs" / "EVALUATION.md"


@dataclass
class Config:
    code: str
    model: str
    effort: str

    @property
    def title(self) -> str:
        return f"{self.model} · חשיבה {self.effort}"

    @property
    def cents_per_form(self) -> float:
        family = next((k for k, v in MODELS.items() if v == self.model), "opus")
        return CENTS_ESTIMATE.get((family, self.effort), 14.0)

    def make(self) -> ExtractionProvider:
        from app.providers.claude_vision import ClaudeExtractionProvider

        return ClaudeExtractionProvider(model=self.model, effort=self.effort)


def parse_config(code: str) -> Config:
    """`opus-high`, `sonnet-medium`, … or `current` (the .env setting)."""
    code = code.strip().lower()
    if code in ("current", "o"):
        return Config("current", SETTINGS.reader_model, SETTINGS.claude_effort)
    family, _, effort = code.partition("-")
    if family not in MODELS or effort not in EFFORTS:
        raise ValueError(code)
    return Config(code, MODELS[family], effort)


@dataclass
class FieldScore:
    correct: int = 0
    orange: int = 0
    silent: int = 0
    ambiguous_flagged: int = 0
    ambiguous_unflagged: int = 0

    @property
    def total(self) -> int:
        return self.correct + self.orange + self.silent + self.ambiguous_flagged + self.ambiguous_unflagged


@dataclass
class Score:
    forms: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0
    failures: int = 0
    fields: dict[str, FieldScore] = field(default_factory=lambda: {f.key: FieldScore() for f in FIELDS})
    errors: list[dict] = field(default_factory=list)

    def sum(self, attr: str) -> int:
        return sum(getattr(s, attr) for s in self.fields.values())

    @property
    def total(self) -> int:
        return self.sum("total")

    def line(self) -> str:
        if not self.total:
            return "אין נתונים"
        per_form = (self.cost_usd / self.forms * 100) if self.forms else 0
        return (
            f"שגיאות שקטות {self.sum('silent'):>3} | נכון ולא סומן {self.sum('correct') / self.total:6.1%} | "
            f"כתום {(self.sum('orange') + self.sum('ambiguous_flagged')) / self.total:6.1%} | "
            f"{per_form:5.2f}¢ לטופס | {self.seconds / max(1, self.forms):5.1f} שנ׳ לטופס | כשלים {self.failures}"
        )


def _same(key: str, a: list[str], b: list[str]) -> bool:
    return sorted(normalize(key, v) for v in a) == sorted(normalize(key, v) for v in b)


def score_form(cfg: Config, form: gold.GoldForm) -> tuple[dict, list[dict]]:
    """Read one verified form with `cfg`; return its outcome per field."""
    try:
        reader = Reader(cfg.make())
    except ExtractionError as exc:
        return {"failed": True, "error": str(exc), "cost": 0.0, "seconds": 0.0, "fields": {}}, []

    source = SourceFile(index=1, filename=form.source_name)  # בלי hash: קריאה טרייה, בלי מטמון
    record = read_form(reader, form.page_png, "evaluate", source, form.page_number)
    outcome = {"failed": bool(record.error), "error": record.error, "cost": record.cost_usd or 0.0,
               "seconds": record.seconds, "fields": {}}
    details = []
    for spec in FIELDS:
        truth = form.truth.get(spec.key, [])
        got = record.fields[spec.key]
        flagged = got.state == STATE_REVIEW
        if spec.key in form.ambiguous:
            result = "ambiguous_flagged" if flagged else "ambiguous_unflagged"
        elif flagged:
            result = "orange"
        elif _same(spec.key, got.values, truth):
            result = "correct"
        else:
            result = "silent"
        outcome["fields"][spec.key] = result
        details.append({"form": form.key, "page": form.page_number, "field": spec.key, "outcome": result,
                        "truth": truth, "shown": got.values, "reasons": got.reasons})
    return outcome, details


def run_config(cfg: Config, forms: list[gold.GoldForm], parallel: int) -> tuple[Score, Score, list[dict]]:
    dev, hold, details = Score(), Score(), []
    with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        results = list(pool.map(lambda f: (f, *score_form(cfg, f)), forms))
    for form, outcome, form_details in results:
        score = hold if form.holdout else dev
        score.forms += 1
        score.cost_usd += outcome["cost"]
        score.seconds += outcome["seconds"]
        if outcome["failed"]:
            score.failures += 1
        for key, result in outcome["fields"].items():
            setattr(score.fields[key], result, getattr(score.fields[key], result) + 1)
        for d in form_details:
            if d["outcome"] == "silent":
                score.errors.append(d)
        details.extend(form_details)
    return dev, hold, details


def markdown(report: dict) -> str:
    """The honest results table, in Hebrew, for docs/EVALUATION.md."""
    lines = [
        "# תוצאות מדידה",
        "",
        f"הורץ: {report['when']} · {report['forms']} טפסים מאומתים ע\"י אדם "
        f"({report['holdout']} מהם שמורים — לא שימשו לכוונון ההוראות).",
        "",
        "**המדד הראשי: שגיאות שקטות** — ערך שגוי שהוצג **בלי** סימון כתום. המטרה: 0.",
        "\"נכון ולא סומן\" = השדה מולא אוטומטית ונכון. \"כתום\" = נשלח לבדיקת אדם (כולל שדות לא חד-משמעיים).",
        "",
        "| הגדרה | טפסים | שגיאות שקטות | נכון ולא סומן | כתום | עלות לטופס | זמן לטופס |",
        "|---|---|---|---|---|---|---|",
    ]
    for code, c in report["configs"].items():
        total = c["all"]
        n = max(1, total["total"])
        lines.append(
            f"| {c['title']} | {total['forms']} | **{total['silent']}** | {total['correct'] / n:.0%} | "
            f"{total['orange'] / n:.0%} | {total['cost_per_form_cents']:.1f}¢ | {total['seconds_per_form']:.0f} שנ׳ |"
        )
    labels = {f.key: f.label_he for f in FIELDS}
    for code, c in report["configs"].items():
        lines += ["", f"## {c['title']} — לפי שדה", "",
                  "| שדה | נכון ולא סומן | כתום | שגיאות שקטות | לא חד-משמעי שלא סומן |", "|---|---|---|---|---|"]
        for key, s in c["per_field"].items():
            n = max(1, s["total"])
            lines.append(f"| {labels[key]} | {s['correct']}/{s['total']} ({s['correct'] / n:.0%}) | "
                         f"{s['orange'] + s['ambiguous_flagged']} | **{s['silent']}** | {s['ambiguous_unflagged']} |")
        if c["silent_errors"]:
            lines += ["", "שגיאות שקטות:", ""]
            for e in c["silent_errors"]:
                lines.append(f"- עמ׳ {e['page']} · {labels[e['field']]}: הוצג `{' | '.join(e['shown']) or 'ריק'}`, "
                             f"נכון `{' | '.join(e['truth']) or 'ריק'}`")
    lines += ["", "מדגם קטן אינו הבטחת דיוק בעולם האמיתי. העלות לפי מחירון Anthropic, מחושבת מהטוקנים בפועל.", ""]
    return "\n".join(lines)


def _summary(dev: Score, hold: Score) -> dict:
    both = Score()
    for s in (dev, hold):
        both.forms += s.forms
        both.cost_usd += s.cost_usd
        both.seconds += s.seconds
        both.failures += s.failures
        for key, fs in s.fields.items():
            for attr in ("correct", "orange", "silent", "ambiguous_flagged", "ambiguous_unflagged"):
                setattr(both.fields[key], attr, getattr(both.fields[key], attr) + getattr(fs, attr))
    forms = max(1, both.forms)
    return {
        "forms": both.forms, "total": both.total, "silent": both.sum("silent"), "correct": both.sum("correct"),
        "orange": both.sum("orange") + both.sum("ambiguous_flagged"),
        "ambiguous_unflagged": both.sum("ambiguous_unflagged"), "failures": both.failures,
        "cost_per_form_cents": both.cost_usd / forms * 100, "seconds_per_form": both.seconds / forms,
    }, {k: {**v.__dict__, "total": v.total} for k, v in both.fields.items()}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(prog="evaluate", description="מדידת הקורא מול טפסים מאומתים.")
    parser.add_argument("--configs", default="current",
                        help="למשל current,opus-medium,sonnet-high,haiku-high (ברירת מחדל: ההגדרה ב-.env)")
    parser.add_argument("--parallel", type=int, default=SETTINGS.max_parallel_calls)
    parser.add_argument("--yes", action="store_true", help="להריץ בפועל (עולה כסף)")
    args = parser.parse_args(argv)

    try:
        configs = [parse_config(c) for c in args.configs.split(",") if c.strip()]
    except ValueError as exc:
        print(f"הגדרה לא מוכרת: {exc}. דוגמה: opus-high, sonnet-medium, haiku-high, current")
        return 2

    forms = gold.load_all()
    if not forms:
        print("אין עדיין טפסים מאומתים. הריצי uv run gold-draft, ואשרי טפסים במסך.")
        return 1

    held = sum(f.holdout for f in forms)
    estimate = sum(c.cents_per_form for c in configs) * len(forms)
    print(f"{len(forms)} טפסים מאומתים ({held} שמורים לבדיקה סופית בלבד).")
    for c in configs:
        print(f"  {c.code}: {c.title} — אומדן ~{c.cents_per_form * len(forms):.0f}¢")
    print(f"עלות משוערת כוללת: ~{estimate:.0f}¢ (~${estimate / 100:.2f}).")
    if not args.yes:
        print("לא בוצעה אף קריאה. להרצה בפועל: הוסיפי --yes")
        return 0

    report: dict = {"when": datetime.now().isoformat(timespec="seconds"), "forms": len(forms),
                    "holdout": held, "configs": {}}
    started = time.perf_counter()
    for cfg in configs:
        print(f"\n{cfg.code} — {cfg.title} …", flush=True)
        dev, hold, details = run_config(cfg, forms, args.parallel)
        summary, per_field = _summary(dev, hold)
        report["configs"][cfg.code] = {
            "title": cfg.title, "model": cfg.model, "effort": cfg.effort,
            "all": summary, "per_field": per_field,
            "dev": dev.line(), "holdout": hold.line(),
            "silent_errors": dev.errors + hold.errors, "details": details,
        }
        print(f"  פיתוח  ({dev.forms}): {dev.line()}")
        print(f"  שמורים ({hold.forms}): {hold.line()}")
        for err in (dev.errors + hold.errors)[:10]:
            print(f"    ✗ עמ׳ {err['page']} · {err['field']}: הוצג {err['shown']} — נכון {err['truth']}")

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    out = RUNS_DIR / f"run-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(markdown(report), encoding="utf-8")
    print(f"\nזמן כולל {time.perf_counter() - started:.0f} שנ׳. פירוט מלא: {out}")
    print(f"טבלת תוצאות: {REPORT}")
    print("סדר הבחירה: הכי מעט שגיאות שקטות → אחר כך עלות → אחר כך זמן.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
