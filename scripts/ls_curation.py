"""Curation filed per study inside one key: the rule 04l's page and its Python readers share.

The curator pages keep their state in localStorage, and localStorage is per ORIGIN:
every page opened from file:// in Chrome is one origin, and so is everything
serve_curators.py serves. Scene uids are built from subject names, which a pilot and
a main study easily share, so two studies' pages read and wrote each other's
curation under the same key. The key names cannot change - the app mirrors them to
the project and to curation/<key>.json by name (app/state.py KEYS) - so the VALUE
says whose it is:

    {"_studies": {<study>: <what the key held before>}, "_legacy": <an old flat value>}

Underscore-led because a scene uid cannot start with one, so the two shapes cannot be
mistaken for each other. The page (04l_roi_curator.py) reads and writes only its own
study's entry and carries every other one through.

A value from before this change is flat and cannot say whose it is. Here, reading
curation/<key>.json from inside a study's out_root, a flat value IS that study's: the
file has always been one study's, and treating it so keeps every importer's behaviour
on an existing study exactly what it was. (The page, on a shared origin, cannot assume
that; see its openStore.)
"""

import os


def study_id(out_root):
    """Whose curation this is. out_root is what makes a study a study: it is where its
    sections live. Normalised so the same folder spelled two ways on Windows is one study.
    04l embeds exactly this in its page, so the page and the importers agree."""
    return os.path.normcase(os.path.abspath(out_root))


def is_filed(value):
    return isinstance(value, dict) and isinstance(value.get("_studies"), dict)


def own(value, study):
    """This study's part of a stored value: its entry, or the whole of a flat value."""
    if is_filed(value):
        return value["_studies"].get(study)
    return value


def legacy(value):
    """What a filed value still carries from before it was filed, else None."""
    return value.get("_legacy") if is_filed(value) else None


def file_under(value, study, mine):
    """`value` with this study's part replaced by `mine`, every other study's kept.

    A flat `value` is this study's (see the module note), so it is replaced. A filed
    one keeps its other studies untouched, and keeps its `_legacy` minus the section
    uids `mine` now holds - those are superseded by the write, everything else in it
    may be another study's and is not this writer's to drop.
    """
    out = {"_studies": dict(value["_studies"]) if is_filed(value) else {}}
    out["_studies"][study] = mine
    old = legacy(value)
    if isinstance(old, dict) and isinstance(mine, dict):
        old = {k: v for k, v in old.items() if k not in mine}
    if old not in (None, {}):
        out["_legacy"] = old
    return out
