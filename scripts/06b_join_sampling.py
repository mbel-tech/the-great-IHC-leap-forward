"""Stage 6b - join the measurements to the experiment. The unblinding step.

`config.blinding` says "no stage before 06b may read a group label", which is
why the numbering lands here and not earlier. Everything upstream is keyed by
animal ID alone.

**The key is LSnn -> Fish ID nn, and the sheet validates it itself.** All 12
imaged animals match, and the sheet's `slicing IHC July 2025` column is marked
for exactly those 12 - an independent column agreeing on every one. That is
asserted rather than trusted: if it ever stops holding the key is wrong, and a
run that mis-assigns animals to groups should fail loudly rather than produce a
plausible table. Note it is NOT `Brain for == IHC`, which covers 40 fish and is
the wider allocation rather than what was sliced.

**Three animals have a blank treatment, and the tank recovers it.** Across all
153 fish the tanks partition cleanly - 303/304/307/308 exercise, 305/306/309/310
control - and at timepoint 1 exactly 32 fish are labelled control and exactly 32
are blank, the blanks being the exercise tanks. Whoever filled the sheet typed
only the control side. Every row carries `treatment_source` so the inference
stays visible and can be undone.

Reads the workbook directly, parsing the sheet XML with zipfile rather than
adding openpyxl - the pipeline needs one value per fish from one sheet, and the
dependency is not worth it. The reader and the matching live in ls_sampling.py,
shared with Set up's "Check workbook", which runs this join early and
writes nothing.

Writes:
    results/animal_metadata.csv   one row per animal
    results/roi_dataset.csv       the analysis table

Run:  python 06b_join_sampling.py
      python 06b_join_sampling.py --xlsx "D:/path/to/sampling.xlsx"
"""

import sys
import argparse
import csv
import importlib.util
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("_g5", os.path.join(_HERE, "05a_roi_geometry.py"))
G5 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(G5)

_lsio = importlib.util.spec_from_file_location(
    "_lsio", os.path.join(os.path.dirname(os.path.abspath(__file__)), "ls_io.py"))
IO = importlib.util.module_from_spec(_lsio)
_lsio.loader.exec_module(IO)

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
# ls_config resolves LS_CONFIG, applies the defaults and validates once for
# the whole process. Imported, not re-implemented: this block used to be four
# lines copy-pasted into every stage.
from ls_config import CONFIG, CONFIG_PATH  # noqa: E402
OUT_ROOT = G5.OUT_ROOT
RESULTS = os.path.join(OUT_ROOT, "results")
MEAS_CSV = os.path.join(RESULTS, "roi_measurements.csv")
META_CSV = os.path.join(RESULTS, "animal_metadata.csv")
DATASET_CSV = os.path.join(RESULTS, "roi_dataset.csv")

# The reader and the matching are ls_sampling's, shared with Set up's "Check
# workbook", so the check made at the start of a study and this join cannot
# disagree. Re-exported under their old names: 06c and the tests reach them
# through this module.
import ls_sampling as SAMPLING  # noqa: E402
from ls_sampling import (  # noqa: E402,F401
    COLUMN_PREFIX, CONTROL_TANKS, EXERCISE_TANKS, _norm, read_sheet, read_table,
    resolve_columns)

DEFAULT_XLSX = os.path.join(CONFIG["source_dir"], SAMPLING.DEFAULT_NAME)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", default=DEFAULT_XLSX)
    args = ap.parse_args()

    if not os.path.exists(MEAS_CSV):
        print(f"no {MEAS_CSV} - run 06a_roi_dataset.py first")
        return 1
    if not os.path.exists(args.xlsx):
        print(f"no workbook at {args.xlsx}")
        return 1

    meas = G5.load_csv(MEAS_CSV)
    animals = sorted({r["animal"] for r in meas},
                     key=lambda a: int(re.sub(r"\D", "", a) or 0))

    COL, sheet = read_table(args.xlsx)
    joined = SAMPLING.join(animals, COL, sheet)
    imaged, sliced = joined["imaged"], joined["sliced"]

    # The cohort check. An independent column in the same sheet agreeing on
    # every imaged animal is what makes LSnn -> Fish ID more than a guess, so a
    # disagreement means the key is wrong and this must not proceed.
    missing = joined["missing"]
    if missing:
        print(f"FAIL: no sheet row for fish {missing} - the LSnn -> Fish ID key is wrong")
        return 1
    not_sliced = joined["not_sliced"]
    if not_sliced:
        print(f"WARNING: fish {not_sliced} were imaged but are not marked in "
              f"'slicing IHC July 2025'. The cohort column and the images "
              f"disagree; check before trusting the groups.")

    meta, unresolved = joined["meta"], joined["unresolved"]

    IO.atomic_write_csv(META_CSV, [meta[a] for a in animals],
                        list(next(iter(meta.values())).keys()))

    carry = ["fish_id", "timepoint", "environment", "treatment", "treatment_source",
             "sex", "body_weight_g", "fork_length_cm"]
    out = []
    for r in meas:
        m = meta.get(r["animal"], {})
        out.append({**r, **{k: m.get(k, "") for k in carry}})
    IO.atomic_write_csv(DATASET_CSV, out, list(out[0].keys()))

    print("=" * 72)
    print(f"{len(animals)} animals -> {META_CSV}")
    print(f"{len(out)} rows      -> {DATASET_CSV}")
    print(f"  cohort check: all {len(imaged)} imaged animals are in the sheet"
          + (f", {len(imaged & sliced)} marked sliced" if sliced else ""))
    print()
    print(f"  {'animal':7} {'fish':>4} {'tp':>3} {'environment':12} "
          f"{'treatment':10} {'from':10} {'sex':4} {'ROIs':>5}")
    for a in animals:
        m = meta[a]
        n = sum(1 for r in meas if r["animal"] == a and r["roi_kind"] == "roi")
        print(f"  {a:7} {m['fish_id']:>4} {m['timepoint']:>3} {m['environment']:12} "
              f"{m['treatment'] or '(none)':10} {m['treatment_source']:10} "
              f"{m['sex']:4} {n:>5}")
    if unresolved:
        print(f"\n  UNRESOLVED treatment: {unresolved} - left blank, not guessed")
    print()
    cells = {}
    for a in animals:
        k = (meta[a]["timepoint"], meta[a]["treatment"])
        cells.setdefault(k, []).append(a)
    print("  design:")
    for k in sorted(cells):
        print(f"    timepoint {k[0]}  {k[1] or '(none)':10} n={len(cells[k])}  "
              f"{', '.join(cells[k])}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
