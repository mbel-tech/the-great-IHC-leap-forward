"""What the slide headers said, in a read-only window.

Opened from Set up > Slides > "What the slides say…". It shows the report
autodetect builds (instrument, channels, scenes, optics), what each value was
used for, what differs between files, and anything worth a look. Nothing here
is editable.

The row-building functions are plain and Qt-free, and render whatever keys a
report has rather than a fixed list, so a newer report still shows in full.
"""

import json
import re

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


#: Report keys whose plain name says more than the key does.
_NAMES = {"per_file": "Scenes per file (fewest / typical / most)",
          "nyquist": "Fine enough to resolve it"}


def _name(key):
    return _NAMES.get(key) or _label(key)


def _settings():
    """{dotted key: its plain label} from ls_config, or {} when it cannot be read."""
    try:
        import ls_config as LC
        labels = {path: k.label for path, k in LC.BY_PATH.items()}
    except Exception:                                   # noqa: BLE001 - fall back to keys
        return {}
    for key, words in _SETTING_WORDS.items():
        # Only where ls_config's label is the key spelled out (Pixel size µm,
        # which ls_config spells from pixel_size_um).
        spelled = str(key).split(".")[-1].replace("_", " ").lower()
        if key not in labels or spelled in str(labels[key]).lower().replace("µm", "um"):
            labels[key] = words
    return labels


#: Settings whose ls_config label is only the key spelled out.
_SETTING_WORDS = {
    "pixel_size_um": "Pixel size",
    "overview_target_um_per_px": "Overview picture resolution",
    "curation_render.um_per_px": "Curation picture resolution",
    "scene_render.um_per_px": "Scene picture resolution",
    "channels.dapi_index": "Counterstain channel",
    "channels.marker_index": "Marker channel",
    "acquisition.layout": "How the markers were scanned",
    "acquisition.markers": "Markers",
    "acquisition.channels": "Channel table",
    "display.colours": "Marker colours",
    "display.nuclear_colour": "Counterstain colour",
    "slide_naming.pattern": "File name rule",
}


_SETTING = re.compile(r"[a-z]+(?:[_.][a-z]+)+")


def _plain_settings(text, labels):
    """Each setting key in `text` replaced by its label (pixel_size_um -> Pixel size)."""
    if not labels:
        return text
    return _SETTING.sub(lambda m: labels.get(m.group(0), m.group(0)), text)


def _fields(section):
    """Rows for a dict of field -> value, or field -> {value, ...}."""
    rows = []
    for key, v in section.items():
        if isinstance(v, dict) and "value" in v:
            extra = ""
            if v.get("distinct", 1) and v.get("distinct", 1) > 1:
                extra = "differs between files (%d values)" % v["distinct"]
            rows.append((_name(key), _text(v["value"]), extra))
        elif isinstance(v, list) and v and isinstance(v[0], dict):
            for i, item in enumerate(v):
                rows.append((_name(key) + " %d" % (i + 1), _text(item), ""))
        else:
            rows.append((_name(key), _text(v), ""))
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
                bits = ["%s: %s" % (_name(k), _text(x)) for k, x in c.items() if k != "name"]
                out.append((c.get("name", ""), "; ".join(bits), ""))
        else:
            out.append((_name(key), _text(v), ""))
    return out


def use_rows(report):
    """[(field, value, used for)], each setting named by its plain label."""
    labels = _settings()
    out = []
    for u in (report or {}).get("uses") or []:
        field = str(u.get("field", ""))
        out.append((labels.get(field, field), _text(u.get("value")),
                    _plain_settings(str(u.get("use", "")), labels)))
    return out


def varying_rows(report):
    """[(field, values seen, with how many files each)]"""
    out = []
    for key, vals in ((report or {}).get("varying") or {}).items():
        seen = ["%s (%s)" % (_text(v[0]), v[1]) if isinstance(v, (list, tuple)) and len(v) == 2
                else _text(v) for v in vals]
        out.append((key.replace("Information/", "").replace("Image/Dimensions/", ""),
                    ", ".join(seen)))
    return out


def varying_note(report):
    """What to say above (or instead of) the list of fields that differ, or ""."""
    report = report or {}
    if report.get("varying_dropped"):
        return ("The list of fields that differ between files was too long to keep in the "
                "study file; it is in slide_metadata.json in the output folder.")
    total, shown = report.get("varying_total"), len(report.get("varying") or {})
    if isinstance(total, int) and total > shown:
        return "Showing the first %d of %d fields that differ." % (shown, total)
    return ""


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
            ("Instrument", ["Field", "Value", "Note"], instrument_rows(report)),
            ("Channels", h, rows),
            ("Scenes", ["Field", "Value", "Note"], scene_rows(report)),
            ("Optics", ["Field", "Value", "Note"], optics_rows(report)),
            ("What each value did", ["Field", "Value", "Used for"], use_rows(report)),
            ("Differs between files", ["Field", "Values (files)"], varying_rows(report)),
        )
        self.varying_line = None
        for title, headers, data in sections:
            self._heading(lay, title)
            if title == "Differs between files" and varying_note(report):
                self.varying_line = QLabel(varying_note(report))
                self.varying_line.setWordWrap(True)
                self.varying_line.setStyleSheet(T.HINT)
                lay.addWidget(self.varying_line)
            if not data:
                if title == "Differs between files" and report.get("varying_dropped"):
                    continue
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
