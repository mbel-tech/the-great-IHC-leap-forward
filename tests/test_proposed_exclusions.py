"""With the rotation curator gone, 04f's proposals are the source pass's
exclusions - but only for a study that never had a rotation_overrides.csv.

A study curated in 04d keeps its file, and the file stays the complete record:
re-running 04f there must not exclude a section somebody already placed ROIs
on, and its hand rotations must still reach the frame unchanged.

Run:  python tests/test_proposed_exclusions.py
"""

import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from _fixture import temp_study, load_stage  # noqa: E402

fails = 0


def chk(label, got, want=True):
    global fails
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label:62} {'' if ok else f'{got!r} != {want!r}'}")
    if not ok:
        fails += 1


def write(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


PAIRED = {"acquisition": {"layout": "paired", "channels": [],
                          "markers": ["AF568", "AF488"]}}

with temp_study(**PAIRED):
    RF = load_stage("04a_reformat.py")
    pcna, perk = RF.marker_paths("AF488"), RF.marker_paths("AF568")
    uids = [f"LS01_s01b_sc{i:02d}" for i in range(4)]
    write(RF.CANDIDATES_CSV, [
        {"uid": uids[0], "proposed": "1", "reason": "no tissue"},
        {"uid": uids[1], "proposed": "0", "reason": ""},
        {"uid": uids[2], "proposed": "1", "reason": "out of focus"}])

    print("--- a new study: no override file ---")
    rot, exc = RF.load_overrides(pcna)
    chk("no rotations", rot, {})
    chk("the 04f proposals are the exclusions", sorted(exc), [uids[0], uids[2]])
    chk("...recorded as automatic, with 04f's reason", exc[uids[2]], ("auto", "out of focus"))
    rot, exc = RF.load_overrides(perk)
    chk("a pERK pass takes nothing from 04f", (rot, exc), ({}, {}))

    write(RF.REVIEW_CSV, [{"scene_uid": uids[0], "marker": "AF488",
                           "decision": "restored", "excluded": "0",
                           "mask_rejected": "0", "reason": ""}])
    _, exc = RF.load_overrides(pcna)
    exc, _ = RF.apply_review(exc, "AF488")
    chk("Review mode reinstates a proposal", sorted(exc), [uids[2]])
    os.remove(RF.REVIEW_CSV)

    print()
    print("--- a study curated in the old rotation curator ---")
    write(pcna["overrides"], [
        {"scene_uid": uids[1], "extra_rotation": "12.5", "flip": "1", "excluded": "0",
         "rotation_source": "manual", "decision": "", "reason": ""},
        {"scene_uid": uids[2], "extra_rotation": "0", "flip": "0", "excluded": "0",
         "rotation_source": "", "decision": "restored", "reason": ""}])
    rot, exc = RF.load_overrides(pcna)
    chk("hand rotations reach the frame unchanged", rot, {uids[1]: (12.5, True)})
    chk("04f is not consulted: nothing newly excluded", exc, {})

print()
print("--- 04f re-run after a final reformat ---")
# A study with no override file takes its exclusions from 04f's proposals. The
# final `04a --apply-overrides` then leaves those sections out of
# reformat_index.csv - and 04f only walked that index, so a re-run of 04f
# dropped them from exclusion_candidates.csv and the NEXT final reformat put
# them silently back into the analysis. A section already excluded and absent
# from the index is carried through unchanged.
import contextlib  # noqa: E402
import io  # noqa: E402

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

with temp_study(**PAIRED):
    F = load_stage("04f_exclusion_candidates.py")
    RF = F._RF
    src = RF.marker_paths(RF.DEFAULT_MARKER)
    kept, gone, stale = "LS01_s01b_sc01", "LS01_s01b_sc02", "LS01_s01b_sc03"
    write(F.QC_CSV, [{"scene_uid": u, "animal": "LS01",
                      "marker_channel": RF.DEFAULT_MARKER, "focus_score": "0.2"}
                     for u in (kept, gone, stale)])
    # One real overview, so the run has something to measure: a big bright
    # disc on a dark field, well over the tissue threshold.
    yy, xx = np.mgrid[:600, :600]
    img = np.where((yy - 300) ** 2 + (xx - 300) ** 2 < 220 ** 2, 180, 8).astype(np.uint8)
    d = os.path.join(F.OVERVIEW_DIR, "LS01", RF.DEFAULT_MARKER)
    os.makedirs(d, exist_ok=True)
    Image.fromarray(img).save(os.path.join(d, kept + "_DAPI.png"))
    write(src["index"], [{"kind": "section", "id": kept, "animal": "LS01",
                          "section_order": "1", "angle": "0", "fill": "0.5"}])
    write(src["excluded"], [{"scene_uid": gone, "decision": "auto",
                             "reason": "no tissue piece larger than 0.40 mm2"}])
    cand_cols = ["uid", "animal", "section_order", "proposed", "artifact_class",
                 "reason", "largest_mm2", "total_mm2", "n_pieces", "focus_score",
                 "frame_mm2"]
    prior_gone = dict(zip(cand_cols, [gone, "LS01", "2", "1", "no_tissue",
                                      "no tissue piece larger than 0.40 mm2",
                                      "0.4", "0.6", "0", "0.2", "9.7"]))
    # `stale` was measured once, never excluded, and is no longer indexed:
    # nothing says it is excluded, so nothing carries it.
    prior_stale = dict(zip(cand_cols, [stale, "LS01", "3", "0", "", "",
                                       "5.0", "6.0", "1", "0.2", "9.7"]))
    write(F.CANDIDATES_CSV, [prior_gone, prior_stale])

    saved = sys.argv
    out = io.StringIO()
    try:
        sys.argv = ["04f", "--montage", "0"]
        with contextlib.redirect_stdout(out):
            F.main()
    finally:
        sys.argv = saved
    with open(F.CANDIDATES_CSV, newline="", encoding="utf-8") as fh:
        got = {r["uid"]: r for r in csv.DictReader(fh)}
    chk("the indexed section is measured", kept in got)
    chk("an excluded, unindexed section is carried, not dropped", gone in got)
    chk("...unchanged", got.get(gone), prior_gone)
    chk("a section neither indexed nor excluded is not carried", stale in got, False)
    chk("the run says how many it carried",
        "carried 1 already-excluded section(s)" in out.getvalue())

    # A missing file is the ordinary case and says nothing; one that exists
    # and cannot be read used to be the same silent [] - and then the excluded
    # sections this exists to keep were dropped without a word.
    keep_ex = F.EXCLUDED_CSV
    try:
        F.EXCLUDED_CSV = os.path.join(os.path.dirname(keep_ex), "no_such_file.csv")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            got_c = F.carry_excluded(set())
        chk("a missing exclusions file carries nothing, quietly",
            (got_c, out.getvalue()), ([], ""))
        F.EXCLUDED_CSV = os.path.join(os.path.dirname(keep_ex), "a_directory")
        os.makedirs(F.EXCLUDED_CSV, exist_ok=True)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            got_c = F.carry_excluded(set())
        chk("an exclusions file that cannot be read carries nothing...", got_c, [])
        chk("...and says why", "could not read" in out.getvalue()
            and "a_directory" in out.getvalue())
    finally:
        F.EXCLUDED_CSV = keep_ex

print()
print("--- 04f: areas at each section's own scale; an unmeasured section fails the run ---")
# The areas used to be converted with the LS study's 5.20 um/px as a literal,
# so a study exported at another overview scale had every area gate in the
# wrong place. And a section whose overview could not be read was a silent
# `continue`: never measured, never proposed, and the stage showed done.
with temp_study(**PAIRED):
    F = load_stage("04f_exclusion_candidates.py", name="lsstage_04f_scale")
    RF = F._RF
    src = RF.marker_paths(RF.DEFAULT_MARKER)
    at5, at10, absent = "LS02_s01b_sc01", "LS02_s01b_sc02", "LS02_s01b_sc03"
    write(F.QC_CSV, [{"scene_uid": u, "animal": "LS02",
                      "marker_channel": RF.DEFAULT_MARKER, "focus_score": "0.2",
                      "um_px": um}
                     for u, um in ((at5, "5.20"), (at10, "10.40"), (absent, "5.20"))])
    yy, xx = np.mgrid[:600, :600]
    img = np.where((yy - 300) ** 2 + (xx - 300) ** 2 < 220 ** 2, 180, 8).astype(np.uint8)
    d = os.path.join(F.OVERVIEW_DIR, "LS02", RF.DEFAULT_MARKER)
    os.makedirs(d, exist_ok=True)
    for u in (at5, at10):
        Image.fromarray(img).save(os.path.join(d, u + "_DAPI.png"))
    write(src["index"], [{"kind": "section", "id": u, "animal": "LS02",
                          "section_order": str(i), "angle": "0", "fill": "0.5"}
                         for i, u in enumerate((at5, at10, absent))])
    saved = sys.argv
    out = io.StringIO()
    try:
        sys.argv = ["04f", "--montage", "0"]
        with contextlib.redirect_stdout(out):
            rc = F.main()
    finally:
        sys.argv = saved
    with open(F.CANDIDATES_CSV, newline="", encoding="utf-8") as fh:
        got = {r["uid"]: r for r in csv.DictReader(fh)}
    a5, a10 = float(got[at5]["total_mm2"]), float(got[at10]["total_mm2"])
    chk("the same pixels at twice the um_px are four times the area",
        round(a10 / a5, 1), 4.0)
    chk("...the 5.20 um/px one measured at that scale (a 220 px disc ~ 4.1 mm2)",
        3.8 < a5 < 4.4)
    chk("a section whose overview is missing fails the run", rc, 1)
    said = [ln for ln in out.getvalue().splitlines() if ln.strip()]
    chk("...the reason is its last line",
        bool(said) and "1 section(s) NOT MEASURED" in said[-1])
    chk("...and the section is named", absent in out.getvalue())
    chk("the sections that were measured are still written", sorted(got), [at5, at10])

print()
print("FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
