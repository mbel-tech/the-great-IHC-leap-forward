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

A STUDY THAT MOVED. out_root changes when the data moves to another drive, and with it
the name its curation is filed under - hours of hand curation that would then sit
under a folder nothing looks up any more. Not lost, but hidden, which to the operator
is the same thing. So every write also records, in `_tokens`, the study's own mark:
`<out_root>/curation/study_id.txt`, a random id made once, which moves WITH the folder.
An entry under another folder that carries this study's mark is this study's, moved,
and is carried over: copied under the new folder, the old entry left in place.

The mark, not the uids, is what tells a moved study from a different one: a pilot and
a main study share uids by construction, and the whole point of filing per study is
that one never takes the other's work. An entry with no mark (written before marks
were recorded) can be either, so a page only reports it. A study's OWN file can say
more: it lives inside out_root, so if its only entry is under another folder, marked or
not, it is this study's - moved, or received from another machine in a bundle, where the
sender's mark came along and this copy has made its own.
"""

import os
import uuid

TOKEN_FILE = os.path.join("curation", "study_id.txt")


def study_id(out_root):
    """Whose curation this is. out_root is what makes a study a study: it is where its
    sections live. Normalised so the same folder spelled two ways on Windows is one study.
    04l embeds exactly this in its page, so the page and the importers agree."""
    return os.path.normcase(os.path.abspath(out_root))


def study_token(out_root, create=False):
    """This study's mark, kept inside out_root so it moves with the data. None if it has
    none yet and `create` is not set: only the curator builds make one."""
    path = os.path.join(out_root, TOKEN_FILE)
    try:
        with open(path, encoding="utf-8") as fh:
            token = fh.read().strip()
        if token:
            return token
    except OSError:
        pass
    if not create:
        return None
    token = uuid.uuid4().hex
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(token + "\n")
    os.replace(tmp, path)
    return token


def is_filed(value):
    return isinstance(value, dict) and isinstance(value.get("_studies"), dict)


def carried_from(value, study, token=None):
    """The older folder whose entry is this study's, when it has none under `study`.

    One entry carrying this study's mark; failing that, the file's only entry (see the
    module note). Two marked candidates, or several unmarked ones, are not guessed
    between. None when there is nothing to carry over.
    """
    if not is_filed(value) or study in value["_studies"]:
        return None
    marks = value.get("_tokens") if isinstance(value.get("_tokens"), dict) else {}
    others = [k for k in value["_studies"] if k != study]
    marked = [k for k in others if token and marks.get(k) == token]
    if marked:
        return marked[0] if len(marked) == 1 else None
    return others[0] if len(others) == 1 else None


def own(value, study, token=None):
    """This study's part of a stored value: its entry, the entry it had under an older
    folder (carried_from), or the whole of a flat value."""
    if is_filed(value):
        old = carried_from(value, study, token)
        return value["_studies"].get(old if old is not None else study)
    return value


def legacy(value):
    """What a filed value still carries from before it was filed, else None."""
    return value.get("_legacy") if is_filed(value) else None


def file_under(value, study, mine, token=None):
    """`value` with this study's part replaced by `mine`, every other study's kept.

    A flat `value` is this study's (see the module note), so it is replaced. A filed
    one keeps its other studies untouched, and keeps its `_legacy` minus the section
    uids `mine` now holds - those are superseded by the write, everything else in it
    may be another study's and is not this writer's to drop.
    """
    out = {"_studies": dict(value["_studies"]) if is_filed(value) else {}}
    out["_studies"][study] = mine
    # Every mark carried through, this study's recorded when it has one: an entry
    # carried over from an older folder stays there, marked, never deleted.
    marks = dict(value.get("_tokens") or {}) if is_filed(value) else {}
    if token:
        marks[study] = token
    if marks:
        out["_tokens"] = marks
    old = legacy(value)
    if isinstance(old, dict) and isinstance(mine, dict):
        old = {k: v for k, v in old.items() if k not in mine}
    if old not in (None, {}):
        out["_legacy"] = old
    return out
