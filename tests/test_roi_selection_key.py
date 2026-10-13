"""The ROI selection reaches disk: its key is mirrored, and its seed is wired.

The curator keeps the operator's choice of ROIs in a localStorage key of its own.
In the app a key is only carried to disk (and back into the next session) if it
is listed in app/state.py KEYS, which is how a setting that works in a browser
silently resets on every app launch. And the page can only be seeded from the
curation folder if 04l fills a placeholder for it.

app/state.py imports Qt, so it is read as text rather than imported.

Run:  python tests/test_roi_selection_key.py
"""

import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
failures = []


def chk(label, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + label.ljust(64) + " " + repr(got))
    if not ok:
        print("     want " + repr(want))
        failures.append(label)


page = open(os.path.join(REPO, "scripts", "04l_roi_curator.py"), encoding="utf-8").read()
state = open(os.path.join(REPO, "app", "state.py"), encoding="utf-8").read()

keys = None
for node in ast.parse(state).body:
    if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "KEYS" for t in node.targets):
        keys = [e.value for e in node.value.elts]

m = re.search(r'const SEL_KEY = "([^"]+)";', page)
chk("the page declares its selection key", bool(m), True)
key = m.group(1)

chk("app/state.py KEYS is a list of strings", isinstance(keys, list) and all(isinstance(k, str) for k in keys), True)
chk("the selection key is mirrored to disk by the app", key in keys, True)
chk("...and listed once", keys.count(key), 1)
chk("the curation store key is a different key", "ls_roi_curator_v1" != key, True)

chk("the page reads its seed from a placeholder", len(re.findall(r"const SEL_SEED = __ROISEL__;", page)), 1)
chk("04l fills that placeholder when it builds the page", len(re.findall(r'"__ROISEL__": roisel', page)), 1)
chk("the seed file is the key's own file under curation/", f'"{key}.json"' in page, True)
chk("--no-seed leaves the seed out, like the curation seed", "if not args.no_seed:" in page.split("roisel = None")[1][:80], True)

print()
print("FAILED: " + ", ".join(failures) if failures else "ALL PASS")
sys.exit(1 if failures else 0)
