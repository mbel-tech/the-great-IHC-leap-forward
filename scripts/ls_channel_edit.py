"""Editing a study's channels and markers, without a window.

The settings dialog offers a table for what each CZI channel is and what colour
each marker is drawn in. Everything that table decides lives here, so the rules
the dialog enforces are the rules the pipeline enforces and can be tested
without Qt:

    load_rows(cfg)         the study's channels / markers as editable rows
    edits(cfg, layout, rows)
                           the config keys those rows write, as {dotted path: value}
    apply(cfg, edits)      a copy of cfg with those keys set
    check(cfg, layout)     (errors, warnings), from the same validators the
                           pipeline runs at import
    renames(rows)          markers whose name changed (that renames real files)
    czi_channels(path)     the channel names a CZI file reports, to fill from

A row is a plain dict:

    orig            the name it had when loaded, or None for a new row. A rename
                    is followed through `display.colours` and `display.composite`
                    by this, so recolouring is not lost by retyping a name.
    name, role, czi_name, index, segment, backend, nucleus_shaped
                    the channel's own fields (`acquisition.channels`); a paired
                    study has only a name per marker
    colour          a colour name or #rrggbb, or None for the positional default
    show            drawn in the composite and the curator's Review pane
    extra           any other keys the channel entry carried (a note, say),
                    written back untouched

The counterstain's colour is `display.nuclear_colour`, a plain setting the
dialog already offers; it is not repeated here.
"""

import copy
import os

import ls_channels as CH


def _dig(cfg, *keys):
    node = cfg
    for k in keys:
        node = node.get(k) if isinstance(node, dict) else None
        if node is None:
            return None
    return node


def layout_of(cfg):
    layout = _dig(cfg, "acquisition", "layout")
    return layout if layout in CH.LAYOUTS else CH.LAYOUT_MULTIPLEX


def _explicit_composite(cfg):
    chosen = _dig(cfg, "display", "composite")
    return [m for m in chosen if isinstance(m, str)] if isinstance(chosen, list) else []


def load_rows(cfg, layout=None):
    """The rows to edit: the channel table (multiplex) or the marker list (paired)."""
    layout = layout or layout_of(cfg)
    colours = _dig(cfg, "display", "colours")
    colours = colours if isinstance(colours, dict) else {}
    chosen = _explicit_composite(cfg)

    def shown(name):
        return (name in chosen) if chosen else True

    rows = []
    if layout == CH.LAYOUT_PAIRED:
        declared = _dig(cfg, "acquisition", "markers")
        for m in (declared if isinstance(declared, list) else []):
            if isinstance(m, str):
                rows.append({"orig": m, "name": m, "role": CH.MARKER,
                             "colour": colours.get(m), "show": shown(m), "extra": {}})
        return rows

    table = _dig(cfg, "acquisition", "channels")
    for entry in (table if isinstance(table, list) else []):
        if not isinstance(entry, dict):
            continue
        known = {"name", "role", "czi_name", "index", "segment", "backend",
                 "nucleus_shaped"}
        parsed = CH.parse([entry], has_nuclear=True)[0]
        name = entry.get("name")
        rows.append({
            "orig": name, "name": name or "", "role": entry.get("role") or CH.MARKER,
            "czi_name": entry.get("czi_name") or "", "index": entry.get("index"),
            "segment": parsed.segment, "backend": parsed.backend,
            "segment_set": "segment" in entry,
            "nucleus_shaped": bool(entry.get("nucleus_shaped", True)),
            "colour": colours.get(name) if entry.get("role") == CH.MARKER else None,
            "show": shown(name) if entry.get("role") == CH.MARKER else False,
            "extra": {k: v for k, v in entry.items() if k not in known}})
    return rows


def new_row(layout, role=CH.MARKER):
    """A blank row, with the defaults a new channel would get."""
    if layout == CH.LAYOUT_PAIRED:
        return {"orig": None, "name": "", "role": CH.MARKER, "colour": None,
                "show": True, "extra": {}}
    return {"orig": None, "name": "", "role": role, "czi_name": "", "index": None,
            "segment": CH.SEGMENT_NUCLEAR, "backend": None, "nucleus_shaped": True,
            "colour": None, "show": role == CH.MARKER, "extra": {}}


def _is_marker(row):
    return row.get("role", CH.MARKER) == CH.MARKER


def default_colours(rows):
    """{marker: colour the positional rule would give it} for the shown markers.

    First shown marker red, second green, the rest none - the same rule
    `ls_channels.marker_colours` applies, so the dialog can say what "default"
    means before anything is saved.
    """
    shown = [r["name"] for r in rows if _is_marker(r) and r.get("show") and r.get("name")]
    out = {}
    for i, name in enumerate(shown):
        out[name] = CH._POSITIONAL[i] if i < len(CH._POSITIONAL) else None
    return out


def _channel_entry(row, has_nuclear):
    entry = dict(row.get("extra") or {})
    entry["name"] = row["name"].strip()
    entry["role"] = row.get("role") or CH.MARKER
    czi = (row.get("czi_name") or "").strip()
    if czi:
        entry["czi_name"] = czi
    index = row.get("index")
    if index is not None and index != "":
        entry["index"] = int(index)
    if entry["role"] == CH.MARKER:
        segment = row.get("segment") or CH.SEGMENT_NUCLEAR
        # A default the study never wrote is not written for it: opening the
        # editor and pressing OK must leave the file as it was.
        implied = CH.SEGMENT_NUCLEAR if has_nuclear else CH.SEGMENT_OWN
        if row.get("segment_set") or segment != implied:
            entry["segment"] = segment
        if segment == CH.SEGMENT_OWN:
            entry["backend"] = row.get("backend") or "stardist"
            entry["nucleus_shaped"] = bool(row.get("nucleus_shaped", True))
    return entry


def edits(cfg, layout, rows):
    """The keys the rows write, as {dotted path: value}.

    Colours and the composite are rebuilt from the rows, so a marker that was
    removed or renamed leaves nothing behind for the validator to refuse.
    """
    markers = [r for r in rows if _is_marker(r)]
    names = [r["name"].strip() for r in markers]
    out = {}
    if layout == CH.LAYOUT_PAIRED:
        out["acquisition.markers"] = names
        out["acquisition.channels"] = []
    else:
        has_nuclear = any(r.get("role") == CH.NUCLEAR for r in rows)
        out["acquisition.channels"] = [_channel_entry(r, has_nuclear) for r in rows]
    out["display.colours"] = {r["name"].strip(): r["colour"] for r in markers
                              if r.get("colour") and r["name"].strip()}

    shown = [r["name"].strip() for r in markers if r.get("show") and r["name"].strip()]
    if len(shown) == len([n for n in names if n]):
        out["display.composite"] = []              # empty means every marker, in order
    else:
        # Keep the order the study already chose where it still applies; markers
        # newly shown follow in table order.
        old = [_renamed(cfg, rows, m) for m in _explicit_composite(cfg)]
        out["display.composite"] = ([m for m in old if m in shown]
                                    + [m for m in shown if m not in old])
    return out


def _renamed(cfg, rows, name):
    """`name` as it is called now (a renamed marker keeps its place)."""
    for r in rows:
        if r.get("orig") == name:
            return r["name"].strip()
    return name


def renames(rows):
    """[(old, new)] for rows whose name changed. Output paths are built from names."""
    return [(r["orig"], r["name"].strip()) for r in rows
            if r.get("orig") and r["name"].strip() != r["orig"]]


def apply(cfg, changes, layout=None):
    """A copy of `cfg` with `changes` set; `layout` is set too when given."""
    out = copy.deepcopy(cfg)
    if layout:
        changes = dict(changes, **{"acquisition.layout": layout})
    for path, value in changes.items():
        node = out
        parts = path.split(".")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
            if not isinstance(node, dict):
                raise ValueError("%s is not a block" % p)
        node[parts[-1]] = value
    return out


def check(cfg, layout):
    """(errors, warnings) for the channels and display of `cfg`, as the pipeline judges them."""
    acq = _dig(cfg, "acquisition")
    acq = dict(acq) if isinstance(acq, dict) else {}
    errors, warnings = CH.validate(acq, layout)
    probe = copy.deepcopy(cfg)
    probe.setdefault("acquisition", {})["layout"] = layout
    e2, w2 = CH.validate_display(probe)
    return errors + e2, warnings + w2


def czi_channels(path):
    """The rows a CZI file suggests (see `ls_channel_guess.detect`); ValueError if it cannot be read."""
    import czi_meta
    import ls_channel_guess as G
    try:
        with open(path, "rb") as fh:
            root = czi_meta.read_metadata(fh)
        infos = czi_meta.channel_info(root)
    except (OSError, ValueError) as e:
        raise ValueError("%s could not be read as a CZI file: %s" % (os.path.basename(path), e))
    if not infos:
        raise ValueError("%s reports no channels" % os.path.basename(path))
    return G.detect(infos)


def merge_detected(rows, found, layout):
    """Rows with a CZI's channels folded in; the person's own choices are never overwritten.

    A channel already in the table (matched on the file's channel name) only gets
    its detection note refreshed. A new channel is appended with what the file
    suggests. The counterstain is suggested only when the table has none; a second
    one the file reports is appended as `ignore`.
    """
    rows = [dict(r) for r in rows]
    have = {(r.get("czi_name") or "").lower(): r for r in rows if r.get("czi_name")}
    has_nuclear = any(r.get("role") == CH.NUCLEAR for r in rows)
    added = 0
    for f in found:
        row = have.get(f["czi_name"].lower())
        if row is not None:
            row["detected"] = f["detected"]
            row["band"] = f["band"]
            continue
        # A counterstain the table already has under another name is not measured
        # a second time as a marker; the new row is set aside for the person to judge.
        role = CH.IGNORE if f["role"] == CH.NUCLEAR and has_nuclear else f["role"]
        nr = new_row(layout, role)
        has_nuclear = has_nuclear or nr["role"] == CH.NUCLEAR
        nr.update({"name": f["name"], "czi_name": f["czi_name"], "index": f["index"],
                   "detected": f["detected"], "band": f["band"]})
        if nr["role"] == CH.MARKER:
            nr["colour"] = f["colour"]
        rows.append(nr)
        added += 1
    return rows, added
