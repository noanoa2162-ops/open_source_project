"""`uv run serve` — start the local demo server."""

from __future__ import annotations

import webbrowser

import uvicorn

from app.config import SETTINGS

HOST = "127.0.0.1"
PORT = 8000


def _has_key_for(model: str) -> bool:
    if model.startswith("claude"):
        return bool(SETTINGS.anthropic_api_key)
    if model.startswith(("gemini", "gemma")):
        return bool(SETTINGS.gemini_api_key)
    return bool(SETTINGS.openai_api_key)


def run() -> None:
    url = f"http://{HOST}:{PORT}"
    print(f"\n  טופס למידע — דמו להאקתון")
    for model in SETTINGS.judges:
        print(f"  קורא: {model}   מפתח: {'נטען' if _has_key_for(model) else 'חסר!'}")
    if any(m.startswith("claude") for m in SETTINGS.judges):
        print(f"  עומק חשיבה של Claude: {SETTINGS.claude_effort}")
    cap = SETTINGS.max_cost_per_batch_usd
    print(f"  תקרת תקציב להעלאה: {'אין' if cap <= 0 else f'${cap:.2f}'}")
    print(
        f"  עד {SETTINGS.max_files_per_batch} קבצים ו-{SETTINGS.max_pages_per_batch}"
        " טפסים בגרירה אחת (ניתן לשינוי ב-.env)"
    )
    print(f"\n  פתחו בדפדפן: {url}\n")
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass
    uvicorn.run("app.api:app", host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    run()
