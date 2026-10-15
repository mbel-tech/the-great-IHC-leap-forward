"""Settings export and import (`ls_settings_io`).

Pinned: an export never carries curation, run history, stage-written blocks, or (by
default) folder paths and the treatment key; an import is a merge that keeps this
study's folders and groups unless asked, refuses settings that do not validate,
copies the study file aside, and records what it changed.

Run:  python tests/test_settings_io.py
"""

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

failures = []


def chk(label, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + label.ljust(72) + " " + repr(got)[:90])
    if not ok:
        print("     want " + repr(want)[:200])
        failures.append(label)


from _fixture import write_study                              # noqa: E402

TMP = tempfile.mkdtemp()
os.environ["LS_HOME"] = os.path.join(TMP, "home")             # never the real one

import ls_config as LC                                          # noqa: E402
import ls_project as P                                          # noqa: E402
import ls_settings_io as SI                                     # noqa: E402


def study(name, **over):
    d = os.path.join(TMP, name)
    os.makedirs(d, exist_ok=True)
    p = P.Project.open(write_study(d, **over))
    p.save()
    return p, d


LC.write_settings({"setup_advanced": True, "recent_projects": ["/x.json"], "last_project": "/x.json"})
a, da = study("a", section_thickness_um=20.0,
              groups={"order": ["ctrl", "drug"], "by_animal": {"LS1": "drug"}})
a.cfg["curation"] = {"roi": "x"}
a.cfg.setdefault("detection", {})["auto"] = {"made": "by a stage"}

print("--- export ---")
ex = SI.build_export(a)
s = ex["study"]
chk("kind is marked", ex["kind"], SI.KIND)
chk("no project block, curation or schema", [k for k in ("project", "curation", "schema_version") if k in s], [])
chk("no folder paths by default", [k for k in ("source_dir", "out_root", "export_dir", "atlas_pdf") if k in s], [])
chk("no treatment key by default", "groups" in s, False)
chk("no stage-written block", "auto" in (s.get("detection") or {}), False)
chk("no _comment notes", [k for k in s if k.startswith("_")], [])
chk("settings travel", s["section_thickness_um"], 20.0)
chk("app preferences travel, machine lists do not", ex["app"], {"setup_advanced": True})
ex2 = SI.build_export(a, include_paths=True, include_groups=True)
chk("paths and groups on request", (ex2["study"]["out_root"], ex2["study"]["groups"]["by_animal"]),
    (os.path.join(da, "out"), {"LS1": "drug"}))
f = os.path.join(TMP, "a-settings.json")
SI.write_export(f, ex2)
chk("written file reads back", SI.read_export(f)["study"]["section_thickness_um"], 20.0)

print("--- import ---")
b, db = study("b", section_thickness_um=14.0, groups={"order": ["x"], "by_animal": {}})
LC.write_settings({"setup_advanced": False})
plan = SI.plan_import(b, SI.read_export(f))
keys = [k for k, _, _ in plan.changes]
chk("preview lists the changed setting", "section_thickness_um" in keys, True)
chk("...but not this study's folders", [k for k in keys if k in P.path_keys()], [])
chk("...nor its groups", [k for k in keys if k.startswith("groups")], [])
chk("...and says what it kept", plan.kept, ["folders", "treatment groups"])
chk("app preference change is listed", plan.app, {"setup_advanced": True})
chk("it validates", plan.errors, [])
backup = SI.apply_import(b, plan)
chk("setting applied in memory", b.get("section_thickness_um"), 20.0)
chk("folders kept", b.get("out_root"), os.path.join(db, "out"))
chk("groups kept", b.get("groups.order"), ["x"])
chk("study is unsaved afterwards", b.dirty, True)
chk("backup written", os.path.exists(backup) and json.load(open(backup))["section_thickness_um"], 14.0)
chk("decision recorded", b.block["decisions"][-1]["by"], "settings import")
chk("app preference applied", LC.read_settings()["setup_advanced"], True)
chk("machine settings untouched", LC.read_settings()["last_project"], "/x.json")
chk("re-planning finds nothing more", bool(SI.plan_import(b, SI.read_export(f))), False)

plan = SI.plan_import(b, SI.read_export(f), use_paths=True, use_groups=True)
chk("file's folders on request", dict((k, n) for k, _, n in plan.changes).get("out_root"), os.path.join(da, "out"))

print("--- refusals ---")
bad = os.path.join(TMP, "bad.json")
json.dump({"kind": SI.KIND, "settings_version": 1, "study": {"section_thickness_um": "thick"}}, open(bad, "w"))
plan = SI.plan_import(b, SI.read_export(bad))
chk("invalid setting is an error", bool(plan.errors), True)
try:
    SI.apply_import(b, plan)
    chk("apply refuses it", False, True)
except SI.SettingsError:
    chk("apply refuses it", True, True)
chk("...and nothing changed", b.get("section_thickness_um"), 20.0)
json.dump({"hello": 1}, open(bad, "w"))
try:
    SI.read_export(bad)
    chk("a non-settings file is refused", False, True)
except SI.SettingsError:
    chk("a non-settings file is refused", True, True)
json.dump({"kind": SI.KIND, "settings_version": 99, "study": {}}, open(bad, "w"))
try:
    SI.read_export(bad)
    chk("a newer export is refused", False, True)
except SI.SettingsError as e:
    chk("a newer export is refused", "newer" in str(e), True)
chk("a study file imports too", SI.read_export(a.path)["study"]["section_thickness_um"], 20.0)

print()
print("FAILED: " + ", ".join(failures) if failures else "all passed")
sys.exit(1 if failures else 0)
