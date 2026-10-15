"""Where the app's crash log goes, and that writing it never makes things worse.

The installed app's own folder may be read-only (a Program Files install), so
a crash written beside the executable left no log at all. The log goes to a
per-user folder instead - %LOCALAPPDATA% on Windows - with the temp folder as
the last resort, and the excepthook must not raise whatever happens.

Every location here is a temp directory: nothing is written to a real
LOCALAPPDATA or home.

Run:  python tests/test_crash_log.py
"""

import importlib.util
import io
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from _fixture import use_temp_study                        # noqa: E402
STUDY = use_temp_study()

# app/__main__.py by path, under its own name, so importing it does not run main().
_spec = importlib.util.spec_from_file_location(
    "lsapp_main", os.path.join(REPO, "app", "__main__.py"))
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

failures = []


def chk(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name:60} {got!r}")
    if not ok:
        failures.append(name)


TMP = tempfile.mkdtemp(prefix="lsapp-crashlog-test-")
saved_env = {n: os.environ.get(n) for n in ("LOCALAPPDATA", "XDG_STATE_HOME")}
saved_tempdir = tempfile.tempdir
saved_stderr = sys.stderr
saved_argv = sys.argv[:]
# No dialog, whatever else happens: nobody is there to close it.
sys.argv = [sys.argv[0], "--self-test"]
try:
    temp = os.path.join(TMP, "temp")
    os.makedirs(temp)
    tempfile.tempdir = temp

    # --------------------------------------------- per-user folder when it works
    lad = os.path.join(TMP, "LocalAppData")
    os.environ["LOCALAPPDATA"] = lad
    want = os.path.join(lad, "The Great IHC Leap Forward", "logs", "lsapp-crash.log")
    chk("crash log goes under LOCALAPPDATA", M.crash_log_path(), want)
    chk("its folder is created", os.path.isdir(os.path.dirname(want)), True)
    chk("not beside the executable", M.crash_log_path().startswith(M.REPO + os.sep), False)

    # ------------------------------------------ an unwritable choice falls back
    blocker = os.path.join(TMP, "not-a-folder")
    with open(blocker, "w") as fh:
        fh.write("a file where the folder would go")
    os.environ["LOCALAPPDATA"] = blocker
    chk("unwritable per-user folder falls back to temp",
        M.crash_log_path(), os.path.join(temp, "lsapp-crash.log"))

    # ------------------------------------------------ the hook writes the trace
    os.environ["LOCALAPPDATA"] = lad
    sys.stderr = io.StringIO()
    try:
        1 / 0
    except ZeroDivisionError as e:
        M._report(type(e), e, e.__traceback__)
    sys.stderr = saved_stderr
    with open(want, encoding="utf-8") as fh:
        text = fh.read()
    chk("hook wrote the traceback", ("Traceback" in text, "ZeroDivisionError" in text),
        (True, True))
    chk("traceback names the failing line", "1 / 0" in text, True)

    # ------------------------------------- nowhere writable: still never raises
    os.environ["LOCALAPPDATA"] = blocker
    tempfile.tempdir = blocker
    sys.stderr = io.StringIO()
    raised = None
    try:
        try:
            raise RuntimeError("boom")
        except RuntimeError as e:
            M._report(type(e), e, e.__traceback__)
    except BaseException as e:                              # noqa: BLE001
        raised = e
    sys.stderr = saved_stderr
    chk("hook does not raise when nothing is writable", raised, None)
finally:
    sys.stderr = saved_stderr
    sys.argv = saved_argv
    tempfile.tempdir = saved_tempdir
    for n, v in saved_env.items():
        if v is None:
            os.environ.pop(n, None)
        else:
            os.environ[n] = v
    import shutil
    shutil.rmtree(TMP, ignore_errors=True)

print()
print("PASS" if not failures else f"FAIL: {len(failures)}: {failures}")
sys.exit(1 if failures else 0)
