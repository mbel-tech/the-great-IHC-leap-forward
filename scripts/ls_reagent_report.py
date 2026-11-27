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

import hashlib
import json
import math
import os
import statistics as st
from array import array

import csv

import ls_channels as CH
import ls_naming as NM
import ls_overlay as LO
import ls_paths as LP
import ls_reagent_controls as RC

#: The report, beside roi_measurements.csv.
REPORT_CSV = "reagent_controls.csv"

COLUMNS = ["scene_uid", "file", "kind", "marker", "region", "pairing",
           "n_nuclei", "n_positive", "pct_positive", "positive_per_mm2",
           "stained_pct_median", "stained_pct_min", "stained_pct_max",
           "stained_positive_per_mm2_median", "stained_positive_per_mm2_min",
           "stained_positive_per_mm2_max",
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

#: The text for a study with a measured no-primary reagent control; {markers} is
#: the markers it covers (limitation_text).
CONTROL_TEXT = (
    "This study has a no-primary reagent control for {markers}. What the positivity rule "
    "calls on it, beside the paired stained sections, is on the \"Reagent controls\" "
    "sheet: read an absolute positivity rate of {markers} against that rate, not as zero "
    "background. `detector_specificity.csv` still carries the background discs' "
    "false-positive rate. For any marker not named here there is no such control, and an "
    "absolute positivity rate for it is not defensible; the comparison between arms at "
    "matched levels is.")

#: The note on a confirmed reagent control 06a has nothing measured for yet.
NOT_MEASURED = "confirmed reagent control, not measured yet"
#: The note on the one row of a measured control with no ROI region to report.
NO_ROI_REGION = ("measured, but no ROI region to report: no nucleus and no usable area "
                 "in its ROIs (none placed, or none segmented)")

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


def control_set_hash(pairs):
    """A marker's reagent-control set, hashed: the sorted distinct (scene_uid,
    kind) pairs, a missing kind as "". 06k records it per marker in af_gate.csv
    from the confirmed lookup; 06c works it out again from reagent_controls.csv
    (one row or more per control, every confirmed one, measured or not) and
    blanks the flags when the two differ."""
    items = sorted({(str(u), str(k or "")) for u, k in pairs})
    return hashlib.sha1(json.dumps(items).encode("utf-8")).hexdigest()[:16]


def drop_controls(rows, lookup):
    """`rows` (scene_uid + marker or marker_channel) less the reagent controls."""
    return [r for r in rows if not RC.is_control(
        lookup, r["scene_uid"], r.get("marker", r.get("marker_channel")))]


# ---------------------------------------------------------------- pairing

def _base(p):
    return os.path.basename(str(p or "").replace("\\", "/"))


def boxed_sections(config):
    """{(scene_uid, marker)} that 05a placed boxes on, from each marker's
    roi_boxes file under out_root; None when 05a has written none yet."""
    names = LP.for_config(config)
    out, seen = set(), False
    for m in CH.marker_names(config or {}):
        path = names.read("roi_boxes", m, announce=lambda *_a, **_k: None)
        if not path or not os.path.exists(path):
            continue
        seen = True
        try:
            with open(path, newline="", encoding="utf-8") as fh:
                out |= {(r["scene_uid"], m) for r in csv.DictReader(fh) if r.get("scene_uid")}
        except (OSError, KeyError, csv.Error, UnicodeDecodeError):
            continue
    return out if seen else None


def pairing_for(uid, marker, lookup, scene_rows, config, boxed=None):
    """(kind, stained uids) a reagent control is compared with, for one marker.

    In order: the operator's `reagent_controls.paired_with` (keyed by the
    control's uid or file; each target a stained uid or file), the stained
    scenes on the same file, the same animal's stained scenes of that marker,
    then every stained section of that marker (`study_wide`). A target that
    is itself a reagent control for the marker is never a pairing.

    `boxed`: {(scene_uid, marker)} 05a placed boxes on (boxed_sections, or
    06a's own). The default tiers consider only those: on a real study most
    sections never get ROIs, and a tier holding only sections that will never
    have a cut left A's stained columns and all of B blank for good. With no
    05a geometry for this marker at all (None, or none of its sections) the
    manifest's sections are used, as before. The operator's choice is kept as
    chosen.
    """
    mine = [r for r in scene_rows if r["scene_uid"] == uid]
    file = _base(mine[0].get("file")) if mine else ""
    animal = mine[0].get("animal", "") if mine else ""
    every = [r for r in scene_rows
             if r.get("marker_channel") == marker and r["scene_uid"] != uid
             and not RC.is_control(lookup, r["scene_uid"], marker)]
    stained = every
    if boxed and any(m == marker for _u, m in boxed):
        stained = [r for r in every if (r["scene_uid"], marker) in boxed]

    def uids(rows):
        return sorted({r["scene_uid"] for r in rows}, key=NM.natural_key)

    pw = RC.entries(config)["paired_with"]
    targets = pw.get(uid) or (pw.get(file) if file else None) or []
    if targets:
        want = set(targets)
        got = uids(r for r in every
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


def pairings(lookup, scene_rows, config, boxed=None):
    """pairing_for every flagged (scene_uid, marker)."""
    return {(u, m): pairing_for(u, m, lookup, scene_rows, config, boxed)
            for u, m in lookup}


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


def report_rows(control_nuclei, stained_nuclei, cuts, pairing, meta=None, areas=None,
                factors=None, stained_areas=None):
    """One row per reagent control x marker x region, with COLUMNS.

    `control_nuclei`, `stained_nuclei`: iterables of dicts with scene_uid,
    marker, region, roi_kind, marker_median, censored, own (kept nuclei only:
    06a has already dropped artifact and off-tissue). Each is read once.
    `cuts`: (scene_uid, marker) -> cut, for controls and stained sections alike.
    `pairing`: (scene_uid, marker) -> (kind, stained uids) for every control.
    `meta`: (scene_uid, marker) -> {"file", "kind"}. `areas`: (scene_uid,
    marker, region) -> usable ROI area in mm2; it gives positive_per_mm2 and a
    row to a region whose discs held no nucleus. `factors`: (marker, region) ->
    the Abercrombie factor 06a applies to that marker and region's positive
    density (1.0 where it withholds the correction), so positive_per_mm2 reads
    like 06a's positive_density_per_mm2. A missing key is 1.0, with a note.
    `stained_areas`: the same as `areas` for the stained sections; it gives
    stained_positive_per_mm2_* (each paired section's positive density in the
    region, by the same factor).

    `specificity_margin` is stained minus control in percentage points, or -
    for a marker segmented on its own channel, which has no fraction - in
    positive nuclei per mm2, and the note then says so.
    """
    stained_areas = stained_areas or {}
    meta, areas = meta or {}, areas or {}
    factors = factors if factors is not None else {}
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
        if not regions[(uid, mk)]:
            # Measured with nothing in its ROIs: still one row, so a confirmed
            # control never just disappears (06c checks 06k's control set
            # against this file's sections and kinds).
            row = dict.fromkeys(COLUMNS, "")
            row.update({"scene_uid": uid, "file": info.get("file", ""),
                        "kind": info.get("kind", ""), "marker": mk, "pairing": kind,
                        "note": NO_ROI_REGION})
            out.append(row)
            continue
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
            ab = factors.get((mk, rg))
            if ab is None:
                ab = 1.0
                if cut is not None and area:
                    notes.append("positive_per_mm2 is not Abercrombie-corrected: no "
                                 "stained nuclei of this marker and region to measure "
                                 "h from yet")
            pct = _pct(npos, n) if (cut is not None and n and not own) else ""

            # A beside: each paired section's own pct in this region.
            measured = [s for s in paired if (s, mk) in cuts]
            spct = []
            for s in measured:
                sv = stn.get((s, mk, rg))
                if sv is not None and len(sv.v):
                    spct.append(_pct(sv.n_pos(cuts[(s, mk)]), len(sv.v)))
            sdens = []
            for s in measured:
                s_area = stained_areas.get((s, mk, rg))
                if s_area:
                    sv = stn.get((s, mk, rg)) or _Vals()
                    sdens.append(sv.n_pos(cuts[(s, mk)]) * ab / s_area)
            if own:
                spct = []
            if paired and len(measured) < len(paired):
                notes.append(f"{len(paired) - len(measured)} of {len(paired)} paired "
                             f"stained sections have no cut yet (not measured, or "
                             f"fewer than {LO.CUT_MIN_NUCLEI} background nuclei)")
            if not paired:
                notes.append("no stained section of this marker to pair with")
            s_med, s_min, s_max = _spread(spct)
            d_med, d_min, d_max = _spread(sdens)
            dens = round(npos * ab / area, 1) if (cut is not None and area) else ""
            if own:
                margin = round(d_med - dens, 1) if (d_med != "" and dens != "") else ""
                notes.append("specificity_margin is in positive nuclei per mm2 "
                             "(stained_positive_per_mm2_median - positive_per_mm2), "
                             "not percentage points")
            else:
                margin = round(s_med - pct, 1) if (s_med != "" and pct != "") else ""

            row = dict.fromkeys(COLUMNS, "")
            row.update({
                "scene_uid": uid, "file": info.get("file", ""),
                "kind": info.get("kind", ""), "marker": mk, "region": rg,
                "pairing": kind, "n_nuclei": n, "n_positive": npos,
                "pct_positive": pct,
                "positive_per_mm2": dens,
                "stained_pct_median": s_med, "stained_pct_min": s_min,
                "stained_pct_max": s_max,
                "stained_positive_per_mm2_median": d_med,
                "stained_positive_per_mm2_min": d_min,
                "stained_positive_per_mm2_max": d_max,
                "specificity_margin": margin,
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


def not_measured_rows(keys, lookup, files=None, reasons=None):
    """One row per confirmed reagent control (scene_uid, marker) with nothing
    measured: every measure blank, the note saying so and why, when known.
    A confirmed control never just disappears from the report."""
    files, reasons = files or {}, reasons or {}
    out = []
    for uid, mk in keys:
        row = dict.fromkeys(COLUMNS, "")
        why = reasons.get((uid, mk))
        row.update({"scene_uid": uid, "file": files.get(uid, ""),
                    "kind": (lookup.get((uid, mk)) or {}).get("kind", ""), "marker": mk,
                    "note": NOT_MEASURED + (": " + why if why else "")})
        out.append(row)
    return out


def row_order(row):
    """The report's row order: section (natural), marker, region."""
    return (NM.natural_key(row["scene_uid"]), row["marker"], row["region"])


# ---------------------------------------------------------------- the k check

#: The k check, beside reagent_controls.csv (06a writes it; Task 3's apply reads it).
CUT_CHECK_CSV = "reagent_cut_check.csv"

#: The multipliers the cut is checked at.
K_GRID = (2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0)

CUT_CHECK_COLUMNS = ["marker", "k", "n_control_sections", "n_control_nuclei",
                     "control_fp", "control_fp_lo", "control_fp_hi", "disc_fp",
                     "disc_fp_n", "meets_target", "suggested", "note"]

#: The note on every row of a marker whose controls meet the target at no k.
NO_K_MEETS = "no k on the grid meets the target"

NO_CUT_NOTE = ("left out, fewer than {n} usable background nuclei for a cut of its "
               "own: {uids}")
NO_CONTROL_RATE = "no no_primary control with a cut of its own and ROI nuclei: no rate"
OWN_NOTE = ("segmented on the marker's own channel: there is no count of all cells, "
            "so no false-positive rate (06a withholds it too) and no suggestion")
#: The fewest nuclei a rate is given from (positivity.fp_min_nuclei's default).
FP_MIN_NUCLEI = 50
FEW_CONTROL = ("{n} reagent-control nuclei, fewer than the minimum {min}: no rate and "
               "no suggested k")
FEW_DISC = ("{n} stained background-disc nuclei left out in turn, fewer than the "
            "minimum {min}: no disc rate")
NO_DISC_RATE = (f"no stained section with two background discs of "
                f">={LO.CUT_MIN_NUCLEI} usable nuclei: no disc rate")


def wilson(k, n, z=1.96):
    """The Wilson score interval for k of n, clipped to [0, 1]."""
    if n <= 0:
        raise ValueError("wilson needs n > 0")
    p = k / n
    z2 = z * z
    den = 1.0 + z2 / n
    centre = (p + z2 / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / den
    return max(0.0, centre - half), min(1.0, centre + half)


def _no_primary(c):
    """Only an entry that says it is no_primary: a missing kind is not one."""
    return c.get("kind") == "no_primary"


def cut_check_rows(controls, stained_discs, fp_target, ks=K_GRID,
                   fp_min_nuclei=FP_MIN_NUCLEI):
    """results/reagent_cut_check.csv's rows: per marker x k, CUT_CHECK_COLUMNS.

    `controls`: (scene_uid, marker) -> {"bg": usable background values, "roi":
    [(median, censored)] of its ROI nuclei, "kind": "no_primary"}. An entry
    whose "kind" is not no_primary (or is missing) is ignored; one with
    "own": True is a marker segmented on its own channel, which gets rows
    with no rate. `stained_discs`: (scene_uid, marker) -> that STAINED
    section's background discs, each a list of (median, censored) pairs or
    None (ls_overlay.leave_one_disc_out).

    control_fp: each control section's ROI nuclei against its OWN cut at k
    (ls_overlay.section_cut over its own background), pooled over sections,
    with a Wilson 95% interval. disc_fp: leave-one-disc-out at k over the
    stained sections, pooled. suggested = 1 on the smallest k that meets
    fp_target. A blank is a rate that cannot be given, never a 0: with fewer
    than `fp_min_nuclei` control nuclei (positivity.fp_min_nuclei) there is no
    control rate and no suggestion, and with fewer disc nuclei no disc rate.
    """
    by_marker = {}
    for (uid, mk), c in controls.items():
        if _no_primary(c):
            by_marker.setdefault(mk, []).append((uid, c))
    rows = []
    for mk in sorted(by_marker):
        ctl = sorted(by_marker[mk], key=lambda x: NM.natural_key(x[0]))
        own = any(c.get("own") for _u, c in ctl)
        with_cut = [(list(c.get("bg") or ()), list(c.get("roi") or ())) for _u, c in ctl
                    if LO.background_stats(list(c.get("bg") or ())) is not None]
        no_cut = [u for u, c in ctl
                  if LO.background_stats(list(c.get("bg") or ())) is None]
        discs = [d for (_u, m), d in sorted(stained_discs.items()) if m == mk]
        n = sum(len(roi) for _bg, roi in with_cut)
        notes = []
        if no_cut:
            notes.append(NO_CUT_NOTE.format(n=LO.CUT_MIN_NUCLEI, uids=", ".join(no_cut)))
        if own:
            notes.append(OWN_NOTE)
        elif not n:
            notes.append(NO_CONTROL_RATE)
        elif n < fp_min_nuclei:
            notes.append(FEW_CONTROL.format(n=n, min=fp_min_nuclei))
        m_rows = []
        for k in ks:
            row = dict.fromkeys(CUT_CHECK_COLUMNS, "")
            row.update(marker=mk, k=float(k), n_control_sections=len(with_cut),
                       n_control_nuclei=n)
            if n and n >= fp_min_nuclei and not own:
                fp = 0
                for bg, roi in with_cut:
                    cut = LO.section_cut(bg, k)
                    fp += sum(1 for v, c in roi if LO.is_positive(v, c, cut))
                lo, hi = wilson(fp, n)
                row.update(control_fp=round(fp / n, 4), control_fp_lo=round(lo, 4),
                           control_fp_hi=round(hi, 4),
                           meets_target=int(fp / n <= fp_target), suggested=0)
            dfp = dn = 0
            for d in discs:
                got = LO.leave_one_disc_out(d, k)
                if got:
                    dfp += got[0]
                    dn += got[1]
            if dn and dn >= fp_min_nuclei:
                row.update(disc_fp=round(dfp / dn, 4), disc_fp_n=dn)
            m_rows.append(row)
            row["_dn"] = dn
        rated = [r for r in m_rows if r["meets_target"] != ""]
        meeting = [r for r in rated if r["meets_target"] == 1]
        if meeting:
            meeting[0]["suggested"] = 1
        elif rated:
            notes.append(NO_K_MEETS)
        for r in m_rows:
            dn = r.pop("_dn")
            why = ([] if r["disc_fp"] != "" else
                   [FEW_DISC.format(n=dn, min=fp_min_nuclei)] if dn else [NO_DISC_RATE])
            r["note"] = "; ".join(notes + why)
        rows.extend(m_rows)
    return rows


def agreement(row):
    """Whether the stained sections' disc rate falls inside the controls'
    interval on one cut-check row, in words; None when either is blank."""
    d, lo, hi = (row.get(c) for c in ("disc_fp", "control_fp_lo", "control_fp_hi"))
    if "" in (d, lo, hi) or None in (d, lo, hi):
        return None
    d, lo, hi = float(d), float(lo), float(hi)
    if d < lo:
        return "discs understate"
    if d > hi:
        return "discs overstate"
    return "discs agree with controls"


# ---------------------------------------------------------------- the text

def _has_a(r):
    """Whether a report row carries part A: a positivity call on the control."""
    return any(str(r.get(c) if r.get(c) is not None else "").strip() != ""
               for c in ("pct_positive", "positive_per_mm2"))


def covered_markers(rows):
    """The markers with a no_primary row that has a part-A value, in first-seen order.
    A row for a control not measured yet (no A) covers nothing."""
    out = []
    for r in rows or ():
        if (r.get("kind") or "") == "no_primary" and _has_a(r):
            m = r.get("marker") or ""
            if m not in out:
                out.append(m)
    return out


def has_no_primary(rows):
    return bool(covered_markers(rows))


def limitation_text(rows):
    """06c's limitation text: the existing one unless a no_primary row has a
    part-A value, and then a pointer to the sheet that names the markers it
    covers (any other marker keeps the existing statement's substance)."""
    markers = [m for m in covered_markers(rows) if m] or None
    if not has_no_primary(rows):
        return NO_CONTROL_TEXT
    return CONTROL_TEXT.format(markers=", ".join(markers) if markers else "a marker")


def cut_rule_text(ks=None):
    """How a section's own cut is formed, with the k in force: `ks` is
    {marker: k} (ls_overlay.cut_k); without it, the rule and its default."""
    here = (", here " + ", ".join(f"{m} {float(k)}" for m, k in sorted(ks.items()))
            if ks else f", {LO.CUT_K} unless the study sets it")
    return ("median + k x 1.4826 x MAD of its own background-disc nuclei, "
            "k = positivity.cut_k" + here)


def sheet_header(rows, ks=None):
    """The plain-language lines above the table on 06c's "Reagent controls" sheet.
    `ks`: {marker: the k in force} (ls_overlay.cut_k), stated in the text."""
    return [
        "Reagent controls: sections stained without the primary antibody (no_primary), "
        "unstained, with an isotype IgG, or with a pre-adsorbed antibody. Their rows are "
        "kept out of every other sheet and output; this is where they are reported.",
        limitation_text(rows),
        "A: n_positive and pct_positive are what the positivity rule calls on the reagent "
        "control under its own per-section cut (" + cut_rule_text(ks) + "): the rule's "
        "false-positive rate where there is no "
        "specific signal. stained_pct_* are the paired stained sections' pct_positive in "
        "the same region (median, min, max); specificity_margin = stained_pct_median - "
        "pct_positive, in percentage points. positive_per_mm2 and "
        "stained_positive_per_mm2_* are the same comparison as densities (positive "
        "nuclei per mm2 of usable ROI area, with the Abercrombie factor 06a gives that "
        "marker and region, as positive_density_per_mm2). For a marker segmented on "
        "its own channel the fractions are withheld, and specificity_margin is "
        "stained_positive_per_mm2_median - positive_per_mm2, in per mm2 (the note "
        "says so).",
        "B: control_nuclear_median / stained_nuclear_median = level_ratio. "
        "pct_above_paired_cut scores the control's nuclei against EACH paired stained "
        "section's own cut and gives the median and min-max across those sections, "
        "never a pooled cut: how much of the positive call level the reagent control "
        "reaches. Left empty under study_wide pairing, because across batches the ratio "
        "measures the batch, not the reagent.",
        "pairing: operator (Set up), same_file, same_animal, or study_wide when no "
        "stained section shares the file or the animal.",
        "no_primary rows test the secondary antibody (with autofluorescence); unstained "
        "rows test autofluorescence only, since no antibody was applied; isotype and "
        "preadsorbed rows test the specificity of the primary antibody.",
        f"A row whose note starts \"{NOT_MEASURED}\" is a confirmed reagent control "
        "with nothing measured for that marker yet; its measures are blank and the note "
        "says why.",
        "A background disc is not a reagent control: the primary antibody is on that "
        "tissue, so detector_specificity.csv measures non-specific binding plus "
        "autofluorescence, not zero.",
    ]


# ---------------------------------------------------------------- the corrected rate

#: The corrected rate, beside roi_measurements.csv (06a writes it; 06c shows it).
ROI_CORRECTED_CSV = "roi_corrected.csv"

ROI_CORRECTED_COLUMNS = ["scene_uid", "marker", "region", "n_nuclei", "n_positive",
                         "frac_positive", "frac_positive_corrected",
                         "frac_positive_corrected_lo", "frac_positive_corrected_hi",
                         "positive_density_per_mm2", "positive_density_corrected",
                         "positive_density_corrected_lo", "positive_density_corrected_hi",
                         "fp_rate", "fp_rate_n", "fp_rate_source", "correction_note"]

#: The Monte-Carlo interval's draws and seed.
DRAWS = 4000
SEED = 20261121

#: The tiers a stained section's controls are found by, best first. Never study-wide.
CORRECTION_TIERS = ("operator", "same_file", "same_animal")

NO_PAIR_SOURCE = "none: no same-slide or same-animal control"
#: The tier a stained section gets when the operator pinned it only to
#: no_primary controls not measured yet (06a's corrected_report), and its note.
PAIRED_UNMEASURED = "operator_unmeasured"
PAIRED_UNMEASURED_NOTE = "paired control not measured yet"
AT_OR_BELOW = "at or below the reagent control's false-positive rate"
DENSITY_AT_OR_BELOW = ("at or below the reagent control's positive density: no "
                       "density correction")
NO_CORR_CUT = ("left out, no cut of its own (fewer than {n} usable background "
               "nuclei): {uids}")
NO_STAINED_CUT = (f"no positivity cut on this section (fewer than {LO.CUT_MIN_NUCLEI} "
                  f"usable background-disc nuclei): no rate to correct")


def controls_for(uid, marker, lookup, scene_rows, config, boxed=None, cache=None):
    """(tier, control uids): the no_primary reagent controls whose readings
    correct a stained section's rate, for one marker. pairing_for's tiers
    turned round, from the stained section's side, first that has any:

      operator     controls whose `reagent_controls.paired_with` (keyed by the
                   control's uid or file) names this section or its file;
      same_file    controls on this section's file;
      same_animal  controls of this section's animal.

    A control whose operator pin takes effect for this marker (pairing_for
    gives it "operator": `paired_with` under its uid or file resolves to a
    stained section of the marker) is used ONLY for those sections: it is
    dropped from the same_file and same_animal tiers of every other section.
    A pin that resolves to nothing for this marker (another marker's
    section, a stale name) is ignored, as pairing_for ignores it, so the two
    files always agree. `cache`: a dict the caller keeps across calls with
    the same lookup, rows, config and boxed, holding each control's
    pairing_for so it is worked out once.

    Study-wide pairing is never used: with none of these the answer is
    ("none", []). Only `no_primary` controls; isotype, preadsorbed and
    unstained never. `boxed`: {(scene_uid, marker)} 05a placed boxes on; as in
    pairing_for, the default tiers consider only those when 05a has boxed any
    section of this marker. The operator's choice is kept as chosen.
    """
    info = {}
    for r in scene_rows:
        info.setdefault(r["scene_uid"], (_base(r.get("file")), r.get("animal", "")))
    file, animal = info.get(uid, ("", ""))
    cands = sorted({cu for (cu, mk), e in lookup.items()
                    if mk == marker and cu != uid and _no_primary(e or {})},
                   key=NM.natural_key)
    # The operator tier is pairing_for's own: a pin counts for this marker only
    # when it resolves to a stained section of this marker there (a pin to
    # another marker's section, or a stale one, falls through as it does).
    cache = {} if cache is None else cache
    for cu in cands:
        if (cu, marker) not in cache:
            cache[(cu, marker)] = pairing_for(cu, marker, lookup, scene_rows, config, boxed)
    pf = {cu: cache[(cu, marker)] for cu in cands}
    got = [cu for cu in cands if pf[cu][0] == "operator" and uid in pf[cu][1]]
    if got:
        return "operator", got
    # A pin that takes effect is exclusive: such a control pairs with nothing else.
    cands = [cu for cu in cands if pf[cu][0] != "operator"]
    if boxed and any(m == marker for _u, m in boxed):
        cands = [cu for cu in cands if (cu, marker) in boxed]
    if file:
        got = [cu for cu in cands if info.get(cu, ("", ""))[0] == file]
        if got:
            return "same_file", got
    if animal:
        got = [cu for cu in cands if info.get(cu, ("", ""))[1] == animal]
        if got:
            return "same_animal", got
    return "none", []


def _rng(seed):
    import numpy as np
    return np.random.default_rng(seed)


def rogan_gladen(n_pos, n, f_pos, f_n, draws=DRAWS, seed=SEED):
    """The Rogan-Gladen correction of a positive rate for a false-positive
    rate, assuming sensitivity 1 (nothing truly positive is missed):
    corrected = (p - f) / (1 - f), p = n_pos / n, f = f_pos / f_n.

    The 95% interval propagates Jeffreys posteriors, Beta(k + 0.5, n - k + 0.5),
    for both p and f: `draws` Monte-Carlo draws with a fixed `seed`, the
    2.5-97.5 percentiles of the corrected draws, clipped to [0, 1]. Where
    every stained nucleus is positive the upper end is 1 (the Jeffreys
    interval's boundary convention), so the interval holds the point.

    {"corrected", "lo", "hi", "note"}: floats at full precision (the caller
    rounds), or three blanks with a note - never 0 for a number that cannot
    be given.
    """
    out = {"corrected": "", "lo": "", "hi": "", "note": ""}
    if not n or not f_n:
        out["note"] = ("no stained nuclei" if not n else "no reagent-control nuclei")
        return out
    p, f = n_pos / n, f_pos / f_n
    if p <= f:
        out["note"] = AT_OR_BELOW
        return out
    import numpy as np
    rng = _rng(seed)
    pd = rng.beta(n_pos + 0.5, n - n_pos + 0.5, draws)
    fd = rng.beta(f_pos + 0.5, f_n - f_pos + 0.5, draws)
    cd = np.clip((pd - fd) / (1.0 - fd), 0.0, 1.0)
    lo, hi = (float(x) for x in np.percentile(cd, [2.5, 97.5]))
    if n_pos == n:
        hi = 1.0
    out.update(corrected=(p - f) / (1.0 - f), lo=lo, hi=hi)
    return out


def density_correction(pos, area_mm2, f_pos, f_area_mm2, draws=DRAWS, seed=SEED,
                       factor=1.0, f_factor=None):
    """A positive density less the reagent control's, for a marker segmented
    on its own channel (no denominator, so no fraction to correct):
    corrected = d - d_f, d = pos x factor / area_mm2 and d_f = f_pos x
    f_factor / f_area_mm2. `factor` is the Abercrombie factor 06a gives the
    marker and region (positive_density_per_mm2); `f_factor` the control's,
    `factor` when not given.

    The 95% interval propagates Poisson rates: Gamma(k + 0.5) draws for each
    count over its area, `draws` with a fixed `seed`, 2.5-97.5 percentiles,
    the lower end clipped at 0. Floats at full precision. Blank with a note
    when d <= d_f, or with no area on either side.
    """
    out = {"corrected": "", "lo": "", "hi": "", "note": ""}
    if not area_mm2 or not f_area_mm2:
        out["note"] = "no usable ROI area to give a density from"
        return out
    if f_factor is None:
        f_factor = factor
    d, df = pos * factor / area_mm2, f_pos * f_factor / f_area_mm2
    if d <= df:
        out["note"] = DENSITY_AT_OR_BELOW
        return out
    import numpy as np
    rng = _rng(seed)
    dd = rng.gamma(pos + 0.5, 1.0, draws) * factor / area_mm2
    fd = rng.gamma(f_pos + 0.5, 1.0, draws) * f_factor / f_area_mm2
    lo, hi = (float(x) for x in np.percentile(np.clip(dd - fd, 0.0, None), [2.5, 97.5]))
    out.update(corrected=d - df, lo=lo, hi=hi)
    return out


def _pool_regions(ctl, uids, mk, cuts):
    """{region: (positives, nuclei)} over the controls' ROI nuclei, each
    against its own cut."""
    out = {}
    for u in uids:
        cut = cuts[(u, mk)]
        for rg, vals in ctl.get((u, mk), {}).items():
            k, n = out.get(rg, (0, 0))
            out[rg] = (k + vals.n_pos(cut), n + len(vals.v))
    return out


def _r(x, nd):
    return round(x, nd) if x != "" else ""


def corrected_rows(stained, control_nuclei, cuts, control_sets,
                   fp_min_nuclei=FP_MIN_NUCLEI, control_areas=None,
                   draws=DRAWS, seed=SEED, control_factors=None):
    """results/roi_corrected.csv's rows, ROI_CORRECTED_COLUMNS, one per
    stained (section, marker, region).

    `stained`: (scene_uid, marker, region) -> {"n": ROI nuclei, "pos":
    positives ("" with no cut), "area": usable ROI mm2, "own": segmented on
    its own channel, "pos_ab": sum over its ROI rows of n_positive x that
    row's Abercrombie factor (so pos_ab / area is the pooled
    positive_density_per_mm2), "ab": a factor to fall back on}, pooled over
    the section's ROIs in that region. `control_nuclei`: 06a's control nuclei
    (scene_uid, marker, region, roi_kind, marker_median, censored). `cuts`:
    (control uid, marker) -> its own cut. `control_sets`: (scene_uid, marker)
    -> controls_for(...). `control_areas`: (control uid, marker, region) ->
    usable ROI mm2, for the density correction. `control_factors`: (marker,
    region) -> the Abercrombie factor a control's density there is given
    (06a's rc_factors, as reagent_controls.csv's positive_per_mm2); missing,
    the stained factor.

    Nuclear markers: f is pooled over the paired controls' ROI nuclei in the
    same region, each against its own cut. Under `fp_min_nuclei` nuclei in
    the region f is pooled over all of those controls' regions, and the
    source says "all regions"; under it still, there is no rate (blank, with
    the count in the note).

    A marker segmented on its own channel has no fraction: its positive
    density (positive_density_per_mm2) is corrected by the controls' instead
    (density_correction), with no minimum object count - a good control has
    few objects, and the Poisson interval carries the uncertainty. It needs
    control ROI area: none in the region falls back to all of those controls'
    regions, and the source says so. A control with no cut is left out
    either way, and the source says so.
    """
    control_areas = control_areas or {}
    control_factors = control_factors or {}
    ctl = {}
    for r in control_nuclei:
        if r["roi_kind"] != "roi":
            continue
        ctl.setdefault((r["scene_uid"], r["marker"]), {}).setdefault(
            r["region"], _Vals()).add(r["marker_median"], _truthy(r["censored"]))
    out = []
    for (uid, mk, rg) in sorted(stained, key=lambda k: (NM.natural_key(k[0]), k[1], k[2])):
        s = stained[(uid, mk, rg)]
        n, pos, own = s["n"], s["pos"], bool(s.get("own"))
        area = s.get("area") or 0.0
        pos_ab = s.get("pos_ab")
        if pos_ab is None and pos != "":
            pos_ab = pos * (s.get("ab") or 1.0)
        row = dict.fromkeys(ROI_CORRECTED_COLUMNS, "")
        row.update(scene_uid=uid, marker=mk, region=rg, n_nuclei=n, n_positive=pos,
                   frac_positive=(round(pos / n, 4) if (pos != "" and n and not own) else ""))
        if own and pos != "" and area:
            row["positive_density_per_mm2"] = round(pos_ab / area, 1)
        notes = []
        if own:
            notes.append("segmented on the marker's own channel: no count of all cells, "
                         "so no fraction and no false-positive rate; the positive density "
                         "is corrected instead")
        tier, uids = control_sets.get((uid, mk), ("none", []))
        if tier == PAIRED_UNMEASURED:
            # Pinned by the operator, only to controls with nothing measured:
            # blank until they are, never a fallback tier's rate instead.
            row["fp_rate_source"] = "operator: " + ", ".join(uids) + " (not measured yet)"
            notes.append(PAIRED_UNMEASURED_NOTE)
            row["correction_note"] = "; ".join(notes)
            out.append(row)
            continue
        if tier == "none" or not uids:
            row["fp_rate_source"] = NO_PAIR_SOURCE
            row["correction_note"] = "; ".join(notes)
            out.append(row)
            continue
        used = [u for u in uids if (u, mk) in cuts]
        left = [u for u in uids if (u, mk) not in cuts]
        src = f"{tier}: " + (", ".join(used) if used else "no paired control has a cut")
        tail = ([NO_CORR_CUT.format(n=LO.CUT_MIN_NUCLEI, uids=", ".join(left))]
                if left else [])
        if not used:
            row["fp_rate_source"] = "; ".join([src] + tail)
            notes.append("no paired no_primary control has a cut of its own: no rate")
            row["correction_note"] = "; ".join(notes)
            out.append(row)
            continue
        by_rg = _pool_regions(ctl, used, mk, cuts)

        def source():
            return src + " (" + tail[0] + ")" + "".join("; " + t for t in tail[1:])

        if own:
            areas = {}
            for (u, m, r2), a in control_areas.items():
                if m == mk and u in used:
                    areas[r2] = areas.get(r2, 0.0) + a
            regions = [rg]
            if areas.get(rg, 0.0) > 0:
                tail.insert(0, rg)
            else:
                regions = sorted(set(areas) | set(by_rg))
                tail.insert(0, f"all regions: no control ROI area in {rg}")
            row["fp_rate_source"] = source()
            f_area = sum(areas.get(r2, 0.0) for r2 in regions)
            f_pos = sum(by_rg.get(r2, (0, 0))[0] for r2 in regions)
            row["fp_rate_n"] = sum(by_rg.get(r2, (0, 0))[1] for r2 in regions)
            s_fac = (pos_ab / pos) if (pos not in ("", 0)) else (s.get("ab") or 1.0)
            f_ab = sum(by_rg.get(r2, (0, 0))[0] * control_factors.get((mk, r2), s_fac)
                       for r2 in regions)
            f_fac = (f_ab / f_pos) if f_pos else control_factors.get((mk, rg), s_fac)
            if pos == "":
                notes.append(NO_STAINED_CUT)
            elif not f_area:
                notes.append("no control ROI area in any region: no density correction")
            else:
                dc = density_correction(pos, area, f_pos, f_area, draws, seed,
                                        factor=s_fac, f_factor=f_fac)
                row.update(positive_density_corrected=_r(dc["corrected"], 1),
                           positive_density_corrected_lo=_r(dc["lo"], 1),
                           positive_density_corrected_hi=_r(dc["hi"], 1))
                notes.append(f"control positive density {round(f_ab / f_area, 1)} per mm2"
                             + (": " + dc["note"] if dc["note"] else ""))
            row["correction_note"] = "; ".join(notes)
            out.append(row)
            continue

        f_pos, f_n = by_rg.get(rg, (0, 0))
        if f_n < fp_min_nuclei:
            here = f_n
            f_pos = sum(k for k, _n in by_rg.values())
            f_n = sum(n_ for _k, n_ in by_rg.values())
            tail.insert(0, f"all regions: {here} nuclei in {rg}, fewer than "
                           f"{fp_min_nuclei}")
        else:
            tail.insert(0, rg)
        row["fp_rate_source"] = source()
        row["fp_rate_n"] = f_n
        if f_n < fp_min_nuclei:
            notes.append(FEW_CONTROL.format(n=f_n, min=fp_min_nuclei).replace(
                "no rate and no suggested k", "no rate and no correction"))
            row["correction_note"] = "; ".join(notes)
            out.append(row)
            continue
        row["fp_rate"] = round(f_pos / f_n, 4)
        if pos == "":
            notes.append(NO_STAINED_CUT)
        else:
            rg_out = rogan_gladen(pos, n, f_pos, f_n, draws, seed)
            row.update({c: _r(rg_out[k], 4)
                        for c, k in (("frac_positive_corrected", "corrected"),
                                     ("frac_positive_corrected_lo", "lo"),
                                     ("frac_positive_corrected_hi", "hi"))})
            if rg_out["note"]:
                notes.append(rg_out["note"])
        row["correction_note"] = "; ".join(notes)
        out.append(row)
    return out


def corrected_sheet_header():
    """The lines above 06c's corrected-rate table."""
    return [
        "Corrected positive rate (provisional until validated on a study with known "
        "controls), per stained section, marker and region, from results/roi_corrected.csv. "
        "frac_positive is the raw pooled rate (n_positive / n_nuclei over the section's ROIs "
        "in the region); frac_positive_corrected = (frac_positive - fp_rate) / (1 - fp_rate) "
        "(Rogan-Gladen), with a 95% interval (_lo, _hi) from Jeffreys posteriors for both "
        "rates, 4000 Monte-Carlo draws.",
        "It assumes sensitivity 1: every truly positive nucleus is called positive. It "
        "removes the control's false positives and adds back nothing missed, so it is a "
        "lower-bound adjustment, a lower bound on the true rate if positives are missed.",
        "fp_rate is the paired no-primary reagent controls' rate in the same region, each "
        "control's ROI nuclei against its own cut; fp_rate_source names the pairing "
        "(operator, same_file or same_animal, never study-wide) and the controls, and says "
        "\"all regions\" when the region had fewer than positivity.fp_min_nuclei control "
        "nuclei. A blank is a number that cannot be given (correction_note says why), never "
        "0. For a marker segmented on its own channel the fractions are blank and "
        "positive_density_per_mm2 (Abercrombie-corrected positive nuclei per mm2, as "
        "positive_density_per_mm2 in roi_measurements.csv) is corrected instead: "
        "positive_density_corrected = it less the paired controls' positive density, with a "
        "95% interval from Poisson rates (_lo, _hi). That needs no minimum object count, only "
        "control ROI area (all the controls' regions when the region has none). For other "
        "markers the density columns are blank.",
    ]
