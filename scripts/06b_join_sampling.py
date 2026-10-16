"""Stage 6b - join the measurements to the experiment. The unblinding step.

`config.blinding` says "no stage before 06b may read a group label", which is
why the numbering lands here and not earlier. Everything upstream is keyed by
animal ID alone.

**Which columns.** The study's `sampling` block says which sheet column is the
animal and which hold the treatment; it is chosen in Set up > Slides > Match
the sheet, and read here only when this stage runs. Two or more treatment
columns make one group ("exercise \u00b7 1"). A study with no block whose sheet
has the LS headers gets the choice this stage made before there was one:
Fish ID matched by number (LS22 is fish 22), `treatment`, and the tank rule
below. Anything else stops and says to choose the columns.

**A blank treatment is left blank unless a fill rule says otherwise.** In the
LS sheet the tanks partition cleanly - 303/304/307/308 exercise,
305/306/309/310 control - and whoever filled the sheet typed only the control
side at timepoint 1, so the LS choice carries those as rules. Every row
carries `treatment_source` (sheet / rule / unresolved) so the inference stays
visible and can be undone.

Every other sheet column not marked Ignore is carried into both outputs under
its header, lower-cased with `_` for spaces. The reader and the matching live
in ls_sampling.py, shared with Set up, which runs this join early and writes
nothing.

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
                     key=lambda a: (int(re.sub(r"\D", "", a) or 0), a))

    saved = CONFIG.get("sampling") or {}
    headers, rows = SAMPLING.table(args.xlsx, saved.get("sheet") or None)
    mapping = SAMPLING.mapping_for(saved, headers)
    if mapping is None:
        print("FAIL: the animal and treatment columns are not chosen - use Set up > "
              "Slides > Match the sheet")
        return 1
    joined = SAMPLING.join(animals, headers, rows, mapping)

    # Every measured animal must have exactly one row: an animal joined to the
    # wrong row, or to none, would put it in a group it is not in.
    if joined["duplicate"]:
        print(f"FAIL: {joined['duplicate']} are on more than one row of the sheet")
        return 1
    if joined["missing"]:
        print(f"FAIL: no sheet row for {joined['missing']} (IDs matched "
              f"{'by name' if joined['rule'] == 'exact' else 'by number'} in column "
              f"'{mapping['id_column']}') - check it in Set up > Slides > Match the sheet")
        return 1

    meta, unresolved = joined["meta"], joined["unresolved"]
    cols = list(next(iter(meta.values())).keys())
    IO.atomic_write_csv(META_CSV, [meta[a] for a in animals], cols)

    carry = [c for c in cols if c != "animal"]
    out = []
    for r in meas:
        m = meta.get(r["animal"], {})
        out.append({**r, **{k: m.get(k, "") for k in carry}})
    IO.atomic_write_csv(DATASET_CSV, out, list(out[0].keys()))

    print("=" * 72)
    print(f"{len(animals)} animals -> {META_CSV}")
    print(f"{len(out)} rows      -> {DATASET_CSV}")
    print(f"  id column '{mapping['id_column']}', matched "
          f"{'by name' if joined['rule'] == 'exact' else 'by number'}; groups from "
          + ", ".join(f"'{h}'" for h in mapping["group_columns"]))
    print()
    print(f"  {'animal':8} {'sheet id':>8} {'group':24} {'from':10} {'ROIs':>5}")
    for a in animals:
        m = meta[a]
        n = sum(1 for r in meas if r["animal"] == a and r["roi_kind"] == "roi")
        print(f"  {a:8} {m['sheet_id']:>8} {m['treatment'] or '(none)':24} "
              f"{m['treatment_source']:10} {n:>5}")
    if unresolved:
        print(f"\n  UNRESOLVED treatment: {unresolved} - left blank, not guessed")
    print()
    cells = {}
    for a in animals:
        cells.setdefault(meta[a]["treatment"], []).append(a)
    print("  design:")
    for k in sorted(cells):
        print(f"    {k or '(none)':24} n={len(cells[k])}  {', '.join(cells[k])}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
