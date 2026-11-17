"""Stage 4l - assign a plate, place landmarks, warp the atlas regions onto the section.

This is SHARCQ's workflow (Lauridsen et al. 2022, eNeuro), rebuilt for the salmon
atlas. SHARCQ cannot be used directly - MATLAB, and bound to the Allen or
Franklin-Paxinos 3D atlas - but its shape is the right one, and the important
thing about it is what it does **not** automate: its user "must scroll to the
correct AP coordinate and DV/ML tilt" by eye, then clicks numbered corresponding
points between the slice and the atlas. What it automates is the landmark
registration, the warping, and the per-region counting.

That matters here because automatic level assignment has now been measured to
fail twice on this data, for a reason in the data rather than in the algorithm:

  * `04c`, silhouette IoU: correlation between serial order and best-matching
    plate is -0.05, 0.00, 0.17, -0.04;
  * giRAff's method, registered-intensity cross-correlation: -0.18 to +0.09 in
    both polarities.

See REFERENCES.md. The published tool for this exact task picks the plate by
hand, so this does too.

Interaction
-----------

  **Scrub the plate slider** until the plate matches the section, then **click
  matching points** - once on the section, once on the plate, alternating. Three
  pairs are the minimum for an affine; more improves it.

  **Press `Rotate`, then drag left or right on the section to tilt it.** Rotation
  is a mode rather than a gesture, so dragging can never be mistaken for placing
  a landmark - while it is on, clicks on the section are inert. Hold shift for
  fine control. **The tilt saves itself the moment the mouse is released** and is
  kept with that section, so leaving it and coming back shows it exactly as it was
  left. `Restore original tilt` (or `r`) puts the section back the way
  `04a_reformat` produced it, which is exactly rot = 0. The rotation is a
  **viewing aid and nothing else** - every stored coordinate stays in the
  unrotated reformatted frame, so the transform, the residuals and all three
  exports are identical whether the section was turned or not. The angle is
  carried in `roi_plates.csv` as `view_rotation_deg` for provenance only.

  **`Exclude` rejects the section**, and is a different statement from `No ROI
  here`: no-ROI says the section is fine and has nothing to measure at this
  level, exclude says the section should not be used. Landmarks already placed
  are kept in the exports; the exclusion is a flag to filter on, not a deletion.

  **Work is saved as you go**, to the browser's local storage, on every single
  change - not on a timer. On top of that a rolling snapshot is written once a
  minute under a second key, and `saved HH:MM:SS` in the header reports it. The
  snapshot exists for the one failure the per-change save cannot cover: it
  overwrites the single record every time, so a state that goes wrong has no
  earlier version to go back to. `roiRestoreBackup()` in the console rolls back
  to it - console rather than a button, because it replaces the live state
  wholesale. None of this is a substitute for `Export`, which is what produces
  the actual files.

  **`Shotgun` writes the deck.** Every favourite that has a plate, one slide per
  plate per marker, split down the middle by treatment, DAPI stripped, downloaded
  as a `.pptx`. It is the only button here that reads the group key, and the only
  one that produces a figure rather than a record - see the Shotgun section at the
  foot of the page script for why that is a declared exception to blinding. With
  no `groups` block in `config.json` it is disabled and says so.

  **`By region` writes the same deck cut the other way** - one slide per atlas
  REGION rather than per plate. A section appears on every region it carries an
  ROI for, so it appears more than once, and a favourite carrying no seeded ROI
  does not appear at all: the grouping comes from the seeds the ROIs answer,
  which is the same thing `roi_regions.csv` reports. Use it to compare one region
  across animals; use `Shotgun` to compare one level.

  **`f` marks a section favourite** - the subset worth carrying into actual
  quantification. It is orthogonal to the plate assignment, because a section can
  be worth quantifying before anyone has landmarked it, so it sets no other flag
  and is reported on its own. "favourites only" narrows the strip to that subset.

  **`q` flags a section as hard to attribute** - one whose region boundaries
  or atlas level are hard to call, worth a second look or a second opinion.
  Like a favourite it is orthogonal to everything else and sets no other flag;
  the strip draws it with a dashed orange edge, `roi_plates.csv` carries it in
  its own `difficult` column, and "difficult only" narrows the strip to it.

  **The plate and the section step independently.** The up/down arrows move
  through sections and leave the plate where it is; left/right moves the plate.
  Consecutive sections are at neighbouring levels, so the plate rarely needs to
  move more than a notch. Arrows move, letters act: `a` assign, `f` favourite,
  `x` exclude, `z` undo a point, `r` restore the original tilt. `x` was
  previously "next section", paired with `z`; the arrows do that now.
  Returning to a section that was already assigned or landmarked does show its
  own plate again, because that is a recorded decision rather than a position.

  **Nothing is warped onto the section.** Warping every seed through the fit and
  drawing them was tried and removed: ten placed ROIs produced thirty on screen
  and twenty-seven in the export, and the seventeen nobody had touched were
  extrapolation that looked like measurement. The check the overlay used to
  provide is now the residual per landmark, shown for each one so a mis-clicked
  pair is visible as a large error rather than quietly degrading the fit.

Choosing which regions to draw
------------------------------

  The first time the page opens it asks which regions will be drawn, in a pop-up
  that `o` (or the ROIs... button) reopens. The choice is study-wide, kept under
  its own localStorage key (`ls_roi_curator_v1_rois`, mirrored by the app) and
  not inside the curation record, which holds only sections. Selected outlines
  are highlighted in distinct colours and numbered 1..N in pick order on the
  plate; the rest are dimmed grey and cannot be aimed at. The stored `roi` -
  what a polygon records and what `roi_n` exports - is NEVER renumbered: only
  what is shown is. With nothing chosen the page behaves as it always did.

Regions, drawn rather than sampled
----------------------------------

  A region carries several seeds per lobe - Dl 142 over its plates, Dm 114 - and
  placing a small disc on each measures those discs, not the region. **`Region`
  mode draws the region itself.** Click its shape on the plate to say which one
  you are drawing, then click out the outline on the section, corner by corner;
  the first vertex, Enter or a double-click closes it.

  The plate shows each region as a HULL of its own seeds, one per lobe, so there
  is a shape to answer rather than a scatter of dots to infer one from. The hulls
  are convex and computed in `region_hulls` below - splitting a region's seeds
  before hulling them is the load-bearing part, because these regions are
  bilateral and a hull over both lobes spans the midline gap, which is the same
  failure `04f_exclusion_candidates.py` records for section solidity.

  **A polygon claims the seeds it encloses.** Their pairs stay landmarks - the
  fit wants every one of them, and nothing about placing them changed - but they
  stop being ROIs of their own, because the region drawn over them already
  measures that tissue. Exporting both would count the same nuclei twice.

Scope, deliberately bounded
---------------------------

**Vd, Vv and POA are not separable here.** A telencephalic section is
recognisable as telencephalon, but how far rostral or caudal it sits is not
readable from the section itself, and those three ventral regions occupy
overlapping positions across that range (Vd/Vv on plate_011-015, POA on
plate_020-025). A seed landing on one of them is evidence of "one of these
three", not of that region specifically, so the overlay and the summary show the
group and `roi_regions.csv` carries `region_ambiguous` and `ambiguity_group`
(and `region_uncertain_in_atlas`, for the eight seeds the atlas itself marks "??")
beside the atlas's own label. The label itself is never overwritten - pooling
them stays the reader's decision.

**31 of the 64 plates carry region seeds** - 362 seeds over 13 regions:
telencephalon and POA on plate_009 to plate_025 (Dl 142, Dm 114, Vv 16, POA 16,
Vd 10, Vl 10, Vs 4, Vc 4), the tuberal set on plate_038 to plate_044 (Nucleus
Anterior tuberal 10, Nucleus Lateral tuberal 10, Nucleus Posterior Tuberal 8,
Migrated posterior tuberal nucleus 4), then Rm on plate_051 to plate_057 (14).
A section assigned to any other plate has no regions to receive, so the tool
marks those plates and there is no reason to place landmarks on such a section.

The tuberal four arrived with the 2026-09 atlas, which also dropped the label
"Posterior tuberculum" that used to sit on those levels. Names are carried
exactly as the atlas writes them, long ones included - the curator groups and
orders by them, it does not parse them.

Affine below six points, thin-plate spline above
------------------------------------------------

Three or more pairs determine an **affine** by least squares. From **six** pairs
the tool switches to a **thin-plate spline**, and the header says which is live.

The gate is the whole argument. A TPS interpolates its landmarks *exactly*, so
its residual is zero by construction and tells you nothing; with 3-4 points it
would fit them perfectly and invent deformation everywhere else from almost no
evidence. From six well-spread points the interpolation is constrained by enough
real correspondences to be worth having - and an affine genuinely cannot follow
the local distortion that sectioning and mounting put into a slice, which is why
BigWarp and VisuAlign both use a TPS for exactly this task.

So: read the residual while it is an affine, and read the **overlay** once it is a
spline. `04e_register_elastix.py` remains available for an automatic B-spline
refinement once a plate assignment is trusted.

**The plate is shown in its ORIGINAL form, not reformatted.** The seeds are
recorded as fractions of the original plate, so using the original avoids
carrying them through the reformat's rotate-crop-pad-resize chain - a transform
that `04e` notes is not invertible from the index alone.

Three outputs, because there are three separable decisions
----------------------------------------------------------

`roi_plates.csv`     one row per section the operator has *touched*, with its
                     plate and a `status` of `registered` (3+ landmarks),
                     `plate_only` (plate chosen, not landmarked), `no_roi`
                     (deliberately marked as having nothing to measure) or
                     `excluded`.
`roi_landmarks.csv`  every landmark pair, with its residual - blank where there
                     is no fit to measure one against.
`roi_regions.csv`    one row per ROI actually placed, in the section's
                     reformatted frame, with the radius it was placed at.
                     Nothing here is positioned by the transform. `roi_kind`
                     separates the real ROIs from the background discs, which
                     carry identical columns because everything downstream
                     measures them identically.

**A plate assignment is a judgement in its own right.** An earlier version wrote
nothing for a section with fewer than three landmarks, which discarded exactly
the case where the operator had looked at a section and decided it was not worth
landmarking. `assigned` is set only by a real slider move or an explicit button -
never by merely selecting a section - so an untouched section still says
nothing.

Both channels live in ONE page. pERK and PCNA are separate physical sections cut
at different times and are curated independently - the channel selector next to
the sample selector switches between them, and every decision, count and export
row carries the channel it was made on. What they share is the tool and the atlas
plates, never a judgement: nothing about a pERK section reaches its PCNA partner.

The subset flags below are pERK subsets and say nothing about a PCNA section
(`--analysis-set` is defined by clipped-pixel censoring measured on the pERK
scans; `--worklist` is keyed on pERK uids). They therefore narrow the pERK side
only, and PCNA is offered on its own full set in the same page.

Run:  python 04l_roi_curator.py
      python 04l_roi_curator.py --rgb --worklist
      python 04l_roi_curator.py --animal LS45
"""

import sys
import argparse
import csv
import importlib.util
import json
import math
import os

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
import ls_config as LC  # noqa: E402
from ls_config import CONFIG, CONFIG_PATH  # noqa: E402
import ls_channels as CH  # noqa: E402
import ls_paths as LP  # noqa: E402
import ls_atlas as AT  # noqa: E402
import atlas_polygons as AP  # noqa: E402
# Whose curation a stored value holds - shared with 04q and app/import_exports.py,
# which read and write the same keys' files and must agree with the page.
import ls_curation as CU  # noqa: E402

# The markers this study measures, in declared order. ls_channels is the
# single source of that list; naming a fluorophore here would pin the stage
# to one study.
MARKERS = list(CH.marker_names(CONFIG))

# What the channel selector calls each marker.
#
# LEGACY_LABEL names a DISPLAY CONVENTION for this study, not a rule about what
# a fluorophore means. This study's paired layout declares its markers by
# fluorophore, so there is no channel table to read a friendlier name out of,
# and these are the two labels this page has always shown. Any other study
# either names its channels - a multiplex table typically carries the antibody
# name - or sees the marker's own name, which beats every marker being labelled
# "PCNA".
LEGACY_LABEL = {"AF568": "pERK", "AF488": "PCNA"}
# Derived the same way 04j_censor_clipped.py derives its LABEL: the study's own
# channel table where it declares one.
LABEL = {c.name: c.name for c in
         CH.markers(CH.parse((CONFIG.get("acquisition") or {}).get("channels")))}


def marker_label(marker):
    """The display name for one marker: study, then legacy, then the name."""
    return LABEL.get(marker) or LEGACY_LABEL.get(marker, marker)


# What colour each marker is drawn in, and the counterstain's own. THE SAME
# FUNCTIONS 04o_section_rgb CALLS: the Review pane and the section composites
# in the grid beside it show the same section, and until this existed they
# decided its colour separately - the pane from `p.marker === "AF568"`, the
# composites from the declared order - and disagreed for every study but one.
MARKER_COLOURS = CH.marker_colours(CONFIG)
NUCLEAR_COLOUR = CH.nuclear_colour(CONFIG)
# The same map in the operator's language, for the button that names it.
MARKER_COLOUR_NAMES = CH.marker_colour_names(CONFIG)

#: What the counterstain is CALLED. Its colour and its lift already came from
#: the config; its name was the literal "DAPI" in the Review pane's header, so
#: a study counterstaining with Hoechst was shown a header naming a dye it does
#: not use. Read from the channel table, which is where a multiplex study names
#: it. A paired study declares no table - each scan carries the counterstain
#: plus one marker - so there is nothing to read and "DAPI" stands, which is
#: also the name every `_DAPI.png` this pipeline writes already carries.
_NUCLEAR_CHANNEL = CH.nuclear(CH.parse((CONFIG.get("acquisition") or {}).get("channels")))
NUCLEAR_NAME = (_NUCLEAR_CHANNEL.name if _NUCLEAR_CHANNEL
                and _NUCLEAR_CHANNEL.name else "DAPI")


OUT_ROOT = CONFIG["out_root"]
REFORMAT_DIR = os.path.join(OUT_ROOT, "reformatted")
# Whose curation this page holds. localStorage is per ORIGIN, and every page opened
# from file:// in Chrome is one origin (so is everything serve_curators.py serves) -
# so every study's roi_curator.html reads and writes the same keys, and scene uids
# are built from subject names a pilot and a main study easily share.
STUDY_ID = CU.study_id(OUT_ROOT)

# The section-directory and index rule, borrowed rather than restated. ls_paths
# rather than 04a: this stage builds an HTML page and importing 04a would pull
# numpy, scipy and PIL in to answer a question about a filename.
NAMES = LP.for_config(CONFIG)
WORKLIST_CSV = os.path.join(REFORMAT_DIR, "roi_worklist.csv")


def analysis_set_csv(marker):
    """The analysis set 04j wrote for ONE marker.

    This was the module constant `perk_analysis_set.csv`, restated here from
    04j's LEGACY_ANALYSIS_SET with a comment in each file telling the reader
    about the other. 04j returns a legacy name for those two markers only and
    `<marker>_analysis_set.csv` for everybody else - so the literal named a
    file 04j would never have written for any other study, and `analysis_uids`
    now correctly lets MARKERS[0] through whatever it is called, which is what
    makes that reachable rather than theoretical.

    Per-marker rather than a constant because that is the shape of the
    question: every use site already has a marker in hand.

    ASKED OF ls_paths RATHER THAN OF 04j, for the reason 04m gives beside the
    same call: 04j imports 04a_reformat, 900 lines of numpy, scipy and PIL, and
    this stage builds an HTML page. The rule itself moved INTO ls_paths, beside
    the LEGACY table it was already half-written in, so 04j and this now read
    one copy instead of keeping two in step.
    """
    return NAMES.analysis_set_path(marker)

# Regions that cannot be told apart without knowing the rostrocaudal level.
# A telencephalic section is recognisable as telencephalon, but how far front or
# back it sits is not readable from the section itself - and these three ventral
# regions occupy overlapping positions across that range (Vd/Vv on plate_011 to
# plate_015, POA on plate_020 to plate_025). A seed landing on one of them is
# therefore evidence of "one of these three", not of that specific region.
#
# The atlas's own label is kept unchanged; the group is carried alongside it, so
# nothing is lost and pooling stays the reader's decision rather than being baked
# in here.
AMBIGUOUS_GROUPS = [("Vd", "Vv", "POA")]
REGION_GROUP = {r: "/".join(g) for g in AMBIGUOUS_GROUPS for r in g}
# The canonical reformatted frame every stored coordinate is expressed in. 04o may
# render the picture larger; the curator divides that back out on export.
SEC_GRID = 256

# COL_BAND / COL_SPAN group the seeds of a plate into the vertical strips the
# click-through order runs down; see the long note at their use in `main`. They
# live up here because the region hulls below reuse COL_SPAN, and a reader has
# to be able to see that the two thresholds are deliberately the same number
# rather than two guesses that happen to agree.
COL_BAND, COL_SPAN = 0.08, 0.12

# A region on one plate is not one blob, and hulling it as though it were is the
# mistake this constant exists to stop. These regions are BILATERAL: a hull over
# all of Dl's seeds spans the midline gap and draws a single band across the
# whole brain. `04f_exclusion_candidates.py` documents the identical failure for
# section solidity - "two bilaterally separated lobes ... its hull spans the
# midline gap" - and rejected the metric over it. So a region's seeds are SPLIT
# before they are hulled, and each part is hulled on its own.
#
# The split is a gap in x at COL_SPAN, reused rather than invented. That value is
# already tuned to sit between the two real scales: wide enough to hold a strip
# that drifts sideways as it descends (the left lateral arc on plate_013 runs
# x = 0.05 -> 0.13), narrow enough that a strip cannot chain across the midline
# and swallow its bilateral partner (plate_013's partners sit at 0.05 and 0.94).
# That is exactly the discrimination a lobe split needs.
#
# EVERY gap over the threshold cuts, not only the widest: nothing says a region
# has exactly two parts, and a midline structure like POA has one.
LOBE_GAP = COL_SPAN


def load_seeds(plate_dir=None):

    """Every plate's region seeds, numbered in the fixed click-through order.

    Factored out of `main` so the atlas region tracer (`04a5_atlas_regions.py`)
    reads them the same way the curator does. Seed 4 has to be the same seed in
    both tools and in every export, and a second copy of the ordering rule would
    be a second chance to disagree.
    """
    seeds = {}
    path = os.path.join(plate_dir or PLATE_DIR, "seeds.csv")
    with open(path, newline="", encoding="utf-8") as fh:
        for s in csv.DictReader(fh):
            seeds.setdefault(s["plate_id"], []).append(
                # TWO different uncertainties, and they are not the same thing.
                # `amb` is ours: Vd/Vv/POA cannot be told apart without knowing
                # the section's rostrocaudal level, so the group is shown instead
                # of a name the data cannot support. `unk` is the ATLAS's own -
                # eight seeds are labelled "Rm (Raphe) ??" in the source, and
                # dropping that flag here made the tool report them as settled.
                {"region": s["region"], "amb": REGION_GROUP.get(s["region"], ""),
                 "unk": 1 if s.get("is_unknown") == "1" else 0,
                 "xf": float(s["x_frac"]), "yf": float(s["y_frac"]),
                 "hex": s.get("colour_hex") or "#4da3ff"})
    for lst in seeds.values():
        number_seeds(lst)
    return seeds


def number_seeds(lst):
    """Set `n` on one plate's seeds, in place, in the click-through order.

    A FIXED order, decided here rather than in the page, so that "seed 4" is the
    same seed in the tool, in every export and in a re-run. seeds.csv is in
    extraction order, which is arbitrary and puts bilateral partners far apart.
    Reading order - down the plate, left to right within a row - can be followed
    by eye.

    The rows have to be FOUND, not rounded to a grid. Seeds on one visual row sit
    at y values ~0.001 apart (0.2329, 0.2338, 0.2347 on plate_013) while real
    rows are ~0.02 apart, so any fixed rounding either splits a row or merges
    two. Splitting a row is the damaging one: the x tiebreak never fires, and the
    order zigzags across the midline - 0.39, 0.05, 0.60, 0.94 - which is exactly
    the thing a fixed order is supposed to stop. So rows are grown greedily by
    gap, at a threshold between the two scales.

    Left to right every row, not serpentine. Serpentine is 14% less travel and
    costs more than it saves: these regions are bilateral, and alternating the
    direction flips which hemisphere the next seed is in on every row. COLUMNS,
    top to bottom, starting from the left. Not rows: these seeds run in vertical
    strips - a lateral arc down each side and a medial strip either side of the
    midline - and reading across rows cut every strip into fragments, so
    consecutive numbers landed on opposite sides of the brain. Down a column the
    next number is the next seed in the same structure.

    COL_BAND has to be wide enough to hold a strip that drifts sideways as it
    descends: the left lateral arc on plate_013 runs x = 0.05, 0.04, 0.07, 0.13
    while y goes 0.23 -> 0.50. At 0.02 that arc fragments into four one-seed
    "columns" (74% of all columns were single); at 0.08 plate_013 resolves into
    the six strips actually present and 22% are single. COL_SPAN caps a column's
    total width as well as the step between neighbours, so a strip drifting
    steadily sideways cannot chain across the midline and swallow its bilateral
    partner. Both live at module level, because `region_hulls` splits a region's
    lobes at that same COL_SPAN.
    """
    if not lst:
        return lst
    lst.sort(key=lambda s: (s["xf"], s["yf"]))
    cols, cur = [], [lst[0]]
    for s in lst[1:]:
        if (s["xf"] - cur[-1]["xf"] > COL_BAND
                or s["xf"] - cur[0]["xf"] > COL_SPAN):
            cols.append(cur); cur = [s]
        else:
            cur.append(s)
    cols.append(cur)
    lst[:] = [s for col in cols for s in sorted(col, key=lambda s: s["yf"])]
    for i, s in enumerate(lst, 1):
        s["n"] = i
    return lst


def convex_hull(pts):

    """Monotone-chain hull of (x, y) pairs, no repeated closing point.

    Written out rather than imported. This runs wherever the page is generated
    and is emitted as page data, and pulling scipy in for eight points would be
    the only reason this stage needed it at all.

    CONVEX, not concave, and that is a limit of the data rather than a
    preference: a lobe of Dl carries a handful of seeds, and a concave hull over
    three points is noise dressed as anatomy.

    Fewer than three points is not an error - Vs and Vc carry four seeds in the
    entire atlas - so a part of one or two comes back as itself and the page
    draws a dot or a segment. Collinear points collapse to the two ends for the
    same reason.
    """
    p = sorted(set((float(a), float(b)) for a, b in pts))
    if len(p) < 3:
        return [list(q) for q in p]

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for q in p:
        while len(lower) > 1 and cross(lower[-2], lower[-1], q) <= 0:
            lower.pop()
        lower.append(q)
    upper = []
    for q in reversed(p):
        while len(upper) > 1 and cross(upper[-2], upper[-1], q) <= 0:
            upper.pop()
        upper.append(q)
    return [list(q) for q in lower[:-1] + upper[:-1]]


def region_hulls(sd):
    """One hull per (region, part) on a plate, built from that plate's own seeds.

    This is what the atlas pane draws a region as, instead of leaving the reader
    to infer the region's extent from a scatter of numbered dots. `sd` is the
    plate's seed list, already numbered by the click-through order.

    Vertices are FRACTIONS of the plate image, exactly as the seeds are, so the
    plate can keep being shown in its original unreformatted form and nothing
    here needs a transform.
    """
    by_region = {}
    for s in sd:
        by_region.setdefault(s["region"], []).append(s)
    out = []
    for region in sorted(by_region):
        grp = sorted(by_region[region], key=lambda s: s["xf"])
        parts, cur = [], [grp[0]]
        for s in grp[1:]:
            if s["xf"] - cur[-1]["xf"] > LOBE_GAP:
                parts.append(cur)
                cur = [s]
            else:
                cur.append(s)
        parts.append(cur)
        for i, part in enumerate(parts, 1):
            out.append({
                "region": region,
                # Both uncertainties belong to the seeds, so a hull inherits
                # them rather than inventing its own. `amb` is the same for
                # every seed of a region by construction. `unk` is true if ANY
                # seed under the hull is one the atlas marked "??" - a part
                # holding an uncertain seed is not a settled part.
                "amb": part[0]["amb"],
                "unk": 1 if any(s["unk"] for s in part) else 0,
                "hex": part[0]["hex"],
                "part": i, "n_parts": len(parts),
                "seeds": sorted(s["n"] for s in part),
                "v": [[round(x, 6), round(y, 6)] for x, y in
                      convex_hull([(s["xf"], s["yf"]) for s in part])],
            })
    # THE ROI NUMBER. An ROI is an area, and this is its identity on the plate:
    # one number per region per lobe, carried by every seed under it, so the
    # atlas reads "ROI 1, ROI 2, ROI 3" instead of "seed 1 ... seed 30".
    #
    # Ordered by the group's LOWEST seed, which makes the ROI order the same
    # traversal `number_seeds` already establishes - down each column, columns
    # left to right - rather than a second, alphabetical one nobody asked for.
    # On plate_013 that runs Dl-left, Dm-left, Vd, Vv, Vl, then across the
    # midline and back to Dl-right.
    #
    # The seeds keep their own `n`. Only the DISPLAYED number becomes the ROI's,
    # because `P.seeds[n-1]` is how three places resolve a number back to a seed
    # and the operator's store holds 268 sections whose pairs carry the old
    # per-seed numbers. Renumbering the seeds themselves would re-label every one
    # of them through a scheme with a third as many values, and nothing would
    # error.
    out.sort(key=lambda h: min(h["seeds"]))
    for i, h in enumerate(out, 1):
        h["roi"] = i
    return out


def tag_rois(sd, hulls):
    """Write each seed's ROI number onto it, from the hulls just computed.

    Separate from `region_hulls` on purpose. Having the hull builder quietly
    mutate the list it was handed would mean a caller that only wanted the hulls
    changed the seeds as a side effect, and a caller that wanted the numbers had
    to know to call it first. Here the dependency is one visible line.
    """
    roi_of = {n: h["roi"] for h in hulls for n in h["seeds"]}
    for s in sd:
        s["roi"] = roi_of.get(s["n"], 0)
    return sd


def marker_paths(marker):
    """(index CSV, section directory NAME) for one marker.

    Delegated to ls_paths, which is where `04a_reformat` gets the same two
    answers - "mirroring 04a" is what the previous version's docstring
    claimed, and the version under it was `if marker == "AF568"`, which mirrors
    04a only for the one study whose second marker happens to be called AF488.

    The directory comes back as a bare NAME because the page resolves it
    relative to itself: these end up in an <img> src, not on a filesystem.
    """
    return (NAMES.path("index", marker), NAMES.basename("sections", marker))


def section_dirs():
    """{marker: directory name} for the page. See `__SECDIRS__` in the script.

    The page had this rule written into it twice, in JavaScript, as
    `p.marker === "AF568" ? "_AF568" : ""`. A generated page is the worst place
    for a study-specific literal: it is invisible to every Python test, the JS
    suites only see it if it lands in the largest <script> block, and a wrong
    directory shows up as an <img> that silently does not load.
    """
    return {m: marker_paths(m)[1] for m in MARKERS}


def rgb_dirs():
    """{marker: {"rgb", "thumb"}}: 04o's directory NAMES for each marker.

    Asked of ls_paths, not built as `section_dirs()[m] + "_rgb"` - which is
    what both this page and its Python side did. Under multiplex every marker's
    section directory is `sections`, so that gave every marker one composite
    directory, and the page showed whichever marker 04o had written there
    first under every marker's name.
    """
    return {m: {"rgb": NAMES.basename("sections_rgb", m),
                "thumb": NAMES.basename("sections_rgb_thumb", m)}
            for m in MARKERS}


#: How far the Review pane lifts the counterstain. Not a contrast preference:
#: in tissue the marker runs 4.0-4.6x brighter than DAPI across 24 sampled
#: sections, so at native scale the counterstain is swamped by the thing
#: sitting on top of it and the section reads as marker-only. The pipeline
#: measures the raw data; this is the viewing pane.
DAPI_LIFT = 4

#: The filters that do not depend on which marker is on screen.
FILTER_LUM_ALPHA = "revLumAlpha"
FILTER_DAPI_ONLY = "revDapiOnly"
FILTER_BLANK = "revBlank"
FILTER_FALLBACK = "revNoColour"

#: What a marker this study declared no colour for is drawn in. Grey says "no
#: colour set". Any real colour would be a marker silently borrowing another
#: marker's, which is the fault this whole model removes.
FALLBACK_COLOUR = CH.NAMED_COLOURS["grey"]

#: LUMINANCE -> ALPHA. Canvas compositing reads alpha, and a greyscale mask PNG
#: is opaque everywhere, so `source-in` kept the fill across the whole rectangle
#: rather than only where the mask was set. This matrix copies the red channel
#: into RGB and into A, which makes the stencil's alpha mean what its brightness
#: means. Written out rather than built by `_matrix` because its alpha row reads
#: input R, which no colour filter ever does.
LUM_ALPHA_MATRIX = "1 0 0 0 0  1 0 0 0 0  1 0 0 0 0  1 0 0 0 0"


def _num(value):
    """A matrix coefficient as short as it can be written without changing."""
    value = round(float(value), 4)
    return str(int(value)) if value == int(value) else f"{value:g}"


def _matrix(colour, nuclear, lift):
    """One feColorMatrix `values` string: 4x5, row-major, over (R,G,B,A,1)."""
    rows = [[colour[p], 0, lift * nuclear[p], 0, 0] for p in range(3)]
    rows.append([0, 0, 0, 1, 0])
    return "  ".join(" ".join(_num(v) for v in row) for row in rows)


def review_filters(markers, colours, nuclear, lift=DAPI_LIFT, name=None):
    """The Review pane's colour filters, as DATA rather than as HTML.

    The arithmetic is here, in Python, beside the composite's - one rule, so
    the picture in the grid and the picture in the pane cannot disagree, which
    is the whole point of this change. The page installs them; it does not
    compute them. Before this the pane chose between four hand-written filters
    on `p.marker === "AF568"`, so under any other study every marker rendered
    green while the composites beside them put the first marker in red.

    Returns {"byMarker":    {marker: {"dapi": id, "only": id}},
             "defs":        [{"id": id, "values": "<20 numbers>"}],
             "dapiOnly":    id, "blank": id, "lumAlpha": id,
             "fallback":    {"dapi": id, "only": id},
             "nuclearLabel": what the counterstain is called,
             "nuclearName": the counterstain's colour as a word,
             "lift":        how far it is lifted}

    The last three are for the pane's own header, which read "DAPI blue x4" as
    a literal. The colour and the lift were fixed first; the NAME was still
    hardcoded in the page, so a study counterstaining with Hoechst was told it
    was looking at DAPI. All three now come from the study, so the header can
    only be wrong if the config is.

    `fallback` is grey, for a marker this study declared no colour for - it is
    absent from `byMarker`, so the page resolves it with `byMarker[m] ||
    fallback`. Grey says "no colour set"; green would be the old bug back.

    WHICH INPUT COLUMN IS THE MARKER. Input R, always, in every filter. The
    overview composite is `np.dstack([m8, m8, d8])` (01_overviews.py), so R and
    G are literally the same array, and `_MARK.png` is greyscale - so R carries
    the marker in every source this pane loads and the coefficient on input G
    is always 0. It cannot be per-marker: a magenta marker needs the marker in
    output B, and input B is the counterstain in `_RGB.png`. The four
    hand-written filters took the second marker from input G, which was the
    same picture only because those two columns happen to be equal.

    WHY A PAIR PER CHANNEL rather than one filter with a toggle. Input B is the
    counterstain in `_RGB.png` and the MARKER AGAIN in `_MARK.png`. A single
    filter with the blue row switched off would still read input B for a marker
    that has blue in it, and on `_MARK.png` that is the marker arriving twice.
    So the marker-only variant zeroes the counterstain's whole contribution and
    never reads input B at all.

    Ids are indexed by the marker's position, not built from its name: a marker
    name may contain spaces and `+`, and these become HTML ids referenced from
    `url(#...)` in a CSS filter property.
    """
    dark = (0., 0., 0.)
    defs = [{"id": FILTER_LUM_ALPHA, "values": LUM_ALPHA_MATRIX}]
    by_marker = {}
    for i, marker in enumerate(markers):
        colour = colours.get(marker)
        if colour is None:
            # Measured, but this study never said what colour to draw it in.
            # No filter of its own, so the page falls through to `fallback`.
            continue
        pair = {"dapi": f"revMk{i}Dapi", "only": f"revMk{i}Only"}
        defs.append({"id": pair["dapi"],
                     "values": _matrix(colour, nuclear, lift)})
        defs.append({"id": pair["only"],
                     "values": _matrix(colour, dark, lift)})
        by_marker[marker] = pair

    fallback = {"dapi": FILTER_FALLBACK + "Dapi",
                "only": FILTER_FALLBACK + "Only"}
    defs.append({"id": fallback["dapi"],
                 "values": _matrix(FALLBACK_COLOUR, nuclear, lift)})
    defs.append({"id": fallback["only"],
                 "values": _matrix(FALLBACK_COLOUR, dark, lift)})

    # DAPI alone. `_DAPI.png` is greyscale, so R=G=B and only the blue row does
    # anything. Still lifted: the point of a toggle is to compare, and a
    # channel that changed brightness depending on what was next to it would
    # make that comparison a guess.
    defs.append({"id": FILTER_DAPI_ONLY, "values": _matrix(dark, nuclear, lift)})
    # Both channels off. The image still LOADS - blanked rather than removed -
    # because the overlay canvas takes its size from the base image, so a
    # missing one would take the artifact and censor layers down with it. This
    # way the masks can be read on their own against black.
    defs.append({"id": FILTER_BLANK, "values": _matrix(dark, dark, lift)})

    return {"byMarker": by_marker, "defs": defs,
            "dapiOnly": FILTER_DAPI_ONLY, "blank": FILTER_BLANK,
            "lumAlpha": FILTER_LUM_ALPHA, "fallback": fallback,
            "nuclearLabel": NUCLEAR_NAME if name is None else name,
            "nuclearName": CH.colour_name(nuclear), "lift": lift}


def analysis_uids(marker):
    """The first marker's sections that survived clipped-pixel censoring.

    THE FIRST DECLARED MARKER ONLY, and deliberately. The analysis set is
    defined by clipped-pixel censoring measured on that marker's own scans, so
    it says nothing about the other marker's sections; deriving a subset for
    them by following the pairing would make one channel's curation depend on
    the other's, and under `paired` they are separate physical sections cut at
    different times. Every other marker is curated on its own full set.

    `MARKERS[0]`, not `"AF568"`. The literal was right for the one study whose
    first marker happens to be called AF568 and refused for every other study's
    first marker instead.
    """
    if not MARKERS or marker != MARKERS[0]:
        first = MARKERS[0] if MARKERS else "the first declared marker"
        raise SystemExit(
            f"--analysis-set is a {marker_label(first)} subset (clipped-pixel "
            f"censoring is measured on that marker's scans) and does not "
            f"define one for {marker_label(marker)}.\n"
            f"Run {marker_label(marker)} without it: "
            f"python 04l_roi_curator.py --marker {marker}")
    with open(analysis_set_csv(marker), newline="", encoding="utf-8") as fh:
        perk = {r["scene_uid"] for r in csv.DictReader(fh) if r["in_analysis_set"] == "1"}
    return perk, len(perk), 0
# Which plate set to use, resolved through ls_atlas - the same call
# 04k_level_curator.py and 04e_register_elastix.py make, rather than each
# carrying its own copy of this lookup. The two sets reuse the same plate_NNN
# names for different images, so this must not be hard-coded in two places.
PLATE_SET = AT.set_name(CONFIG)
PLATE_DIR = AT.plate_dir(CONFIG)
CURATOR_HTML = os.path.join(REFORMAT_DIR, "roi_curator.html")
# Where exports are filed. Config so the app and this stage cannot disagree
# about it; one dated folder per export is created inside it.
EXPORT_DIR = LC.export_dir(CONFIG)
# Paired or multiplex. The page needs it to export a section outside its build
# under every marker it is curated for (see exportCsv).
LAYOUT = (CONFIG.get("acquisition") or {}).get("layout", CH.LAYOUT_MULTIPLEX)
PROVENANCE_CSV = os.path.join(REFORMAT_DIR, "section_provenance.csv")

# The Review mode needs one row per SCANNED section - 2,572, against the ~1,242
# the rest of the page carries - so the fields are chosen and the rows are
# emitted as ARRAYS under a shared header rather than as objects. Per-object
# keys would repeat 22 field names 2,572 times and roughly double the page for
# nothing. A separate JSON fetched at load is not an option: `fetch()` is
# refused under file://, which is how this page is often opened.
PROV_FIELDS = [
    "scene_uid", "animal", "marker", "slide", "section_order", "czi_file",
    "scene_index", "status", "decision", "exclusion_class", "decision_reason",
    "proposed_excluded", "tissue_area_mm2", "focus_score", "largest_mm2",
    "n_artifact_objects", "artifact_pct_of_tissue", "in_analysis_set",
    "censored_fraction_in_tissue", "censor_reason",
    "has_overview", "has_section", "has_section_rgb", "has_section_thumb",
    "has_mask", "has_censor", "has_tissue",
    "n_rois", "n_nuclei",
]

# Columns whose values repeat across thousands of rows. "manually excluded:
# tissue too damaged to measure" alone appears 789 times, and there are 222
# distinct CZI files across 2,572 sections. Each of these becomes an index into
# a per-column list. Measured: 773 KB -> 393 KB, which on a page that was 378 KB
# is the difference between a nuisance and a problem.
PROV_POOLED = ("animal", "marker", "slide", "czi_file", "status", "decision",
               "exclusion_class", "decision_reason", "censor_reason")


def load_provenance():
    """`04p_section_provenance.py`'s table, compacted for embedding.

    `{"f": fields, "p": {field: [distinct values]}, "r": [[values]...]}` - rows
    are arrays under a shared header, and pooled columns carry an index into `p`
    instead of the string. Objects per row would repeat 24 field names 2,572
    times; see PROV_POOLED for the rest.

    **The three image paths are NOT carried.** They are a fixed function of the
    uid, animal and marker, and at ~110 bytes each over 2,572 rows they were
    275 KB - a third of the table - to say something the page can derive. What
    IS carried is whether each file exists, which is the part that cannot be
    derived. The naming rule therefore lives in two places, here and in
    `revSrc()`; it is one line in each and it is stated in both.

    Empty when 04p has not been run: the Review button then explains itself and
    the rest of the page is unaffected.
    """
    if not os.path.exists(PROVENANCE_CSV):
        return {"f": PROV_FIELDS, "p": {}, "r": []}
    with open(PROVENANCE_CSV, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    for r in rows:
        r["has_overview"] = 1 if r.get("overview_img") else 0
        r["has_section"] = 1 if r.get("section_img") else 0
        # Separate from has_section on purpose: all 2,572 have a greyscale
        # section image, only the 1,506 reformatted ones have a composite.
        r["has_section_rgb"] = 1 if r.get("section_rgb") else 0
        r["has_section_thumb"] = 1 if r.get("section_thumb") else 0
        r["has_mask"] = 1 if r.get("mask_img") else 0
        # WHETHER the file exists, never which marker. 04j was AF568-only while
        # it read the 8-bit _MARK.png; reading the raw data lifted that and both
        # channels have censor masks now, so a marker test is already wrong.
        r["has_censor"] = 1 if r.get("censor_img") else 0
        r["has_tissue"] = 1 if r.get("tissue_img") else 0

    pool, index = {}, {}
    for k in PROV_POOLED:
        vals = sorted({r.get(k, "") for r in rows})
        pool[k] = vals
        index[k] = {v: i for i, v in enumerate(vals)}
    out = []
    for r in rows:
        out.append([index[k][r.get(k, "")] if k in index else r.get(k, "")
                    for k in PROV_FIELDS])
    return {"f": PROV_FIELDS, "p": pool, "r": out}

# THE GROUP KEY, and the only place in this pipeline before 06b that reads one.
# It exists for the Shotgun deck and reaches nothing else - not a count, not a
# threshold, not a curation decision. `blinding._note` in config.json forbids
# reading a group label this early; this is the declared exception to it, and it
# is opt-in: with `groups` absent or empty the page ships with the button
# disabled and no key in it. Everything else the curator does is unchanged
# whether the key is there or not.
#
# Dropped entirely rather than passed through empty-valued: an animal listed
# with a blank treatment is an animal nobody has assigned yet, and guessing at
# it would put a made-up group on a figure.
_GROUPS_RAW = CONFIG.get("groups", {}) or {}
GROUPS = {
    "order": [g for g in _GROUPS_RAW.get("order", []) if g],
    "by_animal": {a: g for a, g in (_GROUPS_RAW.get("by_animal", {}) or {}).items()
                  if g and not a.startswith("_")},
}

# The page template: HTML, CSS and the curator's JavaScript, filled by IO.fill
# in main(). It lived here as a 5,700-line string literal; it is a file of its
# own so an editor treats it as HTML and Python stops processing its
# backslashes. The file holds the string's EVALUATED value - every "\\" in the
# old literal is one backslash in the file - so the page is byte-identical.
# Read in text mode's default newline handling, NOT newline="": a Windows
# checkout may turn the file's LF into CRLF, and Python always read the literal
# with LF line ends whatever the .py's own were - this keeps that true.
with open(os.path.join(_HERE, "roi_curator_page.html"), encoding="utf-8") as _f:
    PAGE = _f.read()


def build_parser():
    """The parser, built separately so a test can read the marker default and
    the flag help off the object argparse will actually use.

    The help text is DERIVED for the same reason the choices are. Both subset
    flags narrow the first declared marker and leave every other marker on its
    own full set - that is a rule about declared order, not about pERK - and
    the help said "pERK" and "PCNA", which are this study's antibodies. An
    operator of another study reading `--analysis-set` was told the flag
    applied to a marker they do not have.
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--animal", default=None)
    _default = MARKERS[0] if MARKERS else None
    _first = marker_label(_default) if _default else "the first declared marker"
    _rest = "every other marker keeps its full set"
    ap.add_argument("--marker", choices=MARKERS, default=_default,
                    help="which channel the page OPENS on. Both are always loaded - "
                         "the selector next to the sample selector switches - so this "
                         "chooses the starting view, not what is in the page. "
                         f"Opens on {_default}.")
    _aset = (os.path.basename(analysis_set_csv(_default)) if _default
             else "the first marker's analysis set")
    ap.add_argument("--analysis-set", action="store_true",
                    help=f"narrow the {_first} side to "
                         f"{_aset}. Clipped-pixel "
                         f"censoring is measured on that marker's own scans, "
                         f"so it defines a subset for it alone; {_rest}")
    ap.add_argument("--worklist", nargs="?", const=WORKLIST_CSV, default=None,
                    metavar="CSV",
                    help=f"order the {_first} side by 04n_roi_worklist.py, so stopping "
                         "part way still leaves every animal and every level covered. "
                         f"Keyed on {_first} uids, so {_rest}")
    ap.add_argument("--tier", default=None,
                    help="with --worklist: only this tier (e.g. core)")
    ap.add_argument("--no-seed", action="store_true",
                    help="do not carry the app's curation into the page. The "
                         "page normally embeds whatever is in "
                         "out_root/curation so a browser copy opens with the "
                         "same work; this leaves it empty instead")
    ap.add_argument("--rgb", action="store_true",
                    help="show the two-colour composites from 04o_section_rgb.py "
                         "(DAPI blue + marker) instead of the greyscale DAPI the "
                         "geometry was computed on. Same frame either way.")
    ap.add_argument("--export-dir", default=EXPORT_DIR, metavar="DIR",
                    help="where exports are filed, one dated DD.MM.YYYY_HH.MM "
                         "folder per export. Shown in the page; honoured by the "
                         "app. A browser writes here only once the operator has "
                         "picked the folder with the page's own Folder... button, "
                         "because a web page cannot be handed a path")
    ap.add_argument("--use-auto-polygons", action="store_true",
                    help="Wullimann plate set only: also show polygons the program named "
                         "but nobody has reviewed. Off by default - the name is a guess "
                         "and it becomes a measurement key.")
    ap.add_argument("--out", default=CURATOR_HTML, metavar="HTML",
                    help="where to write the page (default: the live curator under "
                         "out_root). tests/run.sh points this at tests/build/ so a "
                         "test run never rewrites the page being curated in")
    return ap


def plate_rows(plate_dir=None, polygon_statuses=("reviewed",), mirror=None):
    """The plate array the page indexes into, in atlas order.

    TWO THINGS THAT USED TO BE WRONG HERE, both silently.

    The sort was `key=lambda p: p["plate_id"]` - a STRING sort, correct only
    because this atlas zero-pads to three digits. An atlas numbering
    `plate_1 .. plate_100` orders 1, 10, 100, 2, and every index stored against
    it means a different plate with no error anywhere. `ls_atlas.plate_order`
    is numeric-aware and falls back to text when the ids carry no number.

    And a plate whose image was absent was `continue`d - dropped from the array,
    shifting every later index by one. One missing PNG renumbered the back half
    of the atlas. It now keeps its slot and carries `missing: 1`, so the page
    can refuse to draw it and say why.

    `missing` means "no readable image", not "no file": `ls_atlas.fingerprints`
    returns `fp: None` both when `image_file` is absent AND when it is there
    but cannot currently be read - locked, mid-write, a permissions error. This
    function does not tell those apart (neither does `ls_atlas`, deliberately -
    see its GONE outcome), so nothing here or in the page may say "missing" to
    the operator; "no readable image" is accurate in both cases.

    POLYGON PLATES. Where the plate set carries `polygons.csv` (the Wullimann
    atlas, whose regions are outlines rather than seed dots) the outline IS the
    area, so it replaces the hull built from seeds: same numbering, colour and
    guided-mode behaviour, one synthetic seed per polygon. Only polygons whose
    status is in `polygon_statuses` are shown - by default just the reviewed
    ones, because an OCR-named polygon is a guess and the name becomes a
    measurement key. `mirror` reflects them about the plate midline; None takes
    `atlas_polygon_mirror` from the study, for atlases whose registry entry allows it.

    Calls `ls_atlas.fingerprints`, which maintains `<plate_dir>/fingerprints.csv`
    - the one sanctioned on-disk write under a plate set's directory. Building
    the page used to be read-only there; it no longer is, on a cold cache.
    """
    plate_dir = plate_dir or PLATE_DIR
    # The set name for the `img` URL below has to come from THIS directory,
    # not from the module-level PLATE_SET constant: a caller that passed an
    # override (a test, or a future caller pointed at a different set) would
    # otherwise get rows whose `img` points into the WRONG set's directory
    # while every other field was read from the right one.
    set_name = os.path.basename(os.path.normpath(plate_dir))
    with open(os.path.join(plate_dir, "plates.csv"), newline="",
              encoding="utf-8") as fh:
        plates = list(csv.DictReader(fh))
    seeds = load_seeds(plate_dir)
    polys = AP.read_polygons(os.path.join(plate_dir, "polygons.csv"))
    if mirror is None:
        mirror = bool(CONFIG.get("atlas_polygon_mirror")) and AT.entry(CONFIG).mirror_allowed
    prints = AT.fingerprints(plate_dir, plates)

    out = []
    for p in AT.plate_order(plates):
        pid = p["plate_id"]
        sd = seeds.get(pid, [])
        if polys.get(pid):
            mid = float(p["midline_frac"]) if mirror and p.get("midline_frac") else None
            sd, hulls = AP.plate_view(polys[pid], mid, polygon_statuses)
        else:
            # The ROI grouping, and the number every seed under it carries.
            hulls = region_hulls(sd)
            tag_rois(sd, hulls)
        pw, ph = int(p["px_w"]), int(p["px_h"])
        # How much room a label actually has on THIS plate: the median distance
        # from a seed to its nearest neighbour, as a fraction of the plate
        # diagonal. A fraction rather than pixels because the page has to turn it
        # into screen pixels against whatever size the pane currently is - bake
        # in pixels and the scaling goes stale the moment the window changes.
        nn = 0.0
        if len(sd) > 1:
            pts = [(x["xf"] * pw, x["yf"] * ph) for x in sd]
            diag = math.hypot(pw, ph)
            near = sorted(min(math.dist(a, b) for j, b in enumerate(pts) if j != i)
                          for i, a in enumerate(pts))
            nn = near[len(near) // 2] / diag
        fp = (prints.get(pid) or {}).get("fp")
        out.append({"id": pid, "nn": round(nn, 5),
                    # Original plate, NOT reformatted: the seeds are fractions
                    # of this image, so no transform chain is needed.
                    "img": f"../atlas/{set_name}/{p['image_file']}",
                    "image_file": p["image_file"],
                    "w": pw, "h": ph,
                    "fp": fp, "missing": 0 if fp else 1,
                    "labelled": int(bool(sd)), "seeds": sd,
                    # What a REGION is on this plate, as opposed to where its
                    # individual seeds are. One entry per region per lobe.
                    "hulls": hulls})
    return out


def main():
    args = build_parser().parse_args()
    if args.worklist and not os.path.exists(args.worklist):
        raise SystemExit(f"--worklist: {args.worklist} not found - run 04n_roi_worklist.py first")

    # Both channels go into one page. They are separate physical sections and
    # stay independently curated - this shares the TOOL, not the decisions - so
    # each channel is assembled on its own terms and tagged with its own subset.
    # Every section any marker's reformat_index carries, BEFORE the worklist /
    # analysis-set / animal narrowing: the sections the pipeline can still
    # place. 05a looks a section up in exactly these files (all markers'), so
    # a curated section outside this set would fail 05a's placement. See
    # CARRIED in the page.
    carried = set()

    def build(marker):
        index_csv, img_dir = marker_paths(marker)
        rgb_dir = rgb_dirs()[marker]["rgb"]
        with open(index_csv, newline="", encoding="utf-8") as fh:
            rows = [r for r in csv.DictReader(fh) if r["kind"] == "section"]
        carried.update(r["id"] for r in rows)
        before, subset = len(rows), "all"

        # MARKERS[0], not "AF568". Both of these read `== "AF568"`, so under
        # any other study neither flag narrowed anything and BOTH channels came
        # back on their full set - the operator asked for the worklist order,
        # got front-to-back, and was told nothing. The subset belongs to
        # whichever marker is declared first, which for this study IS AF568.
        first = MARKERS[0] if MARKERS else None
        if args.analysis_set and marker == first:
            keep, _n_first, _unpaired = analysis_uids(marker)
            rows = [r for r in rows if r["id"] in keep]
            # The tag the page shows for this side, taken from the file the
            # rows actually came from rather than named again. "perk" was the
            # third copy of a name only one study has.
            subset = os.path.splitext(os.path.basename(
                analysis_set_csv(marker)))[0]

        # The worklist is keyed on the first marker's uids, because that is the
        # channel being quantified. Reaching the other marker's side means
        # following the pairing, which the 30 unpaired sections do not survive
        # and which would make one channel's job depend on the other's. So it
        # narrows the first marker only; the rest are curated on their own full
        # set, by the same logic in the same tool.
        rank = None
        if args.worklist and marker == first:
            with open(args.worklist, newline="", encoding="utf-8") as fh:
                wl = [r for r in csv.DictReader(fh)
                      if not args.tier or r["tier"] == args.tier]
            rank = {r["scene_uid"]: int(r["rank"]) for r in wl}
            rows = [r for r in rows if r["id"] in rank]
            subset = "roi_worklist" + (":" + args.tier if args.tier else "")

        if args.animal:
            rows = [r for r in rows if r["animal"] == args.animal]
        rows.sort(key=lambda r: (rank[r["id"]],) if rank
                  else (r["animal"], int(r["section_order"] or 0)))

        # Per section, not per run: a channel can be part-built, and a page that
        # claimed a composite it does not have would show a broken image and
        # offer a DAPI toggle that does nothing. Whatever is missing falls back
        # to the greyscale the geometry was computed on.
        out = []
        for r in rows:
            has = args.rgb and os.path.exists(
                os.path.join(REFORMAT_DIR, rgb_dir, r["id"] + ".png"))
            out.append({"uid": r["id"], "animal": r["animal"], "m": marker,
                        "sub": subset, "rgb": bool(has),
                        "order": int(r["section_order"] or 0),
                        "img": (rgb_dir if has else img_dir) + "/" + r["id"] + ".png"})
        return out, subset, before

    per_marker, markers = {}, []
    for mk in MARKERS:
        got, subset, before = build(mk)
        per_marker[mk] = (got, subset, before)
        # `colour` is the word for what this marker is drawn in, from the
        # same map review_filters() builds the matrices from - so the page's
        # label and the page's pixels cannot say different things. None when
        # this study declared no colour for it; the pane says "no colour set"
        # and draws it grey rather than borrowing another marker's.
        markers.append({"id": mk, "label": marker_label(mk),
                        "n": len(got), "sub": subset,
                        "colour": MARKER_COLOUR_NAMES.get(mk)})
    data = [row for mk in MARKERS for row in per_marker[mk][0]]

    pl = plate_rows(polygon_statuses=("reviewed", "auto") if args.use_auto_polygons else ("reviewed",))
    gaps = [p for p in pl if p["missing"]]
    if gaps:
        # The id alone matches no file on this atlas - `plate_001`'s image is
        # `plate_001_p01.png`, the whole reason `ls_atlas._image_stem` exists -
        # so the filename and the directory are printed too, or an operator
        # checking the report against the folder would be looking for a path
        # that was never going to be there.
        named = ", ".join(f"{p['id']} ({p['image_file']})" for p in gaps[:8])
        print(f"  !! {len(gaps)} plate image(s) not readable in {PLATE_DIR}: "
              + named + (" ..." if len(gaps) > 8 else ""))
        print("     They keep their place in the array so no assignment moves.")

    # The curation the app has on file, carried into the page so a browser copy
    # opens with the same work rather than empty. --no-seed leaves it out, which
    # is what you want when handing the file to someone who should start clean.
    # The study's mark, made here once if it has none: see TOKEN in the page.
    token = CU.study_token(OUT_ROOT, create=True)

    seed = {}
    if not args.no_seed:
        seed_path = os.path.join(OUT_ROOT, "curation", "ls_roi_curator_v1.json")
        try:
            with open(seed_path, encoding="utf-8") as fh:
                stored = json.load(fh)
        except (OSError, ValueError):
            stored = {}
        # The file mirrors the page's key, so it is filed per study too: this study's
        # entry, with what is still unclaimed from before filing under it (this
        # file has only ever been this study's). The seed only fills what the
        # browser does not already hold, so nothing here overrides work.
        mine, old = CU.own(stored, STUDY_ID, token), CU.legacy(stored)
        moved = CU.carried_from(stored, STUDY_ID, token)
        if moved:
            print(f"  Curation from {moved} was carried over (this study's folder moved)")
        seed = {**(old if isinstance(old, dict) else {}),
                **(mine if isinstance(mine, dict) else {})}

    # The ROIs the operator chose to draw, if the app has one on file. Same rule
    # as the seed above: the browser's own copy wins, this only fills an empty one.
    roisel = None
    if not args.no_seed:
        try:
            with open(os.path.join(OUT_ROOT, "curation", "ls_roi_curator_v1_rois.json"),
                      encoding="utf-8") as fh:
                stored = json.load(fh)
            roisel = CU.own(stored, STUDY_ID, token)
            if roisel is None:
                roisel = CU.legacy(stored)
        except (OSError, ValueError):
            roisel = None

    # The ROIs the operator added (ADD ROI), the same way: this study's own, else
    # what predates filing. Only fills a browser that holds none.
    custom = None
    if not args.no_seed:
        try:
            with open(os.path.join(OUT_ROOT, "curation", "ls_roi_curator_v1_custom.json"),
                      encoding="utf-8") as fh:
                stored = json.load(fh)
            custom = CU.own(stored, STUDY_ID, token)
            if custom is None:
                custom = CU.legacy(stored)
        except (OSError, ValueError):
            custom = None

    # The study's atlas_scope, as the page wants it. Reported against what this
    # atlas really carries, so a typo or the wrong atlas shows at build time.
    scope_cfg = CONFIG.get("atlas_scope") or {}
    scope = {"existing": [n for n in (scope_cfg.get("existing_regions") or []) if isinstance(n, str)],
             "toAdd": [n for n in (scope_cfg.get("regions_to_add") or []) if isinstance(n, str)]}
    if scope["existing"] or scope["toAdd"]:
        carried = {h["region"] for p in pl for h in (p.get("hulls") or [])}
        miss_e = [n for n in scope["existing"] if n not in carried]
        found_a = [n for n in scope["toAdd"] if n in carried]
        print(f"  atlas_scope: {len(scope['existing']) - len(miss_e)}/{len(scope['existing'])} "
              f"existing regions found, {len(scope['toAdd']) - len(found_a)} wanted but absent")
        if miss_e:
            print("  !! existing_regions no plate carries: " + ", ".join(miss_e[:12])
                  + (" ..." if len(miss_e) > 12 else ""))
        if found_a:
            print("  !! regions_to_add this atlas already has (move to existing_regions): "
                  + ", ".join(found_a[:12]))

    page = IO.fill(PAGE, {
        "__ATLASSCOPE__": scope,
        "__SEED__": seed, "__ROISEL__": roisel, "__CUSTOM__": custom, "__PROV__": load_provenance(), "__DATA__": data,
        "__PLATES__": pl, "__PLATESET__": PLATE_SET, "__MARKERS__": markers, "__ACQLAYOUT__": LAYOUT, "__CARRIED__": sorted(carried),
        "__STUDY__": STUDY_ID, "__TOKEN__": token,
        # The page compares aspect ratios exactly as `ls_atlas.plate_status`
        # does, so it is handed THAT number rather than repeating it. A literal
        # 0.01 in the script is a section that verifies in the importer and not
        # in the page the moment anyone tunes the tolerance.
        "__ASPECTTOL__": AT.ASPECT_TOLERANCE,
        "__MARKER__": args.marker, "__SECDIRS__": section_dirs(),
        "__RGBDIRS__": rgb_dirs(),
        "__SECGRID__": SEC_GRID, "__GROUPS__": GROUPS,
        "__FILTERS__": review_filters(MARKERS, MARKER_COLOURS, NUCLEAR_COLOUR),
        "__EXPORTDIR__": args.export_dir},
        # The counterstain's name is the one value here that lands in HTML TEXT
        # rather than in the script: it is the label on the two toggle buttons.
        # Through the data argument it would arrive JSON-encoded, quote marks
        # and all, so `text=` HTML-escapes it instead - same single pass, see
        # ls_io.fill.
        text={"__NUCLEAR__": NUCLEAR_NAME})
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(page)

    lab = [p for p in pl if p["labelled"]]
    print(f"wrote {args.out}")
    # Loud, because a page that can lay itself out by treatment is a page that
    # is no longer blind, and that should never be discovered by accident.
    if GROUPS["by_animal"] and GROUPS["order"]:
        n = len(GROUPS["by_animal"])
        print(f"  UNBLINDED: group key loaded for {n} animals "
              f"({' / '.join(GROUPS['order'])}) - the Shotgun deck is laid out by "
              f"treatment. Nothing else in the page reads it.")
    else:
        print("  no group key in config.json - Shotgun is disabled in this page "
              "(fill in groups.order and groups.by_animal to enable it)")
    if seed:
        print(f"  carrying {len(seed)} curated sections into the page")
    for m in markers:
        got, _sub, before = per_marker[m["id"]]
        nrgb = sum(1 for d in got if d["rgb"])
        note = "" if m["sub"] == "all" else f"  [{m['sub']}]"
        print(f"  {m['label']:5} {m['n']:4} of {before} sections{note}"
              f"   {nrgb} with a colour composite")
    print(f"  {len(data)} sections in the page, {len(pl)} plates, "
          f"{sum(len(p['seeds']) for p in pl)} region seeds")
    print(f"  {len(lab)} plates carry seeds"
          + (f": {lab[0]['id']} .. {lab[-1]['id']}" if lab else " - no region can be placed yet"))

    # The lobe split is the one thing about the hulls that can be silently wrong,
    # so it is reported rather than assumed. A region that comes out in ONE part
    # on a plate where it is bilateral would be drawn as a band across the whole
    # brain - the failure `04f_exclusion_candidates.py` documents for solidity -
    # and the widest hull is what makes that visible at a glance. On the current
    # atlas: Dl and Dm split in two on all 17 of their plates, POA and the tuberal
    # regions stay in one at the midline, and the widest hull is 0.20 of a plate
    # against 0.90 without the split.
    hulls = [h for p in pl for h in p["hulls"]]
    if hulls:
        # key= on the width: two hulls of equal width (a mirrored polygon and its
        # original always are) would otherwise be compared as dicts, which raises
        # after the page has been written and makes the run exit non-zero.
        wide = max(((max(v[0] for v in h["v"]) - min(v[0] for v in h["v"]), h)
                    for h in hulls), key=lambda t: t[0])
        multi = sorted({h["region"] for h in hulls if h["n_parts"] > 1})
        one = sorted({h["region"] for h in hulls if h["n_parts"] == 1})
        print(f"  {len(hulls)} region hulls, widest {wide[0]:.2f} of a plate "
              f"({wide[1]['region']})")
        print(f"    split per lobe: {', '.join(multi) if multi else '(none)'}")
        print(f"    one part:       {', '.join(one) if one else '(none)'}")
        if wide[0] > 0.5:
            print(f"    WARNING: a hull spans more than half the plate. The lobe "
                  f"split at LOBE_GAP={LOBE_GAP} is not separating hemispheres "
                  f"on this plate set.")
    print()
    print("Scrub the plate slider until it matches, then click matching points -")
    print("section first, then plate. From three pairs there is a transform and a")
    print("per-landmark residual; from six it is a thin-plate spline. Nothing is")
    print("warped onto the section - the residual is the check.")
    print()
    print("Then press e for Region mode: click a region's shape on the plate to")
    print("say which one you are drawing, and click out its outline on the")
    print("section. A region drawn over its landmarks takes them over, so they")
    print("stay landmarks and stop being discs. h hides the hulls.")
    print()
    print("Only the plates listed above have regions to give. A section assigned")
    print("elsewhere gets no ROIs, so there is no reason to place landmarks on it -")
    print("the working subset selects itself.")
    print()
    print("Export writes roi_landmarks.csv (every pair, with its residual) and")
    print("roi_regions.csv (one row per ROI placed, at the radius it was placed")
    print("at - nothing positioned by the transform). Background discs share that")
    print("file and are told apart by roi_kind, because they are measured the")
    print("same way: what the detector finds in them is its false-positive rate.")
    print()
    print(f"Exports are filed under {args.export_dir}, one folder per export named")
    print("DD.MM.YYYY_HH.MM, with the same stamp on every file in it - so a second")
    print("export an hour later sits beside the first rather than over it.")
    print()
    print("In a browser that has the folder picker (Chrome, Edge) the page writes")
    print("there directly once you have pressed Folder... and chosen it; the choice")
    print("is remembered. Everywhere else - Firefox, Safari, and the app - the file")
    print("is downloaded with the stamp in its name and the host files it: the app")
    print("reads the stamp back off the name and rebuilds the same folder.")


if __name__ == "__main__":
    main()
