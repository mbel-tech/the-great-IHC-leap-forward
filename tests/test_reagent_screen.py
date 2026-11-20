"""The reagent-control signal screen (ls_reagent_screen, 01m_reagent_screen).

Features on synthetic 512 x 512 tiles: a stained tile has bright spots inside
DAPI nuclei and a heavy right tail; a control tile has autofluorescent puncta
that sit anywhere and appear in both marker channels on a shared background.
Every feature must survive an additive offset and a gain change (the ~2x
background batch between animals must not move them). Then the scoring rule,
the guards, the run gate, and one run of the stage on a stand-in for pylibCZIrw.

    python tests/test_reagent_screen.py
"""

import csv
import json
import os
import sys
import zlib

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(REPO, "scripts"), HERE]

import ls_reagent_screen as RS                                   # noqa: E402

failures = []


def chk(name, got, want=True):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name:<70} {got!r}" + ("" if ok else f"  (wanted {want!r})"))
    if not ok:
        failures.append(name)


# ---------------------------------------------------------------- synthetic tiles

N = RS.TILE_PX


def _smooth(a, sigma):
    """A wide Gaussian blur by FFT, for backgrounds only (not the code under test)."""
    fy = np.fft.fftfreq(a.shape[0])[:, None]
    fx = np.fft.fftfreq(a.shape[1])[None, :]
    k = np.exp(-2 * (np.pi * sigma) ** 2 * (fx ** 2 + fy ** 2))
    return np.real(np.fft.ifft2(np.fft.fft2(a) * k))


def _field(rng, sigma=12.0, sd=1.0):
    f = _smooth(rng.normal(size=(N, N)), sigma)
    return f / f.std() * sd


def _discs(centres, radius):
    r = int(np.ceil(radius))
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
    disc = yy ** 2 + xx ** 2 <= radius ** 2
    out = np.zeros((N + 2 * r, N + 2 * r), bool)
    for cy, cx in centres:
        out[cy:cy + 2 * r + 1, cx:cx + 2 * r + 1] |= disc
    return out[r:r + N, r:r + N]


def _nuclei(rng, n=110, radius=13):
    centres = rng.integers(radius, N - radius, size=(n, 2))
    return centres, _discs(centres, radius)


def make_dapi(rng, nuc_mask):
    return 400 + 60 * rng.normal(size=(N, N)) + 3000 * _smooth(nuc_mask.astype(float), 1.0)


def stained_tile(seed):
    """DAPI, marker A, marker B: A's 40 spots sit 90 % in nuclei, nucleus-sized."""
    rng = np.random.default_rng(seed)
    centres, nuc = _nuclei(rng)
    dapi = make_dapi(rng, nuc)

    def marker():
        inside = centres[rng.choice(len(centres), 36, replace=False)]
        outside = []
        while len(outside) < 4:
            p = rng.integers(8, N - 8, size=2)
            if not nuc[p[0], p[1]]:
                outside.append(p)
        spots = _discs(np.vstack([inside, outside]), 6)
        bg = 900 + _field(rng, 14, 120)
        return bg + 40 * rng.normal(size=(N, N)) + 9000 * _smooth(spots.astype(float), 1.0)

    return dapi, marker(), marker()


def control_tile(seed):
    """No primary: shared autofluorescence and the same 40 puncta in both markers."""
    rng = np.random.default_rng(seed)
    _c, nuc = _nuclei(rng)
    dapi = make_dapi(rng, nuc)
    af = 1000 + _field(rng, 10, 250)
    puncta = _discs(rng.integers(4, N - 4, size=(40, 2)), 1.5)
    shared = af + 6000 * _smooth(puncta.astype(float), 0.7)
    a = shared + 25 * rng.normal(size=(N, N))
    b = 0.6 * shared + 25 * rng.normal(size=(N, N))
    return dapi, a, b


ALL = np.ones((N, N), bool)

print("--- stained_vs_control ---")
sd, sa, sb = stained_tile(1)
cd, ca, cb = control_tile(2)
f1s, f1c = RS.f1_nuclear_enrichment(sa, sd), RS.f1_nuclear_enrichment(ca, cd)
f2s, f2c = RS.f2_channel_similarity(sa, sb, ALL), RS.f2_channel_similarity(ca, cb, ALL)
f3s, f3c = RS.f3_tail_spread(sa, ALL), RS.f3_tail_spread(ca, ALL)
print(f"     stained f1={f1s} f2={f2s} f3={f3s}")
print(f"     control f1={f1c} f2={f2c} f3={f3c}")
chk("f1 is above 2.0 for the stained tile", f1s is not None and f1s > 2.0)
chk("f1 is below 1.5 for the control tile", f1c is not None and f1c < 1.5)
chk("f2 is above 0.8 for the control tile", f2c > 0.8)
chk("f2 is below 0.4 for the stained tile", f2s < 0.4)
chk("f3 is larger for the stained tile", f3s > f3c)
chk("f1 is None with no spots (a flat tile)",
    RS.f1_nuclear_enrichment(np.full((N, N), 500.0), sd), None)

print("--- offset_gain_invariant ---")
for label, (d, a, b) in (("stained", (sd, sa, sb)), ("control", (cd, ca, cb))):
    t = [x * 2.0 + 5000 for x in (d, a, b)]
    chk(f"{label}: f1 unchanged under x*2+5000",
        abs(RS.f1_nuclear_enrichment(t[1], t[0]) - RS.f1_nuclear_enrichment(a, d)) < 1e-6)
    chk(f"{label}: f2 unchanged under x*2+5000",
        abs(RS.f2_channel_similarity(t[1], t[2], ALL) - RS.f2_channel_similarity(a, b, ALL)) < 1e-6)
    chk(f"{label}: f3 unchanged under x*2+5000",
        abs(RS.f3_tail_spread(t[1], ALL) - RS.f3_tail_spread(a, ALL)) < 1e-6)
    # Marker only, DAPI left alone: the batch effect is per channel.
    chk(f"{label}: f1 unchanged when only the marker shifts",
        abs(RS.f1_nuclear_enrichment(a * 1.7 + 800, d) - RS.f1_nuclear_enrichment(a, d)) < 1e-6)

print("--- robust_z ---")
z = RS.robust_z({"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0, "e": 100.0})
chk("robust_z of the median is 0", z["c"], 0.0)
chk("robust_z scales by 1.4826 MAD", round(z["e"], 4), round(97 / 1.4826, 4))
chk("MAD 0 gives 0", RS.robust_z({"a": 5.0, "b": 5.0, "c": 5.0, "d": 9.0}),
    {"a": 0.0, "b": 0.0, "c": 0.0, "d": 0.0})
chk("constants as stated", (RS.TILES, RS.TILE_PX, RS.SPOT_K, RS.Z_CANDIDATE),
    (12, 512, 5.0, 3.0))


def row(uid, f1, f2, f3, marker="M1", dapi=True, expo=True, **kw):
    return dict({"scene_uid": uid, "marker": marker, "file": uid.split("_sc")[0] + ".czi",
                 "f1": f1, "f2": f2, "f3": f3,
                 "guard_dapi_ok": dapi, "guard_exposure_ok": expo}, **kw)


rng = np.random.default_rng(11)
STAINED = [row(f"A{i:02d}_s01a_sc{i:02d}", float(rng.normal(4, 0.5)), float(rng.normal(0.1, 0.05)),
               float(rng.normal(2.0, 0.2))) for i in range(30)]
CONTROLS = [row("NC1_s01a_sc00", 1.0, 0.9, 0.4), row("NC2_s01a_sc00", 1.1, 0.85, 0.5)]

print("--- score_candidate ---")
out = RS.score(STAINED + CONTROLS)
cands = [r["scene_uid"] for r in out if r["candidate"]]
chk("exactly the 2 control rows are candidates", sorted(cands), ["NC1_s01a_sc00", "NC2_s01a_sc00"])
chk("their status is candidate",
    [r["status"] for r in out if r["candidate"]], ["candidate", "candidate"])
chk("rank >= 3 on every candidate", all(r["rank"] >= 3 for r in out if r["candidate"]))
chk("rank is the minimum available z",
    all(r["rank"] == min(r["z1"], r["z2"], r["z3"]) for r in out if r["candidate"]))
chk("n_features counts the available features", {r["n_features"] for r in out}, {3})
nc1 = next(r for r in out if r["scene_uid"] == "NC1_s01a_sc00")
chk("signs: control-like is positive on every z", nc1["z1"] > 0 and nc1["z2"] > 0 and nc1["z3"] > 0)
chk("score keeps the input order", [r["scene_uid"] for r in out],
    [r["scene_uid"] for r in STAINED + CONTROLS])
chk("score does not change its input", "z1" not in STAINED[0])

print("--- per marker ---")
other = [dict(r, marker="M2", f3=r["f3"] + 10) for r in STAINED]
out2 = RS.score(STAINED + CONTROLS + other)
chk("a second marker's rows do not move the first marker's z",
    [r["z3"] for r in out2 if r["marker"] == "M1"], [r["z3"] for r in out])

print("--- one_feature_is_review ---")
only3 = [row(r["scene_uid"], None, None, r["f3"]) for r in STAINED] + \
        [row("NC1_s01a_sc00", None, None, 0.4)]
o3 = RS.score(only3)
nc = o3[-1]
chk("one feature: never a candidate", nc["candidate"], False)
chk("one feature, control-like: review", nc["status"], "review")
chk("one feature: n_features is 1", nc["n_features"], 1)
chk("no stained row is a candidate either", [r for r in o3 if r["candidate"]], [])
chk("blank strings read as missing features",
    RS.score([dict(r, f1="", f2="") for r in only3])[-1]["status"], "review")

print("--- guards ---")
g = RS.score(STAINED + [row("NC1_s01a_sc00", 1.0, 0.9, 0.4, expo=False)])[-1]
chk("exposure guard failed: unusual", g["status"], "unusual")
chk("...and not a candidate", g["candidate"], False)
g = RS.score(STAINED + [row("NC1_s01a_sc00", 1.0, 0.9, 0.4, dapi=False)])[-1]
chk("DAPI guard failed: unusual", g["status"], "unusual")
g = RS.score(STAINED + [row("NC1_s01a_sc00", 4.0, 0.1, 2.0, expo=False)])[-1]
chk("a stained-looking row failing a guard is not called a control", g["status"], "")
chk("exposure_ok: the modal exposure per marker",
    RS.exposure_ok({"a.czi": "100", "b.czi": "100", "c.czi": "250", "d.czi": ""}),
    {"a.czi": True, "b.czi": True, "c.czi": False, "d.czi": None})
fr = {f"s{i}": 0.40 + 0.01 * (i % 5) for i in range(20)}
fr["empty"] = 0.01
ok = RS.tissue_ok(fr)
chk("tissue_ok: an empty section is outside the study's range", ok["empty"], False)
chk("tissue_ok: the rest are inside", all(ok[k] for k in fr if k != "empty"))
chk("tissue_ok: an empty section is caught even when the rest are identical (MAD 0)",
    RS.tissue_ok({"a": "0.40", "b": "0.40", "c": "0.40", "empty": "0.01", "gone": None}),
    {"a": True, "b": True, "c": True, "empty": False, "gone": False})

print("--- confirmed_excluded ---")
confirmed = [row(f"RC{i}_s01a_sc00", 1.0, 0.9, 0.4, confirmed=True) for i in range(12)]
oc = RS.score(STAINED + CONTROLS + confirmed)
chk("confirmed rows do not enter robust_z",
    [(r["z1"], r["z2"], r["z3"]) for r in oc[:32]], [(r["z1"], r["z2"], r["z3"]) for r in out])
chk("confirmed rows are not scored", {(r["z1"], r["status"], r["candidate"]) for r in oc[32:]},
    {(None, "", False)})
chk("...the controls are still candidates beside them",
    sorted(r["scene_uid"] for r in oc if r["candidate"]), ["NC1_s01a_sc00", "NC2_s01a_sc00"])

print("--- rejected ---")
orj = RS.score(STAINED + [CONTROLS[0], dict(CONTROLS[1], rejected=True)])
chk("a rejected row is never proposed again",
    [r["scene_uid"] for r in orj if r["status"]], ["NC1_s01a_sc00"])
chk("...but stays in the population",
    orj[0]["z1"] == RS.score(STAINED + CONTROLS)[0]["z1"])

print("--- file_rollup ---")
chk("fraction of a file's sections that are candidates",
    RS.file_rollup([{"scene_uid": "a_sc00", "file": "a.czi", "candidate": True},
                    {"scene_uid": "a_sc01", "file": "a.czi", "candidate": False},
                    {"scene_uid": "b_sc00", "file": "b.czi", "candidate": False}]),
    {"a.czi": 0.5, "b.czi": 0.0})
chk("a section counts once, whichever of its markers is the candidate",
    RS.file_rollup([{"scene_uid": "a_sc00", "file": "a.czi", "candidate": True},
                    {"scene_uid": "a_sc00", "file": "a.czi", "candidate": False},
                    {"scene_uid": "a_sc01", "file": "a.czi", "candidate": False}]),
    {"a.czi": 0.5})

print("--- screen_runs_when ---")
yes_with = {"reagent_controls": {"declared": "yes", "files": {"NC_1.czi": {"kind": "no_primary"}}}}
chk("unsure runs", RS.should_run({"reagent_controls": {"declared": "unsure"}}, False), True)
chk("yes with no entries runs", RS.should_run({"reagent_controls": {"declared": "yes"}}, False), True)
chk("yes with entries does not", RS.should_run(yes_with, False), False)
chk("no does not", RS.should_run({"reagent_controls": {"declared": "no"}}, False), False)
chk("nothing declared does not", RS.should_run({}, False), False)
chk("--force always runs", RS.should_run(yes_with, True), True)
chk("--force runs under no", RS.should_run({"reagent_controls": {"declared": "no"}}, True), True)

# ---------------------------------------------------------------- the stage

print("--- the stage, on a stand-in for pylibCZIrw ---")
from _fixture import use_temp_study, load_stage                  # noqa: E402

STUDY = use_temp_study(reagent_controls={"declared": "unsure"})
OUT = STUDY.out_root
NC_UIDS = {"LS09_s01a_sc01"}          # the no-primary section in the stand-in study

READS = []


class _Rect:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h


class _Doc:
    """Every scene a 4096 x 3072 frame, tissue in its middle; tiles seeded by position."""

    scenes_bounding_rectangle = {s: _Rect(10000 * s, 0, 4096, 3072) for s in range(4)}

    def __init__(self, path):
        self.name = os.path.basename(path)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, roi, plane, scene, zoom):
        READS.append((self.name, scene, plane["C"], zoom))
        x, y, w, h = roi
        if zoom < 1:
            oh, ow = int(round(h * zoom)), int(round(w * zoom))
            a = np.full((oh, ow), 150, np.uint16)
            a[oh // 6: oh * 5 // 6, ow // 6: ow * 5 // 6] = 2500
            a[:, :3] = 0                         # never scanned
            return a[None]
        uid = f"{self.name[:-4].replace('_1a', '_s01a')}_sc{scene:02d}"
        img = _tile(uid, uid in NC_UIDS)[plane["C"]]
        # A per-animal batch: gain and offset differ, which no feature may see.
        gain = 2.0 if self.name.startswith("LS0") else 1.0
        return np.clip(img * gain + 300 * gain, 0, 65000).astype(np.uint16)[None]


_TILES = {}


def _tile(uid, control):
    """One tile per section (every position alike), made once: generating them is the
    slow part of this suite, not screening them."""
    key = (uid, control)
    if key not in _TILES:
        d, a, b = (control_tile if control else stained_tile)(zlib.crc32(uid.encode()))
        _TILES[key] = (d.astype(np.float32), a.astype(np.float32), b.astype(np.float32))
    return _TILES[key]


class _PyCzi:
    @staticmethod
    def open_czi(path, **_kw):
        return _Doc(path)


sys.modules["pylibCZIrw"] = type(sys)("pylibCZIrw")
sys.modules["pylibCZIrw"].czi = _PyCzi
M = load_stage("01m_reagent_screen.py")
MARKER = M.MARKERS[0]

FILES = [f"LS{a:02d}_1a.czi" for a in range(3, 13)]
scenes, files, focus = [], [], []
for f in FILES:
    open(os.path.join(STUDY.source_dir, f), "wb").close()
    for s in range(2):
        uid = f"{f[:-4].replace('_1a', '_s01a')}_sc{s:02d}"
        scenes.append({"scene_uid": uid, "file": f, "animal": f[:4], "slide": "1",
                       "variant": "a", "marker_channel": MARKER, "scene_index": str(s),
                       "section_order": str(s)})
        focus.append({"scene_uid": uid, "file": f, "tissue_fraction": "0.40"})
    files.append({"file": f, "marker_channel": MARKER, "marker_exposure_ms": "120",
                  "clip_ceiling": "65535"})
NC_UIDS = {"LS09_s01a_sc01"}


def _write(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


_write(os.path.join(OUT, "manifest", "manifest_scenes.csv"), scenes)
_write(os.path.join(OUT, "manifest", "manifest_files.csv"), files)
_write(os.path.join(OUT, "qc", "focus.csv"), focus)
with open(os.path.join(OUT, "qc", "display_ranges.json"), "w", encoding="utf-8") as fh:
    json.dump({"DAPI": {"lo": 200, "hi": 4000}, MARKER: {"lo": 500, "hi": 12000}}, fh)


def run(*argv):
    saved = sys.argv
    sys.argv = ["01m"] + list(argv)
    try:
        return M.main()
    finally:
        sys.argv = saved


def read_screen():
    with open(os.path.join(OUT, "qc", "reagent_screen.csv"), newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


run()
got = read_screen()
chk("the CSV has the stated columns", list(got[0]),
    ["scene_uid", "file", "marker", "f1", "f2", "f3", "z1", "z2", "z3", "n_features",
     "guard_dapi_ok", "guard_exposure_ok", "candidate", "rank", "status", "note"])
chk("one row per section x marker", len(got), len(scenes))
chk("the no-primary section is the only candidate",
    [r["scene_uid"] for r in got if r["status"] == "candidate"], sorted(NC_UIDS))
chk("F2 is empty: one marker per scan", {r["f2"] for r in got}, {""})
chk("...and the row says so", all("F2" in r["note"] for r in got))
chk("the batch gain on LS0x does not make them candidates",
    [r["scene_uid"] for r in got if r["scene_uid"].startswith("LS0") and r["status"]
     and r["scene_uid"] not in NC_UIDS], [])
chk("no tile touches a clipped pixel and reads stay native",
    {z for _n, _s, _c, z in READS if z >= 1}, {1.0})
chk("the contact strip is written", os.path.exists(os.path.join(OUT, "qc", "reagent_screen.png")))
import ls_reagent_controls as RC                                 # noqa: E402
cfg = dict(STUDY.config, reagent_controls={"declared": "unsure"})
chk("05b/05c would warn about the unanswered candidate", bool(RC.warn_unanswered(cfg)), True)
first = [(r["scene_uid"], r["f1"], r["f3"]) for r in got]
run()
chk("the same sections give the same features on a re-run (seeded tiles)",
    [(r["scene_uid"], r["f1"], r["f3"]) for r in read_screen()], first)

print("--- all stained: zero candidates ---")
NC_UIDS = set()
run()
got = read_screen()
chk("zero candidates on a study with no reagent controls",
    [r["scene_uid"] for r in got if r["status"] == "candidate"], [])
chk("warn_unanswered has nothing to say", RC.warn_unanswered(cfg), None)

print("--- a confirmed control is passed through, not scored ---")
NC_UIDS = {"LS09_s01a_sc01"}
M.CONFIG["reagent_controls"] = {"declared": "unsure",
                                "scenes": {"LS09_s01a_sc01": {"kind": "no_primary",
                                                              "markers": []}}}
run("--force")
got = {r["scene_uid"]: r for r in read_screen()}
chk("the confirmed section has no z and no status",
    (got["LS09_s01a_sc01"]["z1"], got["LS09_s01a_sc01"]["status"]), ("", ""))
chk("...and says why", "confirmed" in got["LS09_s01a_sc01"]["note"])

print("--- a missing CZI is listed, not dropped ---")
os.remove(os.path.join(STUDY.source_dir, "LS12_1a.czi"))
run("--force")
got = {r["scene_uid"]: r for r in read_screen()}
chk("its sections still have rows", "LS12_s01a_sc00" in got and "LS12_s01a_sc01" in got)
chk("...unscreened, with the reason and no status",
    ("missing" in got["LS12_s01a_sc00"]["note"], got["LS12_s01a_sc00"]["f3"],
     got["LS12_s01a_sc00"]["guard_dapi_ok"], got["LS12_s01a_sc00"]["status"]),
    (True, "", "0", ""))

print("--- F2 inside the stage: two markers of one scan ---")
plan2 = {"A": (1, None), "B": (2, "F1 skipped: test")}
zoom = M.OV.BASE_PX_UM / M.OV.TARGET_UM
two = {}
for uid, fname, s_ in (("LS09_s01a_sc01", "LS09_1a.czi", 1), ("LS10_s01a_sc00", "LS10_1a.czi", 0)):
    doc = _Doc(os.path.join(STUDY.source_dir, fname))
    two[uid] = M.screen_section(doc, doc.scenes_bounding_rectangle[s_], s_, uid, ["A", "B"],
                                0, plan2, zoom, 65535)
print(f"     control f2={two['LS09_s01a_sc01'][0]['A']['f2']:.3f}  "
      f"stained f2={two['LS10_s01a_sc00'][0]['A']['f2']:.3f}")
chk("F2 is high for the control section", two["LS09_s01a_sc01"][0]["A"]["f2"] > 0.8)
chk("F2 is low for a stained section", two["LS10_s01a_sc00"][0]["A"]["f2"] < 0.4)
chk("F2 is symmetric between the two markers",
    two["LS10_s01a_sc00"][0]["A"]["f2"] == two["LS10_s01a_sc00"][0]["B"]["f2"])
chk("F1 skipped only where the plan says so",
    (two["LS10_s01a_sc00"][0]["A"]["f1"] is not None, two["LS10_s01a_sc00"][0]["B"]["f1"]),
    (True, None))
chk("all 12 tiles read", two["LS10_s01a_sc00"][1], RS.TILES)

print("--- which planes, and when F1 is skipped ---")
paired = {"acquisition": {"layout": "paired", "markers": ["AF568", "AF488"],
                          "marker_segmentation": {"AF488": {"segment": "own"}}},
          "channels": {"dapi_index": 0, "marker_index": 1}}
nuc_c, plan, _lay = M.channel_plan(paired)
chk("paired: DAPI plane 0, every marker on plane 1", (nuc_c, {m: p for m, (p, _w) in plan.items()}),
    (0, {"AF568": 1, "AF488": 1}))
chk("paired: F1 skipped where the DAPI is declared unusable",
    (plan["AF568"][1], bool(plan["AF488"][1])), (None, True))
multi = {"acquisition": {"layout": "multiplex", "channels": [
    {"name": "DAPI", "role": "nuclear", "index": 0},
    {"name": "pERK", "role": "marker", "index": 2},
    {"name": "PCNA", "role": "marker", "index": 1, "segment": "own"}]},
    "channels": {"dapi_index": 0, "marker_index": 1}}
nuc_c, plan, _lay = M.channel_plan(multi)
chk("multiplex: each marker on its own declared plane",
    {m: p for m, (p, _w) in plan.items()}, {"pERK": 2, "PCNA": 1})
chk("multiplex: F1 skipped for a marker segmented on its own channel",
    (plan["pERK"][1], bool(plan["PCNA"][1])), (None, True))

print("--- the gate ---")
M.CONFIG["reagent_controls"] = {"declared": "no"}
os.remove(os.path.join(OUT, "qc", "reagent_screen.csv"))
del READS[:]
chk("declared no: skips and exits 0", run(), 0)
chk("...reads no pixels", READS, [])
chk("...and leaves a header-only CSV so the stage reads as done", read_screen(), [])

print()
print(f"{len(failures)} FAILED" if failures else "ALL PASS")
sys.exit(1 if failures else 0)
