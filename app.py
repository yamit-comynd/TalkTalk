"""
TalkTalk — menu bar dictation app.

Hold the Right Option key (⌥ right) to record.
Release to transcribe and inject text into the active app.

On first launch two permissions are requested via native NSAlert dialogs:
  • Input Monitoring  — to detect the hotkey system-wide
  • Accessibility     — to inject text into the active app
Permissions are detected automatically after the user toggles them on;
no restart is ever needed.
"""

import logging
import logging.handlers
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import rumps
from pynput import keyboard

# ── Log file ───────────────────────────────────────────────────────────────────
# PyInstaller spawns helper subprocesses (resource_tracker, multiprocessing
# workers) that re-execute module-level code.  Detect them by their argv so
# they don't truncate the main app's log.
_LOG_PATH = os.path.expanduser("~/.talktalk/talktalk.log")
os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
_is_subprocess = any(
    kw in " ".join(sys.argv)
    for kw in ("resource_tracker", "semaphore_tracker", "-c ", "-B -S")
)
_log_handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
if not _is_subprocess:
    _log_handlers.insert(0, logging.handlers.RotatingFileHandler(
        _LOG_PATH, maxBytes=512 * 1024, backupCount=1, encoding="utf-8",
    ))
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=_log_handlers,
)
log = logging.getLogger("talktalk")
log.info("TalkTalk starting (pid=%d)", os.getpid())

import re
import threading

import ollama

import config
import correction_watcher
import device_manager
import enhancer
import history
import key_capture
import permissions
import vocabulary
from hud import HUD, LoadingToast, SOUND_START_DURATION
from injector import inject_text, undo as undo_last_paste
from recorder import AudioRecorder
from transcriber import Transcriber

# Menu bar title states
_IDLE       = "TT"
_RECORDING  = "● TT"
_PROCESSING = "… TT"
_LOADING    = "◌ TT"   # shown while Whisper model initialises on first run

# How often to check if a higher-priority mic has become available (seconds)
_DEVICE_CHECK_INTERVAL = 5
# HUD animation rate (seconds) — 20fps
_HUD_TICK = 0.05
# Silence detection: RMS*20 below this threshold counts as "no audio"
_SILENCE_LEVEL_THRESHOLD = 0.015
# Ticks at 20fps before we notify the user their mic isn't producing audio (1.5 s)
_SILENCE_TICKS_THRESHOLD = 30
# Wake-from-sleep detection: if the 2s heartbeat timer fires more than this
# many seconds late, the system must have been sleeping in the gap.
_WAKE_HEARTBEAT   = 2.0
_WAKE_GAP_THRESH  = 8.0

# Silence auto-stop delay options shown in the submenu (label → seconds, 0 = off)
_SILENCE_STOP_OPTIONS = [
    ("Off",       0),
    ("1 second",  1),
    ("2 seconds", 2),
    ("3 seconds", 3),
]

# Phrases (lowercased, punctuation stripped) that trigger undo instead of paste
_DEFAULT_FIX_PHRASES = frozenset({
    "fix that", "undo that", "scratch that", "delete that", "cancel that",
})

# Language mode display names
_LANG_MODES = {
    "translate":     "Translate to English",
    "transliterate": "Transliterate (Romanize)",
    "transcribe":    "Original Script",
}

# Whisper model options shown in the menu
_WHISPER_MODELS = [
    ("tiny",   "tiny   (~75MB  · ~0.5s)"),
    ("base",   "base   (~145MB · ~1.1s)"),
    ("small",  "small  (~465MB · ~2.3s)"),
    ("medium", "medium (~1.5GB · ~5s)"),
]



def _resolve_hotkey(name: str):
    if hasattr(keyboard.Key, name):
        return getattr(keyboard.Key, name)
    if len(name) == 1:
        return keyboard.KeyCode.from_char(name)
    raise ValueError(f"Unknown hotkey: {name!r}")


def _resolve_hotkeys(names: list[str]) -> frozenset:
    keys = set()
    for name in names:
        try:
            keys.add(_resolve_hotkey(name))
        except ValueError:
            log.warning("Ignoring unknown hotkey: %r", name)
    return frozenset(keys)


def _get_resource_path(filename: str) -> str | None:
    """Return path to a bundled resource file, or None if not found."""
    # PyInstaller bundles resources in _MEIPASS ( Contents/Resources in .app)
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        bundle_dir = sys._MEIPASS
    else:
        bundle_dir = os.path.dirname(os.path.abspath(__file__))

    # In PyInstaller bundle, data files are at root of _MEIPASS
    icon_path = os.path.join(bundle_dir, filename)
    if os.path.exists(icon_path):
        return icon_path

    # Development mode: look in assets directory
    icon_path = os.path.join(bundle_dir, 'assets', filename)
    if os.path.exists(icon_path):
        return icon_path

    return None


class TalkTalkApp(rumps.App):
    def __init__(self):
        icon_path = _get_resource_path('menubar.icns')
        super().__init__(_IDLE, icon=icon_path, quit_button=None)

        self._cfg = config.load()
        self._hotkeys       = _resolve_hotkeys(self._cfg["hotkeys"])
        self._recording     = False
        self._recording_key = None   # which hotkey started the current recording
        self._recorder_started = False
        self._executor = ThreadPoolExecutor(max_workers=1)

        # HUD state — written from any thread, read by _hud_tick (main thread)
        self._hud_state = "hidden"   # "hidden" | "recording" | "processing"

        # Model-loading state
        self._model_ready     = False   # True only when Whisper loaded successfully
        self._model_load_done = False   # True when load attempt finished (success or fail)

        # Silence detection — incremented each 20fps tick while recording but silent
        self._silence_ticks    = 0
        self._silence_notified = False

        # Mic setup — in system mode follow macOS default; in pinned mode use priority list
        if self._cfg.get("mic_mode", "system") == "system":
            self._active_device_idx  = None
            self._active_device_name = device_manager.get_system_default_name()
        else:
            self._active_device_idx, self._active_device_name = device_manager.resolve_device(
                self._cfg["mic_priority"]
            )
            # If pinned device isn't available yet, fall back to system default
            if self._active_device_idx is None:
                self._active_device_name = device_manager.get_system_default_name()

        try:
            self.recorder = AudioRecorder(device=self._active_device_idx)
        except Exception as exc:
            log.warning("Could not initialise recorder at startup: %s — using fallback", exc)
            self.recorder = AudioRecorder(device=None)

        # Transcriber — loaded eagerly in a background thread so the user
        # sees a visual indicator rather than a silent freeze on first use.
        self._transcriber = None
        self._transcriber_model_size = self._cfg["model"]

        # Last injected text — used by "fix that" and correction watcher
        self._last_injected: str = ""

        # Silence auto-stop — tracks whether mic ever produced audio this recording
        self._recorder_had_audio = False

        # Flag set by the thread-pool worker; read by _hud_tick on the main thread
        # to trigger a history menu rebuild (NSMenu must only be touched on main thread).
        self._history_needs_rebuild = False

        # Per-app profiles — tracks frontmost app (updated by _check_frontmost_app timer)
        self._current_bundle_id    = ""
        self._current_app_name     = ""
        self._pre_profile_settings: dict = {}  # settings saved before profile was applied

        # Last known set of input device names — used by _do_check_device to detect
        # plug/unplug events without rebuilding the mic menu on every 5-second tick.
        self._last_known_inputs: frozenset = frozenset()

        # When non-None, _hud_tick will call _start_key_listener() on the main
        # thread once monotonic time passes this value.  Used to defer the restart
        # after wake-from-sleep so TSMGetInputSourceProperty (pynput init) is called
        # on the main thread, not a background thread.
        self._pending_listener_restart_at: float | None = None

        # Build submenus
        self._mic_menu       = rumps.MenuItem("Microphone")
        self._lang_menu      = rumps.MenuItem("Language")
        self._whisper_menu   = rumps.MenuItem("Whisper Model")
        self._llm_menu       = rumps.MenuItem("LLM Model")
        self._hotkeys_menu   = rumps.MenuItem("Hotkeys")
        self._profiles_menu  = rumps.MenuItem("App Profiles")
        self._silence_menu   = rumps.MenuItem("Silence Auto-Stop")
        self._history_menu   = rumps.MenuItem("Recent Dictations")

        self.menu = [
            self._mic_menu,
            self._lang_menu,
            self._whisper_menu,
            self._llm_menu,
            self._hotkeys_menu,
            self._profiles_menu,
            None,
            self._history_menu,
            None,
            self._silence_menu,
            None,
            rumps.MenuItem("Add vocabulary…",    callback=self._add_vocab),
            rumps.MenuItem("Edit vocabulary…",   callback=self._edit_vocab),
            rumps.MenuItem("Import vocabulary…", callback=self._import_vocab),
            None,
            rumps.MenuItem("Quit TalkTalk", callback=rumps.quit_application),
        ]

        # HUD + loading toast — must be created after self.menu
        # (NSApplication needs to be running first)
        self._hud          = HUD()
        self._loading_toast = LoadingToast()

        # Populate menus after NSMenu is initialised
        self._rebuild_mic_menu()
        self._rebuild_lang_menu()
        self._rebuild_whisper_menu()
        self._rebuild_llm_menu()
        self._rebuild_hotkeys_menu()
        self._rebuild_profiles_menu()
        self._rebuild_silence_menu()
        self._rebuild_history_menu()

        # Snapshot permission state at launch so the poll timer can detect
        # transitions from denied → granted without false-positive notifications.
        self._im_granted = permissions.has_input_monitoring()
        self._ax_granted = permissions.has_accessibility()

        # Start key listener only if Input Monitoring is already granted.
        # If not, _permission_poll will start it the moment the user toggles it on.
        self._listener = None
        if self._im_granted:
            self._start_key_listener()

        # Kick off background model load — UI feedback via _hud_tick
        self.title = _LOADING
        self._loading_toast.show_loading()
        self._executor.submit(self._preload_model)

    @property
    def transcriber(self):
        """Return the transcriber; always pre-loaded by startup background thread."""
        return self._transcriber

    def _preload_model(self):
        """Load the Whisper model in a background thread (called once at startup)."""
        log.info("Pre-loading Whisper model: %r", self._transcriber_model_size)
        try:
            self._transcriber = Transcriber(
                model_size=self._transcriber_model_size,
                language=None,
            )
            log.info("Whisper model loaded")
            self._model_ready = True
        except Exception as exc:
            log.error("Model load failed: %s", exc, exc_info=True)
        finally:
            # Signal the HUD tick that the load attempt is complete.
            self._model_load_done = True

    def _reload_transcriber(self, model_size: str):
        """Reload transcriber with a new model size (shows loading toast while busy)."""
        self._transcriber_model_size = model_size
        self._transcriber  = None
        self._model_ready     = False
        self._model_load_done = False
        self.title = _LOADING
        self._loading_toast.show_loading()
        self._executor.submit(self._preload_model)

    # ------------------------------------------------------------------
    # HUD animation timer (main thread, 20fps)
    # ------------------------------------------------------------------

    @rumps.timer(_HUD_TICK)
    def _hud_tick(self, _):
        # Detect when background model load completes
        if self._model_load_done and self._loading_toast._state == "loading":
            if self._model_ready:
                self._loading_toast.show_ready()
                HUD.play_ready()
            else:
                self._loading_toast.hide()
                rumps.notification(
                    title="TalkTalk — Model Load Failed",
                    subtitle="Whisper could not be loaded",
                    message="Check disk space or internet (first download). "
                            "See ~/.talktalk/talktalk.log for details.",
                )

        # Title management — must only happen on the main thread.
        # Background threads (CGEventTap, executor) update _hud_state only; we derive
        # self.title here so AppKit's NSStatusItem._adjustLength is never called from a
        # performSelectorOnMainThread: dispatch, which crashes on macOS 26.
        if self._loading_toast._state == "loading":
            target_title = _LOADING
        elif self._hud_state == "recording":
            target_title = _RECORDING
        elif self._hud_state == "processing":
            target_title = _PROCESSING
        else:
            target_title = _IDLE
        if self.title != target_title:
            self.title = target_title

        # Silence detection and auto-stop.
        if self._hud_state == "recording":
            if self.recorder.current_level >= _SILENCE_LEVEL_THRESHOLD:
                self._silence_ticks    = 0
                self._silence_notified = False
                self._recorder_had_audio = True
            else:
                self._silence_ticks += 1
                # No audio ever detected → likely a broken/wrong mic device.
                if (not self._recorder_had_audio
                        and self._silence_ticks >= _SILENCE_TICKS_THRESHOLD
                        and not self._silence_notified):
                    self._silence_notified = True
                    rumps.notification(
                        title="TalkTalk — No Audio",
                        subtitle="Microphone not responding",
                        message="Nothing is being captured. "
                                "Open Microphone in the menu to switch.",
                    )
                # Audio was detected and then went silent → auto-stop if configured.
                if self._recorder_had_audio:
                    stop_delay = self._cfg.get("silence_stop_delay", 0)
                    if stop_delay > 0:
                        stop_ticks = int(stop_delay / _HUD_TICK)
                        if self._silence_ticks >= stop_ticks:
                            log.info("Silence auto-stop after %.1fs of silence", stop_delay)
                            self._stop_recording()
        else:
            self._silence_ticks      = 0
            self._silence_notified   = False
            self._recorder_had_audio = False

        self._hud.tick(self.recorder.current_level, self._hud_state)
        self._loading_toast.tick()

        # Deferred history menu rebuild — set by the thread-pool worker after
        # a successful injection; executed here on the main thread.
        if self._history_needs_rebuild:
            self._history_needs_rebuild = False
            self._rebuild_history_menu()

        # Deferred key listener restart — set by _on_system_wake so that
        # pynput's TSMGetInputSourceProperty call (during listener init) happens
        # on the main thread rather than a threading.Timer background thread,
        # which crashes on macOS 26.
        if (self._pending_listener_restart_at is not None
                and time.monotonic() >= self._pending_listener_restart_at):
            self._pending_listener_restart_at = None
            log.info("Restarting key listener (deferred to main thread)")
            self._start_key_listener()

    # ------------------------------------------------------------------
    # Permission setup — one-shot timer (fires once after run loop starts)
    # ------------------------------------------------------------------

    @rumps.timer(0.8)
    def _permission_setup_once(self, sender):
        """
        One-shot: fires 0.8 s after startup so the menu bar icon is settled.
        Checks both permissions and shows the native request dialog for each
        missing one — sequentially, never simultaneously.
        The poll timer handles detection of grants made after this point.
        """
        sender.stop()

        im_ok = permissions.has_input_monitoring()
        ax_ok = permissions.has_accessibility()
        log.info("Startup permissions — IM: %s  AX: %s",
                 "ok" if im_ok else "missing",
                 "ok" if ax_ok else "missing")

        if not im_ok:
            permissions.ask_input_monitoring()
            # ask_* tries native dialog first, falls back to NSAlert + Settings.
            # Either way, the poll timer below will detect the grant.

        if not ax_ok:
            permissions.ask_accessibility()

    # ------------------------------------------------------------------
    # Permission poll — 1 s tick until both confirmed, restartable
    # ------------------------------------------------------------------

    @rumps.timer(1.0)
    def _permission_poll(self, sender):
        """
        Detects permission grants (and mid-session revocations).
        Stops when both are confirmed; _resume_permission_poll() restarts it.
        """
        self._poll_timer = sender

        im_ok = permissions.has_input_monitoring()
        ax_ok = permissions.has_accessibility()

        if im_ok and not self._im_granted:
            log.info("Input Monitoring granted — starting key listener")
            self._im_granted = True
            self._start_key_listener()
            rumps.notification(
                title="TalkTalk",
                subtitle="Input Monitoring enabled ✓",
                message="Your hold-to-talk hotkey is now active.",
            )

        if ax_ok and not self._ax_granted:
            log.info("Accessibility granted")
            self._ax_granted = True
            rumps.notification(
                title="TalkTalk",
                subtitle="Accessibility enabled ✓",
                message="TalkTalk can now type transcribed text into other apps.",
            )

        if im_ok and ax_ok:
            sender.stop()

    def _resume_permission_poll(self):
        """Restart the poll after a mid-session revocation."""
        if hasattr(self, "_poll_timer"):
            self._poll_timer.start()

    # ------------------------------------------------------------------
    # Sleep / wake recovery
    # ------------------------------------------------------------------

    @rumps.timer(_WAKE_HEARTBEAT)
    def _wake_detector(self, _):
        """
        NSTimers (and therefore rumps timers) are suspended during sleep and
        fire coalesced immediately on wake.  If the gap between two consecutive
        firings exceeds _WAKE_GAP_THRESH seconds we know the system just woke,
        and we reset all sleep-sensitive state.
        """
        now = time.monotonic()
        if hasattr(self, "_last_wake_tick"):
            gap = now - self._last_wake_tick
            if gap > _WAKE_GAP_THRESH:
                log.info("Wake from sleep detected (gap=%.1fs) — resetting state", gap)
                self._on_system_wake()
        self._last_wake_tick = now

    def _on_system_wake(self):
        """Reset everything that becomes stale after a sleep/wake cycle."""
        # 1. Clear any stuck recording state (hotkey held when Mac slept, or mic
        #    opened but key released just before sleep).
        if self._recording or self._recorder_started:
            log.info("Clearing stuck recording state from before sleep")
            try:
                self.recorder.stop()
            except Exception:
                pass
        self._recording        = False
        self._recording_key    = None
        self._recorder_started = False
        self._hud_state        = "hidden"
        self.title             = _IDLE

        # 2. Force PortAudio to rescan devices (stale after sleep), THEN
        #    recreate the recorder against the fresh device list.
        device_manager.reinit_portaudio()
        self._reinit_recorder()

        # 3. Restart the key listener — pynput wraps a CGEventTap which macOS
        #    invalidates during sleep.  We delay by 2 s and route through _hud_tick
        #    so _start_key_listener() is called on the main thread.  pynput's new
        #    listener calls TSMGetInputSourceProperty (HIToolbox) during init, which
        #    on macOS 26 asserts it must run on the main dispatch queue.  Calling it
        #    from threading.Timer (background thread) crashes; _hud_tick is safe.
        if self._im_granted:
            log.info("Scheduling key listener restart (2 s, deferred to main thread via _hud_tick)")
            self._pending_listener_restart_at = time.monotonic() + 2.0

        log.info("Wake recovery complete")

    # ------------------------------------------------------------------
    # Mic / recorder helpers
    # ------------------------------------------------------------------

    def _reinit_recorder(self) -> bool:
        """Recreate the AudioRecorder, respecting the current mic mode.

        system mode  — always device=None so PortAudio follows macOS default.
        pinned mode  — use the first available priority device; if it is not
                       connected, fall back to system default and notify the user.
        Safe to call from any thread. Returns True on success.
        """
        try:
            mode = self._cfg.get("mic_mode", "system")
            if mode == "system":
                self._active_device_idx  = None
                self._active_device_name = device_manager.get_system_default_name()
                self.recorder = AudioRecorder(device=None)
            else:
                inputs_by_name = {n: i for i, n in device_manager.list_inputs()}
                pinned = (self._cfg.get("mic_priority") or [None])[0]
                if pinned and pinned in inputs_by_name:
                    self._active_device_name = pinned
                    self._active_device_idx  = inputs_by_name[pinned]
                    self.recorder = AudioRecorder(device=self._active_device_idx)
                else:
                    # Pinned device unavailable — silently fall back to system default
                    self._active_device_idx  = None
                    self._active_device_name = device_manager.get_system_default_name()
                    self.recorder = AudioRecorder(device=None)
                    if pinned:
                        rumps.notification(
                            title="TalkTalk — Mic unavailable",
                            subtitle=f"'{pinned}' not found",
                            message=f"Switched to system default: {self._active_device_name or 'auto'}.",
                        )
            log.info("Recorder reinitialised (mode=%r device=%r idx=%s)",
                     mode, self._active_device_name, self._active_device_idx)
            return True
        except Exception as exc:
            log.warning("Could not reinit recorder: %s", exc)
            return False

    def _rebuild_mic_menu(self):
        mode   = self._cfg.get("mic_mode", "system")
        try:
            inputs = device_manager.list_inputs()
        except Exception as exc:
            log.warning("list_inputs() failed in mic menu rebuild: %s", exc)
            inputs = []
        pinned = (self._cfg.get("mic_priority") or [None])[0]

        if self._mic_menu._menu is not None:
            self._mic_menu.clear()

        # ── "System Default" item at the top ──────────────────────────────
        # Shows the current effective device name in parentheses so the user
        # always knows which mic macOS is actually using.
        sys_display = self._active_device_name or "auto"
        sys_label   = f"System Default  ({sys_display})"
        sys_item    = rumps.MenuItem(sys_label, callback=self._select_system_default)
        sys_item.state = 1 if mode == "system" else 0
        self._mic_menu[sys_label] = sys_item

        # ── Separator ─────────────────────────────────────────────────────
        self._mic_menu["_mic_sep_"] = None

        # ── Individual devices (clicking one pins to that device) ─────────
        for _, name in inputs:
            item = rumps.MenuItem(name, callback=self._select_mic)
            item.state = 1 if mode == "pinned" and name == pinned else 0
            self._mic_menu[name] = item

        if not inputs:
            self._mic_menu["(no inputs found)"] = rumps.MenuItem("(no inputs found)")

    def _select_system_default(self, _):
        """Switch to system-default mode: follow macOS's current input device."""
        self._cfg["mic_mode"] = "system"
        config.save(self._cfg)
        self._reinit_recorder()
        self._rebuild_mic_menu()

    def _select_mic(self, sender):
        """Pin to a specific microphone by name."""
        name = sender.title
        self._cfg["mic_mode"]     = "pinned"
        self._cfg["mic_priority"] = device_manager.promote(self._cfg["mic_priority"], name)
        config.save(self._cfg)
        self._switch_to(name)

    def _switch_to(self, name: str):
        if self._recording:
            return
        if name == self._active_device_name:
            return
        try:
            inputs_by_name = {n: i for i, n in device_manager.list_inputs()}
        except Exception as exc:
            log.warning("list_inputs() failed in _switch_to: %s", exc)
            return
        if name not in inputs_by_name:
            return
        self._active_device_name = name
        self._active_device_idx  = inputs_by_name[name]
        self.recorder = AudioRecorder(device=self._active_device_idx)
        self._rebuild_mic_menu()

    @rumps.timer(_DEVICE_CHECK_INTERVAL)
    def _check_device(self, _):
        if self._recording:
            return
        try:
            self._do_check_device()
        except Exception as exc:
            log.warning("Device check error: %s", exc)

    def _do_check_device(self):
        mode = self._cfg.get("mic_mode", "system")
        if mode == "system":
            # Detect dock/undock: macOS may have changed which device is default.
            current_sys = device_manager.get_system_default_name()
            if current_sys and current_sys != self._active_device_name:
                log.info("System default mic changed: %r → %r",
                         self._active_device_name, current_sys)
                self._active_device_name = current_sys
                self._active_device_idx  = None
                self.recorder = AudioRecorder(device=None)
                self._rebuild_mic_menu()
                return
        else:
            best_idx, best_name = device_manager.resolve_device(self._cfg["mic_priority"])
            if best_name and best_name != self._active_device_name:
                self._switch_to(best_name)
                return
            elif not best_name and self._cfg.get("mic_priority"):
                # Pinned device just disappeared — fall back to system default.
                self._reinit_recorder()
                self._rebuild_mic_menu()
                return

        # No device switch needed. Rebuild the mic menu only if the set of
        # available inputs changed (device plugged in or unplugged) — not on
        # every tick, to avoid touching NSMenu while the menu is open.
        try:
            current_inputs = frozenset(name for _, name in device_manager.list_inputs())
        except Exception:
            return
        if current_inputs != self._last_known_inputs:
            self._last_known_inputs = current_inputs
            self._rebuild_mic_menu()

    # ------------------------------------------------------------------
    # Language submenu
    # ------------------------------------------------------------------

    def _rebuild_lang_menu(self):
        current = self._cfg.get("language_mode", "translate")
        if self._lang_menu._menu is not None:
            self._lang_menu.clear()
        for mode, label in _LANG_MODES.items():
            item = rumps.MenuItem(label, callback=self._select_lang_mode)
            item.state = 1 if mode == current else 0
            self._lang_menu[label] = item

    def _select_lang_mode(self, sender):
        label = sender.title
        mode = next(k for k, v in _LANG_MODES.items() if v == label)
        self._cfg["language_mode"] = mode
        config.save(self._cfg)
        self._rebuild_lang_menu()

    # ------------------------------------------------------------------
    # Whisper model submenu
    # ------------------------------------------------------------------

    def _rebuild_whisper_menu(self):
        current = self._cfg.get("model", "base")
        if self._whisper_menu._menu is not None:
            self._whisper_menu.clear()
        for key, label in _WHISPER_MODELS:
            item = rumps.MenuItem(label, callback=self._select_whisper_model)
            item.state = 1 if key == current else 0
            self._whisper_menu[label] = item

    def _select_whisper_model(self, sender):
        label = sender.title
        key = next(k for k, l in _WHISPER_MODELS if l == label)
        if key == self._cfg.get("model"):
            return
        self._cfg["model"] = key
        config.save(self._cfg)
        self._rebuild_whisper_menu()
        # Reload the transcriber with the new model (blocking — happens in main thread,
        # but model swap is infrequent and only on user action)
        log.info("switching Whisper model to %r", key)
        self._reload_transcriber(key)

    # ------------------------------------------------------------------
    # LLM model submenu
    # ------------------------------------------------------------------

    def _rebuild_llm_menu(self):
        current = self._cfg.get("ollama_model", "")
        if self._llm_menu._menu is not None:
            self._llm_menu.clear()

        try:
            response = ollama.list()
            models = [m.model for m in response.models]
        except Exception:
            models = []

        if models:
            for name in models:
                item = rumps.MenuItem(name, callback=self._select_llm_model)
                item.state = 1 if name == current else 0
                self._llm_menu[name] = item
        else:
            self._llm_menu["(Ollama not running)"] = rumps.MenuItem("(Ollama not running)")

        self._llm_menu["_llm_sep_"] = None
        self._llm_menu["Refresh model list"] = rumps.MenuItem(
            "Refresh model list", callback=lambda _: self._rebuild_llm_menu()
        )

    def _select_llm_model(self, sender):
        self._cfg["ollama_model"] = sender.title
        config.save(self._cfg)
        self._rebuild_llm_menu()

    # ------------------------------------------------------------------
    # Hotkeys submenu
    # ------------------------------------------------------------------

    def _rebuild_hotkeys_menu(self):
        names = self._cfg.get("hotkeys", ["alt_r"])
        if self._hotkeys_menu._menu is not None:
            self._hotkeys_menu.clear()

        for cfg_name in names:
            display = key_capture.config_name_to_display(cfg_name)
            label   = f"{display}  ✕"   # clicking removes this hotkey

            def make_remove(n):
                def _remove(_sender):
                    self._remove_hotkey(n)
                return _remove

            item = rumps.MenuItem(label, callback=make_remove(cfg_name))
            item.state = 1
            self._hotkeys_menu[label] = item

        if names:
            self._hotkeys_menu["_hotkey_sep_"] = None

        self._hotkeys_menu["Add Hotkey\u2026"] = rumps.MenuItem(
            "Add Hotkey\u2026", callback=self._add_hotkey
        )

    def _add_hotkey(self, _sender):
        captured = key_capture.capture_hotkey()
        if captured is None:
            return
        cfg_name, display = captured
        names = self._cfg.get("hotkeys", ["alt_r"])
        if cfg_name in names:
            rumps.notification(
                title="TalkTalk",
                subtitle="Already assigned",
                message=f"{display} is already a hotkey.",
            )
            return
        names.append(cfg_name)
        self._cfg["hotkeys"] = names
        config.save(self._cfg)
        self._hotkeys = _resolve_hotkeys(names)
        self._rebuild_hotkeys_menu()
        rumps.notification(
            title="TalkTalk",
            subtitle="Hotkey added",
            message=f"{display} will now trigger recording.",
        )

    def _remove_hotkey(self, cfg_name: str):
        names = self._cfg.get("hotkeys", ["alt_r"])
        if len(names) <= 1:
            rumps.notification(
                title="TalkTalk",
                subtitle="Cannot remove",
                message="You must keep at least one hotkey.",
            )
            return
        names = [n for n in names if n != cfg_name]
        self._cfg["hotkeys"] = names
        config.save(self._cfg)
        self._hotkeys = _resolve_hotkeys(names)
        self._rebuild_hotkeys_menu()

    # ------------------------------------------------------------------
    # Key listener
    # ------------------------------------------------------------------

    def _start_key_listener(self):
        # Stop any previously running listener before creating a new one.
        # This is safe to call both at startup (when self._listener is None)
        # and when restarting after Input Monitoring is granted mid-session.
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
        self._listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.daemon = True
        self._listener.start()

    def _on_press(self, key):
        if key in self._hotkeys and not self._recording and not self._model_ready:
            if self._model_load_done:
                rumps.notification(
                    title="TalkTalk — Model unavailable",
                    subtitle="Whisper failed to load",
                    message="See ~/.talktalk/talktalk.log for details.",
                )
            else:
                rumps.notification(
                    title="TalkTalk",
                    subtitle="Loading model…",
                    message="The speech model is still initialising. Try again in a moment.",
                )
            return
        if key in self._hotkeys and not self._recording:
            self._recording        = True
            self._recording_key    = key
            self._recorder_started = False
            self._recorder_had_audio = False
            self._hud_state        = "recording"
            HUD.play_start()
            # The sleep + recorder.start() are offloaded so the CGEventTap
            # callback returns immediately.  macOS can invalidate the tap
            # if its callback blocks for more than a few milliseconds.
            threading.Thread(target=self._open_mic_after_sound, daemon=True).start()

    def _open_mic_after_sound(self):
        """Wait for the start chirp to finish, then open the microphone.
        Runs in a background thread so the CGEventTap callback is never blocked."""
        time.sleep(SOUND_START_DURATION + 0.01)
        if not self._recording:
            return   # key was released before the sound finished — nothing to do
        try:
            self.recorder.start()
            self._recorder_started = True
        except Exception as exc:
            log.warning("Failed to start recorder: %s — re-initialising", exc)
            self._recording     = False
            self._recording_key = None
            self._hud_state     = "hidden"
            threading.Thread(target=self._reinit_recorder, daemon=True).start()

    def _stop_recording(self):
        """Stop the active recording and submit audio for transcription.
        Safe to call from any thread (main thread via _hud_tick, or pynput thread
        via _on_release). No-op if not currently recording."""
        if not self._recording:
            return
        self._recording = False
        HUD.play_stop()
        if not self._recorder_started:
            self._hud_state = "hidden"
            return
        try:
            audio = self.recorder.stop()
        except Exception as exc:
            log.warning("recorder.stop() failed: %s — reinitialising", exc)
            self._hud_state = "hidden"
            threading.Thread(target=self._reinit_recorder, daemon=True).start()
            return
        duration = len(audio) / self.recorder.sample_rate
        if duration < 0.3:
            self._hud_state = "hidden"
            return
        self._hud_state = "processing"
        self._executor.submit(self._transcribe_and_inject, audio)

    def _on_release(self, key):
        if key == self._recording_key and self._recording:
            self._stop_recording()

    # ------------------------------------------------------------------
    # Transcription + injection (runs in thread pool)
    # ------------------------------------------------------------------

    def _transcribe_and_inject(self, audio):
        # Snapshot the transcriber reference; _reload_transcriber can replace
        # self._transcriber on the main thread at any moment.
        transcriber = self._transcriber
        if transcriber is None:
            log.warning("Transcriber not available — dropping audio")
            self._hud_state = "hidden"
            return
        try:
            mode   = self._cfg.get("language_mode", "translate")
            task   = "translate" if mode == "translate" else "transcribe"
            prompt = vocabulary.as_initial_prompt()

            t0 = time.perf_counter()
            transcript, detected_lang = transcriber.transcribe(
                audio,
                sample_rate=self.recorder.sample_rate,
                initial_prompt=prompt,
                task=task,
            )
            t_whisper = time.perf_counter() - t0
            log.info("whisper=%.2fs lang=%r text=%r", t_whisper, detected_lang, transcript)

            if mode == "transliterate" and transcript:
                t1 = time.perf_counter()
                llm_model = self._cfg.get("ollama_model", "gemma3:4b")
                transcript = enhancer.transliterate(transcript, detected_lang, model=llm_model)
                log.info("transliterate=%.2fs result=%r", time.perf_counter() - t1, transcript)

            if not transcript:
                log.warning("whisper returned empty transcript — nothing to paste")

            if transcript and self._is_fix_phrase(transcript):
                log.info("Fix phrase detected (%r) — undoing last injection", transcript)
                undo_last_paste()
                self._last_injected = ""
                return

            if transcript:
                # Snapshot the focused field BEFORE injection for correction watching.
                field_before = None
                if self._cfg.get("correction_watch", True):
                    field_before = correction_watcher.read_focused_value()

                try:
                    inject_text(transcript)
                    log.info("injection OK")
                    self._last_injected = transcript

                    # Record in history. Menu rebuild is deferred to _hud_tick
                    # (main thread) — NSMenu must never be touched from a background thread.
                    history.add(transcript, self._current_app_name)
                    self._history_needs_rebuild = True

                    # Watch for user corrections and suggest vocab additions.
                    if self._cfg.get("correction_watch", True):
                        correction_watcher.watch(
                            transcript, field_before,
                            callback=self._on_correction_detected,
                        )
                except PermissionError as exc:
                    log.error("injection permission denied: %s", exc)
                    # AX was revoked mid-session. Reset state and restart the
                    # poll so it detects when the user re-grants it.
                    self._ax_granted = False
                    self._resume_permission_poll()
                    rumps.notification(
                        title="TalkTalk — Accessibility Needed",
                        subtitle="Permission was revoked",
                        message=(
                            "Re-enable TalkTalk in "
                            "System Settings → Privacy & Security → Accessibility."
                        ),
                    )
                except Exception as exc:
                    log.error("injection failed: %s", exc, exc_info=True)
                    rumps.notification(
                        title="TalkTalk",
                        subtitle="Could not paste text",
                        message=str(exc),
                    )
        except Exception as exc:
            log.error("transcription failed: %s", exc, exc_info=True)
            rumps.notification(
                title="TalkTalk",
                subtitle="Transcription failed",
                message=str(exc),
            )
        finally:
            self._hud_state = "hidden"

    def _is_fix_phrase(self, text: str) -> bool:
        """Return True if the transcript is a "fix that" style undo command."""
        normalized = re.sub(r"[^\w\s]", "", text).strip().lower()
        phrases = {p.lower() for p in self._cfg.get("fix_phrases", list(_DEFAULT_FIX_PHRASES))}
        return normalized in phrases

    def _on_correction_detected(self, corrections: list[tuple[str, str]]) -> None:
        """Called from correction_watcher daemon thread when user edits are detected."""
        for orig, corrected in corrections:
            log.info("Correction detected: %r → %r", orig, corrected)
            rumps.notification(
                title="TalkTalk — Learn correction?",
                subtitle=f'"{orig}" → "{corrected}"',
                message=f'Say "add to vocabulary" or open Add Vocabulary… to add "{corrected}".',
            )
            # Auto-add if the corrected word looks like a proper noun (capitalised).
            if corrected and corrected[0].isupper() and corrected not in vocabulary.load():
                vocabulary.add(corrected)
                log.info("Auto-added correction %r to vocabulary", corrected)

    # ------------------------------------------------------------------
    # Vocabulary menu actions
    # ------------------------------------------------------------------

    def _add_vocab(self, _):
        from AppKit import NSApp
        NSApp.activateIgnoringOtherApps_(True)
        window = rumps.Window(
            message="Enter a word or phrase Whisper should recognise correctly:",
            title="Add Vocabulary",
            ok="Add",
            cancel="Cancel",
            dimensions=(320, 28),
        )
        response = window.run()
        if response.clicked and response.text.strip():
            vocabulary.add(response.text.strip())
            rumps.notification(
                title="TalkTalk",
                subtitle="Vocabulary updated",
                message=f"Added: {response.text.strip()!r}",
            )

    def _edit_vocab(self, _):
        from AppKit import NSApp
        NSApp.activateIgnoringOtherApps_(True)
        words = vocabulary.load()
        window = rumps.Window(
            message="One word or phrase per line. Edit, then click Save.",
            title="Edit Vocabulary",
            default_text="\n".join(words),
            ok="Save",
            cancel="Cancel",
            dimensions=(320, 180),
        )
        response = window.run()
        if response.clicked:
            vocabulary.save([w.strip() for w in response.text.splitlines() if w.strip()])

    def _import_vocab(self, _):
        import csv
        from AppKit import NSApp, NSOpenPanel

        NSApp.activateIgnoringOtherApps_(True)
        panel = NSOpenPanel.openPanel()
        panel.setTitle_("Import Vocabulary")
        panel.setMessage_("Select a CSV file — one entry per cell (any layout).")
        panel.setAllowedFileTypes_(["csv"])
        panel.setAllowsMultipleSelection_(False)
        panel.setCanChooseDirectories_(False)

        if panel.runModal() != 1:  # 1 == NSModalResponseOK
            return

        path = panel.URL().path()
        try:
            with open(path, newline="", encoding="utf-8-sig") as f:
                reader = csv.reader(f)
                # Flatten all cells from all rows — handles both "one per line"
                # and "all on one line, comma-separated" layouts.
                new_words = [cell.strip() for row in reader for cell in row if cell.strip()]
        except Exception as e:
            rumps.notification(
                title="TalkTalk",
                subtitle="Import failed",
                message=str(e),
            )
            return

        if not new_words:
            rumps.notification(
                title="TalkTalk",
                subtitle="Nothing imported",
                message="The file contained no words or phrases.",
            )
            return

        added_count = vocabulary.add_many(new_words)
        rumps.notification(
            title="TalkTalk",
            subtitle="Vocabulary updated",
            message=f"Added {added_count} word(s). {len(new_words) - added_count} already existed.",
        )


    # ------------------------------------------------------------------
    # History submenu
    # ------------------------------------------------------------------

    def _rebuild_history_menu(self):
        if self._history_menu._menu is not None:
            self._history_menu.clear()

        entries = history.load()
        if entries:
            for i, entry in enumerate(entries[:10]):
                text     = entry.get("text", "")
                app_name = entry.get("app", "")
                label    = (text[:50] + "…") if len(text) > 50 else text
                if app_name:
                    label += f"  [{app_name}]"

                def make_reinject(t):
                    def _reinject(_sender):
                        try:
                            inject_text(t)
                            log.info("Re-injected from history: %r", t[:60])
                        except Exception as exc:
                            log.warning("Re-inject failed: %s", exc)
                    return _reinject

                item = rumps.MenuItem(f"{i + 1}. {label}", callback=make_reinject(text))
                self._history_menu[f"_hist_{i}"] = item

            self._history_menu["_hist_sep_"] = None

        clear_item = rumps.MenuItem("Clear History", callback=self._clear_history)
        self._history_menu["_hist_clear_"] = clear_item

    def _clear_history(self, _):
        history.clear()
        self._rebuild_history_menu()

    # ------------------------------------------------------------------
    # Silence auto-stop submenu
    # ------------------------------------------------------------------

    def _rebuild_silence_menu(self):
        if self._silence_menu._menu is not None:
            self._silence_menu.clear()

        current = self._cfg.get("silence_stop_delay", 0)
        for label, seconds in _SILENCE_STOP_OPTIONS:
            item = rumps.MenuItem(label, callback=self._select_silence_stop)
            item.state = 1 if seconds == current else 0
            self._silence_menu[label] = item

    def _select_silence_stop(self, sender):
        seconds = next(s for l, s in _SILENCE_STOP_OPTIONS if l == sender.title)
        self._cfg["silence_stop_delay"] = seconds
        config.save(self._cfg)
        self._rebuild_silence_menu()

    # ------------------------------------------------------------------
    # Per-app profiles
    # ------------------------------------------------------------------

    @rumps.timer(2.0)
    def _check_frontmost_app(self, _):
        try:
            from AppKit import NSWorkspace
            app       = NSWorkspace.sharedWorkspace().frontmostApplication()
            bundle_id = app.bundleIdentifier() or ""
            app_name  = app.localizedName() or ""
            if bundle_id != self._current_bundle_id:
                self._current_bundle_id = bundle_id
                self._current_app_name  = app_name
                self._apply_app_profile(bundle_id)
                self._rebuild_profiles_menu()
        except Exception as exc:
            log.debug("App profile check failed: %s", exc)

    def _apply_app_profile(self, bundle_id: str) -> None:
        # Restore any settings that were overridden by the previous profile.
        if self._pre_profile_settings:
            for key, val in self._pre_profile_settings.items():
                self._cfg[key] = val
            self._pre_profile_settings = {}
            self._rebuild_lang_menu()

        profiles = self._cfg.get("profiles", {})
        if bundle_id not in profiles:
            return

        profile = profiles[bundle_id]
        self._pre_profile_settings = {}

        if "language_mode" in profile:
            self._pre_profile_settings["language_mode"] = self._cfg.get("language_mode")
            self._cfg["language_mode"] = profile["language_mode"]
            self._rebuild_lang_menu()
            log.info("Applied profile for %r: language_mode=%r", bundle_id, profile["language_mode"])

    def _rebuild_profiles_menu(self):
        if self._profiles_menu._menu is not None:
            self._profiles_menu.clear()

        profiles = self._cfg.get("profiles", {})
        bundle   = self._current_bundle_id
        app_name = self._current_app_name or bundle or "this app"
        has_profile = bundle in profiles

        # Current app status
        status_label = f"Active: {app_name}" + (" ✓" if has_profile else "")
        status_item  = rumps.MenuItem(status_label)
        status_item.state = 0
        self._profiles_menu["_prof_status_"] = status_item

        self._profiles_menu["_prof_sep1_"] = None

        # Save / clear for current app
        save_item = rumps.MenuItem(
            f"Save settings for {app_name}",
            callback=self._save_profile,
        )
        self._profiles_menu["_prof_save_"] = save_item

        if has_profile:
            clear_item = rumps.MenuItem(
                f"Clear profile for {app_name}",
                callback=self._clear_current_profile,
            )
            self._profiles_menu["_prof_clear_"] = clear_item

        # List all saved profiles
        if profiles:
            self._profiles_menu["_prof_sep2_"] = None
            for bid, prof in profiles.items():
                lang = prof.get("language_mode", "?")
                label = f"{bid}  ({lang})"
                item = rumps.MenuItem(label)
                item.state = 1 if bid == bundle else 0
                self._profiles_menu[f"_prof_{bid}_"] = item

    def _save_profile(self, _):
        if not self._current_bundle_id:
            return
        profiles = self._cfg.get("profiles", {})
        profiles[self._current_bundle_id] = {
            "language_mode": self._cfg.get("language_mode", "translate"),
        }
        self._cfg["profiles"] = profiles
        config.save(self._cfg)
        self._rebuild_profiles_menu()
        rumps.notification(
            title="TalkTalk",
            subtitle=f"Profile saved for {self._current_app_name or self._current_bundle_id}",
            message=f"Language mode: {self._cfg.get('language_mode', 'translate')}",
        )

    def _clear_current_profile(self, _):
        profiles = self._cfg.get("profiles", {})
        profiles.pop(self._current_bundle_id, None)
        self._cfg["profiles"] = profiles
        config.save(self._cfg)
        # Restore pre-profile settings if a profile was active
        if self._pre_profile_settings:
            for key, val in self._pre_profile_settings.items():
                self._cfg[key] = val
            self._pre_profile_settings = {}
            self._rebuild_lang_menu()
        self._rebuild_profiles_menu()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    TalkTalkApp().run()
