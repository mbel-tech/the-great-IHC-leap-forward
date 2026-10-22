"""04j decides which sections to check without a reformat pass in front of it.

It used to read the marker's reformat index, which made the operator reformat
every section once more just to produce a list. It now asks 04a's own loaders
for the exclusions - rotation overrides, then section_review.csv merged over
them - and takes every other scanned section of that marker. That is the rule
04a uses to decide who gets an index row, so the list must come out the same.

Run:  python tests/test_censor_membership.py
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


def focus(uids, marker):
    return [{"scene_uid": u, "animal": "LS01", "section_order": str(i),
             "marker_channel": marker} for i, u in enumerate(uids)]


PAIRED = {"acquisition": {"layout": "paired", "channels": [],
                          "markers": ["AF568", "AF488"]}}

print("--- paired: each marker's own exclusions ---")
with temp_study(**PAIRED) as study:
    J = load_stage("04j_censor_clipped.py")
    RF = J.RF
    pcna, perk = "AF488", "AF568"
    pcna_uids = [f"LS01_s01b_sc{i:02d}" for i in range(6)]
    perk_uids = [f"LS01_s01a_sc{i:02d}" for i in range(6)]
    rows = focus(pcna_uids, pcna) + focus(perk_uids, perk)

    # PCNA: 04d's export. One excluded by 04f and accepted, one by hand, one
    # rotated only.
    write(RF.marker_paths(pcna)["overrides"], [
        {"scene_uid": pcna_uids[1], "extra_rotation": "0", "flip": "0",
         "excluded": "1", "decision": "auto", "reason": "no tissue"},
        {"scene_uid": pcna_uids[2], "extra_rotation": "0", "flip": "0",
         "excluded": "1", "decision": "manual", "reason": "torn"},
        {"scene_uid": pcna_uids[3], "extra_rotation": "12", "flip": "1",
         "excluded": "0", "decision": "", "reason": ""}])
    # pERK: what 04i writes, keyed by its own column.
    write(RF.marker_paths(perk)["overrides"], [
        {"perk_scene_uid": perk_uids[2], "extra_rotation": "0", "flip": "0",
         "excluded": "1", "decision": "manual",
         "reason": "PCNA partner excluded by the operator"}])

    got = [u for u, _, _ in J.sections_to_check(rows, pcna)]
    chk("PCNA: every scanned section but the two excluded",
        got, [pcna_uids[i] for i in (0, 3, 4, 5)])
    chk("...and no pERK section in a PCNA run", any(u in perk_uids for u in got), False)
    got = [u for u, _, _ in J.sections_to_check(rows, perk)]
    chk("pERK: its own exclusion, read through perk_scene_uid",
        got, [perk_uids[i] for i in (0, 1, 3, 4, 5)])

    # The Review mode: reinstate one, drop another, and a row for the other
    # marker that must not leak across.
    write(RF.REVIEW_CSV, [
        {"scene_uid": pcna_uids[2], "marker": pcna, "decision": "restored",
         "excluded": "0", "mask_rejected": "0", "reason": ""},
        {"scene_uid": pcna_uids[5], "marker": pcna, "decision": "",
         "excluded": "1", "mask_rejected": "0", "reason": "bubble"},
        {"scene_uid": perk_uids[4], "marker": perk, "decision": "",
         "excluded": "1", "mask_rejected": "0", "reason": "fold"}])
    got = [u for u, _, _ in J.sections_to_check(rows, pcna)]
    chk("PCNA: section_review.csv merged over the overrides",
        got, [pcna_uids[i] for i in (0, 2, 3, 4)])
    got = [u for u, _, _ in J.sections_to_check(rows, perk)]
    chk("pERK: only its own review rows apply",
        got, [perk_uids[i] for i in (0, 1, 3, 5)])

    # The same rule 04a applies when it writes an index row: not excluded.
    _, exc = RF.load_overrides(RF.marker_paths(pcna))
    exc, _ = RF.apply_review(exc, pcna)
    index_rule = [r["scene_uid"] for r in RF.frames_for(rows, pcna)
                  if r["scene_uid"] not in exc]
    chk("the list is exactly the set 04a would index",
        [u for u, _, _ in J.sections_to_check(rows, pcna)], index_rule)
    chk("animal and section_order carried from focus.csv",
        J.sections_to_check(rows, pcna)[0], (pcna_uids[0], "LS01", "0"))

    print()
    print("--- 04g --include-excluded: every section, no index needed ---")
    write(J.QC_CSV, rows)
    G = load_stage("04g_artifact_mask.py")
    chk("no reformat index exists in this study",
        os.path.exists(RF.marker_paths(pcna)["index"]), False)
    every = G.every_section(pcna, RF.marker_paths(pcna))
    chk("PCNA: every scanned section", [r["id"] for r in every], pcna_uids)
    chk("...flagged excluded exactly where 04j skips them",
        [r["id"] for r in every if not r["excluded"]],
        [u for u, _, _ in J.sections_to_check(rows, pcna)])
    every = G.every_section(perk, RF.marker_paths(perk))
    chk("pERK: every scanned section of its own pass", [r["id"] for r in every], perk_uids)

print()
print("--- multiplex: one frame per scene, whatever marker ---")
with temp_study() as study:
    J = load_stage("04j_censor_clipped.py")
    uids = [f"LS01_s01a_sc{i:02d}" for i in range(3)]
    rows = focus(uids, "AF568")
    marker = J.MARKERS[0]
    write(J.RF.marker_paths(marker)["overrides"], [
        {"scene_uid": uids[0], "extra_rotation": "0", "flip": "0",
         "excluded": "1", "decision": "manual", "reason": "torn"}])
    chk("every scene but the excluded one",
        [u for u, _, _ in J.sections_to_check(rows, marker)], uids[1:])

print("--- nothing left to check stops the stage, writing nothing ---")
with temp_study(**PAIRED) as study:
    J = load_stage("04j_censor_clipped.py", name="lsstage_04j_empty")
    os.makedirs(os.path.dirname(J.QC_CSV), exist_ok=True)
    with open(J.QC_CSV, "w", encoding="utf-8") as fh:
        fh.write("scene_uid,animal,section_order,marker_channel\n")
    saved, sys.argv = sys.argv, ["04j_censor_clipped.py", "--marker", "AF568"]
    raised = ""
    try:
        J.main()
    except BaseException as exc:                    # noqa: BLE001
        raised = f"{type(exc).__name__}: {exc}"
    finally:
        sys.argv = saved
    chk("an empty set stops with a message, not a ZeroDivisionError",
        (raised.split(":")[0], "sections to check: the exclusion step left none" in raised), ("SystemExit", True))
    chk("...and no analysis set is written to read as done",
        os.path.exists(J.analysis_set_path("AF568")), False)

print()
print("FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
