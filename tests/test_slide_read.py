"""Reading slide headers off the window's thread (slide_read.py).

On a USB stick or a OneDrive folder each CZI header can take a second, and with
400 of them read on the window's thread Windows marks the app "Not responding".
Here the header reader is swapped for a slow stand-in and the suite checks that
the event loop keeps turning while it runs, that the result is the one the
plain read gives, and that a read replaced by a newer one never lands.

    python tests/test_slide_read.py
"""

import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(REPO, "app"), os.path.join(REPO, "scripts"), HERE]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from _fixture import use_temp_study                             # noqa: E402

STUDY = use_temp_study()
os.environ["LS_HOME"] = tempfile.mkdtemp()

failures = []


def chk(name, got, want=True):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name:<70} {got!r}" + ("" if ok else f"  (wanted {want!r})"))
    if not ok:
        failures.append(name)


# Reuse the header writers from test_autodetect (everything above its first scenario).
src = open(os.path.join(HERE, "test_autodetect.py")).read().split('print("--- one scan per marker')[0]
ns = {"__name__": "autodetect_helpers", "__file__": os.path.join(HERE, "test_autodetect.py")}
exec(compile(src, "test_autodetect_helpers", "exec"), ns)
xml, write_czi = ns["xml"], ns["write_czi"]
DAPI, AF568, AF488 = ns["DAPI"], ns["AF568"], ns["AF488"]

import autodetect as AD                                         # noqa: E402

# Two folders that read differently: paired scans at 0.65 µm, and at 0.325 µm.
PAIRED = STUDY.source_dir
for n in range(1, 4):
    write_czi(os.path.join(PAIRED, "LS22_%da.czi" % n), xml([DAPI, AF568]))
    write_czi(os.path.join(PAIRED, "LS22_%db.czi" % n), xml([DAPI, AF488]))
FINE = os.path.join(STUDY.root, "fine")
os.makedirs(FINE)
for n in range(1, 4):
    write_czi(os.path.join(FINE, "LS45_%da.czi" % n), xml([DAPI, AF568], px_m=0.325e-6))


def summary(found):
    """What the window shows of a read: values, lines, report."""
    return json.dumps([found.files, found.values, found.lines(), found.report],
                      sort_keys=True, default=str) if found is not None else None


WANT = {d: summary(AD.detect(d)) for d in (PAIRED, FINE)}
chk("the two folders read differently", WANT[PAIRED] != WANT[FINE], True)

# The slow stand-in: every header takes DELAY seconds, as off a USB stick.
DELAY = 0.12
real_read = AD.czi_meta.read_metadata
opened = []


def slow_read(fh):
    opened.append(os.path.dirname(fh.name))
    time.sleep(DELAY)
    return real_read(fh)


AD.czi_meta.read_metadata = slow_read

from PySide6.QtCore import QTimer                               # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox         # noqa: E402

app = QApplication.instance() or QApplication([])
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
# New study's "create anyway?" question: what it said is kept, answers[0] given.
asked, answers = [], [QMessageBox.No]
QMessageBox.question = staticmethod(lambda p, t, x, *a, **k: (asked.append(x), answers[0])[1])

ticks = []
timer = QTimer()
timer.timeout.connect(lambda: ticks.append(time.monotonic()))
timer.start(10)


def settle(reader, ms=20000):
    """Turn the event loop until `reader` (None on the old code) is done."""
    end = time.monotonic() + ms / 1000.0
    while reader is not None and reader.busy and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)
    for _ in range(5):
        app.processEvents()


print("--- Set up > Read settings from the slides ---")
import main_window as MW                                        # noqa: E402

w = MW.MainWindow(REPO, config_path=STUDY.path)
s = w.setup
project = s.session.project
project.set("source_dir", PAIRED, by="test")
for key in ("pixel_size_um", "slide_naming"):            # left for the slides to say
    project.unset(key, by="test")
import copy                                                     # noqa: E402
FILLS = AD.fill(copy.deepcopy(project.cfg), AD.detect(PAIRED))
chk("the study leaves something for the slides to fill in", bool(FILLS), True)
seen = []
opened.clear()
ticks.clear()
t0 = time.monotonic()
s.detect_from_slides(quiet=True)
took = time.monotonic() - t0
reader = getattr(s, "slide_read", None)
if reader is not None:
    reader.progress.connect(lambda k, n: seen.append(s.detect_line.text()))
chk("the button returns at once, not after six slow headers", took < 3 * DELAY, True)
settle(reader)
chk("the window kept processing events while the headers were read",
    len(ticks) >= int(6 * DELAY / 0.010 / 3), True)
chk("...and said how far it got", "Reading slide 6 of 6…" in seen, True)
chk("each header was read once", len(opened), 6)
chk("the settings filled in are the ones a plain read gives",
    {k: AD._get(project.cfg, k)[1] for k in FILLS}, FILLS)
chk("...the study keeps the same report",
    (project.get("project") or {}).get("slides", {}).get("source_dir"), PAIRED)
chk("...and the line says what was found, not how far it got",
    s.detect_line.text().startswith("6 slide files found."), True)

print("--- a second click while reading does not start a second read ---")
opened.clear()
s.detect_from_slides(quiet=True)
s.detect_from_slides(quiet=True)
settle(reader)
chk("the headers were still read once each", len(opened), 6)

print("--- What the slides say… on a folder not read yet ---")
import slide_report as SR                                       # noqa: E402

shown = []
SR.SlideReportDialog.exec = lambda self: shown.append(self) or 0
project.block.pop("slides", None)
AD._CACHE.clear()
opened.clear()
ticks.clear()
t0 = time.monotonic()
s.show_slide_report()
took = time.monotonic() - t0
chk("the report button returns at once too", took < 3 * DELAY, True)
settle(reader)
chk("...the window kept going while it read", len(ticks) >= int(6 * DELAY / 0.010 / 3), True)
chk("...and the report opens when the read lands", len(shown), 1)
chk("...read once", len(opened), 6)

print("--- New study: a newer folder wins over a slower, older read ---")
from new_study import NewStudyDialog                            # noqa: E402

dlg = NewStudyDialog()
landed = []
r = getattr(dlg, "slide_read", None)
if r is not None:
    r.finished.connect(lambda src, found: landed.append(src))
opened.clear()
ticks.clear()
dlg.slides.edit.setText(PAIRED)
t0 = time.monotonic()
dlg._read_slides()
took = time.monotonic() - t0
chk("choosing the slides folder returns at once", took < 3 * DELAY, True)
chk("...the dialog says it is reading", dlg.found.text().startswith("Reading slide"), True)
chk("...and Create waits for it", dlg.ok.isEnabled(), False)
end = time.monotonic() + 2 * DELAY
while time.monotonic() < end:                                   # part-way into the first read
    app.processEvents()
    time.sleep(0.005)
dlg.slides.edit.setText(FINE)
dlg._read_slides()
settle(r)
time.sleep(8 * DELAY)                                           # let the stale thread run out
settle(r)
chk("the dialog holds the newer folder's read", summary(dlg.detection) == WANT[FINE], True)
chk("...and only that one landed", landed, [FINE])
chk("...the older read stopped early", 0 < opened.count(PAIRED) < 6, True)
chk("...and the window kept going throughout", len(ticks) >= int(5 * DELAY / 0.010 / 3), True)

print("--- New study: Create while the read is still going ---")
out = os.path.join(STUDY.root, "new study out")
dlg2 = NewStudyDialog()
dlg2.slides.edit.setText(PAIRED)
dlg2.output.edit.setText(out)
dlg2.thickness.setValue(14.0)
dlg2._create()
chk("nothing is made before the slides are read", os.path.exists(AD.study_file(out)), False)
settle(getattr(dlg2, "slide_read", None))
chk("...and the study is made once they are", os.path.isfile(AD.study_file(out)), True)
chk("...from the same read", summary(dlg2.detection) == WANT[PAIRED], True)
chk("...with no question asked, since the slides read cleanly", asked, [])

print("--- New study: what the read found wrong is shown before the study is made ---")
# Unreadable files and names the rule misses used to be listed under the
# folder and then Create went ahead without a word; now it asks.
BAD = os.path.join(STUDY.root, "bad")
os.makedirs(BAD)
for n in range(1, 4):
    write_czi(os.path.join(BAD, "LS30_%da.czi" % n), xml([DAPI, AF568]))
    write_czi(os.path.join(BAD, "LS30_%db.czi" % n), xml([DAPI, AF488]))
write_czi(os.path.join(BAD, "odd name.czi"), xml([DAPI, AF568]))
open(os.path.join(BAD, "LS30_9a.czi"), "wb").write(b"not a czi")


def make(out, answer):
    asked.clear()
    answers[0] = answer
    d = NewStudyDialog()
    d.slides.edit.setText(BAD)
    d.output.edit.setText(out)
    d.thickness.setValue(14.0)
    d._create()
    settle(getattr(d, "slide_read", None))
    return d


back = os.path.join(STUDY.root, "bad out back")
d3 = make(back, QMessageBox.No)
chk("Create asks before making a study from a troubled read", len(asked), 1)
text = asked[0] if asked else ""
chk("...naming the unreadable file", "could not be read" in text, True)
chk("...and the file name that does not fit", "do not fit" in text or "does not fit" in text, True)
chk("Go back makes nothing", os.path.exists(AD.study_file(back)), False)
chk("...and leaves the dialog open to change things", d3.result(), 0)
chk("...with Create still there to press", d3.ok.isEnabled(), True)
anyway = os.path.join(STUDY.root, "bad out anyway")
make(anyway, QMessageBox.Yes)
chk("Create anyway makes the study", os.path.isfile(AD.study_file(anyway)), True)

print("\n%s" % ("FAILED: %s" % failures if failures else "all passed"))
sys.exit(1 if failures else 0)
