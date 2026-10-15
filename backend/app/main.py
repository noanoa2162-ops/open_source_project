"""`uv run serve` — start the local demo server."""

from __future__ import annotations

import webbrowser

import uvicorn

from app.config import SETTINGS

HOST = "127.0.0.1"
PORT = 8000


def run() -> None:
    url = f"http://{HOST}:{PORT}"
    print(f"\n  טופס למידע — דמו להאקתון")
    print(f"  שופט A: {SETTINGS.judge_a_model}   מפתח Gemini: {'נטען' if SETTINGS.gemini_api_key else 'חסר!'}")
    print(f"  שופט B: {SETTINGS.judge_b_model}   מפתח OpenAI: {'נטען' if SETTINGS.openai_api_key else 'חסר!'}")
    print(f"  תקרת תקציב להעלאה: ${SETTINGS.max_cost_per_batch_usd:.2f}")
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
