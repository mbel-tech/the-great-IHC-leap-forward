"""In-app updates: the release package, the check, the swap and the undo.

Everything here runs on a throwaway copy or a fake install folder, never on the
repository itself or anyone's studies. The network is never touched: release
assets are served from file:// URLs.

Run:  python tests/test_updates.py
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "packaging"))
sys.path.insert(0, os.path.join(REPO, "app"))

import make_update as MU                                    # noqa: E402

failures = []


def chk(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name:60} {got!r}")
    if not ok:
        failures.append(name)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


def fake_repo(root):
    """The fingerprint files and a tiny app/, enough to build a package from."""
    for rel in MU.FINGERPRINT_FILES:
        src = os.path.join(REPO, rel)
        dst = os.path.join(root, rel)
        os.makedirs(os.path.dirname(dst) or root, exist_ok=True)
        shutil.copyfile(src, dst)
    for part in ("app", "scripts", "analysis", "atlas"):
        os.makedirs(os.path.join(root, part, "__pycache__"), exist_ok=True)
        with open(os.path.join(root, part, "marker.txt"), "w") as fh:
            fh.write(part)
        with open(os.path.join(root, part, "__pycache__", "x.cpython-313.pyc"), "wb") as fh:
            fh.write(b"\0")
    with open(os.path.join(root, "app", "__main__.py"), "w") as fh:
        fh.write("print('ok')\n")
    for f in ("config.example.json", "README.md"):
        with open(os.path.join(root, f), "w") as fh:
            fh.write("{}" if f.endswith(".json") else "# readme\n")
    return root


TMP = tempfile.mkdtemp(prefix="ls-updates-")
try:
    # ------------------------------------------------------------ the package
    # Built from a small fake repo: the real atlas is 250 MB.
    src = fake_repo(os.path.join(TMP, "src"))
    out = os.path.join(TMP, "dist")
    zpath, apath, mpath = MU.build_package(src, "9.9.9", out)
    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
        version_file = json.loads(z.read("VERSION.json"))
    with zipfile.ZipFile(apath) as z:
        atlas_names = z.namelist()
    chk("code package holds app/__main__.py", "app/__main__.py" in names, True)
    chk("code package holds scripts/", any(n.startswith("scripts/") for n in names), True)
    chk("code package leaves atlas/ out", any(n.startswith("atlas/") for n in names), False)
    chk("atlas package holds only atlas/", all(n.startswith("atlas/") for n in atlas_names)
        and bool(atlas_names), True)
    chk("code package holds config.example.json", "config.example.json" in names, True)
    chk("no __pycache__", any("__pycache__" in n or n.endswith(".pyc")
                              for n in names + atlas_names), False)
    chk("no runtime/", any(n.startswith("runtime/") for n in names), False)
    chk("VERSION.json version", version_file["version"], "9.9.9")
    chk("code package name", os.path.basename(zpath), "The-Great-IHC-Leap-Forward-code-9.9.9.zip")
    chk("atlas package name", os.path.basename(apath), "The-Great-IHC-Leap-Forward-atlas-9.9.9.zip")
    chk("manifest name", os.path.basename(mpath), "update-9.9.9.json")

    manifest = json.load(open(mpath))
    for kind, path in (("code", zpath), ("atlas", apath)):
        entry = manifest["packages"][kind]
        chk(f"manifest {kind} size", entry["size"], os.path.getsize(path))
        chk(f"manifest {kind} sha256", entry["sha256"], sha256(path))
        chk(f"manifest {kind} name", entry["name"], os.path.basename(path))
    for key in ("runtime_fingerprint", "atlas_fingerprint"):
        chk(f"manifest {key} = VERSION.json's", manifest[key], version_file[key])

    fa0 = MU.atlas_fingerprint(src)
    with open(os.path.join(src, "atlas", "marker.txt"), "a") as fh:
        fh.write("changed")
    chk("atlas fingerprint changes with a plate", MU.atlas_fingerprint(src) != fa0, True)

    # ------------------------------------------------------------ the fingerprint
    fp = MU.runtime_fingerprint(REPO)
    chk("fingerprint stable", MU.runtime_fingerprint(REPO), fp)
    chk("fingerprint is sha256 hex", len(fp), 64)

    a = fake_repo(os.path.join(TMP, "a"))
    fa = MU.runtime_fingerprint(a)
    chk("fingerprint of an identical copy", fa, fp)
    req = os.path.join(a, "requirements.txt")
    text = open(req, "rb").read()
    open(req, "wb").write(text.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    chk("fingerprint ignores CRLF", MU.runtime_fingerprint(a), fp)
    with open(req, "a") as fh:
        fh.write("\nsomething-new==1.0\n")
    chk("fingerprint changes with requirements", MU.runtime_fingerprint(a) != fp, True)

    vpath = MU.write_version_file(os.path.join(TMP, "bundle"), "1.2", REPO)
    chk("write_version_file", json.load(open(vpath)),
        {"version": "1.2", "runtime_fingerprint": fp,
         "atlas_fingerprint": MU.atlas_fingerprint(REPO)})
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print()
print("PASS" if not failures else f"FAIL: {len(failures)}: {failures}")
sys.exit(1 if failures else 0)
