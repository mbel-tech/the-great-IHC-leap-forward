"""Generate the tables in docs/guides/technical-guide.md from the code.

Three tables are kept in the guide between marker comments, so the prose around
them is written by hand and the facts inside them cannot drift:

  stages    every stage in app/stages.py, in run order: id, script, group,
            what it needs, what it leaves behind, which reader decodes pixels
  options   every script's command-line options, read from its argparse calls
            (and the few flags read straight off sys.argv) without importing
            it - a stage reads config.json at import time, and a doc build
            must not need a study
  files     every file in docs/guides/file_formats.json, with its writer, the
            scripts that name it, and every column with type, units, meaning

The markers are `<!-- tables:<key> -->` and `<!-- /tables:<key> -->`. Run this
after changing a stage, an option or a file; tests/test_technical_guide.py
fails until you do.

    python docs/make_technical_tables.py          rewrite the guide in place
    python docs/make_technical_tables.py --check  exit 1 if it is out of date
"""

import ast
import json
import os
import re
import sys
import warnings

# Parsing a script must not print its own SyntaxWarnings (a regex in a page).
warnings.filterwarnings("ignore", category=SyntaxWarning)

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SCRIPTS = os.path.join(REPO, "scripts")
GUIDE = os.path.join(HERE, "guides", "technical-guide.md")
REGISTRY = os.path.join(HERE, "guides", "file_formats.json")

KEYS = ("stages", "options", "files")
SYS_ARGV_FLAG = re.compile(r"""["'](--[a-z0-9-]+)["']\s+in\s+sys\.argv""")


def cell(text):
    """One Markdown table cell: no pipes, no line breaks."""
    text = re.sub(r"\s+", " ", str(text)).strip()
    return text.replace("|", "\\|")


def table(header, rows, widths=None):
    """A pipe table. `widths` are relative column widths: pandoc sizes a wide
    table's columns by the dashes under the header, so a DOCX or PDF build
    gives the long columns room. GitHub ignores them."""
    widths = widths or [1] * len(header)
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join("-" * (3 * w) for w in widths) + "|"]
    out += ["| " + " | ".join(cell(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def script_names():
    """Every file in scripts/ the guide gives an entry, sorted."""
    return sorted(f for f in os.listdir(SCRIPTS)
                  if os.path.isfile(os.path.join(SCRIPTS, f))
                  and f.endswith((".py", ".groovy", ".sh")))


# ------------------------------------------------------------------- stages

def _stages():
    sys.path.insert(0, REPO)
    try:
        from app import stages as S
    finally:
        sys.path.remove(REPO)
    return S


def stages_table():
    S = _stages()
    rows = []
    for s in S.STAGES:
        if callable(s.cli_only):
            app = "from source only (needs StarDist)"
        elif s.cli_only:
            app = "no: " + s.cli_only
        else:
            app = "yes"
        outs = ", ".join(f"`{o}`" for o in s.output_names()) or "none"
        who = "person" if s.operator else ""
        rows.append([f"`{s.sid}`", s.title, s.group,
                     f"`{s.script}`" if s.script else "", ", ".join(s.needs),
                     outs, s.reader, who, app])
    return table(["Id", "Title", "Group", "Script", "Needs", "Leaves behind",
                  "Reader", "Decides", "In the app"], rows,
                 widths=[4, 4, 3, 5, 4, 7, 3, 2, 2])


# ------------------------------------------------------------------ options

def _constants(tree):
    """Module-level names bound to a literal: `DEFAULT_UM_PX = 2.6`."""
    env = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            try:
                env[node.targets[0].id] = ast.literal_eval(node.value)
            except (ValueError, TypeError, SyntaxError):
                pass
    return env


def _src(text, node, env=None):
    """A default or a help string as a reader should see it.

    A literal is itself; a name bound to a literal at module level is that
    literal; an f-string keeps its words and shows each field as its value
    when that is a known literal, else as `<expression>`. Anything else is
    the source text.
    """
    env = env or {}
    if isinstance(node, ast.Name) and node.id in env:
        val = env[node.id]
        return val if isinstance(val, str) else repr(val)
    if isinstance(node, ast.JoinedStr):
        out = []
        for part in node.values:
            if isinstance(part, ast.Constant):
                out.append(str(part.value))
            else:
                inner = part.value
                if isinstance(inner, ast.Name) and inner.id in env:
                    out.append(str(env[inner.id]))
                else:
                    out.append("<%s>" % ast.get_source_segment(text, inner))
        return "".join(out)
    seg = ast.get_source_segment(text, node)
    try:
        val = ast.literal_eval(node)
        return val if isinstance(val, str) else repr(val)
    except (ValueError, TypeError, SyntaxError):
        return seg


def script_options(path):
    """`[(flags, default, help)]` for one script, in source order."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if not path.endswith(".py"):
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    env = _constants(tree)
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"):
            continue
        flags = [a.value for a in node.args
                 if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        default = ""
        if "default" in kw:
            default = _src(text, kw["default"], env)
        elif "action" in kw and _src(text, kw["action"]) == "store_true":
            default = "off"
        hlp = _src(text, kw["help"], env) if "help" in kw else ""
        if "choices" in kw:
            hlp = (hlp + " " if hlp else "") + "Choices: " + _src(text, kw["choices"], env) + "."
        out.append((node.lineno, ", ".join(flags), default, hlp))
    out.sort()
    seen = {o[1] for o in out}
    for m in SYS_ARGV_FLAG.finditer(text):
        if m.group(1) not in seen:
            seen.add(m.group(1))
            out.append((0, m.group(1), "off", "read straight off the command line"))
    return [o[1:] for o in out]


def options_table():
    rows = []
    for name in script_names():
        opts = script_options(os.path.join(SCRIPTS, name))
        if not opts:
            rows.append([f"`{name}`", "(none)", "", ""])
        for flags, default, hlp in opts:
            rows.append([f"`{name}`", f"`{flags}`",
                         f"`{default}`" if default not in ("", "off") else default, hlp])
    return table(["Script", "Option", "Default", "What it does"], rows,
                 widths=[4, 4, 3, 9])


# -------------------------------------------------------------------- files

def load_registry():
    with open(REGISTRY, encoding="utf-8") as fh:
        return json.load(fh)


def code_columns(spec):
    """The column list a writer declares as a module constant: `script.py:NAME`.

    Evaluated from the source, not imported, and only for list literals,
    tuples, names already seen and `+` - which is how every such constant in
    scripts/ is written.
    """
    script, name = spec.split(":")
    with open(os.path.join(SCRIPTS, script), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    env = {}

    def ev(node):
        if isinstance(node, (ast.List, ast.Tuple)):
            vals = []
            for e in node.elts:
                vals += ev(e.value) if isinstance(e, ast.Starred) else [ev(e)]
            return vals
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return env[node.id]
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            # OV.SAT_RAW_KEYS: another script's constant, resolved by the caller
            raise KeyError(node.attr)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return list(ev(node.left)) + list(ev(node.right))
        raise ValueError(ast.dump(node))

    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            try:
                env[node.targets[0].id] = ev(node.value)
            except (KeyError, ValueError):
                pass
    if name not in env:
        raise KeyError(f"{spec}: no list constant of that name")
    return [str(c) for c in env[name]]


def mentions(basename, writer):
    """Scripts other than the writer that name this file, by its base name."""
    stem = re.escape(basename).replace(r"\{marker\}", r"[^\"']*")
    pat = re.compile(r"""["'/]""" + stem + r"""["']""")
    out = []
    for name in script_names():
        if name == writer:
            continue
        with open(os.path.join(SCRIPTS, name), encoding="utf-8") as fh:
            if pat.search(fh.read()):
                out.append(name)
    return out


def files_table():
    reg = load_registry()
    parts = []
    for entry in reg["files"]:
        cols = entry.get("columns", [])
        readers = entry.get("readers")
        if readers is None:
            readers = (mentions(entry["names"][0], entry["writer"].split()[0])
                       if entry["names"] else [])
        head = (f"#### `{entry['path']}`\n\n{entry['what']}\n\n"
                f"- **Written by:** {entry['writer']}\n"
                f"- **Read by:** {', '.join(readers) if readers else 'a person'}\n")
        if entry.get("format"):
            head += f"- **Format:** {entry['format']}\n"
        if cols:
            head += "\n" + table(["Column", "Type", "Units", "Meaning"],
                                 [[f"`{c['name']}`", c["type"], c.get("units", ""),
                                   c["meaning"]] for c in cols],
                                 widths=[4, 2, 2, 9]) + "\n"
        parts.append(head)
    return "\n".join(parts).rstrip()


# ------------------------------------------------------------------- splice

def build_tables():
    return {"stages": stages_table(), "options": options_table(),
            "files": files_table()}


def splice(doc, tables):
    for key, body in tables.items():
        start, end = f"<!-- tables:{key} -->", f"<!-- /tables:{key} -->"
        if start not in doc or end not in doc:
            raise KeyError(f"the guide has no {start} ... {end} block")
        before, rest = doc.split(start, 1)
        _old, after = rest.split(end, 1)
        doc = f"{before}{start}\n{body}\n{end}{after}"
    return doc


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    with open(GUIDE, encoding="utf-8") as fh:
        doc = fh.read()
    new = splice(doc, build_tables())
    if "--check" in argv:
        if new != doc:
            print("technical-guide.md is out of date: run python docs/make_technical_tables.py")
            return 1
        return 0
    if new != doc:
        with open(GUIDE, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(new)
        print("technical-guide.md tables rewritten")
    return 0


if __name__ == "__main__":
    sys.exit(main())
