"""00_manifest and 02_pair_passes know about reagent controls.

A reagent control file is often named with no subject (`NC_3.czi`), so the
study's grammar does not read it. Before, 00 failed the whole run on it. Now a
`reagent_controls.files` entry with a subject gives it the standard uid shape,
the manifest carries a `reagent_control` column (display only: exclusion goes
through ls_reagent_controls.expand on config), a control is numbered in a
series of its own so it cannot shift its donor animal's section_order, 02
never picks a control as a slide's scan, and a name that looks like a control
but was never answered is a warning, not a failure.

The headers are real CZI metadata blocks written by the helpers in
test_autodetect.py; only the scene list is added on top, since those headers
carry none.

Run:  python tests/test_reagent_manifest.py
"""

import contextlib
import csv
import glob
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(REPO, "scripts"), HERE]

from _fixture import use_temp_study, load_stage  # noqa: E402

# Subject must carry digits, so `NC_3` does not parse; the replicate may be
# several letters (`3nc`); a name with no slide (`LS45_noPrimary`) still parses.
NAMING = {"pattern": r"^(?P<subject>[A-Za-z]+\d+)(?:_(?P<slide>\d+)(?P<replicate>[a-z]*))?"
                     r"(?P<rest>_[A-Za-z]+)?\.czi$",
          "case_insensitive": True}
STUDY = use_temp_study(
    slide_naming=NAMING, pixel_size_um=0.65,
    acquisition={"layout": "paired", "markers": ["AF568", "AF488"]},
    reagent_controls={"declared": "yes", "files": {
        "NC_3.czi": {"kind": "no_primary", "subject": "LS45", "slide": 3, "replicate": "nc"}}})
M = load_stage("00_manifest.py", name="lsstage_00_reagent")
P2 = load_stage("02_pair_passes.py", name="lsstage_02_reagent")
import ls_naming as NM  # noqa: E402

src = open(os.path.join(HERE, "test_autodetect.py")).read().split('print("--- one scan per marker')[0]
ns = {"__name__": "autodetect_helpers", "__file__": os.path.join(HERE, "test_autodetect.py")}
exec(compile(src, "test_autodetect_helpers", "exec"), ns)
xml, write_czi = ns["xml"], ns["write_czi"]
DAPI, AF568 = ns["DAPI"], ns["AF568"]

fails = 0


def chk(label, got, want=True):
    global fails
    ok = got == want
    if not ok:
        fails += 1
    print(("ok   " if ok else "FAIL ") + label.ljust(66) + f" {got!r}"
          + ("" if ok else f"   want {want!r}"))


# Scenes per file name; every file defaults to 2.
N_SCENES = {}
_read, _summ = M.read_metadata, M.summarise
M.read_metadata = lambda fh: (os.path.basename(fh.name), _read(fh))


def _summarise(tagged):
    name, root = tagged
    info = _summ(root)
    n = N_SCENES.get(name, 2)
    info["scenes"] = [{"index": i, "name": f"ScanRegion{i}", "center_x_um": 9000.0 * i,
                       "center_y_um": 0.0, "width_um": 7000.0, "height_um": 7000.0}
                      for i in range(1, n + 1)]
    info["size_s"] = n
    return info


M.summarise = _summarise


def run(files, rc):
    """Lay out `files` in the slides folder, set reagent_controls, run 00."""
    for p in glob.glob(os.path.join(M.MANIFEST_DIR, "manifest_*.csv")):
        os.remove(p)
    for p in glob.glob(os.path.join(STUDY.source_dir, "*.czi")):
        os.remove(p)
    for name in files:
        write_czi(os.path.join(STUDY.source_dir, name), xml([DAPI, AF568]))
    M.CONFIG["reagent_controls"] = rc
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = M.main() or 0
    return code, out.getvalue()


def read(name):
    """A manifest table, or [] when this run wrote none."""
    if not os.path.exists(os.path.join(M.MANIFEST_DIR, name)):
        return []
    with open(os.path.join(M.MANIFEST_DIR, name), newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


NC3 = {"kind": "no_primary", "subject": "LS45", "slide": 3, "replicate": "nc"}

print("--- nonparsing_with_subject ---")
code, out = run(["LS45_1a.czi", "NC_3.czi"], {"declared": "yes", "files": {"NC_3.czi": NC3}})
chk("the run is written", code, 0)
chk("NC_3.czi is not a failure", "NC_3.czi" in out and "did not match" in out, False)
scenes = read("manifest_scenes.csv")
nc = [r for r in scenes if r["file"] == "NC_3.czi"]
want_uid = NM.scene_uid({"subject": "LS45", "slide": 3, "replicate": "nc"}, 1)
chk("its uid is the standard shape from the files entry", nc[0]["scene_uid"] if nc else None,
    want_uid)
chk("...which is LS45_s03nc_sc01", want_uid, "LS45_s03nc_sc01")
chk("its scenes say reagent_control no_primary", sorted({r["reagent_control"] for r in nc}),
    ["no_primary"])
chk("a stained scene says ''",
    sorted({r["reagent_control"] for r in scenes if r["file"] == "LS45_1a.czi"}), [""])
files = {r["file"]: r for r in read("manifest_files.csv")}
chk("manifest_files carries the kind", files.get("NC_3.czi", {}).get("reagent_control"), "no_primary")
chk("...and '' for a stained file", files.get("LS45_1a.czi", {}).get("reagent_control"), "")
chk("the control's animal is the donor", nc[0]["animal"] if nc else None, "LS45")

print("--- nonparsing_without_subject ---")
code, out = run(["LS45_1a.czi", "NC_3.czi"], {"declared": "no"})
chk("the run fails", code, 1)
chk("NC_3.czi is a 'filename did not match' failure",
    "NC_3.czi" in out and "filename did not match" in out, True)
code, out = run(["LS45_1a.czi", "NC_3.czi"],
                {"declared": "yes", "files": {"NC_3.czi": {"kind": "no_primary"}}})
chk("an entry without a subject does not rescue it", code, 1)

print("--- two_controls_no_collision ---")
rc = {"declared": "yes", "files": {
    "NC_3.czi": NC3,
    "NC_4.czi": {"kind": "no_primary", "subject": "LS45", "slide": 4, "replicate": "nc"}}}
code, out = run(["LS45_1a.czi", "NC_3.czi", "NC_4.czi"], rc)
chk("the run is written", code, 0)
scenes = read("manifest_scenes.csv")
chk("no duplicate uids", M.duplicate_uids(scenes), {})
chk("both controls are in the manifest",
    sorted({r["file"] for r in scenes if r["reagent_control"]}), ["NC_3.czi", "NC_4.czi"])

print("--- parsed_control_lookup ---")
code, out = run(["LS45_1a.czi", "LS45_3nc.czi"],
                {"declared": "yes", "files": {"LS45_3nc.czi": {"kind": "isotype"}}})
chk("the run is written", code, 0)
scenes = read("manifest_scenes.csv")
chk("a control whose name parses is flagged",
    sorted({r["reagent_control"] for r in scenes if r["file"] == "LS45_3nc.czi"}), ["isotype"])
chk("...under its own parsed uid",
    sorted(r["scene_uid"] for r in scenes if r["file"] == "LS45_3nc.czi"),
    ["LS45_s03nc_sc01", "LS45_s03nc_sc02"])

print("--- own_series ---")
N_SCENES.update({"LS45_1a.czi": 3, "LS45_2a.czi": 2, "LS45_3nc.czi": 4, "LS45_2nc.czi": 5})
code, out = run(["LS45_1a.czi", "LS45_2a.czi", "LS45_3nc.czi"],
                {"declared": "yes", "files": {"LS45_3nc.czi": {"kind": "no_primary"}}})
chk("the run is written", code, 0)
scenes = read("manifest_scenes.csv")
stained = sorted(int(r["section_order"]) for r in scenes if not r["reagent_control"])
chk("stained sections run 1..n over 1a and 2a only", stained, [1, 2, 3, 4, 5])
ctrl = sorted(int(r["section_order"]) for r in scenes if r["reagent_control"])
chk("the control is numbered in its own series", ctrl, [1, 2, 3, 4])
# The case the block offset breaks: a control on a slide that also has a
# stained scan, with more scenes than it. In the stained series it would be
# slide 2's pick, and slide 2's block would be 5 places long, not 2.
code, out = run(["LS45_1a.czi", "LS45_2a.czi", "LS45_2nc.czi", "LS45_3a.czi"],
                {"declared": "yes", "files": {"LS45_2nc.czi": {"kind": "no_primary"}}})
scenes = read("manifest_scenes.csv")
stained = sorted(int(r["section_order"]) for r in scenes if not r["reagent_control"])
chk("a control sharing a slide does not size that slide's block", stained,
    [1, 2, 3, 4, 5, 6, 7])
ctrl = sorted(int(r["section_order"]) for r in scenes if r["reagent_control"])
chk("...and is numbered 1..k on its own", ctrl, [1, 2, 3, 4, 5])
N_SCENES.clear()

print("--- assign_section_order with rows that carry no flag column ---")
rows = [{"animal": "LS1", "marker_channel": "AF568", "slide": 1, "variant": "a",
         "serial_x": i} for i in (1, 2)]
M.assign_section_order(rows, "serial_x")
chk("old rows still number", [r["section_order"] for r in rows], [1, 2])

print("--- never_chosen_variant ---")
P2.CONFIG["reagent_controls"] = {"declared": "yes",
                                 "files": {"LS45_3nc.czi": {"kind": "no_primary"}}}


def srows(file, variant, n):
    return [{"scene_uid": f"LS45_s03{variant}_sc{i:02d}", "file": file, "animal": "LS45",
             "slide": 3, "variant": variant, "marker_channel": "AF568"}
            for i in range(1, n + 1)]


rows = srows("LS45_3a.czi", "a", 4) + srows("LS45_3nc.czi", "nc", 6)
with contextlib.redirect_stdout(io.StringIO()):
    chosen, notes = P2.choose_variants(rows)
picked = chosen.get(("LS45", 3, "AF568"), [])
chk("the stained scan is chosen over a control with more scenes",
    sorted({r["variant"] for r in picked}), ["a"])
rows = srows("LS45_3nc.czi", "nc", 6)
with contextlib.redirect_stdout(io.StringIO()):
    chosen, notes = P2.choose_variants(rows)
chk("a slide whose only scan is a control keeps its own entry",
    len(chosen.get(("LS45", 3, "AF568"), [])), 6)
P2.CONFIG["reagent_controls"] = {"declared": "no"}
rows = srows("LS45_3a.czi", "a", 4) + srows("LS45_3nc.czi", "nc", 6)
with contextlib.redirect_stdout(io.StringIO()):
    chosen, notes = P2.choose_variants(rows)
chk("with nothing confirmed, most scenes still wins",
    sorted({r["variant"] for r in chosen[("LS45", 3, "AF568")]}), ["nc"])

print("--- unconfirmed_warns ---")
code, out = run(["LS45_1a.czi", "LS45_noPrimary.czi"], {"declared": "no"})
chk("the run is written (a warning, not a failure)", code, 0)
warn = [ln for ln in out.splitlines() if "reagent control" in ln and "LS45_noPrimary.czi" in ln]
chk("a line names the file and says reagent control", len(warn) >= 1, True)
code, out = run(["LS45_1a.czi", "LS45_noPrimary.czi"],
                {"declared": "no", "rejected": ["LS45_noPrimary.czi"]})
chk("a rejected name is not warned about",
    any("reagent control" in ln and "LS45_noPrimary.czi" in ln for ln in out.splitlines()),
    False)
code, out = run(["LS45_1a.czi", "NC_9.czi", "LS45_noPrimary.czi"], {"declared": "no"})
chk("the warning is printed on a failed run too",
    any("reagent control" in ln and "LS45_noPrimary.czi" in ln for ln in out.splitlines()),
    True)

print()
print("ALL OK" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
