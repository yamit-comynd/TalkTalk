"""
Microphone device discovery and priority-based auto-selection.

Devices are identified by name (not index), since indices can shift
when peripherals are connected/disconnected. Names are stable for
a given physical device.

Priority list logic:
  - Stored in config as an ordered list of exact device names.
  - On each resolution, the first name in the list that is currently
    connected wins.
  - If nothing matches, falls back to the system default (device=None).
  - Clicking a mic in the menu moves it to position 0 in the list.
"""

import sounddevice as sd


def list_inputs() -> list[tuple[int, str]]:
    """Return [(index, name), ...] for all available input devices."""
    devices = sd.query_devices()
    return [
        (i, d["name"])
        for i, d in enumerate(devices)
        if d["max_input_channels"] > 0
    ]


def resolve_device(priority: list[str]) -> tuple[int | None, str | None]:
    """
    Pick the best available input device given an ordered priority list.
    Returns (device_index, device_name), or (None, None) for system default.
    """
    if not priority:
        return None, None

    available = {name: idx for idx, name in list_inputs()}
    for preferred in priority:
        if preferred in available:
            return available[preferred], preferred

    return None, None


def promote(priority: list[str], name: str) -> list[str]:
    """Move `name` to the front of the priority list, adding it if absent."""
    updated = [n for n in priority if n != name]
    return [name] + updated


def reinit_portaudio() -> None:
    """Tear down and reinitialise the PortAudio session.

    After wake from sleep, PortAudio's cached device list is stale and
    existing streams are invalid.  Calling this forces a full rescan so
    that subsequent device queries and stream opens reflect reality.
    Safe to call with no streams open; blocks briefly while Pa_Terminate
    drains any lingering buffers.
    """
    try:
        sd._terminate()
        sd._initialize()
    except Exception:
        pass


def get_system_default_name() -> str | None:
    """Return the name of the current macOS system default input device.

    This mirrors what macOS shows in System Settings → Sound → Input.
    Returns None if PortAudio reports no default (no input devices at all).
    """
    try:
        idx = sd.default.device[0]  # PortAudio default input index
        if idx < 0:
            return None
        return sd.query_devices(idx)["name"]
    except Exception:
        return None
