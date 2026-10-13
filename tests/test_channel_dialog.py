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

# ---- paired layout shows names and colours only ------------------------------
pe = CEd.ChannelEditor({"acquisition": {"layout": "paired", "markers": ["AF568", "AF488"]}}, "paired")
chk("paired has three columns", pe.table.columnCount(), 3)
chk("paired rows are the markers", [pe._w(i, "Name").text() for i in range(2)], ["AF568", "AF488"])

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

print()
print("FAILED: " + ", ".join(failures) if failures else "ALL PASS")
sys.exit(1 if failures else 0)
