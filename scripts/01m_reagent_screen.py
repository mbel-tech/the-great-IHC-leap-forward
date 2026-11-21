"""Stage 01m - screen every section for reagent controls nobody named.

Proposes, never assigns. A section whose marker channel looks like a
no-primary or unstained control (ls_reagent_screen: F1 nuclear spot enrichment,
F2 between-marker similarity, F3 tail spread, all blind to offset and gain) is
listed as a `candidate` in qc/reagent_screen.csv; the operator confirms or
rejects it in Set up. Until then 05b and 05c warn and carry on
(ls_reagent_controls.warn_unanswered).

Runs when `reagent_controls.declared` is `unsure`, or `yes` with nothing
confirmed yet, or with --force. Otherwise it says why and exits 0, writing a
header-only CSV only when no screen exists yet (so the stage reads as done); an
earlier screen is left as it was.

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

Resumable. Features per section x marker go to qc/reagent_screen_features.csv
after every file, keyed by a fingerprint of what they depend on (the CZI's size
and modification time, the scene, the planes, the clip ceiling, the zoom and the
feature constants). A re-run reads only sections whose key changed or that are
missing, so a stopped run picks up where it was, and a changed answer in Set up
(confirmed, rejected) re-scores in seconds without reading a pixel: scoring and
the strip are always redone from the checkpoint. --force runs even when the
gate says skip, and still resumes. --reread starts the reading over: every
section is read again, and that is resumable too (rows carry the reread's id,
kept in qc/reagent_screen_reread.json until the reread finishes; any run while
one is pending continues it). --limit N reads at most N sections, and leaves the
screen CSV as it was until every section has features.

Run:  python 01m_reagent_screen.py [--force] [--reread] [--limit N]
"""

import argparse
import csv
import hashlib
import importlib.util
import json
import os
import sys
import time

import numpy as np
from PIL import Image, ImageDraw

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from ls_config import CONFIG, CONFIG_PATH  # noqa: E402
import czi_read as CR  # noqa: E402
import ls_channels as CH  # noqa: E402
import ls_io as IO  # noqa: E402
import ls_jsonio as JS  # noqa: E402
import ls_reagent_controls as RC  # noqa: E402
import ls_reagent_screen as RS  # noqa: E402

# 01's tissue threshold and scene reader: 01's method on the same plane and zoom.
# Not bit-for-bit 01's mask - 01 thresholds after the tile field, this the raw plane.
_spec = importlib.util.spec_from_file_location("_ov_01m", os.path.join(_HERE, "01_overviews.py"))
OV = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(OV)


def drive_gone():
    """True while a watched folder (ls_drive) cannot be reached. A per-section
    `except Exception` inside a file's unit re-raises then, so the file is
    redone once the drive is back instead of the section being listed
    unscreened for a reason that was never the section's."""
    return OV.DR is not None and not OV.DR.reachable()[0]

SOURCE_DIR = CONFIG["source_dir"]
OUT_ROOT = CONFIG["out_root"]
QC_DIR = os.path.join(OUT_ROOT, "qc")
SCREEN_CSV = os.path.join(OUT_ROOT, *RC.SCREEN_CSV)
SCREEN_PNG = os.path.join(QC_DIR, "reagent_screen.png")
FOCUS_CSV = os.path.join(QC_DIR, "focus.csv")
RANGES_PATH = os.path.join(QC_DIR, "display_ranges.json")
MANIFEST_SCENES = os.path.join(OUT_ROOT, "manifest", "manifest_scenes.csv")
MANIFEST_FILES = os.path.join(OUT_ROOT, "manifest", "manifest_files.csv")

#: The resume checkpoint: features per section x marker, keyed by a fingerprint
#: of what they depend on. Scoring is redone from it on every run.
FEATURES_CSV = os.path.join(QC_DIR, "reagent_screen_features.csv")
FEATURE_KEYS = ["scene_uid", "file", "marker", "f1", "f2", "f3", "tiles", "tile_x", "tile_y",
                "fingerprint", "reread"]
#: A --reread in progress: {"id": ...}. Removed when it finishes.
REREAD_JSON = os.path.join(QC_DIR, "reagent_screen_reread.json")
#: One small picture of each section's first tile per marker, so the strip is
#: redrawn after a re-score without reading a CZI.
THUMB_DIR = os.path.join(QC_DIR, "reagent_screen_thumbs")
#: Bump when a feature's definition changes: every checkpoint row is then re-read.
FEATURE_VERSION = 3

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
#: Thumbnails are stored at TILE_PX / THUMB_STORE (256 px, 2 x 2 block means), drawn
#: 1:1 at THUMB_PX: fine enough to see 1-3 um puncta by eye.
THUMB_STORE = 2

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
    uniformly over tissue pixels, each at least MIN_TILE_TISSUE tissue. A
    position already offered is not offered again."""
    ys, xs = np.nonzero(mask)
    if not len(ys) or rect.w < RS.TILE_PX or rect.h < RS.TILE_PX:
        return
    side = max(1, int(round(RS.TILE_PX * zoom)))
    seen = set()
    for _ in range(RS.TILES * DRAWS_PER_TILE):
        i = int(rng.integers(len(ys)))
        x0 = int((xs[i] + 0.5) / zoom) - RS.TILE_PX // 2
        y0 = int((ys[i] + 0.5) / zoom) - RS.TILE_PX // 2
        x0 = min(max(x0, 0), rect.w - RS.TILE_PX)
        y0 = min(max(y0, 0), rect.h - RS.TILE_PX)
        if (x0, y0) in seen:
            continue
        seen.add((x0, y0))
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


def shrink(a, f=THUMB_STORE):
    """Block mean by f, as uint16: the stored thumbnail."""
    a = np.asarray(a, dtype=np.float64)
    a = a[: a.shape[0] // f * f, : a.shape[1] // f * f]
    a = a.reshape(a.shape[0] // f, f, a.shape[1] // f, f).mean(axis=(1, 3))
    return np.clip(np.round(a), 0, 65535).astype(np.uint16)


def screen_section(doc, rect, scene, uid, markers, nuc_c, plan, zoom, ceiling):
    """({marker: {f1, f2, f3}}, tiles used, first tile's position,
    {marker: thumbnail of the first tile}).

    Every feature is taken over the tile's tissue pixels (`tm`): the overview
    mask under the tile, and scanned. A tile may be half glass, and a batch
    moves the tissue's level but not the glass's.
    """
    mask = tissue_mask(doc, rect, nuc_c, zoom, scene)
    rng = np.random.default_rng(seed_for(uid))
    planes = {"__nuc": nuc_c}
    planes.update({m: plan[m][0] for m in markers})
    f1 = {m: [] for m in markers}
    f2 = {m: [] for m in markers}
    pooled = {m: [] for m in markers}
    used, first, thumbs = 0, None, {}
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
        if first is None:
            first = (x0, y0)
            thumbs = {m: shrink(t[m]) for m in markers}
        nuc = t["__nuc"].astype(np.float64)
        for m in markers:
            a = t[m].astype(np.float64)
            if plan[m][1] is None:
                v = RS.f1_nuclear_enrichment(a, nuc, tm)
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
    return feats, used, first, thumbs


# ---------------------------------------------------------------- checkpoint

def fingerprint(path, row, markers, nuc_c, plan, zoom, ceiling):
    """What a section's features depend on, hashed. A change in any of them
    (the CZI rewritten or replaced, another plane, another ceiling or zoom, a
    feature constant) makes the checkpoint row stale and the section is read."""
    st = os.stat(path)
    payload = [FEATURE_VERSION, int(st.st_size), int(st.st_mtime_ns), int(row["scene_index"]),
               int(nuc_c), [[m, int(plan[m][0]), bool(plan[m][1])] for m in markers],
               round(float(zoom), 6), int(ceiling), RS.TILES, RS.TILE_PX, RS.SPOT_K,
               RS.DAPI_SIGMA, RS.MARKER_SIGMA, RS.MIN_F1_PIXELS, MIN_TILE_TISSUE,
               DRAWS_PER_TILE]
    return hashlib.sha1(json.dumps(payload).encode("utf-8")).hexdigest()[:16]


def load_checkpoint():
    return {(r["scene_uid"], r["marker"]): r for r in _read_csv(FEATURES_CSV)}


def write_checkpoint(ck, uids):
    """Atomic; only sections still in the manifest are kept."""
    os.makedirs(QC_DIR, exist_ok=True)
    rows = [{k: r.get(k, "") for k in FEATURE_KEYS} for (u, _m), r in ck.items() if u in uids]
    IO.atomic_write_csv(FEATURES_CSV, rows, FEATURE_KEYS)


def thumb_path(uid, marker):
    return os.path.join(THUMB_DIR, f"{uid}_{marker}.png")


def save_thumb(uid, marker, a):
    os.makedirs(THUMB_DIR, exist_ok=True)
    IO.save_image(a.astype(np.uint16), thumb_path(uid, marker), format="PNG")


def load_thumb(uid, marker):
    p = thumb_path(uid, marker)
    if not os.path.exists(p):
        return None
    with Image.open(p) as im:
        return np.asarray(im).astype(np.float64)


def is_current(ck, uid, markers, fp, reread=None):
    """Every marker row present with this fingerprint, and - while a reread is
    in progress - read under that reread."""
    rows = [ck.get((uid, m)) or {} for m in markers]
    return fp is not None and all(
        r.get("fingerprint") == fp and (reread is None or r.get("reread") == reread)
        for r in rows)


def reread_state(start):
    """The id of the reread in progress, starting one when `start`; else None."""
    if os.path.exists(REREAD_JSON):
        try:
            with open(REREAD_JSON, encoding="utf-8") as fh:
                rid = json.load(fh).get("id")
            if rid:
                return rid
        except (OSError, ValueError, AttributeError):
            pass
    if not start:
        return None
    rid = time.strftime("%Y%m%dT%H%M%S") + f"-{os.getpid()}"
    os.makedirs(QC_DIR, exist_ok=True)
    JS.write_json_atomic(REREAD_JSON, {"id": rid})
    return rid


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


def _to8(a, lo, hi):
    if hi <= lo:
        hi = lo + 1
    up = THUMB_PX // max(1, a.shape[0])
    if up > 1:
        a = np.repeat(np.repeat(a, up, axis=0), up, axis=1)
    return np.clip((a - lo) * 255.0 / (hi - lo), 0, 255).astype(np.uint8)


def strip_picture(uid, marker, row, where, plan, say):
    """The stored thumbnail, or failing that the first tile read again."""
    a = load_thumb(uid, marker)
    if a is not None or uid not in where:
        return a
    path = os.path.join(SOURCE_DIR, row["file"])
    s = int(row["scene_index"])
    try:
        from pylibCZIrw import czi as pyczi

        def unit():
            # Self-contained: a redo after a dropped drive opens the file again.
            with CR.open_czi(path, None, pyczi=pyczi) as doc:
                rect = doc.scenes_bounding_rectangle[s]
                x0, y0 = where[uid]
                return read_tile(doc, rect, x0, y0, {"m": plan[marker][0]}, s)["m"]

        t = OV.per_slide(unit, label=f"{row['file']} (strip tile {uid})")
    except Exception as exc:  # noqa: BLE001 - the CSV is written; the picture is extra
        if OV.is_stop(exc):
            raise
        say(f"  !! strip: {uid} not re-read ({type(exc).__name__}: {exc})")
        return None
    return shrink(t).astype(np.float64)


def write_strip(scored, where, plan, by_uid, say=print):
    """qc/reagent_screen.png: per marker, the top candidates beside three
    median sections, every tile at that marker's frozen display range."""
    ranges = {}
    if os.path.exists(RANGES_PATH):
        with open(RANGES_PATH, encoding="utf-8") as fh:
            ranges = json.load(fh)
    lines = []
    for m in dict.fromkeys(r["marker"] for r in scored):
        mine = [r for r in scored if r["marker"] == m and r.get("rank") is not None]
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
            a = strip_picture(uid, m, by_uid[uid], where, plan, say)
            if a is not None:
                tiles.append((a, f"{label} {uid}  rank {r['rank']:.1f}"))
        if lo is None or hi is None:
            pool = np.concatenate([a.ravel() for a, _ in tiles]) if tiles else np.zeros(1)
            lo, hi = float(np.percentile(pool, 1)), float(np.percentile(pool, 99.5))
        lines.append((f"{m}: {len(cands)} candidate(s) shown, then {len(medians)} median "
                      f"section(s); range {lo:.0f}-{hi:.0f}",
                      [(_to8(a, lo, hi), t) for a, t in tiles]))
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


class _Out:
    """A `\\r` progress line, ended before anything else is printed."""

    def __init__(self):
        self.open = False

    def progress(self, text):
        print("\r" + text, end="", flush=True)
        self.open = True

    def __call__(self, text=""):
        if self.open:
            print()
            self.open = False
        print(text)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--force", action="store_true",
                    help="screen even when the study does not ask for it (still resumes)")
    ap.add_argument("--reread", action="store_true",
                    help="read every section again rather than reuse the checkpoint; "
                         "resumable, and a run while one is pending continues it")
    ap.add_argument("--limit", type=int, default=0,
                    help="read at most N sections this run (0: all); a later run resumes")
    args = ap.parse_args()
    say = _Out()

    e = RC.entries(CONFIG)
    if not RS.should_run(CONFIG, args.force):
        why = ("the study says it has no reagent controls" if e["declared"] == "no" else
               "the study's reagent controls are already confirmed in Set up")
        say(f"reagent screen skipped: {why} (reagent_controls.declared = "
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
    uids = list(dict.fromkeys(r["scene_uid"] for r in scenes))
    dapi_ok = RS.tissue_ok({u: (focus.get(u) or {}).get("tissue_fraction") for u in uids})

    # One exposure guard per marker over its files: manifest_files records the
    # exposure of the scan's marker plane (plane 1) only.
    expo = {}
    for m in MARKERS:
        expo[m] = RS.exposure_ok({f: r.get("marker_exposure_ms") for f, r in files.items()
                                  if r.get("marker_channel") == m})

    ceilings = {k: int(v["clip_ceiling"]) for k, v in files.items()
                if str(v.get("clip_ceiling") or "").isdigit()}
    ck = load_checkpoint()
    pending = os.path.exists(REREAD_JSON)
    reread = reread_state(args.reread)
    if reread and pending:
        say(f"continuing the reread started earlier ({reread}): sections it has read are kept")
    fps, problems, todo = {}, {}, {}
    for r in scenes:
        uid, fname = r["scene_uid"], r["file"]
        path = os.path.join(SOURCE_DIR, fname)
        # Before the exists check: a CZI on a drive that has just dropped is
        # waited for, not listed missing.
        OV.wait_if_gone(fname)
        if not os.path.exists(path):
            problems[uid] = "not screened: the CZI is missing"
            continue
        fps[uid] = fingerprint(path, r, section_markers(r, multiplex, plan), nuc_c, plan,
                               zoom, OV.ceiling_for(ceilings, fname))
        if not is_current(ck, uid, section_markers(r, multiplex, plan), fps[uid], reread):
            todo.setdefault(fname, []).append(r)
    for fname in dict.fromkeys(r["file"] for r in scenes if r["scene_uid"] in problems):
        say(f"  !! {fname}: not in {SOURCE_DIR}; its sections are listed unscreened")
    n_todo = sum(len(v) for v in todo.values())
    say(f"{len(uids)} section(s); {len(uids) - n_todo - len(problems)} already screened "
        f"(qc/reagent_screen_features.csv), {n_todo} to read, {RS.TILES} tiles of "
        f"{RS.TILE_PX} px each" + (f", at most {args.limit} this run" if args.limit else "")
        + (" (reread: every section read again)" if reread else ""))

    read = 0
    stopped = False
    for fname, frows in todo.items():
        if args.limit and read >= args.limit:
            stopped = True
            break
        path = os.path.join(SOURCE_DIR, fname)
        # One file is the unit a dropped drive redoes (see 01_overviews'
        # export): each attempt starts `acc` afresh, and nothing reaches the
        # checkpoint, the problems or the count until the attempt is over, so
        # a redo cannot add a row twice and an ordinary failure part way still
        # keeps the sections screened before it. Each section's tiles are
        # drawn from its own seed (seed_for), so a redo picks the same tiles;
        # thumbnails are written atomically and simply overwritten.
        acc = {}

        def unit(path=path, frows=frows, fname=fname):
            acc.clear()
            acc.update(ck={}, problems={}, read=0, stopped=False)
            from pylibCZIrw import czi as pyczi
            with CR.open_czi(path, None, pyczi=pyczi) as doc:
                rects = doc.scenes_bounding_rectangle
                for r in frows:
                    if args.limit and read + acc["read"] >= args.limit:
                        acc["stopped"] = True
                        break
                    uid, s = r["scene_uid"], int(r["scene_index"])
                    markers = section_markers(r, multiplex, plan)
                    if s not in rects:
                        acc["problems"][uid] = f"not screened: scene {s} not in the CZI"
                        continue
                    try:
                        feats, used, first, thumbs = screen_section(
                            doc, rects[s], s, uid, markers, nuc_c, plan, zoom,
                            OV.ceiling_for(ceilings, fname))
                    except Exception as exc:  # noqa: BLE001 - one bad section must not stop it
                        # Not the section's fault if the drive went: the whole
                        # file is redone once it is back.
                        if OV.is_stop(exc) or drive_gone():
                            raise
                        acc["problems"][uid] = f"not screened: {type(exc).__name__}: {exc}"
                        continue
                    finally:
                        acc["read"] += 1
                    for m in markers:
                        f = feats.get(m) or {}
                        acc["ck"][(uid, m)] = {
                            "scene_uid": uid, "file": fname, "marker": m,
                            "f1": _fmt(f.get("f1"), 6), "f2": _fmt(f.get("f2"), 6),
                            "f3": _fmt(f.get("f3"), 6), "tiles": used,
                            "tile_x": "" if first is None else first[0],
                            "tile_y": "" if first is None else first[1],
                            "fingerprint": fps[uid], "reread": reread or ""}
                        if m in thumbs:
                            try:
                                save_thumb(uid, m, thumbs[m])
                            except Exception as exc:  # noqa: BLE001 - the strip re-reads it
                                if OV.is_stop(exc) or drive_gone():
                                    raise
                                say(f"  !! {uid}: thumbnail not saved ({type(exc).__name__}: "
                                    f"{exc}); the strip will read the tile again")
                    say.progress(f"  read {read + acc['read']}/{n_todo}")

        error = None
        try:
            OV.per_slide(unit, label=fname)
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the screen
            if OV.is_stop(exc):
                raise
            error = exc
        # The attempt's results, in the order they were screened - all of
        # them on success, the sections before the failure otherwise.
        ck.update(acc.get("ck", {}))
        problems.update(acc.get("problems", {}))
        read += acc.get("read", 0)
        stopped = stopped or acc.get("stopped", False)
        if error is not None:
            exc = error
            say(f"  !! {fname}: {type(exc).__name__}: {exc}")
            for r in frows:
                if not is_current(ck, r["scene_uid"], section_markers(r, multiplex, plan),
                                  fps.get(r["scene_uid"]), reread):
                    problems.setdefault(r["scene_uid"],
                                        f"not screened: {type(exc).__name__}: {exc}")
        # Checkpoint per file, so a stopped run resumes from here.
        write_checkpoint(ck, set(uids))
        if stopped:
            break
    say()

    left = [r["scene_uid"] for rs in todo.values() for r in rs
            if not is_current(ck, r["scene_uid"], section_markers(r, multiplex, plan),
                              fps.get(r["scene_uid"]), reread) and r["scene_uid"] not in problems]
    if left:
        say(f"--limit {args.limit}: {len(left)} section(s) still to read. Run again to "
            f"resume{' the reread' if reread else ''}; "
            f"{os.path.basename(SCREEN_CSV)} is scored once every section is read.")
        return 0
    if reread:
        os.remove(REREAD_JSON)
        say(f"reread {reread} finished")

    rows, where, by_uid = [], {}, {}
    for r in scenes:
        uid, fname = r["scene_uid"], r["file"]
        by_uid[uid] = r
        markers = section_markers(r, multiplex, plan)
        current = is_current(ck, uid, markers, fps.get(uid))
        problem = "" if current else problems.get(uid, "not screened")
        for m in markers:
            c = (ck.get((uid, m)) or {}) if current else {}
            used = int(c.get("tiles") or 0)
            if c.get("tile_x", "") != "" and uid not in where:
                where[uid] = (int(c["tile_x"]), int(c["tile_y"]))
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
                         "f1": c.get("f1"), "f2": c.get("f2"), "f3": c.get("f3"),
                         "guard_dapi_ok": ok_d, "guard_exposure_ok": ex is not False,
                         "confirmed": confirmed, "rejected": is_rej,
                         "note": "; ".join(notes)})

    scored = RS.score(rows)
    for r in scored:                       # back to numbers for the CSV's own format
        for k in ("f1", "f2", "f3"):
            r[k] = RS._num(r[k])
    write_csv(scored)
    write_strip(scored, where, plan, by_uid, say)

    n_c = sum(r["status"] == "candidate" for r in scored)
    n_r = sum(r["status"] == "review" for r in scored)
    n_u = sum(r["status"] == "unusual" for r in scored)
    say(f"{n_c} candidate(s), {n_r} for review, {n_u} unusual, of {len(scored)} "
        f"section x marker rows -> {SCREEN_CSV}")
    for f, frac in sorted(RS.file_rollup(scored).items()):
        if frac > 0:
            say(f"  {f}: {frac:.0%} of its sections are candidates")
    if n_c or n_r or n_u:
        say("Confirm or reject them in Set up > Slides. The screen only proposes.")
    say("Limits: it cannot see isotype or pre-adsorbed controls, and cannot tell a "
        "no-primary section from a failed stain or an animal that does not express the "
        "marker. Its thresholds are provisional until a run on a study with known controls.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
