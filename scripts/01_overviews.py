"""Stage 1 - export a browsable overview of every section, plus per-section QC.

Port of 01_overviews.groovy from Bio-Formats/Fiji to pylibCZIrw. Motivation:

  speed        0.25 s to open a file and 0.26 s per section, against ~56 s and
               ~9 s through Bio-Formats. The full dataset drops from ~5 hours
               to roughly 12 minutes.

  correctness  Bio-Formats' Memoizer caches reader state keyed on file path
               alone, so a reader saved in tile mode by another script came
               back in tile mode here and 238 sections silently exported as
               single 2040x2040 tiles instead of whole sections. pylibCZIrw has
               no such cache and no autostitch flag to get restored wrongly.

  exactness    `zoom` is continuous, so sections come out at exactly 5.20 um/px
               instead of "nearest stored pyramid level", which is why
               tissue_area_mm2 previously ranged 0.18-28.83 mm2 within one brain.

Display ranges are computed once from TISSUE pixels and then frozen dataset-wide.
Sampling the whole frame let the AF568 background - brighter than the brain on
many sections - pin the top of the range at 65535, leaving real tissue rendering
around 20/255.

Requires the Python 3.13 venv at <root>/work/czienv (pylibCZIrw has no cp314 wheel).

Run:  <root>/work/czienv/Scripts/python.exe 01_overviews.py
      ... --limit 20 --force
"""

import argparse
import csv
import json
import os
import sys
import time

import numpy as np
from PIL import Image
from pylibCZIrw import czi as pyczi

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
# ls_config resolves LS_CONFIG, applies the defaults and validates once for
# the whole process. Imported, not re-implemented: this block used to be four
# lines copy-pasted into every stage.
from ls_config import CONFIG, CONFIG_PATH  # noqa: E402
import czi_read as CR  # noqa: E402
import ls_channels as CH  # noqa: E402
import ls_io as IO  # noqa: E402

SOURCE_DIR = CONFIG["source_dir"]
OUT_ROOT = CONFIG["out_root"]
BASE_PX_UM = CONFIG["pixel_size_um"]
TARGET_UM = CONFIG["overview_target_um_per_px"]

OVERVIEW_DIR = os.path.join(OUT_ROOT, "overviews")
QC_DIR = os.path.join(OUT_ROOT, "qc")
RANGES_PATH = os.path.join(QC_DIR, "display_ranges.json")
QC_CSV = os.path.join(QC_DIR, "focus.csv")
# What 01k_saturation_raw.py used to be the only writer of. This stage reads the
# same two raw planes at the same zoom, so it writes them from the array it
# already holds; 01k is left as a backfill for sections exported before it did.
SAT_RAW_CSV = os.path.join(QC_DIR, "saturation_raw.csv")
# Every section this run could not export, and why. 04a's "unaccounted" check
# is built from focus.csv, so a section that never reached focus.csv - its CZI
# unplugged, still inside a zip, or unreadable - is invisible to it; this is
# where it is named instead. Rewritten every run, header only when nothing
# failed, so an old list cannot outlive the fix.
FAILURES_CSV = os.path.join(QC_DIR, "overview_failures.csv")
FAILURE_KEYS = ["scene_uid", "file", "animal", "marker_channel", "scene_index", "reason"]
CENSOR_RAW_DIR = os.path.join(QC_DIR, "censor_raw")
# Masks are written for every declared marker - see 01k on why AF488 too.
MASK_MARKERS = tuple(CH.marker_names(CONFIG))
SAT_RAW_KEYS = [
    "scene_uid", "file", "animal", "slide", "variant", "marker_channel",
    "scene_index", "section_order", "width", "height", "um_px", "zoom",
    # The scene origin in CZI stage pixels. Recorded here so 06f can map a
    # nucleus from czi_x/czi_y into this mask without reopening 222 CZIs.
    "rect_x", "rect_y", "rect_w", "rect_h",
    "saturated_fraction_raw", "saturated_fraction_dapi_raw",
    "saturated_fraction_corrected", "raw_over_corrected",
    "mask_path", "tilefield_applied", "reader"]

SAMPLE_SECTIONS = 120
SEED = 20260811
# Pulled in from 0.1/99.9: a few clipped pixels inside the mask would otherwise
# drag the top of the range back to 65535.
LO_PCT, HI_PCT = 1.0, 99.5

# Exact geometry, confirmed independently by pylibCZIrw's subblock enumeration
# and by parsing the CZI directory: 2040 px tiles on an 1836 px pitch.
PITCH_PX = 1836
FIELD_DIVISOR = 12

QC_KEYS = ["scene_uid", "file", "animal", "slide", "variant", "marker_channel",
           "scene_index", "section_order", "width", "height", "um_px",
           "tissue_threshold", "tissue_fraction", "tissue_area_mm2", "tissue_mean",
           "background_mean", "contrast", "focus_score", "saturated_fraction",
           "saturated_fraction_raw", "saturated_fraction_dapi_raw",
           "tilefield_applied", "reader"]


# ---------------------------------------------------------------- image helpers

def otsu(values):
    hist, edges = np.histogram(values, bins=512)
    hist = hist.astype(np.float64)
    centres = (edges[:-1] + edges[1:]) / 2.0
    w1, w2 = np.cumsum(hist), np.cumsum(hist[::-1])[::-1]
    valid = (w1 > 0) & (w2 > 0)
    m1 = np.cumsum(hist * centres) / np.maximum(w1, 1e-9)
    m2 = (np.cumsum((hist * centres)[::-1]) / np.maximum(w2[::-1], 1e-9))[::-1]
    var = w1 * w2 * (m1 - m2) ** 2
    var[~valid] = -1
    return float(centres[int(np.argmax(var))])


def tissue_threshold(image):
    """Log-space Otsu.

    DAPI runs a median near 500 against a 99.9th percentile near 30000. Plain
    Otsu maximises between-class variance by chasing that tail and returns a
    threshold above nearly every pixel; logs make the two populations
    comparably wide so the split lands between them.
    """
    sample = image[::3, ::3].ravel()
    positive = sample[sample > 0].astype(np.float64)
    if positive.size < 500:
        return float(image.max())
    return float(np.expm1(otsu(np.log1p(positive))))


def fold_index(length, downsample, field_n):
    """Field-grid index per pixel along one axis.

    Tile starts sit on exact multiples of the pitch from the scene origin, so
    the fold phase is zero and no alignment search is needed.
    """
    native = np.arange(length, dtype=np.float64) * downsample
    return (np.round((native % PITCH_PX) / FIELD_DIVISOR).astype(np.int64)) % field_n


def apply_tile_field(image, field, downsample):
    if field is None:
        return image
    n = field.shape[0]
    gain = field[np.ix_(fold_index(image.shape[0], downsample, n),
                        fold_index(image.shape[1], downsample, n))]
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(gain > 0.05, image.astype(np.float32) / gain, image)
    return np.clip(out, 0, 65535).astype(np.uint16)


def focus_score(image, mask):
    """Sobel-style edge energy inside tissue, normalised by brightness.

    Normalising stops a dim section being mistaken for a blurred one.
    """
    if not mask.any():
        return 0.0
    a = image.astype(np.float32)
    gy = np.abs(np.diff(a, axis=0, prepend=a[:1]))
    gx = np.abs(np.diff(a, axis=1, prepend=a[:, :1]))
    mean = float(a[mask].mean())
    if mean <= 0:
        return 0.0
    return float((gx + gy)[mask].mean() / mean)


def to_8bit(image, lo, hi):
    if hi <= lo:
        hi = lo + 1
    scaled = (image.astype(np.float32) - lo) * (255.0 / (hi - lo))
    return np.clip(scaled, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------- czi access

def read_scene(czidoc, rect, channel, zoom, *, scene):
    """One scene, one channel, at the requested zoom, as 2-D uint16.

    Delegates to czi_read, which names the scene. Without that, a read of this
    rectangle composites whichever neighbouring scenes overlap it - which is
    2241 of the 2572 scenes in this dataset.
    """
    return CR.read_plane(czidoc, rect, channel, scene=scene, zoom=zoom)


def load_manifest():
    with open(os.path.join(OUT_ROOT, "manifest", "manifest_scenes.csv"),
              newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_tile_fields():
    fields = {}
    for c in (0, 1):
        p = os.path.join(QC_DIR, "flatfield", f"tilefield_c{c}.npy")
        if os.path.exists(p):
            fields[c] = np.load(p)
    if fields:
        print(f"tile fields loaded for channels {sorted(fields)}")
    else:
        print("no tile fields found - exporting UNCORRECTED")
    return fields


def load_ceilings():
    """file name -> clip ceiling, from the manifest.

    Recorded rather than assumed: 65535 is right here only because every channel
    of every file is genuinely 16-bit. A manifest written before 00_manifest
    recorded the column falls back to 65535 with a warning, so an old output
    directory still runs.
    """
    path = os.path.join(OUT_ROOT, "manifest", "manifest_files.csv")
    out = {}
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                try:
                    out[r["file"]] = int(r["clip_ceiling"])
                except (KeyError, TypeError, ValueError):
                    continue
    if not out:
        print("  !! no clip_ceiling in the manifest - falling back to 65535; "
              "re-run 00_manifest.py to record it")
    return out


def ceiling_for(ceilings, fname):
    return ceilings.get(fname, 65535)


def write_qc(rows):
    """Atomic write, so an interrupted write cannot truncate the checkpoint."""
    if not rows:
        return
    tmp = QC_CSV + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=QC_KEYS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, QC_CSV)


def raw_clip_record(r, fname, rect, s, zoom, dapi_raw, mark_raw, mark_corrected,
                    ceiling):
    """One saturation_raw.csv row, writing the raw clipping mask on the way.

    Shared with 01k_saturation_raw.py, so the export and the backfill write the
    same row and the same mask from the same arrays: the 16-bit planes as read,
    before the tile field. Nothing here is resampled or rescaled.
    """
    clipped = mark_raw >= ceiling
    raw = float(clipped.mean())
    corrected = float((mark_corrected >= ceiling).mean())
    mask_path = ""
    if r["marker_channel"] in MASK_MARKERS:
        os.makedirs(CENSOR_RAW_DIR, exist_ok=True)
        mask_path = os.path.join(CENSOR_RAW_DIR, r["scene_uid"] + "_clipped.png")
        with IO.atomic_save(mask_path) as tmp:
            Image.fromarray((clipped * 255).astype(np.uint8)).save(tmp, format="PNG")
    h, w = mark_raw.shape
    return {
        "scene_uid": r["scene_uid"], "file": fname, "animal": r["animal"],
        "slide": r["slide"], "variant": r["variant"],
        "marker_channel": r["marker_channel"], "scene_index": s,
        "section_order": r["section_order"],
        "width": w, "height": h,
        "rect_x": rect.x, "rect_y": rect.y, "rect_w": rect.w, "rect_h": rect.h,
        "um_px": "%.2f" % (BASE_PX_UM / zoom), "zoom": "%.4f" % zoom,
        "saturated_fraction_raw": "%.6f" % raw,
        "saturated_fraction_dapi_raw": "%.6f" % float((dapi_raw >= ceiling).mean()),
        "saturated_fraction_corrected": "%.6f" % corrected,
        "raw_over_corrected": "%.3f" % (raw / corrected) if corrected > 0 else "",
        "mask_path": os.path.relpath(mask_path, OUT_ROOT) if mask_path else "",
        # The mask and the _raw columns are measured before the field.
        "tilefield_applied": 0,
        "reader": "pylibCZIrw",
    }


def load_sat_raw():
    """saturation_raw.csv as uid -> row, or empty when there is none yet."""
    if not os.path.exists(SAT_RAW_CSV):
        return {}
    with open(SAT_RAW_CSV, newline="", encoding="utf-8") as fh:
        return {r["scene_uid"]: r for r in csv.DictReader(fh)}


def record_failures(failures, fname, rows, reason):
    """Name the file and every section lost with it, on screen and in the CSV."""
    print(f"\n  !! {fname}: {reason} - {len(rows)} section(s) not exported: "
          + ", ".join(r["scene_uid"] for r in rows))
    for r in rows:
        failures.append({**{k: r.get(k, "") for k in FAILURE_KEYS},
                         "file": fname, "reason": reason})


def write_sat_raw(by_uid):
    if by_uid:
        IO.atomic_write_csv(SAT_RAW_CSV, list(by_uid.values()), SAT_RAW_KEYS)


# ---------------------------------------------------------------- passes

def sample_display_ranges(scenes, fields, zoom, force):
    if os.path.exists(RANGES_PATH) and not force:
        with open(RANGES_PATH, encoding="utf-8") as fh:
            ranges = json.load(fh)
        print(f"display ranges loaded: {ranges}")
        return ranges

    rng = np.random.default_rng(SEED)
    pool = list(scenes)
    rng.shuffle(pool)
    sample = pool[:SAMPLE_SECTIONS]
    by_file = {}
    for r in sample:
        by_file.setdefault(r["file"], []).append(r)

    print(f"sampling {len(sample)} sections for the display range (tissue pixels only) ...")
    values = {}
    done = 0
    for fname, rows in by_file.items():
        path = os.path.join(SOURCE_DIR, fname)
        if not os.path.exists(path):
            continue
        with pyczi.open_czi(path) as czidoc:
            rects = czidoc.scenes_bounding_rectangle
            for r in rows:
                s = int(r["scene_index"])
                if s not in rects:
                    continue
                dapi = apply_tile_field(read_scene(czidoc, rects[s], 0, zoom, scene=s), fields.get(0), 1 / zoom)
                mark = apply_tile_field(read_scene(czidoc, rects[s], 1, zoom, scene=s), fields.get(1), 1 / zoom)
                mask = dapi > tissue_threshold(dapi)
                if mask.sum() < 500:
                    continue
                values.setdefault("DAPI", []).append(dapi[mask][::7])
                values.setdefault(r["marker_channel"], []).append(mark[mask][::7])
                done += 1
        print(f"\r  sampled {done}/{len(sample)}", end="")
    print()

    ranges = {}
    for name, chunks in values.items():
        v = np.concatenate(chunks)
        ranges[name] = {"lo": int(np.percentile(v, LO_PCT)), "hi": int(np.percentile(v, HI_PCT))}
        print(f"  {name}: {ranges[name]['lo']} .. {ranges[name]['hi']}  (n={v.size})")
    os.makedirs(QC_DIR, exist_ok=True)
    with open(RANGES_PATH, "w", encoding="utf-8") as fh:
        json.dump(ranges, fh, indent=2)
    return ranges


def channel_range(ranges, name):
    """(lo, hi) for a channel, or a clear stop when display_ranges.json predates it."""
    if name not in ranges:
        raise SystemExit(f"{RANGES_PATH} has no entry for {name} - it was sampled before "
                         f"this marker existed. Delete it and rerun to resample.")
    return ranges[name]["lo"], ranges[name]["hi"]


def export(scenes, fields, ranges, ceilings, zoom, limit, force):
    done_uids = set()
    qc_rows = []
    if os.path.exists(QC_CSV) and not force:
        with open(QC_CSV, newline="", encoding="utf-8") as fh:
            qc_rows = list(csv.DictReader(fh))
        done_uids = {r["scene_uid"] for r in qc_rows}
        print(f"resuming: {len(done_uids)} sections already exported")

    by_file = {}
    for r in scenes:
        if r["scene_uid"] not in done_uids:
            by_file.setdefault(r["file"], []).append(r)

    total = sum(len(v) for v in by_file.values())
    print(f"{total} sections to export across {len(by_file)} files")
    # Rows for sections exported earlier are kept; a section exported now
    # replaces its row, so --force cannot leave a stale mask row behind.
    sat_raw = load_sat_raw()

    exported = failed = 0
    failures = []
    t0 = time.time()
    for fname, rows in by_file.items():
        if limit and exported >= limit:
            break
        path = os.path.join(SOURCE_DIR, fname)
        if not os.path.exists(path):
            failed += len(rows)
            record_failures(failures, fname, rows,
                            f"CZI missing from {SOURCE_DIR} (drive unplugged, or still zipped?)")
            continue

        marker = rows[0]["marker_channel"]
        ceiling = ceiling_for(ceilings, fname)
        out_dir = os.path.join(OVERVIEW_DIR, rows[0]["animal"], marker)
        os.makedirs(out_dir, exist_ok=True)
        d_lo, d_hi = channel_range(ranges, "DAPI")
        m_lo, m_hi = channel_range(ranges, marker)

        done_here = set()
        try:
            with pyczi.open_czi(path) as czidoc:
                rects = czidoc.scenes_bounding_rectangle
                for r in rows:
                    if limit and exported >= limit:
                        break
                    s = int(r["scene_index"])
                    if s not in rects:
                        failed += 1
                        done_here.add(r["scene_uid"])
                        record_failures(failures, fname, [r], f"scene {s} not in the CZI")
                        continue

                    dapi_raw = read_scene(czidoc, rects[s], 0, zoom, scene=s)
                    mark_raw = read_scene(czidoc, rects[s], 1, zoom, scene=s)
                    # Clipping is measured HERE, before the field is applied.
                    # Dividing by a gain above 1 lifts a pixel off the 16-bit
                    # ceiling and it stops counting as clipped - but the value is
                    # still gone, because no correction puts back what the sensor
                    # never recorded. tilefield_c1 exceeds 1.0 over 54% of its
                    # area, which halved every clipping number reported here.
                    sat_mark_raw = float((mark_raw >= ceiling).mean())
                    sat_dapi_raw = float((dapi_raw >= ceiling).mean())
                    dapi = apply_tile_field(dapi_raw, fields.get(0), 1 / zoom)
                    mark = apply_tile_field(mark_raw, fields.get(1), 1 / zoom)
                    # The raw clipping mask 04j censors from, written here from
                    # the planes just read rather than by a second pass over
                    # every CZI in 01k.
                    sat_raw[r["scene_uid"]] = raw_clip_record(
                        r, fname, rects[s], s, zoom, dapi_raw, mark_raw, mark, ceiling)
                    h, w = dapi.shape
                    um_px = BASE_PX_UM / zoom

                    thr = tissue_threshold(dapi)
                    mask = dapi > thr
                    # No-data corners are exactly zero in both channels and are
                    # not background - they were never scanned.
                    scanned = (dapi > 0) | (mark > 0)
                    background = (~mask) & scanned

                    n_tissue = int(mask.sum())
                    tissue_mean = float(mark[mask].mean()) if n_tissue else 0.0
                    bg_mean = float(mark[background].mean()) if background.sum() > 500 else float("nan")

                    uid = r["scene_uid"]
                    d8, m8 = to_8bit(dapi, d_lo, d_hi), to_8bit(mark, m_lo, m_hi)
                    # Blue nuclei, yellow marker: the most separable pairing and
                    # it survives the common forms of colour blindness.
                    rgb = np.dstack([m8, m8, d8])
                    Image.fromarray(d8).save(os.path.join(out_dir, f"{uid}_DAPI.png"))
                    Image.fromarray(m8).save(os.path.join(out_dir, f"{uid}_MARK.png"))
                    Image.fromarray(rgb).save(os.path.join(out_dir, f"{uid}_RGB.png"))

                    qc_rows.append({
                        "scene_uid": uid, "file": fname, "animal": r["animal"],
                        "slide": r["slide"], "variant": r["variant"],
                        "marker_channel": marker, "scene_index": s,
                        "section_order": r["section_order"],
                        "width": w, "height": h, "um_px": f"{um_px:.2f}",
                        "tissue_threshold": f"{thr:.0f}",
                        "tissue_fraction": f"{n_tissue / dapi.size:.4f}",
                        "tissue_area_mm2": f"{n_tissue * um_px * um_px / 1e6:.3f}",
                        "tissue_mean": f"{tissue_mean:.1f}",
                        "background_mean": "" if np.isnan(bg_mean) else f"{bg_mean:.1f}",
                        "contrast": "" if (np.isnan(bg_mean) or bg_mean <= 0)
                                    else f"{tissue_mean / bg_mean:.3f}",
                        "focus_score": f"{focus_score(dapi, mask):.4f}",
                        # Kept as-is so nothing downstream breaks, and so the
                        # pair stays directly comparable. It measures clipping
                        # AFTER correction and is therefore an undercount; the
                        # _raw columns are the honest number.
                        "saturated_fraction": f"{float((mark >= ceiling).mean()):.6f}",
                        "saturated_fraction_raw": f"{sat_mark_raw:.6f}",
                        "saturated_fraction_dapi_raw": f"{sat_dapi_raw:.6f}",
                        "tilefield_applied": int(bool(fields)),
                        "reader": "pylibCZIrw",
                    })
                    exported += 1
                    done_here.add(uid)
        except Exception as exc:  # noqa: BLE001 - one bad file must not kill the run
            # Only the sections not already written or already recorded: a file
            # that fails on its fifth scene still exported the first four.
            lost = [r for r in rows if r["scene_uid"] not in done_here]
            failed += len(lost)
            record_failures(failures, fname, lost, f"{type(exc).__name__}: {exc}")

        # Checkpoint per file: this runs unattended against an external drive
        # that has already dropped writes once.
        write_qc(qc_rows)
        write_sat_raw(sat_raw)
        rate = exported / max(time.time() - t0, 1e-9)
        print(f"\r  exported {exported}/{total}  failed {failed}  "
              f"({rate:.1f}/s, {(time.time() - t0) / 60:.1f} min)", end="")

    print()
    write_qc(qc_rows)
    write_sat_raw(sat_raw)
    os.makedirs(QC_DIR, exist_ok=True)
    IO.atomic_write_csv(FAILURES_CSV, failures, FAILURE_KEYS)
    return exported, failed, len(qc_rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    zoom = BASE_PX_UM / TARGET_UM
    print(f"target {TARGET_UM} um/px -> zoom {zoom:.4f} (exact, not a pyramid level)")

    scenes = load_manifest()
    print(f"manifest: {len(scenes)} sections")
    fields = load_tile_fields()
    ceilings = load_ceilings()

    ranges = sample_display_ranges(scenes, fields, zoom, args.force)
    exported, failed, total_rows = export(scenes, fields, ranges, ceilings, zoom, args.limit, args.force)

    print()
    print("=" * 72)
    print(f"exported {exported}, failed {failed}, focus.csv now holds {total_rows} sections")
    print("=" * 72)
    if failed:
        # A message rather than a bare `return 1`: app/runner.py ignores what
        # main() returns and reports a SystemExit message as the step failing.
        # From the command line it is still exit status 1.
        raise SystemExit(f"{failed} section(s) not exported - listed above "
                         f"and in {FAILURES_CSV}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
