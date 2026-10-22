"""Run all remaining from the command line, without the window.

    python -m app --run prepare  C:\\study\\out\\study.json
    python -m app --run measure  C:\\study\\out\\study.json

The same plan the screen's Run all remaining makes (workflow.run_plan), each
stage with the options saved in the study and Redo when it is out of date
(workflow.run_values), each run recorded in the study file as the window
records it. One stage at a time, its output printed as it comes; the first
failure stops the run, and so does a hold the window would stop at (the weak
signal check before detection). Unblinding is never planned, as on screen.

Exit status: 0 when every planned stage ran, 1 when one failed or a hold
stopped the run, 2 for a screen or study file that is not there.

Nothing here touches Qt, so it runs where there is no display. A stage that
writes curation (04q's import) needs the window's hand-over to the open
curator, and is left to the window: it is never on Prepare's or Measure's plan.
"""

import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
if getattr(sys, "frozen", False):
    SCRIPTS = os.path.join(getattr(sys, "_MEIPASS", HERE), "scripts")
else:
    SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
for p in (HERE, SCRIPTS):
    if p not in sys.path:
        sys.path.insert(0, p)

import ls_project as LP                     # noqa: E402
import stages as S                          # noqa: E402
import workflow as W                        # noqa: E402
from runner import Runner                   # noqa: E402

USAGE = "usage: python -m app --run <%s> <study.json>"


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def study(path, runner):
    """The study as the screens see it, read from disk now."""
    cfg = LP.Project.open(path).cfg
    return W.Study(cfg.get("out_root", ""), runner.names(), cfg)


def run_screen(path, screen, runner=None, out=None):
    """Run `screen`'s plan for the study at `path`. [(sid, ok, seconds, error)]."""
    out = out or sys.stdout
    path = os.path.abspath(path)
    os.environ["LS_CONFIG"] = path
    runner = runner or Runner(SCRIPTS, path)
    plan, held = W.run_plan(screen, study(path, runner))
    say = lambda text: (out.write(text + "\n"), out.flush())     # noqa: E731
    say(f"{len(plan)} stage(s) to run: {', '.join(plan) or 'none'}")
    for sid, waits in held.items():
        say(f"  held: {sid} waits for {waits}")
    results = []
    for n, sid in enumerate(plan, 1):
        base = S.BY_ID[sid]
        now = study(path, runner)
        saved = LP.Project.open(path).stage_options(sid)
        values = W.run_values(sid, saved, now)
        inputs = W.run_inputs(sid, values, now)
        say(f"=== [{n}/{len(plan)}] {base.title}" + (f"  options: {values}" if values else ""))
        started = now_iso()
        res = runner.run(W.with_options(base, values), on_line=say)
        try:
            LP.Project.open(path).record_run(sid, res.ok, started, now_iso(),
                                             options=values, inputs=inputs)
        except LP.ProjectError as exc:
            say(f"could not record the run in the study: {exc}")
        results.append((sid, bool(res.ok), res.seconds, res.error))
        if not res.ok:
            say(f"--- {base.title}: FAILED after {res.seconds:.0f}s\n{res.error}")
            break
        say(f"--- {base.title}: done in {res.seconds:.0f}s")
        hold = W.run_hold(sid, plan[n:], study(path, runner))
        if hold:
            say(f"--- {hold[0]}\n{hold[1]}")
            results.append((sid, False, 0.0, hold[0]))
            break
    return results


def main(argv):
    """`--run <screen> <study.json>` from argv; the exit status."""
    screens = [s.sid for s in W.STEPS if s.stage_ids()]
    try:
        i = argv.index("--run")
        screen, path = argv[i + 1], argv[i + 2]
    except (ValueError, IndexError):
        sys.stderr.write(USAGE % "|".join(screens) + "\n")
        return 2
    if screen not in screens:
        sys.stderr.write(f"no screen called {screen!r}; there are: {', '.join(screens)}\n")
        return 2
    if not os.path.isfile(path):
        sys.stderr.write(f"no study file at {path}\n")
        return 2
    results = run_screen(path, screen)
    return 0 if all(ok for _sid, ok, _s, _e in results) else 1
