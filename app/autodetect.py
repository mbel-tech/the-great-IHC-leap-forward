"""Reading the settings a study does not need to ask for, out of its slide files.

Several Set up questions have one right answer that is written in the CZI files
themselves: the pixel size, how the files are named, how many channels there are
and which one is the nuclear counterstain, and whether each scan carries every
marker or one. Asking a person to type those is asking them to copy a number
from one place to another, and a typo there surfaces much later as a wrong
density. So they are read here, and Set up shows them under Advanced, already
filled in, with the evidence beside them.

No Qt and no pixels: only the CZI header (czi_meta reads the XML block without
decoding an image), so a folder of hundreds of slides is read in seconds.

    detect(source_dir)          -> Detection (values, why, problems)
    fill(cfg, detection)        -> {key: value} for the keys the study has not set
    compare(cfg, detection)     -> [(key, study value, file value)] that disagree

A value the person already set is never overwritten: `fill` only proposes keys
that are absent, and `compare` reports disagreements for a person to judge.
"""

import os
from collections import Counter

import czi_meta
import ls_channel_guess as G
import ls_channels as CH
import naming_build as B

#: Slide file extensions the manifest reads.
SLIDE_EXT = ".czi"

#: Most headers read in one go. Beyond this the files are sampled evenly; the
#: pixel size and the channel layout do not change from file to file within a
#: study, and a paired study's markers show up in any spread-out sample.
MAX_HEADERS = 400


class Detection:
    def __init__(self):
        self.files = []           # slide file names found
        self.values = {}          # dotted key -> value
        self.why = {}             # dotted key -> one line of evidence
        self.problems = []        # plain-words things a person should look at
        self.read = 0             # headers read
        self.unreadable = []      # (file, reason)
        self.naming = None        # naming_build.auto() result

    def __repr__(self):
        return "<Detection %d files, %s>" % (len(self.files), sorted(self.values))

    def lines(self):
        """What was found, one line each, for showing a person."""
        out = []
        if not self.files:
            return ["No .czi files in this folder."]
        out.append("%d slide file%s found." % (len(self.files), "s" if len(self.files) != 1 else ""))
        for key in ("pixel_size_um", "slide_naming.pattern", "acquisition.layout"):
            if key in self.why:
                out.append(self.why[key])
        return out + self.problems


def slide_files(source_dir):
    """The slide file names in a folder, sorted; not recursive, like the manifest."""
    try:
        names = os.listdir(source_dir)
    except OSError:
        return []
    return sorted(n for n in names if n.lower().endswith(SLIDE_EXT)
                  and os.path.isfile(os.path.join(source_dir, n)))


def _sample(items, n):
    if len(items) <= n:
        return list(items)
    step = len(items) / float(n)
    return [items[int(i * step)] for i in range(n)]


def read_headers(source_dir, names, limit=MAX_HEADERS):
    """[(name, summary, channel_info)] for up to `limit` files; unreadable ones skipped."""
    out, bad = [], []
    for name in _sample(names, limit):
        try:
            with open(os.path.join(source_dir, name), "rb") as fh:
                root = czi_meta.read_metadata(fh)
            out.append((name, czi_meta.summarise(root), czi_meta.channel_info(root)))
        except Exception as exc:                            # noqa: BLE001 - say why, carry on
            bad.append((name, str(exc)[:120]))
    return out, bad


def _pixel_size(d, headers):
    sizes = [round(h["px_um_x"], 4) for _n, h, _c in headers if h.get("px_um_x")]
    if not sizes:
        d.problems.append("No file records its pixel size; set it under Advanced.")
        return
    counts = Counter(sizes)
    value, n = counts.most_common(1)[0]
    d.values["pixel_size_um"] = value
    d.why["pixel_size_um"] = ("Pixel size %g µm, read from %d file%s."
                              % (value, n, "s" if n != 1 else ""))
    if len(counts) > 1:
        others = ", ".join("%g µm (%d)" % (v, k) for v, k in counts.items() if v != value)
        d.problems.append("Some files were scanned at another pixel size: %s. The pipeline "
                          "uses one pixel size per study, so those files need a study of "
                          "their own." % others)
    # The overviews are drawn at 8 native pixels per overview pixel, whatever the
    # objective: 5.2 µm at 0.65.
    d.values["overview_target_um_per_px"] = round(value * 8, 4)
    d.why["overview_target_um_per_px"] = "8 native pixels per overview pixel."


def _naming(d):
    got = B.auto(d.files)
    if not got:
        return
    d.naming = got
    d.values["slide_naming.pattern"] = got["pattern"]
    d.values["slide_naming.example"] = got["example"]
    d.values["slide_naming.case_insensitive"] = got["ignore_case"]
    res = got["result"]
    d.why["slide_naming.pattern"] = "File names read as %s: %s." % (
        B.readout(got["example"], got["spans"]), B.summary(res))
    if res["failed"]:
        d.problems.append("%d file name%s do not fit; check Set up > Slide file names."
                          % (res["failed"], "s" if res["failed"] != 1 else ""))


def _channels(d, headers):
    """Layout, channel table or marker list, and the plane indices, from the headers."""
    if not headers:
        return
    per_file = []
    for name, _h, infos in headers:
        rows = G.detect(infos)
        nuc = [r for r in rows if r["role"] == CH.NUCLEAR]
        mk = [r for r in rows if r["role"] == CH.MARKER]
        per_file.append((name, rows, nuc, mk))
    counts = Counter(len(mk) for _n, _r, _nu, mk in per_file)
    n_markers = counts.most_common(1)[0][0]
    first_rows = next(r for _n, r, _nu, mk in per_file if len(mk) == n_markers)
    nuc_idx = next((r["index"] for r in first_rows if r["role"] == CH.NUCLEAR), None)
    mk_idx = next((r["index"] for r in first_rows if r["role"] == CH.MARKER), None)
    if nuc_idx is not None:
        d.values["channels.dapi_index"] = nuc_idx
    if mk_idx is not None:
        d.values["channels.marker_index"] = mk_idx
    if nuc_idx is None:
        d.problems.append("No channel looks like a nuclear counterstain (DAPI/Hoechst). "
                          "Check Set up > Microscope and channels.")

    # One marker per scan, and the marker differs between scans: each marker was
    # imaged as its own pass (the LS layout). The manifest takes a paired scan's
    # marker from its second plane's name, so the list is made of those names.
    marker_names = Counter()
    order = []
    for _n, _r, _nu, mk in per_file:
        if len(mk) == 1:
            m = mk[0]["czi_name"] or mk[0]["name"]
            if m not in marker_names:
                order.append(m)
            marker_names[m] += 1
    if n_markers == 1 and len(marker_names) > 1:
        markers = [m for m in order if CH.NAME_RE.match(m)]
        d.values["acquisition.layout"] = CH.LAYOUT_PAIRED
        d.values["acquisition.markers"] = markers
        d.why["acquisition.layout"] = (
            "Each scan carries the counterstain and one marker, and the marker differs "
            "between scans (%s): one scan per marker." % ", ".join(
                "%s in %d" % (m, marker_names[m]) for m in markers))
        return
    table = []
    for r in first_rows:
        name = r["name"] or ("Channel%d" % r["index"])
        if not CH.NAME_RE.match(name):
            name = "Channel%d" % r["index"]
        table.append({"name": name, "role": r["role"], "czi_name": r["czi_name"],
                      "index": r["index"]})
    d.values["acquisition.layout"] = CH.LAYOUT_MULTIPLEX
    d.values["acquisition.channels"] = table
    marks = [t["name"] for t in table if t["role"] == CH.MARKER]
    d.why["acquisition.layout"] = (
        "Each scan carries every channel: %s." % ", ".join(
            "%s (%s)" % (t["name"], "counterstain" if t["role"] == CH.NUCLEAR else "marker")
            for t in table))
    if not marks:
        d.problems.append("No marker channel was found besides the counterstain.")


def detect(source_dir, limit=MAX_HEADERS):
    """Everything the slide files say about the settings. Never raises on a bad file."""
    d = Detection()
    d.files = slide_files(source_dir)
    if not d.files:
        return d
    headers, bad = read_headers(source_dir, d.files, limit)
    d.read, d.unreadable = len(headers), bad
    if bad:
        d.problems.append("%d file%s could not be read (e.g. %s: %s)."
                          % (len(bad), "s" if len(bad) != 1 else "", bad[0][0], bad[0][1]))
    _pixel_size(d, headers)
    _naming(d)
    _channels(d, headers)
    return d


def _get(cfg, key):
    node = cfg
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


#: Keys that belong together: if the study has set one, the others are its
#: decision too and are not filled in behind it.
GROUPS = (
    ("slide_naming.pattern", "slide_naming.example", "slide_naming.case_insensitive"),
    ("acquisition.layout", "acquisition.markers", "acquisition.channels",
     "channels.dapi_index", "channels.marker_index"),
)


def fill(cfg, detection, keys=None):
    """{key: value} from `detection` for the keys `cfg` leaves empty.

    `keys` limits it (None: every key detected). A key that is part of a group
    the study has already decided is left alone, so a detected channel table is
    never laid over a study's own markers.
    """
    out = {}
    decided = set()
    for group in GROUPS:
        lead = group[0]
        found, value = _get(cfg, lead)
        if found and value not in (None, "", [], {}):
            decided.update(group)
    for key, value in detection.values.items():
        if keys is not None and key not in keys:
            continue
        if key in decided:
            continue
        found, current = _get(cfg, key)
        if found and current not in (None, "", [], {}):
            continue
        out[key] = value
    return out


def compare(cfg, detection):
    """[(key, study's value, what the files say)] where the two disagree."""
    out = []
    for key in ("pixel_size_um", "acquisition.layout"):
        if key not in detection.values:
            continue
        found, current = _get(cfg, key)
        if not found or current in (None, ""):
            continue
        new = detection.values[key]
        same = (abs(float(current) - float(new)) < 1e-3 * max(1.0, abs(float(new)))
                if isinstance(new, float) else current == new)
        if not same:
            out.append((key, current, new))
    return out


# ---------------------------------------------------------------------------
# A new study from two folders
# ---------------------------------------------------------------------------

#: The study's own file, kept in its output folder so everything about a study -
#: settings, curation, pictures, results - is in one folder that can be moved,
#: copied or backed up as one.
STUDY_FILE = "study.json"

#: Atlas choices a new study is offered, as atlas_source values. OWN keeps the
#: salmon atlas selected until the person adds theirs in Set up > Atlas.
OWN_ATLAS = "own"

#: Template values that are placeholders or another study's answer, not settings.
_TEMPLATE_ONLY = ("atlas_pdf", "export_dir", "scene_render.out_dir",
                  "section_thickness_um", "slide_naming.example")


def study_file(out_root):
    return os.path.join(out_root, STUDY_FILE)


def new_project(source_dir, out_root, thickness_um, atlas="salmon", name=None,
                detection=None):
    """An unsaved project for the two folders, with what the slides say filled in.

    Everything else keeps its default: exports, scene pictures and the atlas
    plates all go inside `out_root`. `detection` is a detect() result (read
    here when not given). Returns (project, detection).
    """
    import atlas_registry as AR
    import ls_project as LP

    out_root = os.path.abspath(out_root)
    source_dir = os.path.abspath(source_dir)
    name = (name or "").strip() or os.path.basename(os.path.normpath(out_root)) or "study"
    if detection is None:
        detection = detect(source_dir)
    p = LP.Project.new(name)
    for key in _TEMPLATE_ONLY:
        p.unset(key, record=False)
    p.set("source_dir", source_dir, by="new study")
    p.set("out_root", out_root, by="new study")
    p.set("section_thickness_um", float(thickness_um), by="new study")
    if atlas in AR.BUILTIN:
        p.set("atlas_source", atlas, by="new study")
        # An atlas whose outlines cover one hemisphere is drawn on both sides.
        p.set("atlas_polygon_mirror", bool(AR.BUILTIN[atlas].mirror_allowed),
              by="new study", record=False)
    for key, value in detection.values.items():
        p.set(key, value, by="read from the slides")
    layout = detection.values.get("acquisition.layout")
    # The template's example of the other layout's table is not this study's.
    if layout == CH.LAYOUT_PAIRED:
        p.set("acquisition.channels", [], record=False)
    elif layout == CH.LAYOUT_MULTIPLEX:
        p.set("acquisition.markers", [], record=False)
    return p, detection
