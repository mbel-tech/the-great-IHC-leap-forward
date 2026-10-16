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
# Set up remembers "Show advanced settings" in the app's settings file.
os.environ["LS_HOME"] = os.path.join(TMP, "home")

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
     "scene_render.out_dir", "scene_render.um_per_px", "scene_render.source",
     "scene_render.range"])
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

print("--- a finished stage goes out of date when what it was made from changes ---")
_res = os.path.join(CFG["out_root"], "results")
_nuc = os.path.join(_res, "roi_nuclei.csv")
_T0 = W.ROI_FIX_AT + 86400                      # after the 14 Oct fixes, unless a check says
_stale_files = []


def _put(path, text, when):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)
    os.utime(path, (when, when))
    _stale_files.append(path)


def _outputs_of(sid, s):
    st = S.BY_ID[sid]
    return [st._alternatives(e, s.out_root, s.names)[1][0] for e in st.outputs]


_s = study()
_export = os.path.join(_s.export_root, "roi_regions.csv")
_put(_export, "uid,roi\nA,1\n", _T0)
for _i, _sid in enumerate(("roi_curator", "tune_detection", "roi_geometry", "detect", "roi_dataset",
                           "join_sampling", "build_results")):
    for _p in _outputs_of(_sid, _s):
        _put(_p, _sid + "\n", _T0 + 10 * (_i + 1))
# 05c needs StarDist, which this interpreter may not have; the plan is what is tested.
_det_cli = S.BY_ID["detect"].cli_only
S.BY_ID["detect"].cli_only = None
_trk = ["roi_geometry", "detect", "roi_dataset"]
chk("outputs made after what they read are done",
    [W.state(S.BY_ID[x], _s) for x in _trk], ["done"] * 3)
chk("...and Measure has nothing left to run", W.run_plan("measure", _s)[0], [])
os.utime(_export, (_T0 + 100, _T0 + 100))
_s = study()
chk("a re-export after 05a ran puts 05a out of date",
    W.state(S.BY_ID["roi_geometry"], _s), "outdated")
chk("...and says why", "ROI export" in (W.outdated(S.BY_ID["roi_geometry"], _s) or ""), True)
chk("...and everything made from it, down to the results",
    [W.state(S.BY_ID[x], _s) for x in _trk + ["build_results"]], ["outdated"] * 4)
chk("Run all remaining on Measure runs them again, in order",
    W.run_plan("measure", _s)[0], ["roi_geometry", "detect", "roi_dataset"])
chk("...and they no longer count as done", W.progress("measure", _s)[0], 1)
chk("detection measured before 05c logged its sections is redone, not resumed",
    W.run_values("detect", {"--qc": True}, _s), {"--qc": True, "--force": True})
# 05c logs each section with a fingerprint of the boxes it was measured on, and
# its resume re-measures only the sections whose boxes changed. Redo there would
# re-measure every section - hours - for the few discs a re-export moved.
_m0 = _s.markers[0]
_log = os.path.join(_res, "roi_nuclei_sections_done.csv")
_geo = os.path.join(_res, "roi_measured_geometry.csv")
_put(_log, "%s,*\n%s,A,0123456789abcdef\n" % (_m0, _m0), _T0 + 40)
chk("a re-export resumes detection when 05c can tell which sections changed",
    W.run_values("detect", {"--qc": True}, _s), {"--qc": True})
try:
    import screens as _SC
except ImportError:                     # no Qt here: the words are not testable
    _SC = None
if _SC is not None:
    chk("...and says that is what Run will do",
        "boxes changed" in _SC.stage_status(S.BY_ID["detect"], _s, {})[1], True)
_put(_log, "%s,*\n%s,A,0123456789abcdef\n%s,B\n" % (_m0, _m0, _m0), _T0 + 40)
chk("a section logged with no fingerprint and no recorded shape: redone, as before",
    W.run_values("detect", {}, _s), {"--force": True})
_put(_geo, "scene_uid,marker,roi_index\nB,%s,1\n" % _m0, _T0 + 40)
chk("...with its measured shape recorded, 05c checks it, so it resumes",
    W.run_values("detect", {}, _s), {})
chk("a stage with no Redo switch runs as it is", W.run_values("roi_geometry", {}, _s), {})
chk("nothing is deleted: the outputs are all still there",
    all(os.path.exists(p) for x in _trk for p in _outputs_of(x, _s)), True)

# Once a run records what it read, the record decides, not the file times.
_cfg = json.loads(json.dumps(CFG))


def _record(cfg, *sids, **over):
    s = study(cfg)
    runs = cfg.setdefault("project", {}).setdefault("runs", [])
    for sid in sids:
        runs.append({"stage": sid, "ok": True, "inputs": W.fingerprint(sid, s), **over})


_record(_cfg, *(_trk + ["join_sampling", "build_results"]))
chk("a run records what it read", sorted(W.fingerprint("detect", study(_cfg))),
    ["files", "settings"])
chk("with that record, the same export read again is not a change",
    [W.state(S.BY_ID[x], study(_cfg)) for x in _trk], ["done"] * 3)
os.utime(_export, (_T0 + 200, _T0 + 200))
chk("...nor is the same export saved again", W.outdated(S.BY_ID["roi_geometry"], study(_cfg)), None)
_put(_export, "uid,roi\nA,2\n", _T0 + 300)
chk("a different export is",
    "ROI export" in (W.outdated(S.BY_ID["roi_geometry"], study(_cfg)) or ""), True)
_put(_export, "uid,roi\nA,1\n", _T0 + 300)
chk("...and putting it back clears it", W.outdated(S.BY_ID["roi_geometry"], study(_cfg)), None)
_cfg2 = dict(_cfg, pixel_size_um=0.7)
chk("a setting a stage reads puts it out of date, by name",
    "Pixel size" in (W.outdated(S.BY_ID["roi_geometry"], study(_cfg2)) or ""), True)
_cfg2 = dict(_cfg, section_thickness_um=99)
chk("...only the stages that read it",
    [W.state(S.BY_ID[x], study(_cfg2)) for x in _trk], ["done", "done", "outdated"])
_cfg2 = json.loads(json.dumps(_cfg))
_cfg2.setdefault("detection", {})["auto"] = {"AF568": {"tuned": "2026-10-20", "curve": [[0.5, 3]]}}
chk("re-tuning that keeps the same cut does not redo detection",
    W.outdated(S.BY_ID["detect"], study(_cfg2)), None)
_cfg2["detection"]["auto"]["AF568"]["chosen"] = {"prob_thresh": 0.6}
chk("...a new cut does", bool(W.outdated(S.BY_ID["detect"], study(_cfg2))), True)
chk("...and re-measures every section, though 05c could resume by section",
    W.run_values("detect", {}, study(_cfg2)), {"--force": True})
_put(_export, "uid,roi\nA,3\n", _T0 + 300)
chk("a different export alone resumes detection, by section",
    (bool(W.outdated(S.BY_ID["detect"], study(_cfg))), W.run_values("detect", {}, study(_cfg))),
    (True, {}))
_put(_export, "uid,roi\nA,1\n", _T0 + 300)
chk("a trial run of a few sections records nothing to compare with",
    W.run_inputs("detect", {"--limit": 5}, study(_cfg)), None)
chk("...a full run does", W.run_inputs("detect", {}, study(_cfg)),
    W.fingerprint("detect", study(_cfg)))

# What the 14 Oct notice said by hand is a stage status now.
os.utime(_nuc, (W.ROI_FIX_AT - 3600, W.ROI_FIX_AT - 3600))
chk("nuclei measured before the 14 Oct ROI fixes are out of date",
    "14 Oct" in (W.outdated(S.BY_ID["detect"], study(_cfg)) or ""), True)
chk("...so Run all remaining measures them again, with Redo",
    (W.run_plan("measure", study(_cfg))[0], W.run_values("detect", {}, study(_cfg))),
    (["detect", "roi_dataset"], {"--force": True}))
chk("no dated notice is left on the screens",
    [W.notices(x, study(_cfg)) for x in ("prepare", "measure", "results")], [[], [], []])
S.BY_ID["detect"].cli_only = _det_cli
chk("a stage the app cannot run here holds what is made from it",
    "roi_dataset" in W.run_plan("measure", study(_cfg))[0], W.state(S.BY_ID["detect"],
                                                                    study(_cfg)) != "manual")
for _p in _stale_files:
    if os.path.exists(_p):
        os.remove(_p)

# 05a writes one pair of files per marker the export holds rows for (PR #99), so
# its status asks after each of those markers, not the first declared one: a
# paired study can curate the second marker first, and a re-export that changes
# only the second marker's regions must not pass on the first marker's files.
print("--- 05a is placed for every marker the ROI export holds ---")
_GEO = S.BY_ID["roi_geometry"]
for _layout in ("paired", "multiplex"):
    _c = json.loads(json.dumps(CFG))
    _c.pop("project", None)
    _c.setdefault("acquisition", {}).update(layout=_layout, markers=["AF568", "AF488"])
    _s = study(_c)

    def _place(marker, when):
        for _a in ("roi_geometry", "roi_boxes"):
            _put(_s.names.path(_a, marker), "g\n", when)

    _put(_export, "scene_uid,marker\nB,AF488\n", _T0)
    _place("AF488", _T0 + 10)
    chk(f"{_layout}: only the second marker curated, and placed: 05a is done",
        W.state(_GEO, _s), "done")
    _put(_export, "scene_uid,marker\nA,AF568\nB,AF488\n", _T0 + 20)
    chk(f"{_layout}: the export now holds a marker with no boxes: out of date, naming it",
        (W.state(_GEO, _s), "AF568" in (W.outdated(_GEO, _s) or "")), ("outdated", True))
    _runs = _c.setdefault("project", {}).setdefault("runs", [])
    _runs.append({"stage": "roi_geometry", "ok": True,
                  "inputs": W.fingerprint("roi_geometry", _s)})
    chk(f"{_layout}: ...even with a run on record that read this export",
        W.state(_GEO, study(_c)), "outdated")
    _c["project"]["runs"] = []
    _place("AF568", _T0 + 30)
    chk(f"{_layout}: first marker placed again, second older than the export: out of date",
        W.state(_GEO, study(_c)), "outdated")
    _place("AF488", _T0 + 30)
    chk(f"{_layout}: every marker in the export placed since it: done",
        W.state(_GEO, study(_c)), "done")
    for _p in _stale_files:
        if os.path.exists(_p):
            os.remove(_p)

# What reads 05a's boxes reads every marker's (06a joins each marker's nuclei to
# its own roi_boxes), so re-placing only the second marker's regions has to
# reach detection and what follows, and a run recorded before each marker was
# fingerprinted must not make a study look out of date that is not.
print("--- what is made from 05a follows every marker's boxes ---")
_DET = S.BY_ID["detect"]
S.BY_ID["detect"].cli_only = None
_c = json.loads(json.dumps(CFG))
_c.pop("project", None)
_c.setdefault("acquisition", {}).update(layout="paired", markers=["AF568", "AF488"])
_s = study(_c)


def _place(marker, when, text="g\n"):
    for _a in ("roi_geometry", "roi_boxes"):
        _put(_s.names.path(_a, marker), text, when)


_put(_export, "scene_uid,marker\nA,AF568\nB,AF488\n", _T0)
_place("AF568", _T0 + 10)
_place("AF488", _T0 + 10)
for _p in _outputs_of("detect", _s):
    _put(_p, "detect\n", _T0 + 20)
_put(_log, "AF568,*\nAF568,A,0123456789abcdef\n", _T0 + 20)     # 05c's per-section log
for _p in _outputs_of("roi_curator", _s) + _outputs_of("tune_detection", _s):
    _put(_p, "page\n", _T0 + 5)
_c["project"] = {"runs": [{"stage": x, "ok": True, "inputs": W.fingerprint(x, _s)}
                          for x in ("roi_geometry", "detect")]}
chk("both markers placed, detection run since: done", W.state(_DET, study(_c)), "done")
_place("AF488", _T0 + 30, "moved\n")
chk("re-placing only the second marker's regions puts detection out of date",
    W.state(_DET, study(_c)), "outdated")
chk("...naming that marker", "AF488" in (W.outdated(_DET, study(_c)) or ""), True)
chk("...and 05c resumes, by section, rather than re-measuring everything",
    W.run_values("detect", {}, study(_c)), {})
_place("AF488", _T0 + 30)
chk("...putting the same boxes back clears it", W.state(_DET, study(_c)), "done")
# A record from before: one hash for 05a, over the first marker's pair only.
_m0_files = [_s.names.path(_a, "AF568") for _a in ("roi_geometry", "roi_boxes")]
_old = {"files": {"roi_geometry": W._hash([W._content(p) for p in _m0_files])},
        "settings": W.fingerprint("detect", _s)["settings"]}
_c["project"]["runs"][1]["inputs"] = _old
_place("AF488", _T0 + 10)
chk("a run recorded before each marker was fingerprinted is still current",
    W.state(_DET, study(_c)), "done")
_place("AF488", _T0 + 30, "moved\n")
chk("...until the second marker is placed again after it",
    "AF488" in (W.outdated(_DET, study(_c)) or ""), True)
# Readiness: 05a with a marker of the export still to place is out of date
# itself, so detection waits for it rather than measuring half the export.
_c["project"]["runs"] = []
for _p in _outputs_of("detect", _s):
    os.remove(_p)
for _a in ("roi_geometry", "roi_boxes"):
    os.remove(_s.names.path(_a, "AF568"))
_geo_cli = _GEO.cli_only
_GEO.cli_only = "test: 05a cannot run here"
chk("only the second marker placed: detection waits for the first's regions",
    ("detect" in W.run_plan("measure", study(_c))[0],
     W.run_plan("measure", study(_c))[1].get("detect")), (False, "roi_geometry"))
_GEO.cli_only = _geo_cli
chk("...and with 05a runnable, places them first",
    W.run_plan("measure", study(_c))[0][:2], ["roi_geometry", "detect"])
S.BY_ID["detect"].cli_only = _det_cli
for _p in _stale_files:
    if os.path.exists(_p):
        os.remove(_p)

print("--- the settings a stage reads are the ones ls_config says it reads ---")
_spec = {k.path: k for k in LC.SPEC}
for st in S.STAGES:
    if st.made_from is None:
        continue
    chk(f"{st.sid}: reads only real settings", [k for k in st.settings if k not in _spec], [])
    codes = W.script_codes(st)
    chk(f"{st.sid}: ...each one naming it as a reader",
        [k for k in st.settings if k in _spec
         and not any(c.split("_")[0] in codes for c in _spec[k].consumers)], [])
    left = [k.path for k in LC.SPEC
            if any(c.split("_")[0] in codes for c in k.consumers)
            and k.path not in st.settings and k.path not in W.NOT_TRACKED]
    chk(f"{st.sid}: every setting it reads is tracked, or says why not", left, [])

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
    W.display_title(S.BY_ID["reformat_pcna"], s0),
    "Standard image of every %s section" % s0.marker_label(1))
named = dict(CFG, marker_identity={s0.markers[0]: "pERK"})
chk("a confirmed antibody name is preferred", study(named).marker_label(0), "pERK")
chk("plain words fill the markers in", "{m" in W.plain(S.BY_ID["pair"], s0), False)

print("--- the words a person reads ---")


def _next_up_with(plan, cur=(), decided_=True):
    """next_up(s0) with the study's state stubbed: `plan` is {screen: stages to
    run}, `cur` the curators whose pages are built, `decided_` their export."""
    saved = (W.run_plan, W.curators, W.decided, W.applies)
    built = {sid: S.BY_ID[sid].done for sid in cur}
    W.run_plan = lambda scr, study, **_k: (list(plan.get(scr, [])), {})
    W.curators = lambda study: list(cur)
    W.decided = lambda sid, study: decided_
    W.applies = lambda st, study: (False, "")       # nothing left to unblind
    for sid in cur:
        S.BY_ID[sid].done = lambda *a: True
    try:
        return W.next_up(s0)[1]
    finally:
        W.run_plan, W.curators, W.decided, W.applies = saved
        for sid, fn in built.items():
            S.BY_ID[sid].done = fn


# 04a turns every section upright now; the ROI curator's Rotate is only for
# the odd one it got wrong, so no step tells a person to do it.
_upright = re.compile(r"turn\w*( the)? sections upright", re.I)
chk("Next up does not ask you to turn the sections upright",
    bool(_upright.search(_next_up_with({}, ["roi_curator"], decided_=False))), False)
chk("...nor does Curate or Prepare",
    [t for t in (W.SCREENS["curate"].role, W.SCREENS["curate"].subtitle,
                 W.SCREENS["prepare"].info) if _upright.search(t) or "rotate" in t.lower()], [])
# The R figures are switched off in 06e: Results promises them only when on.
_fig = [W.SCREENS["results"].subtitle, W.SCREENS["results"].intro,
        _next_up_with({"results": ["build_results"]}),
        W.display_title(S.BY_ID["build_results"], s0),
        W.display_title(S.BY_ID["refresh_loop"], s0)] + \
    [p[0] for p in W.SCREENS["results"].parts] + [p[1] for p in W.SCREENS["results"].parts]
chk("06e still draws no R figures (if it does, R_FIGURES goes back to True)",
    "figures: off" in open(os.path.join(REPO, "scripts", "06e_refresh_loop.py"),
                           encoding="utf-8").read(), not S.R_FIGURES)
chk("with the R figures off, Results promises no figures",
    [] if S.R_FIGURES else [t for t in _fig if "figure" in t.lower()], [])
# The step screens are the basic view: a stage there is named in plain words,
# with its script number and the pipeline's own name kept for All stages.
_plain = {sid: W.display_title(S.BY_ID[sid], s0)
          for sid in ("czi_selftest", "reformat_pcna", "reformat_pcna_final",
                      "censor_pcna", "censor_perk", "reformat_perk_final")}
chk("basic stage names carry no pipeline jargon",
    {sid: t for sid, t in _plain.items()
     if re.search(r"self-test|reformat|censor|\bCZI\b|^\d\d", t, re.I)}, {})
chk("...and stay apart, one name per stage", len(set(_plain.values())), len(_plain))
chk("the pipeline's own name stays for the advanced view",
    W.display_title(S.BY_ID["czi_selftest"], s0, technical=True), "CZI self-test")
chk("no setting is labelled in 'um'",
    [k.label for k in LC.SPEC if re.search(r"\bum\b", k.label)], [])
chk("section thickness is in µm", LC.BY_PATH["section_thickness_um"].label,
    "Section thickness µm")
import naming_build as NB                                       # noqa: E402
chk("a file name rule reads in words",
    NB.reading("LS53_2b.czi", r"(?P<subject>[A-Z]+\d+)_(?P<slide>\d+)(?P<replicate>[a-z])\.czi"),
    "animal LS53 · slide 2 · replicate b")
chk("...and None when the name does not fit", NB.reading("x.czi", r"(?P<subject>LS\d+)"), None)
# Prepare's count and its "N stages left to run" are made over the same stages:
# Run all remaining also builds the curator pages, so they are counted too.
for _scr in ("prepare", "measure", "results"):
    _d, _t = W.progress(_scr, s0)
    _plan, _held = W.run_plan(_scr, s0)
    chk(f"{_scr}: no more left to run than its count says is left",
        len(_plan) + len(_held) <= _t - _d, True)

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
_bundle = os.environ.pop("LS_ATLAS_BUNDLE", None)
salmon = study(dict(CFG, atlas_source="salmon"))
chk("built-in plates ship, so the extraction stages are not part of the study",
    [W.applies(S.BY_ID[sid], salmon)[0] for sid in ("atlas_extract", "atlas_reframe")],
    [False, False])
chk("...and Prepare does not plan or wait on them",
    [sid for sid in W.run_plan("prepare", salmon)[0] if sid in W.ATLAS_BUILD]
    + [w for w in W.run_plan("prepare", salmon)[1].values() if w in W.ATLAS_BUILD], [])
chk("...nor is the plate-box curator a Curate tab", W.curators(salmon), ["roi_curator"])
chk("Rebuild atlas plates brings them back",
    W.applies(S.BY_ID["atlas_extract"], study(dict(CFG, atlas_source="salmon",
                                                    atlas_rebuild_plates=True)))[0], True)
chk("a plate set that does not ship is still built",
    W.applies(S.BY_ID["atlas_remerge"], study(dict(CFG, atlas_plate_set={"dir": "plates_merged"})))[0],
    True)
chk("the Wullimann plates ship too",
    W.applies(S.BY_ID["atlas_wullimann_plates"], study(dict(CFG, atlas_source="wullimann1996")))[0],
    False)
if _bundle is not None:
    os.environ["LS_ATLAS_BUNDLE"] = _bundle
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

print("--- the Slides card is done only when there is nothing left to do on it ---")
# Set up used to tick Slides as soon as a folder was set: unread slides, file
# names the rule misses (files every stage ignores) and markers still called
# Marker1 all read as Done.
NAMED = json.loads(json.dumps(CFG))
NAMED["acquisition"]["channels"][1]["name"] = "pERK"
FIT = ["AB12_3a.czi", "AB12_4.czi"]
chk("no folder: choose it", W.slides_left(dict(NAMED, source_dir=""), False, []),
    ["choose the slides folder"])
chk("a folder whose slides were never read is not done",
    W.slides_left(NAMED, False, FIT), ["read the slides (Read settings from the slides)"])
chk("read, every name fits, markers named: done", W.slides_left(NAMED, True, FIT), [])
chk("file names the rule misses are counted",
    W.slides_left(NAMED, True, FIT + ["scan 7.czi", "LS_x.czi"]),
    ["2 file names don't fit the rule (Slide file names)"])
chk("...one of them in the singular",
    W.slides_left(NAMED, True, FIT + ["scan 7.czi"]),
    ["1 file name doesn't fit the rule (Slide file names)"])
NAME_IT = "name the markers (Microscope and channels > Channels and markers...)"
chk("a marker still called Marker1 is not a name", W.slides_left(CFG, True, FIT), [NAME_IT])
chk("...nor is Channel2, what a read gives a channel with no name",
    W.slides_left(json.loads(json.dumps(NAMED).replace('"pERK"', '"Channel2"')), True, FIT),
    [NAME_IT])
chk("...but a confirmed antibody for it is",
    W.slides_left(dict(CFG, marker_identity={"Marker1": "pERK"}), True, FIT), [])
chk("a study with no marker at all is asked for them",
    W.slides_left(dict(NAMED, acquisition={"layout": "paired", "markers": []}), True, FIT),
    [NAME_IT])
chk("a paired study's dye names are names",
    W.slides_left(dict(NAMED, acquisition={"layout": "paired", "markers": ["AF568", "AF488"]}),
                  True, FIT), [])
chk("Next up says what the Slides card still needs, in its own words",
    W.next_up(s0, setup_ok=False, setup_missing=[
        ("Slides", ["2 file names don't fit the rule (Slide file names)", NAME_IT]),
        ("Atlas", ["Atlas PDF"])])[1],
    "Finish Set up: 2 file names don't fit the rule (Slide file names); %s; "
    "Atlas needs Atlas PDF." % NAME_IT)
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

print("--- Done means complete: detection and curation count sections ---")
# 05c's roi_nuclei.csv exists after the first section, and Curate turned green
# after any export at all, five sections of two thousand included. A study of
# its own, so the files below cannot change what the checks above saw.
DM_CFG = json.load(open(write_study(os.path.join(TMP, "dm"))))
dm = study(DM_CFG)
DM = DM_CFG["out_root"]
M0 = dm.markers[0]


def _csv(path, header, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(",".join(header) + "\n")
        for r in rows:
            fh.write(",".join(r) + "\n")


UIDS = ["S%04d" % i for i in range(1980)]
REF = os.path.join(DM, "reformatted")
_csv(os.path.join(REF, "roi_worklist.csv"), ["rank", "tier", "scene_uid"],
     [(str(i + 1), "core", u) for i, u in enumerate(UIDS)])
EXP = os.path.join(dm.export_root, "02.02.2026_10.00")
_csv(os.path.join(EXP, "roi_regions_02.02.2026_10.00.csv"), ["scene_uid", "roi_kind"],
     [(u, "roi") for u in UIDS[:5]])
open(os.path.join(REF, "roi_curator.html"), "w").write("<html></html>")
# The plate boxes, where this study draws them, are exported once and whole.
os.makedirs(os.path.join(DM, "atlas"), exist_ok=True)
open(os.path.join(DM, "atlas", "plate_reframe.html"), "w").write("<html></html>")
open(os.path.join(DM, "atlas", "plate_boxes.csv"), "w").write("x\n")
chk("five sections exported of a 1,980-section worklist is not Curate done",
    W.step_progress("curate", dm), (5, 1980))
chk("...and says how far it has got",
    W.progress_words("curate", *W.step_progress("curate", dm), study=dm),
    "5 of 1,980 sections curated")
# The rest curated, three of them excluded in the curator instead (roi_plates
# beside the export, excluded=1): an excluded section is a decision made.
_csv(os.path.join(EXP, "roi_regions_02.02.2026_10.00.csv"), ["scene_uid", "roi_kind"],
     [(u, "roi") for u in UIDS[:-3]])
_csv(os.path.join(EXP, "roi_plates_02.02.2026_10.00.csv"), ["scene_uid", "excluded"],
     [(u, "1") for u in UIDS[-3:]])
chk("every worklist section curated or excluded: Curate is done",
    W.progress_words("curate", *W.step_progress("curate", dm), study=dm), "Done")
# 05a copies the export it read to reformatted/roi_regions_used_<marker>.csv,
# AFTER the export was written, so the copy is the newest roi_regions*.csv on
# disk. It is 05a's record, not a curator export: with no roi_plates beside it,
# taking it as the newest export would lose every exclusion and could count an
# older curation than the operator's latest.
_used = os.path.join(REF, "roi_regions_used_%s.csv" % M0)
_csv(_used, ["scene_uid", "roi_kind"], [(u, "roi") for u in UIDS[:5]])
_t = os.path.getmtime(os.path.join(EXP, "roi_regions_02.02.2026_10.00.csv")) + 60
os.utime(_used, (_t, _t))
chk("05a's used copy, newer than the export, is not the newest export",
    os.path.basename(W.decision_file("roi_curator", dm)),
    "roi_regions_02.02.2026_10.00.csv")
chk("...so Curate is still done",
    W.progress_words("curate", *W.step_progress("curate", dm), study=dm), "Done")
os.remove(_used)

# Detection: 640 of the 1,980 curated sections logged as measured.
_csv(os.path.join(REF, "roi_boxes_%s.csv" % M0), ["scene_uid", "roi_index"],
     [(u, "0") for u in UIDS])
_csv(os.path.join(REF, "roi_geometry_%s.csv" % M0), ["scene_uid"], [(u,) for u in UIDS])
os.makedirs(os.path.join(DM, "qc"), exist_ok=True)
open(os.path.join(DM, "qc", "detection_tuning.md"), "w").write("x\n")
_csv(os.path.join(DM, "results", "roi_nuclei.csv"), ["scene_uid"], [(u,) for u in UIDS[:640]])
_csv(os.path.join(DM, "results", "roi_nuclei_sections_done.csv"), [M0, "*"],
     [(M0, u) for u in UIDS[:640]])
DET = S.BY_ID["detect"]
_cli = DET.cli_only
DET.cli_only = None                       # StarDist or not, the count is the point
try:
    chk("a part-measured detection says how far it has got",
        W.incomplete(DET, dm), "640 of 1,980 sections")
    chk("...is not Done on its row",
        __import__("screens").stage_status(DET, dm, {}),
        ("todo", "Detection 640 of 1,980 sections, resume"))
    chk("...is planned again, to resume", "detect" in W.run_plan("measure", dm)[0], True)
    chk("...and does not count as done in Measure's progress",
        W.progress("measure", dm)[0] < W.progress("measure", dm)[1], True)
    _plan = W.run_plan
    W.run_plan = lambda sid, s, *a, **k: (["detect"], {}) if sid == "measure" else ([], {})
    chk("Next up says so", W.next_up(dm)[:2],
        ("measure", "Detection 640 of 1,980 sections, resume."))
    W.run_plan = _plan
    _csv(os.path.join(DM, "results", "roi_nuclei_sections_done.csv"), [M0, "*"],
         [(M0, u) for u in UIDS])
    chk("every section logged: detection is complete", W.incomplete(DET, dm), None)
    chk("...and Done", W.state(DET, dm), "done")
    # A log from before this marker was logged (no `marker,*` line) cannot count
    # anything: the rows are trusted, as 05c trusts them.
    _csv(os.path.join(DM, "results", "roi_nuclei_sections_done.csv"), ["other", "*"], [])
    chk("...and a dataset older than the log is not called partial",
        W.incomplete(DET, dm), None)
finally:
    DET.cli_only = _cli

print("--- curation coverage per animal and level, and the second marker's own pass ---")
# A paired study: the second marker's sections are separate physical sections,
# curated on their own in the same page (04l), so they are a pass of their own.
CV_CFG = json.load(open(write_study(os.path.join(TMP, "cv"))))
CV_CFG["acquisition"] = dict(CV_CFG.get("acquisition") or {}, layout="paired",
                             markers=["AF568", "AF488"])
cv = study(CV_CFG)
CVR = os.path.join(CV_CFG["out_root"], "reformatted")
chk("a multiplex study has no second pass: one scan, one curation", W.second_pass(dm), None)
chk("a paired study curates its second marker on its own", W.second_pass(cv), 1)
chk("...called by its own name", cv.marker_label(W.second_pass(cv)), "AF488")
_csv(os.path.join(CVR, "roi_worklist.csv"), ["rank", "scene_uid", "animal"],
     [("1", "u1", "A1"), ("2", "u4", "A2"), ("3", "u2", "A1"), ("4", "u3", "A1"),
      ("5", "u5", "A2")])
_csv(cv.names.path("index", "AF488"), ["id", "kind", "animal"],
     [("p1", "section", "A1"), ("p2", "section", "A1"), ("p3", "section", "A2"),
      ("p4", "section", "A2"), ("plate", "plate", "")])
CVE = os.path.join(cv.export_root, "03.03.2026_10.00")
_csv(os.path.join(CVE, "roi_regions_03.03.2026_10.00.csv"), ["scene_uid", "marker", "roi_kind"],
     [("u1", "AF568", "roi"), ("u2", "AF568", "roi"), ("u4", "AF568", "roi")])
_csv(os.path.join(CVE, "roi_plates_03.03.2026_10.00.csv"),
     ["scene_uid", "animal", "marker", "plate_id", "excluded"],
     [("u1", "A1", "AF568", "plate_010", "0"), ("u2", "A1", "AF568", "plate_020", "0"),
      ("u4", "A2", "AF568", "plate_010", "0"), ("u3", "A1", "AF568", "plate_020", "1")])
open(os.path.join(CVR, "roi_curator.html"), "w").write("<html></html>")
os.makedirs(os.path.join(CV_CFG["out_root"], "atlas"), exist_ok=True)
open(os.path.join(CV_CFG["out_root"], "atlas", "plate_reframe.html"), "w").write("<html></html>")
open(os.path.join(CV_CFG["out_root"], "atlas", "plate_boxes.csv"), "w").write("x\n")
chk("with no setting, the target is one section per animal and level",
    W.coverage_target(cv), 1)
chk("...and the setting is offered in Set up's advanced settings",
    [sid for sid, _t, _i, keys in W.setting_sections(LC.dialog_fields(), advanced=True)
     if "curation_coverage.sections_per_level" in [k.path for k in keys]], ["curation"])
cov = W.coverage(cv, 0)
chk("coverage lists every animal in the queue, curated or not", cov["animals"], ["A1", "A2"])
chk("...and every level a curated section was placed on", cov["levels"],
    ["plate_010", "plate_020"])
chk("...counting curated sections, not the excluded one",
    cov["counts"], {("A1", "plate_010"): 1, ("A1", "plate_020"): 1, ("A2", "plate_010"): 1})
chk("...and the cells under target are named", cov["under"], [("A2", "plate_020")])
_html = __import__("screens").coverage_html(cov, "AF568")
chk("the coverage table marks the cell under target, and only that one",
    _html.count("background:"), 1)
# What is counted is table cells, one per animal at one level, so the line
# names them as cells and gets the plural right ("6 animal and levels" read
# as broken English on a real study).
chk("...and says how many cells are under, in the singular for one",
    "1 animal/level cell under the target of 1" in _html, True)
chk("...and in the plural for more than one",
    "4 animal/level cells under the target of 2" in __import__("screens").coverage_html(
        W.coverage(study(dict(CV_CFG, curation_coverage={"sections_per_level": 2})), 0),
        "AF568"), True)
chk("the Curate screen lists both passes, each with its own count",
    re.sub("<[^>]+>", "", __import__("screens").passes_html(cv)).split("\n")[0],
    "AF568 curation ● 4 of 5 sections curatedAF488 curation ● 0 of 4 sections curated "
    "Choose AF488 in the curator's Channel.")
chk("a higher target leaves more under it",
    W.coverage(study(dict(CV_CFG, curation_coverage={"sections_per_level": 2})), 0)["under"],
    [("A1", "plate_010"), ("A1", "plate_020"), ("A2", "plate_010"), ("A2", "plate_020")])
chk("the first marker's pass: excluded counts as decided",
    W.curated_sections(cv, 0), (4, 5))
chk("the second marker's pass counts its own sections, not the plates",
    W.curated_sections(cv, 1), (0, 4))
chk("Curate's progress counts both passes", W.step_progress("curate", cv), (4, 9))
# The first marker finished; the second untouched.
_csv(os.path.join(CVE, "roi_regions_03.03.2026_10.00.csv"), ["scene_uid", "marker", "roi_kind"],
     [(u, "AF568", "roi") for u in ("u1", "u2", "u4", "u5")])
chk("the first pass done is not the ROI curator finished while the second is not",
    (W.curated_sections(cv, 0), W.curator_finished("roi_curator", cv)), ((5, 5), False))
_plan, _ap = W.run_plan, W.applies
W.run_plan = lambda sid, s, *a, **k: ([], {})
W.applies = lambda st, s: (False, "") if st.unblinds else _ap(st, s)
try:
    chk("Next up points to the second marker's pass", W.next_up(cv),
        ("curate", "AF568 curation is done. 0 of 4 AF488 sections curated: choose "
                   "AF488 in the ROI curator's Channel and curate or exclude them.",
         "roi_curator"))
    _csv(os.path.join(CVE, "roi_plates_03.03.2026_10.00.csv"),
         ["scene_uid", "animal", "marker", "plate_id", "excluded"],
         [("u3", "A1", "AF568", "plate_020", "1")]
         + [(p, "A1", "AF488", "plate_010", "1") for p in ("p1", "p2", "p3", "p4")])
    chk("...and once it is decided too, everything is done", W.next_up(cv)[0], "home")
    chk("...and the ROI curator is finished", W.curator_finished("roi_curator", cv), True)
finally:
    W.run_plan, W.applies = _plan, _ap
chk("a multiplex study's curation is one pass: its progress is unchanged",
    W.step_progress("curate", dm), W.curated_sections(dm))

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

    # Help: every entry must lead somewhere real - a file that exists, the
    # project's own pages, or a handler the window already has.
    import updates as UP
    help_menu = next(a.menu() for a in w.menuBar().actions() if a.text() == "&Help")
    chk("the Help menu's entries", [a.text() for a in help_menu.actions() if a.text()],
        ["User guide", "Wiki", "Show the activity log", "Open the log folder",
         "Run the CZI self-test", "Check for updates…", "Undo last update", "About…"])
    by_label = {a.text(): a for a in help_menu.actions()}
    opened, revealed, queued = [], [], []
    _real_open, _real_reveal = MW.QDesktopServices.openUrl, MW.reveal
    MW.QDesktopServices.openUrl = staticmethod(lambda url: opened.append(url.toString()))
    MW.reveal = lambda path: revealed.append(path)
    w.run_stages = lambda sids: queued.append(list(sids))
    by_label["User guide"].trigger()
    guide = os.path.join(REPO, "docs", "user-guide.md")
    chk("User guide shows the guide beside the code, rendered",
        (w._guide.isVisible(), os.path.normcase(w._guide.browser.source().toLocalFile()),
         bool(w._guide.browser.toPlainText().strip())),
        (True, os.path.normcase(guide), True))
    w._guide.close()
    _root, w.repo_root = w.repo_root, TMP             # an installed copy ships no docs/
    by_label["User guide"].trigger()
    w.repo_root = _root
    chk("...and without it, the online copy",
        opened, [f"https://github.com/{UP.REPO_SLUG}/blob/main/docs/user-guide.md"])
    by_label["Wiki"].trigger()
    chk("Wiki opens the project's wiki", opened[-1], f"https://github.com/{UP.REPO_SLUG}/wiki")
    by_label["Show the activity log"].trigger()
    chk("Show the activity log opens the log drawer",
        (w.drawer.toggle.isChecked(), w.drawer.log.isVisibleTo(w)), (True, True))
    _env = {n: os.environ.get(n) for n in ("LOCALAPPDATA", "XDG_STATE_HOME")}
    os.environ["LOCALAPPDATA"] = os.path.join(TMP, "LocalAppData")
    by_label["Open the log folder"].trigger()
    for n, v in _env.items():
        os.environ.pop(n, None) if v is None else os.environ.__setitem__(n, v)
    logs = os.path.join(TMP, "LocalAppData", "The Great IHC Leap Forward", "logs")
    chk("Open the log folder opens the crash log's folder, made if missing",
        (revealed, os.path.isdir(logs)), ([logs], True))
    by_label["Run the CZI self-test"].trigger()
    chk("Run the CZI self-test runs stage 00d on Prepare, like its own Run button",
        (queued, w.stack.currentWidget() is w.pages["prepare"]), ([["czi_selftest"]], True))
    MW.QDesktopServices.openUrl, MW.reveal = _real_open, _real_reveal
    del w.run_stages

    w.goto("curate")
    w.curate.show_curator("roi_curator")
    _was = w.curate._study
    w.curate._study = cv
    _csv(os.path.join(CVE, "roi_plates_03.03.2026_10.00.csv"),
         ["scene_uid", "animal", "marker", "plate_id", "excluded"],
         [("u1", "A1", "AF568", "plate_010", "0")])
    w.curate._update_actions()
    chk("a paired study's Curate screen shows both passes and the coverage table",
        (w.curate.passes.isVisibleTo(w.curate), "AF488 curation" in w.curate.passes.text(),
         w.curate.coverage_area.isVisibleTo(w.curate), "<table" in w.curate.coverage.text()),
        (True, True, True, True))
    w.curate._study = _was
    w.curate._update_actions()
    chk("...and a study with no worklist yet shows neither",
        (w.curate.passes.isVisibleTo(w.curate), w.curate.coverage_area.isVisibleTo(w.curate)),
        (False, False))
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
    basic = W.setting_sections(LC.dialog_fields(), advanced=False)
    chk("Set up shows Slides and only the cards with something to ask",
        [w.setup.tabs.tabText(i).lstrip("✓ ") for i in range(w.setup.tabs.count())],
        ["Slides"] + [t for _s, t, _i, _k in basic])
    chk("...which leaves out the cards the slide files and defaults answer",
        [sid for sid, *_ in basic if sid in ("pictures", "detection")], [])
    w.setup.advanced.setChecked(True)
    chk("Show advanced settings brings every card back",
        w.setup.tabs.count(), 1 + len(W.setting_sections(LC.dialog_fields())))
    w.setup.advanced.setChecked(False)
    chk("Set up says where this study's slides come from",
        CFG["source_dir"] in w.setup.current.text(), True)
    chk("...and lists what each incomplete card is missing",
        all(gaps for _t, gaps in w.setup.missing()), True)
    # This study's folder is set but its slides were never read, and its marker
    # is the template's Marker1: Slides is not done, whatever the folder says.
    slides_gaps = dict(w.setup.missing()).get("Slides")
    chk("Set up's Slides card waits for the slides to be read and the markers named",
        slides_gaps, ["read the slides (Read settings from the slides)", NAME_IT])
    done_cards, all_cards = w.setup.completion()
    chk("...so Set up is not Done", done_cards < all_cards, True)
    chk("...and the Slides tab carries no tick", w.setup.tabs.tabText(0), "Slides")
    chk("...and says what is left",
        "Still to do on this tab" in w.setup.current.text()
        and "name the markers" in w.setup.current.text(), True)
    import atlas_dialog as AD
    chk("without the Fiji importer the Atlas card offers no button",
        AD.available() or not hasattr(w.setup, "fiji_btn"), True)
    import importlib.machinery, types
    _ij = types.ModuleType("ls_atlas_imagej")
    _ij.__spec__ = importlib.machinery.ModuleSpec("ls_atlas_imagej", None)
    _ij.calls = []

    def _ij_main(argv):
        # Stands in for the real command: it writes the config on disk, as the
        # import does when it registers and selects the atlas.
        _ij.calls.append(argv)
        if "bad" in argv[0]:
            print("not built - fix these in Fiji and run again:")
            print("  - plate_01.tif: no ROI Manager .zip beside it")
            return 1
        cfg = json.load(open(argv[argv.index("--config") + 1]))
        cfg["study"]["name"] = "after fiji"
        json.dump(cfg, open(argv[argv.index("--config") + 1], "w"))
        print("2 plates, 5 regions -> x")
        return 0
    _ij.main = _ij_main
    sys.modules["ls_atlas_imagej"] = _ij
    w.setup.build()
    chk("with it, the Atlas card offers Add atlas from Fiji",
        w.setup.fiji_btn.text(), "Add atlas from Fiji…")
    chk("the command line it runs",
        AD.argv_for("/f", "zf", "Zebrafish", True, "/c.json"),
        ["/f", "--name", "zf", "--label", "Zebrafish", "--mirror", "--config", "/c.json"])
    dlg = AD.FijiAtlasDialog(w.session, w)
    chk("it cannot run without a folder and a valid name", dlg.go.isEnabled(), False)
    dlg.folder.setText(os.path.join(TMP, "bad"))
    dlg.name.setText("zf")
    chk("...and can with both", dlg.go.isEnabled(), True)
    chk("a folder Fiji got wrong is refused", dlg.build(), False)
    chk("...with each problem on its own line",
        dlg.output.toPlainText().splitlines()[-1], "  - plate_01.tif: no ROI Manager .zip beside it")
    dlg.folder.setText(os.path.join(TMP, "good"))
    chk("a good one is added", dlg.build(), True)
    chk("...and the project is read back with what the import wrote",
        w.session.project.cfg["study"]["name"], "after fiji")
    del sys.modules["ls_atlas_imagej"]
    w.setup.build()
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
    _ws = w.study()
    _put(os.path.join(_ws.export_root, "roi_regions.csv"), "uid,roi\nA,1\n", _T0 + 100)
    for _p in _outputs_of("roi_geometry", _ws):
        _put(_p, "g\n", _T0)
    w.refresh()
    chk("a stage made before the latest export says it is out of date, and why",
        (_geo.status.text().startswith("Out of date: your ROI export is newer"),
         _geo.run_btn.text(), _geo.run_btn.isEnabled()), (True, "Run", True))
    chk("...and there is no separate notice to read or dismiss",
        w.lists["measure"].notice.isVisibleTo(w.lists["measure"]), False)
    _det = w.lists["measure"].rows["detect"]
    chk("a stage made from it is not offered as Done",
        _det.status.text() != "Done", True)
    for _p in _stale_files:
        if os.path.exists(_p):
            os.remove(_p)
    w.refresh()
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
    _rx = r"(?P<subject>[A-Z]+\d+)_(?P<slide>\d+)\.czi"
    _named = dict(CFG, slide_naming={"pattern": _rx, "example": "LS53_2.czi"})
    json.dump(_named, open(_p, "w"))
    form = CD.SettingsForm(REPO, path=_p, keys=["slide_naming.pattern"], advanced=False)
    chk("basic Set up shows the file name rule as an example, not the regex",
        (form.pattern_row.edit.isHidden(), _rx in form.pattern_row.words.text(),
         "animal LS53" in form.pattern_row.words.text()), (True, False, True))
    chk("...which still saves the pattern it holds", form.pattern_row.value(), _rx)
    form = CD.SettingsForm(REPO, path=_p, keys=["slide_naming.pattern"])
    chk("...and advanced shows the regex to edit",
        (form.pattern_row.edit.isHidden(), form.pattern_row.edit.text()), (False, _rx))
    chk("a step screen's row names the stage without its script number",
        (w.lists["prepare"].rows["czi_selftest"].code.text(),
         w.lists["prepare"].rows["czi_selftest"].name.text()),
        ("", W.display_title(S.BY_ID["czi_selftest"], w.study())))
    chk("...which All stages still shows",
        w.lists["all"].rows["czi_selftest"].code.text(), "00d")
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

    import threading as _th
    _release = _th.Event()

    def _counting_run(stage, on_line=None, extra_argv=()):
        on_line("starting")
        on_line("@@progress 5/10 sections")
        _release.wait(10)
        return RN.StageResult(stage.sid, True, 0.1, "")
    w.runner.run = _counting_run
    w.run_stages(["scene_renders"])
    _end = _t.time() + 10
    while w.drawer.bar.maximum() != 10 and _t.time() < _end:
        QCoreApplication.processEvents()
        _t.sleep(0.02)
    _row = w.lists["prepare"].rows["scene_renders"].bar
    chk("a running stage shows how far it has got",
        (w.drawer.bar.isVisibleTo(w.drawer), w.drawer.bar.value(), w.drawer.count.text(),
         _row.maximum(), _row.value()),
        (True, 5, "5 of 10 sections (50%)", 10, 5))
    chk("...and its count lines stay out of the log",
        "@@progress" in w.drawer.log.toPlainText(), False)
    _release.set()
    _end = _t.time() + 10
    while (w._thread is not None or w._queue) and _t.time() < _end:
        QCoreApplication.processEvents()
        _t.sleep(0.02)
    chk("the bar goes when the stage is done", w.drawer.bar.isVisibleTo(w.drawer), False)
    import progress as PR
    _clock = [0.0]
    w.drawer.clock = lambda: _clock[0]
    w.drawer.start_progress()
    w.drawer.show_progress(PR.Progress(0, 100, "sections"))
    _clock[0] = 600.0
    w.drawer.show_progress(PR.Progress(10, 100, "sections"))
    chk("a long stage says about how long it has left, next to its count",
        w.drawer.count.text(), "10 of 100 sections (10%), about 1 h 30 min left")
    w.drawer.stop_progress()
    w.session.project.record_run("scene_renders", True, "2026-01-02T03:00:00+00:00",
                                 "2026-01-02T06:12:00+00:00")
    w.refresh()
    _sr = w.lists["prepare"].rows["scene_renders"]
    chk("its row says how long the last complete run took",
        (_sr.took.text(), _sr.took.isVisibleTo(w.lists["prepare"])),
        ("Last run took 3 h 12 min", True))
    chk("a stage never run says nothing of it",
        w.lists["measure"].rows["roi_geometry"].took.isVisibleTo(w.lists["measure"]), False)

    def _wait_idle():
        end = _t.time() + 10
        while (w._thread is not None or w._queue) and _t.time() < end:
            QCoreApplication.processEvents()
            _t.sleep(0.02)

    # A poor signal check stops Run all remaining before hours of detection.
    # The fake signal check writes detection.auto and the report, as 05b does,
    # and still finishes cleanly: its outputs are kept and detection can be run
    # on its own to carry on anyway.
    _m0 = w.study().markers[0]
    _report = os.path.join(w.study().out_root, "qc", "detection_tuning.md")

    def _tuning_run(verdict):
        ran = []

        def run(stage, on_line=None, extra_argv=()):
            ran.append(stage.sid)
            if stage.sid == "tune_detection":
                cfg = json.load(open(PATH))
                cfg.setdefault("detection", {})["auto"] = {_m0: {
                    "verdict": verdict, "reasons": ["signal-to-noise is 1.2, below 3"]}}
                json.dump(cfg, open(PATH, "w"))
                os.makedirs(os.path.dirname(_report), exist_ok=True)
                open(_report, "w").write("# Detection tuning\n")
            return RN.StageResult(stage.sid, True, 0.1, "")
        return ran, run

    def _run_and_wait(sids):
        w.run_stages(sids)
        _wait_idle()

    _three = ["tune_detection", "detect", "roi_dataset"]
    _ran, w.runner.run = _tuning_run("poor")
    del asked[:]
    _run_and_wait(_three)
    chk("a poor signal check holds the run before detection", _ran, ["tune_detection"])
    chk("...and says which marker and why, and how to carry on",
        (len(asked), _m0 in w.drawer.log.toPlainText(),
         "signal-to-noise is 1.2" in w.drawer.log.toPlainText(),
         "carry on anyway" in w.drawer.log.toPlainText()),
        (1, True, True, True))
    chk("...without calling the signal check a failure",
        (w._running.get("tune_detection"), w._queue), (None, []))
    _tune = w.lists["measure"].rows["tune_detection"]
    chk("the signal check row shows the verdict",
        "poor" in _tune.status.text() and _m0 in _tune.status.text(), True)
    chk("...and offers its report",
        (_tune.open_btn.text(), _tune.open_btn.isVisibleTo(w.lists["measure"])),
        ("Open report", True))
    import screens as SC
    _opened = []
    _real_open = SC.QDesktopServices.openUrl
    SC.QDesktopServices.openUrl = staticmethod(lambda url: _opened.append(url.toLocalFile()))
    _tune.open_btn.click()
    SC.QDesktopServices.openUrl = _real_open
    chk("Open report opens the report file",
        [os.path.normcase(os.path.abspath(p)) for p in _opened],
        [os.path.normcase(os.path.abspath(_report))])
    _ran, w.runner.run = _tuning_run("poor")
    _run_and_wait(["detect"])
    chk("detection run on its own still goes ahead", _ran, ["detect"])
    _ran, w.runner.run = _tuning_run("good")
    del asked[:]
    _run_and_wait(_three)
    chk("a good signal check runs straight on into detection", (_ran, asked), (_three, []))

    def _blocking_run(ok):
        def run(stage, on_line=None, extra_argv=()):
            _release.wait(10)
            return RN.StageResult(stage.sid, ok, 0.1, "", None if ok else "it broke")
        return run

    # Stop, then the stage fails: the next queue must still be stoppable.
    _release.clear()
    w.runner.run = _blocking_run(False)
    w.run_stages(["scene_renders", "pair"])
    w.drawer.stop_btn.click()
    _release.set()
    _wait_idle()
    _release.clear()
    w.runner.run = _blocking_run(True)
    w.run_stages(["scene_renders", "pair"])
    chk("after a stopped run failed, the next queue's Stop works again",
        (w.drawer.stop_btn.isVisibleTo(w.drawer), w.drawer.stop_btn.isEnabled()), (True, True))

    # Switching study while a stage runs would rebind the runner under it.
    _opened = []
    _real_open, _real_new = w.session.open_project, w.session.new_project
    w.session.open_project = lambda *a, **k: _opened.append("open")
    w.session.new_project = lambda *a, **k: _opened.append("new")
    import study_picker as SP
    _real_pick = SP.StudyPicker.exec
    SP.StudyPicker.exec = lambda dlg: (_opened.append("picker"), 0)[1]
    del asked[:]
    w._file_actions["open"].trigger()
    w._file_actions["new"].trigger()
    w._studies()
    w._home_action("open")
    chk("while a stage runs, opening or switching a project is refused",
        (_opened, asked.count("A step is running")), ([], 4))
    w.session.open_project, w.session.new_project = _real_open, _real_new
    SP.StudyPicker.exec = _real_pick

    # Closing mid-run: the box answers with the button whose text is queued.
    close_answer = []

    def _close_exec(box):
        asked.append(box.text())
        want = close_answer.pop(0)
        next(b for b in box.buttons() if b.text() == want).click()
        return 0
    _real_exec = MW.QMessageBox.exec
    MW.QMessageBox.exec = _close_exec
    w.show()
    close_answer.append("Keep running")
    del asked[:]
    chk("closing mid-run asks, and Keep running leaves the window open",
        (w.close(), w.isVisible(), w._thread is not None,
         asked == ["A step is still running. Stop after it finishes, or keep running?"]),
        (False, True, True, True))
    close_answer.append("Stop after it finishes")
    chk("...Stop after it finishes keeps it open while the stage runs",
        (w.close(), w.isVisible(), w._stop_requested), (False, True, True))
    _release.set()
    _wait_idle()
    end = _t.time() + 5
    while w.isVisible() and _t.time() < end:
        QCoreApplication.processEvents()
        _t.sleep(0.02)
    chk("...and closes once it has finished, without running the rest",
        (w.isVisible(), w._thread, w._running.get("pair")), (False, None, None))
    MW.QMessageBox.exec = _real_exec
    w.server.stop()

print()
print("FAILED: " + ", ".join(failures) if failures else "ALL PASS")
sys.exit(1 if failures else 0)
