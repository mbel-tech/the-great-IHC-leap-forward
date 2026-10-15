"""A paired study's markers must name its scans' channels.

The "prova" study declared ["DAPI", "pERK", "PCNA"] against scans whose marker
channels are AF568 and AF488. Every stage ran "ok": 01 and 01k wrote no clipping
masks, 04j called all 1,063 sections unmeasured, and the ROI worklist came out
empty. The manifest's first readers now stop on that mismatch instead.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "scripts"))

import ls_channels as CH                                    # noqa: E402

failures = []


def chk(label, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + label.ljust(58) + " " + repr(got)[:80])
    if not ok:
        print("     want " + repr(want))
        failures.append(label)


def paired(markers):
    return {"acquisition": {"layout": "paired", "markers": markers, "channels": []}}


SCENES = [{"marker_channel": "AF568"}, {"marker_channel": "AF488"},
          {"marker_channel": "AF568"}]

print("--- paired ---")
chk("fluorophore names that match the scans pass",
    CH.manifest_marker_problem(paired(["AF568", "AF488"]), SCENES), None)
chk("order does not matter",
    CH.manifest_marker_problem(paired(["AF488", "AF568"]), SCENES), None)

prova = CH.manifest_marker_problem(paired(["DAPI", "pERK", "PCNA"]), SCENES)
chk("biological names plus DAPI are refused", prova is not None, True)
chk("  ...and the message names the scans' channels",
    "['AF568', 'AF488']" in (prova or ""), True)
chk("  ...and the names no scan carries", "DAPI, pERK, PCNA" in (prova or ""), True)

chk("a declared marker with no scans yet is left to 02",
    CH.manifest_marker_problem(paired(["AF568", "AF488", "AF647"]), SCENES), None)
chk("a scan channel nobody declared is refused",
    CH.manifest_marker_problem(paired(["AF568"]), SCENES) is not None, True)
chk("blank marker_channel rows are ignored",
    CH.manifest_marker_problem(paired(["AF568", "AF488"]),
                               SCENES + [{"marker_channel": ""}, {}]), None)
chk("an empty manifest is not judged",
    CH.manifest_marker_problem(paired(["DAPI"]), []), None)

print("--- multiplex and malformed ---")
MULTI = {"acquisition": {"layout": "multiplex", "channels": [
    {"name": "DAPI", "role": "nuclear", "czi_name": "DAPI", "index": 0},
    {"name": "pERK", "role": "marker", "czi_name": "AF568", "index": 1}]}}
chk("multiplex is not judged here",
    CH.manifest_marker_problem(MULTI, SCENES), None)
chk("no config is not judged", CH.manifest_marker_problem(None, SCENES), None)

print("--- require ---")
try:
    CH.require_manifest_markers(paired(["DAPI", "pERK", "PCNA"]), SCENES, "prova.json")
    chk("require stops the stage", False, True)
except SystemExit as e:
    chk("require stops the stage", str(e).startswith("prova.json: "), True)
try:
    CH.require_manifest_markers(paired(["AF568", "AF488"]), SCENES)
    chk("require lets a matching study through", True, True)
except SystemExit:
    chk("require lets a matching study through", False, True)

if failures:
    print("\n%d FAILED" % len(failures))
    sys.exit(1)
print("\nall passed")
