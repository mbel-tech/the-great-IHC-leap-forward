"""File > Export settings… and Import settings…: the windows over `ls_settings_io`.

Export asks what to include and where to write it. Import reads a file, shows every
setting it would change, refuses one that does not validate, and changes the open
study in memory (saved like any other edit) after copying the study file aside.
"""

import json
import os

from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QHeaderView, QLabel, QMessageBox,
    QTableWidget, QTableWidgetItem, QVBoxLayout)

import ls_config as LC
import ls_settings_io as SI
import theme as T

BTN = QMessageBox
FILTER = "Settings (*.json)"


def _short(value, n=90):
    text = json.dumps(value, ensure_ascii=False) if value is not None else "(not set)"
    return text if len(text) <= n else text[:n - 1] + "…"


class ExportSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export settings")
        intro = QLabel("Writes this study's settings to a JSON file you can import into another "
                       "study or send to someone. Curation, run history and what was read from the "
                       "slides are never included.")
        intro.setWordWrap(True)
        self.paths = QCheckBox("Include folder paths (they only work on this computer)")
        self.groups = QCheckBox("Include the treatment groups (unblinds the analysis)")
        self.app = QCheckBox("Include app preferences")
        self.app.setChecked(True)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Export…")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        for w in (intro, self.paths, self.groups, self.app, buttons):
            lay.addWidget(w)

    def choices(self):
        return {"include_paths": self.paths.isChecked(), "include_groups": self.groups.isChecked(),
                "include_app": self.app.isChecked()}


class ImportPreviewDialog(QDialog):
    """Shows what an import would change; `plan` is the one to apply after OK."""

    def __init__(self, project, data, path, parent=None):
        super().__init__(parent)
        self.project, self.data = project, data
        self.setWindowTitle("Import settings")
        self.resize(820, 520)
        src = data.get("from_study")
        head = QLabel("From %s%s" % (os.path.basename(path), (" (study “%s”)" % src) if src else ""))
        head.setStyleSheet(T.HINT)
        inc = data.get("includes") or {}
        self.paths = QCheckBox("Use the file's folder paths instead of this study's")
        self.paths.setEnabled(bool(inc.get("paths")))
        self.groups = QCheckBox("Use the file's treatment groups instead of this study's")
        self.groups.setEnabled(bool(inc.get("groups")))
        self.app = QCheckBox("Also apply the file's app preferences")
        self.app.setChecked(True)
        self.app.setEnabled(bool(data.get("app")))
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Setting", "Now", "After import"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Import")
        self.buttons.accepted.connect(self._ok)
        self.buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        for w in (head, self.paths, self.groups, self.app, self.summary):
            lay.addWidget(w)
        lay.addWidget(self.table, 1)
        lay.addWidget(self.buttons)
        for box in (self.paths, self.groups, self.app):
            box.stateChanged.connect(self._refresh)
        self._refresh()

    def _refresh(self, *_):
        self.plan = SI.plan_import(self.project, self.data, use_paths=self.paths.isChecked(),
                                   use_groups=self.groups.isChecked(), use_app=self.app.isChecked())
        p = self.plan
        rows = [(k, _short(old), _short(new)) for k, old, new in p.changes]
        rows += [("app: " + k, _short(LC.read_settings().get(k)), _short(v)) for k, v in p.app.items()]
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, text in enumerate(row):
                self.table.setItem(r, c, QTableWidgetItem(text))
        lines = []
        if p.errors:
            lines.append("<b>These settings would not work, so they cannot be imported:</b><br>"
                         + "<br>".join(p.errors))
        elif not p:
            lines.append("Nothing would change: this study already has these settings.")
        else:
            lines.append("%d setting(s) will change. The study file is copied aside first, and the "
                         "change is saved when you save the study." % len(rows))
        if p.kept:
            lines.append("Kept from this study: " + ", ".join(p.kept) + ".")
        if p.warnings and not p.errors:
            lines.append("Warnings: " + "; ".join(p.warnings[:5]))
        self.summary.setText("<br>".join(lines))
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(bool(p) and not p.errors)

    def _ok(self):
        if self.groups.isChecked() and self.groups.isEnabled():
            if BTN.warning(self, "Take the treatment groups?",
                           "This replaces the study's animal-to-treatment key with the file's. "
                           "Continue?", BTN.Yes | BTN.No, BTN.No) != BTN.Yes:
                return
        self.accept()


def export_settings(session, parent=None):
    """Ask, then write the export. Returns the path written, or None."""
    dlg = ExportSettingsDialog(parent)
    if not dlg.exec():
        return None
    project = session.project
    folder = project.folder() or LC.studies_dir()
    start = os.path.join(folder, LC.slugify(project.name) + "-settings.json")
    dest, _ = QFileDialog.getSaveFileName(parent, "Export settings as", start, FILTER)
    if not dest:
        return None
    if not dest.lower().endswith(".json"):
        dest += ".json"
    if project.path and os.path.normcase(os.path.abspath(dest)) == os.path.normcase(project.path):
        BTN.warning(parent, "Not exported", "Choose a different file: that one is the study itself.")
        return None
    try:
        SI.write_export(dest, SI.build_export(project, **dlg.choices()))
    except OSError as exc:
        BTN.warning(parent, "Not exported", str(exc))
        return None
    BTN.information(parent, "Settings exported", dest)
    return dest


def import_settings(session, parent=None):
    """Pick a file, preview, apply. Returns True if the study changed."""
    project = session.project
    if project.read_only:
        BTN.information(parent, "Read-only study", "This study is open read-only, so settings "
                        "cannot be imported into it.")
        return False
    start = project.folder() or LC.studies_dir()
    path, _ = QFileDialog.getOpenFileName(parent, "Import settings from", start, FILTER)
    if not path:
        return False
    try:
        data = SI.read_export(path)
    except SI.SettingsError as exc:
        BTN.warning(parent, "Not imported", str(exc))
        return False
    dlg = ImportPreviewDialog(project, data, path, parent)
    if not dlg.exec():
        return False
    try:
        backup = SI.apply_import(project, dlg.plan)
    except (SI.SettingsError, OSError) as exc:
        BTN.warning(parent, "Not imported", str(exc))
        return False
    session.touch()
    BTN.information(parent, "Settings imported",
                    "Imported. Save the study (File > Save) to keep the change."
                    + ("\n\nThe study file as it was is in:\n%s" % backup if backup else ""))
    return True
