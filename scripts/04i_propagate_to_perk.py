"""Stage 4i - carry the curation from the PCNA scans onto the paired pERK scans.

Every curation decision so far was made on the DAPI channel of the **AF488 (PCNA)**
scan. The **AF568 (pERK)** scan of the same physical section is a separate
acquisition with its own hand-drawn scan box, so it has different framing and
`04a` gives it a different automatic angle. The decisions therefore do not
transfer by copying.

The three kinds of decision transfer differently, and lumping them would be wrong:

**Exclusions transfer directly.** A section excluded because its tissue is torn is
torn in both scans - it is one physical section imaged twice. Straight lookup
through `pairs.csv`.

**Rotations must be re-derived, not copied.** `extra_rotation` is a correction on
top of `04a`'s automatic angle, and that automatic angle differs between the two
scans because the scan boxes differ. What is shared is the *final orientation* the
operator chose. So: reformat the pERK section, then find the rotation that brings
its tissue mask into alignment with the already-curated PCNA mask of the same
section. The target is a real, near-identical shape - the same nuclei in the same
tissue - so unlike the atlas matching in `04c` there is a strong signal here, and
the alignment IoU is reported per section so weak ones can be caught.

The search covers the full 360 degrees, because the two automatic angles are
independent and `04a`'s 180 degree resolution can land differently on each.

`04a`'s angles and the alignment's angle live in different frames - `04a`
squashes each scan to a square before measuring, the alignment works on the
physical grid - and they are converted explicitly rather than mixed; see the
"two frames" note above `derive_rotation`. The number that matters at the end,
the overlap of the two 256-px masks, is reported per section as `final_iou_256`.

**Artifacts are not propagated at all.** Bubbles, aggregates and fibres are
properties of the imaged field, and the two scans image different fields. Running
`04g` directly on the pERK overviews is both simpler and more correct than
mapping a mask across frames.

A flip hypothesis is scored alongside rotation as a check, not as a correction:
the two scans are the same slide the same way up, so flips should essentially
never win. If they do, the pairing is suspect.

Run:  python 04i_propagate_to_perk.py --limit 40      # try a subset first (writes a .trial40 file)
      python 04i_propagate_to_perk.py
"""

import sys
import argparse
import csv
import json
import os
import shutil

import numpy as np
from PIL import Image
from scipy import ndimage

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
# ls_config resolves LS_CONFIG, applies the defaults and validates once for
# the whole process. Imported, not re-implemented: this block used to be four
# lines copy-pasted into every stage.
from ls_config import CONFIG, CONFIG_PATH  # noqa: E402
import ls_naming as NM  # noqa: E402
import ls_paths as LP  # noqa: E402

OUT_ROOT = CONFIG["out_root"]
OVERVIEW_DIR = os.path.join(OUT_ROOT, "overviews")
REFORMAT_DIR = os.path.join(OUT_ROOT, "reformatted")
SEC_DIR = os.path.join(REFORMAT_DIR, "sections")
PAIRS_CSV = os.path.join(OUT_ROOT, "pairs.csv")
# `source` and `derived_as` are what lets a re-run keep a hand correction; see
# merge_hand_rows.
OVERRIDE_KEYS = ["perk_scene_uid", "pcna_scene_uid", "animal", "extra_rotation", "flip",
                 "excluded", "align_iou", "final_iou_256", "flip_margin", "confidence",
                 "reason", "source", "derived_as"]

PHYS_DS = 4           # the shared grid is this many overview pixels -> 20.8 um/px on LS
CANVAS = 320          # px on the shared physical grid; ~6.7 mm across on LS
COARSE = 3.0          # degrees, first pass over the full circle
FINE = 1.0
# Below this alignment IoU the two masks genuinely disagree and the transfer
# should be checked by eye. Set from the measured distribution over all 661
# sections - median 0.789, p25 0.651, p10 0.520 - so this flags the bottom ~13%
# rather than half of them, which a 0.70 gate would have done.
IOU_MIN = 0.55

import importlib.util
_SPEC = importlib.util.spec_from_file_location(
    "reformat_mod", os.path.join(os.path.dirname(os.path.abspath(__file__)), "04a_reformat.py"))
_RF = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_RF)

# The shared physical grid's pixel, in um: PHYS_DS of the study's configured
# overview pixels. A choice of working resolution, not a claim about any
# image - each overview is brought onto it from its OWN scale (focus.csv's
# um_px), in physical_mask. Both passes used to be downsampled by PHYS_DS
# alike, which is a shared grid only while both were exported at one scale.
PHYS_UM = PHYS_DS * _RF.overview_um_px(None)

_lsio = importlib.util.spec_from_file_location(
    "_lsio", os.path.join(os.path.dirname(os.path.abspath(__file__)), "ls_io.py"))
IO = importlib.util.module_from_spec(_lsio)
_lsio.loader.exec_module(IO)

# 04q's backup naming, imported rather than restated: one rule for what a
# curation file's backups are called, `<name>.<YYYYMMDD-HHMMSS>.backup`.
_SPEC_Q = importlib.util.spec_from_file_location(
    "import_curation_mod",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "04q_import_curation.py"))
_IQ = importlib.util.module_from_spec(_SPEC_Q)
_SPEC_Q.loader.exec_module(_IQ)
backup_path = _IQ.backup_path

# The pass being propagated ONTO: the one that does not own the unsuffixed
# names. 04i is paired-only (ls_layouts.RESTRICTED), so there are exactly two
# and this is the other one - derived, because `AF568` here would be a third
# copy of a rule that already exists twice too often.
TARGET_MARKER = next((m for m in _RF.MARKERS if m != _RF.DEFAULT_MARKER),
                     _RF.DEFAULT_MARKER)

# Written through the SAME expression `04a.load_overrides` reads, so the writer
# and the reader cannot end up naming different files. That matters more than
# it looks: load_overrides returns ({}, {}) for a file it cannot find, so a
# writer that drifted one basename away from its reader would drop every
# rotation and exclusion in here with no error at all. On the operator's drive
# this still resolves to the 82,893-byte `perk_overrides.csv` that 130 curated
# sections point at; a study that never had one gets a name with its own
# marker in it.
OUT_CSV = _RF.marker_paths(TARGET_MARKER)["overrides"]

# The pass the curation is coming FROM: the geometry source, whose reformat
# index and exclusion list carry the unsuffixed names this stage reads below.
SOURCE_MARKER = _RF.DEFAULT_MARKER

# pairs.csv's per-marker columns, from the same rule 02 writes them by. They
# used to be the literals `af488_scene_uid` and `af568_scene_uid`, which is
# what `slug()` gives back for the LS study and nothing at all for any other -
# and a missing column reads as an empty dict here, so every section would come
# out as "no partner" rather than as an error.
SOURCE_UID_COL = f"{LP.slug(SOURCE_MARKER)}_scene_uid"
TARGET_UID_COL = f"{LP.slug(TARGET_MARKER)}_scene_uid"


def physical_mask(png_path, canvas, um_px=None):
    """Tissue mask on a shared PHYSICAL grid, centred on its own centroid.

    **Not the reformatted mask, and that distinction is the whole point.** The
    reformat crops each scan to its own tissue bounding box and resizes to
    256x256, so the tissue occupies a different fraction of the frame depending
    on how much debris the scan box happened to include. The two scans of one
    section then differ in *scale*, and no rotation can bring them together - a
    first attempt did exactly that and reached a median IoU of 0.548 with flips
    winning a third of the time, which is what a failed match looks like.

    Each overview is brought from its own scale (`um_px`, focus.csv's; the
    study's overview scale when not given) to PHYS_UM, which puts both on a
    common physical grid where only rotation and translation are free. This
    used to downsample both by PHYS_DS, on the strength of both being at "a
    verified 5.20 um/px" - true of the LS study only, and of two passes only
    while both were exported at one scale. A whole-number step is still taken
    by striding, exactly as before; any other is a resize.
    """
    try:
        img = Image.open(png_path).convert("L")
    except OSError:
        return None
    step = PHYS_UM / (um_px or _RF.overview_um_px(None))
    k = int(round(step))
    if k >= 1 and abs(step - k) < 1e-6:
        a = np.asarray(img).astype(np.float32)[::k, ::k]
    else:
        w, h = img.size
        a = np.asarray(img.resize((max(1, int(round(w / step))),
                                   max(1, int(round(h / step)))),
                                  Image.BILINEAR)).astype(np.float32)
    m = _RF.tissue_mask(a, light_background=False)
    if m is None:
        return None
    ys, xs = np.nonzero(m)
    out = np.zeros((canvas, canvas), bool)
    cy, cx = int(ys.mean()), int(xs.mean())
    y0, x0 = canvas // 2 - cy, canvas // 2 - cx
    sy = slice(max(0, y0), min(canvas, y0 + m.shape[0]))
    sx = slice(max(0, x0), min(canvas, x0 + m.shape[1]))
    ty = slice(max(0, -y0), max(0, -y0) + (sy.stop - sy.start))
    tx = slice(max(0, -x0), max(0, -x0) + (sx.stop - sx.start))
    out[sy, sx] = m[ty, tx]
    return out


def best_iou_over_shifts(a, b):
    """Max IoU between `a` and `b` over ALL translations, from one FFT.

    Intersection under a circular shift is a cross-correlation, and |a| and |b|
    do not change with the shift, so IoU(shift) = I / (|a| + |b| - I) for every
    shift at once. Same identity as the symmetry search in 04h.
    """
    A = a.astype(np.float32)
    B = b.astype(np.float32)
    na, nb = float(A.sum()), float(B.sum())
    if na < 50 or nb < 50:
        return 0.0
    corr = np.fft.irfft2(np.fft.rfft2(A) * np.conj(np.fft.rfft2(B)), s=A.shape)
    return float((corr / np.maximum(na + nb - corr, 1e-6)).max())


def align(src_mask, target_mask):
    """Rotation bringing src onto target, translation free.

    Returns (degrees, iou, flip_margin).

    **Flipping is measured but never applied.** Two scans of one section on one
    slide cannot be mirror images of each other - the scanner does not mirror -
    so a flip here can only be an error. And it would be a damaging one: it would
    swap left and right hemispheres in the pERK data relative to PCNA, which is
    exactly what a lateralisation result would be read off.

    The flip hypothesis was scored during development, won on 18.5% of sections,
    and turned out to win by a median of **0.0056 IoU** - a coin toss on a
    near-symmetric shape, with 96% of wins under 0.05. It is retained as a
    reported margin so the evidence stays visible, and discarded as a correction.
    """
    for flip in (False,):
        m = src_mask
        coarse = np.arange(0, 360, COARSE)
        scores = [best_iou_over_shifts(
            ndimage.rotate(m.astype(np.uint8), float(t), order=0, reshape=False) > 0,
            target_mask) for t in coarse]
        t0 = float(coarse[int(np.argmax(scores))])
        fine = np.arange(t0 - COARSE, t0 + COARSE + 1e-9, FINE)
        fs = [best_iou_over_shifts(
            ndimage.rotate(m.astype(np.uint8), float(t), order=0, reshape=False) > 0,
            target_mask) for t in fine]
        k = int(np.argmax(fs))
        deg, score = float(fine[k]) % 360, float(fs[k])

    # Diagnostic only: how much better the mirrored section would have scored.
    mf = src_mask[:, ::-1]
    coarse = np.arange(0, 360, COARSE)
    flip_best = max(best_iou_over_shifts(
        ndimage.rotate(mf.astype(np.uint8), float(t), order=0, reshape=False) > 0,
        target_mask) for t in coarse)
    return deg, score, float(flip_best - score)


def mask_iou(a, b):
    return float((a & b).sum() / max((a | b).sum(), 1))


# ---------------------------------------------------------------- two frames
#
# 04a measures its angle AFTER resizing the overview to a WORK_SIZE square, and
# a scan box is rarely square, so that resize is anisotropic: x is scaled by
# WORK_SIZE/w and y by WORK_SIZE/h. Every angle 04a reports or consumes - the
# `angle` column of reformat_index.csv, the angle `reformat()` returns, and the
# `extra_angle` it accepts - is measured in that squashed frame, and the frame
# is different for every scan. `align()` above works on the isotropic physical
# grid. The two kinds of angle cannot be added or subtracted directly; the
# conversion goes through the direction vector, which is what the anisotropic
# resize actually transforms.
#
# What `ndimage.rotate(img, A)` does to a direction at angle a (atan2(dy, dx) in
# array coordinates) is a -> a - A, so rotating by A puts the direction at
# angle A along +x. That is why 04a rotates by `principal_angle` rather than its
# negative, and it is the invariant carried across frames here: the direction
# that ends up horizontal in the PCNA output must end up horizontal in the pERK
# output. Its angle is different in each of the three frames.


def squashed_to_physical(angle, w, h):
    """A direction's angle in a scan's WORK_SIZE-square frame -> on the slide."""
    r = np.radians(angle)
    return float(np.degrees(np.arctan2(h * np.sin(r), w * np.cos(r)))) % 360.0


def physical_to_squashed(angle, w, h):
    """A direction's angle on the slide -> in that scan's WORK_SIZE-square frame."""
    r = np.radians(angle)
    return float(np.degrees(np.arctan2(w * np.sin(r), h * np.cos(r)))) % 360.0


def derive_rotation(p_source, p_target, total_source, curated_mask=None,
                    source_flip=False, um_source=None, um_target=None):
    """The correction 04a needs for one target scan. None if either has no mask.

    `source_flip` is the operator's flip on the curated section (`manual_flip`
    in reformat_index.csv). It is not a correction 04i decides - see `align()`
    on why a flip is never derived - but one the target has to CARRY: the two
    scans are one section, so if the operator mirrored one of them the other
    must be mirrored too or the hemispheres swap between the passes. It is
    applied to the goal the way 04a applies it, after the rotation, and the
    target is aligned mirrored too: a rotation cannot turn a shape into its
    mirror image. `final_iou_256` reformats the target with the same flip,
    because the curated mask on disk is the flipped one.

    `total_source` is the TOTAL rotation 04a applied to the CURATED scan
    (automatic plus the operator's correction), from reformat_index.csv, in
    that scan's squashed frame. `curated_mask`, if given, is the curated
    section's 256-px mask; the target scan is then reformatted with the derived
    correction and the IoU of the two 256-px masks is reported as
    `final_iou_256`.

    That last number is the one that matters and it is bounded well below 1
    even when the angle is right: the two 256-px masks are the same shape
    squashed by two different scan boxes, and the squash survives 04a's crop
    and square-pad. Measured on a synthetic section with boxes 944x1632 against
    1174x1404 the best any rotation can reach is 0.66. So it is reported
    against nothing, as a per-section number to compare with its neighbours,
    not gated.

    `um_source` / `um_target` are each overview's own pixel size (focus.csv's
    um_px): the two passes need not have been exported at one scale.
    """
    m_source = physical_mask(p_source, CANVAS, um_source)
    m_target = physical_mask(p_target, CANVAS, um_target)
    if m_source is None or m_target is None:
        return None
    try:
        w_source, h_source = Image.open(p_source).size
        w_target, h_target = Image.open(p_target).size
    except OSError:
        return None

    # The direction 04a put along +x in the curated output, as an angle on the
    # slide. Rotating the physical curated mask by it reproduces the curated
    # orientation in physical units - rotating by `total_source` itself would
    # apply a squashed-frame angle to an unsquashed mask.
    total_source_phys = squashed_to_physical(total_source, w_source, h_source)
    goal = ndimage.rotate(m_source.astype(np.uint8), total_source_phys, order=0,
                          reshape=False) > 0
    if source_flip:
        # 04a's order: rotate, THEN mirror left-right. The target is mirrored
        # the same way before aligning, and the angle found for the mirrored
        # target is turned back into one for the unmirrored scan - 04a rotates
        # the scan and then mirrors it. ndimage.rotate moves a direction a to
        # a - A and a left-right mirror moves it to 180 - a, so
        # mirror(rotate(T, A)) == rotate(mirror(T), -A): the angle is negated.
        goal = goal[:, ::-1]
        deg_m, score, flip_margin = align(m_target[:, ::-1], goal)
        deg = (-deg_m) % 360.0
    else:
        # `deg` is the same direction, on the slide, in the target scan's frame.
        deg, score, flip_margin = align(m_target, goal)

    # 04a will apply its own automatic angle to the target scan, in that scan's
    # squashed frame, so the correction it needs is the difference between the
    # total rotation measured here - moved into the same frame - and that
    # automatic angle.
    auto = _RF.reformat(p_target, light_background=False)
    if auto is None:
        return None
    auto_target = float(auto[2])
    extra = (physical_to_squashed(deg, w_target, h_target) - auto_target) % 360

    final_iou = None
    if curated_mask is not None:
        done = _RF.reformat(p_target, light_background=False, extra_angle=extra,
                            flip=bool(source_flip))
        if done is not None:
            final_iou = mask_iou(done[1], curated_mask)
    return {"extra": extra, "deg": deg, "auto_target": auto_target,
            "align_iou": score, "flip_margin": flip_margin,
            "final_iou_256": final_iou}


def output_path(limit):
    """Where this run's rows go.

    04a reads the overrides file as the COMPLETE record, so a `--limit` trial
    written there would leave every section outside the trial at its raw
    automatic angle. A trial is written beside it and the real file is left
    as it was.
    """
    if not limit:
        return OUT_CSV
    root, ext = os.path.splitext(OUT_CSV)
    return f"{root}.trial{limit}{ext}"


# ------------------------------------------------------------ hand rows
#
# The overrides file is ALSO a hand-kept file: the operator corrects a
# rotation or an exclusion in it directly (the LS study's `perk_overrides.csv`
# is 82,893 bytes of that), and nothing else in the pipeline writes it. A full
# run used to rewrite it from derived rows alone, so every correction typed
# into it was gone after the next run, with nothing said.
#
# Nothing in the old columns tells a hand row from a derived one - `reason`
# and `confidence` are empty on both - so each row now says what it is:
# `source` (`derived` / `manual`) and `derived_as`, the decision 04i wrote into
# the row. A person editing the file does not touch those two, so a row whose
# decision no longer equals its own `derived_as` was edited and is manual. That
# is what makes a changed DERIVATION (the curated pass re-curated, a better
# alignment) distinguishable from a changed ROW: comparing against this run's
# derivation alone would read the first as a hand edit and freeze it forever.

def _norm(v):
    """One spelling per value, so `12`, `12.0` and ` 12` are one decision."""
    s = ("" if v is None else str(v)).strip()
    try:
        return f"{float(s):g}"
    except ValueError:
        return s


def decision_of(row):
    """The part of a row 04a acts on (plus the pairing), as one string."""
    # The uid is text and compared as text; only the numbers are normalised,
    # and an empty number is 0, which is how 04a reads it.
    pair = str(row.get("pcna_scene_uid") or "").strip()
    return ";".join([f"pcna_scene_uid={pair}"]
                    + [f"{k}={_norm(row.get(k)) or '0'}"
                       for k in ("extra_rotation", "flip", "excluded")])


def merge_hand_rows(derived, existing):
    """This run's derived rows with the existing file's hand rows kept.

    Returns (rows, n_kept). An existing row is MANUAL when it says so, when
    its decision differs from its own `derived_as`, or - for a file written
    before rows were marked - when it differs from this run's derivation for
    that section or this run derived nothing for it. That last rule keeps a
    legacy row that may be stale rather than dropping one that may be hand
    work; the run says how many it kept, and deleting a row hands that
    section back to 04i.

    A hand row replaces the derived row for its section in place; `derived_as`
    is updated to what 04i would say now, so the difference stays visible.
    """
    by_uid = {r["perk_scene_uid"]: r for r in derived}
    manual = {}
    for r in existing:
        uid = (r.get("perk_scene_uid") or "").strip()
        if not uid:
            continue
        src = (r.get("source") or "").strip()
        mark = (r.get("derived_as") or "").strip()
        if src == "manual":
            hand = True
        elif src == "derived" and mark:
            hand = decision_of(r) != mark
        else:
            fresh = by_uid.get(uid)
            hand = fresh is None or decision_of(r) != decision_of(fresh)
        if hand:
            row = {k: r.get(k, "") for k in OVERRIDE_KEYS}
            row["source"] = "manual"
            if uid in by_uid:
                row["derived_as"] = by_uid[uid]["derived_as"]
            manual.setdefault(uid, []).append(row)
    out = []
    for r in derived:
        out.extend(manual.pop(r["perk_scene_uid"], [r]))
    for rows in manual.values():
        out.extend(rows)
    n_kept = sum(1 for r in out if r["source"] == "manual")
    return out, n_kept


def _as_text(rows, keys):
    """Rows as the CSV writer would spell them, for a did-anything-change test."""
    return [[("" if r.get(k) is None else str(r.get(k))) for k in keys] for r in rows]


def write_overrides(path, rows):
    """Write the full overrides file, backing up the old one first when this
    changes it. Returns (written, backup path or None). Nothing is written when
    nothing changed: an identical backup buries the one that matters, as 04q
    found."""
    bak = None
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as fh:
            old = list(csv.reader(fh))
        if old and old[0] == OVERRIDE_KEYS and old[1:] == _as_text(rows, OVERRIDE_KEYS):
            return False, None
        bak = backup_path(path)
        shutil.copy2(path, bak)
    IO.atomic_write_csv(path, rows, OVERRIDE_KEYS)
    return True, bak


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    with open(PAIRS_CSV, newline="", encoding="utf-8") as fh:
        pairs = [r for r in csv.DictReader(fh)
                 if r.get(SOURCE_UID_COL) and r.get(TARGET_UID_COL)]
    target_of = {r[SOURCE_UID_COL]: r[TARGET_UID_COL] for r in pairs}

    # Curated state of the SOURCE pass: what survived, and what was excluded.
    # These two are unsuffixed by the naming rule - the source IS the geometry
    # source - so the basenames below are that rule's answer, not a literal
    # about which fluorophore was curated first.
    kept = {r["id"]: r for r in csv.DictReader(open(
        os.path.join(REFORMAT_DIR, "reformat_index.csv"), encoding="utf-8"))
        if r["kind"] == "section"}
    excluded = {r["scene_uid"] for r in csv.DictReader(open(
        os.path.join(REFORMAT_DIR, "excluded_sections.csv"), encoding="utf-8"))}

    chan, um_px = {}, {}
    with open(os.path.join(OUT_ROOT, "qc", "focus.csv"), newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            chan[r["scene_uid"]] = (r["animal"], r["marker_channel"])
            um_px[r["scene_uid"]] = _RF.overview_um_px(r)

    rows, n_excl, n_nopair, failed, n_flip = [], 0, 0, 0, 0
    todo = list(kept.items())
    if args.limit:
        todo = todo[: args.limit]

    # 1. Exclusions, straight through the pairing.
    for uid in excluded:
        u_target = target_of.get(uid)
        if u_target:
            rows.append({"perk_scene_uid": u_target, "pcna_scene_uid": uid,
                         "animal": NM.subject_of(uid), "extra_rotation": 0, "flip": 0,
                         "excluded": 1, "align_iou": "", "final_iou_256": "",
                         "flip_margin": "", "confidence": "",
                         # The VALUE is left as it is: 04m:145 and 04p:286
                         # both describe this exact string, and it is in
                         # the operator's 82,893-byte overrides file. A
                         # rename here is a data change, not a rename.
                         "reason": "PCNA partner excluded by the operator"})
            rows[-1].update(source="derived", derived_as=decision_of(rows[-1]))
            n_excl += 1

    # 2. Rotations, re-derived by aligning to the curated source mask.
    for i, (uid, meta) in enumerate(todo, 1):
        u_target = target_of.get(uid)
        if not u_target:
            n_nopair += 1
            continue
        # THE CHANNEL COMES OUT OF focus.csv, and it used to be read and then
        # thrown away: `animal488, _ = chan.get(...)` and then a literal
        # "AF488"/"AF568" in the path. Overviews live in
        # overviews/<animal>/<marker_channel>/, so for any other study both
        # paths named directories that do not exist, every section failed the
        # check below, and rotation_overrides_<marker>.csv came out holding
        # only the exclusion rows. 04a --apply-overrides then applied zero
        # rotations and every section was reformatted at its raw automatic
        # angle, unaligned with the partner it was meant to match. Nothing
        # errored; the summary printed a plausible "failed to reformat" count.
        animal_source, chan_source = chan.get(
            uid, (NM.subject_of(uid), SOURCE_MARKER))
        animal_target, chan_target = chan.get(
            u_target, (NM.subject_of(u_target), TARGET_MARKER))
        p_source = os.path.join(OVERVIEW_DIR, animal_source, chan_source,
                                uid + "_DAPI.png")
        p_target = os.path.join(OVERVIEW_DIR, animal_target, chan_target,
                                u_target + "_DAPI.png")
        if not (os.path.exists(p_source) and os.path.exists(p_target)):
            failed += 1
            continue
        # The curated source mask as 04a wrote it - the thing the target
        # section has to line up with in the end.
        mp = os.path.join(SEC_DIR, uid + "_mask.npy")
        curated = np.load(mp).astype(bool) if os.path.exists(mp) else None

        # `angle` in reformat_index is the TOTAL rotation 04a applied to the
        # curated scan - automatic plus the operator's correction - and
        # `manual_flip` is the operator's flip, which 04a applied after it.
        # An index written before 04a recorded the flip has no column; that
        # reads as unflipped, which is what such an index was.
        src_flip = (meta.get("manual_flip") or "0").strip() == "1"
        d = derive_rotation(p_source, p_target, float(meta["angle"]), curated,
                            source_flip=src_flip, um_source=um_px.get(uid),
                            um_target=um_px.get(u_target))
        if d is None:
            failed += 1
            continue
        n_flip += int(src_flip)
        score = d["align_iou"]
        rows.append({"perk_scene_uid": u_target, "pcna_scene_uid": uid,
                     "animal": animal_target,
                     "extra_rotation": int(round(d["extra"])), "flip": int(src_flip),
                     "excluded": 0, "align_iou": round(score, 4),
                     "final_iou_256": ("" if d["final_iou_256"] is None
                                       else round(d["final_iou_256"], 4)),
                     "flip_margin": round(d["flip_margin"], 4),
                     "confidence": "low" if score < IOU_MIN else "high",
                     "reason": "", "source": "derived"})
        rows[-1]["derived_as"] = decision_of(rows[-1])
        if (i % 50) == 0:
            print(f"\r  aligned {i}/{len(todo)}", end="")
    print(f"\r  aligned {len(todo)} sections            ")

    out_csv = output_path(args.limit)
    n_kept, written, bak = 0, True, None
    if out_csv == OUT_CSV:
        # A full run: the real file, which may hold hand corrections. They are
        # merged back in, and the old file is backed up before it changes.
        existing = []
        if os.path.exists(OUT_CSV):
            with open(OUT_CSV, newline="", encoding="utf-8") as fh:
                existing = list(csv.DictReader(fh))
        derived = rows
        rows, n_kept = merge_hand_rows(derived, existing)
        written, bak = write_overrides(OUT_CSV, rows)
    else:
        # A trial goes beside the real file and never replaces it, so there is
        # nothing in it to keep and nothing to back up.
        derived = rows
        IO.atomic_write_csv(out_csv, rows, OVERRIDE_KEYS)

    # The statistics below describe what 04i DERIVED; a hand row is not a
    # measurement.
    live = [r for r in derived if not r["excluded"] and r["align_iou"] != ""]
    s = np.array([r["align_iou"] for r in live], dtype=float)
    hi = sum(1 for r in live if r["confidence"] == "high")
    fm = np.array([r["flip_margin"] for r in live], dtype=float)
    print()
    print("=" * 74)
    print(f"{len(rows)} rows -> {out_csv}"
          + ("" if written else "  (unchanged, not rewritten)"))
    if out_csv != OUT_CSV:
        print(f"  (--limit trial: {OUT_CSV} is untouched; run without --limit "
              f"to write it)")
    if bak:
        print(f"  backed up the previous file -> {os.path.basename(bak)}")
    if out_csv == OUT_CSV:
        print(f"  kept {n_kept} hand-edited row(s) from the existing file "
              f"(source=manual; delete a row to hand it back to 04i)")
    print(f"  exclusions transferred : {n_excl}")
    print(f"  rotations derived      : {len(live)}")
    print(f"  operator flips carried : {n_flip}")
    print(f"  curated sections with no {TARGET_MARKER} partner : {n_nopair}")
    print(f"  failed to reformat     : {failed}")
    if len(s):
        print()
        print(f"  alignment IoU : median {np.median(s):.3f}  p10 {np.percentile(s, 10):.3f}  "
              f"p90 {np.percentile(s, 90):.3f}  min {s.min():.3f}")
        print(f"  high confidence (>= {IOU_MIN}) : {hi} ({100 * hi / len(live):.0f}%)")
        f256 = np.array([r["final_iou_256"] for r in live if r["final_iou_256"] != ""],
                        dtype=float)
        if len(f256):
            print(f"  final 256-px IoU (pERK reformatted with the derived correction, "
                  f"against the curated PCNA mask) : median {np.median(f256):.3f}  "
                  f"p10 {np.percentile(f256, 10):.3f}  n {len(f256)}")
            print("  This is lower than the alignment IoU by construction: 04a squashes each")
            print("  scan to a square before the crop, and the two scan boxes differ, so the")
            print("  two 256-px silhouettes are different shapes even when the angle is right.")
        print(f"  mirrored-would-score-better on {int((fm > 0).sum())} section(s), "
              f"by a median of {np.median(fm[fm > 0]) if (fm > 0).any() else 0:.4f} IoU")
        print("  Flipping is measured but never derived: two scans of one slide cannot")
        print("  be mirrored, and a spurious flip would swap the hemispheres. A flip the")
        print("  operator made on the curated section is carried, not derived.")
        print()
        print("  This is a shape alignment between two images of the SAME physical")
        print("  section, so a low IoU means the pairing is wrong, not that the tissue")
        print("  is ambiguous. Inspect the low-confidence rows before trusting them.")
    print("=" * 74)


if __name__ == "__main__":
    main()
