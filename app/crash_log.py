"""Where the app's crash log is written: one per-user folder, the same everywhere.

Its own module because two places need the same answer: the excepthook in
__main__.py, which writes the log, and Help > Open the log folder, which has to
open the folder that log is actually in rather than a second guess at it.
"""

import os

APP_NAME = "The Great IHC Leap Forward"
CRASH_LOG_NAME = "lsapp-crash.log"


def crash_log_path():
    """A crash log file this user can write, creating its folder if needed.

    %LOCALAPPDATA% on Windows (always set there); elsewhere XDG_STATE_HOME or
    ~/.local/state, which is where logs belong on Linux and harmless on a Mac.
    The folder is tried by actually opening the log for append, because
    os.access says nothing useful about a Program Files folder on Windows. If
    that fails too, the temp folder: a log somewhere beats none. It never
    raises: the excepthook calls it while something has already gone wrong.
    """
    base = (os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_STATE_HOME")
            or os.path.join(os.path.expanduser("~"), ".local", "state"))
    import tempfile
    for folder in (os.path.join(base, APP_NAME, "logs"), tempfile.gettempdir()):
        path = os.path.join(folder, CRASH_LOG_NAME)
        try:
            os.makedirs(folder, exist_ok=True)
            with open(path, "a", encoding="utf-8"):
                pass
            return path
        except Exception:                                   # noqa: BLE001
            continue
    return None
