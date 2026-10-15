"""What the slide headers said, in a read-only window.

Opened from Set up > Slides > "What the slides say…". It shows the report
autodetect builds (instrument, channels, scenes, optics), what each value was
used for, what differs between files, and anything worth a look. Nothing here
is editable.

The row-building functions are plain and Qt-free, and render whatever keys a
report has rather than a fixed list, so a newer report still shows in full.
"""

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QScrollArea, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget)

import theme as T


def _text(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (list, tuple)):
        return " – ".join(_text(x) for x in v) if all(
            not isinstance(x, (dict, list, tuple)) for x in v) else json.dumps(v, ensure_ascii=False)
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def _label(key):
    return str(key).replace("_", " ").capitalize()


def _fields(section):
    """Rows for a dict of field -> value, or field -> {value, ...}."""
    rows = []
    for key, v in section.items():
        if isinstance(v, dict) and "value" in v:
            extra = ""
            if v.get("distinct", 1) and v.get("distinct", 1) > 1:
                extra = "differs between files (%d values)" % v["distinct"]
            rows.append((_label(key), _text(v["value"]), extra))
        elif isinstance(v, list) and v and isinstance(v[0], dict):
            for i, item in enumerate(v):
                rows.append((_label(key) + " %d" % (i + 1), _text(item), ""))
        else:
            rows.append((_label(key), _text(v), ""))
    return rows


def instrument_rows(report):
    """[(field, value, note)]"""
    sec = (report or {}).get("instrument") or {}
    return _fields(sec) if isinstance(sec, dict) else []


def channel_rows(report):
    """([headers], [row]) - one row per plane and channel, whatever columns exist."""
    rows = (report or {}).get("channels") or []
    cols = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    return [_label(c) for c in cols], [tuple(_text(r.get(c)) for c in cols) for r in rows]


def scene_rows(report):
    sec = (report or {}).get("scenes") or {}
    return _fields(sec) if isinstance(sec, dict) else []


def optics_rows(report):
    """[(field, value, note)]: the top-level values, then a row per channel."""
    sec = (report or {}).get("optics") or {}
    out = []
    for key, v in sec.items():
        if key == "channels":
            for c in v or []:
                bits = ["%s: %s" % (_label(k), _text(x)) for k, x in c.items() if k != "name"]
                out.append((c.get("name", ""), "; ".join(bits), ""))
        else:
            out.append((_label(key), _text(v), ""))
    return out


def use_rows(report):
    """[(field, value, used for)]"""
    return [(u.get("field", ""), _text(u.get("value")), u.get("use", ""))
            for u in (report or {}).get("uses") or []]


def varying_rows(report):
    """[(field, values seen, with how many files each)]"""
    out = []
    for key, vals in ((report or {}).get("varying") or {}).items():
        seen = ["%s (%s)" % (_text(v[0]), v[1]) if isinstance(v, (list, tuple)) and len(v) == 2
                else _text(v) for v in vals]
        out.append((key.replace("Information/", "").replace("Image/Dimensions/", ""),
                    ", ".join(seen)))
    return out


def look_at(report):
    """Things to look at: problems first, then notes."""
    return list((report or {}).get("problems") or []) + list((report or {}).get("notes") or [])


def _table(headers, rows):
    t = QTableWidget(len(rows), len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QTableWidget.NoEditTriggers)
    t.setSelectionBehavior(QTableWidget.SelectRows)
    for r, row in enumerate(rows):
        for c, v in enumerate(row):
            item = QTableWidgetItem(str(v))
            item.setToolTip(str(v))
            t.setItem(r, c, item)
    t.resizeColumnsToContents()
    t.horizontalHeader().setStretchLastSection(True)
    t.setMinimumHeight(min(260, 30 + 26 * max(len(rows), 1)))
    return t


class SlideReportDialog(QDialog):
    """`report` is the summary kept in the study file (or a full detect report)."""

    def __init__(self, report, full_path=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("What the slides say")
        self.resize(820, 640)
        self.tables = {}
        report = report or {}
        outer = QVBoxLayout(self)
        read = report.get("read") or {}
        head = "Read from the slide files' headers."
        if read.get("files_read") is not None:
            head = "%s of %s slide files read." % (read.get("files_read"), read.get("files_found"))
        if report.get("read_at"):
            head += " Read at %s." % str(report["read_at"]).replace("T", " ")[:19]
        top = QLabel(head)
        top.setStyleSheet(T.QUIET)
        outer.addWidget(top)

        body = QWidget()
        lay = QVBoxLayout(body)
        notes = look_at(report)
        if notes:
            self._heading(lay, "Things to look at")
            self.look = QLabel("\n\n".join(notes))
            self.look.setWordWrap(True)
            self.look.setStyleSheet("color:%s" % T.WARN)
            lay.addWidget(self.look)
        h, rows = channel_rows(report)
        sections = (
            ("Instrument", ["Field", "Value", ""], instrument_rows(report)),
            ("Channels", h, rows),
            ("Scenes", ["Field", "Value", ""], scene_rows(report)),
            ("Optics", ["Field", "Value", ""], optics_rows(report)),
            ("What each value did", ["Field", "Value", "Used for"], use_rows(report)),
            ("Differs between files", ["Field", "Values (files)"], varying_rows(report)),
        )
        for title, headers, data in sections:
            self._heading(lay, title)
            if not data:
                none = QLabel("Nothing recorded." if title != "Differs between files"
                              else "Every file agrees.")
                none.setStyleSheet(T.HINT)
                lay.addWidget(none)
                continue
            self.tables[title] = _table(headers, data)
            lay.addWidget(self.tables[title])
        lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        self.full_line = QLabel("Every header field is in the full file: %s" % full_path
                                if full_path else
                                "The full list of header fields is written to slide_metadata.json "
                                "in the output folder when the slides are read.")
        self.full_line.setWordWrap(True)
        self.full_line.setStyleSheet(T.HINT)
        self.full_line.setTextInteractionFlags(Qt.TextSelectableByMouse)
        outer.addWidget(self.full_line)
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        outer.addWidget(box)

    @staticmethod
    def _heading(lay, text):
        lab = QLabel("<b>%s</b>" % text)
        lay.addWidget(lab)
