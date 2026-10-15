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
import ls_jsonio  # noqa: E402
import ls_naming as NM  # noqa: E402
import ls_segment as SG  # noqa: E402
import ls_tune as TU  # noqa: E402

G5 = D5.G5
QC = os.path.join(D5.OUT_ROOT, "qc")
TUNING_CSV = os.path.join(QC, "detection_tuning.csv")
REPORT_MD = os.path.join(QC, "detection_tuning.md")
COLUMNS = ["marker", "scene_uid", "animal", "roi_index", "segmented_on",
           "width", "height", "background", "foreground", "noise", "snr",
           "contrast", "coverage", "saturated"]

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


def spread(items, n):
    if len(items) <= n:
        return list(items)
    return [items[int((i + 0.5) * len(items) / n)] for i in range(n)]


def window(b, side):
    """The box's central window of at most side x side native pixels."""
    x0, y0, w, h = int(b["czi_x0"]), int(b["czi_y0"]), int(b["czi_w"]), int(b["czi_h"])
    cw, ch = min(w, side), min(h, side)
    return D5._Rect(x0 + (w - cw) // 2, y0 + (h - ch) // 2, cw, ch)


def read_crops(marker, uids, by_sec, geom, rois, side, model_input=False):
    """[(uid, roi_index, box, crop, x)] off the plane 05c would segment.

    `crop` is the box's central window, raw. `x` is that same window of the
    WHOLE box as 05c gives it to StarDist (ls_segment.stardist_input over the
    box, under 05c's section range when the marker shares one), or None when
    `model_input` is off or the section has no range to share - 05c skips such
    a section, so it has no input to tune on either.
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
        with CR.open_czi(path, int(float(g.get("scene_w") or 0)) or None,
                         pyczi=pyczi) as doc:
            norm, usable = None, model_input
            if shared:
                # 05c's own helper over the section's whole box list, so the
                # range is the one 05c will stretch by, not one of the sample.
                try:
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
                out.append((uid, bi, b, full[cut], x))
    return out


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
    uids = sample_sections([u for u in by_sec if u in geom], args.sections)
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

    out_rows = []
    for (uid, bi, b, crop, _x), q in zip(crops, rows):
        out_rows.append([marker, uid, NM.subject_of(uid), bi, seg_on,
                         crop.shape[1], crop.shape[0]]
                        + [round(q[k], 4) for k in COLUMNS[7:]])
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
            lines += [f"Probability cut: **{p if p is not None else e['model_default']['prob_thresh']}**"
                      f"{'' if p is not None else ' (the model default, kept)'}. {e['why']}.", "",
                      "| cut | " + " | ".join(f"{c:g}" for c, _ in e["curve"]) + " |",
                      "|---|" + "---|" * len(e["curve"]),
                      "| nuclei | " + " | ".join(str(n) for _, n in e["curve"]) + " |", ""]
        elif e.get("why"):
            lines += [e["why"] + ".", ""]
    lines += ["To override, set detection.stardist.prob_thresh (Set up > "
              "Advanced > Nucleus detection), or turn off "
              "detection.stardist.use_auto to use the model's own.", ""]
    return "\n".join(lines)


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
    if not entries:
        return 1

    os.makedirs(QC, exist_ok=True)
    with open(TUNING_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(COLUMNS)
        w.writerows(rows)
    with open(REPORT_MD, "w", encoding="utf-8") as fh:
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
