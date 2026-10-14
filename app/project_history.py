"""What the project remembers: decisions made, stages run, CZI files read, and notes.

A read-only window over the `project` block (`ls_project`), newest first. The only
thing editable is the notes box, which goes through the open project like any
other change and is saved with File > Save.

The row-building functions are plain and Qt-free so what the window shows can be
tested without one.
"""

import json

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget)

import theme as T

SHORT = 70


def _short(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= SHORT else text[:SHORT - 1] + "…"


def _when(iso):
    """`2026-01-02T03:04:05+00:00` -> `2026-01-02 03:04:05`; anything else as it is."""
    return str(iso).replace("T", " ")[:19] if iso else ""


def decision_rows(block):
    """[(when, setting, was, now, by)] newest first. Withheld values say so."""
    out = []
    for d in reversed((block or {}).get("decisions") or []):
        if d.get("redacted"):
            was = now = "(not recorded)"
        else:
            was = "" if d.get("from") is None else _short(d["from"])
            now = "(removed)" if d.get("to") is None else _short(d["to"])
        out.append((_when(d.get("at")), d.get("key", ""), was, now, d.get("by", "")))
    return out


def run_rows(block):
    """[(stage, started, ended, result, config, code)] newest first."""
    return [(r.get("stage", ""), _when(r.get("started")), _when(r.get("ended")),
             "ok" if r.get("ok") else "FAILED", r.get("config_sha") or "", r.get("code") or "")
            for r in reversed((block or {}).get("runs") or [])]


def detected_rows(block):
    """[(read at, file, channels as one line)] newest first."""
    out = []
    for d in reversed((block or {}).get("detected") or []):
        chans = "; ".join("%s%s" % (c.get("name") or "?", " (%s)" % c["band"] if c.get("band") else "")
                          for c in d.get("channels") or [])
        out.append((_when(d.get("read_at")), d.get("file", ""), chans))
    return out


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
    return t


class ProjectHistory(QDialog):
    """Tabs over one project's record. `session` is the open `ProjectSession`."""

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        p = session.project
        block = p.block
        self.setWindowTitle("Project history - %s" % p.name)
        self.resize(900, 520)

        head = QLabel("%s\nid %s    created %s    last saved %s    app %s" % (
            p.path or "not saved yet", block.get("id", "?"), _when(block.get("created")) or "?",
            _when(block.get("modified")) or "never", block.get("app_version") or "?"))
        head.setStyleSheet(T.HINT)
        head.setWordWrap(True)

        self.tabs = QTabWidget()
        self.decisions = _table(["When", "Setting", "Was", "Now", "By"], decision_rows(block))
        self.runs = _table(["Stage", "Started", "Ended", "Result", "Config", "Code"], run_rows(block))
        self.detected = _table(["Read", "File", "Channels"], detected_rows(block))
        self.tabs.addTab(self.decisions, "Decisions (%d)" % self.decisions.rowCount())
        self.tabs.addTab(self.runs, "Runs (%d)" % self.runs.rowCount())
        self.tabs.addTab(self.detected, "CZI files read (%d)" % self.detected.rowCount())

        notes = QWidget()
        nl = QVBoxLayout(notes)
        self.notes = QPlainTextEdit(block.get("notes") or "")
        self.notes.setReadOnly(p.read_only)
        self.notes.setPlaceholderText("Anything worth remembering about this project...")
        nl.addWidget(self.notes)
        self.tabs.addTab(notes, "Notes")

        foot = QLabel("The animal-to-treatment key is never written into this history, only the "
                      "fact that it changed. Lists keep the newest entries (runs 200, decisions 500).")
        foot.setStyleSheet(T.HINT)
        foot.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(head)
        lay.addWidget(self.tabs, 1)
        lay.addWidget(foot)
        lay.addWidget(buttons)

    def done(self, result):
        """Closing by any route (Close, Esc, the window's X) keeps edited notes."""
        p = self.session.project
        text = self.notes.toPlainText()
        if not p.read_only and text != (p.block.get("notes") or ""):
            p.set_notes(text)
            self.session.touch()
        super().done(result)
