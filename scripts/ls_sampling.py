"""The sampling workbook: read it, and join the study's animals to it.

06b_join_sampling.py is the unblinding stage and the only one that writes what
this decides. Set up reads the same workbook early, with the same functions:
"Match the sheet" (app/sheet_match.py) is where the person says which column
is the animal and which hold the treatment, "Check workbook" runs the join and
"Load the groups for Shotgun" keeps its groups. The check is only worth having
if it cannot disagree with the real join, so the reader and the matching live
here once. No config is read at import: the app imports this too, and its study
is not the process's LS_CONFIG.

The choice is the study's `sampling` block (ls_config):

    {"id_column": "Fish ID", "group_columns": ["treatment"],
     "ignore_columns": [], "match": "auto",
     "fill": [{"when": "brackish Tank", "is": ["303"], "set": "treatment",
               "to": "exercise"}]}

Columns are named by header text, compared with case and spacing ignored. A
study with no block whose sheet has the LS headers gets `legacy_mapping`, which
is what 06b did before the choice existed.

    sheet_names(path)                 the worksheets, in workbook order
    table(path, sheet)                (headers, data rows)
    mapping_for(saved, headers)       the saved choice, else the LS one, else None
    guess(headers, rows, animals)     a starting choice for the window
    join(animals, headers, rows, m)   what 06b decides per animal
    check(path, animals, saved)       that join as a report, never raising
    words(report)                     the report as lines for a person
    group_key(report)                 groups.order / groups.by_animal from a check
"""

import html
import os
import re
import zipfile

#: What 06b reads when its --xlsx is left empty: this file in the slides folder.
DEFAULT_NAME = "large scale exp sampling data.xlsx"

# Which tank means which treatment, in the LS sheet. The sheet states this
# itself on the rows written "303/304" and "307/308"; these are the same tanks
# named singly. Only legacy_mapping uses them, as fill rules.
EXERCISE_TANKS = {"303", "304", "307", "308"}
CONTROL_TANKS = {"305", "306", "309", "310"}

# The LS sheet's columns, by a prefix of the header cell after lower-casing and
# collapsing whitespace. 06c still reads `environment` through these; 06b only
# uses them to recognise an LS sheet in a study that has made no choice.
COLUMN_PREFIX = {"fish": "fish id", "timepoint": "timepoint", "environment": "enviro",
                 "brackish_tank": "brackish tank", "sea_tank": "sea water tank",
                 "treatment": "treatment", "body_weight_g": "body weight",
                 "fork_length_cm": "fork length", "sex": "sex", "brain_for": "brain for",
                 "sliced_july_2025": "slicing ihc july 2025"}

#: Columns 06b writes itself, so a sheet column of the same name is not carried.
RESERVED = ("animal", "sheet_id", "treatment", "treatment_source")

#: How two or more treatment columns become one group label.
JOIN = " \u00b7 "

MATCHES = ("auto", "exact", "number")


def _norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def resolve_columns(header):
    """{key: column index} from the header row, or a SystemExit naming what is
    missing. Exactly one header cell may match each prefix."""
    cells = {i: _norm(v) for i, v in header.items()}
    out, bad = {}, []
    for key, prefix in COLUMN_PREFIX.items():
        hits = [i for i, v in cells.items() if v.startswith(prefix)]
        if len(hits) != 1:
            bad.append(f"{key} (header starting '{prefix}': {len(hits)} matches)")
        else:
            out[key] = hits[0]
    if bad:
        raise SystemExit("the sampling workbook's header does not match: " + "; ".join(bad))
    return out


def read_table(path):
    """(columns, data rows): the LS sheet with its header resolved (06c's reader)."""
    rows = read_sheet(path)
    if not rows:
        raise SystemExit(f"{path}: the first worksheet is empty")
    return resolve_columns(rows[0]), rows[1:]


def read_sheet(path, sheet=None):
    """A worksheet (`sheet` by name, else the first) as a list of {column index: value}.

    Parsed from the sheet XML directly. openpyxl would be the obvious tool and
    is not installed in the pipeline environment; this needs one sheet and no
    formulas, so it does not earn a dependency.
    """
    z = zipfile.ZipFile(path)
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        x = z.read("xl/sharedStrings.xml").decode("utf-8")
        for si in re.findall(r"<si>(.*?)</si>", x, re.S):
            shared.append(html.unescape(
                "".join(re.findall(r"<t[^>]*>(.*?)</t>", si, re.S))))

    def colnum(ref):
        n = 0
        for c in re.match(r"([A-Z]+)", ref).group(1):
            n = n * 26 + ord(c) - 64
        return n - 1

    sheets = sheet_names(path)
    member = sheets[0][1] if sheets else "xl/worksheets/sheet1.xml"
    if sheet is not None:
        named = [m for n, m in sheets if n == sheet]
        if not named:
            raise SystemExit(f"{os.path.basename(path)} has no worksheet '{sheet}'")
        member = named[0]
    rows = []
    x = z.read(member).decode("utf-8")
    # Excel writes an empty row, and a formatted blank cell, as a self-closing
    # tag (`<row r="5"/>`, `<c r="B2" s="3"/>`). Each pattern takes that form
    # first; read as an open tag it runs on to the NEXT closing tag and the
    # blank cell takes its neighbour's value - a blank treatment read as a tank.
    for r in re.findall(r"<row\b[^>]*?(?:/>|>(.*?)</row>)", x, re.S):
        out = {}
        for ref, attrs, body in re.findall(
                r'<c r="([A-Z]+\d+)"([^>]*?)(?:/>|>(.*?)</c>)', r, re.S):
            v = re.search(r"<v>(.*?)</v>", body, re.S)
            t = re.search(r't="([^"]*)"', attrs)
            if v is None:
                inline = re.findall(r"<t[^>]*>(.*?)</t>", body, re.S)
                val = html.unescape("".join(inline)) if inline else ""
            elif t and t.group(1) == "s":
                val = shared[int(v.group(1))]
            else:
                val = v.group(1)
            out[colnum(ref)] = html.unescape(str(val)).strip()
        rows.append(out)
    return rows


def fish_id(animal):
    """The LS sheet's Fish ID for an animal: LSnn -> nn."""
    return re.sub(r"\D", "", animal)


def sheet_names(path):
    """[(name, member)] for every worksheet, in the order the workbook lists them."""
    z = zipfile.ZipFile(path)
    names = z.namelist()
    try:
        wb = z.read("xl/workbook.xml").decode("utf-8")
        rels = z.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    except KeyError:
        return [("Sheet1", "xl/worksheets/sheet1.xml")] \
            if "xl/worksheets/sheet1.xml" in names else []
    target = {}
    for rel in re.findall(r"<Relationship\b[^>]*>", rels):
        rid = re.search(r'Id="([^"]+)"', rel)
        tgt = re.search(r'Target="([^"]+)"', rel)
        if rid and tgt:
            t = tgt.group(1).lstrip("/")
            target[rid.group(1)] = t if t.startswith("xl/") else "xl/" + t
    out = []
    for tag in re.findall(r"<sheet\b[^>]*>", wb):
        name = re.search(r'name="([^"]*)"', tag)
        rid = re.search(r'r:id="([^"]+)"', tag) or re.search(r'\bid="([^"]+)"', tag)
        member = target.get(rid.group(1)) if rid else None
        if name and member in names:
            out.append((html.unescape(name.group(1)), member))
    return out


def table(path, sheet=None):
    """(headers, data rows) of one worksheet: row 1 is the header.

    `headers` is a list, one per column up to the last one with a header, ""
    for an unnamed column. `sheet` is a worksheet name; None is the first.
    """
    rows = read_sheet(path, sheet)
    if not rows:
        raise SystemExit(f"{os.path.basename(path)}: the worksheet is empty")
    first = rows[0]
    width = max(first) + 1 if first else 0
    return [first.get(i, "") for i in range(width)], rows[1:]


def column(headers, name):
    """The index of the header `name` (case and spacing ignored), or None."""
    want = _norm(name)
    hits = [i for i, h in enumerate(headers) if _norm(h) == want and want]
    return hits[0] if hits else None


def slug(header):
    """A header as a CSV column name: lower case, `_` for anything else."""
    return re.sub(r"[^a-z0-9]+", "_", _norm(header)).strip("_") or "column"


def _id_text(v):
    """A sheet ID as text: Excel stores 22 as "22" or "22.0"."""
    v = re.sub(r"\s+", " ", str(v or "")).strip()
    return v[:-2] if re.fullmatch(r"\d+\.0", v) else v


def _number(v):
    d = re.sub(r"\D", "", v or "")
    return str(int(d)) if d else ""


def match_ids(values, animals, how="auto"):
    """({animal: row index}, rule used, duplicated ids) for one ID column.

    `values` is the column, one per data row. exact: the cell is the animal's
    name (case ignored). number: the digits agree, so 22 is LS22. auto: exact
    if that matches anyone, else number. A cell that names an animal twice is
    a duplicate and matches nobody: two rows for one animal cannot both be it.
    """
    animals = list(animals)
    rules = ("exact", "number") if how == "auto" else (how,)
    best = ({}, rules[0], [])
    for rule in rules:
        key = (lambda v: _id_text(v).lower()) if rule == "exact" else \
            (lambda v: _number(_id_text(v)))
        rows = {}
        for i, v in enumerate(values):
            k = key(v)
            if k:
                rows.setdefault(k, []).append(i)
        found, dup = {}, []
        for a in animals:
            hit = rows.get(key(a), [])
            if len(hit) == 1:
                found[a] = hit[0]
            elif hit:
                dup.append(a)
        if found or dup:
            return found, rule, dup
    return best


def legacy_mapping(headers):
    """The LS sheet's choice, as 06b made it before there was one; None if not that sheet."""
    try:
        col = resolve_columns(dict(enumerate(headers)))
    except SystemExit:
        return None
    tank, treat = headers[col["brackish_tank"]], headers[col["treatment"]]
    return {"id_column": headers[col["fish"]], "group_columns": [treat],
            "ignore_columns": [], "match": "number", "lowercase": True,
            "fill": [{"when": tank, "is": sorted(EXERCISE_TANKS), "set": treat,
                      "to": "exercise"},
                     {"when": tank, "is": sorted(CONTROL_TANKS), "set": treat,
                      "to": "control"}]}


def mapping_for(saved, headers):
    """The choice to use: the study's own, else the LS one if this is the LS sheet."""
    if saved and saved.get("id_column"):
        return saved
    return legacy_mapping(headers)


def problems(mapping, headers):
    """Headers the choice names that the sheet does not have, as sentences."""
    out = []
    if not mapping or not mapping.get("id_column"):
        return ["no animal ID column is chosen"]
    if not mapping.get("group_columns"):
        out.append("no treatment column is chosen")
    named = [mapping["id_column"]] + list(mapping.get("group_columns") or []) + \
        [h for r in mapping.get("fill") or [] for h in (r.get("when"), r.get("set"))]
    gone = sorted({h for h in named if h and column(headers, h) is None})
    if gone:
        out.append("the sheet has no column %s" % ", ".join("'%s'" % h for h in gone))
    if mapping.get("match", "auto") not in MATCHES:
        out.append("match must be one of %s" % ", ".join(MATCHES))
    return out


def guess(headers, rows, animals):
    """A starting choice: the ID column that matches most animals, and a treatment column.

    The ID column is the one with the most animals matched (exact before by
    number on a tie); the treatment column is the first other header that
    starts with treatment, group or condition. Either may be None.
    """
    animals = list(animals)
    best, score = None, (0, 0)
    for i, h in enumerate(headers):
        if not h:
            continue
        found, rule, _dup = match_ids([r.get(i, "") for r in rows], animals)
        s = (len(found), rule == "exact")
        if s > score:
            best, score = h, s
    groups = [h for h in headers if h and h != best
              and re.match(r"(treatment|group|condition)", _norm(h))][:1]
    return {"id_column": best, "group_columns": groups, "ignore_columns": [],
            "match": "auto", "fill": []}


def join(animals, headers, rows, mapping):
    """What 06b decides for each animal, as a dict; writes nothing.

    `missing` are animals with no row (06b stops on any), `duplicate` those
    whose ID is on two rows, `meta` a row per matched animal. A group is the
    treatment cells joined with JOIN; a blank cell is filled by the first fill
    rule that fits, else the animal is in `unresolved` with no group.
    """
    bad = problems(mapping, headers)
    if bad:
        raise SystemExit("the sampling sheet cannot be joined: " + "; ".join(bad))
    animals = list(animals)
    idc = column(headers, mapping["id_column"])
    gcols = [column(headers, h) for h in mapping["group_columns"]]
    ignore = {column(headers, h) for h in mapping.get("ignore_columns") or []}
    keep = [i for i, h in enumerate(headers)
            if h and i != idc and i not in ignore and slug(h) not in RESERVED]
    names, seen = {}, set()
    for i in keep:                        # two headers with one slug: number the second
        n, k = slug(headers[i]), 2
        while n in seen:
            n, k = "%s_%d" % (slug(headers[i]), k), k + 1
        names[i] = n
        seen.add(n)
    found, rule, dup = match_ids([r.get(idc, "") for r in rows], animals,
                                 mapping.get("match", "auto"))
    fill = [dict(r, when_i=column(headers, r["when"]), set_i=column(headers, r["set"]),
                 values={_id_text(v).lower() for v in r.get("is") or []})
            for r in mapping.get("fill") or []]
    low = bool(mapping.get("lowercase"))
    meta, unresolved = {}, []
    for a in animals:
        if a not in found:
            continue
        r = rows[found[a]]
        cells, src = [], "sheet"
        for g in gcols:
            v = re.sub(r"\s+", " ", str(r.get(g, "") or "")).strip()
            if not v:
                for f in fill:
                    if f["set_i"] == g and \
                            _id_text(r.get(f["when_i"], "")).lower() in f["values"]:
                        v, src = f["to"], "rule"
                        break
            cells.append(v.lower() if low else v)
        group = JOIN.join(cells) if all(cells) else ""
        if not group:
            src = "unresolved"
            unresolved.append((a, ", ".join("%s: %s" % (headers[g], r.get(g, "") or "blank")
                                            for g in gcols)))
        meta[a] = {"animal": a, "sheet_id": _id_text(r.get(idc, "")),
                   "treatment": group, "treatment_source": src,
                   **{names[i]: str(r.get(i, "") or "") for i in keep}}
    ids = {_id_text(r.get(idc, "")) for r in rows} - {""}
    used = {meta[a]["sheet_id"] for a in meta}
    return {"rule": rule, "missing": [a for a in animals if a not in found and a not in dup],
            "duplicate": dup, "meta": meta, "unresolved": unresolved,
            "unused_ids": sorted(ids - used, key=lambda v: (_number(v) == "", len(v), v))}


def check(path, animals, saved=None, sheet=None):
    """06b's join of `animals` ({animal: slide count}) to the workbook at `path`.

    Never raises and never writes: a workbook that cannot be read, or a choice
    it cannot be joined by, is the report's `error`.
    """
    animals = dict(animals or {})
    rep = {"path": path, "error": None, "animals": len(animals), "matched": [],
           "slides": sum(animals.values()), "slides_matched": 0, "missing": [],
           "duplicate": [], "blank": [], "from_rule": [], "groups": {},
           "by_animal": {}, "rule": None, "unused_ids": [], "mapping": None}
    if not path or not os.path.isfile(path):
        rep["error"] = f"no workbook at {path}" if path else "no workbook chosen"
        return rep
    sheet = sheet or (saved or {}).get("sheet") or None
    try:
        headers, rows = table(path, sheet)
    except SystemExit as exc:
        rep["error"] = str(exc)
        return rep
    except (OSError, KeyError, ValueError, IndexError, zipfile.BadZipFile,
            UnicodeDecodeError) as exc:
        rep["error"] = f"{os.path.basename(path)} could not be read as a workbook ({exc})"
        return rep
    mapping = mapping_for(saved, headers)
    if mapping is None:
        rep["error"] = ("the animal and treatment columns are not chosen yet: use "
                        "Set up > Slides > Match the sheet")
        return rep
    rep["mapping"] = mapping
    names = sorted(animals, key=lambda a: (int(_number(a) or 0), a))
    try:
        j = join(names, headers, rows, mapping)
    except SystemExit as exc:
        rep["error"] = str(exc)
        return rep
    rep.update(rule=j["rule"], missing=j["missing"], duplicate=j["duplicate"],
               blank=list(j["unresolved"]), unused_ids=j["unused_ids"])
    for a, m in j["meta"].items():
        if not m["treatment"]:
            continue
        rep["matched"].append(a)
        rep["by_animal"][a] = m["treatment"]
        rep["slides_matched"] += animals[a]
        rep["groups"][m["treatment"]] = rep["groups"].get(m["treatment"], 0) + 1
        if m["treatment_source"] == "rule":
            rep["from_rule"].append(a)
    return rep


def group_key(rep):
    """The `groups` block a check gives: {"order": [...], "by_animal": {...}}.

    What the Shotgun deck (04l) lays slides out by, so a deck can be built
    before Unblind. The order is alphabetical, which puts "control" on the left
    of every slide; groups.order in the settings changes it. Only animals that
    got a group are in it: an unresolved one is left out, never guessed at.
    Empty when the check failed or matched nobody.
    """
    if rep.get("error") or not rep.get("by_animal"):
        return {"order": [], "by_animal": {}}
    by_animal = dict(rep["by_animal"])
    return {"order": sorted(set(by_animal.values())), "by_animal": by_animal}


def words(rep):
    """The report as lines a person reads, the most important first."""
    if rep["error"]:
        return ["The workbook cannot be used: " + rep["error"] + "."]
    out = ["%d of %d animals (%d of %d slides) matched to a group."
           % (len(rep["matched"]), rep["animals"], rep["slides_matched"], rep["slides"])]
    if rep.get("rule"):
        out.append("IDs matched %s." % ("by name" if rep["rule"] == "exact" else "by number"))
    if rep["groups"]:
        out.append("Groups: " + ", ".join("%s %d" % (g, n)
                                         for g, n in sorted(rep["groups"].items())) + ".")
    if rep["missing"]:
        out.append("No row in the sheet for %s: Unblind stops on these."
                   % ", ".join(rep["missing"]))
    if rep.get("duplicate"):
        out.append("On two or more rows: %s. Fix the sheet; these match nothing."
                   % ", ".join(rep["duplicate"]))
    if rep["blank"]:
        out.append("Blank treatment, and no rule fills it: %s. Left without a group."
                   % "; ".join("%s (%s)" % (a, why) for a, why in rep["blank"]))
    if rep.get("from_rule"):
        out.append("Blank treatment filled by a rule: %s." % ", ".join(rep["from_rule"]))
    return out
