# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for TalkTalk.app
# Run via build.sh — do not invoke pyinstaller directly.

block_cipher = None

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=[
        # faster_whisper VAD model assets (silero_vad_v6.onnx)
        (".venv-build/lib/python3.13/site-packages/faster_whisper/assets", "faster_whisper/assets"),
        # Menu bar icon
        ("assets/menubar.icns", "."),
    ],
    hiddenimports=[
        # faster-whisper + CTranslate2 backend
        "faster_whisper",
        "ctranslate2",
        "tokenizers",
        "huggingface_hub",
        # audio
        "sounddevice",
        "soundfile",
        "_sounddevice_data",
        # pynput macOS backend
        "pynput",
        "pynput.keyboard",
        "pynput.keyboard._darwin",
        "pynput.mouse",
        "pynput.mouse._darwin",
        # Quartz + AppKit (permissions.py — Input Monitoring probe + NSAlert dialogs)
        "Quartz",
        "Quartz.CoreGraphics",
        "AppKit",
        "objc",
        # transliteration + LLM
        "unidecode",
        "ollama",
        "httpx",
        # menu bar
        "rumps",
        # numpy sub-modules sometimes missed
        "numpy.core._methods",
        "numpy.lib.format",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=["packaging/hook_freeze_support.py"],
    excludes=[
        "matplotlib",
        "tkinter",
        "scipy",
        "PIL",
        "Pillow",
        "IPython",
        "jupyter",
        "PyQt5",
        "PyQt6",
        "wx",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TalkTalk",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,      # UPX can break macOS code-signing; keep off
    console=False,  # no terminal window
    disable_windowed_traceback=False,
    target_arch=None,   # None = native arch (arm64 on Apple Silicon)
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="TalkTalk",
)

app = BUNDLE(
    coll,
    name="TalkTalk.app",
    icon="assets/TalkTalk.icns",
    bundle_identifier="com.talktalk.app",
    version="0.0.1",
    info_plist={
        "CFBundleName":               "TalkTalk",
        "CFBundleDisplayName":        "TalkTalk",
        "CFBundleVersion":            "0.0.1",
        "CFBundleShortVersionString": "0.0.1",
        # Menu-bar-only app — no Dock icon, but still shows in Force Quit
        "LSUIElement": True,
        # Privacy permission strings — macOS requires these in the plist.
        "NSMicrophoneUsageDescription": (
            "TalkTalk records your microphone while you hold the hotkey "
            "to transcribe speech on-device."
        ),
        "NSAppleEventsUsageDescription": (
            "TalkTalk sends keystrokes via System Events to paste "
            "transcribed text into the active app."
        ),
        "NSAccessibilityUsageDescription": (
            "TalkTalk needs Accessibility access to inject transcribed "
            "text into other apps."
        ),
        "NSInputMonitoringUsageDescription": (
            "TalkTalk monitors the hotkey system-wide to start and "
            "stop recording."
        ),
    },
)
