"""The tutorial's demo pack: opening it, installing the tutorial study, ready-made
results and saved progress.

    python tests/test_tutorial_pack.py
"""

import json
import os
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(REPO, "app"), os.path.join(REPO, "scripts"), HERE]

TMP = tempfile.mkdtemp()
os.environ["LS_HOME"] = os.path.join(TMP, "home")
os.environ["LS_TUTORIAL_HOME"] = os.path.join(TMP, "Tutorial study")
os.environ.pop("LS_TUTORIAL_PACK", None)

import _tutorial_fixture as TF                                  # noqa: E402
import tutorial_pack as TP                                      # noqa: E402

failures = []


def chk(name, got, want=True):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name:<62} {got!r}" + ("" if ok else f"  (wanted {want!r})"))
    if not ok:
        failures.append(name)


def raises(fn):
    try:
        fn()
    except TP.PackError as e:
        return str(e)
    return None


src = TF.make_pack(TMP)
zpath = TF.zip_pack(src, TMP)

print("--- opening ---")
p = TP.open_pack(src)
chk("dir pack: practice sections", p.practice_sections, TF.PRACTICE)
z = TP.open_pack(zpath)
chk("zip pack: same meta", z.meta, p.meta)
chk("answer curation read", sorted(p.answer_curation()), sorted(TF.PRACTICE))
chk("tutorial_home from the environment", TP.tutorial_home(), os.environ["LS_TUTORIAL_HOME"])

bad = TF.fresh_copy(src, TMP, "bad")
os.remove(os.path.join(bad, "pack.json"))
why = raises(lambda: TP.open_pack(bad))
chk("bad pack refused", why is not None)
chk("...saying what is missing", "pack.json" in (why or ""))

evil = os.path.join(TMP, "evil.zip")
with zipfile.ZipFile(evil, "w") as zz:
    zz.writestr("pack.json", "{}")
    zz.writestr("../evil.txt", "x")
chk("zip slip refused", raises(lambda: TP.open_pack(evil)) is not None)
chk("...nothing written outside", os.path.exists(os.path.join(TMP, "evil.txt")), False)
chk("...nor beside the unpacked copy",
    os.path.exists(os.path.join(os.environ["LS_HOME"], "tutorial-pack", "evil.txt")), False)

z2 = TP.open_pack(zpath)
chk("a zip pack is unpacked once and reused", z2.root, z.root)
chk("...after a refused zip, too", os.path.isfile(os.path.join(z2.root, "pack.json")))
mac = os.path.join(TMP, "finder.zip")
with zipfile.ZipFile(mac, "w") as zz:
    for dirpath, _dirs, files in os.walk(src):
        for f in files:
            full = os.path.join(dirpath, f)
            zz.write(full, os.path.join("pack", os.path.relpath(full, src)))
    zz.writestr("__MACOSX/pack/._pack.json", "x")
    zz.writestr(".DS_Store", "x")
chk("a zip made by macOS Finder opens", raises(lambda: TP.open_pack(mac)), None)

print("--- finding a pack ---")
app_root = os.path.join(TMP, "app")
os.makedirs(app_root)
chk("none found", TP.find_pack(app_root), None)
os.environ["LS_TUTORIAL_PACK"] = zpath
chk("the environment names one", TP.find_pack(app_root), zpath)
del os.environ["LS_TUTORIAL_PACK"]
TF.fresh_copy(src, app_root, "demo")
chk("the demo folder beside the app", TP.find_pack(app_root), os.path.join(app_root, "demo"))
TP.save_settings({"pack": zpath})
chk("settings remember a chosen pack", TP.settings().get("pack"), zpath)
chk("...but the shipped one comes first", TP.find_pack(app_root), os.path.join(app_root, "demo"))
chk("the chosen one when none ships", TP.find_pack(os.path.join(TMP, "nowhere")), zpath)

print("--- installing ---")
home = TP.tutorial_home()
study = TP.install(p, home)
chk("study file inside the tutorial folder", study, os.path.join(home, "output", "study.json"))
import ls_project as LP                                         # noqa: E402
proj = LP.Project.open(study)
chk("slides inside the tutorial folder", proj.get("source_dir"), os.path.join(home, "slides"))
chk("outputs inside the tutorial folder", proj.get("out_root"), os.path.join(home, "output"))
chk("slide copied", os.path.isfile(os.path.join(home, "slides", "LS22_1a.czi")))
chk("workbook is the unblinding stage's option", proj.stage_options("join_sampling"),
    {"--xlsx": os.path.join(home, "sampling.xlsx")})
import ls_config as LC                                          # noqa: E402
with open(study, encoding="utf-8") as fh:
    installed = json.load(fh)
chk("the installed settings pass the settings check", LC.validate(installed)[0], [])
chk("...with no folder of another study kept",
    "out_dir" in (installed.get("scene_render") or {}), False)
chk("install twice refuses", raises(lambda: TP.install(p, home)) is not None)
open(os.path.join(home, "output", "mine.txt"), "w").write("x")
TP.install(p, home, fresh=True)
chk("fresh: old one kept aside", os.path.isfile(os.path.join(home + ".old", "output", "mine.txt")))
chk("fresh: new one installed", os.path.isfile(os.path.join(home, "output", "study.json")))

print("--- ready-made results ---")
out = os.path.join(home, "output")
got = TP.use_ready(p, out, ("scene_renders",))
chk("copies exactly that stage's files", got, [os.path.join("qc", "scene_renders.csv")])
chk("...into the output folder", os.path.isfile(os.path.join(out, "qc", "scene_renders.csv")))
chk("other stage not copied", os.path.exists(os.path.join(out, "results", "roi_nuclei.csv")), False)
chk("a stage the pack lacks copies nothing", TP.use_ready(p, out, ("rgb_perk",)), [])
chk("has_ready", (TP.has_ready(p, ("detect",)), TP.has_ready(p, ("rgb_perk",))), (True, False))

print("--- progress ---")
pr = TP.Progress.load(out)
chk("no file: empty", (pr.current, pr.done, pr.skipped, pr.previous_study), ("", set(), set(), ""))
pr.current, pr.done, pr.skipped, pr.previous_study = "slides", {"welcome"}, {"project"}, "/x.json"
pr.save()
again = TP.Progress.load(out)
chk("round trip", (again.current, again.done, again.skipped, again.previous_study),
    ("slides", {"welcome"}, {"project"}, "/x.json"))
chk("stored beside the study", os.path.isfile(os.path.join(out, "tutorial.json")))
chk("settings file in the app home",
    json.load(open(os.path.join(os.environ["LS_HOME"], "tutorial.json"))).get("pack"), zpath)

print(f"\n{len(failures)} failure(s)")
sys.exit(1 if failures else 0)
