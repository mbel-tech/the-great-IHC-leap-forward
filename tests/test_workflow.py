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
chk("every screen says what your part in it is",
    [s.sid for s in W.STEPS + W.EXTRA if not s.role.strip()], [])
chk("...and every step has its one line in the step list",
    [s.sid for s in W.STEPS if not s.subtitle.strip()], [])
chk("Curate's part names the Done button that takes you on",
    "Done: export and go to Measure" in W.CURATE.role, True)
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
chk("the picture settings share one Pictures card",
    [k.path for sid, _t, _i, keys in cards if sid == "pictures" for k in keys],
    ["curation_render.source", "curation_render.um_per_px",
     "scene_render.out_dir", "scene_render.um_per_px", "scene_render.range"])
chk("...under a heading for each kind of picture",
    [k for k in W.SECTION_HEADINGS if k not in shown], [])
chk("a card whose settings are not in ls_config yet is hidden",
    [c[0] for c in W.setting_sections([_K("study.name")])], ["folders"])

print("--- limits the stages enforce are enforced in Set up ---")
_sr = next(k for k in LC.SPEC if k.path == "scene_render.um_per_px")
chk("0.65 um/px scene pictures are allowed", LC._check(_sr, 0.65), None)
chk("finer than the scan's own pixels is refused",
    "at least 0.65" in (LC._check(_sr, 0.3) or ""), True)
_cr = next(k for k in LC.SPEC if k.path == "curation_render.um_per_px")
chk("curation pictures finer than 04o draws are refused",
    "at least 1.3" in (LC._check(_cr, 0.65) or ""), True)

print("--- a run that leaves some items undone says so ---")
_out = "wrote 40 scenes at 0.65 um/px\n  3 not drawn:\n    LS53_2b_s1  needs about 9.1 GB\n"
chk("skipped scenes are counted", W.shortfall("scene_renders", _out), "3 scenes not drawn")
chk("one skipped scene", W.shortfall("scene_renders", "  1 not drawn:\n"), "1 scene not drawn")
chk("a full run has no shortfall", W.shortfall("scene_renders", "wrote 40 scenes\n"), None)
chk("other stages are not read for it", W.shortfall("manifest", _out), None)
chk("colour pictures not built are counted",
    W.shortfall("rgb_perk", "wrote 9 composites\n  2 not built:\n"), "2 pictures not built")
chk("the artifact masks always include excluded sections (no option to break it)",
    [o.flag for o in W.options_for("artifact_pcna")], ["--limit"])
chk("the scene pictures folder follows Set up",
    W.output_folder("scene_renders", {"out_root": "/s", "scene_render": {"out_dir": "/p"}}),
    ("Open pictures", "/p"))
chk("...and defaults inside the study",
    W.output_folder("scene_renders", {"out_root": "/s"})[1], os.path.join("/s", "scene_renders"))

print("--- older results are flagged for measuring again ---")
_res = os.path.join(CFG["out_root"], "results")
os.makedirs(_res, exist_ok=True)
_nuc = os.path.join(_res, "roi_nuclei.csv")
open(_nuc, "w").write("uid\n")
chk("fresh nuclei need nothing", W.stale_results(study()), None)
os.utime(_nuc, (W.ROI_FIX_AT - 3600, W.ROI_FIX_AT - 3600))
chk("nuclei measured before the ROI fixes are flagged on Measure",
    "Redo sections already done" in " ".join(W.notices("measure", study())), True)
chk("...and on Results", bool(W.notices("results", study())), True)
chk("...but not on Prepare", W.notices("prepare", study()), [])
os.remove(_nuc)

print("--- outputs and inputs the screens offer ---")
chk("overviews open their folder", W.output_folder("overviews", {"out_root": "/s"}),
    ("Open pictures", os.path.join("/s", "overviews")))
chk("results open their folder", W.output_folder("build_results", {"out_root": "/s"})[0],
    "Open results")
chk("the sampling workbook can be chosen",
    W.build_argv(S.BY_ID["join_sampling"], {"--xlsx": "C:/data/sampling.xlsx"})[-2:],
    ["--xlsx", "C:/data/sampling.xlsx"])
chk("...and left empty runs the stage's default",
    W.clean_options("join_sampling", {"--xlsx": ""}), {})

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
chk("no rotation step holds Prepare: the final images are planned",
    ("reformat_pcna_final" in plan, "reformat_pcna_final" in held), (True, False))
chk("...and nothing waits on a curator that is gone",
    "rotation_curator" in set(held.values()) | set(plan), False)
chk("the ROI curator page itself is built",
    "roi_curator" in plan or held.get("roi_curator") == "atlas_reframe", True)
chk("stages for another atlas are left out",
    "atlas_wullimann_plates" in plan, s0.atlas_source == "wullimann1996")
chk("stages run outside the app are left out", "fiji_export" in W.run_plan("instrument", s0)[0], False)
chk("the superseded level curator is never planned",
    "level_curator" in plan + W.run_plan("all", s0)[0], False)
os.makedirs(os.path.join(OUT, "reformatted"), exist_ok=True)
measure, mheld = W.run_plan("measure", s0)
chk("Measure waits on the ROI curator's export",
    mheld.get("roi_geometry"), "roi_curator")
chk("the artifact masks run first, with the first images",
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
chk("Run all remaining never unblinds",
    [s for s in W.run_plan("results", s0)[0] + W.run_plan("all", s0)[0]
     if S.BY_ID[s].unblinds], [])
chk("Curate's progress counts curators exported",
    W.progress_words("curate", *W.step_progress("curate", s0)).endswith("exported")
    or W.progress_words("curate", *W.step_progress("curate", s0)) == "Done", True)
chk("progress words", (W.progress_words("setup", 5, 6), W.progress_words("prepare", 10, 19),
                       W.progress_words("curate", 0, 1), W.progress_words("measure", 3, 3)),
    ("5 of 6 cards", "10 of 19 stages", "0 of 1 exported", "Done"))
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
    import theme
    theme.apply(app)
    unblind_answer = []           # what the next Unblind dialog does: (accept?, workbook)

    def _unblind_exec(dlg):
        asked.append(dlg.windowTitle())
        chk("the unblind dialog starts on Cancel", dlg.cancel_btn.isDefault(), True)
        accept, book = unblind_answer.pop(0) if unblind_answer else (False, None)
        if book is not None:
            dlg.field.setValue(book)
        return MW.QDialog.Accepted if accept else MW.QDialog.Rejected
    MW.UnblindDialog.exec = _unblind_exec
    w = MW.MainWindow(REPO, config_path=PATH)
    chk("the step bar has the six steps", w.nav.count(), 6)
    import widgets as WG
    chk("each step carries its line and its progress",
        (w.nav.item(1).data(WG.StepDelegate.ROLE_SUB),
         bool(re.match(r"\d+ of \d+ cards|Done", w.nav.item(1).data(WG.StepDelegate.ROLE_PROGRESS)))),
        ("Tell the app about your slides", True))
    chk("the step list stays between 236 and 280 px",
        (w.nav.parentWidget().minimumWidth(), w.nav.parentWidget().maximumWidth()), (236, 280))
    chk("every screen shows its Your part",
        all(p.findChild(WG.ScreenHeader).role.text() for p in w.pages.values()), True)
    for sid in ("home", "setup", "prepare", "curate", "measure", "results", "instrument", "all"):
        w.goto(sid)
        chk(f"goto {sid} shows it", w.stack.currentWidget() is w.pages[sid])
    chk("Advanced marks the screen it is on", w.advanced["all"].isChecked(), True)
    w.advanced["instrument"].click()
    chk("...and its buttons go there",
        (w.stack.currentWidget() is w.pages["instrument"], w.advanced["all"].isChecked()),
        (True, False))
    w.goto("curate")
    w.curate.show_curator("roi_curator")
    chk("the ROI curator offers Done above its page",
        (w.curator.done_btn.text(), w.curator.done_btn.isVisibleTo(w.curator)),
        ("Done: export and go to Measure", True))
    w.curator.run_export = lambda then: then(False)
    w.curator.done_btn.click()
    chk("Done with no page loaded says so and stays",
        ("not loaded" in w.curate.decision.text(), w.stack.currentWidget() is w.curate),
        (True, True))
    w.curator.run_export = lambda then: then(True)
    w.curator.window_btn.setChecked(True)
    w.curator.done_btn.click()
    w.curator.exported.emit(os.path.join(TMP, "roi_plates_14.10.2026_15.20.csv"))
    chk("...waits for the regions file, not the first of the three",
        w.stack.currentWidget() is w.curate, True)
    w.curator.exported.emit(os.path.join(TMP, "roi_regions_14.10.2026_15.20.csv"))
    chk("...then goes to Measure, with the page back in the app",
        (w.stack.currentWidget() is w.pages["measure"], w.curator.window_btn.isChecked(),
         w.curator.parentWidget() is w.curate), (True, False, True))
    w.curator.exported.emit(os.path.join(TMP, "roi_regions_14.10.2026_15.21.csv"))
    w.goto("curate")
    chk("a plain Export afterwards does not move the screen",
        w.stack.currentWidget() is w.curate, True)
    w.curate.show_curator("atlas_reframe")
    chk("other curators have no Done", w.curator.done_btn.isVisibleTo(w.curator), False)
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
    _js = w.lists["results"].rows["join_sampling"]
    chk("the unblinding row says Run... since it asks first", _js.run_btn.text(), "Run…")
    _ran = []
    _real_run_next = w._run_next
    w._run_next = lambda: _ran.append(list(w._queue))
    unblind_answer.append((True, "C:/data/sampling.xlsx"))
    started = w.run_stages(["join_sampling"])
    chk("Unblind and run keeps the workbook as the stage's option",
        (started, w.session.project.stage_options("join_sampling")),
        (True, {"--xlsx": "C:/data/sampling.xlsx"}))
    w._run_next = _real_run_next
    w._queue, w._running = [], {}
    w.drawer.stop_btn.setVisible(False)
    w.refresh()
    _geo = w.lists["measure"].rows["roi_geometry"]
    chk("a stage held by the ROI export says so and offers the curator",
        (_geo.status.text(), _geo.run_btn.text(), _geo.run_btn.isEnabled()),
        ("Waiting for your ROI export", "Open curator", True))
    _geo.run_btn.click()
    chk("...which goes to Curate", w.stack.currentWidget() is w.curate, True)
    open(_nuc, "w").write("uid\n")
    os.utime(_nuc, (W.ROI_FIX_AT - 3600, W.ROI_FIX_AT - 3600))
    w.refresh()
    chk("Measure shows the redo notice for old nuclei",
        (w.lists["measure"].notice.isVisibleTo(w.lists["measure"]),
         "Place the ROIs" in w.lists["measure"].notice.text()), (True, True))
    w.lists["measure"].dismiss_btn.click()
    w.refresh()
    chk("Dismiss puts the notice away", w.lists["measure"].notice.isVisibleTo(w.lists["measure"]),
        False)
    os.remove(_nuc)
    w.refresh()
    chk("...and drops it once they are measured again",
        w.lists["measure"].notice.isVisibleTo(w.lists["measure"]), False)
    import config_dialog as CD
    _cfg = dict(CFG, acquisition={"layout": "paired", "markers": ["AF568", "AF488"]})
    _p = os.path.join(TMP, "paired.json")
    json.dump(_cfg, open(_p, "w"))
    form = CD.SettingsForm(REPO, path=_p, keys=["acquisition.layout"])
    chk("Set up offers where each marker's nuclei are found",
        list(form.seg_row.combos), ["AF568", "AF488"])
    chk("...on the counterstain unless chosen", form._segmentation_value(), None)
    chk("...with nothing withheld", form.seg_row.notes["AF488"].isVisibleTo(form), False)
    form.seg_row.combos["AF488"].setCurrentText(CD.OWN_ROUTE)
    chk("PCNA on its own channel writes the block 05c reads",
        form._segmentation_value(), {"AF488": CD.OWN_ENTRY})
    chk("...and says what that withholds",
        (form.seg_row.notes["AF488"].isVisibleTo(form), form.seg_row.notes["AF488"].text()),
        (True, "Total cell density is withheld for this marker."))
    _out = json.loads(json.dumps(_cfg))
    LC._set(_out, ["acquisition", "marker_segmentation"], form._segmentation_value())
    chk("...which validates", [e for e in LC.validate(_out, _p)[0] if "segment" in e], [])
    form.widgets["acquisition.layout"].setCurrentText("multiplex")
    chk("a multiplex study hides it and writes nothing",
        (form._segmentation_value(), form.seg_row.isVisibleTo(form)), (None, False))
    chk("the scene pictures row keeps Open pictures until there are pictures",
        (w.lists["prepare"].rows["scene_renders"].open_btn.text(),
         w.lists["prepare"].rows["scene_renders"].open_btn.isVisibleTo(w.lists["prepare"])),
        ("Open pictures", False))
    w.open_curator("scene_renders")
    chk("opening it before any pictures exist says so", "Nothing to open yet" in asked, True)
    import runner as RN
    from PySide6.QtCore import QCoreApplication
    import time as _t
    w.runner.run = lambda stage, on_line=None, extra_argv=(): RN.StageResult(
        stage.sid, True, 0.1, "wrote 9 scenes\n  2 not drawn:\n    s1  needs about 9 GB\n")
    w.run_stages(["scene_renders"])
    _end = _t.time() + 10
    while (w._thread is not None or w._queue) and _t.time() < _end:
        QCoreApplication.processEvents()
        _t.sleep(0.02)
    chk("a finished run that skipped scenes says so in the drawer",
        "2 scenes not drawn" in w.drawer.state.text(), True)
    w.server.stop()

print()
print("FAILED: " + ", ".join(failures) if failures else "ALL PASS")
sys.exit(1 if failures else 0)
