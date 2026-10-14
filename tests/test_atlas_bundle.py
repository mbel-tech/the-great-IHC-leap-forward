"""The atlas plates shipped in atlas/ reach a study that has none.

Run:  python tests/test_atlas_bundle.py
"""

import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "scripts"))

import ls_atlas as AT  # noqa: E402
import ls_config as LC  # noqa: E402

FAILS = []


def chk(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"\n     got {got!r}\n     want {want!r}"))
    if not ok:
        FAILS.append(name)


def listing(d):
    return sorted(f for f in os.listdir(d) if not f.startswith("fingerprints"))


tmp = tempfile.mkdtemp()
try:
    # A small stand-in for atlas/, so this checks the mechanism, not 190 MB.
    bundle = os.path.join(tmp, "bundle")
    os.makedirs(os.path.join(bundle, "plates_final"))
    for f in ("plates.csv", "plate_001_p01.png"):
        with open(os.path.join(bundle, "plates_final", f), "wb") as fh:
            fh.write(b"x" * 10)
    os.environ["LS_ATLAS_BUNDLE"] = bundle

    out = os.path.join(tmp, "out")
    os.makedirs(out)
    cfg = {"out_root": out, "atlas_plate_set": {"dir": "plates_final"}}
    d = AT.plate_dir(cfg)
    chk("a missing set is copied in on first use", listing(d), ["plate_001_p01.png", "plates.csv"])
    chk("no partial copy is left behind", os.path.exists(d + ".copying"), False)

    with open(os.path.join(d, "plate_001_p01.png"), "wb") as fh:
        fh.write(b"mine")
    AT.plate_dir(cfg)
    with open(os.path.join(d, "plate_001_p01.png"), "rb") as fh:
        chk("a study's own set is never overwritten", fh.read(), b"mine")

    chk("a set the bundle lacks is left missing",
        os.path.exists(AT.plate_dir(cfg, set_name="plates_merged")), False)

    ghost = os.path.join(tmp, "no_such_out_root")
    AT.plate_dir({"out_root": ghost, "atlas_plate_set": {"dir": "plates_final"}})
    chk("an out_root that does not exist is not created", os.path.exists(ghost), False)

    os.environ["LS_ATLAS_BUNDLE"] = ""
    out2 = os.path.join(tmp, "out2")
    os.makedirs(out2)
    AT.plate_dir({"out_root": out2, "atlas_plate_set": {"dir": "plates_final"}})
    chk("LS_ATLAS_BUNDLE empty turns copying off", os.listdir(out2), [])
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# The shipped files themselves.
chk("the salmon PDF ships", os.path.isfile(LC.BUNDLED_ATLAS_PDF), True)
chk("an empty atlas_pdf falls back to it",
    LC.apply_defaults({"atlas_pdf": ""})["atlas_pdf"], LC.BUNDLED_ATLAS_PDF)
chk("a configured atlas_pdf is kept",
    LC.apply_defaults({"atlas_pdf": "/x/a.pdf"})["atlas_pdf"], "/x/a.pdf")
final = os.path.join(AT.BUNDLED, "plates_final")
chk("plates_final ships 64 plates",
    len([f for f in os.listdir(final) if f.endswith(".png")]), 64)
chk("atlas/README.md names Marco A. Vindas",
    "Marco A. Vindas" in open(os.path.join(AT.BUNDLED, "README.md"), encoding="utf-8").read(), True)

print(f"\n{len(FAILS)} failed" if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
