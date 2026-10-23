"""The technical guide agrees with the code.

Its stage, option and file tables are generated (docs/make_technical_tables.py)
and must match what the generator makes today; every script has an entry; every
CSV a script names is in the file registry; and every column the registry lists
is one the writer's code still spells. A renamed column, a new option, a new
stage or a new script fails here until the guide is brought up to date. Runs
under pytest or on its own.
"""
import os
import re
import sys

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(REPO, "docs"))
import make_technical_tables as T  # noqa: E402

CSV_NAME = re.compile(r"""["']([A-Za-z0-9_{}./-]*[A-Za-z0-9_}]\.csv)["']""")


def _doc():
    with open(T.GUIDE, encoding="utf-8") as fh:
        return fh.read()


def _registered():
    names = set()
    for entry in T.load_registry()["files"]:
        names.update(entry["names"])
    return names


def test_tables_current():
    doc = _doc()
    assert T.splice(doc, T.build_tables()) == doc, \
        "run python docs/make_technical_tables.py"


def test_splice_refuses_bad_markers():
    body = {"stages": "X"}
    for doc in ("a <!-- tables:stages --> b <!-- /tables:stages --> "
                "c <!-- tables:stages --> d <!-- /tables:stages -->",
                "a <!-- /tables:stages --> b <!-- tables:stages -->",
                "no markers"):
        try:
            T.splice(doc, body)
        except KeyError:
            continue
        raise AssertionError("splice accepted: " + doc)


def test_every_script_has_entry():
    headings = set(re.findall(r"^### `([^`]+)`", _doc(), re.M))
    missing = [n for n in T.script_names() if n not in headings]
    assert missing == [], missing


def test_every_written_csv_registered():
    reg, ignore = _registered(), set(T.load_registry().get("not_files", {}))
    missing = set()
    for name in T.script_names():
        with open(os.path.join(T.SCRIPTS, name), encoding="utf-8") as fh:
            for m in CSV_NAME.finditer(fh.read()):
                base = os.path.basename(m.group(1))
                if base not in reg and base not in ignore:
                    missing.add(f"{name}: {base}")
    assert sorted(missing) == [], sorted(missing)


def test_columns_match_code():
    bad = []
    for entry in T.load_registry()["files"]:
        cols = [c["name"] for c in entry.get("columns", [])]
        if entry.get("columns_from"):
            code = T.code_columns(entry["columns_from"])
            if code != cols:
                bad.append(f"{entry['path']}: registry {cols} != code {code}")
            continue
        if not cols:
            continue
        src = ""
        for script in entry.get("column_sources", [entry["writer"].split()[0]]):
            with open(os.path.join(T.SCRIPTS, script), encoding="utf-8") as fh:
                src += fh.read()
        for c in entry.get("columns", []):
            literal = c.get("literal", c["name"])
            # The whole name, quoted: opened by a quote or by the } closing an
            # f-string field (f"{slug}_file"), closed by a quote or by the { of
            # one. Without the closing half, "edge_affinity_x" vouched for
            # "edge_affinity".
            if not re.search(r"""["'}]""" + re.escape(literal) + r"""["'{]""", src):
                bad.append(f"{entry['path']}: {c['name']} not in the writer's code")
    assert bad == [], ("\n".join(bad) + "\nThe registry is written by hand: fix "
                       "docs/guides/file_formats.json, then run "
                       "python docs/make_technical_tables.py")


if __name__ == "__main__":
    bad = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print("PASS", name)
            except (AssertionError, KeyError, FileNotFoundError) as e:
                bad += 1
                print("FAIL", name, str(e)[:2000])
    sys.exit(1 if bad else 0)
