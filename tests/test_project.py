"""The project file: create, open, modify, save - and what must never happen.

A project is the study config plus a `project` block only the app reads. What is
pinned, and why each is a place it could go quietly wrong:

  - opening a file and saving it without a change must not rewrite it (a project
    that churns on every open fills version control and hides real changes)
  - a failed write must leave the old file whole (atomic), and a save keeps
    backups
  - `groups.*` values must never reach the decision trail (that is the
    unblinding key)
  - the run history must not carry unsaved edits to disk, and must not make the
    config look changed (the config hash ignores the `project` block)
  - a file from a newer app opens read-only and is never rewritten
  - every list in the block is bounded

Run:  python tests/test_project.py
"""

import copy
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


def raises(fn, cls, needle=""):
    try:
        fn()
    except cls as e:
        return needle in str(e)
    return False


from _fixture import write_study                              # noqa: E402

TMP = tempfile.mkdtemp()
os.environ["LS_HOME"] = os.path.join(TMP, "home")             # never the real one

import ls_config as LC                                          # noqa: E402
import ls_jsonio                                                # noqa: E402
import ls_project as P                                          # noqa: E402

P._now = lambda: "2026-01-02T03:04:05+00:00"


def read(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def fresh(name="s"):
    d = os.path.join(TMP, name)
    os.makedirs(d, exist_ok=True)
    return write_study(d), d


print("--- atomic writes ---")
f = os.path.join(TMP, "a", "x.json")
ls_jsonio.write_json_atomic(f, {"a": 1})
chk("a write creates the folder and the file", read(f), {"a": 1})
chk("...and leaves no temp file", os.path.exists(f + ".tmp"), False)


class Boom:
    def __repr__(self):
        raise RuntimeError("cannot serialise")


try:
    ls_jsonio.write_json_atomic(f, {"a": object()})
except TypeError:
    pass
chk("a failed write leaves the old file whole", read(f), {"a": 1})
chk("...and cleans its temp file", os.path.exists(f + ".tmp"), False)
for v in (2, 3, 4, 5):
    ls_jsonio.write_json_atomic(f, {"a": v}, backups=3)
chk("backups keep the newest previous versions", [read(f + ".bak%d" % n)["a"] for n in (1, 2, 3)], [4, 3, 2])
chk("...and no more than asked", os.path.exists(f + ".bak4"), False)

print("--- creating ---")
p = P.Project.new("My study")
chk("a new project has a name", p.name, "My study")
chk("...is dirty (never saved)", p.dirty, True)
chk("...has a project block at version 1", p.block["project_version"], 1)
chk("...with an id and a created time", (len(p.block["id"]) == 36, p.block["created"]), (True, "2026-01-02T03:04:05+00:00"))
chk("...and empty history", (p.block["detected"], p.block["decisions"], p.block["runs"]), ([], [], []))
chk("the stock template has no project block",
    "project" in json.load(open(os.path.join(LC.REPO, "config.example.json"))), False)
chk("a project with no file cannot be saved", raises(p.save, P.ProjectError, "save_as"), True)
chk("nothing to revert on a never-saved project", raises(p.revert, P.ProjectError, "never been saved"), True)

print("--- save as, open, modify, save ---")
path, d = fresh("one")
q = P.Project.open(path)
chk("a legacy file (no block) opens clean", q.dirty, False)
chk("...and is given a block in memory", "project" in q.cfg, True)
target = os.path.join(d, "proj.json")
q.save_as(target)
chk("save as writes the file", os.path.exists(target), True)
chk("...stamps modified", read(target)["project"]["modified"], "2026-01-02T03:04:05+00:00")
chk("...and is clean afterwards", q.dirty, False)
chk("save as will not overwrite another file", raises(lambda: P.Project.open(path).save_as(target), P.ProjectError, "exists"), True)

r = P.Project.open(target)
chk("a saved project opens clean", r.dirty, False)
before = open(target, "rb").read()
r.save()
chk("opening and saving with no change rewrites the same bytes", open(target, "rb").read(), before)
chk("set reports a real change", r.set("pixel_size_um", 0.5, by="test"), True)
chk("...and no change at all", r.set("pixel_size_um", 0.5, by="test"), False)
chk("...which makes the project dirty", r.dirty, True)
chk("...and is recorded as a decision",
    [(x["key"], x["from"], x["to"], x["by"]) for x in r.block["decisions"]], [("pixel_size_um", 0.65, 0.5, "test")])
r.save()
chk("saving writes it", read(target)["pixel_size_um"], 0.5)
chk("...keeps a backup of the previous file", read(target + ".bak1")["pixel_size_um"], 0.65)
chk("...and is clean again", r.dirty, False)
chk("a nested key is set without disturbing its siblings",
    (r.set("display.nuclear_colour", "cyan"), r.get("display.colours"), r.get("display.nuclear_colour")), (True, {}, "cyan"))
r.revert()
chk("revert discards unsaved changes", (r.get("display.nuclear_colour"), r.dirty), ("blue", False))
chk("the project validates as a study config", r.validate()[0], [])
chk("apply_edits returns what changed",
    r.apply_edits({"display.nuclear_colour": "cyan", "pixel_size_um": 0.5}, by="editor"), ["display.nuclear_colour"])

print("--- blinding ---")
r2 = P.Project.open(target)
r2.set("groups.by_animal", {"LS1": "TREATMENT X"}, by="test")
r2.set("groups.order", ["A", "B"], by="test")
trail = json.dumps(r2.block["decisions"])
chk("the animal-to-treatment key is recorded by name only", ("TREATMENT X" in trail, "LS1" in trail), (False, False))
chk("...and marked as withheld", [x.get("redacted") for x in r2.block["decisions"][-2:]], [True, True])
chk("(the value is still in the config, where it already was)", r2.get("groups.by_animal"), {"LS1": "TREATMENT X"})
big = [{"name": "ch%d" % i, "role": "marker", "czi_name": "x" * 40} for i in range(60)]
r2.set("acquisition.channels", big, by="test")
last = r2.block["decisions"][-1]
chk("a large value is summarised, not copied into the trail",
    (last["to"].get("large"), len(last["to"]["sha"])), ("list of 60", 12))

print("--- detected evidence ---")
r3 = P.Project.open(target)
r3.note_detected("/x/a.czi", [{"name": "DAPI", "band": "blue"}])
r3.note_detected("/x/a.czi", [{"name": "DAPI", "band": "blue"}, {"name": "AF568", "band": "red"}])
chk("detection is kept once per file, newest wins", [(d["file"], len(d["channels"])) for d in r3.block["detected"]],
    [(os.path.abspath("/x/a.czi"), 2)])
chk("and never touches the config proper", r3.get("acquisition"), P.Project.open(target).get("acquisition"))

print("--- the run history ---")
r4 = P.Project.open(target)
r4.set("pixel_size_um", 0.9, by="test")                       # an UNSAVED edit
rec = r4.record_run("05c_detect_rois", True, "2026-01-02T03:00:00+00:00", "2026-01-02T03:05:00+00:00")
on_disk = read(target)
chk("a run is on disk", [x["stage"] for x in on_disk["project"]["runs"]], ["05c_detect_rois"])
chk("...without the unsaved edit", on_disk["pixel_size_um"], 0.5)
chk("...and the edit is still unsaved in memory", (r4.get("pixel_size_um"), r4.dirty), (0.9, True))
chk("the run records the hash of what is on disk, not of the edit",
    rec["config_sha"], P.config_sha(on_disk))
chk("the config hash ignores the project block",
    P.config_sha(on_disk), P.config_sha({k: v for k, v in on_disk.items() if k != "project"}))
chk("...so a run does not look like a config change", P.config_sha(on_disk), P.config_sha(read(target)))
r4.revert()
chk("reverting keeps the run (it already happened)", [x["stage"] for x in r4.block["runs"]], ["05c_detect_rois"])
chk("a run does not make a clean project dirty", (P.Project.open(target).dirty, r4.dirty), (False, False))

print("--- bounds ---")
r5 = P.Project.open(target)
for i in range(P.CAPS["runs"] + 5):
    r5.record_run("s%d" % i, True, "a", "b")
runs = read(target)["project"]["runs"]
chk("the run list is capped", len(runs), P.CAPS["runs"])
chk("...dropping the oldest", (runs[0]["stage"], runs[-1]["stage"]), ("s5", "s%d" % (P.CAPS["runs"] + 4)))
for i in range(P.CAPS["decisions"] + 3):
    r5.set("notes_x%d" % (i % 2), i, by="t")
chk("the decision list is capped", len(r5.block["decisions"]), P.CAPS["decisions"])
for i in range(P.CAPS["detected"] + 3):
    r5.note_detected("/f/%d.czi" % i, [])
chk("the detected list is capped", len(r5.block["detected"]), P.CAPS["detected"])
over = dict(read(target)); over["project"] = dict(over["project"], runs=[{}] * (P.CAPS["runs"] + 1))
chk("a hand-made oversize list is a config error",
    any("most kept" in e for e in LC.validate(over, "x")[0]), True)
chk("a malformed block is a config error", any("project" in e for e in LC.validate(dict(read(target), project=5), "x")[0]), True)

print("--- versions and conflicts ---")
future = read(target)
future["project"]["project_version"] = 99
fp = os.path.join(d, "future.json")
json.dump(future, open(fp, "w"))
fb = open(fp, "rb").read()
r6 = P.Project.open(fp)
chk("a project from a newer app opens read-only", r6.read_only, True)
chk("...cannot be changed", raises(lambda: r6.set("pixel_size_um", 1), P.ProjectError, "newer"), True)
chk("...or saved", raises(r6.save, P.ProjectError, "newer"), True)
chk("...and the file is untouched", open(fp, "rb").read(), fb)
P.UPGRADES[1] = lambda b: dict(b, upgraded=True)
P.PROJECT_VERSION = 2
old = P.Project.open(target)
chk("an older block is upgraded in memory", (old.block["project_version"], old.block.get("upgraded")), (2, True))
P.PROJECT_VERSION = 1
P.UPGRADES.clear()

c1 = P.Project.open(target)
with open(target, "r+", encoding="utf-8") as fh:               # someone edits it by hand
    t = json.load(fh); t["pixel_size_um"] = 7; fh.seek(0); json.dump(t, fh, indent=2); fh.truncate()
c1.set("display.nuclear_colour", "cyan")
chk("saving over a hand edit is refused", raises(c1.save, P.ProjectConflict, "changed on disk"), True)
c1.save(force=True)
chk("...unless forced", read(target)["display"]["nuclear_colour"], "cyan")

print("--- bad files ---")
bad = os.path.join(d, "bad.json")
open(bad, "w").write("{nope")
chk("invalid JSON is refused with the path", raises(lambda: P.Project.open(bad), P.ProjectError, "bad.json"), True)
open(bad, "w").write("[1,2]")
chk("a non-object is refused", raises(lambda: P.Project.open(bad), P.ProjectError, "not a project"), True)
chk("a missing file is refused", raises(lambda: P.Project.open(os.path.join(d, "none.json")), P.ProjectError, "could not be read"), True)
open(bad, "w").write(json.dumps({"project": 5}))
chk("a non-block project key is refused", raises(lambda: P.Project.open(bad), P.ProjectError, "block"), True)

print("--- recent projects ---")
LC.remember_project(target)
LC.remember_project(path)
chk("recent lists the newest first", LC.recent_projects(), [os.path.abspath(path), os.path.abspath(target)])
LC.remember_project(target)
chk("...and re-opening moves one to the front, once", LC.recent_projects(), [os.path.abspath(target), os.path.abspath(path)])
os.remove(path)
chk("...and drops a file that has gone", LC.recent_projects(), [os.path.abspath(target)])
chk("the last project is remembered", LC.read_settings()["last_project"], os.path.abspath(target))

print("--- the version stamp ---")
chk("a code version is a short hash or None", P.code_version() is None or isinstance(P.code_version(), str), True)

print()
print("FAILED: " + ", ".join(failures) if failures else "ALL PASS")
sys.exit(1 if failures else 0)
