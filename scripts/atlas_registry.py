"""Which atlases the pipeline knows, and what each one needs from the stages.

An atlas used to be a name compared against in four places - `== WULLIMANN` in
`ls_atlas.reformatted_dir`, `ls_atlas.set_name`, `04e_register_elastix` and
`04l_roi_curator` - so a third atlas meant editing all four and finding the
fifth. This module turns each of those comparisons into a fact about the atlas
that the stage asks for instead:

    set_dir               the plate-set folder under <out_root>/atlas/. None means
                          "the study chooses" (salmon reads `atlas_plate_set.dir`).
    reformatted_dir       the subfolder of reformatted/ that holds this atlas's
                          reformatted plates, so a tool globbing it never sees
                          another atlas's plates.
    region_kind           "seeds" (coloured dots, grouped into hulls) or "polygons"
                          (closed outlines, one region each). Informational: 04l
                          already chooses per plate by whether `polygons.csv`
                          has rows for it.
    micrograph_half       the plate draws the atlas on one half and a micrograph
                          on the other (Wullimann), so registration (04e) reads
                          only that half.
    mirror_allowed        `atlas_polygon_mirror` applies: the outlines cover one
                          hemisphere and can be reflected about the midline.

Two built-in atlases, plus any the study declares under `atlases` in its config
(see `ls_atlas_import.py`). The module is standard library only, so `ls_config`
and `ls_atlas` can both import it without a cycle.

A bad declaration raises rather than falling back. A typo that silently chose the
salmon atlas would register sections against the wrong species and nothing
downstream would say so.
"""

import re
from collections import namedtuple

Entry = namedtuple("Entry", [
    "name", "label", "set_dir", "reformatted_dir", "region_kind",
    "micrograph_half", "mirror_allowed", "builtin"])

SALMON = "salmon"
WULLIMANN = "wullimann1996"
WULLIMANN_SET = "plates_wullimann"

KINDS = ("seeds", "polygons")

BUILTIN = {
    SALMON: Entry(SALMON, "Salmon atlas (PDF with colour-dot legend)", None, "plates",
                  "seeds", False, False, True),
    WULLIMANN: Entry(WULLIMANN, "Wullimann 1996 zebrafish (outlined cross sections)",
                     WULLIMANN_SET, WULLIMANN_SET, "polygons", True, True, True),
}

# Set directories the built-ins use. A custom atlas may not take one: plate ids
# collide between sets (`plate_012` names a different picture in each), and the
# reformatted/ folders are keyed by the same name.
RESERVED_DIRS = frozenset(["plates", "plates_merged", "plates_final", WULLIMANN_SET])

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
_DIR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,60}$")
_FIELDS = {"label", "set_dir", "region_kind", "micrograph_half", "mirror_allowed"}


def _custom(name, spec):
    """One declared atlas as an Entry, or ValueError saying what is wrong."""
    where = "atlases.%s" % name
    if not _NAME_RE.match(name):
        raise ValueError("%s: the name must be lower-case letters, digits and _, "
                         "starting with a letter (2-41 characters)" % where)
    if name in BUILTIN:
        raise ValueError("%s: that name belongs to a built-in atlas" % where)
    if not isinstance(spec, dict):
        raise ValueError("%s must be an object" % where)
    unknown = sorted(k for k in spec if k not in _FIELDS and not str(k).startswith("_"))
    if unknown:
        raise ValueError("%s: unknown field(s) %s (allowed: %s)"
                         % (where, ", ".join(map(str, unknown)), ", ".join(sorted(_FIELDS))))
    set_dir = spec.get("set_dir")
    if not isinstance(set_dir, str) or not _DIR_RE.match(set_dir) or ".." in set_dir:
        raise ValueError("%s.set_dir must be a plain folder name" % where)
    if set_dir in RESERVED_DIRS:
        raise ValueError("%s.set_dir %r is used by a built-in atlas; plate ids collide "
                         "between sets, so a custom atlas needs its own folder" % (where, set_dir))
    kind = spec.get("region_kind")
    if kind not in KINDS:
        raise ValueError("%s.region_kind must be one of %s" % (where, ", ".join(KINDS)))
    for flag in ("micrograph_half", "mirror_allowed"):
        if flag in spec and not isinstance(spec[flag], bool):
            raise ValueError("%s.%s must be true or false" % (where, flag))
    if spec.get("mirror_allowed") and kind != "polygons":
        raise ValueError("%s: only polygon atlases can be mirrored" % where)
    label = spec.get("label")
    if label is not None and not isinstance(label, str):
        raise ValueError("%s.label must be text" % where)
    return Entry(name, label or name, set_dir, set_dir, kind,
                 bool(spec.get("micrograph_half", False)),
                 bool(spec.get("mirror_allowed", False)), False)


def entries(cfg=None):
    """{name: Entry}: the built-ins, then the study's own, in declaration order."""
    out = dict(BUILTIN)
    declared = (cfg or {}).get("atlases")
    if declared in (None, {}):
        return out
    if not isinstance(declared, dict):
        raise ValueError("atlases must be an object of {name: {...}}")
    seen_dirs = {}
    for name, spec in declared.items():
        e = _custom(name, spec)
        if e.set_dir in seen_dirs:
            raise ValueError("atlases.%s and atlases.%s share the folder %r"
                             % (seen_dirs[e.set_dir], name, e.set_dir))
        seen_dirs[e.set_dir] = name
        out[name] = e
    return out


def names(cfg=None):
    return tuple(entries(cfg))


def get(cfg, name):
    """The Entry for `name`; ValueError listing the valid names if there is none."""
    reg = entries(cfg)
    if name not in reg:
        raise ValueError("atlas_source %r is not one of %s" % (name, ", ".join(reg)))
    return reg[name]
