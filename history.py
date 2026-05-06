"""
Transcript history — stores recent dictations for quick replay.
Persisted at ~/.talktalk/history.json (newest first, max MAX_ENTRIES).
"""

import json
import time
from pathlib import Path

MAX_ENTRIES   = 20
_HISTORY_FILE = Path.home() / ".talktalk" / "history.json"


def load() -> list[dict]:
    """Return history entries as [{text, ts, app}, ...], newest first."""
    if _HISTORY_FILE.exists():
        try:
            data = json.loads(_HISTORY_FILE.read_text())
            if isinstance(data, list):
                return data[:MAX_ENTRIES]
        except Exception:
            pass
    return []


def add(text: str, app_name: str = "") -> None:
    entries = load()
    entries.insert(0, {"text": text, "ts": time.time(), "app": app_name})
    entries = entries[:MAX_ENTRIES]
    _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _HISTORY_FILE.write_text(json.dumps(entries, indent=2))


def clear() -> None:
    _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _HISTORY_FILE.write_text("[]")
