"""00_manifest refuses a study it would otherwise hand downstream wrong.

Two things every later stage takes on trust from stage 0:

  * the configured `pixel_size_um` is the scale of every file. Nothing after
    00 reads the scale from the CZI, so a slide scanned with another objective
    would be measured as if it were not.
  * `scene_uid` names one section of one file. It is the key of every table,
    and a flat filename in several output folders (01k's `<uid>_clipped.png`),
    so two files giving one uid overwrite each other without any error.

The CZI reader is replaced by fakes, so this runs on header values alone.

Run:  python tests/test_manifest_checks.py
"""

import contextlib
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from _fixture import use_temp_study, load_stage  # noqa: E402

#: The live LS grammar, as pinned in tests/test_slide_naming.py: the marker
#: may ride in the file suffix, which the scene uid leaves out.
LS = {
    "pattern": r"^(?P<subject>LS\d+)_(?P<slide>\d+)(?P<replicate>[a-z])"
               r"(?P<rescan>-?)(?P<pass>_[A-Za-z0-9]+)?\.czi$",
    "case_insensitive": False,
}
STUDY = use_temp_study(slide_naming=LS, pixel_size_um=0.65)
M = load_stage("00_manifest.py")
REAL_DISCOVER = M.discover_sources

fails = 0


def chk(label, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
    print(("ok   " if ok else "FAIL ") + label.ljust(60) + f" {got!r}"
          + ("" if ok else f"   want {want!r}"))


def fake_info(px_um, marker):
    """What czi_meta.summarise returns, for a two-scene, two-channel file."""
    return {
        "size_x": 100, "size_y": 100, "size_c": 2, "size_s": 2, "size_m": 1,
        "pixel_type": "Gray16", "component_bit_count": 16, "clip_ceiling": 65535,
        "acquired": "2025-12-24T09:13:45",
        "px_um_x": px_um, "px_um_y": px_um,
        "objective_mag": "10", "objective_na": "0.45", "camera": "cam",
        "shading_reference_mode": None, "online_stitching": "false",
        "channels": [
            {"name": "DAPI", "exposure_ms": 5.0, "pixel_type": "Gray16",
             "component_bit_count": 16},
            {"name": marker, "exposure_ms": 300.0, "pixel_type": "Gray16",
             "component_bit_count": 16},
        ],
        "scenes": [
            {"index": i, "name": f"ScanRegion{i}", "center_x_um": 9000.0 * i,
             "center_y_um": 0.0, "width_um": 7000.0, "height_um": 7000.0}
            for i in range(2)
        ],
    }


def run(files, info=None):
    """Run 00's main() over {filename: (px_um, marker)}. (exit code, output)."""
    info = info or fake_info
    M.discover_sources = lambda stack, skipped=None: [
        ("LOOSE", name, os.path.join(STUDY.source_dir, name),
         (lambda n: (lambda: contextlib.nullcontext(n)))(name))
        for name in files]
    M.read_metadata = lambda name: name
    M.summarise = lambda name: info(*files[name])
    manifest = os.path.join(M.MANIFEST_DIR, "manifest_scenes.csv")
    if os.path.exists(manifest):
        os.remove(manifest)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = M.main()
    return code or 0, out.getvalue(), os.path.exists(manifest)


print("--- a clean paired study passes ---")
# The live LS naming: the two marker passes of one slide differ by replicate
# letter, and LS53_2b-_A568 sits beside LS53_2c (AF488), so no uid repeats.
code, out, wrote = run({
    "LS53_2b-_A568.czi": (0.65, "AF568"),
    "LS53_2c.czi": (0.65, "AF488"),
    "LS45_8a.czi": (0.65, "AF568"),
    "LS45_8b.czi": (0.65, "AF488"),
})
chk("exit code is 0", code, 0)
chk("the manifest is written", wrote, True)

print("--- pixel size disagrees with the config ---")
code, out, wrote = run({
    "LS45_8a.czi": (0.65, "AF568"),
    "LS45_8b.czi": (0.325, "AF488"),
})
chk("exit code is non-zero", code != 0, True)
chk("the message names the file", "LS45_8b.czi" in out, True)
chk("...and its px_um", "0.325" in out, True)
chk("no manifest is written for downstream to read", wrote, False)

code, out, wrote = run({"LS45_8a.czi": (0.6515, "AF568")})
chk("0.23% off is header rounding, accepted", code, 0)
code, out, wrote = run({"LS45_8a.czi": (0.6536, "AF568")})
chk("0.55% off is refused", code != 0, True)
code, out, wrote = run({"LS45_8a.czi": (None, "AF568")})
chk("a file with no scale cannot be checked, refused", code != 0, True)

print("--- two files give the same scene uid ---")
code, out, wrote = run({
    "LS45_8b.czi": (0.65, "AF488"),
    "LS45_8b_A488.czi": (0.65, "AF488"),
})
chk("exit code is non-zero", code != 0, True)
chk("the message names the uid", "LS45_s08b_sc00" in out, True)
chk("...and both files", ("LS45_8b.czi" in out, "LS45_8b_A488.czi" in out), (True, True))
chk("no manifest is written for downstream to read", wrote, False)

# Across marker passes too: 01k's clip masks are one flat folder keyed by uid
# alone, so an AF568 and an AF488 file giving one uid still collide.
code, out, wrote = run({
    "LS53_2b-_A568.czi": (0.65, "AF568"),
    "LS53_2b-_A488.czi": (0.65, "AF488"),
})
chk("a repeat across the two marker passes is refused too", code != 0, True)

# ------------------------------------------------ a failed ingest says so
# Each of these used to exit 0. The old manifest_files.csv was rewritten from
# the files that did read while manifest_scenes.csv kept the previous run's
# rows, so the two tables described different runs.
import csv  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402
import zipfile  # noqa: E402

FILES_CSV = os.path.join(M.MANIFEST_DIR, "manifest_files.csv")
SCENES_CSV = os.path.join(M.MANIFEST_DIR, "manifest_scenes.csv")
REVIEW_CSV = os.path.join(M.QC_DIR, "slide_layout_review.csv")


def seed_stale():
    """Last run's two tables, so a run can be seen to leave them both alone."""
    os.makedirs(M.MANIFEST_DIR, exist_ok=True)
    for p in (FILES_CSV, SCENES_CSV):
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("stale\n")


def stale_both():
    out = []
    for p in (FILES_CSV, SCENES_CSV):
        with open(p, encoding="utf-8") as fh:
            out.append(fh.read() == "stale\n")
    return out


def last_line(out):
    lines = [ln for ln in out.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def run_main():
    out = io.StringIO()
    code = None
    with contextlib.redirect_stdout(out):
        try:
            code = M.main() or 0
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
            print(exc.code)
    return code, out.getvalue()


def run_stale(files, info=None):
    """run() without deleting anything first: both tables seeded stale."""
    info = info or fake_info
    M.discover_sources = lambda stack, skipped=None: [
        ("LOOSE", name, os.path.join(STUDY.source_dir, name),
         (lambda n: (lambda: contextlib.nullcontext(n)))(name))
        for name in files]
    M.read_metadata = lambda name: name
    M.summarise = lambda name: info(*files[name])
    seed_stale()
    return run_main()


print("--- a file whose name does not fit the grammar ---")
MAP_8A = os.path.join(M.SLIDEMAP_DIR, "LS45_8a.png")
if os.path.exists(MAP_8A):
    os.remove(MAP_8A)
code, out = run_stale({"LS45_8a.czi": (0.65, "AF568"), "not_a_slide.czi": (0.65, "AF568")})
chk("exit code is non-zero", code != 0, True)
chk("neither manifest table is replaced", stale_both(), [True, True])
chk("the failure is counted", "failures         : 1" in out, True)
chk("the reason is the last line", "could not be read" in last_line(out), True)
# app/runner.py shows only the last line, so it must name the file and say
# what to do about THIS cause.
chk("...naming the file", "not_a_slide.czi" in last_line(out), True)
chk("...with the remedy for a name", "rename it" in last_line(out), True)
chk("a refused run draws no slide map for the manifest it did not write",
    os.path.exists(MAP_8A), False)


def no_scenes(px, marker):
    d = fake_info(px, marker)
    if marker == "AF488":
        d["scenes"] = []
        d["size_s"] = 0
    return d


print("--- a file with no Scenes block ---")
code, out = run_stale({"LS45_8a.czi": (0.65, "AF568"), "LS45_8b.czi": (0.65, "AF488")},
                      no_scenes)
chk("exit code is non-zero", code != 0, True)
chk("neither manifest table is replaced", stale_both(), [True, True])
chk("the file is named", "LS45_8b.czi" in out and "no scenes" in out, True)
chk("...in the last line, with a remedy that is not renaming",
    "LS45_8b.czi" in last_line(out) and "re-copy or re-export" in last_line(out)
    and "rename" not in last_line(out), True)


def short_scenes(px, marker):
    d = fake_info(px, marker)
    d["size_s"] = 3          # the header says three; the Scenes block places two
    return d


print("--- the Scenes block disagrees with SizeS ---")
code, out = run_stale({"LS45_8a.czi": (0.65, "AF568")}, short_scenes)
chk("exit code is non-zero (it was only printed)", code != 0, True)
chk("neither manifest table is replaced", stale_both(), [True, True])
chk("the message says SizeS", "SizeS is 3" in out, True)


def single_scene_no_sizes(px, marker):
    d = fake_info(px, marker)
    d["scenes"] = d["scenes"][:1]
    d["size_s"] = 0          # ZEN leaves SizeS out of a single-scene header
    return d


print("--- a single-scene file whose header states no SizeS ---")
code, out = run_stale({"LS45_8a.czi": (0.65, "AF568")}, single_scene_no_sizes)
chk("is read, not refused", code, 0)
chk("...and the manifest is written", stale_both(), [False, False])

print("--- no CZI at all ---")
code, out = run_stale({})
chk("exit code is non-zero", code != 0, True)
chk("the reason is the last line", "found no CZI" in last_line(out), True)
chk("...and last run's tables are left alone", stale_both(), [True, True])

print("--- an empty table is written header-only ---")
os.makedirs(M.QC_DIR, exist_ok=True)
with open(REVIEW_CSV, "w", encoding="utf-8") as fh:
    fh.write("file,n_rows\nLS99_1a.czi,4\n")
code, out = run_stale({"LS45_8a.czi": (0.65, "AF568")})
chk("a clean run exits 0", code, 0)
with open(REVIEW_CSV, newline="", encoding="utf-8") as fh:
    review = list(csv.reader(fh))
chk("last run's slide_layout_review.csv is replaced", len(review), 1)
chk("...by a header", review[0][:2], ["file", "animal"])

print("--- the marker check after the writes runs ---")
saved = M.CONFIG.get("acquisition")
M.CONFIG["acquisition"] = {"layout": "paired", "markers": ["pERK"], "channels": []}
try:
    code, out = run_stale({"LS45_8a.czi": (0.65, "AF568")})
finally:
    M.CONFIG["acquisition"] = saved
chk("a paired study whose markers name no scan stops", code != 0, True)
chk("...saying why", "acquisition.markers" in last_line(out), True)
chk("...after writing the manifest for the operator to read", stale_both(), [False, False])

print("--- discovery: archives and source_files ---")
src = tempfile.mkdtemp(prefix="manifest_src_")
saved_dir, saved_only = M.SOURCE_DIR, M.CONFIG.get("source_files")
try:
    with zipfile.ZipFile(os.path.join(src, "batch.zip"), "w") as z:
        z.writestr("LS45_8a.czi", b"a")
        z.writestr("LS45_8b.czi", b"b")
    with open(os.path.join(src, "broken.zip"), "wb") as fh:
        fh.write(b"not a zip")
    M.SOURCE_DIR = src

    M.CONFIG["source_files"] = None
    skipped = []
    with contextlib.ExitStack() as stack:
        entries = REAL_DISCOVER(stack, skipped)
    chk("an unreadable archive is counted", [s[0] for s in skipped], ["broken.zip"])
    chk("...and the readable one still read", sorted(e[1] for e in entries),
        ["LS45_8a.czi", "LS45_8b.czi"])

    M.discover_sources = REAL_DISCOVER
    M.read_metadata = lambda fh: fh.read().decode()
    M.summarise = lambda name: fake_info(0.65, "AF568" if name == "a" else "AF488")
    seed_stale()
    code, out = run_main()
    chk("main() fails on an unreadable archive", code != 0, True)
    chk("...and writes neither table", stale_both(), [True, True])
    chk("...naming it", "broken.zip" in out, True)

    # A CZI inside a zip, named on its own: it used to be compared with the
    # zip's name, so the whole archive was skipped without a count.
    M.CONFIG["source_files"] = ["LS45_8b.czi"]
    skipped = []
    with contextlib.ExitStack() as stack:
        entries = REAL_DISCOVER(stack, skipped)
    chk("source_files can name a CZI inside a zip", [e[1] for e in entries], ["LS45_8b.czi"])
    chk("...and an unnamed broken archive is not this run's failure", skipped, [])

    M.CONFIG["source_files"] = ["batch.zip"]
    with contextlib.ExitStack() as stack:
        entries = REAL_DISCOVER(stack, [])
    chk("naming the zip still takes all of it", len(entries), 2)

    M.CONFIG["source_files"] = ["LS45_8c.czi"]
    skipped = []
    with contextlib.ExitStack() as stack:
        entries = REAL_DISCOVER(stack, skipped)
    chk("a named file found nowhere is counted", [s[1] for s in skipped], ["LS45_8c.czi"])
    chk("...mentioning the archive that could not be searched",
        "broken.zip" in (skipped[0][2] if skipped else ""), True)
finally:
    M.SOURCE_DIR = saved_dir
    M.CONFIG["source_files"] = saved_only
    shutil.rmtree(src, ignore_errors=True)

print()
print("--- the drive drops while 00 reads: waited for, redone, same manifest ---")
# The slides are on a USB drive that goes away rather than failing a read. An
# archive or header read that fails while the slides folder is unreachable is
# not the file's fault: 00 waits for the folder, opens the archive afresh,
# reads the header again, and writes exactly what an uninterrupted run
# writes. A read that fails with the folder there is recorded, as before.
#
# The drop is real as far as 00 can tell: the folder is renamed away for
# 0.3 s. zipfile is a stand-in whose archives, once a wait has ended since
# they were opened, refuse to open a member - as a vanished volume's do.
import threading  # noqa: E402
import time  # noqa: E402

DR = M.IO.drive_module()
saved_env = {k: os.environ.get(k) for k in ("LS_WATCH", "LS_PROGRESS")}
saved_poll = DR.POLL
DR.POLL = 0.05
os.environ["LS_PROGRESS"] = "1"
src = tempfile.mkdtemp(prefix="manifest_drop_")
os.environ["LS_WATCH"] = src
saved_dir, saved_only = M.SOURCE_DIR, M.CONFIG.get("source_files")
saved_zip = M.zipfile
DROP = {"at": None, "t": None}
BREAK = {"at": None}


def vanish_for(seconds):
    gone = src + ".gone"
    os.rename(src, gone)

    def back():
        time.sleep(seconds)
        os.rename(gone, src)
    t = threading.Thread(target=back, daemon=True)
    t.start()
    return t


def maybe_drop(key, what):
    if DROP["at"] == key:
        DROP["at"] = None
        DROP["t"] = vanish_for(0.3)
        raise OSError(5, "Input/output error", what)


class StandInZip(zipfile.ZipFile):
    def __init__(self, path, *a, **kw):
        maybe_drop(("archive", os.path.basename(path)), path)
        super().__init__(path, *a, **kw)
        self.gen = len(DR.PAUSES)

    def open(self, name, *a, **kw):
        if len(DR.PAUSES) > self.gen:
            raise OSError(116, "Stale file handle", self.filename)
        return super().open(name, *a, **kw)


class StandInZipModule:
    ZipFile = StandInZip
    BadZipFile = zipfile.BadZipFile


def header(fh):
    name = os.path.basename(getattr(fh, "name", ""))
    if BREAK["at"] == name:
        raise RuntimeError("corrupt header")
    maybe_drop(("header", name), name)
    return fh.read().decode()


def manifest_snapshot():
    got = {}
    for root in (M.MANIFEST_DIR, M.QC_DIR):
        for dirpath, _, names in os.walk(root):
            for n in names:
                p = os.path.join(dirpath, n)
                with open(p, "rb") as fh:
                    got[os.path.relpath(p, STUDY.out_root)] = fh.read()
    return got


def clear_manifest():
    for root in (M.MANIFEST_DIR, M.QC_DIR):
        shutil.rmtree(root, ignore_errors=True)


def drop_run():
    code, out = run_main()
    if DROP["t"] is not None:
        DROP["t"].join(5)
        DROP["t"] = None
    return code, out


try:
    with zipfile.ZipFile(os.path.join(src, "batch.zip"), "w") as z:
        z.writestr("LS45_8a.czi", b"a")
        z.writestr("LS45_8b.czi", b"b")
    with zipfile.ZipFile(os.path.join(src, "later.zip"), "w") as z:
        z.writestr("LS46_3a.czi", b"c")
    with open(os.path.join(src, "LS47_1a.czi"), "wb") as fh:
        fh.write(b"d")
    M.SOURCE_DIR = src
    M.CONFIG["source_files"] = None
    M.discover_sources = REAL_DISCOVER
    M.zipfile = StandInZipModule
    M.read_metadata = header
    M.summarise = lambda name: fake_info(0.65, "AF568" if name in "ac" else "AF488")

    clear_manifest()
    code, out = drop_run()
    chk("undisturbed: exit 0", code, 0)
    base00 = manifest_snapshot()
    with open(FILES_CSV, newline="", encoding="utf-8") as fh:
        chk("...every file in the manifest",
            sorted(r["file"] for r in csv.DictReader(fh)),
            ["LS45_8a.czi", "LS45_8b.czi", "LS46_3a.czi", "LS47_1a.czi"])

    for what, key in (("a zip member's header", ("header", "LS45_8b.czi")),
                      ("a loose CZI's header", ("header", "LS47_1a.czi")),
                      ("opening an archive", ("archive", "later.zip"))):
        clear_manifest()
        DROP["at"] = key
        code, out = drop_run()
        chk(f"drop on {what}: it fired", DROP["at"], None)
        chk("...exit 0", code, 0)
        chk("...the drive came back", "@@drive back" in out, True)
        got = manifest_snapshot()
        chk("...the same files", sorted(got), sorted(base00))
        chk("...byte for byte", [k for k in base00 if got.get(k) != base00[k]], [])
        uids = []
        if os.path.exists(SCENES_CSV):
            with open(SCENES_CSV, newline="", encoding="utf-8") as fh:
                uids = [r["scene_uid"] for r in csv.DictReader(fh)]
        chk("...no duplicate rows", len(uids), len(set(uids)))

    clear_manifest()
    t = vanish_for(0.3)
    code, out = drop_run()
    t.join(5)
    chk("the folder gone as 00 starts: exit 0", code, 0)
    chk("...after waiting for the drive, not finding nothing", "@@drive back" in out, True)
    got = manifest_snapshot()
    chk("...byte for byte", [k for k in base00 if got.get(k) != base00[k]], [])

    clear_manifest()
    BREAK["at"] = "LS45_8b.czi"
    seed_stale()
    code, out = drop_run()
    BREAK["at"] = None
    chk("a header that fails with the folder there: the run fails", code != 0, True)
    chk("...naming it, with the error", "LS45_8b.czi" in out and "corrupt header" in out, True)
    chk("...without waiting for a drive that never left", "@@drive" in out, False)
    chk("...and writes neither table", stale_both(), [True, True])
finally:
    M.SOURCE_DIR = saved_dir
    M.CONFIG["source_files"] = saved_only
    M.zipfile = saved_zip
    DR.POLL = saved_poll
    for k, v in saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    shutil.rmtree(src, ignore_errors=True)
    shutil.rmtree(src + ".gone", ignore_errors=True)

print()
print("ALL OK" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
