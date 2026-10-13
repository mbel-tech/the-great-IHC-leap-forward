"""A table for what each CZI channel is and what colour each marker is drawn in.

A thin window over `ls_channel_edit`, which owns the rules: this only shows the
rows, collects them, and says what the pipeline's own validators think of them
as you type. Nothing is written to disk here - the settings dialog keeps the
result and writes it with the rest of the settings when you press Save.

Multiplex studies edit a channel table (name, role, how the file names it, how
the marker is found); paired studies edit a plain list of marker names, because
each of their scans carries the counterstain plus one marker.
"""

import copy
import os

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox,
    QTableWidget, QVBoxLayout)

import ls_channel_edit as E
import ls_channel_guess as G
import ls_channels as CH

DEFAULT_TEXT = "(default)"
CUSTOM_TEXT = "Custom..."


CUSTOM_LABEL = "Custom (type your own)"


class MarkerBox(QComboBox):
    """A marker name: pick from the list, or choose Custom and type your own.

    The list opens with Custom, then the markers usually seen in this channel's
    colour band (when the file said which band it is), then every other marker.
    It is an editable box, so typing a name that is not listed works too.
    """

    def __init__(self, text="", band=None, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        typical, rest = G.markers_for(band)
        self.addItem(CUSTOM_LABEL)
        if typical:
            self._header("typical for %s" % band)
            self.addItems(typical)
        self._header("other markers" if typical else "markers")
        self.addItems(rest)
        self.setEditText(text)
        self.activated.connect(self._picked)

    def _header(self, title):
        self.addItem("-- %s --" % title)
        self.model().item(self.count() - 1).setEnabled(False)

    def _picked(self, i):
        if self.itemText(i) == CUSTOM_LABEL:
            self.setEditText("")
            self.lineEdit().setFocus()

    def text(self):
        t = self.currentText().strip()
        return "" if t == CUSTOM_LABEL else t

    def setText(self, t):
        self.setEditText(t)


class ChannelEditor(QDialog):
    """Edit the channel table (multiplex) or marker list (paired) of one config.

    `cfg` is the config as it would be saved now; `layout` the acquisition layout
    currently chosen in settings. `result_edits` is {dotted key: value} after OK.
    """

    def __init__(self, cfg, layout, parent=None, on_detected=None):
        super().__init__(parent)
        self.on_detected = on_detected      # called (file, channels) after a CZI is read
        self.cfg = copy.deepcopy(cfg)
        self.layout = layout
        self.paired = layout == CH.LAYOUT_PAIRED
        self.result_edits = None
        self.setWindowTitle("Channels and markers")
        self.resize(1280 if not self.paired else 560, 520)

        self.cols = (["Show", "Name", "Colour"] if self.paired else
                     ["Show", "Name", "Role", "CZI channel name", "Plane",
                      "Measured in", "Detector", "Nucleus-shaped", "Colour", "Detected"])
        self.table = QTableWidget(0, len(self.cols))
        self.table.setHorizontalHeaderLabels(self.cols)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setColumnWidth(self.cols.index("Name"), 150)
        if not self.paired:
            self.table.setColumnWidth(self.cols.index("CZI channel name"), 140)
            self.table.setColumnWidth(self.cols.index("Detected"), 300)

        intro = QLabel(
            "Each marker is a channel of the scan. The name becomes folder and file "
            "names and the marker column in every output, so rename with care." if not self.paired
            else "A paired study has one scan per marker, so only the marker names are "
                 "declared. The name becomes folder and file names in every output.")
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#9aa0a8;font-size:11px")
        self.status = QLabel("")
        self.status.setWordWrap(True)

        bar = QHBoxLayout()
        for text, fn in (("Add marker", lambda: self._add(CH.MARKER)),
                         ("Remove", self._remove), ("Up", lambda: self._move(-1)),
                         ("Down", lambda: self._move(1))):
            b = QPushButton(text)
            b.clicked.connect(fn)
            bar.addWidget(b)
        if not self.paired:
            nb = QPushButton("Add counterstain")
            nb.clicked.connect(lambda: self._add(CH.NUCLEAR))
            bar.insertWidget(1, nb)
            fb = QPushButton("Detect from a CZI file...")
            fb.setToolTip("read each channel's dye and wavelengths from a scan, and fill in "
                          "its file name, plane, role and colour")
            fb.clicked.connect(self._detect_from_czi)
            bar.addWidget(fb)
        bar.addStretch(1)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._ok)
        self.buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addWidget(self.table, 1)
        lay.addLayout(bar)
        lay.addWidget(self.status)
        lay.addWidget(self.buttons)

        self._meta = []
        self._populate(E.load_rows(self.cfg, layout))

    # ---- building the table ------------------------------------------------

    def _populate(self, rows):
        self.table.setRowCount(0)
        self._meta = []
        for r in rows:
            self._add_row(r)
        self._refresh()

    def _combo(self, items, current, fn=None):
        c = QComboBox()
        c.addItems(items)
        if current in items:
            c.setCurrentText(current)
        c.currentTextChanged.connect(fn or (lambda *_: self._refresh()))
        return c

    def _colour_combo(self, value):
        c = QComboBox()
        c.addItems([DEFAULT_TEXT] + list(CH.NAMED_COLOURS) + [CUSTOM_TEXT])
        if value and value not in CH.NAMED_COLOURS:
            c.insertItem(c.count() - 1, value)         # a #rrggbb the study already had
        c.setCurrentText(value if value else DEFAULT_TEXT)
        c.activated.connect(lambda _i, w=c: self._colour_picked(w))
        c.currentTextChanged.connect(lambda *_: self._refresh())
        return c

    def _colour_picked(self, combo):
        if combo.currentText() != CUSTOM_TEXT:
            return
        picked = QColorDialog.getColor(QColor("#ff00ff"), self, "Marker colour")
        if picked.isValid():
            hexs = picked.name()
            if combo.findText(hexs) < 0:
                combo.insertItem(combo.count() - 1, hexs)
            combo.setCurrentText(hexs)
        else:
            combo.setCurrentText(DEFAULT_TEXT)

    def _add_row(self, r):
        i = self.table.rowCount()
        self.table.insertRow(i)
        self._meta.append({"orig": r.get("orig"), "extra": r.get("extra") or {},
                           "segment_set": r.get("segment_set", False),
                           "detected": r.get("detected") or "", "band": r.get("band")})
        show = QCheckBox()
        show.setChecked(bool(r.get("show")))
        show.stateChanged.connect(lambda *_: self._refresh())
        name = MarkerBox(r.get("name") or "", r.get("band"))
        name.editTextChanged.connect(lambda *_: self._refresh())
        cell = {"Show": show, "Name": name, "Colour": self._colour_combo(r.get("colour"))}
        if not self.paired:
            role = self._combo(list(CH.ROLES), r.get("role"))
            czi = QLineEdit(r.get("czi_name") or "")
            czi.textChanged.connect(lambda *_: self._refresh())
            plane = QSpinBox()
            plane.setRange(-1, 99)
            plane.setSpecialValueText("-")
            plane.setValue(-1 if r.get("index") is None else int(r["index"]))
            plane.valueChanged.connect(lambda *_: self._refresh())
            seg = self._combo(list(CH.SEGMENTS), r.get("segment") or CH.SEGMENT_NUCLEAR)
            seg.setToolTip("nuclear: counted inside the nuclei found on the counterstain; "
                           "own: objects are found on the marker's own channel")
            det = self._combo(list(CH.BACKENDS), r.get("backend") or "stardist")
            shaped = QCheckBox()
            shaped.setChecked(bool(r.get("nucleus_shaped", True)))
            shaped.stateChanged.connect(lambda *_: self._refresh())
            cell.update({"Role": role, "CZI channel name": czi, "Plane": plane,
                         "Measured in": seg, "Detector": det, "Nucleus-shaped": shaped,
                         "Detected": self._detected_label(r)})
        for c, title in enumerate(self.cols):
            self.table.setCellWidget(i, c, cell[title])

    @staticmethod
    def _detected_label(r):
        label = QLabel(r.get("detected") or "")
        label.setStyleSheet("color:#9aa0a8;font-size:11px")
        label.setToolTip("what the CZI file says this channel is; the file records the dye, "
                         "not the antibody, so the marker name is your choice")
        return label

    # ---- reading it back ---------------------------------------------------

    def _w(self, row, title):
        return self.table.cellWidget(row, self.cols.index(title))

    def rows(self):
        out = []
        for i, meta in enumerate(self._meta):
            colour = self._w(i, "Colour").currentText()
            r = {"orig": meta["orig"], "extra": meta["extra"],
                 "segment_set": meta["segment_set"],
                 "detected": meta["detected"], "band": meta["band"],
                 "name": self._w(i, "Name").text(), "show": self._w(i, "Show").isChecked(),
                 "colour": None if colour in (DEFAULT_TEXT, CUSTOM_TEXT) else colour,
                 "role": CH.MARKER}
            if not self.paired:
                plane = self._w(i, "Plane").value()
                r.update({"role": self._w(i, "Role").currentText(),
                          "czi_name": self._w(i, "CZI channel name").text(),
                          "index": None if plane < 0 else plane,
                          "segment": self._w(i, "Measured in").currentText(),
                          "backend": self._w(i, "Detector").currentText(),
                          "nucleus_shaped": self._w(i, "Nucleus-shaped").isChecked()})
            out.append(r)
        return out

    def _refresh(self):
        if not hasattr(self, "_meta") or len(self._meta) != self.table.rowCount():
            return
        rows = self.rows()
        defaults = E.default_colours(rows)
        for i, r in enumerate(rows):
            is_marker = r["role"] == CH.MARKER
            own = is_marker and r.get("segment") == CH.SEGMENT_OWN
            self._w(i, "Show").setEnabled(is_marker)
            self._w(i, "Colour").setEnabled(is_marker)
            if is_marker:
                d = defaults.get(r["name"].strip())
                self._w(i, "Colour").setToolTip(
                    "(default) = %s: the first shown marker is red, the second green, "
                    "the rest have no colour and are not drawn" % (d or "no colour for this marker"))
            else:
                self._w(i, "Colour").setToolTip("the counterstain's colour is set under "
                                                "'Counterstain colour' in Settings")
            if not self.paired:
                self._w(i, "Measured in").setEnabled(is_marker)
                self._w(i, "Detector").setEnabled(own)
                self._w(i, "Nucleus-shaped").setEnabled(own)
        try:
            cfg = E.apply(self.cfg, E.edits(self.cfg, self.layout, rows), self.layout)
            errors, warnings = E.check(cfg, self.layout)
        except (ValueError, TypeError) as exc:
            errors, warnings = [str(exc)], []
        if not rows:
            warnings = warnings or ["nothing declared yet"]
        lines = ["<span style='color:#e5534b'>%s</span>" % _esc(e) for e in errors[:5]]
        lines += ["<span style='color:#c69026'>%s</span>" % _esc(w) for w in warnings[:3]]
        self.status.setText("<br>".join(lines) if lines else
                            "<span style='color:#3fb950'>The pipeline accepts this table.</span>")
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(not errors)

    # ---- actions -----------------------------------------------------------

    def _add(self, role):
        self._add_row(E.new_row(self.layout, role))
        self._refresh()
        self.table.setCurrentCell(self.table.rowCount() - 1, self.cols.index("Name"))

    def _selected(self):
        r = self.table.currentRow()
        return r if 0 <= r < self.table.rowCount() else None

    def _remove(self):
        r = self._selected()
        if r is None:
            return
        rows = self.rows()
        del rows[r]
        self._populate(rows)

    def _move(self, d):
        r = self._selected()
        if r is None or not 0 <= r + d < self.table.rowCount():
            return
        rows = self.rows()
        rows[r], rows[r + d] = rows[r + d], rows[r]
        self._populate(rows)
        self.table.setCurrentCell(r + d, 0)

    def _detect_from_czi(self):
        start = self.cfg.get("source_dir") or ""
        path, _ = QFileDialog.getOpenFileName(
            self, "A CZI scan of this study", start if os.path.isdir(start) else "", "CZI (*.czi)")
        if not path:
            return
        try:
            found = E.czi_channels(path)
        except (ValueError, ImportError) as exc:
            QMessageBox.warning(self, "Could not read the file", str(exc))
            return
        if self.on_detected:
            self.on_detected(path, [{"name": f["czi_name"], "plane": f["index"], "role": f["role"],
                                     "band": f["band"], "description": f["detected"]}
                                    for f in found])
        rows, added = E.merge_detected(self.rows(), found, self.layout)
        self._populate(rows)
        lines = ["  plane %d  %s  ->  %s" % (f["index"], f["czi_name"] or "?", f["detected"])
                 for f in found]
        QMessageBox.information(
            self, "Channels detected",
            "%d channel(s) in the file, %d new in the table:\n\n%s\n\n"
            "The file records each channel's dye, not the antibody. Check the counterstain, "
            "and pick or type the marker name for each channel (the list offers the markers "
            "usually seen in that colour)." % (len(found), added, "\n".join(lines)))

    def _ok(self):
        rows = self.rows()
        moved = E.renames(rows)
        if moved and QMessageBox.question(
                self, "Rename markers?",
                "Output folders, files and CSV values are named after the marker, so renaming\n"
                + "\n".join("  %s -> %s" % m for m in moved)
                + "\nstarts new outputs; existing ones keep the old name.\n\nKeep the rename?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.result_edits = E.edits(self.cfg, self.layout, rows)
        self.accept()


def _esc(t):
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
