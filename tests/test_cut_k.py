"""The positivity cut's multiplier comes from the config, per marker.

The cut has been median + 3 x 1.4826 x MAD of a section's own background-disc
nuclei, with the 3 a constant (ls_overlay.CUT_K). It is now
`positivity.cut_k`, a {marker or "*": k} map, so a reagent-control check can
later suggest a different multiplier per marker. What is pinned here:

  * the fallback chain: cut_k[marker], then cut_k["*"], then 3.0;
  * section_cut with no k is the old arithmetic, bit for bit;
  * ls_config refuses a multiplier outside 1-10, a map that is not a map, and
    an fp_target outside 0.001-0.2 - each naming its key;
  * NO CONTROLS, NO CHANGE: a study with no `positivity` block gets 06a
    outputs byte-identical to the code before this key (19f24a6), and so does
    one with the default written out;
  * every place 06a, 05c and the reagent report form a cut uses the marker's k;
  * a new cut_k puts 06a, the results build and 06i out of date, never 05c,
    and the default written out (as Set up saves it) changes nothing.

GOLDEN FILES. tests/golden/cut_k/ holds 06a's outputs on a fixed fixture, made
by tests/_cut_k_golden.py from 19f24a6's scripts in a temporary worktree. They
are committed because CI checks out one commit (fetch-depth 1) and cannot
reach 19f24a6. Where it IS reachable this suite also rebuilds them from
19f24a6 in a temporary worktree and checks they still match, so the committed
files cannot quietly stop being the base's.

Run:  python tests/test_cut_k.py
"""

import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SCRIPTS = os.path.join(REPO, "scripts")
sys.path[:0] = [os.path.join(REPO, "app"), SCRIPTS, HERE]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from _fixture import use_temp_study, write_study                # noqa: E402

STUDY = use_temp_study()

import ls_config as LC                                          # noqa: E402
import ls_overlay as LO                                         # noqa: E402

failures = []
BASE = "19f24a6"
GOLDEN = os.path.join(HERE, "golden", "cut_k")
GEN = os.path.join(HERE, "_cut_k_golden.py")


def chk(name, got, want=True):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name:<70} {got!r}" + ("" if ok else f"  (wanted {want!r})"))
    if not ok:
        failures.append(name)


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


print("--- cut_k_fallback_chain ---")
cfg = {"positivity": {"cut_k": {"pERK": 3.5, "*": 2.5}}}
chk("a marker of its own", LO.cut_k(cfg, "pERK"), 3.5)
chk("another marker takes \"*\"", LO.cut_k(cfg, "PCNA"), 2.5)
chk("no positivity block: 3.0", LO.cut_k({}, "pERK"), 3.0)
chk("no marker: \"*\"", LO.cut_k(cfg, None), 2.5)
chk("a map without \"*\": the constant for other markers",
    LO.cut_k({"positivity": {"cut_k": {"pERK": 3.5}}}, "PCNA"), 3.0)
chk("an empty map: the constant", LO.cut_k({"positivity": {"cut_k": {}}}, "pERK"), 3.0)
chk("a None config: the constant", LO.cut_k(None, "pERK"), 3.0)
chk("the constant is still 3.0", LO.CUT_K, 3.0)
chk("a float, whatever the map holds", type(LO.cut_k({"positivity": {"cut_k": {"*": 4}}}, "x")),
    float)

print("--- section_cut_default_unchanged ---")
v = [100.0] * 10 + [110.0] * 5
m, s, _ = LO.background_stats(v)
chk("no k is k=3.0", LO.section_cut(v) == LO.section_cut(v, 3.0), True)
chk("...and the old formula", LO.section_cut(v), m + 3.0 * s)
w = [80.0, 90.0, 100.0, 110.0, 120.0, 131.0, 97.5]
m, s, _ = LO.background_stats(w)
chk("with a spread: the old formula, bit for bit", LO.section_cut(w), m + 3.0 * s)
chk("k=4: median + 4 spreads", LO.section_cut(w, 4.0), m + 4.0 * s)
chk("k=2.5 is below k=3", LO.section_cut(w, 2.5) < LO.section_cut(w), True)
chk("fewer than 5 values: still none, whatever k", LO.section_cut([1.0, 2.0, 3.0, 4.0], 4.0),
    None)

print("--- validate_cut_k ---")
_tmp = tempfile.mkdtemp()
with open(write_study(os.path.join(_tmp, "v")), encoding="utf-8") as fh:
    GOOD = json.load(fh)


def problems(positivity):
    c = copy.deepcopy(GOOD)
    c["positivity"] = positivity
    errors, _w = LC.validate(c)
    return [e for e in errors if "positivity" in e]


chk("the study config is valid as it is", LC.validate(GOOD)[0], [])
chk("the template carries the defaults",
    {k: v for k, v in GOOD.get("positivity", {}).items() if not k.startswith("_")},
    {"fp_target": 0.01, "cut_k": {"*": 3.0}, "fp_min_nuclei": 50})
chk("the defaults are", (LC.BY_PATH["positivity.fp_target"].default,
                         LC.BY_PATH["positivity.cut_k"].default,
                         LC.BY_PATH["positivity.cut_k_source"].default,
                         LC.BY_PATH["positivity.fp_min_nuclei"].default),
    (0.01, {"*": 3.0}, {"by": "default"}, 50))
chk("a per-marker map is accepted", problems({"cut_k": {"pERK": 3.5, "*": 2.5}}), [])
chk("1.0 and 10.0 are accepted", problems({"cut_k": {"pERK": 1.0, "PCNA": 10}}), [])
chk("fp_target 0.001 and 0.2 are accepted",
    [problems({"fp_target": 0.001}), problems({"fp_target": 0.2})], [[], []])
for label, pos in (("k 0.5", {"cut_k": {"pERK": 0.5}}),
                   ("k 11", {"cut_k": {"pERK": 11}}),
                   ("a bare number", {"cut_k": 3.0}),
                   ("a list", {"cut_k": [3.0]}),
                   ("a bool", {"cut_k": {"pERK": True}}),
                   ("text", {"cut_k": {"pERK": "3"}}),
                   ("an empty marker name", {"cut_k": {"": 3.0}})):
    got = problems(pos)
    chk(f"cut_k {label} is refused, naming the key",
        bool(got) and all("positivity.cut_k" in e for e in got), True)
for label, pos in (("0.5", {"fp_target": 0.5}), ("0.0005", {"fp_target": 0.0005})):
    got = problems(pos)
    chk(f"fp_target {label} is refused, naming the key",
        bool(got) and all("positivity.fp_target" in e for e in got), True)
got = problems({"fp_min_nuclei": 0})
chk("fp_min_nuclei 0 is refused", bool(got) and "positivity.fp_min_nuclei" in got[0], True)
chk("fp_min_nuclei 1 is accepted", problems({"fp_min_nuclei": 1}), [])
chk("cut_k_source is a record, not checked", problems({"cut_k_source": {"by": "operator"}}), [])
chk("the mad_k note no longer links the two",
    ("separate" in LC.BY_PATH["detection.threshold.mad_k"].note.lower()
     and "same multiplier 06a uses" not in LC.BY_PATH["detection.threshold.mad_k"].note), True)

print("--- no_controls_identical: 06a's outputs against the base's ---")


def run_gen(scripts, mode):
    out = tempfile.mkdtemp(prefix=f"cutk_{mode}_")
    env = dict(os.environ)
    env.pop("LS_CONFIG", None)
    p = subprocess.run([sys.executable, GEN, scripts, out, mode], env=env,
                       capture_output=True, text=True)
    if p.returncode != 0:
        print(p.stdout[-2000:], p.stderr[-2000:])
        return None, out
    return json.loads(p.stdout.strip().splitlines()[-1]), out


def same_files(a, b):
    names = sorted(os.listdir(a))
    if names != sorted(os.listdir(b)):
        return f"different files: {names} vs {sorted(os.listdir(b))}"
    diff = [n for n in names if open(os.path.join(a, n), "rb").read()
            != open(os.path.join(b, n), "rb").read()]
    return diff


GOLD_FILES = sorted(os.listdir(GOLDEN)) if os.path.isdir(GOLDEN) else []
chk("the golden files are there", GOLD_FILES,
    sorted(f"{fx}__{f}" for fx in ("main", "two_marker")
           for f in ("roi_measurements.csv", "positivity_cuts.csv",
                     "detector_specificity.csv")))
chk("...and hold S1's cut and S3's leave-one-disc-out rate",
    ("S1,AF568,144.478" in open(os.path.join(GOLDEN, "main__positivity_cuts.csv")).read(),
     ",2,12,0.1667," in open(os.path.join(GOLDEN, "main__detector_specificity.csv")).read()),
    (True, True))

info, now = run_gen(SCRIPTS, "none")
chk("this code ran, from this checkout",
    (info or {}).get("ls_overlay"), os.path.join(SCRIPTS, "ls_overlay.py"))
chk("...on a config with no positivity block", (info or {}).get("config", "?"), None)
chk("no positivity block: byte-identical to the base", same_files(now, GOLDEN), [])
info, expl = run_gen(SCRIPTS, "explicit")
chk("the default written out: byte-identical to the base", same_files(expl, GOLDEN), [])
chk("...on a config that says {\"*\": 3.0}", (info or {}).get("config"), {"cut_k": {"*": 3.0}})
info, k4 = run_gen(SCRIPTS, "k4")
cuts4 = open(os.path.join(k4, "main__positivity_cuts.csv")).read()
chk("AF568 at k=4: S1's cut is median + 4 spreads",
    f"S1,AF568,{LO.section_cut([80.0, 90.0, 100.0, 110.0, 120.0], 4.0)!r}" in cuts4, True)
chk("...the per-section rows change", same_files(k4, GOLDEN) != [], True)
two4 = open(os.path.join(k4, "two_marker__positivity_cuts.csv")).read().splitlines()
chk("...and AF488 keeps 3.0 (no \"*\")", "M2,AF488,144.478" in two4, True)
spec_gold = open(os.path.join(GOLDEN, "main__detector_specificity.csv")).read().splitlines()
spec4 = open(os.path.join(k4, "main__detector_specificity.csv")).read().splitlines()
S3_DISCS = [[90.0, 95.0, 100.0, 105.0, 110.0, 150.0],
            [85.0, 100.0, 102.0, 108.0, 112.0, 140.0]]


def loo(k):
    """Leave-one-disc-out false positives over S3's two discs, at k, by hand."""
    fp = 0
    for i, disc in enumerate(S3_DISCS):
        cut = LO.section_cut([x for j, d in enumerate(S3_DISCS) if j != i for x in d], k)
        fp += sum(1 for x in disc if LO.is_positive(x, False, cut))
    return fp


s3g = [r for r in spec_gold if r.startswith("S3,")][0].split(",")
s34 = [r for r in spec4 if r.startswith("S3,")][0].split(",")
# columns 9,10: loo_false_positives, loo_nuclei
chk("the k=3 and k=4 leave-one-disc-out counts differ (else the check is empty)",
    loo(3.0) != loo(4.0), True)
chk("...and 06a's is formed at the marker's k",
    (s3g[9], s34[9], s34[10]), (str(loo(3.0)), str(loo(4.0)), "12"))

# The live comparison: the committed files are still what the base writes.
reach = subprocess.run(["git", "-C", REPO, "cat-file", "-e", BASE + "^{commit}"],
                       capture_output=True).returncode == 0
if reach:
    wt_root = tempfile.mkdtemp(prefix="cutk_base_")
    wt = os.path.join(wt_root, "wt")
    try:
        add = subprocess.run(["git", "-C", REPO, "worktree", "add", "--detach", wt, BASE],
                             capture_output=True, text=True)
        chk(f"a worktree of {BASE}", add.returncode, 0)
        info, base = run_gen(os.path.join(wt, "scripts"), "none")
        chk("...ran the base's code",
            ((info or {}).get("ls_overlay", "").startswith(wt),
             (info or {}).get("section_cut_takes_k")), (True, False))
        chk("the golden files are what the base writes today", same_files(base, GOLDEN), [])
        chk("...and what this code writes", same_files(base, now), [])
    finally:
        subprocess.run(["git", "-C", REPO, "worktree", "remove", "--force", wt],
                       capture_output=True)
        shutil.rmtree(wt_root, ignore_errors=True)
    print(f"     (live comparison with {BASE} ran)")
else:
    print(f"     ({BASE} is not in this clone - a shallow checkout - so only the "
          f"committed golden files were compared)")
for d in (now, expl, k4):
    shutil.rmtree(d, ignore_errors=True)

print("--- every cut 06a, 05c and the reagent report form uses the marker's k ---")
G6A = load("g6a_cutk", "06a_roi_dataset.py")
BG = [80.0, 90.0, 100.0, 110.0, 120.0]


class _D:
    def __init__(self, vals):
        self.v, self.cen = list(vals), [0] * len(vals)


discs = [_D(d) for d in S3_DISCS]
chk("leave_one_disc_out: no k is the old rate", G6A.leave_one_disc_out(discs), (loo(3.0), 12))
chk("...k=3.0 the same", G6A.leave_one_disc_out(discs, k=3.0), (loo(3.0), 12))
chk("...k=6 calls fewer", G6A.leave_one_disc_out(discs, k=6.0), (loo(6.0), 12))
chk("...(and fewer it is)", loo(6.0) < loo(3.0), True)


class _RR:
    """ls_reagent_report with the pairing and the rows stubbed: only the
    control's own cut, which 06a forms, is under test."""
    def __getattr__(self, name):
        return getattr(_keep_rr, name)

    @staticmethod
    def study_rows(_cfg):
        return []

    @staticmethod
    def pairing_for(*_a):
        return ("same_file", [])

    @staticmethod
    def not_measured_rows(*_a):
        return []

    @staticmethod
    def report_rows(*_a, **_k):
        return []


_keep_rr, _keep_pos = G6A.RR, G6A.CONFIG.get("positivity")
try:
    G6A.RR = _RR()
    key = ("C1", "AF568")
    call = dict(lookup={key: {"kind": "no_primary"}}, rc_nuc={}, rc_bg={key: BG}, areas={},
                by_idx={}, cuts={}, measured={key})
    G6A.CONFIG["positivity"] = {"cut_k": {"*": 3.0}}
    chk("a reagent control's own cut, default", G6A.reagent_report(**call)[1],
        {key: LO.section_cut(BG)})
    G6A.CONFIG["positivity"] = {"cut_k": {"AF568": 4.0, "*": 2.0}}
    chk("...at its marker's k", G6A.reagent_report(**call)[1],
        {key: LO.section_cut(BG, 4.0)})
finally:
    G6A.RR = _keep_rr
    if _keep_pos is None:
        G6A.CONFIG.pop("positivity", None)
    else:
        G6A.CONFIG["positivity"] = _keep_pos

G5C = load("g5c_cutk", "05c_detect_rois.py")
rows = [{"roi_kind": "background", "censored": "0", "artifact": "0", "marker_median": str(x)}
        for x in BG]
_keep_pos = G5C.CONFIG.get("positivity")
try:
    G5C.CONFIG.pop("positivity", None)
    chk("05c's crop cut, no positivity block: the old cut",
        G5C.crop_cut(rows, "AF568"), LO.section_cut(BG))
    G5C.CONFIG["positivity"] = {"cut_k": {"AF568": 4.0}}
    chk("...at its marker's k", G5C.crop_cut(rows, "AF568"), LO.section_cut(BG, 4.0))
    chk("...and another marker's", G5C.crop_cut(rows, "AF488"), LO.section_cut(BG))
finally:
    if _keep_pos is None:
        G5C.CONFIG.pop("positivity", None)
    else:
        G5C.CONFIG["positivity"] = _keep_pos

print("--- stages_track_cut_k ---")
import ls_paths                                                  # noqa: E402
import stages as S                                               # noqa: E402
import workflow as W                                             # noqa: E402

with open(STUDY.path, encoding="utf-8") as fh:
    CFG = json.load(fh)
CFG.pop("positivity", None)


def study(cfg):
    markers = cfg.get("acquisition", {}).get("markers") or ["AF568", "AF488"]
    names = ls_paths.Names(cfg["out_root"], markers, cfg.get("acquisition", {}).get("layout"))
    return W.Study(cfg["out_root"], names, cfg)


def _outputs_of(sid, s):
    st = S.BY_ID[sid]
    return [st._alternatives(e, s.out_root, s.names)[1][0] for e in st.outputs]


def _put(path, text, when):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)
    os.utime(path, (when, when))


T0 = max(t for t, _why in W.FIXED_AT.values()) + 86400
_s = study(CFG)
_put(os.path.join(_s.export_root, "roi_regions.csv"), "uid,roi\nA,1\n", T0)
TRACKED = ["roi_curator", "tune_detection", "roi_geometry", "detect", "roi_dataset",
           "join_sampling", "build_results", "detection_overlays"]
for i, sid in enumerate(TRACKED):
    for p in _outputs_of(sid, _s):
        _put(p, sid + "\n", T0 + 10 * (i + 1))
_det_cli = S.BY_ID["detect"].cli_only
S.BY_ID["detect"].cli_only = None
try:
    rec = json.loads(json.dumps(CFG))
    runs = rec.setdefault("project", {}).setdefault("runs", [])
    for sid in TRACKED:
        runs.append({"stage": sid, "ok": True, "inputs": W.fingerprint(sid, study(rec))})
    WATCH = ["detect", "roi_dataset", "build_results", "detection_overlays"]

    def why(cfg):
        return {sid: W.outdated(S.BY_ID[sid], study(cfg)) for sid in WATCH}

    chk("recorded with no positivity block: nothing out of date", why(rec),
        dict.fromkeys(WATCH))
    c2 = json.loads(json.dumps(rec))
    c2["positivity"] = {"cut_k": {"AF568": 3.5, "*": 3.0}}
    got = why(c2)
    chk("a new cut_k puts 06a out of date", bool(got["roi_dataset"]), True)
    chk("...and the results build", bool(got["build_results"]), True)
    chk("...and 06i", bool(got["detection_overlays"]), True)
    chk("...and NOT 05c (it only colours its crops by it)", got["detect"], None)
    chk("...05c does not list it", "positivity.cut_k" in S.BY_ID["detect"].settings, False)
    chk("...and says why it is not tracked", "positivity.cut_k" in W.NOT_TRACKED, True)
    c3 = json.loads(json.dumps(rec))
    c3["positivity"] = {"cut_k": {"*": 3.0}, "fp_target": 0.01, "fp_min_nuclei": 50,
                        "cut_k_source": {"by": "default"}}
    chk("the defaults written out (Set up saves them) change nothing", why(c3),
        dict.fromkeys(WATCH))
    for _leaf, _v in (("fp_target", 0.02), ("fp_min_nuclei", 80)):
        c5 = json.loads(json.dumps(rec))
        c5["positivity"] = {_leaf: _v}
        got = why(c5)
        chk(f"a new {_leaf} puts 06a and the results build out of date, not 05c",
            (bool(got["roi_dataset"]), bool(got["build_results"]), got["detect"]),
            (True, True, None))
    c4 = json.loads(json.dumps(rec))
    c4["positivity"] = {"cut_k": {"AF568": 3.0, "AF488": 3}}
    chk("...nor does every marker at 3.0", why(c4), dict.fromkeys(WATCH))
    # And a run recorded WITH a cut_k, set back to the default.
    rec2 = json.loads(json.dumps(c2))
    for r in rec2["project"]["runs"]:
        r["inputs"] = W.fingerprint(r["stage"], study(rec2))
    chk("recorded at 3.5: current", why(rec2), dict.fromkeys(WATCH))
    rec2.pop("positivity")
    chk("...and back to the default puts 06a out of date again",
        bool(why(rec2)["roi_dataset"]), True)
finally:
    S.BY_ID["detect"].cli_only = _det_cli

print("--- Set up shows them ---")
det_card = [paths for sid, _t, _i, paths in W.SETTING_SECTIONS if sid == "detection"][0]
chk("fp_target is in Detection settings", "positivity.fp_target" in det_card, True)
chk("fp_target is a number field", LC.BY_PATH["positivity.fp_target"].ui, "number")
chk("cut_k is not a one-line field", LC.BY_PATH["positivity.cut_k"].ui, None)
chk("the per-marker table: \"*\" first, then each marker named",
    W.cut_k_rows({"positivity": {"cut_k": {"pERK": 3.5, "*": 2.5}},
                  "acquisition": {"layout": "paired", "markers": ["pERK", "PCNA"]}}),
    [("every marker (*)", 2.5), ("pERK", 3.5), ("PCNA", 2.5)])
chk("...with no positivity block, 3.0 throughout",
    W.cut_k_rows({"acquisition": {"layout": "paired", "markers": ["AF568"]}}),
    [("every marker (*)", 3.0), ("AF568", 3.0)])
chk("...a map entry for a marker the study lacks is still shown",
    W.cut_k_rows({"positivity": {"cut_k": {"old": 4.0}}, "acquisition": {"layout": "paired", "markers": []}}),
    [("every marker (*)", 3.0), ("old", 4.0)])

shutil.rmtree(_tmp, ignore_errors=True)
print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("all passed")
