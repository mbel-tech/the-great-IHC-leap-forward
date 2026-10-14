"""Runs at start-up of the bundled interpreter, before the app is imported.

The installed layout is

    <install>/runtime/python.exe     this interpreter (python.org embeddable)
    <install>/app, scripts, analysis the repository, unchanged
    <install>/R                      R and the figure packages, when bundled
    <install>/keras                  StarDist's pretrained model

Nothing in app/ or scripts/ knows it is installed. This file points the two
things that would otherwise look on the system - Rscript and the Keras model
cache - at the copies beside it. Anything the user has set already wins.
"""

import os
import sys

_root = os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))

# 06e looks for RSCRIPT before PATH and Program Files.
_rscript = os.path.join(_root, "R", "bin", "Rscript.exe")
if os.path.exists(_rscript):
    os.environ.setdefault("RSCRIPT", _rscript)
    os.environ["PATH"] = os.path.dirname(_rscript) + os.pathsep + os.environ.get("PATH", "")

# StarDist fetches its model through keras.utils.get_file, which caches under
# KERAS_HOME. The model is downloaded at build time, so Measure works offline.
_keras = os.path.join(_root, "keras")
if os.path.isdir(_keras):
    os.environ.setdefault("KERAS_HOME", _keras)

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

del _root, _rscript, _keras
