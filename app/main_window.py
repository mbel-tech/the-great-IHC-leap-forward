"""The application shell: a step bar down the left, one screen at a time on the right.

Deliberately a thin frame. What the pipeline is lives in `stages.py`; how it is
laid out for the operator, and explained, lives in `workflow.py`; running one
stage lives in `runner.py`; the screens themselves in `screens.py`. This file
decides what is on screen, and runs the queue of stages the screens ask for.

The previous layout - every stage in one list, in the order of the methods
figure - is still here as View > All stages.
"""

import json
import os

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QListWidgetItem, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QStackedWidget, QVBoxLayout,
    QWidget)

import ls_project as LP
import progress as PR
import stages as S
import state as ST
import theme as T
import workflow as W
from curator_view import CuratorView, LocalServer
from import_slides import ImportScreen
from project_session import ProjectSession, now_iso
from runner import Runner
from study_picker import reveal
import updates as UP
from update_ui import Updater
from screens import CurateScreen, HomeScreen, SetupScreen, StageListScreen
from widgets import COLOUR, QUIET, StepList, UnblindDialog


class _Job(QObject):
    """Runs one stage off the GUI thread."""
    line = Signal(str)
    finished = Signal(object)

    def __init__(self, runner, stage):
        super().__init__()
        self.runner, self.stage = runner, stage

    def run(self):
        res = self.runner.run(self.stage, on_line=self.line.emit)
        self.finished.emit(res)


class ActivityDrawer(QFrame):
    """The strip along the bottom: what is running, its last line, and the full log."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("drawer")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 4, 10, 4)
        top = QHBoxLayout()
        self.state = QLabel("Idle")
        f = self.state.font()
        f.setBold(True)
        self.state.setFont(f)
        top.addWidget(self.state)
        self.last = QLabel("")
        self.last.setStyleSheet(QUIET)
        self.last.setMinimumWidth(100)
        top.addWidget(self.last, 1)
        # How far the running stage has got. Busy (sliding) until the stage
        # prints a count, then filled; see progress.py for what counts.
        self.bar = QProgressBar()
        self.bar.setObjectName("drawerProgress")
        self.bar.setTextVisible(False)
        self.bar.setFixedWidth(220)
        self.bar.setVisible(False)
        top.addWidget(self.bar)
        self.count = QLabel("")
        self.count.setStyleSheet(QUIET)
        self.count.setVisible(False)
        top.addWidget(self.count)
        self.stop_btn = QPushButton("Stop after this stage")
        self.stop_btn.setVisible(False)
        top.addWidget(self.stop_btn)
        self.toggle = QPushButton("Show log")
        self.toggle.setCheckable(True)
        self.toggle.toggled.connect(self._toggle)
        top.addWidget(self.toggle)
        lay.addLayout(top)
        self.log = QPlainTextEdit()
        self.log.setObjectName("log")
        self.log.setReadOnly(True)
        self.log.setFont(T.font(12, mono=True))
        self.log.setMaximumBlockCount(20000)
        self.log.setMinimumHeight(180)
        self.log.setVisible(False)
        lay.addWidget(self.log)

    def _toggle(self, on):
        self.log.setVisible(on)
        self.toggle.setText("Hide log" if on else "Show log")

    def start_progress(self):
        """A stage has started: a busy bar until it reports a count."""
        self.bar.setRange(0, 0)
        self.bar.setVisible(True)
        self.count.setText("")
        self.count.setVisible(False)

    def show_progress(self, p):
        self.bar.setRange(0, p.total)
        self.bar.setValue(p.done)
        self.count.setText(PR.words(p))
        self.count.setVisible(True)

    def stop_progress(self):
        self.bar.setVisible(False)
        self.count.setVisible(False)

    def append(self, text):
        self.log.appendPlainText(text)
        tail = (text or "").strip().splitlines()
        if tail:
            self.last.setText(tail[-1][:160])


class MainWindow(QMainWindow):
    def __init__(self, repo_root, scripts_dir=None, config_path=None):
        super().__init__()
        self.repo_root = repo_root
        # Whichever study the picker settled on. Falls back to the module's
        # own resolution so the window can still be opened directly in a test.
        import ls_config as LC
        self.config_path = os.path.abspath(config_path or LC.resolve_path())
        # Frozen, the stages ship inside the bundle while config.json sits beside
        # the executable, so the two roots are not the same folder.
        self.scripts_dir = scripts_dir or os.path.join(repo_root, "scripts")
        self.runner = Runner(self.scripts_dir, self.config_path)
        # The open project: the study config plus the app's record of working on it.
        # Held in memory; stages read the saved file, so a run saves it first.
        self.session = ProjectSession(LP.Project.open(self.config_path), self)
        self._pending_log = []
        for key, _old, new in self.session.project.reanchored:
            self._pending_log.append(
                f"paths updated for this location: {key} -> {new} (File > Save to keep)")
        self.session.changed.connect(self._update_title)
        self.session.opened.connect(self._project_opened)
        self.session.saved.connect(self._project_saved)
        self.out_root = self._config()["out_root"]
        line = self.session.sync_curation(self)
        if line:
            self._pending_log.append(f"curation: {line}")
        self.server = LocalServer(self.out_root)
        self._thread = None
        self._running = {}          # stage id -> running / queued / failed, this session
        self._queue = []
        self._queue_total = 0
        self._shortfalls = []
        self._stop_requested = False
        # Set when the window was asked to close mid-run: it closes once the
        # running stage has finished, never while its thread is still writing.
        self._close_when_idle = False

        self.resize(1400, 900)

        # ---- the screens ----------------------------------------------------
        self.curator = CuratorView(self.server, self.out_root,
                                   project_source=lambda: self.session.project)
        self.curator.logged.connect(self._log)
        self.importer = ImportScreen(self.session, self.runner)
        self.importer.logged.connect(self._log)
        self.importer.imported.connect(self.refresh)

        self.home = HomeScreen()
        self.home.goto.connect(self.goto)
        self.home.action.connect(self._home_action)
        self.setup = SetupScreen(self.repo_root, self.importer, self.session)
        self.setup.applied.connect(self.refresh)
        self.setup.logged.connect(self._log)
        self.curate = CurateScreen(self.curator)
        self.lists = {sid: StageListScreen(W.SCREENS[sid])
                      for sid in ("prepare", "measure", "results", "instrument", "all")}

        self.stack = QStackedWidget()
        self.pages = {"home": self.home, "setup": self.setup, "curate": self.curate}
        self.pages.update(self.lists)
        for sid in ("home", "setup", "prepare", "curate", "measure", "results",
                    "instrument", "all"):
            self.stack.addWidget(self.pages[sid])
        for scr in [self.curate] + list(self.lists.values()):
            scr.run_requested.connect(self.run_stages)
            scr.options_changed.connect(self._options_changed)
            scr.goto.connect(self.goto)
        for scr in self.lists.values():
            scr.open_requested.connect(self.open_curator)

        # ---- the step list ---------------------------------------------------
        # The six steps, then the two screens most people never need under
        # Advanced at the foot. Between 236 and 280 px: narrower and the
        # descriptions wrap to three lines, wider and the screens lose room.
        self.nav = StepList()
        for i, scr in enumerate(W.STEPS):
            it = QListWidgetItem(scr.title)
            it.setData(Qt.UserRole, scr.sid)
            self.nav.addItem(it)
            self.nav.set_step(i, title=scr.title, badge=str(i) if i else "⌂",
                              sub=scr.subtitle)
        self.nav.currentItemChanged.connect(self._nav_changed)
        steps = QFrame()
        steps.setObjectName("stepColumn")
        steps.setMinimumWidth(236)
        steps.setMaximumWidth(280)
        sl = QVBoxLayout(steps)
        sl.setContentsMargins(0, 8, 0, 10)
        sl.setSpacing(2)
        sl.addWidget(self.nav, 1)
        adv = QLabel("ADVANCED")
        adv.setObjectName("groupLabel")
        adv.setContentsMargins(16, 6, 0, 2)
        sl.addWidget(adv)
        self.advanced = {}
        for sid, label in (("all", "All stages"), ("instrument", "Instrument characterisation")):
            b = QPushButton(label)
            b.setObjectName("advButton")
            b.setCheckable(True)
            b.setToolTip(W.SCREENS[sid].intro)
            b.clicked.connect(lambda _=False, s=sid: self.goto(s))
            box = QHBoxLayout()
            box.setContentsMargins(6, 0, 6, 0)
            box.addWidget(b)
            sl.addLayout(box)
            self.advanced[sid] = b

        # ---- the activity drawer -------------------------------------------
        self.drawer = ActivityDrawer()
        self.drawer.stop_btn.clicked.connect(self._request_stop)
        self.log = self.drawer.log

        body = QWidget()
        hb = QHBoxLayout(body)
        hb.setContentsMargins(0, 0, 0, 0)
        hb.setSpacing(0)
        hb.addWidget(steps)
        hb.addWidget(self.stack, 1)
        # Help menu, the "a new version is ready" bar and the check behind them.
        self.updater = Updater(self, UP.install_root())
        central = QWidget()
        vb = QVBoxLayout(central)
        vb.setContentsMargins(0, 0, 0, 0)
        vb.setSpacing(0)
        vb.addWidget(self.updater.banner)
        vb.addWidget(body, 1)
        vb.addWidget(self.drawer)
        self.setCentralWidget(central)

        self._build_status_bar()
        self._build_menu()
        self._update_title()
        self.nav.setCurrentRow(0)
        self.refresh()
        for line in self._pending_log:
            self._log(line)
        self._log(f"repo    {repo_root}")
        self._log(f"out     {self.out_root}")
        self._log(f"serving {self.out_root} on 127.0.0.1:{self.server.port}")
        # Written every start so it tracks the key list, and so it is sitting
        # next to the curators when someone needs it rather than being something
        # they have to be told to generate.
        try:
            ST.write_export_page(self.out_root)
        except OSError as e:
            self._log(f"could not write the state export page: {e}")
        held = ST.CurationStore(self.out_root, lambda: self.session.project).all_present()
        self._log("curation state on disk: " + (", ".join(held) if held else "none yet"))

    # ---- status bar -------------------------------------------------------

    def _build_status_bar(self):
        """Study on the left, what is running in the middle, outputs on the right."""
        bar = self.statusBar()
        self.sb_study = QLabel("")
        self.sb_state = QLabel("Idle")
        self.sb_state.setAlignment(Qt.AlignCenter)
        self.sb_out = QLabel("")
        self.sb_out.setFont(T.font(12, mono=True))
        self.sb_out.setTextInteractionFlags(Qt.TextSelectableByMouse)
        for w in (self.sb_study, self.sb_state, self.sb_out):
            w.setContentsMargins(8, 0, 8, 0)
        bar.addWidget(self.sb_study)
        bar.addWidget(self.sb_state, 1)
        bar.addPermanentWidget(self.sb_out)
        self._set_run_state("Idle")

    def _set_run_state(self, text, state=None):
        """The status bar's middle: Idle in grey, or what runs in the running colour."""
        if not hasattr(self, "sb_state"):
            return
        self.sb_state.setText(text)
        self.sb_state.setStyleSheet(f"color:{COLOUR[state]}" if state else QUIET)

    # ---- menus ------------------------------------------------------------

    def _build_menu(self):
        """Kept on self deliberately.

        addMenu returns a QMenu that Python owns; without a reference here the
        wrapper is collected and the underlying C++ menu goes with it, so the
        menu bar ends up holding a deleted object. Same for the actions.
        """
        self._build_file_menu()
        self._actions = []

        self._view_menu = self.menuBar().addMenu("&View")
        for i, scr in enumerate(W.STEPS):
            act = self._view_menu.addAction(scr.title)
            act.setShortcut(QKeySequence(f"Ctrl+{i + 1}"))
            act.triggered.connect(lambda _=False, s=scr.sid: self.goto(s))
            self._actions.append(act)
        self._view_menu.addSeparator()
        act = self._view_menu.addAction("All stages")
        act.setToolTip("Every stage in one list, in the order of the methods figure")
        act.triggered.connect(lambda: self.goto("all"))
        self._actions.append(act)
        act = self._view_menu.addAction("Refresh status")
        act.setShortcut(QKeySequence("F5"))
        act.triggered.connect(self.refresh)
        self._actions.append(act)

        self._tools_menu = self.menuBar().addMenu("&Tools")
        for label, slot in (("Instrument characterisation", lambda: self.goto("instrument")),
                            ("All settings…", self._settings),
                            ("Switch study…", self._studies)):
            act = self._tools_menu.addAction(label)
            act.triggered.connect(slot)
            self._actions.append(act)
        self._migrate_menu = self._tools_menu.addMenu("Migrate browser curation")
        for label, slot in (("Import curation state…", self._import_state),
                            ("Where is my curation state?", self._explain_state)):
            act = self._migrate_menu.addAction(label)
            act.triggered.connect(slot)
            self._actions.append(act)
        self.updater.build_menu(self.menuBar())

    def _build_file_menu(self):
        """New / Open / Open Recent / Save / Save As / Revert - the project's own verbs."""
        self._file_menu = self.menuBar().addMenu("&File")
        self._file_actions = {}

        def add(key, label, slot, shortcut=None):
            act = self._file_menu.addAction(label)
            if shortcut:
                act.setShortcut(shortcut)
            act.triggered.connect(slot)
            self._file_actions[key] = act
            return act

        add("new", "&New project...", lambda: self._idle_for("start a new project")
            and self.session.new_project(self.repo_root, self), "Ctrl+N")
        add("open", "&Open project...", lambda: self._idle_for("open another project")
            and self.session.open_project(self, repo_root=self.repo_root), "Ctrl+O")
        self._recent_menu = self._file_menu.addMenu("Open &Recent")
        self._file_menu.aboutToShow.connect(self._fill_recent)
        self._file_menu.addSeparator()
        add("save", "&Save", lambda: self.session.save(self), "Ctrl+S")
        add("save_as", "Save &As...", lambda: self.session.save_as(self), "Ctrl+Shift+S")
        add("revert", "&Revert", self._revert)
        self._file_menu.addSeparator()
        add("history", "Project &history...", self._history)
        add("copy", "Export a &copy...", lambda: self.session.export_copy(self))
        self._fill_recent()

    def _revert(self):
        self.session.revert(self)
        self.setup.build()
        self.refresh()

    def _history(self):
        from project_history import ProjectHistory
        ProjectHistory(self.session, self).exec()

    def _home_action(self, key):
        if key in ("switch", "open", "new") and not self._idle_for("open another project"):
            return
        if key == "switch":
            self._studies()
        elif key == "open":
            self.session.open_project(self, repo_root=self.repo_root)
        elif key == "new":
            self.session.new_project(self.repo_root, self)
        elif key == "copy":
            self.session.export_copy(self)
        elif key == "history":
            self._history()

    def _fill_recent(self):
        import ls_config as LC
        self._recent_menu.clear()
        self._recent_actions = []
        paths = LC.recent_projects()
        for path in paths:
            act = self._recent_menu.addAction(os.path.basename(path))
            act.setToolTip(path)
            act.triggered.connect(lambda _=False, p=path: self._idle_for("open another project")
                                  and self.session.open_project(self, p))
            self._recent_actions.append(act)
        self._recent_menu.setEnabled(bool(paths))
        ro = self.session.project.read_only
        self._file_actions["save"].setEnabled(not ro)
        self._file_actions["revert"].setEnabled(self.session.dirty)

    def _update_title(self):
        self.setWindowTitle("The Great IHC Leap Forward  -  " + self.session.title())
        if hasattr(self, "_file_actions"):
            self._fill_recent()

    # ---- project changes ----------------------------------------------------

    def _project_opened(self, path):
        """A different project file is now open: point the stages and the screens at it."""
        if not path or os.path.normcase(os.path.abspath(path)) == os.path.normcase(self.config_path):
            return
        self.config_path = os.path.abspath(path)
        self.runner.rebind(self.config_path)
        self.setup.build()
        self._rebase()
        line = self.session.sync_curation(self)
        if line:
            self._log(f"curation: {line}")
            self.curator.refresh_seed()
        self._log(f"project is now {self.config_path}")

    def _project_saved(self):
        """After a save the stages see the new settings; re-root only if out_root moved."""
        new_root = self._config()["out_root"]
        line = self.session.sync_curation(self) if new_root != self.out_root else None
        if new_root != self.out_root:
            self._rebase()
            if line:
                self._log(f"curation: {line}")
            self._log(f"saved - out_root {self.out_root}, serving on 127.0.0.1:{self.server.port}; "
                      "stage modules reload on next run")
        else:
            self.refresh()

    def _idle_for(self, action):
        """True if no stage is running; otherwise say why `action` has to wait.

        Opening another project rebinds the runner, the server and the
        curation store while the running stage is still writing into the old
        study's out_root. Refused here, before the session opens anything: by
        the time _project_opened hears of it, the project has already changed.
        """
        if self._thread is None:
            return True
        QMessageBox.information(self, "A step is running",
                                "A step is still running. Wait for it to finish, or press "
                                f"Stop after this stage, before you {action}.")
        return False

    def _studies(self):
        """Switch to another study, or make one."""
        from study_picker import StudyPicker
        if not self._idle_for("switch study"):
            return
        if not self.session.confirm_discard(self, "switch study"):
            return
        dlg = StudyPicker(self.repo_root, self)
        if dlg.exec() and dlg.chosen and os.path.normcase(dlg.chosen) != \
                os.path.normcase(self.config_path):
            self.session.open_project(self, dlg.chosen, confirmed=True)

    def _rebase(self):
        """Re-root everything that was pointed at the previous out_root."""
        self.out_root = self._config()["out_root"]
        # The server, the curator view and the curation store were all rooted
        # at the old out_root; without this they keep serving and saving into
        # the tree that was just moved away from.
        self.server.stop()
        self.server = LocalServer(self.out_root)
        self.curator.rebase(self.server, self.out_root)
        self.refresh()

    def _study_name(self):
        try:
            return (self._config().get("study") or {}).get("name") or \
                os.path.splitext(os.path.basename(self.config_path))[0]
        except Exception:                                   # noqa: BLE001
            return "?"

    def _settings(self):
        from config_dialog import ConfigDialog
        if self.session.project.read_only:
            QMessageBox.information(self, "Read-only project",
                                    "This project was written by a newer version of the app and "
                                    "cannot be changed here.")
            return
        dlg = ConfigDialog(self.repo_root, path=self.config_path, parent=self,
                           project=self.session.project)
        if dlg.exec():
            self.session.touch()
            self.setup.build()
            self.refresh()
            self._log("settings applied to the project - saved automatically before the "
                      "next run, or with File > Save")

    def _options_changed(self, sid, values):
        """A stage's options were changed on screen: keep what differs from the defaults."""
        project = self.session.project
        if project.read_only:
            return
        try:
            clean = W.clean_options(sid, values)
        except ValueError as exc:
            self._log(f"option not kept: {exc}")
            return
        if clean == project.stage_options(sid):
            return
        project.set_stage_options(sid, clean)
        self.session.touch()
        self.refresh()

    def _import_state(self):
        """Take over curation done in a browser, once.

        The app cannot read another application's localStorage, so this is the
        only honest route: the browser exports, the app imports.
        """
        path, _ = QFileDialog.getOpenFileName(
            self, "curation_state.json exported from your browser",
            self.out_root, "JSON (*.json)")
        if not path:
            return
        try:
            keys = ST.CurationStore(self.out_root, lambda: self.session.project).import_bundle(path)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Could not import", str(e))
            return
        self.curator.refresh_seed()
        self._log(f"imported curation state: {', '.join(keys) or 'nothing'}")
        QMessageBox.information(
            self, "Curation state imported",
            f"{len(keys)} key(s) imported into\n"
            f"{os.path.join(self.out_root, 'curation')}\n\n"
            "Reopen a curator to see it.")

    def _explain_state(self):
        page = os.path.join(self.out_root, "reformatted",
                            "export_curation_state.html")
        QMessageBox.information(
            self, "Curation state",
            "Decisions you make in this app are saved into the project file itself, "
            "as you make them:\n"
            f"{self.config_path}\n\n"
            "The stages read plain copies of them in\n"
            f"{os.path.join(self.out_root, 'curation')}\n"
            "which the app rewrites from the project, so edit nothing there.\n\n"
            "Curation you did earlier in a web browser is still in that "
            "browser's storage, which no application can read from outside. "
            "To bring it over, open this page in that browser:\n\n"
            f"{page}\n\n"
            "then use Tools > Migrate browser curation > Import curation state.")

    # ---- helpers ----------------------------------------------------------

    def _config(self):
        with open(self.config_path, encoding="utf-8") as fh:
            return json.load(fh)

    def _log(self, text):
        self.drawer.append(text)

    def study(self):
        """The open study as the screens need it, read once per refresh."""
        cfg = self._config()
        return W.Study(cfg.get("out_root", ""), self.runner.names(), cfg)

    # ---- navigation ---------------------------------------------------------

    def _nav_changed(self, item, _prev):
        if item is not None:
            self._show(item.data(Qt.UserRole))

    def _show(self, sid):
        page = self.pages.get(sid)
        if page is not None:
            self.stack.setCurrentWidget(page)
        for key, b in self.advanced.items():
            b.setChecked(key == sid)
        if sid == "curate":
            self.curate.ensure_page()

    def goto(self, target):
        """Show a screen. `prepare:run` also starts its Run all remaining."""
        sid, _, then = target.partition(":")
        if sid not in self.pages:
            return
        row = next((i for i, s in enumerate(W.STEPS) if s.sid == sid), None)
        self.nav.blockSignals(True)
        if row is None:
            self.nav.clearSelection()
            self.nav.setCurrentRow(-1)
        else:
            self.nav.setCurrentRow(row)
        self.nav.blockSignals(False)
        self._show(sid)
        if then == "run" and sid in self.lists:
            self.refresh()
            plan, _held = W.run_plan(sid, self.study())
            if plan:
                self.run_stages(plan)

    def open_curator(self, sid):
        if sid in W.FOLDERS:
            self.open_folder(sid)
            return
        self.curate.show_curator(sid)
        self.goto("curate")

    def open_folder(self, sid):
        """Show the folder of pictures a stage made, in the file manager."""
        got = W.output_folder(sid, self._config())
        if not got or not os.path.isdir(got[1]):
            QMessageBox.information(
                self, "Nothing to open yet",
                "%s has not made any pictures yet%s." % (
                    W.display_title(S.BY_ID[sid]),
                    " (they will go to %s)" % got[1] if got else ""))
            return
        reveal(got[1])

    # ---- status -------------------------------------------------------------

    def refresh(self):
        self.out_root = self._config()["out_root"]
        # Asked ONCE for the whole sweep. Resolving it per stage would re-read
        # config.json for every stage on every refresh, and a refresh happens on
        # every stage completion.
        study = self.study()
        busy = self._thread is not None
        project = self.session.project
        for scr in self.lists.values():
            scr.refresh(study, self._running, project, busy)
        self.curate.refresh(study, self._running, project, busy)
        self.setup.update_marks(study)
        sd, st = self.setup.completion()
        self.home.refresh(study, project, sd, st, busy, self.setup.missing())
        for i, scr in enumerate(W.STEPS):
            if scr.sid == "home":
                words, complete = "", False
            else:
                done, total = (sd, st) if scr.sid == "setup" else W.step_progress(scr.sid, study)
                words = W.progress_words(scr.sid, done, total)
                complete = bool(total) and done >= total
            self.nav.set_step(i, title=scr.title, badge=str(i) if i else "⌂",
                              sub=scr.subtitle, progress=words, complete=complete)
        self.sb_study.setText(f"Study: {self._study_name()}")
        self.sb_out.setText(self.out_root)
        self.sb_out.setToolTip(f"Outputs are written to {self.out_root}")

    # ---- running ----------------------------------------------------------

    def run_stages(self, sids):
        """Queue stages to run in order, one at a time. Saves the project first."""
        if self._thread is not None:
            QMessageBox.information(self, "Already running",
                                    "A stage is running. Wait for it to finish, or press "
                                    "Stop after this stage.")
            return False
        stages = [S.BY_ID[s] for s in sids if s in S.BY_ID]
        if not stages:
            return False
        unblinding = [st for st in stages if st.unblinds]
        if unblinding and not self._confirm_unblind(unblinding):
            return False
        if not self.session.ensure_saved_for_run(self, auto=True):
            return False
        line = self.session.sync_curation(self)
        if line:
            self._log(f"curation: {line}")
        self._queue = [st.sid for st in stages]
        self._shortfalls = []
        self._queue_total = len(self._queue)
        self._stop_requested = False
        for sid in self._queue:
            self._running[sid] = "queued"
        self.drawer.stop_btn.setVisible(len(self._queue) > 1)
        # Stop disables itself when pressed, and a run that then fails ends in
        # _done's failure branch, which only hides it; without this the next
        # queue's Stop stays greyed out.
        self.drawer.stop_btn.setEnabled(True)
        self._run_next()
        return True

    def _confirm_unblind(self, unblinding):
        """Ask before the treatment groups are read, and which workbook to read.

        The workbook is the unblinding stage's own --xlsx option, so what is
        chosen here is what its Options panel shows, and is kept in the project.
        """
        names = ", ".join(W.display_title(st, self.study()) for st in unblinding)
        sid = "join_sampling" if any(st.sid == "join_sampling" for st in unblinding) else None
        project = self.session.project
        before = project.stage_options(sid).get("--xlsx", "") if sid else ""
        dlg = UnblindDialog(names, before, self)
        if not sid:
            dlg.field.setEnabled(False)
        if dlg.exec() != QDialog.Accepted:
            return False
        chosen = dlg.workbook()
        if sid and chosen != (before or ""):
            values = dict(project.stage_options(sid))
            values["--xlsx"] = chosen
            self._options_changed(sid, values)
        return True

    def _request_stop(self):
        self._stop_requested = True
        self.drawer.stop_btn.setEnabled(False)
        self.drawer.state.setText(self.drawer.state.text() + " - stopping after this stage")

    def _run_next(self):
        if not self._queue or self._stop_requested:
            for sid in self._queue:
                self._running.pop(sid, None)
            stopped = bool(self._queue)
            self._queue = []
            self.drawer.stop_btn.setVisible(False)
            self.drawer.stop_btn.setEnabled(True)
            self.drawer.state.setText("Stopped" if stopped else "Idle")
            self._set_run_state("Stopped" if stopped else "Idle")
            self.refresh()
            return
        sid = self._queue.pop(0)
        base = S.BY_ID[sid]
        values = self.session.project.stage_options(sid)
        st = W.with_options(base, values)
        n = self._queue_total - len(self._queue)
        self._run_started = now_iso()
        self._run_values = values
        self._running[sid] = "running"
        self._thread = QThread(self)
        self.refresh()
        title = W.display_title(base, self.study())
        self.drawer.state.setText(f"Running {title}"
                                  + (f"  ({n} of {self._queue_total})" if self._queue_total > 1 else ""))
        self.drawer.state.setStyleSheet(f"color:{COLOUR['running']}")
        self._set_run_state(f"Running: {title}"
                            + (f"  ({n} of {self._queue_total})" if self._queue_total > 1 else ""),
                            "running")
        self._log(f"\n=== {base.title} ===")
        if values:
            self._log(f"options: {values}")
        self._job = _Job(self.runner, st)
        self._job.moveToThread(self._thread)
        self._thread.started.connect(self._job.run)
        self._job.line.connect(self._stage_line)
        self.drawer.start_progress()
        self._row_progress(sid, None)
        # A bound method of the window, not a lambda: a lambda has no thread of
        # its own, so Qt would call it on the worker thread, which then tries to
        # wait on itself and starts the next stage's thread from inside it.
        self._current = base
        self._job.finished.connect(self._job_finished)
        self._thread.start()

    def _job_finished(self, res):
        self._done(self._current, res)
        if self._close_when_idle and self._thread is None:
            # Queued, not called: close() from inside the finished signal would
            # tear the window down while this slot is still on the stack.
            QTimer.singleShot(0, self.close)

    def _stage_line(self, line):
        """A line from the running stage: to the log, and to the bars if it is a count."""
        p = PR.parse(line)
        if p is None or not p.hidden:
            self._log(line)
        current = getattr(self, "_current", None)
        if p is not None and current is not None:
            self.drawer.show_progress(p)
            self._row_progress(current.sid, p)

    def _row_progress(self, sid, p):
        """The same count on the running stage's own row, wherever it is listed.

        None puts the row's bar back to busy, for a stage that has just started.
        """
        for scr in [self.curate] + list(self.lists.values()):
            row = getattr(scr, "rows", {}).get(sid)
            if not isinstance(getattr(row, "bar", None), QProgressBar):
                continue
            if p is None:
                row.bar.setRange(0, 0)
            else:
                row.bar.setRange(0, p.total)
                row.bar.setValue(p.done)
                row.bar.setToolTip(PR.words(p))

    def _done(self, st, res):
        self._thread.quit()
        self._thread.wait()
        self._thread = None
        self.drawer.stop_progress()
        problem = self.session.record_run(st.sid, res.ok, getattr(self, "_run_started", None),
                                          now_iso(), options=getattr(self, "_run_values", None))
        if problem:
            self._log(f"could not record the run in the project: {problem}")
        self.drawer.state.setStyleSheet("")
        if res.ok:
            self._running.pop(st.sid, None)
            self._log(f"--- {st.title}: done in {res.seconds:.1f}s")
            short = W.shortfall(st.sid, res.output)
            if short:
                self._log(f"--- {st.title}: {short}; the reasons are listed above")
                self._shortfalls.append(f"{W.display_title(st)}: {short}")
            # A stop already asked for (or a close waiting on this stage) ends the
            # run anyway, and a box here would only stand in the close's way.
            hold = None if self._stop_requested else W.run_hold(st.sid, self._queue,
                                                                self.study())
            if hold:
                # Not a failure: the stage finished and kept its outputs. The run
                # stops for a person to look before what comes next.
                for sid in self._queue:
                    self._running.pop(sid, None)
                self._queue = []
                self.drawer.stop_btn.setVisible(False)
                self.drawer.state.setText(hold[0])
                self.drawer.state.setStyleSheet(f"color:{COLOUR['waiting']}")
                self._set_run_state("Idle")
                self._log(f"--- {hold[0]}\n{hold[1]}")
                self.refresh()
                QMessageBox.information(self, hold[0], hold[1])
                return
            if st.curator and self.curate.current == st.sid:
                self.curator.show_page(st.curator, W.display_title(st, self.study()))
            self._run_next()
            if self._thread is None and not self._queue:
                done = f"Finished {self._queue_total} stage{'s' if self._queue_total != 1 else ''}"
                if self._shortfalls:
                    # Done, but not everything was made: say so where the eye is.
                    done += " (" + "; ".join(self._shortfalls) + ", see log)"
                    self.drawer.state.setStyleSheet(f"color:{COLOUR['waiting']}")
                self.drawer.state.setText(done)
                self._set_run_state("Idle")
            return
        self._running[st.sid] = "failed"
        for sid in self._queue:
            self._running.pop(sid, None)
        self._queue = []
        self.drawer.stop_btn.setVisible(False)
        self.drawer.state.setText(f"{W.display_title(st)} failed")
        self.drawer.state.setStyleSheet(f"color:{COLOUR['failed']}")
        self._set_run_state(f"Failed: {W.display_title(st)}", "failed")
        self._log(f"--- {st.title}: FAILED\n{res.error}")
        self.refresh()
        QMessageBox.warning(self, f"{W.display_title(st)} failed",
                            (res.error or "").split("\n\n")[0][:600]
                            + "\n\nThe full message is in the log (Show log, bottom right).")

    def closeEvent(self, e):
        if self._thread is not None:
            # Closing now would destroy the QThread while the stage is still
            # writing, and Qt aborts the whole app ("QThread: Destroyed while
            # thread is still running"), leaving half-written outputs.
            e.ignore()
            box = QMessageBox(QMessageBox.Question, "A step is running",
                              "A step is still running. Stop after it finishes, or keep running?",
                              parent=self)
            stop = box.addButton("Stop after it finishes", QMessageBox.AcceptRole)
            keep = box.addButton("Keep running", QMessageBox.RejectRole)
            box.setDefaultButton(keep)
            box.exec()
            if box.clickedButton() is stop:
                self._close_when_idle = True
                if not self._stop_requested:
                    self._request_stop()
            return
        self._close_when_idle = False
        if not self.session.confirm_discard(self, "close"):
            e.ignore()
            return
        self.server.stop()
        super().closeEvent(e)
