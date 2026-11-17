"""A stage whose last run failed is not done, even when its outputs exist.

Done is read from the output folder, and some outputs are written by another
stage too: 01 writes 01k's `qc/saturation_raw.csv` and `qc/censor_raw/`. On the
Contagion slides 01k refused the study, Run all remaining stopped, and the next
Run all remaining skipped 01k as done and went on without a single clipping
mask. A failed last run now holds a stage at todo until it succeeds.

    python tests/test_failed_run.py
"""

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(REPO, "app"), os.path.join(REPO, "scripts"), HERE]

from _fixture import write_study                                # noqa: E402

TMP = tempfile.mkdtemp()
PATH = write_study(os.path.join(TMP, "s"))
os.environ["LS_CONFIG"] = PATH

import ls_paths                                                 # noqa: E402
import stages as S                                              # noqa: E402
import workflow as W                                            # noqa: E402

failures = []


def chk(name, got, want=True):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name:<70} {got!r}" + ("" if ok else f"  (wanted {want!r})"))
    if not ok:
        failures.append(name)


CFG = json.load(open(PATH))
OUT = CFG["out_root"]
os.makedirs(os.path.join(OUT, "qc", "censor_raw"), exist_ok=True)
open(os.path.join(OUT, "qc", "saturation_raw.csv"), "w").close()
ST = S.BY_ID["saturation_raw"]


def study(runs):
    cfg = dict(CFG)
    cfg["project"] = dict(cfg.get("project") or {}, runs=runs)
    markers = cfg.get("acquisition", {}).get("markers") or ["AF568", "AF488"]
    names = ls_paths.Names(OUT, markers, cfg.get("acquisition", {}).get("layout"))
    return W.Study(OUT, names, cfg)


def run(ok, when):
    return {"stage": "saturation_raw", "ok": ok, "started": when, "ended": when}


chk("01k's outputs exist", ST.done(OUT, study([]).names))
chk("never run here: done, as the folder says", W.state(ST, study([])), "done")
chk("last run failed: todo",
    W.state(ST, study([run(False, "2026-10-22T09:12:47+00:00")])), "todo")
chk("...and Run all remaining would run it again",
    "saturation_raw" in W.run_plan("prepare", study([run(False, "2026-10-22T09:12:47+00:00")]))[0])
chk("failed, then succeeded: done",
    W.state(ST, study([run(False, "2026-10-22T09:12:47+00:00"),
                       run(True, "2026-10-22T09:20:00+00:00")])), "done")
chk("an older failure under a newer success is history, not a hold",
    W.incomplete(ST, study([run(True, "2026-10-22T09:00:00+00:00"),
                            run(False, "2026-10-21T09:00:00+00:00"),
                            run(True, "2026-10-22T09:30:00+00:00")])), None)


# A --limit trial writes beside the real outputs, so it says nothing about
# them. A passing trial after a failed full run used to clear "its last run
# failed", and the partial table the full run wrote then read as done.
def trial(ok, when):
    return dict(run(ok, when), options={"--limit": 2})


failed_then_trial = [run(False, "2026-10-22T09:12:47+00:00"),
                     trial(True, "2026-10-22T09:20:00+00:00")]
chk("a passing --limit trial does not clear a failed full run",
    W.incomplete(ST, study(failed_then_trial)), "its last run failed")
chk("...the stage stays todo", W.state(ST, study(failed_then_trial)), "todo")
chk("a failed trial after a good full run is not a hold on the stage",
    W.incomplete(ST, study([run(True, "2026-10-22T09:00:00+00:00"),
                            trial(False, "2026-10-22T09:30:00+00:00")])), None)
chk("last_run without full_only still reports the trial",
    W.last_run({"runs": failed_then_trial}, "saturation_raw").get("options"),
    {"--limit": 2})

print()
print("ALL PASS" if not failures else f"{len(failures)} FAILED")
sys.exit(1 if failures else 0)
