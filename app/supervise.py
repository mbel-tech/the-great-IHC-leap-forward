"""Run a CZI-reading stage in a child process the app watches over.

The slides are on a USB drive that drops. A stage that reads CZI pixels
(`reader` pylibCZIrw or czifile in stages.py) pauses for the drive and redoes
the slide by itself (scripts/ls_drive.py), but a read through native libCZI
can also crash the process outright as the volume goes - and in the app's own
process that is the window gone. So the runner starts those stages as

    python -m app --stage <id> <study.json> --child

and `run_child` supervises that process:

  * Its stdout and stderr, merged, reach `on_line` line by line as they come,
    with `\\r` counted as a line end (the runner's `_Tee` does the same in
    process).
  * Its `@@drive lost` / `@@drive back` lines are collected as the run's
    pauses: `{at, seconds, folder, label}`, `at` in ISO time.
  * Exit status 0 ran, 1 failed, 2 refused and 130 Ctrl+C are the child's own
    answers and are returned as they are. Any other status, or death by a
    signal, is abnormal. If it happens while a watched folder is unreachable
    (or while a loss the child announced is still open), the supervisor waits
    for the drive itself - saying `@@drive lost` through `on_line` if the
    child had not, and `@@drive back` when it returns - and starts the child
    again; the stage's own resumability skips the slides already done. With
    the drive there, an abnormal death is an ordinary failure.
  * The stop file (`LS_STOP_FILE` in the child) asks everything to give up:
    the child's own wait raises on it, the supervisor's wait checks it every
    poll, and a child that ends while it exists is never started again. A
    stop while the drive was gone is reported as STOPPED_IN_PAUSE, with the
    pause so far kept.

The supervisor's own wait does not call `ls_drive.wait_for_drive`: that prints
to this process's stdout (the app's, on a worker thread) and reads the stop
file from this process's environment, where neither is right. It polls
`ls_drive.reachable` instead.
"""

import codecs
import importlib.util
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

import progress as PR

HERE = os.path.dirname(os.path.abspath(__file__))
if getattr(sys, "frozen", False):
    SCRIPTS = os.path.join(getattr(sys, "_MEIPASS", HERE), "scripts")
else:
    SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")


def _drive_module():
    """The one ls_drive (as scripts/ls_io.drive_module keeps it)."""
    mod = sys.modules.get("ls_drive")
    if mod is not None:
        return mod
    spec = importlib.util.spec_from_file_location("ls_drive", os.path.join(SCRIPTS, "ls_drive.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ls_drive"] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop("ls_drive", None)
        raise
    return mod


DR = _drive_module()

#: The child's own answers. Anything else is a crash.
NORMAL_EXITS = (0, 1, 2, 130)

STOPPED_IN_PAUSE = "stopped while the drive was disconnected"
STOPPED = "stopped before the stage finished"

#: Where the child runs `-m app` from: the folder that holds the app package.
APP_PARENT = os.path.dirname(HERE)


def python_exe():
    """This interpreter, or the console `python.exe` beside a `pythonw.exe`.

    pythonw has no console, so a child started with it has nowhere to print
    and its output and exit status never reach the log.
    """
    exe = sys.executable
    if os.path.basename(exe).lower() == "pythonw.exe":
        console = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.exists(console):
            return console
    return exe


def child_argv(sid, study):
    """The command line that runs stage `sid` of `study` as a supervised child."""
    return [python_exe(), "-m", "app", "--stage", sid, study, "--child"]


def _iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


def _label(argv):
    try:
        return "stage " + argv[argv.index("--stage") + 1]
    except (ValueError, IndexError):
        return "the stage"


class _Run:
    """What one supervision has seen: lines, pauses, and a loss still open."""

    def __init__(self, on_line):
        self.on_line = on_line
        self.pauses = []
        self.open = None            # {"t0", "folder", "label"} after a lost, until back
        self.words = []             # non-empty, non-marker lines, for the error
        self._blank = False         # an empty line held back: it may precede a marker

    def line(self, text):
        mark = PR.parse_drive(text)
        if self._blank and not mark:
            self._emit("")
        self._blank = False
        if text == "":
            self._blank = True      # ls_drive starts each marker with a newline
            return
        if mark:
            self._mark(mark)
        elif text.strip():
            self.words.append(text.strip())
            del self.words[:-20]
        self._emit(text)

    def finish(self):
        if self._blank:
            self._emit("")
        self._blank = False

    def _emit(self, text):
        if self.on_line:
            self.on_line(text)

    def _mark(self, mark):
        if mark[0] == "lost":
            if self.open is None:
                self.open = {"t0": time.time(), "folder": mark[1], "label": mark[2]}
        elif self.open is not None:
            now = time.time()
            secs = mark[1]
            self.pauses.append({"at": _iso(now - secs), "seconds": round(secs, 1),
                                "folder": self.open["folder"], "label": self.open["label"]})
            self.open = None

    def say_lost(self, folder, label):
        """The supervisor's own `lost`, unless the child's is still open."""
        if self.open is None:
            self.line(f"@@drive lost {folder} | {label}")

    def say_back(self):
        """Close the open loss with a `back`, timed from when it was seen."""
        if self.open is not None:
            self.line(f"@@drive back {time.time() - self.open['t0']:.1f}")


def _stream(proc, run):
    dec = codecs.getincrementaldecoder("utf-8")(errors="replace")
    buf = ""
    fd = proc.stdout.fileno()
    while True:
        chunk = os.read(fd, 65536)
        text = dec.decode(chunk, final=not chunk)
        buf += text.replace("\r\n", "\n").replace("\r", "\n")
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            run.line(line)
        if not chunk:
            break
    if buf:
        run.line(buf)
    run.finish()
    proc.stdout.close()


def _launch(argv, env, cwd):
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, env=env, cwd=cwd, **kw)


def _tail(run, rc):
    last = run.words[-1] if run.words else ""
    if rc in NORMAL_EXITS:
        return last or f"stage exited with status {rc}"
    how = (f"was ended by signal {-rc}" if rc < 0 else f"exited with status {rc}")
    return f"the stage's process {how}" + (f"; its last line: {last}" if last else "")


def run_child(argv, on_line, stop_file, *, env=None, cwd=None, poll=None, on_proc=None):
    """Run `argv` as a supervised child; (rc, pauses, tail). Never raises for the child.

    `env` is added to this process's environment for the child (the runner
    passes LS_CONFIG); LS_CHILD, LS_PROGRESS and LS_STOP_FILE are set here.
    `on_proc` is handed each Popen as it starts, so a stop can terminate it.
    `tail` is the child's last line, or what went wrong; on a stop during a
    pause it is STOPPED_IN_PAUSE.
    """
    poll = DR.POLL if poll is None else poll
    child_env = dict(os.environ)
    child_env.update(env or {})
    child_env.update({"LS_CHILD": "1", "LS_PROGRESS": "1", "LS_STOP_FILE": stop_file,
                      "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"})
    run = _Run(on_line)
    label = _label(argv)
    stopped = lambda: bool(stop_file) and os.path.exists(stop_file)     # noqa: E731

    while True:
        if stopped():
            return 1, run.pauses, STOPPED
        proc = _launch(argv, child_env, cwd)
        if on_proc:
            on_proc(proc)
        if stopped():
            proc.terminate()        # a stop that came between the check and the start
        try:
            _stream(proc, run)
        finally:
            rc = proc.wait()
            if on_proc:
                on_proc(None)

        ok, folder = DR.reachable()
        drive_out = not ok or run.open is not None
        if stopped():
            # Checked before the status: a terminated child's status says
            # nothing about why (-15 on POSIX, 1 on Windows).
            if drive_out:
                if not ok:
                    run.say_lost(folder, label)
                run.say_back()
                return 1, run.pauses, STOPPED_IN_PAUSE
            return (0, run.pauses, _tail(run, 0)) if rc == 0 else (rc or 1, run.pauses, STOPPED)
        if rc in NORMAL_EXITS or not drive_out:
            run.say_back()          # a loss the child left open has ended with it
            return rc, run.pauses, _tail(run, rc)

        # Died abnormally with the drive gone: wait for it, then start again.
        run.line(f"the stage's process ended (status {rc}) while the drive was "
                 "disconnected; it starts again when the drive is back")
        if not ok:
            run.say_lost(folder, label)
        while not DR.reachable()[0]:
            if stopped():
                run.say_back()
                return 1, run.pauses, STOPPED_IN_PAUSE
            time.sleep(poll)
        run.say_back()
