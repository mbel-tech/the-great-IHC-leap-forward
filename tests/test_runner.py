"""The in-process stage runner, without a window.

Three things the app depends on and nothing else checked:

  * a stage's `\\r` progress lines reach the log as they happen, not as one
    line when the stage ends;
  * `{out_root}` and friends in a stage's argv are expanded, because 04q and
    06b take absolute paths and stages.py cannot know them;
  * the stage sees the runner's config through LS_CONFIG, which is what makes
    the frozen build work at all.

Run:  python tests/test_runner.py
"""

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "app"))

import runner as R                                          # noqa: E402

failures = []


def chk(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name:60} {got!r}")
    if not ok:
        failures.append(name)


# ---------------------------------------------------------------- _Tee
lines = []
t = R._Tee(lines.append)
t.write("  1/10\r  2/10\r  3/10\n")
chk("carriage-return progress is delivered per update", lines,
    ["  1/10", "  2/10", "  3/10"])
lines.clear()
t.write("half")
chk("a partial line waits", lines, [])
t.write(" line\nnext\r")
chk("...until its terminator, either kind", lines, ["half line", "next"])

# ------------------------------------------------------------- argv
with tempfile.TemporaryDirectory() as tmp:
    out_root = os.path.join(tmp, "out")
    cfg_path = os.path.join(tmp, "config.json")
    with open(cfg_path, "w", encoding="utf-8") as fh:
        json.dump({"out_root": out_root}, fh)
    scripts = os.path.join(tmp, "scripts")
    os.makedirs(scripts)

    r = R.Runner(scripts, cfg_path)
    chk("LS_CONFIG names the runner's config",
        os.environ.get("LS_CONFIG"), os.path.abspath(cfg_path))
    chk("{out_root} expands", r.expand_argv(["--x", "{out_root}/a.csv"]),
        ["--x", os.path.join(out_root, "a.csv")])
    chk("{repo} is the folder holding config.json",
        r.expand_argv(["{repo}"]), [os.path.dirname(os.path.abspath(cfg_path))])
    chk("plain arguments pass through", r.expand_argv(["--n", "96"]), ["--n", "96"])

    # A stage that reports what it was handed.
    with open(os.path.join(scripts, "99_probe.py"), "w", encoding="utf-8") as fh:
        fh.write("import os, sys\n"
                 "def main():\n"
                 "    print('cfg=' + os.environ.get('LS_CONFIG', ''))\n"
                 "    print('argv=' + '|'.join(sys.argv[1:]))\n"
                 "    for i in range(3): print(f'  {i}/3', end='\\r')\n"
                 "    print()\n")

    class St:
        sid, title, script, cli_only = "probe", "Probe", "99_probe.py", None
        argv = ["--in", "{out_root}/x"]

    got = []
    res = r.run(St(), on_line=got.append)
    chk("the probe stage ran", res.ok, True)
    chk("...and saw the config", "cfg=" + os.path.abspath(cfg_path) in got, True)
    chk("...and the expanded argv",
        "argv=--in|" + os.path.join(out_root, "x") in got, True)
    chk("...and its progress arrived line by line",
        [g for g in got if g.strip().endswith("/3")], ["  0/3", "  1/3", "  2/3"])

# ------------------------------------------------- main() returning a status
# Most stages end `sys.exit(main())` and refuse work with `return 1`. In
# process there is no sys.exit, so the runner has to read main()'s return value
# itself; a stage that refused must not be recorded as done.
with tempfile.TemporaryDirectory() as tmp:
    cfg_path = os.path.join(tmp, "config.json")
    with open(cfg_path, "w", encoding="utf-8") as fh:
        json.dump({"out_root": os.path.join(tmp, "out")}, fh)
    scripts = os.path.join(tmp, "scripts")
    os.makedirs(scripts)
    with open(os.path.join(scripts, "98_refuse.py"), "w", encoding="utf-8") as fh:
        fh.write("def main():\n"
                 "    print('working...')\n"
                 "    print('FAIL: no roi_regions.csv found')\n"
                 "    return 1\n")
    with open(os.path.join(scripts, "97_zero.py"), "w", encoding="utf-8") as fh:
        fh.write("def main():\n    print('fine')\n    return 0\n")

    r = R.Runner(scripts, cfg_path)

    class Refuse:
        sid, title, script, cli_only, argv = "refuse", "Refuse", "98_refuse.py", None, []

    class Zero:
        sid, title, script, cli_only, argv = "zero", "Zero", "97_zero.py", None, []

    res = r.run(Refuse())
    chk("main() returning 1 is a failure", res.ok, False)
    chk("...and the error names the stage's last words",
        "no roi_regions.csv found" in (res.error or ""), True)
    chk("main() returning 0 is a success", r.run(Zero()).ok, True)

    # cli_only may be a callable, asked each time (05c: whether this
    # interpreter can import StarDist). A function is truthy, so reading the
    # attribute as the answer refused 05c everywhere, StarDist or not, with
    # "is not run by the app: <function detect_cli_reason ...>".
    class Detectable(Zero):
        cli_only = staticmethod(lambda: None)

    class NotHere(Zero):
        cli_only = staticmethod(lambda: "needs StarDist")

    Detectable.cli_reason = NotHere.cli_reason = lambda self: self.cli_only()
    chk("a callable cli_only that answers None runs the stage", r.run(Detectable()).ok, True)
    res = r.run(NotHere())
    chk("...one that gives a reason refuses it", res.ok, False)
    chk("...in its own words", "needs StarDist" in (res.error or ""), True)


# ------------------------------------------------- switching study, mid-session
# ls_config caches the parsed config and lives in sys.modules, which the runner
# does not own. Clearing only its own module cache would reload every stage and
# hand each one the config that was just replaced - the operator changes
# out_root in Settings and the next stage writes to the old tree, silently.
sys.path.insert(0, os.path.join(REPO, "scripts"))
import ls_config as LC                                      # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    def a_config(tag):
        root = os.path.join(tmp, tag)
        os.makedirs(os.path.join(root, "slides"), exist_ok=True)
        open(os.path.join(root, "atlas.pdf"), "wb").close()
        with open(os.path.join(REPO, "config.example.json"), encoding="utf-8") as fh:
            cfg = json.load(fh)
        cfg["source_dir"] = os.path.join(root, "slides")
        cfg["out_root"] = os.path.join(root, "out")
        cfg["atlas_pdf"] = os.path.join(root, "atlas.pdf")
        path = os.path.join(root, "study.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
        return path, cfg["out_root"]

    first, out_a = a_config("a")
    second, out_b = a_config("b")

    run = R.Runner(os.path.join(REPO, "scripts"), first)
    mod = run.load("00b_verify_extraction.py")
    chk("a stage reads the runner's config", mod.OUT_ROOT, out_a)

    run.rebind(second)
    chk("rebind moves LS_CONFIG", os.environ["LS_CONFIG"], os.path.abspath(second))
    chk("the stage cache was dropped", run._modules, {})
    chk("...and so was the config module", "ls_config" in sys.modules, False)

    mod = run.load("00b_verify_extraction.py")
    chk("the reloaded stage sees the NEW study", mod.OUT_ROOT, out_b)

LC.clear_cache()


# ------------------------------------------------- the cache notices an edit
# This was the file's modification time. Two saves inside one filesystem
# timestamp tick look identical that way, and the cached stage modules would be
# handed back with the previous config still baked into their constants.
with tempfile.TemporaryDirectory() as tmp:
    cfg = os.path.join(tmp, "c.json")
    with open(cfg, "w", encoding="utf-8") as fh:
        fh.write('{"out_root": "/a"}')
    run = R.Runner(os.path.join(REPO, "scripts"), cfg)

    chk("the first look always counts as changed", run._config_changed(), True)
    chk("an untouched file has not changed", run._config_changed(), False)

    stat = os.stat(cfg)
    with open(cfg, "w", encoding="utf-8") as fh:
        fh.write('{"out_root": "/b"}')
    os.utime(cfg, (stat.st_atime, stat.st_mtime))
    chk("a different file with the same mtime HAS changed",
        run._config_changed(), True)


# ------------------------------------------------- Stop during a wait for the drive, in process
# A stage run in this process can wait for a dropped drive too (ls_io.retry ->
# ls_drive.wait_for_drive on out_root). Stop has to reach it: the runner gives
# every run its own stop file through LS_STOP_FILE, and Runner.stop creates it.
import shutil                                               # noqa: E402
import threading                                            # noqa: E402
import time                                                 # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    cfg_path = os.path.join(tmp, "config.json")
    with open(cfg_path, "w", encoding="utf-8") as fh:
        json.dump({"out_root": os.path.join(tmp, "out")}, fh)
    scripts = os.path.join(tmp, "scripts")
    os.makedirs(scripts)
    watch = os.path.join(tmp, "drive")
    os.makedirs(watch)
    seen = {}
    with open(os.path.join(scripts, "96_waits.py"), "w", encoding="utf-8") as fh:
        fh.write("import os, sys\n"
                 f"sys.path.insert(0, {os.path.join(REPO, 'scripts')!r})\n"
                 "import ls_io\n"
                 "def main():\n"
                 "    ls_io.drive_module().POLL = 0.05\n"
                 "    print('stopfile=' + os.environ.get('LS_STOP_FILE', ''))\n"
                 "    ls_io.atomic_write_csv(os.path.join(os.environ['LS_WATCH'], 'x.csv'),"
                 " [], ['a'])\n"
                 "    return 0\n")

    class Waits:
        sid, title, script, cli_only, argv = "waits", "Waits", "96_waits.py", None, []

    r = R.Runner(scripts, cfg_path)
    before_watch = os.environ.get("LS_WATCH")
    before_stop = os.environ.pop("LS_STOP_FILE", None)
    os.environ["LS_WATCH"] = watch
    shutil.move(watch, watch + "-gone")             # the drive is unplugged
    lines = []
    out = {}

    def go():
        out["res"] = r.run(Waits(), on_line=lines.append)

    th = threading.Thread(target=go, daemon=True)
    t0 = time.time()
    th.start()
    # No chk() while the stage runs: its stdout is redirected into the stage's log.
    end = time.time() + 10
    while not any("@@drive lost" in ln for ln in lines) and time.time() < end:
        time.sleep(0.02)
    waiting = any("@@drive lost" in ln for ln in lines)
    stop_file = next((ln.split("=", 1)[1] for ln in lines if ln.startswith("stopfile=")), "")
    existed = os.path.exists(stop_file) if stop_file else None
    r.stop()
    th.join(10)
    hung = th.is_alive()
    if hung:                                        # unstick it, or the rest blocks on the lock
        shutil.move(watch + "-gone", watch)
        th.join(10)
        shutil.move(watch, watch + "-gone")
    chk("the in-process stage is waiting for the drive", waiting, True)
    chk("...with a stop file named for it that does not exist yet",
        (bool(stop_file), existed), (True, False))
    chk("Stop ends it promptly", (hung, time.time() - t0 < 10), (False, True))
    res = out.get("res")
    chk("...as a stopped run", (res.ok, res.stopped) if res else None, (False, True))
    chk("...with the stopped text",
        (res.error, res.record_error()) if res else None,
        ("stopped while the drive was disconnected",) * 2)
    chk("...with the pause it was stopped in recorded",
        [(p["folder"], p["seconds"] >= 0) for p in res.pauses] if res else None,
        [(watch, True)])
    chk("the stop file is cleaned up", os.path.exists(stop_file), False)
    chk("LS_STOP_FILE is put back as it was", os.environ.get("LS_STOP_FILE"), None)
    shutil.move(watch + "-gone", watch)
    if before_watch is None:
        os.environ.pop("LS_WATCH", None)
    else:
        os.environ["LS_WATCH"] = before_watch
    if before_stop is not None:
        os.environ["LS_STOP_FILE"] = before_stop

    # A stop that comes before the run has its file is not lost.
    os.environ["LS_WATCH"] = watch
    shutil.move(watch, watch + "-gone")
    out.clear()
    r._lock.acquire()                               # another stage still has the runner
    th = threading.Thread(target=go, daemon=True)
    th.start()
    time.sleep(0.2)                                 # this run is starting, its file not named
    r.stop()
    r._lock.release()
    th.join(10)
    hung = th.is_alive()
    if hung:
        shutil.move(watch + "-gone", watch)
        th.join(10)
        shutil.move(watch, watch + "-gone")
    res = out.get("res")
    chk("a stop asked for as the run starts still stops it",
        (hung, res.stopped if res else None), (False, True))
    shutil.move(watch + "-gone", watch)
    os.environ.pop("LS_WATCH", None)
    if before_watch is not None:
        os.environ["LS_WATCH"] = before_watch

    # A wait in this process that ends with the drive back is in the result's
    # pauses, as a supervised child's is: the run history records it.
    os.environ["LS_WATCH"] = watch
    shutil.move(watch, watch + "-gone")
    threading.Timer(0.4, lambda: shutil.move(watch + "-gone", watch)).start()
    out.clear()
    lines.clear()
    go()
    res = out.get("res")
    chk("an in-process wait that ends: the run is ok", res.ok if res else None, True)
    chk("...and its pause is recorded",
        [(p["folder"], p["seconds"] >= 0.2, sorted(p)) for p in res.pauses] if res else None,
        [(watch, True, ["at", "folder", "label", "seconds"])])
    chk("...the marker lines still reach the log",
        [ln.split()[1] for ln in lines if ln.startswith("@@drive")], ["lost", "back"])
    os.environ.pop("LS_WATCH", None)
    if before_watch is not None:
        os.environ["LS_WATCH"] = before_watch

    # Ctrl+C during an in-process wait for the drive: recorded as a stop in a
    # pause, with the pause, and marked interrupted (the command line's 130).
    import _thread
    os.environ["LS_WATCH"] = watch
    shutil.move(watch, watch + "-gone")
    t = threading.Timer(0.6, _thread.interrupt_main)
    t.start()
    try:
        res = r.run(Waits(), on_line=lambda ln: None)
    except KeyboardInterrupt:
        res = None
    t.join()
    shutil.move(watch + "-gone", watch)
    chk("Ctrl+C in an in-process wait: interrupted, stopped in a pause",
        (res.interrupted, res.record_error(), len(res.pauses)) if res else None,
        (True, "stopped while the drive was disconnected", 1))
    os.environ.pop("LS_WATCH", None)
    if before_watch is not None:
        os.environ["LS_WATCH"] = before_watch

    # In the supervised child, the supervisor's stop file is the one that counts.
    os.environ["LS_CHILD"] = "1"
    os.environ["LS_STOP_FILE"] = os.path.join(tmp, "supervisor.flag")
    with open(os.path.join(scripts, "95_env.py"), "w", encoding="utf-8") as fh:
        fh.write("import os\ndef main():\n"
                 "    print('stopfile=' + os.environ.get('LS_STOP_FILE', ''))\n    return 0\n")

    class Env:
        sid, title, script, cli_only, argv = "env", "Env", "95_env.py", None, []

    got = []
    r.run(Env(), on_line=got.append)
    chk("...a child run keeps the supervisor's LS_STOP_FILE",
        got[:1], ["stopfile=" + os.path.join(tmp, "supervisor.flag")])
    os.environ.pop("LS_CHILD", None)
    os.environ.pop("LS_STOP_FILE", None)

# The study's folders reach ls_drive as LS_WATCH for the length of a run, in
# this process and in a supervised child, so a drive that drops with study.json
# on it is still seen to have dropped (the config cannot be read mid-drop).
with tempfile.TemporaryDirectory() as tmp:
    src, out_root = os.path.join(tmp, "slides"), os.path.join(tmp, "out")
    os.makedirs(src)
    os.makedirs(out_root)
    cfg_path = os.path.join(out_root, "study.json")
    with open(cfg_path, "w", encoding="utf-8") as fh:
        json.dump({"source_dir": src, "out_root": out_root}, fh)
    scripts = os.path.join(tmp, "scripts")
    os.makedirs(scripts)
    with open(os.path.join(scripts, "94_watch.py"), "w", encoding="utf-8") as fh:
        fh.write("import os\ndef main():\n"
                 "    print('watch=' + os.environ.get('LS_WATCH', ''))\n    return 0\n")

    class Watch:
        sid, title, script, cli_only, argv = "watch", "Watch", "94_watch.py", None, []

    before_watch = os.environ.pop("LS_WATCH", None)
    r = R.Runner(scripts, cfg_path)
    got = []
    r.run(Watch(), on_line=got.append)
    chk("an in-process run sees the study's folders as LS_WATCH",
        got[:1], ["watch=" + os.pathsep.join([src, out_root])])
    chk("...and LS_WATCH is put back after it", os.environ.get("LS_WATCH"), None)

    seen = {}
    real_run_child = R.SV.run_child

    def fake_run_child(argv, on_line, stop_file, **kw):
        seen["watch"] = os.environ.get("LS_WATCH")
        seen["env"] = dict(kw.get("env") or {})
        return 0, [], ""
    R.SV.run_child = fake_run_child

    class Reader:
        sid, title, script, cli_only, argv = "watch", "Watch", "94_watch.py", None, []
        reader = "pylibCZIrw"
    try:
        r.run(Reader())
    finally:
        R.SV.run_child = real_run_child
    chk("a supervised run's parent watches the study's folders",
        seen.get("watch"), os.pathsep.join([src, out_root]))
    chk("...and LS_WATCH is put back after it", os.environ.get("LS_WATCH"), None)

    os.environ["LS_WATCH"] = os.path.join(tmp, "mine")
    got = []
    r.run(Watch(), on_line=got.append)
    chk("an LS_WATCH already set is left as it is",
        (got[:1], os.environ.get("LS_WATCH")),
        (["watch=" + os.path.join(tmp, "mine")], os.path.join(tmp, "mine")))
    os.environ.pop("LS_WATCH", None)
    if before_watch is not None:
        os.environ["LS_WATCH"] = before_watch

print()
print("ALL PASS" if not failures else f"{len(failures)} FAILED")
sys.exit(1 if failures else 0)
