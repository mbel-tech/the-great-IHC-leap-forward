"""What to put in a shareable copy of the project.

A thin window over `ls_bundle`: it asks, shows what the answer would cost, and returns
the choices. Writing is `ls_bundle`'s job, started by `ProjectSession.export_copy`.
"""

from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QLabel, QMessageBox, QVBoxLayout)

import ls_bundle as LB
import theme as T


def _human(n):
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "bytes" else ("%.1f %s" % (n, unit))
        n /= 1024.0


class ExportCopyDialog(QDialog):
    """Choose what travels. `choices()` is {blinded, operator_csvs, atlases} after OK."""

    def __init__(self, cfg, out_root, parent=None):
        super().__init__(parent)
        self.cfg, self.out_root = cfg, out_root
        self.setWindowTitle("Export a copy")
        intro = QLabel(
            "A copy is the project file with your curation inside it, with the paths on this "
            "machine blanked (the person opening it sets their own) and your run history left out.")
        intro.setWordWrap(True)
        self.blinded = QCheckBox("Blinded: leave out the animal-to-treatment key")
        self.blinded.setChecked(True)
        self.operator = QCheckBox("Also include operator CSVs (layout, rotations, exclusions)")
        self.atlases = QCheckBox("Also include this study's imported atlases")
        self.size = QLabel("")
        self.size.setStyleSheet(T.HINT)
        self.size.setWordWrap(True)
        for box in (self.operator, self.atlases):
            box.stateChanged.connect(self._refresh)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._ok)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        for w in (intro, self.blinded, self.operator, self.atlases, self.size, buttons):
            lay.addWidget(w)
        self._refresh()

    def _refresh(self, *_):
        extras = self.operator.isChecked() or self.atlases.isChecked()
        if extras and self.out_root:
            n, total = LB.size_estimate(self.cfg, self.out_root, self.atlases.isChecked(), self.operator.isChecked())
            self.size.setText("The extras are %d file(s), %s, so the copy is a .zip." % (n, _human(total)))
        else:
            self.size.setText("Just the project file (a .json).")

    def choices(self):
        return {"blinded": self.blinded.isChecked(), "operator_csvs": self.operator.isChecked(),
                "atlases": self.atlases.isChecked()}

    def _ok(self):
        if not self.blinded.isChecked():
            ans = QMessageBox.warning(
                self, "Include the treatment key?",
                "This copy will contain which animal received which treatment. Anyone who opens it can "
                "unblind the analysis. Share it only with people who are allowed to know, and never put "
                "it in a repository.\n\nExport it unblinded?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ans != QMessageBox.Yes:
                return
        self.accept()
