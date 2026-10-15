"""Choosing which study the app is working on.

A study is one config file with a name. Everything the app then does - the
stage list, the local server, the curator pages - is rooted in that file's
`out_root`, so this is the first question the app has to answer and the one it
must never answer by guessing.

It replaces `ensure_config`, which asked only whether a usable `config.json`
existed. That was fine while there was one dataset. With several, "which one am
I looking at" has to be visible and deliberate, which is why the chosen study's
name sits in the status bar for the rest of the session.
"""

import json
import os
import subprocess
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog, QLabel,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout)

import ls_config as LC
import theme as T


def reveal(path):
    """Open a folder in the platform's file manager."""
    path = os.path.abspath(path)
    try:
        if sys.platform == "win32":
            os.startfile(path)                              # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except OSError:
        pass


class StudyPicker(QDialog):
    """Pick a study, make one, or bring a pre-study config in as one."""

    def __init__(self, repo_root, parent=None):
        super().__init__(parent)
        self.repo_root = repo_root
        self.chosen = None
        self.setWindowTitle("Studies")
        self.resize(560, 380)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _: self._open())

        where = QLabel(f"Studies live in {LC.studies_dir()}")
        where.setStyleSheet(T.HINT)
        where.setWordWrap(True)

        new = QPushButton("New study…")
        new.clicked.connect(self._new)
        self.migrate_btn = QPushButton("Import existing config…")
        self.migrate_btn.clicked.connect(self._migrate)
        show = QPushButton("Reveal folder")
        show.clicked.connect(lambda: reveal(LC.studies_dir()))

        row = QHBoxLayout()
        row.addWidget(new)
        row.addWidget(self.migrate_btn)
        row.addWidget(show)
        row.addStretch(1)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Open
                                        | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._open)
        self.buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Which study are you working on?"))
        lay.addWidget(self.list, 1)
        lay.addWidget(where)
        lay.addLayout(row)
        lay.addWidget(self.buttons)

        self._refresh()

    # ------------------------------------------------------------------
    def _refresh(self):
        self.list.clear()
        active = LC.read_settings().get("active_study")
        last = LC.read_settings().get("last_project")
        listed = set()
        for slug, name, path in LC.list_studies():
            item = QListWidgetItem(f"{name}    ({slug})")
            item.setData(Qt.UserRole, (slug, path))
            self.list.addItem(item)
            listed.add(os.path.normcase(os.path.abspath(path)))
            if slug == active:
                self.list.setCurrentItem(item)
        # Studies kept in their own output folder (study.json there) are found
        # through the recent list rather than the studies folder.
        for path in LC.recent_projects():
            if os.path.normcase(os.path.abspath(path)) in listed:
                continue
            item = QListWidgetItem(f"{_study_name(path)}    ({path})")
            item.setData(Qt.UserRole, (None, path))
            self.list.addItem(item)
            if last and os.path.normcase(path) == os.path.normcase(last):
                self.list.setCurrentItem(item)
        if self.list.count() and self.list.currentItem() is None:
            self.list.setCurrentRow(0)

        legacy = os.path.join(self.repo_root, "config.json")
        self.migrate_btn.setEnabled(os.path.exists(legacy))
        self.migrate_btn.setToolTip(
            f"Copy {legacy} into a named study" if os.path.exists(legacy)
            else "No pre-study config.json to import")
        self.buttons.button(QDialogButtonBox.Open).setEnabled(
            self.list.count() > 0)

    def _ask_name(self, title, prompt):
        name, ok = QInputDialog.getText(self, title, prompt)
        return name.strip() if ok and name.strip() else None

    def _new(self):
        """Two folders and the slides answer the rest; the study opens straight away."""
        from new_study import NewStudyDialog
        dlg = NewStudyDialog(self)
        if dlg.exec() != QDialog.Accepted or not dlg.path:
            return
        LC.remember_project(dlg.path)
        LC.clear_cache()
        self.chosen = dlg.path
        self.accept()

    def _migrate(self):
        """Bring the pre-study config.json in under a name, leaving it in place."""
        name = self._ask_name("Import existing config",
                              "Name for the study in config.json:")
        if not name:
            return
        try:
            path = LC.migrate(name)
        except SystemExit as exc:
            QMessageBox.warning(self, "Could not import", str(exc))
            return
        QMessageBox.information(
            self, "Imported",
            f"Written to {path}\n\nThe original config.json is untouched.")
        self._refresh()

    def _open(self):
        item = self.list.currentItem()
        if item is None:
            return
        slug, path = item.data(Qt.UserRole)
        if slug:
            LC.write_settings({"active_study": slug})
        LC.remember_project(path)
        LC.clear_cache()
        self.chosen = path
        self.accept()


def _study_name(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return (json.load(fh).get("study") or {}).get("name") or os.path.basename(path)
    except (OSError, ValueError, AttributeError):
        return os.path.basename(path)


def ensure_study(repo_root, parent=None):
    """The path of the study to work in, or None if the user backed out.

    A study already resolvable - LS_CONFIG, LS_STUDY, or the one last opened -
    is used without asking. Only a first run, or a settings file pointing at a
    study that has since been deleted, brings the picker up.
    """
    if not (os.environ.get("LS_CONFIG") or os.environ.get("LS_STUDY")):
        # The study open when the app last closed - which may live in its own
        # output folder, outside the studies folder the slug rule looks in.
        last = LC.read_settings().get("last_project")
        if isinstance(last, str) and os.path.isfile(last):
            return os.path.abspath(last)
    try:
        path = LC.resolve_path()
        if os.path.exists(path):
            return path
    except SystemExit:
        pass

    dlg = StudyPicker(repo_root, parent)
    if dlg.exec() != QDialog.Accepted:
        return None
    return dlg.chosen
