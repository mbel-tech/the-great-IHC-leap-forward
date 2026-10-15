"""A curation save that fails has to be seen, at once, until one succeeds.

The curators save on every decision through `state.Bridge`. A failed write used
to be only `print()`ed, and in the installed app stdout goes nowhere: the
operator could curate for hours with nothing reaching disk. These checks make
the write fail the ways it does in the field - a file another program holds
open (Windows refuses the replace), a project file that cannot be read back -
and look for the failure where the operator will see it, and for the last good
file still being there.

Run:  python tests/test_curation_save.py
"""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "app"))
sys.path.insert(0, os.path.join(REPO, "scripts"))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# The curator view reads config for its export folder; never the operator's study.
from _fixture import use_temp_study                              # noqa: E402

STUDY = use_temp_study()

from PySide6.QtWidgets import QApplication, QMessageBox          # noqa: E402

app = QApplication.instance() or QApplication([])

import ls_project as LP                                          # noqa: E402
import state as ST                                               # noqa: E402

fails = 0


def chk(label, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
    print(("ok   " if ok else "FAIL ") + label.ljust(64) + f" {got!r}"
          + ("" if ok else f"   want {want!r}"))


class held_open:
    """os.replace refusing, as Windows does while another program has the file open."""

    def __enter__(self):
        self.real = os.replace

        def refuse(src, dst):
            raise PermissionError(13, "Access is denied", src, None, dst)
        os.replace = refuse

    def __exit__(self, *exc):
        os.replace = self.real


KEY = "ls_roi_curator_v1"
GOOD = '{"LS1_s1":{"plate":"good"}}'
NEW = '{"LS1_s1":{"plate":"new"}}'

tmp = tempfile.mkdtemp()

print("--- the store keeps the last good file ---")
store = ST.CurationStore(os.path.join(tmp, "plain"))
store.write(KEY, GOOD)
try:
    with held_open():
        store.write(KEY, NEW)
    raised = None
except PermissionError as e:
    raised = type(e).__name__
chk("a refused replace raises", raised, "PermissionError")
chk("...the previous good file is intact", open(store.path(KEY), encoding="utf-8").read(), GOOD)
chk("...and no half-written temp file is left beside it", os.path.exists(store.path(KEY) + ".tmp"), False)

print("--- the bridge reports a failure instead of printing it ---")
saved, errors = [], []
br = ST.Bridge(store, on_save=lambda k, n: saved.append(k),
               on_error=lambda k, msg: errors.append((k, msg)))
with held_open():
    br.save(KEY, NEW)
chk("a failed save reaches on_error, not on_save", (len(errors), saved), (1, []))
msg = errors[0][1] if errors else ""
chk("...naming the curation", KEY in msg, True)
chk("...the file it could not write", store.path(KEY) in msg, True)
chk("...and why (the file is held open)", ("Access is denied" in msg, "open in another program" in msg), (True, True))
br.save(KEY, NEW)
chk("the next good save reaches on_save", (saved, len(errors)), ([KEY], 1))
chk("...and is on disk", open(store.path(KEY), encoding="utf-8").read(), NEW)


class BrokenProject:
    """A project whose file cannot be read back, so recording curation in it fails."""
    read_only = False
    path = os.path.join(tmp, "study.json")

    def curation_text(self, key):
        return None

    def write_curation(self, key, text, out_root=None):
        raise LP.ProjectError("%s could not be read to record curation: bad JSON" % self.path)


errors.clear()
pbr = ST.Bridge(ST.CurationStore(os.path.join(tmp, "proj"), lambda: BrokenProject()),
                on_error=lambda k, m: errors.append((k, m)))
try:
    pbr.save(KEY, NEW)
    escaped = None
except Exception as e:                                           # noqa: BLE001
    escaped = type(e).__name__
chk("a project error does not escape the slot (Qt would only print it)", escaped, None)
chk("...it is reported, naming the project file", (len(errors), BrokenProject.path in (errors[0][1] if errors else "")), (1, True))

print("--- the curator view shows it until a save succeeds ---")
import curator_view as CV                                        # noqa: E402

out = os.path.join(tmp, "view")
os.makedirs(out)
server = CV.LocalServer(out)
view = CV.CuratorView(server, out)
view.show()
lines = []
view.logged.connect(lines.append)
view.bridge.save(KEY, GOOD)


def boxes():
    return [b for b in view.findChildren(QMessageBox) if b.isVisible()]


with held_open():
    view.bridge.save(KEY, NEW)
chk("a failure puts a warning on the curator at once", view.save_warning.isVisible(), True)
chk("...that says what was not saved", KEY in view.save_warning.text(), True)
chk("...with one message box", len(boxes()), 1)
chk("...and a line in the log", any("NOT SAVED" in s and KEY in s for s in lines), True)
with held_open():
    view.bridge.save(KEY, NEW)
chk("a second failure in the same streak keeps the warning, no second box",
    (view.save_warning.isVisible(), len(boxes())), (True, 1))
chk("the good file is still the last good decision", open(view.store.path(KEY), encoding="utf-8").read(), GOOD)
for b in boxes():
    b.close()
view.refresh_seed()
with held_open():
    view.bridge.save(KEY, NEW)
chk("after a seed refresh the new bridge still reports", view.save_warning.isVisible(), True)
view.bridge.save(KEY, NEW)
chk("a save that succeeds clears the warning", view.save_warning.isVisible(), False)
chk("...and says so in the log", any("reaching disk again" in s for s in lines), True)
with held_open():
    view.bridge.save(KEY, GOOD)
chk("a new failure streak shows a box again", len(boxes()), 1)
server.stop()

print()
print("FAILED: %d" % fails if fails else "ALL PASS")
sys.exit(1 if fails else 0)
