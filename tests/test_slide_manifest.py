"""The slide manifest: slides per animal, checked against the workbook both ways.

Run:  python tests/test_slide_manifest.py
"""

import csv
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "app"))
import slide_manifest as SM  # noqa: E402

fails = 0


def chk(what, got, want):
    global fails
    ok = got == want
    fails += not ok
    print(("ok   " if ok else "FAIL ") + what + ("" if ok else f"\n     got {got!r}\n     want {want!r}"))


PATTERN = r"^(?P<subject>LS\d+)_(?P<slide>\d+)(?P<replicate>[a-z]?)\.czi$"
MAPPING = {"id_column": "Fish", "group_columns": ["Treatment"], "ignore_columns": [],
           "match": "auto", "fill": []}


def touch(folder, names):
    for n in names:
        with open(os.path.join(folder, n), "wb") as fh:
            fh.write(b"")


def sheet(folder, rows):
    path = os.path.join(folder, "sampling.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Fish", "Treatment"])
        w.writerows(rows)
    return path


def by_animal(m):
    return {r["animal"]: r for r in m["rows"]}


with tempfile.TemporaryDirectory() as tmp:
    slides = os.path.join(tmp, "slides")
    out = os.path.join(tmp, "out")
    os.makedirs(slides)
    # LS1 and LS2: 3 slides, 6 files. LS3: 2 slides. LS4: 3 slides, one stain missing.
    # LS5: slides, no workbook row. LS9 (sheet only): no slides. One stray name.
    names = ["LS%d_%d%s.czi" % (a, s, v) for a in (1, 2, 5) for s in (1, 2, 3) for v in "ab"]
    names += ["LS3_%d%s.czi" % (s, v) for s in (1, 2) for v in "ab"]
    names += ["LS4_1a.czi", "LS4_1b.czi", "LS4_2a.czi", "LS4_2b.czi", "LS4_3a.czi"]
    names += ["scan_final.czi"]
    touch(slides, names)
    book = sheet(tmp, [["LS1", "Control"], ["LS2", "High"], ["LS3", "Control"],
                       ["LS4", "High"], ["LS9", "Control"]])
    cfg = {"source_dir": slides, "out_root": out, "slide_naming": {"pattern": PATTERN},
           "sampling": MAPPING}

    inv = SM.inventory(cfg)
    chk("inventory: read from the folder", inv["from"], "slides folder")
    chk("inventory: the stray name is unrecognised", inv["unrecognised"], ["scan_final.czi"])
    per = SM.per_animal(inv["files"])
    chk("10a + 10b is one slide", (per["LS1"]["slides"], len(per["LS1"]["files"])), (3, 6))
    chk("files sort naturally", per["LS1"]["files"][:2], ["LS1_1a.czi", "LS1_1b.czi"])

    m = SM.make(cfg, book)
    rows = by_animal(m)
    chk("matched animal is OK with its treatment",
        (rows["LS1"]["status"], rows["LS1"]["treatment"]), (SM.OK, "Control"))
    chk("fewer slides than most", rows["LS3"]["status"], "Fewer slides than most (3)")
    chk("a missing stain shows as fewer files", rows["LS4"]["status"], "Fewer files than most (6)")
    chk("slides with no workbook row", rows["LS5"]["status"], SM.NOT_IN_BOOK)
    chk("workbook row with no slides",
        (rows["LS9"]["status"], rows["LS9"]["slides"]), (SM.NO_SLIDES, 0))
    chk("workbook-only rows come last", m["rows"][-1]["animal"], "LS9")
    chk("usual counts", m["usual"], (3, 6))
    for bit in ("5 animals in the slides folder", "5 animals in the workbook",
                "1 in the workbook with no slides", "1 with slides but no workbook row",
                "2 with fewer slides or files than most", "1 file name not recognised"):
        chk("summary says '%s'" % bit, bit in m["summary"], True)

    path = SM.save(m, out)
    with open(path, newline="", encoding="utf-8") as fh:
        saved = list(csv.DictReader(fh))
    chk("CSV columns", list(saved[0].keys()), list(SM.COLUMNS))
    chk("CSV has every animal plus the unrecognised file", len(saved), len(m["rows"]) + 1)
    chk("CSV keeps the treatment", saved[0]["treatment"], "Control")
    chk("no temp file left", os.listdir(os.path.dirname(path)), [SM.CSV_NAME])

    # A workbook that cannot be used still gives the inventory.
    m = SM.make(cfg, os.path.join(tmp, "nope.xlsx"))
    chk("no workbook: every animal still listed", len(m["rows"]), 5)
    chk("no workbook: status says unchecked", m["rows"][0]["status"], SM.UNCHECKED)
    chk("no workbook: a note says why", "could not be checked" in m["notes"][0], True)
    m = SM.make(cfg, None)
    chk("no workbook chosen: still built", len(m["rows"]), 5)

    # One animal on two rows.
    book2 = sheet(tmp, [["LS1", "Control"], ["LS1", "High"], ["LS2", "High"]])
    m = SM.make(cfg, book2)
    chk("an animal on two rows is said so", by_animal(m)["LS1"]["status"], SM.TWO_ROWS)

    # One row two animals match (by number, 1 is LS1 and LS01).
    os.makedirs(os.path.join(tmp, "s01"))
    touch(os.path.join(tmp, "s01"), ["LS1_1a.czi", "LS01_1a.czi", "LS2_1a.czi"])
    book3 = sheet(tmp, [["1", "Control"], ["2", "High"]])
    m = SM.make(dict(cfg, source_dir=os.path.join(tmp, "s01")), book3)
    rows3 = by_animal(m)
    chk("a shared row is said so, naming the other animal",
        (rows3["LS1"]["status"], rows3["LS01"]["status"]),
        ("Shares workbook row 1 with LS01", "Shares workbook row 1 with LS1"))
    chk("...and the shared row is not 'no slides'", "1" in rows3, False)
    chk("...the others still match", rows3["LS2"]["status"], SM.OK)

    # The folder gone: Prepare's manifest is read, its redundant copies skipped.
    os.makedirs(os.path.join(out, "manifest"), exist_ok=True)
    with open(os.path.join(out, "manifest", "manifest_files.csv"), "w", newline="",
              encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "animal", "slide", "variant", "is_redundant_copy"])
        w.writerows([["LS1_1a.czi", "LS1", "1", "a", "0"], ["LS1_1b.czi", "LS1", "1", "b", "0"],
                     ["LS1_1a.czi", "LS1", "1", "a", "1"], ["x.czi", "", "", "", "0"]])
    cfg_gone = dict(cfg, source_dir=os.path.join(tmp, "gone"))
    inv = SM.inventory(cfg_gone)
    chk("folder gone: read from the manifest", inv["from"].startswith("Prepare's manifest"), True)
    chk("folder gone: redundant copy skipped", len(inv["files"]), 2)
    chk("folder gone: no animal is unrecognised", inv["unrecognised"], ["x.czi"])

    # No slide number in the names: each file is its own slide.
    flat = os.path.join(tmp, "flat")
    os.makedirs(flat)
    touch(flat, ["LS7-a.czi", "LS7-b.czi"])
    inv = SM.inventory({"source_dir": flat, "slide_naming": {"pattern": r"^(?P<subject>LS\d+)-\w\.czi$"}})
    chk("no slide number: each file a slide", SM.per_animal(inv["files"])["LS7"]["slides"], 2)

    inv = SM.inventory({"source_dir": slides, "slide_naming": {}})
    chk("no naming rule: an error, nothing guessed",
        (bool(inv["error"]), inv["files"]), (True, []))
    chk("nothing at all", SM.inventory({})["error"], "no slides folder is chosen")

chk("most common: ties go to the higher", SM.most_common([2, 2, 3, 3, 1]), 3)
chk("most common: none", SM.most_common([]), None)

print("\n%d failed" % fails if fails else "\nall passed")
sys.exit(1 if fails else 0)
