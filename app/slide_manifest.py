"""Set up > Slides > "Slide manifest…": what is in the slides folder, per animal.

One row per animal: how many slides and files it has, which files, its
treatment, and whether anything looks missing. The slides folder is checked
against the sampling workbook in both directions: animals with slides but no
workbook row, and workbook rows with no slides. An animal with fewer slides
(or files) than most animals in the study is flagged too, since that is
usually a slide that was not scanned or not copied.

The workbook join is ls_sampling.check, the one Check workbook and Unblind
use, so the two never disagree. A workbook that cannot be used still gives
the inventory, with the reason in the note.

The treatment is shown, and written to the CSV: the person chose to see the
key here. The CSV is not one of the files a study bundle carries.

Everything in this module is plain Python with no Qt, so it can be tested on
its own; the window is slide_manifest_dialog.
"""

import csv
import os
import re
from collections import Counter

import autodetect as AD
import ls_naming as NM
import ls_sampling as SAMPLING

CSV_NAME = "slide_manifest.csv"
COLUMNS = ("animal", "treatment", "slides", "files", "slide_files", "status")

OK = "OK"
NO_SLIDES = "In workbook, no slides"
NOT_IN_BOOK = "Slides, not in workbook"
TWO_ROWS = "On two or more workbook rows"
UNCHECKED = "Workbook not checked"


def _natural_key(text):
    """LS7 < LS22 < LS120 (the copy app/slides.py keeps, for the same reason)."""
    return tuple((1, int(part)) if part.isdigit() else (0, part.lower())
                 for part in re.split(r"(\d+)", str(text)) if part != "")


def _manifest_rows(out_root):
    path = os.path.join(out_root, "manifest", "manifest_files.csv") if out_root else ""
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except (OSError, ValueError, csv.Error):
        return None


def inventory(cfg):
    """The slide files, read with the study's naming rule.

    {"files": [{file, animal, slide, replicate}], "unrecognised": [names],
     "from": where they were read from, "error": why nothing was read or None}.
    The slides folder is read when it can be; else Prepare's manifest
    (manifest/manifest_files.csv), which names the files the folder had.
    """
    cfg = cfg or {}
    out = {"files": [], "unrecognised": [], "from": "slides folder", "error": None}
    source = cfg.get("source_dir") or ""
    names = AD.slide_files(source) if source and os.path.isdir(source) else []
    if names:
        try:
            pattern = NM.compile_pattern(cfg.get("slide_naming") or {})
        except (NM.NamingError, TypeError, ValueError, KeyError) as exc:
            out["error"] = "the slide naming rule cannot be used (%s)" % exc
            out["unrecognised"] = list(names)
            return out
        for name in names:
            parsed = NM.parse_name(name, pattern)
            if not parsed or not parsed.get("subject"):
                out["unrecognised"].append(name)
                continue
            out["files"].append({"file": name, "animal": parsed["subject"],
                                 "slide": parsed.get("slide"),
                                 "replicate": parsed.get("replicate") or ""})
        return out
    rows = _manifest_rows(cfg.get("out_root"))
    if rows:
        out["from"] = "Prepare's manifest (the slides folder could not be read)"
        for r in rows:
            if str(r.get("is_redundant_copy") or "0").strip() not in ("", "0", "false", "False"):
                continue            # the same file found twice (loose and in a zip)
            name = r.get("file") or ""
            if not r.get("animal"):
                out["unrecognised"].append(name)
                continue
            slide = (r.get("slide") or "").strip()
            out["files"].append({"file": name, "animal": r["animal"],
                                 "slide": int(slide) if slide.isdigit() else None,
                                 "replicate": r.get("variant") or ""})
        return out
    out["error"] = ("no slide files were found in the slides folder" if source
                    else "no slides folder is chosen")
    return out


def per_animal(files):
    """{animal: {"files": [names], "slides": n}}; a slide is one slide number.

    10a and 10b are one slide in two files. A file whose name carries no slide
    number counts as a slide of its own.
    """
    out = {}
    for f in files:
        a = out.setdefault(f["animal"], {"files": [], "keys": set()})
        a["files"].append(f["file"])
        a["keys"].add(("n", f["slide"]) if f.get("slide") is not None else ("f", f["file"]))
    return {k: {"files": sorted(v["files"], key=_natural_key), "slides": len(v["keys"])}
            for k, v in out.items()}


def most_common(counts):
    """The count most animals have (ties go to the higher); None with no counts."""
    counts = [c for c in counts if c > 0]
    if not counts:
        return None
    tally = Counter(counts)
    return max(tally, key=lambda c: (tally[c], c))


def counts_for_check(animals):
    """{animal: file count}, what ls_sampling.check takes."""
    return {a: len(v["files"]) for a, v in animals.items()}


def build(inv, rep=None):
    """The manifest: {"rows", "summary", "notes", "unrecognised", "from", "usual"}.

    `inv` is inventory(); `rep` is ls_sampling.check() on counts_for_check(),
    or None when no workbook was checked. Never raises.
    """
    animals = per_animal(inv.get("files") or [])
    checked = bool(rep) and not rep.get("error")
    usual_slides = most_common([v["slides"] for v in animals.values()])
    usual_files = most_common([len(v["files"]) for v in animals.values()])
    missing = set(rep.get("missing") or []) if checked else set()
    duplicate = set(rep.get("duplicate") or []) if checked else set()
    shared = {}
    for sid, who in (rep.get("shared") or []) if checked else []:
        for a in who:
            shared[a] = (sid, [b for b in who if b != a])
    treatment = dict(rep.get("by_animal") or {}) if checked else {}

    rows, flagged = [], Counter()
    for a in sorted(animals, key=_natural_key):
        v = animals[a]
        if not checked:
            status = [UNCHECKED]
        elif a in missing:
            status = [NOT_IN_BOOK]
        elif a in duplicate:
            status = [TWO_ROWS]
        elif a in shared:
            sid, others = shared[a]
            status = ["Shares workbook row %s with %s" % (sid, ", ".join(others))]
        else:
            status = []
        if usual_slides and v["slides"] < usual_slides:
            status.append("Fewer slides than most (%d)" % usual_slides)
            flagged["fewer"] += 1
        elif usual_files and len(v["files"]) < usual_files:
            status.append("Fewer files than most (%d)" % usual_files)
            flagged["fewer"] += 1
        rows.append({"animal": a, "treatment": treatment.get(a, ""),
                     "slides": v["slides"], "files": len(v["files"]),
                     "slide_files": ", ".join(v["files"]),
                     "status": "; ".join(status) or OK})
    unused = list(rep.get("unused_ids") or []) if checked else []
    # A row is unused when its animal is on two rows (or shares one); that animal
    # has slides and is said so above, so it is not "in the workbook, no slides".
    unused = [sid for sid in unused
              if not SAMPLING.match_ids([sid], list(animals), rep.get("rule") or "auto")[0]]
    for sid in unused:
        rows.append({"animal": sid, "treatment": "", "slides": 0, "files": 0,
                     "slide_files": "", "status": NO_SLIDES})

    n_slides = sum(v["slides"] for v in animals.values())
    n_files = sum(len(v["files"]) for v in animals.values())
    parts = ["%d animals in the slides folder, %d slides (%d files)."
             % (len(animals), n_slides, n_files)]
    if checked:
        in_book = len(animals) - len(missing) + len(unused)
        parts.append("%d animals in the workbook." % in_book)
        problems = [(len(unused), "in the workbook with no slides"),
                    (len(missing), "with slides but no workbook row"),
                    (len(duplicate), "on two or more workbook rows"),
                    (len(shared), "sharing a workbook row")]
        parts.extend("%d %s." % (n, what) for n, what in problems if n)
    if flagged["fewer"]:
        parts.append("%d with fewer slides or files than most." % flagged["fewer"])
    if inv.get("unrecognised"):
        n = len(inv["unrecognised"])
        parts.append("%d file name%s not recognised." % (n, "" if n == 1 else "s"))

    notes = []
    if inv.get("error"):
        notes.append("Slides: " + inv["error"] + ".")
    if rep is None:
        notes.append("No workbook was checked, so there is no treatment or workbook check.")
    elif rep.get("error"):
        notes.append("The workbook could not be checked: %s. Only the slides are listed."
                     % rep["error"])
    elif rep.get("blank"):
        notes.append("Blank treatment in the workbook (Unblind stops on these): %s."
                     % ", ".join(a for a, _why in rep["blank"]))
    return {"rows": rows, "summary": " ".join(parts), "notes": notes,
            "unrecognised": list(inv.get("unrecognised") or []),
            "from": inv.get("from"), "usual": (usual_slides, usual_files)}


def make(cfg, workbook):
    """inventory + the workbook check + build, for the window's worker. Never raises."""
    inv = inventory(cfg)
    counts = counts_for_check(per_animal(inv["files"]))
    rep = SAMPLING.check(workbook, counts, (cfg or {}).get("sampling")) if workbook else None
    return build(inv, rep)


def csv_path(out_root):
    return os.path.join(out_root, "manifest", CSV_NAME)


def save(manifest, out_root):
    """Write the rows to out_root/manifest/slide_manifest.csv; the path written."""
    import ls_io
    path = csv_path(out_root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rows = list(manifest["rows"]) + [
        {"animal": "", "treatment": "", "slides": "", "files": "", "slide_files": name,
         "status": "File name not recognised"} for name in manifest.get("unrecognised") or []]
    ls_io.atomic_write_csv(path, rows, list(COLUMNS))
    return path
