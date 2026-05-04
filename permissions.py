"""
macOS permissions for TalkTalk — Input Monitoring + Accessibility.

Design principles:
  - Use the correct, non-destructive TCC probe APIs — not CGEventTapCreate.
  - On macOS 10.15+ the authoritative IM check is IOHIDCheckAccess(); the
    CGEventTap-creation probe is unreliable (returns nil on macOS 26 even
    when IM is granted).
  - Request access via native system dialogs (IOHIDRequestAccess /
    AXIsProcessTrustedWithOptions) before falling back to manual Settings.
  - Never open System Settings without the user clicking a button.
  - No time.sleep() anywhere.
"""

import ctypes
import ctypes.util
import subprocess

import objc
from AppKit import NSAlert
from Foundation import NSDictionary

# ── IOKit — Input Monitoring (authoritative TCC check) ────────────────────────

_IOKit = ctypes.cdll.LoadLibrary(
    "/System/Library/Frameworks/IOKit.framework/IOKit"
)
_IOKit.IOHIDCheckAccess.restype  = ctypes.c_uint32
_IOKit.IOHIDCheckAccess.argtypes = [ctypes.c_uint32]
_IOKit.IOHIDRequestAccess.restype  = ctypes.c_bool
_IOKit.IOHIDRequestAccess.argtypes = [ctypes.c_uint32]

_kIOHIDRequestTypeListenEvent = 1
_kIOHIDAccessTypeGranted      = 0   # other values: 1=denied, 2=unknown/not-determined

# ── ApplicationServices — Accessibility ───────────────────────────────────────

_AppServices = ctypes.cdll.LoadLibrary(
    ctypes.util.find_library("ApplicationServices")
    or "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
)
_AppServices.AXIsProcessTrusted.restype               = ctypes.c_bool
_AppServices.AXIsProcessTrusted.argtypes              = []
_AppServices.AXIsProcessTrustedWithOptions.restype    = ctypes.c_bool
_AppServices.AXIsProcessTrustedWithOptions.argtypes   = [ctypes.c_void_p]

_NSAlertFirstButtonReturn = 1000


# ── Live checks (call from any thread) ────────────────────────────────────────

def has_input_monitoring() -> bool:
    """
    Read Input Monitoring status directly from the TCC database via
    IOHIDCheckAccess().  This is the authoritative, non-destructive probe —
    it does NOT attempt to create a CGEventTap, which can return nil on
    modern macOS (26+) even when the permission is actually granted.
    """
    return _IOKit.IOHIDCheckAccess(_kIOHIDRequestTypeListenEvent) == _kIOHIDAccessTypeGranted


def has_accessibility() -> bool:
    """True if Accessibility (AX) is granted for this process."""
    return bool(_AppServices.AXIsProcessTrusted())


# ── Request access (call from main thread) ────────────────────────────────────

def request_input_monitoring() -> bool:
    """
    Show the native macOS Input Monitoring permission dialog via
    IOHIDRequestAccess().  Returns True immediately if already granted;
    shows the system prompt if not yet determined; opens Settings if
    previously denied (macOS handles this automatically).
    """
    return bool(_IOKit.IOHIDRequestAccess(_kIOHIDRequestTypeListenEvent))


def request_accessibility() -> bool:
    """
    Show the native 'TalkTalk wants to control your computer' dialog.
    Returns True if already / now granted.
    """
    opts = NSDictionary.dictionaryWithObject_forKey_(True, "AXTrustedCheckOptionPrompt")
    _AppServices.AXIsProcessTrustedWithOptions(objc.pyobjc_id(opts))
    return has_accessibility()


# ── Open the right pane directly ──────────────────────────────────────────────

def open_input_monitoring_settings():
    subprocess.run(
        ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"],
        capture_output=True,
    )


def open_accessibility_settings():
    subprocess.run(
        ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"],
        capture_output=True,
    )


# ── Fallback NSAlert dialogs (main thread only) ───────────────────────────────
# Only shown when the native request dialog isn't available / was pre-denied.

def _alert(title: str, body: str, primary: str, secondary: str = "Not Now") -> bool:
    alert = NSAlert.alloc().init()
    alert.setMessageText_(title)
    alert.setInformativeText_(body)
    alert.addButtonWithTitle_(primary)
    alert.addButtonWithTitle_(secondary)
    return alert.runModal() == _NSAlertFirstButtonReturn


def ask_input_monitoring() -> bool:
    """
    Try the native IOHIDRequestAccess dialog first.  If it doesn't result in
    a grant (e.g. previously denied), fall back to our own NSAlert that
    directs the user to the right Settings pane.
    Returns True if granted after this call.
    """
    # Native path — covers first-time prompt and already-granted cases.
    if request_input_monitoring():
        return True

    # Permission was previously denied; the native dialog won't re-appear.
    # Show our own explanation and send them to the right Settings pane.
    if _alert(
        "Allow TalkTalk to detect your hotkey",
        "Input Monitoring was denied earlier.\n\n"
        "Open Settings → Privacy & Security → Input Monitoring, "
        "then enable TalkTalk. The app detects the change automatically "
        "— no restart needed.",
        "Open Settings",
    ):
        open_input_monitoring_settings()

    return has_input_monitoring()


def ask_accessibility() -> bool:
    """
    Try the native AX prompt first.  Fall back to manual Settings if denied.
    Returns True if granted after this call.
    """
    if request_accessibility():
        return True

    if _alert(
        "Allow TalkTalk to type in other apps",
        "Accessibility access was denied earlier.\n\n"
        "Open Settings → Privacy & Security → Accessibility, "
        "then enable TalkTalk. The app detects the change automatically "
        "— no restart needed.",
        "Open Settings",
    ):
        open_accessibility_settings()

    return has_accessibility()
