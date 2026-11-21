"""Per-nucleus shape and texture features (ls_nucleus_features, 06l_nucleus_features).

The features on synthetic label images: a filled disc is round and solid; a
granular marker has spots and a higher coefficient of variation than a smooth
one; the texture features survive an offset and a gain change (x2 + 1000, the
background batch between animals). Then the stage, on a stand-in for
pylibCZIrw: it reads only boxes and saved outlines (never StarDist), writes one
row per roi_nuclei.csv row, resumes per section after a --limit stop, re-reads
only a section whose outlines changed, and leaves roi_nuclei.csv byte for byte.

    python tests/test_nucleus_features.py
"""

import contextlib
import csv
import hashlib
import io
import os
import sys
import threading

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(REPO, "scripts"), HERE]

import ls_nucleus_features as NF                                 # noqa: E402

failures = []


def chk(name, got, want=True):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name:<70} {got!r}" + ("" if ok else f"  (wanted {want!r})"))
    if not ok:
        failures.append(name)


# ---------------------------------------------------------------- synthetic nuclei

def disc(shape, cy, cx, r):
    yy, xx = np.mgrid[:shape[0], :shape[1]]
    return (yy - cy) ** 2 + (xx - cx) ** 2 <= r ** 2


def two_nuclei(seed, spots_a=6, spots_b=0, mean_a=1000.0, mean_b=1000.0, sd=5.0):
    """A 60 x 120 box: nucleus 1 on the left, nucleus 2 on the right, background round them."""
    rng = np.random.default_rng(seed)
    shape = (60, 120)
    labels = np.zeros(shape, np.uint32)
    m1, m2 = disc(shape, 30, 30, 12), disc(shape, 30, 90, 12)
    labels[m1], labels[m2] = 1, 2
    marker = 500 + rng.normal(0, sd, shape)
    marker[m1] = mean_a + rng.normal(0, sd, m1.sum())
    marker[m2] = mean_b + rng.normal(0, sd, m2.sum())
    for cx, n in ((30, spots_a), (90, spots_b)):
        for k in range(n):
            a = 2 * np.pi * k / max(n, 1)
            y, x = int(30 + 7 * np.sin(a)), int(cx + 7 * np.cos(a))
            marker[y - 1:y + 2, x - 1:x + 2] += 600
            marker[y, x] += 100                     # one clear peak per spot
    dapi = 400 + rng.normal(0, 5, shape)
    dapi[labels > 0] += 2000
    return labels, dapi, marker


print("--- disc_roundness ---")
labels, dapi, marker = two_nuclei(1)
f = NF.features(labels, 1, dapi, marker)
print(f"     {f}")
chk("the keys are the five stated", sorted(f),
    sorted(["roundness", "solidity", "marker_cv", "marker_spots", "other_marker_median"]))
chk("a filled disc has roundness > 0.85", f["roundness"] > 0.85)
chk("...and solidity > 0.95", f["solidity"] > 0.95)
chk("other_marker_median is blank with no other channel", f["other_marker_median"], "")
crescent = np.zeros((60, 60), np.uint32)
crescent[disc((60, 60), 30, 30, 15) & ~disc((60, 60), 30, 38, 13)] = 1
fc = NF.features(crescent, 1, np.zeros((60, 60)), np.full((60, 60), 100.0))
chk("a crescent is less round and less solid than a disc",
    fc["roundness"] < f["roundness"] and fc["solidity"] < f["solidity"])
other = np.full(labels.shape, 7.0)
other[labels == 1] = 321.0
chk("other_marker_median is the other channel's median inside the nucleus",
    NF.features(labels, 1, dapi, marker, other)["other_marker_median"], 321.0)

print("--- granular_more_spots ---")
labels, dapi, marker = two_nuclei(2, spots_a=6, spots_b=0)
g, s = NF.features(labels, 1, dapi, marker), NF.features(labels, 2, dapi, marker)
print(f"     granular {g}\n     smooth   {s}")
chk("a granular nucleus has marker_spots >= 3", g["marker_spots"] >= 3)
chk("...a smooth one fewer", s["marker_spots"] < g["marker_spots"])
chk("...and a higher marker_cv than the smooth one", g["marker_cv"] > s["marker_cv"])

print("--- offset_gain_invariant_texture ---")
for label_, kw in (("granular vs smooth", dict(spots_a=6, spots_b=0)),
                   ("granular vs less granular", dict(spots_a=8, spots_b=3)),
                   # Means far apart: a plain sd / mean reverses this pair under
                   # x2 + 1000; the CV above the box's own background must not.
                   ("dim granular vs bright smooth",
                    dict(spots_a=4, spots_b=0, mean_a=700.0, mean_b=4000.0, sd=40.0))):
    labels, dapi, marker = two_nuclei(3, **kw)
    t = marker * 2.0 + 1000
    before = [NF.features(labels, i, dapi, marker) for i in (1, 2)]
    after = [NF.features(labels, i, dapi * 2.0 + 1000, t) for i in (1, 2)]
    chk(f"{label_}: marker_cv ordering unchanged under x2 + 1000",
        (before[0]["marker_cv"] > before[1]["marker_cv"])
        == (after[0]["marker_cv"] > after[1]["marker_cv"]))
    chk(f"{label_}: marker_spots equal under x2 + 1000",
        [b["marker_spots"] for b in before], [a["marker_spots"] for a in after])
    chk(f"{label_}: roundness and solidity do not see the intensities",
        [(b["roundness"], b["solidity"]) for b in before],
        [(a["roundness"], a["solidity"]) for a in after])

print("--- edge cases ---")
labels, dapi, marker = two_nuclei(4)
flat = np.full(labels.shape, 800.0)
ff = NF.features(labels, 1, dapi, flat)
chk("a flat marker has no spots", ff["marker_spots"], 0)
chk("a flat marker with no signal above the background has a blank marker_cv",
    ff["marker_cv"], "")
try:
    NF.features(labels, 99, dapi, marker)
    chk("features() raises for a nucleus id the label image does not have", False)
except ValueError:
    chk("features() raises for a nucleus id the label image does not have", True)
sl = NF.bboxes(labels)
chk("bboxes() gives the slice of each label",
    NF.features(labels, 2, dapi, marker, bbox=sl[2], background=NF.background(labels, marker)),
    NF.features(labels, 2, dapi, marker))

# ---------------------------------------------------------------- the stage

print("--- the stage, on a stand-in for pylibCZIrw ---")
from _fixture import use_temp_study, load_stage, temp_study      # noqa: E402

PAIRED = {"layout": "paired", "markers": ["AF568", "AF488"], "channels": []}
STUDY = use_temp_study(acquisition=PAIRED)
OUT = STUDY.out_root
MARKER = "AF568"
W, H = 120, 60
READS = []
OPENS = []
DROP = {"at": None, "thread": None}


class _Doc:
    def __init__(self, path):
        self.name = os.path.basename(path)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, roi, plane, scene, zoom=1.0):
        if DROP["at"] == int(scene):
            # The slide drive goes away mid-section and comes back 0.3 s later.
            DROP["at"] = None
            gone = STUDY.source_dir + ".gone"
            os.rename(STUDY.source_dir, gone)
            t = threading.Timer(0.3, os.rename, (gone, STUDY.source_dir))
            t.start()
            DROP["thread"] = t
            raise OSError(5, "Input/output error")
        READS.append((self.name, int(scene), int(plane["C"]), tuple(roi)))
        x, y, w, h = roi
        _labels, dapi, marker = two_nuclei(int(scene) * 100 + int(x), spots_a=5)
        a = dapi if int(plane["C"]) == 0 else marker
        return np.clip(a[:h, :w], 0, 65000).astype(np.uint16)[None]


class _PyCzi:
    @staticmethod
    def open_czi(path, **_kw):
        OPENS.append(os.path.basename(path))
        return _Doc(path)


sys.modules["pylibCZIrw"] = type(sys)("pylibCZIrw")
sys.modules["pylibCZIrw"].czi = _PyCzi
M = load_stage("06l_nucleus_features.py")
D5 = M.D5
G5 = D5.G5


def _no_model():
    raise AssertionError("06l loaded StarDist")


D5.load_model = _no_model


def write_rows(path, rows, keys=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    keys = keys or list(rows[0])
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def read_rows(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


UIDS = [f"AB12_s01a_sc{s:02d}" for s in range(4)]
SCENE = {u: i + 1 for i, u in enumerate(UIDS)}
G5.use_marker(MARKER)
write_rows(G5.GEOM_CSV, [{"scene_uid": u, "animal": "AB12", "marker": MARKER,
                          "czi_file": "AB12_1a.czi", "scene_index": str(SCENE[u]),
                          "scene_w": "400", "m00": "1", "m01": "0", "m02": "0",
                          "m10": "0", "m11": "1", "m12": "0"} for u in UIDS])
boxes = []
for u in UIDS:
    for x0, kind in ((10, "roi"), (200, "background")):
        boxes.append({"scene_uid": u, "animal": "AB12", "marker": MARKER, "roi_kind": kind,
                      "region": "Dm" if kind == "roi" else "__background__", "seed_n": "1",
                      "czi_x0": str(x0), "czi_y0": "20", "czi_w": str(W), "czi_h": str(H),
                      "sec_x": "0", "sec_y": "0", "sec_r": "20", "sec_poly": "",
                      "area_um2": "1000.0"})
write_rows(G5.BOX_CSV, boxes)
open(os.path.join(STUDY.source_dir, "AB12_1a.czi"), "wb").close()
LABELS = two_nuclei(0)[0]
nuclei = []
for u in UIDS:
    D5.save_outlines(MARKER, u, {1: (LABELS, 10, 20), 2: (LABELS, 200, 20)})
    for bi, x0 in ((1, 10), (2, 200)):
        for nid in (1, 2):
            row = {c: "" for c in D5.COLUMNS}
            row.update(scene_uid=u, animal="AB12", marker=MARKER, roi_kind="roi",
                       region="Dm", seed_n="1", roi_index=str(bi), nucleus_id=str(nid),
                       czi_x=str(x0 + 30 + 60 * (nid - 1)), czi_y="50")
            nuclei.append(row)
# A row of the other marker: never touched, and never given features under AF568.
other_row = dict(nuclei[0], marker="AF488")
write_rows(D5.NUCLEI_CSV, nuclei + [other_row], D5.COLUMNS)


def digest(path):
    with open(path, "rb") as fh:
        return hashlib.sha1(fh.read()).hexdigest()


NUCLEI_SHA = digest(D5.NUCLEI_CSV)
LAST = [""]


def run(*argv):
    sys.argv = ["06l_nucleus_features.py"] + list(argv)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = M.main()
    LAST[0] = buf.getvalue()
    print("     | " + LAST[0].strip().replace("\n", "\n     | "))
    return rc


def scenes_read():
    return sorted({s for _n, s, _c, _r in READS})


def features_rows():
    return read_rows(M.FEATURES_CSV)


print("--- resume ---")
chk("--limit 2 exits 0", run("--limit", "2"), 0)
first = scenes_read()
chk("...read exactly 2 sections", len(first), 2)
chk("...and checkpointed exactly their nuclei",
    sorted({r["scene_uid"] for r in features_rows()}),
    sorted(u for u in UIDS if SCENE[u] in first))
chk("...4 rows per section", len(features_rows()), 8)
del READS[:]
chk("a re-run exits 0", run(), 0)
chk("...and reads only the other 2", scenes_read(),
    sorted(SCENE[u] for u in UIDS if SCENE[u] not in first))
rows = features_rows()
chk("one row per AF568 roi_nuclei.csv row", len(rows), len(nuclei))
chk("the columns are the stated ones", list(rows[0]),
    ["scene_uid", "marker", "roi_index", "nucleus_id", "roundness", "solidity",
     "marker_cv", "marker_spots", "other_marker_median"])
chk("keyed like roi_nuclei.csv",
    sorted((r["scene_uid"], r["roi_index"], r["nucleus_id"]) for r in rows),
    sorted((r["scene_uid"], r["roi_index"], r["nucleus_id"]) for r in nuclei))
chk("other_marker_median is blank under the paired layout",
    {r["other_marker_median"] for r in rows}, {""})
chk("the granular nucleus (id 1) has more spots than the smooth one (id 2)",
    all(int(a["marker_spots"]) > int(b["marker_spots"])
        for a in rows if a["nucleus_id"] == "1"
        for b in rows if b["nucleus_id"] == "2"
        and (b["scene_uid"], b["roi_index"]) == (a["scene_uid"], a["roi_index"])))
chk("only the marker plane was read, never DAPI",
    {c for _n, _s, c, _r in READS}, {D5.plane_index_for(MARKER)})
del READS[:]
run()
chk("a re-run with nothing changed reads no pixels", READS, [])
snapshot = digest(M.FEATURES_CSV)

print("--- a section whose outlines changed is read again, alone ---")
u2 = UIDS[2]
D5.save_outlines(MARKER, u2, {1: (LABELS, 10, 20), 2: (LABELS, 200, 20)})
os.utime(D5.outline_path(MARKER, u2), ns=(1, 1))
del READS[:]
run()
chk("...only that section is read", scenes_read(), [SCENE[u2]])
chk("...and the file has no duplicate rows", len(features_rows()), len(nuclei))

print("--- a torn checkpoint ---")
with open(M.FEATURES_CSV, "a", encoding="utf-8", newline="") as fh:
    fh.write(f"{UIDS[3]},{MARKER},1,1,0.9")          # a half-written row
del READS[:]
run()
chk("a torn last row is cut back and nothing is read again", READS, [])
chk("...and the file is whole", len(features_rows()), len(nuclei))
# Rows of a section with no done line (the run died between the two writes).
with open(M.FEATURES_CSV, "a", encoding="utf-8", newline="") as fh:
    fh.write(f"AB12_s09a_sc09,{MARKER},1,1,0.9,0.9,0.1,1,\r\n")
run()
chk("rows of a section never logged done are dropped",
    "AB12_s09a_sc09" in {r["scene_uid"] for r in features_rows()}, False)

print("--- --force reads everything again, to the same file ---")
del READS[:]
run("--force")
chk("--force reads every section", scenes_read(), sorted(SCENE.values()))
chk("...and writes the same features", sorted(map(tuple, (r.values() for r in features_rows()))),
    sorted(map(tuple, (r.values() for r in rows))))

print("--- a section with no saved outlines is skipped, never re-segmented ---")
os.remove(D5.outline_path(MARKER, UIDS[0]))
del READS[:]
chk("exits 0", run("--force"), 0)
chk("...the section is named as not done", UIDS[0] in LAST[0] and "outline" in LAST[0])
chk("...its rows are gone rather than kept stale",
    UIDS[0] in {r["scene_uid"] for r in features_rows()}, False)
chk("...and it was not read", SCENE[UIDS[0]] in scenes_read(), False)

print("--- --limit is not spent on a section that cannot be done ---")
os.remove(M.DONE_CSV)                         # every section to read again
del READS[:]
run("--limit", "1")
chk("--limit 1, the first section unable to be done: the next one is read",
    scenes_read(), [SCENE[UIDS[1]]])
chk("...and the one not done is named", f"{UIDS[0]}: NOT DONE" in LAST[0])
del READS[:]
run("--limit", "1")
chk("...a second --limit 1 goes on to the one after", scenes_read(), [SCENE[UIDS[2]]])
run()

print("--- the drive drops mid-section: waited for, the section read again ---")
before = sorted(map(tuple, (r.values() for r in features_rows())))
DROP["at"] = SCENE[UIDS[2]]
del READS[:]
chk("exits 0", run("--force"), 0)
DROP["thread"].join()
chk("...the section is read again whole after the drop: each box once",
    sum(1 for _n, s_, _c, _r in READS if s_ == SCENE[UIDS[2]]), 2)
chk("...and the file is what an uninterrupted run writes",
    sorted(map(tuple, (r.values() for r in features_rows()))), before)
chk("...nothing listed as not done but the one with no outlines",
    "1 section(s) not done" in LAST[0])

print("--- roi_nuclei_untouched ---")
chk("roi_nuclei.csv is byte-identical after every run", digest(D5.NUCLEI_CSV), NUCLEI_SHA)

print("--- a refused run writes nothing ---")
with temp_study(acquisition=PAIRED) as empty:
    os.makedirs(os.path.join(empty.out_root, "results"), exist_ok=True)
    with open(os.path.join(empty.out_root, "results", "roi_nuclei.csv"), "w",
              encoding="utf-8") as fh:
        fh.write(",".join(D5.COLUMNS) + "\n")
    ME = load_stage("06l_nucleus_features.py", name="lsstage_06l_empty")
    sys.argv = ["06l_nucleus_features.py"]
    with contextlib.redirect_stdout(io.StringIO()):
        rc = ME.main()
    chk("no boxes: exit 1", rc, 1)
    chk("...and no nucleus_features.csv for the app to read as done",
        os.path.exists(ME.FEATURES_CSV), False)

print("--- multiplex: the other marker's median inside the nucleus ---")

chk("paired: no other marker", M.other_marker(MARKER), None)
TWO_NUCLEAR = {"layout": "multiplex", "channels": [
    {"name": "DAPI", "role": "nuclear", "czi_name": "DAPI", "index": 0},
    {"name": "pERK", "role": "marker", "czi_name": "AF568", "index": 1,
     "segment": "nuclear"},
    {"name": "PCNA", "role": "marker", "czi_name": "AF488", "index": 2,
     "segment": "nuclear"}]}
with temp_study(acquisition=TWO_NUCLEAR):
    MX = load_stage("06l_nucleus_features.py", name="lsstage_06l_multiplex")
    chk("multiplex: pERK's other marker is PCNA", MX.other_marker("pERK"), "PCNA")
    chk("...and PCNA's is pERK", MX.other_marker("PCNA"), "pERK")

    class _PlaneDoc:
        def read(self, roi, plane, scene, zoom=1.0):
            return np.full((H, W), 100.0 * (int(plane["C"]) + 1))[None]

    box = dict(boxes[0], marker="pERK")
    got = MX.measure(_PlaneDoc(), "pERK", UIDS[0], [box], {1: (LABELS, 10, 20)},
                     {1: [1, 2]}, 1)
    chk("...other_marker_median is PCNA's plane (index 2) inside the nucleus",
        [r[-1] for r in got], ["300.0", "300.0"])

print()
if failures:
    print(f"FAILED: {len(failures)}")
    for n in failures:
        print("  " + n)
    sys.exit(1)
print("all ok")
