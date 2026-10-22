"""A one-marker study runs each pass once and calls it by its own marker.

The stage table is written for LS: pERK first, PCNA second, PCNA the pass the
geometry comes from. With one marker (the Contagion slides: DAPI + AF568) the
"PCNA" artifact mask and final reformat wrote the very files their pERK twins
write a minute later - unsuffixed, because there is only one pass - and the
screens called the rest "marker 2", a marker the study does not have.

    python tests/test_one_marker_study.py
"""

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(REPO, "app"), os.path.join(REPO, "scripts"), HERE]

from _fixture import write_study                                # noqa: E402

TMP = tempfile.mkdtemp()
PATH = write_study(os.path.join(TMP, "s"), acquisition={
    "layout": "multiplex", "markers": [], "channels": [
        {"name": "DAPI", "role": "nuclear", "czi_name": "DAPI", "index": 0},
        {"name": "AF568", "role": "marker", "czi_name": "AF568", "index": 1}]})
os.environ["LS_CONFIG"] = PATH

import ls_paths                                                 # noqa: E402
import stages as S                                              # noqa: E402
import workflow as W                                            # noqa: E402

failures = []


def chk(name, got, want=True):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name:<66} {got!r}" + ("" if ok else f"  (wanted {want!r})"))
    if not ok:
        failures.append(name)


CFG = json.load(open(PATH))


def study(markers, layout="multiplex"):
    return W.Study(CFG["out_root"], ls_paths.Names(CFG["out_root"], markers, layout), CFG)


ONE, TWO = study(["AF568"]), study(["AF568", "AF488"])
for twin, own in (("artifact_pcna", "artifact_perk"),
                  ("reformat_pcna_final", "reformat_perk_final")):
    chk(f"one marker: {twin} is not run (its {own} twin writes the same)",
        W.applies(S.BY_ID[twin], ONE)[0], False)
    chk(f"two markers: {twin} still is", W.applies(S.BY_ID[twin], TWO)[0], True)

chk("the first reformat still runs: 04f's proposals are made from it",
    W.applies(S.BY_ID["reformat_pcna"], ONE)[0], True)
plan = W.run_plan("prepare", ONE)[0]
chk("the plan masks artifacts once", [s for s in plan if s.startswith("artifact_")],
    ["artifact_perk"])
chk("...and makes the final images once",
    [s for s in plan if s.startswith("reformat_") and s.endswith("_final")],
    ["reformat_perk_final"])

titles = [W.display_title(S.BY_ID[s], ONE) for s in plan]
chk("no screen names a marker the study does not have",
    [t for t in titles if "marker 2" in t], [])
chk("the first reformat is named for the one marker",
    W.display_title(S.BY_ID["reformat_pcna"], ONE), "Standard image of every AF568 section")
chk("two markers keep their own names",
    W.display_title(S.BY_ID["reformat_pcna"], TWO), "Standard image of every AF488 section")

print()
print("ALL PASS" if not failures else f"{len(failures)} FAILED")
sys.exit(1 if failures else 0)
