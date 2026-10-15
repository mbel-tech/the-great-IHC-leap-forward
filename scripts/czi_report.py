"""Every field of a CZI metadata header, flattened, and summarised across files.

No Qt. flatten() turns the XML tree read by czi_meta.read_metadata into
path -> value; combine() says, per path, what the study's files hold.
"""

from collections import Counter

# Fields that differ from file to file by nature; matched as substrings of the path.
NATURALLY_DIFFERENT = (
    "AcquisitionDateAndTime", "CreationDate", "Guid", "@Id",
    "CenterPosition", "Scenes", "SizeS", "SizeX", "SizeY",
    "Title", "FileName", "Document/Name",
    # Stage and focus positions and timings: every scan is somewhere else.
    "StagePosition", "FocusPosition", "Position/X", "Position/Y", "Position/Z",
    "PositionX", "PositionY", "PositionZ", "/Z@", "TimeStamp", "Duration",
)

#: Subtrees below Metadata walked first, so the field cap never starves them.
FIRST = ("Information", "Scaling")


def _key(el, count, position):
    """Path segment for el: bare tag when sole, else tag plus Id/Name/Index/position.

    `count` is how many siblings share el's tag; `position` is el's 1-based place
    among them."""
    tag = el.tag
    if count < 2:
        return tag
    for attr in ("Id", "Name", "Index"):
        if el.get(attr):
            return f"{tag}[{el.get(attr)}]"
    return f"{tag}[{position}]"


def _children(el, path):
    """[(kid, kid path)] in document order; sibling tags counted once per parent."""
    kids = list(el)
    counts = Counter(k.tag for k in kids)
    seen = Counter()
    out = []
    for kid in kids:
        seen[kid.tag] += 1
        seg = _key(kid, counts[kid.tag], seen[kid.tag])
        out.append((kid, f"{path}/{seg}" if path else seg))
    return out


def flatten(root, max_fields=5000, max_text=200):
    """Every non-empty text and attribute as path -> value (paths below Metadata)."""
    start = root.find("Metadata")
    if start is None:
        start = root
    out = {}
    cap = [False]

    def put(path, value):
        if len(out) >= max_fields:
            cap[0] = True
            return False
        if len(value) > max_text:
            value = value[:max_text] + "…"
        out[path] = value
        return True

    def walk(el, path):
        for name, val in el.attrib.items():
            if not put(f"{path}@{name}", val):
                return False
        text = (el.text or "").strip()
        if text and not put(path, text):
            return False
        for kid, kid_path in _children(el, path):
            if not walk(kid, kid_path):
                return False
        return True

    # Information and Scaling first (real ZEN headers put Experiment,
    # HardwareSetting and the like before them), then everything else; the
    # paths are the same as a walk in document order would give.
    ok = True
    for name, val in start.attrib.items():
        if not put(f"@{name}", val):
            ok = False
            break
    text = (start.text or "").strip()
    if ok and text:
        ok = put("", text)
    if ok:
        kids = _children(start, "")
        first = [k for k in kids if k[0].tag in FIRST]
        rest = [k for k in kids if k[0].tag not in FIRST]
        for kid, kid_path in first + rest:
            if not walk(kid, kid_path):
                break
    if cap[0]:
        out["_truncated"] = "yes"
    return out


def combine(flat_by_file):
    """path -> {"files": n, "values": [[value, count]...<=5], "distinct": k}."""
    seen = {}
    for flat in flat_by_file.values():
        for path, value in flat.items():
            seen.setdefault(path, Counter())[value] += 1
    return {
        path: {"files": sum(c.values()),
               "values": [[v, n] for v, n in c.most_common(5)],
               "distinct": len(c)}
        for path, c in seen.items()
    }


def _natural(path):
    return any(s in path for s in NATURALLY_DIFFERENT)


def varying(combined):
    """Paths whose value differs between files, leaving out naturally different ones."""
    return {p: v for p, v in combined.items() if v["distinct"] > 1 and not _natural(p)}


def constant(combined):
    """Paths with one value across every file that has them."""
    return {p: v for p, v in combined.items() if v["distinct"] == 1}


def order(path):
    """Sort key: Information/* first, then Scaling/*, then the rest, each alphabetical."""
    for i, top in enumerate(FIRST):
        if path == top or path.startswith(top + "/") or path.startswith(top + "@"):
            return (i, path)
    return (len(FIRST), path)
