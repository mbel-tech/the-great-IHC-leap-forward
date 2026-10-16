"""Mirroring the atlas outlines is findable, and follows the atlas chosen.

Set up's basic view shows "Mirror outlines to both sides" right under the atlas
choice, rather than only under Show advanced settings. Choosing a one-sided
atlas (Wullimann) in the list ticks it; choosing one that outlines both sides
(salmon) clears it and greys it out. Opening the form changes nothing.

Runs offscreen; skipped when PySide6 is not installed.

Run:  python tests/test_atlas_mirror_setting.py
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "app"))
sys.path.insert(0, os.path.join(REPO, "scripts"))

try:
    from PySide6.QtWidgets import QApplication
except ImportError:
    print("SKIP PySide6 is not installed - the stages do not need it")
    sys.exit(0)

from _fixture import use_temp_study                         # noqa: E402

STUDY = use_temp_study(atlas_source="wullimann1996", atlas_polygon_mirror=False)

import config_dialog as CD                                  # noqa: E402
import ls_config as LC                                      # noqa: E402
import workflow as W                                        # noqa: E402

app = QApplication([])
failures = []


def chk(label, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + label.ljust(68) + " " + repr(got))
    if not ok:
        print("     want " + repr(want))
        failures.append(label)


basic = {k.path for _sid, _t, _i, keys in W.setting_sections(LC.dialog_fields(), advanced=False)
         for k in keys}
chk("the basic view offers the mirror setting", "atlas_polygon_mirror" in basic, True)
cards = [(sid, [k.path for k in keys]) for sid, _t, _i, keys
         in W.setting_sections(LC.dialog_fields(), advanced=False)]
atlas = dict(cards).get("atlas", [])
chk("...on the Atlas card, after the atlas choice",
    atlas.index("atlas_polygon_mirror") > atlas.index("atlas_source"), True)

form = CD.SettingsForm(REPO, path=STUDY.path, keys=["atlas_source", "atlas_polygon_mirror"],
                       advanced=False)
box = form.widgets["atlas_polygon_mirror"]
chk("opening the form keeps the study's value", box.isChecked(), False)
chk("...and it can be ticked for Wullimann", box.isEnabled(), True)

form.atlas_combo.setCurrentText("salmon")
chk("salmon clears it", box.isChecked(), False)
chk("...and greys it out", box.isEnabled(), False)

form.atlas_combo.setCurrentText("wullimann1996")
chk("choosing Wullimann ticks it", box.isChecked(), True)
chk("...and it can be changed", box.isEnabled(), True)

values = {path: get() for path, (_k, get) in form.rows.items()}
chk("what the form hands back", values["atlas_polygon_mirror"], True)

print()
print("FAILED: " + ", ".join(failures) if failures else "all passed")
sys.exit(1 if failures else 0)
