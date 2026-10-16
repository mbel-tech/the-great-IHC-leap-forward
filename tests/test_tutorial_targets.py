"""The tutorial's step list points at things that exist.

Every step names a screen and, usually, a control for the pointer bubble. A
screen that is rebuilt or a button that is renamed would leave the tutorial
pointing at nothing, so this builds the real window and looks each one up.

    python tests/test_tutorial_targets.py
"""

import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(REPO, "app"), os.path.join(REPO, "scripts"), HERE]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from _fixture import use_temp_study                            # noqa: E402

STUDY = use_temp_study()
os.environ["LS_HOME"] = tempfile.mkdtemp()

failures = []


def chk(name, got, want=True):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name:<70} {got!r}" + ("" if ok else f"  (wanted {want!r})"))
    if not ok:
        failures.append(name)


import stages as S                                              # noqa: E402
import tutorial_steps as TS                                     # noqa: E402

print("--- the list itself ---")
chk("step ids unique", len({s.sid for s in TS.STEPS}), len(TS.STEPS))
chk("first step is on Project", TS.STEPS[0].screen, "home")
chk("last step is the finish", TS.STEPS[-1].screen, "finish")
order = [TS.SCREENS.index(s.screen) for s in TS.STEPS if s.screen in TS.SCREENS]
chk("every screen is a known one", len(order), len(TS.STEPS))
chk("screens come in the app's order, never going back", order, sorted(order))
for s in TS.STEPS:
    kind, arg = TS.parse_done(s.done)
    if kind not in TS.DONE_KINDS:
        chk(f"{s.sid}: done kind known", kind, "one of DONE_KINDS")
    if kind == "stage" and arg not in S.BY_ID:
        chk(f"{s.sid}: stage {arg} exists", False)
    if s.slow:
        chk(f"{s.sid}: a slow step waits on a slow stage", arg in TS.SLOW)
        chk(f"{s.sid}: ...and carries ready-made results", bool(s.ready))
    elif kind == "stage" and arg in TS.SLOW:
        chk(f"{s.sid}: a slow stage's step is marked slow", False)
    for sid in s.ready:
        if sid not in S.BY_ID:
            chk(f"{s.sid}: ready stage {sid} exists", False)
chk("parse_done splits the argument", TS.parse_done("stage:detect"), ("stage", "detect"))
chk("parse_done of next", TS.parse_done("next"), ("next", ""))
chk("by_sid", TS.by_sid("welcome").screen, "home")

print("--- the window has every target ---")
from PySide6.QtWidgets import QApplication, QWidget             # noqa: E402
app = QApplication.instance() or QApplication([])
import main_window as MW                                        # noqa: E402
w = MW.MainWindow(REPO, config_path=STUDY.path)
for s in TS.STEPS:
    if s.target and not s.target.startswith("curator:"):
        chk(f"{s.sid}: {s.target} exists", w.findChild(QWidget, s.target) is not None)

print("--- the curator page has every curator target ---")
src = open(os.path.join(REPO, "scripts", "04l_roi_curator.py"), encoding="utf-8").read()
for s in TS.STEPS:
    if s.target.startswith("curator:"):
        m = re.fullmatch(r"curator:#([A-Za-z0-9_-]+)", s.target)
        chk(f"{s.sid}: {s.target} is an id selector", m is not None)
        if m:
            chk(f"{s.sid}: id {m.group(1)} is on the page", f'id="{m.group(1)}"' in src)

w.close()
print(f"\n{len(failures)} failure(s)")
sys.exit(1 if failures else 0)
