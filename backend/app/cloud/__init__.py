"""Optional cloud infrastructure. Local mode continues to use SQLite and disk."""

import os


def enabled() -> bool:
    return os.environ.get("CLOUD_MODE") == "1"
