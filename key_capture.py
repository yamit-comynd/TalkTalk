"""
Key-capture dialog for TalkTalk.

capture_hotkey() shows a small floating panel and returns
(config_name, display_name) for the key the user presses,
or None if they cancel (Escape or close the panel).

Must be called from the main thread.
"""

import threading
import time

from AppKit import (
    NSApplication,
    NSBackingStoreBuffered,
    NSFont,
    NSMakeRect,
    NSObject,
    NSPanel,
    NSScreen,
    NSTextField,
    NSTextAlignmentCenter,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskTitled,
    NSWindowStyleMaskUtilityWindow,
)
from pynput import keyboard

# ── Human-readable names for pynput Key enum members ──────────────────────

_DISPLAY: dict[str, str] = {
    "alt":               "⌥ Option",
    "alt_l":             "⌥ Left Option",
    "alt_r":             "⌥ Right Option",
    "cmd":               "⌘ Cmd",
    "cmd_l":             "⌘ Left Cmd",
    "cmd_r":             "⌘ Right Cmd",
    "ctrl":              "^ Ctrl",
    "ctrl_l":            "^ Left Ctrl",
    "ctrl_r":            "^ Right Ctrl",
    "shift":             "⇧ Shift",
    "shift_l":           "⇧ Left Shift",
    "shift_r":           "⇧ Right Shift",
    "space":             "Space",
    "tab":               "⇥ Tab",
    "enter":             "↩ Return",
    "backspace":         "⌫ Delete",
    "delete":            "⌦ Fwd Delete",
    "caps_lock":         "Caps Lock",
    "f1":  "F1",  "f2":  "F2",  "f3":  "F3",  "f4":  "F4",
    "f5":  "F5",  "f6":  "F6",  "f7":  "F7",  "f8":  "F8",
    "f9":  "F9",  "f10": "F10", "f11": "F11", "f12": "F12",
    "f13": "F13", "f14": "F14", "f15": "F15", "f16": "F16",
    "f17": "F17", "f18": "F18", "f19": "F19", "f20": "F20",
    "up":            "↑ Up",
    "down":          "↓ Down",
    "left":          "← Left",
    "right":         "→ Right",
    "page_up":       "Page Up",
    "page_down":     "Page Down",
    "home":          "Home",
    "end":           "End",
    "num_lock":      "Num Lock",
    "scroll_lock":   "Scroll Lock",
    "print_screen":  "Print Screen",
    "pause":         "Pause",
    "insert":        "Insert",
    "media_play_pause":  "Play/Pause",
    "media_volume_mute": "Mute",
    "media_volume_up":   "Volume Up",
    "media_volume_down": "Volume Down",
}

_W, _H = 320, 130


def key_display_name(key) -> str:
    """Human-readable label for a pynput Key or KeyCode object."""
    if isinstance(key, keyboard.Key):
        return _DISPLAY.get(key.name, key.name)
    if isinstance(key, keyboard.KeyCode) and key.char:
        return key.char.upper()
    return str(key)


def config_name_to_display(cfg_name: str) -> str:
    """Convert a config-file key name string to a display label."""
    if cfg_name in _DISPLAY:
        return _DISPLAY[cfg_name]
    if len(cfg_name) == 1:
        return cfg_name.upper()
    return cfg_name


def key_config_name(key) -> str | None:
    """
    Config-file string for a pynput key (usable with _resolve_hotkey).
    Returns None for keys that cannot be represented.
    """
    if isinstance(key, keyboard.Key):
        return key.name
    if isinstance(key, keyboard.KeyCode):
        if key.char and len(key.char) == 1:
            return key.char
    return None


# ── Window-close delegate ──────────────────────────────────────────────────

class _CloseDelegate(NSObject):
    """Signals the done-event when the user clicks the panel's close button."""

    # PyObjC requires class-level declarations for instance variables
    _done:   object = None
    _result: object = None

    def windowShouldClose_(self, _sender):
        if self._done and not self._done.is_set():
            self._result[0] = None
            self._done.set()
        return True


# ── Public API ─────────────────────────────────────────────────────────────

def capture_hotkey() -> tuple[str, str] | None:
    """
    Show a floating panel and wait for the user to press a key.

    Returns (config_name, display_name) or None if cancelled.
    Must be called from the main thread.
    """
    result:   list = [None]
    captured: list = [None]   # written by pynput thread
    done = threading.Event()

    # ── Panel ─────────────────────────────────────────────────────────────
    panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, _W, _H),
        (NSWindowStyleMaskTitled
         | NSWindowStyleMaskClosable
         | NSWindowStyleMaskUtilityWindow),
        NSBackingStoreBuffered,
        False,
    )
    panel.setTitle_("Add Hotkey")
    panel.setLevel_(8)   # NSModalPanelWindowLevel
    panel.center()

    # ── Delegate (handles X / close button) ───────────────────────────────
    delegate = _CloseDelegate.alloc().init()
    delegate._done   = done
    delegate._result = result
    panel.setDelegate_(delegate)

    # ── Content views ──────────────────────────────────────────────────────
    cv = panel.contentView()

    def _add_label(text, x, y, w, h, *, bold=False, size=13, center=True):
        tf = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
        tf.setStringValue_(text)
        tf.setBezeled_(False)
        tf.setDrawsBackground_(False)
        tf.setEditable_(False)
        tf.setSelectable_(False)
        if center:
            tf.setAlignment_(NSTextAlignmentCenter)
        tf.setFont_(
            NSFont.boldSystemFontOfSize_(size) if bold
            else NSFont.systemFontOfSize_(size)
        )
        cv.addSubview_(tf)
        return tf

    _add_label("Press any key to use as a hotkey", 20, _H - 38, _W - 40, 18)
    key_label = _add_label("—", 20, _H - 78, _W - 40, 30, bold=True, size=20)
    _add_label("Press Escape or close this window to cancel",
               20, 10, _W - 40, 16, size=11)

    panel.orderFrontRegardless()
    panel.makeKeyAndOrderFront_(None)

    # ── pynput listener (background thread) ──────────────────────────────
    def on_press(key):
        if key == keyboard.Key.esc:
            result[0] = None
            done.set()
            return False   # stop listener

        cfg = key_config_name(key)
        if cfg is None:
            return          # skip unknown keys — keep listening

        captured[0] = (cfg, key_display_name(key))
        return False        # stop listener; main loop handles the rest

    listener = keyboard.Listener(on_press=on_press)
    listener.start()

    # ── Modal polling loop (main thread) ──────────────────────────────────
    # Uses Apple's "modal session" pattern so the rest of the app is blocked
    # but the run loop keeps ticking (timers, events, etc. still fire).
    app       = NSApplication.sharedApplication()
    session   = app.beginModalSessionForWindow_(panel)
    show_until: float | None = None

    while not done.is_set():
        app.runModalSession_(session)

        if captured[0] is not None and show_until is None:
            # Key captured — update label on main thread, then start countdown
            cfg, display = captured[0]
            result[0]    = (cfg, display)
            key_label.setStringValue_(display)
            show_until = time.monotonic() + 0.35   # show for 350 ms

        if show_until is not None and time.monotonic() >= show_until:
            done.set()

        time.sleep(0.005)

    listener.stop()
    app.endModalSession_(session)
    app.stopModal()
    panel.orderOut_(None)

    return result[0]
