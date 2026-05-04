"""
Romanization of non-Latin scripts via Ollama LLM.

For transliterate mode we call an Ollama model (default: gemma3:4b) with a
focused prompt so Hindi/Devanagari produces natural output like
"main theek hoon" rather than a mechanical character-for-character mapping.

Script detection uses Unicode ranges — so even if Whisper mis-labels the
language code (e.g. returns "ur" for Hindi), text in Devanagari still gets
transliterated correctly.

Falls back to unidecode if Ollama is unavailable or returns empty output.
"""

import ollama
from unidecode import unidecode

# Languages that already use Latin/Roman script — no romanization needed.
_LATIN_SCRIPT = {
    "en", "es", "fr", "de", "it", "pt", "nl", "sv", "da", "no", "fi",
    "pl", "cs", "sk", "ro", "hu", "hr", "sl", "et", "lv", "lt", "sq",
    "af", "id", "ms", "tl", "sw", "cy", "ga", "eu", "gl", "ca", "la",
}

# Unicode block ranges that are NOT Latin/ASCII — text containing these needs
# transliteration regardless of what Whisper reported as the language.
_NON_LATIN_RANGES = [
    (0x0900, 0x097F),   # Devanagari (Hindi, Marathi, Sanskrit, Nepali)
    (0x0980, 0x09FF),   # Bengali
    (0x0A00, 0x0A7F),   # Gurmukhi
    (0x0A80, 0x0AFF),   # Gujarati
    (0x0B00, 0x0B7F),   # Oriya
    (0x0B80, 0x0BFF),   # Tamil
    (0x0C00, 0x0C7F),   # Telugu
    (0x0C80, 0x0CFF),   # Kannada
    (0x0D00, 0x0D7F),   # Malayalam
    (0x0600, 0x06FF),   # Arabic / Urdu / Farsi
    (0x0400, 0x04FF),   # Cyrillic
    (0x4E00, 0x9FFF),   # CJK Unified Ideographs
    (0x3040, 0x309F),   # Hiragana
    (0x30A0, 0x30FF),   # Katakana
    (0xAC00, 0xD7AF),   # Hangul
]

_PROMPT = (
    "Transliterate the following text into Roman/Latin script using natural phonetic spelling. "
    "For Hindi, use common romanization (e.g. 'main theek hoon', not 'maina Thika hoom'). "
    "Output ONLY the transliterated text — no explanation, no labels, nothing else.\n"
    "Text: {text}\n"
    "Transliterated:"
)


def _needs_transliteration(text: str, detected_lang: str) -> bool:
    """
    Return True if text should be transliterated.
    Checks both the language code AND the actual Unicode content of the text,
    so mis-detected language codes (e.g. 'ur' for Hindi) don't slip through.
    """
    if detected_lang in _LATIN_SCRIPT:
        return False
    # Double-check: if the text itself contains non-Latin Unicode, force transliteration
    for ch in text:
        cp = ord(ch)
        for lo, hi in _NON_LATIN_RANGES:
            if lo <= cp <= hi:
                return True
    # Detected as non-Latin language but text has no non-Latin chars — already Roman
    return detected_lang not in _LATIN_SCRIPT


def transliterate(text: str, detected_lang: str = "en", model: str = "gemma3:4b") -> str:
    """
    Romanize text that is in a non-Latin script using the configured Ollama model.
    Falls back to unidecode if Ollama is unavailable.
    """
    if not text.strip():
        return text
    if not _needs_transliteration(text, detected_lang):
        return text

    try:
        resp = ollama.generate(
            model=model,
            prompt=_PROMPT.format(text=text),
            options={"temperature": 0, "num_predict": 300},
        )
        result = resp.response.strip()
        # Strip any "Transliterated:" prefix the model echoed back
        for prefix in ("Transliterated:", "Output:", "Roman:", "Result:"):
            if result.lower().startswith(prefix.lower()):
                result = result[len(prefix):].strip()
        if result:
            return result
    except Exception:
        pass

    # Fallback: unidecode (mechanical but better than nothing)
    return unidecode(text)
