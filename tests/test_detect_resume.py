"""Stage 5c's two file-handling helpers: the header check and the forced drop.

roi_nuclei.csv is the artefact this pipeline exists to produce - hours of
StarDist over 130 scenes, shared by both markers, appended to across runs. Two
ways of quietly corrupting it are pinned here:

  * RESUMING ONTO AN OLD HEADER. COLUMNS gained `off_tissue` on 2026-09-02. A
    resume against a file written before that appends 21-field rows under a
    20-field header; csv.DictReader maps the extra field to key None, so every
    new nucleus reads off_tissue = None in 06a and 06g cannot backfill a file
    that is half old and half new. The resume must refuse and name 06g.
  * `--force --limit N`. The force path used to drop EVERY row of the marker
    and then re-measure only N sections, silently discarding the rest. It
    must drop exactly the sections it is about to redo, and nothing of the
    other marker.
  * RESUMING ONTO ANYTHING ELSE, WITH `legacy` SUPPLIED. `legacy` turns one
    header into a rewrite of the file, and 05c.main() passes it on the only
    path that appends to roi_nuclei.csv - so the refusal that matters is the
    one with `legacy` in hand, and it is the one nothing used to check.
  * RESUMING ONTO A PRE-PROVENANCE HEADER. COLUMNS gained `segmented_on`,
    `backend` and `nucleus_shaped` on 2026-09-08, and the check above then
    refused the operator's live 961,233-row file - hours of StarDist that
    cannot be resumed onto. Unlike `off_tissue`, the absence of these three
    has a meaning: before them this stage segmented the counterstain with
    StarDist and had no other route, so every such row is nuclear / stardist /
    nucleus-shaped. The resume backfills them and goes ahead, and what it must
    NOT do is leave the file half 21 fields and half 24 - csv.DictReader
    returns None for the fields a header does not name, None is falsy, and 06a
    would withhold the Abercrombie correction from every correctly-nuclear row.

05c imports StarDist lazily inside load_model(), so the module itself loads on
a machine without it. Synthetic files throughout.

Run:  python tests/test_detect_resume.py
"""

import sys
import csv
import importlib.util
import os
import shutil
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")

# This suite imports stage modules, which read config at import. Without a
# config of its own it would fall through to the operator's live study and
# then pass or fail on their data. See tests/_fixture.py.
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from _fixture import use_temp_study  # noqa: E402

STUDY = use_temp_study()


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


G5C = load("g5c", "05c_detect_rois.py")

fails = 0


def chk(label, got, want):
    global fails
    ok = str(got) == str(want)
    if not ok:
        fails += 1
    print(("ok   " if ok else "FAIL ") + label.ljust(58) + " " + str(got)
          + ("" if ok else "   want " + str(want)))


OLD_COLUMNS = [c for c in G5C.COLUMNS if c != "off_tissue"]


def write(path, cols, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        w.writerows(rows)


def read(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def row(uid, marker, n):
    """One nucleus row in the CURRENT column order, filler everywhere else."""
    r = dict.fromkeys(G5C.COLUMNS, "0")
    r.update(scene_uid=uid, marker=marker, nucleus_id=str(n),
             roi_kind="roi", region="Dm")
    return [r[c] for c in G5C.COLUMNS]


def legacy_row(uid, marker, n):
    """The same row under the header a file had before the provenance columns."""
    r = dict.fromkeys(G5C.COLUMNS, "0")
    r.update(scene_uid=uid, marker=marker, nucleus_id=str(n),
             roi_kind="roi", region="Dm")
    return [r[c] for c in G5C.LEGACY_COLUMNS]


def torn_check(tmp):
    """A run cut off mid-write must not leave a section that resume calls done.

    roi_nuclei.csv is appended section by section onto a drive that has
    dropped writes. A crash inside writerows left some of a section's rows -
    enough for "any row present" to call it measured, so it was never redone -
    and a last line with no newline, onto which the next run glued its first
    row. The fix: cut a torn last line back to the last whole one, and keep a
    log of sections whose rows reached the disk whole. A section with rows but
    no log entry was torn: its rows go and it is measured again.
    """
    path = os.path.join(tmp, "torn.csv")
    write(path, G5C.COLUMNS, [row("S1", "pERK", 1), row("S1", "pERK", 2)])
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("S2,1,pERK,12.5")                      # cut off mid-row
    cut = G5C.repair_torn_tail(path)
    chk("a torn last line is cut off", cut > 0, True)
    with open(path, "rb") as fh:
        chk("...and the file ends on a whole line", fh.read().endswith(b"\n"), True)
    chk("...keeping every whole row", len(read(path)), 2)
    chk("a whole file is left alone", G5C.repair_torn_tail(path), 0)

    log = os.path.join(tmp, "done.csv")
    chk("no log yet reads as None (an older file)", G5C.read_done_log(log, "pERK"), None)
    with open(log, "a", encoding="utf-8") as fh:
        G5C.start_done_log(fh, "pERK")
        G5C.record_done(fh, "pERK", "S1")
    # The second marker's rows predate the log: trusted, never called torn -
    # read as "nothing logged", its whole dataset would be dropped.
    chk("a marker the log has not started on reads as None",
        G5C.read_done_log(log, "PCNA"), None)
    chk("...so none of its sections is torn",
        G5C.torn_sections({"S9"}, G5C.read_done_log(log, "PCNA")), set())
    with open(log, "a", encoding="utf-8") as fh:
        G5C.start_done_log(fh, "PCNA", {"S9"})
    chk("a logged section reads back, per marker",
        sorted(G5C.read_done_log(log, "pERK")), ["S1"])
    chk("rows without a log entry are a torn section",
        sorted(G5C.torn_sections({"S1", "S3"}, G5C.read_done_log(log, "pERK"))), ["S3"])
    chk("with no log, nothing is called torn",
        G5C.torn_sections({"S1", "S3"}, None), set())
    G5C.forget_done(log, "pERK", {"S1"})
    chk("forgetting drops only that marker's sections",
        (sorted(G5C.read_done_log(log, "pERK")), sorted(G5C.read_done_log(log, "PCNA"))),
        ([], ["S9"]))


def header_check(tmp):
    """A resume onto a pre-off_tissue file must stop and say what to run."""
    old = os.path.join(tmp, "old_nuclei.csv")
    write(old, OLD_COLUMNS, [row("A1", "AF568", 1)[:-1]])
    try:
        G5C.check_header(old, G5C.COLUMNS)
        got = "no exit"
    except SystemExit as exc:
        got = str(exc)
    chk("old 20-column header -> SystemExit", got != "no exit", True)
    chk("...and the message names 06g_flag_off_tissue.py",
        "06g_flag_off_tissue.py" in got, True)
    chk("...and names the missing column", "off_tissue" in got, True)

    # A file written before the provenance columns is a DIFFERENT case from
    # the one above, and the difference is that its absence has a meaning.
    # Before those columns existed 05c segmented the counterstain with StarDist
    # and had no other route, so every row in such a file is nuclear/stardist/
    # nucleus-shaped - which is exactly what the backfill writes. Refusing it
    # instead cost the operator's 961,233-row file its resume.
    leg = os.path.join(tmp, "legacy_nuclei.csv")
    write(leg, G5C.LEGACY_COLUMNS,
          [legacy_row("A1", "AF568", 1), legacy_row("A1", "AF568", 2)])
    try:
        G5C.check_header(leg, G5C.COLUMNS)
        got = "no exit"
    except SystemExit:
        got = "exit"
    chk("a legacy header with no `legacy` mapping is still refused", got, "exit")

    try:
        G5C.check_header(leg, G5C.COLUMNS, legacy=G5C.LEGACY_PROVENANCE)
        got = "accepted"
    except SystemExit as exc:
        got = str(exc)
    chk("...and accepted with one", got, "accepted")
    rows = read(leg)
    chk("...the file now carries the current header",
        list(rows[0].keys()) == G5C.COLUMNS, True)
    # The counterstain as THIS study names it. A study with no channel table -
    # which is every `paired` one, including the live study - has no declared
    # name for it and gets NUCLEAR_FALLBACK, "nuclear"; the example study this
    # suite runs under declares a channel table, so it gets "DAPI". Either way
    # a backfilled row says what a freshly appended one says, which is the
    # check below.
    chk("...every old row reads as the counterstain",
        {r["segmented_on"] for r in rows},
        "{%r}" % G5C.LEGACY_PROVENANCE["segmented_on"])
    chk("...which is what a nuclear-segmented marker records anyway",
        G5C.LEGACY_PROVENANCE["segmented_on"],
        G5C.segment_plane_for(G5C.G5.MARKER))
    chk("...and with no channel table that is the plain word",
        G5C.NUCLEAR_FALLBACK, "nuclear")
    chk("...segmented by stardist", {r["backend"] for r in rows}, "{'stardist'}")
    chk("...and nucleus-shaped", {r["nucleus_shaped"] for r in rows}, "{'1'}")
    chk("...with nothing else touched",
        [r["nucleus_id"] for r in rows], "['1', '2']")
    with open(leg, newline="", encoding="utf-8") as fh:
        widths = {len(r) for r in csv.reader(fh)}
    chk("...and one width for every line, header included",
        widths, "{%d}" % len(G5C.COLUMNS))
    try:
        G5C.check_header(leg, G5C.COLUMNS, legacy=G5C.LEGACY_PROVENANCE)
        got = "accepted"
    except SystemExit as exc:
        got = str(exc)
    chk("...and the upgraded file passes the plain check", got, "accepted")

    cur = os.path.join(tmp, "cur_nuclei.csv")
    write(cur, G5C.COLUMNS, [row("A1", "AF568", 1)])
    try:
        G5C.check_header(cur, G5C.COLUMNS)
        got = "fine"
    except SystemExit as exc:
        got = str(exc)
    chk("current header passes", got, "fine")

    # The same columns in a different order is still a mismatch: 05c writes
    # positionally, and a permuted file would take every new row scrambled.
    perm = os.path.join(tmp, "perm_nuclei.csv")
    write(perm, list(reversed(G5C.COLUMNS)), [])
    try:
        G5C.check_header(perm, G5C.COLUMNS)
        got = "no exit"
    except SystemExit:
        got = "exit"
    chk("same columns, different order -> SystemExit", got, "exit")

    # THE REFUSAL, WITH `legacy` SUPPLIED - which is how 05c.main() always
    # calls it, and the only call that can rewrite the operator's file.
    #
    # Every refusal above passes `columns` alone, and none of them touches the
    # branch that actually runs in production. Mutating
    #
    #     if legacy and header == [c for c in columns if c not in legacy]:
    #
    # to `if legacy:` survived this suite and the whole of tests/run.sh, and
    # at the production call site it BACKFILLED a permuted 21-column file -
    # 961,233 rows rewritten into columns they do not belong in - and raised
    # KeyError('off_tissue') on the two headers below it. The backfill is a
    # rewrite, so a refusal that had already rewritten the file would be no
    # refusal at all: the bytes are checked, not just the exception.
    def refuses(name, cols, rows):
        path = os.path.join(tmp, name + ".csv")
        write(path, cols, rows)
        with open(path, "rb") as fh:
            before = fh.read()
        try:
            G5C.check_header(path, G5C.COLUMNS, legacy=G5C.LEGACY_PROVENANCE)
            got = "accepted"
        except SystemExit:
            got = "refused"
        except Exception as exc:                                 # noqa: BLE001
            got = f"{exc.__class__.__name__}: {exc}"
        chk(f"legacy supplied, {name} -> refused", got, "refused")
        with open(path, "rb") as fh:
            chk(f"...and {name} was left exactly as it was",
                fh.read() == before, True)

    leg = legacy_row("A1", "AF568", 1)
    # A permuted 21-column header. The set is right and the order is not,
    # which is the case the mutant rewrote rather than refused.
    refuses("permuted_21", list(reversed(G5C.LEGACY_COLUMNS)),
            [list(reversed(leg))])
    # 20 columns: the pre-off_tissue header. `legacy` says nothing about
    # off_tissue, so this is not a file this stage may guess at - 06g is.
    keep = [c for c in G5C.LEGACY_COLUMNS if c != "off_tissue"]
    refuses("cols_20_no_off_tissue", keep,
            [[v for c, v in zip(G5C.LEGACY_COLUMNS, leg) if c != "off_tissue"]])
    # 21 columns, one of them renamed. A column this stage does not write is
    # not something to guess at either.
    refuses("renamed_column",
            ["nuclei_id" if c == "nucleus_id" else c
             for c in G5C.LEGACY_COLUMNS], [leg])


def drop_check(tmp):
    """`drop_rows` removes the marker's rows for the given uids and no others."""
    path = os.path.join(tmp, "nuclei.csv")

    def seed():
        write(path, G5C.COLUMNS, [
            row("A1", "AF568", 1), row("A1", "AF568", 2),
            row("A2", "AF568", 3), row("A3", "AF568", 4),
            row("B1", "AF488", 5),
        ])

    # --force --limit 1: todo is one uid. The other two AF568 sections and
    # the AF488 one must all still be there afterwards.
    seed()
    dropped, kept = G5C.drop_rows(path, "AF568", ["A1"])
    rows = read(path)
    chk("--force --limit: dropped only the todo uid's rows", dropped, 2)
    chk("...kept the rest", kept, 3)
    chk("...the other AF568 sections survive",
        sorted({r["scene_uid"] for r in rows if r["marker"] == "AF568"}),
        "['A2', 'A3']")
    chk("...and so does the other marker",
        [r["scene_uid"] for r in rows if r["marker"] == "AF488"], "['B1']")
    chk("...the header is intact", list(rows[0].keys()) == G5C.COLUMNS, True)
    chk("...and no .tmp is left behind", os.path.exists(path + ".tmp"), False)

    # A uid of the OTHER marker in todo is not this marker's to drop. Sections
    # are disjoint across markers, so this never happens in practice, but the
    # helper is the one place that deletes data and should be exact.
    seed()
    dropped, kept = G5C.drop_rows(path, "AF568", ["B1"])
    chk("a uid of the other marker is never dropped", dropped, 0)
    chk("...file untouched", len(read(path)), 5)

    # Plain --force: todo is every section of the marker.
    seed()
    dropped, kept = G5C.drop_rows(path, "AF568", ["A1", "A2", "A3"])
    rows = read(path)
    chk("--force with no limit drops all of the marker", dropped, 4)
    chk("...and keeps the other marker", [r["scene_uid"] for r in rows], "['B1']")

    # Dropping everything must still leave a headed, appendable file.
    seed()
    G5C.drop_rows(path, "AF568", ["A1", "A2", "A3"])
    G5C.drop_rows(path, "AF488", ["B1"])
    with open(path, newline="", encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    chk("an emptied file keeps its header", lines, [",".join(G5C.COLUMNS)])


# --------------------------------------------------------------------------
print()
print("--- 05c measures the scene, not its neighbours ---")

# The substring checks this replaced - 'scene=int(g["scene_index"])' in
# inspect.getsource(G5C), and 'doc.read(roi=roi, plane=' NOT in it - assert
# that a line of text exists (or does not) somewhere in the file. They would
# pass an implementation that read the wrong row's scene, one where the
# rectangle beside it did not match the box, or one missing a space in
# plane={"C": 0} (a `not in` check on that literal text passes just as
# happily against `plane={"C":0}`, with the space gone). 05c is where every
# nucleus in the dataset gets measured, so what actually matters is what
# reaches doc.read() - drive the real detection loop for one section and one
# ROI box through a fake reader and a fake StarDist model, and check the
# kwargs the reads recorded.
#
# What this exercises: main()'s CZI-reading path end to end - argument
# parsing, section/box resume bookkeeping, the tissue-mask gate, and the two
# CR.read_planes calls for the one box. What it does NOT exercise: real
# segmentation or measurement. The fake model reports "no nuclei found",
# which is enough to prove what reached read() without needing StarDist to
# actually run, or csbdeep normalisation / regionprops to behave any
# particular way on synthetic data.


class _FakeCzi:
    """Records the kwargs .read() was called with, and returns a fixed plane."""

    def __init__(self, shape):
        self.calls = []
        self._shape = shape

    def read(self, **kwargs):
        self.calls.append(kwargs)
        return np.random.default_rng(len(self.calls)).random(self._shape)


class _FakeOpenCzi:
    """Stands in for pylibCZIrw.czi.open_czi: a context manager around one doc."""

    def __init__(self, doc):
        self.doc = doc

    def __call__(self, path, *a, **kw):
        return self

    def __enter__(self):
        return self.doc

    def __exit__(self, *exc):
        return False


class _FakeModel:
    """Stands in for the StarDist model: reports no nuclei, every time.

    Segmentation is out of scope for this check (see the note above) - this
    is enough to prove the read reached the model with its result unused
    downstream, without loading a real pretrained model.
    """

    def predict_instances(self, img, **kw):
        return np.zeros(img.shape, dtype=np.int32), {}


def detect_read_check():
    try:
        import pylibCZIrw.czi as _real_pyczi
    except ImportError:                                          # noqa: BLE001
        print("   (pylibCZIrw is not installed on this machine - the "
              "detection read cannot be exercised, even against a fake "
              "reader, since 05c imports the package itself)")
        return

    # The marker is the STUDY's, not a literal. This suite runs under
    # use_temp_study(), whose one marker is whatever config.example.json
    # declares, and 05a's use_marker() refuses a marker the study never
    # declared. The literal used to work only because 05a's MARKERS was the
    # LS pair written into the file.
    uid, czi_file = "AB12_1a-s0", "AB12_1a.czi"
    marker = G5C.G5.MARKER
    scene_index = 6
    x0, y0, bw, bh = 300, 400, 24, 20

    os.makedirs(G5C.REFORMAT_DIR, exist_ok=True)
    G5C.G5.use_marker(marker)

    geom_row = {
        "scene_uid": uid, "animal": "AB12", "marker": marker,
        "czi_file": czi_file, "scene_index": str(scene_index),
        "m00": "1", "m01": "0", "m02": "0",
        "m10": "0", "m11": "1", "m12": "0",
    }
    with open(G5C.G5.GEOM_CSV, "w", newline="\n", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(geom_row))
        w.writeheader()
        w.writerow(geom_row)

    box_row = {
        "scene_uid": uid, "animal": "AB12", "marker": marker,
        "roi_kind": "roi", "region": "Dm", "seed_n": "1",
        "czi_x0": str(x0), "czi_y0": str(y0),
        "czi_w": str(bw), "czi_h": str(bh),
        "sec_x": "128", "sec_y": "128", "sec_r": "50", "sec_poly": "",
    }
    with open(G5C.G5.BOX_CSV, "w", newline="\n", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(box_row))
        w.writeheader()
        w.writerow(box_row)

    # Where 05c's own mask_at() will look. It tries `sections_AF568` first and
    # falls back to `sections`, so the fallback is the one directory that is
    # right for any study - the literal in mask_at is a separate genericisation
    # job, and writing to `sections_<this study's marker>` here would only pass
    # while that marker happened to be AF568.
    mask_dir = os.path.join(G5C.REFORMAT_DIR, "sections")
    os.makedirs(mask_dir, exist_ok=True)
    np.save(os.path.join(mask_dir, f"{uid}_mask.npy"),
            np.ones((256, 256), dtype=bool))

    fake_doc = _FakeCzi((bh, bw))
    saved_open_czi = _real_pyczi.open_czi
    saved_load_model = G5C.load_model
    saved_argv = sys.argv
    _real_pyczi.open_czi = _FakeOpenCzi(fake_doc)
    G5C.load_model = lambda: _FakeModel()
    sys.argv = ["05c_detect_rois.py", "--marker", marker, "--order", "uid"]
    try:
        rc = G5C.main()
    finally:
        _real_pyczi.open_czi = saved_open_czi
        G5C.load_model = saved_load_model
        sys.argv = saved_argv

    chk("the run completed", rc, 0)
    chk("two reads were made (dapi + marker) for the one box",
        len(fake_doc.calls), 2)
    chk("both reads named the same scene",
        {c["scene"] for c in fake_doc.calls}, {scene_index})
    chk("...which is the geometry row's scene_index",
        fake_doc.calls[0]["scene"] if fake_doc.calls else None, scene_index)
    chk("both reads used the box's own rectangle (czi_x0/y0/w/h)",
        {c["roi"] for c in fake_doc.calls}, {(x0, y0, bw, bh)})
    chk("...and the dapi/marker channel indices",
        sorted(c["plane"]["C"] for c in fake_doc.calls),
        sorted([G5C.DAPI_C, G5C.MARK_C]))


detect_read_check()


def _run_main(model, *flags):
    """main() against the one-box study detect_read_check() left on disk.

    Returns the exit code and how many plane reads were made - 0 reads means
    the section was not measured again.
    """
    import pylibCZIrw.czi as _real_pyczi
    fake_doc = _FakeCzi((20, 24))
    saved = (_real_pyczi.open_czi, G5C.load_model, sys.argv)
    _real_pyczi.open_czi = _FakeOpenCzi(fake_doc)
    G5C.load_model = lambda: model
    sys.argv = ["05c_detect_rois.py", "--marker", G5C.G5.MARKER,
                "--order", "uid", *flags]
    try:
        rc = G5C.main()
    finally:
        _real_pyczi.open_czi, G5C.load_model, sys.argv = saved
    return rc, len(fake_doc.calls)


def zero_found_check():
    """A section measured and found EMPTY is done; one skipped is not.

    detect_read_check()'s fake model finds nothing, so its section writes no
    nucleus rows. The done log used to be written only when there were rows,
    and the resume took "done" from the rows - so an empty section was never
    done: every plain resume measured it again, and 06a, which has only the
    rows to go on, left it out of the dataset instead of reporting zeros.

    A section skipped for want of a shared intensity range (`no_norm`) also
    writes no rows, and that one was NOT measured: it must stay undone.
    """
    try:
        import pylibCZIrw.czi  # noqa: F401
    except ImportError:                                          # noqa: BLE001
        print("   (pylibCZIrw is not installed - the zero-found resume cannot "
              "be driven through main())")
        return
    uid, marker = "AB12_1a-s0", G5C.G5.MARKER
    logged = G5C.read_done_log(G5C.DONE_LOG, marker) or set()
    chk("a section measured with nothing found is logged done",
        uid in logged, True)
    rc, reads = _run_main(_FakeModel())
    chk("...and a plain resume does not measure it again", (rc, reads), (0, 0))

    # The same section, forced, but its marker has no range to share.
    saved = (G5C.needs_shared_norm, G5C.section_range)

    def no_range(*a, **kw):
        raise G5C.SG.SegmentError("flat marker plane")
    G5C.needs_shared_norm = lambda m: True
    G5C.section_range = no_range
    try:
        rc, _ = _run_main(_FakeModel(), "--force")
    finally:
        G5C.needs_shared_norm, G5C.section_range = saved
    chk("a no_norm skip completes", rc, 0)
    logged = G5C.read_done_log(G5C.DONE_LOG, marker) or set()
    chk("...and is NOT logged done", uid in logged, False)
    rc, reads = _run_main(_FakeModel())
    chk("...so the next resume measures it", (rc, reads > 0), (0, True))
    logged = G5C.read_done_log(G5C.DONE_LOG, marker) or set()
    chk("...and then it is logged done", uid in logged, True)


print()
print("--- a section measured and found empty ---")
zero_found_check()


class _OneNucleusModel:
    """Stands in for StarDist and reports exactly one object, centred.

    The other fake reports none, which is enough to prove what reached read().
    This one has to produce a row, because the thing under test is that an
    APPENDED row and a BACKFILLED one end up the same width in the same file.
    """

    def predict_instances(self, img, **kw):
        lab = np.zeros(img.shape, dtype=np.int32)
        h, w = img.shape
        lab[h // 2 - 2:h // 2 + 2, w // 2 - 2:w // 2 + 2] = 1
        return lab, {}


def legacy_resume_check():
    """The real resume path, onto a file written before the provenance columns.

    header_check() drives check_header directly; this drives main(), because
    what matters operationally is that a resume ONTO THE OPERATOR'S FILE goes
    ahead and that what comes out is one width. Their live roi_nuclei.csv is
    961,233 rows under the 21-column header, and re-running the stage instead
    means re-segmenting every one of them with StarDist.
    """
    try:
        import pylibCZIrw.czi as _real_pyczi
    except ImportError:                                          # noqa: BLE001
        print("   (pylibCZIrw is not installed - the legacy resume cannot be "
              "driven through main())")
        return

    uid, czi_file = "AB12_9a-s0", "AB12_9a.czi"
    marker = G5C.G5.MARKER
    x0, y0, bw, bh = 0, 0, 24, 20
    G5C.G5.use_marker(marker)
    os.makedirs(G5C.RESULTS, exist_ok=True)

    geom_row = {
        "scene_uid": uid, "animal": "AB12", "marker": marker,
        "czi_file": czi_file, "scene_index": "0",
        "m00": "1", "m01": "0", "m02": "0",
        "m10": "0", "m11": "1", "m12": "0",
    }
    with open(G5C.G5.GEOM_CSV, "w", newline="\n", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(geom_row))
        w.writeheader()
        w.writerow(geom_row)

    box_row = {
        "scene_uid": uid, "animal": "AB12", "marker": marker,
        "roi_kind": "roi", "region": "Dm", "seed_n": "1",
        "czi_x0": str(x0), "czi_y0": str(y0),
        "czi_w": str(bw), "czi_h": str(bh),
        "sec_x": "12", "sec_y": "10", "sec_r": "50", "sec_poly": "",
    }
    with open(G5C.G5.BOX_CSV, "w", newline="\n", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(box_row))
        w.writeheader()
        w.writerow(box_row)

    mask_dir = os.path.join(G5C.REFORMAT_DIR, "sections")
    os.makedirs(mask_dir, exist_ok=True)
    np.save(os.path.join(mask_dir, f"{uid}_mask.npy"),
            np.ones((256, 256), dtype=bool))

    # A pre-provenance file holding another section's measurements. Another
    # section, so this run has something to do: a resume that finds its work
    # already done would stop before it ever opened the file for append.
    write(G5C.NUCLEI_CSV, G5C.LEGACY_COLUMNS,
          [legacy_row("AB12_8a-s0", marker, 1)])

    fake_doc = _FakeCzi((bh, bw))
    saved_open_czi = _real_pyczi.open_czi
    saved_load_model = G5C.load_model
    saved_argv = sys.argv
    _real_pyczi.open_czi = _FakeOpenCzi(fake_doc)
    G5C.load_model = lambda: _OneNucleusModel()
    sys.argv = ["05c_detect_rois.py", "--marker", marker, "--order", "uid"]
    try:
        rc = G5C.main()
    finally:
        _real_pyczi.open_czi = saved_open_czi
        G5C.load_model = saved_load_model
        sys.argv = saved_argv

    chk("a resume onto a pre-provenance file completes", rc, 0)
    with open(G5C.NUCLEI_CSV, newline="", encoding="utf-8") as fh:
        lines = list(csv.reader(fh))
    chk("...the file carries the current header", lines[0] == G5C.COLUMNS, True)
    chk("...one width for every line, old rows and new",
        {len(r) for r in lines}, "{%d}" % len(G5C.COLUMNS))
    rows = read(G5C.NUCLEI_CSV)
    chk("...the appended nucleus is there",
        sorted(r["scene_uid"] for r in rows), "['AB12_8a-s0', '%s']" % uid)
    old = [r for r in rows if r["scene_uid"] == "AB12_8a-s0"][0]
    chk("...the backfilled row reads as the counterstain",
        old["segmented_on"], G5C.segment_plane_for(marker))
    chk("...segmented by stardist", old["backend"], "stardist")
    chk("...and nucleus-shaped", old["nucleus_shaped"], "1")
    # The failure this whole fix exists to prevent: DictReader gives None for a
    # field the header does not name, None is falsy, and 06a would then withhold
    # the Abercrombie correction from every correctly-nuclear row.
    chk("...and no row has a field the header does not name",
        any(None in r for r in rows), False)
    # The invariant that matters: one file, one answer. A backfilled row and a
    # row this run measured must not describe the same segmentation two ways.
    new = [r for r in rows if r["scene_uid"] == uid][0]
    chk("the newly measured nucleus carries the same provenance",
        (new["segmented_on"], new["backend"], new["nucleus_shaped"]),
        (old["segmented_on"], old["backend"], old["nucleus_shaped"]))


print()
print("--- a resume onto a file written before the provenance columns ---")
legacy_resume_check()


def changed_boxes_check():
    """A re-export re-measures the sections whose boxes changed, and no others.

    Any change to the curation export used to cost a full re-detection - hours
    on a real study: the app saw 05a's boxes change and ran 05c with --force,
    because 05c's plain resume skipped every section that had rows, the moved
    discs included (the review's bug 7). Now each section is logged with a
    fingerprint of the boxes (and geometry row) it was measured on, and a
    resume re-measures exactly the sections whose fingerprint differs, drops
    the ones no longer in the boxes, and keeps the rest untouched.
    """
    try:
        import pylibCZIrw.czi  # noqa: F401
    except ImportError:                                          # noqa: BLE001
        print("   (pylibCZIrw is not installed - the incremental re-detection "
              "cannot be driven through main())")
        return
    marker = G5C.G5.MARKER
    G5C.G5.use_marker(marker)
    for p in (G5C.NUCLEI_CSV, G5C.DONE_LOG, G5C.GEOMETRY_CSV, G5C.NORM_CSV,
              G5C.BACKGROUND_CSV, G5C.NO_ARTIFACT_CSV, G5C.SETTINGS_JSON):
        if os.path.exists(p):
            os.remove(p)
    # Three sections, each read at its own x so a read names its section.
    secs = {"AB12_2a-s0": 100, "AB12_3a-s0": 200, "AB12_4a-s0": 300}
    mask_dir = os.path.join(G5C.REFORMAT_DIR, "sections")
    os.makedirs(mask_dir, exist_ok=True)

    def put(boxes):
        """Write the geometry and box files for {uid: sec_r}."""
        gcols = ["scene_uid", "animal", "marker", "czi_file", "scene_index",
                 "m00", "m01", "m02", "m10", "m11", "m12"]
        bcols = ["scene_uid", "animal", "marker", "roi_kind", "region",
                 "seed_n", "czi_x0", "czi_y0", "czi_w", "czi_h", "sec_x",
                 "sec_y", "sec_r", "sec_poly"]
        with open(G5C.G5.GEOM_CSV, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(gcols)
            for u in boxes:
                w.writerow([u, "AB12", marker, u + ".czi", "0",
                            "1", "0", "0", "0", "1", "0"])
        with open(G5C.G5.BOX_CSV, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(bcols)
            for u, r in boxes.items():
                x0 = secs[u]
                w.writerow([u, "AB12", marker, "roi", "Dm", "1", x0, 0, 24,
                            20, x0 + 12, 10, r, ""])
        for u in boxes:
            np.save(os.path.join(mask_dir, f"{u}_mask.npy"),
                    np.ones((512, 512), dtype=bool))

    def run(*flags):
        """(exit code, the sections read) for one main() run."""
        import pylibCZIrw.czi as _real_pyczi
        fake_doc = _FakeCzi((20, 24))
        saved = (_real_pyczi.open_czi, G5C.load_model, sys.argv)
        _real_pyczi.open_czi = _FakeOpenCzi(fake_doc)
        G5C.load_model = lambda: _OneNucleusModel()
        sys.argv = ["05c_detect_rois.py", "--marker", marker, "--order", "uid",
                    *flags]
        try:
            rc = G5C.main()
        finally:
            _real_pyczi.open_czi, G5C.load_model, sys.argv = saved
        by_x = {x: u for u, x in secs.items()}
        return rc, sorted({by_x[c["roi"][0]] for c in fake_doc.calls})

    def nuclei():
        return sorted(r["scene_uid"] for r in read(G5C.NUCLEI_CSV))

    def geometry():
        return {r["scene_uid"]: r["sec_r"] for r in read(G5C.GEOMETRY_CSV)}

    A, B, C = sorted(secs)
    put({A: "50", B: "50", C: "50"})
    chk("a first run measures every section", run(), (0, [A, B, C]))
    chk("...one nucleus each", nuclei(), [A, B, C])
    chk("...and logs each with a box fingerprint",
        sorted(G5C.read_done_prints(G5C.DONE_LOG, marker)), [A, B, C])
    chk("the same boxes again measure nothing", run(), (0, []))

    # B's disc resized, C dropped from the curation, A untouched.
    put({A: "50", B: "40"})
    chk("a re-export re-measures only the section whose boxes changed",
        run(), (0, [B]))
    chk("...keeps the unchanged section's rows and replaces the changed one's,"
        " dropping the removed section's", nuclei(), [A, B])
    chk("...its measured geometry is the new shape, the removed one's gone",
        geometry(), {A: "50", B: "40"})
    logged = G5C.read_done_log(G5C.DONE_LOG, marker)
    chk("...and the done log no longer names the removed section",
        sorted(logged), [A, B])
    chk("a resume after that has nothing to do", run(), (0, []))

    # A section ADDED to the curation is measured, and only it.
    put({A: "50", B: "40", C: "50"})
    chk("an added section is measured on its own", run(), (0, [C]))

    # Sections measured before the fingerprint existed: two columns per log
    # line. The measured-geometry record still decides for them.
    with open(G5C.DONE_LOG, newline="", encoding="utf-8") as fh:
        old = [r[:2] for r in csv.reader(fh)]
    with open(G5C.DONE_LOG, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(old)
    put({A: "45", B: "40", C: "50"})
    chk("with no fingerprint, a shape changed since its recorded geometry is "
        "re-measured", run(), (0, [A]))
    chk("...and it is fingerprinted now",
        A in G5C.read_done_prints(G5C.DONE_LOG, marker), True)

    # No fingerprint AND no geometry record: nothing says what the section
    # was measured on, so it is kept, as every resume before this did.
    with open(G5C.DONE_LOG, newline="", encoding="utf-8") as fh:
        old = [r[:2] for r in csv.reader(fh)]
    with open(G5C.DONE_LOG, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(old)
    os.remove(G5C.GEOMETRY_CSV)
    put({A: "45", B: "30", C: "50"})
    chk("with neither record, a section is kept as before", run(), (0, []))
    chk("...rows and all", nuclei(), [A, B, C])
    chk("--force still re-measures everything", run("--force"), (0, [A, B, C]))
    chk("...one nucleus each, nothing doubled", nuclei(), [A, B, C])

    # A box file that has lost most of what was measured looks like a trial
    # 05a run (--limit), not a curation: the measurements stay.
    put({A: "45"})
    chk("most sections gone from the boxes: nothing is dropped",
        (run(), nuclei()), ((0, []), [A, B, C]))
    put({A: "45", B: "30", C: "50"})
    chk("...and they are still done when the boxes come back", run(), (0, []))

    # Different detection thresholds still refuse a resume onto the old rows:
    # only a full --force re-measure can change them.
    if G5C.backend_for(marker) == "stardist":
        saved = G5C.stardist_settings
        G5C.stardist_settings = lambda m: {"prob_thresh": 0.9,
                                           "nms_thresh": None,
                                           "source": "test"}
        try:
            put({A: "45", B: "35", C: "50"})
            chk("a box change with new thresholds is refused, not mixed",
                run(), (1, []))
        finally:
            G5C.stardist_settings = saved


print()
print("--- a re-export re-measures only the sections whose boxes changed ---")
changed_boxes_check()


def main():
    tmp = tempfile.mkdtemp(prefix="ls5c_")
    try:
        header_check(tmp)
        drop_check(tmp)
        torn_check(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    print("ALL PASS" if not fails else f"{fails} FAILURE(S)")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
