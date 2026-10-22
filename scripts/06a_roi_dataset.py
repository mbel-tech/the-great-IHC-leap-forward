"""Stage 6a - per-nucleus measurements to a per-ROI dataset. Still blind.

Reads `results/roi_nuclei.csv` and decides three things.

**Where the positivity cut comes from.** Each section's own background discs -
tissue the operator judged to carry no real signal - give a robust upper bound:

    cut = median(background) + 3 * 1.4826 * MAD(background)

Per section, because this is a high-baseline marker and the background level
moves from section to section. **The earlier reason given here was wrong and is
corrected:** it claimed the background level "splits the animals into two groups
8,732 units apart" and called that a staining batch effect. LOGS.md later
overturned that reading - restricted to clip-free sections the two groups are
the same (background 10,844 vs 12,300; tissue 5,542 vs 5,494), and the 1.86x gap
is *produced by* clipped pixels pinned at 65,535 dragging the mean up. That is
information loss, not a gain difference. The per-section cut is still the right
choice; the reason is section-to-section variation, not two batches.

Robust rather than a percentile, and this is the point: a percentile would fix
the false-positive rate by construction and destroy the only independent check
available. With a spread based cut, how many background nuclei land above it is
a MEASUREMENT.

**What the background discs then report.** The same detector over tissue called
empty is a false-positive rate, per section and per animal. It is not a negative
control - the primary antibody is on that tissue - so it measures non-specific
binding plus autofluorescence, not zero.

**Abercrombie.** Sections are 14 um and imaged in one plane, so what is counted
is nuclear PROFILES: a nucleus straddling the cut face still appears in the
section it is cut into, and neighbouring sections are consecutive, so pooling
raw counts across them counts that cell twice. N = n * T/(T + h), with h MEASURED
here per region and marker from the segmentation rather than taken from the
7.0 um in config - the factor moves from 0.74 to 0.58 across a plausible range,
and an assumed h would bury that in every density.

**And only where the objects are nuclei.** N = n * T/(T+h) assumes spherical,
randomly positioned objects; a threshold region on a fibre-stained channel is
neither, so a study declaring `nucleus_shaped: false` for a marker gets the raw
count, a factor of 1.0, and a `abercrombie_withheld_reason` saying so in the
row itself rather than in a config a reader would have to go and find. A
roi_nuclei.csv with no such column is read as nuclear, which is what every row
written before 2026-09-08 is. No study declares `nucleus_shaped: false` today,
so nothing in the withheld path has met real data.

Writes, all still keyed by animal only:
    results/roi_measurements.csv       one row per placed ROI
    results/detector_specificity.csv   what the background discs found

Run:  python 06a_roi_dataset.py
"""

import sys
import collections
import csv
import importlib.util
import os
import statistics as st
from array import array

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("_g5", os.path.join(_HERE, "05a_roi_geometry.py"))
G5 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(G5)

_lsio = importlib.util.spec_from_file_location(
    "_lsio", os.path.join(os.path.dirname(os.path.abspath(__file__)), "ls_io.py"))
IO = importlib.util.module_from_spec(_lsio)
_lsio.loader.exec_module(IO)

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
# ls_config resolves LS_CONFIG, applies the defaults and validates once for
# the whole process. Imported, not re-implemented: this block used to be four
# lines copy-pasted into every stage.
from ls_config import CONFIG, CONFIG_PATH  # noqa: E402
import ls_overlay as LO  # noqa: E402
OUT_ROOT = G5.OUT_ROOT
RESULTS = os.path.join(OUT_ROOT, "results")
NUCLEI_CSV = os.path.join(RESULTS, "roi_nuclei.csv")
MEAS_CSV = os.path.join(RESULTS, "roi_measurements.csv")
SPEC_CSV = os.path.join(RESULTS, "detector_specificity.csv")
#: The cut each section was counted against, at full precision (repr, so it
#: reads back as the same float). roi_measurements.csv carries it rounded to
#: 0.1 for reading; a nucleus whose median sits on that rounded value may be
#: on either side of the real one, and 06i_detection_overlays.py colours each
#: nucleus by the cut it was counted by. A file of its own rather than a
#: column, so the spreadsheets and R scripts reading roi_measurements.csv see
#: the table they always have.
CUTS_CSV = os.path.join(RESULTS, "positivity_cuts.csv")
CUTS_COLUMNS = ["scene_uid", "marker", "cut"]
#: What 05c recorded about the shape each ROI was measured on (05c's
#: GEOMETRY_CSV), and where this stage lists the sections whose shape has
#: changed since. See check_geometry.
GEOMETRY_CSV = os.path.join(RESULTS, "roi_measured_geometry.csv")
GEOMETRY_CHANGED_CSV = os.path.join(RESULTS, "roi_geometry_changed.csv")
#: 05c's log of the sections it finished (05c's DONE_LOG). A section measured
#: and found empty has no nucleus rows, and this is the only place it shows.
DONE_LOG = os.path.join(RESULTS, "roi_nuclei_sections_done.csv")


def measured_empty(path, have):
    """(scene_uid, marker) pairs 05c logged done that have no rows in `have`.

    Those are sections 05c measured and found nothing in: it logs them, and
    not one it skipped for want of a shared intensity range or one a crash cut
    off - both of which leave a measured-geometry row too, so that record is
    not proof of a measurement and this log is. `have` is the (uid, marker)
    pairs with rows. No log, or none for a marker, is no such section.
    """
    if not os.path.exists(path):
        return set()
    with open(path, newline="", encoding="utf-8") as fh:
        logged = {(r[1], r[0]) for r in csv.reader(fh)
                  if len(r) >= 2 and r[1] != "*"}
    return logged - set(have)

T_UM = CONFIG["section_thickness_um"]
AB = CONFIG["detection"].get("abercrombie", {})
AB_ON = bool(AB.get("enabled"))
BASE_PX_UM = CONFIG["pixel_size_um"]

# The intensity a nucleus is judged on. The median over its own pixels, so one
# bright speck inside a nucleus cannot carry it over the line.
STAT = "marker_median"


def mad(xs):
    m = st.median(xs)
    return st.median([abs(x - m) for x in xs])


def reads_nucleus_shaped(value):
    """How a `nucleus_shaped` cell reads, INCLUDING when there is no cell.

    THE LS COMPATIBILITY RULE, and it is the reason this is a function rather
    than `== "1"` inline. A roi_nuclei.csv written before 2026-09-08 has no
    such column, and every object in it came from the nuclear channel
    segmented by StarDist, because that was the only route 05c had. Its
    silence is therefore not "unknown" - it is True, and any other reading
    would withhold the Abercrombie correction from 961,233 rows that are
    nuclei. 05c backfills the column with exactly this reading when it resumes
    onto such a file.
    """
    if value is None or value == "":
        return True
    return str(value).strip().lower() not in ("0", "false", "no")


def abercrombie_reason(segmented_on, nucleus_shaped, enabled=True):
    """Why the correction is withheld, or "" when it applies.

    A string rather than a boolean, because the output has to say WHY. A reader
    six months from now finds a density that is not corrected and needs to know
    whether that was a decision or an omission.

    `enabled` is the study's own switch, and it is here so that a blank cell
    means one thing only: the correction was applied. Otherwise a study with
    `detection.abercrombie.enabled: false` would emit factor 1.0 beside an
    empty reason, which reads as "corrected, and the factor happened to be 1".
    """
    if not enabled:
        return ("detection.abercrombie.enabled is false for this study, so no "
                "count is corrected; the densities here are of nuclear "
                "profiles, not of nuclei")
    if nucleus_shaped:
        return ""
    return (f"objects segmented on {segmented_on} are not nucleus-shaped; "
            f"Abercrombie assumes spherical, randomly positioned objects and "
            f"does not hold for them, so counts here are raw")


def denominator_reason(own, segmented_on):
    """Why a marker's fraction and total cell density are withheld, or "".

    Blank means a denominator exists: the objects are nuclei found on the
    counterstain, and `n_nuclei` is a count of all cells that was never decided
    by the marker. NOT blank means the objects were found on the marker's own
    channel, so `n_nuclei` is "objects the marker lit up" - the numerator's own
    population - and a fraction of it, or a "cell density" built from it, would
    be a number that reads as a statement about cells and is not one.

    The positive count and the positive density are NOT withheld: those are
    what such a marker can honestly say. A string rather than a boolean for the
    reason denominator_reason's sibling gives - the output has to say why.
    """
    if not own:
        return ""
    return (f"objects segmented on {segmented_on} itself; with no nuclear "
            f"counterstain there is no count of all cells, so the fraction "
            f"positive and the total cell density are withheld")


def abercrombie_factor(h, apply=True):
    """T/(T+h), or 1.0 when the correction does not apply.

    1.0 rather than blank so that the corrected column is always a number and
    always comparable; the withheld_reason column is what says it was not
    corrected. A blank would make every consumer handle a missing value, and
    the ones that forgot would silently drop the row.
    """
    if not apply or not h:
        return 1.0
    return T_UM / (T_UM + float(h))


def roi_area_um2(b):
    """The area of the ROI on the slide, in um^2, as 05a measured it.

    `area_um2` is the answer for either shape - pi*a*b for a disc, the shoelace
    over the mapped vertices for a drawn region - and is preferred wherever it
    is present. Falling back to the two semi-axes keeps a roi_boxes file written
    before 05a computed the area readable; a POLYGON has no semi-axes and leaves
    those columns blank, so the fallback is for old disc files only.

    Never pi*r^2 in canonical pixels either way: the map is not a similarity."""
    a = b.get("area_um2", "")
    if a not in ("", None):
        return float(a)
    return np.pi * float(b["axis_a_um"]) * float(b["axis_b_um"])


def split_by_marker(boxes):
    """One section's boxes, split per marker, each group keeping its own order.

    THE JOIN KEY IS (scene_uid, marker, roi_index), NOT (scene_uid, roi_index).
    05c_detect_rois numbers `roi_index` within THAT MARKER'S OWN box file,
    from 1, and 05a.all_boxes() concatenates every marker's file. Under
    `paired` the uids are disjoint - all_boxes() proves it - so a section has
    exactly one group here and this returns the same list in the same order:
    the join is byte-for-byte what it was.

    Under `multiplex` the markers ARE one scan of one section, so all_boxes()
    hands over both markers' boxes for the SAME uid and they interleave. A
    section with 2 discs per marker gets positions 1..4, while `roi_index` only
    ever runs 1..2 - so without this split the second marker's discs would be
    unreachable and its nuclei would be pooled onto the first marker's. That
    is the silent, plausible, wrong number the `paired` uid guard exists to
    prevent, and relaxing that guard for `multiplex` moved the problem here.
    """
    out = {}
    for b in boxes:
        out.setdefault(b.get("marker", ""), []).append(b)
    return out


def check_join(nuc, box_by):
    """The nucleus-to-disc join is POSITIONAL. Check it before trusting it.

    A nucleus names its disc by `roi_index`, the row position of that disc in
    roi_boxes_<marker>.csv for its section, and the emit loop in main()
    enumerates the same list in the same order. 05a rewrites that file from
    the newest curator export, so a disc inserted or removed on a section after
    05c ran shifts every later index on it: the background disc's nuclei would
    be summed under an ROI, with a count and a density that look fine. The
    nuclei rows also carry roi_kind and region, so for every (section, marker,
    index) the box at that position must agree on both - a mismatch means the
    boxes changed under the measurements.

    The MARKER is part of that key, and has to be: see split_by_marker. A
    nucleus whose marker has no boxes on its section is fatal for the same
    reason a shifted index is - the emit loop would otherwise report zero
    nuclei for that marker while its nuclei were counted under another's disc.

    Sections present in the nuclei but absent from every box file are the
    other case: a stale row, or a box file regenerated without them. Those are
    skipped by the emit loop already; they are returned and reported, not
    fatal.
    """
    seen, bad, orphan = set(), {}, set()
    split = {u: split_by_marker(bs) for u, bs in box_by.items()}
    for r in nuc:
        uid = r["scene_uid"]
        mk = r.get("marker", "")
        key = (uid, mk, r["roi_index"])
        if key in seen:
            continue
        seen.add(key)
        groups = split.get(uid)
        if not groups:
            orphan.add(uid)
            continue
        boxes = groups.get(mk)
        if not boxes:
            bad.setdefault(uid, f"nuclei for marker {mk!r} but this section's "
                                f"boxes are {sorted(groups)}")
            continue
        i = int(r["roi_index"])
        if not 1 <= i <= len(boxes):
            bad.setdefault(uid, f"roi_index {i} but only {len(boxes)} boxes"
                                f" for {mk}")
            continue
        b = boxes[i - 1]
        if b["roi_kind"] != r["roi_kind"] or b["region"] != r["region"]:
            bad.setdefault(uid, f"roi_index {i}: nuclei say {r['roi_kind']}/"
                                f"{r['region']}, box is {b['roi_kind']}/{b['region']}")
    if orphan:
        print(f"  !! {len(orphan)} sections in roi_nuclei.csv have no boxes in any "
              f"roi_boxes_<marker>.csv and are skipped: {sorted(orphan)[:8]}")
    if bad:
        lines = "\n".join(f"    {u}  {why}" for u, why in sorted(bad.items()))
        raise SystemExit(
            f"!! roi_boxes disagree with roi_nuclei.csv on {len(bad)} sections:\n"
            f"{lines}\n"
            f"  The boxes were regenerated after detection, so the positional "
            f"join is shifted. Either restore the roi_boxes file the nuclei "
            f"were measured against, or re-run 05c_detect_rois.py --force for "
            f"those sections.")
    return orphan


def _same_value(a, b):
    """Whether two CSV cells hold the same value: as numbers where both are,
    so "128" and "128.0" agree, and as text otherwise (region, sec_poly)."""
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return (a or "") == (b or "")


def check_geometry(nuc, box_by, recorded, report_path):
    """The shape each ROI was measured on must still be the shape it has.

    check_join() proves every index still names a disc of the same kind and
    region. It cannot see a disc that was MOVED: dragged, resized, a polygon
    redrawn, then re-exported. 05a rewrites roi_boxes from that export, 05c's
    resume skips the section because it already has rows, and the emit loop
    then divided the nuclei counted in the old shape by the area of the new
    one - a plausible density, from two different discs.

    `recorded` is 05c's GEOMETRY_CSV: per (section, marker, roi_index), the box
    columns membership and area depend on, as 05c read them. Any of those that
    differs now, or a different number of ROIs, is FATAL for the section, as a
    shifted join is - the count has to be measured again on the shape it will
    be divided by. The sections are written to `report_path` as well as named,
    because the fix is per section and the list can be long.

    A section with nuclei and NO record was measured before 05c kept one. That
    cannot be checked either way, so it is returned and reported, not fatal:
    refusing would make every existing dataset unusable until re-detected.
    """
    rec, fields = {}, []
    if recorded:
        fields = [c for c in recorded[0]
                  if c not in ("scene_uid", "marker", "roi_index")]
        for r in recorded:
            rec.setdefault((r["scene_uid"], r["marker"]), {})[
                int(r["roi_index"])] = r
    split = {u: split_by_marker(bs) for u, bs in box_by.items()}
    changed, unverified = {}, set()
    for uid, mk in sorted({(r["scene_uid"], r.get("marker", "")) for r in nuc}):
        boxes = split.get(uid, {}).get(mk)
        if not boxes:
            continue                    # check_join's case, reported there
        then = rec.get((uid, mk))
        if not then:
            unverified.add(uid)
            continue
        if len(then) != len(boxes):
            changed[(uid, mk)] = (f"measured on {len(then)} ROIs, the boxes "
                                  f"now have {len(boxes)}")
            continue
        for i, b in enumerate(boxes, 1):
            old = then.get(i, {})
            diff = [f for f in fields
                    if not _same_value(old.get(f, ""), b.get(f, ""))]
            if diff:
                changed[(uid, mk)] = f"roi_index {i}: " + ", ".join(
                    f"{f} {old.get(f, '')!r} -> {b.get(f, '')!r}" for f in diff)
                break
    if unverified:
        print(f"  !! {len(unverified)} sections were measured before 05c "
              f"recorded ROI geometry, so a disc moved since detection cannot "
              f"be caught on them: {sorted(unverified)[:8]}")
    if not changed:
        # A list from an earlier refusal must not outlive the problem it named.
        if os.path.exists(report_path):
            os.remove(report_path)
        return unverified
    IO.atomic_write_csv(report_path,
                        [{"scene_uid": u, "marker": m, "reason": why}
                         for (u, m), why in sorted(changed.items())],
                        ["scene_uid", "marker", "reason"])
    shown = sorted(changed.items())[:10]
    lines = "\n".join(f"    {u} [{m}]  {why}" for (u, m), why in shown)
    if len(changed) > len(shown):
        lines += f"\n    ... and {len(changed) - len(shown)} more"
    redo = " and ".join(f"05c_detect_rois.py --marker {m} --force"
                        for m in sorted({m for _, m in changed}))
    raise SystemExit(
        f"!! {len(changed)} sections have ROIs whose shape changed after "
        f"their nuclei were counted:\n{lines}\n"
        f"  Their counts belong to the old shapes and the areas to the new "
        f"ones, so no density from them is right. All of them are listed in "
        f"{report_path}. Re-measure them on the current shapes ({redo}), or "
        f"restore the curator export they were measured with and re-run "
        f"05a_roi_geometry.py.")


class _Disc:
    """One disc's nuclei, as the few numbers the emit loop reads off them.

    roi_nuclei.csv used to be read whole, a dict per nucleus with eight more
    keys added - about 2 KB a row, several GB once PCNA's ~5 million rows are
    in the file. Every decision here is per section, per disc or per (marker,
    region), so this keeps, per disc, the intensity, diameter, censored and
    own-channel flag of each nucleus in compact arrays (the medians need the
    values themselves, not a running sum), and the provenance answers the emit
    loop takes off a disc's rows. A nucleus costs ~18 bytes here, not ~2 KB.

    The stream keeps file order, so every "first row that says X" below is
    the same row the old list gave, and every mean and median is taken over
    the same values: statistics.mean sums exactly and median sorts, so
    neither depends on the order the values arrive in.

    `kind` and `region` are the first row's. They are the disc's own - 05c
    writes them off the box - and check_join() has checked them against it.
    """
    __slots__ = ("kind", "region", "v", "d", "cen", "own", "shaped",
                 "segon_unshaped", "segon_own")

    def __init__(self, kind, region):
        self.kind, self.region = kind, region
        self.v, self.d = array("d"), array("d")
        self.cen, self.own = bytearray(), bytearray()
        self.shaped = True              # all(nucleus_shaped) over the rows
        self.segon_unshaped = None      # segmented_on of the first unshaped row
        self.segon_own = None           # ...and of the first own-channel row


def main(argv=None):
    # argv is accepted and ignored: this stage has no flags, and taking it
    # lets 06e call every stage the same way instead of branching on a label.
    del argv
    if not os.path.exists(NUCLEI_CSV):
        print(f"no {NUCLEI_CSV} - run 05c_detect_rois.py first")
        return 1
    box_by = {}
    # EVERY marker's boxes. roi_nuclei.csv holds both once the PCNA pass has
    # run, and the cut is per section and h is per (marker, region), so one
    # run covers both without pooling anything across them.
    for b in G5.all_boxes():
        box_by.setdefault(b["scene_uid"], []).append(b)

    # ONE STREAMING PASS over the nuclei, building every index at once.
    #
    # This used to re-scan the whole table for each of three things: the cut
    # filtered all of it per section, h did the same per (marker, region), and
    # by_idx did it once more per section. At 883,078 nuclei that is hundreds of
    # millions of comparisons. 06c_excel_dataset.py already hit exactly this and
    # records what it cost - a rebuild that ran long enough to be killed
    # part-written, leaving an xlsx openpyxl could not reopen. PCNA is roughly
    # six times this file, so the shape matters more than the current runtime.
    #
    # And it is a STREAM: no row outlives its own iteration. What is kept is
    # per disc (see _Disc) and per section, so the table never sits in memory
    # as rows - which it did, a dict each, until PCNA made that several GB.
    #
    # Artifact pixels are not tissue and leave the analysis entirely. Censored
    # ones are RIGHT-censored - value lost, but at least the ceiling - so they
    # stay in the count and are excluded only from the intensity statistics.
    # Reversing those two would bias positive rates down in exactly the
    # brightest-staining animals (LOGS.md 2026-08-12).
    #
    # `off_tissue` joins the artifact side, for the same reason and more
    # bluntly: the nucleus is not on the section at all. 05c accepted a nucleus
    # on disc membership alone, so a disc overhanging the silhouette - or, in
    # one case, sitting 2.6 mm off the scanned scene entirely - contributed
    # objects found on glass. Those are worst in a BACKGROUND disc, where they
    # enter median + 3*1.4826*MAD and drag the positivity cut down, making a
    # section look more positive than it is.
    n_all = n_art = n_off = n_kept = 0
    # Every section 05c MEASURED, taken before the drop: one whose nuclei were
    # all on an artifact or off the tissue was still looked at, and its discs
    # are a count of 0, not missing.
    uids = set()
    # The first row of each (section, marker, roi_index), every row included:
    # all that check_join() and check_geometry() read, since both look at one
    # row per disc and skip the rest. One row per disc, not per nucleus.
    firsts = {}
    # (uid, marker, roi_index) -> that disc's kept nuclei. roi_index is what
    # ties a nucleus to the disc it was found in - 05c numbers the discs in the
    # order roi_boxes_<marker>.csv lists them for a section, WITHIN THAT
    # MARKER, and the emit loop below enumerates the same per-marker list in
    # the same order. The marker is in the key because it is in 05c's
    # numbering; see split_by_marker.
    by_idx = {}
    bg_by = collections.defaultdict(lambda: array("d"))    # uid -> bg values
    # (marker, region) -> diameters
    diam_by = collections.defaultdict(lambda: array("d"))
    d_by_marker = collections.defaultdict(lambda: array("d"))
    uids_by_marker = collections.defaultdict(set)   # kept nuclei's sections
    # marker -> (segmented_on, nucleus_shaped), for a disc that has no nuclei
    # of its own to read it off. Not-nucleus-shaped wins over nucleus-shaped:
    # one config cannot produce both for one marker, and if a file somehow
    # carries both the correction is the thing to withhold, not to apply.
    shape_by = {}
    own_by = {}                                # marker -> segmented on itself
    with open(NUCLEI_CSV, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            n_all += 1
            uid = r["scene_uid"]
            uids.add(uid)
            firsts.setdefault((uid, r.get("marker", ""), r["roi_index"]), r)
            v = float(r[STAT])
            cen = r["censored"] == "1"
            art = r["artifact"] == "1"
            # .get, because a roi_nuclei.csv written before 2026-09-02 has no
            # such column. Absent reads as False, which reproduces the old
            # behaviour exactly rather than silently reinterpreting an old
            # file - run 06g_flag_off_tissue.py to backfill it.
            off = r.get("off_tissue") == "1"
            d = float(r["equiv_diam_um"])
            if art:
                n_art += 1
                continue
            if off:
                n_off += 1
                continue
            n_kept += 1
            mk = r["marker"]
            # What KIND of object this row is, which decides whether
            # Abercrombie applies to it. Read off the row rather than
            # re-derived from config, because a dataset outlives the config
            # that made it. Absent means nuclear - see reads_nucleus_shaped.
            shaped = reads_nucleus_shaped(r.get("nucleus_shaped"))
            segon = r.get("segmented_on") or "the nuclear channel"
            # NUCLEI found on the marker's own channel (PCNA with no usable
            # DAPI). Read off the row, like the shape: a nuclear-route row says
            # "nuclear" (or the counterstain's name), never the marker's, and
            # an old file with no column is False. Nucleus-shaped only: a fibre
            # or plaque marker is also segmented on itself, but its objects are
            # regions, not a cell population, and what 06a does for those - raw
            # counts, with the Abercrombie reason saying so - is older than this
            # and unchanged.
            own = (bool(r.get("segmented_on"))
                   and r["segmented_on"] == mk and shaped)
            key = (uid, mk, int(r["roi_index"]))
            disc = by_idx.get(key)
            if disc is None:
                disc = by_idx[key] = _Disc(r["roi_kind"], r["region"])
            disc.v.append(v)
            disc.d.append(d)
            disc.cen.append(cen)
            disc.own.append(own)
            if not shaped:
                disc.shaped = False
                if disc.segon_unshaped is None:
                    disc.segon_unshaped = segon
            if own and disc.segon_own is None:
                disc.segon_own = segon
            d_by_marker[mk].append(d)
            uids_by_marker[mk].add(uid)
            seen = shape_by.get(mk)
            if seen is None or (seen[1] and not shaped):
                shape_by[mk] = (segon, shaped)
            own_by[mk] = own_by.get(mk, False) or own
            if r["roi_kind"] == "background":
                if not cen:
                    bg_by[uid].append(v)
            elif not own:
                diam_by[(mk, r["region"])].append(d)

    # Measured and found empty: discs with a count of 0, like the sections
    # above whose every nucleus was dropped. Only where it still has boxes -
    # the emit loop has nothing to write for one that has none - and checked
    # against the geometry it was measured on like any other: 0 counted on
    # the old shape of a moved disc is not 0 on the new one.
    empty = {(u, m) for u, m in measured_empty(
                 DONE_LOG, {(k[0], k[1]) for k in firsts})
             if u in box_by}
    uids |= {u for u, _ in empty}
    # MEASURED IS PER MARKER. Under `multiplex` every marker's boxes share the
    # section's uid, so a uid in `uids` says only that SOME marker was looked
    # at there. A marker 05c has not run on it yet has no rows and no log
    # entry, and must stay absent as an unmeasured section does - not report
    # zeros, which read as "looked and found none". Under `paired` a section
    # has one marker and this is the uid test above unchanged.
    measured = {(k[0], k[1]) for k in firsts} | empty
    print(f"{n_all} nuclei over {len(uids)} sections"
          + (f" ({len(empty)} measured with none found)" if empty else ""))
    check_join(firsts.values(), box_by)
    check_geometry(list(firsts.values())
                   + [{"scene_uid": u, "marker": m} for u, m in sorted(empty)],
                   box_by,
                   G5.load_csv(GEOMETRY_CSV) if os.path.exists(GEOMETRY_CSV)
                   else None,
                   GEOMETRY_CHANGED_CSV)
    del firsts
    print(f"  dropped {n_art} on an artifact, {n_off} off the tissue "
          f"-> {n_kept} nuclei quantified")

    # ---- the per-section cut, from that section's own background discs
    cuts, bgstat = {}, {}
    for uid, bg in bg_by.items():
        if len(bg) < 5:
            continue
        m, s = st.median(bg), mad(bg) * 1.4826
        cuts[uid] = m + 3.0 * s
        bgstat[uid] = (m, s, len(bg))

    # ---- h for Abercrombie, measured per region and marker
    #
    # For a marker segmented on its OWN channel the objects are not "all the
    # nuclei": StarDist on a marker also outlines dim, unspecific blobs, and
    # Abercrombie's h is the size of the thing being COUNTED. So h comes from
    # the objects that are counted as positive - above their section's cut, or
    # censored - in real ROIs. A section with no cut contributes nothing: its
    # objects are not counted either.
    pos_by_marker = collections.defaultdict(lambda: array("d"))
    for (uid, mk, _), disc in by_idx.items():
        if disc.kind == "background" or uid not in cuts:
            continue
        cut = cuts[uid]
        for v, d, c, o in zip(disc.v, disc.d, disc.cen, disc.own):
            if o and LO.is_positive(v, c, cut):
                diam_by[(mk, disc.region)].append(d)
                pos_by_marker[mk].append(d)
    hs = {k: st.mean(v) for k, v in diam_by.items() if v}

    # THE FALLBACK IS PER MARKER TOO, and it is not an edge case: background
    # discs carry region "__background__", which is never a key in diam_by, so
    # EVERY background row takes it - 586 of 2,069 today. A single pooled mean
    # would let PCNA, at roughly six times the pERK volume, set the Abercrombie
    # factor on pERK's background rows. The number would change, driven entirely
    # by the other antibody, and nothing would error.
    h_by_marker = {}
    for mk, d in d_by_marker.items():
        if own_by.get(mk):
            # The same population as the per-region h above; every object of
            # the marker only if none was positive, so a marker with no
            # positives still gets a factor rather than none.
            d = pos_by_marker.get(mk) or d
        if d:
            h_by_marker[mk] = st.mean(d)
    # Every kept diameter, once. statistics.mean sums exactly, so this is the
    # mean the old single list gave, whatever order the markers come in.
    d_all = array("d")
    for d in d_by_marker.values():
        d_all.extend(d)
    h_all = st.mean(d_all) if d_all else 0.0
    del d_all

    rows, spec = [], []
    for uid in sorted(uids):
        cut = cuts.get(uid)
        # PER MARKER, so `i` restarts at 1 for each one - which is how 05c
        # numbered them. Under `paired` there is exactly one group per section
        # and this is the old single loop unchanged.
        for mk, mboxes in split_by_marker(box_by.get(uid, [])).items():
            if (uid, mk) not in measured:
                continue
            for i, b in enumerate(mboxes, 1):
                here = by_idx.get((uid, mk, i))
                area = roi_area_um2(b)
                n_nuc = len(here.v) if here else 0
                usable = ([v for v, c in zip(here.v, here.cen) if not c]
                          if here else [])
                # A censored nucleus counts as POSITIVE and is still dropped from
                # `usable`. That is not an inconsistency: 04j_censor_clipped.py
                # defines a censored pixel as right-censored at the 16-bit ceiling,
                # so it is unambiguously above any cut, while including a ceiling
                # value in a median would bias the intensity statistic. 04j states
                # both halves outright - censoring "MUST NOT be excluded from
                # detection or from positivity" and MUST be excluded from any
                # intensity statistic. Do not "fix" this to drop them.
                n_pos = (sum(1 for v, c in zip(here.v, here.cen) if LO.is_positive(v, c, cut))
                         if here else 0) if cut is not None else ""
                h = hs.get((b["marker"], b["region"]),
                           h_by_marker.get(b["marker"], h_all))
                # ABERCROMBIE APPLIES WHERE THE OBJECTS ARE NUCLEI. N = n *
                # T/(T+h) assumes spherical, randomly positioned objects; a
                # threshold region on a fibre-stained channel is neither, and
                # correcting it would put a made-up number into a density.
                #
                # This disc's own nuclei answer first. A disc with none falls
                # back to the marker's other rows, and a marker with no rows at
                # all to nucleus-shaped - which is what every file written
                # before the provenance columns is, LS's included.
                shaped = here.shaped if here else None
                if shaped is None:
                    seg_on, shaped = shape_by.get(b["marker"],
                                                  ("the nuclear channel", True))
                else:
                    seg_on = (here.segon_unshaped
                              if here.segon_unshaped is not None else "")
                why = abercrombie_reason(seg_on, shaped, enabled=AB_ON)
                ab = abercrombie_factor(h, apply=not why)
                # Whether this disc's objects were found on the marker itself:
                # its own nuclei answer first, the marker's other rows if it
                # has none - the same two-step the shape uses above.
                own = (any(here.own) if here
                       else own_by.get(b["marker"], False))
                no_denom = denominator_reason(
                    own, (here.segon_own if here and here.segon_own is not None
                          else b["marker"]))
                row = {
                    "scene_uid": uid, "animal": b["animal"], "marker": b["marker"],
                    "roi_kind": b["roi_kind"], "region": b["region"],
                    "seed_n": b["seed_n"], "roi_index": i,
                    "plate_id": "", "sec_x": b["sec_x"], "sec_y": b["sec_y"],
                    "roi_area_um2": round(area, 1),
                    "roi_area_mm2": round(area / 1e6, 6),
                    "n_nuclei": n_nuc,
                    "n_positive": n_pos,
                    "frac_positive": (round(n_pos / n_nuc, 4)
                                      if (cut is not None and n_nuc
                                          and not no_denom) else ""),
                    "profile_density_per_mm2": round(n_nuc / (area / 1e6), 1) if area else "",
                    "abercrombie_factor": round(ab, 4),
                    # EVERY row carries this key, blank included. There is no
                    # COLUMNS constant here - the header is the first emitted
                    # row's keys (see the write below) - so a key present on
                    # some rows and absent from others either vanishes from the
                    # header or raises in the writer.
                    "abercrombie_withheld_reason": why,
                    "mean_nucleus_diam_um": round(h, 2) if h else "",
                    "cell_density_per_mm2": (round(n_nuc * ab / (area / 1e6), 1)
                                             if (area and not no_denom) else ""),
                    # Blank where a denominator exists. EVERY row carries the
                    # key, as abercrombie_withheld_reason does, because the
                    # header is the first emitted row's keys.
                    "denominator_withheld_reason": no_denom,
                    "positive_density_per_mm2": (round(n_pos * ab / (area / 1e6), 1)
                                                 if (cut is not None and area) else ""),
                    "marker_median_of_roi": (round(st.median(usable), 1)
                                             if usable else ""),
                    "section_bg_level": round(bgstat[uid][0], 1) if uid in bgstat else "",
                    "section_bg_spread": round(bgstat[uid][1], 1) if uid in bgstat else "",
                    "section_bg_nuclei": bgstat[uid][2] if uid in bgstat else 0,
                    "section_positivity_cut": round(cut, 1) if cut is not None else "",
                    "n_censored": sum(here.cen) if here else 0,
                }
                rows.append(row)

        # what the background discs report about the detector, per section
        bgrows = [r for r in rows if r["scene_uid"] == uid
                  and r["roi_kind"] == "background"]
        if bgrows and cut is not None:
            tot_n = sum(r["n_nuclei"] for r in bgrows)
            tot_p = sum(r["n_positive"] for r in bgrows)
            tot_a = sum(r["roi_area_mm2"] for r in bgrows)
            dens = [r["profile_density_per_mm2"] for r in bgrows
                    if r["profile_density_per_mm2"] != ""]
            spec.append({
                "scene_uid": uid, "animal": bgrows[0]["animal"],
                "marker": bgrows[0]["marker"],
                "n_discs": len(bgrows), "bg_nuclei": tot_n,
                "bg_area_mm2": round(tot_a, 6),
                "bg_nuclei_per_mm2": round(tot_n / tot_a, 1) if tot_a else "",
                "bg_false_positives": tot_p,
                # The rate's denominator is every object the detector chose to
                # find in empty tissue. For a counterstain that is "nuclei",
                # whatever their brightness; for a marker segmented on itself
                # it is "blobs StarDist outlined on noise", which depends on
                # how sensitive StarDist happens to be and not on the antibody.
                # So the rate is withheld there, and the per-area count of
                # false positives - which has no such denominator - stands in.
                "false_positive_rate": (round(tot_p / tot_n, 4)
                                        if (tot_n and not bgrows[0][
                                            "denominator_withheld_reason"])
                                        else ""),
                "bg_false_positives_per_mm2": (round(tot_p / tot_a, 1)
                                               if tot_a else ""),
                "denominator_withheld_reason": bgrows[0][
                    "denominator_withheld_reason"],
                "bg_level": round(bgstat[uid][0], 1),
                "bg_spread": round(bgstat[uid][1], 1),
                "positivity_cut": round(cut, 1),
                # Disagreement between discs on one section is heterogeneous
                # autofluorescence, and is worth seeing before it is averaged.
                "disc_density_spread": (round(max(dens) - min(dens), 1)
                                        if len(dens) > 1 else 0),
            })

    real = [r for r in rows if r["roi_kind"] == "roi"]
    # One row per (section, marker) that was counted against a cut, from the
    # emitted rows, so it describes exactly the sections roi_measurements.csv
    # does. Header only when no section has a cut; gone with the rest below
    # when there is nothing at all.
    exact = sorted({(r["scene_uid"], r["marker"]) for r in rows
                    if r["scene_uid"] in cuts})
    # Beside whichever roi_measurements.csv is written, so the two always
    # describe the same run.
    cuts_csv = os.path.join(os.path.dirname(MEAS_CSV), os.path.basename(CUTS_CSV))
    if rows:
        IO.atomic_write_csv(cuts_csv,
                            [{"scene_uid": u, "marker": m, "cut": repr(cuts[u])}
                             for u, m in exact], CUTS_COLUMNS)
    elif os.path.exists(cuts_csv):
        os.remove(cuts_csv)
    for path, data in ((MEAS_CSV, rows), (SPEC_CSV, spec)):
        if not data:
            # An EMPTY result must not leave the previous run's file standing.
            # detector_specificity.csv is read by plot_roi_figures.R to caption
            # every positivity figure with a false-positive rate; a stale one
            # would put a number from an earlier dataset on a new figure and
            # look entirely current. Skipping the write was the quiet option.
            if os.path.exists(path):
                os.remove(path)
                print(f"  nothing to write - removed the previous {os.path.basename(path)}")
            continue
        IO.atomic_write_csv(path, data, list(data[0].keys()))

    print("=" * 72)
    print(f"{len(real)} ROIs and {len(rows) - len(real)} background discs -> {MEAS_CSV}")
    print(f"{len(spec)} sections -> {SPEC_CSV}")
    # ONE BLOCK PER MARKER. This stage runs once for both, and all three of
    # these numbers are per marker. An h RANGE spanning two markers is not a
    # range of anything - pERK 9.4 and PCNA 6.0 would print as "6.0-9.4" and
    # destroy the check that a large deviation means the h grouping changed -
    # and the false-positive rate is the number the operator is told to stop and
    # read before building any figure on the cut. It is filtered on the figure;
    # pooling it in the console would put the two in disagreement.
    markers = sorted({r["marker"] for r in rows}) or [""]
    for mk in markers:
        m_uids = uids_by_marker.get(mk, set())
        m_cuts = [u for u in cuts if u in m_uids]
        m_hs = {k: v for k, v in hs.items() if k[0] == mk}
        m_spec = [s for s in spec if s["marker"] == mk]
        head = f"  [{mk}] " if len(markers) > 1 else "  "
        print(f"{head}sections with a cut : {len(m_cuts)} of {len(m_uids)}"
              f"   (needs >=5 background nuclei)")
        if AB_ON and m_hs:
            # The console must not print a factor this stage did not apply.
            # h is still measured for a withheld marker - it is a property of
            # the objects - but quoting the factor beside it would contradict
            # the 1.0 in every one of that marker's rows.
            m_why = abercrombie_reason(*shape_by.get(
                mk, ("the nuclear channel", True)), enabled=AB_ON)
            lo, hi = min(m_hs.values()), max(m_hs.values())
            if m_why:
                print(f"{head}Abercrombie         : WITHHELD - {m_why} "
                      f"(measured h {lo:.1f}-{hi:.1f} um)")
            else:
                print(f"{head}Abercrombie         : T={T_UM} um, measured h "
                      f"{lo:.1f}-{hi:.1f} um -> factor "
                      f"{T_UM/(T_UM+hi):.3f}-{T_UM/(T_UM+lo):.3f}")
        fp = [s["false_positive_rate"] for s in m_spec
              if s["false_positive_rate"] != ""]
        fpd = [s["bg_false_positives_per_mm2"] for s in m_spec
               if s["bg_false_positives_per_mm2"] != ""]
        if not fp and fpd and own_by.get(mk):
            # Withheld for a marker segmented on itself - see the spec row. The
            # per-area count has no such denominator and is what to read.
            print(f"{head}false positives     : median {st.median(fpd):.1f} "
                  f"per mm2 of background (the RATE is withheld: no count of "
                  f"all cells)")
        if fp:
            print(f"{head}false-positive rate : median {st.median(fp)*100:.1f}%, "
                  f"range {min(fp)*100:.1f}-{max(fp)*100:.1f}%   "
                  f"(measured on {sum(s['bg_nuclei'] for s in m_spec)} "
                  f"background nuclei)")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
