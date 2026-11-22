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
    # Exit 1, not 0: the app runs 05c in process and shows a stage done on 0,
    # so a run that left a section unmeasured looked complete.
    chk("a no_norm skip fails the run", rc, 1)
    logged = G5C.read_done_log(G5C.DONE_LOG, marker) or set()
    chk("...and is NOT logged done", uid in logged, False)
    rc, reads = _run_main(_FakeModel())
    chk("...so the next resume measures it", (rc, reads > 0), (0, True))
    logged = G5C.read_done_log(G5C.DONE_LOG, marker) or set()
    chk("...and then it is logged done", uid in logged, True)


print()
print("--- a section measured and found empty ---")
zero_found_check()


def no_tissue_mask_check():
    """A section with no tissue mask is skipped, and the run says it failed.

    Without 04a's `<uid>_mask.npy` there is no telling a nucleus on the section
    from one on the glass, so 05c skips the section. It used to return 0 all
    the same, and the app - which runs 05c in process and reads only the
    return value - showed the stage done with the section never measured.
    """
    try:
        import pylibCZIrw.czi  # noqa: F401
    except ImportError:                                          # noqa: BLE001
        print("   (pylibCZIrw is not installed - main() cannot be driven)")
        return
    import contextlib
    import io
    uid, marker = "AB12_1a-s0", G5C.G5.MARKER
    mask = os.path.join(G5C.REFORMAT_DIR, "sections", f"{uid}_mask.npy")
    aside = mask + ".aside"
    os.replace(mask, aside)
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            rc, reads = _run_main(_FakeModel(), "--force")
    finally:
        os.replace(aside, mask)
    chk("a section with no tissue mask fails the run", rc, 1)
    chk("...and is not read at all", reads, 0)
    chk("...writes no nucleus rows",
        [r for r in read(G5C.NUCLEI_CSV) if r["scene_uid"] == uid], [])
    chk("...and no status rows",
        [r for r in read(G5C.STATUS_CSV) if r["scene_uid"] == uid], [])
    logged = G5C.read_done_log(G5C.DONE_LOG, marker) or set()
    chk("...and is NOT logged done", uid in logged, False)
    said = [ln.strip() for ln in out.getvalue().splitlines() if ln.strip()]
    last = said[-1] if said else ""
    # app/runner.py shows the last line printed as the failure message.
    chk("...the last line says how many were not measured",
        "1 section(s) NOT MEASURED" in last, True)
    chk("...and that the stage is not complete", "not complete" in last, True)
    rc, reads = _run_main(_FakeModel())
    chk("with the mask back, the resume measures it and completes",
        (rc, reads > 0), (0, True))
    logged = G5C.read_done_log(G5C.DONE_LOG, marker) or set()
    chk("...and then it is logged done", uid in logged, True)


print()
print("--- a section with no tissue mask ---")
no_tissue_mask_check()


def usable_area_check():
    """The share of an ROI's outline a nucleus could be counted on.

    06a drops nuclei flagged artifact or off_tissue; the area it divides by
    has to lose the same ground. The map is the identity here, so native px
    are grid indices: a disc of radius 40 about grid (128, 128) in a 97 px
    box, with the masks laid over known halves of it.
    """
    Minv = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    # The curator stores pixel edges; find the stored centre that lands on
    # grid index 128 whatever the offset is.
    g0 = float(G5C.G5.curator_to_grid(0.0, 0.0)[0])
    c = 128.0 - g0
    b = {"sec_x": c, "sec_y": c, "sec_r": 40, "sec_poly": "",
         "czi_x0": 80, "czi_y0": 80, "czi_w": 97, "czi_h": 97}
    full = np.ones((256, 256), bool)
    left = full.copy()
    left[:, 128:] = False

    frac, how = G5C.usable_area_fraction(b, Minv, full, None, None, None)
    chk("all tissue, no artifact mask: the whole outline", round(frac, 3), 1.0)
    chk("...judged on the grid (no overview-frame mask)", how, "grid")
    frac, _ = G5C.usable_area_fraction(b, Minv, left, None, None, None)
    chk("tissue under the left half only: about half", abs(frac - 0.5) < 0.02, True)
    frac, _ = G5C.usable_area_fraction(b, Minv, full, None, left, None)
    chk("an artifact over the left half: about half", abs(frac - 0.5) < 0.02, True)
    frac, _ = G5C.usable_area_fraction(b, Minv, left, None, left, None)
    chk("tissue only where the artifact is: none", frac, 0.0)
    frac, _ = G5C.usable_area_fraction(b, Minv, ~full, None, None, None)
    chk("no tissue at all: none", frac, 0.0)

    # The overview-frame masks are read at the point's own position: mask
    # pixel p sits at scene_x + p * k.
    ov_full = (np.ones((64, 64), bool), 0.0, 0.0, 4.0)
    ov_left = (np.zeros((64, 64), bool), 0.0, 0.0, 4.0)
    ov_left[0][:, :32] = True                       # native x < 126
    frac, how = G5C.usable_area_fraction(b, Minv, full, ov_left, None, None)
    chk("the overview mask decides where there is one", abs(frac - 0.5) < 0.05, True)
    chk("...and says so", how, "overview")
    frac, how = G5C.usable_area_fraction(b, Minv, full, ov_full, left, None)
    chk("an artifact with only its grid copy is 'grid'", how, "grid")

    # The same rule that flags a nucleus: a point is usable exactly when the
    # loop would have kept a centroid there.
    pts = [(100.0, 128.0), (150.0, 128.0), (128.0, 100.0), (-5.0, 3.0)]
    one = [bool(G5C.overview_hit(ov_left, x, y)) for x, y in pts]
    many = G5C.overview_hits(ov_left, np.array([p[0] for p in pts]),
                             np.array([p[1] for p in pts])).tolist()
    chk("overview_hits agrees with overview_hit point for point", many, one)

    frac, how = G5C.usable_area_fraction(dict(b, czi_w=0), Minv, full, None,
                                         None, None)
    chk("a box with no pixels has no fraction", (frac, how), (None, ""))

    rows = G5C.status_rows("U", [dict(b, marker="M"), dict(b, marker="M")],
                           {2: "the box is 4 x 90 px"}, Minv, full, None,
                           None, None)
    chk("status rows: one per box, numbered from 1",
        [(r[0], r[1], r[2]) for r in rows], [("U", "M", 1), ("U", "M", 2)])
    chk("...segmented, and the one that was not, with its reason",
        [(r[3], r[4]) for r in rows], [(1, ""), (0, "the box is 4 x 90 px")])
    chk("...each with its usable fraction", [float(r[5]) for r in rows], [1.0, 1.0])


print()
print("--- the usable area of an ROI ---")
usable_area_check()


def status_written_check():
    """main() leaves a status row for every box of a section it measures,
    and --backfill-status adds the area for a section that has none."""
    try:
        import pylibCZIrw.czi  # noqa: F401
    except ImportError:                                          # noqa: BLE001
        print("   (pylibCZIrw is not installed - main() cannot be driven)")
        return
    uid, marker = "AB12_1a-s0", G5C.G5.MARKER
    rc, _ = _run_main(_FakeModel(), "--force")
    chk("a forced run completes", rc, 0)
    rows = [r for r in read(G5C.STATUS_CSV) if r["scene_uid"] == uid]
    chk("one status row for the section's one box",
        [(r["marker"], r["roi_index"], r["segmented"]) for r in rows],
        [(marker, "1", "1")])
    # This fixture's box (300, 400) lies off its own shape (grid 128, r 50,
    # identity map), so no sample is inside it: no fraction, and 06a then
    # divides by the whole outline. usable_area_check() has the real cases.
    chk("...and no fraction for a box that misses its own shape",
        rows[0]["usable_area_fraction"] if rows else None, "")
    rc, _ = _run_main(_FakeModel(), "--force")
    chk("forcing it again replaces the row, never doubles it",
        len([r for r in read(G5C.STATUS_CSV) if r["scene_uid"] == uid]), 1)

    # A section measured before the record existed: no file at all.
    os.remove(G5C.STATUS_CSV)
    rc, reads = _run_main(_FakeModel(), "--backfill-status")
    chk("--backfill-status completes and reads no CZI", (rc, reads), (0, 0))
    rows = [r for r in read(G5C.STATUS_CSV) if r["scene_uid"] == uid]
    chk("...adds the section's row", len(rows), 1)
    chk("...leaves `segmented` blank: it was not recorded",
        rows[0]["segmented"] if rows else None, "")
    rc, _ = _run_main(_FakeModel(), "--backfill-status")
    chk("...and a second backfill adds nothing",
        len([r for r in read(G5C.STATUS_CSV) if r["scene_uid"] == uid]), 1)

    # 06j is that same backfill for every marker that has boxes: the repair
    # the app runs. Handed this suite's 05c, so it works on this study.
    J = load("_j6", "06j_backfill_roi_area.py")
    J._load = lambda *a: G5C
    os.remove(G5C.STATUS_CSV)
    chk("06j completes", J.main(), 0)
    chk("...and writes the section's row",
        [r["scene_uid"] for r in read(G5C.STATUS_CSV)], [uid])
    saved = G5C.NUCLEI_CSV
    G5C.NUCLEI_CSV = saved + ".absent"
    try:
        chk("06j with nothing measured is a failure, not a quiet success",
            J.main(), 1)
    finally:
        G5C.NUCLEI_CSV = saved


print()
print("--- the box status record ---")
status_written_check()


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


def multiplex_second_marker_check():
    """Under `multiplex`, the second marker's first run measures every section.

    Both markers' rows in roi_nuclei.csv name the SAME sections, and the
    resume counted a section as measured if it had a row of ANY marker. So
    the first `--marker Mk2` run after Mk1 found Mk1's rows, seeded the done
    log (which had no Mk2 entry yet) with every one of those sections as done
    for Mk2, and measured nothing; 06a then reported them all as measured and
    empty for Mk2. And a box file from before 05a stamped the placed marker
    (roi_boxes_Mk2.csv saying Mk1 on every row) made 05c read Mk1's plane and
    label the nuclei Mk1; it is refused now.
    """
    try:
        import pylibCZIrw.czi as _real_pyczi
    except ImportError:                                          # noqa: BLE001
        print("   (pylibCZIrw is not installed - the multiplex resume cannot "
              "be driven through main())")
        return
    from _fixture import temp_study, load_stage
    with temp_study(acquisition={"layout": "multiplex", "channels": [
            {"name": "DAPI", "role": "nuclear", "czi_name": "DAPI", "index": 0},
            {"name": "Mk1", "role": "marker", "czi_name": "AF568", "index": 1,
             "segment": "nuclear"},
            {"name": "Mk2", "role": "marker", "czi_name": "AF647", "index": 2,
             "segment": "nuclear"}]}):
        M = load_stage("05c_detect_rois.py", name="lsstage_05c_mx2")
        secs = {"AB12_2a-s0": 100, "AB12_3a-s0": 200}
        mask_dir = os.path.join(M.REFORMAT_DIR, "sections")
        os.makedirs(mask_dir, exist_ok=True)
        gcols = ["scene_uid", "animal", "marker", "czi_file", "scene_index",
                 "m00", "m01", "m02", "m10", "m11", "m12"]
        bcols = ["scene_uid", "animal", "marker", "roi_kind", "region",
                 "seed_n", "czi_x0", "czi_y0", "czi_w", "czi_h", "sec_x",
                 "sec_y", "sec_r", "sec_poly"]

        def put(mk, stamp=None):
            with open(M.G5.geom_csv(mk), "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(gcols)
                for u in secs:
                    w.writerow([u, "AB12", mk, u + ".czi", "0",
                                "1", "0", "0", "0", "1", "0"])
            with open(M.G5.box_csv(mk), "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(bcols)
                for u, x0 in secs.items():
                    w.writerow([u, "AB12", stamp or mk, "roi", "Dm", "1", x0, 0,
                                24, 20, x0 + 12, 10, "50", ""])
            for u in secs:
                np.save(os.path.join(mask_dir, f"{u}_mask.npy"),
                        np.ones((512, 512), dtype=bool))

        def run(mk):
            """(exit code or SystemExit text, sections read, planes read)."""
            fake_doc = _FakeCzi((20, 24))
            saved = (_real_pyczi.open_czi, M.load_model, sys.argv)
            _real_pyczi.open_czi = _FakeOpenCzi(fake_doc)
            M.load_model = lambda: _OneNucleusModel()
            sys.argv = ["05c_detect_rois.py", "--marker", mk, "--order", "uid"]
            try:
                rc = M.main()
            except SystemExit as exc:
                rc = f"SystemExit: {exc}"
            finally:
                _real_pyczi.open_czi, M.load_model, sys.argv = saved
            by_x = {x: u for u, x in secs.items()}
            return (rc, sorted({by_x[c["roi"][0]] for c in fake_doc.calls}),
                    sorted({c["plane"]["C"] for c in fake_doc.calls}))

        put("Mk1")
        put("Mk2")
        A, B = sorted(secs)
        chk("multiplex: Mk1's run measures both sections", run("Mk1")[:2],
            (0, [A, B]))
        rc, read_secs, planes = run("Mk2")
        chk("multiplex: Mk2's first run after Mk1 measures every section",
            (rc, read_secs), (0, [A, B]))
        chk("...on Mk2's own plane", 2 in planes, True)
        chk("...and logs them done for Mk2",
            sorted(M.read_done_log(M.DONE_LOG, "Mk2") or []), [A, B])
        chk("...with Mk2's own nuclei in the file",
            sorted((r["scene_uid"], r["marker"]) for r in read(M.NUCLEI_CSV)),
            sorted([(A, "Mk1"), (B, "Mk1"), (A, "Mk2"), (B, "Mk2")]))

        # The stale file: every row of roi_boxes_Mk2.csv says Mk1.
        put("Mk2", stamp="Mk1")
        rc, read_secs, _ = run("Mk2")
        chk("multiplex: a stale roi_boxes_Mk2.csv (rows say Mk1) is refused",
            str(rc).startswith("SystemExit") and "roi_boxes_Mk2.csv" in str(rc)
            and "['Mk1']" in str(rc), True)
        chk("...before anything is read", read_secs, [])


print()
print("--- multiplex: the second marker is measured, not inherited ---")
multiplex_second_marker_check()


def drive_drop_check():
    """The drive drops while a section is being measured: wait, redo it.

    The slides are on a USB drive that goes away rather than failing a read.
    05c's unit is one section's ROIs: a read that fails while the slides
    folder is unreachable waits for it, the section is measured again from
    its first ROI - the CZI opened afresh - and committed once, by the
    section's own checkpoint. What reaches the disk must be exactly what an
    uninterrupted run writes: these are the paper's counts. A read that fails
    with the folder there is an ordinary failure, and stops the run as it
    always did, with the sections before it on disk and logged.

    The drop is real as far as the stage can tell: the stand-in reader renames
    the slides folder away and raises OSError, and a thread puts it back.
    Handles opened before a wait refuse to read after it, as a vanished
    volume's do. A model that finds objects, and pixels keyed by what is read
    (never by how often), so a redo that read anything twice, or a row added
    twice, shows.
    """
    try:
        import pylibCZIrw.czi as _real_pyczi
        import csbdeep.utils  # noqa: F401
    except ImportError as exc:                                   # noqa: BLE001
        print(f"   ({exc} - the detection loop cannot be driven here)")
        return
    import contextlib
    import glob
    import io
    import threading
    import time
    from _fixture import temp_study, load_stage

    W, H = 24, 20
    paired = {"layout": "paired", "markers": ["AF568", "AF488"], "channels": []}
    with temp_study(acquisition=paired) as study:
        M = load_stage("05c_detect_rois.py", name="lsstage_05c_drop")
        DR = M.IO.drive_module()
        chk("ls_drive loads", DR is not None, True)
        saved_env = {k: os.environ.get(k) for k in ("LS_WATCH", "LS_PROGRESS")}
        saved_poll = DR.POLL
        os.environ["LS_WATCH"] = study.source_dir
        os.environ["LS_PROGRESS"] = "1"
        DR.POLL = 0.05
        marker = "AF568"
        M.G5.use_marker(marker)
        src = study.source_dir
        secs = {"AB12_1a-s0": ("AB12_1a.czi", (100, 130)),
                "AB12_2a-s0": ("AB12_2a.czi", (400, 430))}
        for fname, _ in secs.values():
            open(os.path.join(src, fname), "wb").close()

        geoms = [{"scene_uid": u, "animal": "AB12", "marker": marker,
                  "czi_file": f, "scene_index": "0",
                  "m00": "1", "m01": "0", "m02": "0",
                  "m10": "0", "m11": "1", "m12": "0"} for u, (f, _) in secs.items()]
        os.makedirs(os.path.dirname(M.G5.GEOM_CSV), exist_ok=True)
        os.makedirs(M.RESULTS, exist_ok=True)
        write(M.G5.GEOM_CSV, list(geoms[0]), [list(g.values()) for g in geoms])
        boxes = [{"scene_uid": u, "animal": "AB12", "marker": marker,
                  "roi_kind": "roi", "region": "Dm", "seed_n": str(i),
                  "czi_x0": str(x), "czi_y0": "100", "czi_w": str(W),
                  "czi_h": str(H), "sec_x": str(x + 5.0), "sec_y": "105.0",
                  "sec_r": "4", "sec_poly": ""}
                 for u, (_, xs) in secs.items() for i, x in enumerate(xs, 1)]
        write(M.G5.BOX_CSV, list(boxes[0]), [list(b.values()) for b in boxes])
        mask_dir = os.path.join(M.REFORMAT_DIR, "sections")
        os.makedirs(mask_dir, exist_ok=True)
        for u in secs:
            np.save(os.path.join(mask_dir, f"{u}_mask.npy"),
                    np.ones((256, 256), dtype=bool))

        drop = {"at": None, "thread": None}
        brk = {"at": None}
        reads = []

        def vanish_for(seconds):
            gone = src + ".gone"
            os.rename(src, gone)

            def back():
                time.sleep(seconds)
                os.rename(gone, src)
            t = threading.Thread(target=back, daemon=True)
            t.start()
            return t

        class Doc:
            def __init__(self, path):
                if not os.path.exists(path):
                    raise FileNotFoundError(path)
                self.path, self.gen = path, len(DR.PAUSES)

            def read(self, **kw):
                key = (int(kw["plane"]["C"]), int(kw["roi"][0]))
                if len(DR.PAUSES) > self.gen:
                    raise OSError(116, "Stale file handle", self.path)
                if brk["at"] == key:
                    raise RuntimeError("corrupt subblock")
                if drop["at"] == key:
                    drop["at"] = None
                    drop["thread"] = vanish_for(0.3)
                    raise OSError(5, "Input/output error", self.path)
                reads.append(key)
                rng = np.random.default_rng(1000 * key[0] + key[1])
                a = rng.normal(250, 5, (H, W))
                if key[0] == 0:
                    a[:8, :] = 900
                return a

        class Opener:
            def __call__(self, path, *a, **kw):
                self.doc = Doc(path)
                return self

            def __enter__(self):
                return self.doc

            def __exit__(self, *exc):
                return False

        class Model:
            def predict_instances(self, img, **kw):
                lab = np.zeros(np.asarray(img).shape, dtype=np.int32)
                if np.ptp(img) > 0:
                    lab[2:8, 2:8] = 1
                    lab[12:18, 14:20] = 2
                return lab, {}

        def run05c(*flags):
            saved = (_real_pyczi.open_czi, M.load_model, sys.argv)
            _real_pyczi.open_czi = Opener()
            M.load_model = lambda: Model()
            sys.argv = ["05c_detect_rois.py", "--marker", marker,
                        "--order", "uid", "--no-overlays", *flags]
            out = io.StringIO()
            try:
                with contextlib.redirect_stdout(out):
                    try:
                        rc = M.main()
                    except Exception as exc:            # noqa: BLE001
                        rc = exc
            finally:
                _real_pyczi.open_czi, M.load_model, sys.argv = saved
                if drop["thread"] is not None:
                    drop["thread"].join(5)
                    drop["thread"] = None
            return rc, out.getvalue()

        out_root = study.out_root
        tables = [M.NUCLEI_CSV, M.GEOMETRY_CSV, M.STATUS_CSV, M.DONE_LOG,
                  M.NORM_CSV, M.BACKGROUND_CSV, M.NO_ARTIFACT_CSV,
                  M.SETTINGS_JSON, M.COLOC_CSV]

        def clear():
            for p in tables:
                if os.path.exists(p):
                    os.remove(p)
            shutil.rmtree(M.OUTLINE_DIR, ignore_errors=True)

        def snap():
            got = {}
            for p in tables:
                if os.path.exists(p):
                    with open(p, "rb") as fh:
                        got[os.path.relpath(p, out_root)] = fh.read()
            # Outlines by content: an npz is a zip, whose members carry times.
            for p in sorted(glob.glob(os.path.join(M.OUTLINE_DIR, "**", "*.npz"),
                                      recursive=True)):
                with np.load(p) as z:
                    got[os.path.relpath(p, out_root)] = {
                        k: z[k].tobytes() + str(z[k].dtype).encode()
                        for k in z.files}
            return got

        def tmp_left():
            return sorted(glob.glob(os.path.join(out_root, "**", "*.tmp"), recursive=True)
                          + glob.glob(os.path.join(out_root, "**", "*.part"),
                                      recursive=True))

        def dups(path, *cols):
            keys = [tuple(r[c] for c in cols) for r in read(path)]
            return len(keys) - len(set(keys))

        try:
            print("--- 05c: the drive drops mid-section ---")
            clear()
            rc, text = run05c()
            chk("uninterrupted 05c exits zero", rc, 0)
            base = snap()
            n_rows = len(read(M.NUCLEI_CSV))
            chk("...and counted nuclei in both sections",
                sorted({r["scene_uid"] for r in read(M.NUCLEI_CSV)}), sorted(secs))
            n_reads = len(reads)

            clear()
            reads.clear()
            drop["at"] = (1, 430)         # the second ROI of the second section
            rc, text = run05c()
            chk("the drop fired", drop["at"], None)
            chk("05c with the drive dropped exits zero", rc, 0)
            chk("...says the drive came back", "@@drive back" in text, True)
            got = snap()
            chk("...writes the same files", sorted(got), sorted(base))
            chk("...byte for byte", [k for k in base if got.get(k) != base[k]], [])
            chk("...the same number of nucleus rows", len(read(M.NUCLEI_CSV)), n_rows)
            chk("...no duplicate (scene_uid, roi) rows in the box status",
                dups(M.STATUS_CSV, "scene_uid", "marker", "roi_index"), 0)
            chk("...nor in the measured geometry",
                dups(M.GEOMETRY_CSV, "scene_uid", "marker", "roi_index"), 0)
            chk("...nor a nucleus twice",
                dups(M.NUCLEI_CSV, "scene_uid", "roi_index", "nucleus_id"), 0)
            chk("...the dropped section read again from its first ROI",
                len(reads) > n_reads, True)
            chk("...no .tmp or .part left", tmp_left(), [])

            print()
            print("--- 05c: the folder is gone before a section is read ---")
            clear()
            t = vanish_for(0.3)
            rc, text = run05c()
            t.join(5)
            chk("exits zero", rc, 0)
            chk("...after waiting for the drive", "@@drive back" in text, True)
            got = snap()
            chk("...byte for byte as uninterrupted",
                [k for k in base if got.get(k) != base[k]], [])

            print()
            print("--- 05c: a read that fails with the folder there: as before ---")
            clear()
            brk["at"] = (1, 430)
            rc, text = run05c()
            brk["at"] = None
            chk("the failure stops the run, as it always did",
                type(rc).__name__ + ": " + str(rc), "RuntimeError: corrupt subblock")
            chk("...without waiting for a drive that never left", "@@drive" in text, False)
            chk("...the section before it is on disk",
                sorted({r["scene_uid"] for r in read(M.NUCLEI_CSV)}), ["AB12_1a-s0"])
            chk("...and logged done, the failed one not",
                sorted(M.read_done_log(M.DONE_LOG, marker) or set()), ["AB12_1a-s0"])
            rc, text = run05c()
            chk("the resume completes", rc, 0)
            with open(M.NUCLEI_CSV, "rb") as fh:
                chk("...and the counts are as uninterrupted",
                    fh.read() == base[os.path.relpath(M.NUCLEI_CSV, out_root)], True)

            print()
            print("--- 05c: Stopped is not swallowed ---")
            clear()
            stop = os.path.join(study.root, "STOP")
            open(stop, "wb").close()
            os.environ["LS_STOP_FILE"] = stop
            drop["at"] = (1, 430)
            try:
                rc, text = run05c()
            finally:
                os.environ.pop("LS_STOP_FILE", None)
                os.remove(stop)
            chk("the stop file stops the stage", type(rc).__name__, "Stopped")
            chk("...the stopped section is not logged done",
                sorted(M.read_done_log(M.DONE_LOG, marker) or set()), ["AB12_1a-s0"])

            print()
            print("--- 05c: a mask looked for as the drive blips is not 'absent' ---")
            # The race ls_drive.present closes: the folder is away for the one
            # look at the artifact mask and back before anything polls. Read
            # as absent, every nucleus of the section would say artifact=0 -
            # wrong counts, and nothing anywhere to say so.
            for u in secs:
                np.save(os.path.join(mask_dir, f"{u}_artifact.npy"),
                        np.ones((256, 256), dtype=bool))
            clear()
            rc, text = run05c()
            chk("with artifact masks: exits zero", rc, 0)
            base_art = snap()
            # Section 1's boxes lie on the 256 grid (identity map), so the
            # mask flags its nuclei; section 2's lie off it.
            chk("...every nucleus of section 1 is on an artifact",
                sorted({r["artifact"] for r in read(M.NUCLEI_CSV)
                        if r["scene_uid"] == "AB12_1a-s0"}), ["1"])
            target = os.path.join(mask_dir, "AB12_1a-s0_artifact.npy")
            real_exists = os.path.exists
            looks = []

            def blip(p):
                if p == target:
                    looks.append(p)
                    if len(looks) == 2:      # 1: the no-artifact warning; 2: the read
                        return False
                return real_exists(p)
            clear()
            os.path.exists = blip
            try:
                rc, text = run05c()
            finally:
                os.path.exists = real_exists
            chk("the blip happened", len(looks) >= 2, True)
            chk("...exits zero", rc, 0)
            got = snap()
            chk("...the counts are as uninterrupted, artifact flags and all",
                [k for k in base_art if got.get(k) != base_art[k]], [])
            for u in secs:
                os.remove(os.path.join(mask_dir, f"{u}_artifact.npy"))

            print()
            print("--- 05c: out_root drops between sections ---")
            # The tables 05c appends to are on out_root. A handle opened before
            # a drop is dead after it on Windows even with the drive back, so
            # a run that held its handles for the whole run failed at the next
            # commit. Here every handle 05c opens for writing under out_root
            # refuses to write once a wait has ended since it was opened, as a
            # vanished volume's do; the drop comes while section 2 is read,
            # after section 1 is committed.
            import builtins
            real_open = builtins.open

            class DeadAfterDrop:
                def __init__(self, fh):
                    self._fh, self._gen = fh, len(DR.PAUSES)

                def _live(self):
                    if len(DR.PAUSES) > self._gen:
                        raise OSError(116, "Stale file handle", self._fh.name)

                def write(self, s):
                    self._live()
                    return self._fh.write(s)

                def flush(self):
                    self._live()
                    return self._fh.flush()

                def fileno(self):
                    self._live()
                    return self._fh.fileno()

                def __getattr__(self, name):
                    return getattr(self._fh, name)

                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    self._fh.close()
                    return False

            def guarded_open(path, mode="r", *a, **kw):
                fh = real_open(path, mode, *a, **kw)
                if any(c in mode for c in "aw+") and \
                        os.path.abspath(str(path)).startswith(out_root + os.sep):
                    return DeadAfterDrop(fh)
                return fh

            odrop = {"at": (0, 400), "thread": None}
            real_read = Doc.read

            def read_dropping_out_root(self, **kw):
                key = (int(kw["plane"]["C"]), int(kw["roi"][0]))
                if odrop["at"] == key:
                    odrop["at"] = None
                    os.rename(out_root, out_root + ".gone")
                    # The read blocks while the drive is away, as a real one does.
                    time.sleep(0.4)
                    os.rename(out_root + ".gone", out_root)
                return real_read(self, **kw)

            os.environ["LS_WATCH"] = os.pathsep.join([src, out_root])
            clear()
            Doc.read = read_dropping_out_root
            M.open = guarded_open
            try:
                rc, text = run05c()
            finally:
                Doc.read = real_read
                del M.open
                os.environ["LS_WATCH"] = src
            chk("the out_root drop fired", odrop["at"], None)
            chk("05c exits zero", rc, 0)
            chk("...after waiting for the drive", "@@drive back" in text, True)
            got = snap()
            chk("...writes the same files", sorted(got), sorted(base))
            chk("...byte for byte as an undropped run",
                [k for k in base if got.get(k) != base[k]], [])
            chk("...no nucleus twice",
                dups(M.NUCLEI_CSV, "scene_uid", "roi_index", "nucleus_id"), 0)
            chk("...no section logged done twice",
                len(M.read_done_log(M.DONE_LOG, marker) or ()), len(secs))
            with open(M.DONE_LOG, newline="", encoding="utf-8") as fh:
                logged = [r[1] for r in csv.reader(fh) if r and r[1] != "*"]
            chk("...the done log has one line per section", sorted(logged), sorted(secs))
            chk("...no .tmp or .part left", tmp_left(), [])
        finally:
            DR.POLL = saved_poll
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


print()
print("--- the drive drops while 05c measures ---")
drive_drop_check()


def table_retry_check():
    """A Table append that fails half way is retried without a torn or doubled row."""
    tmp = tempfile.mkdtemp(prefix="ls5ct_")
    try:
        path = os.path.join(tmp, "t.csv")
        t = G5C.Table(path, "w", ["a", "b"])
        t.start()
        t.add([["1", "x"]])
        import builtins
        real_open = builtins.open
        failed = []

        class HalfThenFail:
            def __init__(self, fh):
                self._fh = fh

            def write(self, s):
                if not failed:
                    failed.append(s)
                    self._fh.write(s[:len(s) // 2])
                    self._fh.flush()
                    raise OSError(5, "Input/output error", path)
                return self._fh.write(s)

            def __getattr__(self, name):
                return getattr(self._fh, name)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self._fh.close()
                return False

        def flaky_open(p, mode="r", *a, **kw):
            fh = real_open(p, mode, *a, **kw)
            return HalfThenFail(fh) if mode == "a" else fh
        G5C.open = flaky_open
        try:
            t.add([["2", "yy"], ["3", "zzz"]])
        finally:
            del G5C.open
        with open(path, "rb") as fh:
            got = fh.read()
        chk("a half-written append failed once", len(failed), 1)
        chk("...and the retry left each row once, whole", got,
            b"a,b\r\n1,x\r\n2,yy\r\n3,zzz\r\n")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


print()
print("--- a 05c table append retried after failing half way ---")
table_retry_check()


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
