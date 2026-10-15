"""The channel editor as a window, driven without one.

tests/test_channel_edit.py pins the rules. This is the wiring: that the table
shows the study's channels, that editing it reaches the settings dialog's Save
and nothing is written before Save, that the pipeline's verdict is shown and
blocks OK, and that a layout change after editing is caught.

Runs offscreen; skipped when PySide6 is not installed.

Run:  python tests/test_channel_dialog.py
"""

import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "app"))
sys.path.insert(0, os.path.join(REPO, "scripts"))

try:
    from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QMessageBox, QPushButton
except ImportError:
    print("SKIP PySide6 is not installed - the stages do not need it")
    sys.exit(0)

from _fixture import use_temp_study                         # noqa: E402

CHANNELS = [{"name": "DAPI", "role": "nuclear", "czi_name": "DAPI", "index": 0},
            {"name": "Fos", "role": "marker", "czi_name": "AF568", "index": 1},
            {"name": "Npy", "role": "marker", "czi_name": "AF488", "index": 2}]
STUDY = use_temp_study(acquisition={"layout": "multiplex", "channels": CHANNELS},
                       display={"colours": {"Fos": "magenta"}})

import channel_editor as CEd                                # noqa: E402
import config_dialog as CD                                  # noqa: E402

app = QApplication([])
failures = []


def chk(label, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + label.ljust(68) + " " + repr(got))
    if not ok:
        print("     want " + repr(want))
        failures.append(label)


shown = []
QMessageBox.question = staticmethod(lambda p, t, x, *a, **k: (shown.append(t), QMessageBox.Yes)[1])
QMessageBox.warning = staticmethod(lambda p, t, x, *a, **k: shown.append("warn:" + t))
QMessageBox.information = staticmethod(lambda p, t, x, *a, **k: shown.append("info:" + t))


def read():
    with open(STUDY.path, encoding="utf-8") as fh:
        return json.load(fh)


before = read()

# ---- the editor on its own ---------------------------------------------------
ed = CEd.ChannelEditor(before, "multiplex")
chk("one table row per channel", ed.table.rowCount(), 3)
chk("names are shown", [ed._w(i, "Name").text() for i in range(3)], ["DAPI", "Fos", "Npy"])
chk("a marker's colour is shown", ed._w(1, "Colour").currentText(), "magenta")
chk("the counterstain row has no colour or show box",
    (ed._w(0, "Colour").isEnabled(), ed._w(0, "Show").isEnabled()), (False, False))
chk("a valid table is accepted", ed.buttons.button(QDialogButtonBox.Ok).isEnabled(), True)
chk("...and the pipeline says so", "accepts" in ed.status.text(), True)

ed._w(2, "Name").setText("Fos")                      # two markers, one name
chk("a duplicate name blocks OK", ed.buttons.button(QDialogButtonBox.Ok).isEnabled(), False)
chk("...and says why, in the pipeline's words", "both called" in ed.status.text(), True)
ed._w(2, "Name").setText("Npy")
chk("fixing it unblocks OK", ed.buttons.button(QDialogButtonBox.Ok).isEnabled(), True)

ed._w(2, "Measured in").setCurrentText("own")
chk("'own' enables the detector choice", ed._w(2, "Detector").isEnabled(), True)
ed._w(2, "Measured in").setCurrentText("nuclear")
chk("'nuclear' disables it", ed._w(2, "Detector").isEnabled(), False)

ed.table.setCurrentCell(2, 0)
ed._move(-1)
chk("Up swaps the rows", [ed._w(i, "Name").text() for i in range(3)], ["DAPI", "Npy", "Fos"])
ed._remove()
chk("Remove drops the selected row", ed.table.rowCount(), 2)
ed._add(CH_MARKER := "marker")
chk("Add appends a blank marker", (ed.table.rowCount(), ed._w(2, "Name").text()), (3, ""))
chk("...which the pipeline refuses until it is named", ed.buttons.button(QDialogButtonBox.Ok).isEnabled(), False)

ed2 = CEd.ChannelEditor(before, "multiplex")
ed2._ok()
chk("OK with no edit writes the same channels",
    CD.CE.apply(before, ed2.result_edits)["acquisition"]["channels"], CHANNELS)

# ---- paired layout: scan channel, what it stains, colour ---------------------
PCFG = {"acquisition": {"layout": "paired", "markers": ["AF568", "AF488"]},
        "marker_identity": {"AF568": "pERK"}}
pe = CEd.ChannelEditor(PCFG, "paired", scan_names=["AF568", "AF488"])
chk("paired has four columns", pe.table.columnCount(), 4)
chk("paired rows are the markers", [pe._w(i, "Name").text() for i in range(2)], ["AF568", "AF488"])
chk("...with what each stains", [pe._w(i, "Stains").text() for i in range(2)], ["pERK", ""])
pe._w(1, "Stains").setEditText("PCNA")
pe._ok()
chk("the antibody is kept beside the scan name, not in place of it",
    (pe.result_edits["acquisition.markers"], pe.result_edits["marker_identity"]),
    (["AF568", "AF488"], {"AF568": "pERK", "AF488": "PCNA"}))
pe2 = CEd.ChannelEditor(PCFG, "paired", scan_names=["AF568", "AF488"])
pe2._w(0, "Name").setEditText("pERK")
chk("a marker named by its protein matches no scan, and OK says so",
    (pe2.buttons.button(CEd.QDialogButtonBox.Ok).isEnabled(),
     "What it stains" in pe2.status.text()), (False, True))

# ---- the slides' channel names are read off the window's thread -------------
# On a USB stick or OneDrive each CZI header can take a second; read on the
# window's thread, Windows greyed the editor out as "Not responding" before it
# even opened. The read is held on a gate here, so the editor must be up and
# saying so while it is still running.
import threading                                            # noqa: E402
import time                                                 # noqa: E402
import autodetect as AD                                     # noqa: E402
from PySide6.QtCore import QCoreApplication                 # noqa: E402

slides = os.path.join(STUDY.root, "paired_slides")
os.makedirs(slides, exist_ok=True)
open(os.path.join(slides, "a.czi"), "wb").close()
gate, real_detect, threads = threading.Event(), AD.detect, []


def gated_detect(source_dir, *a, **k):
    threads.append(threading.current_thread())
    gate.wait(10)
    found = AD.Detection()
    found.files = ["a.czi"]
    found.values = {"acquisition.layout": "paired", "acquisition.markers": ["AF568", "AF488"]}
    return found


def pump(until, s=10):
    end = time.monotonic() + s
    while not until() and time.monotonic() < end:
        QCoreApplication.processEvents()
        time.sleep(0.01)


AD.detect = gated_detect
AD._CACHE.clear()
started = time.monotonic()
pr = CEd.ChannelEditor(PCFG, "paired", source_dir=slides)
pr.show()
QCoreApplication.processEvents()
chk("the editor opens before the slides are read", time.monotonic() - started < 5, True)
pump(lambda: threads)
chk("...the read runs on another thread",
    bool(threads) and threads[0] is not threading.main_thread(), True)
chk("...the editor says it is reading", "Reading" in pr.status.text(), True)
chk("...and OK waits for the names to check against",
    pr.buttons.button(QDialogButtonBox.Ok).isEnabled(), False)
chk("...while the table is there to edit already", pr.table.rowCount(), 2)
gate.set()
pump(lambda: pr.scan_names)
chk("the names fill in when the read is done", pr.scan_names, ["AF568", "AF488"])
chk("...the scan-channel boxes offer them",
    [pr._w(0, "Name").itemText(i) for i in range(pr._w(0, "Name").count())], ["AF568", "AF488"])
chk("...without changing what each row says",
    [pr._w(i, "Name").text() for i in range(2)], ["AF568", "AF488"])
chk("...the intro names them", "The slides name: AF568, AF488" in pr.intro.text(), True)
chk("...and OK is back", pr.buttons.button(QDialogButtonBox.Ok).isEnabled(), True)
pr.close()

# Closed mid-read: the read is given up and its late answer lands nowhere.
AD._CACHE.clear()
gate.clear()
threads.clear()
pc = CEd.ChannelEditor(PCFG, "paired", source_dir=slides)
pump(lambda: threads)
pc.reject()
gate.set()
threads[0].join(10)
pump(lambda: False, 0.3)
chk("closing mid-read drops the late answer", (pc.scan_names, pc.result_edits), ([], None))
del pc


# A read that fails says so, and OK is not held for it.
def broken_detect(source_dir, *a, **k):
    raise OSError("the drive went away")


AD.detect = broken_detect
AD._CACHE.clear()
pf = CEd.ChannelEditor(PCFG, "paired", source_dir=slides)
pump(lambda: not pf._read.busy)
chk("a failed read says why", "the drive went away" in pf.status.text(), True)
chk("...and OK is not held for it", pf.buttons.button(QDialogButtonBox.Ok).isEnabled(), True)
pf.close()
AD.detect = real_detect

# ---- through the settings dialog ---------------------------------------------
dlg = CD.ConfigDialog(REPO, path=STUDY.path)
btn = [b for b in dlg.findChildren(QPushButton) if b.text().startswith("Channels and markers")]
chk("the settings dialog offers the editor", len(btn), 1)


def scripted(self):
    self._w(1, "Name").setText("cFos")
    self._w(1, "Colour").setCurrentText("yellow")
    self._w(2, "Show").setChecked(False)
    self._ok()
    return QDialog.Accepted


CEd.ChannelEditor.exec = scripted
btn[0].click()
chk("nothing is written before Save", read() == before, True)
chk("the rename was confirmed with the operator", "Rename markers?" in shown, True)
dlg._save()
after = read()
chk("Save writes the renamed channel", [c["name"] for c in after["acquisition"]["channels"]], ["DAPI", "cFos", "Npy"])
chk("...its colour under the new name", after["display"]["colours"], {"cFos": "yellow"})
chk("...and the composite without the hidden marker", after["display"]["composite"], ["cFos"])
chk("...which the pipeline accepts", CD.CE.check(after, "multiplex")[0], [])

# ---- a layout change after editing is caught ---------------------------------
dlg2 = CD.ConfigDialog(REPO, path=STUDY.path)
CEd.ChannelEditor.exec = lambda self: (self._ok(), QDialog.Accepted)[1]
[b for b in dlg2.findChildren(QPushButton) if b.text().startswith("Channels and markers")][0].click()
dlg2.rows["acquisition.layout"] = (dlg2.rows["acquisition.layout"][0], lambda: "paired")
shown.clear()
dlg2._save()
chk("saving after a layout change is refused", "warn:Channels need another look" in shown, True)
chk("...and writes nothing", read() == after, True)

# ---- a paired study's editor is handed the folder, not names read here --------
dlg3 = CD.ConfigDialog(REPO, path=STUDY.path)
dlg3.rows["acquisition.layout"] = (dlg3.rows["acquisition.layout"][0], lambda: "paired")
dlg3.form._source_dir = lambda: slides
AD.scan_marker_names = lambda *_: (_ for _ in ()).throw(AssertionError("read on the window's thread"))
opened = []
CEd.ChannelEditor.exec = lambda self: (opened.append((self._read_for, self._read.busy)),
                                       self._read.cancel(), QDialog.Rejected)[2]
[b for b in dlg3.findChildren(QPushButton) if b.text().startswith("Channels and markers")][0].click()
chk("the settings dialog leaves the slides to the editor's own read", opened, [(slides, True)])

# ---- the marker list and detection from a CZI ----------------------------------
import struct                                               # noqa: E402
import tempfile                                             # noqa: E402
import czi_meta                                             # noqa: E402
from PySide6.QtWidgets import QFileDialog                   # noqa: E402

box = CEd.MarkerBox("", "green")
items = [box.itemText(i) for i in range(box.count())]
chk("the list opens with Custom", items[0], CEd.CUSTOM_LABEL)
chk("...then the band's own markers under a header", items[1:4], ["-- typical for green --", "Ki-67", "Pan-cytokeratin"])
chk("...then everything else", "-- other markers --" in items and "CD68" in items, True)
chk("headers cannot be chosen", box.model().item(1).isEnabled(), False)
box.setText("Ki-67")
chk("a listed marker reads back", box.text(), "Ki-67")
box.setText("my own antibody")
chk("a typed marker reads back", box.text(), "my own antibody")
box._picked(0)
chk("choosing Custom clears the box for typing", box.text(), "")
chk("a box left on the Custom label counts as empty", (box.setText(CEd.CUSTOM_LABEL), box.text())[1], "")
chk("with no band known, nothing is 'typical'",
    any(t.startswith("-- typical") for t in (CEd.MarkerBox("", None).itemText(i) for i in range(CEd.MarkerBox("", None).count()))), False)

XML = """<ImageDocument><Metadata><Information><Image><Dimensions><Channels>
<Channel Id="Channel:0" Name="DAPI"><EmissionWavelength>465</EmissionWavelength></Channel>
<Channel Id="Channel:1" Name="AF568"><EmissionWavelength>603</EmissionWavelength></Channel>
<Channel Id="Channel:2" Name="AF488"><EmissionWavelength>519</EmissionWavelength></Channel>
</Channels></Dimensions></Image></Information></Metadata></ImageDocument>"""
body = XML.encode()
head = bytearray(czi_meta.HEADER_LEN)
head[:10] = b"ZISRAWFILE"
struct.pack_into("<QQ", head, 84, 0, czi_meta.HEADER_LEN)
seg = bytearray(czi_meta.SEGMENT_HEADER_LEN)
seg[:14] = b"ZISRAWMETADATA"
czi_path = os.path.join(tempfile.mkdtemp(), "scan.czi")
with open(czi_path, "wb") as fh:
    fh.write(bytes(head) + bytes(seg) + struct.pack("<ii", len(body), 0).ljust(czi_meta.METADATA_DATA_LEN, b"\0") + body)

QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (czi_path, ""))
fresh = CEd.ChannelEditor({"acquisition": {"layout": "multiplex", "channels": []}}, "multiplex")
chk("an empty table to start", fresh.table.rowCount(), 0)
fresh._detect_from_czi()
chk("detection fills one row per channel", fresh.table.rowCount(), 3)
chk("...with the file's channel names", [fresh._w(i, "CZI channel name").text() for i in range(3)], ["DAPI", "AF568", "AF488"])
chk("...the counterstain found", [fresh._w(i, "Role").currentText() for i in range(3)], ["nuclear", "marker", "marker"])
chk("...the colours from the bands", [fresh._w(i, "Colour").currentText() for i in (1, 2)], ["red", "green"])
chk("...the detection shown beside each", fresh._w(1, "Detected").text(), "AF568 | em 603 nm | red band")
chk("...each name box offers its own band first",
    [fresh._w(i, "Name").itemText(1) for i in range(3)],
    ["-- typical for blue --", "-- typical for red --", "-- typical for green --"])
chk("...and the pipeline accepts the result", "accepts" in fresh.status.text(), True)
fresh._w(1, "Name").setText("cFos")                       # the person picks the marker
fresh._detect_from_czi()
chk("detecting again keeps what was chosen",
    ([fresh._w(i, "Name").text() for i in range(3)], fresh.table.rowCount()),
    (["DAPI", "cFos", "AF488"], 3))
fresh._ok()
chk("the chosen marker is what is written",
    [c["name"] for c in fresh.result_edits["acquisition.channels"]], ["DAPI", "cFos", "AF488"])

print()
print("FAILED: " + ", ".join(failures) if failures else "ALL PASS")
sys.exit(1 if failures else 0)
