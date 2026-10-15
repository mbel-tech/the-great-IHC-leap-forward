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

    detect(source_dir)          -> Detection (values, why, problems, report)
    fill(cfg, detection)        -> {key: value} for the keys the study has not set
    compare(cfg, detection)     -> [(key, study value, file value)] that disagree

A value the person already set is never overwritten: `fill` only proposes keys
that are absent, and `compare` reports disagreements for a person to judge.
"""

import copy
import json
import os
import re
import statistics
from collections import Counter

import czi_meta
import czi_report
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
        self.notes = []           # plain-words information (sampling), not shown by lines()
        self.read = 0             # headers read
        self.unreadable = []      # (file, reason)
        self.naming = None        # naming_build.auto() result
        self.report = {}          # every header field and what it did (JSON-safe)

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
        inst = self.report.get("instrument") or {}
        obj, cam = inst.get("objective_name") or inst.get("objective_magnification"), inst.get("camera")
        if obj or cam:
            out.append("Instrument: " + ", ".join(
                [x for x in (obj and "objective " + obj["value"],
                             cam and "camera " + cam["value"]) if x]) + ".")
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
    """[(name, summary, channel_info, flat)] for up to `limit` files; unreadable ones skipped.

    `flat` is czi_report.flatten() of the same parsed header: every field.
    """
    out, bad = [], []
    for name in _sample(names, limit):
        try:
            with open(os.path.join(source_dir, name), "rb") as fh:
                root = czi_meta.read_metadata(fh)
            out.append((name, czi_meta.summarise(root), czi_meta.channel_info(root),
                        czi_report.flatten(root)))
        except Exception as exc:                            # noqa: BLE001 - say why, carry on
            bad.append((name, str(exc)[:120]))
    return out, bad


def _pixel_size(d, headers):
    sizes = [round(h["px_um_x"], 4) for _n, h, _c, _f in headers if h.get("px_um_x")]
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
    render = render_um_per_px(value)
    for key in ("curation_render.um_per_px", "scene_render.um_per_px"):
        d.values[key] = render
        d.why[key] = ("%g µm per picture pixel: whole native %g µm pixels, between 1.3 "
                      "and 2.6 where the pixel size allows." % (render, value))


def render_um_per_px(px):
    """A whole number of native pixels near 2.6 µm: k starts at 4, halves while
    the result is over 2.6 (down to 1), doubles while it is under 1.3."""
    eps = 1e-9
    k = 4
    while px * k > 2.6 + eps and k > 1:
        k /= 2.0
    while px * k < 1.3 - eps:
        k *= 2
    return round(px * k, 4)


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
    for name, _h, infos, _f in headers:
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


def _colours(d, headers):
    """display.colours (marker -> its band's colour) and display.nuclear_colour."""
    layout = d.values.get("acquisition.layout")
    if not headers or not layout:
        return
    bands, nuc_bands = {}, Counter()
    for _n, _h, infos, _f in headers:
        for r in G.detect(infos):
            if r["role"] == CH.NUCLEAR:
                if r["band"]:
                    nuc_bands[r["band"]] += 1
                continue
            for key in {r["czi_name"], r["name"], "Channel%d" % r["index"]}:
                if key and r["band"]:
                    bands.setdefault(key, Counter())[r["band"]] += 1
    if layout == CH.LAYOUT_PAIRED:
        markers = list(d.values.get("acquisition.markers") or [])
        lookup = {m: bands.get(m) for m in markers}
    else:
        table = d.values.get("acquisition.channels") or []
        markers = [t["name"] for t in table if t["role"] == CH.MARKER]
        lookup = {t["name"]: bands.get(t["czi_name"]) or bands.get(t["name"])
                  or bands.get("Channel%d" % t["index"])
                  for t in table if t["role"] == CH.MARKER}
    colours = {}
    for m in markers:
        got = lookup.get(m)
        if got:
            colour = G.BAND_COLOUR.get(got.most_common(1)[0][0])
            if colour:
                colours[m] = colour
    if colours:
        d.values["display.colours"] = colours
        d.why["display.colours"] = "Marker colours from each channel's band: %s." % ", ".join(
            "%s %s" % (m, c) for m, c in colours.items())
    if nuc_bands:
        colour = G.BAND_COLOUR.get(nuc_bands.most_common(1)[0][0])
        if colour:
            d.values["display.nuclear_colour"] = colour
            d.why["display.nuclear_colour"] = "Counterstain drawn %s, from its band." % colour


# ---------------------------------------------------------------------------
# The report: every header field, and what each one did
# ---------------------------------------------------------------------------

_BRACKETS = re.compile(r"\[[^\]]*\]")


def _plain(path):
    """A flat path without its bracket keys: Objective[Objective:1]/LensNA -> Objective/LensNA."""
    return _BRACKETS.sub("", path) if "[" in path else path


def tag_index(flat, suffixes=None):
    """{bracket-free path: first value} for one file's flat fields, built once.

    With `suffixes`, only paths whose last segment is the last segment of one of
    them are kept, so the index stays a handful of entries per file.
    """
    lasts = {x.rsplit("/", 1)[-1] for x in suffixes} if suffixes else None
    out = {}
    for path, value in flat.items():
        if lasts is not None:
            # The last segment alone, cheaply; a bracket key holding "/" in the
            # last segment ("Objective[20x/0.8]@Name") falls back to the full strip.
            tail = path[path.rfind("/") + 1:]
            if "]" in tail and "[" not in tail:
                tail = _plain(path).rsplit("/", 1)[-1]
            elif "[" in tail:
                tail = _BRACKETS.sub("", tail)
            if tail not in lasts:
                continue
        out.setdefault(_plain(path), value)
    return out


def by_tag(index, *suffixes):
    """The first value whose bracket-free path ends with one of `suffixes`, tried in order.

    `index` is a tag_index() (or a raw flat dict, indexed here)."""
    if any("[" in p for p in index):
        index = tag_index(index, suffixes)
    for suffix in suffixes:
        if suffix in index:
            return index[suffix]
        tail = "/" + suffix
        for p, v in index.items():
            if p.endswith(tail):
                return v
    return None


def _num(text):
    try:
        v = float(text)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def _fmt(v):
    if isinstance(v, float):
        return "%g" % v
    return str(v)


def _most(values):
    """{"value": most common (with "(varies: ...)" when files differ), "files", "distinct"}."""
    vals = [_fmt(v) for v in values if v not in (None, "")]
    if not vals:
        return None
    counts = Counter(vals)
    top = counts.most_common(1)[0][0]
    out = {"value": top, "top": top, "files": len(vals), "distinct": len(counts)}
    if len(counts) > 1:
        out["value"] = "%s (varies: %s)" % (top, ", ".join(
            "%s in %d" % (v, n) for v, n in counts.most_common()))
        out["values"] = [[v, n] for v, n in counts.most_common()]
    return out


#: Instrument fields: (key, summarise() key or None, flat tag suffixes).
_INSTRUMENT = (
    ("microscope", None, ("Microscopes/Microscope@Name", "Microscopes/Microscope/System",
                          "Microscope@Name", "Microscope/System")),
    ("objective_name", None, ("Instrument/Objectives/Objective@Name",
                              "Objectives/Objective@Name", "Objective/Manufacturer/Model")),
    ("objective_magnification", "objective_mag", ("Objective/NominalMagnification",)),
    ("objective_na", "objective_na", ("Objective/LensNA",)),
    ("objective_immersion", None, ("Objective/Immersion",)),
    ("camera", "camera", ("Detector/Manufacturer/Model", "Detector@Name", "CameraName")),
    ("application", None, ("Application/Name",)),
    ("application_version", None, ("Application/Version",)),
    ("pixel_type", "pixel_type", ("Image/PixelType",)),
    ("bit_depth", "component_bit_count", ("Image/ComponentBitCount",)),
    ("online_stitching", "online_stitching", ("IsOnlineStitchingEnabled",)),
    ("shading_reference_mode", "shading_reference_mode", ("SelectedShadingReferenceMode",)),
)

#: What each instrument field does, in plain words.
_INSTRUMENT_USE = {
    "objective_name": "checks: objective must match across files",
    "objective_magnification": "checks: objective must match across files",
    "objective_na": "sets the optical resolution; checks: objective must match across files",
    "pixel_type": "checks: pixel type must match across files",
    "bit_depth": "checks: bit depth must match across files",
}


#: Every suffix _INSTRUMENT looks for, so each file is indexed once for all of them.
_INSTRUMENT_SUFFIXES = tuple(x for _k, _s, xs in _INSTRUMENT for x in xs)


def _instrument(headers):
    out = {}
    indexes = [tag_index(flat, _INSTRUMENT_SUFFIXES) for _n, _h, _c, flat in headers]
    for key, skey, suffixes in _INSTRUMENT:
        vals = []
        for (_n, h, _c, _flat), index in zip(headers, indexes):
            v = h.get(skey) if skey else None
            if v in (None, ""):
                v = by_tag(index, *suffixes)
            vals.append(v)
        got = _most(vals)
        if got:
            out[key] = got
    return out


def _channel_rows(headers):
    """One row per (plane index, channel name) across the files."""
    rows = {}
    for _n, h, infos, _f in headers:
        summ = h.get("channels") or []
        for i, info in enumerate(infos):
            name = info.get("name") or "Channel%d" % i
            r = rows.setdefault((i, name), {"index": i, "name": name, "dye": Counter(),
                                            "ex": Counter(), "em": Counter(), "band": Counter(),
                                            "exp": [], "bits": Counter(), "files": 0})
            r["files"] += 1
            dye = G.dye(info)
            if dye:
                r["dye"][dye] += 1
            if info.get("excitation_nm"):
                r["ex"][info["excitation_nm"]] += 1
            if info.get("emission_nm"):
                r["em"][info["emission_nm"]] += 1
            b = G.band(info)
            if b:
                r["band"][b] += 1
            s = summ[i] if i < len(summ) else {}
            if s.get("exposure_ms"):
                r["exp"].append(s["exposure_ms"])
            bits = s.get("component_bit_count") or h.get("component_bit_count")
            if bits:
                r["bits"][bits] += 1
    out = []
    for (_i, _name), r in sorted(rows.items(), key=lambda kv: kv[0]):
        top = lambda c: c.most_common(1)[0][0] if c else None   # noqa: E731
        out.append({
            "index": r["index"], "name": r["name"], "files": r["files"],
            "dye": top(r["dye"]), "excitation_nm": top(r["ex"]), "emission_nm": top(r["em"]),
            "band": top(r["band"]),
            "exposure_ms": ([round(min(r["exp"]), 3), round(max(r["exp"]), 3)]
                            if r["exp"] else None),
            "bit_depth": top(r["bits"]),
            "bit_depths": sorted(r["bits"]),
        })
    return out


def _scenes(headers):
    counts = [len(h.get("scenes") or []) for _n, h, _c, _f in headers]
    ws = [s["width_um"] for _n, h, _c, _f in headers for s in (h.get("scenes") or [])
          if s.get("width_um")]
    hs = [s["height_um"] for _n, h, _c, _f in headers for s in (h.get("scenes") or [])
          if s.get("height_um")]
    rng = lambda xs: [round(min(xs), 1), round(max(xs), 1)] if xs else None   # noqa: E731
    return {"per_file": ([min(counts), statistics.median(counts), max(counts)]
                         if counts else None),
            "width_um": rng(ws), "height_um": rng(hs)}


def _report(d, headers):
    """Detection.report: what the headers hold and what each field did. Also adds the
    checks (exposure, bit depth, pixel type, objective) to d.problems and the
    sampling (Nyquist) lines to d.notes."""
    flats = {name: flat for name, _h, _c, flat in headers}
    combined = czi_report.combine(flats)
    dates = sorted(h["acquired"] for _n, h, _c, _f in headers if h.get("acquired"))
    rep = {"read": {"files_read": len(headers), "files_found": len(d.files),
                    "acquired": [dates[0], dates[-1]] if dates else None}}
    inst = _instrument(headers)
    rep["instrument"] = inst
    chans = _channel_rows(headers)
    rep["channels"] = chans
    rep["scenes"] = _scenes(headers)
    uses = []

    def use(field, value, what):
        uses.append({"field": field, "value": value if isinstance(value, (int, float, str))
                     or value is None else json_safe(value), "use": what})

    px = d.values.get("pixel_size_um")
    use("pixel_size_um", px, "sets pixel_size_um, overview_target_um_per_px, "
        "curation_render.um_per_px, scene_render.um_per_px" if px else "recorded only")
    for key, _s, _x in _INSTRUMENT:
        if key in inst:
            use("instrument." + key, inst[key]["value"],
                _INSTRUMENT_USE.get(key, "recorded only"))

    # Checks that span files.
    for key, label in (("bit_depth", "bit depth"), ("pixel_type", "pixel type")):
        got = inst.get(key)
        if got and got["distinct"] > 1:
            d.problems.append("The files differ in %s: %s." % (label, ", ".join(
                "%s in %d" % (v, n) for v, n in got["values"])))
    objs = [g for g in (inst.get(k) for k in ("objective_name", "objective_magnification",
                                              "objective_na")) if g and g["distinct"] > 1]
    if objs:
        d.problems.append("The files were not all scanned with the same objective: %s." % (
            "; ".join(", ".join("%s in %d" % (v, n) for v, n in g["values"]) for g in objs)))
    by_name = {}
    for c in chans:
        if c["exposure_ms"]:
            lo, hi = by_name.get(c["name"], (c["exposure_ms"][0], c["exposure_ms"][1]))
            by_name[c["name"]] = (min(lo, c["exposure_ms"][0]), max(hi, c["exposure_ms"][1]))
    for name, (lo, hi) in by_name.items():
        if hi > lo * 1.01:
            d.problems.append("%s was exposed for %g–%g ms across files; signal is not "
                              "directly comparable between those files." % (name, lo, hi))

    nuc = set()
    for _n, _h, infos, _f in headers:
        for r in G.detect(infos):
            if r["role"] == CH.NUCLEAR:
                nuc.add(r["czi_name"] or r["name"])
    for c in chans:
        tag = "channels[%d %s]." % (c["index"], c["name"])
        use(tag + "name", c["name"], "sets acquisition.layout, acquisition.markers or "
            "acquisition.channels, channels.dapi_index, channels.marker_index")
        use(tag + "dye", c["dye"], "recorded only; with the name, sets the band when no "
            "wavelength is given")
        use(tag + "excitation_nm", c["excitation_nm"], "sets the band when no emission is given")
        use(tag + "emission_nm", c["emission_nm"], "sets the band; with the NA, the optical "
            "resolution")
        use(tag + "band", c["band"], "sets display.nuclear_colour" if c["name"] in nuc
            else "sets display.colours")
        use(tag + "exposure_ms", c["exposure_ms"], "checks: exposures must match across files")
        use(tag + "bit_depth", c["bit_depth"], "checks: bit depth must match across files")
    for key in ("per_file", "width_um", "height_um"):
        use("scenes." + key, rep["scenes"][key], "recorded only")

    na = None
    if inst.get("objective_na"):
        na = _num(inst["objective_na"]["top"])
    optics = {"na": na, "pixel_size_um": px, "channels": []}
    seen = set()
    for c in chans:
        if c["name"] in seen:
            continue
        seen.add(c["name"])
        res = (round(0.61 * c["emission_nm"] / 1000.0 / na, 3)
               if na and c["emission_nm"] else None)
        ok = (px <= res / 2.0 + 1e-9) if (res and px) else None
        optics["channels"].append({"name": c["name"], "resolution_um": res, "nyquist": ok})
        use("optics[%s]" % c["name"], res, "checks: pixel size must be at most half the "
            "optical resolution" if res else "recorded only: NA or emission unknown")
        if ok is False:
            d.notes.append("%s is sampled at %g µm, coarser than half its %.2f µm optical "
                              "resolution." % (c["name"], px, res))
    rep["optics"] = optics
    rep["uses"] = uses
    vary = czi_report.varying(combined)
    rep["varying"] = {p: vary[p]["values"] for p in sorted(vary)[:50]}
    rep["fields"] = combined
    d.report = rep


def json_safe(value):
    """Lists and dicts of plain values, as json.dumps takes them."""
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    if isinstance(value, float) and value != value:
        return None
    if value is None or isinstance(value, (int, float, str, bool)):
        return value
    return str(value)


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
    _colours(d, headers)
    if headers:
        _report(d, headers)
        d.report = json_safe(d.report)
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
    ("display.colours", "display.nuclear_colour"),
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
    # A colour may only name a marker the study measures: when the study chose its
    # own markers, the detected colours keep just the ones it shares.
    if isinstance(out.get("display.colours"), dict):
        merged = copy.deepcopy(cfg) if isinstance(cfg, dict) else {}
        for key, value in out.items():
            node = merged
            parts = key.split(".")
            for part in parts[:-1]:
                if not isinstance(node.get(part), dict):
                    node[part] = {}
                node = node[part]
            node[parts[-1]] = value
        names = set(CH.marker_names(merged))
        kept = {m: c for m, c in out["display.colours"].items() if m in names}
        if kept:
            out["display.colours"] = kept
        else:
            del out["display.colours"]
    return out


def compare(cfg, detection):
    """[(key, study's value, what the files say)] where the two disagree."""
    out = []
    # A paired marker must be named as the scans name it, or no scan pairs with it.
    if (_get(cfg, "acquisition.layout")[1] == CH.LAYOUT_PAIRED
            and detection.values.get("acquisition.layout") == CH.LAYOUT_PAIRED):
        declared = _get(cfg, "acquisition.markers")[1] or []
        scans = detection.values.get("acquisition.markers") or []
        stray = [m for m in declared if m not in scans]
        if stray:
            out.append(("acquisition.markers", declared, scans))
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
    if detection.report:
        p.note_slides(detection.report, detection.notes, detection.problems, REPORT_FILE)
    layout = detection.values.get("acquisition.layout")
    # The template's example of the other layout's table is not this study's.
    if layout == CH.LAYOUT_PAIRED:
        p.set("acquisition.channels", [], record=False)
    elif layout == CH.LAYOUT_MULTIPLEX:
        p.set("acquisition.markers", [], record=False)
    return p, detection


REPORT_FILE = "slide_metadata.json"


def write_report(out_root, detection):
    """Write the whole report (with every header field) into an existing out_root.

    Returns the path, or None when there is nothing to write, the folder does
    not exist, or it cannot be written. Never raises.
    """
    report = getattr(detection, "report", None)
    if not report or not out_root or not os.path.isdir(out_root):
        return None
    path = os.path.join(out_root, REPORT_FILE)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=1, sort_keys=True, ensure_ascii=False)
            f.write("\n")
    except (OSError, TypeError, ValueError):
        return None
    return path


_CACHE = {}


def cached(source_dir):
    """detect(source_dir), remembered while the folder's listing is unchanged."""
    if not source_dir or not os.path.isdir(source_dir):
        return None
    try:
        stamp = (tuple(slide_files(source_dir)), os.path.getmtime(source_dir))
    except OSError:
        return None
    hit = _CACHE.get(source_dir)
    if hit and hit[0] == stamp:
        return hit[1]
    found = detect(source_dir)
    _CACHE[source_dir] = (stamp, found)
    return found


def scan_marker_names(source_dir):
    """The marker channel names a paired study's scans carry (AF568, AF488), or []."""
    found = cached(source_dir)
    if not found or found.values.get("acquisition.layout") != CH.LAYOUT_PAIRED:
        return []
    return list(found.values.get("acquisition.markers") or [])
