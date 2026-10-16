"""A synthetic tutorial demo pack, so the tutorial suites never need real tissue.

Not a test. Shared setup for tests/test_tutorial_*.py.

    pack = make_pack(tmpdir)          # a folder laid out like a real pack
    zpath = zip_pack(pack, tmpdir)    # the same pack as a .zip
"""

import json
import os
import shutil
import struct
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(REPO, "scripts")]

import czi_meta                                                 # noqa: E402

PRACTICE = ["LS22_s01a_sc00", "LS22_s01a_sc01"]


def _sq(x, y, w, h):
    return [x, y, x + w, y, x + w, y + h, x, y + h]


ANSWER = {
    PRACTICE[0]: {"plate": 3, "pairs": [], "assigned": True,
                  "polys": [{"v": _sq(10, 10, 100, 100), "roi": 1, "region": "Dm", "part": 0,
                             "n": 0},
                            {"v": _sq(200, 10, 80, 80), "roi": 2, "region": "Dl", "part": 0,
                             "n": 0}]},
    PRACTICE[1]: {"plate": 4, "pairs": [], "assigned": True,
                  "polys": [{"v": _sq(10, 10, 60, 60), "roi": 1, "region": "Vd", "part": 0,
                             "n": 0}]},
}


def _header_czi(path):
    """A CZI with a metadata segment and no pixels: enough for name and header reads."""
    text = ('<ImageDocument><Metadata><Information><Image><SizeC>2</SizeC><Dimensions>'
            '<Channels><Channel Name="DAPI"><EmissionWavelength>465</EmissionWavelength>'
            '</Channel><Channel Name="AF568"><EmissionWavelength>603</EmissionWavelength>'
            '</Channel></Channels></Dimensions></Image></Information><Scaling><Items>'
            '<Distance Id="X"><Value>6.5e-07</Value></Distance><Distance Id="Y"><Value>'
            '6.5e-07</Value></Distance></Items></Scaling></Metadata></ImageDocument>')
    body = text.encode("utf-8")
    head = bytearray(czi_meta.HEADER_LEN)
    head[:10] = b"ZISRAWFILE"
    struct.pack_into("<QQ", head, 84, 0, czi_meta.HEADER_LEN)
    seg = bytearray(czi_meta.SEGMENT_HEADER_LEN)
    seg[:14] = b"ZISRAWMETADATA"
    sizes = struct.pack("<ii", len(body), 0).ljust(czi_meta.METADATA_DATA_LEN, b"\0")
    with open(path, "wb") as fh:
        fh.write(bytes(head) + bytes(seg) + sizes + body)


def make_pack(tmp, name="pack"):
    root = os.path.join(tmp, name)
    for sub in ("slides", "study", "answer",
                os.path.join("ready", "scene_renders", "qc"),
                os.path.join("ready", "detect", "results")):
        os.makedirs(os.path.join(root, sub), exist_ok=True)
    with open(os.path.join(root, "pack.json"), "w", encoding="utf-8") as fh:
        json.dump({"version": 1, "slide": "LS22_1a.czi", "practice_sections": PRACTICE,
                   "credits": "Synthetic test pack"}, fh)
    _header_czi(os.path.join(root, "slides", "LS22_1a.czi"))
    with open(os.path.join(REPO, "config.example.json"), encoding="utf-8") as fh:
        cfg = json.load(fh)
    cfg["source_dir"] = cfg["out_root"] = cfg["export_dir"] = cfg["atlas_pdf"] = ""
    cfg.setdefault("study", {})["name"] = "Tutorial study"
    with open(os.path.join(root, "study", "study.json"), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    import openpyxl
    wb = openpyxl.Workbook()
    wb.active.append(["Fish ID", "treatment"])
    wb.active.append([22, "Group A"])
    wb.save(os.path.join(root, "study", "sampling.xlsx"))
    with open(os.path.join(root, "ready", "scene_renders", "qc", "scene_renders.csv"), "w") as fh:
        fh.write("uid,ok\nLS22_s01a_sc00,1\n")
    with open(os.path.join(root, "ready", "detect", "results", "roi_nuclei.csv"), "w") as fh:
        fh.write("uid,roi,n\nLS22_s01a_sc00,1,42\n")
    with open(os.path.join(root, "answer", "ls_roi_curator_v1.json"), "w") as fh:
        json.dump(ANSWER, fh)
    return root


def zip_pack(root, tmp):
    path = os.path.join(tmp, os.path.basename(root) + ".zip")
    with zipfile.ZipFile(path, "w") as z:
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                full = os.path.join(dirpath, f)
                z.write(full, os.path.relpath(full, root))
    return path


def fresh_copy(root, tmp, name):
    dst = os.path.join(tmp, name)
    shutil.copytree(root, dst)
    return dst
