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

The study file normally lives IN out_root, so it goes with the drive. The
folders it named are therefore remembered per config path (`config_folders`)
and used while the file cannot be read: read afresh mid-drop it named nothing,
every folder looked reachable, and a reader recorded the slide as failed. The
cache is filled when this module loads and when `per_slide` starts a unit, so
it is there before the drive can go; the app also sets `LS_WATCH` from the
config before each run (app/runner.py), which needs no read at all.

Stopping: while waiting there is no deadline, so the app (or anything else)
asks the stage to give up by creating the file named by env `LS_STOP_FILE`;
the wait notices within one poll and raises `Stopped`.

What the app sees. With env `LS_PROGRESS=1` the module prints exactly
`@@drive lost <folder> | <label>` and `@@drive back <seconds>`, in the style of
ls_progress's `@@progress` lines, each after a newline of its own: a stage's
`\r  12/454` counter ends without one, and the marker must not be glued onto
it. Otherwise it prints a human line when the wait starts, one a minute while
it lasts, and "back after N s".

`per_slide_settled` also redoes an attempt the drive went away DURING without
the attempt failing: an ls_io write inside it waited for the drive and then
succeeded, so the CZI handles the attempt holds are to a volume that went.
`disturbed_since(mark())` is the test, and a stage's per-item `except` inside
a unit uses it to re-raise rather than record the drive's failure as the
item's.

`present(path, label)` is `os.path.exists` for a file on the drive: a file
that is absent because the drive has dropped is waited for and looked for
again, never reported missing. It replaces `wait_if_gone(); os.path.exists()`,
which reported a file missing when the drive dropped between the two.

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

# {abspath of a config: [source_dir, out_root]} as last read from it.
_KNOWN = {}


class Stopped(Exception):
    """The stop file appeared while waiting for the drive."""


def _progress():
    return os.environ.get("LS_PROGRESS") == "1"


def config_folders(cfg_path=None):
    """`source_dir` and `out_root` from the config at `cfg_path` (env
    `LS_CONFIG` by default), or the ones it named when last read if it cannot
    be read now - it is usually on the drive that has gone. [] for a config
    never read."""
    cfg_path = cfg_path or os.environ.get("LS_CONFIG")
    if not cfg_path:
        return []
    key = os.path.abspath(cfg_path)
    try:
        with open(cfg_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        return list(_KNOWN.get(key, []))
    if not isinstance(cfg, dict):
        return list(_KNOWN.get(key, []))
    folders = [cfg[k] for k in ("source_dir", "out_root")
               if isinstance(cfg.get(k), str) and cfg[k]]
    _KNOWN[key] = folders
    return list(folders)


def watched():
    """The folders whose absence means the drive is gone."""
    env = os.environ.get("LS_WATCH")
    if env:
        return [p for p in env.split(os.pathsep) if p]
    return config_folders()


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
        print(f"\n@@drive lost {folder} | {label}", flush=True)
    else:
        print(f"  !! drive unreachable ({folder}), paused at {label}; "
              "waiting, Ctrl+C to stop", flush=True)


def _say_back(seconds):
    if _progress():
        print(f"\n@@drive back {seconds:.1f}", flush=True)
    else:
        print(f"  drive back after {seconds:.1f} s", flush=True)


def _record(t0, seconds, folder, label):
    PAUSES.append({"at": t0, "seconds": seconds, "folder": folder, "label": label})


def _close_announced():
    """If a watchdog-printed loss is pending, end it with `back` and a PAUSES entry."""
    global _ANNOUNCED
    ann, _ANNOUNCED = _ANNOUNCED, None
    if ann:
        seconds = time.time() - ann["t0"]
        _record(ann["t0"], seconds, ann["folder"], ann["label"])
        _say_back(seconds)


def wait_for_drive(label, poll=None, paths=None):
    """Block until every watched folder is reachable; return seconds waited.

    Returns 0.0 at once when nothing is gone. Raises `Stopped` if the file
    named by LS_STOP_FILE exists at any poll while waiting. `paths` names the
    folders to wait for in place of `watched()` (the run record waits for the
    folder its study file is in).
    """
    global _ANNOUNCED
    poll = POLL if poll is None else poll
    ok, folder = reachable(paths)
    if ok:
        _close_announced()
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
        ok, now_gone = reachable(paths)
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


def present(path, label):
    """`os.path.exists(path)`, asked of a drive that may have dropped.

    True if the file is there. If not, and a watched folder is gone, wait for
    it (`wait_if_gone(label)`) and look again: a file on a vanished drive is
    not missing. False only when the file is absent with every watched folder
    reachable, before the look and after it - so a drive that drops between
    a wait and the look (the race `wait_if_gone(); os.path.exists()` had) is
    waited for too. With nothing watched, exactly `os.path.exists`.
    """
    while True:
        if os.path.exists(path):
            return True
        if reachable()[0]:
            # The miss may have been the drive going and coming back already:
            # look once more, and believe "missing" only if the folders were
            # reachable on both sides of that look.
            if os.path.exists(path):
                return True
            if reachable()[0]:
                return False
        wait_if_gone(label)


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
            elif ok:
                _close_announced()

    def __enter__(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        # A read blocked on a vanished volume can hang the thread's listdir;
        # it is a daemon, so give up on it rather than hang the stage too.
        self._thread.join(timeout=self.poll + 1)
        if _ANNOUNCED is not None and reachable()[0]:
            _close_announced()
        return False


def per_slide(fn, *, label):
    """Run `fn`; if it fails because the drive went away, wait and run it again."""
    global _ANNOUNCED
    watched()                   # the config's folders known before the drive can go
    while True:
        try:
            with Watchdog(label) as wd:
                try:
                    return fn()
                except (KeyboardInterrupt, Stopped):
                    raise
                except Exception:
                    if reachable()[0] and not wd.lost:
                        raise
            wait_for_drive(label)
        except (KeyboardInterrupt, Stopped):
            # Leave nothing pending: a stale announcement would suppress the
            # next `lost` and lend its start time to an unrelated wait.
            _ANNOUNCED = None
            raise


def mark():
    """A token for "now", for `disturbed_since`: how many waits have ended."""
    return len(PAUSES)


def disturbed_since(token):
    """True if the drive may have gone since `mark()` gave `token`.

    That is: a wait has ended since (an ls_io write waits for the drive by
    itself and then succeeds, so a unit can see no error at all), a loss the
    watchdog announced is still open, or a watched folder is gone now. A
    stage's per-item `except` inside a unit re-raises when this is true: the
    item did not fail, the drive did, and the whole unit is redone.
    """
    return len(PAUSES) > token or _ANNOUNCED is not None or not reachable()[0]


def per_slide_settled(fn, *, label):
    """`per_slide`, and also redo an attempt the drive went away during.

    `per_slide` redoes an attempt that RAISED while the drive was gone. An
    attempt can also be hit without that: an ls_io write inside it waits for
    the drive and succeeds, and the CZI handles the attempt holds are then to
    a volume that went away - the next read fails with everything reachable,
    or, worse, a file looked for during the wait was "missing" and nothing
    failed at all. So an attempt during which `disturbed_since` became true is
    redone, whether it returned or raised; one that was not disturbed returns
    or raises exactly as `per_slide` would. `Stopped` and Ctrl+C pass through.
    """
    while True:
        tok = [None]

        def attempt():
            tok[0] = mark()
            return fn()

        try:
            out = per_slide(attempt, label=label)
        except (KeyboardInterrupt, Stopped):
            raise
        except Exception:
            if tok[0] is None or not disturbed_since(tok[0]):
                raise
            wait_for_drive(label)
            continue
        if not disturbed_since(tok[0]):
            return out
        wait_for_drive(label)


# Remember the study's folders now, while its file can still be read.
try:
    watched()
except Exception:                                           # noqa: BLE001
    pass
