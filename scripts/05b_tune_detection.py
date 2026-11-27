"""05b  Check each marker's signal quality and tune nucleus detection to it.

Runs after 05a has placed the ROI boxes and before 05c detects. For each
marker it reads a sample of ROI crops off the plane 05c will segment - the
nuclear counterstain under `segment: nuclear`, the marker's own channel under
`segment: own` (PCNA in the LS study) - at the native pixel size, and:

  1. measures the signal: background, foreground, noise, signal-to-noise,
     contrast, coverage and saturation (ls_tune.quality), one row per crop in
     qc/detection_tuning.csv;
  2. judges it: good / marginal / poor, with the reasons in words;
  3. for a StarDist marker, runs the model once per crop and counts objects
     at each probability cut in ls_tune.PROB_GRID, and picks the cut where
     the count is least sensitive to it (ls_tune.pick_prob_thresh);
  4. writes all of it to the study config under detection.auto.<marker>, and
     a short report to qc/detection_tuning.md.

05c then uses the chosen cut, unless detection.stardist sets one by hand or
detection.stardist.use_auto is off.

THE SAMPLE IS BLIND AND STUDY-WIDE. Sections are spread evenly over animals in
name order; the group key is never read. One cut per marker is chosen for the
whole study, never one per section - see ls_tune for why a per-section cut
would be a treatment-correlated bias.

NOTHING IS DOWNSAMPLED. A crop larger than --window is cut down to its central
window, which loses area and keeps every pixel at full resolution.

THE MODEL SEES WHAT 05c FEEDS IT. Each box is read whole and stretched to 0..1
the way 05c stretches it - its own percentiles on the nuclear plane, the
section's range over every crop (background discs included) on a marker's own
channel - and only THEN cut to its window. Cutting first would stretch the
window by its own content, which is not the input the chosen cut is used on.

    python scripts/05b_tune_detection.py                 # every marker
    python scripts/05b_tune_detection.py --marker AF488 --sections 20
    python scripts/05b_tune_detection.py --dry-run       # report only
"""

import argparse
import csv
import datetime
import importlib.util
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
_spec = importlib.util.spec_from_file_location(
    "_d5", os.path.join(_HERE, "05c_detect_rois.py"))
D5 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(D5)

from ls_config import CONFIG, CONFIG_PATH  # noqa: E402
import czi_read as CR  # noqa: E402
import ls_io as IO  # noqa: E402

# The one shared ls_drive (None when it cannot be loaded, and then this stage
# behaves as it did before it existed): a section whose reads fail because the
# USB drive went away waits for it and is read again from its first box.
DR = IO.drive_module()


def per_section(fn, *, label):
    """Run one section's self-contained read, redoing it after a dropped drive
    - also when the drive went during it without the read failing (ls_drive's
    per_slide_settled)."""
    return DR.per_slide_settled(fn, label=label) if DR is not None else fn()
import ls_jsonio  # noqa: E402
import ls_naming as NM  # noqa: E402
import ls_reagent_controls as RC  # noqa: E402
import ls_segment as SG  # noqa: E402
import ls_tune as TU  # noqa: E402

G5 = D5.G5
QC = os.path.join(D5.OUT_ROOT, "qc")
TUNING_CSV = os.path.join(QC, "detection_tuning.csv")
REPORT_MD = os.path.join(QC, "detection_tuning.md")
COLUMNS = ["marker", "scene_uid", "animal", "roi_index", "segmented_on",
           "width", "height", "background", "foreground", "noise", "snr",
           "contrast", "coverage", "saturated"]
#: Added to the table only when the reagent-control constraint ran for some
#: marker (see `constrain_marker`), so a study without a usable control keeps
#: the table it always had, byte for byte. Per marker, at the final cut, on
#: each of that marker's rows; blank on the rows of a marker it did not run for.
CONSTRAINT_COLUMNS = ["control_density", "stained_density", "admissible",
                      "constraint_moved_from"]
ALL_COLUMNS = COLUMNS + CONSTRAINT_COLUMNS

#: Why the constraint did or did not run, per marker, for the report only:
#: detection.auto says nothing new when it did not run.
CONSTRAINT_NOTES = {}

#: The 2D_versatile_fluo model's own probability cut, used for the grid when
#: no model is loaded and as the fallback when the model does not say.
FALLBACK_PROB = 0.479071463157368


def sample_sections(uids, n):
    """`n` sections spread over animals: evenly through each animal, then
    taken one animal at a time so a small sample still covers every animal.
    """
    per = {}
    for u in sorted(uids, key=NM.natural_key):
        per.setdefault(NM.subject_of(u), []).append(u)
    animals = sorted(per, key=NM.natural_key)
    if not animals or n <= 0:
        return []
    k = max(1, -(-n // len(animals)))
    picks = {}
    for a in animals:
        secs = per[a]
        if len(secs) <= k:
            picks[a] = list(secs)
        else:
            picks[a] = [secs[int((i + 0.5) * len(secs) / k)] for i in range(k)]
    out = []
    for i in range(k):
        for a in animals:
            if i < len(picks[a]):
                out.append(picks[a][i])
    return out[:n]


def tuning_sections(uids, lookup, marker, n):
    """sample_sections over the stained sections of `marker` only.

    A reagent control is dim by design: in the sample it would drag the signal
    verdict and the chosen probability cut toward its autofluorescence. Which
    sections are controls comes from config (ls_reagent_controls.expand), per
    marker, so a multiplex scene that is a control for another marker stays in.
    """
    _controls, stained = RC.reagent_order(uids, lookup, marker)
    return sample_sections(stained, n)


def spread(items, n):
    if len(items) <= n:
        return list(items)
    return [items[int((i + 0.5) * len(items) / n)] for i in range(n)]


def window(b, side):
    """The box's central window of at most side x side native pixels."""
    x0, y0, w, h = int(b["czi_x0"]), int(b["czi_y0"]), int(b["czi_w"]), int(b["czi_h"])
    cw, ch = min(w, side), min(h, side)
    return D5._Rect(x0 + (w - cw) // 2, y0 + (h - ch) // 2, cw, ch)


def read_crops(marker, uids, by_sec, geom, rois, side, model_input=False,
               borrow=None):
    """[(uid, roi_index, box, crop, x)] off the plane 05c would segment.

    `crop` is the box's central window, raw. `x` is that same window of the
    WHOLE box as 05c gives it to StarDist (ls_segment.stardist_input over the
    box, under 05c's section range when the marker shares one), or None when
    `model_input` is off or the section has no range to share - 05c skips such
    a section, so it has no input to tune on either.

    `borrow`: {uid: stained uid} for reagent controls stretched by their
    stained pair's range, as 05c detects them (05c.borrowed_from).
    """
    from pylibCZIrw import czi as pyczi
    own = D5._segments_own(marker)
    plane = D5.plane_index_for(marker) if own else D5.NUCLEAR_C
    shared = model_input and D5.needs_shared_norm(marker)
    out = []
    for uid in uids:
        g = geom.get(uid)
        if g is None:
            continue
        boxes = [(i, b) for i, b in enumerate(by_sec[uid], 1)
                 if b.get("marker", marker) == marker
                 and (b.get("roi_kind") or "roi") != "background"
                 and int(b["czi_w"]) >= 8 and int(b["czi_h"]) >= 8]
        if not boxes:
            continue
        path = os.path.join(CONFIG["source_dir"], g["czi_file"])

        # One section is the unit a dropped drive redoes: each attempt opens
        # the CZI afresh and collects the section's crops into a list of its
        # own, which joins `out` only once the attempt has returned - so a
        # redo cannot add a crop twice. A read that fails with the folders
        # reachable is raised, as it always was.
        def unit(uid=uid, g=g, boxes=boxes, path=path):
            got = []
            with CR.open_czi(path, int(float(g.get("scene_w") or 0)) or None,
                             pyczi=pyczi) as doc:
                norm, usable = None, model_input
                if shared:
                    # 05c's own helper over the section's whole box list, so
                    # the range is the one 05c will stretch by, not one of the
                    # sample.
                    try:
                        if borrow and uid in borrow:
                            norm = D5.borrowed_range(borrow[uid], marker,
                                                     by_sec, geom)[:2]
                        else:
                            norm = D5.section_range(doc, by_sec[uid], marker,
                                                    int(g["scene_index"]))[:2]
                    except SG.SegmentError:
                        usable = False
                for bi, b in spread(boxes, rois):
                    full = CR.read_plane(doc, D5._Rect(int(b["czi_x0"]), int(b["czi_y0"]),
                                                       int(b["czi_w"]), int(b["czi_h"])),
                                         plane, scene=int(g["scene_index"])
                                         ).astype(np.float32)
                    r = window(b, side)
                    cut = (slice(r.y - int(b["czi_y0"]), r.y - int(b["czi_y0"]) + r.h),
                           slice(r.x - int(b["czi_x0"]), r.x - int(b["czi_x0"]) + r.w))
                    x = SG.stardist_input(full, norm)[cut] if usable else None
                    got.append((uid, bi, b, full[cut], x))
            return got

        out.extend(per_section(unit, label=f"{uid} ({g['czi_file']})"))
    return out


def constraint_pairs(marker, by_sec, geom, lookup):
    """([(control uid, stained uid, tier)], [(control uid, why)]): the
    no_primary reagent controls of `marker` the PCNA constraint can use, each
    with the stained section whose range it is read at (05c.borrowed_from:
    operator, same_file or same_animal, never study-wide), and the ones it
    cannot. Nothing is read when no control is confirmed."""
    pairs, skipped = [], []
    if not lookup:
        return pairs, skipped
    cache = {}
    for uid, mk in sorted(lookup, key=lambda k: NM.natural_key(k[0])):
        if mk != marker or (lookup[(uid, mk)] or {}).get("kind") != "no_primary":
            continue
        if uid not in by_sec or uid not in geom:
            skipped.append((uid, "no ROI boxes"))
            continue
        src, tier = D5.borrowed_from(uid, marker, by_sec, geom, lookup, cache)
        if src is None:
            skipped.append((uid, "no stained section on its file or animal"
                            if tier in (None, "study_wide") else
                            "its paired stained sections have no ROI boxes"))
            continue
        pairs.append((uid, src, tier))
    return pairs, skipped


def read_control_crops(marker, pairs, by_sec, geom, rois, side):
    """The reagent controls' ROI central windows, as the model sees them at
    their stained pair's range: read_crops with the range borrowed."""
    borrow = {u: s for u, s, _t in pairs}
    return read_crops(marker, list(borrow), by_sec, geom, rois, side,
                      model_input=True, borrow=borrow)


def _area_mm2(xs):
    px = float(D5.BASE_PX_UM) ** 2 / 1e6
    return sum(int(x.shape[0]) * int(x.shape[1]) for x in xs) * px


def fp_target():
    return float((CONFIG.get("positivity") or {}).get("fp_target", 0.01))


def constrain_marker(marker, pairs, skipped, by_sec, geom, args, model, grid,
                     curve, xs, chosen, default):
    """(cut, constraint record) for a `segment: own` marker with usable
    no_primary reagent controls; `chosen` (None: the model's default) held to
    ls_tune.constrain. Runs StarDist over the controls' ROI central windows at
    the same grid, each control at its stained pair's range. (chosen, None)
    when no control gave a window to count."""
    ccrops = read_control_crops(marker, pairs, by_sec, geom, args.rois, args.window)
    cxs = [c[4] for c in ccrops if c[4] is not None]
    if not cxs:
        CONSTRAINT_NOTES[marker] = ("not applied: no reagent-control window "
                                    "could be read at its stained pair's range")
        return chosen, None
    print(f"{marker}: counting at {len(grid)} probability cuts over {len(cxs)} "
          f"reagent-control crops, at their stained pairs' ranges...")
    ccurve = TU.count_curve(model, cxs, grid, nms_thresh=D5._STARDIST.get("nms_thresh"),
                            n_tiles_for=SG.tiles_for)
    area_s, area_c = _area_mm2(xs), _area_mm2(cxs)
    target = fp_target()
    new, why = TU.constrain(curve, ccurve, area_s, area_c, chosen, target,
                            default=default)
    table = TU.densities(curve, ccurve, area_s, area_c, target)
    at = default if new is None else new
    here = next((v for p, v in table.items() if abs(p - at) < 1e-4), None) or {}
    moved = (new is not None and chosen is None) or (
        new is not None and abs(new - chosen) > 1e-6)
    used = sorted({c[0] for c in ccrops if c[4] is not None}, key=NM.natural_key)
    rec = {"fp_target": target,
           "controls": [{"control": u, "range_from": s, "pairing": t}
                        for u, s, t in pairs if u in used],
           "skipped": [{"control": u, "why": w} for u, w in skipped]
                      + [{"control": u, "why": f"no window could be read at its "
                                               f"stained pair's range ({s}: flat, "
                                               f"empty or too small)"}
                         for u, s, _t in pairs if u not in used],
           "control_crops": len(cxs),
           "area_stained_mm2": round(area_s, 6),
           "area_control_mm2": round(area_c, 6),
           "control_curve": [[round(p, 6), n] for p, n in ccurve],
           "control_density": _r(here.get("control_density")),
           "stained_density": _r(here.get("stained_density")),
           "admissible": here.get("admissible"),
           "moved_from": (round(default if chosen is None else chosen, 6)
                          if moved else None),
           "why": why,
           "provisional": True}
    CONSTRAINT_NOTES[marker] = "applied"
    return new, rec


def _r(v):
    return None if v is None else round(float(v), 3)


def tune_marker(marker, args, model):
    """The detection.auto entry for one marker, and its per-crop rows."""
    G5.use_marker(marker)
    if not os.path.exists(G5.BOX_CSV):
        print(f"{marker}: no {G5.BOX_CSV} - run 05a_roi_geometry.py "
              f"--marker {marker} first")
        return None, []
    boxes = G5.load_csv(G5.BOX_CSV)
    geom = {g["scene_uid"]: g for g in G5.load_csv(G5.GEOM_CSV)}
    by_sec = {}
    for b in boxes:
        by_sec.setdefault(b["scene_uid"], []).append(b)
    uids = tuning_sections([u for u in by_sec if u in geom], D5.reagent_lookup(),
                           marker, args.sections)
    print(f"{marker}: sampling {len(uids)} of {len(by_sec)} sections, up to "
          f"{args.rois} ROIs each, native pixels, windows of at most "
          f"{args.window} px")
    backend = D5.backend_for(marker)
    crops = read_crops(marker, uids, by_sec, geom, args.rois, args.window,
                       model_input=backend == "stardist" and model is not None)
    if not crops:
        print(f"{marker}: nothing to measure")
        return None, []

    ceilings = D5.load_ceilings()
    seg_on = D5.segment_plane_for(marker)
    rows, bg_failed = [], 0
    for uid, bi, b, crop, _x in crops:
        q = TU.quality(crop, ceiling=ceilings.get(geom[uid]["czi_file"],
                                                  D5.DEFAULT_CEILING))
        q.update(scene_uid=uid)
        rows.append(q)
        if backend == "threshold":
            bg_failed += int(SG.background_check(crop, k=D5.MAD_K)["failed"])
    summary = TU.summarise(rows)
    word, reasons = TU.verdict(summary)
    if backend == "threshold" and bg_failed:
        reasons.append(f"{bg_failed} of {len(crops)} crops broke the threshold "
                       f"backend's background assumption")
    entry = {"tuned": datetime.date.today().isoformat(),
             "segmented_on": seg_on, "backend": backend,
             "sampled_sections": summary["sections"],
             "sampled_crops": summary["crops"],
             "quality": {k: (round(v, 4) if isinstance(v, float) else v)
                         for k, v in summary.items()
                         if k not in ("sections", "crops")},
             "verdict": word, "reasons": reasons, "chosen": {}}

    if backend == "stardist" and model is not None:
        default = round(float(getattr(getattr(model, "thresholds", None), "prob",
                                      FALLBACK_PROB) or FALLBACK_PROB), 6)
        grid = sorted(set(TU.PROB_GRID) | {default})
        xs = [c[4] for c in crops if c[4] is not None]
        print(f"{marker}: counting nuclei at {len(grid)} probability cuts "
              f"over {len(xs)} crops...")
        nms = D5._STARDIST.get("nms_thresh")
        curve = TU.count_curve(model, xs, grid, nms_thresh=nms,
                               n_tiles_for=SG.tiles_for)
        chosen, why = TU.pick_prob_thresh(curve, default)
        # THE REAGENT-CONTROL CONSTRAINT, for a marker segmented on its own
        # channel only, and only with a usable no_primary control: without
        # one this block does nothing and detection.auto is what it always was.
        if D5.needs_shared_norm(marker):
            pairs, skipped = constraint_pairs(marker, by_sec, geom, D5.reagent_lookup())
            if pairs:
                chosen, rec = constrain_marker(marker, pairs, skipped, by_sec, geom,
                                               args, model, grid, curve, xs,
                                               chosen, default)
                if rec is not None:
                    entry["constraint"] = rec
            else:
                CONSTRAINT_NOTES[marker] = (
                    "not applied: no no_primary reagent control pairable with a "
                    "stained section on its file or animal"
                    + ("" if not skipped else " (" + "; ".join(
                        f"{u}: {w}" for u, w in skipped) + ")"))
        entry["model_default"] = {"prob_thresh": default,
                                  "nms_thresh": float(getattr(getattr(model, "thresholds", None), "nms", 0.3) or 0.3)}
        entry["curve"] = [[round(p, 6), n] for p, n in curve]
        entry["why"] = why
        # A cut kept at the model's own is recorded as None, so a study that
        # tuned and changed nothing is not told its earlier counts differ.
        entry["chosen"]["prob_thresh"] = (
            None if chosen is None or abs(chosen - default) < 1e-6 else round(chosen, 4))
        n_default = dict(curve).get(default)
        n_chosen = dict(curve).get(chosen if chosen is not None else default)
        entry["count_at_default"], entry["count_at_chosen"] = n_default, n_chosen
    elif backend == "stardist":
        entry["why"] = "StarDist is not available here, so only the signal was measured"

    con = entry.get("constraint")
    extra = ["", "", "", ""] if con is None else [
        "" if con["control_density"] is None else con["control_density"],
        "" if con["stained_density"] is None else con["stained_density"],
        {True: "yes", False: "no"}.get(con["admissible"], ""),
        "" if con["moved_from"] is None else f"{con['moved_from']:g}"]
    out_rows = []
    for (uid, bi, b, crop, _x), q in zip(crops, rows):
        out_rows.append([marker, uid, NM.subject_of(uid), bi, seg_on,
                         crop.shape[1], crop.shape[0]]
                        + [round(q[k], 4) for k in COLUMNS[7:]] + extra)
    return entry, out_rows


def report(entries):
    lines = ["# Detection tuning", "",
             f"Made by 05b_tune_detection.py on {datetime.date.today().isoformat()}. "
             "One setting per marker for the whole study; the sample is spread "
             "over animals and never reads the group key.", ""]
    for marker, e in entries.items():
        q = e["quality"]
        lines += [f"## {marker} (segmented on {e['segmented_on']}, {e['backend']})", "",
                  f"**Signal: {e['verdict']}.** " + " ".join(r[:1].upper() + r[1:] + "."
                                                          for r in e["reasons"]), "",
                  f"Sampled {e['sampled_crops']} crops from {e['sampled_sections']} sections.", "",
                  "| background | foreground | noise | signal-to-noise | contrast | coverage | saturated |",
                  "|---|---|---|---|---|---|---|",
                  "| %s | %s | %s | %s | %s | %s | %s |" % tuple(
                      _fmt(q.get(k), pct=k in ("coverage", "saturated"))
                      for k in ("background", "foreground", "noise", "snr",
                                "contrast", "coverage", "saturated")), ""]
        if "curve" in e:
            p = e["chosen"].get("prob_thresh")
            c = e.get("constraint") or {}
            held = ""
            if c.get("moved_from") is not None:
                held = (f" Moved up from {c['moved_from']:g} by the reagent-control "
                        f"constraint (below).")
            elif c and not c.get("admissible"):
                held = (" **Flagged: no cut on the grid meets the reagent-control "
                        "constraint (below); kept.**")
            lines += [f"Probability cut: **{p if p is not None else e['model_default']['prob_thresh']}**"
                      f"{'' if p is not None else ' (the model default, kept)'}. "
                      f"{'The plateau rule: ' if c else ''}{e['why']}.{held}", "",
                      "| cut | " + " | ".join(f"{c:g}" for c, _ in e["curve"]) + " |",
                      "|---|" + "---|" * len(e["curve"]),
                      "| nuclei | " + " | ".join(str(n) for _, n in e["curve"]) + " |", ""]
        elif e.get("why"):
            lines += [e["why"] + ".", ""]
        lines += constraint_lines(marker, e)
    lines += ["To override, set detection.stardist.prob_thresh (Set up > "
              "Advanced > Nucleus detection), or turn off "
              "detection.stardist.use_auto to use the model's own.", ""]
    return "\n".join(lines)


def constraint_lines(marker, e):
    """The report's paragraph on the reagent-control constraint (provisional)."""
    c = e.get("constraint")
    if c is None:
        note = CONSTRAINT_NOTES.get(marker)
        return [f"Reagent-control constraint: {note}.", ""] if note else []
    out = [f"Reagent-control constraint (provisional): {c['why']}. A cut is "
           f"admissible when the false detections per mm2 on paired no-primary "
           f"controls are at most {c['fp_target']:g} x the detections per mm2 "
           f"on the stained sample. Each control is counted at its stained "
           f"pair's intensity range: "
           + ", ".join(f"{x['control']} at {x['range_from']} ({x['pairing']})"
                       for x in c["controls"]) + ".", ""]
    if c.get("skipped"):
        out += ["Not used: " + "; ".join(f"{x['control']} ({x['why']})"
                                         for x in c["skipped"]) + ".", ""]
    dens = TU.densities(e["curve"], c["control_curve"], c["area_stained_mm2"],
                        c["area_control_mm2"], c["fp_target"])
    out += ["| cut | control_density | stained_density | admissible |",
            "|---|---|---|---|"]
    out += [f"| {p:g} | {v['control_density']:.3g} | {v['stained_density']:.3g} | "
            f"{'yes' if v['admissible'] else 'no'} |" for p, v in sorted(dens.items())]
    out += [""]
    return out


def _fmt(v, pct=False):
    if v is None:
        return "-"
    return f"{v:.2%}" if pct else f"{v:.3g}"


def write_config(entries):
    import json
    with open(CONFIG_PATH, encoding="utf-8") as fh:
        cfg = json.load(fh)
    det = cfg.setdefault("detection", {})
    auto = det.get("auto") if isinstance(det.get("auto"), dict) else {}
    auto.update(entries)
    det["auto"] = auto
    ls_jsonio.write_json_atomic(CONFIG_PATH, cfg, backups=1, compact=("curation",))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--marker", choices=G5.MARKERS,
                    help="one marker (default: every marker)")
    ap.add_argument("--sections", type=int, default=12,
                    help="sections to sample per marker (default 12)")
    ap.add_argument("--rois", type=int, default=3,
                    help="ROIs to sample per section (default 3)")
    ap.add_argument("--window", type=int, default=1024,
                    help="largest crop side in native pixels (default 1024); "
                         "a larger box is cut to its centre, never resampled")
    ap.add_argument("--no-model", action="store_true",
                    help="measure the signal only; do not run StarDist")
    ap.add_argument("--dry-run", action="store_true",
                    help="write the report but leave the study config alone")
    args = ap.parse_args()

    markers = [args.marker] if args.marker else list(G5.MARKERS)
    if not markers:
        print("this study declares no markers")
        return 1
    unanswered = RC.warn_unanswered(CONFIG)
    if unanswered:
        print(unanswered)
    model = None
    if not args.no_model and any(D5.backend_for(m) == "stardist" for m in markers):
        try:
            model = D5.load_model()
        except ImportError as exc:
            print(f"StarDist is not available ({exc}); measuring the signal only")

    entries, rows = {}, []
    for m in markers:
        e, r = tune_marker(m, args, model)
        if e is not None:
            entries[m] = e
            rows += r
            p = e["chosen"].get("prob_thresh")
            print(f"{m}: signal {e['verdict']} - " + "; ".join(e["reasons"]))
            if "curve" in e:
                print(f"{m}: probability cut "
                      f"{p if p is not None else 'kept at the model default'}"
                      f" - {e['why']}")
            c = e.get("constraint")
            if c is not None:
                print(f"{'!! ' if not c['admissible'] else ''}{m}: reagent-control "
                      f"constraint (provisional) - {c['why']}")
            elif CONSTRAINT_NOTES.get(m):
                print(f"{m}: reagent-control constraint {CONSTRAINT_NOTES[m]}")
    if not entries:
        return 1

    os.makedirs(QC, exist_ok=True)
    # Both through a temp file and a swap, so a crash leaves the previous
    # tuning record rather than half of a new one.
    # The constraint's columns only when it ran: zip stops at COLUMNS otherwise.
    cols = ALL_COLUMNS if any("constraint" in e for e in entries.values()) else COLUMNS
    IO.atomic_write_csv(TUNING_CSV, [dict(zip(cols, r)) for r in rows], cols)
    with IO.atomic_save(REPORT_MD) as tmp:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(report(entries))
    print(f"report -> {REPORT_MD}\nper crop -> {TUNING_CSV}")

    for m, e in entries.items():
        if D5.backend_for(m) != "stardist":
            continue
        before = D5.recorded_settings(m, measured=_measured(m))
        after = dict(D5.stardist_settings(m))
        if D5._STARDIST.get("use_auto", True) and D5._STARDIST.get("prob_thresh") is None:
            after["prob_thresh"] = e["chosen"].get("prob_thresh")
        if before is not None and not D5.same_settings(before, after):
            print(f"!! {m} already has sections measured with other thresholds. "
                  f"05c will refuse to resume until every {m} section is "
                  f"re-measured (--force), or use_auto is turned off.")

    if args.dry_run:
        print("--dry-run: the study config was not changed")
    else:
        write_config(entries)
        print(f"tuned settings -> {CONFIG_PATH} (detection.auto)")
    return 0


def _measured(marker):
    if not os.path.exists(D5.NUCLEI_CSV):
        return False
    with open(D5.NUCLEI_CSV, newline="", encoding="utf-8") as fh:
        rd = csv.reader(fh)
        head = next(rd, [])
        col = head.index("marker") if "marker" in head else None
        return any(col is not None and len(r) > col and r[col] == marker for r in rd)


if __name__ == "__main__":
    raise SystemExit(main())
