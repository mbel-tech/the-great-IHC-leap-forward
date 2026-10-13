"""Importing an atlas made elsewhere: what is accepted, what is refused, what is left behind.

An atlas file the pipeline reads wrongly does not fail loudly: a vertex outside
0..1 draws off the plate, a plate id that is not in plates.csv is a region that
silently never appears, an image of the wrong size misplaces every landmark. So
the contract is judged BEFORE anything is copied, and the three promises that
make an import safe are tested here as promises:

  - a folder that fails the contract changes nothing (no folder, no config edit);
  - an atlas that is already registered, or a folder that already exists, is
    never replaced - curation is keyed to the images in a plate set;
  - what is imported is read by the real curator, not only by the checker.

Run:  python tests/test_atlas_import.py
"""

import csv
import io
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "scripts"))

from _fixture import use_temp_study, load_stage                  # noqa: E402

failures = []


def chk(label, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + label.ljust(68) + " " + repr(got))
    if not ok:
        print("     want " + repr(want))
        failures.append(label)


def mentions(rep, needle):
    return any(needle in line for line in rep.errors)


study = use_temp_study()
import atlas_registry as REG                                     # noqa: E402
import ls_atlas as AT                                            # noqa: E402
import ls_atlas_import as AI                                     # noqa: E402
import ls_config as LC                                           # noqa: E402
from PIL import Image                                            # noqa: E402

W, H = 400, 300
SQUARE = lambda x, y, s=0.2: [(x, y), (x + s, y), (x + s, y + s), (x, y + s)]   # noqa: E731


def verts(pts):
    return ";".join("%.4f %.4f" % p for p in pts)


def write_csv(path, header, rows, bom=False):
    with open(path, "w", newline="", encoding="utf-8-sig" if bom else "utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def make_set(root, kind="polygons", n=3, half=False, bom=False):
    """A valid plate set: n plates, two regions each."""
    os.makedirs(root, exist_ok=True)
    plates, polys, seeds = [], [], []
    for i in range(1, n + 1):
        pid = "mouse_%03d" % i
        Image.new("RGB", (W, H), (200, 200, 200)).save(os.path.join(root, pid + ".png"))
        row = [pid, pid + ".png", W, H]
        if half:
            row.append(0.5)
        plates.append(row)
        for r, (name, x) in enumerate((("Cx", .1), ("Hp", .5)), 1):
            polys.append([pid, r, name, "reviewed", "1.0", verts(SQUARE(x, .2))])
            seeds.append([pid, name, x + .1, .3])
    write_csv(os.path.join(root, "plates.csv"),
              ["plate_id", "image_file", "px_w", "px_h"] + (["midline_frac"] if half else []),
              plates, bom)
    if kind == "polygons":
        write_csv(os.path.join(root, "polygons.csv"),
                  ["plate_id", "roi_number", "region", "status", "label_conf", "vertices"], polys, bom)
    else:
        write_csv(os.path.join(root, "seeds.csv"), ["plate_id", "region", "x_frac", "y_frac"], seeds, bom)
    return root


def edit_csv(path, fn):
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    fn(rows)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)


tmp = tempfile.mkdtemp(prefix="atlas_import_")
try:
    # ---- a good set is accepted ---------------------------------------------
    good = make_set(os.path.join(tmp, "good"))
    rep = AI.check(good, "polygons")
    chk("a valid polygon set passes", rep.ok, True)
    chk("...and reports its plates and regions",
        (rep.facts["plates"], rep.facts["regions"], rep.facts["region_names"]), (3, 2, ["Cx", "Hp"]))
    chk("a valid seed set passes", AI.check(make_set(os.path.join(tmp, "goodseeds"), "seeds"), "seeds").ok, True)
    chk("a set saved with a BOM (Excel) passes", AI.check(make_set(os.path.join(tmp, "bom"), bom=True), "polygons").ok, True)
    chk("a micrograph-half set with midline_frac passes",
        AI.check(make_set(os.path.join(tmp, "half"), half=True), "polygons", True).ok, True)

    # ---- a bad one is refused, and the reason is named -----------------------
    chk("an unknown region kind is refused", AI.check(good, "blobs").ok, False)
    chk("a missing folder is refused", mentions(AI.check(os.path.join(tmp, "nope"), "polygons"), "not a folder"), True)

    d = make_set(os.path.join(tmp, "noplates")); os.remove(os.path.join(d, "plates.csv"))
    chk("no plates.csv", mentions(AI.check(d, "polygons"), "plates.csv is missing"), True)

    d = make_set(os.path.join(tmp, "nopoly")); os.remove(os.path.join(d, "polygons.csv"))
    chk("polygon atlas without polygons.csv", mentions(AI.check(d, "polygons"), "polygons.csv is missing"), True)

    d = make_set(os.path.join(tmp, "col"))
    edit_csv(os.path.join(d, "plates.csv"), lambda r: [row.pop(3) for row in r])
    chk("a missing column is named", mentions(AI.check(d, "polygons"), "px_h"), True)

    d = make_set(os.path.join(tmp, "dup"))
    edit_csv(os.path.join(d, "plates.csv"), lambda r: r.append(list(r[1])))
    chk("a duplicated plate id", mentions(AI.check(d, "polygons"), "used twice"), True)

    d = make_set(os.path.join(tmp, "noimg")); os.remove(os.path.join(d, "mouse_002.png"))
    chk("a plate whose image is missing", mentions(AI.check(d, "polygons"), "not in the folder"), True)

    d = make_set(os.path.join(tmp, "esc"))
    edit_csv(os.path.join(d, "plates.csv"), lambda r: r[1].__setitem__(1, "../outside.png"))
    chk("an image path that leaves the folder", mentions(AI.check(d, "polygons"), "inside the folder"), True)

    d = make_set(os.path.join(tmp, "abs"))
    edit_csv(os.path.join(d, "plates.csv"), lambda r: r[1].__setitem__(1, os.path.join(d, "mouse_001.png")))
    chk("an absolute image path", mentions(AI.check(d, "polygons"), "inside the folder"), True)

    d = make_set(os.path.join(tmp, "size"))
    edit_csv(os.path.join(d, "plates.csv"), lambda r: r[1].__setitem__(2, "999"))
    chk("px_w that disagrees with the image", mentions(AI.check(d, "polygons"), "is 400x300"), True)

    d = make_set(os.path.join(tmp, "notimg"))
    with open(os.path.join(d, "mouse_001.png"), "wb") as fh:
        fh.write(b"not a png")
    chk("an image that cannot be opened", mentions(AI.check(d, "polygons"), "cannot be opened") or mentions(AI.check(d, "polygons"), "not an image"), True)

    d = make_set(os.path.join(tmp, "off"))
    edit_csv(os.path.join(d, "polygons.csv"), lambda r: r[1].__setitem__(5, "0.1 0.1;1.5 0.1;1.5 0.9"))
    chk("a vertex off the plate (a pixel coordinate, not a fraction)", mentions(AI.check(d, "polygons"), "off the plate"), True)

    d = make_set(os.path.join(tmp, "two"))
    edit_csv(os.path.join(d, "polygons.csv"), lambda r: r[1].__setitem__(5, "0.1 0.1;0.5 0.5"))
    chk("an outline of two vertices", mentions(AI.check(d, "polygons"), "at least 3 vertices"), True)

    d = make_set(os.path.join(tmp, "pair"))
    edit_csv(os.path.join(d, "polygons.csv"), lambda r: r[1].__setitem__(5, "0.1;0.5 0.5;0.2 0.9"))
    chk("a vertex that is not an x y pair", mentions(AI.check(d, "polygons"), "not an 'x y' pair"), True)

    d = make_set(os.path.join(tmp, "ghost"))
    edit_csv(os.path.join(d, "polygons.csv"), lambda r: r[1].__setitem__(0, "mouse_999"))
    chk("a polygon on a plate that is not in plates.csv", mentions(AI.check(d, "polygons"), "not in plates.csv"), True)

    d = make_set(os.path.join(tmp, "status"))
    edit_csv(os.path.join(d, "polygons.csv"), lambda r: r[1].__setitem__(3, "maybe"))
    chk("an unknown status", mentions(AI.check(d, "polygons"), "status 'maybe'"), True)

    d = make_set(os.path.join(tmp, "noname"))
    edit_csv(os.path.join(d, "polygons.csv"), lambda r: r[1].__setitem__(2, ""))
    chk("a reviewed polygon with no region name", mentions(AI.check(d, "polygons"), "needs a region name"), True)

    d = make_set(os.path.join(tmp, "nomid"), half=False)
    chk("a micrograph-half atlas needs midline_frac", mentions(AI.check(d, "polygons", True), "midline_frac"), True)

    d = make_set(os.path.join(tmp, "seedoff"), "seeds")
    edit_csv(os.path.join(d, "seeds.csv"), lambda r: r[1].__setitem__(2, "250"))
    chk("a seed outside the plate", mentions(AI.check(d, "seeds"), "fractions of the plate"), True)

    d = make_set(os.path.join(tmp, "seedempty"), "seeds")
    edit_csv(os.path.join(d, "seeds.csv"), lambda r: r.__delitem__(slice(1, None)))
    chk("a seeds.csv with no seeds", mentions(AI.check(d, "seeds"), "no seeds"), True)

    # Warnings do not block an import
    d = make_set(os.path.join(tmp, "unrev"))
    edit_csv(os.path.join(d, "polygons.csv"), lambda r: [row.__setitem__(3, "auto") for row in r[1:]])
    rep = AI.check(d, "polygons")
    chk("polygons that are all unreviewed: allowed, but warned",
        (rep.ok, any("reviewed" in w for w in rep.warnings)), (True, True))
    rep = AI.check(make_set(os.path.join(tmp, "plain")), "polygons")
    chk("ids ending in distinct numbers raise no ordering warning",
        any("sort as text" in w for w in rep.warnings), False)

    # ---- importing -----------------------------------------------------------
    cfg_before = open(study.path, encoding="utf-8").read()
    res = AI.import_atlas(study.path, good, "mouse", "polygons", label="Mouse brain",
                          mirror_allowed=True)
    dest = os.path.join(study.out_root, "atlas", "plates_mouse")
    chk("the plate set lands under atlas/plates_<name>", res["dest"], dest)
    chk("...with plates.csv, polygons.csv and the images",
        sorted(os.listdir(dest)), ["mouse_001.png", "mouse_002.png", "mouse_003.png", "plates.csv", "polygons.csv", "seeds.csv"])
    with open(os.path.join(dest, "seeds.csv"), newline="", encoding="utf-8") as fh:
        seed_rows = list(csv.reader(fh))
    chk("...and a header-only seeds.csv (the curator opens it unconditionally)", len(seed_rows), 1)
    with open(study.path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    chk("the config registers the atlas", cfg["atlases"]["mouse"],
        {"label": "Mouse brain", "set_dir": "plates_mouse", "region_kind": "polygons",
         "micrograph_half": False, "mirror_allowed": True})
    chk("...and selects it", cfg["atlas_source"], "mouse")
    chk("...leaving every other key as it was",
        {k: v for k, v in cfg.items() if k not in ("atlases", "atlas_source")},
        {k: v for k, v in json.loads(cfg_before).items() if k not in ("atlases", "atlas_source")})
    chk("the study still validates", LC.validate(cfg, study.path)[0], [])
    chk("ls_atlas routes the study to its new set",
        (AT.source(cfg), AT.set_name(cfg), AT.entry(cfg).mirror_allowed), ("mouse", "plates_mouse", True))

    # The real consumer, not only the checker. Re-reads the study's config.
    LC.clear_cache()
    C = load_stage("04l_roi_curator.py")
    rows = C.plate_rows(AT.plate_dir(LC.load(study.path)))
    chk("04l reads the imported plates", [r["id"] for r in rows], ["mouse_001", "mouse_002", "mouse_003"])
    chk("...with their regions as outlines (one hull per region)",
        [sorted(h["region"] for h in r["hulls"]) for r in rows][0], ["Cx", "Hp"])
    chk("...each hull's vertices are the polygon's, as fractions",
        len(rows[0]["hulls"][0]["v"]), 4)

    # ---- an import is never an overwrite -------------------------------------
    cfg_text = open(study.path, encoding="utf-8").read()
    listing = sorted(os.listdir(os.path.join(study.out_root, "atlas")))
    for label, args in (
        ("the same name again", dict(name="mouse")),
        ("a built-in's name", dict(name="salmon")),
        ("a name with capitals or spaces", dict(name="Mouse Brain")),
    ):
        try:
            AI.import_atlas(study.path, make_set(os.path.join(tmp, "again")), args["name"], "polygons")
            outcome = "imported"
        except ValueError:
            outcome = "refused"
        chk("refused: " + label, outcome, "refused")
    chk("...and nothing changed in the study (config byte-identical)", open(study.path, encoding="utf-8").read(), cfg_text)
    chk("...nor in atlas/", sorted(os.listdir(os.path.join(study.out_root, "atlas"))), listing)

    os.makedirs(os.path.join(study.out_root, "atlas", "plates_taken"))
    try:
        AI.import_atlas(study.path, make_set(os.path.join(tmp, "again")), "taken", "polygons")
        outcome = "imported"
    except ValueError as e:
        outcome = "refused (exists)" if "already exists" in str(e) else "refused for another reason: %s" % e
    chk("an existing destination folder is never merged into", outcome, "refused (exists)")
    chk("...and its config is untouched", open(study.path, encoding="utf-8").read(), cfg_text)

    # A folder that fails the contract leaves nothing behind
    bad = make_set(os.path.join(tmp, "badimport"))
    os.remove(os.path.join(bad, "mouse_002.png"))
    try:
        AI.import_atlas(study.path, bad, "bad_one", "polygons")
        outcome = "imported"
    except ValueError:
        outcome = "refused"
    chk("a folder that fails the contract is refused", outcome, "refused")
    chk("...leaving no folder and no config entry",
        (os.path.exists(os.path.join(study.out_root, "atlas", "plates_bad_one")),
         os.path.exists(os.path.join(study.out_root, "atlas", "plates_bad_one.importing")),
         "bad_one" in open(study.path, encoding="utf-8").read()), (False, False, False))

    # --no-select registers without switching
    res = AI.import_atlas(study.path, make_set(os.path.join(tmp, "second"), "seeds"), "second_one", "seeds", select=False)
    with open(study.path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    chk("select=False keeps the study's atlas", cfg["atlas_source"], "mouse")
    chk("...while registering the new one", sorted(cfg["atlases"]), ["mouse", "second_one"])
    chk("dry_run checks and returns, writing nothing",
        (AI.import_atlas(study.path, make_set(os.path.join(tmp, "dry")), "dry_run_atlas", "polygons", dry_run=True)["name"],
         os.path.exists(os.path.join(study.out_root, "atlas", "plates_dry_run_atlas"))), ("dry_run_atlas", False))

    # ---- the config validator -----------------------------------------------
    cfg = json.load(open(study.path, encoding="utf-8"))
    chk("atlas_source accepts a registered atlas", [e for e in LC.validate(cfg, "c")[0] if "atlas_source" in e], [])
    typo = dict(cfg, atlas_source="mouse_brian")
    errs = [e for e in LC.validate(typo, "c")[0] if "atlas_source" in e]
    chk("...and rejects a typo, listing what is registered",
        (len(errs), "mouse, second_one" in errs[0]), (1, True))
    broken = dict(cfg, atlases={"x_one": {"set_dir": "plates_final", "region_kind": "seeds"}})
    errs = [e for e in LC.validate(broken, "c")[0] if "atlases" in e]
    chk("a bad atlases entry is a config error, not a traceback", (len(errs), "collide" in errs[0]), (1, True))

    # ---- the command line ------------------------------------------------------
    buf, old = io.StringIO(), sys.stdout
    sys.stdout = buf
    try:
        rc = AI.main([good, "--kind", "polygons", "--check"])
    finally:
        sys.stdout = old
    chk("--check judges and prints, exit 0 for a good folder", (rc, "ready to import" in buf.getvalue()), (0, True))
    sys.stdout = buf = io.StringIO()
    try:
        rc = AI.main([os.path.join(tmp, "noimg"), "--kind", "polygons", "--check"])
    finally:
        sys.stdout = old
    chk("--check exits 1 for a bad one, naming the problem", (rc, "not in the folder" in buf.getvalue()), (1, True))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("FAILED: " + ", ".join(failures) if failures else "ALL PASS")
sys.exit(1 if failures else 0)
