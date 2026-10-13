"""Sharing a project: a blinded copy of the one file, and the extras that live outside it.

A project is one file: the study config, the app's record, and (since curation moved in) the
curation decisions themselves. Sharing it is therefore mostly a *copy of that file* with the
things that must not travel taken out:

    export_copy(project, dest)     the project file, paths blanked, treatment key stripped
    build(project, out_root, dest) the same file inside a zip with the extras that are not in it:
        manifest.json              format, version, who/when, every member's size and sha256
        project.json               the copy above
        operator/...               optional: the operator-entered CSVs, same relative paths
        atlas/plates_<name>/...    optional: the study's own imported atlases

Three rules that matter more than the format:

  * **Blinding.** `groups.order` and `groups.by_animal` are the unblinding key, so a copy is
    *blinded* unless the caller says otherwise. `reformatted/shotgun_manifest*.csv` and
    `reformatted/roi_curator.html` are never included (they carry or embed the key).
  * **A bundle is not trusted.** Opening one verifies the manifest, every hash, and that no
    member path can leave the folder it is extracted into (zip-slip).
  * **Nothing is replaced without a copy.** Importing extras into an existing out_root copies
    each file it would replace into `curation/_backup_<stamp>/` first.

Standard library only (no Qt), so it runs headless too:

    python scripts/ls_bundle.py copy STUDY.json DEST.json [--unblinded]
    python scripts/ls_bundle.py export STUDY.json DEST.zip [--unblinded] [--no-atlases] [--no-operator]
    python scripts/ls_bundle.py inspect BUNDLE.zip
    python scripts/ls_bundle.py import BUNDLE.zip OUT_ROOT [--replace-all | --keep-existing]
"""

import argparse
import copy
import datetime
import fnmatch
import hashlib
import json
import os
import posixpath
import shutil
import sys
import zipfile

import ls_project as LP

FORMAT = "ls-bundle"
BUNDLE_VERSION = 1

#: Largest single member and whole bundle accepted on import (a decompression bomb guard).
MAX_MEMBER = 2 * 1024 ** 3
MAX_TOTAL = 8 * 1024 ** 3

#: Operator-entered CSVs under out_root, as glob patterns relative to it.
OPERATOR_PATTERNS = ("manifest/layout_overrides.csv",
                     "reformatted/rotation_overrides.csv",
                     "reformatted/*_overrides.csv",
                     "reformatted/excluded_sections*.csv")

#: Never written into a bundle, whatever else matches: they carry or embed the treatment key.
NEVER = ("reformatted/shotgun_manifest*.csv", "reformatted/roi_curator.html")

#: Settings that are paths on the sender's machine. Blanked, so a bundle does not leak
#: usernames or drive layout and the receiver has to choose their own.
PATH_SETTINGS = ("source_dir", "out_root", "atlas_pdf", "export_dir")

#: The unblinding key.
GROUP_KEYS = ("groups",)

PREFIXES = ("operator/", "atlas/")


class BundleError(Exception):
    """The bundle could not be made, or cannot be trusted."""


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _is_never(rel):
    return any(fnmatch.fnmatch(rel, pat) for pat in NEVER)


# ---- building ------------------------------------------------------------------------

def _blank_project(cfg, blinded):
    cfg = copy.deepcopy(cfg)
    for key in PATH_SETTINGS:
        if key in cfg:
            cfg[key] = None
    if blinded:
        groups = cfg.get("groups")
        if isinstance(groups, dict):
            if "order" in groups:
                groups["order"] = []
            if "by_animal" in groups:
                groups["by_animal"] = {}
    # A bundle carries decisions, not who ran what on which machine.
    block = cfg.get("project")
    if isinstance(block, dict):
        block["runs"] = []
        block["synced"] = {}            # hashes of one machine's copies mean nothing on another
    return cfg


def _with_curation(cfg, out_root):
    """`cfg` carrying the curation: the project's own, or - for one that predates curation
    living in the file - what is in out_root/curation (read only; the original is untouched)."""
    cfg = copy.deepcopy(cfg)
    if not cfg.get("curation") and out_root and os.path.isdir(out_root):
        found = {}
        for key, path in LP._files_in(out_root).items():
            try:
                found[key] = LP.curation_value(LP._read_text(path))
            except (OSError, UnicodeDecodeError):
                continue
        if found:
            cfg["curation"] = found
    return cfg


def _members(cfg, out_root, atlases, operator_csvs):
    """[(archive path, absolute file)] for everything the bundle would carry, sorted."""
    found = {}
    if operator_csvs:
        for pat in OPERATOR_PATTERNS:
            folder, _, leaf = pat.rpartition("/")
            base = os.path.join(out_root, *folder.split("/"))
            if not os.path.isdir(base):
                continue
            for name in sorted(os.listdir(base)):
                rel = folder + "/" + name
                if fnmatch.fnmatch(name, leaf) and not _is_never(rel) and os.path.isfile(os.path.join(base, name)):
                    found["operator/" + rel] = os.path.join(base, name)
    if atlases:
        for name, spec in sorted((cfg.get("atlases") or {}).items()):
            set_dir = spec.get("set_dir") if isinstance(spec, dict) else None
            root = os.path.join(out_root, "atlas", set_dir) if set_dir else None
            if not root or not os.path.isdir(root):
                continue
            for dirpath, _dirs, files in os.walk(root):
                for f in sorted(files):
                    if f.endswith((".tmp", ".bak")):
                        continue
                    full = os.path.join(dirpath, f)
                    rel = os.path.relpath(full, os.path.join(out_root, "atlas")).replace(os.sep, "/")
                    found["atlas/" + rel] = full
    return sorted(found.items())


def size_estimate(cfg, out_root, atlases=True, operator_csvs=True):
    """(file count, total bytes) of what an export would carry, for showing before it is written."""
    members = _members(cfg, out_root, atlases, operator_csvs)
    return len(members), sum(os.path.getsize(p) for _a, p in members)


def build(project_path, out_root, dest, blinded=True, atlases=True, operator_csvs=True):
    """Write a bundle of the project saved at `project_path` to `dest`. Returns its manifest."""
    try:
        with open(project_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError) as e:
        raise BundleError("%s could not be read: %s" % (project_path, e))
    if not isinstance(cfg, dict):
        raise BundleError("%s is not a project" % project_path)

    project_json = json.dumps(_blank_project(_with_curation(cfg, out_root), blinded),
                              indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    files = [("project.json", project_json.encode("utf-8"))]
    for arc, path in _members(cfg, out_root, atlases, operator_csvs):
        with open(path, "rb") as fh:
            files.append((arc, fh.read()))

    block = cfg.get("project") if isinstance(cfg.get("project"), dict) else {}
    manifest = {
        "format": FORMAT, "bundle_version": BUNDLE_VERSION, "created": _now(),
        "project": {"name": (cfg.get("study") or {}).get("name"), "id": block.get("id")},
        "app_version": block.get("app_version"),
        "blinded": bool(blinded), "atlases": bool(atlases), "operator_csvs": bool(operator_csvs),
        "files": [{"path": a, "bytes": len(d), "sha256": _sha(d)} for a, d in files],
    }
    dest = os.path.abspath(dest)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".tmp"
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            zf.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
            for arc, data in files:
                zf.writestr(arc, data)
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return manifest


def export_copy(project_path, dest, blinded=True):
    """Write a shareable copy of the project file itself. Returns a summary dict.

    Paths are blanked, the treatment key is stripped unless `blinded` is False, the sender's run
    history is dropped, and the curation travels inside it (taken from out_root/curation for a
    project that predates curation living in the file). Atomic, like every write here.
    """
    try:
        with open(project_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError) as e:
        raise BundleError("%s could not be read: %s" % (project_path, e))
    if not isinstance(cfg, dict):
        raise BundleError("%s is not a project" % project_path)
    copy_cfg = _blank_project(_with_curation(cfg, cfg.get("out_root")), blinded)
    dest = os.path.abspath(dest)
    import ls_jsonio
    ls_jsonio.write_json_atomic(dest, copy_cfg)
    return {"path": dest, "blinded": bool(blinded),
            "curation_keys": sorted((copy_cfg.get("curation") or {}))}


# ---- reading and trusting --------------------------------------------------------------

def _safe_member(name):
    """None if `name` is an acceptable member path, else why not."""
    if name == "manifest.json" or name == "project.json":
        return None
    if "\\" in name or name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        return "absolute or backslash path"
    parts = name.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return "path escapes its folder"
    if posixpath.normpath(name) != name:
        return "path is not normal"
    if not name.startswith(PREFIXES):
        return "unexpected location"
    if name.startswith("operator/") and _is_never(name.split("/", 1)[1]):
        return "carries the treatment key"
    return None


def inspect(path):
    """The manifest of a bundle, after checking everything that can be checked.

    Raises BundleError for anything that makes the bundle untrustworthy: not a zip, wrong
    format, a newer version, a member that is missing, extra, oversized, unsafe or whose
    hash differs from the manifest.
    """
    try:
        zf = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as e:
        raise BundleError("%s is not a bundle: %s" % (os.path.basename(path), e))
    with zf:
        if "manifest.json" not in zf.namelist():
            raise BundleError("%s has no manifest.json" % os.path.basename(path))
        try:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        except ValueError as e:
            raise BundleError("the manifest is not valid JSON: %s" % e)
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
            raise BundleError("this is not a project bundle")
        version = manifest.get("bundle_version")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise BundleError("the manifest has no usable bundle_version")
        if version > BUNDLE_VERSION:
            raise BundleError("this bundle is version %d; this app reads up to %d. Update the app."
                              % (version, BUNDLE_VERSION))
        listed = manifest.get("files")
        if not isinstance(listed, list) or not all(isinstance(f, dict) for f in listed):
            raise BundleError("the manifest's file list is malformed")
        names = {i.filename: i for i in zf.infolist() if not i.filename.endswith("/")}
        declared = {}
        total = 0
        for f in listed:
            name = f.get("path")
            if not isinstance(name, str):
                raise BundleError("a manifest entry has no path")
            problem = _safe_member(name)
            if problem or name == "manifest.json":
                raise BundleError("unsafe member %r: %s" % (name, problem or "reserved"))
            if name in declared:
                raise BundleError("%r is listed twice" % name)
            declared[name] = f
        if "project.json" not in declared:
            raise BundleError("the bundle has no project.json")
        extra = sorted(set(names) - set(declared) - {"manifest.json"})
        if extra:
            raise BundleError("members not in the manifest: %s" % ", ".join(extra[:5]))
        for name, f in declared.items():
            info = names.get(name)
            if info is None:
                raise BundleError("%r is in the manifest but not in the bundle" % name)
            if info.file_size > MAX_MEMBER:
                raise BundleError("%r is too large (%d bytes)" % (name, info.file_size))
            total += info.file_size
            if total > MAX_TOTAL:
                raise BundleError("the bundle expands to more than %d bytes" % MAX_TOTAL)
            data = zf.read(name)
            if len(data) != f.get("bytes") or _sha(data) != f.get("sha256"):
                raise BundleError("%r does not match the manifest (damaged or edited)" % name)
    return manifest


def project_from(path):
    """The bundle's project config (paths blanked, group key stripped if it was blinded)."""
    inspect(path)
    with zipfile.ZipFile(path) as zf:
        return json.loads(zf.read("project.json").decode("utf-8"))


# ---- importing ----------------------------------------------------------------------------

def _target(out_root, arc):
    """Where a member goes under out_root."""
    if arc.startswith("operator/"):
        return os.path.join(out_root, *arc.split("/")[1:])
    return os.path.join(out_root, *arc.split("/"))          # atlas/...


def plan_import(path, out_root):
    """[(member, target, 'new' | 'identical' | 'differs')] for everything but project.json."""
    manifest = inspect(path)
    out = []
    for f in manifest["files"]:
        arc = f["path"]
        if arc == "project.json":
            continue
        target = _target(out_root, arc)
        if not os.path.exists(target):
            status = "new"
        else:
            with open(target, "rb") as fh:
                status = "identical" if _sha(fh.read()) == f["sha256"] else "differs"
        out.append((arc, target, status))
    return out


def apply_import(path, out_root, replace=None):
    """Write the bundle's files under `out_root`. Returns {'written': [...], 'kept': [...], 'backup': dir|None}.

    `replace` is a set of member names to overwrite where they differ, or None for "all that differ".
    New files are always written; identical ones are skipped. Every file that is replaced is first
    copied to `curation/_backup_<stamp>/` (same relative layout). The bundle is fully verified
    before the first byte is written, and each write is atomic.
    """
    plan = plan_import(path, out_root)                 # inspects, raising before anything is written
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = os.path.join(out_root, "curation", "_backup_" + stamp)
    written, kept, used_backup = [], [], False
    with zipfile.ZipFile(path) as zf:
        for arc, target, status in plan:
            if status == "identical":
                continue
            if status == "differs" and replace is not None and arc not in replace:
                kept.append(arc)
                continue
            data = zf.read(arc)
            if status == "differs":
                rel = os.path.relpath(target, out_root)
                dest = os.path.join(backup, rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copy2(target, dest)
                used_backup = True
            os.makedirs(os.path.dirname(target), exist_ok=True)
            tmp = target + ".tmp"
            try:
                with open(tmp, "wb") as fh:
                    fh.write(data)
                os.replace(tmp, target)
            except BaseException:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
            written.append(arc)
    return {"written": written, "kept": kept, "backup": backup if used_backup else None}


# ---- command line ----------------------------------------------------------------------------

def _human(n):
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%d %s" % (n, unit) if unit == "bytes" else "%.1f %s" % (n, unit)
        n /= 1024.0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("copy")
    c.add_argument("project")
    c.add_argument("dest")
    c.add_argument("--unblinded", action="store_true")
    e = sub.add_parser("export")
    e.add_argument("project")
    e.add_argument("dest")
    e.add_argument("--unblinded", action="store_true", help="keep the treatment key in project.json")
    e.add_argument("--no-atlases", action="store_true")
    e.add_argument("--no-operator", action="store_true")
    i = sub.add_parser("inspect")
    i.add_argument("bundle")
    m = sub.add_parser("import")
    m.add_argument("bundle")
    m.add_argument("out_root")
    g = m.add_mutually_exclusive_group()
    g.add_argument("--replace-all", action="store_true")
    g.add_argument("--keep-existing", action="store_true")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "copy":
            res = export_copy(args.project, args.dest, blinded=not args.unblinded)
            print("wrote %s  (%d curation state(s))%s" % (res["path"], len(res["curation_keys"]),
                  "" if res["blinded"] else "  (UNBLINDED - contains the treatment key)"))
        elif args.cmd == "export":
            with open(args.project, encoding="utf-8") as fh:
                cfg = json.load(fh)
            out_root = cfg.get("out_root")
            if not out_root:
                raise BundleError("the project has no out_root")
            man = build(args.project, out_root, args.dest, blinded=not args.unblinded,
                        atlases=not args.no_atlases, operator_csvs=not args.no_operator)
            print("wrote %s: %d files, %s%s" % (args.dest, len(man["files"]),
                  _human(sum(f["bytes"] for f in man["files"])), "" if man["blinded"] else "  (UNBLINDED - contains the treatment key)"))
        elif args.cmd == "inspect":
            man = inspect(args.bundle)
            print("%s  v%d  %s  project %s  %s" % (os.path.basename(args.bundle), man["bundle_version"],
                  man.get("created"), (man.get("project") or {}).get("name"),
                  "blinded" if man.get("blinded") else "UNBLINDED"))
            for f in man["files"]:
                print("  %-60s %10s" % (f["path"], _human(f["bytes"])))
        else:
            plan = plan_import(args.bundle, args.out_root)
            for arc, _t, status in plan:
                print("  %-9s %s" % (status, arc))
            replace = set() if args.keep_existing else None
            res = apply_import(args.bundle, args.out_root, replace)
            print("wrote %d, kept %d%s" % (len(res["written"]), len(res["kept"]),
                  ", previous versions in " + res["backup"] if res["backup"] else ""))
    except BundleError as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
