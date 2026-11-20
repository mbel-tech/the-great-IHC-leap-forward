"""Reagent controls: slides or sections stained without the primary antibody, with an
isotype IgG, pre-adsorbed, or unstained. Config lookup and the file-name scan.

"Control" already means the control treatment group in this codebase, so everything here
says "reagent control". A reagent control is a property of how a section was stained, not
of the animal or the treatment: it is not blinding information, and nothing here reads
`groups.by_animal`. `scan` is handed the group labels as a parameter, never the key.

This module does not import ls_config (ls_config imports it for KINDS).
"""

import csv
import os
import re
from dataclasses import dataclass, field

import ls_naming as NM

KINDS = ("no_primary", "unstained", "isotype", "preadsorbed", "other")

# Every token is bounded by start, end, space _ - . ( ) / \ . Never \b: `_` is a word char.
_D = r"(?:^|(?<=[\s_\-.()/\\]))"
_E = r"(?=$|[\s_\-.()/\\])"
_S = r"[\s_\-.]?"


def _tok(body):
    return re.compile(_D + "(?:" + body + ")" + _E, re.I)


# Strong tiers, as (kind, pattern). Secondary-only and the generic negative control are
# stored as no_primary. `IgG` alone is deliberately absent: it needs a qualifier.
_STRONG_SRC = [
    ("no_primary",
     rf"no{_S}(?:prim(?:ary|ario|aer|är)?|1(?:st|ary|°|ry)?|pab|1ab)"
     rf"|(?:w[\s_\-./]?o|without){_S}(?:prim(?:ary)?|1(?:st|ary|°)?|pab)"
     rf"|minus{_S}(?:prim(?:ary)?|1(?:st|ary)?)"
     rf"|(?:senza|sans|ohne|uten|sin){_S}(?:prim\w*|1\w*)"
     rf"|omit(?:ted)?{_S}prim\w*|prim(?:ary)?{_S}omi(?:t|tted|ssion)|-1(?:ab|°)"),
    ("no_primary",
     rf"(?:sec(?:ondary)?|2(?:nd|ary|°)?|2ab){_S}only|only{_S}(?:sec(?:ondary)?|2(?:nd|ary)?)"),
    ("no_primary",
     rf"neg(?:ative)?{_S}(?:ctrl|ctl|cont(?:rols?)?|kontroll?e?n?|controll[oi])"
     rf"|controllo{_S}negativo|negativ{_S}kontroll|negativkontrolle"),
    ("isotype",
     rf"isotype|iso{_S}ctrl|(?:rb|rabbit|ms|mouse|n|normal|non{_S}immune){_S}igg"
     rf"|pre{_S}immune|preimm"),
    ("preadsorbed",
     rf"pre{_S}(?:ads(?:orbed|orption)?|abs(?:orbed|orption)?)|peptide{_S}block(?:ed)?"
     rf"|blocking{_S}peptide"),
    ("unstained", rf"unstained|no{_S}(?:stain|ab|antibod(?:y|ies)|abs)|auto{_S}fluo\w*"),
]
_STRONG = [(kind, _tok(src)) for kind, src in _STRONG_SRC]

# Weak tier: proposed but never pre-ticked. Ambiguous with sample or treatment codes.
_WEAK = _tok(r"nc\d*|cn|nk|neg\d*|ctrl|ctl|control|kontroll")

_MARKER_PREFIX = r"(?:no|w/?o|minus|without|senza|ohne|uten)" + _S


@dataclass
class Match:
    path: str
    kind: str
    tier: str            # "strong" | "weak"
    token: str
    markers: list = field(default_factory=list)   # [] = every marker


def _norm(s):
    return re.sub(r"[^0-9a-z]", "", s.casefold())


def _suppressed(token, labels):
    t = _norm(token)
    return bool(t) and (t in labels or re.sub(r"\d+$", "", t) in labels)


def _compile_extra(extra):
    out = []
    for e in extra or ():
        try:
            kind = e.get("kind") if e.get("kind") in KINDS else "other"
            out.append((kind, re.compile(e["regex"], re.I)))
        except (re.error, KeyError, TypeError, AttributeError):
            continue
    return out


def _scan_one(path, marker_res, labels, extra):
    # (tier rank, kind rank, specific, match)
    hits = []
    for kind, rx in _STRONG:
        m = rx.search(path)
        if m:
            hits.append((0, KINDS.index(kind), 0, kind, m.group(0), []))
    for kind, rx in extra:
        m = rx.search(path)
        if m:
            hits.append((0, KINDS.index(kind), 0, kind, m.group(0), []))
    spec = [(name, rx.search(path)) for name, rx in marker_res]
    spec = [(n, m) for n, m in spec if m]
    if spec:
        hits.append((0, KINDS.index("no_primary"), 1, "no_primary", spec[0][1].group(0),
                     [n for n, _ in spec]))
    if not hits:
        for m in _WEAK.finditer(path):
            if not _suppressed(m.group(0), labels):
                hits.append((1, KINDS.index("no_primary"), 0, "no_primary", m.group(0), []))
                break
    if not hits:
        return None
    t, _, _, kind, token, markers = min(hits, key=lambda h: h[:3])
    return Match(path, kind, "strong" if t == 0 else "weak", token, markers)


def scan(paths, markers, group_labels=(), extra=()):
    """One Match per path that looks like a reagent control: the strongest, first by tier
    and then by kind order. `paths` are relative to the slides folder; `/` and `\\` both
    delimit. A weak token equal to a group label is never proposed."""
    marker_res = [(m, _tok(_MARKER_PREFIX + re.escape(m))) for m in markers or () if m]
    labels = {_norm(g) for g in group_labels or ()}
    labels.discard("")
    extra_res = _compile_extra(extra)
    out = []
    for p in paths:
        m = _scan_one(p, marker_res, labels, extra_res)
        if m:
            out.append(m)
    return out


def entries(config):
    """The `reagent_controls` block with every sub-key present."""
    rc = (config or {}).get("reagent_controls") or {}
    return {
        "declared": rc.get("declared") or "no",
        "files": dict(rc.get("files") or {}),
        "scenes": dict(rc.get("scenes") or {}),
        "paired_with": dict(rc.get("paired_with") or {}),
        "rejected": list(rc.get("rejected") or []),
        "extra_patterns": list(rc.get("extra_patterns") or []),
    }


def _base(p):
    return os.path.basename(str(p).replace("\\", "/"))


def expand(config, scene_rows):
    """(scene_uid, marker) -> entry for every flagged scene x marker. A file entry covers all
    its scenes; a scene entry replaces it for that scene. `markers: []` means every marker
    the scene has rows for."""
    e = entries(config)
    scene_markers, scene_file = {}, {}
    for r in scene_rows:
        uid = r["scene_uid"]
        scene_markers.setdefault(uid, [])
        if r["marker_channel"] not in scene_markers[uid]:
            scene_markers[uid].append(r["marker_channel"])
        scene_file.setdefault(uid, _base(r.get("file", "")))
    out = {}
    for uid, ms in scene_markers.items():
        entry = e["scenes"].get(uid) or e["files"].get(scene_file[uid])
        if not entry:
            continue
        wanted = entry.get("markers") or ms
        for m in ms:
            if m in wanted:
                out[(uid, m)] = entry
    return out


def is_control(lookup, uid, marker):
    return (uid, marker) in lookup


def control_subject(config, basename):
    """{"subject", "slide", "replicate"} from `files.<basename>`, or None."""
    ent = entries(config)["files"].get(basename)
    if not ent or not ent.get("subject"):
        return None
    return {"subject": ent["subject"], "slide": ent.get("slide"),
            "replicate": ent.get("replicate")}


def unanswered(config, matches):
    """Strong matches neither confirmed (files/scenes) nor rejected."""
    e = entries(config)
    answered = set(e["files"]) | set(e["scenes"]) | set(e["rejected"])
    return [m for m in matches
            if m.tier == "strong" and m.path not in answered and _base(m.path) not in answered]


#: The suffix of a reagent control's series key: 00_manifest numbers a control in
#: (animal + RC_SERIES, marker), never in its donor animal's series.
RC_SERIES = "#rc"

#: The signal screen's output (01m), one row per section x marker.
SCREEN_CSV = ("qc", "reagent_screen.csv")


def manifest_lookup(config, markers=()):
    """expand() over 00's manifest_scenes.csv under `config["out_root"]`.

    `markers`: every marker each scene also carries. Under `multiplex` one scan
    holds every marker but the manifest has one row per scene, naming one; a
    stage that works per marker passes the study's markers so a scene entry
    with `markers: []` covers all of them. Under `paired` pass nothing.

    {} when nothing is confirmed, or when there is no manifest yet: a stage run
    before 00 has no section a control could be. Never reads the manifest's
    display-only `reagent_control` column.
    """
    e = entries(config)
    if not e["files"] and not e["scenes"]:
        return {}
    return expand(config, manifest_rows(config, markers))


def manifest_rows(config, markers=()):
    """00's manifest_scenes.csv under `config["out_root"]`, one row per scene x
    marker: under `multiplex` (pass the study's markers) each scene row is
    repeated for every marker it carries. [] when there is no manifest yet."""
    path = os.path.join((config or {}).get("out_root") or "", "manifest", "manifest_scenes.csv")
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    rows += [dict(r, marker_channel=m) for r in list(rows) for m in markers or ()
             if m and m != r["marker_channel"]]
    return rows


def reagent_order(uids, lookup, marker=None):
    """(controls, rest), each in natural uid order.

    With `marker`, a uid is a control when it is one for that marker; without,
    when it is one for any marker (pass a lookup already narrowed to one marker).
    """
    if marker is None:
        flagged = {uid for uid, _m in lookup}
    else:
        flagged = {uid for uid, m in lookup if m == marker}
    ordered = sorted(uids, key=NM.natural_key)
    return ([u for u in ordered if u in flagged], [u for u in ordered if u not in flagged])


def series_animal(row, lookup):
    """The key a reader groups a row by: its `animal`, or animal + "#rc" when the
    row's (scene_uid, marker_channel) is a reagent control - the series 00
    numbered its section_order in. Internal only: never a uid part or a column."""
    marker = row.get("marker_channel", row.get("marker"))
    if is_control(lookup, row["scene_uid"], marker):
        return row["animal"] + RC_SERIES
    return row["animal"]


def series_label(series):
    """How a series key is printed: the animal, or "<animal> (reagent control)"."""
    if series.endswith(RC_SERIES):
        return series[:-len(RC_SERIES)] + " (reagent control)"
    return series


def warn_unanswered(config):
    """The warning text when `declared` is unsure and the signal screen
    (qc/reagent_screen.csv) has candidates neither confirmed nor rejected, else None.
    A warning: 05b and 05c print it and carry on."""
    e = entries(config)
    if e["declared"] != "unsure":
        return None
    path = os.path.join((config or {}).get("out_root") or "", *SCREEN_CSV)
    if not os.path.exists(path):
        return None
    answered = set(e["files"]) | set(e["scenes"]) | set(e["rejected"])
    with open(path, newline="", encoding="utf-8") as fh:
        left = []
        for r in csv.DictReader(fh):
            if (r.get("status") or "").strip() != "candidate":
                continue
            uid, f = r.get("scene_uid") or "", r.get("file") or ""
            if uid in answered or f in answered or _base(f) in answered:
                continue
            if uid not in left:
                left.append(uid)
    if not left:
        return None
    shown = ", ".join(left[:5]) + (" ..." if len(left) > 5 else "")
    return (f"!! reagent controls: this study says 'unsure', and the signal screen "
            f"({'/'.join(SCREEN_CSV)}) has {len(left)} candidate section(s) not yet "
            f"confirmed as a reagent control or rejected: {shown}. Carrying on; answer "
            f"them in Set up, then re-run this stage.")
