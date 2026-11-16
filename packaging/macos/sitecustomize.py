"""Runs at start-up of the bundled interpreter, before the app is imported.

Inside the app the layout is

    Contents/Resources/runtime/bin/python3   this interpreter (python-build-standalone)
    Contents/Resources/app, scripts, ...     the repository, unchanged
    Contents/Resources/keras                 StarDist's pretrained model

Nothing in app/ or scripts/ knows it is installed. This file points what would
otherwise look on the system at the copies beside it - the Keras model cache,
and the certificate list the bundled OpenSSL needs for HTTPS. Anything the
user has set already wins. (The Windows twin is packaging/windows/sitecustomize.py.)
"""

import os
import sys

_root = os.path.dirname(os.path.abspath(sys.base_prefix))

# StarDist fetches its model through keras.utils.get_file, which caches under
# KERAS_HOME. The model is downloaded at build time, so Measure works offline.
_keras = os.path.join(_root, "keras")
if os.path.isdir(_keras):
    os.environ.setdefault("KERAS_HOME", _keras)

# python-build-standalone's OpenSSL does not read the macOS keychain, so
# without this every HTTPS request (the update check) fails certificate checks.
if "SSL_CERT_FILE" not in os.environ:
    try:
        import certifi
        os.environ["SSL_CERT_FILE"] = certifi.where()
    except ImportError:
        pass

# An update whose helper died half way can leave the install without its `app`
# folder, and then `-m app` cannot even be found. The helper keeps a journal
# while it swaps (app/update_apply.py); its own copy in updates/ - outside every
# folder it moves - knows how to put the old version back, so it is loaded from
# there, before anything is imported from app/. Silent: the app reports what
# happened on its next start, and nothing here may stop the interpreter.
if os.path.exists(os.path.join(_root, "updates", "swap-journal.json")):
    try:
        import importlib.util
        _spec = importlib.util.spec_from_file_location(
            "_lsapp_update_recover", os.path.join(_root, "updates", "update_apply.py"))
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _mod.recover(_root)
        del _spec, _mod
    except Exception:                                       # noqa: BLE001
        pass

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

del _root, _keras
