"""ls_drive: wait for a dropped drive, redo the unit that was interrupted.

Run:  python tests/test_ls_drive.py
"""

import contextlib
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")

_spec = importlib.util.spec_from_file_location("lsdrive", os.path.join(SCRIPTS, "ls_drive.py"))
D = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(D)
D.POLL = 0.1

fails = 0


def chk(label, got, want=True):
    global fails
    ok = got == want
    if not ok:
        fails += 1
    print(("ok   " if ok else "FAIL ") + label.ljust(54) + f" {got!r}"
          + ("" if ok else f"   want {want!r}"))


def capture(fn):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        out = fn()
    return out, buf.getvalue()


tmp = tempfile.mkdtemp(prefix="lsdrive_")
a, b = os.path.join(tmp, "a"), os.path.join(tmp, "b")
gone = os.path.join(tmp, "b_gone")
stopfile = os.path.join(tmp, "stop")


def setup():
    for p in (a, b, gone):
        shutil.rmtree(p, ignore_errors=True)
    os.makedirs(a)
    os.makedirs(b)
    if os.path.exists(stopfile):
        os.remove(stopfile)


def drop():
    os.rename(b, gone)


def restore():
    os.rename(gone, b)


def later(delay, fn):
    t = threading.Timer(delay, fn)
    t.start()
    return t


os.environ["LS_WATCH"] = os.pathsep.join([a, b])
os.environ["LS_STOP_FILE"] = stopfile
os.environ.pop("LS_PROGRESS", None)
try:
    # watched() / reachable()
    setup()
    chk("watched from LS_WATCH", D.watched(), [a, b])
    chk("reachable both", D.reachable(), (True, None))
    drop()
    chk("reachable after drop", D.reachable(), (False, b))
    restore()

    # watched() from LS_CONFIG
    import json
    cfg = os.path.join(tmp, "cfg.json")
    with open(cfg, "w") as fh:
        json.dump({"source_dir": a, "out_root": "", "x": 1}, fh)
    os.environ.pop("LS_WATCH")
    os.environ["LS_CONFIG"] = cfg
    chk("watched from config, empty dropped", D.watched(), [a])
    os.environ["LS_WATCH"] = os.pathsep.join([a, b])

    # wait_for_drive, progress markers
    setup()
    os.environ["LS_PROGRESS"] = "1"
    drop()
    t = later(0.5, restore)
    n0 = len(D.PAUSES)
    secs, out = capture(lambda: D.wait_for_drive("slide 7", poll=0.1))
    t.join()
    chk("wait returns >= 0.4", secs >= 0.4)
    chk("exactly one lost line", out.count("@@drive lost "), 1)
    chk("exactly one back line", out.count("@@drive back "), 1)
    chk("lost line format", f"@@drive lost {b} | slide 7" in out)
    chk("PAUSES label", D.PAUSES[-1]["label"], "slide 7")
    chk("PAUSES grew by 1", len(D.PAUSES) - n0, 1)
    chk("reachable wait is 0.0", D.wait_for_drive("x"), 0.0)

    # human lines
    os.environ.pop("LS_PROGRESS")
    drop()
    t = later(0.3, restore)
    _, out = capture(lambda: D.wait_for_drive("slide 8", poll=0.1))
    t.join()
    chk("human mode has no markers", "@@drive" not in out)
    chk("human mode says paused", "paused at slide 8" in out and "back after" in out)

    # per_slide: redo after drop
    setup()
    drop()
    t = later(0.4, restore)
    calls = []

    def fn1():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("gone")
        return "ok"
    res, _ = capture(lambda: D.per_slide(fn1, label="s"))
    t.join()
    chk("per_slide returns ok", res, "ok")
    chk("per_slide fn called twice", len(calls), 2)

    # second drop during the redo
    setup()
    drop()
    n0 = len(D.PAUSES)
    calls = []
    stop_r = threading.Event()

    def restorer():
        done = 0
        while done < 2 and not stop_r.is_set():
            if not os.path.isdir(b) and os.path.isdir(gone):
                time.sleep(0.3)
                restore()
                done += 1
            time.sleep(0.02)
    rt = threading.Thread(target=restorer)
    rt.start()

    def fn2():
        calls.append(1)
        if len(calls) == 2:
            drop()
            raise OSError("gone again")
        if len(calls) == 1:
            raise OSError("gone")
        return "done"
    res, _ = capture(lambda: D.per_slide(fn2, label="s"))
    rt.join()
    chk("second drop result", res, "done")
    chk("second drop fn called 3 times", len(calls), 3)
    chk("second drop PAUSES grew by 2", len(D.PAUSES) - n0, 2)

    # reachable folder + failing fn: ordinary failure
    setup()
    n0 = len(D.PAUSES)
    calls = []

    def fn3():
        calls.append(1)
        raise ValueError("bad czi")
    try:
        capture(lambda: D.per_slide(fn3, label="s"))
        got = None
    except ValueError as exc:
        got = str(exc)
    chk("ordinary failure re-raised", got, "bad czi")
    chk("ordinary failure called once", len(calls), 1)
    chk("ordinary failure no pause", len(D.PAUSES), n0)

    # KeyboardInterrupt is never retried, even with the drive gone
    drop()
    calls = []

    def fn4():
        calls.append(1)
        raise KeyboardInterrupt
    try:
        capture(lambda: D.per_slide(fn4, label="s"))
        got = None
    except KeyboardInterrupt:
        got = "ki"
    chk("KeyboardInterrupt propagates", (got, len(calls)), ("ki", 1))
    restore()

    # Stop file
    setup()
    drop()
    t = later(0.3, lambda: open(stopfile, "w").close())
    t0 = time.time()
    try:
        capture(lambda: D.wait_for_drive("s", poll=0.1))
        got = None
    except D.Stopped:
        got = "stopped"
    t.join()
    chk("Stopped raised", got, "stopped")
    chk("Stopped within 1 s", time.time() - t0 < 1.0)
    # and per_slide does not swallow it
    try:
        capture(lambda: D.per_slide(lambda: (_ for _ in ()).throw(OSError("x")), label="s"))
        got = None
    except D.Stopped:
        got = "stopped"
    chk("per_slide passes Stopped through", got, "stopped")
    os.remove(stopfile)
    restore()

    # Watchdog announces a loss while fn is blocked
    setup()
    os.environ["LS_PROGRESS"] = "1"
    seen = {}

    def fn5():
        threading.Timer(0.1, drop).start()
        time.sleep(0.6)
        seen["out"] = sys.stdout.getvalue()
        return 1
    res, out = capture(lambda: D.per_slide(fn5, label="blocked"))
    chk("watchdog printed lost before fn returned", "@@drive lost " in seen.get("out", ""))
    chk("watchdog loss printed once", out.count("@@drive lost "), 1)
    restore()

    # (a) watchdog-announced loss heals while fn blocks, then fn raises
    setup()
    D._ANNOUNCED = None
    os.environ["LS_PROGRESS"] = "1"
    calls = []

    def fn6():
        calls.append(1)
        if len(calls) == 1:
            threading.Timer(0.1, drop).start()
            time.sleep(0.4)
            restore()
            time.sleep(0.05)
            raise OSError("blip")
        return "again"
    res, out = capture(lambda: D.per_slide(fn6, label="blip"))
    chk("healed blip retried", (res, len(calls)), ("again", 2))
    chk("healed blip: one lost, one back", (out.count("@@drive lost "), out.count("@@drive back ")), (1, 1))
    chk("healed blip: nothing pending", D._ANNOUNCED, None)
    drop()
    t = later(0.3, restore)
    _, out = capture(lambda: D.wait_for_drive("next", poll=0.1))
    t.join()
    chk("later loss announced again", out.count("@@drive lost "), 1)

    # (b) watchdog-announced loss, then Stopped: next loss still announced
    setup()
    D._ANNOUNCED = None

    def fn7():
        threading.Timer(0.1, drop).start()
        time.sleep(0.4)
        raise D.Stopped("x")
    try:
        capture(lambda: D.per_slide(fn7, label="stopme"))
        got = None
    except D.Stopped:
        got = "stopped"
    chk("Stopped passes through watchdog", got, "stopped")
    chk("Stopped leaves nothing pending", D._ANNOUNCED, None)
    t = later(0.3, restore)
    _, out = capture(lambda: D.wait_for_drive("after", poll=0.1))
    t.join()
    chk("loss after Stopped announced", out.count("@@drive lost "), 1)

    # mark / disturbed_since: a wait finished since the mark, a loss still
    # announced, or a folder gone now.
    setup()
    D._ANNOUNCED = None
    os.environ["LS_PROGRESS"] = "1"
    m = D.mark()
    chk("undisturbed since a fresh mark", D.disturbed_since(m), False)
    drop()
    chk("disturbed while a folder is gone", D.disturbed_since(m), True)
    t = later(0.3, restore)
    capture(lambda: D.wait_for_drive("absorbed", poll=0.1))
    t.join()
    chk("disturbed once a wait has finished since", D.disturbed_since(m), True)
    chk("...and not since a newer mark", D.disturbed_since(D.mark()), False)

    # per_slide_settled: a wait absorbed inside the unit (an ls_io write that
    # waited for the drive and then succeeded), after which the unit fails
    # with everything reachable - its handles were to the volume that went.
    def absorbed_wait():
        drop()
        t = later(0.3, restore)
        D.wait_for_drive("an atomic write", poll=0.1)
        t.join()

    calls = []

    def fn8():
        calls.append(1)
        if len(calls) == 1:
            absorbed_wait()
            raise RuntimeError("stale handle")
        return "again"
    res, out = capture(lambda: D.per_slide_settled(fn8, label="absorbed"))
    chk("settled: a failure after an absorbed wait is redone", (res, len(calls)), ("again", 2))
    calls = []

    def fn9():
        calls.append(1)
        if len(calls) == 1:
            absorbed_wait()
            return "first"
        return "second"
    res, out = capture(lambda: D.per_slide_settled(fn9, label="absorbed"))
    chk("settled: a success with a wait inside it is redone", (res, len(calls)), ("second", 2))
    calls = []

    def fn10():
        calls.append(1)
        raise RuntimeError("corrupt")
    try:
        capture(lambda: D.per_slide_settled(fn10, label="bad"))
        got = None
    except RuntimeError:
        got = "raised"
    chk("settled: an undisturbed failure is raised at once", (got, len(calls)), ("raised", 1))
    calls = []

    def fn11():
        calls.append(1)
        if len(calls) == 1:
            drop()
            later(0.3, restore)
            raise OSError("gone")
        return "ok"
    res, out = capture(lambda: D.per_slide_settled(fn11, label="plain drop"))
    chk("settled: a plain drop is redone once, not twice", (res, len(calls)), ("ok", 2))
    chk("...one lost, one back", (out.count("@@drive lost "), out.count("@@drive back ")), (1, 1))
    open(stopfile, "w").close()
    try:
        capture(lambda: D.per_slide_settled(lambda: (_ for _ in ()).throw(D.Stopped("x")),
                                            label="s"))
        got = None
    except D.Stopped:
        got = "stopped"
    os.remove(stopfile)
    chk("settled passes Stopped through", got, "stopped")
    os.environ.pop("LS_PROGRESS", None)

    # present(path, label): "is this file there?" asked of a drive that may
    # have dropped. A file on the vanished folder is waited for, not missing.
    setup()
    D._ANNOUNCED = None
    os.environ["LS_PROGRESS"] = "1"
    f = os.path.join(b, "slide.czi")
    open(f, "wb").close()
    chk("present: a file that is there", D.present(f, "slide.czi"), True)
    res, out = capture(lambda: D.present(os.path.join(b, "nope.czi"), "nope.czi"))
    chk("present: missing with the folder there is missing", res, False)
    chk("...said without waiting", "@@drive" in out, False)
    drop()
    t = later(0.3, restore)
    res, out = capture(lambda: D.present(f, "slide.czi"))
    t.join()
    chk("present: on a dropped drive, waited for and found", res, True)
    chk("...after one lost and one back",
        (out.count("@@drive lost "), out.count("@@drive back ")), (1, 1))
    drop()
    t = later(0.3, restore)
    res, out = capture(lambda: D.present(os.path.join(b, "nope.czi"), "nope.czi"))
    t.join()
    chk("present: on a dropped drive, waited for and still missing", res, False)
    chk("...after the wait", "@@drive back " in out, True)

    # The race this closes: the drive goes between the wait and the look.
    # Here the first look misses (the folder is away), and the folder is back
    # before the reachability check, so nothing waits - the file is looked for
    # again rather than reported missing.
    real_exists = os.path.exists
    looks = []

    def flappy(p):
        looks.append(p)
        if p == f and len(looks) == 1:
            return False          # looked while the drive was away
        return real_exists(p)
    os.path.exists = flappy
    try:
        res, out = capture(lambda: D.present(f, "slide.czi"))
    finally:
        os.path.exists = real_exists
    chk("present: a miss that healed before the wait is looked at again",
        res, True)

    # A folder that drops AFTER the wait but before the look: still waited for.
    looks = []

    def drop_on_first_look(p):
        looks.append(p)
        if p == f and len(looks) == 1:
            drop()
            later(0.3, restore)
        return real_exists(p)
    os.path.exists = drop_on_first_look
    try:
        res, out = capture(lambda: D.present(f, "slide.czi"))
    finally:
        os.path.exists = real_exists
    time.sleep(0.4)
    chk("present: a drop as the file is looked for is waited out", res, True)
    os.environ.pop("LS_PROGRESS", None)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("ALL OK" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
