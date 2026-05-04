"""
Floating HUD — a small black circle at the top centre of the screen.

The spinner is a CAShapeLayer arc (270° partial circle) whose transform is
updated each tick. Because it's a vector layer with an explicit centre point,
it is always perfectly centred — no font metrics or cell padding involved.

LoadingToast — a wider pill shown while the Whisper model loads on first run.
It auto-dismisses after showing "Ready to listen" for a beat.

Sounds are generated with numpy (no audio files needed).
"""

import math
import threading

import numpy as np
import sounddevice as sd
from AppKit import (
    NSBackingStoreBuffered,
    NSColor,
    NSFont,
    NSScreen,
    NSTextField,
    NSTextAlignmentCenter,
    NSTextAlignmentLeft,
    NSView,
    NSWindow,
)
from Foundation import NSMakePoint, NSMakeRect
from Quartz import (
    CAShapeLayer,
    CATransform3DMakeRotation,
    CGColorCreateGenericRGB,
    CGPathAddArc,
    CGPathCreateMutable,
)

_SZ         = 32          # recording/processing circle diameter in points
_FLOATING   = 3           # NSWindowLevelFloating
_COLLECTION = 1 | 64      # CanJoinAllSpaces | IgnoresCycle

_ARC_R      = _SZ / 2 - 5.0   # arc radius (leaves room for stroke + antialiasing)
_LINE_W     = 2.5
_SPIN_RATE  = 0.40         # radians per tick at 20 fps ≈ 1.3 rev/sec

# ── Sounds ────────────────────────────────────────────────────────────────────

_SR  = 44_100
_VOL = 0.15  # Subtle volume (was 0.40)


def _sweep(f0: float, f1: float, dur: float) -> np.ndarray:
    n    = int(_SR * dur)
    freq = np.linspace(f0, f1, n)
    wave = np.sin(2 * np.pi * np.cumsum(freq) / _SR).astype(np.float32)
    fade = max(1, min(int(_SR * 0.010), n // 4))
    wave[:fade]  *= np.linspace(0, 1, fade)
    wave[-fade:] *= np.linspace(1, 0, fade)
    return wave * _VOL


SOUND_START_DURATION = 0.08
_SOUND_START = _sweep(380, 1050, SOUND_START_DURATION)
_SOUND_STOP  = _sweep(850,  180, 0.10)
_SOUND_READY = _sweep(440,  880, 0.12)   # soft ascending chime for "ready"


def _play(audio: np.ndarray):
    threading.Thread(
        target=sd.play,
        args=(audio, _SR),
        kwargs={"blocking": True},
        daemon=True,
    ).start()


# ── Compact recording / processing circle ─────────────────────────────────────

class HUD:
    def __init__(self):
        self._angle   = 0.0
        self._visible = False
        self._create_window()

    def _create_window(self):
        screen = NSScreen.mainScreen()
        sw     = screen.frame().size.width
        vf     = screen.visibleFrame()   # excludes menu bar + Dock

        x = (sw - _SZ) / 2
        y = vf.origin.y + vf.size.height - _SZ - 6

        self._window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, _SZ, _SZ),
            0,                        # NSWindowStyleMaskBorderless
            NSBackingStoreBuffered,
            False,
        )
        self._window.setLevel_(_FLOATING)
        self._window.setBackgroundColor_(NSColor.clearColor())
        self._window.setOpaque_(False)
        self._window.setIgnoresMouseEvents_(True)
        self._window.setHasShadow_(False)
        self._window.setCollectionBehavior_(_COLLECTION)

        # ── Black disc ────────────────────────────────────────────────────
        disc = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, _SZ, _SZ))
        disc.setWantsLayer_(True)
        disc.layer().setBackgroundColor_(CGColorCreateGenericRGB(0, 0, 0, 0.88))
        disc.layer().setCornerRadius_(_SZ / 2)
        disc.layer().setMasksToBounds_(True)

        # ── Arc spinner ───────────────────────────────────────────────────
        # 270° partial circle centred exactly at (_SZ/2, _SZ/2).
        # CAShapeLayer rotates around its anchorPoint (0.5, 0.5) which maps
        # to the centre of its bounds — so the arc always spins on-centre.
        cx = cy = _SZ / 2
        path = CGPathCreateMutable()
        CGPathAddArc(path, None, cx, cy, _ARC_R, 0, math.pi * 1.5, False)

        self._arc = CAShapeLayer.layer()
        self._arc.setFrame_(NSMakeRect(0, 0, _SZ, _SZ))
        self._arc.setPath_(path)
        self._arc.setStrokeColor_(CGColorCreateGenericRGB(1, 1, 1, 0.90))
        self._arc.setFillColor_(CGColorCreateGenericRGB(0, 0, 0, 0))
        self._arc.setLineWidth_(_LINE_W)
        self._arc.setLineCap_("round")

        disc.layer().addSublayer_(self._arc)
        self._window.setContentView_(disc)

    def _reposition(self):
        """Recalculate window position from current screen geometry.
        Called each time the HUD is about to become visible so that after a
        sleep/wake or display-arrangement change the circle lands in the right spot."""
        screen = NSScreen.mainScreen()
        sw = screen.frame().size.width
        vf = screen.visibleFrame()
        x = (sw - _SZ) / 2
        y = vf.origin.y + vf.size.height - _SZ - 6
        self._window.setFrameOrigin_(NSMakePoint(x, y))

    # ── Main-thread tick (called by rumps timer at 20 fps) ─────────────────

    def tick(self, _level: float, state: str):
        if state == "hidden":
            if self._visible:
                self._window.orderOut_(None)
                self._visible = False
            return

        if not self._visible:
            self._reposition()
            self._window.orderFrontRegardless()
            self._visible = True

        self._angle -= _SPIN_RATE
        self._arc.setTransform_(CATransform3DMakeRotation(self._angle, 0, 0, 1))

    # ── Sounds (call from any thread) ──────────────────────────────────────

    @staticmethod
    def play_start():
        _play(_SOUND_START)

    @staticmethod
    def play_stop():
        _play(_SOUND_STOP)

    @staticmethod
    def play_ready():
        _play(_SOUND_READY)


# ── Model-loading toast (wider pill with spinner + text) ──────────────────────

_TOAST_W  = 230
_TOAST_H  = 48
_TOAST_R  = _TOAST_H / 2        # fully-rounded pill
_SPIN_SZ  = 22                   # spinner diameter inside the pill
_SPIN_R   = _SPIN_SZ / 2 - 3.5  # arc radius inside spinner
_READY_DISMISS_TICKS = 55        # ~2.75 s at 20 fps before auto-hide


class LoadingToast:
    """Floating pill: shows a spinner + label while the Whisper model loads,
    then briefly shows "Ready to listen" before fading away."""

    def __init__(self):
        self._angle         = 0.0
        self._visible       = False
        self._ready_ticks   = 0   # countdown to auto-hide after "ready"
        self._state         = "hidden"  # "loading" | "ready" | "hidden"
        self._create_window()

    def _create_window(self):
        screen = NSScreen.mainScreen()
        sw     = screen.frame().size.width
        vf     = screen.visibleFrame()

        x = (sw - _TOAST_W) / 2
        y = vf.origin.y + vf.size.height - _TOAST_H - 8

        self._window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, _TOAST_W, _TOAST_H),
            0,
            NSBackingStoreBuffered,
            False,
        )
        self._window.setLevel_(_FLOATING)
        self._window.setBackgroundColor_(NSColor.clearColor())
        self._window.setOpaque_(False)
        self._window.setIgnoresMouseEvents_(True)
        self._window.setHasShadow_(True)
        self._window.setCollectionBehavior_(_COLLECTION)

        # ── Dark pill background ───────────────────────────────────────────
        pill = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, _TOAST_W, _TOAST_H))
        pill.setWantsLayer_(True)
        pill.layer().setBackgroundColor_(CGColorCreateGenericRGB(0.08, 0.08, 0.08, 0.93))
        pill.layer().setCornerRadius_(_TOAST_R)
        pill.layer().setMasksToBounds_(True)

        # ── Spinner arc ────────────────────────────────────────────────────
        spin_x = (_TOAST_H - _SPIN_SZ) / 2   # vertically centred
        spin_y = (_TOAST_H - _SPIN_SZ) / 2

        cx = cy = _SPIN_SZ / 2
        path = CGPathCreateMutable()
        CGPathAddArc(path, None, cx, cy, _SPIN_R, 0, math.pi * 1.5, False)

        self._arc = CAShapeLayer.layer()
        self._arc.setFrame_(NSMakeRect(spin_x, spin_y, _SPIN_SZ, _SPIN_SZ))
        self._arc.setPath_(path)
        self._arc.setStrokeColor_(CGColorCreateGenericRGB(1, 1, 1, 0.90))
        self._arc.setFillColor_(CGColorCreateGenericRGB(0, 0, 0, 0))
        self._arc.setLineWidth_(2.0)
        self._arc.setLineCap_("round")
        pill.layer().addSublayer_(self._arc)

        # ── Text label ─────────────────────────────────────────────────────
        label_x = _TOAST_H + 2          # leave room for the spinner
        label_w = _TOAST_W - label_x - 10
        self._label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(label_x, 0, label_w, _TOAST_H)
        )
        self._label.setStringValue_("Loading model…")
        self._label.setEditable_(False)
        self._label.setBordered_(False)
        self._label.setDrawsBackground_(False)
        self._label.setTextColor_(NSColor.whiteColor())
        self._label.setFont_(NSFont.systemFontOfSize_(13.0))
        self._label.setAlignment_(NSTextAlignmentLeft)
        pill.addSubview_(self._label)

        self._window.setContentView_(pill)

    def _reposition(self):
        """Recalculate window position from current screen geometry."""
        screen = NSScreen.mainScreen()
        sw = screen.frame().size.width
        vf = screen.visibleFrame()
        x = (sw - _TOAST_W) / 2
        y = vf.origin.y + vf.size.height - _TOAST_H - 8
        self._window.setFrameOrigin_(NSMakePoint(x, y))

    # ── API called from app.py ─────────────────────────────────────────────

    def show_loading(self):
        """Switch to loading state (call from main thread)."""
        self._state       = "loading"
        self._ready_ticks = 0
        self._label.setStringValue_("Loading model…")
        self._label.setTextColor_(NSColor.whiteColor())
        self._label.setAlignment_(NSTextAlignmentLeft)
        # Restore offset layout — leave room for the spinner on the left
        label_x = _TOAST_H + 2
        self._label.setFrame_(NSMakeRect(label_x, 0, _TOAST_W - label_x - 10, _TOAST_H))
        self._arc.setHidden_(False)
        self._reposition()
        if not self._visible:
            self._window.orderFrontRegardless()
            self._visible = True

    def show_ready(self):
        """Switch to ready state; auto-hides after ~2.75 s (call from main thread)."""
        self._state       = "ready"
        self._ready_ticks = _READY_DISMISS_TICKS
        self._label.setStringValue_("Ready to listen ✓")
        # Subtle green tint so "ready" reads visually differently from "loading"
        self._label.setTextColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(
            0.40, 1.00, 0.55, 1.0
        ))
        # Spinner is hidden — expand label to full pill width and center it
        self._label.setFrame_(NSMakeRect(0, 0, _TOAST_W, _TOAST_H))
        self._label.setAlignment_(NSTextAlignmentCenter)
        self._arc.setHidden_(True)
        self._reposition()
        if not self._visible:
            self._window.orderFrontRegardless()
            self._visible = True

    def hide(self):
        """Immediately hide the toast (call from main thread)."""
        self._state = "hidden"
        if self._visible:
            self._window.orderOut_(None)
            self._visible = False

    # ── Main-thread tick (called by rumps timer at 20 fps) ─────────────────

    def tick(self):
        if self._state == "hidden":
            if self._visible:
                self._window.orderOut_(None)
                self._visible = False
            return

        if not self._visible:
            self._reposition()
            self._window.orderFrontRegardless()
            self._visible = True

        if self._state == "loading":
            self._angle -= _SPIN_RATE
            self._arc.setTransform_(CATransform3DMakeRotation(self._angle, 0, 0, 1))
        elif self._state == "ready":
            if self._ready_ticks > 0:
                self._ready_ticks -= 1
            else:
                self.hide()
