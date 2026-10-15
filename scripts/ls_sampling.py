"""The sampling workbook: read it, and join the study's animals to it.

06b_join_sampling.py is the unblinding stage and the only one that writes what
this decides. It used to hold all of this itself, and the workbook was first
read at the very end of a study - so a sheet with a renamed column, or a fish
nobody typed in, was found months after the slides were. Set up's "Check
workbook" now runs the same join at the start, writing nothing.

That check is only worth having if it cannot disagree with the real join, so
the reader and the matching live here once and 06b (and 06c, through 06b)
call them. No config is read at import: the app imports this too, and its
study is not the process's LS_CONFIG.

    read_table(path)            (columns, data rows), header resolved
    join(animals, col, sheet)   what 06b decides per animal
    check(path, animals)        that join as a report, never raising
    words(report)               the report as lines for a person
"""

import html
import os
import re
import zipfile

#: What 06b reads when its --xlsx is left empty: this file in the slides folder.
DEFAULT_NAME = "large scale exp sampling data.xlsx"

# Which tank means which treatment. The sheet states this itself on the rows
# written "303/304" and "307/308"; these are the same tanks named singly.
EXERCISE_TANKS = {"303", "304", "307", "308"}
CONTROL_TANKS = {"305", "306", "309", "310"}

# The columns this stage needs, by a prefix of the header cell after
# lower-casing and collapsing whitespace. The live sheet spells them
# "enviroment" and "sea water  tank"; the prefixes are chosen so a fixed typo
# still matches, and an inserted column moves the index rather than the
# meaning.
COLUMN_PREFIX = {"fish": "fish id", "timepoint": "timepoint", "environment": "enviro",
                 "brackish_tank": "brackish tank", "sea_tank": "sea water tank",
                 "treatment": "treatment", "body_weight_g": "body weight",
                 "fork_length_cm": "fork length", "sex": "sex", "brain_for": "brain for",
                 "sliced_july_2025": "slicing ihc july 2025"}


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
    """(columns, data rows): the sheet with its header resolved."""
    rows = read_sheet(path)
    if not rows:
        raise SystemExit(f"{path}: the first worksheet is empty")
    return resolve_columns(rows[0]), rows[1:]


def read_sheet(path):
    """The first worksheet as a list of {column index: value}.

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

    rows = []
    x = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
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
    """The sheet's Fish ID for an animal: LSnn -> nn."""
    return re.sub(r"\D", "", animal)


def join(animals, col, sheet):
    """What 06b decides for each animal, as a dict; writes nothing.

    `missing` are the fish ids with no sheet row (06b stops on any), and
    `meta` holds a row per animal that has one. A blank treatment is taken
    from the brackish tank where the tank says; where it does not, the
    treatment is left blank and the animal is in `unresolved` with its tank.
    """
    fish = {r.get(col["fish"], ""): r for r in sheet}
    sliced = {f for f, r in fish.items() if r.get(col["sliced_july_2025"], "") == "x"}
    imaged = {fish_id(a) for a in animals}
    meta, unresolved = {}, []
    for a in animals:
        n = fish_id(a)
        r = fish.get(n)
        if r is None:
            continue
        tr = (r.get(col["treatment"], "") or "").strip().lower()
        src = "sheet"
        if not tr:
            tank = (r.get(col["brackish_tank"], "") or "").strip()
            if tank in EXERCISE_TANKS:
                tr, src = "exercise", "tank"
            elif tank in CONTROL_TANKS:
                tr, src = "control", "tank"
            else:
                tr, src = "", "unresolved"
                unresolved.append((a, tank))
        meta[a] = {
            "animal": a, "fish_id": n,
            "timepoint": r.get(col["timepoint"], ""),
            "environment": r.get(col["environment"], ""),
            "treatment": tr, "treatment_source": src,
            "brackish_tank": r.get(col["brackish_tank"], ""),
            "sex": r.get(col["sex"], ""),
            "body_weight_g": r.get(col["body_weight_g"], ""),
            "fork_length_cm": r.get(col["fork_length_cm"], ""),
            "brain_for": r.get(col["brain_for"], ""),
            "sliced_july_2025": int(n in sliced),
        }
    return {"imaged": imaged, "sliced": sliced,
            "missing": sorted(imaged - set(fish)),
            "not_sliced": sorted(imaged - sliced),
            "meta": meta, "unresolved": unresolved}


def check(path, animals):
    """06b's join of `animals` ({animal: slide count}) to the workbook at `path`.

    Never raises and never writes: a workbook that cannot be read is the
    report's `error`, in the words 06b would stop with.
    """
    animals = dict(animals or {})
    rep = {"path": path, "error": None, "animals": len(animals), "matched": [],
           "slides": sum(animals.values()), "slides_matched": 0, "missing": [],
           "blank": [], "from_tank": [], "not_sliced": [], "groups": {}}
    if not path or not os.path.isfile(path):
        rep["error"] = f"no workbook at {path}" if path else "no workbook chosen"
        return rep
    try:
        col, sheet = read_table(path)
    except SystemExit as exc:                   # 06b's own words for a bad header
        rep["error"] = str(exc)
        return rep
    except (OSError, KeyError, ValueError, IndexError, zipfile.BadZipFile,
            UnicodeDecodeError) as exc:
        rep["error"] = f"{os.path.basename(path)} could not be read as a workbook ({exc})"
        return rep
    names = sorted(animals, key=lambda a: (int(fish_id(a) or 0), a))
    j = join(names, col, sheet)
    missing = set(j["missing"])
    rep["missing"] = [a for a in names if fish_id(a) in missing]
    rep["not_sliced"] = [a for a in names if fish_id(a) in set(j["not_sliced"])
                         and a not in rep["missing"]] if j["sliced"] else []
    rep["blank"] = list(j["unresolved"])
    for a, m in j["meta"].items():
        if not m["treatment"]:
            continue
        rep["matched"].append(a)
        rep["slides_matched"] += animals[a]
        rep["groups"][m["treatment"]] = rep["groups"].get(m["treatment"], 0) + 1
        if m["treatment_source"] == "tank":
            rep["from_tank"].append(a)
    return rep


def words(rep):
    """The report as lines a person reads, the most important first."""
    if rep["error"]:
        return ["The workbook cannot be used: " + rep["error"] + "."]
    out = ["%d of %d fish (%d of %d slides) matched to a group."
           % (len(rep["matched"]), rep["animals"], rep["slides_matched"], rep["slides"])]
    if rep["groups"]:
        out.append("Groups: " + ", ".join("%s %d" % (g, n)
                                         for g, n in sorted(rep["groups"].items())) + ".")
    if rep["missing"]:
        out.append("No row in the sheet for %s: Unblind stops on these."
                   % ", ".join(rep["missing"]))
    if rep["blank"]:
        out.append("Blank treatment, and the tank does not say: %s. Left without a group."
                   % ", ".join("%s (tank %s)" % (a, t or "blank") for a, t in rep["blank"]))
    if rep["from_tank"]:
        out.append("Blank treatment taken from the tank: %s." % ", ".join(rep["from_tank"]))
    if rep["not_sliced"]:
        out.append("Not marked in 'slicing IHC July 2025': %s." % ", ".join(rep["not_sliced"]))
    return out
