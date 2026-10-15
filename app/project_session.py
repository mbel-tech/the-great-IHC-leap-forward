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
from PySide6.QtWidgets import QFileDialog, QMessageBox

import ls_bundle as LB
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

    def ensure_saved_for_run(self, parent=None, auto=False):
        """Stages read the file on disk, so a run needs the project saved.

        `auto` saves without asking: the window's Run buttons pass it, because a
        prompt between pressing Run and the run starting was the most common way
        to run a stage on the settings from before the last change. File >
        Revert and Project history still undo what was saved.
        """
        if not self.dirty:
            return True
        if auto:
            return self.save(parent)
        ans = BTN.question(
            parent, "Save before running?",
            "Stages read the saved project, not what is on screen. Save '%s' first?" % self.project.name,
            BTN.Save | BTN.Cancel, BTN.Save)
        return ans == BTN.Save and self.save(parent)

    # ---- new / open -----------------------------------------------------------

    def new_project(self, repo_root, parent=None):
        """Two folders, the section thickness and the atlas; the slides answer the rest.

        The study file is written into the output folder (study.json), so the whole
        study is that one folder. Choosing an output folder that already holds a
        study opens it instead.
        """
        if not self.confirm_discard(parent, "start a new project"):
            return False
        from new_study import NewStudyDialog
        dlg = NewStudyDialog(parent)
        if not dlg.exec() or not dlg.path:
            return False
        if dlg.opened:
            return self.open_project(parent, dlg.path, confirmed=True, repo_root=repo_root)
        self.set_project(dlg.project)
        return True

    def open_project(self, parent=None, path=None, confirmed=False, repo_root=None):
        """Open a project file (or a zip copy of one). Returns True if it is now the open one."""
        if not confirmed and not self.confirm_discard(parent, "open another project"):
            return False
        if path is None:
            path, _ = QFileDialog.getOpenFileName(parent, "Open project",
                                                  os.path.dirname(self.project.path or "") or LC.studies_dir(),
                                                  "Project (*.json *.zip)")
            if not path:
                return False
        if path.lower().endswith(".zip"):
            return self.open_bundle(path, parent, repo_root)
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

    # ---- sharing -----------------------------------------------------------------

    def export_copy(self, parent=None):
        """Write a shareable copy: the project file (blinded by default), or a zip with extras."""
        from bundle_dialog import ExportCopyDialog
        project = self.project
        if not project.path:
            if not self.save_as(parent):
                return None
        elif project.dirty and not self.ensure_saved_for_run(parent):
            return None
        out_root = project.get("out_root")
        out_root = out_root if isinstance(out_root, str) and os.path.isdir(out_root) else None
        dlg = ExportCopyDialog(project.cfg, out_root, parent)
        if not dlg.exec():
            return None
        c = dlg.choices()
        zipped = bool((c["operator_csvs"] or c["atlases"]) and out_root)
        stem = os.path.splitext(os.path.basename(project.path))[0] + ("-copy" if c["blinded"] else "-UNBLINDED-copy")
        start = os.path.join(os.path.dirname(project.path), stem + (".zip" if zipped else ".json"))
        dest, _ = QFileDialog.getSaveFileName(parent, "Save the copy as", start,
                                              "Bundle (*.zip)" if zipped else "Project (*.json)")
        if not dest:
            return None
        if not dest.lower().endswith(".zip" if zipped else ".json"):
            dest += ".zip" if zipped else ".json"
        try:
            if zipped:
                LB.build(project.path, out_root, dest, blinded=c["blinded"],
                         atlases=c["atlases"], operator_csvs=c["operator_csvs"])
            else:
                LB.export_copy(project.path, dest, blinded=c["blinded"])
        except (LB.BundleError, OSError) as exc:
            BTN.warning(parent, "Copy not written", str(exc))
            return None
        BTN.information(parent, "Copy written", "%s\n\n%s" % (dest, "Blinded: the treatment key is not in it."
                        if c["blinded"] else "UNBLINDED: it contains the treatment key."))
        return dest

    def open_bundle(self, path, parent=None, repo_root=None):
        """Open a zip copy as a NEW project: set its paths, save it, then bring its extras in."""
        try:
            manifest = LB.inspect(path)
            cfg = LB.project_from(path)
        except (LB.BundleError, OSError) as exc:
            BTN.warning(parent, "Could not open the bundle", str(exc))
            return False
        project = LP.Project(cfg)
        project._snapshot = None
        name = (manifest.get("project") or {}).get("name") or "project"
        dest, _ = QFileDialog.getSaveFileName(parent, "Save the project from this bundle as",
                                              os.path.join(LC.studies_dir(), LC.slugify(name) + ".json"),
                                              "Project (*.json)")
        if not dest:
            return False
        if not dest.lower().endswith(".json"):
            dest += ".json"
        if os.path.exists(dest):
            BTN.warning(parent, "Already exists", "%s already exists. Choose another name." % dest)
            return False
        from config_dialog import ConfigDialog
        dlg = ConfigDialog(repo_root or LC.REPO, path=dest, parent=parent, project=project)
        if not dlg.exec():
            return False
        try:
            project.save_as(dest)
        except LP.ProjectError as exc:
            BTN.warning(parent, "Not saved", str(exc))
            return False
        self.set_project(project)
        out_root = project.get("out_root")
        if isinstance(out_root, str) and os.path.isdir(out_root):
            try:
                plan = LB.plan_import(path, out_root)
                differ = [a for a, _t, st in plan if st == "differs"]
                replace = None
                if differ and BTN.question(
                        parent, "Replace files?",
                        "%d file(s) in the data folder differ from the bundle's:\n%s\n\nReplace them? "
                        "(Yes: replaced after a backup. No: yours are kept.)" % (len(differ), "\n".join(differ[:8])),
                        BTN.Yes | BTN.No, BTN.No) != BTN.Yes:
                    replace = set()
                LB.apply_import(path, out_root, replace)
            except (LB.BundleError, OSError) as exc:
                BTN.warning(parent, "Extras not imported", str(exc))
        return True

    # ---- curation ---------------------------------------------------------------

    def sync_curation(self, parent=None):
        """Make the project's curation and the copies in out_root agree. Returns a log line, or None.

        Curation lives in the project file; the stages read plain copies under
        out_root/curation. Opening, saving and running all call this. Anything that is simply
        stale is rewritten, a copy edited by something else while the project was untouched is
        adopted, and only a real disagreement (both changed) is put to the person; whichever
        version loses is kept in curation/_backup_<time>/.
        """
        project = self.project
        out_root = project.get("out_root")
        if project.read_only or not isinstance(out_root, str) or not os.path.isdir(out_root):
            return None
        try:
            result = project.reconcile_curation(out_root)
            resolve = {}
            if result.conflict:
                keys = ", ".join(result.conflict)
                ans = BTN.question(
                    parent, "Curation differs",
                    "The curation saved in this project and the files in\n%s\ndiffer for: %s.\n\n"
                    "Both changed since they last agreed. Use the project's version?\n\n"
                    "Yes = the project's version (the files are replaced)\n"
                    "No = the files' version (the project is updated)\n"
                    "The version you do not choose is kept in a backup folder beside them."
                    % (os.path.join(out_root, "curation"), keys),
                    BTN.Yes | BTN.No | BTN.Cancel, BTN.Yes)
                if ans == BTN.Yes:
                    resolve = {k: "project" for k in result.conflict}
                elif ans == BTN.No:
                    resolve = {k: "files" for k in result.conflict}
            done = project.apply_reconcile(out_root, result, resolve)
        except (LP.ProjectError, OSError) as exc:
            BTN.warning(parent, "Curation not synchronised", str(exc))
            return None
        self.changed.emit()
        bits = []
        if done["adopted"]:
            bits.append("took %d curation file(s) into the project (%s)" % (len(done["adopted"]), ", ".join(done["adopted"])))
        if done["restored"]:
            bits.append("rewrote %d curation file(s) from the project" % len(done["restored"]))
        left = [k for k in result.conflict if k not in done["restored"] and k not in done["adopted"]]
        if left:
            bits.append("left %s as they are (undecided)" % ", ".join(left))
        if done["backup"]:
            bits.append("previous versions kept in %s" % done["backup"])
        return "; ".join(bits) or None

    # ---- the run history ----------------------------------------------------------

    def record_run(self, stage, ok, started, ended, options=None):
        """Append to the run history. Returns an error message, or None."""
        try:
            self.project.record_run(stage, ok, started, ended, options=options)
        except LP.ProjectError as exc:
            return str(exc)
        self.changed.emit()
        return None


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
