"""The Add slides screen.

Pick a folder or a set of `.czi` files, see exactly what was understood, then
commit. The table is the point: `00_manifest` silently skips anything whose name
does not fit its grammar, so an unrecognised file is invisible to every stage
downstream and nothing ever mentions it. Here it is a red row.

Nothing is moved without the plan being shown first, in gigabytes.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

import slides as SL
import theme as T


class ImportScreen(QWidget):
    imported = Signal()          # config changed; the window should re-read status
    logged = Signal(str)

    def __init__(self, session, runner, parent=None):
        super().__init__(parent)
        # The open project (app/project_session.py), not its file: the window holds
        # the study in memory and saves it whole, so a value written to the file
        # behind its back is put back by the next Save.
        self.session = session
        self.runner = runner
        self.files = []
        self.chose_folder = None
        self.plan = None

        head = QLabel(
            "<b>Add slides</b> &nbsp; Choose the folder your <code>.czi</code> files "
            "live in, or pick individual files to process only those.")
        head.setWordWrap(True)

        b_folder = QPushButton("Choose folder…")
        b_folder.clicked.connect(self._pick_folder)
        b_files = QPushButton("Choose files…")
        b_files.clicked.connect(self._pick_files)

        self.summary = QLabel("Nothing selected yet.")
        self.summary.setWordWrap(True)
        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setStyleSheet(T.QUIET)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["File", "Size", "Animal", "Slide", "Status"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)

        self.apply_btn = QPushButton("Use these slides")
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self._apply)

        top = QHBoxLayout()
        top.addWidget(b_folder)
        top.addWidget(b_files)
        top.addStretch(1)
        top.addWidget(self.apply_btn)

        lay = QVBoxLayout(self)
        lay.addWidget(head)
        lay.addLayout(top)
        lay.addWidget(self.summary)
        lay.addWidget(self.table, 1)
        lay.addWidget(self.note)

    # ---- config -----------------------------------------------------------

    def _config(self):
        return self.session.project.cfg

    # ---- picking ----------------------------------------------------------

    def _pick_folder(self):
        cfg = self._config()
        d = QFileDialog.getExistingDirectory(
            self, "Folder containing the slides", cfg.get("source_dir", ""))
        if d:
            self.chose_folder = d
            self._load([d])

    def _pick_files(self):
        cfg = self._config()
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Slide files", cfg.get("source_dir", ""),
            "Slides (*.czi *.zip)")
        if paths:
            self.chose_folder = None
            self._load(paths)

    def _load(self, paths):
        pat = SL.name_pattern(self.runner)
        self.files = SL.scan_paths(paths, pat)
        s = SL.summarise(self.files)

        self.table.setRowCount(len(self.files))
        for i, f in enumerate(self.files):
            cells = [f.name, f"{f.size/1e9:.2f} GB" if f.size else "-",
                     f.animal, f.slide,
                     "ok" if f.recognised else f.reason]
            for c, text in enumerate(cells):
                it = QTableWidgetItem(text)
                if not f.recognised:
                    it.setForeground(Qt.red)
                self.table.setItem(i, c, it)

        bad = f" &nbsp; <span style='color:{T.DANGER}'><b>{s['n_bad']} not "\
              f"recognised</b> - these would be ignored by every stage</span>" \
              if s["n_bad"] else ""
        self.summary.setText(
            f"<b>{s['n']}</b> files &nbsp; <b>{s['bytes']/1e9:.1f} GB</b> &nbsp; "
            f"{len(s['animals'])} animals: {', '.join(s['animals']) or '-'}{bad}")

        cfg = self._config()
        self.plan = SL.plan_import(self.files, cfg["out_root"],
                                   chose_folder=self.chose_folder)
        mode, target, note, names = self.plan
        self.note.setText(f"<b>{target}</b><br>{note}")
        self.apply_btn.setEnabled(bool(self.files))
        self.apply_btn.setText(
            "Copy these slides…" if mode == SL.COPY else "Use these slides")

    # ---- committing -------------------------------------------------------

    def _apply(self):
        if not self.plan:
            return
        mode, target, note, names = self.plan
        if self.session.project.read_only:
            # Checked before anything is linked or copied, not when the setting fails.
            QMessageBox.information(
                self, "Read-only project",
                "This project was written by a newer version of the app and cannot "
                "be changed here.")
            return

        if mode == SL.COPY:
            # The only path that writes bytes, so it is the only one that asks
            # twice - and the second ask states the size again.
            if QMessageBox.question(
                    self, "Copy slide files?",
                    note + "\n\nCopy them anyway?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No) != QMessageBox.Yes:
                return

        staged, errors = SL.apply_import(self.files, mode, target,
                                         progress=lambda i, n, nm: None)
        if errors:
            QMessageBox.warning(
                self, "Some files could not be staged",
                "\n".join(f"{n}: {e}" for n, e in errors[:10]))

        # Through the open project, then saved: the stages read the file, and
        # 00_manifest must see this folder on the very next run.
        project = self.session.project
        project.set("source_dir", target, by="Add slides")
        if names:
            project.set("source_files", names, by="Add slides")
        else:
            project.unset("source_files", by="Add slides")
        self.session.touch()
        saved = self.session.save(self)

        which = f"{len(names)} selected files" if names else "the whole folder"
        self.logged.emit(f"source_dir = {target}  ({which})"
                         + (f", {staged} staged" if staged else "")
                         + ("" if saved else " - not saved yet (File > Save)"))
        self.imported.emit()
        QMessageBox.information(
            self, "Slides added",
            f"source_dir is now:\n{target}\n\nProcessing {which}.\n\n"
            "Run 'Read slide headers' next.")
