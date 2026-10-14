"""Check a built bundle can do everything the app asks of it, offline.

    <bundle>/runtime/python.exe packaging/windows/smoke_test.py <bundle>

Imports every third-party library a stage uses, loads StarDist's model with the
network switched off, and asks the bundled R for the figure packages. Exits
non-zero on the first thing missing, naming it. The app's own --self-test covers
the window, the local server and QtWebEngine; this covers what the stages need,
which a window appearing says nothing about.
"""

import importlib
import os
import socket
import subprocess
import sys

bundle = os.path.abspath(sys.argv[1])
failures = []


def check(name, fn):
    try:
        detail = fn()
        print(f"  PASS  {name}" + (f"  ({detail})" if detail else ""))
    except BaseException as e:                              # noqa: BLE001
        print(f"  FAIL  {name}: {type(e).__name__}: {e}")
        failures.append(name)


# Every third-party top-level import in app/ and scripts/. stardist and
# tensorflow are checked by the model load below.
MODULES = ["numpy", "scipy", "pandas", "PIL", "matplotlib", "tifffile",
           "czifile", "imagecodecs", "fitz", "itk", "cv2", "skimage",
           "openpyxl", "pylibCZIrw", "PySide6.QtWebEngineWidgets",
           "PySide6.QtWebChannel"]
for m in MODULES:
    check(f"import {m}", lambda m=m: getattr(importlib.import_module(m),
                                               "__version__", ""))


def elastix():
    import itk
    itk.ElastixRegistrationMethod                           # noqa: B018 - lazy load
    return ""


check("itk-elastix registration", elastix)


def stardist_offline():
    real = socket.socket

    class NoNetwork(real):
        def connect(self, *a, **k):
            raise OSError("network disabled by smoke test")

    socket.socket = NoNetwork
    try:
        from stardist.models import StarDist2D
        model = StarDist2D.from_pretrained("2D_versatile_fluo")
        return f"KERAS_HOME={os.environ.get('KERAS_HOME')}, {type(model).__name__}"
    finally:
        socket.socket = real


check("StarDist model loads offline", stardist_offline)


def rscript():
    r = os.environ.get("RSCRIPT")
    if not r:
        return "no bundled R (skipped)"
    pkgs = ["ggplot2", "ggtext", "lmerTest", "emmeans", "multcomp", "officer", "readxl"]
    expr = "for (p in c(%s)) library(p, character.only = TRUE)" % ", ".join(
        f'"{p}"' for p in pkgs)
    out = subprocess.run([r, "-e", expr], capture_output=True, text=True)
    if out.returncode:
        raise RuntimeError(out.stderr.strip()[-800:])
    return r


check("R and figure packages", rscript)

print(f"\nsmoke test {'FAILED' if failures else 'PASSED'}")
sys.exit(1 if failures else 0)
