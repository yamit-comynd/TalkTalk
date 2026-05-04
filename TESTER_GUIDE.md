# TalkTalk — Tester Guide

## What is TalkTalk?

Most of us think faster than we type. We lose ideas mid-sentence, slow down to fix typos, and spend more time wrestling with the keyboard than actually saying what we mean. TalkTalk is built to close that gap.

**TalkTalk is a macOS dictation app that lives in your menu bar and works everywhere on your Mac.** Hold a key, speak, release — your words appear instantly in whatever app you're using. No mode-switching, no special interface, no copy-paste. Just talk, and your thoughts land exactly where your cursor is.

This is typing at the speed of thought. Not because the technology is magic, but because speaking is the most natural thing humans do — and TalkTalk gets out of the way and lets you do it.

### What you can use it for

- **Talking to AI tools** — this is the killer use case. Whether you're in Claude Code, ChatGPT, OpenAI Codex, Cursor, or any other AI assistant, TalkTalk lets you prompt with your voice. Describe what you want to build, explain a bug, or think out loud with your AI pair programmer — at the speed you actually think, not at the speed you type. Long, nuanced prompts that you'd never bother typing become effortless to speak.
- **Writing** — emails, documents, Slack messages, notes. Draft a paragraph in 10 seconds instead of 60.
- **Coding comments and documentation** — dictate inline comments or docstrings without breaking your flow.
- **Search and commands** — speak into any text field. Address bars, search boxes, terminal prompts.
- **Long-form thinking** — voice your ideas as they come. Clean them up later. The first draft is the hardest part — TalkTalk makes it effortless.
- **Multilingual** — speak in any language and get English output, or transcribe in your original language. Built-in translation, no extra service required.

### Your data never leaves your Mac

TalkTalk does not call any online service to transcribe your voice. There is no cloud API, no external server, no account required. The Whisper speech model runs entirely on your Mac — your words are processed locally and go nowhere. What you dictate stays on your device, full stop.

This matters especially when you're working with code, internal documents, or anything sensitive. You can use TalkTalk freely without worrying about what's being sent where.

### Why it's different from built-in dictation

macOS has its own dictation, but it requires you to enable it per-app, activate it through a menu or double-tap, and it frequently drops words or loses context. TalkTalk uses **Whisper** — OpenAI's open-source speech model, running entirely on your Mac, offline, with no data sent anywhere. It's faster, more accurate, and works the same everywhere: browser, terminal, Notion, Figma, Slack, your own apps.

---

Thanks for trying TalkTalk! This guide covers installation, permissions, and the basics of using the app.

---

## Installation

1. **Download** the `TalkTalk.dmg` file shared with you
2. Open the DMG and drag **TalkTalk** into your Applications folder
3. Eject the DMG
4. Open TalkTalk from Applications (or Spotlight)

> **First launch only:** macOS will show a warning that TalkTalk is from an unidentified developer.
> Right-click the app icon → **Open** → click **Open** in the dialog.
> You only need to do this once.

---

## Permissions

TalkTalk needs three permissions to work. macOS will ask for each one on first launch — grant all three.

### 1. Microphone
Needed to record your voice.

- A standard macOS dialog will appear: **"TalkTalk would like to access the microphone"**
- Click **OK**

### 2. Input Monitoring
Needed to detect your hotkey while you're in any other app.

- A dialog will appear asking for Input Monitoring access
- Click **Allow** (or **Open System Settings** if prompted, then toggle TalkTalk on)
- Go to: **System Settings → Privacy & Security → Input Monitoring** and enable TalkTalk

### 3. Accessibility
Needed to type transcribed text into whatever app you're using.

- A dialog will appear: **"TalkTalk wants to control your computer"**
- Click **Open System Settings**, then toggle TalkTalk on under **Accessibility**
- Go to: **System Settings → Privacy & Security → Accessibility** and enable TalkTalk

> **All three must be enabled.** If the app seems to record but nothing gets typed, Accessibility is likely missing. If the hotkey does nothing, Input Monitoring is likely missing.

> TalkTalk detects permission changes automatically — no restart needed after granting.

---

## Basic Usage

### Hold to record, release to transcribe

| Action | What happens |
|--------|-------------|
| **Hold** Right Option (⌥) | Recording starts — you'll hear a short chirp and see a spinning indicator |
| **Speak** | Talk normally |
| **Release** Right Option | Recording stops, transcription runs (~1–2s), text is typed into your active app |

The transcribed text is pasted wherever your cursor is — any text field, document, or chat window.

### Default hotkey: Right Option ⌥

The right Option key (the one on the right side of the spacebar) is the default.  
It's easy to hold, and rarely conflicts with other shortcuts.

---

## Menus & Settings

Click the **TT** icon in the menu bar to access settings.

### Microphone
Select which microphone to use. TalkTalk defaults to your system microphone.  
If you plug in a headset or USB mic, it will appear here — select it for best accuracy.

### Language Mode
| Mode | What it does |
|------|-------------|
| **Translate to English** *(default)* | Speak any language, get English text |
| **Original Script** | Transcribe in whatever language you speak |
| **Transliterate** | Romanize non-Latin scripts (e.g. Hindi speech → roman letters) — requires Ollama |

### Whisper Model
Controls the speech recognition quality vs. speed tradeoff.

| Model | Size | Speed | Best for |
|-------|------|-------|---------|
| **base** *(default)* | ~145 MB | ~1–1.5s | Most use — good balance |
| tiny | ~75 MB | ~0.5s | Fastest, less accurate |
| small | ~465 MB | ~2–3s | Better accuracy on accented or noisy speech |
| medium | ~1.5 GB | ~5s | Best accuracy, noticeably slower |

> The selected model downloads automatically on first use and is cached on your Mac.
> Switching models triggers a brief "Loading model…" indicator.

### Hotkeys
You can add or remove hotkeys. Click the **✕** next to any hotkey to remove it.  
Click **Add Hotkey…** then press any key to register it.

At least one hotkey must always be active.

### Vocabulary
Add words or phrases that Whisper should recognise correctly — useful for names, technical terms, or product names it tends to mishear.

---

## Tips

- **Speak naturally** — don't pause unnaturally or shout. Normal conversational speech works best.
- **Short pause before speaking** — wait ~0.5s after holding the hotkey before you start talking. The chirp sound signals that recording has started.
- **Noisy environment** — switch to the `small` model for better accuracy.
- **Nothing getting typed?** — check that TalkTalk has Accessibility in System Settings → Privacy & Security → Accessibility.
- **Hotkey not working?** — check Input Monitoring in System Settings → Privacy & Security → Input Monitoring.
- **After locking / sleeping your Mac** — TalkTalk recovers automatically on wake. If something seems off, a quick quit-and-relaunch fixes it.

---

## Known Limitations

- Requires macOS 13 (Ventura) or later
- The Transliterate mode requires [Ollama](https://ollama.com) running locally with a model installed
- The first transcription after launch may take a moment while the Whisper model loads — the "Loading model…" pill in the menu bar area will tell you when it's ready

---

## Sending Feedback

Please note anything that feels broken, confusing, or just off. Specific is best:

- What you were doing
- What you expected to happen
- What actually happened
- Your Mac model and macOS version (Apple menu → About This Mac)
