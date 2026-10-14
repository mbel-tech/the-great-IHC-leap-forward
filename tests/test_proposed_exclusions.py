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
print("FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
