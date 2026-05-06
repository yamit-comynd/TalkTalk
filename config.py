"""
App configuration. Settings are persisted to ~/.talktalk/config.json.
"""

import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".talktalk"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULTS: dict = {
    # "system"  — always follow macOS default input (smart: switches on dock/undock/wake)
    # "pinned"  — use the first available name from mic_priority
    "mic_mode": "system",
    # Ordered list of preferred mic names (used only in "pinned" mode).
    "mic_priority": [],
    # Whisper model size: tiny / base / small / medium / large
    # base (~145MB): ~1-1.2s latency, good accuracy for clear speech
    # tiny (~75MB):  ~0.4-0.6s, noticeably less accurate on noisy audio
    # small (~465MB): ~2-2.5s, more robust — switch back if accuracy suffers
    "model": "base",
    # Source language hint for Whisper (None = auto-detect, recommended)
    "language": None,
    # How to handle non-English speech:
    #   "translate"     — auto-detect source, output English
    #   "transliterate" — auto-detect source, romanize via LLM (e.g. Hindi → "main theek hoon")
    #   "transcribe"    — auto-detect source, output in original script
    "language_mode": "translate",
    # Ollama model used for transliteration
    "ollama_model": "gemma3:4b",
    # List of activation key names (pynput Key attribute name or single char).
    # Any key in the list can trigger recording.
    # "alt_r" = Right Option key — easy to hold, rarely conflicts
    "hotkeys": ["alt_r"],
    # Phrases that undo the last injection instead of pasting new text.
    "fix_phrases": ["fix that", "undo that", "scratch that", "delete that", "cancel that"],
    # Auto-stop recording after this many seconds of silence (0 = disabled).
    "silence_stop_delay": 0,
    # Per-app language mode overrides: {bundle_id: {language_mode: ...}}
    "profiles": {},
    # Watch for post-injection corrections and suggest vocab additions.
    "correction_watch": True,
}


def load() -> dict:
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text())
            merged = {**DEFAULTS, **data}
            # Migrate legacy single "hotkey" string → "hotkeys" list
            if "hotkey" in data and "hotkeys" not in data:
                merged["hotkeys"] = [data["hotkey"]]
            merged.pop("hotkey", None)
            # Migrate: users who had a non-empty mic_priority but no mic_mode
            # were effectively in "pinned" mode before this key existed.
            if data.get("mic_priority") and "mic_mode" not in data:
                merged["mic_mode"] = "pinned"
            return merged
        except Exception:
            pass
    return dict(DEFAULTS)


def save(config: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2))
