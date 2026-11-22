"""Swap an update into the install folder after the app has closed, or undo the last one.

    runtime\\python.exe -I updates\\update_apply.py apply <install> <install>\\updates\\new-<v> <pid>
    runtime\\python.exe -I updates\\update_apply.py undo  <install> -                           <pid>

app/updates.py copies this file into updates\\ and starts it, because the copy
in app\\ is one of the things it replaces. Standard library only, for the same
reason: it must not import anything it is about to move.

Only the managed parts (PARTS) are moved, and only those the update carries; the
interpreter, R, the StarDist model and every study stay where they are. The
replaced parts go to updates\\previous\\, which is what Help > Undo last update
puts back. If any move fails, or the new code does not import, every move made
so far is reversed and the app starts on the version it had. If even a reversal
fails, nothing is deleted: the old parts stay under updates\\ and the message on
the next start says where.

The helper can also die half way (power cut, killed by antivirus, the PC shut
down). Before it moves anything it writes updates\\swap-journal.json, saying
which parts it is about to move and where, and removes it once the swap is
settled. `recover()` reads a leftover journal and puts every part back where it
was before the swap started, under an exclusive lock file. app/__main__.py runs
it before the app starts; the bundled runtime's sitecustomize.py runs it only
when the `app` folder itself is missing, the one case the app cannot reach.
"""

import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

PARTS = ("app", "scripts", "analysis", "atlas",
         "config.example.json", "README.md", "VERSION.json")

RETRIES = 5
RETRY_DELAY = 1.0      # antivirus and Explorer hold files open for a moment

# The helper has no console; without this each child python.exe would open one,
# and closing it would kill the check and undo the update.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _move(src, dst):
    os.replace(src, dst)


def _move_retrying(src, dst):
    for attempt in range(RETRIES):
        try:
            return _move(src, dst)
        except PermissionError:
            if attempt == RETRIES - 1:
                raise
            time.sleep(RETRY_DELAY)


def _remove(path):
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
    elif os.path.exists(path):
        os.remove(path)


def _python(root, windowed=False):
    exe = "pythonw.exe" if windowed else "python.exe"
    return os.path.join(root, "runtime", exe)


def default_check(root):
    """Does the swapped-in code import? Runs the app's own --check-install."""
    try:
        r = subprocess.run([_python(root), "-m", "app", "--check-install"], cwd=root,
                           capture_output=True, timeout=120, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as e:
        _log(root, f"check could not run: {e}")
        return False
    if r.returncode != 0:
        _log(root, "check failed:\n" + (r.stdout + r.stderr).decode("utf-8", "replace"))
    return r.returncode == 0


JOURNAL = "swap-journal.json"


def journal_path(root):
    return os.path.join(root, "updates", JOURNAL)


def _write_journal(root, data):
    """Replace the journal in one step, so a crash leaves the old one or the new one, never half."""
    path = journal_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _drop_journal(root):
    for path in (journal_path(root), journal_path(root) + ".tries"):
        try:
            os.remove(path)
        except OSError:
            pass


def _swap(root, incoming, parked, check, mode="apply"):
    """Move each live part present in `incoming` to `parked`, and the new one in.

    "ok"      swapped and the check passed
    "undone"  something failed and every move was reversed
    "stuck"   something failed and a move could not be reversed; whatever did
              not go back is still in `parked` or `incoming`, which the caller
              must then keep

    The journal is written before the first move: which parts will move, whether
    each had a live copy, and the two folders. That is enough for `recover()` to
    tell, part by part, where the old copy is after a crash at any point. It is
    removed here when everything went back ("undone"); after "ok" the caller
    removes it once the old version is filed away; after "stuck" it stays, so the
    next start tries the reversal again.
    """
    parts = [[part, os.path.exists(os.path.join(root, part))] for part in PARTS
             if os.path.exists(os.path.join(incoming, part))]
    try:
        _write_journal(root, {"mode": mode, "phase": "swapping", "pid": os.getpid(),
                              "incoming": os.path.abspath(incoming),
                              "parked": os.path.abspath(parked), "parts": parts})
    except OSError as e:
        _log(root, f"could not write the swap journal, so nothing was moved: {e}")
        return "undone"
    # A count left beside an EARLIER journal (its removal failed) belongs to that
    # journal, not this one: read with it, a later interrupted update would give up
    # at once. If it cannot be removed, nothing is moved.
    try:
        os.remove(journal_path(root) + ".tries")
    except FileNotFoundError:
        pass
    except OSError as e:
        _log(root, f"could not clear an old recovery count, so nothing was moved: {e}")
        _drop_journal(root)
        return "undone"
    os.makedirs(parked)
    done = []                                   # (src, dst) in the order moved
    try:
        for part in PARTS:
            new = os.path.join(incoming, part)
            if not os.path.exists(new):
                continue
            live = os.path.join(root, part)
            if os.path.exists(live):
                _move_retrying(live, os.path.join(parked, part))
                done.append((live, os.path.join(parked, part)))
            _move_retrying(new, live)
            done.append((new, live))
        if check(root):
            try:
                with open(journal_path(root), encoding="utf-8") as fh:
                    journal = json.load(fh)
                _write_journal(root, dict(journal, phase="swapped"))
            except (OSError, ValueError) as e:
                # Still "swapping" on disk: a crash before the journal goes would put the
                # old version back, which is safe, only one update behind.
                _log(root, f"could not mark the swap journal as done: {e}")
            return "ok"
        _log(root, "the new version did not pass its check")
    except OSError as e:
        _log(root, f"move failed: {e}")
    outcome = "undone"
    for src, dst in reversed(done):
        try:
            _move_retrying(dst, src)
        except OSError as e:
            _log(root, f"could not put back {src} (it is still at {dst}): {e}")
            outcome = "stuck"
    if outcome == "undone":
        _drop_journal(root)
    return outcome


def _holds_parts(folder):
    return any(os.path.exists(os.path.join(folder, part)) for part in PARTS)


def _discard(folder, root):
    """Delete a parked folder only when no managed part is left in it; otherwise keep it and say so."""
    if not os.path.exists(folder):
        return
    if _holds_parts(folder):
        _log(root, f"kept {folder}: it still holds parts of an installed version")
        LAST_KEPT[:] = [folder]
        return
    shutil.rmtree(folder, ignore_errors=True)


def _hand_over(root, parked):
    """File `parked` as updates/previous, the copy Help > Undo puts back. True if it is there.

    The undo copy already there is moved aside (one rename) rather than deleted first:
    deleting a tree half way (a file held open on Windows) and then failing to move
    `parked` in would leave neither a whole `previous` nor `parked`. On any failure the
    old undo copy goes back, `parked` is kept, and the caller must not delete it.
    """
    updates = os.path.join(root, "updates")
    previous = os.path.join(updates, "previous")
    aside = None
    if os.path.exists(previous):
        aside = os.path.join(updates, f"previous-old-{_stamp()}")
        try:
            _move_retrying(previous, aside)
        except OSError as e:
            _log(root, f"could not keep the previous version for undo (its old copy is in use): {e}")
            return False
    try:
        _move_retrying(parked, previous)
    except OSError as e:
        _log(root, f"could not keep the previous version for undo: {e}")
        if aside:
            try:
                _move_retrying(aside, previous)
            except OSError as e2:
                _log(root, f"the undo copy from before is left at {aside}: {e2}")
        return False
    if aside:
        shutil.rmtree(aside, ignore_errors=True)
    return True


def _stamp():
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def apply(root, new_dir, check=default_check):
    """Swap new_dir in. The replaced parts become updates/previous only on success,
    so a failed update keeps the undo of the one before it."""
    LAST_KEPT[:] = []
    updates = os.path.join(root, "updates")
    parked = os.path.join(updates, f"parked-{_stamp()}")
    outcome = _swap(root, new_dir, parked, check, "apply")
    if outcome == "stuck":
        LAST_KEPT[:] = [parked]
        return outcome
    if outcome == "ok":
        # `parked` is the version just replaced: it is deleted only by becoming `previous`.
        # A hand-over that fails (files held open on Windows) keeps it where it is.
        if not _hand_over(root, parked):
            LAST_KEPT[:] = [parked]
        _drop_journal(root)
    else:
        _discard(parked, root)
    shutil.rmtree(new_dir, ignore_errors=True)
    return outcome


def undo(root, check=default_check):
    LAST_KEPT[:] = []
    updates = os.path.join(root, "updates")
    previous = os.path.join(updates, "previous")
    parked = os.path.join(updates, f"undone-{_stamp()}")
    outcome = _swap(root, previous, parked, check, "undo")
    if outcome == "stuck":
        LAST_KEPT[:] = [parked]
        return outcome
    if outcome == "ok":
        # One step back only: the version just left is not kept for a redo.
        shutil.rmtree(previous, ignore_errors=True)
        shutil.rmtree(parked, ignore_errors=True)
        _drop_journal(root)
    else:
        _discard(parked, root)
    return outcome


def _alive(pid):
    """Is process `pid` still running? Never this process; never kills anything to find out."""
    if not isinstance(pid, int) or pid <= 0 or pid == os.getpid():
        return False
    if sys.platform == "win32":
        # os.kill(pid, 0) on Windows TERMINATES the process: ask for its exit code instead.
        import ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = ctypes.c_void_p             # a HANDLE, not a C int
        k32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
        k32.GetExitCodeProcess.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
        k32.CloseHandle.argtypes = (ctypes.c_void_p,)
        h = k32.OpenProcess(0x1000, False, pid)              # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            # Access denied means it exists (another user's, or protected): treat as running,
            # so a helper's journal is never taken over while it might still be working.
            return ctypes.get_last_error() == 5             # ERROR_ACCESS_DENIED
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return not ok or code.value == 259                   # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


LOCK = "swap-journal.lock"
STALE_LOCK = 600        # seconds: a recovery never takes this long; an older lock is a crash's
MAX_ATTEMPTS = 3        # failed reversals before recovery stops and says "run the installer"
TRIED_ENV = "LSAPP_UPDATE_RECOVERY_"


def _lock_holder(path):
    """(pid written in a lock file or 0, its mtime) - or None if there is no such file."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return int(fh.read().strip() or 0), mtime
    except (OSError, ValueError):
        return 0, mtime


def _take_lock(root):
    """Create updates/swap-journal.lock exclusively; its path, or None if another process holds it.

    O_CREAT|O_EXCL is atomic on NTFS and POSIX alike, so of several interpreters starting
    together exactly one recovers. A lock whose writer is dead, or older than STALE_LOCK, is
    a leftover from a crash and is taken over - by renaming it aside under a name of our own
    first, never by deleting `path`: between judging it stale and deleting it, another
    process may have taken it over and written a fresh lock there, and deleting that would
    leave two recovering at once. If what was renamed is not the lock judged stale, it is
    put back and this process stands aside. One being written this instant (no pid yet,
    young) counts as held.
    """
    path = os.path.join(root, "updates", LOCK)
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            seen = _lock_holder(path)
            if seen is None:
                continue                                    # just released: try again
            holder, mtime = seen
            if time.time() - mtime < STALE_LOCK and (holder == 0 or _alive(holder)):
                return None
            aside = f"{path}.stale-{os.getpid()}-{_stamp()}"
            try:
                os.replace(path, aside)
            except OSError:
                return None                                 # someone else is handling it
            if _lock_holder(aside) != seen:
                # Not the one judged stale: another process took it over in between. Give it back
                # - but never over a lock a THIRD process has written at `path` since.
                _put_back(aside, path)
                return None
            try:
                os.remove(aside)
            except OSError:
                pass
            continue
        except OSError:
            return None
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
        return path
    return None


def _put_back(aside, path):
    """Move `aside` back to `path` only if `path` is free; True if it went back.

    Never replaces: "check it is free, then os.replace" left a window in which a third
    process could write a fresh lock at `path`, and the replace then overwrote it - that
    process went on believing it held the lock while the file named another. A hard link
    (POSIX) or a rename (Windows, where rename refuses an existing target) fails instead of
    overwriting. When it fails the aside is left where it is: `path` belongs to someone else.
    """
    try:
        if os.name == "nt":
            os.rename(aside, path)
        else:
            os.link(aside, path)
            try:
                os.remove(aside)
            except OSError:
                pass
        return True
    except OSError:
        return False


def _release_lock(path):
    """Remove the lock only if it is still ours (after STALE_LOCK another process may own it)."""
    held = _lock_holder(path)
    if held is not None and held[0] == os.getpid():
        try:
            os.remove(path)
        except OSError:
            pass


def _tell(root, text):
    """Log `text` and add it to what the app shows on its next start."""
    _log(root, text)
    try:
        with open(os.path.join(root, "updates", "last-result.txt"), "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    except OSError:
        pass


def recover(root):
    """Finish or reverse a swap whose helper died. Returns (message or None, [parts moved]).

    Nothing to do without a journal, or while the helper that wrote it is still running
    (its own check and compile steps start interpreters too), or while another process
    holds the lock. Once per process per install: sitecustomize and app/__main__.py run in
    the same process, and a second try straight after a failed one would fail the same way
    and say so twice. Never raises: a failure is logged and the app starts as it can.
    """
    tried = TRIED_ENV + hashlib.sha1(os.path.abspath(root).encode("utf-8")).hexdigest()[:10]
    path = journal_path(root)
    if os.environ.get(tried) == str(os.getpid()) or not os.path.exists(path):
        return None, []
    try:
        with open(path, encoding="utf-8") as fh:
            j = json.load(fh)
    except OSError:
        return None, []
    except ValueError:
        j = None
    if isinstance(j, dict) and _alive(j.get("pid")):
        return None, []
    lock = _take_lock(root)
    if lock is None:
        return None, []
    os.environ[tried] = str(os.getpid())
    try:
        return _recover_locked(root, j)
    except Exception as e:                                  # noqa: BLE001
        _log(root, f"recovering the interrupted update failed: {e!r}")
        return None, []
    finally:
        _release_lock(lock)


def _read_tries(root, j):
    """{"attempts", "told"}: from the journal, or from its side file when that was all that could
    be written (whichever counted further)."""
    tries = {"attempts": int(j.get("attempts") or 0), "told": bool(j.get("told"))}
    try:
        with open(journal_path(root) + ".tries", encoding="utf-8") as fh:
            side = json.load(fh)
        tries["attempts"] = max(tries["attempts"], int(side.get("attempts") or 0))
        tries["told"] = tries["told"] or bool(side.get("told"))
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return tries


def _write_tries(root, j, tries):
    """Keep the count in the journal, or else in a side file beside it. False if neither took it."""
    try:
        _write_journal(root, dict(j, **tries))
        return True
    except OSError as e:
        _log(root, f"could not count the recovery attempt in the journal: {e}")
    try:
        with open(journal_path(root) + ".tries", "w", encoding="utf-8") as fh:
            json.dump(tries, fh)
        return True
    except OSError:
        return False


def _valid_journal(j):
    if not isinstance(j, dict) or j.get("phase") not in ("swapping", "swapped"):
        return False
    if not all(isinstance(j.get(k), str) and j.get(k) for k in ("parked", "incoming")):
        return False
    parts = j.get("parts")
    return isinstance(parts, list) and all(
        isinstance(p, list) and len(p) == 2 and p[0] in PARTS and isinstance(p[1], bool)
        for p in parts)


def _recover_locked(root, j):
    """recover(), with the lock held. Part by part, from what is on disk now:

      had a live copy, old copy in parked   the live one (if any) is the new one: back
                                            to `incoming`, then the old one back in
      had a live copy, none in parked       it never moved; nothing to do
      new part, live and not in incoming    it is the new one: back to `incoming`

    so it works wherever the crash fell, including between moving the live `app` out and
    the new one in. "swapped" means the new version passed its check and only the filing
    of the old one was cut short; that is finished instead. A reversal that fails is
    retried on later starts, saying so the first time, and given up after MAX_ATTEMPTS.
    """
    log = os.path.join(root, "updates", "last-update.log")
    if not _valid_journal(j):
        bad = journal_path(root) + f".bad-{_stamp()}"
        try:
            os.replace(journal_path(root), bad)
        except OSError:
            bad = journal_path(root)
        text = (f"An update left a journal that cannot be read ({bad}), so nothing was put "
                f"back. If the app misbehaves, run the installer again; your studies are not "
                f"affected. Details: {log}")
        _tell(root, text)
        return text, []
    mode = j.get("mode") or "apply"
    what = "update" if mode == "apply" else "undo of the last update"
    parked, incoming = j["parked"], j["incoming"]
    updates = os.path.join(root, "updates")
    moved, problems = [], []
    if j["phase"] == "swapped":
        if mode == "apply" and os.path.isdir(parked) and not _hand_over(root, parked):
            problems.append(f"the version it replaced is kept in {parked}")
        _drop_journal(root)
        left = sorted(n for n in os.listdir(updates)
                      if n.startswith(("undone-", "previous-old-"))) if os.path.isdir(updates) else []
        if left:
            _log(root, "left behind by the interrupted swap, safe to delete: " + ", ".join(left))
        text = (f"The {what} finished, but the app closed before it was tidied up"
                + (f"; {problems[0]}." if problems else "; that is done now."))
        _tell(root, text)
        return text, moved
    # Without `app/` only sitecustomize gets here, once per launch, and nothing can run on the
    # install: giving up would leave it broken for good with a message no screen shows, and
    # there is no running app for a retry to move folders under. So nothing is counted then.
    app_missing = not os.path.isdir(os.path.join(root, "app"))
    tries = _read_tries(root, j)
    if tries["attempts"] >= MAX_ATTEMPTS and not app_missing:
        return None, []                                     # said so when it gave up
    for part, had_live in reversed(j["parts"]):
        live, old, new = (os.path.join(root, part), os.path.join(parked, part),
                          os.path.join(incoming, part))
        try:
            if had_live and os.path.exists(old):
                if os.path.exists(live):
                    if os.path.exists(new):
                        problems.append(f"{part}: both {live} and {new} exist")
                        continue
                    os.makedirs(incoming, exist_ok=True)
                    _move_retrying(live, new)
                _move_retrying(old, live)
                moved.append(part)
            elif not had_live and os.path.exists(live) and not os.path.exists(new):
                os.makedirs(incoming, exist_ok=True)
                _move_retrying(live, new)
                moved.append(part)
        except OSError as e:
            problems.append(f"{part} could not be put back: {e}")
        if had_live and not os.path.exists(live):
            problems.append(f"{part} is missing from {root}")
    if not problems:
        _drop_journal(root)
        if os.path.isdir(parked) and not _holds_parts(parked):
            shutil.rmtree(parked, ignore_errors=True)
        text = f"The {what} was interrupted, so it was reversed: you are on {_version(root)}, as before."
        _tell(root, text)
        return text, moved
    detail = "; ".join(problems)
    if not app_missing:
        tries["attempts"] += 1
    if not tries["told"]:
        tries["told"] = True
        text = (f"The {what} was interrupted and could not be fully reversed ({detail}). It "
                f"will be tried again on the next start; if the app does not start, run the "
                f"installer again. Your studies are not affected. Details: {log}")
    elif tries["attempts"] >= MAX_ATTEMPTS and not app_missing:
        text = (f"The {what} was interrupted, and putting the old version back failed "
                f"{tries['attempts']} times ({detail}). It will not be tried again: run the "
                f"installer again. Your studies are not affected. Details: {log}")
    else:
        text = None
    if not _write_tries(root, j, tries):
        # The count could not be kept anywhere, so this would say the same thing on every start.
        _log(root, f"recovery attempt failed and could not be counted: {detail}")
        return None, moved
    if text is None:
        _log(root, f"recovery attempt {tries['attempts']} failed: {detail}")
        return None, moved
    _tell(root, text)
    return text, moved


LAST_KEPT = []          # where a stuck rollback left the parts it could not put back


def result_text(mode, outcome, old, new, root):
    """The one line the app shows on its next start."""
    log = os.path.join(root, "updates", "last-update.log")
    if outcome == "ok":
        done = f"Updated to {new}." if mode == "apply" else f"Undid the update; back on {new}."
        if LAST_KEPT:
            done += (f" The version it replaced could not be kept for Help > Undo (files in "
                     f"use); it is in {LAST_KEPT[0]}. Details: {log}")
        return done
    if outcome == "undone":
        what = f"The update to {new}" if mode == "apply" else "Undoing the update"
        return f"{what} failed, and nothing was changed: you are still on {old}. Details: {log}"
    kept = LAST_KEPT[0] if LAST_KEPT else os.path.join(root, "updates")
    return (f"{'The update to ' + new if mode == 'apply' else 'Undoing the update'} failed "
            f"and could not be fully reversed. The files of {old} are kept in {kept}. "
            f"If the app misbehaves, run the installer again; your studies are not "
            f"affected. Details: {log}")


# ---------------------------------------------------------------- the helper process

CONSOLE_LAUNCHER = "ihc.cmd"
CONSOLE_LAUNCHER_TEXT = '@"%~dp0runtime\\python.exe" -m app %*'


def ensure_console_launcher(root, platform=None):
    """Write ihc.cmd beside the runtime of an installed Windows copy when it is missing or differs.

    build.ps1 writes it, but a copy updated from inside the app never saw the
    installer that carries it. Atomic (temp file, then replace); a read-only
    folder or any other OSError is logged and ignored. Returns True when it
    wrote the file.
    """
    if (platform or sys.platform) != "win32":
        return False
    if not os.path.exists(os.path.join(root, "runtime", "python.exe")):
        return False
    path = os.path.join(root, CONSOLE_LAUNCHER)
    tmp = path + ".tmp"
    try:
        try:
            with open(path, encoding="ascii") as fh:
                if fh.read().strip() == CONSOLE_LAUNCHER_TEXT:
                    return False
        except OSError:
            pass
        with open(tmp, "w", encoding="ascii", newline="\r\n") as fh:
            fh.write(CONSOLE_LAUNCHER_TEXT + "\n")
        os.replace(tmp, path)
        return True
    except (OSError, UnicodeError) as e:
        _log(root, f"could not write {CONSOLE_LAUNCHER}: {e}")
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def _log(root, text):
    try:
        os.makedirs(os.path.join(root, "updates"), exist_ok=True)
        with open(os.path.join(root, "updates", "last-update.log"), "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}  {text}\n")
    except OSError:
        pass


def _version(path):
    try:
        with open(os.path.join(path, "VERSION.json"), encoding="utf-8") as fh:
            return json.load(fh).get("version", "?")
    except (OSError, ValueError):
        return "?"


def _wait_for_exit(pid, timeout=600):
    if pid <= 0:
        return
    if sys.platform == "win32":
        import ctypes
        SYNCHRONIZE = 0x00100000
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(SYNCHRONIZE, False, pid)
        if h:
            k32.WaitForSingleObject(h, int(timeout * 1000))
            k32.CloseHandle(h)
        time.sleep(1)                           # let the last file handles go
        return
    end = time.time() + timeout
    while time.time() < end:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.2)


def _detached(args, root):
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(args, cwd=root, creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


def main(argv):
    mode, root, new_dir, pid = argv[0], os.path.abspath(argv[1]), argv[2], int(argv[3])
    _wait_for_exit(pid)
    old = _version(root)
    if mode == "apply":
        new = _version(new_dir)
        _log(root, f"updating {old} -> {new}")
        outcome = apply(root, new_dir)
    else:
        new = _version(os.path.join(root, "updates", "previous"))
        _log(root, f"undoing {old} -> {new}")
        outcome = undo(root)
    result = result_text(mode, outcome, old, new, root)
    _log(root, result)
    try:
        with open(os.path.join(root, "updates", "last-result.txt"), "w", encoding="utf-8") as fh:
            fh.write(result)
    except OSError:
        pass
    if outcome == "ok":
        # So the first start after an update does not write .pyc into the
        # install while the app is running. Failures here only cost speed.
        parts = [p for p in ("app", "scripts", "analysis") if os.path.isdir(os.path.join(root, p))]
        try:
            subprocess.run([_python(root), "-m", "compileall", "-q", *parts], cwd=root,
                           capture_output=True, timeout=300, creationflags=NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            pass
    _detached([_python(root, windowed=True), "-m", "app"], root)
    return 0 if outcome == "ok" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
