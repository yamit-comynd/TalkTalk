"""
Text injection into the currently focused app.

Strategy:
  • Snapshot the clipboard (all content types) before writing
  • pbcopy            — writes transcribed text to the clipboard
  • CGEventPost       — sends Cmd+V directly from this process (needs Accessibility)
  • Restore snapshot  — 500 ms later, once the target app has consumed the paste,
                        the original clipboard contents are silently put back

Rapid-dictation safety:
  The first inject in a streak takes an "original" clipboard snapshot.
  Subsequent back-to-back injects reuse that same snapshot so the clipboard
  is always restored to what it was *before the first dictation*, not to an
  intermediate state written by TalkTalk.  If the user manually copies
  something during the streak, the restore is skipped.

Raises PermissionError if Accessibility is not yet granted.
"""

import subprocess
import threading
import time

from AppKit import NSData, NSPasteboard, NSPasteboardItem
from Quartz import (
    CGEventCreateKeyboardEvent,
    CGEventPost,
    CGEventSetFlags,
    kCGEventFlagMaskCommand,
    kCGHIDEventTap,
)

import ctypes
import ctypes.util

# ── Accessibility check ────────────────────────────────────────────────────────

_appservices = ctypes.cdll.LoadLibrary(
    ctypes.util.find_library("ApplicationServices")
    or "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
)
_appservices.AXIsProcessTrusted.restype  = ctypes.c_bool
_appservices.AXIsProcessTrusted.argtypes = []


def is_ax_trusted() -> bool:
    return bool(_appservices.AXIsProcessTrusted())


# ── Constants ──────────────────────────────────────────────────────────────────

_V_KEYCODE      = 9     # macOS HID keycode for 'v'
_Z_KEYCODE      = 6     # macOS HID keycode for 'z'
_PASTE_DELAY    = 0.08  # seconds between clipboard write and Cmd+V keystroke
_RESTORE_DELAY  = 0.50  # seconds to wait before restoring the original clipboard


# ── Clipboard snapshot / restore ───────────────────────────────────────────────

def _snapshot_clipboard() -> tuple[int, list[dict]]:
    """
    Capture every pasteboard item and all their data types.
    Returns (changeCount, snapshot) so the caller can detect interleaved writes.
    """
    pb = NSPasteboard.generalPasteboard()
    count = pb.changeCount()
    snapshot: list[dict] = []
    for item in (pb.pasteboardItems() or []):
        types_data: dict[str, bytes] = {}
        for t in (item.types() or []):
            d = item.dataForType_(t)
            if d is not None:
                types_data[t] = bytes(d)
        if types_data:
            snapshot.append(types_data)
    return count, snapshot


def _do_restore(target_count: int, snapshot: list[dict]) -> None:
    """
    Called in a background thread.  Waits _RESTORE_DELAY seconds, then restores
    the clipboard — but only if changeCount exactly matches target_count, meaning
    nobody (user or another dictation) has written since our last pbcopy.
    """
    time.sleep(_RESTORE_DELAY)
    pb = NSPasteboard.generalPasteboard()

    with _clip_lock:
        if pb.changeCount() != target_count:
            return  # user copied something, or another dictation already ran

        # This IS the last pending restore — reset module state.
        _reset_clip_state()

    pb.clearContents()
    if not snapshot:
        return
    items = []
    for item_data in snapshot:
        item = NSPasteboardItem.alloc().init()
        for type_str, data_bytes in item_data.items():
            item.setData_forType_(
                NSData.dataWithBytes_length_(data_bytes, len(data_bytes)),
                type_str,
            )
        items.append(item)
    pb.writeObjects_(items)


# ── Clipboard streak state ─────────────────────────────────────────────────────
# Tracks the original clipboard across rapid back-to-back dictations so the
# restore always returns to *pre-first-dictation* state, not an intermediate
# TalkTalk-written state.

_clip_lock:        threading.Lock   = threading.Lock()
_clip_base_count:  int | None       = None   # changeCount before our first pbcopy
_clip_base_snap:   list[dict]       = []     # clipboard contents before first write
_clip_own_writes:  int              = 0      # how many pbcopy calls since snapshot


def _reset_clip_state() -> None:
    """Must be called with _clip_lock held."""
    global _clip_base_count, _clip_base_snap, _clip_own_writes
    _clip_base_count = None
    _clip_base_snap  = []
    _clip_own_writes = 0


# ── Public API ─────────────────────────────────────────────────────────────────

def inject_text(text: str) -> None:
    """
    Paste *text* into the focused window, then silently restore the clipboard.

    Safe to call from any thread.

    Raises
    ------
    PermissionError
        If this process does not have Accessibility permission.
    subprocess.CalledProcessError
        If pbcopy fails (should never happen in practice).
    """
    global _clip_base_count, _clip_base_snap, _clip_own_writes

    if not text:
        return

    if not is_ax_trusted():
        raise PermissionError(
            "TalkTalk needs Accessibility permission to paste text — "
            "enable it in System Settings → Privacy & Security → Accessibility."
        )

    with _clip_lock:
        pb      = NSPasteboard.generalPasteboard()
        current = pb.changeCount()

        # Start a fresh snapshot if: this is the first dictation in a streak,
        # or the user has written to the clipboard since our last pbcopy.
        if _clip_base_count is None or current != _clip_base_count + _clip_own_writes:
            _clip_base_count, _clip_base_snap = _snapshot_clipboard()
            _clip_own_writes = 0

        # Write transcribed text.
        subprocess.run(["pbcopy"], input=text, text=True, check=True)
        _clip_own_writes += 1
        target_count = _clip_base_count + _clip_own_writes
        restore_snap = _clip_base_snap   # local ref — safe outside lock

    # Give the clipboard write a moment to propagate before sending the keystroke.
    time.sleep(_PASTE_DELAY)

    # Send Cmd+V.  Both key-down and key-up carry kCGEventFlagMaskCommand so
    # that apps which inspect modifier state on the key-up event (e.g. Chrome,
    # Electron apps) see a well-formed chord and don't drop the paste.
    for is_down in (True, False):
        evt = CGEventCreateKeyboardEvent(None, _V_KEYCODE, is_down)
        CGEventSetFlags(evt, kCGEventFlagMaskCommand)
        CGEventPost(kCGHIDEventTap, evt)

    # Restore the original clipboard once the target app has had time to read it.
    threading.Thread(
        target=_do_restore,
        args=(target_count, restore_snap),
        daemon=True,
    ).start()


def undo() -> None:
    """Send Cmd+Z to the focused app to undo the last paste. No-op if AX not granted."""
    if not is_ax_trusted():
        return
    for is_down in (True, False):
        evt = CGEventCreateKeyboardEvent(None, _Z_KEYCODE, is_down)
        CGEventSetFlags(evt, kCGEventFlagMaskCommand)
        CGEventPost(kCGHIDEventTap, evt)
