"""Wait out a USB drive that drops, then redo the slide it dropped under.

The slides are multi-GB CZI files on a USB drive, and the drive does not merely
fail a read - it goes away (see scripts/ls_io.py). A stage that loops over
slides used to die with `FileNotFoundError` on a path that exists, and the
operator had to notice, replug, and restart. This module makes that loop
patient instead:

    import ls_drive as DR
    for uid in uids:
        DR.per_slide(lambda: process(uid), label=f"slide {uid}")

`per_slide` runs the unit. If it raises and a watched folder is unreachable, it
waits - with no time limit - until the folder is back, then runs the unit again
from the start. If the folder IS reachable the failure is the unit's own (a
corrupt file, a bug) and is re-raised at once: waiting would hide it.
`KeyboardInterrupt` and `Stopped` are never swallowed or retried.

Which folders are "the drive": env `LS_WATCH` (an os.pathsep list) if set, else
`source_dir` and `out_root` read straight from the JSON at env `LS_CONFIG`. It
does not import ls_config, so it works for a stage that has not loaded one. A
folder counts as reachable when `os.listdir` on it succeeds.

Stopping: while waiting there is no deadline, so the app (or anything else)
asks the stage to give up by creating the file named by env `LS_STOP_FILE`;
the wait notices within one poll and raises `Stopped`.

What the app sees. With env `LS_PROGRESS=1` the module prints exactly
`@@drive lost <folder> | <label>` and `@@drive back <seconds>`, in the style of
ls_progress's `@@progress` lines. Otherwise it prints a human line when the
wait starts, one a minute while it lasts, and "back after N s".

`Watchdog` covers the case where the read does not raise but BLOCKS on the
vanished volume: a daemon thread polls `reachable()` and prints the `lost`
line itself, once per loss, so the app can flag it while the stage looks hung.
If the drive returns and the read completes without ever raising, the watchdog
prints the `back` line, so the app is never left showing a loss that is over.
When `per_slide`'s attempt then fails and `wait_for_drive` runs, it sees the
loss was already announced and does not print `lost` a second time.

Imported by path, like every other cross-script import here, and possibly more
than once, so the only state is two module-level values: `PAUSES` (a log of
completed waits) and `_ANNOUNCED` (a loss the watchdog has printed that no
`back` has closed yet).
"""

import json
import os
import threading
import time

POLL = 2.0
MINUTE = 60.0

# One dict per completed wait: at (epoch start), seconds, folder, label.
PAUSES = []

# {"folder", "label", "t0"} while a printed `lost` has no `back` yet, else None.
_ANNOUNCED = None


class Stopped(Exception):
    """The stop file appeared while waiting for the drive."""


def _progress():
    return os.environ.get("LS_PROGRESS") == "1"


def watched():
    """The folders whose absence means the drive is gone."""
    env = os.environ.get("LS_WATCH")
    if env:
        return [p for p in env.split(os.pathsep) if p]
    cfg_path = os.environ.get("LS_CONFIG")
    if not cfg_path:
        return []
    try:
        with open(cfg_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        return []
    return [cfg[k] for k in ("source_dir", "out_root")
            if isinstance(cfg.get(k), str) and cfg[k]]


def reachable(paths=None):
    """(True, None), or (False, first folder that cannot be listed)."""
    for p in (watched() if paths is None else paths):
        try:
            os.listdir(p)
        except OSError:
            return False, p
    return True, None


def _say_lost(folder, label):
    if _progress():
        print(f"@@drive lost {folder} | {label}", flush=True)
    else:
        print(f"  !! drive unreachable ({folder}), paused at {label}; "
              "waiting, Ctrl+C to stop", flush=True)


def _say_back(seconds):
    if _progress():
        print(f"@@drive back {seconds:.0f}", flush=True)
    else:
        print(f"  drive back after {seconds:.0f} s", flush=True)


def _record(t0, seconds, folder, label):
    PAUSES.append({"at": t0, "seconds": seconds, "folder": folder, "label": label})


def wait_for_drive(label, poll=None):
    """Block until every watched folder is reachable; return seconds waited.

    Returns 0.0 at once when nothing is gone. Raises `Stopped` if the file
    named by LS_STOP_FILE exists at any poll while waiting.
    """
    global _ANNOUNCED
    poll = POLL if poll is None else poll
    ok, folder = reachable()
    if ok:
        return 0.0
    ann, _ANNOUNCED = _ANNOUNCED, None
    t0 = ann["t0"] if ann else time.time()
    if not ann:
        _say_lost(folder, label)
    next_note = time.monotonic() + MINUTE
    while True:
        stop = os.environ.get("LS_STOP_FILE")
        if stop and os.path.exists(stop):
            raise Stopped(f"stopped while waiting for {folder}")
        time.sleep(poll)
        ok, now_gone = reachable()
        if ok:
            break
        folder = now_gone
        if not _progress() and time.monotonic() >= next_note:
            _say_lost(folder, label)
            next_note += MINUTE
    seconds = time.time() - t0
    _record(t0, seconds, folder, label)
    _say_back(seconds)
    return seconds


def wait_if_gone(label):
    """Wait only when a watched folder is gone; 0.0 otherwise."""
    return 0.0 if reachable()[0] else wait_for_drive(label)


class Watchdog:
    """Poll the drive on a daemon thread while a possibly-blocking read runs.

    `lost` is True if a loss was seen during the block, even one that healed.
    """

    def __init__(self, label, poll=None):
        self.label = label
        self.poll = POLL if poll is None else poll
        self.lost = False
        self._stop = threading.Event()
        self._thread = None

    def _run(self):
        global _ANNOUNCED
        while not self._stop.wait(self.poll):
            ok, folder = reachable()
            if not ok and _ANNOUNCED is None:
                self.lost = True
                _ANNOUNCED = {"folder": folder, "label": self.label, "t0": time.time()}
                _say_lost(folder, self.label)
            elif ok and _ANNOUNCED is not None and _ANNOUNCED["label"] == self.label:
                ann, _ANNOUNCED = _ANNOUNCED, None
                seconds = time.time() - ann["t0"]
                _record(ann["t0"], seconds, ann["folder"], self.label)
                _say_back(seconds)

    def __enter__(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()
        return False


def per_slide(fn, *, label):
    """Run `fn`; if it fails because the drive went away, wait and run it again."""
    while True:
        with Watchdog(label) as wd:
            try:
                return fn()
            except (KeyboardInterrupt, Stopped):
                raise
            except Exception:
                if reachable()[0] and not wd.lost:
                    raise
        wait_for_drive(label)
