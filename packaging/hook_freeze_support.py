# PyInstaller runtime hook — must run before any app code.
# On macOS, Python spawns child processes by re-running the bundled exe.
# freeze_support() detects that case and exits early instead of launching
# the full app again, preventing the infinite-instances loop.
import multiprocessing
multiprocessing.freeze_support()
