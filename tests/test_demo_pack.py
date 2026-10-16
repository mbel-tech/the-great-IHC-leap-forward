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
json.dump({"source_dir": "/x", "out_root": "/y", "curation": {"a": 1}, "study": {"name": "LS"}},
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
chk("a made-up workbook", os.path.isfile(os.path.join(PACK, "study", "sampling.xlsx")))
try:
    MP.crop_czi(SRC, os.path.join(TMP, "z.czi"), [9])
    chk("scene 9 refused", False)
except SystemExit as e:
    chk("scene 9 refused, saying so", "no scene 9" in str(e))

print("--- the frame check ---")
src_out, trial = os.path.join(TMP, "src_out"), os.path.join(TMP, "trial")
for root_, uids in ((src_out, ("LS22_s01a_sc01", "LS22_s01a_sc02")),
                    (trial, ("LS22_s01a_sc00", "LS22_s01a_sc01"))):
    d = os.path.join(root_, "reformatted", "sections_AF568")
    os.makedirs(d)
    for i, u in enumerate(uids):
        open(os.path.join(d, u + ".png"), "wb").write(b"section %d" % i)
os.makedirs(os.path.join(src_out, "curation"))
json.dump({"LS22_s01a_sc01": {"polys": [{"v": [0, 0, 5, 0, 5, 5], "roi": 1}]},
           "LS22_s01a_sc02": {"polys": []}, "LS22_s01a_sc03": {"polys": []}},
          open(os.path.join(src_out, "curation", "ls_roi_curator_v1.json"), "w"))
rc = MP.main(["check", "--pack", PACK, "--source-out", src_out, "--trial-out", trial])
chk("matching frames pass", rc, 0)
key = json.load(open(os.path.join(PACK, "answer", "ls_roi_curator_v1.json")))
chk("the answer key holds exactly the practice sections, under their new ids",
    sorted(key), ["LS22_s01a_sc00", "LS22_s01a_sc01"])
chk("...with the source's outlines", key["LS22_s01a_sc00"]["polys"][0]["roi"], 1)
shutil.rmtree(os.path.join(PACK, "answer"))
open(os.path.join(trial, "reformatted", "sections_AF568", "LS22_s01a_sc01.png"), "wb").write(b"moved")
rc = MP.main(["check", "--pack", PACK, "--source-out", src_out, "--trial-out", trial])
chk("a section that reformats differently fails", rc, 2)
chk("...and writes no answer key", os.path.exists(os.path.join(PACK, "answer")), False)

shutil.rmtree(TMP, True)
print(f"\n{len(failures)} failure(s)")
sys.exit(1 if failures else 0)
