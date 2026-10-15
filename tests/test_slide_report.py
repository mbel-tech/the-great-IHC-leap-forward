"""The "What the slides say…" window and its button in Set up.

Headers are written by hand as in test_autodetect.py.

    python tests/test_slide_report.py
"""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(REPO, "app"), os.path.join(REPO, "scripts"), HERE]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from _fixture import write_study                                # noqa: E402

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
A, xml, write_czi = ns["A"], ns["xml"], ns["write_czi"]
DAPI, AF568, AF488 = ns["DAPI"], ns["AF568"], ns["AF488"]

TMP = tempfile.mkdtemp()
SLIDES = os.path.join(TMP, "slides")
os.makedirs(SLIDES)
for name, chans, exp in (("LS22_1a.czi", [DAPI, AF568], {"DAPI": 20, "AF568": 120}),
                         ("LS22_1b.czi", [DAPI, AF488], {"DAPI": 20, "AF488": 80}),
                         ("LS45_8a.czi", [DAPI, AF568], {"DAPI": 20, "AF568": 300})):
    write_czi(os.path.join(SLIDES, name),
              xml(chans, exposure_ms=exp, objective=(20, 0.8, "Air"), bits=14, camera="Axiocam"))
found = A.detect(SLIDES)
report = dict(found.report)
report["notes"], report["problems"] = list(found.notes), list(found.problems)

import slide_report as SR                                       # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton         # noqa: E402

app = QApplication.instance() or QApplication([])

print("--- the window ---")
full = os.path.join(TMP, "slide_metadata.json")
dlg = SR.SlideReportDialog(report, full)
t = dlg.tables
chk("a table per section",
    sorted(t), ["Channels", "Differs between files", "Instrument", "Optics", "Scenes",
                "What each value did"])
chk("one instrument row per field", t["Instrument"].rowCount(), len(report["instrument"]))
chk("one channel row per plane and channel", t["Channels"].rowCount(), 3)
chk("optics: its values and a row per channel",
    t["Optics"].rowCount(), (len(report["optics"]) - 1) + 3)
chk("a row for every use", t["What each value did"].rowCount(), len(report["uses"]))
chk("a row for every field that differs", t["Differs between files"].rowCount(),
    len(report["varying"]))
chk("the exposure that differs is listed",
    any("Exposure" in t["Differs between files"].item(r, 0).text()
        for r in range(t["Differs between files"].rowCount())), True)
chk("the problem is shown", "exposed for 120" in dlg.look.text(), True)
chk("...and the notes", "coarser than half" in dlg.look.text(), True)
chk("the full file is named", full in dlg.full_line.text(), True)
buttons = [b.text() for b in dlg.findChildren(QPushButton)]
chk("only a Close button", len(buttons), 1)
empty = SR.SlideReportDialog({})
chk("a report with nothing in it still builds", empty.tables, {})
odd = SR.SlideReportDialog({"extra": 1, "instrument": {"x": {"value": "1"}}})
chk("...and with a section it does not know", odd.tables["Instrument"].rowCount(), 1)

print("--- the button in Set up ---")
PATH = write_study(os.path.join(TMP, "s"))
os.environ["LS_CONFIG"] = PATH
os.environ["LS_HOME"] = os.path.join(TMP, "home")
try:
    import main_window as MW                                    # noqa: E402
except ImportError as exc:
    print("SKIP the window (%s)" % exc)
    MW = None

if MW:
    import json
    cfg = json.load(open(PATH))
    cfg["source_dir"] = os.path.join(TMP, "nowhere")
    json.dump(cfg, open(PATH, "w"))
    w = MW.MainWindow(REPO, config_path=PATH)
    s = w.setup
    chk("Set up has the button", s.report_btn.text(), "What the slides say…")
    chk("disabled with no report and no slides folder", s.report_btn.isEnabled(), False)
    project = s.session.project
    project.set("source_dir", SLIDES, by="test")
    s.build()
    chk("enabled once there is a readable slides folder", s.report_btn.isEnabled(), True)
    got, path = s._slide_report()
    chk("with no kept report it reads the folder", bool(got and got.get("instrument")), True)
    chk("...and no full file yet", path, None)
    s.detect_from_slides(quiet=True)
    got, path = s._slide_report()
    chk("after reading, the study keeps a summary", "full_report" in got, True)
    chk("...and the full file is named by its absolute path",
        path, os.path.join(cfg["out_root"], "slide_metadata.json"))
    project.set("source_dir", os.path.join(TMP, "gone"), by="test")
    s.build()
    chk("a kept report keeps the button on even if the folder is gone",
        s.report_btn.isEnabled(), True)

print("\n%s" % ("FAILED: %s" % failures if failures else "all passed"))
sys.exit(1 if failures else 0)
