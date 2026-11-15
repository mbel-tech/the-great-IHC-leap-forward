"""`04q_import_curation` - the curator's exports turned back into seed state.

This is the path that decides WHERE EVERY ROI SITS when curation comes back from
a browser, and almost everything it can get wrong is quiet:

  * a global K reproduces one set of sections exactly and displaces every ROI in
    the other by 3x - on a page that loads without complaint and puts the discs
    somewhere plausible;
  * scaling the plate coordinates too would break the transform while leaving
    the section points looking right;
  * a background disc that loses its "bg" marker becomes an ROI, and one that
    keeps it but loses its radius becomes an ROI-sized disc - both change what
    the false-positive rate is measured on;
  * a dropped `excl`/`fav` flag silently reinstates sections the operator threw
    out, or loses the subset they marked;
  * a coordinate row for a section with no plates row means the three files did
    not come from one export, which has to be said rather than crashed on.

None of those raise. All of them produce a page that opens and looks curated.

Run:  python tests/test_import_curation.py
"""

import importlib.util
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")

# This suite imports stage modules, which read config at import. Without a
# config of its own it would fall through to the operator's live study and
# then pass or fail on their data. See tests/_fixture.py.
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from _fixture import use_temp_study  # noqa: E402

STUDY = use_temp_study()

_spec = importlib.util.spec_from_file_location(
    "q4", os.path.join(SCRIPTS, "04q_import_curation.py"))
Q = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(Q)

fails = 0


def chk(label, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
    print(("ok   " if ok else "FAIL ") + label.ljust(56) + " %r" % (got,)
          + ("" if ok else "   want %r" % (want,)))


def close(label, got, want, tol=1e-9):
    global fails
    ok = abs(got - want) <= tol
    if not ok:
        fails += 1
    print(("ok   " if ok else "FAIL ") + label.ljust(56) + " %r" % (got,)
          + ("" if ok else "   want %r" % (want,)))


# Two sections, deliberately at DIFFERENT scales and different markers: the
# pERK one is displayed as a 768px composite (K=3), the PCNA one as the 256px
# greyscale (K=1). A global K cannot satisfy both, which is the point.
PLATES = [
    # plate_id and plate_index deliberately DISAGREE. They used to, because the
    # page wrote the index it had and the id it had and nothing checked them
    # against each other; the importer took the index, so an export made against
    # one atlas restored against another silently. The id is the key now.
    {"scene_uid": "A_s01a_sc00", "marker": "AF568", "plate_id": "plate_010",
     "plate_index": "9", "plate_fp": "aaaaaaaaaaaa", "plate_px": "100x200",
     "status": "registered", "favorite": "0",
     "view_rotation_deg": "12.5", "excluded": "0"},
    {"scene_uid": "B_s01b_sc00", "marker": "AF488", "plate_id": "",
     "plate_index": "", "plate_fp": "", "plate_px": "",
     "status": "favourite_only", "favorite": "1",
     "view_rotation_deg": "0.0", "excluded": "0"},
    {"scene_uid": "C_s02a_sc00", "marker": "AF568", "plate_id": "",
     "plate_index": "", "plate_fp": "", "plate_px": "",
     "status": "excluded", "favorite": "0",
     "view_rotation_deg": "270.0", "excluded": "1"},
    {"scene_uid": "D_s02b_sc00", "marker": "AF488", "plate_id": "plate_003",
     "plate_index": "2", "plate_fp": "", "plate_px": "",
     "status": "no_roi", "favorite": "0",
     "view_rotation_deg": "0.0", "excluded": "0"},
]

LANDMARKS = [
    {"scene_uid": "A_s01a_sc00", "sec_x": "10.38", "sec_y": "97.28",
     "sec_r": "9.69", "plate_x": "141.85", "plate_y": "498.17", "seed_n": "1"},
    # no seed_n: a landmark not answering a numbered atlas ROI
    {"scene_uid": "A_s01a_sc00", "sec_x": "14.75", "sec_y": "118.07",
     "sec_r": "14.19", "plate_x": "146.94", "plate_y": "630.78", "seed_n": ""},
    {"scene_uid": "B_s01b_sc00", "sec_x": "20.00", "sec_y": "30.00",
     "sec_r": "8.00", "plate_x": "11.00", "plate_y": "22.00", "seed_n": "2"},
]

REGIONS = [
    {"scene_uid": "A_s01a_sc00", "roi_kind": "background", "sec_x": "13.60",
     "sec_y": "73.68", "sec_r": "8.00"},
    {"scene_uid": "B_s01b_sc00", "roi_kind": "background", "sec_x": "50.00",
     "sec_y": "60.00", "sec_r": "5.50"},
    # ROI rows are already carried by roi_landmarks.csv and must NOT be added
    # again here, or every placed ROI would be duplicated.
    {"scene_uid": "A_s01a_sc00", "roi_kind": "roi", "sec_x": "10.38",
     "sec_y": "97.28", "sec_r": "9.69"},
    # A DRAWN REGION. This is the one thing roi_regions.csv holds that
    # roi_landmarks.csv cannot supply, so it is also the one thing a round trip
    # can silently lose. On the K=3 section, to prove the vertices go through
    # the same per-section scale as sec_x/sec_y and not some other one.
    {"scene_uid": "A_s01a_sc00", "roi_kind": "roi", "roi_shape": "polygon",
     "region": "Dl", "part": "2", "sec_x": "15.00", "sec_y": "15.00",
     "sec_r": "", "roi_n": "4", "landmarks_at_draw": "2",
     "sec_poly": "10.00 10.00;20.00 10.00;20.00 20.00;10.00 20.00"},
]

# Under three corners is not a shape. Kept OUT of the fixture above so that
# `skipped` stays empty there and goes on meaning "a well-formed export loses
# nothing" - a refusal folded into that list would have made the assertion pass
# for the wrong reason ever after.
BAD_POLY = [
    {"scene_uid": "B_s01b_sc00", "roi_kind": "roi", "roi_shape": "polygon",
     "region": "Dm", "part": "1", "sec_x": "0", "sec_y": "0", "sec_r": "",
     "sec_poly": "1.00 1.00;2.00 2.00"},
]

# The scale the real page implies for these two, without touching a disk.
K = {"A_s01a_sc00": 3.0, "B_s01b_sc00": 1.0, "C_s02a_sc00": 3.0,
     "D_s02b_sc00": 1.0}


def k_of(uid, marker):
    return K.get(uid, 1.0)


def main():
    state, skipped = Q.build(PLATES, LANDMARKS, REGIONS, k_of)

    # ---- the scale is per section ------------------------------------------
    a = state["A_s01a_sc00"]["pairs"][0]
    b = state["B_s01b_sc00"]["pairs"][0]
    close("K=3 section: sec_x scaled", a[0], 31.14)
    close("...sec_y scaled", a[1], 291.84)
    close("...and the radius with it", a[5], 29.07)
    close("K=1 section in the SAME import is not scaled", b[0], 20.0)
    close("...nor its radius", b[5], 8.0)

    # The trap this stage exists for: one global K would have to be 3 or 1, and
    # either way one of these two moves.
    chk("the two sections did not share a scale",
        (a[0] / 10.38, b[0] / 20.0), (3.0, 1.0))

    # ---- the plate frame is not the section frame ---------------------------
    close("plate_x is left alone", a[2], 141.85)
    close("plate_y is left alone", a[3], 498.17)

    # ---- pair shape --------------------------------------------------------
    chk("a landmark pair has six elements", len(a), 6)
    chk("seed_n is carried", a[4], 1)
    chk("a landmark with no seed_n gets 0",
        state["A_s01a_sc00"]["pairs"][1][4], 0)

    bg = [p for p in state["A_s01a_sc00"]["pairs"] if len(p) > 6]
    chk("the background disc is marked", bg[0][6], Q.BG_MARK)
    chk("...and answers no plate point", bg[0][2:5], [0, 0, 0])
    close("...at its own scaled radius", bg[0][5], 24.0)   # 8.00 * 3

    # An ROI row in roi_regions.csv is the same pair roi_landmarks.csv already
    # carried; counting both would double every placed ROI.
    chk("ROI rows in regions are not re-added",
        len(state["A_s01a_sc00"]["pairs"]), 3)   # 2 landmarks + 1 background

    # ---- a drawn region survives the round trip ----------------------------
    pg = state["A_s01a_sc00"]["polys"]
    chk("the polygon came back", len(pg), 1)
    chk("...with its region", pg[0]["region"], "Dl")
    chk("...and its lobe", pg[0]["part"], 2)
    chk("...as a flat x,y list, the shape the page stores",
        pg[0]["v"], [30.0, 30.0, 60.0, 30.0, 60.0, 60.0, 30.0, 60.0])
    # 10.00 * 3: the vertices ride the SAME per-section K as sec_x and sec_y.
    # Scaling them by anything else would put a region somewhere the operator
    # never drew one, on a page that opens without complaint.
    chk("the vertices took this section's own scale, not a global one",
        [v / 10.0 for v in pg[0]["v"][:2]], [3.0, 3.0])
    bad, bad_skip = Q.build(PLATES, [], BAD_POLY, k_of)
    chk("a two-corner row is refused rather than imported as a shape",
        bad["B_s01b_sc00"]["polys"], [])
    chk("...and is reported rather than dropped in silence",
        [k for k, _u in bad_skip], ["polygon"])
    # A polygon is not a pair. Adding it to `pairs` would put a shape into the
    # landmark fit, whose (0, 0) plate point would drag the whole transform.
    chk("the polygon did not join the pairs",
        len(state["A_s01a_sc00"]["pairs"]), 3)
    # An export written before polygons existed has no roi_shape column at all.
    chk("a pre-polygon import still yields an empty polys list",
        state["C_s02a_sc00"]["polys"], [])

    # The ROI NUMBER has to survive. It is what the guided cursor reads to know a
    # re-imported section is already done; without it the walk would ask for
    # every region again on a section that was finished weeks ago.
    chk("the polygon's ROI number came back", pg[0]["roi"], 4)
    noroi, _s = Q.build(PLATES, [], [dict(REGIONS[-1], roi_n="")], k_of)
    chk("...and a row written before roi_n existed imports as 0",
        noroi["A_s01a_sc00"]["polys"][0]["roi"], 0)

    # The ORDER STAMP. `z` compares a region's age against the landmarks by it,
    # and the curator rebuilds polys from this file, so a stamp that does not
    # survive here does not survive at all.
    chk("the order stamp came back", pg[0]["n"], 2)
    zero, _s = Q.build(PLATES, [], [dict(REGIONS[-1], landmarks_at_draw="0")], k_of)
    chk("...and 0 is a stamp, not a missing one - a region drawn before any "
        "landmark", zero["A_s01a_sc00"]["polys"][0]["n"], 0)
    # Absent has to stay absent rather than defaulting: the curator reads an
    # unstamped region as the newer of the two, and a 0 invented here would
    # assert the opposite - that it was drawn before every landmark on the
    # section - about a file that never said so.
    older = {k: v for k, v in REGIONS[-1].items() if k != "landmarks_at_draw"}
    pre, _s = Q.build(PLATES, [], [older], k_of)
    chk("a row written before the column carries no stamp at all",
        "n" in pre["A_s01a_sc00"]["polys"][0], False)

    # ---- the decision flags ------------------------------------------------
    chk("a named plate reads as assigned", state["A_s01a_sc00"]["assigned"], True)
    # THE ID WINS. The index is kept as a record of what the array looked like
    # and used only when there is no id at all.
    chk("...and the plate comes from the id", state["A_s01a_sc00"]["plate_id"], "plate_010")
    chk("...not from the index it disagrees with", state["A_s01a_sc00"]["plate"], 9)
    chk("...and it is marked unverified until the atlas is there to check",
        state["A_s01a_sc00"]["verified"], "unchecked")
    chk("no plate named, not assigned", state["B_s01b_sc00"]["assigned"], False)
    chk("...and the index falls back to 0", state["B_s01b_sc00"]["plate"], 0)
    chk("favourite is read", state["B_s01b_sc00"]["fav"], True)
    chk("exclusion is read", state["C_s02a_sc00"]["excl"], True)
    chk("...and does not also mark it favourite", state["C_s02a_sc00"]["fav"], False)
    chk("no_roi is read from status", state["D_s02b_sc00"]["noroi"], True)
    chk("...and only from that status", state["A_s01a_sc00"]["noroi"], False)
    chk("rotation survives", state["C_s02a_sc00"]["rot"], 270)
    chk("a whole rotation is written as an int, like the page does",
        isinstance(state["C_s02a_sc00"]["rot"], int), True)

    # ---- every section is present, decision or not -------------------------
    chk("every plates row becomes a section", len(state), 4)
    chk("an excluded section still carries its pairs list",
        state["C_s02a_sc00"]["pairs"], [])
    chk("no coordinate rows were dropped", skipped, [])

    # ---- a coordinate row with no plates row -------------------------------
    orphan, sk = Q.build(
        PLATES,
        LANDMARKS + [{"scene_uid": "GHOST", "sec_x": "1", "sec_y": "2",
                      "sec_r": "3", "plate_x": "4", "plate_y": "5", "seed_n": ""}],
        REGIONS + [{"scene_uid": "GHOST2", "roi_kind": "background",
                    "sec_x": "1", "sec_y": "2", "sec_r": "3"}],
        k_of)
    chk("an orphan landmark is reported, not invented",
        ("landmark", "GHOST") in sk, True)
    chk("...and an orphan background disc too",
        ("background", "GHOST2") in sk, True)
    chk("...and neither creates a section", "GHOST" in orphan, False)

    # ---- counts, which are what the operator reads back --------------------
    c = Q.counts(state)
    chk("counts: sections", c["sections"], 4)
    chk("counts: excluded", c["excluded"], 1)
    chk("counts: favourites", c["favourites"], 1)
    chk("counts: assigned", c["assigned"], 2)
    chk("counts: landmark pairs", c["landmarks"], 3)
    chk("counts: background discs", c["background"], 2)

    # ---- compare() names work that exists only on disk ---------------------
    tmp = tempfile.mkdtemp(prefix="lsimp_")
    try:
        disk = os.path.join(tmp, "seed.json")
        with open(disk, "w", encoding="utf-8") as fh:
            json.dump({"A_s01a_sc00": state["A_s01a_sc00"],
                       "ONLY_ON_DISK": {"plate": 0, "pairs": [], "assigned": False,
                                        "noroi": False, "fav": True, "rot": 0,
                                        "excl": False}}, fh)
        d = Q.compare(state, disk)
        chk("compare: work only on disk is named", d["only_on_disk"], ["ONLY_ON_DISK"])
        chk("compare: unchanged sections are not flagged", d["changed"], [])
        chk("compare: the new ones are counted", len(d["added"]), 3)
        chk("compare: a missing file is not an error", Q.compare(state, disk + "x"), None)

        # ---- the CLI does not write unless asked ---------------------------
        import csv as _csv

        def dump(name, rows, fields):
            p = os.path.join(tmp, name)
            with open(p, "w", newline="", encoding="utf-8") as fh:
                w = _csv.DictWriter(fh, fieldnames=fields)
                w.writeheader()
                w.writerows(rows)
            return p

        pl = dump("p.csv", PLATES, list(PLATES[0]))
        lm = dump("l.csv", LANDMARKS, list(LANDMARKS[0]))
        # The UNION of every row's keys, in first-seen order. Taking the first
        # row's alone was fine while every region row was a disc; a polygon row
        # carries four columns a disc does not, and DictWriter raises on them.
        rgf = list(dict.fromkeys(k for r in REGIONS for k in r))
        rg = dump("r.csv", REGIONS, rgf)
        out = os.path.join(tmp, "written.json")
        argv = ["--plates", pl, "--landmarks", lm, "--regions", rg,
                "--out", out, "--no-rgb"]
        Q.main(argv)
        chk("a report-only run writes nothing", os.path.exists(out), False)
        Q.main(argv + ["--write"])
        chk("--write writes the file", os.path.exists(out), True)
        with open(out, encoding="utf-8") as fh:
            written = json.load(fh)
        # Filed per study like the page's key (scripts/ls_curation.py).
        chk("...filed under this study", list(written["_studies"]), [Q.STUDY_ID])
        chk("...with every section in it", len(written["_studies"][Q.STUDY_ID]), 4)

        # The first write made the file, so there was nothing to back up. A
        # second write of the same export changes nothing, so it neither writes
        # nor leaves a backup: a backup per identical import would bury the one
        # that matters under copies of the same state.
        Q.main(argv + ["--write"])
        chk("an identical rewrite leaves no backup",
            [n for n in os.listdir(tmp) if n.endswith(".backup")], [])

        # ---- another study's curation in the same file is not this import's -----
        # The page keeps its curation filed per study inside one localStorage key,
        # because every study's page shares one origin; the app mirrors that value
        # to this file. An import replaces THIS study's records and nothing else.
        rec = {"plate": 0, "pairs": [], "assigned": False, "noroi": False,
               "fav": True, "rot": 0, "excl": False}
        uid0 = sorted(state)[0]
        with open(out, "w", encoding="utf-8") as fh:
            json.dump({"_studies": {Q.STUDY_ID: {"MINE_ONLY": rec},
                                    "/elsewhere/other_study": {uid0: rec}},
                       "_legacy": {uid0: rec, "UNCLAIMED": rec}}, fh)
        d = Q.compare(state, out)
        chk("compare reads this study's part of a filed file", d["only_on_disk"], ["MINE_ONLY"])
        chk("...and not another study's, though it shares a uid", uid0 in d["changed"], False)
        Q.main(argv + ["--write"])
        with open(out, encoding="utf-8") as fh:
            after = json.load(fh)
        chk("--write keeps another study's records",
            after["_studies"].get("/elsewhere/other_study"), {uid0: rec})
        chk("...merges into this study's, keeping what only it held",
            sorted(after["_studies"][Q.STUDY_ID]), sorted(list(state) + ["MINE_ONLY"]))
        chk("...and keeps what is still unclaimed from before filing, minus what it replaced",
            after.get("_legacy"), {"UNCLAIMED": rec})

        # ---- this study, after its folder moved to another drive -------------
        # The file lives in out_root, so its one entry under another, unmarked folder
        # is this study's: compared against, and left in place by the write.
        with open(out, "w", encoding="utf-8") as fh:
            json.dump({"_studies": {"/old/drive/out": {"MINE_ONLY": rec}}}, fh)
        d = Q.compare(state, out)
        chk("a moved study's old entry is what is compared", d["only_on_disk"], ["MINE_ONLY"])
        Q.main(argv + ["--write"])
        with open(out, encoding="utf-8") as fh:
            after = json.load(fh)
        chk("...the write files under the new folder, the old entry's work merged in",
            sorted(after["_studies"][Q.STUDY_ID]), sorted(list(state) + ["MINE_ONLY"]))
        chk("...and leaves the old entry in place", after["_studies"].get("/old/drive/out"),
            {"MINE_ONLY": rec})

        # ---- an import never discards curation (REVIEW.md rule 4) ------------
        # The app's Import button runs this stage with --write unconditionally.
        # It used to REPLACE this study's entry with whatever the export held, so
        # a session that exported one animal dropped the other eleven, and the
        # single `.backup` it kept was overwritten by the next import.
        merge_dir = os.path.join(tmp, "merge")
        os.makedirs(merge_dir)
        seed = os.path.join(merge_dir, "ls_roi_curator_v1.json")
        uid_a, uid_c = "A_s01a_sc00", "C_s02a_sc00"
        old_a = dict(rec, fav=False, rot=90)
        stored0 = {"_studies": {Q.STUDY_ID: {uid_a: old_a, "B_KEPT_ONLY": rec},
                                "/elsewhere/other_study": {"X": rec}}}
        with open(seed, "w", encoding="utf-8") as fh:
            json.dump(stored0, fh)
        with open(seed, encoding="utf-8") as fh:
            original_text = fh.read()
        exp_plates = [r for r in PLATES if r["scene_uid"] in (uid_a, uid_c)]
        exp_marks = [r for r in LANDMARKS if r["scene_uid"] == uid_a]
        mp = dump("merge_p.csv", exp_plates, list(PLATES[0]))
        ml = dump("merge_l.csv", exp_marks, list(LANDMARKS[0]))
        mr = dump("merge_r.csv", [], rgf)
        margv = ["--plates", mp, "--landmarks", ml, "--regions", mr,
                 "--out", seed, "--no-rgb"]
        exported, _ = Q.build(exp_plates, exp_marks, [], lambda u, m: 1.0)

        def backups():
            return sorted(n for n in os.listdir(merge_dir) if n.endswith(".backup"))

        def stored():
            with open(seed, encoding="utf-8") as fh:
                return json.load(fh)

        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = Q.main(margv)
        chk("a report-only run names what --write would keep",
            "B_KEPT_ONLY" in buf.getvalue(), True)
        chk("...and makes no backup", (rc, backups()), (0, []))

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            Q.main(margv + ["--write"])
        mine = stored()["_studies"][Q.STUDY_ID]
        chk("--write MERGES: export's sections plus the stored-only one",
            sorted(mine), sorted([uid_a, "B_KEPT_ONLY", uid_c]))
        chk("...the section in both takes the export's record", mine[uid_a], exported[uid_a])
        chk("...the stored-only section is kept as it was", mine["B_KEPT_ONLY"], rec)
        chk("...the new one is added", mine[uid_c], exported[uid_c])
        chk("...another study's entry is untouched",
            stored()["_studies"]["/elsewhere/other_study"], {"X": rec})
        chk("...the report counts and names what it kept",
            ("kept 1" in buf.getvalue(), "B_KEPT_ONLY" in buf.getvalue()), (True, True))
        first = backups()
        chk("...one timestamped backup beside it", len(first), 1)
        chk("...named <name>.<YYYYMMDD-HHMMSS>.backup",
            bool(first) and first[0].startswith("ls_roi_curator_v1.json.")
            and len(first[0].split(".")[2]) == len("YYYYMMDD-HHMMSS"), True)
        if first:
            with open(os.path.join(merge_dir, first[0]), encoding="utf-8") as fh:
                chk("...holding the original state, byte for byte", fh.read(), original_text)

        merged = stored()
        Q.main(margv + ["--write", "--replace"])
        mine = stored()["_studies"][Q.STUDY_ID]
        chk("--replace drops the stored-only section", sorted(mine), sorted([uid_a, uid_c]))
        chk("...and still carries another study's entry through",
            stored()["_studies"]["/elsewhere/other_study"], {"X": rec})
        two = backups()
        chk("two writes leave two distinct backups", len(two), 2)
        chk("...the first is not overwritten", set(first) <= set(two), True)
        if len(two) == 2:
            with open(os.path.join(merge_dir, first[0]), encoding="utf-8") as fh:
                chk("...and still holds the original state", fh.read(), original_text)
            newer = [n for n in two if n not in first][0]
            with open(os.path.join(merge_dir, newer), encoding="utf-8") as fh:
                chk("...the second holds what the second write replaced", json.load(fh), merged)

        # A write interrupted before the replace leaves the file as it was. The
        # original state goes back first, so the import has something to change.
        with open(seed, "w", encoding="utf-8") as fh:
            fh.write(original_text)
        before = original_text
        real_replace = os.replace

        def boom(src, dst):
            raise OSError("simulated crash before the replace")
        os.replace = boom
        try:
            raised = False
            try:
                Q.main(margv + ["--write"])
            except OSError:
                raised = True
        finally:
            os.replace = real_replace
        chk("an interrupted write raises", raised, True)
        chk("...and leaves the previous file intact",
            open(seed, encoding="utf-8").read(), before)
        chk("...with no temp file left beside it",
            [n for n in os.listdir(merge_dir) if n.endswith(".tmp")], [])

        # The names are a list a person reads: capped, with the rest counted.
        many = ["S%02d" % i for i in range(25)]
        line = Q.some(many)
        chk("a long list is capped", ("S00" in line, "S24" in line), (True, False))
        chk("...and says how many more", "and 15 more" in line, True)
        chk("a short list is named in full", Q.some(["a", "b"]), "a, b")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- k_from_disk under a study that is not LS --------------------------
    #
    # Everything above drives build() with a k_of the suite made up. This
    # drives the REAL one, off real files, under a study whose markers are not
    # AF568/AF488.
    #
    # `marker_dir` was `"sections_AF568" if marker == "AF568" else "sections"`.
    # For any other study every marker resolved to `sections`, so the probe for
    # `<dir>_rgb/<uid>.png` missed, `k` fell back to 1.0 - and the page had
    # drawn at K=3, because the composite it loaded really is 768 px. Every
    # imported disc, landmark and polygon vertex then landed at one THIRD of
    # its true coordinate. Nothing raised, nothing logged: the seed file loads
    # and puts the discs somewhere plausible, which is verbatim the failure
    # this stage's own docstring says it exists to prevent.
    from _fixture import temp_study, load_stage
    from PIL import Image
    with temp_study(acquisition={"layout": "paired",
                                 "markers": ["Mk1", "Mk2"]}) as mk:
        RF = load_stage("04a_reformat.py", name="lsstage_04a_q")
        Q2 = load_stage("04q_import_curation.py", name="lsstage_04q_mk")
        reformat = os.path.join(mk.out_root, "reformatted")

        # One 768-px composite per marker, each in the directory 04a would have
        # written it to. Mk2 is the geometry source, so its is unsuffixed.
        for marker, uid in (("Mk1", "U_mk1"), ("Mk2", "U_mk2")):
            d = RF.marker_paths(marker)["sections"] + "_rgb"
            os.makedirs(d, exist_ok=True)
            Image.new("RGB", (768, 768)).save(os.path.join(d, uid + ".png"))

        k_disk = Q2.k_from_disk(reformat)
        chk("the suffixed marker's composite is found: K=3, not 1.0",
            k_disk("U_mk1", "Mk1"), 3.0)
        chk("...and the geometry source's, in the unsuffixed directory",
            k_disk("U_mk2", "Mk2"), 3.0)
        chk("a section with no image on disk is still 1.0",
            k_disk("U_none", "Mk1"), 1.0)
        # And the directory itself, stated directly, so a future reader does
        # not have to infer the rule from a scale factor.
        chk("marker_dir is 04a's directory, basename for basename",
            (Q2.marker_dir("Mk1"), Q2.marker_dir("Mk2")),
            (os.path.basename(RF.marker_paths("Mk1")["sections"]),
             os.path.basename(RF.marker_paths("Mk2")["sections"])))
        chk("...which for a paired study means suffixed and unsuffixed",
            (Q2.marker_dir("Mk1"), Q2.marker_dir("Mk2")),
            ("sections_Mk1", "sections"))

    print()
    print("ALL PASS" if not fails else "%d FAILED" % fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
