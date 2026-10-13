"""The open project, and every question the window has to ask about it.

`ls_project.Project` knows what a project is; this holds the one that is open,
tells the window when it changes, and carries the flows that need a person to
answer something - New, Open, Save, Save As, "you have unsaved changes". They live
here and not in the main window so they can be driven without one (the window needs
QtWebEngine; this does not).

Stages read the project file from disk, never from memory. So a project with unsaved
changes is not what a stage would see, and running one asks to save first.
"""

import os
from datetime import datetime, timezone

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

import ls_config as LC
import ls_project as LP

BTN = QMessageBox


class ProjectSession(QObject):
    changed = Signal()             # anything the title or menus show
    dirtyChanged = Signal(bool)
    saved = Signal()
    opened = Signal(str)           # a different project is now the open one

    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.project = project
        self._was_dirty = project.dirty
        if project.path:
            LC.remember_project(project.path)

    # ---- state ----------------------------------------------------------------

    @property
    def dirty(self):
        return self.project.dirty

    def title(self):
        p = self.project
        return "%s%s  -  %s%s" % (p.name, "*" if p.dirty else "", p.path or "not saved yet",
                                  "  (read-only)" if p.read_only else "")

    def touch(self):
        """Something changed the project in memory: tell the window."""
        now = self.project.dirty
        if now != self._was_dirty:
            self._was_dirty = now
            self.dirtyChanged.emit(now)
        self.changed.emit()

    def set_project(self, project):
        self.project = project
        self._was_dirty = project.dirty
        if project.path:
            LC.remember_project(project.path)
        self.opened.emit(project.path or "")
        self.dirtyChanged.emit(self._was_dirty)
        self.changed.emit()

    # ---- saving ---------------------------------------------------------------

    def save(self, parent=None):
        """Write the project. True if it is saved afterwards."""
        if not self.project.path:
            return self.save_as(parent)
        try:
            self.project.save()
        except LP.ProjectConflict as exc:
            ans = BTN.question(parent, "The file changed on disk",
                               "%s\n\nSave over it anyway? (Choose No to keep the file as it is "
                               "and use File > Revert to read it again.)" % exc,
                               BTN.Yes | BTN.No, BTN.No)
            if ans != BTN.Yes:
                return False
            try:
                self.project.save(force=True)
            except LP.ProjectError as exc2:
                BTN.warning(parent, "Not saved", str(exc2))
                return False
        except LP.ProjectError as exc:
            BTN.warning(parent, "Not saved", str(exc))
            return False
        LC.remember_project(self.project.path)
        self._after_write()
        return True

    def save_as(self, parent=None):
        start = self.project.path or os.path.join(LC.studies_dir(), LC.slugify(self.project.name) + ".json")
        path, _ = QFileDialog.getSaveFileName(parent, "Save project as", start, "Project (*.json)")
        if not path:
            return False
        if not path.lower().endswith(".json"):
            path += ".json"
        overwrite = False
        if os.path.exists(path) and os.path.abspath(path) != self.project.path:
            if BTN.question(parent, "Replace file?", "%s already exists. Replace it?" % path,
                            BTN.Yes | BTN.No, BTN.No) != BTN.Yes:
                return False
            overwrite = True
        try:
            self.project.save_as(path, overwrite=overwrite)
        except LP.ProjectError as exc:
            BTN.warning(parent, "Not saved", str(exc))
            return False
        LC.remember_project(path)
        self._after_write()
        self.opened.emit(path)               # the window now points the stages at this file
        return True

    def _after_write(self):
        self._was_dirty = self.project.dirty
        self.dirtyChanged.emit(self._was_dirty)
        self.saved.emit()
        self.changed.emit()

    def revert(self, parent=None):
        if not self.dirty:
            return
        if BTN.question(parent, "Revert?", "Discard all changes made since the last save?",
                        BTN.Yes | BTN.No, BTN.No) != BTN.Yes:
            return
        try:
            self.project.revert()
        except LP.ProjectError as exc:
            BTN.warning(parent, "Cannot revert", str(exc))
            return
        self.touch()

    # ---- the questions ----------------------------------------------------------

    def confirm_discard(self, parent=None, action="continue"):
        """True if it is fine to go on: nothing unsaved, or the person saved or discarded it."""
        if not self.dirty:
            return True
        ans = BTN.question(
            parent, "Unsaved changes",
            "'%s' has unsaved changes.\n\nSave them before you %s?" % (self.project.name, action),
            BTN.Save | BTN.Discard | BTN.Cancel, BTN.Save)
        if ans == BTN.Cancel:
            return False
        if ans == BTN.Save:
            return self.save(parent)
        return True                          # Discard

    def ensure_saved_for_run(self, parent=None):
        """Stages read the file on disk, so a run needs the project saved."""
        if not self.dirty:
            return True
        ans = BTN.question(
            parent, "Save before running?",
            "Stages read the saved project, not what is on screen. Save '%s' first?" % self.project.name,
            BTN.Save | BTN.Cancel, BTN.Save)
        return ans == BTN.Save and self.save(parent)

    # ---- new / open -----------------------------------------------------------

    def new_project(self, repo_root, parent=None):
        """Name it, choose where it lives, fill in its settings; nothing is written until they are valid."""
        if not self.confirm_discard(parent, "start a new project"):
            return False
        name, ok = QInputDialog.getText(parent, "New project", "Name for this project:")
        name = (name or "").strip()
        if not ok or not name:
            return False
        try:
            start = os.path.join(LC.studies_dir(), LC.slugify(name) + ".json")
        except SystemExit as exc:
            BTN.warning(parent, "Name", str(exc))
            return False
        path, _ = QFileDialog.getSaveFileName(parent, "Where should the project file live?",
                                              start, "Project (*.json)")
        if not path:
            return False
        if not path.lower().endswith(".json"):
            path += ".json"
        if os.path.exists(path):
            BTN.warning(parent, "Already exists", "%s already exists. Open it instead, or choose "
                        "another name." % path)
            return False
        from config_dialog import ConfigDialog
        project = LP.Project.new(name)
        dlg = ConfigDialog(repo_root, path=path, study_name=name, parent=parent, project=project)
        if not dlg.exec():
            return False
        try:
            project.save_as(path)
        except LP.ProjectError as exc:
            BTN.warning(parent, "Not saved", str(exc))
            return False
        self.set_project(project)
        return True

    def open_project(self, parent=None, path=None, confirmed=False):
        """Open a project file. Returns True if it is now the open one."""
        if not confirmed and not self.confirm_discard(parent, "open another project"):
            return False
        if path is None:
            path, _ = QFileDialog.getOpenFileName(parent, "Open project",
                                                  os.path.dirname(self.project.path or "") or LC.studies_dir(),
                                                  "Project (*.json)")
            if not path:
                return False
        try:
            project = LP.Project.open(path)
        except LP.ProjectError as exc:
            BTN.warning(parent, "Could not open", str(exc))
            return False
        errors, _ = project.validate()
        if errors:
            BTN.warning(parent, "Opened with problems",
                        "This project has settings the pipeline would refuse:\n\n"
                        + "\n\n".join(errors[:5]) + "\n\nFix them in Settings before running a stage.")
        if project.reanchored:
            BTN.information(parent, "Paths updated for this location",
                            "This project's data was not where it was last saved, but was found beside "
                            "the project file, so these settings now point there:\n\n"
                            + "\n".join("  %s: %s" % (k, new) for k, _old, new in project.reanchored)
                            + "\n\nChoose File > Save to keep this.")
        if project.read_only:
            BTN.information(parent, "Read-only",
                            "This project was written by a newer version of the app. You can look "
                            "at it, but it cannot be changed or saved here.")
        self.set_project(project)
        return True

    # ---- the run history ----------------------------------------------------------

    def record_run(self, stage, ok, started, ended):
        """Append to the run history. Returns an error message, or None."""
        try:
            self.project.record_run(stage, ok, started, ended)
        except LP.ProjectError as exc:
            return str(exc)
        self.changed.emit()
        return None


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
