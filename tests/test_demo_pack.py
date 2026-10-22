"""make_demo_pack.py: cutting whole scenes out of a slide, and the frame check.

    python tests/test_demo_pack.py
"""

import json
import os
import shutil
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(REPO, "scripts"), os.path.join(REPO, "app"), HERE]

failures = []


def chk(name, got, want=True):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name:<62} {got!r}" + ("" if ok else f"  (wanted {want!r})"))
    if not ok:
        failures.append(name)


try:
    from pylibCZIrw import czi as pyczi
except ImportError:
    print("SKIP pylibCZIrw is not installed - cutting a slide needs it")
    sys.exit(0)

import czi_meta                                                 # noqa: E402
import czi_read as CR                                           # noqa: E402
import make_demo_pack as MP                                     # noqa: E402

TMP = tempfile.mkdtemp(prefix="demo_pack_")
SRC = os.path.join(TMP, "LS22_1a.czi")
rng = np.random.default_rng(3)
with pyczi.create_czi(SRC, compression_options="zstd1:ExplicitLevel=1") as w:
    for s in range(4):
        for c in (0, 1):
            w.write(rng.integers(0, 4095, (300, 260), dtype=np.uint16),
                    location=(s * 400, 50), plane={"C": c}, scene=s)
    w.write_metadata("src", channel_names={0: "DAPI", 1: "AF568"}, scale_x=6.5e-7, scale_y=6.5e-7)
# The slide's own metadata says more than pylibCZIrw writes: bit depth, emission.
root = MP.read_xml(SRC)
for ch in root.iter("Channel"):
    if ch.get("Name"):
        ET = __import__("xml.etree.ElementTree", fromlist=["x"])
        ET.SubElement(ch, "ComponentBitCount").text = "12"
        ET.SubElement(ch, "EmissionWavelength").text = "603" if ch.get("Name") == "AF568" else "465"
MP.append_metadata(SRC, MP.ET.tostring(root, encoding="unicode"))
src_meta = czi_meta.summarise(MP.read_xml(SRC))

STUDY = os.path.join(TMP, "study.json")
json.dump({"source_dir": "/x", "out_root": "/y", "curation": {"a": 1}, "study": {"name": "LS"},
           "export_dir": "/shared/exports", "scene_render": {"out_dir": "/real/pictures"}},
          open(STUDY, "w"))
PACK = os.path.join(TMP, "pack")
rc = MP.main(["cut", "--czi", SRC, "--scenes", "1,2", "--study", STUDY,
              "--practice", "LS22_s01a_sc01,LS22_s01a_sc02", "--out", PACK])
chk("cut runs", rc, 0)
DST = os.path.join(PACK, "slides", "LS22_1a.czi")
with pyczi.open_czi(SRC) as a, pyczi.open_czi(DST) as b:
    ra, rb = CR.scene_rects(a), CR.scene_rects(b)
    chk("two scenes kept", sorted(rb), [0, 1])
    same = all(np.array_equal(CR.read_plane(a, MP.Rect(*ra[old]), c, scene=old),
                              CR.read_plane(b, MP.Rect(*rb[new]), c, scene=new))
               for old, new in ((1, 0), (2, 1)) for c in (0, 1))
    chk("every pixel of every channel is the source's", same)
dst_meta = czi_meta.summarise(MP.read_xml(DST))
chk("channel names, bit depth and emission kept",
    [(c["name"], c["component_bit_count"], c["emission_nm"]) for c in dst_meta["channels"]],
    [(c["name"], c["component_bit_count"], c["emission_nm"]) for c in src_meta["channels"]])
chk("pixel size kept", (dst_meta["px_um_x"], dst_meta["px_um_y"]), (0.65, 0.65))
chk("...as the source has it", dst_meta["px_um_x"], src_meta["px_um_x"])
meta = json.load(open(os.path.join(PACK, "pack.json")))
chk("practice sections renumbered", meta["practice_sections"], ["LS22_s01a_sc00", "LS22_s01a_sc01"])
study = json.load(open(os.path.join(PACK, "study", "study.json")))
chk("study paths blanked and curation dropped",
    (study["source_dir"], study["out_root"], "curation" in study), ("", "", False))
chk("no folder of the source study is kept",
    ("export_dir" in study, "out_dir" in (study.get("scene_render") or {})), (False, False))
chk("a made-up workbook", os.path.isfile(os.path.join(PACK, "study", "sampling.xlsx")))
try:
    MP.crop_czi(SRC, os.path.join(TMP, "z.czi"), [9])
    chk("scene 9 refused", False)
except SystemExit as e:
    chk("scene 9 refused, saying so", "no scene 9" in str(e))

print("--- the frame check ---")
from PIL import Image                                           # noqa: E402


def frame(path, seed, shape=(256, 256)):
    """A section on a black ground, off-centre so a turn moves it."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img = np.zeros(shape, np.uint8)
    img[20:120, 30:200] = np.random.default_rng(seed).integers(50, 255, (100, 170), dtype=np.uint8)
    Image.fromarray(img).save(path)


src_out, trial = os.path.join(TMP, "src_out"), os.path.join(TMP, "trial")
# The source is paired (sections_AF568/), the demo study one scan (sections/).
for i, (old, new) in enumerate((("LS22_s01a_sc01", "LS22_s01a_sc00"),
                                ("LS22_s01a_sc02", "LS22_s01a_sc01"))):
    frame(os.path.join(src_out, "reformatted", "sections_AF568", old + ".png"), i)
    frame(os.path.join(trial, "reformatted", "sections", new + ".png"), i)
# A colour composite is not the frame the curator draws on.
frame(os.path.join(trial, "reformatted", "sections_AF568_rgb", "LS22_s01a_sc00.png"), 9)
os.makedirs(os.path.join(src_out, "curation"))
json.dump({"LS22_s01a_sc01": {"polys": [{"v": [0, 0, 5, 0, 5, 5], "roi": 1}]},
           "LS22_s01a_sc02": {"polys": []}, "LS22_s01a_sc03": {"polys": []}},
          open(os.path.join(src_out, "curation", "ls_roi_curator_v1.json"), "w"))
rc = MP.main(["check", "--pack", PACK, "--source-out", src_out, "--trial-out", trial])
chk("matching frames pass, whichever sections folder holds them", rc, 0)
key = json.load(open(os.path.join(PACK, "answer", "ls_roi_curator_v1.json")))
chk("the answer key holds exactly the practice sections, under their new ids",
    sorted(key), ["LS22_s01a_sc00", "LS22_s01a_sc01"])
chk("...with the source's outlines", key["LS22_s01a_sc00"]["polys"][0]["roi"], 1)
shutil.rmtree(os.path.join(PACK, "answer"))
moved = os.path.join(trial, "reformatted", "sections", "LS22_s01a_sc01.png")
Image.fromarray(np.rot90(np.asarray(Image.open(moved)))).save(moved)
rc = MP.main(["check", "--pack", PACK, "--source-out", src_out, "--trial-out", trial])
chk("a section that reformats turned fails", rc, 2)
dim = os.path.join(trial, "reformatted", "sections", "LS22_s01a_sc00.png")
Image.fromarray((np.asarray(Image.open(dim)) * 0.6).astype(np.uint8)).save(dim)
chk("...but one only graded darker has its tissue in the same place",
    MP.frames_match(src_out, trial, "LS22_s01a_sc01", "LS22_s01a_sc00"), (True, ""))
chk("...and writes no answer key", os.path.exists(os.path.join(PACK, "answer")), False)

print("--- the answer key from an export ---")
frame(moved, 1)
regions = os.path.join(TMP, "roi_regions_07.09.2026_11.27.csv")
plates = os.path.join(TMP, "roi_plates_07.09.2026_11.27.csv")
with open(plates, "w", encoding="utf-8") as fh:
    fh.write("scene_uid,marker,plate_set,plate_id,plate_index,status,favorite,view_rotation_deg,excluded\n"
             "LS22_s01a_sc01,AF568,plates_final,plate_013,13,ok,1,12.0,0\n"
             "LS22_s01a_sc02,AF568,plates_final,plate_014,14,ok,0,0.0,0\n"
             "LS22_s01a_sc03,AF568,plates_final,plate_015,15,ok,0,0.0,0\n")
with open(regions, "w", encoding="utf-8") as fh:
    fh.write("scene_uid,marker,roi_kind,region,sec_x,sec_y,sec_r,roi_shape,part,sec_poly,roi_n,landmarks_at_draw\n"
             "LS22_s01a_sc01,AF568,roi,Dl,,,,polygon,1,10 10;50 10;50 50,1,0\n"
             "LS22_s01a_sc01,AF568,background,__background__,90,90,8,disc,,,,\n"
             "LS22_s01a_sc02,AF568,roi,Dm,,,,polygon,1,20 20;60 20;60 60,2,\n"
             "LS22_s01a_sc03,AF568,roi,Vv,,,,polygon,1,1 1;9 1;9 9,3,\n")
rc = MP.main(["check", "--pack", PACK, "--source-out", src_out, "--trial-out", trial,
              "--regions", regions, "--plates", plates])
chk("an export can be the answer key", rc, 0)
key = json.load(open(os.path.join(PACK, "answer", "ls_roi_curator_v1.json")))
chk("...holding the practice sections under their new ids", sorted(key),
    ["LS22_s01a_sc00", "LS22_s01a_sc01"])
chk("...with the drawn outlines, at the export's 256 px", (key["LS22_s01a_sc00"]["frame"],
    key["LS22_s01a_sc00"]["polys"][0]["v"], key["LS22_s01a_sc00"]["polys"][0]["region"]),
    (256, [10, 10, 50, 10, 50, 50], "Dl"))
chk("...and the plate the expert matched", key["LS22_s01a_sc00"].get("plate_id"), "plate_013")
meta = json.load(open(os.path.join(PACK, "pack.json")))
chk("the pack says where its answer key came from", meta.get("answer_from"),
    "roi_regions_07.09.2026_11.27.csv")

print("--- one marker ---")
rc = MP.main(["one-marker", "--pack", PACK, "--channel", "AF568", "--nuclear", "DAPI"])
chk("one-marker runs", rc, 0)
study = json.load(open(os.path.join(PACK, "study", "study.json")))
chk("the study is one scan per section with the slide's two channels",
    (study["acquisition"]["layout"],
     [(c["name"], c["role"], c["index"]) for c in study["acquisition"]["channels"]]),
    ("multiplex", [("DAPI", "nuclear", 0), ("AF568", "marker", 1)]))
import ls_config as LC                                          # noqa: E402
chk("...and passes the settings check", [e for e in LC.validate(study)[0] if "acquisition" in e], [])

print("--- carrying the source's calibration ---")
os.makedirs(os.path.join(src_out, "qc", "flatfield"))
json.dump({"DAPI": {"lo": 233, "hi": 27993}, "AF568": {"lo": 100, "hi": 4000}},
          open(os.path.join(src_out, "qc", "display_ranges.json"), "w"))
for c in (0, 1):
    np.save(os.path.join(src_out, "qc", "flatfield", f"tilefield_c{c}.npy"), np.ones((4, 4)))
over = os.path.join(src_out, "reformatted", "perk_overrides.csv")
with open(over, "w", encoding="utf-8") as fh:
    fh.write("perk_scene_uid,extra_rotation,flip,excluded,decision,reason\n"
             "LS22_s01a_sc01,78,0,0,,\n"
             "LS22_s01a_sc02,292.5,1,0,,\n"
             "LS22_s01a_sc03,10,0,1,manual,torn\n")
rc = MP.main(["calibrate", "--pack", PACK, "--source-out", src_out, "--overrides", over,
              "--channel", "AF568"])
chk("calibrate runs", rc, 0)
pout = os.path.join(PACK, "output")
chk("the source's display ranges are carried",
    json.load(open(os.path.join(pout, "qc", "display_ranges.json")))["DAPI"], {"lo": 233, "hi": 27993})
chk("...and its tile fields", sorted(os.listdir(os.path.join(pout, "qc", "flatfield"))),
    ["tilefield_c0.npy", "tilefield_c1.npy"])
import csv                                                      # noqa: E402
rows = list(csv.DictReader(open(os.path.join(pout, "reformatted", "rotation_overrides.csv"))))
chk("every cut scene has a rotation row, under its new id, in the one-scan study's file",
    [(r["scene_uid"], r["extra_rotation"], r["flip"], r["excluded"]) for r in rows],
    [("LS22_s01a_sc00", "78", "0", "0"), ("LS22_s01a_sc01", "292.5", "1", "0")])
chk("the pack lists what it carries", json.load(open(os.path.join(PACK, "pack.json")))
    ["output_from_source"], ["qc/display_ranges.json", "qc/flatfield/tilefield_c0.npy",
                             "qc/flatfield/tilefield_c1.npy", "reformatted/rotation_overrides.csv"])
print("--- align: the trial's angle made the source's ---")
with open(os.path.join(src_out, "reformatted", "reformat_index_AF568.csv"), "w") as fh:
    fh.write("kind,id,angle\nsection,LS22_s01a_sc01,11.5\nsection,LS22_s01a_sc02,227.1\n")
with open(os.path.join(trial, "reformatted", "reformat_index.csv"), "w") as fh:
    fh.write("kind,id,angle\nsection,LS22_s01a_sc00,188.5\nsection,LS22_s01a_sc01,226.5\n")
rc = MP.main(["align", "--pack", PACK, "--source-out", src_out, "--trial-out", trial])
chk("align runs", rc, 0)
for where_, root_ in (("pack", pout), ("trial", trial)):
    rows = list(csv.DictReader(open(os.path.join(root_, "reformatted", "rotation_overrides.csv"))))
    chk(f"the section that turned the other way is turned back, in the {where_}",
        [(r["scene_uid"], round(float(r["extra_rotation"]), 1)) for r in rows],
        [("LS22_s01a_sc00", 261.0), ("LS22_s01a_sc01", 293.1)])
import tutorial_pack as TP                                      # noqa: E402
HOME = os.path.join(TMP, "home")
study_file = TP.install(TP.open_pack(PACK), HOME)
chk("the tutorial study starts with them in its output folder",
    [os.path.isfile(os.path.join(os.path.dirname(study_file), *p.split("/")))
     for p in ("qc/display_ranges.json", "reformatted/rotation_overrides.csv")], [True, True])

shutil.rmtree(TMP, True)
print(f"\n{len(failures)} failure(s)")
sys.exit(1 if failures else 0)
