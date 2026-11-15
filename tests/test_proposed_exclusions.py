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

print()
print("FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
