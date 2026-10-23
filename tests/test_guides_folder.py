"""The guides live in docs/guides/, and nothing still points at their old places.

Installed apps open docs/user-guide.md on GitHub from Help, so each old path
keeps a one-line pointer to the new one. Every picture a guide shows must
resolve relative to the guide itself, and no code, test, workflow or page may
name the old paths except the pointers and the dated plans and logs. Runs under
pytest or on its own.
"""
import os
import re
import subprocess
import sys

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DOCS = os.path.join(REPO, "docs")
GUIDES = os.path.join(DOCS, "guides")
MOVED = ("user-guide", "pipeline-guide", "pipeline-methods")
IMAGE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")
# Dated records describe the repo as it was; rewriting them would falsify them.
HISTORY = ("docs/superpowers/", "LOGS.md", "docs/czi-reading-audit.md", "docs/user-guide.md",
           "docs/pipeline-guide.md", "docs/pipeline-methods.md",
           "tests/test_guides_folder.py")


def test_pointers_exist():
    for name in MOVED:
        assert os.path.isfile(os.path.join(GUIDES, name + ".md")), name
        with open(os.path.join(DOCS, name + ".md"), encoding="utf-8") as f:
            assert f"guides/{name}.md" in f.read(), name


def test_guide_images_resolve():
    missing = []
    for fn in sorted(os.listdir(GUIDES)):
        if not fn.endswith(".md"):
            continue
        with open(os.path.join(GUIDES, fn), encoding="utf-8") as f:
            text = f.read()
        for target in IMAGE.findall(text):
            if re.match(r"[a-z]+:", target):
                continue
            path = os.path.join(GUIDES, target)
            # A git-ignored picture (img/workflow.png) is drawn by the build,
            # not committed, so a fresh clone cannot have it.
            ignored = subprocess.run(["git", "check-ignore", "-q", path],
                                     cwd=REPO).returncode == 0
            if not ignored and not os.path.isfile(path):
                missing.append(f"{fn}: {target}")
    assert missing == []


def test_no_stale_refs():
    out = subprocess.run(
        ["git", "grep", "-n", "-E",
         r"docs/img/|docs/(user-guide|pipeline-guide|pipeline-methods)\.md"],
        cwd=REPO, capture_output=True, text=True).stdout
    stale = [ln for ln in out.splitlines() if not ln.startswith(HISTORY)]
    assert stale == [], "\n".join(stale)


if __name__ == "__main__":
    bad = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print("PASS", name)
            except AssertionError as e:
                bad += 1
                print("FAIL", name, e)
    sys.exit(1 if bad else 0)
