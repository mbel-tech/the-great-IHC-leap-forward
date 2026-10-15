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
import update_apply as UA                                   # noqa: E402

os.environ["no_proxy"] = os.environ["NO_PROXY"] = "127.0.0.1,localhost"

failures = []

# What build.ps1 writes: pip freeze, then the interpreter and R.
BUNDLE_VERSIONS = "numpy==2.3.1\nPySide6==6.9.0\ntensorflow==2.20.0\npython==3.13.5\nR==4.5.1\n"


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
    """A tiny app/ and a BUNDLE_VERSIONS.txt, enough to build a package from."""
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "BUNDLE_VERSIONS.txt"), "w") as fh:
        fh.write(BUNDLE_VERSIONS)
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
    zpath, apath, mpath = MU.build_package(src, "9.9.9", out, src)
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
    # Of the libraries actually built into the runtime, not of the requirement
    # files: those say ">=", so two builds of the same files can differ.
    b1 = fake_repo(os.path.join(TMP, "b1"))
    fp = MU.runtime_fingerprint(b1)
    chk("fingerprint stable", MU.runtime_fingerprint(b1), fp)
    chk("fingerprint is sha256 hex", len(fp), 64)
    vf = os.path.join(b1, "BUNDLE_VERSIONS.txt")
    open(vf, "w", newline="\r\n").write("".join(reversed(BUNDLE_VERSIONS.splitlines(True))))
    chk("fingerprint ignores line order and CRLF", MU.runtime_fingerprint(b1), fp)
    open(vf, "w").write(BUNDLE_VERSIONS.replace("numpy==2.3.1", "numpy==2.4.0"))
    chk("fingerprint changes with a library version", MU.runtime_fingerprint(b1) != fp, True)
    open(vf, "w").write(BUNDLE_VERSIONS.replace("python==3.13.5", "python==3.13.6"))
    chk("fingerprint changes with the Python version", MU.runtime_fingerprint(b1) != fp, True)
    open(vf, "w").write(BUNDLE_VERSIONS.replace("R==4.5.1", "R==4.5.2"))
    chk("fingerprint changes with the R version", MU.runtime_fingerprint(b1) != fp, True)
    try:
        MU.runtime_fingerprint(os.path.join(TMP, "no-bundle"))
        chk("no BUNDLE_VERSIONS.txt is an error", "no error", "error")
    except OSError:
        chk("no BUNDLE_VERSIONS.txt is an error", "error", "error")

    b2 = fake_repo(os.path.join(TMP, "b2"))
    vpath = MU.write_version_file(b2, "1.2", src)
    chk("write_version_file", json.load(open(vpath)),
        {"version": "1.2", "runtime_fingerprint": fp,
         "atlas_fingerprint": MU.atlas_fingerprint(src)})

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
    code_zip, atlas_zip, man = MU.build_package(src2, "1.3", rel_dir, src2)
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
    oc, oa, om = MU.build_package(src2, "1.4", other_dir, src2)
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

    # ------------------------------------------------------------ applying
    def snapshot(root, skip=("updates",) + UA.PARTS):
        """Every file outside the managed parts and updates/, with its content."""
        out = {}
        for dirpath, dirnames, filenames in os.walk(root):
            rel = os.path.relpath(dirpath, root)
            if rel != "." and rel.split(os.sep)[0] in skip:
                continue
            for n in filenames:
                if rel == "." and n in skip:
                    continue
                out[os.path.join(rel, n)] = open(os.path.join(dirpath, n), "rb").read()
        return out

    def marker(root, part="app"):
        return open(os.path.join(root, part, "marker.txt")).read()

    def make_update(inst):
        """A staged new-1.3 whose markers say 'new', and which drops scripts/old.py."""
        new = os.path.join(inst, "updates", "new-1.3")
        shutil.rmtree(new, ignore_errors=True)
        for part in ("app", "scripts", "analysis"):
            os.makedirs(os.path.join(new, part))
            with open(os.path.join(new, part, "marker.txt"), "w") as fh:
                fh.write("new " + part)
        shutil.copyfile(os.path.join(inst, "README.md"), os.path.join(new, "README.md"))
        with open(os.path.join(new, "VERSION.json"), "w") as fh:
            json.dump(dict(same, version="1.3"), fh)
        return new

    def fresh_install(name):
        inst = fake_install(os.path.join(TMP, name))
        with open(os.path.join(inst, "scripts", "old.py"), "w") as fh:
            fh.write("gone in 1.3")
        os.makedirs(os.path.join(inst, "studies"))
        with open(os.path.join(inst, "studies", "study.json"), "w") as fh:
            fh.write("{}")
        return inst

    inst = fresh_install("apply-ok")
    before = snapshot(inst)
    ok = UA.apply(inst, make_update(inst), check=lambda r: True)
    chk("apply: returns True", ok, True)
    chk("apply: new app in place", marker(inst), "new app")
    chk("apply: version is 1.3", UP.installed_info(inst)["version"], "1.3")
    chk("apply: file the release dropped is gone",
        os.path.exists(os.path.join(inst, "scripts", "old.py")), False)
    chk("apply: atlas (not in the update) kept", marker(inst, "atlas"), "atlas")
    chk("apply: old app kept in previous/", marker(os.path.join(inst, "updates", "previous")), "app")
    chk("apply: undo possible", UP.can_undo(inst), True)
    chk("apply: staged folder removed", os.path.exists(os.path.join(inst, "updates", "new-1.3")), False)
    chk("apply: nothing else touched", snapshot(inst) == before, True)

    ok = UA.undo(inst, check=lambda r: True)
    chk("undo: returns True", ok, True)
    chk("undo: old app back", marker(inst), "app")
    chk("undo: version is 1.2", UP.installed_info(inst)["version"], "1.2")
    chk("undo: dropped file back", os.path.exists(os.path.join(inst, "scripts", "old.py")), True)
    chk("undo: atlas kept", marker(inst, "atlas"), "atlas")
    chk("undo: nothing else touched", snapshot(inst) == before, True)

    inst = fresh_install("apply-check-fails")
    ok = UA.apply(inst, make_update(inst), check=lambda r: False)
    chk("check fails: returns False", ok, False)
    chk("check fails: old app back", marker(inst), "app")
    chk("check fails: old scripts back", marker(inst, "scripts"), "scripts")
    chk("check fails: version is 1.2", UP.installed_info(inst)["version"], "1.2")
    chk("check fails: no undo offered", UP.can_undo(inst), False)

    inst = fresh_install("apply-move-fails")
    real_move = UA._move

    def locked(src, dst):
        if os.path.basename(src) == "analysis" and "updates" not in os.path.relpath(src, inst):
            raise PermissionError("locked by another program")
        return real_move(src, dst)

    UA._move, UA.RETRY_DELAY = locked, 0
    try:
        ok = UA.apply(inst, make_update(inst), check=lambda r: True)
    finally:
        UA._move, UA.RETRY_DELAY = real_move, 1.0
    chk("move fails: returns False", ok, False)
    chk("move fails: app back", marker(inst), "app")
    chk("move fails: scripts back", marker(inst, "scripts"), "scripts")
    chk("move fails: analysis untouched", marker(inst, "analysis"), "analysis")
    chk("move fails: version is 1.2", UP.installed_info(inst)["version"], "1.2")

    src_main = open(os.path.join(REPO, "app", "__main__.py")).read()
    chk("app/__main__.py handles --check-install", "--check-install" in src_main, True)

    # ------------------------------------------------------------ the window side
    try:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QMainWindow
    except ImportError:
        print("(PySide6 not installed here: skipping the window-side checks)")
    else:
        import update_ui as UI
        qapp = QApplication.instance() or QApplication([])
        said = []

        def make(root):
            win = QMainWindow()
            u = UI.Updater(win, root)
            u._say = lambda title, text: said.append(text)
            menu = u.build_menu(win.menuBar())
            return win, u, menu

        win, u, menu = make(None)
        labels = [a.text() for a in menu.actions() if a.text()]
        chk("Help menu entries", labels, ["Check for updates…", "Undo last update", "About…"])
        chk("from source: undo disabled", u.undo_action.isEnabled(), False)
        u.check_now()
        chk("from source: check says git", "git pull" in (said[-1] if said else ""), True)
        chk("from source: banner hidden", u.banner.isHidden(), True)

        inst = fresh_install("ui")
        win, u, menu = make(inst)
        u.show_decision(UP.Decision("code", version="1.3", notes="n"))
        chk("code update: banner shown", u.banner.isHidden(), False)
        chk("code update: banner text", "Version 1.3 is ready to install" in u.banner_text.text(), True)
        u.show_decision(UP.Decision("current", version="1.2"))
        chk("up to date on start: banner hidden", u.banner.isHidden(), True)
        u.show_decision(UP.Decision("current", version="1.2"), quiet=False)
        chk("up to date from the menu: says so", said[-1], "You have the newest version (1.2).")
        os.makedirs(os.path.join(inst, "updates"), exist_ok=True)
        with open(os.path.join(inst, "updates", "last-result.txt"), "w") as fh:
            fh.write("Updated to 1.3.")
        u.report_last_result()
        chk("result of the last update shown once", said[-1], "Updated to 1.3.")
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print()
print("PASS" if not failures else f"FAIL: {len(failures)}: {failures}")
sys.exit(1 if failures else 0)
