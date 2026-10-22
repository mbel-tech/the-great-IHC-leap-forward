"""04n builds the ROI queue for a multiplex study.

04n read only 04m's `sections_dataset.csv`, and 04m is paired-only: it joins two
passes of a section, and a multiplex scan has no partner pass. So on the
Contagion slides 04n died with a FileNotFoundError and the ROI curator, which
needs the worklist, could never be built. A multiplex queue is the sections the
marker's final reformat kept and 04j left in the analysis set, with no partner
columns and no unpaired tier.

    python tests/test_worklist_multiplex.py
"""

import contextlib
import csv
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from _fixture import use_temp_study, load_stage  # noqa: E402

STUDY = use_temp_study(
    acquisition={"layout": "multiplex", "markers": [], "channels": [
        {"name": "DAPI", "role": "nuclear", "czi_name": "DAPI", "index": 0},
        {"name": "pERK", "role": "marker", "czi_name": "AF568", "index": 1}]},
    display={"colours": {"pERK": "red"}})
M = load_stage("04n_roi_worklist.py")

fails = 0


def chk(label, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
    print(("ok   " if ok else "FAIL ") + label.ljust(62) + f" {got!r}"
          + ("" if ok else f"   want {want!r}"))


def write(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


# CD16: 12 sections, one excluded (not reformatted) and one censored out.
# PO16: 12 sections. AB1: 3 sections, too few to estimate from.
scenes = [(a, i) for a, n in (("CD16", 12), ("PO16", 12), ("AB1", 3)) for i in range(1, n + 1)]
uid = lambda a, i: f"{a}_s01a_sc{i:02d}"                                 # noqa: E731
write(M.MANIFEST_CSV,
      [{"scene_uid": uid(a, i), "animal": a, "marker_channel": "pERK",
        "section_order": i} for a, i in scenes])
write(M.index_csv(),
      [{"id": uid(a, i), "kind": "section"} for a, i in scenes if (a, i) != ("CD16", 5)]
      + [{"id": "plate_001", "kind": "plate"}])
write(M.NAMES.read("analysis_set", M.MARKER),
      [{"scene_uid": uid(a, i), "animal": a, "section_order": i,
        "in_analysis_set": 0 if (a, i) == ("CD16", 7) else 1}
       for a, i in scenes if (a, i) != ("CD16", 5)])

sys.argv = ["04n_roi_worklist.py"]
with contextlib.redirect_stdout(io.StringIO()):
    code = M.main()
chk("04n runs for a multiplex study", code or 0, 0)

with open(M.OUT_CSV, newline="", encoding="utf-8") as fh:
    rows = list(csv.DictReader(fh))
uids = [r["scene_uid"] for r in rows]
chk("every kept, uncensored section is queued once", len(uids), 25)
chk("...not the excluded one", uid("CD16", 5) in uids, False)
chk("...nor the censored one", uid("CD16", 7) in uids, False)
chk("the two estimable animals are core", sorted({r["animal"] for r in rows
                                                  if r["tier"] == "core"}), ["CD16", "PO16"])
chk("the small animal is held out, at the end",
    [r["tier"] for r in rows if r["animal"] == "AB1"], ["animal_underpowered"] * 3)
chk("no partner, so no unpaired tier", "unpaired_unchecked" in {r["tier"] for r in rows}, False)
chk("...and the partner columns are blank",
    {r["partner_scene_uid"] for r in rows} | {r["pair_confidence"] for r in rows}, {""})
chk("each animal's first two picks are its ends",
    [r["section_order"] for r in rows if r["animal"] == "PO16"][:2], ["1", "12"])

print("--- the stage table: a paired worklist waits for 04m ---")
sys.path[:0] = [os.path.join(os.path.dirname(HERE), "app")]
import stages as S                                              # noqa: E402
import workflow as W                                            # noqa: E402
import ls_paths                                                 # noqa: E402


def study(layout):
    cfg = {"acquisition": {"layout": layout}, "out_root": STUDY.root}
    return W.Study(STUDY.root, ls_paths.Names(STUDY.root, ["AF568", "AF488"], layout), cfg)


wl = S.BY_ID["worklist"]
chk("paired: 04n needs 04m, the table it reads",
    [n for n in wl.needs if W.applies(S.BY_ID[n], study("paired"))[0]
     and n == "sections_dataset"], ["sections_dataset"])
chk("multiplex: that need does not apply, so it holds nothing",
    W.applies(S.BY_ID["sections_dataset"], study("multiplex"))[0], False)

print()
print("ALL OK" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
