"""Stage 6k - flag stained nuclei that look like reagent-control false positives.

A FLAG ONLY. No count, rate or density anywhere is changed by it: roi_nuclei.csv,
roi_measurements.csv and roi_corrected.csv are read or not read, never written.
06c shows, beside the raw positive rate, what the rate would be without the
flagged nuclei, and labels it provisional. Censoring on this flag waits for the
validation: the operator hand-labels the review strip of a study with known
controls, and the flag must reach a precision of at least 0.8 at a score of 0.5
on it.

THE MODEL (ls_af_model): positive-unlabelled logistic regression, per marker.
  labelled    above-cut ROI nuclei of the study's UNSTAINED reagent controls
              when there are at least GATE (200) of them for the marker; the
              flag is then `af_suspect` ("autofluorescent"). Otherwise those of
              its NO-PRIMARY controls, at least GATE, and the flag is
              `control_like` ("looks like a reagent-control false positive:
              autofluorescence or non-specific secondary"). Never the two
              pooled; isotype and pre-adsorbed controls are never used. The
              kind is the confirmed entry's own `kind` (ls_reagent_controls),
              never a file name.
  unlabelled  above-cut ROI nuclei of stained sections.
Each stained above-cut nucleus gets af_score, the probability of the control
class, and is flagged at af_score >= FLAG_AT (0.5; the 4-decimal score written
is the one compared, so the file agrees with itself).

"Above the cut" is ls_overlay.is_positive against 06a's cut for that section
and marker: positivity_cuts.csv for a stained section, reagent_control_cuts.csv
(the control's own cut) for a control. A section with no cut has no above-cut
nuclei. Nuclei on an artifact or off the tissue are left out, as 06a leaves
them out; so are background-disc nuclei (only ROI nuclei are counted positive).

THE FEATURES are section-relative (ls_af_model.features_matrix): the marker in
spreads above the section's own background discs, DAPI as a robust z within the
section, the other marker (multiplex only, the one 06l read: 06l's
other_marker) in spreads above its own discs on the same section, size, shape
and texture. The disc and DAPI statistics are recomputed here from
roi_nuclei.csv by ls_overlay.background_stats, the function the cut is formed
by.

ONLY FEATURED SECTIONS. A section counts as featured when 06l logged it in
nucleus_features_done.csv - the features CSV existing says nothing about which
sections it holds - with the fingerprint 06l would give it now
(06l_nucleus_features.fingerprints_now, 06l's own function), and every
above-cut nucleus of it has a row there. A done line whose fingerprint is not
today's is out of date: 05c re-detected or 05a re-boxed the section since 06l
ran, and its nucleus ids may now name other nuclei, so its features are never
joined. The others are skipped, counted and said; the gate counts after that.
A stained section of a scored marker skipped as out of date is listed in
af_scored_sections.csv with role `features_out_of_date`, so 06c can say so.

GATE. Below GATE above-cut nuclei of either kind for a marker, nothing is
written for it and the stage says how many there were. When no marker is
scored, nothing is written at all and the outputs of an earlier run are
removed - a stale af_scores.csv would have 06c subtract flags this study no
longer supports - and the stage exits 1.

Writes:
  results/af_scores.csv           one row per scored nucleus (COLUMNS)
  results/af_scored_sections.csv  the stained sections scored, per marker, with
                                  how many above-cut nuclei and how many
                                  flagged: 06c reads it to tell a section with
                                  no flagged nucleus from one never scored;
                                  with the cut each was scored against, and
                                  the controls trained on with theirs (role),
                                  so 06c can tell flags older than the cut;
                                  and the stained sections skipped because
                                  their 06l features are out of date
  results/af_gate.csv             per marker, its above-cut unstained and
                                  no_primary control nuclei and what it was
                                  trained on (blank: below the gate), so 06c
                                  can say why a marker has no rows; and a
                                  hash of the reagent controls it treated as
                                  controls for the marker (control_set,
                                  ls_reagent_report.control_set_hash of their
                                  scene_uid and kind), so 06c can tell flags
                                  made before a control was confirmed or
                                  removed
  qc/af_review.png                per marker, the 24 highest-scoring stained
                                  nuclei and 24 drawn at random (seed 20261121)
                                  as 64 px crops of the marker channel
  qc/af_review.csv                which nucleus each tile is, with a blank
                                  operator_label column for the hand-labelling

The crops are read from the CZI (pylibCZIrw, through 05c's plane choice); a
crop that cannot be read - the slide drive is not there - is drawn grey and
says so, and the CSVs are written before any crop is read.

Run:  python 06k_af_score.py
"""

import argparse
import array
import collections
import csv
import importlib.util
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from ls_config import CONFIG, CONFIG_PATH  # noqa: E402,F401
import ls_af_model as AF  # noqa: E402
import ls_io as IO  # noqa: E402
import ls_naming as NM  # noqa: E402
import ls_overlay as LO  # noqa: E402
import ls_reagent_report as RR  # noqa: E402

OUT_ROOT = CONFIG["out_root"]
RESULTS = os.path.join(OUT_ROOT, "results")
QC = os.path.join(OUT_ROOT, "qc")
NUCLEI_CSV = os.path.join(RESULTS, "roi_nuclei.csv")
CUTS_CSV = os.path.join(RESULTS, "positivity_cuts.csv")
RC_CUTS_CSV = os.path.join(RESULTS, "reagent_control_cuts.csv")
FEATURES_CSV = os.path.join(RESULTS, "nucleus_features.csv")
DONE_CSV = os.path.join(RESULTS, "nucleus_features_done.csv")
SCORES_CSV = os.path.join(RESULTS, "af_scores.csv")
SECTIONS_CSV = os.path.join(RESULTS, "af_scored_sections.csv")
GATE_CSV = os.path.join(RESULTS, "af_gate.csv")
REVIEW_PNG = os.path.join(QC, "af_review.png")
REVIEW_CSV = os.path.join(QC, "af_review.csv")

COLUMNS = ["scene_uid", "marker", "roi_index", "nucleus_id", "af_score", "flag_name",
           "flagged", "trained_on"]
SECTION_COLUMNS = ["scene_uid", "marker", "role", "cut", "n_scored", "n_flagged",
                   "flag_name", "trained_on"]
GATE_COLUMNS = ["marker", "n_unstained", "n_no_primary", "trained_on", "reason",
                "control_set"]
REVIEW_COLUMNS = ["marker", "strip", "position", "scene_uid", "roi_index", "nucleus_id",
                  "af_score", "flagged", "flag_name", "trained_on", "operator_label"]

#: Fewest above-cut control nuclei of the chosen kind, per marker, to train on.
GATE = 200
#: A nucleus is flagged at this score or above.
FLAG_AT = 0.5
#: The model's seed, and the review strip's random draw.
SEED = AF.SEED
#: The review strip: this many tiles per row, each TILE px square.
N_REVIEW = 24
TILE = 64
#: The kinds, in the order they are tried, and the flag each one names.
FLAG_NAMES = {"unstained": "af_suspect", "no_primary": "control_like"}

_FEATURE_FIELDS = ["roundness", "solidity", "marker_cv", "marker_spots",
                   "other_marker_median"]
_NUCLEUS_FIELDS = ["area_um2", "equiv_diam_um", "dapi_mean", "marker_median",
                   "marker_p90", "czi_x", "czi_y"]


# ---------------------------------------------------------------- 06l, lazily

_L6 = []


def stage_06l():
    """06l loaded once, on first use: its fingerprint of a section's features
    (fingerprints_now), its other_marker() rule and, through it, 05c's plane
    choice and 05a's geometry. Not at import: a run refused for a missing input
    never needs it."""
    if not _L6:
        spec = importlib.util.spec_from_file_location(
            "_l6_for_06k", os.path.join(_HERE, "06l_nucleus_features.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _L6.append(mod)
    return _L6[0]


def other_marker_of(marker):
    """The marker 06l read other_marker_median from, or None (paired)."""
    if not RR.study_markers(CONFIG):
        return None
    return stage_06l().other_marker(marker)


# ---------------------------------------------------------------- inputs


def read_cuts(path):
    """{(scene_uid, marker): cut} from a cuts CSV 06a wrote; {} when absent."""
    if not os.path.exists(path):
        return {}
    with open(path, newline="", encoding="utf-8") as fh:
        return {(r["scene_uid"], r["marker"]): float(r["cut"])
                for r in csv.DictReader(fh) if r.get("cut") not in (None, "")}


def read_done():
    """{(scene_uid, marker): fingerprint} 06l logged as written whole, the last
    line for a section winning (as 06l reads it)."""
    if not os.path.exists(DONE_CSV):
        return {}
    with open(DONE_CSV, newline="", encoding="utf-8") as fh:
        return {(r["scene_uid"], r["marker"]): r.get("fingerprint") or ""
                for r in csv.DictReader(fh) if r.get("scene_uid") and r.get("marker")}


def current_done(done):
    """(current, out of date): the done keys whose fingerprint is the one 06l
    gives the section now, and the rest. A marker 06l cannot fingerprint (its
    boxes are gone) has none current: 06l would drop its features too."""
    now = stage_06l().fingerprints_now() if done else {}
    cur = {k for k, fp in done.items() if fp and now.get((k[1], k[0])) == fp}
    return cur, set(done) - cur


def control_kinds():
    """{(scene_uid, marker): kind} of every confirmed reagent control."""
    return {k: (e or {}).get("kind") for k, e in RR.study_lookup(CONFIG).items()}


def scan_nuclei(cuts, kinds):
    """One pass over roi_nuclei.csv.

    Returns (candidates, section_stats, sections):
      candidates     {(uid, marker, roi_index, nucleus_id): row} - ROI nuclei
                     above their section's cut, on a stained section or a
                     control of a kind used here
      section_stats  {(uid, marker): {"bg": (median, spread) | None,
                     "dapi": (median, spread) | None}} for every section
      sections       {(uid, marker)} that have a cut and are of use here
    """
    bg, dapi = {}, {}
    cand = {}
    with open(NUCLEI_CSV, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r.get("artifact") == "1" or r.get("off_tissue") == "1":
                continue
            key = (r["scene_uid"], r["marker"])
            try:
                d = float(r["dapi_mean"])
            except (KeyError, TypeError, ValueError):
                d = None
            if d is not None and np.isfinite(d):
                dapi.setdefault(key, array.array("d")).append(d)
            cen = r.get("censored") == "1"
            if r.get("roi_kind") == "background":
                if not cen:
                    bg.setdefault(key, array.array("d")).append(float(r["marker_median"]))
                continue
            if r.get("roi_kind") != "roi" or key not in cuts:
                continue
            kind = kinds.get(key)
            if key in kinds and kind not in FLAG_NAMES:
                continue
            if not LO.is_positive(float(r["marker_median"]), cen, cuts[key]):
                continue
            cand[(r["scene_uid"], r["marker"], int(r["roi_index"]), int(r["nucleus_id"]))] = {
                "scene_uid": r["scene_uid"], "marker": r["marker"],
                **{f: r.get(f, "") for f in _NUCLEUS_FIELDS}}
    stats = {}
    for key in set(bg) | set(dapi):
        b = LO.background_stats(list(bg.get(key, ())))
        dd = LO.background_stats(list(dapi.get(key, ())))
        stats[key] = {"bg": b[:2] if b else None, "dapi": dd[:2] if dd else None}
    sections = {k for k in set(bg) | set(dapi) if k in cuts
                and (k not in kinds or kinds[k] in FLAG_NAMES)}
    return cand, stats, sections


def join_features(cand, featured):
    """Attach 06l's features to each candidate of a featured section, in place.

    Returns the featured sections that turned out to lack a row for one of
    their above-cut nuclei: those are treated as not featured."""
    if not os.path.exists(FEATURES_CSV):
        return set(featured)
    with open(FEATURES_CSV, newline="", encoding="utf-8") as fh:
        rd = csv.reader(fh)
        head = next(rd, None) or []
        try:
            iu, im = head.index("scene_uid"), head.index("marker")
            ir, inid = head.index("roi_index"), head.index("nucleus_id")
            idx = {f: head.index(f) for f in _FEATURE_FIELDS}
        except ValueError:
            raise SystemExit(f"!! {FEATURES_CSV} does not have 06l's columns - run "
                             f"06l_nucleus_features.py again") from None
        need = max([iu, im, ir, inid] + list(idx.values()))
        for row in rd:
            if len(row) <= need or (row[iu], row[im]) not in featured:
                continue
            try:
                k = (row[iu], row[im], int(row[ir]), int(row[inid]))
            except ValueError:
                continue
            c = cand.get(k)
            if c is not None:
                c.update({f: row[i] for f, i in idx.items()})
                c["_featured"] = True
    return {(u, m) for (u, m, _r, _n), c in cand.items()
            if (u, m) in featured and not c.get("_featured")}


# ---------------------------------------------------------------- the model, per marker


def choose(n_by_kind):
    """The kind trained on, or None: unstained at GATE, else no_primary at GATE."""
    for kind in FLAG_NAMES:
        if n_by_kind.get(kind, 0) >= GATE:
            return kind
    return None


def score_marker(marker, controls, stained, stats):
    """[(candidate key, af_score)] for the stained nuclei of one marker."""
    other = other_marker_of(marker)
    rows = []
    for _k, c in controls + stained:
        rows.append(dict(c, other_marker=other or ""))
    X, _names = AF.features_matrix(rows, stats)
    y = np.r_[np.ones(len(controls)), np.zeros(len(stained))]
    model = AF.fit(X, y, seed=SEED)
    p = AF.predict(model, X[len(controls):])
    return [(k, round(float(s), 4)) for (k, _c), s in zip(stained, p)]


def _order(k):
    return (k[1], NM.natural_key(k[0]), k[2], k[3])


# ---------------------------------------------------------------- the review strip


def review_items(scored, marker):
    """(top, random): up to N_REVIEW each, of one marker's scored nuclei."""
    mine = sorted(((k, s) for k, s in scored if k[1] == marker),
                  key=lambda t: (-t[1], _order(t[0])))
    top = mine[:N_REVIEW]
    rest = sorted(mine[N_REVIEW:], key=lambda t: _order(t[0]))
    rng = np.random.default_rng(SEED)
    pick = sorted(rng.choice(len(rest), size=min(N_REVIEW, len(rest)), replace=False)) \
        if rest else []
    return top, [rest[i] for i in pick]


def read_crops(marker, items, cand):
    """{candidate key: uint8 TILE x TILE crop}, for the items that could be read.

    Centred on the nucleus's czi_x / czi_y, on 05c's plane for the marker, with
    05c's display stretch (ls_overlay.stretch) per crop. Anything that cannot
    be read - no geometry, no slide, no pylibCZIrw - is left out and drawn grey."""
    out = {}
    try:
        L6 = stage_06l()
        D5, G5, CR = L6.D5, L6.G5, L6.CR
        from pylibCZIrw import czi as pyczi
        G5.use_marker(marker)
        geom = {g["scene_uid"]: g for g in G5.load_csv(G5.GEOM_CSV)} \
            if os.path.exists(G5.GEOM_CSV) else {}
        plane = D5.plane_index_for(marker)
    except Exception as exc:  # noqa: BLE001 - a picture, never a reason to fail
        print(f"  {marker}: review crops not read ({type(exc).__name__}: {exc}); "
              f"drawn grey")
        return out
    by_uid = {}
    for k, _s in items:
        by_uid.setdefault(k[0], []).append(k)
    for uid, keys in sorted(by_uid.items()):
        g = geom.get(uid)
        if not g:
            print(f"  {marker} {uid}: no geometry row for this section; its review "
                  f"crops are drawn grey")
            continue
        path = os.path.join(CONFIG["source_dir"], g["czi_file"])
        try:
            with CR.open_czi(path, int(float(g.get("scene_w") or 0)) or None,
                             pyczi=pyczi) as doc:
                for k in keys:
                    c = cand[k]
                    x0 = int(round(float(c["czi_x"]))) - TILE // 2
                    y0 = int(round(float(c["czi_y"]))) - TILE // 2
                    a = CR.read_planes(doc, D5._Rect(x0, y0, TILE, TILE), {"mark": plane},
                                       scene=int(g["scene_index"]))["mark"]
                    a = np.asarray(a, dtype=np.float64)
                    if a.shape != (TILE, TILE):
                        # At the scene's edge the read comes back short.
                        print(f"  {marker} {uid} nucleus {k[3]}: crop read {a.shape}, "
                              f"not {TILE} x {TILE}; drawn grey")
                        continue
                    lo, hi = LO.stretch(a)
                    out[k] = LO.grey(a, lo, hi)
        except Exception as exc:  # noqa: BLE001
            print(f"  {marker} {uid}: review crops not read ({type(exc).__name__}: "
                  f"{exc}); drawn grey")
    return out


def draw_strip(blocks):
    """The review picture: per (title, [(caption, crop or None)]) a title band
    and one row of tiles, each with its score under it."""
    from PIL import Image, ImageDraw
    cap, band = 14, 18
    width = N_REVIEW * TILE
    height = sum(band + TILE + cap for _ in blocks) or band
    img = Image.new("RGB", (width, height), (0, 0, 0))
    dr = ImageDraw.Draw(img)
    y = 0
    for title, tiles in blocks:
        dr.text((4, y + 3), title, fill=(255, 255, 255))
        y += band
        for i, (caption, crop) in enumerate(tiles):
            x = i * TILE
            if crop is None:
                dr.rectangle([x + 1, y + 1, x + TILE - 2, y + TILE - 2], fill=(60, 60, 60))
                dr.text((x + 6, y + TILE // 2 - 6), "no image", fill=(200, 200, 200))
            else:
                img.paste(Image.fromarray(crop).convert("RGB"), (x, y))
            dr.text((x + 3, y + TILE + 1), caption, fill=(255, 255, 255))
        y += TILE + cap
    LO.save_png(REVIEW_PNG, np.asarray(img))


# ---------------------------------------------------------------- the run


def remove_outputs():
    gone = []
    for p in (SCORES_CSV, SECTIONS_CSV, GATE_CSV, REVIEW_PNG, REVIEW_CSV):
        if os.path.exists(p):
            os.remove(p)
            gone.append(os.path.basename(p))
    return gone


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.parse_args(argv)

    for path, what in ((NUCLEI_CSV, "05c_detect_rois.py"), (CUTS_CSV, "06a_roi_dataset.py"),
                       (DONE_CSV, "06l_nucleus_features.py")):
        if not os.path.exists(path):
            gone = remove_outputs()
            print(f"no {path} - run {what} first. Nothing written"
                  + (f"; removed the earlier {', '.join(gone)}" if gone else "") + ".")
            return 1
    cuts = read_cuts(CUTS_CSV)
    rc_cuts = read_cuts(RC_CUTS_CSV)
    kinds = control_kinds()
    # A control's above-cut nuclei are counted against ITS OWN cut, a stained
    # section's against its own; the two files never share a key.
    all_cuts = {**cuts, **{k: v for k, v in rc_cuts.items() if k in kinds}}
    cand, stats, sections = scan_nuclei(all_cuts, kinds)

    done, out_of_date = current_done(read_done())
    stale_feats = sorted((k for k in sections if k in out_of_date),
                         key=lambda k: (k[1], NM.natural_key(k[0])))
    if stale_feats:
        print(f"{len(stale_feats)} section(s) skipped as their 06l features are out of "
              f"date: re-detected or re-boxed since 06l ran, so their nucleus ids may "
              f"name other nuclei - run 06l_nucleus_features.py (e.g. "
              f"{stale_feats[0][1]} {stale_feats[0][0]})")
    featured = {k for k in sections if k in done}
    featured -= join_features(cand, featured)
    skipped = sorted(sections - featured - set(stale_feats),
                     key=lambda k: (k[1], NM.natural_key(k[0])))
    if skipped:
        print(f"{len(skipped)} section(s) skipped as not yet featured: no complete "
              f"line in nucleus_features_done.csv - run 06l_nucleus_features.py "
              f"(e.g. {skipped[0][1]} {skipped[0][0]})")

    by_marker = {}
    for k, c in sorted(cand.items(), key=lambda t: _order(t[0])):
        if (k[0], k[1]) not in featured:
            continue
        kind = kinds.get((k[0], k[1]))
        by_marker.setdefault(k[1], {"unstained": [], "no_primary": [], "stained": []})[
            kind if kind else "stained"].append((k, c))
    for mk in sorted({m for _u, m in kinds if kinds[(_u, m)] in FLAG_NAMES}
                     - set(by_marker)):
        by_marker[mk] = {"unstained": [], "no_primary": [], "stained": []}

    scored, sec_rows, info, gate_rows = [], [], {}, []
    for mk in sorted(by_marker):
        g = by_marker[mk]
        n = {kind: len(g[kind]) for kind in FLAG_NAMES}
        kind = choose(n)
        gate_rows.append({"control_set": RR.control_set_hash(
                              (u, kd) for (u, m), kd in kinds.items() if m == mk),
                          "marker": mk, "n_unstained": n["unstained"],
                          "n_no_primary": n["no_primary"],
                          "trained_on": kind if kind and g["stained"] else "",
                          # Why not scored: below the training gate, or past it
                          # with no featured stained nucleus above the cut.
                          "reason": ("below_gate" if kind is None
                                     else "" if g["stained"] else "no_stained")})
        if kind is None:
            print(f"{mk}: not scored - {n['unstained']} above-cut nuclei on unstained "
                  f"reagent controls and {n['no_primary']} on no_primary ones (featured "
                  f"sections only), fewer than the {GATE} needed of one kind. Nothing "
                  f"written for it.")
            continue
        if not g["stained"]:
            print(f"{mk}: not scored - no above-cut nucleus on a featured stained section")
            continue
        flag = FLAG_NAMES[kind]
        got = score_marker(mk, g[kind], g["stained"], stats)
        scored += got
        info[mk] = (flag, kind)
        n_flag = sum(1 for _k, s in got if s >= FLAG_AT)
        print(f"{mk}: trained on {n[kind]} above-cut {kind} reagent-control nuclei "
              f"against {len(g['stained'])} stained; flag {flag}: {n_flag} of "
              f"{len(got)} stained above-cut nuclei at score >= {FLAG_AT}"
              + (f" ({n['unstained']} unstained, under {GATE}, not used)"
                 if kind == "no_primary" and n["unstained"] else ""))
        per = {}
        for k, s in got:
            t = per.setdefault((k[0], k[1]), [0, 0])
            t[0] += 1
            t[1] += s >= FLAG_AT
        # The cut each section was scored against, and each control trained
        # on: 06c blanks a row whose flags were made under another cut.
        stained_secs = {s for s in featured if s[1] == mk and s not in kinds}
        for u, m in sorted(stained_secs, key=lambda s: NM.natural_key(s[0])):
            t = per.get((u, m), [0, 0])
            sec_rows.append({"scene_uid": u, "marker": m, "role": "stained",
                             "cut": repr(all_cuts[(u, m)]), "n_scored": t[0],
                             "n_flagged": t[1], "flag_name": flag, "trained_on": kind})
        for u, m in [s for s in stale_feats if s[1] == mk and s not in kinds]:
            sec_rows.append({"scene_uid": u, "marker": m, "role": "features_out_of_date",
                             "cut": repr(all_cuts[(u, m)]), "n_scored": "",
                             "n_flagged": "", "flag_name": flag, "trained_on": kind})
        used = collections.Counter((k[0], k[1]) for k, _c in g[kind])
        for u, m in sorted({s for s in featured if s[1] == mk and kinds.get(s) == kind},
                           key=lambda s: NM.natural_key(s[0])):
            sec_rows.append({"scene_uid": u, "marker": m, "role": "control",
                             "cut": repr(all_cuts[(u, m)]), "n_scored": used[(u, m)],
                             "n_flagged": "", "flag_name": flag, "trained_on": kind})

    if not scored:
        gone = remove_outputs()
        print("nothing scored - no af_scores.csv written"
              + (f" (removed the earlier {', '.join(gone)})" if gone else ""))
        return 1

    rows = [{"scene_uid": k[0], "marker": k[1], "roi_index": k[2], "nucleus_id": k[3],
             "af_score": repr(s), "flag_name": info[k[1]][0],
             "flagged": 1 if s >= FLAG_AT else 0, "trained_on": info[k[1]][1]}
            for k, s in sorted(scored, key=lambda t: _order(t[0]))]
    IO.atomic_write_csv(SCORES_CSV, rows, COLUMNS)
    IO.atomic_write_csv(SECTIONS_CSV, sec_rows, SECTION_COLUMNS)
    IO.atomic_write_csv(GATE_CSV, gate_rows, GATE_COLUMNS)
    print(f"{len(rows)} stained nuclei scored -> {SCORES_CSV}")

    # The review strip, after the CSVs: a crop that cannot be read is grey.
    blocks, key_rows = [], []
    for mk in sorted(info):
        flag, kind = info[mk]
        top, rnd = review_items(scored, mk)
        crops = read_crops(mk, top + rnd, cand)
        for strip, items, title in (
                ("top", top, f"{mk}: the {len(top)} highest af_score (flag {flag}, "
                             f"trained on {kind}) - provisional"),
                ("random", rnd, f"{mk}: {len(rnd)} at random (seed {SEED})")):
            blocks.append((title, [(f"{s:.2f}", crops.get(k)) for k, s in items]))
            for i, (k, s) in enumerate(items, 1):
                key_rows.append({"marker": mk, "strip": strip, "position": i,
                                 "scene_uid": k[0], "roi_index": k[2], "nucleus_id": k[3],
                                 "af_score": repr(s), "flagged": 1 if s >= FLAG_AT else 0,
                                 "flag_name": flag, "trained_on": kind,
                                 "operator_label": ""})
    os.makedirs(QC, exist_ok=True)
    IO.atomic_write_csv(REVIEW_CSV, key_rows, REVIEW_COLUMNS)
    draw_strip(blocks)
    print(f"review strip -> {REVIEW_PNG} (key: {REVIEW_CSV})")
    print("PROVISIONAL: a flag only, no count is changed. Validate on a study with known "
          "controls (hand-label the strip; precision >= 0.8 at score 0.5) before any "
          "censoring.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
