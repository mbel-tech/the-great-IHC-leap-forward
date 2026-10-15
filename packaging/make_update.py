"""Build the code-only update a release carries, and the VERSION.json an install holds.

    python packaging/make_update.py --version 1.2.0 --bundle dist/LS-IHC --out dist
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

The runtime fingerprint is a hash of the bundle's BUNDLE_VERSIONS.txt: the pip
freeze of the runtime as built, plus the Python and R versions. It is taken from
what was built rather than from requirements*.txt, because those say ">=" and
two builds of the same files can hold different libraries. build.ps1 builds
against packaging/windows/bundle-lock.txt, so releases share a runtime (and get
code updates) until someone changes the lock.
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

def package_name(version, kind="code"):
    return f"{PRODUCT}-{kind}-{version}.zip"


def manifest_name(version):
    return f"update-{version}.json"


def runtime_fingerprint(bundle_dir):
    """SHA-256 of the bundle's BUNDLE_VERSIONS.txt, ignoring line order, case and line endings."""
    with open(os.path.join(bundle_dir, "BUNDLE_VERSIONS.txt"), encoding="utf-8-sig") as fh:
        lines = sorted({ln.strip().lower() for ln in fh if ln.strip()})
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


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


def version_info(version, repo, bundle_dir):
    return {"version": version,
            "runtime_fingerprint": runtime_fingerprint(bundle_dir),
            "atlas_fingerprint": atlas_fingerprint(repo)}


def write_version_file(bundle_dir, version, repo=REPO):
    """bundle_dir/VERSION.json, from the bundle's own BUNDLE_VERSIONS.txt."""
    path = os.path.join(bundle_dir, "VERSION.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(version_info(version, repo, bundle_dir), fh, indent=2)
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


def build_package(repo, version, out_dir, bundle_dir):
    """Write the code zip, the atlas zip and the manifest into out_dir.

    bundle_dir is the built bundle whose runtime these packages run on.
    Returns (code zip, atlas zip, manifest) paths.
    """
    os.makedirs(out_dir, exist_ok=True)
    info = version_info(version, repo, bundle_dir)
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
    ap.add_argument("--bundle", required=True,
                    help="the built bundle (holds BUNDLE_VERSIONS.txt)")
    ap.add_argument("--out", help="folder for the zips and the manifest")
    ap.add_argument("--version-file-only", action="store_true",
                    help="only write <bundle>/VERSION.json (build.ps1 does this)")
    a = ap.parse_args(argv)
    if a.version_file_only:
        print(write_version_file(a.bundle, a.version))
        return 0
    if not a.out:
        ap.error("--out or --version-file-only is needed")
    for path in build_package(REPO, a.version, a.out, a.bundle):
        print(f"{path}  {os.path.getsize(path):,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
