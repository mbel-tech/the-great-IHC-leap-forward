"""Every field of a CZI metadata header, flattened, and summarised across files.

No Qt. flatten() turns the XML tree read by czi_meta.read_metadata into
path -> value; combine() says, per path, what the study's files hold.
"""

from collections import Counter

# Fields that differ from file to file by nature; matched as substrings of the path.
NATURALLY_DIFFERENT = (
    "AcquisitionDateAndTime", "CreationDate", "Guid", "Id@",
    "CenterPosition", "Scenes", "SizeS", "SizeX", "SizeY",
    "Title", "FileName", "Document/Name",
)


def _key(el, siblings):
    """Path segment for el: bare tag when sole, else tag plus Id/Name/Index/position."""
    tag = el.tag
    if len(siblings) < 2:
        return tag
    for attr in ("Id", "Name", "Index"):
        if el.get(attr):
            return f"{tag}[{el.get(attr)}]"
    return f"{tag}[{siblings.index(el) + 1}]"


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
        kids = list(el)
        for kid in kids:
            same = [k for k in kids if k.tag == kid.tag]
            if not walk(kid, f"{path}/{_key(kid, same)}" if path else _key(kid, same)):
                return False
        return True

    walk(start, "")
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
