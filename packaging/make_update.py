"""Build the code-only update a release carries, and the VERSION.json an install holds.

    python packaging/make_update.py --version 1.2.0 --out dist
        -> dist/update-1.2.0.json
           dist/The-Great-IHC-Leap-Forward-code-1.2.0.zip
    python packaging/make_update.py --version 1.2.0 --version-file-only dist/LS-IHC
        -> dist/LS-IHC/VERSION.json

        -> dist/The-Great-IHC-Leap-Forward-atlas-1.2.0.zip
           (the atlas plates, ~250 MB, downloaded only when they changed)

The installed app (app/updates.py) compares the release's runtime fingerprint
with its own VERSION.json. Equal: only the code changed, so the zips can replace
app/, scripts/, analysis/ (and atlas/, when its own fingerprint differs) in
place. Different: a library, Python or R changed, and only the full installer
can bring that.

The fingerprint is deliberately coarse. Any edit to a file that decides what is
in runtime/, R/ or keras/ asks for the installer; a needless reinstall costs a
download, a missed one would leave the app importing libraries it doesn't have.
"""

import argparse
import hashlib
import json
import os
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PRODUCT = "The-Great-IHC-Leap-Forward"

# What an update replaces. app/update_apply.py swaps exactly these. The atlas
# is its own package: its plates are 250 MB and change far less often than the
# code, so it is downloaded only when its fingerprint moved.
CODE_PARTS = ("app", "scripts", "analysis")
ATLAS_PART = "atlas"
FILES = ("config.example.json", "README.md")

FINGERPRINT_FILES = (
    "requirements.txt",
    "requirements-czi.txt",
    "packaging/windows/requirements-bundle.txt",
    "packaging/windows/build.ps1",
    "packaging/windows/sitecustomize.py",
)


def package_name(version, kind="code"):
    return f"{PRODUCT}-{kind}-{version}.zip"


def manifest_name(version):
    return f"update-{version}.json"


def runtime_fingerprint(repo=REPO):
    """SHA-256 over the files that decide the bundled runtime, line endings normalised."""
    h = hashlib.sha256()
    for rel in FINGERPRINT_FILES:
        with open(os.path.join(repo, rel), "rb") as fh:
            data = fh.read().replace(b"\r\n", b"\n")
        h.update(rel.encode() + b"\0" + data + b"\0")
    return h.hexdigest()


def _walk(repo, part):
    """Files under repo/part, sorted, without bytecode."""
    for dirpath, dirnames, filenames in os.walk(os.path.join(repo, part)):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for name in sorted(filenames):
            if not name.endswith(".pyc"):
                yield os.path.join(dirpath, name)


def atlas_fingerprint(repo=REPO):
    """SHA-256 over every atlas file's path and content."""
    h = hashlib.sha256()
    for full in _walk(repo, ATLAS_PART):
        h.update(os.path.relpath(full, repo).replace(os.sep, "/").encode() + b"\0")
        with open(full, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
        h.update(b"\0")
    return h.hexdigest()


def version_info(version, repo=REPO):
    return {"version": version,
            "runtime_fingerprint": runtime_fingerprint(repo),
            "atlas_fingerprint": atlas_fingerprint(repo)}


def write_version_file(dest_dir, version, repo=REPO):
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, "VERSION.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(version_info(version, repo), fh, indent=2)
    return path


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _zip(repo, path, parts, files=(), extra=None):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for part in parts:
            for full in _walk(repo, part):
                z.write(full, os.path.relpath(full, repo).replace(os.sep, "/"))
        for name in files:
            z.write(os.path.join(repo, name), name)
        for name, text in (extra or {}).items():
            z.writestr(name, text)
    return {"name": os.path.basename(path), "size": os.path.getsize(path),
            "sha256": _sha256(path)}


def build_package(repo, version, out_dir):
    """Write the code zip, the atlas zip and the manifest into out_dir.

    Returns (code zip, atlas zip, manifest) paths.
    """
    os.makedirs(out_dir, exist_ok=True)
    info = version_info(version, repo)
    code = os.path.join(out_dir, package_name(version, "code"))
    atlas = os.path.join(out_dir, package_name(version, "atlas"))
    manifest = dict(info)
    manifest["packages"] = {
        "code": _zip(repo, code, CODE_PARTS, FILES,
                     {"VERSION.json": json.dumps(info, indent=2)}),
        "atlas": _zip(repo, atlas, (ATLAS_PART,)),
    }
    mpath = os.path.join(out_dir, manifest_name(version))
    with open(mpath, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    return code, atlas, mpath


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--version", required=True, help="release version, without the leading v")
    ap.add_argument("--out", help="folder for the zip and its manifest")
    ap.add_argument("--version-file-only", metavar="DIR",
                    help="only write DIR/VERSION.json (the build does this for the bundle)")
    a = ap.parse_args(argv)
    if a.version_file_only:
        print(write_version_file(a.version_file_only, a.version))
        return 0
    if not a.out:
        ap.error("--out or --version-file-only is needed")
    for path in build_package(REPO, a.version, a.out):
        print(f"{path}  {os.path.getsize(path):,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
