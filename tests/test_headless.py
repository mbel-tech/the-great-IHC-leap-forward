"""`python -m app --run <screen> <study.json>`: Run all remaining without the window.

There was no way to create a study's results from a command line, so running
the Contagion slides remotely meant a hand-written script over autodetect,
workflow and runner. `app/headless.py` is that script, in the repo: the same
plan the screen's button makes, the same options and run records, one stage at
a time, stopping at the first failure.

The stages themselves are stood in for here; what is checked is the loop
around them.

    python tests/test_headless.py
"""

import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(REPO, "app"), os.path.join(REPO, "scripts"), HERE]

from _fixture import write_study                                # noqa: E402

TMP = tempfile.mkdtemp()
PATH = write_study(os.path.join(TMP, "s"))
os.environ["LS_CONFIG"] = PATH

import headless as H                                            # noqa: E402
import ls_project as LP                                         # noqa: E402
import workflow as W                                            # noqa: E402
from runner import StageResult                                  # noqa: E402

failures = []


def chk(name, got, want=True):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name:<62} {got!r}" + ("" if ok else f"  (wanted {want!r})"))
    if not ok:
        failures.append(name)


class StandIn:
    """Answers like Runner.run, failing the stages named in `fail`."""

    def __init__(self, fail=()):
        self.fail, self.ran = set(fail), []

    def names(self):
        return H.Runner(H.SCRIPTS, PATH).names()

    def run(self, stage, on_line=None, extra_argv=()):
        self.ran.append(stage.sid)
        if on_line:
            on_line(f"{stage.sid} says hello")
        ok = stage.sid not in self.fail
        return StageResult(stage.sid, ok, 0.1, "", None if ok else "refused")


plan = W.run_plan("prepare", H.study(PATH, StandIn()))[0]
chk("the fixture study has stages to run", len(plan) > 3)

out = io.StringIO()
r = StandIn()
results = H.run_screen(PATH, "prepare", runner=r, out=out)
chk("every planned stage runs, in plan order", r.ran, plan)
chk("...and each one is reported ok", [ok for _sid, ok, _s, _e in results], [True] * len(plan))
chk("the stage's own lines reach the output", f"{plan[0]} says hello" in out.getvalue())
runs = LP.Project.open(PATH).block.get("runs") or []
chk("each run is recorded in the study file, as the window does",
    [x["stage"] for x in runs][-len(plan):], plan)

PATH2 = write_study(os.path.join(TMP, "s2"))
r = StandIn(fail={plan[1]})
results = H.run_screen(PATH2, "prepare", runner=r, out=io.StringIO())
chk("it stops at the first failure", r.ran, plan[:2])
chk("...which is reported with its message", results[-1][1:], (False, 0.1, "refused"))
chk("...and recorded as failed", (LP.Project.open(PATH2).block.get("runs") or [])[-1]["ok"], False)

print("--- the command line ---")
env = dict(os.environ, PYTHONIOENCODING="utf-8")
p = subprocess.run([sys.executable, "-m", "app", "--run", "nowhere", PATH], cwd=REPO,
                   env=env, capture_output=True, text=True, timeout=120)
chk("an unknown screen is refused, with exit status 2", p.returncode, 2)
chk("...naming the screens there are", "prepare" in p.stderr)
p = subprocess.run([sys.executable, "-m", "app", "--run", "prepare",
                    os.path.join(TMP, "missing.json")], cwd=REPO, env=env,
                   capture_output=True, text=True, timeout=120)
chk("a study file that is not there is refused", p.returncode, 2)

print()
print("ALL PASS" if not failures else f"{len(failures)} FAILED")
sys.exit(1 if failures else 0)
