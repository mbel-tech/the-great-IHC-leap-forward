"""A partial or trial run never replaces a complete output.

Five stages could be narrowed - `--limit N`, `--tier X`, `--force --limit N` -
and each then atomically replaced its FULL table with the subset. The stage
still read as done (the file exists), and everything downstream worked on a
fraction of the study:

  05a --limit N         roi_boxes_<m>.csv / roi_geometry_<m>.csv held N sections
  04n --tier X          roi_worklist.csv held one tier
  04g --limit N         artifact_summary*.csv held N sections
  01  --force --limit N focus.csv checkpointed N rows over the whole file
  01k --force --limit N saturation_raw.csv the same

Each case here: a full table exists, the narrowed run is made, and the full
table still holds every original row. A trial file must not satisfy the app's
done check for its stage either. Every stage is driven through its own code;
the CZI reader, the reformat and the detector are stand-ins, so no slide is
touched.

Run:  python tests/test_partial_runs.py
"""

import contextlib
import csv
import io
import os
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from _fixture import temp_study, load_stage  # noqa: E402

sys.path.insert(0, os.path.join(REPO, "app"))
import stages as S  # noqa: E402

fails = 0


def chk(label, got, want=True):
    global fails
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label:66} {'' if ok else f'{got!r} != {want!r}'}")
    if not ok:
        fails += 1


def write(path, rows, cols=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols or list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def read(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def run_main(mod, argv):
    """mod.main() under argv, quietly. Its return code, or the SystemExit."""
    saved = sys.argv
    sys.argv = argv
    try:
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            return mod.main()
    except SystemExit as exc:
        return exc.code
    finally:
        sys.argv = saved


def done(sid, out_root, markers, layout):
    import ls_paths
    return S.BY_ID[sid].done(out_root, ls_paths.Names(out_root, markers, layout))


# --------------------------------------------------------------------- 05a
print("--- 05a --limit: a trial pair beside the real one ---")

REP = {"src_w": 1000, "src_h": 1000, "work_size": 400, "angle": 0.0,
       "flip": False, "rot_w": 400, "rot_h": 400, "x0": 0, "y0": 0,
       "crop_w": 400, "crop_h": 400, "side": 400, "ox": 0, "oy": 0, "grid": 256}


def _reformat(src, report=None, **kw):
    if report is not None:
        report.update(REP)
    return np.zeros((256, 256), np.uint8)


with temp_study(acquisition={"layout": "paired", "markers": ["Mk1", "Mk2"]}) as st:
    P5 = load_stage("05a_roi_geometry.py", name="lsstage_05a_partial")
    uids = [f"AB12_1a-s{i}" for i in range(3)]
    write(P5.FOCUS_CSV, [{"scene_uid": u, "animal": "AB12", "marker_channel": "Mk1",
                          "um_px": "0.65"} for u in uids])
    write(P5.MANIFEST_CSV, [{"scene_uid": u, "file": "AB12_1a.czi",
                             "scene_index": str(i)} for i, u in enumerate(uids)])
    write(P5.RF.marker_paths("Mk1")["index"],
          [{"id": u, "kind": "section", "angle": "0", "manual_rotation": "0",
            "manual_flip": "0"} for u in uids])
    d = os.path.join(P5.OVERVIEW_DIR, "AB12", "Mk1")
    os.makedirs(d, exist_ok=True)
    for u in uids:
        open(os.path.join(d, u + "_DAPI.png"), "wb").close()
    regions = os.path.join(st.root, "roi_regions.csv")
    write(regions, [{"scene_uid": u, "marker": "Mk1", "roi_kind": "roi",
                     "region": "R1", "seed_n": "1", "sec_x": "128", "sec_y": "128",
                     "sec_r": "10", "sec_poly": ""} for u in uids])

    P5.RF.reformat = _reformat
    P5.scene_rects = lambda path: {i: (100, 200, 1000, 1000) for i in range(3)}

    rc = run_main(P5, ["05a", regions, "--all-sections", "--marker", "Mk1"])
    chk("05a full run returns 0", rc, 0)
    box, geom = P5.box_csv("Mk1"), P5.geom_csv("Mk1")
    full_box, full_geom = text(box), text(geom)
    used = P5.used_copy_csv("Mk1")
    full_used = text(used)
    chk("the full run placed every section", len(read(geom)), 3)

    # A later export carrying one section only must not reach the archive
    # either: the trial reads it, the real pair and its record stay as they were.
    trial_regions = os.path.join(st.root, "roi_regions_trial.csv")
    write(trial_regions, read(regions)[:1])
    rc = run_main(P5, ["05a", trial_regions, "--all-sections", "--marker", "Mk1",
                       "--limit", "1"])
    chk("05a --limit 1 returns 0", rc, 0)
    chk("roi_boxes_Mk1.csv is byte-for-byte the full run's", text(box), full_box)
    chk("roi_geometry_Mk1.csv is byte-for-byte the full run's", text(geom), full_geom)
    chk("...the archived export is still the full run's", text(used), full_used)
    tbox, tgeom = P5.trial_path(box, 1), P5.trial_path(geom, 1)
    chk("the trial boxes are written beside it",
        os.path.basename(tbox), "roi_boxes_Mk1.trial1.csv")
    chk("...holding the one trial section", len(read(tgeom)), 1)

    os.remove(box)
    os.remove(geom)
    chk("trial files alone do not make 05a done",
        done("roi_geometry", st.out_root, ["Mk1", "Mk2"], "paired"), False)

# --------------------------------------------------------------------- 04n
print()
print("--- 04n --tier: one tier is written beside the full queue ---")

with temp_study(acquisition={"layout": "multiplex", "markers": [], "channels": [
        {"name": "DAPI", "role": "nuclear", "czi_name": "DAPI", "index": 0},
        {"name": "pERK", "role": "marker", "czi_name": "AF568", "index": 1}]},
        display={"colours": {"pERK": "red"}}) as st:
    M = load_stage("04n_roi_worklist.py", name="lsstage_04n_partial")
    scenes = [(a, i) for a, n in (("CD16", 12), ("AB1", 3)) for i in range(1, n + 1)]
    uid = lambda a, i: f"{a}_s01a_sc{i:02d}"                       # noqa: E731
    write(M.MANIFEST_CSV, [{"scene_uid": uid(a, i), "animal": a,
                            "marker_channel": "pERK", "section_order": i}
                           for a, i in scenes])
    write(M.index_csv(), [{"id": uid(a, i), "kind": "section"} for a, i in scenes])
    write(M.NAMES.read("analysis_set", M.MARKER),
          [{"scene_uid": uid(a, i), "animal": a, "section_order": i,
            "in_analysis_set": 1} for a, i in scenes])

    chk("04n full run returns 0", run_main(M, ["04n"]) or 0, 0)
    full = text(M.OUT_CSV)
    chk("the full queue holds both tiers",
        sorted({r["tier"] for r in read(M.OUT_CSV)}), ["animal_underpowered", "core"])
    chk("04n --tier core returns 0", run_main(M, ["04n", "--tier", "core"]) or 0, 0)
    chk("roi_worklist.csv is byte-for-byte the full queue", text(M.OUT_CSV), full)
    one = M.output_path("core")
    chk("the one-tier queue is written beside it",
        os.path.basename(one), "roi_worklist.core.csv")
    chk("...and holds that tier only", {r["tier"] for r in read(one)}, {"core"})
    chk("an unknown tier is refused, not written as a file name",
        run_main(M, ["04n", "--tier", "../x"]), 2)
    os.remove(M.OUT_CSV)
    chk("a one-tier file alone does not make 04n done",
        done("worklist", st.out_root, ["pERK"], "multiplex"), False)

# --------------------------------------------------------------------- 04g
print()
print("--- 04g --limit: a trial summary beside the real one ---")

with temp_study() as st:
    G = load_stage("04g_artifact_mask.py", name="lsstage_04g_partial")
    marker = G.MARKERS[0]
    uids = [f"LS01_s01a_sc{i:02d}" for i in range(4)]
    write(G.QC_CSV, [{"scene_uid": u, "animal": "LS01", "section_order": str(i),
                      "marker_channel": marker, "saturated_fraction": "0"}
                     for i, u in enumerate(uids)])
    write(G._RF.marker_paths(marker)["index"],
          [{"id": u, "kind": "section", "animal": "LS01", "section_order": str(i)}
           for i, u in enumerate(uids)])
    d = os.path.join(G.OVERVIEW_DIR, "LS01", marker)
    os.makedirs(d, exist_ok=True)
    for u in uids:
        Image.fromarray(np.full((40, 40), 100, np.uint8)).save(
            os.path.join(d, u + "_DAPI.png"))
    # The detector is not what is under test: every section is tissue, clean.
    G.load_tissue = lambda im: np.ones(im.shape, bool)
    G.detect = lambda im, tis: (np.zeros(im.shape, np.uint8), [])

    chk("04g full run finishes", run_main(G, ["04g", "--marker", marker]), None)
    out = G.summary_csv(marker)
    full = text(out)
    chk("the full summary holds every section", len(read(out)), 4)
    chk("04g --limit 2 finishes",
        run_main(G, ["04g", "--marker", marker, "--limit", "2"]), None)
    chk("the summary is byte-for-byte the full run's", text(out), full)
    trial = G.output_path(marker, 2)
    chk("the trial summary is written beside it",
        os.path.basename(trial), os.path.basename(out)[:-4] + ".trial2.csv")
    chk("...holding the two trial sections", len(read(trial)), 2)
    os.remove(out)
    for sid in ("artifact_pcna", "artifact_perk"):
        chk(f"a trial summary alone does not make {sid} done",
            done(sid, st.out_root, [marker], "multiplex"), False)

# ---------------------------------------------------------------- 01 / 01k


class _Rect:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h


class _Doc:
    """Three scenes, deterministic planes with tissue and a clipped block."""

    scenes_bounding_rectangle = {s: _Rect(100 * s, 0, 320, 240) for s in range(3)}

    def __init__(self, path):
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, roi, plane, scene, zoom):
        h, w = max(int(round(roi[3] * zoom)), 8), max(int(round(roi[2] * zoom)), 8)
        rng = np.random.default_rng(1000 * scene + plane["C"])
        a = rng.integers(200, 3000, size=(h, w)).astype(np.uint16)
        a[h // 4:h // 2, w // 3:w // 2] = 20000
        if plane["C"] == 1:
            a[0, 0] = 65535
        return a[None, :, :]


class _PyCzi:
    @staticmethod
    def open_czi(path):
        return _Doc(path)


print()
print("--- 01 --force --limit: every other section keeps its focus.csv row ---")

with temp_study() as st:
    sys.modules["pylibCZIrw"] = type(sys)("pylibCZIrw")
    sys.modules["pylibCZIrw"].czi = _PyCzi
    OV = load_stage("01_overviews.py", name="lsstage_01_partial")
    OV.pyczi = _PyCzi
    FNAME = "LS01_s01a.czi"
    open(os.path.join(st.source_dir, FNAME), "wb").close()
    marker = list(OV.MASK_MARKERS)[0]
    scenes = [{"scene_uid": f"LS01_s01a_sc{s:02d}", "file": FNAME, "animal": "LS01",
               "slide": "1", "variant": "a", "marker_channel": marker,
               "scene_index": str(s), "section_order": str(s)} for s in range(3)]
    write(os.path.join(st.out_root, "manifest", "manifest_scenes.csv"), scenes)
    zoom = OV.BASE_PX_UM / OV.TARGET_UM
    ranges = {"DAPI": {"lo": 200, "hi": 20000}, marker: {"lo": 200, "hi": 20000}}

    with contextlib.redirect_stdout(io.StringIO()):
        OV.export(scenes, {}, ranges, {}, zoom, 0, True)
    before = {r["scene_uid"]: r for r in read(OV.QC_CSV)}
    chk("the full export wrote a row per section", len(before), 3)
    # Mark the rows, so a kept row is told apart from a re-exported one.
    rows = read(OV.QC_CSV)
    for r in rows:
        r["focus_score"] = "KEPT"
    write(OV.QC_CSV, rows)

    with contextlib.redirect_stdout(io.StringIO()):
        exported, _failed, n = OV.export(scenes, {}, ranges, {}, zoom, 1, True)
    after = read(OV.QC_CSV)
    chk("--force --limit 1 exported one section", exported, 1)
    chk("focus.csv still holds every section", sorted(r["scene_uid"] for r in after),
        sorted(before))
    chk("...the one exported now was replaced",
        [r["scene_uid"] for r in after if r["focus_score"] != "KEPT"],
        [scenes[0]["scene_uid"]])
    chk("...and the count reported is the whole file", n, 3)

    print()
    print("--- 01k --force --limit: every other section keeps its raw row ---")
    K = load_stage("01k_saturation_raw.py", name="lsstage_01k_partial")
    sat_before = read(K.OUT_CSV)
    chk("saturation_raw.csv holds every section", len(sat_before), 3)
    for r in sat_before:
        r["reader"] = "KEPT"
    write(K.OUT_CSV, sat_before)
    rc = run_main(K, ["01k", "--force", "--limit", "1", "--native-sample", "0",
                      "--no-merge"])
    chk("01k --force --limit 1 returns 0", rc, 0)
    sat_after = read(K.OUT_CSV)
    chk("saturation_raw.csv still holds every section",
        sorted(r["scene_uid"] for r in sat_after), sorted(before))
    chk("...only the one measured now was replaced",
        [r["scene_uid"] for r in sat_after if r["reader"] != "KEPT"],
        [scenes[0]["scene_uid"]])

    os.remove(os.path.join(st.source_dir, FNAME))
    rc = run_main(K, ["01k", "--force", "--native-sample", "0", "--no-merge"])
    chk("a forced 01k that measured nothing still fails", rc, 1)
    chk("...and keeps every earlier row", len(read(K.OUT_CSV)), 3)

print()
print("FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
