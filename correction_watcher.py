"""
Watches for user corrections after text injection and suggests vocab additions.

Workflow:
  1. Before inject_text(), caller reads the focused field: read_focused_value()
  2. After injection, caller calls watch(injected, field_before, callback)
  3. A daemon thread waits WATCH_DELAY seconds, re-reads the focused field,
     diffs the injection zone word-by-word against the injected text, and
     calls callback([(injected_word, corrected_word), ...]).

Falls back silently if AX APIs are unavailable or the focused element
doesn't expose its value (common in some non-standard text renderers).
"""

import difflib
import re
import threading
import time

WATCH_DELAY = 4.0  # seconds to wait before reading corrected text

# ---------------------------------------------------------------------------
# AX field reading
# ---------------------------------------------------------------------------

def _get_libs():
    """Lazily load CoreFoundation + ApplicationServices via ctypes."""
    import ctypes
    import ctypes.util

    cf = ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
    )
    ax = ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
    )
    return ctypes, cf, ax


def read_focused_value() -> str | None:
    """
    Read the text value of the currently focused AX element.
    Returns None if unavailable (no AX permission, unsupported element, etc.).
    """
    try:
        ctypes, cf, ax = _get_libs()

        kCFStringEncodingUTF8 = 0x08000100

        cf.CFStringCreateWithCString.restype  = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFStringGetLength.restype          = ctypes.c_long
        cf.CFStringGetLength.argtypes         = [ctypes.c_void_p]
        cf.CFStringGetCString.restype         = ctypes.c_bool
        cf.CFStringGetCString.argtypes        = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
        cf.CFRelease.restype                  = None
        cf.CFRelease.argtypes                 = [ctypes.c_void_p]

        ax.AXUIElementCreateSystemWide.restype  = ctypes.c_void_p
        ax.AXUIElementCreateSystemWide.argtypes = []
        ax.AXUIElementCopyAttributeValue.restype  = ctypes.c_int
        ax.AXUIElementCopyAttributeValue.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
        ]

        attr_focused = cf.CFStringCreateWithCString(None, b"AXFocusedUIElement", kCFStringEncodingUTF8)
        attr_value   = cf.CFStringCreateWithCString(None, b"AXValue",            kCFStringEncodingUTF8)

        system_el = ax.AXUIElementCreateSystemWide()
        if not system_el:
            return None

        focused_el = ctypes.c_void_p()
        err = ax.AXUIElementCopyAttributeValue(system_el, attr_focused, ctypes.byref(focused_el))
        cf.CFRelease(system_el)
        if err != 0 or not focused_el.value:
            return None

        value_ref = ctypes.c_void_p()
        err = ax.AXUIElementCopyAttributeValue(focused_el.value, attr_value, ctypes.byref(value_ref))
        cf.CFRelease(focused_el)
        if err != 0 or not value_ref.value:
            return None

        buf_size = cf.CFStringGetLength(value_ref.value) * 4 + 8
        # Skip very large fields (e.g. a full document) — too expensive to diff
        if buf_size > 50_000:
            cf.CFRelease(value_ref)
            return None

        buf = ctypes.create_string_buffer(buf_size)
        ok  = cf.CFStringGetCString(value_ref.value, buf, buf_size, kCFStringEncodingUTF8)
        cf.CFRelease(value_ref)
        return buf.value.decode("utf-8", errors="replace") if ok else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Diff logic
# ---------------------------------------------------------------------------

def _words(text: str) -> list[str]:
    return re.findall(r"[\w']+", text)


def _find_corrections(injected: str, field_before: str | None, field_after: str) -> list[tuple[str, str]]:
    """
    Return (injected_word, corrected_word) pairs where the user appears to have
    replaced a word from the injection with a different word.

    Uses the pre-injection field length to locate the injection zone, then
    aligns words with SequenceMatcher to find single-word replacements.
    """
    if field_before is not None:
        # Narrow to the region that the injection touched + a small buffer.
        start = max(0, len(field_before) - 10)
        end   = min(len(field_after), len(field_before) + len(injected) + 80)
        zone  = field_after[start:end]
    else:
        zone = field_after

    injected_words = _words(injected)
    zone_words     = _words(zone)

    if not injected_words or not zone_words:
        return []

    matcher = difflib.SequenceMatcher(None, injected_words, zone_words, autojunk=False)
    corrections: list[tuple[str, str]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "replace" and (i2 - i1) == 1 and (j2 - j1) == 1:
            orig      = injected_words[i1]
            corrected = zone_words[j1]
            if orig.lower() != corrected.lower():
                corrections.append((orig, corrected))
    return corrections


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def watch(
    injected_text: str,
    field_value_before: str | None,
    callback,           # callable(list[tuple[str, str]])
    delay: float = WATCH_DELAY,
) -> None:
    """
    Spawn a daemon thread that waits `delay` seconds then checks for corrections.
    `callback` is called with a (possibly empty) list of (injected_word, corrected_word).
    """
    def _run():
        time.sleep(delay)
        field_after = read_focused_value()
        if field_after is None:
            return
        corrections = _find_corrections(injected_text, field_value_before, field_after)
        if corrections:
            callback(corrections)

    threading.Thread(target=_run, daemon=True).start()
