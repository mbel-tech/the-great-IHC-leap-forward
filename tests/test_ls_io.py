"""ls_io: tmp-then-replace writes, with a retry.

Run:  python tests/test_ls_io.py
"""

import csv
import importlib.util
import os
import shutil
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")

_spec = importlib.util.spec_from_file_location("lsio", os.path.join(SCRIPTS, "ls_io.py"))
IO = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(IO)

fails = 0


def chk(label, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
    print(("ok   " if ok else "FAIL ") + label.ljust(54) + f" {got!r}"
          + ("" if ok else f"   want {want!r}"))


def read(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


tmp = tempfile.mkdtemp(prefix="lsio_")
real_replace, real_sleep = IO.os.replace, IO.time.sleep
try:
    p = os.path.join(tmp, "out.csv")
    IO.atomic_write_csv(p, [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}], ["a", "b"])
    chk("rows round-trip", read(p), [{"a": "1", "b": "x"}, {"a": "2", "b": "y"}])
    chk("no .tmp left behind", os.path.exists(p + ".tmp"), False)

    IO.atomic_write_csv(p, [], ["a", "b"])
    # newline="" so the CRLF the csv writer emits is not translated away on read.
    with open(p, newline="", encoding="utf-8") as fh:
        chk("empty rows -> header only", fh.read(), "a,b\r\n")

    IO.atomic_write_csv(p, [{"a": 1, "b": 2, "extra": 3}], ["a", "b"])
    chk("extra keys are ignored", read(p), [{"a": "1", "b": "2"}])

    # A dropout: os.replace fails twice, then works. The old file must be intact
    # in between, and the retry must not sleep for real.
    IO.atomic_write_csv(p, [{"a": "old", "b": "old"}], ["a", "b"])
    calls, slept = {"n": 0}, []

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise OSError("volume vanished")
        return real_replace(src, dst)

    IO.os.replace, IO.time.sleep = flaky, slept.append
    try:
        IO.atomic_write_csv(p, [{"a": "new", "b": "new"}], ["a", "b"])
    finally:
        IO.os.replace, IO.time.sleep = real_replace, real_sleep
    chk("retried until the replace went through", calls["n"], 3)
    chk("backoff doubled", slept, [1, 2])
    chk("the new content landed", read(p)[0]["a"], "new")

    # Exhausted attempts propagate, and the previous file is still there.
    IO.atomic_write_csv(p, [{"a": "keep", "b": "keep"}], ["a", "b"])

    def dead(src, dst):
        raise OSError("gone")

    IO.os.replace, IO.time.sleep = dead, lambda s: None
    raised = ""
    try:
        IO.atomic_write_csv(p, [{"a": "lost", "b": "lost"}], ["a", "b"], attempts=2)
    except OSError as exc:
        raised = str(exc)
    finally:
        IO.os.replace, IO.time.sleep = real_replace, real_sleep
    chk("exhausted attempts raise", raised, "gone")
    chk("...and the old file is untouched", read(p)[0]["a"], "keep")
    chk("...and no .tmp is left beside it", os.path.exists(p + ".tmp"), False)

    # atomic_save: a context that hands out the temp path.
    q = os.path.join(tmp, "blob.bin")
    with IO.atomic_save(q) as t:
        chk("temp path is beside the target", os.path.dirname(t), tmp)
        with open(t, "wb") as fh:
            fh.write(b"abc")
        chk("target absent until the block ends", os.path.exists(q), False)
    with open(q, "rb") as fh:
        chk("target written on exit", fh.read(), b"abc")
    chk("temp gone on exit", os.path.exists(t), False)

    boom = ""
    try:
        with IO.atomic_save(q) as t:
            with open(t, "wb") as fh:
                fh.write(b"partial")
            raise ValueError("writer died")
    except ValueError as exc:
        boom = str(exc)
    chk("an exception inside the block propagates", boom, "writer died")
    with open(q, "rb") as fh:
        chk("...and leaves the previous file alone", fh.read(), b"abc")
    chk("...and cleans the temp up", os.path.exists(t), False)
    # ---- the drive: wait it out, do not burn an attempt ---------------------
    import threading
    import numpy as np
    from PIL import Image

    drv = IO.drive_module()
    old_poll, old_watch = drv.POLL, os.environ.get("LS_WATCH")
    drv.POLL = 0.05
    try:
        vol = os.path.join(tmp, "vol")
        gone = os.path.join(tmp, "vol_away")
        os.mkdir(vol)
        os.environ["LS_WATCH"] = vol
        os.rename(vol, gone)
        threading.Timer(0.5, lambda: os.rename(gone, vol)).start()
        vp = os.path.join(vol, "d.csv")
        IO.atomic_write_csv(vp, [{"a": 1, "b": 2}], ["a", "b"], attempts=1)
        chk("csv written after the drive came back (attempts=1)", read(vp),
            [{"a": "1", "b": "2"}])

        # Reachable folder: a failure is an ordinary failure, backoff applies.
        n, slept2 = {"n": 0}, []

        def fail_twice():
            n["n"] += 1
            if n["n"] <= 2:
                raise OSError("blip")
            return "done"

        IO.time.sleep = slept2.append
        try:
            got = IO.retry(fail_twice, "x", 3)
        finally:
            IO.time.sleep = real_sleep
        chk("reachable failure keeps backoff", (got, slept2), ("done", [1, 2]))

        def always():
            raise OSError("bad")

        IO.time.sleep = lambda s: None
        try:
            IO.retry(always, "x", 2)
            got = "returned"
        except OSError:
            got = "raised"
        finally:
            IO.time.sleep = real_sleep
        chk("reachable failure still exhausts attempts", got, "raised")

        # Stopped propagates out of the wait.
        stop = os.path.join(tmp, "stop")
        open(stop, "w").close()
        os.environ["LS_STOP_FILE"] = stop
        os.environ["LS_WATCH"] = os.path.join(tmp, "nowhere")
        try:
            IO.retry(always, "x", 3)
            got = "returned"
        except drv.Stopped:
            got = "Stopped"
        finally:
            del os.environ["LS_STOP_FILE"]
        chk("Stopped propagates", got, "Stopped")
        import sys
        sys.path.insert(0, SCRIPTS)
        import ls_drive
        chk("one shared ls_drive instance", ls_drive is drv, True)
        os.environ["LS_STOP_FILE"] = stop
        try:
            IO.retry(always, "x", 3)
            got = "returned"
        except ls_drive.Stopped:
            got = "Stopped"
        finally:
            del os.environ["LS_STOP_FILE"]
        chk("Stopped is the stage's ls_drive.Stopped", got, "Stopped")
        os.remove(stop)
    finally:
        drv.POLL = old_poll
        if old_watch is None:
            os.environ.pop("LS_WATCH", None)
        else:
            os.environ["LS_WATCH"] = old_watch

    # ---- save_image ----------------------------------------------------------
    ip = os.path.join(tmp, "im.png")
    IO.save_image(np.zeros((4, 4), np.uint8), ip)
    with Image.open(ip) as im:
        chk("save_image writes a valid PNG", (im.format, im.size), ("PNG", (4, 4)))
    chk("...and leaves no .tmp", os.path.exists(ip + ".tmp"), False)
    before = open(ip, "rb").read()

    class Boom(Image.Image):
        def save(self, *a, **k):
            open(a[0], "wb").write(b"partial")
            raise ValueError("encoder died")

    bad = Boom()
    got = "saved"
    try:
        IO.save_image(bad, ip)
    except ValueError:
        got = "raised"
    chk("a failing PIL save raises", got, "raised")
    chk("...old file untouched", open(ip, "rb").read(), before)
    chk("...no .tmp litter", os.path.exists(ip + ".tmp"), False)
    # A drop during the encode itself waits, then succeeds.
    saved_poll, saved_watch = drv.POLL, os.environ.get("LS_WATCH")
    drv.POLL = 0.05
    os.environ["LS_WATCH"] = vol
    os.rename(vol, gone)
    threading.Timer(0.5, lambda: os.rename(gone, vol)).start()
    real = Image.Image.save
    first = {"n": 0}

    def flaky_save(self, fp, *a, **k):
        first["n"] += 1
        if first["n"] == 1:
            raise OSError("volume vanished")
        return real(self, fp, *a, **k)

    Image.Image.save = flaky_save
    try:
        IO.save_image(np.zeros((4, 4), np.uint8), os.path.join(vol, "w.png"))
    finally:
        Image.Image.save = real
        drv.POLL = saved_poll
        if saved_watch is None:
            os.environ.pop("LS_WATCH", None)
        else:
            os.environ["LS_WATCH"] = saved_watch
    chk("save_image waits out a drop during encode",
        (first["n"], os.path.exists(os.path.join(vol, "w.png")),
         os.path.exists(os.path.join(vol, "w.png.tmp"))), (2, True, False))
    jp = os.path.join(tmp, "im.jpg")
    IO.save_image(Image.new("RGB", (4, 4)), jp, quality=80)
    with Image.open(jp) as im:
        chk("format inferred from extension", im.format, "JPEG")

    # ---- pick_column: a moved column is a stop, never a blank -------------
    #
    # The failure this exists to prevent has no symptom. `04n`'s num(r, k) read
    # its partner columns with `.get(k, "")`, so a renamed column and a
    # genuinely empty cell produce the same character in the same place - and
    # the worklist still has 454 rows, still has the column, and carries no
    # numbers in it at all.
    header = ["scene_uid", "partner_focus_score", "n_pieces"]
    chk("the current name is found", IO.pick_column(header, "partner_focus_score"),
        "partner_focus_score")
    chk("the first candidate present wins",
        IO.pick_column(header, "partner_focus_score", "pcna_focus_score"),
        "partner_focus_score")
    chk("...and a legacy header still reads",
        IO.pick_column(["pcna_focus_score"], "partner_focus_score",
                       "pcna_focus_score"), "pcna_focus_score")
    chk("candidate order decides, not header order",
        IO.pick_column(header, "n_pieces", "scene_uid"), "n_pieces")

    got = "accepted"
    try:
        IO.pick_column(header, "partner_n_pieces", "pcna_n_pieces")
    except SystemExit as exc:
        got = str(exc)
    chk("a header with neither name refuses",
        got.startswith("none of the columns ['partner_n_pieces', 'pcna_n_pieces']"),
        True)
    chk("...and the refusal shows the header it actually saw",
        "'partner_focus_score'" in got, True)

    got = "accepted"
    try:
        IO.pick_column(header, "gone", what="roi_worklist.csv")
    except SystemExit as exc:
        got = str(exc)
    chk("...and names the file when told which one",
        "in roi_worklist.csv" in got, True)

    # DictReader hands back None for a file with no header at all. That must
    # refuse rather than raise TypeError on `in None`.
    got = "accepted"
    try:
        IO.pick_column(None, "scene_uid")
    except SystemExit:
        got = "refused"
    chk("no header at all refuses", got, "refused")
finally:
    IO.os.replace, IO.time.sleep = real_replace, real_sleep
    shutil.rmtree(tmp, ignore_errors=True)

print("\n" + ("ALL PASS" if not fails else f"{fails} FAILED"))
raise SystemExit(1 if fails else 0)
