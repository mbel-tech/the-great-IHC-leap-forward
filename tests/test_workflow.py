"""The operator's layout of the pipeline (app/workflow.py) and the window built on it.

  1. Every stage has a home and plain words, and every option is a real flag.
  2. An untouched option runs a stage exactly as the stage table writes it.
  3. Run all remaining runs what is missing, in order, and waits for curators.
  4. Options chosen on screen are kept in the project file and reach the run.
  5. The window: every screen builds, Run all asks before unblinding.

    python tests/test_workflow.py
"""

import json
import os
import re
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


TMP = tempfile.mkdtemp()
PATH = write_study(os.path.join(TMP, "s"))
os.environ["LS_CONFIG"] = PATH

import ls_config as LC                                          # noqa: E402
import ls_project as LP                                         # noqa: E402
import ls_paths                                                 # noqa: E402
import stages as S                                              # noqa: E402
import workflow as W                                            # noqa: E402

CFG = json.load(open(PATH))
OUT = CFG["out_root"]


def study(cfg=None):
    cfg = cfg or CFG
    markers = cfg.get("acquisition", {}).get("markers") or ["AF568", "AF488"]
    names = ls_paths.Names(cfg["out_root"], markers, cfg.get("acquisition", {}).get("layout"))
    return W.Study(cfg["out_root"], names, cfg)


print("--- every stage has a home and plain words ---")
homes = {}
for scr in W.STEPS + W.EXTRA:
    for sid in [i for _h, _e, ids in scr.parts for i in ids]:
        chk(f"{scr.sid}: {sid} is a stage, or one the other table had",
            sid in S.BY_ID or sid in W.OTHER_TABLE)
for st in S.STAGES:
    where = W.screen_of(st.sid)
    chk(f"{st.sid} is on a screen or hidden on purpose", where is not None or st.sid in W.HIDDEN)
    chk(f"{st.sid} has plain words", bool(W.PLAIN.get(st.sid, "").strip()))
on_steps = [sid for scr in W.STEPS + [W.INSTRUMENT] for sid in scr.stage_ids()]
chk("no stage is on two screens", sorted(x for x in set(on_steps) if on_steps.count(x) > 1), [])
chk("All stages lists every stage", sorted(W.ALL.stage_ids()), sorted(st.sid for st in S.STAGES))
chk("the step bar is in working order",
    [s.sid for s in W.STEPS], ["home", "setup", "prepare", "curate", "measure", "results"])
chk("saturation stages stay in the daily path, since 04j reads them",
    (W.screen_of("saturation_raw"), W.screen_of("saturation_map")), ("prepare", "prepare"))

print("--- every option is a real flag of its script ---")
for sid, opts in W.OPTIONS.items():
    st = S.BY_ID.get(sid)
    chk(f"options for {sid}: a stage, or one the other table had",
        st is not None or sid in W.OTHER_TABLE)
    if not st or not st.script.endswith(".py"):
        continue
    src = open(os.path.join(REPO, "scripts", st.script), encoding="utf-8").read()
    flags = set(re.findall(r'add_argument\(\s*["\'](--[\w-]+)', src))
    for o in opts:
        chk(f"{sid} {o.flag}: the script takes it", o.flag in flags)
        chk(f"{sid} {o.flag}: has a label and help", bool(o.label and o.help))
        if o.kind == "bool":
            chk(f"{sid} {o.flag}: a flag the table sets defaults on",
                o.default, o.flag in st.argv)

print("--- an untouched option changes nothing ---")
for st in S.STAGES:
    chk(f"{st.sid}: argv unchanged with no options", W.build_argv(st, {}), list(st.argv))
roi = S.BY_ID["roi_curator"]
chk("switching off a flag the table sets removes it",
    W.build_argv(roi, {"--rgb": False}), ["--marker", "{marker0}", "--worklist"])
chk("switching one on adds it",
    W.build_argv(roi, {"--analysis-set": True})[-1], "--analysis-set")
det = S.BY_ID["detect"]
chk("a choice away from the script's default is passed",
    W.build_argv(det, {"--order": "uid"}), ["--order", "uid"])
chk("a choice at the script's default is not",
    W.build_argv(det, {"--order": "balanced"}), [])
pick = S.BY_ID["pick_sections"]
chk("a value the table passes stays, changed",
    W.build_argv(pick, {"--n": 120}), ["--n", "120", "--scenes-per-file", "4"])
chk("with_options leaves the table alone",
    (W.with_options(roi, {"--rgb": False}).argv, roi.argv[-1]),
    (["--marker", "{marker0}", "--worklist"], "--rgb"))
chk("only what differs from the default is kept",
    W.clean_options("detect", {"--order": "balanced", "--qc": True, "--nope": 1}), {"--qc": True})
try:
    W.clean_options("detect", {"--order": "random"})
    chk("an impossible choice is refused", False)
except ValueError:
    chk("an impossible choice is refused", True)

print("--- settings are sorted into Set up's cards ---")
fields = LC.dialog_fields()
cards = W.setting_sections(fields)
shown = [k.path for _sid, _t, _i, keys in cards for k in keys]
chk("every setting the dialog offers is on a card", sorted(shown), sorted(k.path for k in fields))
chk("nothing is left over today", [c[0] for c in cards if c[0] == "other"], [])


class _K:
    def __init__(self, path):
        self.path = path


chk("a new setting lands in Other settings",
    W.setting_sections([_K("brand.new")])[-1][0], "other")
chk("a card whose settings are not in ls_config yet is hidden",
    [c[0] for c in W.setting_sections([_K("study.name")])], ["folders"])

print("--- names and words for this study ---")
s0 = study()
chk("titles lose their script number", W.display_title(S.BY_ID["manifest"]), "Read slide headers")
chk("titles use the study's markers",
    W.display_title(S.BY_ID["reformat_pcna"], s0), "Reformat %s sections" % s0.marker_label(1))
named = dict(CFG, marker_identity={s0.markers[0]: "pERK"})
chk("a confirmed antibody name is preferred", study(named).marker_label(0), "pERK")
chk("plain words fill the markers in", "{m" in W.plain(S.BY_ID["pair"], s0), False)

print("--- Run all remaining ---")
plan, held = W.run_plan("prepare", s0)
chk("a fresh study starts with the slide headers", plan[:1], ["manifest"])
chk("the plan keeps the table's order",
    plan, [st.sid for st in S.STAGES if st.sid in plan])
chk("nothing after the rotation curator runs before you export",
    "reformat_pcna_final" in plan, False)
chk("...it is held, and says what for", held.get("reformat_pcna_final"), "rotation_curator")
chk("the curator page itself is built", "rotation_curator" in plan, True)
chk("stages for another atlas are left out",
    "atlas_wullimann_plates" in plan, s0.atlas_source == "wullimann1996")
chk("stages run outside the app are left out", "fiji_export" in W.run_plan("instrument", s0)[0], False)
chk("the superseded level curator is never planned",
    "level_curator" in plan + W.run_plan("all", s0)[0], False)
os.makedirs(os.path.join(OUT, "reformatted"), exist_ok=True)
chk("no export, not decided", W.decided("rotation_curator", s0), False)
open(os.path.join(OUT, "reformatted", "rotation_overrides.csv"), "w").write("uid\n")
chk("an export decides it", W.decided("rotation_curator", s0), True)
plan2, held2 = W.run_plan("prepare", s0)
chk("...and the stages behind it are planned",
    ("reformat_pcna_final" in plan2, "reformat_pcna_final" in held2), (True, False))
chk("the artifact masks run before the rotation curator",
    [m for m in ("artifact_pcna", "artifact_perk") if m in W.PREPARE.parts[2][2]],
    ["artifact_pcna", "artifact_perk"])
chk("repairs for older files are never part of Run all remaining",
    [s for s in W.run_plan("measure", s0)[0] if s in W.REPAIRS], [])
chk("...nor are pictures only for looking at",
    [s for s in W.run_plan("prepare", s0)[0] if s in W.VIEW_ONLY], [])
os.makedirs(os.path.join(s0.export_root, "01.02.2026_10.00"), exist_ok=True)
chk("an ROI export is found in a stamped folder too", W.decided("roi_curator", s0), False)
open(os.path.join(s0.export_root, "01.02.2026_10.00", "roi_regions_01.02.2026_10.00.csv"), "w").write("x\n")
chk("...once it is there", W.decided("roi_curator", s0), True)
where, words, _sid = W.next_up(s0)
chk("Next up on a fresh study is Prepare", (where, words.startswith("Run Prepare")), ("prepare", True))
chk("Next up sends an unfinished Set up back to it", W.next_up(s0, setup_ok=False)[0], "setup")
chk("...and names the settings it is missing",
    W.next_up(s0, setup_ok=False, setup_missing=[("Atlas", ["Atlas PDF"])])[1],
    "Finish Set up: Atlas needs Atlas PDF.")
d, t = W.progress("prepare", s0)
chk("progress counts only stages the app runs here", (d <= t, t > 10), (True, True))
chk("a run's duration in words",
    W.duration_words({"started": "2026-01-01T10:00:00+00:00", "ended": "2026-01-01T10:03:10+00:00"}),
    "took 3 min")

print("--- options live in the project file ---")
pj = LP.Project.open(PATH)
chk("untouched, a stage has no options", pj.stage_options("detect"), {})
pj.set_stage_options("detect", {"--order": "uid"})
chk("chosen options make the project unsaved", pj.dirty, True)
pj.save()
again = LP.Project.open(PATH)
chk("...and are saved with it", again.stage_options("detect"), {"--order": "uid"})
chk("the saved file still validates", LC.validate(json.load(open(PATH)), PATH)[0], [])
again.set_stage_options("detect", {})
chk("an empty set forgets them, leaving no empty block",
    ("stage_options" in again.block), False)
rec = again.record_run("detect", True, "a", "b", options={"--order": "uid"})
chk("a run records the options it ran with", again.block["runs"][-1].get("options"), {"--order": "uid"})

print("--- the window ---")
try:
    from PySide6.QtWidgets import QApplication, QMessageBox
    import main_window as MW
except ImportError as exc:                                      # pragma: no cover
    print("SKIP the window (%s)" % exc)
    MW = None

if MW:
    app = QApplication.instance() or QApplication([])
    asked = []
    QMessageBox.warning = staticmethod(lambda *a, **k: (asked.append(a[1]), QMessageBox.Cancel)[1])
    QMessageBox.information = staticmethod(lambda *a, **k: asked.append(a[1]))
    w = MW.MainWindow(REPO, config_path=PATH)
    chk("the step bar has the six steps", w.nav.count(), 6)
    for sid in ("home", "setup", "prepare", "curate", "measure", "results", "instrument", "all"):
        w.goto(sid)
        chk(f"goto {sid} shows it", w.stack.currentWidget() is w.pages[sid])
    chk("Set up has a card for slides and one per settings group",
        w.setup.tabs.count(), 1 + len(W.setting_sections(LC.dialog_fields())))
    chk("Set up says where this study's slides come from",
        CFG["source_dir"] in w.setup.current.text(), True)
    chk("...and lists what each incomplete card is missing",
        all(gaps for _t, gaps in w.setup.missing()), True)
    row = w.lists["measure"].rows["detect"]
    chk("a stage with options offers them", row.opt_btn.isVisibleTo(w.lists["measure"]), True)
    chk("its (i) explains it in plain words first",
        row.info.body().startswith("<p>" + W.plain(S.BY_ID["detect"], w.study())[:20]), True)
    chk("the window opens with the options the file holds",
        row.options.widgets["--order"].currentText(), "uid")
    row.options.widgets["--qc"].setChecked(True)
    chk("changing an option on screen keeps it in the project",
        w.session.project.stage_options("detect"), {"--order": "uid", "--qc": True})
    chk("...and marks the project unsaved", w.session.dirty, True)
    row.options.reset()
    chk("Reset to defaults forgets it", w.session.project.stage_options("detect"), {})
    started = w.run_stages(["join_sampling"])
    chk("running the unblinding stage asks first, and Cancel stops it",
        (started, "Unblind the analysis?" in asked), (False, True))
    chk("nothing is left queued", (w._queue, w._thread), ([], None))
    w.server.stop()

print()
print("FAILED: " + ", ".join(failures) if failures else "ALL PASS")
sys.exit(1 if failures else 0)
