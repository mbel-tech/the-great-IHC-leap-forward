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
for b in boxes():
    b.close()
server.stop()

print("--- an import is not saved over by the page that was open before it ---")
# A real page in a real QtWebEngine, because the bug lives between the two: the open
# curator keeps the pre-import state in memory and in localStorage, re-saves it (on
# every decision, every 60 s, on unload), and a reload only seeds keys that are empty.
# The page is the ROI curator's storage pattern and nothing else (04l: `let S =
# initState(localStorage.getItem(KEY))`, `save`, `setInterval(autosave)`, beforeunload),
# with the interval cut from 60 s to 200 ms.
from PySide6.QtCore import QEventLoop, QTimer                   # noqa: E402
import glob                                                      # noqa: E402
import json                                                      # noqa: E402

PAGE = """<!doctype html><meta charset="utf-8"><script>
const KEY = "ls_roi_curator_v1";
let S = JSON.parse(localStorage.getItem(KEY) || "{}");
const save = () => localStorage.setItem(KEY, JSON.stringify(S));
setInterval(save, 200);
addEventListener("beforeunload", save);
</script>"""


def spin(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def js(code):
    got, loop = [], QEventLoop()
    view.view.page().runJavaScript(code, 0, lambda v: (got.append(v), loop.quit()))
    QTimer.singleShot(5000, loop.quit)
    loop.exec()
    return got[0] if got else None


def page_state():
    raw = js("JSON.stringify(S)")
    return json.loads(raw) if raw else None


def stored():
    raw = view.store.read(KEY)
    return json.loads(raw) if raw else None


def settle(cond, ms=6000):
    """Spin until `cond()` holds or `ms` pass; a page load and a few autosaves fit easily."""
    for _ in range(ms // 100):
        if cond():
            return True
        spin(100)
    return cond()


def wait_loaded(want):
    """Until the page shows `want` and has saved it at least once more (so it is live)."""
    return settle(lambda: page_state() == want) and (spin(600) or True)


OLD = {"LS1_s1": {"plate": "old"}}
IMPORTED = {"LS1_s1": {"plate": "imported"}, "LS1_s2": {"plate": "imported"}}

out = os.path.join(tmp, "import")
os.makedirs(os.path.join(out, "reformatted"))
with open(os.path.join(out, "reformatted", "roi.html"), "w", encoding="utf-8") as fh:
    fh.write(PAGE)
ST.CurationStore(out).write(KEY, json.dumps(OLD))               # curation from an earlier day
server = CV.LocalServer(out)
view = CV.CuratorView(server, out)
view.show()
lines = []
view.logged.connect(lines.append)
view.show_page("reformatted/roi.html")
chk("the open page starts from the store", wait_loaded(OLD), True)

bundle = os.path.join(tmp, "curation_state.json")
with open(bundle, "w", encoding="utf-8") as fh:
    json.dump({KEY: json.dumps(IMPORTED)}, fh)
keys = view.store.import_bundle(bundle)
view.curation_replaced(keys)
settle(lambda: page_state() == IMPORTED)
spin(800)                                                        # several autosaves
chk("after the import the store still holds the import", stored(), IMPORTED)
chk("...the open page was reloaded with it", page_state(), IMPORTED)
chk("...so its localStorage holds it too", json.loads(js("localStorage.getItem(KEY)") or "null"), IMPORTED)
backups = glob.glob(os.path.join(out, "curation", "_backup_*", KEY + ".json"))
chk("what the import replaced is kept in a backup folder",
    [json.load(open(p, encoding="utf-8")) for p in backups], [OLD])

js("S.LS1_s3 = {plate: 'after'}; save(); true")
after = dict(IMPORTED, LS1_s3={"plate": "after"})
chk("a decision made on the reloaded page is saved", settle(lambda: stored() == after), True)
view.view.reload()
wait_loaded(after)
chk("...and a later reload does not seed the import over it again",
    (page_state(), stored()), (after, after))

lines.clear()
view.bridge.saveFrom("a page from before the import", KEY, json.dumps(OLD))
chk("a save from a page loaded before the import is not written", stored(), after)
aside = glob.glob(os.path.join(out, "curation", "_backup_*", "from_open_page", KEY + ".json"))
chk("...it is kept aside instead", [json.load(open(p, encoding="utf-8")) for p in aside], [OLD])
chk("...and the log says where", any(KEY in s and "from_open_page" in s for s in lines), True)
chk("a backup folder is not a curation file the reconcile would adopt",
    sorted(LP._files_in(out)), [KEY])

print("--- an import that runs as a stage (04q) ---")
view.hold([KEY])
js("S.LS1_s4 = {plate: 'during'}; save(); true")
during = dict(after, LS1_s4={"plate": "during"})
spin(600)
chk("while it runs, the page's saves of that key are held back", stored(), after)
view.release()
chk("a run that wrote nothing releases them to the store", stored(), during)

view.hold([KEY])
js("S.LS1_s5 = {plate: 'during'}; save(); true")
spin(600)
STAGE = {"LS1_s9": {"plate": "from 04q"}}
view.store.write(KEY, json.dumps(STAGE))                         # what 04q --write does
view.curation_replaced([KEY])
settle(lambda: page_state() == STAGE)
spin(800)
chk("a run that wrote the import keeps it", stored(), STAGE)
chk("...and the page shows it", page_state(), STAGE)
kept = sorted(glob.glob(os.path.join(out, "curation", "_backup_*", "from_open_page", KEY + ".json")))
chk("...while the page's held work is kept aside, not dropped",
    any(json.load(open(p, encoding="utf-8")).get("LS1_s5") == {"plate": "during"} for p in kept), True)
server.stop()

print()
print("FAILED: %d" % fails if fails else "ALL PASS")
sys.exit(1 if fails else 0)
