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
"""

import datetime
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


def _swap(root, incoming, parked, check):
    """Move each live part present in `incoming` to `parked`, and the new one in.

    "ok"      swapped and the check passed
    "undone"  something failed and every move was reversed
    "stuck"   something failed and a move could not be reversed; whatever did
              not go back is still in `parked` or `incoming`, which the caller
              must then keep
    """
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
    return outcome


def _stamp():
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def apply(root, new_dir, check=default_check):
    """Swap new_dir in. The replaced parts become updates/previous only on success,
    so a failed update keeps the undo of the one before it."""
    updates = os.path.join(root, "updates")
    parked = os.path.join(updates, f"parked-{_stamp()}")
    outcome = _swap(root, new_dir, parked, check)
    if outcome == "stuck":
        LAST_KEPT[:] = [parked]
        return outcome
    if outcome == "ok":
        previous = os.path.join(updates, "previous")
        shutil.rmtree(previous, ignore_errors=True)
        try:
            _move_retrying(parked, previous)
        except OSError as e:
            _log(root, f"could not keep the previous version for undo: {e}")
    shutil.rmtree(parked, ignore_errors=True)
    shutil.rmtree(new_dir, ignore_errors=True)
    return outcome


def undo(root, check=default_check):
    updates = os.path.join(root, "updates")
    previous = os.path.join(updates, "previous")
    parked = os.path.join(updates, f"undone-{_stamp()}")
    outcome = _swap(root, previous, parked, check)
    if outcome == "stuck":
        LAST_KEPT[:] = [parked]
        return outcome
    if outcome == "ok":
        # One step back only: the version just left is not kept for a redo.
        shutil.rmtree(previous, ignore_errors=True)
    shutil.rmtree(parked, ignore_errors=True)
    return outcome


LAST_KEPT = []          # where a stuck rollback left the parts it could not put back


def result_text(mode, outcome, old, new, root):
    """The one line the app shows on its next start."""
    log = os.path.join(root, "updates", "last-update.log")
    if outcome == "ok":
        return f"Updated to {new}." if mode == "apply" else f"Undid the update; back on {new}."
    if outcome == "undone":
        what = f"The update to {new}" if mode == "apply" else "Undoing the update"
        return f"{what} failed, and nothing was changed: you are still on {old}. Details: {log}"
    kept = LAST_KEPT[0] if LAST_KEPT else os.path.join(root, "updates")
    return (f"{'The update to ' + new if mode == 'apply' else 'Undoing the update'} failed "
            f"and could not be fully reversed. The files of {old} are kept in {kept}. "
            f"If the app misbehaves, run the installer again; your studies are not "
            f"affected. Details: {log}")


# ---------------------------------------------------------------- the helper process

def _log(root, text):
    try:
        os.makedirs(os.path.join(root, "updates"), exist_ok=True)
        with open(os.path.join(root, "updates", "last-update.log"), "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}  {text}\n")
    except OSError:
        pass


def _version(path):
    import json
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
