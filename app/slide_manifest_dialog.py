"""The "Slide manifest" window, and the worker that builds it off the window's thread.

The table is slide_manifest.build's; this file only shows it and saves it.
The workbook may be on a network drive or OneDrive, so it is read on a plain
daemon thread, as workbook_check does.
"""

import copy
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QListWidget, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout)

import slide_manifest as SM
import theme as T

HEADERS = ("Animal", "Treatment", "Slides", "Files", "Slide files", "Status")


class ManifestJob(QObject):
    """Runs slide_manifest.make off the window's thread; `finished(manifest)` when done."""

    finished = Signal(object)
    _done = Signal(int, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._n = 0
        self._running = None
        self._done.connect(self._on_done)

    @property
    def busy(self):
        return self._running is not None

    def start(self, cfg, workbook):
        self._n += 1
        self._running = self._n
        threading.Thread(target=self._run, args=(self._n, copy.deepcopy(cfg), workbook),
                         daemon=True, name="slide-manifest").start()

    def _run(self, n, cfg, workbook):
        try:
            out = SM.make(cfg, workbook)
        except Exception as exc:                            # noqa: BLE001 - say why
            out = {"error": "%s: %s" % (type(exc).__name__, exc)}
        try:
            self._done.emit(n, out)
        except RuntimeError:            # the window went away; nobody to tell
            pass

    def _on_done(self, n, out):
        if n != self._running:
            return
        self._running = None
        self.finished.emit(out)


class SlideManifestDialog(QDialog):
    """`manifest` is slide_manifest.build's; Save writes it under `out_root`."""

    def __init__(self, manifest, out_root=None, read_only=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Slide manifest")
        self.resize(900, 640)
        self.manifest = manifest
        self.out_root = out_root
        self.saved_path = None
        outer = QVBoxLayout(self)

        self.summary = QLabel(manifest.get("summary", ""))
        self.summary.setWordWrap(True)
        outer.addWidget(self.summary)
        src = QLabel("Read from the %s. This window shows each animal's treatment."
                     % (manifest.get("from") or "slides folder"))
        src.setWordWrap(True)
        src.setStyleSheet(T.QUIET)
        outer.addWidget(src)
        for note in manifest.get("notes") or []:
            lab = QLabel(note)
            lab.setWordWrap(True)
            lab.setStyleSheet("color:%s" % T.WARN)
            outer.addWidget(lab)

        rows = manifest.get("rows") or []
        self.table = QTableWidget(len(rows), len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        warn = QColor(T.WARN)
        for r, row in enumerate(rows):
            for c, key in enumerate(SM.COLUMNS):
                item = QTableWidgetItem(str(row[key]))
                item.setToolTip(str(row[key]))
                if row["status"] != SM.OK and key in ("animal", "status"):
                    item.setForeground(warn)
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(4, min(self.table.columnWidth(4), 320))
        self.table.horizontalHeader().setStretchLastSection(True)
        outer.addWidget(self.table, 1)

        bad = manifest.get("unrecognised") or []
        if bad:
            outer.addWidget(QLabel("<b>File names not recognised</b> (no animal could be "
                                   "read from them):"))
            self.unrecognised = QListWidget()
            self.unrecognised.addItems(bad)
            self.unrecognised.setMaximumHeight(110)
            outer.addWidget(self.unrecognised)

        self.saved_line = QLabel("")
        self.saved_line.setWordWrap(True)
        self.saved_line.setStyleSheet(T.HINT)
        self.saved_line.setTextInteractionFlags(Qt.TextSelectableByMouse)
        outer.addWidget(self.saved_line)
        box = QDialogButtonBox(QDialogButtonBox.Close)
        self.save_btn = QPushButton("Save as CSV")
        self.save_btn.setToolTip("Write this table to manifest/%s in the study's output "
                                 "folder" % SM.CSV_NAME)
        self.save_btn.setEnabled(bool(out_root) and not read_only)
        self.save_btn.clicked.connect(self.save)
        box.addButton(self.save_btn, QDialogButtonBox.ActionRole)
        box.rejected.connect(self.reject)
        outer.addWidget(box)

    def save(self):
        try:
            self.saved_path = SM.save(self.manifest, self.out_root)
        except OSError as exc:
            self.saved_line.setText("Not saved: %s" % exc)
            self.saved_line.setStyleSheet("color:%s" % T.WARN)
            return None
        self.saved_line.setText("Saved: %s" % self.saved_path)
        return self.saved_path
