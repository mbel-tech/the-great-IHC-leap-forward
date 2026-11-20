"""Reagent controls, measured: pairing and the rows of results/reagent_controls.csv.

06a routes a reagent control's nuclei (per scene x marker) away from every
experimental output and hands them here, with the paired stained sections'
nuclei and every section's own positivity cut. Nothing is re-read and nothing is
re-derived: the cut is ls_overlay.section_cut, positivity is ls_overlay.is_positive.

Two parts per control x marker x region:

  A  the positivity rule's call where no specific signal can be: the control's
     nuclei against its OWN per-section cut (n_positive, pct_positive), beside
     the paired stained sections' pct_positive in that region.
  B  the level comparison, only within a pairing: the control's nuclear median
     over the stained one, and the control's nuclei scored against EACH paired
     stained section's own cut - median and min-max across those sections,
     never one pooled cut (the operator's choice). Empty under study_wide
     pairing, where the comparison crosses batches and measures the batch.

Which sections are controls is decided from config (ls_reagent_controls), never
from a file name or the manifest's display-only column. This reads no group key.
"""

import os
import statistics as st
from array import array

import ls_channels as CH
import ls_naming as NM
import ls_overlay as LO
import ls_reagent_controls as RC

#: The report, beside roi_measurements.csv.
REPORT_CSV = "reagent_controls.csv"

COLUMNS = ["scene_uid", "file", "kind", "marker", "region", "pairing",
           "n_nuclei", "n_positive", "pct_positive", "positive_per_mm2",
           "stained_pct_median", "stained_pct_min", "stained_pct_max",
           "specificity_margin",
           "control_nuclear_median", "stained_nuclear_median", "level_ratio",
           "pct_above_paired_cut", "pct_above_paired_cut_min",
           "pct_above_paired_cut_max", "note"]

PAIRINGS = ("operator", "same_file", "same_animal", "study_wide")

#: 06c's limitation text for a study with no no-primary reagent control, word for
#: word as 06c's docstring states it.
NO_CONTROL_TEXT = (
    "With no no-primary control and no tERK normaliser an absolute positivity rate "
    "is not defensible; the comparison between arms at matched levels is, and "
    "`detector_specificity.csv` carries the false-positive rate to quote with it.")

CONTROL_TEXT = (
    "This study has a no-primary reagent control. What the positivity rule calls on it, "
    "beside the paired stained sections, is on the \"Reagent controls\" sheet: read an "
    "absolute positivity rate against that rate, not as zero background. "
    "`detector_specificity.csv` still carries the background discs' false-positive rate.")

BATCH_NOTE = ("study-wide pairing: no stained section on the same file or animal, so "
              "the level comparison (B) would cross batches and measure the batch, "
              "not the reagent; B is left empty")


# ---------------------------------------------------------------- the study

def study_markers(config):
    """The markers a manifest scene row stands for under `multiplex`, else ()."""
    acq = (config or {}).get("acquisition") or {}
    if acq.get("layout", CH.LAYOUT_MULTIPLEX) == CH.LAYOUT_MULTIPLEX:
        return tuple(CH.marker_names(config))
    return ()


def study_lookup(config):
    """{(scene_uid, marker): entry} over the manifest, multiplex-aware."""
    return RC.manifest_lookup(config, study_markers(config))


def study_rows(config):
    """The manifest, one row per scene x marker, multiplex-aware."""
    return RC.manifest_rows(config, study_markers(config))


def drop_controls(rows, lookup):
    """`rows` (scene_uid + marker or marker_channel) less the reagent controls."""
    return [r for r in rows if not RC.is_control(
        lookup, r["scene_uid"], r.get("marker", r.get("marker_channel")))]


# ---------------------------------------------------------------- pairing

def _base(p):
    return os.path.basename(str(p or "").replace("\\", "/"))


def pairing_for(uid, marker, lookup, scene_rows, config):
    """(kind, stained uids) a reagent control is compared with, for one marker.

    In order: the operator's `reagent_controls.paired_with` (keyed by the
    control's uid or file; each target a stained uid or file), the stained
    scenes on the same file, the same animal's stained scenes of that marker,
    then every stained section of that marker (`study_wide`). A target that
    is itself a reagent control for the marker is never a pairing.
    """
    mine = [r for r in scene_rows if r["scene_uid"] == uid]
    file = _base(mine[0].get("file")) if mine else ""
    animal = mine[0].get("animal", "") if mine else ""
    stained = [r for r in scene_rows
               if r.get("marker_channel") == marker and r["scene_uid"] != uid
               and not RC.is_control(lookup, r["scene_uid"], marker)]

    def uids(rows):
        return sorted({r["scene_uid"] for r in rows}, key=NM.natural_key)

    pw = RC.entries(config)["paired_with"]
    targets = pw.get(uid) or (pw.get(file) if file else None) or []
    if targets:
        want = set(targets)
        got = uids(r for r in stained
                   if r["scene_uid"] in want or _base(r.get("file")) in want)
        if got:
            return "operator", got
    if file:
        got = uids(r for r in stained if _base(r.get("file")) == file)
        if got:
            return "same_file", got
    if animal:
        got = uids(r for r in stained if r.get("animal") == animal)
        if got:
            return "same_animal", got
    return "study_wide", uids(stained)


def pairings(lookup, scene_rows, config):
    """pairing_for every flagged (scene_uid, marker)."""
    return {(u, m): pairing_for(u, m, lookup, scene_rows, config) for u, m in lookup}


# ---------------------------------------------------------------- the rows

class _Vals:
    """One section's ROI nuclei in one region: intensities and censored flags."""
    __slots__ = ("v", "cen")

    def __init__(self):
        self.v, self.cen = array("d"), bytearray()

    def add(self, value, censored):
        self.v.append(float(value))
        self.cen.append(bool(censored))

    def usable(self):
        """Uncensored values: a censored nucleus is out of every intensity statistic."""
        return [v for v, c in zip(self.v, self.cen) if not c]

    def n_pos(self, cut):
        return sum(1 for v, c in zip(self.v, self.cen) if LO.is_positive(v, c, cut))


def _truthy(x):
    return x not in (False, None, "", "0", 0)


def _pct(k, n):
    return round(100.0 * k / n, 1)


def _spread(xs):
    """(median, min, max) rounded to 0.1, or three blanks."""
    if not xs:
        return "", "", ""
    return round(st.median(xs), 1), round(min(xs), 1), round(max(xs), 1)


def report_rows(control_nuclei, stained_nuclei, cuts, pairing, meta=None, areas=None):
    """One row per reagent control x marker x region, with COLUMNS.

    `control_nuclei`, `stained_nuclei`: iterables of dicts with scene_uid,
    marker, region, roi_kind, marker_median, censored, own (kept nuclei only:
    06a has already dropped artifact and off-tissue). Each is read once.
    `cuts`: (scene_uid, marker) -> cut, for controls and stained sections alike.
    `pairing`: (scene_uid, marker) -> (kind, stained uids) for every control.
    `meta`: (scene_uid, marker) -> {"file", "kind"}. `areas`: (scene_uid,
    marker, region) -> usable ROI area in mm2; it gives positive_per_mm2 and a
    row to a region whose discs held no nucleus.
    """
    meta, areas = meta or {}, areas or {}
    ctl, own_by = {}, {}
    for n in control_nuclei:
        key = (n["scene_uid"], n["marker"])
        own_by[n["marker"]] = own_by.get(n["marker"], False) or _truthy(n.get("own"))
        if n["roi_kind"] != "roi":
            continue
        ctl.setdefault(key + (n["region"],), _Vals()).add(n["marker_median"],
                                                          _truthy(n["censored"]))
    needed = {(s, m) for (u, m), (_k, us) in pairing.items() for s in us}
    stn = {}
    for n in stained_nuclei:
        own_by[n["marker"]] = own_by.get(n["marker"], False) or _truthy(n.get("own"))
        if n["roi_kind"] != "roi" or (n["scene_uid"], n["marker"]) not in needed:
            continue
        stn.setdefault((n["scene_uid"], n["marker"], n["region"]), _Vals()).add(
            n["marker_median"], _truthy(n["censored"]))

    regions = {}
    for u, m, rg in list(ctl) + list(areas):
        regions.setdefault((u, m), set()).add(rg)
    for key in pairing:
        regions.setdefault(key, set())

    out = []
    for (uid, mk) in sorted(regions, key=lambda k: (NM.natural_key(k[0]), k[1])):
        kind, paired = pairing.get((uid, mk), ("study_wide", []))
        info = meta.get((uid, mk), {})
        cut = cuts.get((uid, mk))
        own = own_by.get(mk, False)
        for rg in sorted(regions[(uid, mk)]):
            vals = ctl.get((uid, mk, rg)) or _Vals()
            n = len(vals.v)
            notes = []
            npos = vals.n_pos(cut) if cut is not None else ""
            if cut is None:
                notes.append(f"no own cut: fewer than {LO.CUT_MIN_NUCLEI} usable "
                             f"background-disc nuclei on this section, so A is blank")
            if own:
                notes.append("objects segmented on the marker itself; with no nuclear "
                             "counterstain there is no denominator, so every fraction "
                             "is withheld and positive_per_mm2 is reported")
            area = areas.get((uid, mk, rg))
            pct = _pct(npos, n) if (cut is not None and n and not own) else ""

            # A beside: each paired section's own pct in this region.
            measured = [s for s in paired if (s, mk) in cuts]
            spct = []
            for s in measured:
                sv = stn.get((s, mk, rg))
                if sv is not None and len(sv.v):
                    spct.append(_pct(sv.n_pos(cuts[(s, mk)]), len(sv.v)))
            if own:
                spct = []
            if paired and len(measured) < len(paired):
                notes.append(f"{len(paired) - len(measured)} of {len(paired)} paired "
                             f"stained sections have no cut yet (not measured, or "
                             f"fewer than {LO.CUT_MIN_NUCLEI} background nuclei)")
            if not paired:
                notes.append("no stained section of this marker to pair with")
            s_med, s_min, s_max = _spread(spct)

            row = dict.fromkeys(COLUMNS, "")
            row.update({
                "scene_uid": uid, "file": info.get("file", ""),
                "kind": info.get("kind", ""), "marker": mk, "region": rg,
                "pairing": kind, "n_nuclei": n, "n_positive": npos,
                "pct_positive": pct,
                "positive_per_mm2": (round(npos / area, 1)
                                     if (cut is not None and area) else ""),
                "stained_pct_median": s_med, "stained_pct_min": s_min,
                "stained_pct_max": s_max,
                "specificity_margin": (round(s_med - pct, 1)
                                       if (s_med != "" and pct != "") else ""),
            })

            if kind == "study_wide":
                notes.append(BATCH_NOTE)
            else:
                cu = vals.usable()
                pooled = [v for s in paired
                          for v in (stn.get((s, mk, rg)) or _Vals()).usable()]
                c_med = round(st.median(cu), 1) if cu else ""
                s_lev = round(st.median(pooled), 1) if pooled else ""
                row["control_nuclear_median"] = c_med
                row["stained_nuclear_median"] = s_lev
                row["level_ratio"] = (round(st.median(cu) / st.median(pooled), 4)
                                      if (cu and pooled and st.median(pooled) > 0) else "")
                if n and not own:
                    above = [_pct(vals.n_pos(cuts[(s, mk)]), n) for s in measured]
                    a_med, a_min, a_max = _spread(above)
                    row["pct_above_paired_cut"] = a_med
                    row["pct_above_paired_cut_min"] = a_min
                    row["pct_above_paired_cut_max"] = a_max
            row["note"] = "; ".join(notes)
            out.append(row)
    return out


# ---------------------------------------------------------------- the text

def has_no_primary(rows):
    return any((r.get("kind") or "") == "no_primary" for r in rows or ())


def limitation_text(rows):
    """06c's limitation text: the existing one unless a no_primary row exists."""
    return CONTROL_TEXT if has_no_primary(rows) else NO_CONTROL_TEXT


def sheet_header(rows):
    """The plain-language lines above the table on 06c's "Reagent controls" sheet."""
    return [
        "Reagent controls: sections stained without the primary antibody (no_primary), "
        "unstained, with an isotype IgG, or with a pre-adsorbed antibody. Their rows are "
        "kept out of every other sheet and output; this is where they are reported.",
        limitation_text(rows),
        "A: n_positive and pct_positive are what the positivity rule calls on the reagent "
        "control under its own per-section cut (median + 3 x 1.4826 x MAD of its own "
        "background-disc nuclei): the rule's false-positive rate where there is no "
        "specific signal. stained_pct_* are the paired stained sections' pct_positive in "
        "the same region (median, min, max); specificity_margin = stained_pct_median - "
        "pct_positive. For a marker segmented on its own channel the fractions are "
        "withheld and positive_per_mm2 (profiles over the usable ROI area, not "
        "Abercrombie-corrected) is reported.",
        "B: control_nuclear_median / stained_nuclear_median = level_ratio. "
        "pct_above_paired_cut scores the control's nuclei against EACH paired stained "
        "section's own cut and gives the median and min-max across those sections, "
        "never a pooled cut: how much of the positive call level the reagent control "
        "reaches. Left empty under study_wide pairing, because across batches the ratio "
        "measures the batch, not the reagent.",
        "pairing: operator (Set up), same_file, same_animal, or study_wide when no "
        "stained section shares the file or the animal.",
        "no_primary and unstained rows test the secondary antibody and autofluorescence; "
        "isotype and preadsorbed rows test the specificity of the primary antibody.",
        "A background disc is not a reagent control: the primary antibody is on that "
        "tissue, so detector_specificity.csv measures non-specific binding plus "
        "autofluorescence, not zero.",
    ]
