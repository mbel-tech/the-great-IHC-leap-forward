"""The application shell: stages down the left, work on the right.

Deliberately a thin frame. Every judgement about the pipeline lives in
`stages.py`; everything about running one lives in `runner.py`. This file decides
only what is on screen.
"""

import json
import os

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QStackedWidget,
    QVBoxLayout, QWidget)

import ls_project as LP
import stages as S
import state as ST
from config_dialog import ConfigDialog
from curator_view import CuratorView, LocalServer
from import_slides import ImportScreen
from project_session import ProjectSession, now_iso
from runner import Runner

DOT = {"done": "#3fb950", "todo": "#8b949e", "cli": "#6e4d99",
       "running": "#4da3ff", "failed": "#f85149", "skip": "#484f58"}


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
        # Held in memory; stages read the saved file, so a run asks to save first.
        self.session = ProjectSession(LP.Project.open(self.config_path), self)
        for key, _old, new in self.session.project.reanchored:
            self._pending_log = getattr(self, "_pending_log", []) + [
                f"paths updated for this location: {key} -> {new} (File > Save to keep)"]
        self.session.changed.connect(self._update_title)
        self.session.opened.connect(self._project_opened)
        self.session.saved.connect(self._project_saved)
        self.out_root = self._config()["out_root"]
        line = self.session.sync_curation(self)
        if line:
            self._pending_log = getattr(self, "_pending_log", []) + [f"curation: {line}"]
        self.server = LocalServer(self.out_root)
        self._thread = None
        self._states = {}

        self.resize(1360, 860)

        # ---- left: the pipeline ------------------------------------------
        self.list = QListWidget()
        self.list.setAlternatingRowColors(False)
        self.list.currentItemChanged.connect(self._on_select)
        self.list.setMinimumWidth(330)

        self.run_btn = QPushButton("Run stage")
        self.run_btn.clicked.connect(self._run_current)
        self.refresh_btn = QPushButton("Refresh status")
        self.refresh_btn.clicked.connect(self.refresh)
        btns = QHBoxLayout()
        btns.addWidget(self.run_btn, 1)
        btns.addWidget(self.refresh_btn)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(6, 6, 6, 6)
        lv.addWidget(self.list, 1)
        lv.addLayout(btns)

        # ---- right: log, import, curators --------------------------------
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFont("Consolas", 9))
        self.log.setMaximumBlockCount(20000)

        self.blurb = QLabel("")
        self.blurb.setWordWrap(True)
        self.blurb.setStyleSheet("padding:6px")

        log_page = QWidget()
        lg = QVBoxLayout(log_page)
        lg.setContentsMargins(0, 0, 0, 0)
        lg.addWidget(self.blurb)
        lg.addWidget(self.log, 1)

        self.importer = ImportScreen(self.config_path, self.runner)
        self.importer.logged.connect(self._log)
        self.importer.imported.connect(self.refresh)

        self.curator = CuratorView(self.server, self.out_root,
                                   project_source=lambda: self.session.project)
        self.curator.logged.connect(self._log)

        self.stack = QStackedWidget()
        self.stack.addWidget(log_page)      # 0
        self.stack.addWidget(self.importer)  # 1
        self.stack.addWidget(self.curator)   # 2

        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(self.stack)
        split.setStretchFactor(1, 1)
        self.setCentralWidget(split)

        self._build_menu()
        self._update_title()
        self.statusBar().showMessage(
            f"{self._study_name()}   out_root  {self.out_root}")
        self._build_list()
        self.refresh()
        for line in getattr(self, "_pending_log", []):
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
        self._log(f"curation state on disk: "
                  + (", ".join(held) if held else "none yet"))

    # ---- menu -------------------------------------------------------------

    def _build_menu(self):
        """Kept on self deliberately.

        addMenu returns a QMenu that Python owns; without a reference here the
        wrapper is collected and the underlying C++ menu goes with it, so the
        menu bar ends up holding a deleted object. Same for the actions.
        """
        self._build_file_menu()
        self._menu = self.menuBar().addMenu("&Pipeline")
        self._actions = []
        for label, slot in (("Studies…", self._studies),
                            ("Settings…", self._settings),
                            (None, None),
                            ("Import curation state…", self._import_state),
                            ("Where is my curation state?", self._explain_state)):
            if label is None:
                self._menu.addSeparator()
                continue
            act = self._menu.addAction(label)
            act.triggered.connect(slot)
            self._actions.append(act)

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

        add("new", "&New project...", lambda: self.session.new_project(self.repo_root, self), "Ctrl+N")
        add("open", "&Open project...", lambda: self.session.open_project(self, repo_root=self.repo_root), "Ctrl+O")
        self._recent_menu = self._file_menu.addMenu("Open &Recent")
        self._file_menu.aboutToShow.connect(self._fill_recent)
        self._file_menu.addSeparator()
        add("save", "&Save", lambda: self.session.save(self), "Ctrl+S")
        add("save_as", "Save &As...", lambda: self.session.save_as(self), "Ctrl+Shift+S")
        add("revert", "&Revert", lambda: self.session.revert(self))
        self._file_menu.addSeparator()
        add("history", "Project &history...", self._history)
        add("copy", "Export a &copy...", lambda: self.session.export_copy(self))
        self._fill_recent()

    def _history(self):
        from project_history import ProjectHistory
        ProjectHistory(self.session, self).exec()

    def _fill_recent(self):
        import ls_config as LC
        self._recent_menu.clear()
        self._recent_actions = []
        paths = LC.recent_projects()
        for path in paths:
            act = self._recent_menu.addAction(os.path.basename(path))
            act.setToolTip(path)
            act.triggered.connect(lambda _=False, p=path: self.session.open_project(self, p))
            self._recent_actions.append(act)
        self._recent_menu.setEnabled(bool(paths))
        ro = self.session.project.read_only
        self._file_actions["save"].setEnabled(not ro)
        self._file_actions["revert"].setEnabled(self.session.dirty)

    def _update_title(self):
        self.setWindowTitle("LS IHC pipeline  -  " + self.session.title())
        if hasattr(self, "_file_actions"):
            self._fill_recent()

    def _project_opened(self, path):
        """A different project file is now open: point the stages and the screens at it."""
        if not path or os.path.normcase(os.path.abspath(path)) == os.path.normcase(self.config_path):
            return
        self.config_path = os.path.abspath(path)
        self.runner.rebind(self.config_path)
        self.importer.config_path = self.config_path
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
        self.statusBar().showMessage(f"{self._study_name()}   out_root  {self.out_root}")

    def _studies(self):
        """Switch to another study, or make one."""
        from study_picker import StudyPicker
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
        self.statusBar().showMessage(f"{self._study_name()}   out_root  {self.out_root}")

    def _study_name(self):
        try:
            return (self._config().get("study") or {}).get("name") or                 os.path.splitext(os.path.basename(self.config_path))[0]
        except Exception:                                   # noqa: BLE001
            return "?"

    def _settings(self):
        if self.session.project.read_only:
            QMessageBox.information(self, "Read-only project",
                                    "This project was written by a newer version of the app and "
                                    "cannot be changed here.")
            return
        dlg = ConfigDialog(self.repo_root, path=self.config_path, parent=self,
                           project=self.session.project)
        if dlg.exec():
            self.session.touch()
            self._log("settings applied to the project - choose File > Save to write them; "
                      "stages read the saved file")

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
            "then use Pipeline > Import curation state.")

    # ---- helpers ----------------------------------------------------------

    def _config(self):
        with open(self.config_path, encoding="utf-8") as fh:
            return json.load(fh)

    def _log(self, text):
        self.log.appendPlainText(text)

    def _build_list(self):
        self.list.clear()

        def header(text):
            head = QListWidgetItem(text.upper())
            head.setFlags(Qt.NoItemFlags)
            hf = head.font(); hf.setPointSize(max(7, hf.pointSize() - 1)); head.setFont(hf)
            head.setForeground(Qt.gray)
            self.list.addItem(head)

        # The figure's first band. Add slides is a screen rather than a stage,
        # so it is the one row with no script behind it.
        header("Acquisition")
        add = QListWidgetItem("  Add slides")
        add.setData(Qt.UserRole, "__import__")
        f = add.font(); f.setBold(True); add.setFont(f)
        self.list.addItem(add)
        for group in S.GROUPS:
            header(group)
            for st in S.in_group(group):
                it = QListWidgetItem("   " + st.title)
                it.setData(Qt.UserRole, st.sid)
                self.list.addItem(it)

    def _state_of(self, st, names=None):
        if self._states.get(st.sid) in ("running", "failed"):
            return self._states[st.sid]
        if names is None:
            names = self.runner.names()
        # A stage this study's acquisition layout does not have. Dimmed and
        # still listed, for the reason the stage table gives for cli_only
        # stages: hiding it turns "your study does not do this" into "this
        # does not exist". run_all.sh skips exactly the same list.
        if not st.applies(names.layout):
            return "skip"
        if st.cli_reason():
            return "cli"
        return "done" if st.done(self.out_root, names) else "todo"

    def refresh(self):
        self.out_root = self._config()["out_root"]
        # Asked ONCE for the whole sweep. Resolving it per stage would re-read
        # config.json 57 times on every refresh, and a refresh happens on every
        # stage completion.
        names = self.runner.names()
        for i in range(self.list.count()):
            it = self.list.item(i)
            sid = it.data(Qt.UserRole)
            if not sid or sid == "__import__":
                continue
            st = S.BY_ID[sid]
            state = self._state_of(st, names)
            # The figure's red outline: a person decides here. And the one
            # stage that reads the group key, marked so it cannot be run by
            # accident while the analysis is meant to be blind.
            mark = "  ✎" if st.operator else ("  ⚠ unblinds" if st.unblinds else "")
            it.setText(f"  ● {st.title}{mark}")
            it.setForeground(QColor(DOT[state]))
            it.setToolTip(st.cli_reason() or st.blurb)
        self.statusBar().showMessage(f"out_root  {self.out_root}")

    # ---- selection --------------------------------------------------------

    def _current_stage(self):
        it = self.list.currentItem()
        if not it:
            return None
        sid = it.data(Qt.UserRole)
        return S.BY_ID.get(sid) if sid and sid != "__import__" else None

    def _on_select(self, item, _prev):
        if not item:
            return
        sid = item.data(Qt.UserRole)
        if sid == "__import__":
            self.stack.setCurrentIndex(1)
            self.run_btn.setEnabled(False)
            return
        st = S.BY_ID.get(sid)
        if not st:
            return
        if st.curator:
            self.curator.show_page(st.curator, st.title)
            self.stack.setCurrentIndex(2)
        else:
            self.stack.setCurrentIndex(0)

        # One resolution for the whole panel: which markers this study has and
        # what its artifacts are called. Asked before anything uses it, because
        # `names` below is a list of stage TITLES and shadowing is how a panel
        # ends up calling .markers on a string.
        study = self.runner.names()
        blocked = S.blocked_by(st, self.out_root, study)
        reason = st.cli_reason()
        off_layout = not st.applies(study.layout)
        bits = [f"<b>{st.title}</b>", st.blurb]
        if st.operator:
            bits.append("<span style='color:#f85149'><b>An operator decides here.</b> "
                        "The page opens on the right; Export writes beside it.</span>")
        if st.unblinds:
            bits.append("<span style='color:#f85149'><b>Reads the treatment group.</b> "
                        "Nothing before this stage may.</span>")
        if off_layout:
            bits.append(
                "<span style='color:#8b949e'><b>Not part of this study.</b> "
                f"It applies to {' and '.join(st.layouts)} acquisition; this "
                f"study is {study.layout}.</span>")
        if reason:
            bits.append(f"<span style='color:#bc8cff'><b>Not run here.</b> {reason}</span>")
        elif blocked:
            names = ", ".join(S.BY_ID[b].title for b in blocked)
            bits.append(f"<span style='color:#d29922'>Usually run after: {names}"
                        "</span>")
        # The right-hand column of the figure: the file each stage leaves
        # behind, and which reader touched pixels to make it.
        if st.outputs:
            # The NAMES this study writes, not the table's tokens: a stage that
            # leaves `sections_{marker0}` is a sentence about the code, and the
            # operator is looking for a folder on their drive.
            shown = st.output_names(self.out_root, study)
            missing = set(st.missing(self.out_root, study))
            files = ", ".join(
                (f"<span style='color:#d29922'>{o}</span>" if o in missing else o)
                for o in shown)
            bits.append(f"<span style='color:#9aa0a8'>leaves: {files}</span>")
        if st.reader != "none":
            bits.append(f"<span style='color:#9aa0a8'>pixels read by {st.reader}</span>")
        if st.script:
            # The command as it would actually run: `--marker {marker0}`
            # is not something anyone can type.
            argv = " ".join(S.fill_markers(a, study.markers)
                            for a in st.argv)
            bits.append(f"<code>{st.script} {argv}</code>")
        self.blurb.setText("<br>".join(b for b in bits if b))
        self.run_btn.setEnabled(bool(st.script) and not reason and not off_layout)
        self.run_btn.setText("Regenerate" if st.curator else "Run stage")

    # ---- running ----------------------------------------------------------

    def _run_current(self):
        st = self._current_stage()
        if not st or self._thread:
            return
        if not self.session.ensure_saved_for_run(self):
            return
        line = self.session.sync_curation(self)
        if line:
            self._log(f"curation: {line}")
        self._run_started = now_iso()
        self._states[st.sid] = "running"
        self.refresh()
        self.run_btn.setEnabled(False)
        self._log(f"\n=== {st.title} ===")

        self._thread = QThread(self)
        self._job = _Job(self.runner, st)
        self._job.moveToThread(self._thread)
        self._thread.started.connect(self._job.run)
        self._job.line.connect(self._log)
        self._job.finished.connect(lambda res, s=st: self._done(s, res))
        self._thread.start()

    def _done(self, st, res):
        self._thread.quit()
        self._thread.wait()
        self._thread = None
        self._states[st.sid] = "done" if res.ok else "failed"
        problem = self.session.record_run(st.sid, res.ok, getattr(self, "_run_started", None), now_iso())
        if problem:
            self._log(f"could not record the run in the project: {problem}")
        if res.ok:
            self._log(f"--- {st.title}: done in {res.seconds:.1f}s")
        else:
            self._log(f"--- {st.title}: FAILED\n{res.error}")
            QMessageBox.warning(self, f"{st.title} failed",
                                (res.error or "").split("\n\n")[0][:600])
        if st.curator and res.ok:
            self.curator.show_page(st.curator, st.title)
        self.refresh()
        self.run_btn.setEnabled(True)

    def closeEvent(self, e):
        if not self.session.confirm_discard(self, "close"):
            e.ignore()
            return
        self.server.stop()
        super().closeEvent(e)
