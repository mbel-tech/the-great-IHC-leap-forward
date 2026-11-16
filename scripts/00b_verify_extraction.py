"""Stage 0b - verify the extracted CZIs against the zip central directories.

The transfer zips are STORED, so every member's uncompressed size and CRC are
already recorded in the central directory and can be checked without reading a
single byte of the archives.

Size checking is the default and is nearly free. --crc additionally verifies
content, which means reading every extracted file end to end (~420 GB), so it
is opt-in.

Run:  python 00b_verify_extraction.py
      python 00b_verify_extraction.py --crc        # slow, full content check
      python 00b_verify_extraction.py --watch      # poll until extraction ends
"""

import csv
import glob
import importlib.util
import json
import os
import sys
import time
import zipfile
import zlib
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
# ls_config resolves LS_CONFIG, applies the defaults and validates once for
# the whole process. Imported, not re-implemented: this block used to be four
# lines copy-pasted into every stage.
from ls_config import CONFIG, CONFIG_PATH  # noqa: E402

SOURCE_DIR = CONFIG["source_dir"]
OUT_ROOT = CONFIG["out_root"]
QC_DIR = os.path.join(OUT_ROOT, "qc")

_lsio = importlib.util.spec_from_file_location(
    "_lsio", os.path.join(os.path.dirname(os.path.abspath(__file__)), "ls_io.py"))
IO = importlib.util.module_from_spec(_lsio)
_lsio.loader.exec_module(IO)
# source_files matching, shared with 00_manifest so the two cannot drift.
_lssrc = importlib.util.spec_from_file_location(
    "_lssrc", os.path.join(os.path.dirname(os.path.abspath(__file__)), "ls_sources.py"))
SRC = importlib.util.module_from_spec(_lssrc)
_lssrc.loader.exec_module(SRC)

# check()'s columns, pinned: an inventory with nothing in it must still
# leave a file with a header rather than crash on rows[0].
STATUS_KEYS = ["file", "zip", "expected_bytes", "actual_bytes", "status", "crc_ok"]

WATCH_INTERVAL_S = 60
CRC_CHUNK = 1 << 24


def zip_inventory(problems=None):
    """member filename -> {size, crc, zip} for every CZI in every transfer zip.

    `problems`, a list, receives one line per archive that could not be read
    and per member that two archives disagree on. Both used to be printed and
    passed over, so a corrupt transfer zip - whose CZIs are then never checked
    at all - ended in a run that exited 0.

    A `source_files` list narrows this exactly as it narrows 00_manifest's
    discovery: a named zip takes every CZI in it, a named CZI takes that member
    of whichever zip holds it, and anything else in the folder is not this
    study's. Without that, a subset study - the case source_files exists for,
    where the rest of the folder is often never extracted - failed here on
    slides it never asked for. An unreadable archive nobody named is likewise
    not a problem of this run - unless a named CZI is then found nowhere
    (its only zip may be that archive), which is one, named with the archive.
    The matching is ls_sources', the one 00 uses.
    """
    problems = problems if problems is not None else []
    only = SRC.named(CONFIG.get("source_files"))
    inventory = {}
    # A named CZI extracted loose is found; 00b only checks zip members, and a
    # loose file with no archive has nothing to be checked against.
    found = {os.path.basename(p)
             for p in glob.glob(os.path.join(SOURCE_DIR, "*.czi"))}
    unreadable = []
    for zpath in sorted(glob.glob(os.path.join(SOURCE_DIR, "*.zip"))):
        zname = os.path.basename(zpath)
        whole = SRC.takes_zip(zname, only)
        try:
            zf = zipfile.ZipFile(zpath)
        except (zipfile.BadZipFile, OSError) as exc:
            if whole:
                print(f"  !! unreadable archive: {zname}")
                problems.append(f"unreadable archive {zname}: {exc}")
            else:
                unreadable.append(zname)
            continue
        if whole:
            found.add(zname)
        with zf:
            for info in zf.infolist():
                name = os.path.basename(info.filename)
                if not SRC.takes_member(info.filename, whole, only):
                    continue
                found.add(name)
                if name in inventory and inventory[name]["size"] != info.file_size:
                    print(f"  !! {name} differs between archives - keeping first")
                    problems.append(f"{name} differs between {inventory[name]['zip']} "
                                    f"and {os.path.basename(zpath)}")
                    continue
                inventory.setdefault(
                    name,
                    {
                        "size": info.file_size,
                        "crc": info.CRC,
                        "zip": os.path.basename(zpath),
                        "compress_type": info.compress_type,
                    },
                )
    # Only with a source_files list, as in 00: a named file found nowhere -
    # used to be skipped silently, so a CZI whose only zip would not open was
    # never checked and the run said the extraction was verified.
    if only:
        for name, why in SRC.unfound(only, found, unreadable, SOURCE_DIR):
            print(f"  !! {name}: {why}")
            problems.append(f"{name}: {why}")
    return inventory


def file_crc(path):
    crc = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(CRC_CHUNK)
            if not chunk:
                break
            crc = zlib.crc32(chunk, crc)
    return crc & 0xFFFFFFFF


def check(inventory, do_crc=False):
    rows = []
    for name, meta in sorted(inventory.items()):
        path = os.path.join(SOURCE_DIR, name)
        row = {
            "file": name,
            "zip": meta["zip"],
            "expected_bytes": meta["size"],
            "actual_bytes": "",
            "status": "",
            "crc_ok": "",
        }
        if not os.path.exists(path):
            row["status"] = "missing"
        else:
            actual = os.path.getsize(path)
            row["actual_bytes"] = actual
            if actual != meta["size"]:
                # A file still being written is short, not corrupt.
                row["status"] = "in_progress" if actual < meta["size"] else "size_mismatch"
            else:
                row["status"] = "ok"
                if do_crc:
                    row["crc_ok"] = int(file_crc(path) == meta["crc"])
                    if not row["crc_ok"]:
                        row["status"] = "crc_mismatch"
        rows.append(row)
    return rows


def summarise(rows, inventory):
    counts = defaultdict(int)
    for r in rows:
        counts[r["status"]] += 1

    total_bytes = sum(m["size"] for m in inventory.values())
    done_bytes = sum(r["actual_bytes"] or 0 for r in rows if r["status"] == "ok")
    return counts, total_bytes, done_bytes


def report(rows, inventory, do_crc):
    counts, total_bytes, done_bytes = summarise(rows, inventory)
    print()
    print("=" * 72)
    print(f"CZIs listed in the archives : {len(rows)}")
    for status in ("ok", "in_progress", "missing", "size_mismatch", "crc_mismatch"):
        if counts.get(status):
            print(f"  {status:<14}: {counts[status]}")
    print(
        f"extracted so far            : {done_bytes / 1e9:.1f} GB of {total_bytes / 1e9:.1f} GB "
        f"({100.0 * done_bytes / max(total_bytes, 1):.1f}%)"
    )

    bad = [r for r in rows if r["status"] in ("size_mismatch", "crc_mismatch")]
    if bad:
        print(f"\n  CORRUPT - re-extract these {len(bad)}:")
        for r in bad[:20]:
            print(f"    {r['file']}  from {r['zip']}  ({r['actual_bytes']} vs {r['expected_bytes']})")

    pending = [r for r in rows if r["status"] in ("missing", "in_progress")]
    if pending:
        remaining = sum(
            inventory[r["file"]]["size"] - (r["actual_bytes"] or 0) for r in pending
        )
        print(f"\n  still to extract: {len(pending)} files, {remaining / 1e9:.1f} GB")
        by_zip = defaultdict(int)
        for r in pending:
            by_zip[r["zip"]] += 1
        for z, n in sorted(by_zip.items(), key=lambda kv: -kv[1]):
            print(f"    {n:>3} from {z}")
    elif not bad:
        print("\n  extraction complete and every size matches." + ("" if do_crc else "  (run --crc to verify content)"))
    print("=" * 72)
    return counts


def verdict(counts, problems):
    """Why this run must exit non-zero, or None when every CZI checked out.

    A verification that finds a corrupt, missing or half-written file, or an
    archive it could not read, has not verified the extraction - and it used
    to say so in its table and then exit 0, so the app showed the step done.
    """
    bad = {s: counts.get(s, 0) for s in
           ("size_mismatch", "crc_mismatch", "missing", "in_progress")}
    parts = [f"{n} {s.replace('_', ' ')}" for s, n in bad.items() if n]
    if problems:
        parts.append(f"{len(problems)} archive problem(s)")
    if not parts:
        return None
    return ("extraction not verified: " + ", ".join(parts)
            + " - see qc/extraction_status.csv")


def main():
    do_crc = "--crc" in sys.argv
    watch = "--watch" in sys.argv

    print(f"Reading zip central directories in {SOURCE_DIR} ...")
    problems = []
    inventory = zip_inventory(problems)
    stored = sum(1 for m in inventory.values() if m["compress_type"] == 0)
    print(f"  {len(inventory)} CZI members ({stored} STORED)")

    while True:
        rows = check(inventory, do_crc)
        counts = report(rows, inventory, do_crc)

        os.makedirs(QC_DIR, exist_ok=True)
        out = os.path.join(QC_DIR, "extraction_status.csv")
        IO.atomic_write_csv(out, rows, STATUS_KEYS)
        print(f"  wrote {out}")

        if not watch or not (counts.get("missing") or counts.get("in_progress")):
            break
        print(f"  waiting {WATCH_INTERVAL_S}s ...\n")
        time.sleep(WATCH_INTERVAL_S)

    for line in problems:
        print(f"  !! {line}")
    # No archives is not a failure: a study of loose CZIs has nothing to check
    # against, and the empty table says exactly that.
    reason = verdict(counts, problems)
    if reason:
        print(reason)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
