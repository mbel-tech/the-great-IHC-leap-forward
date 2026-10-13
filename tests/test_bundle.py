"""Sharing a project: the copy of the one file, and the extras that live outside it.

  - **One file:** the curation travels inside the copy of the project file; a project that
    predates curation living in the file has it read from out_root/curation (read only)
  - **Blinding:** the group key never reaches a blinded copy or zip, anywhere; the shotgun
    manifests and the embedded-key page never reach any of them
  - **Trust:** a tampered, truncated, over-new, foreign or path-escaping bundle is refused
    before anything is written
  - **Safety:** replacing a file copies it first; a failed import writes nothing
  - **Fidelity:** export -> import of the extras into a fresh out_root is byte-identical

Run:  python tests/test_bundle.py
"""

import json
import os
import shutil
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

failures = []


def chk(label, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + label.ljust(72) + " " + repr(got)[:80])
    if not ok:
        print("     want " + repr(want)[:200])
        failures.append(label)


def raises(fn, needle=""):
    try:
        fn()
    except B.BundleError as e:
        return needle in str(e)
    return False


from _fixture import write_study                              # noqa: E402

TMP = tempfile.mkdtemp()
os.environ["LS_HOME"] = os.path.join(TMP, "home")

import ls_bundle as B                                           # noqa: E402
import ls_project as LP                                         # noqa: E402

SECRET = "TREATMENT-X-SECRET"


def w(path, text="", mode="w"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode) as fh:
        fh.write(text)
    return path


def make_study(name):
    d = os.path.join(TMP, name)
    os.makedirs(d)
    cfg_path = write_study(d, groups={"order": ["A", SECRET], "by_animal": {"LS1": SECRET, "LS2": "A"}},
                           atlases={"mouse": {"set_dir": "plates_mouse", "region_kind": "polygons"}})
    out = os.path.join(d, "out")
    w(os.path.join(out, "curation", "ls_roi_curator_v1.json"), '{"LS1_s1": {"plate": "plate_001"}}')
    w(os.path.join(out, "curation", "ls_roi_curator_v1_rois.json"), '["Dm","Dl"]')
    w(os.path.join(out, "curation", "ls_brand_new_key.json"), '{"x": 1}')
    w(os.path.join(out, "curation", "ls_roi_curator_v1.json.tmp"), "half")
    w(os.path.join(out, "curation", "_backup_20200101_000000", "ls_roi_curator_v1.json"), "old")
    w(os.path.join(out, "manifest", "layout_overrides.csv"), "scene,rows\nA,3\n")
    w(os.path.join(out, "manifest", "manifest_files.csv"), "derived\n")
    w(os.path.join(out, "reformatted", "rotation_overrides.csv"), "scene_uid,extra_rotation\nA,90\n")
    w(os.path.join(out, "reformatted", "perk_overrides.csv"), "scene_uid,extra_rotation\nA,5\n")
    w(os.path.join(out, "reformatted", "excluded_sections.csv"), "scene_uid\nB\n")
    w(os.path.join(out, "reformatted", "shotgun_manifest_x.csv"), "animal,%s\nLS1,%s\n" % (SECRET, SECRET))
    w(os.path.join(out, "reformatted", "roi_curator.html"), "<html>%s</html>" % SECRET)
    w(os.path.join(out, "atlas", "plates_mouse", "plates.csv"), "plate_id\np1\n")
    w(os.path.join(out, "atlas", "plates_mouse", "p1.png"), b"\x89PNG\x00\x01", mode="wb")
    w(os.path.join(out, "atlas", "plates", "salmon.png"), b"not ours", mode="wb")
    p = LP.Project.open(cfg_path)
    p.record_run("05c", True, "a", "b")
    p.save()
    return cfg_path, out


def names(path):
    with zipfile.ZipFile(path) as zf:
        return sorted(zf.namelist())


cfg_path, out = make_study("one")
dest = os.path.join(TMP, "x", "b.lsbundle.zip")

print("--- what a bundle holds ---")
man = B.build(cfg_path, out, dest)
n = names(dest)
chk("a manifest, the project and the extras, in a zip", ("manifest.json" in n, "project.json" in n), (True, True))
chk("curation is NOT a separate member: it is inside project.json", [x for x in n if x.startswith("curation/")], [])
chk("operator CSVs keep their relative place",
    [x for x in n if x.startswith("operator/")],
    ["operator/manifest/layout_overrides.csv", "operator/reformatted/excluded_sections.csv",
     "operator/reformatted/perk_overrides.csv", "operator/reformatted/rotation_overrides.csv"])
chk("derived manifests are not operator decisions", "operator/manifest/manifest_files.csv" in n, False)
chk("the study's own atlas is included, not the built-in one",
    [x for x in n if x.startswith("atlas/")], ["atlas/plates_mouse/p1.png", "atlas/plates_mouse/plates.csv"])
chk("every member is hashed in the manifest",
    sorted(f["path"] for f in man["files"]), sorted(x for x in n if x != "manifest.json"))
chk("no temp file is left beside the bundle", os.path.exists(dest + ".tmp"), False)
pj0 = B.project_from(dest)
chk("a project that predates curation-in-the-file has it read from out_root/curation, parsed",
    pj0["curation"], {"ls_brand_new_key": {"x": 1}, "ls_roi_curator_v1": {"LS1_s1": {"plate": "plate_001"}},
                      "ls_roi_curator_v1_rois": ["Dm", "Dl"]})
chk("...without touching the original project file", "curation" in json.load(open(cfg_path)), False)
chk("temp files and backup folders are not read as curation", any("half" in json.dumps(v) or v == "old" for v in pj0["curation"].values()), False)

print("--- blinding ---")
blob = b"".join(zipfile.ZipFile(dest).read(x) for x in n)
chk("no treatment value appears anywhere in a blinded bundle", SECRET.encode() in blob, False)
chk("the shotgun manifest and the key-embedding page are never included",
    any("shotgun" in x or "roi_curator.html" in x for x in n), False)
pj = B.project_from(dest)
chk("project.json has the group key emptied, not removed", (pj["groups"]["order"], pj["groups"]["by_animal"]), ([], {}))
chk("the manifest says it is blinded", man["blinded"], True)
chk("path settings are blanked (no sender layout leaks)", [pj[k] for k in B.PATH_SETTINGS], [None] * 4)
chk("...even in the raw bytes", os.path.dirname(out).encode() in zipfile.ZipFile(dest).read("project.json"), False)
chk("the sender's run history stays with the sender", pj["project"]["runs"], [])
chk("decisions and the portable record travel", ("decisions" in pj["project"], "portable" in pj["project"]), (True, True))
dest2 = os.path.join(TMP, "x", "u.lsbundle.zip")
man2 = B.build(cfg_path, out, dest2, blinded=False)
chk("an unblinded bundle keeps the key, and says so", (man2["blinded"], B.project_from(dest2)["groups"]["by_animal"]["LS1"]), (False, SECRET))
chk("...yet still never carries the shotgun manifest or the page",
    any("shotgun" in x or "roi_curator.html" in x for x in names(dest2)), False)
dest3 = os.path.join(TMP, "x", "lean.lsbundle.zip")
B.build(cfg_path, out, dest3, atlases=False, operator_csvs=False)
chk("atlases and operator CSVs can be left out",
    [x for x in names(dest3) if x.startswith(("atlas/", "operator/"))], [])
chk("a size estimate is available before writing", B.size_estimate(LP.Project.open(cfg_path).cfg, out)[0], 6)
chk("...and is smaller without the extras", B.size_estimate(LP.Project.open(cfg_path).cfg, out, False, False)[0], 0)

print("--- trust ---")
chk("a good bundle inspects", B.inspect(dest)["format"], "ls-bundle")


def rewrite(src, dst, edit):
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            res = edit(info.filename, data)
            if res is None:
                continue
            zout.writestr(res[0], res[1])
        edit(None, None)


def bad(label, edit, needle):
    p = os.path.join(TMP, "bad_%d.zip" % len(failures) + str(abs(hash(label)) % 10000))
    rewrite(dest, p, edit)
    chk(label, raises(lambda: B.inspect(p), needle), True)
    return p


bad("an edited member is refused", lambda n, d: (n, d + b" " if n == "operator/manifest/layout_overrides.csv" else d) if n else None, "does not match")
bad("a member missing from the bundle is refused", lambda n, d: None if n == "operator/reformatted/perk_overrides.csv" else (n, d) if n else None, "not in the bundle")


def add_extra(n, d):
    if n is None:
        return None
    return (n, d)


p_extra = os.path.join(TMP, "extra.zip")
shutil.copy(dest, p_extra)
with zipfile.ZipFile(p_extra, "a") as zf:
    zf.writestr("curation/smuggled.json", "{}")
chk("a member the manifest does not list is refused", raises(lambda: B.inspect(p_extra), "not in the manifest"), True)


def forge(arc):
    def edit(n, d):
        if n is None:
            return None
        if n == "manifest.json":
            m = json.loads(d)
            m["files"].append({"path": arc, "bytes": 1, "sha256": "0" * 64})
            return (n, json.dumps(m).encode())
        return (n, d)
    return edit


for arc, why in (("curation/../../evil.json", "escapes"), ("/etc/passwd", "absolute"), ("curation\\x.json", "backslash"),
                 ("C:/x.json", "absolute"), ("elsewhere/x.json", "unexpected"), ("operator/reformatted/shotgun_manifest_a.csv", "treatment key"),
                 ("curation//x.json", "escapes")):
    bad("a forged path is refused before anything is read: %r" % arc, forge(arc), "unsafe member")


def manifest_edit(**kw):
    def edit(n, d):
        if n is None:
            return None
        if n == "manifest.json":
            m = json.loads(d)
            m.update(kw)
            return (n, json.dumps(m).encode())
        return (n, d)
    return edit


bad("a newer bundle version is refused, naming the versions", manifest_edit(bundle_version=99), "version 99")
bad("a foreign zip is refused", manifest_edit(format="something-else"), "not a project bundle")
bad("a bundle with no version is refused", manifest_edit(bundle_version="1"), "bundle_version")
bad("a malformed file list is refused", manifest_edit(files="no"), "malformed")
foreign = os.path.join(TMP, "foreign.zip")
with zipfile.ZipFile(foreign, "w") as zf:
    zf.writestr("hello.txt", "hi")
chk("a zip with no manifest is refused", raises(lambda: B.inspect(foreign), "no manifest"), True)
txt = os.path.join(TMP, "t.zip")
open(txt, "w").write("not a zip")
chk("a file that is not a zip is refused", raises(lambda: B.inspect(txt), "not a bundle"), True)
chk("a missing file is refused", raises(lambda: B.inspect(os.path.join(TMP, "none.zip")), "not a bundle"), True)

print("--- importing the extras ---")
fresh = os.path.join(TMP, "fresh_out")
plan = B.plan_import(dest, fresh)
chk("into an empty out_root everything is new", ({s_ for _a, _t, s_ in plan}, len(plan)), ({"new"}, 6))
res = B.apply_import(dest, fresh)
chk("applying writes every member except project.json", len(res["written"]), len(plan))
chk("...with no backup, since nothing was replaced", res["backup"], None)


def tree(root):
    out = {}
    for dp, _d, files in os.walk(root):
        for f in files:
            full = os.path.join(dp, f)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if "_backup_" in rel or rel.endswith(".tmp"):
                continue
            out[rel] = open(full, "rb").read()
    return out


sent = tree(out)
got = tree(fresh)
carried = {k: v for k, v in sent.items() if k.startswith("atlas/plates_mouse/")
           or k in ("manifest/layout_overrides.csv", "reformatted/rotation_overrides.csv",
                    "reformatted/perk_overrides.csv", "reformatted/excluded_sections.csv")}
chk("round trip: what arrives is byte-identical to what was sent", got, carried)
chk("applying again changes nothing (all identical)", ({s_ for _a, _t, s_ in B.plan_import(dest, fresh)}, B.apply_import(dest, fresh)["written"]), ({"identical"}, []))

mine = os.path.join(fresh, "reformatted", "rotation_overrides.csv")
KEY = "operator/reformatted/rotation_overrides.csv"
open(mine, "w").write("mine")
plan = {a: s_ for a, _t, s_ in B.plan_import(dest, fresh)}
chk("a changed file is flagged as differing", plan[KEY], "differs")
res = B.apply_import(dest, fresh, replace=set())
chk("keep-mine leaves it and reports it", (open(mine).read(), res["kept"], res["written"]), ("mine", [KEY], []))
res = B.apply_import(dest, fresh, replace={KEY})
chk("replacing writes the bundle's version", open(mine).read(), "scene_uid,extra_rotation\nA,90\n")
chk("...after copying the one it replaced into a backup folder",
    open(os.path.join(res["backup"], "reformatted", "rotation_overrides.csv")).read(), "mine")
chk("the backup is under curation/_backup_<stamp>", os.path.basename(res["backup"]).startswith("_backup_"), True)
open(mine, "w").write("mine again")
res = B.apply_import(dest, fresh)
chk("replace-all (None) replaces every difference", (open(mine).read(), res["written"]), ("scene_uid,extra_rotation\nA,90\n", [KEY]))

print("--- a failed import writes nothing ---")
other = os.path.join(TMP, "untouched_out")
tampered = os.path.join(TMP, "tampered.zip")
rewrite(dest, tampered, lambda n, d: (n, d + b"!" if n == "operator/reformatted/rotation_overrides.csv" else d) if n else None)
chk("a tampered bundle is refused", raises(lambda: B.apply_import(tampered, other), "does not match"), True)
chk("...and nothing was created", os.path.exists(other), False)

print("--- command line ---")
chk("inspect exits 0 on a good bundle", B.main(["inspect", dest]), 0)
chk("inspect exits 1 on a bad one", B.main(["inspect", tampered]), 1)
cli_dest = os.path.join(TMP, "cli.zip")
chk("export from the command line", (B.main(["export", cfg_path, cli_dest]), os.path.exists(cli_dest)), (0, True))
cli_out = os.path.join(TMP, "cli_out")
chk("import from the command line", (B.main(["import", cli_dest, cli_out]), os.path.exists(os.path.join(cli_out, "manifest", "layout_overrides.csv"))), (0, True))

print("--- a copy of the one file ---")
copy_dest = os.path.join(TMP, "x", "copy.json")
res = B.export_copy(cfg_path, copy_dest)
cp = json.load(open(copy_dest))
chk("the copy is a project file with its curation inside it", sorted(cp["curation"]), ["ls_brand_new_key", "ls_roi_curator_v1", "ls_roi_curator_v1_rois"])
chk("...blinded, with paths blanked and no run history",
    (cp["groups"], [cp[k] for k in B.PATH_SETTINGS], cp["project"]["runs"], cp["project"]["synced"]),
    ({"order": [], "by_animal": {}}, [None] * 4, [], {}))
chk("...and no treatment value anywhere in the text", SECRET in open(copy_dest).read(), False)
chk("it validates as a project (apart from the blanked paths)", [e for e in LP.LC.validate(cp, "x")[0] if "source_dir" not in e and "out_root" not in e and "atlas_pdf" not in e], [])
unb = os.path.join(TMP, "x", "copy_unblinded.json")
B.export_copy(cfg_path, unb, blinded=False)
chk("an unblinded copy keeps the key", json.load(open(unb))["groups"]["by_animal"]["LS1"], SECRET)
live = LP.Project.open(cfg_path)
live.write_curation("ls_roi_curator_v1", '{"LS9_s1": 1}', out_root=out)
live_copy = os.path.join(TMP, "x", "live_copy.json")
B.export_copy(cfg_path, live_copy)
chk("a project that holds its curation exports what the project holds, not stale files",
    json.load(open(live_copy))["curation"]["ls_roi_curator_v1"], {"LS9_s1": 1})
chk("the command line makes a copy", (B.main(["copy", cfg_path, os.path.join(TMP, "x", "cli_copy.json")]), os.path.exists(os.path.join(TMP, "x", "cli_copy.json"))), (0, True))
chk("a missing project is refused", raises(lambda: B.export_copy(os.path.join(TMP, "none.json"), copy_dest), "could not be read"), True)

print()
print("FAILED: " + ", ".join(failures) if failures else "ALL PASS")
sys.exit(1 if failures else 0)
