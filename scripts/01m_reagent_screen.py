"""Stage 01m - screen every section for reagent controls nobody named.

Proposes, never assigns. A section whose marker channel looks like a
no-primary or unstained control (ls_reagent_screen: F1 nuclear spot enrichment,
F2 between-marker similarity, F3 tail spread, all blind to offset and gain) is
listed as a `candidate` in qc/reagent_screen.csv; the operator confirms or
rejects it in Set up. Until then 05b and 05c warn and carry on
(ls_reagent_controls.warn_unanswered).

Runs when `reagent_controls.declared` is `unsure`, or `yes` with nothing
confirmed yet, or with --force. Otherwise it says why and exits 0, leaving a
header-only CSV when there is none, so the stage reads as done.

Per section, TILES tiles of TILE_PX native pixels are drawn inside the DAPI
tissue mask (01_overviews' own threshold on the raw nuclear plane at overview zoom;
01 itself thresholds after the tile field),
seeded by the scene uid, and a tile touching a pixel at the clip ceiling in any
plane is skipped. Per section x marker: F1 is the median over tiles (skipped for
a marker segmented on its own channel, whose DAPI is declared unusable), F2 the
median over tiles of the best Spearman against another marker of the same scan
(empty for one marker per scan), F3 over the pooled tissue pixels.

Limits: it cannot see isotype or pre-adsorbed controls, and cannot tell a
no-primary section from a failed stain or a non-expressing animal. Thresholds
are provisional until a validation run on a study with known controls.

Never reads groups.by_animal: a reagent control is not blinding information.

Run:  python 01m_reagent_screen.py [--force]
"""

import argparse
import csv
import hashlib
import importlib.util
import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from ls_config import CONFIG, CONFIG_PATH  # noqa: E402
import czi_read as CR  # noqa: E402
import ls_channels as CH  # noqa: E402
import ls_io as IO  # noqa: E402
import ls_reagent_controls as RC  # noqa: E402
import ls_reagent_screen as RS  # noqa: E402

# 01's tissue threshold and scene reader: 01's method on the same plane and zoom.
# Not bit-for-bit 01's mask - 01 thresholds after the tile field, this the raw plane.
_spec = importlib.util.spec_from_file_location("_ov_01m", os.path.join(_HERE, "01_overviews.py"))
OV = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(OV)

SOURCE_DIR = CONFIG["source_dir"]
OUT_ROOT = CONFIG["out_root"]
QC_DIR = os.path.join(OUT_ROOT, "qc")
SCREEN_CSV = os.path.join(OUT_ROOT, *RC.SCREEN_CSV)
SCREEN_PNG = os.path.join(QC_DIR, "reagent_screen.png")
FOCUS_CSV = os.path.join(QC_DIR, "focus.csv")
RANGES_PATH = os.path.join(QC_DIR, "display_ranges.json")
MANIFEST_SCENES = os.path.join(OUT_ROOT, "manifest", "manifest_scenes.csv")
MANIFEST_FILES = os.path.join(OUT_ROOT, "manifest", "manifest_files.csv")

SCREEN_KEYS = ["scene_uid", "file", "marker", "f1", "f2", "f3", "z1", "z2", "z3",
               "n_features", "guard_dapi_ok", "guard_exposure_ok", "candidate", "rank",
               "status", "note"]

#: A tile is drawn only where at least this share of it is tissue.
MIN_TILE_TISSUE = 0.5
#: Draws per wanted tile before a section gives up: clipped or edge tiles are skipped.
DRAWS_PER_TILE = 8
#: The contact strip: this many top candidates beside this many median sections.
STRIP_CANDIDATES = 8
STRIP_MEDIANS = 3
THUMB_PX = 256

MARKERS = list(CH.marker_names(CONFIG))


class _Rect:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h


# ---------------------------------------------------------------- channels

def channel_plan(cfg):
    """(nuclear plane, {marker: (plane, f1_skip_reason or None)}, layout).

    The same answers 05c gives (plane_index_for, _segments_own), built here
    from ls_channels rather than by importing 05c.
    """
    acq = cfg.get("acquisition") or {}
    chans = cfg.get("channels") or {}
    dapi_c = int(chans.get("dapi_index", 0))
    mark_c = int(chans.get("marker_index", 1))
    layout = acq.get("layout")
    plan = {}
    if layout == CH.LAYOUT_PAIRED:
        own = {c.name for c in CH.paired_overrides(cfg) if c.segment == CH.SEGMENT_OWN}
        for m in CH.marker_names(cfg):
            plan[m] = (mark_c, "F1 skipped: this marker's DAPI is declared unusable "
                               "(segmented on its own channel)" if m in own else None)
        return dapi_c, plan, layout
    table = CH.parse(acq.get("channels"))
    nuc = CH.nuclear(table)
    nuc_c = int(nuc.index) if nuc is not None and nuc.index is not None else dapi_c
    for c in CH.markers(table):
        why = None
        if nuc is None:
            why = "F1 skipped: the study declares no nuclear channel"
        elif c.segment == CH.SEGMENT_OWN:
            why = "F1 skipped: this marker is segmented on its own channel"
        plan[c.name] = (int(c.index) if c.index is not None else mark_c, why)
    return nuc_c, plan, layout


# ---------------------------------------------------------------- inputs

def _read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def seed_for(uid):
    """A stable seed per section: hashlib, since Python's hash() is salted."""
    return int.from_bytes(hashlib.sha1(uid.encode("utf-8")).digest()[:8], "little")


def tissue_mask(doc, rect, plane, zoom, scene):
    """01's DAPI tissue mask at overview zoom; never-scanned zero pixels excluded."""
    dapi = OV.read_scene(doc, rect, plane, zoom, scene=scene)
    return (dapi > OV.tissue_threshold(dapi)) & (dapi > 0)


def tile_positions(mask, zoom, rect, rng):
    """Native top-left offsets (inside the scene rect) of candidate tiles, drawn
    uniformly over tissue pixels, each at least MIN_TILE_TISSUE tissue."""
    ys, xs = np.nonzero(mask)
    if not len(ys) or rect.w < RS.TILE_PX or rect.h < RS.TILE_PX:
        return
    side = max(1, int(round(RS.TILE_PX * zoom)))
    for _ in range(RS.TILES * DRAWS_PER_TILE):
        i = int(rng.integers(len(ys)))
        x0 = int((xs[i] + 0.5) / zoom) - RS.TILE_PX // 2
        y0 = int((ys[i] + 0.5) / zoom) - RS.TILE_PX // 2
        x0 = min(max(x0, 0), rect.w - RS.TILE_PX)
        y0 = min(max(y0, 0), rect.h - RS.TILE_PX)
        oy, ox = int(y0 * zoom), int(x0 * zoom)
        win = mask[oy:oy + side, ox:ox + side]
        if win.size and win.mean() >= MIN_TILE_TISSUE:
            yield x0, y0


def tile_mask(mask, zoom, x0, y0):
    """The overview mask under one native tile, nearest-neighbour."""
    iy = np.minimum(((y0 + np.arange(RS.TILE_PX)) * zoom).astype(int), mask.shape[0] - 1)
    ix = np.minimum(((x0 + np.arange(RS.TILE_PX)) * zoom).astype(int), mask.shape[1] - 1)
    return mask[np.ix_(iy, ix)]


def read_tile(doc, rect, x0, y0, planes, scene):
    r = _Rect(rect.x + x0, rect.y + y0, RS.TILE_PX, RS.TILE_PX)
    return CR.read_planes(doc, r, planes, scene=scene)


def screen_section(doc, rect, scene, uid, markers, nuc_c, plan, zoom, ceiling):
    """{marker: {f1, f2, f3}}, the tiles used, and the first tile's position."""
    mask = tissue_mask(doc, rect, nuc_c, zoom, scene)
    rng = np.random.default_rng(seed_for(uid))
    planes = {"__nuc": nuc_c}
    planes.update({m: plan[m][0] for m in markers})
    f1 = {m: [] for m in markers}
    f2 = {m: [] for m in markers}
    pooled = {m: [] for m in markers}
    used, first = 0, None
    for x0, y0 in tile_positions(mask, zoom, rect, rng):
        if used >= RS.TILES:
            break
        t = read_tile(doc, rect, x0, y0, planes, scene)
        if any((a >= ceiling).any() for a in t.values()):
            continue                                   # touches a clipped pixel
        tm = tile_mask(mask, zoom, x0, y0) & np.logical_or.reduce(
            [a > 0 for a in t.values()])
        if tm.sum() < 3:
            continue
        used += 1
        first = first or (x0, y0)
        for m in markers:
            a = t[m].astype(np.float64)
            if plan[m][1] is None:
                v = RS.f1_nuclear_enrichment(a, t["__nuc"].astype(np.float64))
                if v is not None:
                    f1[m].append(v)
            others = [RS.f2_channel_similarity(a, t[o].astype(np.float64), tm)
                      for o in markers if o != m]
            others = [v for v in others if v is not None]
            if others:
                f2[m].append(max(others))
            pooled[m].append(a[tm])
    feats = {}
    for m in markers:
        p = np.concatenate(pooled[m]) if pooled[m] else np.zeros(0)
        feats[m] = {"f1": float(np.median(f1[m])) if f1[m] else None,
                    "f2": float(np.median(f2[m])) if f2[m] else None,
                    "f3": RS.f3_tail_spread(p, np.ones(p.shape, bool)) if p.size else None}
    return feats, used, first


# ---------------------------------------------------------------- outputs

def _fmt(v, nd=4):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def write_csv(rows):
    os.makedirs(QC_DIR, exist_ok=True)
    out = [{k: _fmt(r.get(k)) for k in SCREEN_KEYS} for r in rows]
    IO.atomic_write_csv(SCREEN_CSV, out, SCREEN_KEYS)


def _thumb(a, lo, hi):
    if hi <= lo:
        hi = lo + 1
    f = RS.TILE_PX // THUMB_PX
    a = a.astype(np.float64)
    if f > 1:
        a = a[: a.shape[0] // f * f, : a.shape[1] // f * f]
        a = a.reshape(a.shape[0] // f, f, a.shape[1] // f, f).mean(axis=(1, 3))
    return np.clip((a - lo) * 255.0 / (hi - lo), 0, 255).astype(np.uint8)


def write_strip(scored, where, plan, by_uid):
    """qc/reagent_screen.png: per marker, the top candidates beside three
    median sections, every tile at that marker's frozen display range."""
    ranges = {}
    if os.path.exists(RANGES_PATH):
        with open(RANGES_PATH, encoding="utf-8") as fh:
            ranges = json.load(fh)
    lines = []
    for m in dict.fromkeys(r["marker"] for r in scored):
        mine = [r for r in scored if r["marker"] == m and r.get("rank") is not None
                and r["scene_uid"] in where]
        cands = sorted((r for r in mine if r["candidate"]), key=lambda r: -r["rank"])
        cands = cands[:STRIP_CANDIDATES]
        rest = sorted((r for r in mine if not r["candidate"]), key=lambda r: r["rank"])
        mid = len(rest) // 2
        medians = rest[max(0, mid - 1): mid - 1 + STRIP_MEDIANS] if rest else []
        rng = ranges.get(m) or {}
        lo, hi = rng.get("lo"), rng.get("hi")
        tiles = []
        for r, label in [(r, "candidate") for r in cands] + [(r, "median") for r in medians]:
            uid = r["scene_uid"]
            row = by_uid[uid]
            path = os.path.join(SOURCE_DIR, row["file"])
            s = int(row["scene_index"])
            from pylibCZIrw import czi as pyczi
            try:
                with CR.open_czi(path, None, pyczi=pyczi) as doc:
                    rect = doc.scenes_bounding_rectangle[s]
                    x0, y0 = where[uid]
                    a = read_tile(doc, rect, x0, y0, {"m": plan[m][0]}, s)["m"]
            except Exception as exc:  # noqa: BLE001 - the CSV is written; the picture is extra
                print(f"  !! strip: {uid} not re-read ({type(exc).__name__}: {exc})")
                continue
            tiles.append((a, f"{label} {uid}  rank {r['rank']:.1f}"))
        if lo is None or hi is None:
            pool = np.concatenate([a.ravel() for a, _ in tiles]) if tiles else np.zeros(1)
            lo, hi = float(np.percentile(pool, 1)), float(np.percentile(pool, 99.5))
        lines.append((f"{m}: {len(cands)} candidate(s) shown, then {len(medians)} median "
                      f"section(s); range {lo:.0f}-{hi:.0f}",
                      [(_thumb(a, lo, hi), t) for a, t in tiles]))
    n_cols = max([len(t) for _h, t in lines] + [1])
    head, cap = 22, 16
    w = max(n_cols * (THUMB_PX + 6), 640)
    h = sum(head + THUMB_PX + cap for _ in lines) or head
    img = Image.new("RGB", (w, h), (24, 24, 24))
    draw = ImageDraw.Draw(img)
    y = 0
    for text, tiles in lines:
        draw.text((4, y + 4), text, fill=(230, 230, 230))
        for i, (t, label) in enumerate(tiles):
            x = i * (THUMB_PX + 6)
            img.paste(Image.fromarray(t).convert("RGB"), (x, y + head))
            colour = (255, 200, 60) if label.startswith("candidate") else (170, 170, 170)
            draw.text((x + 2, y + head + THUMB_PX + 2), label[:40], fill=colour)
        y += head + THUMB_PX + cap
    if not lines:
        draw.text((4, 4), "no section could be screened", fill=(230, 230, 230))
    with IO.atomic_save(SCREEN_PNG) as tmp:
        img.save(tmp, format="PNG")


# ---------------------------------------------------------------- main

def _base(p):
    return os.path.basename(str(p).replace("\\", "/"))


def section_markers(row, multiplex, plan):
    """The markers a manifest row is screened for: every study marker under
    multiplex (one scan carries them all), its own marker under paired."""
    ms = MARKERS if multiplex else [row["marker_channel"]]
    return [m for m in ms if m in plan]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--force", action="store_true",
                    help="screen even when the study does not ask for it")
    args = ap.parse_args()

    e = RC.entries(CONFIG)
    if not RS.should_run(CONFIG, args.force):
        why = ("the study says it has no reagent controls" if e["declared"] == "no" else
               "the study's reagent controls are already confirmed in Set up")
        print(f"reagent screen skipped: {why} (reagent_controls.declared = "
              f"{e['declared']!r}). --force screens anyway.")
        if not os.path.exists(SCREEN_CSV):
            write_csv([])
        return 0

    scenes = _read_csv(MANIFEST_SCENES)
    if not scenes:
        raise SystemExit(f"no {MANIFEST_SCENES}: run 00_manifest.py first "
                         f"(study settings: {CONFIG_PATH})")
    if not os.path.exists(FOCUS_CSV):
        raise SystemExit(f"no {FOCUS_CSV}: run 01_overviews.py first")
    focus = {r["scene_uid"]: r for r in _read_csv(FOCUS_CSV)}
    files = {r["file"]: r for r in _read_csv(MANIFEST_FILES)}

    nuc_c, plan, layout = channel_plan(CONFIG)
    multiplex = layout != CH.LAYOUT_PAIRED
    lookup = RC.manifest_lookup(CONFIG, MARKERS if multiplex else ())
    rejected = set(e["rejected"])
    zoom = OV.BASE_PX_UM / OV.TARGET_UM
    fractions = {u: (focus.get(u) or {}).get("tissue_fraction") for u in
                 dict.fromkeys(r["scene_uid"] for r in scenes)}
    dapi_ok = RS.tissue_ok(fractions)

    # One exposure guard per marker over its files: manifest_files records the
    # exposure of the scan's marker plane (plane 1) only.
    expo = {}
    for m in MARKERS:
        expo[m] = RS.exposure_ok({f: r.get("marker_exposure_ms") for f, r in files.items()
                                  if r.get("marker_channel") == m})

    by_file = {}
    for r in scenes:
        by_file.setdefault(r["file"], []).append(r)
    ceilings = {k: int(v["clip_ceiling"]) for k, v in files.items()
                if str(v.get("clip_ceiling") or "").isdigit()}
    print(f"screening {len(scenes)} section(s) in {len(by_file)} file(s), "
          f"{RS.TILES} tiles of {RS.TILE_PX} px each ...")
    from pylibCZIrw import czi as pyczi
    measured, done = {}, 0
    for fname, frows in by_file.items():
        path = os.path.join(SOURCE_DIR, fname)
        if not os.path.exists(path):
            print(f"  !! {fname}: not in {SOURCE_DIR}; its sections are listed unscreened")
            for r in frows:
                measured[r["scene_uid"]] = ({}, 0, None, "not screened: the CZI is missing")
            continue
        try:
            with CR.open_czi(path, None, pyczi=pyczi) as doc:
                rects = doc.scenes_bounding_rectangle
                for r in frows:
                    uid, s = r["scene_uid"], int(r["scene_index"])
                    markers = section_markers(r, multiplex, plan)
                    if s not in rects:
                        measured[uid] = ({}, 0, None, f"not screened: scene {s} not in the CZI")
                        continue
                    try:
                        measured[uid] = screen_section(
                            doc, rects[s], s, uid, markers, nuc_c, plan, zoom,
                            OV.ceiling_for(ceilings, fname)) + ("",)
                    except Exception as exc:  # noqa: BLE001 - one bad section must not stop it
                        measured[uid] = ({}, 0, None,
                                         f"not screened: {type(exc).__name__}: {exc}")
                    done += 1
                    print(f"\r  screened {done}/{len(scenes)}", end="")
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the screen
            print(f"\n  !! {fname}: {type(exc).__name__}: {exc}")
            for r in frows:
                measured.setdefault(r["scene_uid"], (
                    {}, 0, None, f"not screened: {type(exc).__name__}: {exc}"))
    print()

    rows, where, by_uid = [], {}, {}
    for r in scenes:
        uid, fname = r["scene_uid"], r["file"]
        by_uid[uid] = r
        feats, used, first, problem = measured[uid]
        if first is not None:
            where[uid] = first
        markers = section_markers(r, multiplex, plan)
        for m in markers:
            f = feats.get(m) or {}
            notes = [problem] if problem else []
            if not problem and used < RS.TILES:
                notes.append(f"{used} of {RS.TILES} tiles (tissue or clipping)")
            if plan[m][1]:
                notes.append(plan[m][1])
            if len(markers) < 2:
                notes.append("F2 empty: one marker per scan")
            ex = expo.get(m, {}).get(fname)
            if ex is None:
                notes.append("exposure not recorded for this marker; not checked")
            elif ex is False:
                notes.append("exposure differs from this marker's usual one")
            ok_d = bool(dapi_ok.get(uid)) and used > 0
            if not ok_d:
                notes.append("DAPI tissue fraction outside the study's range"
                             if used else "no tissue tile could be read")
            confirmed = RC.is_control(lookup, uid, m)
            if confirmed:
                notes.append("confirmed reagent control: not scored")
            is_rej = uid in rejected or fname in rejected or _base(fname) in rejected
            if is_rej:
                notes.append("rejected in Set up: not proposed again")
            rows.append({"scene_uid": uid, "file": fname, "marker": m,
                         "f1": f.get("f1"), "f2": f.get("f2"), "f3": f.get("f3"),
                         "guard_dapi_ok": ok_d, "guard_exposure_ok": ex is not False,
                         "confirmed": confirmed, "rejected": is_rej,
                         "note": "; ".join(notes)})

    scored = RS.score(rows)
    write_csv(scored)
    write_strip(scored, where, plan, by_uid)

    n_c = sum(r["status"] == "candidate" for r in scored)
    n_r = sum(r["status"] == "review" for r in scored)
    n_u = sum(r["status"] == "unusual" for r in scored)
    print(f"{n_c} candidate(s), {n_r} for review, {n_u} unusual, of {len(scored)} "
          f"section x marker rows -> {SCREEN_CSV}")
    for f, frac in sorted(RS.file_rollup(scored).items()):
        if frac > 0:
            print(f"  {f}: {frac:.0%} of its sections are candidates")
    if n_c or n_r or n_u:
        print("Confirm or reject them in Set up > Slides. The screen only proposes.")
    print("Limits: it cannot see isotype or pre-adsorbed controls, and cannot tell a "
          "no-primary section from a failed stain or an animal that does not express the "
          "marker. Its thresholds are provisional until a run on a study with known controls.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
