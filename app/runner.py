"""Run a pipeline stage in this process, on a worker thread.

The stages are command-line scripts with numeric-prefixed names, so they cannot
be imported by `import 00_manifest`. `04o_section_rgb.py` already solves this for
its own use of `04a_reformat` - load by file path via importlib - and this is the
same trick generalised. No stage is renamed or otherwise altered to suit the app.

In-process rather than subprocess, for one reason that matters: the frozen build
has no external Python to shell out to. Running the stages as imported modules is
what lets a single bundled interpreter be the whole application.

The exception is a stage that reads CZI pixels (`reader` pylibCZIrw or czifile):
a native crash as the slides drive drops would take the window with it, so it
runs as a child process of this same interpreter, `python -m app --stage <id>
<study.json> --child --argv-json <argv>`, supervised by app/supervise.py, which starts it again if
it dies while the drive is gone. Inside that child (env `LS_CHILD=1`) the stage
runs in-process as before.

A stage run in this process can wait for the drive too (an ls_io write on
out_root); its `@@drive` lines are read by the supervisor's own parser
(supervise._Run), so those waits reach `StageResult.pauses` and the run
history as a child's do.

Two things the stages already do that this has to respect:

  * Several of them `raise SystemExit(message)` on bad input - `analysis_uids`
    does it for AF488, `04o` does it for a missing composite directory. That is a
    deliberate, informative failure, and it must land in the log as a failed
    stage rather than taking the window down with it.

  * They read config.json at import time into module-level constants. The import
    screen rewrites config, so a module imported before that would keep serving
    stale paths. Modules are therefore cached per config mtime and dropped when
    it changes.
"""

import contextlib
import importlib.util
import io
import json
import hashlib
import os
import sys
import tempfile
import threading
import time
import traceback

import stages as SG
import supervise as SV

#: Stages run in a supervised child process rather than in this one.
CHILD_READERS = ("pylibCZIrw", "czifile")


def _is_stopped(exc):
    """Whether `exc` is ls_drive.Stopped (the one module ls_io keeps)."""
    try:
        return isinstance(exc, SV._drive_module().Stopped)
    except Exception:                                       # noqa: BLE001
        return False


class StageResult:
    def __init__(self, sid, ok, seconds, output, error=None, pauses=None,
                 stopped=False, interrupted=False):
        self.sid = sid
        self.ok = ok
        self.seconds = seconds
        self.output = output
        self.error = error
        # Waits for a dropped drive: [{at, seconds, folder, label}].
        self.pauses = list(pauses or [])
        # Stopped while waiting for the drive (ls_drive.Stopped); ended by Ctrl+C.
        self.stopped = bool(stopped)
        self.interrupted = bool(interrupted)

    def record_error(self):
        """What the run history keeps of a failure: its first line, short. None if ok."""
        if self.ok:
            return None
        if self.stopped:
            return SV.STOPPED_IN_PAUSE
        if not self.error:
            return None
        first = str(self.error).strip().splitlines()
        return first[0][:300] if first else None


class _Tee(io.TextIOBase):
    """Collect stage output and hand each line to a callback as it appears.

    Line-buffered on purpose: the point of the log pane is watching a long stage
    progress, and a stage that prints a percentage every few seconds is useless
    if the text only lands when it finishes.
    """

    def __init__(self, on_line):
        self._on_line = on_line
        self._buf = ""
        self._all = []

    def write(self, s):
        if not s:
            return 0
        self._all.append(s)
        # A stage reporting progress prints "\r  12/454" with end="" and never
        # sends a newline until it is finished, so a carriage return has to
        # count as a line end here or the pane stays blank for the whole run.
        self._buf += s.replace("\r\n", "\n").replace("\r", "\n")
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if self._on_line:
                self._on_line(line)
        return len(s)

    def flush(self):
        if self._buf and self._on_line:
            self._on_line(self._buf)
            self._buf = ""

    def text(self):
        return "".join(self._all)


class Runner:
    """Loads and runs stages. One instance per application."""

    def __init__(self, scripts_dir, config_path):
        self.scripts_dir = scripts_dir
        self.config_path = os.path.abspath(config_path)
        self._modules = {}
        self._config_stamp = None
        self._lock = threading.Lock()
        # The running child, and the file that asks it (and its supervisor) to stop.
        self._proc = None
        self._stop_file = None
        # A stop that came while a child run was starting, before its stop file
        # had a name; honoured as soon as it has one.
        self._starting = False
        self._stop_pending = False
        self._stop_lock = threading.Lock()
        # The stages resolve config.json relative to their own file unless
        # LS_CONFIG says otherwise. Frozen, their own file is inside _internal/
        # and config.json is beside the executable, so without this every
        # stage fails at import with a missing file.
        os.environ["LS_CONFIG"] = self.config_path
        # Lets scripts/ls_progress.py print the counts the progress bar reads.
        # Unset on the command line, where those lines would only be noise.
        os.environ["LS_PROGRESS"] = "1"

    # ---- argv -------------------------------------------------------------

    def _config_dict(self):
        try:
            with open(self.config_path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def names(self):
        """This study's `ls_paths.Names` - markers, layout and artifact names.

        The app asks for this rather than reading `acquisition.markers` itself,
        because which markers a study HAS is layout-dependent (a multiplex
        study has no such list; its markers are the channel table's) and
        `ls_channels.marker_names` is the one place that knows. `ls_paths`
        wraps it and adds the pre-rename names, which is the other half of what
        the stage table needs.

        Not cached here: `for_config` memoises on the three facts a Names is
        built from, so re-asking after a config edit gets the new study and
        re-asking without one costs a dict lookup.
        """
        return SG.LP.for_config(self._config_dict())

    def markers(self):
        """The study's declared markers, in order. `()` if it cannot be read."""
        return tuple(self.names().markers)

    def expand_argv(self, argv):
        """Fill `{out_root}`, `{repo}`, `{scripts}` and `{markerN}` in argv.

        stages.py is static data and cannot know where the outputs live or what
        this study's markers are called; the stages that take absolute paths
        (04q's three exports, 06b's workbook) and the ten that take `--marker`
        get them here, from the same config the stage itself will read.

        The marker substitution is applied separately from the path ones and
        does NOT mark the argument as a path: `--marker {marker0}` becomes
        `--marker AF568`, and normpath on a bare word is a no-op today but
        would mangle a marker name containing a slash or a dot.
        """
        out_root = self._config_dict().get("out_root", "")
        subs = {"{out_root}": out_root,
                "{repo}": os.path.dirname(self.config_path),
                "{scripts}": self.scripts_dir}
        markers = self.markers()
        out = []
        for a in argv:
            a = SG.fill_markers(a, markers)
            hit = any(k in a for k in subs)
            for k, v in subs.items():
                a = a.replace(k, v)
            # normpath only on expanded paths: a plain argument such as "--n"
            # or "AF568" must reach the stage exactly as written.
            out.append(os.path.normpath(a) if hit else a)
        return out

    # ---- module loading ---------------------------------------------------

    def rebind(self, config_path):
        """Point every future stage at a different study."""
        self.config_path = os.path.abspath(config_path)
        os.environ["LS_CONFIG"] = self.config_path
        self._config_stamp = None
        self._forget()

    def _forget(self):
        """Drop the cached stage modules AND the config module they read.

        `ls_config` caches the parsed config and lives in `sys.modules`, which
        this class does not own. Clearing only `self._modules` would reload
        every stage and hand each one the config that was just replaced.
        """
        self._modules.clear()
        sys.modules.pop("ls_config", None)

    def _config_changed(self):
        """Whether the config file's CONTENT has changed since the last check.

        This was the modification time. Two edits inside one filesystem
        timestamp tick - which on a fast save is entirely possible - looked
        identical, so the cached stage modules were served with the previous
        config still baked into their constants. The file is a few kilobytes;
        hashing it costs nothing next to importing a stage.
        """
        try:
            with open(self.config_path, "rb") as fh:
                raw = fh.read()
            try:
                # The `project` block is the app's own record (run history, decision
                # trail). It changes every time a stage finishes, and no stage reads
                # it, so a hash that included it would call every run "a config change"
                # and reload every stage module for nothing.
                doc = json.loads(raw.decode("utf-8"))
                if isinstance(doc, dict):
                    doc.pop("project", None)
                    # Curation changes on every decision and no stage reads it from here
                    # (they read the copies written to out_root/curation).
                    doc.pop("curation", None)
                    raw = json.dumps(doc, sort_keys=True).encode("utf-8")
            except (ValueError, UnicodeDecodeError):
                pass                      # not JSON: hash the bytes, as before
            stamp = hashlib.sha256(raw).hexdigest()
        except OSError:
            stamp = None
        if stamp != self._config_stamp:
            self._config_stamp = stamp
            return True
        return False

    def load(self, script):
        """Import a stage script by path, reusing it unless config moved.

        The module name is prefixed because `00_manifest` is not a legal
        identifier and would collide with nothing meaningful anyway; keeping the
        prefix makes tracebacks say which stage they came from.
        """
        if self._config_changed():
            self._forget()
        if script in self._modules:
            return self._modules[script]

        path = os.path.join(self.scripts_dir, script)
        if not os.path.exists(path):
            raise FileNotFoundError(f"stage script not found: {path}")

        name = "lsstage_" + os.path.splitext(script)[0]
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        # Registered before exec so a stage that imports itself, or that another
        # stage imports, resolves to the same object rather than loading twice.
        sys.modules[name] = mod
        # The stages import each other by relative path and some read files
        # beside themselves, so run them with scripts/ importable.
        added = self.scripts_dir not in sys.path
        if added:
            sys.path.insert(0, self.scripts_dir)
        try:
            spec.loader.exec_module(mod)
        finally:
            if added:
                try:
                    sys.path.remove(self.scripts_dir)
                except ValueError:
                    pass
        self._modules[script] = mod
        return mod

    # ---- running ----------------------------------------------------------

    @contextlib.contextmanager
    def _watching(self):
        """LS_WATCH set to the study's source_dir and out_root for one run.

        ls_drive takes the folders to watch from LS_WATCH, else from the study
        file - which normally lives IN out_root and so cannot be read once the
        drive has gone. Named here, before the run and while the file can be
        read, neither the stage, nor a supervised child (it inherits this
        environment), nor the supervisor's own look at the drive has to read
        it mid-drop. An LS_WATCH already set is left as it is.
        """
        if os.environ.get("LS_WATCH"):
            yield
            return
        try:
            folders = SV._drive_module().config_folders(self.config_path)
        except Exception:                                   # noqa: BLE001
            folders = []
        if not folders:
            yield
            return
        os.environ["LS_WATCH"] = os.pathsep.join(folders)
        try:
            yield
        finally:
            os.environ.pop("LS_WATCH", None)

    def run(self, stage, on_line=None, extra_argv=()):
        """Run one stage to completion. Returns a StageResult; never raises.

        Serialised on a lock: the stages write to shared output directories and
        several of them rewrite the same index files, so two at once would be a
        race with the user's data on the losing side.
        """
        with self._watching():
            return self._run(stage, on_line, extra_argv)

    def _run(self, stage, on_line, extra_argv):
        # A callable is asked (05c: can this interpreter import StarDist?); the
        # function itself is truthy, and read as the answer it refused 05c always.
        why = stage.cli_only() if callable(stage.cli_only) else stage.cli_only
        if why:
            return StageResult(stage.sid, False, 0.0, "",
                               f"{stage.title} is not run by the app: {why}")
        if not stage.script:
            return StageResult(stage.sid, False, 0.0, "",
                               f"{stage.title} has no script to run")

        if (getattr(stage, "reader", None) in CHILD_READERS
                and not os.environ.get("LS_CHILD")):
            return self._run_child(stage, on_line, list(stage.argv) + list(extra_argv))

        argv = [stage.script] + self.expand_argv(list(stage.argv) + list(extra_argv))
        # The stage's `@@drive` lines, read by the supervisor's own parser, so
        # a wait in this process is in the result's pauses as a child's is.
        drive = SV._Run(on_line)
        tee = _Tee(drive.line)
        t0 = time.time()

        def done(res):
            drive.finish()
            drive.close_quietly()       # a loss still open ends with the stage
            res.pauses = drive.pauses
            return res

        # A stage run here can wait for a dropped drive too (ls_io.retry on
        # out_root); Stop reaches that wait through its own LS_STOP_FILE. In
        # the supervised child the supervisor's stop file is already set, and
        # is the one that counts.
        own_stop = not os.environ.get("LS_CHILD")
        if own_stop:
            with self._stop_lock:
                self._starting, self._stop_pending = True, False
        with self._lock:
            old_argv, old_cwd = sys.argv, os.getcwd()
            if own_stop:
                old_stop = os.environ.get("LS_STOP_FILE")
                stop_file = self._new_stop_file()
                os.environ["LS_STOP_FILE"] = stop_file
            try:
                mod = self.load(stage.script)
                sys.argv = argv
                # Some stages resolve paths relative to the scripts directory,
                # which is where run_all.sh cds to before calling them.
                os.chdir(self.scripts_dir)
                with contextlib.redirect_stdout(tee), contextlib.redirect_stderr(tee):
                    if not hasattr(mod, "main"):
                        raise AttributeError(
                            f"{stage.script} has no main() - it cannot be run in-process")
                    rc = mod.main()
                tee.flush()
                # Most stages end `sys.exit(main())` and refuse work with
                # `return 1`. In process nothing calls sys.exit, so the return
                # value is the only place that refusal shows up - read it as
                # SystemExit would be read, or the stage is recorded as done.
                # Only an int or a message counts: a few mains hand back data.
                failed = ((isinstance(rc, int) and not isinstance(rc, bool) and rc != 0)
                          or (isinstance(rc, str) and rc))
                if failed:
                    said = [ln.strip() for ln in tee.text().splitlines() if ln.strip()]
                    msg = rc if isinstance(rc, str) else (
                        said[-1] if said else f"stage exited with status {rc}")
                    return done(StageResult(stage.sid, False, time.time() - t0,
                                            tee.text(), msg))
                return done(StageResult(stage.sid, True, time.time() - t0, tee.text()))

            except SystemExit as e:
                # argparse exits 0 for --help; a stage exits non-zero, or with a
                # message, to refuse work it cannot do. Both are outcomes, not
                # crashes, and the message is the useful part.
                tee.flush()
                code = e.code
                if code in (0, None):
                    return done(StageResult(stage.sid, True, time.time() - t0, tee.text()))
                msg = code if isinstance(code, str) else f"stage exited with status {code}"
                return done(StageResult(stage.sid, False, time.time() - t0, tee.text(), msg))

            except BaseException as e:                      # noqa: BLE001
                tee.flush()
                if _is_stopped(e):
                    return done(StageResult(stage.sid, False, time.time() - t0, tee.text(),
                                            SV.STOPPED_IN_PAUSE, stopped=True))
                if isinstance(e, KeyboardInterrupt) and drive.open is not None:
                    # Ctrl+C during a wait for the drive: a stop in a pause.
                    return done(StageResult(stage.sid, False, time.time() - t0, tee.text(),
                                            SV.STOPPED_IN_PAUSE, interrupted=True))
                detail = "".join(traceback.format_exception(type(e), e, e.__traceback__))
                return done(StageResult(stage.sid, False, time.time() - t0,
                                        tee.text(), f"{type(e).__name__}: {e}\n\n{detail}",
                                        interrupted=isinstance(e, KeyboardInterrupt)))

            finally:
                if own_stop:
                    if old_stop is None:
                        os.environ.pop("LS_STOP_FILE", None)
                    else:
                        os.environ["LS_STOP_FILE"] = old_stop
                    self._drop_stop_file(stop_file)
                sys.argv = old_argv
                try:
                    os.chdir(old_cwd)
                except OSError:
                    pass

    # ---- in a child process -----------------------------------------------

    def _run_child(self, stage, on_line, argv):
        """Run a reader stage as a supervised child (app/supervise.py).

        The child is `python -m app --stage <id> <study.json> --child
        --argv-json <argv>`; `argv` is what the in-process path would expand
        (the options and extra arguments included), so the child runs exactly
        the stage this was asked for.
        """
        out = []

        def line(text):
            out.append(text + "\n")
            if on_line:
                on_line(text)

        def track(proc):
            self._proc = proc

        with self._stop_lock:
            self._starting, self._stop_pending = True, False
        with self._lock:
            stop_file = self._new_stop_file()
            t0 = time.time()
            try:
                rc, pauses, tail = SV.run_child(
                    SV.child_argv(stage.sid, self.config_path, argv), line, stop_file,
                    # LS_SCRIPTS: the child loads the stage from the same
                    # scripts folder this runner does.
                    env={"LS_CONFIG": self.config_path, "LS_SCRIPTS": self.scripts_dir},
                    cwd=SV.APP_PARENT, on_proc=track)
            except BaseException as e:                      # noqa: BLE001
                detail = "".join(traceback.format_exception(type(e), e, e.__traceback__))
                return StageResult(stage.sid, False, time.time() - t0, "".join(out),
                                   f"{type(e).__name__}: {e}\n\n{detail}",
                                   interrupted=isinstance(e, KeyboardInterrupt))
            finally:
                self._proc = None
                self._drop_stop_file(stop_file)
            # A stop during a wait for the drive is the supervisor's own verdict
            # (the stop file was there and the drive was out): it says so with
            # STOPPED_IN_PAUSE, never from the child's text alone.
            return StageResult(stage.sid, rc == 0, time.time() - t0, "".join(out),
                               None if rc == 0 else tail, pauses,
                               stopped=(rc == 1 and tail == SV.STOPPED_IN_PAUSE),
                               interrupted=(rc == 130))

    def _new_stop_file(self):
        """A fresh stop-file name for the run starting now (the file itself is
        absent: its existence is the request), with a stop that came while the
        run was starting honoured at once. Called holding self._lock."""
        fd, stop_file = tempfile.mkstemp(prefix="ls-stop-", suffix=".flag")
        os.close(fd)
        os.remove(stop_file)
        with self._stop_lock:
            self._stop_file = stop_file
            self._starting = False
            if self._stop_pending:
                open(stop_file, "w").close()
                self._stop_pending = False
        return stop_file

    def _drop_stop_file(self, stop_file):
        with self._stop_lock:
            self._stop_file = None
        try:
            os.remove(stop_file)
        except OSError:
            pass

    def stop(self):
        """Ask the running stage to stop now.

        Creates the run's stop file - a wait for the drive ends on it, in a
        child, its supervisor or a stage run in this process - and terminates
        the child if there is one. A stage in this process that is not waiting
        for the drive is not affected until it next waits.
        """
        with self._stop_lock:
            stop_file, proc = self._stop_file, self._proc
            if stop_file:
                try:
                    open(stop_file, "w").close()
                except OSError:
                    pass
            elif self._starting:
                self._stop_pending = True
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
