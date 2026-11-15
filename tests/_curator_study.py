"""A synthetic study the curator pages can be generated from, with no operator data.

Not a test. Shared setup for tests/run.sh.

    python tests/_curator_study.py DIR      # lays the study out, prints its config path

WHY THIS EXISTS. The page suites (tests/*.test.js) run against pages that
`04l_roi_curator.py --worklist --rgb --no-seed` and `04k_level_curator.py`
GENERATE, and those read pipeline outputs: a reformat index per marker, the
ROI worklist, composites on disk, the provenance table, a plate set. run.sh
used to build them from the repo's config.json only, so on a fresh clone - and
therefore in CI - every page suite was skipped. This writes the smallest set of
those outputs that lets the REAL generators run end to end, so what CI tests is
the page the scripts actually emit, through the same `fill`/`embed` path and
the same Python-side derivations (section_dirs, rgb_dirs, review_filters,
load_provenance, plate_rows), not a template filled by hand.

WHAT IS SYNTHETIC AND WHAT IS NOT.
  * Everything under reformatted/ is made up here: animals, uids, ranks,
    provenance rows. Pictures are 1x1 PNGs - the pages only test whether a file
    exists, and nothing in the stub DOM renders.
  * The atlas is NOT made up. The study names `plates_final`, and 04l seeds it
    from the copy shipped in the repo's atlas/ folder (ls_atlas.seed_from_bundle)
    exactly as it does for a new study. That is published atlas material
    already in the repository, not operator data, and the page suites assert
    facts about it (every uncertain seed is Rm, Dl is bilateral, ...).
  * Names. The suites were written against a paired study whose markers are
    AF568 then AF488 and which reads animal "LS105" through the stub DOM
    (tests/harness.js). Those are the names this study uses, so the suites run
    unedited; they are fluorophore names and an animal id already committed in
    the harness, not anything read from a study. Animals are LS<number>
    because shotgun.test.js orders them by `+a.slice(2)`.

EXTENDING IT (later checks on these page suites). The study is described by
the module constants below, not by code paths:
  * ANIMALS        - {animal: (n first-marker sections, n second-marker sections)}
  * NOT_IN_WORKLIST- first-marker uids 04n "left out", so the worklist narrows
  * NO_RGB         - sections with no composite on disk (the --rgb fallback)
  * THUMBS         - sections that also have a composite thumbnail
  * PROVENANCE     - per-uid overrides of a provenance row (status, reason ...)
  * CONFIG_OVERRIDES - top-level config keys handed to _fixture.write_study
Add a section, an animal, a provenance case or a config key there and every
page built from this study carries it. Something the generators read that is
not written yet (a new pipeline output) goes in its own `_write_<thing>`
function called from `write_curator_study`, in the same style. Keep it
synthetic: nothing here may be copied from a real study's outputs.

A suite that asserts a fact only the real study has (a count of its sections, a
specific real scene_uid) does not belong in the synthetic run: it is listed in
run.sh's NEEDS_REAL_STUDY with the reason, rather than edited to pass here.
"""

import csv
import json
import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# _fixture sets LS_ATLAS_BUNDLE="" on import so that the PYTHON suites never get
# the shipped plates. This process only writes files; the plate set is seeded by
# the 04l run that follows, in its own process, which does not inherit that.
import _fixture                                                  # noqa: E402

MARKERS = ["AF568", "AF488"]

# Six animals because shotgun.test.js lays out a deck from six: one with twelve
# or more first-marker sections (the overflowing slide) and five more with at
# least two and a second-marker side. LS105 is the animal the harness's stub DOM
# selects by default. LS101 sorts first, so it is the animal 04k's page (which
# reads the second marker's index, reformat_index.csv) opens on, and
# level_curator.test.js needs three sections there.
ANIMALS = {
    "LS101": (4, 3),
    "LS102": (3, 2),
    "LS103": (3, 2),
    "LS104": (3, 2),
    "LS105": (14, 3),
    "LS106": (3, 2),
}

# Slide names per marker: paired scans of one animal are separate slides, so the
# two markers never share a uid (channels.test.js checks they do not).
SLIDE = {"AF568": "s01a", "AF488": "s02a"}


def uid(animal, marker, i):
    return f"{animal}_{SLIDE[marker]}_sc{i:02d}"


# One first-marker section per animal that 04n "did not rank", so --worklist
# visibly narrows that side. The LAST one, so the sections suites pick (rows()[0..3]
# of LS105) are all in.
NOT_IN_WORKLIST = {uid(a, MARKERS[0], n - 1) for a, (n, _m) in ANIMALS.items()
                   if a != "LS105"}

# Sections with no composite on disk: --rgb falls back to the greyscale for them.
NO_RGB = {uid("LS101", MARKERS[0], 0), uid("LS102", MARKERS[1], 0)}

# Sections 04o --thumbs has also made a small composite for. Only some, so the
# Review pane meets all three states: thumbnail, composite only, greyscale only.
THUMBS = {uid("LS105", MARKERS[0], i) for i in range(4)}

# Provenance cases review.test.js needs to find: an exclusion with a free-text
# reason (with a comma in it), a section that never reached a reformatted image,
# censor masks on both markers. Everything else is "reformatted" and kept.
PROVENANCE = {
    uid("LS102", MARKERS[0], 1): {"status": "excluded", "decision": "excluded",
                                  "exclusion_class": "manual",
                                  "decision_reason": "manually excluded: tissue torn, folded"},
    uid("LS103", MARKERS[1], 1): {"status": "excluded", "decision": "excluded",
                                  "exclusion_class": "manual",
                                  "decision_reason": "manually excluded: out of focus"},
    uid("LS104", MARKERS[0], 1): {"censor_img": True},
    uid("LS104", MARKERS[1], 0): {"censor_img": True},
}
# Scanned but never reformatted: in the provenance table, not in the index.
UNREFORMATTED = [("LS106", MARKERS[1], 9)]

# The config the study runs under: a paired layout declaring the two markers,
# no group key (Shotgun stays off; shotgun.test.js fills its own) and the
# shipped salmon plate set.
CONFIG_OVERRIDES = {
    "acquisition": {"layout": "paired", "channels": [], "markers": list(MARKERS),
                    "marker_segmentation": {}},
    "groups": {"order": [], "by_animal": {}},
    "atlas_source": "salmon",
    "atlas_plate_set": {"dir": "plates_final"},
}


def _png(path):
    """A real 1x1 PNG: the pages only ask whether the file exists."""
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))
    raw = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(b"\x00\x00"))
           + chunk(b"IEND", b""))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(raw)


def _write_csv(path, fields, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def _sections(marker):
    """[(animal, i, uid)] for one marker, in animal then section order."""
    col = MARKERS.index(marker)
    return [(a, i, uid(a, marker, i))
            for a, counts in ANIMALS.items() for i in range(counts[col])]


def _write_index(names, reformat_dir):
    """04a's reformat index and greyscale sections, and 04o's composites."""
    for mk in MARKERS:
        _write_csv(names.path("index", mk), ["id", "kind", "animal", "section_order"],
                   [{"id": u, "kind": "section", "animal": a, "section_order": i + 1}
                    for a, i, u in _sections(mk)])
        sec = os.path.join(reformat_dir, names.basename("sections", mk))
        rgb = os.path.join(reformat_dir, names.basename("sections_rgb", mk))
        thumb = os.path.join(reformat_dir, names.basename("sections_rgb_thumb", mk))
        for _a, _i, u in _sections(mk):
            _png(os.path.join(sec, u + ".png"))
            if u not in NO_RGB:
                _png(os.path.join(rgb, u + ".png"))
            if u in THUMBS:
                _png(os.path.join(thumb, u + ".png"))


def _write_worklist(reformat_dir):
    """04n's worklist, for the first marker: ranked, two tiers."""
    rows = [(a, i, u) for a, i, u in _sections(MARKERS[0]) if u not in NOT_IN_WORKLIST]
    _write_csv(os.path.join(reformat_dir, "roi_worklist.csv"),
               ["rank", "tier", "scene_uid", "animal", "section_order"],
               [{"rank": r + 1, "tier": "core" if r % 2 == 0 else "extra",
                 "scene_uid": u, "animal": a, "section_order": i + 1}
                for r, (a, i, u) in enumerate(rows)])


def _write_provenance(names, reformat_dir):
    """04p's section_provenance.csv, with the paths 04p would have written."""
    rows = []
    every = [(a, mk, i, u, True) for mk in MARKERS for a, i, u in _sections(mk)]
    every += [(a, mk, i, uid(a, mk, i), False) for a, mk, i in UNREFORMATTED]
    for a, mk, i, u, reformatted in every:
        over = PROVENANCE.get(u, {})
        sec = names.basename("sections", mk)
        rgb = names.basename("sections_rgb", mk)
        thumb = names.basename("sections_rgb_thumb", mk)
        row = {"scene_uid": u, "animal": a, "marker": mk, "slide": SLIDE[mk],
               "scene_index": i, "section_order": i + 1,
               "czi_file": f"{a}_{SLIDE[mk]}.czi",
               "status": "reformatted" if reformatted else "not_reformatted",
               "decision": "kept" if reformatted else "",
               "exclusion_class": "", "decision_reason": "",
               "tissue_area_mm2": "4.2", "focus_score": "0.8",
               "n_artifact_objects": "0", "artifact_pct_of_tissue": "0.0",
               "in_analysis_set": "1" if mk == MARKERS[0] else "",
               "overview_img": f"../overviews/{a}/{mk}/{u}_RGB.png",
               "section_img": f"{sec}/{u}.png" if reformatted else "",
               "section_rgb": (f"{rgb}/{u}.png"
                               if reformatted and u not in NO_RGB else ""),
               "section_thumb": f"{thumb}/{u}.png" if u in THUMBS else "",
               "mask_img": f"../artifacts/{u}_artifact.png",
               "tissue_img": "", "censor_img": "",
               "n_rois": "", "n_nuclei": ""}
        row.update({k: v for k, v in over.items() if k != "censor_img"})
        if over.get("censor_img"):
            row["censor_img"] = f"../censor/{u}_censor.png"
        rows.append(row)
    fields = list(rows[0])
    _write_csv(os.path.join(reformat_dir, "section_provenance.csv"), fields, rows)


def write_curator_study(tmp):
    """Lay the synthetic study out under `tmp`. Returns its config path."""
    path = _fixture.write_study(tmp, **json.loads(json.dumps(CONFIG_OVERRIDES)))
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    reformat_dir = os.path.join(cfg["out_root"], "reformatted")

    # ls_paths answers "what is the index / section directory called for this
    # marker" from the config, the way 04a and 04l ask it - so the layout here
    # cannot drift from the layout the generators expect.
    sys.path.insert(0, _fixture.SCRIPTS)
    import ls_paths as LP
    names = LP.for_config(cfg)

    _write_index(names, reformat_dir)
    _write_worklist(reformat_dir)
    _write_provenance(names, reformat_dir)
    return path


if __name__ == "__main__":
    print(write_curator_study(os.path.abspath(sys.argv[1])))
