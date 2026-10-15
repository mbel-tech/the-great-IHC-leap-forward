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
import updates as UP                                        # noqa: E402

os.environ["no_proxy"] = os.environ["NO_PROXY"] = "127.0.0.1,localhost"

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

    # ------------------------------------------------------------ versions
    chk("v1 < v1.1", UP.parse_version("v1") < UP.parse_version("v1.1"), True)
    chk("v1.1 < v1.2.3", UP.parse_version("v1.1") < UP.parse_version("v1.2.3"), True)
    chk("v1.10 > v1.9", UP.parse_version("v1.10") > UP.parse_version("v1.9"), True)
    chk("v1.0 == v1", UP.parse_version("v1.0") == UP.parse_version("v1"), True)
    chk("nightly is not a version", UP.parse_version("nightly"), None)
    chk("v1-beta is not a version", UP.parse_version("v1-beta"), None)

    # ------------------------------------------------------------ deciding
    def url(path):
        return "file://" + os.path.abspath(path).replace(os.sep, "/")

    rel_dir = os.path.join(TMP, "release")
    src2 = fake_repo(os.path.join(TMP, "src2"))
    code_zip, atlas_zip, man = MU.build_package(src2, "1.3", rel_dir)
    manifest = json.load(open(man))
    release = {
        "tag_name": "v1.3", "body": "What changed", "html_url": "https://example/rel",
        "assets": [{"name": os.path.basename(p), "browser_download_url": url(p)}
                   for p in (code_zip, atlas_zip, man)]
                  + [{"name": "The-Great-IHC-Leap-Forward-Setup-1.3.exe",
                      "browser_download_url": "https://example/setup.exe"}],
    }
    same = {"version": "1.2", "runtime_fingerprint": manifest["runtime_fingerprint"],
            "atlas_fingerprint": manifest["atlas_fingerprint"]}

    d = UP.decide(dict(same, version="1.3"), release, manifest)
    chk("same version -> current", d.kind, "current")
    d = UP.decide(dict(same, version="2.0"), release, manifest)
    chk("older release -> current (never a downgrade)", d.kind, "current")
    d = UP.decide(same, release, manifest)
    chk("newer, same runtime -> code", d.kind, "code")
    chk("code: only the code package when the atlas is the same",
        [x["name"] for x in d.downloads], [os.path.basename(code_zip)])
    chk("code: notes and version", (d.version, d.notes), ("1.3", "What changed"))
    d = UP.decide(dict(same, atlas_fingerprint="old"), release, manifest)
    chk("atlas changed -> both packages", len(d.downloads), 2)
    d = UP.decide(dict(same, runtime_fingerprint="old"), release, manifest)
    chk("runtime changed -> installer", d.kind, "installer")
    chk("installer url", d.installer_url, "https://example/setup.exe")
    d = UP.decide(same, release, None)
    chk("no manifest -> installer", d.kind, "installer")
    d = UP.decide(same, dict(release, tag_name="nightly"), manifest)
    chk("unversioned tag -> current", d.kind, "current")
    d = UP.decide(same, release, dict(manifest, version="1.4"))
    chk("manifest for another version -> installer", d.kind, "installer")

    t0 = __import__("time").time()
    try:
        UP.fetch_latest(timeout=5, url="http://127.0.0.1:9/")
        chk("offline -> UpdateError", "no error", "UpdateError")
    except UP.UpdateError:
        chk("offline -> UpdateError", "UpdateError", "UpdateError")
    chk("offline fails fast", __import__("time").time() - t0 < 6, True)
    chk("fetch_manifest from the release", UP.fetch_manifest(release)["version"], "1.3")

    # ------------------------------------------------------------ staging
    def fake_install(path, version="1.2"):
        fake_repo(path)
        os.makedirs(os.path.join(path, "runtime"), exist_ok=True)
        open(os.path.join(path, "runtime", "python.exe"), "w").close()
        with open(os.path.join(path, "VERSION.json"), "w") as fh:
            json.dump(dict(same, version=version), fh)
        return path

    inst = fake_install(os.path.join(TMP, "install"))
    d = UP.decide(UP.installed_info(inst), release, manifest)
    new = UP.stage(inst, d)
    chk("staged into updates/new-1.3", os.path.relpath(new, inst).replace(os.sep, "/"),
        "updates/new-1.3")
    chk("staged app/__main__.py", os.path.exists(os.path.join(new, "app", "__main__.py")), True)
    chk("staged VERSION.json", UP.installed_info(new)["version"], "1.3")
    chk("staged leaves the live install alone",
        UP.installed_info(inst)["version"], "1.2")

    bad = UP.decide(UP.installed_info(inst), release,
                    dict(manifest, packages=dict(manifest["packages"],
                         code=dict(manifest["packages"]["code"], sha256="0" * 64))))
    try:
        UP.stage(inst, bad)
        chk("bad hash refused", "staged", "UpdateError")
    except UP.UpdateError:
        chk("bad hash refused", "UpdateError", "UpdateError")
    left = sorted(os.listdir(os.path.join(inst, "updates")))
    chk("bad hash leaves no new-* or download", [n for n in left if n.startswith("new-")
                                                  or n == "download"], [])

    other_dir = os.path.join(TMP, "release-other")
    oc, oa, om = MU.build_package(src2, "1.4", other_dir)
    wrong = dict(release, assets=[{"name": os.path.basename(code_zip),
                                    "browser_download_url": url(oc)}])
    om_d = json.load(open(om))
    wrong_manifest = dict(manifest, packages={"code": dict(om_d["packages"]["code"],
                                                          name=os.path.basename(code_zip))})
    try:
        UP.stage(inst, UP.decide(UP.installed_info(inst), wrong, wrong_manifest))
        chk("package for another version refused", "staged", "UpdateError")
    except UP.UpdateError:
        chk("package for another version refused", "UpdateError", "UpdateError")

    os.makedirs(os.path.join(inst, "updates"), exist_ok=True)
    with open(os.path.join(inst, "updates", "last-result.txt"), "w") as fh:
        fh.write("Updated to 1.3")
    chk("last_result read once", UP.last_result(inst), "Updated to 1.3")
    chk("last_result then gone", UP.last_result(inst), None)
    chk("source tree is not an install", UP.installed_info(REPO) is None
        or not os.path.exists(os.path.join(REPO, "runtime", "python.exe")), True)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print()
print("PASS" if not failures else f"FAIL: {len(failures)}: {failures}")
sys.exit(1 if failures else 0)
