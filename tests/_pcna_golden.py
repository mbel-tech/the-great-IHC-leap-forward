"""Run 05b on a fixed fixture with a given scripts folder; write what it wrote.

Not a test. tests/test_pcna_constraint.py runs it, in a child process each
time, to show that a study with no usable reagent control gets byte-identical
05b outputs (detection.auto and qc/detection_tuning.csv) from the code before
and after the PCNA tuning constraint, and that the constraint code is never
entered for it.

    python tests/_pcna_golden.py <scripts dir> <out dir> <mode>

`mode` is what the study says about reagent controls:
  none      no reagent_controls entries at all
  unusable  an isotype control on a stained file, and a no_primary control
            whose only pairing is study-wide - neither may constrain anything
  usable    a no_primary control on a stained section's file (same_file)

Writes <mode>__auto.json (detection.auto as written to the study file) and
<mode>__detection_tuning.csv, and prints one JSON line: which scripts ran and
how often the constraint code was entered (0 for code that has none).

The golden files in tests/golden/pcna_constraint/ were made by running this
against the scripts of 5405f5f:
    python tests/_pcna_golden.py <5405f5f>/scripts tests/golden/pcna_constraint none
    python tests/_pcna_golden.py <5405f5f>/scripts tests/golden/pcna_constraint unusable
"""

import csv
import datetime as _dt
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import types

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ["LS_ATLAS_BUNDLE"] = ""

ACQ = {"layout": "paired", "markers": ["AF568", "AF488"], "channels": [],
       "marker_segmentation": {"AF488": {"segment": "own", "backend": "stardist",
                                         "nucleus_shaped": True}}}
SIDE = 120
STAINED = ["AB1_1a_s01", "AB1_1a_s02", "AB2_3a_s01"]
FILE_OF = {"AB1_1a_s01": "AB1_1a.czi", "AB1_1a_s02": "AB1_1a.czi",
           "AB2_3a_s01": "AB2_3a.czi", "AB1_1a_s03": "AB1_1a.czi",
           "ZZ9_9a_s01": "ZZ9_9a.czi"}

#: Objects the stand-in reports at each cut on a stained crop (tests/
#: test_tune_detection.py's curve), and on a reagent control's crop seen at
#: the stained section's range - dim, so mostly empty.
CURVE = {0.30: 900, 0.35: 700, 0.40: 520, 0.45: 400, 0.479071: 360,
         0.50: 330, 0.55: 325, 0.60: 320, 0.65: 280, 0.70: 200, 0.75: 140,
         0.80: 90}
CTRL = {0.30: 60, 0.35: 40, 0.40: 25, 0.45: 15, 0.479071: 10, 0.50: 8,
        0.55: 5, 0.60: 3, 0.65: 2, 0.70: 1, 0.75: 0, 0.80: 0}


class Thresholds:
    prob, nms = 0.479071463157368, 0.3


class StubModel:
    """Counts follow CURVE, or CTRL for an input that is nearly all zero."""
    thresholds = Thresholds()

    def predict_instances(self, x, prob_thresh=None, nms_thresh=None, **kw):
        table = CTRL if float(np.mean(x)) < 0.1 else CURVE
        n = table[min(table, key=lambda c: abs(c - (prob_thresh or 0.479071)))]
        lab = np.zeros(x.shape, np.int32)
        lab.ravel()[:n] = np.arange(1, n + 1)
        return lab, {}


def nuclei(seed):
    rng = np.random.default_rng(seed)
    a = np.full((SIDE, SIDE), 200.0)
    yy, xx = np.mgrid[:SIDE, :SIDE]
    for _ in range(25):
        cy, cx = rng.integers(6, SIDE - 6, 2)
        a[(yy - cy) ** 2 + (xx - cx) ** 2 <= 25] = 1200
    return a + rng.normal(0, 40, a.shape)


def blank(seed):
    return np.random.default_rng(seed).normal(200, 10, (SIDE, SIDE))


class Doc:
    def __init__(self, planes):
        self.planes = planes

    def read(self, **kw):
        x, y, w, h = kw["roi"]
        return self.planes[int(kw["plane"]["C"])][y:y + h, x:x + w]


class Opened:
    def __init__(self, doc):
        self.doc = doc

    def __enter__(self):
        return self.doc

    def __exit__(self, *e):
        return False


def fake_open(path, *a, **k):
    """A control's file reads blank; every other file reads nuclei."""
    name = os.path.basename(str(path))
    if name.startswith("CTRL_"):
        return Opened(Doc({c: blank(c + 7) for c in range(3)}))
    return Opened(Doc({c: nuclei(c + 1) for c in range(3)}))


def controls_for_mode(mode):
    if mode == "none":
        return [], {}
    if mode == "unusable":
        return (["AB1_1a_s03", "ZZ9_9a_s01"],
                {"declared": "yes",
                 "scenes": {"AB1_1a_s03": {"kind": "isotype", "markers": []},
                            "ZZ9_9a_s01": {"kind": "no_primary", "markers": []}}})
    if mode == "usable":
        return (["AB1_1a_s03"],
                {"declared": "yes",
                 "scenes": {"AB1_1a_s03": {"kind": "no_primary", "markers": []}}})
    raise SystemExit(f"unknown mode {mode!r}")


def write_config(tmp, mode):
    sys.path.insert(0, HERE)
    from _fixture import write_study
    controls, rc = controls_for_mode(mode)
    over = {"acquisition": ACQ}
    if rc:
        over["reagent_controls"] = rc
    path = write_study(tmp, **over)
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    man = os.path.join(cfg["out_root"], "manifest")
    os.makedirs(man, exist_ok=True)
    with open(os.path.join(man, "manifest_scenes.csv"), "w", newline="",
              encoding="utf-8") as fh:
        w = csv.DictWriter(fh, ["scene_uid", "file", "animal", "marker_channel"])
        w.writeheader()
        for u in STAINED + controls:
            for m in ("AF568", "AF488"):
                w.writerow({"scene_uid": u, "file": FILE_OF[u],
                            "animal": u.split("_")[0], "marker_channel": m})
    return path, controls


def lay_out(D5, marker, uids, controls):
    D5.G5.use_marker(marker)
    os.makedirs(os.path.dirname(D5.G5.GEOM_CSV), exist_ok=True)
    with open(D5.G5.GEOM_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["scene_uid", "animal", "marker", "czi_file", "scene_index",
                    "m00", "m01", "m02", "m10", "m11", "m12"])
        for u in uids:
            # A control's scan reads blank (fake_open); the name says so.
            name = ("CTRL_" if u in controls else "") + u + ".czi"
            w.writerow([u, u.split("_")[0], marker, name, 0, 1, 0, 0, 0, 1, 0])
    with open(D5.G5.BOX_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["scene_uid", "animal", "marker", "roi_kind", "region",
                    "seed_n", "czi_x0", "czi_y0", "czi_w", "czi_h", "sec_x",
                    "sec_y", "sec_r", "sec_poly"])
        for u in uids:
            w.writerow([u, u.split("_")[0], marker, "roi", "Dm", 1, 0, 0,
                        SIDE, SIDE, 60, 60, 200, ""])
            w.writerow([u, u.split("_")[0], marker, "background", "bg", 1,
                        0, 0, SIDE, SIDE, 60, 60, 5, ""])


class _FixedDate(_dt.date):
    @classmethod
    def today(cls):
        return cls(2026, 1, 1)


def main(scripts, out, mode):
    scripts = os.path.abspath(scripts)
    os.makedirs(out, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="pcna_")
    try:
        cfg_path, controls = write_config(tmp, mode)
        os.environ["LS_CONFIG"] = cfg_path
        os.environ.pop("LS_CONFIG_STRICT", None)
        sys.path.insert(0, scripts)
        spec = importlib.util.spec_from_file_location(
            "g5b", os.path.join(scripts, "05b_tune_detection.py"))
        B = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(B)
        for m in ("AF568", "AF488"):
            lay_out(B.D5, m, STAINED + controls, controls)
        # `tuned` is today's date: frozen, so two runs can be compared.
        B.datetime = types.SimpleNamespace(date=_FixedDate)

        calls = {"constrain": 0, "control_crops": 0}

        def spy(owner, name, key):
            if not hasattr(owner, name):
                return
            real = getattr(owner, name)

            def wrapped(*a, **k):
                calls[key] += 1
                return real(*a, **k)
            setattr(owner, name, wrapped)
        spy(B.TU, "constrain", "constrain")
        spy(B, "read_control_crops", "control_crops")

        import pylibCZIrw.czi as pyczi
        saved = (pyczi.open_czi, sys.argv)
        pyczi.open_czi = fake_open
        B.D5.load_model = lambda: StubModel()
        sys.argv = ["05b"]
        try:
            rc = B.main()
        finally:
            pyczi.open_czi, sys.argv = saved
        with open(cfg_path, encoding="utf-8") as fh:
            auto = json.load(fh)["detection"]["auto"]
        with open(os.path.join(out, f"{mode}__auto.json"), "w", encoding="utf-8",
                  newline="\n") as fh:
            json.dump(auto, fh, indent=1)
            fh.write("\n")
        shutil.copyfile(B.TUNING_CSV, os.path.join(out, f"{mode}__detection_tuning.csv"))
        if mode == "usable":
            # The report says the date; it is not compared byte for byte.
            shutil.copyfile(B.REPORT_MD, os.path.join(out, f"{mode}__detection_tuning.md"))
        print(json.dumps({"scripts": os.path.dirname(os.path.abspath(B.__file__)),
                          "rc": rc, "calls": calls}))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main(*sys.argv[1:4])
