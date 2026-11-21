"""ihc.cmd is written beside the runtime of an installed Windows copy, and only then.

Run:  python tests/test_console_launcher.py
"""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "app"))

import update_apply as UA                                   # noqa: E402

failures = []


def chk(name, got, want):
    ok = got == want
    print(("  PASS  " if ok else "  FAIL  ") + name + ("" if ok else f": {got!r} != {want!r}"))
    if not ok:
        failures.append(name)


def fake_install(d):
    os.makedirs(os.path.join(d, "runtime"))
    open(os.path.join(d, "runtime", "python.exe"), "w").close()
    return os.path.join(d, "ihc.cmd")


with tempfile.TemporaryDirectory() as d:
    cmd = fake_install(d)
    chk("missing: written", UA.ensure_console_launcher(d, "win32"), True)
    with open(cmd, newline="") as fh:
        chk("content", fh.read().strip(), '@"%~dp0runtime\\python.exe" -m app %*')
    m = os.path.getmtime(cmd)
    chk("same: untouched", (UA.ensure_console_launcher(d, "win32"), os.path.getmtime(cmd)), (False, m))
    open(cmd, "w").write("stale")
    chk("different: rewritten", UA.ensure_console_launcher(d, "win32"), True)
    chk("no stray temp file", os.path.exists(cmd + ".tmp"), False)

with tempfile.TemporaryDirectory() as d:
    fake_install(d)
    os.makedirs(os.path.join(d, "ihc.cmd"))      # a directory in the way: the write cannot succeed
    try:
        r = UA.ensure_console_launcher(d, "win32")
    except Exception as e:                       # noqa: BLE001
        r = repr(e)
    chk("unwritable: no exception", r, False)

with tempfile.TemporaryDirectory() as d:
    chk("not Windows: nothing", (UA.ensure_console_launcher(d, "linux"), os.listdir(d)), (False, []))
    chk("no runtime: nothing", (UA.ensure_console_launcher(d, "win32"), os.listdir(d)), (False, []))

print("PASS" if not failures else f"FAIL: {failures}")
sys.exit(1 if failures else 0)
