# TalkTalk — Codebase Guide

## What this project is

TalkTalk is a macOS menu-bar dictation app. The user holds a hotkey (default: Right Option ⌥), speaks, and releases — transcribed text is injected directly into whatever app is focused. No UI mode-switching, no copy-paste. Speech recognition runs entirely on-device via OpenAI's Whisper model (through `faster-whisper`); no data leaves the Mac.

**Target user:** developers and knowledge workers who want to prompt AI tools (Claude, ChatGPT, Cursor) and write emails/docs at the speed of speech.

**Key constraints that shape every design decision:**
- Must work system-wide in any app (requires macOS Accessibility + Input Monitoring permissions)
- Transcription must be fully offline (no API keys, no cloud)
- Zero UI — lives entirely in the menu bar
- Must survive sleep/wake cycles, dock/undock events, and mid-session permission changes

---

## Tech stack

| Layer | Library |
|---|---|
| Menu bar UI | `rumps` (wraps `NSStatusItem`) |
| Hotkey detection | `pynput` (wraps `CGEventTap`) |
| Audio capture | `sounddevice` (wraps PortAudio/CoreAudio) |
| Speech recognition | `faster-whisper` (CTranslate2-optimised Whisper) |
| Text injection | `NSPasteboard` + `CGEventPost` (Cmd+V) |
| macOS APIs | `PyObjC` (AppKit, Quartz, Foundation, CoreAudio) |
| Transliteration | `ollama` (local LLM — optional, only for Transliterate mode) |
| Packaging | `PyInstaller` + ad-hoc `codesign` |

---

## File map

```
# ── Core application (Python source — stays at root for PyInstaller compatibility)
app.py              Entry point + rumps App class. Owns all state and wires everything together.
config.py           Load/save ~/.talktalk/config.json. All defaults live here.
device_manager.py   Mic enumeration, system-default resolution, PortAudio reinit.
recorder.py         AudioRecorder — opens a sounddevice InputStream, accumulates audio chunks.
transcriber.py      Transcriber — wraps WhisperModel, returns (text, language_code).
enhancer.py         Transliteration via Ollama. Falls back to unidecode if Ollama is down.
injector.py         inject_text() — snapshot clipboard → pbcopy → Cmd+V → restore clipboard.
hud.py              HUD (spinning circle) + LoadingToast (pill). Pure AppKit/Quartz — no rumps.
permissions.py      TCC permission checks (IOHIDCheckAccess, AXIsProcessTrusted) and request flows.
key_capture.py      capture_hotkey() — floating NSPanel that waits for a key press.
vocabulary.py       Load/save/format ~/.talktalk/vocabulary.json as Whisper initial_prompt.

# ── Packaging
packaging/
  TalkTalk.spec           PyInstaller build spec. Controls bundled deps, data files, Info.plist.
  entitlements.plist      macOS entitlements required for codesign (com.apple.security.*).
  hook_freeze_support.py  PyInstaller runtime hook: calls multiprocessing.freeze_support() early.

# ── Build & distribution scripts (all must be run from project root)
scripts/
  build.sh                Full build: venv → pip → PyInstaller → codesign → install → TCC reset.
  distribute.sh           Re-signs with Developer ID, notarizes, and wraps in a .dmg.
  package_release.sh      Combines build + guide generation into a versioned release/ bundle.
  generate_guide_html.py  Converts docs/TESTER_GUIDE.md to a self-contained HTML file.

# ── Developer tools
tools/
  pipeline.py     Interactive CLI test for the record → transcribe pipeline. Not shipped.

# ── Documentation
docs/
  TESTER_GUIDE.md  End-user guide sent to testers.

# ── Assets
assets/
  menubar.icns    Menu bar icon (16×16 template image).
  TalkTalk.icns   App icon (Dock/Finder/About).
  *.csv / *.txt   Bundled vocabulary word lists.
```

---

## Architecture & threading model

This is the most important thing to understand before editing any code.

```
Main thread (NSRunLoop / rumps)
│
├── rumps timers (all fire on main thread):
│   ├── _hud_tick        20 fps — animates HUD, detects model-load done, silence detection
│   ├── _wake_detector    2 s   — detects sleep/wake by timer gap
│   ├── _check_device     5 s   — detects system default mic change (dock/undock)
│   └── _permission_poll  1 s   — polls TCC until both permissions granted
│
├── pynput CGEventTap thread (macOS-managed, NOT a Python thread)
│   ├── _on_press  → sets flags, calls HUD.play_start(), spawns _open_mic_after_sound thread
│   └── _on_release → calls recorder.stop(), submits _transcribe_and_inject to executor
│       ⚠️  Keep this callback FAST. Never sleep, never block on I/O here.
│           macOS will disable the CGEventTap if it doesn't return promptly.
│
├── ThreadPoolExecutor (max_workers=1) — the "background worker"
│   └── _transcribe_and_inject  runs Whisper + inject_text sequentially
│       Only one transcription ever runs at a time (intentional — serialises access).
│
└── Miscellaneous daemon threads:
    ├── _open_mic_after_sound  — sleeps 90ms then opens mic (keeps CGEventTap fast)
    ├── _preload_model         — loads Whisper model at startup
    └── _do_restore (injector) — restores clipboard 500ms after paste
```

**Cross-thread state access rules:**
- `self._recording`, `self._hud_state`, `self.title` are written by both the CGEventTap thread and main thread. They are simple booleans/strings — Python's GIL makes these safe without a lock.
- `self._transcriber` can be replaced on the main thread (model reload). `_transcribe_and_inject` snapshots it to a local variable at the top of the function and null-checks before use.
- `injector.py` module-level clipboard state (`_clip_lock`, `_clip_base_count`, etc.) is protected by a `threading.Lock` because `_do_restore` runs on a separate thread.

---

## Data flow: key press → pasted text

```
1. User holds hotkey
   └── _on_press() [CGEventTap thread]
         ├── Sets self._recording = True, self._hud_state = "recording"
         ├── Calls HUD.play_start() (plays chirp sound)
         └── Spawns _open_mic_after_sound thread

2. _open_mic_after_sound [daemon thread]
   └── Sleeps ~90ms (so chirp doesn't get recorded), then:
         └── recorder.start() — opens sd.InputStream on the active device

3. Audio accumulates in recorder._chunks (callback thread, per sounddevice)

4. User releases hotkey
   └── _on_release() [CGEventTap thread]
         ├── self._recording = False
         ├── HUD.play_stop() (plays stop sound)
         ├── recorder.stop() → returns flat float32 numpy array
         └── executor.submit(_transcribe_and_inject, audio)

5. _transcribe_and_inject [thread pool worker]
   ├── transcriber.transcribe(audio, task="translate"|"transcribe")
   │     └── faster-whisper → WhisperModel.transcribe() → (text, language)
   ├── If mode=="transliterate": enhancer.transliterate(text, lang) via Ollama
   └── inject_text(transcript)
         ├── Snapshot clipboard (NSPasteboard)
         ├── pbcopy writes transcript to clipboard
         ├── CGEventPost sends Cmd+V to focused app
         └── Schedules _do_restore thread (restores clipboard after 500ms)
```

---

## Config system

**File:** `~/.talktalk/config.json`  
**Defaults:** defined in `config.DEFAULTS` — the source of truth for all keys.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `mic_mode` | `"system"` \| `"pinned"` | `"system"` | Follow macOS default vs. pin to a specific device |
| `mic_priority` | `list[str]` | `[]` | Ordered preferred device names (pinned mode only) |
| `model` | str | `"base"` | Whisper model size: tiny / base / small / medium |
| `language` | str \| null | `null` | Source language hint (null = auto-detect) |
| `language_mode` | str | `"translate"` | `translate` / `transcribe` / `transliterate` |
| `ollama_model` | str | `"gemma3:4b"` | Ollama model for transliterate mode |
| `hotkeys` | `list[str]` | `["alt_r"]` | pynput Key names or single chars |

`config.load()` merges saved JSON over DEFAULTS (so new keys from DEFAULTS always appear for existing users). It also handles two migrations: the legacy `hotkey` → `hotkeys` rename, and the implicit `mic_priority` → `mic_mode: "pinned"` promotion.

**User data directory:** `~/.talktalk/`
- `config.json` — persisted settings
- `vocabulary.json` — custom Whisper vocabulary
- `talktalk.log` — rotating log (512 KB + 1 backup)

---

## Microphone management

**Two modes (set by `mic_mode` in config):**

- **`"system"` (default):** Uses `device=None` in sounddevice — PortAudio delegates to CoreAudio, which follows whatever macOS has set as the default input. Automatically correct when docked (webcam mic) or undocked (built-in mic). On wake from sleep, `reinit_portaudio()` (= `sd._terminate()` + `sd._initialize()`) forces a full PortAudio rescan before opening a new stream.

- **`"pinned"`:** Uses the first name from `mic_priority` that is currently connected. Falls back to system default with a notification if the pinned device is unavailable.

**`_check_device` timer (5 s):**
- System mode: calls `get_system_default_name()` each tick; if the system default changed (dock/undock), silently recreates the recorder with `device=None`.
- Pinned mode: tries to resolve the priority list; falls back to system default if the device disappears.

**`_on_system_wake`:**
1. Clears stuck recording state
2. `device_manager.reinit_portaudio()` — **must happen before any sounddevice call**
3. `_reinit_recorder()` — creates a fresh `AudioRecorder`
4. Restarts the pynput key listener (macOS invalidates CGEventTap on wake)

---

## Permissions (macOS TCC)

Two permissions are required. Both are detected live; no restart is ever needed.

| Permission | API | What it enables |
|---|---|---|
| Input Monitoring | `IOHIDCheckAccess` / `IOHIDRequestAccess` | pynput CGEventTap — hotkey detection |
| Accessibility | `AXIsProcessTrusted` | `CGEventPost` Cmd+V — text injection |

**Microphone** is also required but macOS prompts for it automatically when `sd.InputStream` is first opened — no explicit request code needed.

`_permission_setup_once` fires 0.8 s after startup (so the menu bar icon is settled) and calls `ask_input_monitoring()` / `ask_accessibility()` for any missing permissions. `_permission_poll` then ticks every second until both are confirmed, and fires again if a mid-session revocation is detected.

**Build note:** each PyInstaller build produces a new binary hash. macOS TCC is tied to the binary hash, so `build.sh` always calls `tccutil reset` after installing — this forces a fresh permission prompt on the next launch. This is expected behaviour for development builds.

---

## Text injection

`injector.inject_text(text)` is the only public function. It:

1. Checks `AXIsProcessTrusted()` and raises `PermissionError` if not granted.
2. Snapshots the clipboard (all pasteboard item types, not just plain text).
3. Writes `text` to the clipboard via `pbcopy`.
4. Sends Cmd+V via `CGEventCreateKeyboardEvent` / `CGEventPost`. Both key-down and key-up carry `kCGEventFlagMaskCommand` — Electron and Chrome apps inspect modifier state on key-up; without it they drop the paste.
5. Restores the original clipboard 500 ms later in a background thread.

**Clipboard streak tracking:** rapid back-to-back dictations reuse the same "original" snapshot (`_clip_base_snap`, `_clip_base_count`, `_clip_own_writes`) so the clipboard is always restored to the pre-first-dictation state, not to an intermediate TalkTalk-written state.

**Why Cmd+V instead of typing character-by-character?** `CGEventCreateKeyboardEvent` + `CGEventPost` only supports sending raw HID keycodes (US layout). Mapping arbitrary Unicode to keycodes is unreliable across keyboard layouts and for non-ASCII characters. The clipboard approach works for any Unicode string and any app.

---

## HUD system

Two separate floating windows, both built with raw AppKit (no rumps, no standard window chrome):

**`HUD` (recording / processing indicator):**
- 32×32 px black disc, top-centre of screen, floating above all spaces
- A CAShapeLayer 270° arc spins at 20 fps while state is "recording" or "processing"
- `ignoresMouseEvents = True` — completely click-through
- Repositioned from current screen geometry each time it becomes visible (handles display arrangement changes)

**`LoadingToast` (model initialisation):**
- 230×48 px pill, same position as HUD
- States: "loading" (spinner + "Loading model…") → "ready" ("Ready to listen ✓", auto-hides after ~2.75 s) → "hidden"
- If model load fails, the toast is hidden immediately and a `rumps.notification` is fired instead

Both windows use `NSWindowCollectionBehavior CanJoinAllSpaces | IgnoresCycle` so they appear on all desktops and don't appear in the app switcher.

---

## Build & distribution

### Development (no build needed)

```bash
# One-time setup
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run the app directly
python app.py

# Test the audio pipeline interactively (no permissions needed)
python tools/pipeline.py
python tools/pipeline.py --model small --device 3
```

### Building a distributable .app

```bash
./scripts/build.sh          # → dist/TalkTalk.app, installs to /Applications, resets TCC
./scripts/build.sh --dmg    # same + wraps in dist/TalkTalk.dmg
```

`build.sh` maintains a separate ``.venv-build`` (Python 3.13) so the dev venv is never touched. It:
1. Creates/reuses `.venv-build` and installs deps + PyInstaller
2. Cleans `build/` and `dist/`
3. Runs `pyinstaller TalkTalk.spec`
4. Ad-hoc signs all `.dylib`/`.so` files (inside-out), then the main executable (with `entitlements.plist`), then the bundle
5. Installs to `/Applications/TalkTalk.app`
6. Resets TCC permissions (new binary hash = new permissions needed)

### Creating a tester release

```bash
./scripts/distribute.sh       # re-signs with Developer ID, notarizes, wraps in .dmg
./scripts/package_release.sh  # combines build + guide into a versioned release/ bundle
```

### PyInstaller spec (`TalkTalk.spec`)

Key things the spec does that aren't obvious:
- Bundles `faster_whisper/assets/` (contains the silero VAD ONNX model) — without this, VAD filtering silently fails
- Specifies `runtime_hooks=["hook_freeze_support.py"]` — required to prevent infinite subprocess spawning on macOS
- Sets `LSUIElement: True` in `Info.plist` — makes TalkTalk menu-bar-only (no Dock icon)
- Sets all four `NS*UsageDescription` keys — macOS requires these for TCC permission prompts to appear

---

## Whisper / transcription

`Transcriber` wraps `faster_whisper.WhisperModel` with:
- `device="cpu"`, `compute_type="int8"` — optimised for Apple Silicon MPS isn't supported by CTranslate2 yet; int8 quantisation is ~3× faster than float32 on CPU with negligible accuracy loss
- `vad_filter=True` — skips silent regions; measurably reduces transcription time for recordings with leading/trailing silence
- `condition_on_previous_text=False` — prevents Whisper from hallucinating based on its own prior output across sequential calls
- `beam_size=1` — greedy decoding; faster than beam search with minimal quality difference for short utterances

Models download from HuggingFace on first use and are cached in `~/.cache/huggingface/`. Model sizes: tiny (~75 MB), base (~145 MB), small (~465 MB), medium (~1.5 GB).

The model is loaded eagerly at startup in a background thread. `_model_ready` is only set `True` on successful load. `_model_load_done` is always set `True` in the `finally` block (success or fail) and is what `_hud_tick` uses to transition the loading toast.

---

## Vocabulary system

Words stored in `~/.talktalk/vocabulary.json` are formatted as a Whisper `initial_prompt`:

```
"Terminology: TalkTalk, CoMYND, PyInstaller, CTranslate2."
```

Whisper uses `initial_prompt` as prior context — listing terms as a comma-separated sentence effectively biases the model toward recognising them. The prompt is hard-capped at 800 characters (Whisper's token budget for initial_prompt is 224 tokens ≈ 800 chars; exceeding it causes silent left-truncation and hallucination).

---

## Common tasks

### Add a new config key
1. Add it with a default value to `config.DEFAULTS` in `config.py`
2. Read it in `app.py` via `self._cfg.get("key", fallback)`
3. If it needs a menu item, add a `_rebuild_*_menu` + `_select_*` pair following the existing pattern
4. If existing users need migration, add a migration block in `config.load()`

### Add a new menu item
Follow the pattern in `_rebuild_lang_menu` / `_select_lang_mode`:
1. Add a `rumps.MenuItem("Microphone")` to `self.menu` in `__init__`
2. Write `_rebuild_*_menu(self)` — clear the submenu, add items with `item.state = 1` for the current value
3. Write `_select_*_mode(self, sender)` — update `self._cfg`, call `config.save()`, rebuild the menu
4. Call `_rebuild_*_menu()` in `__init__` after the other menu builds

### Add a new permission
Follow the pattern in `permissions.py`:
1. Add a `has_*()` function using the appropriate TCC API
2. Add `ask_*()` with native prompt + NSAlert fallback
3. Snapshot the state in `__init__` (`self._*_granted`)
4. Poll in `_permission_poll` and notify + take action on grant

### Change the hotkey system
`_resolve_hotkey(name)` converts a config string to a pynput `Key` or `KeyCode`. The string is either a `keyboard.Key` attribute name (e.g. `"alt_r"`) or a single ASCII character. `key_capture.py` handles the UI for capturing a new hotkey — it runs a modal NSPanel loop and returns `(config_name, display_name)`.

---

## Known limitations & gotchas

- **CGEventTap callbacks must return quickly.** macOS will disable the tap if it blocks. Never `time.sleep()`, do I/O, or call Whisper in `_on_press` / `_on_release`. Offload anything slow to a background thread.

- **Each build resets TCC permissions.** Ad-hoc signing ties the permission grant to the binary hash. A new build = new hash = all three permissions (Microphone, Input Monitoring, Accessibility) must be re-granted. This is normal for development.

- **Transliterate mode requires Ollama running locally.** `ollama serve` must be running and have a model pulled (default: `gemma3:4b`). The app detects this gracefully and falls back to `unidecode` if Ollama is unreachable.

- **Whisper runs on CPU.** CTranslate2 (the faster-whisper backend) does not support Apple's MPS/Metal. int8 CPU inference is fast enough for dictation latency (~0.5–2 s depending on model).

- **No automated tests.** Use `pipeline.py` to manually test the audio → transcription pipeline. UI behaviour must be tested by running the app.

- **macOS 13 (Ventura) minimum.** The `IOHIDCheckAccess` API used for Input Monitoring detection requires macOS 13+. Earlier versions are not supported.
