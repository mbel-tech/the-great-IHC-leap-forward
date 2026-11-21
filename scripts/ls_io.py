"""Shared file-writing helpers: tmp-then-replace, with a retry.

Every keyed output in the pipeline - the reformat index, the analysis set, the
override files, the ROI geometry, the workbooks - is read by a later stage as
the truth about a section. A half-written copy of any of them is worse than the
previous copy, so nothing here writes in place: the bytes go to `<path>.tmp`
beside the target and `os.replace` swaps them in as one step.

The retry came from 01k_saturation_raw.py. The D: volume does not merely drop
writes - it goes away: a dropout on 2026-09-01 killed 01k and 01g within nine
minutes of each other, both with `FileNotFoundError` on a path that exists,
which is what a vanished volume looks like from inside `open()`. Five attempts
over ~31 s; if the drive is still gone after that it is not a blip, and the
exception propagates rather than being swallowed.

Imported by path, like every other cross-script import here:

    _lsio = importlib.util.spec_from_file_location(
        "_lsio", os.path.join(os.path.dirname(os.path.abspath(__file__)), "ls_io.py"))
    IO = importlib.util.module_from_spec(_lsio)
    _lsio.loader.exec_module(IO)
"""

import contextlib
import csv
import datetime
import html
import importlib.util
import json
import os
import re
import sys
import time


_DRIVE = None


def drive_module():
    """The ONE ls_drive module (None if it cannot be loaded).

    Reuses `sys.modules["ls_drive"]` when a stage already imported it; else
    loads scripts/ls_drive.py by path and registers it under that name BEFORE
    executing it, so a later `import ls_drive` (or any by-path load that calls
    this) gets the same object - one `Stopped` class, one `PAUSES` list. Stages
    should get ls_drive through here (or a plain `import ls_drive`).

    Lazy, so importing ls_io costs nothing and a checkout or study without
    ls_drive behaves exactly as before.
    """
    global _DRIVE
    mod = sys.modules.get("ls_drive")
    if mod is not None:
        _DRIVE = mod
        return mod
    if _DRIVE is None:
        try:
            spec = importlib.util.spec_from_file_location(
                "ls_drive", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "ls_drive.py"))
            mod = importlib.util.module_from_spec(spec)
            sys.modules["ls_drive"] = mod
            try:
                spec.loader.exec_module(mod)
            except BaseException:
                sys.modules.pop("ls_drive", None)
                raise
            _DRIVE = mod
        except Exception:
            _DRIVE = False
    return _DRIVE or None


def retry(fn, path, attempts, what="write to"):
    """Run `fn`, retrying an OSError on a drive that goes away mid-operation.

    `what` names the operation in the message. It was hardcoded to "write
    to", which was true of the two writers in this file and a lie the moment
    `ls_atlas` used this to READ plates.csv: an operator watching a retry
    storm during a read would have been told their data was being written to
    a failing drive, which is the one sentence guaranteed to make them pull
    it out.

    Public because a sibling module needs it. It was `_retry`, and `ls_atlas`
    reached past the underscore to get at it - a private name with an outside
    caller is not private, it is only undocumented.

    If the OSError comes while a watched folder (see ls_drive) is unreachable,
    the drive is gone, not blipping: wait for it with no limit and try again
    WITHOUT using up an attempt. `Stopped` and Ctrl+C propagate. A failure
    while everything is reachable is backed off and counted as before.
    """
    attempt = 0
    while attempt < attempts:
        try:
            return fn()
        except OSError as exc:
            drv = drive_module()
            if drv is not None and not drv.reachable()[0]:
                drv.wait_for_drive(f"{what} {path}")
                continue
            if attempt == attempts - 1:
                raise
            wait = 2 ** attempt
            print("\n  !! %s %s failed (%s); retrying in %ds"
                  % (what, path, exc, wait))
            time.sleep(wait)
            attempt += 1


# The old private name, kept so an unseen caller does not break.
_retry = retry


def atomic_write_csv(path, rows, keys, attempts=5):
    """Write `rows` (dicts) under `keys` to `path`: header always, atomically.

    `keys` is required rather than taken from `rows[0]`. An empty result must
    still produce a file with a header, so a downstream reader sees "nothing
    here" instead of last week's rows - or a crash on `rows[0]`.
    """
    tmp = path + ".tmp"

    def go():
        try:
            with open(tmp, "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
                w.writeheader()
                w.writerows(rows)
            os.replace(tmp, path)
        except BaseException:
            # The old file is intact either way; a half-written temp beside it
            # is litter that looks like an output, so it goes too. A retry
            # writes it afresh.
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    retry(go, path, attempts)


def pick_column(fieldnames, *candidates, what=None):
    """The first candidate present in a CSV header, or a refusal naming all of
    them.

    Never defaults. `r[uid_col]` raising KeyError 400 rows into a loop is worse
    than refusing at the header, and `.get()` returning "" for a moved column is
    worse than both: a blank cell and a renamed column look identical.

    That last shape is not hypothetical here. `04n_roi_worklist.py` read its
    partner columns through a `num(r, k)` helper built on `.get()`, so the day
    04m renamed `pcna_focus_score` the worklist would have kept its column,
    kept its 454 rows and written an empty string into every one of them - a
    file that is the right size, the right shape and silently carries no
    numbers. Asked once against `fieldnames`, before the loop, a rename is a
    stop with both names in the message instead.

    `what` names the file for the message, since a header on its own does not
    say which of five CSVs a stage was reading.

    Raises SystemExit, the refusal every stage in this pipeline uses, so an
    operator gets the message rather than a traceback.
    """
    have = list(fieldnames or [])
    for candidate in candidates:
        if candidate in have:
            return candidate
    where = f" in {what}" if what else ""
    raise SystemExit(
        f"none of the columns {list(candidates)} is{where} - the header is "
        f"{have}. One of them was renamed, or this is not the file it should "
        f"be; either way the value cannot be read and a blank is not an answer.")


@contextlib.contextmanager
def atomic_save(path, attempts=5):
    """`with atomic_save(path) as tmp:` - write to `tmp`; `path` is swapped in
    when the block ends without an exception.

    For writers that own their file format (openpyxl, PIL): the tmp path has
    no useful extension, so pass the format explicitly - `im.save(tmp,
    format="PNG")`, and openpyxl's `wb.save(tmp)` does not care.
    """
    tmp = path + ".tmp"
    try:
        yield tmp
        retry(lambda: os.replace(tmp, path), path, attempts)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def save_image(im, path, format=None, **kw):
    """Save a PIL image (or an ndarray) to `path` atomically.

    `format` is inferred from the extension when None, since the temp file's
    own name has none to infer from. Extra keywords go to `Image.save`.
    """
    from PIL import Image
    if not isinstance(im, Image.Image):
        im = Image.fromarray(im)
    if format is None:
        ext = os.path.splitext(path)[1].lower()
        Image.init()
        format = Image.registered_extensions().get(ext)
        if format is None:
            raise ValueError(f"cannot infer an image format from {path!r}")
    tmp = path + ".tmp"

    def go():
        try:
            im.save(tmp, format=format, **kw)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    retry(go, path, 5)


def embed(obj):
    """JSON for a <script> block.

    `json.dumps` leaves `</script>` and `<!--` alone, and either one inside a
    string value ends the block or opens an HTML comment. Both are escaped in
    a way a JS string literal ignores: `<\\/` reads as `</`, `<\\!--` as `<!--`.
    """
    return json.dumps(obj).replace("</", "<\\/").replace("<!--", "<\\!--")


def fill(template, values, text=None):
    """Substitute every `__NAME__` placeholder in ONE pass.

    Chained `str.replace` calls re-scan the JSON just inserted, so a placeholder
    name inside a data value would be substituted as well. A placeholder the
    template does not carry is a programming error and raises.

    TWO KINDS OF DESTINATION, because a page is not all script.

    `values` land in a script block and are JSON-encoded by `embed`, which is
    what every caller wanted until a page needed to NAME something. `text`
    lands in HTML TEXT - a button's label, a heading - where `embed` would put
    the JSON string's own quote marks on screen: `>"DAPI"<`. Those are
    HTML-escaped instead, so a counterstain called `A&B` reaches the page as
    itself rather than as markup.

    Both go through the SAME pass, and that is the point of the parameter
    rather than a second call: a str.replace before or after this one would
    re-scan whatever the other had already inserted, which is the exact bug the
    one-pass rule exists to prevent. A name given in both is refused - two
    encodings for one placeholder is a caller that has not decided.

    Nothing to substitute returns the template unchanged. An empty pattern
    matches at every position, so the `re` route would rewrite the whole page.
    """
    text = dict(text or {})
    both = dict(values or {})
    overlap = sorted(set(both) & set(text))
    if overlap:
        raise KeyError(f"placeholder(s) {overlap} are given as both script data "
                       f"and page text; one placeholder, one encoding")
    both.update(text)
    if not both:
        return template
    for name in both:
        if name not in template:
            raise KeyError(f"placeholder {name} is not in the template")
    pattern = re.compile("|".join(re.escape(k) for k in both))

    def one(match):
        name = match.group(0)
        if name in text:
            return html.escape(str(text[name]))
        return embed(both[name])

    return pattern.sub(one, template)


def backup_path(path, now=None):
    """`<path>.<YYYYMMDD-HHMMSS>.backup`, or `...-2`, `-3`... if that is taken.

    Never an existing name: a backup is the only copy of what a write replaced,
    and the old single `.backup` was overwritten by the very next import. One
    rule for every curation file a stage backs up before rewriting it (04q's
    seed, 04i's overrides file).
    """
    stamp = (now or datetime.datetime.now()).strftime("%Y%m%d-%H%M%S")
    cand, n = "%s.%s.backup" % (path, stamp), 1
    while os.path.exists(cand):
        n += 1
        cand = "%s.%s-%d.backup" % (path, stamp, n)
    return cand
