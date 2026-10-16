"""Stage 1l - a picture of every scene on every slide, straight from the CZI, for looking at.

The overviews (01) are 5.20 um/px and the curation composites (04o) are cut,
tilted and squashed into the reformatted frame. Neither is the plain answer to
"show me what is on this slide": every scene, the way it was scanned, sharp
enough to see nuclei. This is that, and nothing reads it - it exists to be
browsed in a file viewer.

For each scene of each CZI in the manifest it writes, under
the scene pictures folder (`scene_render.out_dir`, by default
`<out_root>/scene_renders`), under `<marker>/`:

  stain+<counterstain>/<scene_uid>.png   the marker in its colour over the
                                         counterstain in its own - the same
                                         colours and the same MAX rule as the
                                         04o composites
  stain_only/<scene_uid>.png             the marker alone, in grey: grey uses
                                         all of the screen's brightness, where
                                         red or green alone uses a third of it,
                                         so faint signal stays readable

and one row per picture in `qc/scene_renders.csv` saying which
picture is which and how it was drawn. The row is keyed by the picture's own
path, so runs into different folders each keep theirs.

How the pixels are made
-----------------------

At `--um-px` (default 2.6, finest the native 0.65) from the CZI's own 16-bit planes,
always naming the scene (czi_read). The native 0.65 um/px pixels are read in
bands of rows and block-averaged down before the next band is fetched - 04o's
`read_region`, borrowed rather than restated - so the memory used is one band
plus the finished picture, whatever the scene's size. Block averaging is why
the picture cannot alias: every native pixel contributes, none is skipped.
The resolution is therefore a whole number of native pixels: 2.6 is 4x4
blocks, 1.3 is 2x2, 0.65 is the scan's own pixels. A value in between is rounded to the nearest whole block
and the CSV records what was drawn.

The tile-field correction is applied as 01 applies it, when 01f has built one,
except on clipped pixels: a pixel at the clip ceiling has lost its value, and
dividing it by a gain that runs 0.81-1.10 across the tile would print the tile
pattern onto every clipped area (the hard rectangles first seen in the AF568
rim on LS105). Clipped pixels stay at the top of the range instead.

Parts of a scene's rectangle that hold no tiles at all read as 0 and are
drawn black: there is nothing there to show.

Outside the section
-------------------

Only the section is drawn; the slide around it is black, as it is in the
curator. A scene's rectangle holds a good deal of glass besides the section,
and on these slides that glass carries the PAP pen ring drawn round each
section - bright in the marker channels, so at the frozen range it printed a
broad red or green ring round every picture that the curator never showed.
The curator is clean because 04o blanks everything outside 04a's tissue
silhouette; this blanks the same silhouette, `04a.overview_silhouette` (the
one 05c judges `off_tissue` by), made from 01's counterstain overview of the
scene and stretched over the picture. Inside it nothing changes: the pixels,
resolution and brightness are exactly as they were.

Where 01 has made no overview of the scene the picture is drawn whole, as
before, and the CSV's `background` column says `no outline`; it is redrawn
once the overview exists. Where the overview holds no silhouette 04a could
form (nothing tissue-like on it) it says `no silhouette`, also drawn whole.
`--keep-background` draws every scene whole on purpose, for looking at the
glass itself, and the column says `kept`. Pictures drawn before this column
existed are redrawn on the next run.

Brightness: `--range dataset` (the default) uses 01's frozen display range,
`qc/display_ranges.json`, so the same brightness means the same signal on
every picture - the reason the overviews froze theirs. At 2.6 um/px a nucleus
is no longer averaged with the neuropil around it, so the brightest ones can
saturate under a range set at 5.2. `--range section` stretches each scene to
its own tissue instead (01's percentiles, 1 to 99.5, inside 01's log-space
tissue mask, leaving out clipped pixels): every picture well exposed, but
brightness no longer compares between pictures. Without a display_ranges.json
the section range is used and the CSV says so.

A frozen range whose top is the clip ceiling is no range at all - AF568 in this
dataset froze at 1070..65535 because its rim clips, which leaves the tissue
near black. Such a channel is stretched per scene as `section` does, the run
says so once, and the CSV's `marker_range_from` / `nuclear_range_from` say
`scene` instead of `dataset`.

Nothing here is measured. 05c reads native pixels from the CZI, whatever these
pictures look like.

Zeiss's preview copy (`--source zeiss_preview`, off by default)
----------------------------------------------------------------

The CZI also holds smaller copies of every scan that the Zeiss software made
at scan time, at 1/2, 1/4, 1/8... of the native size. `scene_render.source:
zeiss_preview` reads the copy at the picture's resolution in one call, so 15 of
every 16 native pixels at 2.6 um/px are never decoded. Same size of picture,
but Zeiss did the averaging and compressed that copy on its own, so values
differ slightly from the default and clipped areas are diluted as
docs/czi-reading-audit.md finding 2 describes - a block is drawn at the clip
ceiling only if the copy says so. Only these pictures change; every stage that
measures reads native pixels. At 0.65 um/px there is no copy to read and the
scan itself is used. The index's `source` column says which route drew each
picture, and changing the setting redraws them.

How it is made fast
-------------------

Decoding the scanner's JPEG-XR tiles is nearly all of the time. Three things
cut it without changing a byte of any picture (tests/test_czi_speed.py):

  * libCZI keeps the tiles it has decoded (`czi_read.open_czi`), so a tile
    crossed by three bands is decoded once, not three times;
  * the counterstain and the marker are read at the same time, one thread
    each, from their own handle on the file;
  * several CZIs are drawn at once (`--workers`, by default half the cores,
    at most four) - but a second scene only starts while
    the RAM itself, not the page file, has room for it. The first scene is
    held to the rule below and nothing else, so a tight machine draws one at a
    time, as before, instead of paging.

Pictures and index rows come out in the same order whatever finishes first.

Run:  python 01l_scene_renders.py
      python 01l_scene_renders.py --um-px 1.3 --marker AF488
      python 01l_scene_renders.py --um-px 0.65 --limit 5
      python 01l_scene_renders.py --range section --force --limit 10
"""

import argparse
import csv
import importlib.util
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from ls_config import CONFIG, CONFIG_PATH  # noqa: E402,F401
import ls_config as LC  # noqa: E402
import ls_channels as CH  # noqa: E402
import ls_io as IO  # noqa: E402
import czi_read as CR  # noqa: E402
import ls_progress as PG  # noqa: E402

OUT_ROOT = CONFIG["out_root"]
SOURCE_DIR = CONFIG["source_dir"]
NATIVE_UM = float(CONFIG["pixel_size_um"])
MARKERS = list(CH.marker_names(CONFIG))

MANIFEST_CSV = os.path.join(OUT_ROOT, "manifest", "manifest_scenes.csv")
QC_DIR = os.path.join(OUT_ROOT, "qc")
RANGES_PATH = os.path.join(QC_DIR, "display_ranges.json")
FIELD_DIR = os.path.join(QC_DIR, "flatfield")
# The folder, resolution and brightness are study settings (`scene_render.*`),
# so the app's Set up screen and a command-line run agree; the flags below
# override them for one run. The index stays under out_root whichever folder
# the pictures go to, so the app can tell the step has run.
_SR = CONFIG.get("scene_render") or {}
OUT_DIR = LC.scene_render_dir(CONFIG)
INDEX_CSV = os.path.join(QC_DIR, "scene_renders.csv")
# Every scene this run tried and could not draw, and why, as 01 keeps
# overview_failures.csv. Rewritten every run, header only when nothing failed,
# so an old list cannot outlive the fix.
FAILURES_CSV = os.path.join(QC_DIR, "scene_render_failures.csv")
FAILURE_KEYS = ["scene_uid", "file", "animal", "marker_channel", "scene_index", "reason"]

DEFAULT_UM_PX = float(_SR.get("um_per_px", 2.6))
DEFAULT_RANGE = _SR.get("range", "dataset")
DEFAULT_SOURCE = _SR.get("source") or "scan"
SOURCES = ("scan", "zeiss_preview")
#: CZIs drawn at once. Each uses two threads (one per channel), so half the
#: cores, and no more than four: past that the disk, not the CPU, is the limit.
DEFAULT_WORKERS = max(1, min(4, (os.cpu_count() or 2) // 2))
#: The finest picture made: the native pixels themselves. Banded reading keeps
#: the read small at any resolution, but the finished picture is held whole,
#: and at 0.65 that is 4x the area of 1.3 - so each scene is checked against
#: the memory this computer has free before it is drawn (see `fits`).
MIN_UM_PX = NATIVE_UM
#: Bytes held per output pixel at the peak: two float32 planes (counterstain
#: and marker), the tile-field gain, the 8-bit planes, the RGB composite and
#: PIL's copy of it while encoding, and the two clip masks. Rounded up.
BYTES_PER_PX = 26
#: Left over after a scene is allocated, so the allocation itself is not what
#: tips the computer over. Small, because `available_memory` already counts
#: only memory that can be had without taking it from anything running.
MEMORY_HEADROOM = 0.25e9
#: Native rows per read. Smaller than 04o's 2048 because a whole scene is wider
#: than a cut section; at 1024 rows a band of a 20,000 px wide scene is 40 MB.
BAND_ROWS = 1024

ONLY_DIR = "stain_only"
#: A frozen range whose top is within this fraction of the clip ceiling was
#: pinned there by clipped pixels and is not used.
PINNED = 0.95
#: Pixels sampled for a per-scene range: percentiles do not need every pixel,
#: and a 0.65 um/px scene has hundreds of millions.
RANGE_SAMPLE = 4_000_000

INDEX_KEYS = ["scene_uid", "file", "animal", "slide", "variant",
              "marker_channel", "scene_index", "section_order",
              "um_per_px", "block", "width", "height", "range",
              "nuclear_lo", "nuclear_hi", "nuclear_range_from",
              "marker_lo", "marker_hi", "marker_range_from",
              "clipped_fraction_marker", "tilefield_applied",
              "stain_counterstain_png", "stain_only_png", "reader", "source",
              "background"]


def _load(script, name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, script))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


OVERVIEW_DIR = os.path.join(OUT_ROOT, "overviews")
#: Picture rows the silhouette is stretched over at once: a whole 0.65 um/px
#: scene's float32 weights at once would be GBs.
MASK_BAND_ROWS = 1024

# 04o owns the banded block-mean read, the composite and which plane is which.
# Borrowed so these pictures and the curation composites cannot drift apart in
# colour or in how a plane is read.
SR = _load("04o_section_rgb.py", "_01l_section_rgb")
MARKER_COLOURS = CH.marker_colours(CONFIG)
NUCLEAR_COLOUR = CH.nuclear_colour(CONFIG)


def nuclear_name():
    """The counterstain's name, for the folder: DAPI unless the study says."""
    chans = CH.parse((CONFIG.get("acquisition") or {}).get("channels"))
    nuc = CH.nuclear(chans)
    return nuc.name if nuc is not None and nuc.name else "DAPI"


def block_for(um_px, native=NATIVE_UM):
    """Native pixels per side of one output pixel: the nearest whole number."""
    return max(1, int(round(um_px / native)))


def tissue_threshold(image):
    """01's log-space Otsu - see 01_overviews.tissue_threshold."""
    sample = image[::3, ::3].ravel()
    positive = sample[sample > 0].astype(np.float64)
    if positive.size < 500:
        return float(image.max())
    hist, edges = np.histogram(np.log1p(positive), bins=512)
    hist = hist.astype(np.float64)
    centres = (edges[:-1] + edges[1:]) / 2.0
    w1, w2 = np.cumsum(hist), np.cumsum(hist[::-1])[::-1]
    valid = (w1 > 0) & (w2 > 0)
    m1 = np.cumsum(hist * centres) / np.maximum(w1, 1e-9)
    m2 = (np.cumsum((hist * centres)[::-1]) / np.maximum(w2[::-1], 1e-9))[::-1]
    var = w1 * w2 * (m1 - m2) ** 2
    var[~valid] = -1
    return float(np.expm1(centres[int(np.argmax(var))]))


def apply_field(plane, field, b, clipped=None):
    """Divide out 01f's tile field, at each block's centre in the tile fold.

    `clipped` marks blocks whose every native pixel sat at the clip ceiling;
    they are put back at 65535 after the division rather than taking the
    field's tile pattern.

    `plane` is block-averaged b x b from the scene origin, so output pixel j
    covers native [j*b, (j+1)*b) and its centre is j*b + (b - 1)/2 - the
    position 01 folds when it corrects the overview.
    """
    if field is None:
        return plane
    n = field.shape[0]

    def idx(length):
        native = np.arange(length, dtype=np.float64) * b + (b - 1) / 2.0
        return (np.round((native % SR.PITCH_PX) / SR.FIELD_DIVISOR).astype(np.int64)) % n

    gain = field[np.ix_(idx(plane.shape[0]), idx(plane.shape[1]))].astype(np.float32)
    # In place: at 0.65 um/px a second float32 copy of a scene is a GB.
    out = plane if plane.dtype == np.float32 else plane.astype(np.float32)
    ok = gain > 0.05
    out[ok] /= gain[ok]
    np.clip(out, 0, 65535, out=out)
    if clipped is not None:
        out[clipped] = 65535
    return out


def read_blocks(read, rect, b):
    """The whole scene rectangle, block-averaged b x b, as float32."""
    rx, ry, rw, rh = rect
    got = SR.read_region(read, (rx, ry, rx + rw - 1, ry + rh - 1), rect, b,
                         band_rows=max(b, BAND_ROWS // b * b))
    if got is None:
        return None
    return np.asarray(got[0], dtype=np.float32)


def read_preview(doc, rect, b, idx, scene):
    """The scene at 1/b of native size from Zeiss's stored copy, as float32.

    Shaped as `read_blocks` shapes the default route - whole b x b blocks from
    the scene origin - so the tile field and the index read it the same way.
    """
    rx, ry, rw, rh = rect
    w, h = rw // b, rh // b
    if w < 1 or h < 1:
        return None
    got = CR.read_plane(doc, SR.GEO._Rect(rx, ry, rw, rh), idx, scene=scene,
                        zoom=1.0 / b)
    out = np.zeros((h, w), np.float32)
    hh, ww = min(h, got.shape[0]), min(w, got.shape[1])
    out[:hh, :ww] = got[:hh, :ww]
    return out


def overview_png(row):
    """01's counterstain overview of this scene: the picture the silhouette is
    made from, as 04o makes its own from it."""
    return os.path.join(OVERVIEW_DIR, row.get("animal", ""), row["marker_channel"],
                        row["scene_uid"] + "_DAPI.png")


def scene_silhouette(path, rect):
    """04a's tissue silhouette of the scene in the overview frame, or None.

    None when there is no overview, no silhouette can be formed from it, or
    the overview is not this scene rectangle - one of another size would put
    the section's outline in the wrong place.
    """
    if not os.path.exists(path):
        return None
    try:
        with Image.open(path) as im:
            ow, oh = im.size
    except OSError:
        return None
    scale = float(CONFIG.get("overview_target_um_per_px") or 5.2) / NATIVE_UM
    if abs(rect[2] / scale - ow) > 2 or abs(rect[3] / scale - oh) > 2:
        return None
    mask = SR.RF.overview_silhouette(path)
    return None if mask is None else np.asarray(mask, bool)


def _axis(n_out, b, n_native, n_ov):
    """Linear-interpolation indices and weights placing output pixels on the
    overview: output pixel j covers native [j*b, (j+1)*b), overview pixel p
    covers native [p*k, (p+1)*k), centres to centres."""
    k = n_native / n_ov
    f = (np.arange(n_out, dtype=np.float64) + 0.5) * b / k - 0.5
    f = np.clip(f, 0, n_ov - 1)
    i0 = np.floor(f).astype(np.int64)
    i1 = np.minimum(i0 + 1, n_ov - 1)
    return i0, i1, (f - i0).astype(np.float32)


def blank_outside(planes, mask, rect, b):
    """Zero every pixel of `planes` (same-shaped uint8 arrays) outside the
    silhouette `mask`, IN PLACE.

    The silhouette is interpolated and cut at one half, as 04o's frame_masks
    does, so its edge is not a staircase of 5.2 um overview pixels. Banded,
    so the memory is one band of weights whatever the resolution.
    """
    h, w = planes[0].shape[:2]
    m = mask.astype(np.float32)
    y0, y1, wy = _axis(h, b, rect[3], m.shape[0])
    x0, x1, wx = _axis(w, b, rect[2], m.shape[1])
    for r0 in range(0, h, MASK_BAND_ROWS):
        r1 = min(h, r0 + MASK_BAND_ROWS)
        a = wy[r0:r1, None]
        rows = m[y0[r0:r1]] * (1 - a) + m[y1[r0:r1]] * a
        inside = (rows[:, x0] * (1 - wx) + rows[:, x1] * wx) > 0.5
        for p in planes:
            p[r0:r1][~inside] = 0


def section_range(values, tissue, clipped=None):
    """01's percentiles, 1 and 99.5, on this scene's own tissue.

    Clipped pixels are left out: where more than 0.5% of the tissue clips, as
    it does on a third of the AF568 sections, they are the 99.5th percentile
    and the range tops out at 65535 with the tissue drawn near black.
    """
    step = max(1, int(np.sqrt(values.size / RANGE_SAMPLE)))
    v = values[::step, ::step]
    keep = v > 0
    if clipped is not None:
        keep &= ~clipped[::step, ::step]
    if tissue is not None:
        inside = keep & tissue[::step, ::step]
        if inside.sum() >= 500:
            keep = inside
    vals = v[keep]
    if vals.size < 50:
        return float(values.min()), float(values.max())
    return float(np.percentile(vals, 1.0)), float(np.percentile(vals, 99.5))


def pinned(rng, ceiling=65535):
    """True when a frozen range's top sits at the clip ceiling."""
    return rng is None or float(rng["hi"]) >= PINNED * ceiling


def to_8bit(values, lo, hi):
    hi = hi if hi > lo else lo + 1
    out = (values.astype(np.float32) - lo) * (255.0 / (hi - lo))
    np.clip(out, 0, 255, out=out)
    return np.rint(out).astype(np.uint8)


def load_ranges():
    if not os.path.exists(RANGES_PATH):
        return None
    with open(RANGES_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def load_fields(indices):
    fields = {}
    for idx in set(indices):
        p = os.path.join(FIELD_DIR, f"tilefield_c{idx}.npy")
        if os.path.exists(p):
            fields[idx] = np.load(p)
    return fields


def load_ceilings():
    """file name -> clip ceiling, from 00's manifest, as 01 reads it."""
    path = os.path.join(OUT_ROOT, "manifest", "manifest_files.csv")
    out = {}
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                try:
                    out[r["file"]] = int(r["clip_ceiling"])
                except (KeyError, TypeError, ValueError):
                    continue
    return out


def load_index():
    if not os.path.exists(INDEX_CSV):
        return {}
    with open(INDEX_CSV, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:   # columns renamed: carry an older index's paths across
        for old in ("stain_counterstain", "stain_only"):
            if r.get(old) and not r.get(old + "_png"):
                r[old + "_png"] = r[old]
    return {index_key(r): r for r in rows if index_key(r)}


def index_key(row_or_path):
    """A picture's row is keyed by its stain-only path, normalised.

    Keyed by scene_uid alone, a run into another folder or at another
    resolution replaced the row of a picture that is still on disk.
    """
    p = row_or_path if isinstance(row_or_path, str) else (
        row_or_path.get("stain_only_png") or "")
    return os.path.normcase(os.path.abspath(p)) if p else ""


def available_ram():
    """Bytes of RAM free now, page file NOT counted, or None when unknown.

    The test for drawing a second scene alongside the first: a scene that
    spills into the page file costs more than it gains running in parallel.
    """
    try:
        import psutil
        return int(psutil.virtual_memory().available)
    except Exception:  # noqa: BLE001 - psutil is optional
        pass
    if sys.platform == "win32":
        try:
            import ctypes

            class _Status(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = _Status()
            st.dwLength = ctypes.sizeof(_Status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return int(st.ullAvailPhys)
        except Exception:  # noqa: BLE001
            return None
        return None
    try:
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                if key == "MemAvailable":
                    return int(rest.split()[0]) * 1024
    except (OSError, ValueError):
        pass
    return None


def available_memory():
    """Bytes a new allocation can get now, or None when the platform will not say.

    Free RAM PLUS free page file (swap), because that is the limit an
    allocation actually fails at. Free RAM alone was the first version: on the
    laptop it read 0.5 GB with the usual programs open, and every scene was
    skipped - including 2.6 um/px ones a few hundred MB in size, which Windows
    hands out from the page file without complaint. Exceeding free RAM makes a
    scene slower, not impossible.
    """
    try:
        import psutil
        return int(psutil.virtual_memory().available + psutil.swap_memory().free)
    except Exception:  # noqa: BLE001 - psutil is optional
        pass
    if sys.platform == "win32":
        try:
            import ctypes

            class _Status(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = _Status()
            st.dwLength = ctypes.sizeof(_Status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                # The commit limit left: RAM and page file together.
                return int(st.ullAvailPageFile)
        except Exception:  # noqa: BLE001
            return None
        return None
    try:
        got = {}
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                if key in ("MemAvailable", "SwapFree"):
                    got[key] = int(rest.split()[0]) * 1024
        if "MemAvailable" in got:
            return got["MemAvailable"] + got.get("SwapFree", 0)
    except (OSError, ValueError):
        pass
    return None


def memory_needed(rect, b):
    """Peak bytes to draw one scene at b x b native pixels per output pixel:
    the picture, a band in flight for each of the two channels, and each
    channel's cache of decoded tiles."""
    return ((rect[2] // b) * (rect[3] // b) * BYTES_PER_PX
            + 2 * (2 * BAND_ROWS * rect[2] * 2)
            + 2 * CR.tile_cache_bytes(rect[2]))


class MemoryGate:
    """Lets a scene start only while there is memory for it.

    With nothing else drawing, the rule is `fits` - RAM and page file - as it
    has always been, and a refusal is final for that scene. With other scenes
    in flight, the scene waits until free RAM alone covers it on top of what
    those scenes reserved (counted again even once allocated: cautious on
    purpose), or until it is the only one left.
    """

    def __init__(self):
        self.cond = threading.Condition()
        self.inflight = 0

    def acquire(self, need):
        """None once `need` bytes are reserved, else the bytes free (refused)."""
        with self.cond:
            while True:
                if self.inflight == 0:
                    room = fits(need)
                    if room is None:
                        self.inflight += need
                    return room
                ram = available_ram()
                if ram is not None and need + self.inflight + MEMORY_HEADROOM <= ram:
                    self.inflight += need
                    return None
                self.cond.wait(timeout=2.0)

    def release(self, need):
        with self.cond:
            self.inflight -= need
            self.cond.notify_all()


def fits(need):
    """None when `need` bytes fit in free memory (or nobody can say), else the
    bytes free. Checked per scene, so a big scene is skipped with a reason and
    the run carries on, rather than the whole run dying part way."""
    free = available_memory()
    if free is None or need + MEMORY_HEADROOM <= free:
        return None
    return free


def same_drawing(row, um_px, range_mode, only_path, source="scan",
                 background="blanked"):
    """True when the index says the picture on disk was drawn as asked now.

    Changing the resolution or brightness in Set up has to redraw the pictures
    on the next run without anyone remembering --force; a picture with no row
    (drawn before the index existed, or by hand) is kept, as 01 keeps its own.
    `background` is what this run would do outside the section (see
    `background_for`); a picture drawn before 01 made its overview is redrawn
    once the overview is there.
    """
    if row is None:
        return True
    try:
        same_px = abs(float(row.get("um_per_px") or 0) - um_px) < 0.005
    except ValueError:
        same_px = False
    drawn = row.get("background")
    if drawn == "no silhouette":     # tried, and the overview had no outline
        drawn = "blanked"
    return (same_px and row.get("range") == range_mode
            and (row.get("source") or "scan") == source
            and drawn == background
            and index_key(row) == index_key(only_path))


def background_for(row, keep_background):
    """What a picture of this scene is drawn with outside the section now:
    `kept` when asked, `no outline` when 01 has made no overview to take the
    section's outline from, else `blanked`."""
    if keep_background:
        return "kept"
    return "blanked" if os.path.exists(overview_png(row)) else "no outline"


def save_png(img, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with IO.atomic_save(path) as tmp:
        Image.fromarray(img).save(tmp, format="PNG")


def render_scene(read_nuc, read_mark, rect, b, fields_nm, ranges, range_mode,
                 marker, colour, ceiling=65535):
    """(composite rgb, stain-only grey, row facts) for one scene, or None.

    `read_nuc` and `read_mark` take (rect, b) and return the plane in b x b
    blocks as float32 - `read_blocks` over the scan, or `read_preview`.
    """
    nf, mf = fields_nm
    # The marker is read on a second thread while the counterstain is read and
    # drawn here: libCZI decodes without holding the GIL, so the two run on two
    # cores. Its own handle and cache, so the two never share a reader.
    with ThreadPoolExecutor(1) as ex:
        pending = ex.submit(read_mark, rect, b)
        try:
            return _render(read_nuc, pending, rect, b, nf, mf, ranges, range_mode,
                           marker, colour, ceiling)
        finally:
            pending.cancel()


def _render(read_nuc, pending, rect, b, nf, mf, ranges, range_mode, marker,
            colour, ceiling):
    nuc = read_nuc(rect, b)
    if nuc is None:
        return None
    # A block mean at the ceiling means every native pixel in it clipped.
    nclip = nuc >= ceiling - 0.5
    nuc = apply_field(nuc, nf, b, nclip)
    frozen_n = None if range_mode == "section" else ranges.get("DAPI")
    frozen_m = None if range_mode == "section" else ranges.get(marker)
    n_from = "scene" if pinned(frozen_n, ceiling) else "dataset"
    m_from = "scene" if pinned(frozen_m, ceiling) else "dataset"
    tissue = None
    if "scene" in (n_from, m_from):
        # Threshold from a sample, clipped pixels zeroed (left out, as 0 is):
        # a full-size copy at 0.65 um/px would be most of a GB.
        step = max(1, int(np.sqrt(nuc.size / RANGE_SAMPLE)))
        sample = np.where(nclip[::step, ::step], 0, nuc[::step, ::step])
        tissue = nuc > tissue_threshold(sample)

    if n_from == "dataset":
        n_lo, n_hi = frozen_n["lo"], frozen_n["hi"]
    else:
        n_lo, n_hi = section_range(nuc, tissue, nclip)
    nuc8 = to_8bit(nuc, n_lo, n_hi)
    del nuc

    mark = pending.result()
    if mark is None:
        return None
    mclip = mark >= ceiling - 0.5
    mark = apply_field(mark, mf, b, mclip)
    if m_from == "dataset":
        m_lo, m_hi = frozen_m["lo"], frozen_m["hi"]
    else:
        m_lo, m_hi = section_range(mark, tissue, mclip | nclip)
    mark8 = to_8bit(mark, m_lo, m_hi)
    clipped_frac = float(mclip.mean())
    del mark, mclip, nclip

    rgb = SR.composite(mark8, nuc8, colour, NUCLEAR_COLOUR)
    facts = {"nuclear_lo": "%.0f" % n_lo, "nuclear_hi": "%.0f" % n_hi,
             "nuclear_range_from": n_from,
             "marker_lo": "%.0f" % m_lo, "marker_hi": "%.0f" % m_hi,
             "marker_range_from": m_from,
             "clipped_fraction_marker": "%.4f" % clipped_frac,
             "width": mark8.shape[1], "height": mark8.shape[0]}
    return rgb, mark8, facts


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--um-px", type=float, default=DEFAULT_UM_PX, metavar="UM",
                    help=f"picture resolution in um per pixel (default from config "
                         f"scene_render.um_per_px: {DEFAULT_UM_PX}; "
                         f"finest {MIN_UM_PX}). Rounded to whole native "
                         f"{NATIVE_UM} um pixels")
    ap.add_argument("--range", choices=["dataset", "section"], default=DEFAULT_RANGE,
                    help="dataset: 01's frozen brightness range, so pictures compare. "
                         "section: each scene stretched to its own tissue "
                         f"(default from config scene_render.range: {DEFAULT_RANGE})")
    ap.add_argument("--out-dir", default=None, metavar="DIR",
                    help="where the pictures go (default from config "
                         f"scene_render.out_dir: {OUT_DIR})")
    ap.add_argument("--source", choices=SOURCES, default=DEFAULT_SOURCE,
                    help="scan: average the scan's own pixels (exact, the default). "
                         "zeiss_preview: Zeiss's stored smaller copy, much faster, "
                         "values differ slightly. Pictures only; nothing measured "
                         f"reads them (default from config scene_render.source: "
                         f"{DEFAULT_SOURCE})")
    ap.add_argument("--marker", choices=MARKERS or None, default=None,
                    help="only the scans of this marker (default: every marker)")
    ap.add_argument("--keep-background", action="store_true",
                    help="draw the whole scene rectangle, glass and pen ring "
                         "included (default blanks everything outside the "
                         "section, as the curator does)")
    ap.add_argument("--force", action="store_true",
                    help="redraw pictures that already exist (default skips them)")
    ap.add_argument("--workers", type=int, default=DEFAULT_WORKERS, metavar="N",
                    help="CZIs drawn at once, while free RAM allows (default half "
                         f"the cores, at most 4: {DEFAULT_WORKERS} here)")
    ap.add_argument("--limit", type=int, default=0, metavar="N",
                    help="stop after trying N scenes, drawn or not (0 = all)")
    args = ap.parse_args()

    if args.um_px < MIN_UM_PX - 1e-9:
        raise SystemExit(
            f"--um-px {args.um_px}: the scan's own pixels are {NATIVE_UM} um, "
            f"so that is the finest picture there is.")
    out_dir = args.out_dir or OUT_DIR
    if args.source not in SOURCES:
        raise SystemExit(f"scene_render.source {args.source!r}: choose one of "
                         f"{', '.join(SOURCES)}")
    b = block_for(args.um_px)
    # At native size there is no smaller copy: both routes are the scan itself.
    source = args.source if b > 1 else "scan"
    if source != args.source:
        print("0.65 um/px is the scan's own pixels - drawing from the scan")
    if source == "zeiss_preview":
        print("drawing from Zeiss's stored preview copy (faster; pictures differ "
              "slightly from the scan route; nothing measured reads them)")
    um_px = b * NATIVE_UM
    if abs(um_px - args.um_px) > 1e-6:
        print(f"{args.um_px} um/px rounded to {um_px:.2f} ({b}x{b} native pixels)")

    try:
        from pylibCZIrw import czi as pyczi
    except ImportError:
        raise SystemExit("this stage reads the CZIs and needs pylibCZIrw - run it with "
                         "work/czienv, the interpreter 01 runs on.")

    with open(MANIFEST_CSV, newline="", encoding="utf-8") as fh:
        scenes = [r for r in csv.DictReader(fh)
                  if not args.marker or r.get("marker_channel") == args.marker]

    range_mode = args.range
    ranges = load_ranges() if range_mode == "dataset" else None
    if range_mode == "dataset" and ranges is None:
        print(f"no {RANGES_PATH} yet (01 writes it) - stretching each scene to "
              f"its own tissue instead")
        range_mode = "section"
    ceilings = load_ceilings()
    if range_mode == "dataset":
        for name, rng in sorted(ranges.items()):
            if pinned(rng):
                print(f"{RANGES_PATH}: {name} is frozen at {rng['lo']}..{rng['hi']}, "
                      "pinned at the clip ceiling - stretching it per scene instead")

    nuc_dir = "stain+" + nuclear_name()
    index = load_index()
    by_file = {}
    for r in scenes:
        by_file.setdefault(r["file"], []).append(r)

    # Which scenes each file has to draw, decided up front and in file order,
    # so --limit and the skips are what they were when this ran one file at a
    # time.
    existing = tried = 0
    failed = []
    jobs = []           # (fname, n scenes in it, rows to draw, marker info)
    fields_cache = {}
    for fname in sorted(by_file):
        if args.limit and tried >= args.limit:
            break
        rows = sorted(by_file[fname], key=lambda r: int(r["scene_index"]))
        marker = rows[0]["marker_channel"]
        colour = MARKER_COLOURS.get(marker)
        if colour is None:
            failed += [(r["scene_uid"], f"no display colour for {marker}") for r in rows]
            jobs.append((fname, len(rows), [], None))
            continue
        n_idx, m_idx = SR.plane_indices(marker)
        if (n_idx, m_idx) not in fields_cache:
            f = load_fields((n_idx, m_idx))
            fields_cache[(n_idx, m_idx)] = (f.get(n_idx), f.get(m_idx))
        mdir = os.path.join(out_dir, marker)

        todo = []
        for r in rows:
            uid = r["scene_uid"]
            both = [os.path.join(mdir, nuc_dir, uid + ".png"),
                    os.path.join(mdir, ONLY_DIR, uid + ".png")]
            if (not args.force and all(os.path.exists(p) for p in both)
                    and same_drawing(index.get(index_key(both[1])), um_px, range_mode,
                                     both[1], source,
                                     background_for(r, args.keep_background))):
                existing += 1
                continue
            todo.append(r)
        # --limit counts the scenes tried, not the ones drawn: a run where every
        # scene is skipped (too little memory) must still stop after N.
        if args.limit:
            todo = todo[:max(0, args.limit - tried)]
        tried += len(todo)
        jobs.append((fname, len(rows), todo,
                     (marker, colour, n_idx, m_idx, fields_cache[(n_idx, m_idx)], mdir)))

    gate = MemoryGate()

    def draw_file(fname, todo, info):
        """(index rows by key, failures, uids drawn) for one CZI's scenes."""
        marker, colour, n_idx, m_idx, fields_nm, mdir = info
        new_rows, bad, drawn = {}, [], set()
        path = os.path.join(SOURCE_DIR, fname)
        if not os.path.exists(path):
            return new_rows, [(r["scene_uid"], f"CZI not found: {path}") for r in todo], drawn
        try:
            with pyczi.open_czi(path) as doc:
                rects = CR.scene_rects(doc)
            widest = max([rects[int(r["scene_index"])][2] for r in todo
                          if int(r["scene_index"]) in rects] or [1])
            # One handle per channel, each with its own cache of decoded tiles.
            with CR.open_czi(path, widest) as doc_n, CR.open_czi(path, widest) as doc_m:
                for r in todo:
                    uid, si = r["scene_uid"], int(r["scene_index"])
                    if si not in rects:
                        bad.append((uid, f"scene {si} absent from {fname}"))
                        continue

                    def reader(doc, idx, si=si):
                        if source == "zeiss_preview":
                            return lambda rect, bb: read_preview(doc, rect, bb, idx, si)
                        plane = (lambda x, y, w, h: CR.read_plane(
                            doc, SR.GEO._Rect(x, y, w, h), idx, scene=si))
                        return lambda rect, bb: read_blocks(plane, rect, bb)

                    need = memory_needed(rects[si], b)
                    room = gate.acquire(need)
                    if room is not None:
                        bad.append((uid, f"needs about {need / 1e9:.1f} GB at "
                                    f"{um_px:.2f} um/px and only {room / 1e9:.1f} GB "
                                    "can be had (memory and page file) - close "
                                    "other programs or choose a coarser resolution"))
                        continue
                    # Per scene: one picture that cannot be written (open in a
                    # viewer on Windows, say) or drawn must not cost the rest
                    # of the slide; it is named and the others carry on.
                    try:
                        got = render_scene(reader(doc_n, n_idx), reader(doc_m, m_idx),
                                           rects[si], b, fields_nm, ranges, range_mode,
                                           marker, colour, ceilings.get(fname, 65535))
                        if got is None:
                            bad.append((uid, "empty scene rectangle"))
                            continue
                        rgb, only, facts = got
                        bg = background_for(r, args.keep_background)
                        if bg == "blanked":
                            sil = scene_silhouette(overview_png(r), rects[si])
                            if sil is None:
                                bg = "no silhouette"
                            else:
                                blank_outside((rgb, only), sil, rects[si], b)
                        facts["background"] = bg
                        p_rgb = os.path.join(mdir, nuc_dir, uid + ".png")
                        p_only = os.path.join(mdir, ONLY_DIR, uid + ".png")
                        save_png(rgb, p_rgb)
                        save_png(only, p_only)
                        del rgb, only, got
                    except Exception as exc:  # noqa: BLE001 - see above
                        bad.append((uid, f"{type(exc).__name__}: {exc}"))
                        print(f"\n  !! {uid}: {type(exc).__name__}: {exc}")
                        continue
                    finally:
                        gate.release(need)
                    new_rows[index_key(p_only)] = {
                        "scene_uid": uid, "file": fname, "animal": r.get("animal", ""),
                        "slide": r.get("slide", ""), "variant": r.get("variant", ""),
                        "marker_channel": marker, "scene_index": si,
                        "section_order": r.get("section_order", ""),
                        "um_per_px": "%.2f" % um_px, "block": b, "range": range_mode,
                        "tilefield_applied": int(fields_nm[1] is not None),
                        "stain_counterstain_png": os.path.abspath(p_rgb),
                        "stain_only_png": os.path.abspath(p_only),
                        "reader": "pylibCZIrw", "source": source, **facts}
                    drawn.add(uid)
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the rest
            # Only the scenes not already drawn or already named: a file that
            # fails part-way (its handles, say) still drew the ones before.
            named = {uid for uid, _ in bad}
            bad += [(r["scene_uid"], f"{type(exc).__name__}: {exc}") for r in todo
                    if r["scene_uid"] not in drawn and r["scene_uid"] not in named]
            print(f"\n  !! {fname}: {exc}")
        return new_rows, bad, drawn

    workers = max(1, args.workers)
    work = [j for j in jobs if j[2]]
    if len(work) > 1 and workers > 1:
        print(f"drawing up to {min(workers, len(work))} CZIs at once")
    made = 0
    t0 = time.time()
    n_scenes, n_seen = len(scenes), 0
    with ThreadPoolExecutor(workers) as ex:
        futures = {j[0]: ex.submit(draw_file, j[0], j[2], j[3]) for j in work}
        # Collected in file order, not as they finish, so the index rows and
        # the failures list come out as a one-at-a-time run writes them.
        for fname, n_rows, todo, _info in jobs:
            PG.report(n_seen, n_scenes, "sections")
            n_seen += n_rows
            if fname not in futures:
                continue
            new_rows, bad, drawn = futures[fname].result()
            index.update(new_rows)
            failed += bad
            made += len(drawn)
            # Checkpoint per file, as 01 does: this runs for a long time against a
            # drive that has dropped writes before.
            if index:
                IO.atomic_write_csv(INDEX_CSV, list(index.values()), INDEX_KEYS)
            # Only when this file drew something: a run that skips every scene
            # otherwise fills its log with one identical progress line per CZI.
            if drawn:
                rate = made / max(time.time() - t0, 1e-9)
                print(f"\r  drew {made} scenes  ({rate:.2f}/s, "
                      f"{(time.time() - t0) / 60:.1f} min)", end="")

    print()
    print(f"wrote {made} scenes at {um_px:.2f} um/px ({range_mode} range"
          f"{', Zeiss preview' if source == 'zeiss_preview' else ''}) to {out_dir}")
    if existing:
        print(f"  {existing} already there, left alone (--force redraws them)")
    by_uid = {r["scene_uid"]: r for r in scenes}
    os.makedirs(QC_DIR, exist_ok=True)
    IO.atomic_write_csv(FAILURES_CSV, [
        {**{k: by_uid.get(uid, {}).get(k, "") for k in FAILURE_KEYS},
         "scene_uid": uid, "reason": why} for uid, why in failed], FAILURE_KEYS)
    if failed:
        print(f"  {len(failed)} not drawn:")
        for uid, why in failed[:10]:
            print(f"    {uid}  {why}")
        # A message rather than a bare `return 1`: app/runner.py ignores what
        # main() returns and reports a SystemExit message as the step failing.
        # From the command line it is still exit status 1.
        raise SystemExit(f"{len(failed)} scene(s) not drawn - listed above "
                         f"and in {FAILURES_CSV}")


if __name__ == "__main__":
    main()
