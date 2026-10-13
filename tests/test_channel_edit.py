"""The channel editor's rules: what its rows write, and that the pipeline accepts it.

No window here (that is tests/test_channel_dialog.py). What is pinned:

  - loading then writing back with no edit changes nothing, so opening the editor
    and pressing OK can never rewrite a study
  - a rename follows the marker through its colour and its place in the composite
  - a removed marker leaves no colour behind (the validator refuses one)
  - "default colour" is written as nothing, not as red
  - what the editor writes is judged by the same validators the pipeline runs

Run:  python tests/test_channel_edit.py
"""

import copy
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

failures = []


def chk(label, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + label.ljust(70) + " " + repr(got))
    if not ok:
        print("     want " + repr(want))
        failures.append(label)


import ls_channel_edit as E                                          # noqa: E402

MULTI = {
    "acquisition": {"layout": "multiplex", "channels": [
        {"name": "DAPI", "role": "nuclear", "czi_name": "DAPI", "index": 0, "_note": "keep me"},
        {"name": "Fos", "role": "marker", "czi_name": "AF568", "index": 1},
        {"name": "Npy", "role": "marker", "czi_name": "AF488", "index": 2,
         "segment": "own", "backend": "threshold", "nucleus_shaped": False}]},
    "display": {"colours": {"Fos": "magenta"}, "nuclear_colour": "blue"},
}
PAIRED = {"acquisition": {"layout": "paired", "markers": ["AF568", "AF488", "AF647"],
                          "channels": []},
          "display": {"colours": {"AF647": "cyan"}, "composite": ["AF488", "AF568"]}}

print("--- loading ---")
rows = E.load_rows(MULTI)
chk("one row per channel, in order", [r["name"] for r in rows], ["DAPI", "Fos", "Npy"])
chk("roles survive", [r["role"] for r in rows], ["nuclear", "marker", "marker"])
chk("a colour belongs to its marker", [r["colour"] for r in rows], [None, "magenta", None])
chk("the nuclear row is never 'shown' as a marker", [r["show"] for r in rows], [False, True, True])
chk("unknown keys on a channel are carried", rows[0]["extra"], {"_note": "keep me"})
chk("segment defaults are filled as the pipeline fills them",
    (rows[1]["segment"], rows[2]["segment"], rows[2]["backend"]), ("nuclear", "own", "threshold"))
prows = E.load_rows(PAIRED)
chk("paired rows are the marker list", [r["name"] for r in prows], ["AF568", "AF488", "AF647"])
chk("an explicit composite marks who is shown", [r["show"] for r in prows], [True, True, False])

print("--- an untouched round trip changes nothing ---")
ch = E.edits(MULTI, "multiplex", rows)
back = E.apply(MULTI, ch)
chk("channels identical", back["acquisition"]["channels"], MULTI["acquisition"]["channels"])
chk("colours identical", back["display"]["colours"], MULTI["display"]["colours"])
chk("composite stays 'all markers' (empty)", back["display"]["composite"], [])
chk("counterstain colour untouched", back["display"]["nuclear_colour"], "blue")
pch = E.edits(PAIRED, "paired", prows)
pback = E.apply(PAIRED, pch)
chk("paired markers identical", pback["acquisition"]["markers"], PAIRED["acquisition"]["markers"])
chk("paired composite order kept", pback["display"]["composite"], ["AF488", "AF568"])
chk("paired colours identical", pback["display"]["colours"], PAIRED["display"]["colours"])
chk("paired keeps an empty channel table", pback["acquisition"]["channels"], [])

print("--- edits ---")
r2 = copy.deepcopy(rows)
r2[1]["name"] = "cFos"
out = E.apply(MULTI, E.edits(MULTI, "multiplex", r2))
chk("a rename is reported", E.renames(r2), [("Fos", "cFos")])
chk("...and the new name is written", [c["name"] for c in out["acquisition"]["channels"]],
    ["DAPI", "cFos", "Npy"])

r3 = copy.deepcopy(rows)
r3[1]["name"] = "cFos"
r3[1]["colour"] = "magenta"
chk("a renamed marker keeps its colour under the new name",
    E.apply(MULTI, E.edits(MULTI, "multiplex", r3))["display"]["colours"], {"cFos": "magenta"})

r4 = [r for r in copy.deepcopy(rows) if r["name"] != "Fos"]
out4 = E.apply(MULTI, E.edits(MULTI, "multiplex", r4))
chk("a removed marker leaves no colour behind", out4["display"]["colours"], {})
chk("...so the pipeline accepts the result", E.check(out4, "multiplex")[0], [])

r5 = copy.deepcopy(rows)
r5[1]["colour"] = None
chk("'default' writes no colour", E.apply(MULTI, E.edits(MULTI, "multiplex", r5))["display"]["colours"], {})
chk("the positional default is red then green",
    E.default_colours(rows), {"Fos": "red", "Npy": "green"})

r6 = copy.deepcopy(rows)
r6[2]["show"] = False
chk("hiding a marker lists only the shown ones",
    E.apply(MULTI, E.edits(MULTI, "multiplex", r6))["display"]["composite"], ["Fos"])
chk("...and the default colours then skip it", E.default_colours(r6), {"Fos": "red"})

r7 = copy.deepcopy(prows)
r7[2]["show"] = True
r7[2]["name"] = "Cy5"
r7[2]["colour"] = "cyan"
o7 = E.apply(PAIRED, E.edits(PAIRED, "paired", r7))
chk("a renamed paired marker follows into the colours", o7["display"]["colours"], {"Cy5": "cyan"})
chk("showing every marker clears the composite list", o7["display"]["composite"], [])

r8 = copy.deepcopy(rows)
r8[2]["segment"] = "nuclear"
o8 = E.apply(MULTI, E.edits(MULTI, "multiplex", r8))["acquisition"]["channels"][2]
chk("segmenting on the nucleus drops the own-channel fields",
    ("backend" in o8, "nucleus_shaped" in o8), (False, False))

r9 = copy.deepcopy(rows) + [E.new_row("multiplex")]
chk("a new blank row is refused by the validator, not written silently",
    E.check(E.apply(MULTI, E.edits(MULTI, "multiplex", r9)), "multiplex")[0] != [], True)

print("--- the pipeline's own judgement ---")
bad = copy.deepcopy(rows)
bad[2]["name"] = "Fos"
chk("two channels with one name are an error",
    any("both called" in e for e in E.check(E.apply(MULTI, E.edits(MULTI, "multiplex", bad)), "multiplex")[0]), True)
bad = copy.deepcopy(rows)
bad[1]["colour"] = "mauve"
chk("an unknown colour is an error",
    any("mauve" in e for e in E.check(E.apply(MULTI, E.edits(MULTI, "multiplex", bad)), "multiplex")[0]), True)
bad = copy.deepcopy(rows)
bad[2]["backend"], bad[2]["nucleus_shaped"] = "stardist", False
chk("stardist on a non-nucleus marker is an error",
    any("stardist" in e for e in E.check(E.apply(MULTI, E.edits(MULTI, "multiplex", bad)), "multiplex")[0]), True)
chk("the stock example is accepted", E.check(MULTI, "multiplex"), ([], []))
chk("a paired study with the stock list is accepted", E.check(PAIRED, "paired")[0], [])
nopaired = E.apply(PAIRED, {"acquisition.markers": ["A", "A"]})
chk("a repeated paired marker is an error",
    any("twice" in e for e in E.check(nopaired, "paired")[0]), True)

print("--- reading a CZI ---")
try:
    E.czi_channels("/nonexistent.czi")
    chk("a missing file is reported", False, True)
except ValueError as e:
    chk("a missing file is reported, not crashed on", "could not be read" in str(e), True)

print()
print("FAILED: " + ", ".join(failures) if failures else "ALL PASS")
sys.exit(1 if failures else 0)
