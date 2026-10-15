"""Every link between wiki pages, and every picture, must resolve.

The wiki is published from docs/wiki/ by .github/workflows/wiki.yml. A page
links to another by its name without .md, as GitHub wikis do: [Measure](Measure)
or [Measure](Measure#a-heading). Runs under pytest or on its own.
"""
import os
import re
import sys

WIKI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "wiki")
# Pictures live in docs/img/user-guide/ and are copied to the wiki's images/ folder
# when it is published, so a page's images/<name> resolves there (or in docs/wiki/images/).
SHARED = os.path.join(WIKI, "..", "img", "user-guide")
LINK = re.compile(r"(!?)\[[^\]]*\]\(([^)\s]+)\)")


def _slug(heading):
    s = heading.strip().lower()
    s = re.sub(r"[^\w\- ]", "", s)
    return s.replace(" ", "-")


def _anchors(page):
    with open(os.path.join(WIKI, page + ".md"), encoding="utf-8") as f:
        return {_slug(m.group(1)) for m in re.finditer(r"^#+\s+(.*)$", f.read(), re.M)}


def problems():
    pages = {f[:-3] for f in os.listdir(WIKI) if f.endswith(".md")}
    found = []
    for page in sorted(pages - {"README"}):
        with open(os.path.join(WIKI, page + ".md"), encoding="utf-8") as f:
            text = re.sub(r"```.*?```", "", f.read(), flags=re.S)
        for bang, target in LINK.findall(text):
            if re.match(r"[a-z]+:", target) or target.startswith("#"):
                continue
            if bang:
                name = os.path.basename(target)
                if not (os.path.isfile(os.path.join(WIKI, target))
                        or os.path.isfile(os.path.join(SHARED, name))):
                    found.append(f"{page}: missing picture {target}")
                continue
            name, _, anchor = target.partition("#")
            if name not in pages:
                found.append(f"{page}: link to missing page {name}")
            elif anchor and anchor not in _anchors(name):
                found.append(f"{page}: no heading #{anchor} on {name}")
    return found


def test_wiki_links_resolve():
    assert problems() == []


def test_every_page_is_in_the_sidebar():
    with open(os.path.join(WIKI, "_Sidebar.md"), encoding="utf-8") as f:
        sidebar = f.read()
    pages = {f[:-3] for f in os.listdir(WIKI) if f.endswith(".md") and not f.startswith("_")}
    missing = [p for p in pages - {"README"} if f"]({p})" not in sidebar]
    assert missing == []


if __name__ == "__main__":
    bad = problems()
    print("\n".join(bad) or "wiki links OK")
    sys.exit(1 if bad else 0)
