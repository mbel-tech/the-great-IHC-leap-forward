"""04o's colour pictures: one place per marker, and every reader looks there.

`rgb_perk` and `rgb_pcna` run `04o_section_rgb.py` once per marker, and 04o
wrote into `<the marker's section directory>_rgb/<uid>.png`. Under `paired`
that is two directories, because the two markers are separate scans with
separate section directories. Under `multiplex` it is ONE: every marker shares
`sections/`, because one scan carries them all - and a scene's uid is the same
for every marker, so both markers' composites were the same filename in the
same directory. The second run then skipped every section as "already there"
(or, with --force, painted over the first marker's), and the curator showed one
marker's picture under the other marker's name. Nothing raised.

What this pins:

  * a multiplex study, both 04o runs: both markers' composites and thumbnails
    are on disk side by side, each in its own marker's colour;
  * the app's stage table asks for exactly those directories, and for two
    different ones, so one marker's run cannot tick the other's stage;
  * the readers - 04l's page, 04p's provenance rows, 04q's and the app's frame
    probes - look in the directory of the marker they are showing;
  * a paired study's names are byte-for-byte what they were, so a study already
    on disk is untouched.

Run:  python tests/test_rgb_per_marker.py
"""

import csv
import os
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "scripts"))
sys.path.insert(0, os.path.join(REPO, "app"))

from _fixture import temp_study, load_stage                    # noqa: E402

failures = []


def chk(label, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + label.ljust(62) + " " + repr(got))
    if not ok:
        print("     want " + repr(want))
        failures.append(label)


def write_csv(path, cols, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


MULTIPLEX = {"layout": "multiplex", "channels": [
    {"name": "DAPI", "role": "nuclear", "czi_name": "DAPI", "index": 0},
    {"name": "Mk1", "role": "marker", "czi_name": "AF568", "index": 1,
     "segment": "nuclear"},
    {"name": "Mk2", "role": "marker", "czi_name": "AF647", "index": 2,
     "segment": "nuclear"}]}
PAIRED = {"layout": "paired", "markers": ["AF568", "AF488"]}
UID = "S1"
SIDE = 768


def run_04o(O, marker):
    """One `rgb_*` stage, as the app runs it, on the overview route.

    `reformat` is replaced by a pass-through: the geometry is 04a's business and
    has its own suites. What is under test is where the picture LANDS, so the
    fake hands back the overview PNGs as they are - the marker plane is the
    marker's own _MARK.png, which is what makes the two runs' pictures differ.
    """
    def fake_reformat(dapi, companion=None, **_kw):
        img = np.asarray(Image.open(dapi).convert("L"))
        comp = np.asarray(Image.open(companion).convert("L"))
        return img, None, 0.0, None, None, comp

    saved_argv, saved_rf = sys.argv, O.RF.reformat
    sys.argv = ["04o_section_rgb.py", "--marker", marker, "--all", "--thumbs",
                "--source", "overview"]
    O.RF.reformat = fake_reformat
    raised = ""
    try:
        O.main()
    except BaseException as exc:                    # noqa: BLE001
        raised = f"{type(exc).__name__}: {exc}"
    finally:
        sys.argv, O.RF.reformat = saved_argv, saved_rf
    return raised


print("--- multiplex: both markers' composites survive both runs ---")
with temp_study(acquisition=MULTIPLEX) as study:
    O = load_stage("04o_section_rgb.py", name="lsstage_04o_mx")
    out = study.out_root
    ref = os.path.join(out, "reformatted")
    # One scene, one frame, every marker: the multiplex shape. The index and
    # focus table are the only inputs `--all` reads before rendering.
    write_csv(os.path.join(ref, "reformat_index.csv"), ["id", "kind", "animal"],
              [{"id": UID, "kind": "section", "animal": "AB12"}])
    write_csv(os.path.join(out, "qc", "focus.csv"),
              ["scene_uid", "marker_channel", "animal"],
              [{"scene_uid": UID, "marker_channel": m, "animal": "AB12"}
               for m in ("Mk1", "Mk2")])
    for m in ("Mk1", "Mk2"):
        d = os.path.join(out, "overviews", "AB12", m)
        os.makedirs(d, exist_ok=True)
        Image.fromarray(np.full((SIDE, SIDE), 40, np.uint8)).save(
            os.path.join(d, UID + "_DAPI.png"))
        Image.fromarray(np.full((SIDE, SIDE), 200, np.uint8)).save(
            os.path.join(d, UID + "_MARK.png"))

    chk("04o runs for the first marker", run_04o(O, "Mk1"), "")
    chk("04o runs for the second marker", run_04o(O, "Mk2"), "")

    names = O.RF.NAMES
    rgb = {m: names.path("sections_rgb", m) for m in ("Mk1", "Mk2")}
    thumb = {m: names.path("sections_rgb_thumb", m) for m in ("Mk1", "Mk2")}
    chk("each marker has its own composite directory",
        len(set(rgb.values())), 2)
    chk("...and its own thumbnail directory", len(set(thumb.values())), 2)
    chk("...named after the marker, as paired names a non-source marker",
        [os.path.basename(rgb[m]) for m in ("Mk1", "Mk2")],
        ["sections_Mk1_rgb", "sections_Mk2_rgb"])
    chk("...with the thumbnails beside them",
        [os.path.basename(thumb[m]) for m in ("Mk1", "Mk2")],
        ["sections_Mk1_rgb_thumb", "sections_Mk2_rgb_thumb"])
    chk("both markers' composites are on disk",
        [os.path.exists(os.path.join(rgb[m], UID + ".png")) for m in ("Mk1", "Mk2")],
        [True, True])
    chk("...and both markers' thumbnails",
        [os.path.exists(os.path.join(thumb[m], UID + ".png")) for m in ("Mk1", "Mk2")],
        [True, True])
    chk("nothing is left in the shared directory the bug wrote into",
        os.path.exists(os.path.join(ref, "sections_rgb")), False)

    # The colours are what proves the second run did not skip or paint over the
    # first: Mk1 is drawn red and Mk2 green by default.
    def planes(path):
        a = np.asarray(Image.open(path).convert("RGB"))
        return [int(a[..., c].max() > 0) for c in range(3)]
    chk("Mk1's composite is Mk1's colour (red + blue)",
        planes(os.path.join(rgb["Mk1"], UID + ".png")), [1, 0, 1])
    chk("Mk2's composite is Mk2's colour (green + blue)",
        planes(os.path.join(rgb["Mk2"], UID + ".png")), [0, 1, 1])
    chk("resolution is unchanged by where the file goes",
        Image.open(os.path.join(rgb["Mk2"], UID + ".png")).size, (SIDE, SIDE))

    # The app's stage table: done means THIS marker's pictures are there.
    import stages as S
    want = {"rgb_perk": "Mk1", "rgb_pcna": "Mk2"}
    for sid, m in want.items():
        chk(f"{sid} asks for {m}'s own directories",
            S.BY_ID[sid].output_names(out, names),
            [f"reformatted/sections_{m}_rgb",
             f"reformatted/sections_{m}_rgb_thumb"])
        chk(f"...and is done once 04o has run for {m}",
            S.BY_ID[sid].done(out, names), True)

    # The readers. Each has to name the marker's directory, not the shared one.
    L = load_stage("04l_roi_curator.py", name="lsstage_04l_mx")
    chk("04l's page is told each marker's composite directory",
        L.rgb_dirs(), {"Mk1": {"rgb": "sections_Mk1_rgb",
                               "thumb": "sections_Mk1_rgb_thumb"},
                       "Mk2": {"rgb": "sections_Mk2_rgb",
                               "thumb": "sections_Mk2_rgb_thumb"}})
    P = load_stage("04p_section_provenance.py", name="lsstage_04p_mx")
    chk("04p points each marker's provenance row at its own pictures",
        [P.rgb_dirs(m) for m in ("Mk1", "Mk2")],
        [("sections_Mk1_rgb", "sections_Mk1_rgb_thumb"),
         ("sections_Mk2_rgb", "sections_Mk2_rgb_thumb")])

    # The frame probes: 768 because each marker's own composite is there. Give
    # Mk2 a different-sized picture so a probe reading Mk1's file is caught.
    Image.new("RGB", (512, 512)).save(os.path.join(rgb["Mk2"], UID + ".png"))
    Q = load_stage("04q_import_curation.py", name="lsstage_04q_mx")
    k_of = Q.k_from_disk(ref)
    chk("04q scales each marker by its own composite",
        [k_of(UID, "Mk1"), k_of(UID, "Mk2")],
        [3.0, 2.0])
    import import_exports as IE
    chk("the app's import reads each marker's own composite",
        [IE.frame_from_disk(ref, names=names)(UID, m) for m in ("Mk1", "Mk2")],
        [768.0, 512.0])


print()
print("--- paired: every name is what it was ---")
with temp_study(acquisition=PAIRED) as study:
    import ls_paths as LP
    names = LP.Names(study.out_root, PAIRED["markers"], "paired")
    chk("the measured pass keeps sections_AF568_rgb",
        os.path.basename(names.path("sections_rgb", "AF568")), "sections_AF568_rgb")
    chk("the geometry source keeps sections_rgb",
        os.path.basename(names.path("sections_rgb", "AF488")), "sections_rgb")
    chk("...and their thumbnails keep their names",
        [os.path.basename(names.path("sections_rgb_thumb", m))
         for m in ("AF568", "AF488")],
        ["sections_AF568_rgb_thumb", "sections_rgb_thumb"])
    import stages as S
    chk("rgb_perk's declared outputs are unchanged",
        S.BY_ID["rgb_perk"].output_names(study.out_root, names),
        ["reformatted/sections_AF568_rgb", "reformatted/sections_AF568_rgb_thumb"])
    chk("rgb_pcna's declared outputs are unchanged",
        S.BY_ID["rgb_pcna"].output_names(study.out_root, names),
        ["reformatted/sections_rgb", "reformatted/sections_rgb_thumb"])
    O = load_stage("04o_section_rgb.py", name="lsstage_04o_pd")
    chk("04o writes where it always wrote",
        [os.path.basename(O.rgb_dir(m)) for m in ("AF568", "AF488")],
        ["sections_AF568_rgb", "sections_rgb"])
    L = load_stage("04l_roi_curator.py", name="lsstage_04l_pd")
    chk("04l's page gets the same names as before",
        L.rgb_dirs(), {"AF568": {"rgb": "sections_AF568_rgb",
                                 "thumb": "sections_AF568_rgb_thumb"},
                       "AF488": {"rgb": "sections_rgb",
                                 "thumb": "sections_rgb_thumb"}})
    P = load_stage("04p_section_provenance.py", name="lsstage_04p_pd")
    chk("04p's provenance paths are the same as before",
        [P.rgb_dirs(m) for m in ("AF568", "AF488")],
        [("sections_AF568_rgb", "sections_AF568_rgb_thumb"),
         ("sections_rgb", "sections_rgb_thumb")])

print()
print("--- nothing to composite is a failure, not a quiet zero ---")
with temp_study(acquisition=MULTIPLEX) as study:
    O = load_stage("04o_section_rgb.py", name="lsstage_04o_empty")
    out = study.out_root
    # Every section excluded upstream: the index holds no section rows.
    write_csv(os.path.join(out, "reformatted", "reformat_index.csv"),
              ["id", "kind", "animal"], [])
    write_csv(os.path.join(out, "qc", "focus.csv"),
              ["scene_uid", "marker_channel", "animal"], [])
    raised = run_04o(O, "Mk1")
    chk("an empty index stops the stage", raised.startswith("SystemExit"), True)
    chk("...saying there is nothing to composite", "no Mk1 sections to composite" in raised, True)

print("ALL PASS" if not failures else f"{len(failures)} FAILED")
sys.exit(1 if failures else 0)
