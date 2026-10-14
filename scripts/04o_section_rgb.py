"""Stage 4o - colour composites of the curation sections, in the reformatted frame.

The ROI curator was showing `sections_AF568/*.png`: 256x256, greyscale, and
**DAPI**, because that is what `04a_reformat` runs on - the marker channel is a
sparse signal and a poor silhouette, so geometry is decided on the counterstain.
Those images are already cut and tilted; what they are not is the two-colour
picture the section is actually read as, and the marker channel - the thing being
quantified - is not in them at all.

This writes the same sections as RGB composites in the *same* frame: the
counterstain in its colour - blue unless `display.nuclear_colour` says
otherwise, and it is the geometry channel, exactly what the curator showed
before - plus ONE marker in its own, from `display.colours`.

One marker, because this stage builds one marker per run (`--marker`) into that
marker's own `sections*_rgb/`. Under the paired layout a composite is therefore
a marker and the counterstain, never two markers: the two markers are separate
physical sections and have separate directories. The set across two runs is
red-and-green; no single picture is. An earlier version of this docstring
listed both markers here, which is how a reader comes to believe otherwise.

The frame has to be identical or every landmark placed on it is placed on the
wrong picture, so the marker is not reformatted separately. It rides through
`reformat(companion=...)` as a passenger on the geometry channel's own rotation,
flip, crop, pad and resize - the same trick the artifact and censor masks already
use - and the blue channel of the output is the byte-for-byte image the curator
was showing. `--verify` checks exactly that rather than assuming it.

Only the sections in `roi_worklist.csv` are built by default, so that covers the
curation job rather than all 718 reformatted sections. `--all` builds the
marker's whole reformat index instead, and `--include-excluded` builds every
scanned section in `focus.csv` - including the 1,066 the pipeline threw out.

Those excluded sections are exactly why this reaches past the index. They have a
greyscale image (`04a --render-excluded` writes one so they can be LOOKED at in
Review mode) but no composite, so the review grid showed them in grey next to
colour neighbours - which reads as a rendering fault rather than as "this one
was excluded". Being in `sections_*_rgb/` still puts nothing into the analysis -
the index is what does that, and this does not touch it.

AN EXCLUDED SECTION IS BUILT UNMASKED, because that is what its greyscale is.
04a masks only under `--mask-artifacts`, and the excluded ones were rendered
before `04g --include-excluded` gave them artifact masks at all - so their
stored image has no masking in it, while every section in the index does.
Applying the mask here anyway made 848 of the 1,066 fail `--verify` against the
picture the grid actually shows: same frame, different pixels.

It is also the better picture to review. An excluded section is on screen so
somebody can decide whether to reinstate it, and a base image with the artifact
already painted black cannot answer that - the mask is a layer you switch on in
Review mode, and it has nothing to show if it has been baked in.

Thumbnails
----------

`--thumbs` writes a `RF.GRID`-sized copy of each composite into
`<sections>_rgb_thumb/`. The review grid draws a few hundred cells at 78px, and
a 768px composite is 448 KB against 21 KB for the greyscale it replaced - one
animal's grid went from 4.7 MB to 56.6 MB for pictures nobody sees at that size.
That is the same mistake the grid already made once with the 1632x1862
overviews, which left most cells blank behind `loading="lazy"` and looked broken
rather than slow.

A thumbnail is a DOWNSCALE OF THE COMPOSITE, never a re-render. Re-rendering
would put a second code path between the picture and the frame it was built in,
and the only thing that makes these safe to show next to landmark work is that
they are the same image, smaller.

Rendering from the CZI
----------------------

The route above starts from the 01 overview, and every step of it loses
something: the overview is 5.20 um/px against the scan's 0.65 (64x fewer
pixels), it is 8-bit through a frozen display range that clips the top 0.5% of
tissue, 04a squashes it SQUARE (anisotropically) and then resamples it three
times - into the working grid, through the rotation, onto the output grid - and
the result is shown at about 8 um per pixel.

`curation_render.source: czi` (the default) skips all of it. The frame is still
decided exactly as before - 04a's mask, angle and crop at WORK_SIZE, which is
what every stored landmark means - but the PIXELS are fetched from the CZI's
own 16-bit planes through the same grid -> slide map 05a uses to put ROIs back
on the slide, in one resampling, at `curation_render.um_per_px`. Binned by
block mean first where that is coarser than native, so a downsample cannot
alias. Tile field applied as 01 applies it, stretched by 04a's own rule
(median - 1 MAD to median + 4 MAD of in-tissue pixels) but on the 16-bit
values, so there is no second quantisation.

Same frame, so the curator needs nothing new: it lifts landmarks placed on a
768 composite into the larger picture by the width ratio (`syncFrame`), and
every export is already divided back to the canonical 256 grid. And because
the picture is built FROM 05a's map, a point on it is where 05a will measure;
the 768 composite only approximated that.

What it does not undo: the scan's own JPEG-XR tile compression (fixed at
acquisition, see docs/pipeline-guide.md), and the display stretch, which
saturates the brightest in-tissue pixels exactly as before. Measurement never
saw any of this - 05c reads native pixels from the CZI.

Run:  python 04o_section_rgb.py
      python 04o_section_rgb.py --tier core
      python 04o_section_rgb.py --verify
      python 04o_section_rgb.py --thumbs --all --marker AF488
"""

import sys
import argparse
import csv
import importlib.util
import os

import numpy as np
from PIL import Image
from scipy import ndimage

_HERE = os.path.dirname(os.path.abspath(__file__))
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
# ls_config resolves LS_CONFIG, applies the defaults and validates once for
# the whole process. Imported, not re-implemented: this block used to be four
# lines copy-pasted into every stage.
from ls_config import CONFIG, CONFIG_PATH  # noqa: E402
import ls_channels as CH  # noqa: E402
import czi_read as CR  # noqa: E402

# The markers this study measures, in declared order. ls_channels is the
# single source of that list; naming a fluorophore here would pin the stage
# to one study.
MARKERS = list(CH.marker_names(CONFIG))

_spec = importlib.util.spec_from_file_location("_rf", os.path.join(_HERE, "04a_reformat.py"))
RF = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RF)

_lsio = importlib.util.spec_from_file_location(
    "_lsio", os.path.join(_HERE, "ls_io.py"))
IO = importlib.util.module_from_spec(_lsio)
_lsio.loader.exec_module(IO)

# 05a owns the map from the canonical grid back to the slide. It is borrowed,
# not restated: a second copy of six chained transforms is six chances for the
# picture and the measurement to disagree.
_geo = importlib.util.spec_from_file_location(
    "_geo", os.path.join(_HERE, "05a_roi_geometry.py"))
GEO = importlib.util.module_from_spec(_geo)
_geo.loader.exec_module(GEO)

OUT_ROOT = RF.OUT_ROOT
MANIFEST_CSV = os.path.join(OUT_ROOT, "manifest", "manifest_scenes.csv")
FIELD_DIR = os.path.join(OUT_ROOT, "qc", "flatfield")

_RENDER = CONFIG.get("curation_render") or {}
RENDER_SOURCE = _RENDER.get("source", "czi")
RENDER_UM_PX = float(_RENDER.get("um_per_px", 2.6))

# The tile field's fold, as 01_overviews applies it: tiles start on exact
# multiples of the pitch from the scene origin.
PITCH_PX = 1836
FIELD_DIVISOR = 12
REFORMAT_DIR = RF.REFORMAT_DIR
WORKLIST_CSV = os.path.join(REFORMAT_DIR, "roi_worklist.csv")
FOCUS_CSV = os.path.join(OUT_ROOT, "qc", "focus.csv")

# Marker -> the colour it is drawn in, and the counterstain's own colour.
# Positional by default - first marker shown red, second green, which is what
# this stage has always written - and named by `display.colours` when a study
# wants otherwise. ls_channels owns the rule, and the curator's Review pane
# reads the SAME function: naming a fluorophore or a plane index here is how
# the pane and the composites came to disagree about what colour a section is.
MARKER_COLOURS = CH.marker_colours(CONFIG)
NUCLEAR_COLOUR = CH.nuclear_colour(CONFIG)


def composite(marker_img, dapi_img, colour, nuclear=(0., 0., 1.)):
    """One marker in its own colour, over the counterstain in its own.

    Per plane the two contributions are combined with MAX, not sum. For a
    marker with no blue in it - which is every marker this pipeline has ever
    had - max(0, dapi) is dapi exactly, so the blue plane is still the
    byte-for-byte greyscale the geometry was decided on and --verify still
    means what it meant.

    Where they DO share a plane - a magenta marker is red plus blue, and blue
    is the counterstain - max keeps both readable and never clips. Sum was the
    alternative: it makes overlap brighter, the conventional way to read
    co-localisation, but it saturates to 255 and then a very bright marker and
    a marker-over-counterstain look identical. This picture exists so somebody
    can judge whether a section is measurable, so not clipping wins.

    Both images are uint8 and both coefficients are exact in binary for every
    colour in the table, so a plane whose coefficient is 1.0 comes back as the
    input array byte for byte. tests/test_composite.py pins that with
    np.array_equal against the plane assignment this replaced, because the
    constants probe cannot see pixels and 130 curated sections carry landmark
    coordinates placed on the composites already on disk.
    """
    mark = np.asarray(marker_img, dtype=np.float64)
    dapi = np.asarray(dapi_img, dtype=np.float64)
    rgb = np.zeros(mark.shape + (3,), np.uint8)
    for p in range(3):
        plane = np.maximum(colour[p] * mark, nuclear[p] * dapi)
        rgb[..., p] = np.clip(np.rint(plane), 0, 255).astype(np.uint8)
    return rgb


def plane_indices(marker):
    """(nuclear plane, marker plane) in the CZI, resolved as 05c resolves them.

    The channel table when the study has one, `channels.dapi_index` and
    `channels.marker_index` when it does not - which is the paired LS study,
    and gives 0 and 1, the planes 01 drew the overviews from.
    """
    chans = CH.parse((CONFIG.get("acquisition") or {}).get("channels"))
    legacy = CONFIG.get("channels") or {}
    nuc = CH.nuclear(chans)
    n_idx = (int(nuc.index) if nuc is not None and nuc.index is not None
             else int(legacy.get("dapi_index", 0)))
    by_name = {c.name: c for c in chans}
    c = by_name.get(marker)
    m_idx = (int(c.index) if c is not None and c.index is not None
             else int(legacy.get("marker_index", 1)))
    return n_idx, m_idx


def czi_frame(rep, scene_rect, overview_um_px, native_um_px):
    """The 2x3 map canonical grid (u, v) -> absolute CZI pixel (x, y).

    Built exactly as 05a builds the matrix it writes to roi_geometry.csv, from
    the same `report` of the same 04a call, so the picture rendered through it
    and the boxes 05c measures are one geometry.
    """
    g2o = GEO.grid_to_overview(rep)
    scale = overview_um_px / native_um_px
    rx, ry = scene_rect[0], scene_rect[1]
    return GEO.affine_from(lambda u, v: (lambda p: (rx + p[0] * scale,
                                                    ry + p[1] * scale))(g2o(u, v)))


def render_side(M, native_um_px, out_um_px, grid=None):
    """Output edge in pixels, so that NO direction is sampled coarser than asked.

    The canonical frame is anisotropic - 04a squashes a rectangular overview
    square - so one grid step is a different length along its two axes. Sizing
    by the larger singular value puts `out_um_px` on the stretched axis and
    oversamples the other, rather than undersampling either.
    """
    grid = grid or RF.GRID
    s_max = float(np.linalg.svd(np.asarray(M, float)[:, :2], compute_uv=False)[0])
    return max(grid, int(np.ceil(grid * s_max * native_um_px / out_um_px)))


def output_grid(side, grid=None):
    """Canonical (u, v) of every output pixel centre, as two (side, side) arrays.

    The same pixel-centre convention as PIL's resize, which is what 05a's map
    assumes of the canonical grid: output pixel i is canonical (i + 0.5) * grid
    / side - 0.5. So this picture IS the canonical frame scaled by side/grid,
    which is the relation the curator's syncFrame and every export rely on.
    """
    grid = grid or RF.GRID
    t = (np.arange(side, dtype=np.float64) + 0.5) * grid / side - 0.5
    return np.meshgrid(t, t)


def bin_factor(native_um_px, out_um_px):
    """Whole native pixels averaged per sample: the anti-aliasing for a downsample."""
    return max(1, int(np.floor(out_um_px / native_um_px + 1e-9)))


def sample_plane(read, X, Y, scene_rect, b, field=None):
    """One CZI plane sampled at the absolute native coordinates (X, Y).

    `read(x, y, w, h)` returns that native rectangle of the plane. Only the
    bounding box of the points is read, then block-averaged b x b and
    interpolated bilinearly - a single resampling of the scanner's pixels.
    Points outside the scene rectangle are 0, which is what 01 writes for a
    corner that was never scanned.

    The tile field is divided out AFTER sampling, at each sample's own
    position in the tile fold. It is smooth across a 12 px field cell, so the
    order does not matter to the picture, and this way it costs one lookup per
    output pixel instead of a pass over the whole native rectangle.
    """
    rx, ry, rw, rh = scene_rect
    x0 = int(max(rx, np.floor(X.min()) - b))
    y0 = int(max(ry, np.floor(Y.min()) - b))
    x1 = int(min(rx + rw, np.ceil(X.max()) + b + 1))
    y1 = int(min(ry + rh, np.ceil(Y.max()) + b + 1))
    out = np.zeros(X.shape, np.float32)
    if x1 - x0 < b or y1 - y0 < b:
        return out
    w = (x1 - x0) // b * b
    h = (y1 - y0) // b * b
    raw = np.asarray(read(x0, y0, w, h))
    if b > 1:
        raw = raw.reshape(h // b, b, w // b, b).mean(axis=(1, 3), dtype=np.float32)
    else:
        raw = raw.astype(np.float32)
    # A block covers native [x0 + j*b, x0 + (j+1)*b); its centre, in the
    # pixel-centre coordinates X is in, is x0 + j*b + (b - 1)/2.
    cx = (X - x0 - (b - 1) / 2.0) / b
    cy = (Y - y0 - (b - 1) / 2.0) / b
    inside = (X >= rx - 0.5) & (X <= rx + rw - 0.5) & (Y >= ry - 0.5) & (Y <= ry + rh - 0.5)
    vals = ndimage.map_coordinates(raw, [cy, cx], order=1, mode="nearest")
    out = np.where(inside, vals, 0.0).astype(np.float32)
    if field is not None:
        n = field.shape[0]
        fx = (np.round(((X - rx) % PITCH_PX) / FIELD_DIVISOR).astype(np.int64)) % n
        fy = (np.round(((Y - ry) % PITCH_PX) / FIELD_DIVISOR).astype(np.int64)) % n
        gain = field[fy, fx]
        with np.errstate(divide="ignore", invalid="ignore"):
            out = np.where(gain > 0.05, out / gain, out)
        out = np.clip(out, 0, 65535).astype(np.float32)
    return out


def work_mask(dapi_overview):
    """04a's tissue mask, in 04a's working frame, exactly as reformat() forms it."""
    img = Image.open(dapi_overview).convert("L")
    work = np.asarray(img.resize((RF.WORK_SIZE, RF.WORK_SIZE), Image.BILINEAR)).astype(np.float32)
    return RF.tissue_mask(work, False), img.size


def frame_masks(rep, U, V, wmask, artifact=None):
    """(tissue, artifact) on the output grid, both from 04a's own masks.

    The tissue mask is 04a's, so the picture is blanked where the 256 frame is
    and nowhere else; it is interpolated and cut at one half so its edge is not
    a staircase of WORK_SIZE cells. The artifact mask is 04g's, in the overview
    frame, read nearest - a mask is a decision, not a signal to smooth.
    """
    g2o = GEO.grid_to_overview(rep)
    px, py = g2o(U, V)
    ws = float(rep["work_size"])
    wx = (px + 0.5) * ws / rep["src_w"] - 0.5
    wy = (py + 0.5) * ws / rep["src_h"] - 0.5
    tissue = ndimage.map_coordinates(wmask.astype(np.float32), [wy, wx],
                                     order=1, mode="constant", cval=0.0) > 0.5
    art = None
    if artifact is not None:
        ah, aw = artifact.shape
        ax = (px + 0.5) * aw / rep["src_w"] - 0.5
        ay = (py + 0.5) * ah / rep["src_h"] - 0.5
        art = ndimage.map_coordinates(artifact.astype(np.uint8), [ay, ax],
                                      order=0, mode="constant", cval=0) > 0
    return tissue, art


def stretch(values, tissue, art=None):
    """04a's display rule - median - 1 MAD to median + 4 MAD of in-tissue,
    non-artifact pixels - applied to 16-bit values, to uint8.

    The rule is 04a's (`_stretch_to_grid`), so the picture reads the way the
    curator is used to; what changes is that it is applied once, to the
    scanner's values, instead of to an 8-bit image that already went through
    01's display range.
    """
    keep = tissue if art is None else (tissue & ~art)
    inside = values[keep]
    if inside.size > 50:
        med = float(np.median(inside))
        mad = float(np.median(np.abs(inside - med))) * 1.4826
        if mad <= 0:
            mad = max(float(inside.std()), 1.0)
        lo, hi = med - mad, med + 4.0 * mad
    else:
        lo, hi = float(values.min()), float(values.max())
    norm = np.clip((values - lo) / max(hi - lo, 1e-6), 0, 1) * 255.0
    norm[~tissue] = 0
    if art is not None:
        norm[art] = 0
    return np.rint(norm).astype(np.uint8)


def render_from_czi(read_dapi, read_mark, rep, scene_rect, overview_um_px,
                    native_um_px, out_um_px, wmask, artifact=None,
                    dapi_field=None, mark_field=None):
    """(dapi8, mark8) for one section on the canonical frame at `out_um_px`."""
    M = czi_frame(rep, scene_rect, overview_um_px, native_um_px)
    side = render_side(M, native_um_px, out_um_px)
    U, V = output_grid(side)
    X, Y = GEO.apply_affine(M, U, V)
    b = bin_factor(native_um_px, out_um_px)
    dapi = sample_plane(read_dapi, X, Y, scene_rect, b, dapi_field)
    mark = sample_plane(read_mark, X, Y, scene_rect, b, mark_field)
    tissue, art = frame_masks(rep, U, V, wmask, artifact)
    return stretch(dapi, tissue, art), stretch(mark, tissue, art)


def worklist_uid_column(marker, fields):
    """Which `roi_worklist.csv` column carries this marker's scene uid.

    The worklist is keyed on the pass being quantified, and reaches the other
    pass only through the pairing - so one marker reads the row's own uid and
    the other reads the partner's. Which is which was `args.marker == "AF488"`,
    a literal that was true for exactly one study; every marker of any other
    study took the `else` branch and both passes were built from the SAME uids,
    silently.

    `RF.DEFAULT_MARKER` is the comparison, not `MARKERS[1]` spelled out here:
    the partner column belongs to whichever pass owns the unsuffixed outputs,
    and 04a is where that is decided.

    NOT `RF.marker_paths(marker)["uid_col"]`, which the plan for this change
    suggested. That table names 04a's OVERRIDE columns - `perk_scene_uid` for
    the non-default marker, `scene_uid` for the default - and this file's
    columns are `scene_uid` and `partner_scene_uid`. Taking the name from
    there would ask a header that has never had a `perk_scene_uid` column for
    one, which `pick_column` would refuse, and would hand the DEFAULT marker
    `scene_uid`, which is the other pass's section.

    `pick_column`, not a literal and not `.get()`: 04n renamed this column from
    `pcna_scene_uid` to `partner_scene_uid`, so a worklist under either
    spelling has to keep working, and a `.get()` on the wrong one yields an
    empty uid list and a run that builds no composites at all without saying so.
    """
    if marker == RF.DEFAULT_MARKER:
        return IO.pick_column(fields, "partner_scene_uid", "pcna_scene_uid",
                              what=os.path.basename(WORKLIST_CSV))
    return "scene_uid"


def write_thumbs(rgb_dir, size=RF.GRID, force=False):
    """Downscale every composite in `rgb_dir` into `<rgb_dir>_thumb`.

    Reads the composites rather than rebuilding from the overviews: a thumbnail
    that went through its own render could differ from the picture the landmarks
    were placed on, and the whole point is that it cannot.

    LANCZOS because this is a 3:1 reduction of a sparse fluorescent signal -
    nearest or bilinear drop isolated positive nuclei entirely, which is exactly
    the thing the grid is being scanned for.
    """
    out_dir = rgb_dir + "_thumb"
    os.makedirs(out_dir, exist_ok=True)
    made = existing = failed = 0
    for name in sorted(os.listdir(rgb_dir)):
        if not name.endswith(".png"):
            continue
        dst = os.path.join(out_dir, name)
        if os.path.exists(dst) and not force:
            existing += 1
            continue
        try:
            with Image.open(os.path.join(rgb_dir, name)) as im:
                im.convert("RGB").resize((size, size), Image.LANCZOS).save(dst)
            made += 1
        except OSError:
            failed += 1
    print(f"wrote {made} thumbnails ({size}px) to {out_dir}")
    if existing:
        print(f"  {existing} already there, left alone (--force rebuilds them)")
    if failed:
        print(f"  {failed} could not be read")
    return made, existing, failed


class CziUnavailable(Exception):
    """This section cannot be rendered from its CZI; the reason says why."""


def load_artifact_native(uid):
    """04g's mask at its own size - the overview frame - or None."""
    p = os.path.join(OUT_ROOT, "artifacts", uid + "_artifact.png")
    if not os.path.exists(p):
        return None
    return np.asarray(Image.open(p).convert("L")) > 0


def refresh_thumb(rgb_dir, uid, size):
    """Re-shrink one composite's thumbnail, if that marker has thumbnails.

    A thumbnail is a downscale of the composite (see write_thumbs), so one
    left from the old composite would be a picture of a different render.
    """
    tdir = rgb_dir + "_thumb"
    if not os.path.isdir(tdir):
        return
    with Image.open(os.path.join(rgb_dir, uid + ".png")) as im:
        im.convert("RGB").resize((size, size), Image.LANCZOS).save(
            os.path.join(tdir, uid + ".png"))


class CziSource:
    """Everything a CZI render needs that is per study, plus the open file."""

    def __init__(self, marker):
        try:
            from pylibCZIrw import czi as pyczi     # noqa: F401
        except ImportError:
            raise SystemExit(
                "rendering from the CZI needs pylibCZIrw, which this Python does "
                "not have - run this stage with work/czienv (the interpreter 01 "
                "runs on), or pass --source overview for the old 8-bit route.")
        self._pyczi = pyczi
        self.n_idx, self.m_idx = plane_indices(marker)
        with open(MANIFEST_CSV, newline="", encoding="utf-8") as fh:
            self.man = {r["scene_uid"]: r for r in csv.DictReader(fh)}
        self.fields = {}
        for idx in {self.n_idx, self.m_idx}:
            p = os.path.join(FIELD_DIR, f"tilefield_c{idx}.npy")
            if os.path.exists(p):
                self.fields[idx] = np.load(p)
        if not self.fields:
            print("  no tile fields found - rendering UNCORRECTED, as 01 would")
        self.native = float(GEO.BASE_PX_UM)
        self._path = None
        self._doc = None
        self._rects = None
        self.rows = []

    def file_of(self, uid):
        m = self.man.get(uid)
        return m["file"] if m else None

    def _open(self, fname):
        if fname == self._path:
            return
        self._close_doc()
        path = os.path.join(CONFIG["source_dir"], fname)
        if not os.path.exists(path):
            raise CziUnavailable(f"CZI not found: {path}")
        self._doc = self._pyczi.open_czi(path)
        self._doc.__enter__()
        self._rects = {int(k): (int(r.x), int(r.y), int(r.w), int(r.h))
                       for k, r in self._doc.scenes_bounding_rectangle.items()}
        self._path = fname

    def _close_doc(self):
        if self._doc is not None:
            self._doc.__exit__(None, None, None)
        self._doc = self._path = self._rects = None

    def render(self, uid, focus_row, dapi_png, extra, flip, art, cen, art_native, um_px):
        m = self.man.get(uid)
        if m is None:
            raise CziUnavailable("not in manifest_scenes.csv")
        self._open(m["file"])
        si = int(m["scene_index"])
        if si not in self._rects:
            raise CziUnavailable(f"scene {si} absent from {m['file']}")
        rect = self._rects[si]

        # The geometry, decided exactly as 04a and 05a decide it - S = 1.
        rep = {}
        if RF.reformat(dapi_png, light_background=False, extra_angle=extra, flip=flip,
                       artifact=art, censor=cen, report=rep) is None:
            return None
        ov_um = float(focus_row["um_px"])
        # 05a's own consistency check: the overview must be this scene
        # rectangle at the recorded scale, or the frame would be offset.
        scale = ov_um / self.native
        exp_w, exp_h = round(rect[2] / scale), round(rect[3] / scale)
        if abs(exp_w - rep["src_w"]) > 2 or abs(exp_h - rep["src_h"]) > 2:
            raise CziUnavailable(
                f"overview {rep['src_w']}x{rep['src_h']} does not match scene "
                f"rect {exp_w}x{exp_h} at {ov_um} um/px")
        wmask, _ = work_mask(dapi_png)
        if wmask is None:
            return None

        doc = self._doc

        def reader(idx):
            return lambda x, y, w, h: CR.read_plane(doc, GEO._Rect(x, y, w, h), idx, scene=si)

        return render_from_czi(reader(self.n_idx), reader(self.m_idx), rep, rect,
                               ov_um, self.native, um_px, wmask, art_native,
                               self.fields.get(self.n_idx), self.fields.get(self.m_idx))

    def log(self, uid, side, um_px):
        self.rows.append({"scene_uid": uid, "source": "czi", "um_per_px": um_px,
                          "side_px": side, "czi_file": self.file_of(uid),
                          "reader": "pylibCZIrw"})

    def close(self, out_dir):
        """Append this run's renders to `<out_dir>_render.csv`: which picture is which."""
        self._close_doc()
        if not self.rows:
            return
        path = out_dir + "_render.csv"
        new = not os.path.exists(path)
        with open(path, "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(self.rows[0]))
            if new:
                w.writeheader()
            w.writerows(self.rows)


def main():
    ap = argparse.ArgumentParser()
    _default = MARKERS[0] if MARKERS else None
    ap.add_argument("--marker", choices=MARKERS, default=_default,
                    help=f"which marker to process (default {_default})")
    ap.add_argument("--tier", default=None, help="only this worklist tier")
    ap.add_argument("--scale", type=int, default=3, metavar="N",
                    help="render at N x the canonical 256 grid (default 3 = 768). "
                         "Sampling only - the mask, angle and frame are decided at "
                         "WORK_SIZE and do not change, so a landmark still means the "
                         "same thing once divided by N")
    ap.add_argument("--all", action="store_true",
                    help="build every section in this marker's own reformat index "
                         "instead of the pERK worklist. The worklist is keyed on "
                         "pERK and reaches PCNA only through the pairing, so it "
                         "cannot define the PCNA job - this can")
    ap.add_argument("--include-excluded", action="store_true",
                    help="build every SCANNED section for this marker, not just the "
                         "ones that survived. The excluded ones already have a "
                         "greyscale image from 04a --render-excluded and are shown "
                         "in Review mode; without this they are the only grey cells "
                         "in a colour grid. Adds no index row, so nothing downstream "
                         "picks them up")
    ap.add_argument("--force", action="store_true",
                    help="rebuild composites that already exist (default is to skip "
                         "them, so topping a set up costs only the missing ones)")
    ap.add_argument("--source", choices=["czi", "overview"], default=RENDER_SOURCE,
                    help="where the pixels come from (default from config "
                         f"curation_render.source: {RENDER_SOURCE}). czi renders "
                         "from the scan's own 16-bit planes in one resampling; "
                         "overview is the old 8-bit 5.2 um/px route")
    ap.add_argument("--um-px", type=float, default=RENDER_UM_PX, metavar="UM",
                    help="resolution of a czi render (default from config "
                         f"curation_render.um_per_px: {RENDER_UM_PX}); 0.65 is native")
    ap.add_argument("--verify", action="store_true",
                    help="check the blue plane still equals the existing greyscale section")
    ap.add_argument("--thumbs", action="store_true",
                    help="downscale the composites already built for this marker to "
                         f"{RF.GRID}px for the review grid, and do nothing else")
    ap.add_argument("--thumb-size", type=int, default=RF.GRID, metavar="N",
                    help=f"thumbnail edge in pixels (default {RF.GRID})")
    args = ap.parse_args()

    paths = RF.marker_paths(args.marker)
    src_dir = paths["sections"]
    out_dir = src_dir + "_rgb"
    index_path = paths["index"]

    # Thumbnails are a resize of what is already on disk, so they need none of
    # the overrides, focus table or worklist below.
    if args.thumbs:
        if not os.path.isdir(out_dir):
            print(f"no composites to shrink: {out_dir} does not exist")
            return
        write_thumbs(out_dir, args.thumb_size, args.force)
        return

    overrides, _ = RF.load_overrides(paths)
    os.makedirs(out_dir, exist_ok=True)

    with open(FOCUS_CSV, newline="", encoding="utf-8") as fh:
        secs = {r["scene_uid"]: r for r in csv.DictReader(fh)
                if r["marker_channel"] == args.marker}

    if args.include_excluded:
        # focus.csv is the whole scanned set for this marker - the same list 04p
        # walks - so this reaches the sections the index deliberately omits.
        uids = sorted(secs)
    elif args.all:
        with open(paths["index"], newline="", encoding="utf-8") as fh:
            uids = [r["id"] for r in csv.DictReader(fh) if r["kind"] == "section"]
    else:
        with open(WORKLIST_CSV, newline="", encoding="utf-8") as fh:
            rd = csv.DictReader(fh)
            fields = list(rd.fieldnames or [])
            want = [r for r in rd if not args.tier or r["tier"] == args.tier]
        # The worklist is keyed on the measured pass. Building the other side
        # from it means following the pairing to the partner scene, which the
        # 30 unpaired sections do not have - hence --all, which reads the
        # marker's own index. Which column that is, see worklist_uid_column.
        uid_col = worklist_uid_column(args.marker, fields)
        uids = [u for u in ((w.get(uid_col) or "").strip() for w in want) if u]

    # The index is the definition of "survived", so it is also the definition of
    # "was masked on the way in" - 04a writes an index row for exactly the
    # sections it reformatted under the analysis flags.
    with open(index_path, newline="", encoding="utf-8") as fh:
        in_index = {r["id"] for r in csv.DictReader(fh) if r["kind"] == "section"}

    colour = MARKER_COLOURS.get(args.marker)
    if colour is None:
        # Measured, but this study never said what colour to draw it in. Not a
        # loop body - this stage builds one marker per run - so it stops here
        # and says which markers DO have a colour. Picking one for it would be
        # a marker silently wearing another marker's colour, which is the
        # fault `display.colours` exists to remove; drawing it black would be
        # a picture of nothing that looks like a failed render.
        shown = ", ".join(sorted(MARKER_COLOURS))
        print(f"{args.marker} is not composited: this study gives a colour to "
              f"{shown or 'no marker'}, and the first two markers shown get "
              f"red and green by default.")
        print(f"  Give it one: `display.colours: {{\"{args.marker}\": "
              f"\"magenta\"}}` in config.json.")
        return
    made = skipped = mismatched = existing = 0
    missing = []

    # --verify is a statement about the 256 greyscale, which only the overview
    # route reproduces; it never renders from the CZI.
    czi = None
    if args.source == "czi" and not args.verify:
        if args.um_px <= 0:
            raise SystemExit("--um-px must be greater than zero")
        czi = CziSource(args.marker)
        # One file open per CZI rather than one per section.
        uids = sorted(uids, key=lambda u: czi.file_of(u) or "")
        print(f"rendering from the CZI at {args.um_px} um/px "
              f"(native {GEO.BASE_PX_UM}); --source overview for the old route")

    for uid in uids:
        out_path = os.path.join(out_dir, uid + ".png")
        if not args.verify and not args.force and os.path.exists(out_path):
            existing += 1
            continue
        r = secs.get(uid)
        if r is None:
            missing.append((uid, "not in focus.csv"))
            continue
        base = os.path.join(OUT_ROOT, "overviews", r["animal"], args.marker, uid)
        dapi, mark = base + "_DAPI.png", base + "_MARK.png"
        if not os.path.exists(dapi) or not os.path.exists(mark):
            missing.append((uid, "overview PNG missing"))
            continue

        extra, flip = overrides.get(uid, (0.0, False))
        # Masked exactly as this section's own greyscale was - see the module
        # docstring. Not a preference: the blue plane has to equal the picture
        # already on disk, and for an excluded section that picture is raw.
        excluded_here = uid not in in_index
        art = None if excluded_here else RF.load_artifact(uid, (RF.WORK_SIZE, RF.WORK_SIZE))
        cen = None if excluded_here else RF.load_censor(uid, (RF.WORK_SIZE, RF.WORK_SIZE))
        if czi is not None:
            try:
                got = czi.render(uid, r, dapi, extra, flip, art, cen,
                                 None if excluded_here else load_artifact_native(uid),
                                 args.um_px)
            except CziUnavailable as exc:
                missing.append((uid, str(exc)))
                continue
            if got is None:
                missing.append((uid, "no tissue mask could be formed"))
                continue
            img, comp = got
            Image.fromarray(composite(comp, img, colour, NUCLEAR_COLOUR)).save(out_path)
            refresh_thumb(out_dir, uid, args.thumb_size)
            czi.log(uid, img.shape[0], args.um_px)
            made += 1
            continue

        out = RF.reformat(dapi, light_background=False, extra_angle=extra, flip=flip,
                          artifact=art, censor=cen, companion=mark,
                          render_scale=1 if args.verify else args.scale)
        if out is None:
            missing.append((uid, "no tissue mask could be formed"))
            continue
        img, _mask, _angle, _a, _c, comp = out

        # The blue plane must be the image the curator was already showing, or a
        # landmark placed on this picture does not mean what the exports say.
        if args.verify:
            prev = os.path.join(src_dir, uid + ".png")
            if os.path.exists(prev):
                if np.array_equal(img, np.array(Image.open(prev))):
                    skipped += 1
                else:
                    mismatched += 1
                    print(f"  MISMATCH {uid}")
                continue

        Image.fromarray(composite(comp, img, colour, NUCLEAR_COLOUR)).save(out_path)
        made += 1

    if czi is not None:
        czi.close(out_dir)
    if args.verify:
        print(f"verified {skipped} sections, {mismatched} mismatched")
    else:
        print(f"wrote {made} composites to {out_dir}")
        if existing:
            print(f"  {existing} already there, left alone (--force rebuilds them)")
    if missing:
        print(f"  {len(missing)} not built:")
        for uid, why in missing[:10]:
            print(f"    {uid}  {why}")


if __name__ == "__main__":
    main()
