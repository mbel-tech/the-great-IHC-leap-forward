"""The atlas registry: what each atlas is, and that no stage compares a name.

Two things are pinned. First, that routing the two built-in atlases through the
registry changed nothing they used to answer - the folder names are where a
study's existing curation lives, so a changed string is silent data loss. Second,
that a third atlas works with no edit to any stage, which is the whole point of
the registry and which the old `== WULLIMANN` comparisons made impossible.

Run:  python tests/test_atlas_registry.py
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "scripts"))

failures = []


def chk(label, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + label.ljust(66) + " " + repr(got))
    if not ok:
        print("     want " + repr(want))
        failures.append(label)


def raises(fn, needle=""):
    try:
        fn()
    except ValueError as e:
        return needle in str(e)
    return False


import atlas_registry as REG                                    # noqa: E402
import ls_atlas as AT                                           # noqa: E402

# ---- the two built-ins answer exactly what they always did -------------------

chk("built-ins, in order", list(REG.entries()), ["salmon", "wullimann1996"])
chk("ls_atlas.SOURCES is the built-ins", AT.SOURCES, ("salmon", "wullimann1996"))
chk("the old module constants survive", (AT.SALMON, AT.WULLIMANN, AT.WULLIMANN_SET),
    ("salmon", "wullimann1996", "plates_wullimann"))
chk("default atlas is salmon", AT.source({}), "salmon")
chk("salmon plate set is the study's own choice (default plates_final)", AT.set_name({}), "plates_final")
chk("...and follows atlas_plate_set.dir",
    AT.set_name({"atlas_plate_set": {"dir": "plates_final"}}), "plates_final")
chk("...a malformed block is judged, not crashed on",
    AT.set_name({"atlas_plate_set": "oops"}), "plates_final")
chk("salmon reformatted folder unchanged", AT.reformatted_dir({}), "plates")
chk("wullimann plate set is fixed",
    AT.set_name({"atlas_source": "wullimann1996", "atlas_plate_set": {"dir": "plates_final"}}),
    "plates_wullimann")
chk("wullimann reformatted folder unchanged",
    AT.reformatted_dir({"atlas_source": "wullimann1996"}), "plates_wullimann")
chk("wullimann plate_dir unchanged",
    AT.plate_dir({"out_root": "/o", "atlas_source": "wullimann1996"}).replace("\\", "/"),
    "/o/atlas/plates_wullimann")
chk("salmon: no micrograph half, no mirror",
    (AT.entry({}).micrograph_half, AT.entry({}).mirror_allowed), (False, False))
chk("wullimann: micrograph half and mirror",
    (AT.entry({"atlas_source": "wullimann1996"}).micrograph_half,
     AT.entry({"atlas_source": "wullimann1996"}).mirror_allowed), (True, True))
chk("an unknown atlas still raises, listing the valid ones",
    raises(lambda: AT.source({"atlas_source": "zebra"}), "salmon, wullimann1996"), True)

# ---- a third atlas needs no edit to any stage -------------------------------

CUSTOM = {"atlas_source": "mouse_brain",
          "atlases": {"mouse_brain": {"label": "Mouse brain", "set_dir": "plates_mouse",
                                      "region_kind": "polygons", "mirror_allowed": True}}}
chk("a declared atlas is listed after the built-ins", AT.names(CUSTOM),
    ("salmon", "wullimann1996", "mouse_brain"))
chk("...and can be the study's atlas", AT.source(CUSTOM), "mouse_brain")
chk("...with its own plate set", AT.set_name(CUSTOM), "plates_mouse")
chk("...which atlas_plate_set cannot override",
    AT.set_name(dict(CUSTOM, atlas_plate_set={"dir": "plates_final"})), "plates_mouse")
chk("...its own reformatted folder", AT.reformatted_dir(CUSTOM), "plates_mouse")
chk("...under atlas/",
    AT.plate_dir(dict(CUSTOM, out_root="/o")).replace("\\", "/"), "/o/atlas/plates_mouse")
chk("...and the capabilities it declared",
    (AT.entry(CUSTOM).region_kind, AT.entry(CUSTOM).mirror_allowed,
     AT.entry(CUSTOM).micrograph_half), ("polygons", True, False))
chk("...which is not a built-in", AT.entry(CUSTOM).builtin, False)
chk("the built-ins keep working beside it",
    AT.set_name({"atlases": CUSTOM["atlases"], "atlas_source": "wullimann1996"}), "plates_wullimann")

# ---- a bad declaration raises ----------------------------------------------


def bad(**spec_over):
    spec = {"set_dir": "plates_x", "region_kind": "seeds"}
    spec.update(spec_over)
    return {"atlases": {"x_atlas": spec}}


chk("a name that shadows a built-in is refused",
    raises(lambda: REG.entries({"atlases": {"salmon": {"set_dir": "plates_x", "region_kind": "seeds"}}}),
           "built-in"), True)
chk("a bad name is refused",
    raises(lambda: REG.entries({"atlases": {"Bad Name": {"set_dir": "plates_x", "region_kind": "seeds"}}}),
           "lower-case"), True)
chk("a built-in's plate folder is refused", raises(lambda: REG.entries(bad(set_dir="plates_final")), "collide"), True)
chk("the wullimann plate folder is refused", raises(lambda: REG.entries(bad(set_dir="plates_wullimann")), "collide"), True)
chk("a path in set_dir is refused", raises(lambda: REG.entries(bad(set_dir="../x")), "plain folder"), True)
chk("a nested path in set_dir is refused", raises(lambda: REG.entries(bad(set_dir="a/b")), "plain folder"), True)
chk("a missing set_dir is refused", raises(lambda: REG.entries({"atlases": {"x_atlas": {"region_kind": "seeds"}}}), "set_dir"), True)
chk("an unknown region_kind is refused", raises(lambda: REG.entries(bad(region_kind="blobs")), "region_kind"), True)
chk("an unknown field is refused", raises(lambda: REG.entries(bad(colour="red")), "unknown field"), True)
chk("a _note field is allowed", raises(lambda: REG.entries(bad(_note="why")), ""), False)
chk("mirroring needs polygons", raises(lambda: REG.entries(bad(mirror_allowed=True)), "polygon"), True)
chk("a flag must be a boolean", raises(lambda: REG.entries(bad(micrograph_half="yes")), "true or false"), True)
chk("atlases must be an object", raises(lambda: REG.entries({"atlases": ["a"]}), "object"), True)
chk("two atlases cannot share a folder",
    raises(lambda: REG.entries({"atlases": {"a_one": {"set_dir": "plates_x", "region_kind": "seeds"},
                                            "a_two": {"set_dir": "plates_x", "region_kind": "seeds"}}}),
           "share the folder"), True)
chk("an empty or absent block is just the built-ins",
    (list(REG.entries({"atlases": {}})), list(REG.entries({"atlases": None}))),
    (["salmon", "wullimann1996"], ["salmon", "wullimann1996"]))
chk("an unknown atlas_source still raises with a custom one declared",
    raises(lambda: AT.source(dict(CUSTOM, atlas_source="nope")), "mouse_brain"), True)

# ---- the config schema offers the same list --------------------------------

import ls_config as LC                                          # noqa: E402

key = [k for k in LC.SPEC if k.path == "atlas_source"][0]
chk("the settings dropdown is the registry's built-ins", list(key.choices), list(REG.BUILTIN))
chk("ls_config.ATLAS_SOURCES is the same list", list(LC.ATLAS_SOURCES), list(REG.BUILTIN))

# ---- no stage compares an atlas name any more ------------------------------
#
# The point of the registry. `== AT.WULLIMANN` anywhere outside ls_atlas is the
# next atlas's bug waiting; 04w and 04x are the Wullimann builders and may name
# their own set.
pattern = re.compile(r"[!=]=\s*(?:AT\.)?WULLIMANN\b|\bWULLIMANN\s*[!=]=")
offenders = []
for folder in ("scripts", "app"):
    for fn in sorted(os.listdir(os.path.join(REPO, folder))):
        if not fn.endswith(".py") or fn in ("ls_atlas.py", "atlas_registry.py"):
            continue
        with open(os.path.join(REPO, folder, fn), encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                if pattern.search(line) and not line.lstrip().startswith("#"):
                    offenders.append("%s/%s:%d" % (folder, fn, n))
chk("no stage compares an atlas name", offenders, [])

print()
print("FAILED: " + ", ".join(failures) if failures else "ALL PASS")
sys.exit(1 if failures else 0)
