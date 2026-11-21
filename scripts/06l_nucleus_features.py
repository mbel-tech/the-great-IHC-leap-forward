"""Stage 6l - shape and texture of every measured nucleus. A backfill.

roi_nuclei.csv says how large each nucleus is and how bright its DAPI and its
marker are. The autofluorescence score (06k) also wants to know its shape and
how its marker is spread inside it: autofluorescent debris and lipofuscin are
granular and often not round, a stained nucleus is filled. This adds those,
one row per roi_nuclei.csv row, to `results/nucleus_features.csv`, keyed
exactly as roi_nuclei.csv is (scene_uid, marker, roi_index, nucleus_id):

  roundness            4 pi A / P^2 of the nucleus's mask
  solidity             its area over its convex hull's
  marker_cv            sd of the marker inside it over its mean ABOVE the box's
                       own glass (offset- and gain-free; see ls_nucleus_features)
  marker_spots         local maxima above its median + 3 x 1.4826 x MAD
  other_marker_median  under multiplex, the other marker's median inside it;
                       blank under paired, where the other marker is a
                       different section

THE DETECTION IS NOT RUN AGAIN. The nuclei are the label images 05c saved
(detection_outlines/<marker>/<uid>.npz, one `roi<NN>` per box) on the boxes
05a wrote; only the marker plane (and, under multiplex, the other marker's) is
read from the CZI for each box that has nuclei. StarDist is never loaded. A
section with no saved outlines - measured before 05c saved them - is listed as
not done and skipped: `06i_detection_overlays.py` saves them once (it
re-segments under the settings the rows were measured with), and this stage
then picks the section up. Its old features, if any, are dropped rather than
kept against outlines that are gone.

roi_nuclei.csv is read, never written.

Every check that ties a label to a row is made before a section is read: each
box's saved origin and shape must be the box's now, and every nucleus_id in
roi_nuclei.csv must be a label in its box. A section that fails is not done,
with the reason printed; its rows here are dropped.

The other marker under multiplex is the first other marker of the study's
declared order whose plane differs from this one's. A study of more than two
markers gets that one marker's level only.

RESUMES PER SECTION. Each section's rows are appended to the CSV and synced,
then one line goes to `results/nucleus_features_done.csv` with a fingerprint
of what the rows were computed from: the feature version, the outline file
(size and mtime), the section's (roi_index, nucleus_id) list, its boxes, its
CZI file and scene, and the planes read. A run reads only the sections not
logged with today's fingerprint. A section's rows with no done line (a run
that died between the two writes) or a stale one (re-detected, re-curated) are
dropped first, through a temp file and one replace; a torn last line is cut
back. So a stopped run, a re-run and a --force all leave one row per nucleus.

--limit N reads at most N sections and stops; run again to go on. --force
reads every section again, even those with current features (with --limit, the
first N of them; the others keep their features). --marker restricts to one
marker. A CZI read that fails because the slide drive went away is waited out
and the section read again (ls_drive), exactly as 05c and 06i do.

Run:  python 06l_nucleus_features.py [--marker M] [--limit N] [--force]
"""

import argparse
import csv
import hashlib
import importlib.util
import json
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from ls_config import CONFIG, CONFIG_PATH  # noqa: E402,F401
import czi_read as CR  # noqa: E402
import ls_channels as CH  # noqa: E402
import ls_io as IO  # noqa: E402
import ls_nucleus_features as NF  # noqa: E402

# 05c is loaded, not imported by name: its file name starts with a digit. Its
# outline file, plane choice, drive handling and file repairs are used as
# they are, so the nuclei here are the ones it counted.
_spec = importlib.util.spec_from_file_location(
    "_d5_for_06l", os.path.join(_HERE, "05c_detect_rois.py"))
D5 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(D5)
G5 = D5.G5

RESULTS = D5.RESULTS
NUCLEI_CSV = D5.NUCLEI_CSV
FEATURES_CSV = os.path.join(RESULTS, "nucleus_features.csv")
DONE_CSV = os.path.join(RESULTS, "nucleus_features_done.csv")

KEYS = ["scene_uid", "marker", "roi_index", "nucleus_id"]
FEATURE_COLUMNS = ["roundness", "solidity", "marker_cv", "marker_spots",
                   "other_marker_median"]
COLUMNS = KEYS + FEATURE_COLUMNS
DONE_COLUMNS = ["marker", "scene_uid", "fingerprint"]
#: Bump when a feature's definition changes: every section is then read again.
FEATURE_VERSION = 1
#: Sections whose roi_nuclei.csv rows are held at once (as 06i): the file is
#: millions of rows, scanned once per batch rather than held whole.
BATCH = 32
#: The box fields a section's features depend on: the rectangle read.
BOX_FIELDS = ["czi_x0", "czi_y0", "czi_w", "czi_h"]


class NotDone(Exception):
    """A section that is not done; the message says why."""


# ---------------------------------------------------------------- inputs


def other_marker(marker):
    """The marker whose median goes in other_marker_median, or None.

    Only under multiplex, where the markers are planes of the one scan of one
    section. Under paired the other marker was scanned on another section.
    """
    if G5.LAYOUT != CH.LAYOUT_MULTIPLEX:
        return None
    mine = D5.plane_index_for(marker)
    for m in G5.MARKERS:
        if m != marker and D5.plane_index_for(m) != mine:
            return m
    return None


def scan_nuclei(marker, uids=None):
    """One pass over roi_nuclei.csv for `marker`.

    With `uids` None: {uid: sha1 of its (roi_index, nucleus_id) list} for every
    section. With `uids`: {uid: [(roi_index, nucleus_id), ...]} for those.
    """
    out = {}
    with open(NUCLEI_CSV, newline="", encoding="utf-8") as fh:
        rd = csv.reader(fh)
        header = next(rd, None) or []
        try:
            iu, im = header.index("scene_uid"), header.index("marker")
            ir, inid = header.index("roi_index"), header.index("nucleus_id")
        except ValueError:
            raise SystemExit(f"!! {NUCLEI_CSV} has no scene_uid / marker / "
                             f"roi_index / nucleus_id column") from None
        need = max(iu, im, ir, inid)
        for row in rd:
            if len(row) <= need or row[im] != marker:
                continue
            uid = row[iu]
            if uids is None:
                h = out.get(uid)
                if h is None:
                    h = out[uid] = hashlib.sha1()
                h.update(f"{row[ir]},{row[inid]};".encode("ascii", "replace"))
            elif uid in uids:
                out.setdefault(uid, []).append((int(row[ir]), int(row[inid])))
    if uids is None:
        return {u: h.hexdigest() for u, h in out.items()}
    return out


def fingerprint(marker, uid, keys_hash, boxes, g):
    """What a section's features are computed from, hashed (see the docstring)."""
    path = D5.outline_path(marker, uid)
    try:
        st = os.stat(path)
        npz = [int(st.st_size), int(st.st_mtime_ns)]
    except OSError:
        npz = None
    other = other_marker(marker)
    payload = [FEATURE_VERSION, NF.SPOT_K, keys_hash, npz,
               [[b.get(f, "") for f in BOX_FIELDS] for b in boxes],
               (g or {}).get("czi_file", ""), (g or {}).get("scene_index", ""),
               D5.plane_index_for(marker),
               None if other is None else [other, D5.plane_index_for(other)]]
    return hashlib.sha1(json.dumps(payload).encode("utf-8")).hexdigest()[:16]


def load_outlines(marker, uid):
    """{roi_index: (labels, x0, y0)} from the saved npz - never re-segmented."""
    out = {}
    with np.load(D5.outline_path(marker, uid)) as npz:
        for k in npz.files:
            if k.startswith("roi") and not k.endswith("_origin"):
                x0, y0 = (int(v) for v in npz[k + "_origin"])
                out[int(k[3:])] = (npz[k], x0, y0)
    return out


def check(outlines, keys, boxes):
    """{roi_index: [nucleus_id, ...]} to measure; NotDone when the saved
    outlines do not hold what roi_nuclei.csv says they do."""
    want = {}
    for ri, nid in keys:
        want.setdefault(ri, []).append(nid)
    for ri, nids in sorted(want.items()):
        if ri < 1 or ri > len(boxes):
            raise NotDone(f"roi{ri:02d}: roi_nuclei.csv has rows for a box the "
                          f"section does not have ({len(boxes)} boxes)")
        if ri not in outlines:
            raise NotDone(f"roi{ri:02d}: no outlines saved for this box")
        labels, x0, y0 = outlines[ri]
        b = boxes[ri - 1]
        if (x0, y0) != (int(b["czi_x0"]), int(b["czi_y0"])):
            raise NotDone(f"roi{ri:02d}: outlines were made at ({x0}, {y0}), the "
                          f"box is now at ({b['czi_x0']}, {b['czi_y0']})")
        if labels.shape != (int(b["czi_h"]), int(b["czi_w"])):
            raise NotDone(f"roi{ri:02d}: outlines are {labels.shape}, the box is "
                          f"{b['czi_h']}x{b['czi_w']}")
        if len(set(nids)) != len(nids):
            raise NotDone(f"roi{ri:02d}: a nucleus_id is in roi_nuclei.csv twice")
        present = set(np.unique(labels).tolist())
        missing = [n for n in nids if n not in present]
        if missing:
            raise NotDone(f"roi{ri:02d}: nucleus_id {missing[0]} is not in the "
                          f"saved outlines ({len(missing)} missing)")
    return want


def _fmt(v, nd):
    if v == "" or v is None:
        return ""
    return repr(round(float(v), nd))


def measure(doc, marker, uid, boxes, outlines, want, scene):
    """The feature rows of one section, in roi_index then nucleus_id order."""
    other = other_marker(marker)
    planes = {"mark": D5.plane_index_for(marker)}
    if other is not None:
        planes["other"] = D5.plane_index_for(other)
    rows = []
    for ri in sorted(want):
        b = boxes[ri - 1]
        labels, x0, y0 = outlines[ri]
        got = CR.read_planes(doc, D5._Rect(x0, y0, int(b["czi_w"]), int(b["czi_h"])),
                             planes, scene=scene)
        mark = got["mark"].astype(np.float64)
        if mark.shape != labels.shape:
            raise NotDone(f"roi{ri:02d}: the marker plane read {mark.shape}, the "
                          f"outlines are {labels.shape}")
        oth = got["other"].astype(np.float64) if "other" in got else None
        bbox = NF.bboxes(labels)
        glass = NF.background(labels, mark)
        for nid in sorted(want[ri]):
            f = NF.features(labels, nid, None, mark, oth, bbox=bbox[nid],
                            background=glass)
            rows.append([uid, marker, ri, nid, _fmt(f["roundness"], 4),
                         _fmt(f["solidity"], 4), _fmt(f["marker_cv"], 4),
                         f["marker_spots"], _fmt(f["other_marker_median"], 1)])
    return rows


# ---------------------------------------------------------------- the checkpoint


def _read_done():
    """{(marker, uid): fingerprint}, the last line for a section winning."""
    out = {}
    if os.path.exists(DONE_CSV):
        with open(DONE_CSV, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r.get("marker") and r.get("scene_uid"):
                    out[(r["marker"], r["scene_uid"])] = r.get("fingerprint") or ""
    return out


def _header(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return next(csv.reader(fh), None)


def prepare_files():
    """Start both files when absent, or over when written to other columns;
    cut back a torn last line."""
    for path, cols in ((FEATURES_CSV, COLUMNS), (DONE_CSV, DONE_COLUMNS)):
        if os.path.exists(path) and _header(path) != cols:
            print(f"  {os.path.basename(path)} has other columns - starting it over")
            for p in (FEATURES_CSV, DONE_CSV):
                if os.path.exists(p):
                    os.remove(p)
    for path, cols in ((FEATURES_CSV, COLUMNS), (DONE_CSV, DONE_COLUMNS)):
        if not os.path.exists(path):
            IO.atomic_write_csv(path, [], cols)
        elif D5.repair_torn_tail(path):
            print(f"  {os.path.basename(path)}: a torn last line was cut back")


def keep_only(keep):
    """Rewrite both files to the sections in `keep` ({(marker, uid): fp}).

    The done log first: a crash between the two leaves rows with no done line,
    which the next run drops. Each through a temp file and one replace; nothing
    is rewritten when nothing would change."""
    with open(DONE_CSV, newline="", encoding="utf-8") as fh:
        lines = [(r.get("marker"), r.get("scene_uid"), r.get("fingerprint"))
                 for r in csv.DictReader(fh)]
    if sorted(lines) != sorted((m, u, fp) for (m, u), fp in keep.items()):
        IO.atomic_write_csv(DONE_CSV, [{"marker": m, "scene_uid": u, "fingerprint": fp}
                                       for (m, u), fp in sorted(keep.items())],
                            DONE_COLUMNS)
    with open(FEATURES_CSV, newline="", encoding="utf-8") as fh:
        rd = csv.reader(fh)
        next(rd, None)
        stale = any((r[1], r[0]) not in keep for r in rd if len(r) > 1)
    if not stale:
        return 0
    tmp = FEATURES_CSV + ".tmp"
    dropped = 0
    with open(FEATURES_CSV, newline="", encoding="utf-8") as rf, \
            open(tmp, "w", newline="", encoding="utf-8") as tf:
        rd, tw = csv.reader(rf), csv.writer(tf)
        tw.writerow(next(rd, None) or COLUMNS)
        for r in rd:
            if len(r) > 1 and (r[1], r[0]) in keep:
                tw.writerow(r)
            else:
                dropped += 1
        tf.flush()
        os.fsync(tf.fileno())
    os.replace(tmp, FEATURES_CSV)
    return dropped


def commit(marker, uid, fp, rows):
    """Append a section's rows, sync, then log it done and sync."""
    with open(FEATURES_CSV, "a", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)
        fh.flush()
        os.fsync(fh.fileno())
    with open(DONE_CSV, "a", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerow([marker, uid, fp])
        fh.flush()
        os.fsync(fh.fileno())


# ---------------------------------------------------------------- the run


def plan(args):
    """[(marker, by_sec, geom, [(uid, fingerprint)])] for every marker with boxes,
    the sections being those with rows in roi_nuclei.csv."""
    out = []
    markers = [args.marker] if args.marker else list(G5.MARKERS)
    for marker in markers:
        G5.use_marker(marker)
        if not os.path.exists(G5.BOX_CSV) or not os.path.exists(G5.GEOM_CSV):
            print(f"marker: {marker}  skipped (no {os.path.basename(G5.BOX_CSV)} "
                  f"or {os.path.basename(G5.GEOM_CSV)}; run 05a --marker {marker})")
            continue
        by_sec = {}
        for b in G5.load_csv(G5.BOX_CSV):
            by_sec.setdefault(b["scene_uid"], []).append(b)
        geom = {g["scene_uid"]: g for g in G5.load_csv(G5.GEOM_CSV)}
        hashes = scan_nuclei(marker)
        secs = [(u, fingerprint(marker, u, hashes[u], by_sec.get(u, []), geom.get(u)))
                for u in sorted(hashes)]
        out.append((marker, by_sec, geom, secs))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--marker", choices=G5.MARKERS,
                    help="only this marker (default: every marker with boxes)")
    ap.add_argument("--limit", type=int, default=0, metavar="N",
                    help="read at most N sections this run; run again to go on")
    ap.add_argument("--force", action="store_true",
                    help="read sections again even when their features are current")
    args = ap.parse_args()

    if not os.path.exists(NUCLEI_CSV):
        print(f"no {NUCLEI_CSV} - run 05c_detect_rois.py first")
        return 1
    os.makedirs(RESULTS, exist_ok=True)
    prepare_files()
    done = _read_done()
    work = plan(args)
    if not work:
        print("no marker has boxes - run 05a_roi_geometry.py first")
        return 1

    # What is kept: current sections, not being read this run, and every
    # logged section of a marker this run does not look at.
    looked = {m for m, *_ in work}
    keep = {k: v for k, v in done.items() if k[0] not in looked}
    todo, budget = [], args.limit or None
    for marker, by_sec, geom, secs in work:
        for uid, fp in secs:
            current = done.get((marker, uid)) == fp
            read = (not current) or args.force
            if read and (budget is None or budget > 0):
                todo.append((marker, uid, fp))
                if budget is not None:
                    budget -= 1
            elif current:
                keep[(marker, uid)] = fp
    dropped = keep_only(keep)
    if dropped:
        print(f"  {dropped} stale row(s) dropped from {os.path.basename(FEATURES_CSV)}")

    n_all = sum(len(s) for *_, s in work)
    n_left = sum(1 for m, _b, _g, s in work for u, fp in s
                 if (m, u) not in keep) - len(todo)
    n_done = sum(1 for m, _b, _g, s in work for u, _f in s if (m, u) in keep)
    print(f"{n_all} measured section(s); {n_done} already done, {len(todo)} to read"
          + (f" (at most {args.limit} this run)" if args.limit else ""))

    by_marker = {m: (b, g) for m, b, g, _s in work}
    not_done, n_rows, pyczi = [], 0, None
    for start in range(0, len(todo), BATCH):
        batch = todo[start:start + BATCH]
        keys_by = {}
        for marker in dict.fromkeys(m for m, _u, _f in batch):
            keys_by[marker] = scan_nuclei(marker, {u for m, u, _f in batch if m == marker})
        for marker, uid, fp in batch:
            by_sec, geom = by_marker[marker]
            boxes, g = by_sec.get(uid, []), geom.get(uid)
            try:
                if not boxes:
                    raise NotDone(f"no boxes in {os.path.basename(G5.resolve_paths(marker)[1])} for "
                                  f"this section")
                if g is None:
                    raise NotDone("no row in the geometry file for this section")
                npz = D5.outline_path(marker, uid)
                if not D5.present(npz, uid):
                    raise NotDone("no saved outlines (detection_outlines/"
                                  f"{marker}/{uid}.npz) - 06i_detection_overlays.py "
                                  "saves them once, then run this again")
                outlines = load_outlines(marker, uid)
                want = check(outlines, keys_by[marker].get(uid, []), boxes)
                path = os.path.join(CONFIG["source_dir"], g["czi_file"])
                if not D5.present(path, g["czi_file"]):
                    raise NotDone(f"{g['czi_file']} is not in the slides folder")
                if pyczi is None:
                    from pylibCZIrw import czi as pyczi

                def unit(path=path, g=g, marker=marker, uid=uid, boxes=boxes,
                         outlines=outlines, want=want):
                    with CR.open_czi(path, int(float(g.get("scene_w") or 0)) or None,
                                     pyczi=pyczi) as doc:
                        return measure(doc, marker, uid, boxes, outlines, want,
                                       int(g["scene_index"]))
                rows = D5.per_section(unit, label=f"{uid} ({g['czi_file']})")
            except NotDone as exc:
                not_done.append((marker, uid, str(exc)))
                print(f"  {marker} {uid}: NOT DONE - {exc}")
                continue
            except Exception as exc:  # noqa: BLE001 - one bad section must not stop it
                if D5.is_stop(exc):
                    raise
                not_done.append((marker, uid, f"{type(exc).__name__}: {exc}"))
                print(f"  {marker} {uid}: NOT DONE - {type(exc).__name__}: {exc}")
                continue
            commit(marker, uid, fp, rows)
            n_rows += len(rows)
            print(f"  {marker} {uid}: {len(rows)} nuclei")

    print(f"{len(todo) - len(not_done)} section(s) read, {n_rows} nuclei written "
          f"to {FEATURES_CSV}")
    if not_done:
        print(f"{len(not_done)} section(s) not done (listed above); their features "
              f"are absent, not stale")
    if n_left > 0:
        print(f"--limit {args.limit}: {n_left} section(s) still to read. Run again "
              f"to go on.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
