"""Stage 6c - the analysis table as a spreadsheet, buildable mid-run.

Reads `results/roi_measurements.csv` from **06a**, which owns every per-ROI
number - the positivity cut, the Abercrombie factor, the disc area. This stage
groups and formats; it does not re-derive. It used to, and the two
implementations had silently drifted: 06a computes Abercrombie's h per
(marker, region) as config declares, the copy here computed it per
(animal, region), and nothing compared them.

Still buildable at ANY point while `05c_detect_rois.py` is working - run 06a
first, which is seconds, and this reports how much of the job that is.
`06e_refresh_loop.py` does exactly that on each cycle. Combined with 05c's
balanced ordering - which alternates treatment and control section by section -
a partial run is a real comparison rather than a head start for one arm.

The requested sheet is `by_roi`: one row per ROI per sample.

    n_nuclei                 nuclei counted in that region for that animal
    ROI                      the atlas region, e.g. Dm, Dl, Vv
    sample                   animal ID
    treatment                control / exercise
    total_tissue_area_mm2    summed area of the discs those nuclei came from

**`cells_per_mm2` is corrected, unless the last column says it is not.**
`abercrombie_withheld_reason` is blank wherever the Abercrombie correction was
applied, and carries 06a's reason wherever it was not - objects that are not
nucleus-shaped, for which N = n * T/(T+h) does not hold, or a study that
switched the correction off. The factor is 1.0 in those rows, so
`cells_per_mm2` equals `profiles_per_mm2` there and the two columns can be read
side by side. The reason is CARRIED from 06a, not re-derived: a factor of 1.0
on its own cannot be told apart from a correction that came out at 1.0.

**A marker segmented on its own channel has no denominator.** PCNA on the LS
slides: the DAPI plane is noise, so nuclei are found on the marker itself and
`n_nuclei` is "objects the marker lit up", not a count of all cells. For such a
marker `frac_positive` and `cells_per_mm2` are blank and
`denominator_withheld_reason` says why; `n_positive` and
`positive_cells_per_mm2` are what it can honestly report. The reason is CARRIED
from 06a, as the Abercrombie one is.

**Positivity sits alongside the count, not instead of it.** `n_positive`,
`frac_positive` and `positive_cells_per_mm2` come from 06a's per-section cut.
They are blank where a section carried fewer than 5 background nuclei and no cut
could be formed - blank rather than 0, which would read as "looked and found
none". With no no-primary control and no tERK normaliser an absolute positivity
rate is not defensible; the comparison between arms at matched levels is, and
`detector_specificity.csv` carries the false-positive rate to quote with it.
A study with a confirmed no-primary reagent control gets a pointer to the
"Reagent controls" sheet in place of that statement, printed and on the sheet
(ls_reagent_report.limitation_text); without one it is unchanged.

**Reagent controls** (no primary, isotype, unstained ...) are not in any sheet
above: 06a routes their rows to `results/reagent_controls.csv`, and when that
file has rows they are the "Reagent controls" sheet, under a plain-language
header. Their boxes are left out of `planned` and `coverage` too, by the same
config lookup 06a uses (ls_reagent_controls), so an animal whose only sections
are controls is not reported as unmeasured. When 06a also wrote
`results/reagent_cut_check.csv` (the reagent k check), the same sheet carries it
below the controls' table as a "Cut check" block, labelled provisional: the k
each marker's controls suggest. It is applied only from the app's Results page.

**The corrected rate.** When 06a wrote `results/roi_corrected.csv` (a
no-primary reagent control is measured), its rows are the "corrected_rate"
sheet: per stained section, marker and region, the raw pooled `frac_positive`
with `frac_positive_corrected` and its 95% interval right beside it, under a
header that states the sensitivity-1 assumption (the correction is a
lower-bound adjustment) and that it is provisional; for a marker segmented on
its own channel, positive_density_per_mm2 beside positive_density_corrected
instead. It is per section, the
grain the operator chose, so it is its own sheet rather than columns on
`by_roi`, which is per animal; nothing is pooled to the animal here. The
other sheets are unchanged, with or without it.

**The autofluorescence flag.** When 06k wrote `results/af_scores.csv` and
`results/af_scored_sections.csv`, an "af_flag" sheet: per sample, marker and
region of a scored marker, by_roi's raw `frac_positive` with
`frac_positive_without_flagged` right beside it - (n_positive - n_flagged) /
(n_nuclei - n_flagged) - under a header that says provisional and states the
validation criterion. A row any of whose sections 06k did not score is blank
with a note, never the raw rate passed off as "none flagged". No count on any
other sheet changes.

**What "total tissue area" means here.** It is the area of the ROI discs
themselves, mapped onto the slide and summed - not the whole section, and not a
mask-derived tissue fraction. The operator places discs inside tissue, so the
two are the same thing up to the odd disc clipping an edge; saying which one is
meant matters more than the difference. Density is n_nuclei / that area, and the
column is there so it can be recomputed rather than trusted.

A disc is a CIRCLE on the curator's 256 px frame and an ELLIPSE on the slide -
the reformat resizes a hand-drawn scan box to a square grid - so the area comes
from the two semi-axes 05a measured, never from pi*r^2 in curator pixels. The
axis ratio runs 0.66 to 1.42 across these sections.

Three more sheets, because the first one cannot be checked from inside itself:
`by_disc` keeps every individual ROI placement, `by_section` shows how much of
each animal is measured so far, and `coverage` says what is still missing.

Run:  python 06c_excel_dataset.py
      python 06c_excel_dataset.py --out somewhere.xlsx
"""

import sys
import argparse
import collections
import csv
import importlib.util
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("_g5", os.path.join(_HERE, "05a_roi_geometry.py"))
G5 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(G5)

_spec6b = importlib.util.spec_from_file_location(
    "_g6b", os.path.join(_HERE, "06b_join_sampling.py"))
G6B = importlib.util.module_from_spec(_spec6b)
_spec6b.loader.exec_module(G6B)

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
import ls_naming as NM  # noqa: E402
import ls_reagent_report as RR  # noqa: E402
import ls_overlay as LO  # noqa: E402
OUT_ROOT = G5.OUT_ROOT
RESULTS = os.path.join(OUT_ROOT, "results")
NUCLEI_CSV = os.path.join(RESULTS, "roi_nuclei.csv")
MEAS_CSV = os.path.join(RESULTS, "roi_measurements.csv")
XLSX = os.path.join(RESULTS, "roi_dataset.xlsx")

def animal_environment(meta_csv=None):
    """animal -> sea / brackish, from 06b's results/animal_metadata.csv.

    Joined by animal, so it follows exactly the sheet, ID column, matching rule
    and `--xlsx` 06b joined the groups by. It used to re-read the DEFAULT
    workbook path itself, keep only all-digit sheet IDs and prefix "LS" - so a
    study whose workbook was passed with --xlsx, or whose IDs are "LS22" or
    "22b", got no environment, and "07" became an animal "LS07" that might not
    be the one 06b matched.

    The column is the one carried from the sheet whose header starts "enviro"
    (ls_sampling.COLUMN_PREFIX, which also catches the live sheet's
    "enviroment"). Absent file or absent column is SAID, never filled from the
    default workbook behind 06b's back.

    NOTE that environment is perfectly confounded with timepoint in this design
    - brackish IS timepoint 1 and sea IS timepoint 2 - so anything reported by
    environment is equally a statement about time.
    """
    meta_csv = meta_csv or G6B.META_CSV
    if not os.path.exists(meta_csv):
        print(f"  (no environment: no {os.path.basename(meta_csv)} - run "
              f"06b_join_sampling.py)")
        return {}
    rows = G5.load_csv(meta_csv)
    cols = list(rows[0]) if rows else []
    prefix = G6B.SAMPLING.COLUMN_PREFIX["environment"]
    hits = [c for c in cols if c.startswith(prefix)]
    if len(hits) != 1:
        print(f"  (no environment: {os.path.basename(meta_csv)} has "
              + (f"{len(hits)} columns starting '{prefix}' ({', '.join(hits)})"
                 if hits else f"no environment column (none starting '{prefix}')")
              + " - the environment column is left blank)")
        return {}
    return {r["animal"]: (r.get(hits[0]) or "").strip() for r in rows}


def write_workbook(path, sheets):
    """Write (name, rows) pairs as a workbook, one sheet each.

    Shared with 06d so the two datasets are formatted by the same code - bold
    header, frozen first row, columns sized to their contents. A second copy of
    this would be a second thing to keep in step for no benefit.

    A sheet may be (name, rows, lines): the lines are written one per row above
    the table, with a blank row between, and the panes freeze under the table's
    header instead of the first row.

    A fourth element is more tables below the first: [(lines, rows)], each
    after a blank row, its lines above it and its header bold. The columns are
    sized to the first table alone.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    for i, sheet in enumerate(sheets):
        name, data = sheet[0], sheet[1]
        lines = list(sheet[2]) if len(sheet) > 2 else []
        ws = wb.active if i == 0 else wb.create_sheet()
        ws.title = name
        for line in lines:
            ws.append([line])
        if lines:
            ws.append([])
        if not data:
            continue
        top = len(lines) + (1 if lines else 0) + 1
        cols = list(data[0].keys())
        ws.append(cols)
        for c in range(1, len(cols) + 1):
            ws.cell(row=top, column=c).font = Font(bold=True)
        for row in data:
            ws.append([row[c] for c in cols])
        ws.freeze_panes = f"A{top + 1}"
        for c, col in enumerate(cols, 1):
            width = max(len(str(col)), *(len(str(r[col])) for r in data)) + 2
            ws.column_dimensions[get_column_letter(c)].width = min(width, 30)
        for more_lines, more in (sheet[3] if len(sheet) > 3 else ()):
            ws.append([])
            for line in more_lines:
                ws.append([line])
            if not more:
                continue
            more_cols = list(more[0].keys())
            ws.append(more_cols)
            for c in range(1, len(more_cols) + 1):
                ws.cell(row=ws.max_row, column=c).font = Font(bold=True)
            for row in more:
                ws.append([row[c] for c in more_cols])
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with IO.atomic_save(path) as tmp:
        wb.save(tmp)


def resolve_groups(config_groups, meta_csv=None):
    """Treatment per animal. 06b is the unblinding step and validates the
    workbook; config.groups.by_animal is hand-typed. Where both exist they
    must agree, and the metadata fills in animals the config does not list."""
    meta_csv = meta_csv or G6B.META_CSV
    if not os.path.exists(meta_csv):
        return dict(config_groups)
    meta = {r["animal"]: r["treatment"] for r in G5.load_csv(meta_csv) if r.get("treatment")}
    clash = {a: (config_groups[a], meta[a]) for a in config_groups
             if a in meta and config_groups[a] != meta[a]}
    if clash:
        raise SystemExit(
            "config.groups.by_animal disagrees with results/animal_metadata.csv: "
            + ", ".join(f"{a}: config={c} workbook={m}" for a, (c, m) in sorted(clash.items()))
            + "\n  06b_join_sampling.py is the unblinding - fix config.json or rerun 06b.")
    return {**meta, **config_groups}

def _cell(v):
    """A CSV string as the number it is, so the sheet sorts and sums; text stays."""
    if not isinstance(v, str) or not re.fullmatch(r"-?\d+(\.\d+)?", v):
        return v
    return float(v) if "." in v else int(v)


def _ks_06a():
    """{marker: the k 06a formed the reagent controls' cuts at}, from the k
    column of reagent_control_cuts.csv beside MEAS_CSV; {} without the file or
    the column (written before 06a recorded it: the k used is not known)."""
    path = os.path.join(os.path.dirname(MEAS_CSV), AF_RC_CUTS)
    out = {}
    for r in (G5.load_csv(path) if os.path.exists(path) else []):
        try:
            out[r["marker"]] = float(r["k"])
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _cut_ks(rc_rows):
    """{marker: the k its cut is formed at} for the reagent-control sheet's
    header: the k 06a used (_ks_06a), so it states the multiplier the rows
    were made with; positivity.cut_k for a marker 06a recorded none for."""
    used = _ks_06a()
    return {m: used.get(m, LO.cut_k(CONFIG, m))
            for m in sorted({r.get("marker") or "" for r in rc_rows}) if m}


def _k_changed_lines(markers):
    """One line per marker whose positivity.cut_k is not the k 06a used: the
    sheet's numbers are 06a's, and the results need rebuilding."""
    used = _ks_06a()
    out = []
    for m in sorted(markers):
        now = LO.cut_k(CONFIG, m)
        if m in used and float(now) != used[m]:
            out.append(f"k for {m} changed since 06a ran (06a used {used[m]:g}; "
                       f"positivity.cut_k is now {float(now):g}) - rebuild results: the "
                       f"numbers on this sheet are at {used[m]:g}.")
    return out


def _after_unblinding_lines():
    """One line per marker whose k was applied through the typed confirmation
    after unblinding - 06b had run or groups.by_animal was filled
    (positivity.cut_k_source[marker]["after_unblinding"], set by
    app/cut_apply.py) - for the "Reagent controls" sheet's header."""
    src = (CONFIG.get("positivity") or {}).get("cut_k_source")
    src = src if isinstance(src, dict) else {}
    return [f"k for {m} was changed after unblinding (groups were assigned when the "
            f"reagent k check's suggestion was applied)."
            for m in sorted(src) if isinstance(src[m], dict) and src[m].get("after_unblinding")]


#: Above the k check's table on the "Reagent controls" sheet.
CUT_CHECK_LINES = (
    "Cut check (provisional until validated on a study with known controls): the "
    "positivity cut's multiplier k tested against the no-primary reagent controls, from "
    "results/reagent_cut_check.csv. control_fp is the rate of control ROI nuclei called "
    "positive by each control's own cut at k, with its 95% interval (control_fp_lo, "
    "control_fp_hi); disc_fp is the stained sections' leave-one-disc-out rate at the same "
    "k. suggested = 1 on the smallest k that meets positivity.fp_target. A blank is a rate "
    "that cannot be given (the note says why), never 0. The suggestion never changes the "
    "cut by itself: it is applied only from the app's Results page.",
)


#: The sheet results/roi_corrected.csv is shown on, when 06a wrote it.
CORRECTED_SHEET = "corrected_rate"


def _corrected_sheet():
    """The corrected-rate sheet from results/roi_corrected.csv beside
    MEAS_CSV, or None when 06a wrote none (no no_primary control measured).
    The raw frac_positive sits right beside the corrected columns, as in the
    file; the header states the sensitivity-1 assumption."""
    path = os.path.join(os.path.dirname(MEAS_CSV), RR.ROI_CORRECTED_CSV)
    rows = G5.load_csv(path) if os.path.exists(path) else []
    if not rows:
        return None
    return (CORRECTED_SHEET,
            [{c: _cell(r.get(c, "")) for c in RR.ROI_CORRECTED_COLUMNS} for r in rows],
            RR.corrected_sheet_header())


#: The sheet 06k's autofluorescence flag is shown on, when it wrote af_scores.csv.
AF_SHEET = "af_flag"
#: 06k's files, beside MEAS_CSV (06k_af_score.SCORES_CSV / SECTIONS_CSV / GATE_CSV).
AF_SCORES = "af_scores.csv"
AF_SECTIONS = "af_scored_sections.csv"
AF_GATE = "af_gate.csv"
#: 06a's cut files, beside MEAS_CSV (06a_roi_dataset.CUTS_CSV / RC_CUTS_CSV), which
#: 06k's recorded cuts are compared with. 06a is not imported here (it reads a
#: whole study at import); tests/test_af_score.py holds the names to 06a's.
AF_CUTS = "positivity_cuts.csv"
AF_RC_CUTS = "reagent_control_cuts.csv"
#: The note on a row whose flags were scored under a cut 06a no longer uses.
AF_STALE = "flags older than the cut - run 06k"
#: af_scored_sections.csv's role for a stained section 06k skipped because its
#: 06l features were computed before 05c re-detected it (06k_af_score), and the
#: note on a row with one.
AF_FEATURES_ROLE = "features_out_of_date"
AF_FEATURES_STALE = "06l features out of date - run 06l"
#: The note on a row of a marker whose reagent-control set is not the one 06k
#: trained with (af_gate.csv's control_set against reagent_controls.csv now).
AF_CONTROLS_CHANGED = "control set changed - run 06k"

AF_LINES = (
    "Positive rate without flagged nuclei (provisional until validated on a study with "
    "known controls: the operator hand-labels qc/af_review.png and the flag must reach a "
    "precision of at least 0.8 at a score of 0.5 on its random flagged row). A flag only: no count is changed, and "
    "frac_positive is by_roi's raw rate.",
    "06k scores each above-cut nucleus of a stained section by how much it looks like the "
    "above-cut nuclei of the study's reagent controls (unstained ones when there are at "
    "least 200 per marker, flag af_suspect; else no-primary ones, flag control_like; never "
    "both) and flags it at a score of 0.5. frac_positive_without_flagged = (n_positive - "
    "n_flagged) / (n_nuclei - n_flagged): the flagged nuclei leave the count and the "
    "denominator alike.",
    "A blank is a rate that cannot be given, never 0; the note says why (no raw rate, a "
    "section not scored yet, a section whose 06l features are out of date, a reagent-"
    "control set changed since 06k ran, or flags scored under a cut 06a no longer uses).",
)


def _af_sheet(meas, by_roi, sec_by):
    """The af_flag sheet from 06k's af_scores.csv and af_scored_sections.csv
    beside MEAS_CSV, or None when 06k wrote none. One row per by_roi row of a
    scored marker (sample x marker x region): its raw rate, then the rate
    without the flagged nuclei.

    A row is blank with a note when it has no raw rate, when one of its
    sections was not scored (a section 06a measured empty counts as scored,
    with none flagged), or when a cut 06k scored or trained against is not the
    cut in positivity_cuts.csv / reagent_control_cuts.csv now. The header
    names every marker 06k did not score, with its control counts."""
    base = os.path.dirname(MEAS_CSV)
    spath, cpath = os.path.join(base, AF_SCORES), os.path.join(base, AF_SECTIONS)
    if not (os.path.exists(spath) and os.path.exists(cpath)):
        return None
    secs = G5.load_csv(cpath)
    stained = [r for r in secs if r.get("role", "stained") == "stained"]
    scored = {(r["scene_uid"], r["marker"]) for r in stained}
    feats_old = {(r["scene_uid"], r["marker"]) for r in secs
                 if r.get("role") == AF_FEATURES_ROLE}
    secs = [r for r in secs if r.get("role") != AF_FEATURES_ROLE]
    how = {r["marker"]: (r["flag_name"], r["trained_on"]) for r in secs}
    if not how:
        return None

    def cuts_of(name):
        path = os.path.join(base, name)
        return ({(r["scene_uid"], r["marker"]): r["cut"] for r in G5.load_csv(path)}
                if os.path.exists(path) else {})

    def same(a, b):
        try:
            return b is not None and float(a) == float(b)
        except (TypeError, ValueError):
            return False

    now = {"stained": cuts_of(AF_CUTS), "control": cuts_of(AF_RC_CUTS)}
    stale = {(r["scene_uid"], r["marker"]) for r in secs
             if not same(r.get("cut"), now.get(r.get("role", "stained"), {}).get(
                 (r["scene_uid"], r["marker"])))}
    stale_markers = {m for (u, m) in stale
                     if any(r["role"] == "control" for r in secs
                            if (r["scene_uid"], r["marker"]) == (u, m))}
    # Measured and found empty: no nucleus in its ROIs, so nothing to flag -
    # scored with 0. Its background discs may still hold nuclei.
    n_sec = collections.Counter()
    for r in meas:
        n_sec[(r["scene_uid"], r["marker"])] += (int(r["n_nuclei"] or 0)
                                                 if r["roi_kind"] == "roi" else 0)
    empty = {k for k, v in n_sec.items() if v == 0}
    # The control set each marker was trained with (06k's af_gate.csv) against
    # the one reagent_controls.csv lists now: a section confirmed as a control,
    # or one no longer a control, since 06k ran changes what was flagged.
    gpath = os.path.join(base, AF_GATE)
    gate = G5.load_csv(gpath) if os.path.exists(gpath) else []
    rpath = os.path.join(base, RR.REPORT_CSV)
    ctl_now = {}
    for r in (G5.load_csv(rpath) if os.path.exists(rpath) else []):
        ctl_now.setdefault(r.get("marker") or "", set()).add((r["scene_uid"], r.get("kind")))
    ctl_changed = {g["marker"] for g in gate
                   if (g.get("control_set") or "")
                   != RR.control_set_hash(ctl_now.get(g["marker"], ()))}
    # A flagged nucleus's region and animal are its disc's, as 06a wrote it.
    disc = {(r["scene_uid"], r["marker"], r["roi_index"]): r for r in meas}
    flagged = collections.Counter()
    for r in G5.load_csv(spath):
        if r.get("flagged") != "1":
            continue
        d = disc.get((r["scene_uid"], r["marker"], r["roi_index"]))
        if d is not None and d["roi_kind"] == "roi":
            flagged[(d["animal"], d["marker"], d["region"])] += 1
    rows = []
    for b in by_roi:
        mk = b["marker"]
        if mk not in how:
            continue
        key = (b["sample"], mk, b["ROI"])
        uids = sec_by.get(key, set())
        missing = sorted(u for u in uids if (u, mk) not in scored and (u, mk) not in empty)
        old = sorted(u for u in uids if (u, mk) in stale)
        n, npos, raw = b["n_nuclei"], b["n_positive"], b["frac_positive"]
        nf = flagged[key]
        rate, note = "", ""
        if raw == "" or npos == "":
            note = "no raw rate on this row, so none without the flagged nuclei"
        elif any((u, mk) in feats_old for u in missing):
            of = [u for u in missing if (u, mk) in feats_old]
            note = (f"not scored: {AF_FEATURES_STALE} ({len(of)} of {len(uids)} "
                    f"section(s), e.g. {of[0]})")
        elif missing:
            note = (f"not scored: {len(missing)} of {len(uids)} section(s) have no "
                    f"autofluorescence score (06l features missing, or 06k not run since; "
                    f"e.g. {missing[0]})")
        elif mk in ctl_changed:
            note = AF_CONTROLS_CHANGED
        elif old or mk in stale_markers:
            note = AF_STALE
        elif nf > npos:
            note = (f"{nf} flagged but {npos} positive: af_scores.csv is older than "
                    f"roi_measurements.csv - run 06k again")
        elif n - nf <= 0:
            note = "no nucleus left once the flagged ones are taken out"
        else:
            rate = round((npos - nf) / (n - nf), 4)
        rows.append({
            "sample": b["sample"], "marker": mk, "ROI": b["ROI"],
            "treatment": b["treatment"], "n_nuclei": n, "n_positive": npos,
            "frac_positive": raw, "frac_positive_without_flagged": rate,
            "n_flagged": nf if rate != "" else "",
            "n_positive_without_flagged": (npos - nf) if rate != "" else "",
            "n_sections": len(uids), "n_sections_scored": len(uids) - len(missing),
            "flag_name": how[mk][0], "trained_on": how[mk][1], "note": note,
        })
    if not rows:
        return None
    lines = list(AF_LINES)
    for g in gate:
        if g.get("trained_on"):
            continue
        if g.get("reason") == "no_stained":
            lines.append(f"{g['marker']} not scored: no stained nucleus above the cut "
                         f"with features, so it has no row here.")
        else:
            lines.append(f"{g['marker']} not scored: {g['n_unstained']} unstained / "
                         f"{g['n_no_primary']} no_primary < 200 above-cut reagent-control "
                         f"nuclei (06k's training gate), so it has no row here.")
    return (AF_SHEET, rows, lines)


def _rc_sheet(rc_rows):
    """The "Reagent controls" sheet: the controls' table, then the k check's
    when 06a wrote results/reagent_cut_check.csv beside reagent_controls.csv.
    The path is taken from MEAS_CSV when called, as rc_path is."""
    kc_path = os.path.join(os.path.dirname(MEAS_CSV), RR.CUT_CHECK_CSV)
    kc_rows = G5.load_csv(kc_path) if os.path.exists(kc_path) else []
    sheet = ("Reagent controls", [{k: _cell(v) for k, v in r.items()} for r in rc_rows],
             RR.sheet_header(rc_rows, _cut_ks(rc_rows)) + _after_unblinding_lines()
             + _k_changed_lines({r.get("marker") or "" for r in rc_rows + kc_rows} - {""}))
    if kc_rows:
        sheet += ([(list(CUT_CHECK_LINES) + [RR.cut_rule_text(_cut_ks(kc_rows))],
                    [{k: _cell(v) for k, v in r.items()} for r in kc_rows])],)
    return sheet


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=XLSX)
    # argv is passed explicitly by 06e_refresh_loop, whose own flags would
    # otherwise reach this parser and abort the cycle.
    args = ap.parse_args(argv)

    if not os.path.exists(NUCLEI_CSV):
        print(f"no {NUCLEI_CSV} yet - 05c_detect_rois.py has not written anything")
        return 1
    rc_path = os.path.join(os.path.dirname(MEAS_CSV), RR.REPORT_CSV)
    rc_rows = G5.load_csv(rc_path) if os.path.exists(rc_path) else []
    if not os.path.exists(MEAS_CSV) and rc_rows:
        # CONTROLS FIRST. 05c measures reagent controls before any stained
        # section, so early in a run 06a has only them: no experimental row,
        # hence no roi_measurements.csv, and a report. That is a partial run
        # reporting what it has, not a missing input - and failing here would
        # end 06e's loop on the first cycle.
        write_workbook(args.out, [_rc_sheet(rc_rows)])
        print(f"no stained section measured yet - only the reagent controls "
              f"({len(rc_rows)} rows) -> {args.out}")
        print(f"  {RR.limitation_text(rc_rows)}")
        return 0
    if not os.path.exists(MEAS_CSV):
        print(f"no {MEAS_CSV} - run 06a_roi_dataset.py first.\n"
              f"  This stage no longer re-derives the per-ROI numbers; 06a owns "
              f"them, so a stale or absent 06a is a missing input, not a "
              f"fallback to recomputing.")
        return 1

    meas = G5.load_csv(MEAS_CSV)
    # Reagent controls are not planned sections of the experiment: 06a writes
    # none of their rows here, so their boxes leave `planned` and coverage too.
    # Per (section, marker), from config - a multiplex scene that is a control
    # for one marker is still planned for the others.
    rc_lookup = RR.study_lookup(CONFIG)
    boxes = RR.drop_controls(G5.all_boxes(), rc_lookup)   # both markers
    groups = resolve_groups((CONFIG.get("groups") or {}).get("by_animal") or {})
    envs = animal_environment()

    # 06a emits a row only for sections that are in roi_nuclei.csv, so the set
    # of measured sections comes from it directly. Checked against the nuclei
    # file rather than assumed: a 06a that predates the current detection run
    # would otherwise silently under-report coverage, which is the one thing
    # this sheet exists to state.
    measured = {r["scene_uid"] for r in meas}
    # The same, per (section, MARKER), for the coverage sheet. Under `multiplex`
    # every marker's boxes plan the same uids, so a section measured for one
    # marker is NOT measured for the others - see the coverage loop below.
    # Under `paired` a uid has one marker and the two sets say the same thing.
    measured_mk = {(r["scene_uid"], r["marker"]) for r in meas}
    nuc_secs, nuc_mk = set(), set()
    with open(NUCLEI_CSV, encoding="utf-8") as fh:
        rd = csv.reader(fh)
        head = next(rd, None) or []
        mcol = head.index("marker") if "marker" in head else None
        for row in rd:
            if row:
                nuc_secs.add(row[0])
                nuc_mk.add((row[0], row[mcol] if mcol is not None else ""))
    # A SUBSET IS NORMAL MID-RUN, AND MUST NOT FAIL.
    #
    # This stage is meant to be runnable while 05c is still appending. 06a runs
    # first in each 06e cycle and takes seconds to minutes, and 05c keeps
    # writing throughout - so any section that finishes in between leaves 06a
    # legitimately one or more sections behind. Requiring exact equality turned
    # that ordinary race into a hard failure, and 06e escalates a failed refresh
    # into ending the loop, so the check would have killed the very scenario it
    # was written to protect. It says so and carries on instead.
    #
    # The genuine error is the other direction: 06a referencing sections the
    # nuclei file does not contain means the two files are not from the same
    # data at all, and every count would be joined against the wrong geometry.
    #
    # A section 05c measured and found EMPTY is neither. It has no row in
    # roi_nuclei.csv - there was nothing to write - and 06a still reports it,
    # with zero nuclei on every disc, because 05c's done log says it was
    # measured. Counting it as orphaned failed every study with one such
    # section. A section whose 06a rows hold any nucleus is still required in
    # the nuclei file.
    found = collections.Counter()
    for r in meas:
        found[r["scene_uid"]] += int(r["n_nuclei"] or 0)
    measured_empty = {u for u in measured if not found[u]}
    orphaned = measured - nuc_secs - measured_empty
    if orphaned:
        print(f"  INCONSISTENT: roi_measurements.csv has {len(orphaned)} "
              f"sections that are not in roi_nuclei.csv "
              f"(e.g. {sorted(orphaned)[0]}). The two files are not from the "
              f"same run. Re-run 06a_roi_dataset.py.")
        return 1
    # A reagent control is in roi_nuclei.csv and never in roi_measurements.csv -
    # PER (SECTION, MARKER): under `multiplex` a scene that is a control for one
    # marker is stained for the others, and a 06a missing those must still be
    # said to be behind. A section is behind when any of its markers is.
    if mcol is None:                    # no marker column: by section, as before
        behind = nuc_secs - measured - {u for u, _m in rc_lookup}
    else:
        behind = {u for u, m in nuc_mk
                  if (u, m) not in measured_mk and (u, m) not in rc_lookup}
    if behind:
        print(f"  06a is {len(behind)} section(s) behind roi_nuclei.csv - "
              f"reporting the {len(measured)} it covers. Normal while "
              f"05c_detect_rois.py is still running; re-run 06a for the rest.")

    planned = {b["scene_uid"] for b in boxes}
    n_nuclei_total = sum(int(r["n_nuclei"]) for r in meas)
    print(f"{len(measured)} of {len(planned)} sections measured "
          f"({100*len(measured)/len(planned):.0f}%), {n_nuclei_total} nuclei")

    # Per-ROI numbers come from 06a, which owns them. This stage groups and
    # formats; it does not re-derive.
    #
    # The duplicate implementation that used to live here computed Abercrombie's
    # h per (animal, region), while 06a computes it per (marker, region) as
    # config.detection.abercrombie._h_source declares. The two therefore
    # disagreed by up to 9.7% on a per-ROI density, and nothing compared them.
    # Worse, measured nuclear diameter is not the same in both arms - Vl differs
    # by 1.50 um - so a per-animal h put a group-varying correction into every
    # density, which is exactly what config._effect_on_the_comparison warns
    # against. See LOGS.md.
    #
    # 06a has already dropped artifact nuclei and applied the censoring rule
    # (04j: a censored nucleus is positive by construction and excluded from
    # intensity statistics), so neither is repeated here.
    roi_rows = [r for r in meas if r["roi_kind"] == "roi"]

    def num(v, cast=float):
        return cast(v) if v not in ("", None) else None

    n_by = collections.Counter()               # (animal, region) -> nuclei
    p_by = collections.Counter()               # (animal, region) -> positives
    a_by = collections.Counter()               # (animal, region) -> area mm2
    d_by = collections.Counter()               # key -> discs
    sec_by = collections.defaultdict(set)      # key -> scene_uids
    h_by, ab_by, why_by = {}, {}, {}
    # Carried from 06a, like the Abercrombie reason: whether a denominator
    # exists is 06a's call (it can see `segmented_on`). Any non-blank value
    # under a key wins, and every row of a marker carries the same one.
    den_by = {}
    pos_known = collections.Counter()          # discs whose section had a cut

    # THE MARKER IS PART OF THE KEY. Without it a row is AF568 + AF488 summed:
    # different antibodies, different acquisitions, one n_nuclei. It was
    # invisible while only pERK had been measured and 06a read a single marker's
    # boxes, and it became certain the moment 06a started reading both. It also
    # made the h note below false, because two markers under one key carry two
    # different (h, factor) pairs and "the last one" would win arbitrarily.
    for r in roi_rows:
        key = (r["animal"], r["marker"], r["region"])
        n_by[key] += int(r["n_nuclei"])
        a_by[key] += float(r["roi_area_mm2"])
        d_by[key] += 1
        sec_by[key].add(r["scene_uid"])
        npos = num(r["n_positive"], int)
        if npos is not None:
            p_by[key] += npos
            pos_known[key] += 1
        # h and the factor are properties of (marker, region) in 06a, and the
        # marker is in the key here, so every row under a key really does carry
        # the same pair - taking the last is taking the only one.
        h_by[key] = num(r["mean_nucleus_diam_um"])
        ab_by[key] = num(r["abercrombie_factor"]) or 1.0
        # CARRIED, NOT RE-DERIVED. Whether the correction applies is 06a's
        # decision - it is the stage that can see `nucleus_shaped` on the
        # nuclei - and a factor of 1.0 alone cannot be told apart from a
        # correction that happened to come out at 1.0. Same key, same "the last
        # one is the only one" argument as h and the factor above.
        #
        # .get, because a roi_measurements.csv written before 2026-09-08 has no
        # such column. Blank then means what it means everywhere else here: the
        # correction was applied.
        why_by[key] = r.get("abercrombie_withheld_reason", "")
        if r.get("denominator_withheld_reason"):
            den_by[key] = r["denominator_withheld_reason"]

    by_roi = []
    for key in sorted(a_by):
        an, mk, rg = key
        n, area = n_by[key], a_by[key]
        ab = ab_by.get(key) or 1.0
        h = h_by.get(key)
        # A disc whose section had no positivity cut contributes no positives.
        # Reporting 0 for it would read as "looked and found none", so the cell
        # goes blank unless every disc behind it had a cut.
        full = pos_known[key] == d_by[key]
        npos = p_by[key] if full else ""
        # No denominator: the objects were found on the marker itself, so n is
        # "objects the marker lit up", not a count of all cells. The positive
        # count and the positive density are what such a marker can say.
        no_den = den_by.get(key, "")
        by_roi.append({
            "n_nuclei": n,
            "ROI": rg,
            "sample": an,
            "marker": mk,
            "treatment": groups.get(an, ""),
            "environment": envs.get(an, ""),
            "total_tissue_area_mm2": round(area, 6),
            # Positivity sits ALONGSIDE the count, never replacing it. With no
            # no-primary control and no tERK normaliser, an absolute positivity
            # rate is not a claim these data support; the comparison between
            # arms at matched levels is. detector_specificity.csv carries the
            # false-positive rate that has to be quoted with it. A study with a
            # no-primary reagent control reads it against the "Reagent
            # controls" sheet instead (ls_reagent_report.limitation_text).
            "n_positive": npos,
            "frac_positive": (round(npos / n, 4)
                              if (full and n and not no_den) else ""),
            "positive_cells_per_mm2": (round(npos * ab / area, 1)
                                       if (full and area) else ""),
            # Everything past this point is derived from the columns above and
            # is here so the sheet can be checked without recomputing it.
            "n_discs": d_by[key],
            "n_sections": len(sec_by.get(key, ())),
            "profiles_per_mm2": round(n / area, 1) if area else "",
            "mean_nucleus_diam_um": round(h, 2) if h else "",
            "abercrombie_factor": round(ab, 4),
            "cells_per_mm2": (round(n * ab / area, 1)
                              if (area and not no_den) else ""),
            # Beside the corrected density, because that is the number it is
            # about. Blank means the correction was applied; anything else is
            # 06a saying cells_per_mm2 is the profile count uncorrected, and
            # why. A withheld correction that only ever appeared in a CSV
            # nobody opens is not reported.
            "abercrombie_withheld_reason": why_by.get(key, ""),
            # Blank where a count of all cells exists; otherwise why
            # frac_positive and cells_per_mm2 are empty on this row.
            "denominator_withheld_reason": no_den,
        })

    # by_disc keeps every individual placement so by_roi can be checked by
    # pivoting it. The two axis columns are the only thing 06a does not carry,
    # so they are joined back from roi_boxes.csv on (scene_uid, roi_index) -
    # rebuilding the index exactly the way 06a numbered the discs.
    #
    # PER MARKER, exactly as 06a numbers them: 05c's roi_index counts within
    # one marker's own box file, and under `multiplex` all_boxes() returns
    # both markers' boxes for the same uid. Counting them together would put
    # the second marker's semi-axes on the first marker's discs. Under
    # `paired` a uid belongs to one marker, so this is the old counter.
    axes, seen = {}, collections.Counter()
    for b in boxes:
        key = (b["scene_uid"], b["marker"])
        seen[key] += 1
        axes[(b["scene_uid"], b["marker"], seen[key])] = b

    by_disc = []
    for r in meas:
        b = axes.get((r["scene_uid"], r["marker"], int(r["roi_index"])), {})
        by_disc.append({
            "sample": r["animal"], "treatment": groups.get(r["animal"], ""),
            "environment": envs.get(r["animal"], ""),
            "scene_uid": r["scene_uid"], "roi_index": int(r["roi_index"]),
            "roi_kind": r["roi_kind"], "ROI": r["region"], "seed_n": r["seed_n"],
            "marker": r["marker"],
            "n_nuclei": int(r["n_nuclei"]),
            # int, not the raw CSV string. This sheet exists so by_roi can be
            # checked by pivoting it, and a text column will not pivot - the
            # two columns either side of this one are cast for the same reason.
            # Blank stays blank: no cut means no number, not zero.
            "n_positive": num(r["n_positive"], int) if r["n_positive"] != "" else "",
            "tissue_area_mm2": float(r["roi_area_mm2"]),
            "axis_a_um": b.get("axis_a_um", ""),
            "axis_b_um": b.get("axis_b_um", ""),
        })

    # PER (SECTION, MARKER). 06a's positivity cut is one marker's background
    # on one section; under `multiplex` both markers share the section's uid,
    # and keying by uid alone summed their nuclei into one row and showed
    # whichever marker's cut came last under that marker's name. Under
    # `paired` a section has one marker and this is one row per section, as
    # before.
    per_sec = collections.defaultdict(lambda: [0, 0, set(), 0, 0])
    cut_by, animal_by = {}, {}
    for r in meas:
        key = (r["scene_uid"], r["marker"])
        s = per_sec[key]
        cut_by[key] = r["section_positivity_cut"]
        animal_by[key] = r["animal"]
        if r["roi_kind"] == "roi":
            s[0] += int(r["n_nuclei"])
            s[2].add(r["region"])
            s[3] += 1
        else:
            s[1] += int(r["n_nuclei"])
            s[4] += 1

    by_section = []
    # Every measured section is in per_sec: `measured` is read off the same
    # rows.
    for uid, mk in sorted(per_sec):
        n_roi, n_bg, regions, d_roi, d_bg = per_sec[(uid, mk)]
        an = animal_by.get((uid, mk), NM.subject_of(uid))
        by_section.append({
            "sample": an, "treatment": groups.get(an, ""),
            "environment": envs.get(an, ""), "scene_uid": uid,
            "marker": mk,
            "n_roi_nuclei": n_roi, "n_background_nuclei": n_bg,
            "n_roi_discs": d_roi,
            "n_background_discs": d_bg,
            # Blank means the section had fewer than 5 background nuclei, so no
            # cut could be formed and its positivity columns are empty upstream.
            "positivity_cut": cut_by.get((uid, mk), ""),
            "regions": ", ".join(sorted(regions)),
        })

    # Coverage is per (animal, MARKER): pooling the two would report one
    # percentage for a pERK pass that is finished and a PCNA one that has not
    # started, which is the one question this sheet exists to answer.
    #
    # And "measured" is per marker too. It was `pl & measured`, a set of uids,
    # which under `multiplex` - where every marker plans the same uids - made
    # the first marker's finished pass read as every marker's: 2/2 for a marker
    # 05c had never been run on.
    coverage = []
    for an, mk in sorted({(b["animal"], b["marker"]) for b in boxes},
                         key=lambda t: (NM.natural_key(t[0]), t[1])):
        pl = {b["scene_uid"] for b in boxes
              if b["animal"] == an and b["marker"] == mk}
        done = sum(1 for u in pl if (u, mk) in measured_mk)
        coverage.append({
            "sample": an, "marker": mk, "treatment": groups.get(an, ""),
            "environment": envs.get(an, ""),
            "sections_measured": done,
            "sections_planned": len(pl),
            "percent": round(100 * done / len(pl), 1) if pl else "",
        })

    sheets = [("by_roi", by_roi), ("by_disc", by_disc),
              ("by_section", by_section), ("coverage", coverage)]
    corrected = _corrected_sheet()
    if corrected:
        sheets.append(corrected)
    af = _af_sheet(meas, by_roi, sec_by)
    if af:
        sheets.append(af)
    if rc_rows:
        sheets.append(_rc_sheet(rc_rows))
    write_workbook(args.out, sheets)

    print("=" * 72)
    print(f"{len(by_roi)} rows (one per ROI per sample) -> {args.out}")
    print(f"  sheets: by_roi, by_disc ({len(by_disc)}), "
          f"by_section ({len(by_section)}), coverage"
          + (f", {CORRECTED_SHEET} ({len(corrected[1])})" if corrected else "")
          + (f", {AF_SHEET} ({len(af[1])}, provisional)" if af else "")
          + (f", Reagent controls ({len(rc_rows)})" if rc_rows else ""))
    print(f"  {RR.limitation_text(rc_rows)}")
    # Sections, not by_section rows: under `multiplex` a section has a row per
    # marker, and counting rows would count it once per marker.
    tr = collections.Counter(
        t or "(none)"
        for t in {r["scene_uid"]: r["treatment"] for r in by_section}.values())
    print(f"  sections measured by arm: {dict(tr)}")
    print()
    # Matched to the coverage sheet, which is per (sample, MARKER). Summing
    # by sample alone printed each animal's pooled totals twice, once against
    # each marker's section count - two rows that disagreed with themselves.
    print(f"  {'sample':8} {'marker':7} {'treatment':10} {'sections':>9} "
          f"{'ROIs':>6} {'nuclei':>8}")
    for c in coverage:
        same = [r for r in by_roi
                if r["sample"] == c["sample"] and r["marker"] == c["marker"]]
        n = sum(r["n_nuclei"] for r in same)
        d = sum(r["n_discs"] for r in same)
        print(f"  {c['sample']:8} {c['marker']:7} {c['treatment'] or '?':10} "
              f"{c['sections_measured']:>4}/{c['sections_planned']:<4} {d:>6} {n:>8}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
