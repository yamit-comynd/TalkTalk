"""
Custom vocabulary management.

Words and phrases stored here are passed to Whisper as an `initial_prompt`,
which biases the model toward recognising them — useful for proper nouns,
brand names, technical terms, or names that Whisper often mishears.

Stored at: ~/.talktalk/vocabulary.json
"""

import json
from pathlib import Path

VOCAB_FILE = Path.home() / ".talktalk" / "vocabulary.json"


def load() -> list[str]:
    if VOCAB_FILE.exists():
        try:
            data = json.loads(VOCAB_FILE.read_text())
            if isinstance(data, list):
                return [str(w) for w in data if w]
        except Exception:
            pass
    return []


def save(words: list[str]):
    VOCAB_FILE.parent.mkdir(parents=True, exist_ok=True)
    VOCAB_FILE.write_text(json.dumps(sorted(set(words)), indent=2))


def add(word: str):
    words = load()
    word = word.strip()
    if word and word not in words:
        words.append(word)
        save(words)


def add_many(words: list[str]) -> int:
    """Add multiple words, skipping duplicates. Returns the count of newly added words."""
    existing = load()
    existing_set = set(existing)
    new = [w for w in words if w not in existing_set]
    if new:
        save(existing + new)
    return len(new)


def remove(word: str):
    words = load()
    words = [w for w in words if w != word]
    save(words)


def as_initial_prompt() -> str | None:
    """
    Format vocabulary as a Whisper initial_prompt string.
    Returns None if vocabulary is empty.

    Whisper uses the initial_prompt as prior context — listing terms
    as a comma-separated sentence is an effective priming strategy.

    Hard cap: Whisper's initial_prompt is limited to 224 tokens (~800 chars).
    Exceeding it causes silent left-truncation, which creates garbled context
    and makes Whisper hallucinate words from the overflow. We stay well under
    that limit by greedily adding terms until the budget is consumed.
    """
    words = load()
    if not words:
        return None

    prefix = "Terminology: "
    suffix = "."
    budget = 800 - len(prefix) - len(suffix)

    included: list[str] = []
    used = 0
    for word in words:
        # +2 for ", " separator (except the first word)
        cost = len(word) + (2 if included else 0)
        if used + cost > budget:
            break
        included.append(word)
        used += cost

    if not included:
        return None
    return prefix + ", ".join(included) + suffix
