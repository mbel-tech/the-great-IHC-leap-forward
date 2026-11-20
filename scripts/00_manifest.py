"""Stage 0 - manifest, slide-grid reconstruction, and slide-map figures.

Reads every CZI header (loose files and STORED zip entries alike) without
extracting or decoding a single pixel, then:

  1. manifest/manifest_files.csv   one row per CZI
  2. manifest/manifest_scenes.csv  one row per brain section, with the
                                   reconstructed slide grid and the serial
                                   number under each candidate convention
  3. qc/slidemaps/<file>.png       one panel per candidate mounting
                                   convention, for the user to confirm once
  4. qc/slide_layout_summary.csv   grid shape per file, so odd slides stand out
  5. qc/slide_layout_review.csv    slides whose row count needs a human

Nothing downstream may assume a mounting convention until the user picks one.

Run:  python 00_manifest.py
      python 00_manifest.py --accept-suggestions   # write suggested row counts
"""

import contextlib
import csv
import glob
import json
import os
import re
import sys
import zipfile
from collections import defaultdict

import numpy as np
from PIL import Image, ImageDraw, ImageFont

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from czi_meta import read_metadata, summarise  # noqa: E402

# ---------------------------------------------------------------- configuration

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
# ls_config resolves LS_CONFIG, applies the defaults and validates once for
# the whole process. Imported, not re-implemented: this block used to be four
# lines copy-pasted into every stage.
from ls_config import CONFIG, CONFIG_PATH  # noqa: E402
import ls_channels as CH  # noqa: E402
import ls_io as IO  # noqa: E402
# source_files matching, shared with 00b so the two cannot drift.
import ls_sources as SRC  # noqa: E402
import ls_naming as NM  # noqa: E402
import ls_progress as PG  # noqa: E402
import ls_reagent_controls as RC  # noqa: E402

SOURCE_DIR = CONFIG["source_dir"]
OUT_ROOT = CONFIG["out_root"]
# Confirmed mounting convention; section_order is written from this column.
ORDER_CONVENTION = CONFIG["section_order_convention"]

MANIFEST_DIR = os.path.join(OUT_ROOT, "manifest")
SLIDEMAP_DIR = os.path.join(OUT_ROOT, "qc", "slidemaps")
QC_DIR = os.path.join(OUT_ROOT, "qc")

# Filenames look like LS45_8b.czi, LS85_7b-.czi, LS53_2b-_A568.czi
_PATTERN = None


def name_pattern():
    """The study's filename grammar, compiled once.

    Lazy rather than module-level so a study that has not set one yet still
    lets the app load its stage list; the failure belongs to the stage that
    reads a filename, not to importing it.
    """
    global _PATTERN
    if _PATTERN is None:
        _PATTERN = NM.compile_pattern(CONFIG.get("slide_naming"))
    return _PATTERN

# A gap larger than this fraction of the median scene dimension starts a new
# row/column. Scenes on a slide are separated by far more than their own size,
# so this is not a sensitive parameter.
GAP_FRACTION = 0.6

CONVENTIONS = [
    ("S_scan_order", "Scan order: scene index as acquired  <- leading candidate"),
    ("A_rows_LR", "Rows: left -> right, top row first"),
    ("B_cols_TB", "Columns: top -> bottom, left column first"),
    ("C_rows_serpentine", "Serpentine rows: L->R, then R->L"),
    ("D_cols_serpentine", "Serpentine columns: T->B, then B->T"),
]

PANEL_W = 560
MARGIN = 34
PANEL_COLS = 2
TITLE_H = 30

# Cycled per detected row so grouping errors are visible in the figure.
ROW_COLOURS = [
    "#e8f0fb", "#fdeee6", "#eaf6ec", "#f6ecf7",
    "#fdf6e0", "#e6f6f8", "#f9e9ec", "#eeeef8",
]

# Slides whose automatic row count is wrong. Columns: file, n_rows
OVERRIDES_CSV = os.path.join(MANIFEST_DIR, "layout_overrides.csv")

# A within-row Y spread this large relative to section height usually means two
# rows were merged into one.
ROW_SPREAD_FLAG = 0.8


# ---------------------------------------------------------------- source discovery


def discover_sources(stack, skipped=None):
    """Yield (source, member, opener) for every CZI, loose files first.

    `source` is either "LOOSE" or the zip filename; `opener` returns a fresh
    binary file object positioned at 0. `stack` is a contextlib.ExitStack that
    owns every archive opened here; the openers are only valid while it is live.

    An optional `source_files` list in config narrows this to named files. That
    is how "process these slides, not the whole folder" is expressed: the files
    stay where they are and the rest of the folder is ignored. Copying or linking
    a subset into its own directory would say the same thing at the cost of
    moving hundreds of gigabytes, and the slide drive here is exFAT, which
    supports neither hardlinks nor symlinks.

    A name in `source_files` is a loose CZI, a whole zip, or a CZI inside a zip.
    It used to be matched against the zip's own name only, so naming a member
    skipped its whole archive and the slide simply never appeared, uncounted.

    `skipped`, a list, receives (source, member, reason) for everything that was
    meant to be read and could not be: an archive that will not open, and a
    `source_files` name found nowhere. main() fails the run on any of them;
    printing "cannot open" and carrying on made a missing slide look like a
    complete study.
    """
    skipped = skipped if skipped is not None else []
    only = SRC.named(CONFIG.get("source_files"))
    found = set()
    unreadable = []

    entries = []

    for path in sorted(glob.glob(os.path.join(SOURCE_DIR, "*.czi"))):
        name = os.path.basename(path)
        if not SRC.takes_loose(name, only):
            continue
        found.add(name)
        entries.append(("LOOSE", name, path, (lambda p: (lambda: open(p, "rb")))(path)))

    for zpath in sorted(glob.glob(os.path.join(SOURCE_DIR, "*.zip"))):
        zname = os.path.basename(zpath)
        whole = SRC.takes_zip(zname, only)
        try:
            zf = stack.enter_context(zipfile.ZipFile(zpath))
        except (zipfile.BadZipFile, OSError) as exc:
            # Counted only where it was asked for. With a source_files subset
            # an unreadable archive nobody named is not this run's business -
            # unless a named CZI turns out to be missing, which says so below.
            if whole:
                skipped.append((zname, "", f"cannot open the archive: {exc}"))
            else:
                unreadable.append(zname)
            continue
        if whole and only:
            found.add(zname)
        for info in zf.infolist():
            if not SRC.takes_member(info.filename, whole, only):
                continue
            found.add(os.path.basename(info.filename))
            entries.append(
                (
                    zname,
                    info.filename,
                    zpath,
                    (lambda z, n: (lambda: z.open(n)))(zf, info.filename),
                )
            )

    for name, why in SRC.unfound(only, found, unreadable, SOURCE_DIR):
        skipped.append(("source_files", name, why))
    return entries


def parse_name(member):
    """Read one filename with the study's grammar, or None.

    `animal` and `variant` are kept as aliases of ls_naming's `subject` and
    `replicate`: those two are column names in every table on disk and in the
    R scripts, so renaming them here would be a data migration, not a tidy-up.
    """
    parsed = NM.parse_name(member, name_pattern())
    if parsed is None:
        parsed = _control_parts(member)
    if parsed is None:
        return None
    return {**parsed,
            "animal": parsed["subject"],
            "variant": parsed["replicate"]}


def _control_parts(member):
    """The parts a reagent control file's uid needs, from its `files` entry.

    Control slides are often named with no subject (`NC_3.czi`), so the
    study's grammar does not read them, and a name that does not parse is a
    failure of the whole run. The operator's confirmed entry gives the subject
    (the donor animal, or a made-up ID), the slide and the replicate, and the
    uid is built from them in the standard shape - the flag never goes into
    the uid. Held to the same rules as a parsed name: a subject with no `_`,
    and a whole-number slide, so two controls of one animal cannot collide.
    None (still a failure) when the entry does not supply them.
    """
    sub = RC.control_subject(CONFIG, re.split(r"[\\/]", member)[-1])
    if sub is None:
        return None
    subject = str(sub["subject"]).strip()
    slide = sub["slide"]
    if isinstance(slide, str) and slide.strip().isdigit():
        slide = int(slide.strip())
    if not subject or "_" in subject or isinstance(slide, bool) or not isinstance(slide, int):
        return None
    return {"subject": subject, "slide": slide,
            "replicate": str(sub["replicate"] or ""),
            "name_suffix": "", "extras": {}}


def flag_reagent_controls(file_rows, scene_rows):
    """Write `reagent_control` (the kind, or "") on every scene and file row.

    For display only: what is excluded downstream is decided from config by
    ls_reagent_controls.expand, never from this column. A file row carries the
    kind when any of its scenes is a reagent control.
    """
    lookup = RC.expand(CONFIG, scene_rows)
    by_file = {}
    for row in scene_rows:
        entry = lookup.get((row["scene_uid"], row["marker_channel"]))
        row["reagent_control"] = (entry.get("kind") or "other") if entry else ""
        if row["reagent_control"]:
            by_file.setdefault(row["file"], row["reagent_control"])
    for row in file_rows:
        row["reagent_control"] = by_file.get(row["file"], "")
    return len({(r["scene_uid"], r["marker_channel"]) for r in scene_rows
                if r["reagent_control"]})


def reagent_control_warnings(entries):
    """Lines for every name that looks like a reagent control and was neither
    confirmed nor rejected. A warning, never a failure: with `declared: no`
    the operator still hears about `LS45_noPrimary.czi`. Reads only the group
    labels (so a weak token that is a group name is not proposed), never which
    animal is in which group."""
    paths = [member if source == "LOOSE" else f"{source}/{member}"
             for source, member, _container, _opener in entries]
    labels = (CONFIG.get("groups") or {}).get("order") or []
    matches = RC.scan(paths, CH.marker_names(CONFIG), labels,
                      RC.entries(CONFIG)["extra_patterns"])
    lines = []
    for m in RC.unanswered(CONFIG, matches):
        lines.append(f"  ?? {m.path} looks like a reagent control ({m.kind}, "
                     f"'{m.token}') but is neither confirmed nor rejected - "
                     f"answer it in Set up > Slides > Reagent controls.")
    return lines


# ---------------------------------------------------------------- slide grid


def adaptive_threshold(values, fallback):
    """Find the natural break between within-column and between-column gaps.

    Scene size is a poor scale for this: some slides have scan regions drawn
    tightly around small sections, others generously around large ones, and
    sections are often staggered. The spacing between scenes is far more
    reliable, so the threshold is read off the gap distribution itself.
    """
    gaps = np.diff(np.sort(np.asarray(values, dtype=float)))
    gaps = gaps[gaps > 0]
    if len(gaps) < 2:
        return fallback

    ordered = np.sort(gaps)
    k = int(np.argmax(np.diff(ordered)))
    lo, hi = ordered[k], ordered[k + 1]
    if hi < 2.0 * lo:
        # No natural break in the gap distribution. That is ambiguous between
        # "all one group" and "all separate groups", so defer to the physical
        # scale: rows cannot sit closer together than a fraction of a section.
        return fallback
    return (lo + hi) / 2.0


def group_by_threshold(values, threshold):
    """Split sorted-adjacent values wherever the gap exceeds threshold.

    Returned group ids are ranked by position, so 0 is leftmost/topmost.
    """
    values = np.asarray(values, dtype=float)
    order = np.argsort(values)
    groups = np.empty(len(values), dtype=int)
    current = 0
    groups[order[0]] = 0
    for i in range(1, len(order)):
        if values[order[i]] - values[order[i - 1]] > threshold:
            current += 1
        groups[order[i]] = current
    return groups


def split_at_largest_gaps(values, n_groups):
    """Force exactly n_groups by cutting at the (n_groups - 1) largest gaps."""
    values = np.asarray(values, dtype=float)
    order = np.argsort(values)
    ordered = values[order]
    gaps = np.diff(ordered)
    cuts = set(np.argsort(gaps)[-(n_groups - 1):].tolist()) if n_groups > 1 else set()

    groups = np.empty(len(values), dtype=int)
    current = 0
    for position, i in enumerate(order):
        if position > 0 and (position - 1) in cuts:
            current += 1
        groups[i] = current
    return groups


def build_grid(scenes, forced_rows=None):
    """Assign a (row, col) to each scene from its stage centre coordinates.

    Rows first. Sections are laid down in rows along the slide, and rows are
    frequently staggered in X (LS105_8a's two rows are offset by ~3 mm), which
    a column-first model cannot represent at all. Columns are then just the
    rank across each row, which tolerates any amount of stagger.

    `forced_rows` comes from manifest/layout_overrides.csv for the minority of
    slides where the automatic row count is wrong.
    """
    xs = np.array([s["center_x_um"] for s in scenes], dtype=float)
    ys = np.array([s["center_y_um"] for s in scenes], dtype=float)
    hs = np.array([s["height_um"] or 0.0 for s in scenes], dtype=float)

    if forced_rows:
        rows = split_at_largest_gaps(ys, forced_rows)
    else:
        fallback = GAP_FRACTION * (np.median(hs) if np.median(hs) > 0 else 1000.0)
        rows = group_by_threshold(ys, adaptive_threshold(ys, fallback))

    cols = np.zeros(len(scenes), dtype=int)
    for r in np.unique(rows):
        members = np.where(rows == r)[0]
        for rank, i in enumerate(members[np.argsort(xs[members])]):
            cols[i] = rank
    return rows, cols


def suggest_rows(scenes, max_rows=6):
    """Fewest rows that keep every row's Y spread physically plausible.

    Splitting more finely always reduces spread, so the smallest workable row
    count is the honest answer rather than the best-scoring one.
    """
    ys = np.array([s["center_y_um"] for s in scenes], dtype=float)
    for n in range(1, min(max_rows, len(scenes)) + 1):
        candidate = split_at_largest_gaps(ys, n)
        if row_spread_ratio(scenes, candidate) <= ROW_SPREAD_FLAG:
            return n
    return None


def row_spread_ratio(scenes, rows):
    """Widest within-row Y spread as a fraction of section height.

    Two rows merged into one shows up here as a ratio near or above 1, which is
    the failure mode worth flagging for manual review.
    """
    ys = np.array([s["center_y_um"] for s in scenes], dtype=float)
    hs = np.array([s["height_um"] or 0.0 for s in scenes], dtype=float)
    median_h = np.median(hs) if np.median(hs) > 0 else 1.0
    spreads = [
        ys[rows == r].max() - ys[rows == r].min()
        for r in np.unique(rows)
        if (rows == r).sum() > 1
    ]
    return (max(spreads) / median_h) if spreads else 0.0


def serial_numbers(rows, cols, scenes):
    """Serial number per scene under each candidate mounting convention.

    S_scan_order is the order the scan regions were acquired in. Operators draw
    those regions systematically, so on slides where no clean grid exists it is
    usually the only ordering that reflects how the sections were actually laid
    down - the geometry alone cannot recover that.
    """
    n_rows, n_cols = int(rows.max()) + 1, int(cols.max()) + 1
    idx = np.arange(len(rows))
    out = {}

    scan_rank = {s["index"]: r for r, s in enumerate(sorted(scenes, key=lambda s: s["index"]))}
    out["S_scan_order"] = np.array([scan_rank[s["index"]] + 1 for s in scenes])

    def assign(keyfunc):
        order = sorted(idx, key=keyfunc)
        serial = np.empty(len(idx), dtype=int)
        for position, i in enumerate(order):
            serial[i] = position + 1
        return serial

    out["A_rows_LR"] = assign(lambda i: (rows[i], cols[i]))
    out["B_cols_TB"] = assign(lambda i: (cols[i], rows[i]))
    out["C_rows_serpentine"] = assign(
        lambda i: (rows[i], cols[i] if rows[i] % 2 == 0 else n_cols - 1 - cols[i])
    )
    out["D_cols_serpentine"] = assign(
        lambda i: (cols[i], rows[i] if cols[i] % 2 == 0 else n_rows - 1 - rows[i])
    )
    return out


# ---------------------------------------------------------------- section order


CHOSEN_VARIANTS_CSV = os.path.join(MANIFEST_DIR, "chosen_variants.csv")
#: The columns 00 and 02 both read from it.
CHOSEN_VARIANT_COLS = ("animal", "slide", "marker_channel", "variant")


def _slide_key(value):
    """A slide number as one spelling: 8, "8" and "08" are the same slide."""
    text = str(value if value is not None else "").strip()
    return str(int(text)) if text.isdigit() else text


def chosen_variant_overrides():
    """(animal, slide, marker) -> replicate letter, from chosen_variants.csv.

    The person-written file 02_pair_passes.choose_variants reads. Read here
    too so the scan a person picked for a re-scanned slide is the one whose
    sections the next slide's numbers follow on from.
    """
    if not os.path.exists(CHOSEN_VARIANTS_CSV):
        return {}
    with open(CHOSEN_VARIANTS_CSV, newline="", encoding="utf-8") as fh:
        rd = csv.DictReader(fh)
        # A person writes this file: a missing column is a refusal naming the
        # file and the column, not a bare KeyError from inside the loop.
        for col in CHOSEN_VARIANT_COLS:
            IO.pick_column(rd.fieldnames, col, what=CHOSEN_VARIANTS_CSV)
        return {(r["animal"], _slide_key(r["slide"]), r["marker_channel"]): r["variant"]
                for r in rd}


#: Appended to the animal for a reagent control's series. Internal only: it is
#: never written to a table and never part of the uid.
RC_SERIES = "#rc"


def _series_key(row):
    """(animal, marker), or (animal + "#rc", marker) for a reagent control.

    A control gets a series of its own so it cannot take places in its donor
    animal's rostro-caudal order, nor become the scan that sizes a slide's
    block. Read off the row's `reagent_control`, which flag_reagent_controls
    writes from config just before this runs; a row without it is stained.
    """
    animal = row["animal"]
    if row.get("reagent_control"):
        animal += RC_SERIES
    return (animal, row["marker_channel"])


def assign_section_order(scene_rows, order_key):
    """Write `section_order` (and `slide_serial`) on every scene row.

    Rostro-caudal position across the whole animal: slide number first, then
    position within the slide under the confirmed convention (`order_key`).

    One series per (animal, marker), NOT per replicate letter (a reagent
    control has a series of its own, (animal + "#rc", marker): see
    _series_key). The letter is
    which scan of a slide this is, and a slide scanned again (`8c` beside
    `1b..9b`) is the same physical sections as its first scan. Numbering each
    letter as its own series gave `8c` 1..k, colliding with slide 1, and 01d
    (sorted by section_order then slide) and 04b (section_order alone)
    interleaved the two slides.

    So each slide owns a block of places, and every scan of it is numbered
    inside that block: place = sections on the earlier slides + serial within
    the slide. Which scan is used stays Stage 2's decision
    (02_pair_passes.choose_variants), and each slide's block is as long as the
    scan that decision picks - the chosen_variants.csv override, else the scan
    with most scenes, then the earlier letter - so the chosen scans run 1..N
    without a gap or a repeat whatever letter each slide's chosen scan has. A
    scan not chosen shares numbers with the chosen one (same sections); if it
    has more scenes than the chosen scan, its extra ones run past the block.

    Returns (series, re-scanned slides).
    """
    overrides = chosen_variant_overrides()
    series = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for row in scene_rows:
        series[_series_key(row)][row["slide"]][row["variant"]].append(row)

    rescans = 0
    for (key, marker), slides in series.items():
        animal = key[:-len(RC_SERIES)] if key.endswith(RC_SERIES) else key
        offset = 0
        for slide in sorted(slides, key=lambda s: (s is None, s if s is not None else 0)):
            scans = slides[slide]
            pick = overrides.get((animal, _slide_key(slide), marker))
            if pick not in scans:
                pick = sorted(scans, key=lambda v: (-len(scans[v]), v))[0]
            if len(scans) > 1:
                rescans += 1
            for rows in scans.values():
                rows.sort(key=lambda r: r[order_key])
                for position, row in enumerate(rows, start=1):
                    row["slide_serial"] = row[order_key]
                    row["section_order"] = offset + position
            offset += len(scans[pick])
    return len(series), rescans


# ---------------------------------------------------------------- slide-map figure


def _font(size):
    for candidate in (r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\arial.ttf"):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


FONT_TITLE = _font(15)
FONT_LABEL = _font(11)
FONT_SERIAL = _font(20)


def render_slidemap(path, member, scenes, rows, cols, serials):
    """One panel per candidate convention, sized to the slide's own aspect."""
    xs = np.array([s["center_x_um"] for s in scenes])
    ys = np.array([s["center_y_um"] for s in scenes])
    ws = np.array([s["width_um"] or 0.0 for s in scenes])
    hs = np.array([s["height_um"] or 0.0 for s in scenes])

    x0, x1 = (xs - ws / 2).min(), (xs + ws / 2).max()
    y0, y1 = (ys - hs / 2).min(), (ys + hs / 2).max()
    span_x = max(x1 - x0, 1.0)
    span_y = max(y1 - y0, 1.0)

    # Scale to panel width and let height follow, so wide slides do not leave
    # two thirds of every panel empty.
    scale = (PANEL_W - 2 * MARGIN) / span_x
    panel_h = max(int(MARGIN + 18 + span_y * scale + MARGIN), 140)

    n_panel_rows = -(-len(CONVENTIONS) // PANEL_COLS)
    canvas = Image.new(
        "RGB", (PANEL_W * PANEL_COLS, panel_h * n_panel_rows + TITLE_H), "white"
    )
    draw = ImageDraw.Draw(canvas)
    draw.text(
        (12, 8),
        f"{member}   -   {len(scenes)} scenes   -   detected {rows.max() + 1} row(s) x "
        f"{cols.max() + 1} col(s)   -   stage X right, stage Y down   -   fill colour = detected row",
        fill="black",
        font=FONT_TITLE,
    )

    for panel, (key, description) in enumerate(CONVENTIONS):
        ox = (panel % PANEL_COLS) * PANEL_W
        oy = (panel // PANEL_COLS) * panel_h + TITLE_H
        draw.rectangle([ox + 2, oy + 2, ox + PANEL_W - 3, oy + panel_h - 3], outline="#c8c8c8")
        draw.text(
            (ox + MARGIN, oy + 8),
            f"{key}   {description}",
            fill="#a03000" if key == "S_scan_order" else "#0050a0",
            font=FONT_LABEL,
        )

        for i, sc in enumerate(scenes):
            left = ox + MARGIN + (xs[i] - ws[i] / 2 - x0) * scale
            top = oy + MARGIN + 16 + (ys[i] - hs[i] / 2 - y0) * scale
            right = left + max(ws[i] * scale, 6)
            bottom = top + max(hs[i] * scale, 6)
            # Fill encodes the detected row, so a mis-grouped slide is obvious
            # at a glance rather than hidden inside the numbering.
            draw.rectangle(
                [left, top, right, bottom],
                outline="#3c3c3c",
                fill=ROW_COLOURS[int(rows[i]) % len(ROW_COLOURS)],
            )
            draw.text(
                ((left + right) / 2 - 9, (top + bottom) / 2 - 12),
                str(serials[key][i]),
                fill="#c00000",
                font=FONT_SERIAL,
            )
            draw.text(
                (left + 3, top + 2),
                f"s{sc['index']}",
                fill="#707070",
                font=FONT_LABEL,
            )

    # Trailing empty slot carries the reading instructions.
    if len(CONVENTIONS) % PANEL_COLS:
        ox = (len(CONVENTIONS) % PANEL_COLS) * PANEL_W
        oy = (len(CONVENTIONS) // PANEL_COLS) * panel_h + TITLE_H
        for line_no, line in enumerate(
            [
                "Grey s0, s1, ... = scene index inside the CZI.",
                "Red number = serial position under that convention.",
                "",
                "Pick the panel whose red numbers run rostral -> caudal",
                "in the order the sections were mounted.",
            ]
        ):
            draw.text((ox + MARGIN, oy + 30 + line_no * 17), line, fill="#505050", font=FONT_LABEL)

    canvas.save(path)


# ---------------------------------------------------------------- refusals

# How far a file's header scale may sit from pixel_size_um. The header stores
# the scale in metres to many digits, so honest files agree to far better than
# this; another objective (0.325 at 20x against 0.65 at 10x) is off by 50%.
PX_TOLERANCE = 0.005


def pixel_size_mismatches(file_rows, expected):
    """[(file, px_um)] for every file not at the configured scale.

    Every stage after this one measures at CONFIG["pixel_size_um"] and never
    reads the scale from the file again, and the app's autodetect samples only
    some files, so this is the one place a slide scanned with another
    objective can be caught. A file with no scale in its header is listed too:
    it cannot be shown to agree, and would be measured as if it did.
    """
    return [(r["file"], r["px_um"]) for r in file_rows
            if not r["is_redundant_copy"]
            and (not r["px_um"]
                 or abs(r["px_um"] - expected) > PX_TOLERANCE * expected)]


def duplicate_uids(scene_rows):
    """{scene_uid: [file, ...]} for every uid more than one file produced.

    The uid leaves out the name suffix, so LS45_8b.czi and LS45_8b_A488.czi
    both give LS45_s08b_scNN. Checked across ALL files, not per marker pass:
    in the live paired LS study the two passes of a slide already differ by
    replicate letter (LS53_2b-_A568 is AF568, LS53_2c is AF488), so a repeat
    there is never legitimate - and 01k writes clip masks to one flat folder
    keyed by uid alone (01/04j read them back the same way), and later tables
    and the curation JSON key on the uid without the marker, so a repeat
    across passes collides exactly as one within a pass does.
    """
    files = defaultdict(list)
    for r in scene_rows:
        if r["file"] not in files[r["scene_uid"]]:
            files[r["scene_uid"]].append(r["file"])
    return {uid: f for uid, f in files.items() if len(f) > 1}


def refusals(file_rows, scene_rows, expected_px):
    """Lines saying why the manifest must not be written; [] when it may."""
    lines = []
    bad_px = pixel_size_mismatches(file_rows, expected_px)
    if bad_px:
        lines.append(f"{len(bad_px)} file(s) are not at pixel_size_um = "
                     f"{expected_px} (tolerance {PX_TOLERANCE:.1%}); every "
                     f"later stage would measure them at the wrong scale:")
        lines += [f"    {f}: px_um {px if px else 'missing from header'}"
                  for f, px in bad_px]
        lines.append("    -> remove them from source_files, or run them as "
                     "their own study with their own pixel_size_um.")
    dupes = duplicate_uids(scene_rows)
    if dupes:
        lines.append(f"{len(dupes)} scene uid(s) are produced by more than one "
                     f"file; their outputs would overwrite each other:")
        lines += [f"    {uid}: {' and '.join(f)}"
                  for uid, f in sorted(dupes.items())]
        lines.append("    -> the uid leaves out the name suffix; rename or "
                     "drop one of each pair (slide_naming / source_files).")
    return lines


# ---------------------------------------------------------------- main


def load_overrides():
    """Optional per-slide row-count corrections: file,n_rows."""
    if not os.path.exists(OVERRIDES_CSV):
        return {}
    out = {}
    with open(OVERRIDES_CSV, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("file") or "").strip()
            value = (row.get("n_rows") or "").strip()
            if name and value:
                out[name] = int(value)
    print(f"Loaded {len(out)} layout override(s) from {OVERRIDES_CSV}")
    return out


def main():
    os.makedirs(MANIFEST_DIR, exist_ok=True)
    os.makedirs(SLIDEMAP_DIR, exist_ok=True)

    accept_suggestions = "--accept-suggestions" in sys.argv
    overrides = load_overrides()
    accepted = {}
    # The archives stay open only for as long as the openers are used; the
    # CSVs below are written after they are released.
    with contextlib.ExitStack() as stack:
        # (source, member, reason) for every slide file meant to be read that
        # was not. Any entry fails the run and writes no manifest: see below.
        failures = []
        entries = discover_sources(stack, failures)
        print(f"Discovered {len(entries)} CZI entries across loose files and zips.")

        file_rows = []
        scene_rows = []
        layout_rows = []
        seen_names = {}
        # Drawn only once the run is known to be written: a refused run left
        # the slide maps describing it while both manifests described the
        # last good one.
        slidemaps = []

        for n_done, (source, member, container, opener) in enumerate(entries):
            PG.report(n_done, len(entries), "slide files")
            base = os.path.basename(member)
            parsed = parse_name(member)
            if parsed is None:
                failures.append((source, member, "filename did not match "
                                 "slide_naming.pattern (example: "
                                 f"{CONFIG.get('slide_naming', {}).get('example') or 'none set'})"))
                continue

            if base in seen_names:
                # Loose copies duplicate zip members byte for byte; record and move on.
                file_rows.append(
                    {
                        **seen_names[base],
                        "source": source,
                        "container": container,
                        "is_redundant_copy": 1,
                    }
                )
                continue

            try:
                with opener() as fh:
                    info = summarise(read_metadata(fh))
            except Exception as exc:  # noqa: BLE001 - want the reason in the report
                failures.append((source, member, repr(exc)[:120]))
                continue

            # Checked before the file gets a row, so manifest_files.csv never
            # lists a file manifest_scenes.csv has no sections for. Both used
            # to be "printed and carried on": a file with no Scenes block got a
            # file row and no scene rows, and a Scenes block that disagreed
            # with SizeS (sections the header says exist but cannot place)
            # was a line in the summary of a run that exited 0.
            scenes = info["scenes"]
            if not scenes:
                failures.append((source, member, "no scenes in metadata"))
                continue
            # Only where the header states a count: ZEN leaves SizeS out of a
            # single-scene acquisition, czi_meta reads that as 0, and refusing
            # it would stop the whole study over a file whose every scene is
            # placed.
            if info["size_s"] and len(scenes) != info["size_s"]:
                failures.append((source, member,
                                 f"{len(scenes)} scene(s) in the Scenes block but "
                                 f"SizeS is {info['size_s']}; the sections it cannot "
                                 f"place would be missing from the study"))
                continue

            channel_names = [c["name"] for c in info["channels"]]
            marker = channel_names[1] if len(channel_names) > 1 else None
            # As the study names it: every stage matches scenes on this name.
            marker = CH.study_marker_name(CONFIG, marker)
            marker_exposure = info["channels"][1]["exposure_ms"] if len(info["channels"]) > 1 else None

            row = {
                "file": base,
                "source": source,
                "container": container,
                "animal": parsed["animal"],
                "slide": parsed["slide"],
                "variant": parsed["variant"],
                "name_suffix": parsed["name_suffix"],
                "marker_channel": marker,
                "marker_exposure_ms": marker_exposure,
                "dapi_exposure_ms": info["channels"][0]["exposure_ms"] if info["channels"] else None,
                "acquired": info["acquired"],
                "size_x": info["size_x"],
                "size_y": info["size_y"],
                "size_c": info["size_c"],
                "size_s": info["size_s"],
                "n_tiles_m": info["size_m"],
                "n_scenes": len(info["scenes"]),
                "pixel_type": info["pixel_type"],
                "component_bit_count": info["component_bit_count"],
                # Every stage that asks "is this pixel clipped?" reads this
                # rather than a literal. It is 65535 on this dataset because
                # the sensor is genuinely 16-bit, not by assumption.
                "clip_ceiling": info["clip_ceiling"],
                "channel_pixel_types": "|".join(
                    str(c["pixel_type"]) for c in info["channels"]),
                "channel_bit_counts": "|".join(
                    str(c["component_bit_count"]) for c in info["channels"]),
                "px_um": info["px_um_x"],
                "objective_mag": info["objective_mag"],
                "objective_na": info["objective_na"],
                "camera": info["camera"],
                "shading_reference_mode": info["shading_reference_mode"],
                "online_stitching": info["online_stitching"],
                "is_redundant_copy": 0,
            }
            seen_names[base] = {k: row[k] for k in row if k not in ("source", "container", "is_redundant_copy")}
            file_rows.append(row)

            forced = overrides.get(base)
            rows_idx, cols_idx = build_grid(scenes, forced)
            spread = row_spread_ratio(scenes, rows_idx)
            suggestion = suggest_rows(scenes) if (spread > ROW_SPREAD_FLAG and not forced) else None

            if suggestion and accept_suggestions:
                forced = suggestion
                accepted[base] = suggestion
                rows_idx, cols_idx = build_grid(scenes, forced)
                spread = row_spread_ratio(scenes, rows_idx)
                suggestion = None

            serials = serial_numbers(rows_idx, cols_idx, scenes)

            for i, sc in enumerate(scenes):
                scene_rows.append(
                    {
                        "scene_uid": NM.scene_uid(parsed, sc["index"]),
                        "file": base,
                        "animal": parsed["animal"],
                        "slide": parsed["slide"],
                        "variant": parsed["variant"],
                        "marker_channel": marker,
                        "scene_index": sc["index"],
                        "scene_name": sc["name"],
                        "center_x_um": round(sc["center_x_um"], 2) if sc["center_x_um"] is not None else "",
                        "center_y_um": round(sc["center_y_um"], 2) if sc["center_y_um"] is not None else "",
                        "width_um": round(sc["width_um"], 2) if sc["width_um"] else "",
                        "height_um": round(sc["height_um"], 2) if sc["height_um"] else "",
                        "slide_row": int(rows_idx[i]),
                        "slide_col": int(cols_idx[i]),
                        **{f"serial_{k}": int(serials[k][i]) for k, _ in CONVENTIONS},
                    }
                )

            row_counts = np.bincount(rows_idx).tolist()
            layout_rows.append(
                {
                    "file": base,
                    "animal": parsed["animal"],
                    "slide": parsed["slide"],
                    "variant": parsed["variant"],
                    "n_scenes": len(scenes),
                    "n_rows": int(rows_idx.max()) + 1,
                    "n_cols": int(cols_idx.max()) + 1,
                    "row_counts": "|".join(str(c) for c in row_counts),
                    "row_spread_ratio": round(float(spread), 2),
                    "rows_forced": forced or "",
                    "suggested_rows": suggestion or "",
                    "needs_review": int(suggestion is not None),
                }
            )

            slidemaps.append((
                os.path.join(SLIDEMAP_DIR, base.replace(".czi", ".png")),
                base,
                scenes,
                rows_idx,
                cols_idx,
                serials,
            ))

    # Printed on a refused run as well: a control-looking name that fails the
    # grammar is exactly when the operator most needs to hear it.
    rc_warnings = reagent_control_warnings(entries)
    if rc_warnings:
        print()
        print(f"{len(rc_warnings)} file(s) may be reagent controls (warning only):")
        for line in rc_warnings:
            print(line)
    n_rc = flag_reagent_controls(file_rows, scene_rows)

    # Before anything is written for downstream to read: a stale manifest from
    # an earlier good run is safer than a fresh one that is wrong.
    #
    # Nothing found is a failure, not "nothing to write": the old manifest was
    # left in place and the stage read as done over a study it never saw.
    if not entries and not failures:
        print()
        print(f"!! 00_manifest found no CZI in {SOURCE_DIR}"
              + (" matching source_files" if CONFIG.get("source_files") else "")
              + "; manifest not written.")
        return 1
    # A file that could not be read is a failure of the whole run, and neither
    # manifest table is written. Writing the rest used to replace
    # manifest_files.csv while manifest_scenes.csv kept the previous run's rows
    # (or lost a file's sections without a count), so the two tables described
    # different studies and every later stage trusted both.
    if failures:
        print()
        print("=" * 72)
        print(f"slide files read : {len(file_rows)}")
        print(f"failures         : {len(failures)}")
        for f in failures:
            print(f"    !! {f[0]} :: {f[1]} :: {f[2]}")
        print("=" * 72)
        print(failure_line(failures))
        return 1

    refused = refusals(file_rows, scene_rows, CONFIG["pixel_size_um"])
    if refused:
        print()
        print("!! 00_manifest refuses this study; manifest not written.")
        for line in refused:
            print(line)
        return 1

    for args in slidemaps:
        render_slidemap(*args)

    if accepted:
        merged = dict(overrides)
        merged.update(accepted)
        _write_csv(
            OVERRIDES_CSV,
            [{"file": k, "n_rows": v} for k, v in sorted(merged.items())],
        )
        print(f"  accepted {len(accepted)} row-count suggestion(s) into layout_overrides.csv")

    order_key = f"serial_{ORDER_CONVENTION}"
    n_series, n_rescans = assign_section_order(scene_rows, order_key)
    print(f"  section_order written from {order_key} across {n_series} series"
          + (f"; {n_rescans} re-scanned slide(s) numbered inside their slide's "
             f"places" if n_rescans else "")
          + (f"; {n_rc} reagent control scene x marker(s) numbered in their own series"
             if n_rc else ""))

    _write_csv(os.path.join(MANIFEST_DIR, "manifest_files.csv"), file_rows)
    _write_csv(os.path.join(MANIFEST_DIR, "manifest_scenes.csv"), scene_rows)
    _write_csv(os.path.join(QC_DIR, "slide_layout_summary.csv"), layout_rows, LAYOUT_KEYS)

    _report(file_rows, scene_rows, layout_rows)

    # Written first so the operator can read which channels the scans carry;
    # then stopped, because every stage after this keys on those names. This
    # sat after `return 0` and never ran.
    CH.require_manifest_markers(CONFIG, scene_rows, CONFIG_PATH)
    return 0


def _remedy(reason):
    """What to do about one failure, worded for its cause: renaming does not
    mend a damaged file, and source_files is a list of the files to KEEP."""
    if reason.startswith("filename did not match"):
        return "rename it to fit slide_naming.pattern, or list only the files to keep in source_files"
    if reason.startswith("named in source_files"):
        return "correct or remove that name in source_files"
    return ("re-copy or re-export it, or list only the files to keep in source_files")


def failure_line(failures, shown=3):
    """The one line app/runner.py shows for a refused run: which files, why,
    and what to do - the log above it has the full list."""
    parts = []
    for source, member, reason in failures[:shown]:
        name = os.path.basename(member) or source
        parts.append(f"{name} ({reason}; {_remedy(reason)})")
    more = len(failures) - shown
    return (f"!! 00_manifest wrote no manifest: {len(failures)} slide file(s) could not "
            f"be read - " + "; ".join(parts)
            + (f"; and {more} more, listed above" if more > 0 else ""))


# slide_layout_summary.csv / slide_layout_review.csv, pinned so a table with no
# rows is still written - header only - rather than left as last run's file.
LAYOUT_KEYS = ["file", "animal", "slide", "variant", "n_scenes", "n_rows", "n_cols",
               "row_counts", "row_spread_ratio", "rows_forced", "suggested_rows",
               "needs_review"]


def _write_csv(path, rows, keys=()):
    """Every row's columns, `keys` first. A table with no rows is written
    header-only: skipping it left the previous run's file (a review list of
    slides that no longer need review) to be read as this run's."""
    keys = list(keys)
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    if not keys:
        raise ValueError(f"no columns for {path}: pass keys for a table that can be empty")
    # Swapped in, not written in place: every later stage reads the manifest
    # as the truth about the study, and a crash part-way through left a
    # truncated one that they read without complaint.
    IO.atomic_write_csv(path, rows, keys)
    print(f"  wrote {path}  ({len(rows)} rows)")


def _report(file_rows, scene_rows, layout_rows):
    unique = [r for r in file_rows if not r["is_redundant_copy"]]
    print()
    print("=" * 72)
    print(f"unique CZI files parsed : {len(unique)}")
    print(f"redundant loose copies  : {len(file_rows) - len(unique)}")
    print(f"sections (scenes) total : {len(scene_rows)}")

    by_marker = defaultdict(int)
    for r in unique:
        by_marker[r["marker_channel"]] += 1
    print(f"files per marker channel: {dict(by_marker)}")

    # Any file whose header disagrees with the pipeline's assumptions is worth
    # surfacing now rather than discovering halfway through Stage 1.
    odd_px = {round(r["px_um"], 4) for r in unique if r["px_um"]}
    print(f"distinct pixel sizes     : {sorted(odd_px)}")
    shading = {r["shading_reference_mode"] for r in unique}
    print(f"shading reference modes  : {shading}")

    exposures = defaultdict(set)
    for r in unique:
        exposures[r["marker_channel"]].add(r["marker_exposure_ms"])
        exposures["DAPI"].add(r["dapi_exposure_ms"])
    print(f"exposures (ms) per channel: { {k: sorted(v) for k, v in exposures.items()} }")

    row_hist = defaultdict(int)
    for r in layout_rows:
        row_hist[r["n_rows"]] += 1
    print(f"rows per slide           : {dict(sorted(row_hist.items()))}")

    review = sorted(
        (r for r in layout_rows if r["needs_review"]),
        key=lambda r: -r["row_spread_ratio"],
    )
    _write_csv(os.path.join(QC_DIR, "slide_layout_review.csv"), review, LAYOUT_KEYS)
    print(f"slides needing row review: {len(review)}  (worst first, see qc/slide_layout_review.csv)")
    for r in review[:10]:
        print(
            f"    {r['file']}: {r['n_scenes']} scenes, detected {r['n_rows']} row(s) {r['row_counts']}, "
            f"spread {r['row_spread_ratio']}x section height -> suggest {r['suggested_rows']} rows"
        )
    if len(review) > 10:
        print(f"    ... and {len(review) - 10} more")
    if review:
        print(f"    -> accept every suggestion with:  python 00_manifest.py --accept-suggestions")
        print(f"    -> or hand-edit {OVERRIDES_CSV}")

    dupes = defaultdict(list)
    for r in unique:
        dupes[(r["animal"], r["slide"], r["marker_channel"])].append(r["file"])
    real_dupes = {k: v for k, v in dupes.items() if len(v) > 1}
    print(f"duplicate animal+slide+marker units: {len(real_dupes)}")
    for k, v in sorted(real_dupes.items()):
        print(f"    {k[0]} slide {k[1]} {k[2]}: {v}")

    print("=" * 72)
    print("NEXT: open qc/slidemaps/, compare a few against the physical slides,")
    print("      and tell me which convention (A/B/C/D) is correct.")


if __name__ == "__main__":
    raise SystemExit(main())
