"""Run all remaining, one stage, or list the stages, without the window.

    python -m app --run prepare  C:\\study\\out\\study.json
    python -m app --run measure  C:\\study\\out\\study.json
    python -m app --stage overviews C:\\study\\out\\study.json [--opt limit=3]
    python -m app --list C:\\study\\out\\study.json

The same plan the screen's Run all remaining makes (workflow.run_plan), each
stage with the options saved in the study and Redo when it is out of date
(workflow.run_values), each run recorded in the study file as the window
records it. One stage at a time, its output printed as it comes; the first
failure stops the run, and so does a hold the window would stop at (the weak
signal check before detection). Unblinding is never planned, as on screen.

Exit status: 0 when every planned stage ran, 1 when one failed or a hold
stopped the run, 2 for a screen or study file that is not there.

`--stage` runs one stage as the window's Run button does: the options saved
in the study (and Redo when it is out of date), any `--opt key=value` on top,
the run recorded. It refuses (exit 2, nothing run or recorded) a stage this
study does not use, one the app does not run, an unblinding without
`--unblind`, a curator page (its path is printed instead), and one whose
needs are not done unless `--force`. `--list` prints each stage's state as the
window would show it.

`--stage <id> <study.json> --child --argv-json <list>` is what app/supervise.py
starts for a stage that reads CZI pixels: the stage, in that process, with
exactly that argv, its output on stdout and its error as the last line, and no
run record - the supervising parent records the run.

Nothing here touches Qt, so it runs where there is no display. A stage that
writes curation (04q's import) needs the window's hand-over to the open
curator, and is left to the window: it is never on Prepare's or Measure's plan.
"""

import copy
import json
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
import progress as PR                       # noqa: E402
import stages as S                          # noqa: E402
import workflow as W                        # noqa: E402
from runner import Runner                   # noqa: E402

USAGE = "usage: python -m app --run <%s> <study.json>"

HELP = """usage:
  python -m app                              open the window
  python -m app --run <screen> <study.json>  run all remaining on a screen (%s)
  python -m app --stage <id> <study.json> [--force] [--unblind] [--opt key=value]...
                                             run one stage, as its Run button does
                                             (--force: although its needs are not done;
                                             --unblind: a stage that unblinds)
  python -m app --list <study.json>          every stage, with its state

exit status: 0 ran, 1 failed or stopped, 2 refused or bad arguments, 130 Ctrl+C
"""

#: What a stop during a wait for the drive is recorded and reported as.
STOPPED_IN_PAUSE = "stopped while the drive was disconnected"


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def study(path, runner):
    """The study as the screens see it, read from disk now."""
    cfg = LP.Project.open(path).cfg
    return W.Study(cfg.get("out_root", ""), runner.names(), cfg)


def for_terminal(say):
    """`on_line` for a person at a terminal: the app's marker lines made readable.

    The runner asks the stages for `@@progress` counts (the window's bar) and a
    supervised child always prints them; here they are dropped. `@@drive`
    lines become the sentence a person reads. Never used for `--child`: its
    parent needs the markers as they are.
    """
    def line(text):
        drive = PR.parse_drive(text)
        if drive is not None:
            if drive[0] == "lost":
                say(f"slides drive unreachable ({drive[1]}), paused at {drive[2]}; "
                    "waiting, Ctrl+C to stop")
            else:
                say(f"drive back after {drive[1]:.0f} s, carrying on")
            return
        prog = PR.parse(text)
        if prog is not None and prog.hidden:
            return
        say(text)
    return line


def run_screen(path, screen, runner=None, out=None):
    """Run `screen`'s plan for the study at `path`. [(sid, ok, seconds, error)].

    A Ctrl+C inside a stage is recorded like any failed run and then raised
    again as KeyboardInterrupt, so the command line exits 130.
    """
    out = out or sys.stdout
    path = os.path.abspath(path)
    os.environ["LS_CONFIG"] = path
    runner = runner or Runner(SCRIPTS, path)
    plan, held = W.run_plan(screen, study(path, runner))
    say = lambda text: (out.write(text + "\n"), out.flush())     # noqa: E731
    shown = for_terminal(say)
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
        res = runner.run(W.with_options(base, values), on_line=shown)
        try:
            LP.Project.open(path).record_run(sid, res.ok, started, now_iso(),
                                             options=values, inputs=inputs,
                                             pauses=res.pauses, error=res.record_error())
        except LP.ProjectError as exc:
            say(f"could not record the run in the study: {exc}")
        results.append((sid, bool(res.ok), res.seconds, res.error))
        if getattr(res, "interrupted", False):
            say(f"--- {base.title}: stopped by Ctrl+C")
            raise KeyboardInterrupt
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


def _outcome(res):
    """(exit status, error to record) for a StageResult, from its flags.

    Runner.run never raises: a Ctrl+C or a stop during a wait for the drive
    comes back as a failed result marked `interrupted` or `stopped`.
    """
    if res.ok:
        return 0, None
    if getattr(res, "interrupted", False):
        return 130, res.record_error()
    if getattr(res, "stopped", False):
        return 1, STOPPED_IN_PAUSE
    return 1, res.record_error()


def _waits(stage, now):
    """(stage id it waits for, the window's words for it), or None.

    The window's hold (workflow.waits_for, as Run all remaining holds a stage):
    a need not done, out of date or partly done, or a curator not exported.
    """
    need = W.waits_for(stage, now)
    return (need, W.waiting_words(need, now)) if need else None


def _options(sid, given):
    """`--opt` values as the option table's flags and types; ValueError on a bad one."""
    opts = {o.flag: o for o in W.options_for(sid)}
    out = {}
    for key, value in (given or {}).items():
        flag = key if key.startswith("-") else "--" + key
        if flag not in opts:
            known = ", ".join(f[2:] for f in opts) or "none"
            raise ValueError(f"{sid} has no option {key!r} (its options: {known})")
        try:
            out[flag] = opts[flag].coerce(value)
        except ValueError as exc:
            raise ValueError(f"--opt {key}={value}: {exc}") from None
    return out


def _refusal(base, now, force, unblind):
    """Why `--stage` will not run `base` for this study, or None."""
    ok, why = W.applies(base, now)
    if not ok:
        return why
    reason = base.cli_reason()
    if reason:
        return f"{base.title} is not run by the app: {reason}"
    if base.unblinds and not unblind:
        return (f"{base.title} unblinds the study. It is never run without saying so: "
                "add --unblind to run it.")
    if base.curator:
        page = os.path.normpath(os.path.join(now.out_root, *base.curator.split("/")))
        return (f"{base.title} is a page you work in, not a stage to run. "
                f"Open it in a browser: {page}")
    if not base.script:
        return f"{base.title} has no script to run"
    waits = _waits(base, now)
    if waits and not force:
        return (f"{base.title} is waiting for {waits[0]}: {waits[1]}. "
                "Add --force to run it anyway.")
    return None


def _say_error(say, error):
    """A failure's text, its first line last: the supervisor keeps the last line."""
    lines = str(error or "stage failed").strip().splitlines() or ["stage failed"]
    for ln in lines[1:]:
        say(ln)
    say(lines[0])


def run_stage(path, sid, runner=None, out=None, force=False, unblind=False, opts=None,
              child=False, argv=None):
    """Run stage `sid` of the study at `path` as its Run button does. (ok, exit status).

    Refused (False, 2) with the reason printed, nothing run or recorded. With
    `child` it is the supervised child's side: `argv` in place of the stage
    table's, no refusals but an unknown id (the parent decided), no record.
    """
    out = out or sys.stdout
    say = lambda text: (out.write(text + "\n"), out.flush())     # noqa: E731
    path = os.path.abspath(path)
    os.environ["LS_CONFIG"] = path
    base = S.BY_ID.get(sid)
    if base is None:
        say(f"no stage called {sid!r}; python -m app --list {path} lists them")
        return False, 2
    if child:
        return _run_child_side(path, base, runner, say, argv)

    runner = runner or Runner(SCRIPTS, path)
    now = study(path, runner)
    why = _refusal(base, now, force, unblind)
    if why is None:
        try:
            given = _options(sid, opts)
        except ValueError as exc:
            why = str(exc)
    if why:
        say(why)
        return False, 2

    saved = LP.Project.open(path).stage_options(sid)
    values = W.run_values(sid, {**saved, **given}, now)
    inputs = W.run_inputs(sid, values, now)
    say(f"=== {base.title}" + (f"  options: {values}" if values else ""))
    started = now_iso()
    res = runner.run(W.with_options(base, values), on_line=for_terminal(say))
    rc, error = _outcome(res)
    try:
        LP.Project.open(path).record_run(sid, res.ok, started, now_iso(),
                                         options=values, inputs=inputs,
                                         pauses=res.pauses, error=error)
    except LP.ProjectError as exc:
        say(f"could not record the run in the study: {exc}")
    if res.ok:
        say(f"--- {base.title}: done in {res.seconds:.0f}s")
    else:
        say(f"--- {base.title}: FAILED after {res.seconds:.0f}s\n"
            f"{STOPPED_IN_PAUSE if error == STOPPED_IN_PAUSE else res.error}")
    return bool(res.ok), rc


def _run_child_side(path, base, runner, say, argv):
    """The stage in this process with exactly `argv`; never records."""
    runner = runner or Runner(os.environ.get("LS_SCRIPTS") or SCRIPTS, path)
    st = copy.copy(base)                 # as W.with_options does
    st.argv = list(argv or [])
    was = os.environ.get("LS_CHILD")
    os.environ["LS_CHILD"] = "1"         # so Runner runs it here, not in another child
    try:
        res = runner.run(st, on_line=say)
    finally:
        if was is None:
            os.environ.pop("LS_CHILD", None)
        else:
            os.environ["LS_CHILD"] = was
    rc, error = _outcome(res)
    if not res.ok:
        _say_error(say, STOPPED_IN_PAUSE if error == STOPPED_IN_PAUSE else res.error)
    return bool(res.ok), rc


def list_stages(path, out=None):
    """Print `id  group  title  state` for every stage, as the window sees it. Exit status 0."""
    out = out or sys.stdout
    path = os.path.abspath(path)
    os.environ["LS_CONFIG"] = path
    now = study(path, Runner(SCRIPTS, path))
    rows = []
    for st in S.STAGES:
        state = W.state(st, now)
        if state == "notused":
            word = "not for this study"
        elif state == "manual":
            word = f"cli only: {st.cli_reason()}"
        elif state == "outdated":
            word = "out of date"
        elif state == "done":
            word = "done"
        else:
            waits = _waits(st, now)
            word = f"blocked: {waits[0]} - {waits[1]}" if waits else "not run"
        if state == "done" and st.operator and not W.decided(st.sid, now):
            # As the window says it: the page is there, your export is not.
            word = "blocked: your export - page built; waiting for your export"
        rows.append((st.sid, st.group, st.title, word))
    wid, wg, wt = (max(len(r[i]) for r in rows) for i in range(3))
    for sid, group, title, word in rows:
        out.write(f"{sid:<{wid}}  {group:<{wg}}  {title:<{wt}}  {word}\n")
    out.flush()
    return 0


def _screens():
    return [s.sid for s in W.STEPS if s.stage_ids()]


def _main_run(argv):
    screens = _screens()
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


def _bad(text):
    sys.stderr.write(text + "\n\n" + HELP % "|".join(_screens()))
    return 2


def _main_stage(args):
    """`--stage <id> <study.json> [flags]`; the exit status."""
    i = args.index("--stage")
    if len(args) < i + 3 or args[i + 1].startswith("--") or args[i + 2].startswith("--"):
        return _bad("--stage takes a stage id and a study file")
    sid, path = args[i + 1], args[i + 2]
    rest = args[:i] + args[i + 3:]
    kw = {"opts": {}}
    j = 0
    while j < len(rest):
        a = rest[j]
        if a in ("--force", "--unblind", "--child"):
            kw[a[2:]] = True
        elif a in ("--opt", "--argv-json"):
            if j + 1 >= len(rest):
                return _bad(f"{a} takes a value")
            j += 1
            if a == "--opt":
                key, eq, value = rest[j].partition("=")
                if not eq or not key:
                    return _bad(f"--opt takes key=value, not {rest[j]!r}")
                kw["opts"][key] = value
            else:
                try:
                    kw["argv"] = json.loads(rest[j])
                except ValueError as exc:
                    return _bad(f"--argv-json is not JSON: {exc}")
                if not (isinstance(kw["argv"], list)
                        and all(isinstance(x, str) for x in kw["argv"])):
                    return _bad("--argv-json takes a JSON list of strings")
        else:
            return _bad(f"--stage does not take {a!r}")
        j += 1
    if kw.get("child") and "argv" not in kw:
        return _bad("--child needs --argv-json")
    if "argv" in kw and not kw.get("child"):
        return _bad("--argv-json is only for --child")
    if not os.path.isfile(path):
        sys.stderr.write(f"no study file at {path}\n")
        return 2
    return run_stage(path, sid, **kw)[1]


def main(argv):
    """`--run`, `--stage` or `--list` from argv; the exit status."""
    args = list(argv[1:])
    if "--help" in args or "-h" in args:
        sys.stdout.write(HELP % "|".join(_screens()))
        return 0
    try:
        if "--run" in args:
            return _main_run(argv)
        if "--stage" in args:
            return _main_stage(args)
        if "--list" in args:
            i = args.index("--list")
            if len(args) != i + 2 or i != 0:
                return _bad("--list takes a study file")
            if not os.path.isfile(args[i + 1]):
                sys.stderr.write(f"no study file at {args[i + 1]}\n")
                return 2
            return list_stages(args[i + 1])
        return _bad("nothing to do")
    except KeyboardInterrupt:
        sys.stderr.write("stopped (Ctrl+C)\n")
        return 130
    except Exception as exc:                                # noqa: BLE001
        if type(exc).__name__ != "Stopped":                 # ls_drive.Stopped
            raise
        sys.stdout.write(STOPPED_IN_PAUSE + "\n")
        return 1
