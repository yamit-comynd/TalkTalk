"""
Global hotkey listener using NSEvent monitors.

Replaces pynput's keyboard.Listener, which on macOS 26 crashes because its
background thread calls TSMGetInputSourceProperty — an HIToolbox API that
macOS 26 now requires to run on the main dispatch queue.

NSEvent.addGlobalMonitorForEventsMatchingMask:handler: fires on the main
thread, so there is no TSM assertion and no crash.

Key objects passed to the on_press / on_release callbacks are the same
pynput.keyboard.Key / KeyCode types that app.py already uses, so no other
code needs to change.
"""

from pynput.keyboard import Key, KeyCode

# macOS virtual key code (kVK_*) → pynput Key enum, for modifier keys
# detected via NSEventMaskFlagsChanged.
MODIFIER_KEYCODE_TO_KEY: dict[int, Key] = {
    58: Key.alt,        # kVK_Option        Left Option
    61: Key.alt_r,      # kVK_RightOption   Right Option
    59: Key.ctrl,       # kVK_Control       Left Control
    62: Key.ctrl_r,     # kVK_RightControl  Right Control
    56: Key.shift,      # kVK_Shift         Left Shift
    60: Key.shift_r,    # kVK_RightShift    Right Shift
    55: Key.cmd,        # kVK_Command       Left Command
    54: Key.cmd_r,      # kVK_RightCommand  Right Command
    57: Key.caps_lock,  # kVK_CapsLock
}


class GlobalKeyListener:
    """
    Drop-in replacement for pynput.keyboard.Listener for system-wide key monitoring.

    Modifier keys (Option, Control, Shift, Command, Caps Lock) are detected via
    NSEventMaskFlagsChanged.  Character keys are detected via NSEventMaskKeyDown /
    NSEventMaskKeyUp.  All callbacks fire on the main thread.
    """

    def __init__(self, on_press, on_release):
        self._on_press_cb = on_press
        self._on_release_cb = on_release
        self._monitors: list = []
        self._held: set[int] = set()  # modifier keycodes currently held
        self.daemon = True            # pynput compatibility — no thread lifecycle here

    def start(self) -> None:
        from AppKit import (
            NSEvent,
            NSEventMaskFlagsChanged,
            NSEventMaskKeyDown,
            NSEventMaskKeyUp,
        )

        def _flags_changed(event):
            kc = event.keyCode()
            key = MODIFIER_KEYCODE_TO_KEY.get(kc)
            if key is None:
                return
            # NSEventMaskFlagsChanged fires once when the modifier is pressed and
            # once when it is released — toggle held state on each event.
            if kc in self._held:
                self._held.discard(kc)
                self._on_release_cb(key)
            else:
                self._held.add(kc)
                self._on_press_cb(key)

        def _key_down(event):
            ch = _event_char(event)
            if ch:
                self._on_press_cb(KeyCode.from_char(ch))

        def _key_up(event):
            ch = _event_char(event)
            if ch:
                self._on_release_cb(KeyCode.from_char(ch))

        m1 = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            NSEventMaskFlagsChanged, _flags_changed
        )
        m2 = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            NSEventMaskKeyDown, _key_down
        )
        m3 = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            NSEventMaskKeyUp, _key_up
        )
        self._monitors = [m for m in [m1, m2, m3] if m is not None]

    def stop(self) -> None:
        from AppKit import NSEvent
        for m in self._monitors:
            NSEvent.removeMonitor_(m)
        self._monitors.clear()
        self._held.clear()


def _event_char(event) -> str | None:
    """
    Extract a normalised single character from a key event.
    Returns None for control characters, special keys, and empty events.
    Uses charactersIgnoringModifiers so 'a' + Shift still matches hotkey 'a'.
    """
    chars = event.charactersIgnoringModifiers() or event.characters()
    if not chars:
        return None
    ch = chars[0].lower()
    return ch if ord(ch) >= 32 else None
