"""Stage 6j - give already-measured ROIs their usable area. A repair.

06a drops every nucleus flagged `artifact` or `off_tissue` and used to divide
what was left by the ROI's whole outline, so a disc half over a ventricle, the
glass or a masked fold read at about half its density. 05c now records, per
ROI, the share of the outline that is on the tissue and off the artifact mask
(`results/roi_box_status.csv`) and 06a divides by that share of the area.

A study measured before 05c recorded it has no such rows, and 06a says so and
goes on dividing by the whole outline. This adds them, for every marker,
without measuring anything again: it runs `05c_detect_rois.py --marker <m>
--backfill-status` once per marker that has boxes. No CZI is read and no model
is loaded - the answer comes from the masks and the boxes as they are now.

What it cannot add is whether each ROI was SEGMENTED at all: that was not
recorded when those sections were measured, so an ROI 05c skipped (under 8 px
a side, a crop with no intensity range) stays in the dataset as a count of 0
until its section is measured again.

Run 06a (and 6c/6d) again afterwards: the areas, and every density, change.

Run:  python 06j_backfill_roi_area.py
"""

import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
# Resolved here like every stage, so the study this repairs is the one named
# by LS_CONFIG and not whichever a sibling module happened to load.
from ls_config import CONFIG, CONFIG_PATH  # noqa: E402,F401


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(argv=None):
    del argv                                # no flags
    D5 = _load("_d5", "05c_detect_rois.py")
    G5 = D5.G5
    if not os.path.exists(D5.NUCLEI_CSV):
        print(f"no {D5.NUCLEI_CSV} - nothing has been measured, so there is "
              f"nothing to repair")
        return 1
    failed, ran = [], 0
    keep = sys.argv
    try:
        for marker in G5.MARKERS:
            G5.use_marker(marker)
            if not os.path.exists(G5.BOX_CSV):
                print(f"marker: {marker}  (no {os.path.basename(G5.BOX_CSV)} - skipped)")
                continue
            sys.argv = ["05c_detect_rois.py", "--marker", marker,
                        "--backfill-status"]
            ran += 1
            if D5.main():
                failed.append(marker)
    finally:
        sys.argv = keep
    if not ran:
        print("no marker has ROI boxes - run 05a_roi_geometry.py first")
        return 1
    if failed:
        print(f"!! not complete for {', '.join(failed)} - see above")
        return 1
    print("Run 06a_roi_dataset.py again: the areas and densities change.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
