"""Slide file names, set up from one example: type a name, say what its parts are.

The person types (or picks) one of their file names. The program splits it into
pieces and suggests which piece is the animal, which the slide number and which
the replicate letter; a click on a piece changes that. The program then writes
the pattern itself and checks it against every file in the slides folder. Where
files do not fit, it works down a list of known causes (case, a varying ending,
a missing replicate letter, other separators, ...) and says in plain words what
it changed and how many files each change brought in. What still does not fit is
listed with the likely reason, and Save stays off until every file fits or the
person leaves the rest out on purpose.

The regex is never shown unless asked for (Show the pattern), and can then be
edited by hand. The logic is in naming_build.py, tested without a window.
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget)

import naming_build as B
import theme as T

MONO = "Consolas, Menlo, monospace"

#: role -> (button words, question, colour)
ROLES = (
    (B.SUBJECT, "Animal ID", "Which part is the animal (fish) ID?", "#cfe3ff"),
    (B.SLIDE, "Slide number", "Which part is the slide number?", "#d6f5d6"),
    (B.REPLICATE, "Replicate letter", "Which part is the replicate letter (a, b, ...)?",
     "#ffe7c2"),
)
COLOUR = {r: c for r, _w, _q, c in ROLES}
WORD = {B.SUBJECT: "animal", B.SLIDE: "slide", B.REPLICATE: "replicate"}


def slide_names(names):
    """Only the slide files: a stray notes.txt is not a file the pattern must fit."""
    czi = [n for n in names if n.lower().endswith(".czi")]
    return czi or list(names)


class GuidedNamingDialog(QDialog):
    """Build a slide_naming block from one example and the folder's file names."""

    def __init__(self, filenames, current=None, parent=None):
        super().__init__(parent)
        self.filenames = slide_names([os.path.basename(f) for f in filenames])
        self.result_block = None
        self.spans = {}                 # role -> (start, end)
        self.role = B.SUBJECT
        self.manual = False             # the pattern was typed by hand
        self.got = None
        self.setWindowTitle("Slide file names")
        self.resize(820, 720)

        lay = QVBoxLayout(self)
        intro = QLabel("Every file name says which animal, slide and replicate it is. Show "
                       "the program one name and which part is which; it writes the rule "
                       "and checks it against all %d files in the folder."
                       % len(self.filenames))
        intro.setWordWrap(True)
        lay.addWidget(intro)

        lay.addWidget(self._step("1. Type one of your file names, or pick one."))
        self.example = QComboBox()
        self.example.setEditable(True)
        self.example.addItems(self.filenames)
        self.example.setFont(QFont(MONO, 11))
        lay.addWidget(self.example)

        lay.addWidget(self._step("2. Say what each part is. Pick a part below, then click "
                                 "it in the name; click again to unmark it."))
        roles = QHBoxLayout()
        self.role_group = QButtonGroup(self)
        self.role_buttons = {}
        for role, words, _q, colour in ROLES:
            btn = QPushButton(words)
            btn.setCheckable(True)
            btn.setStyleSheet(f"QPushButton{{padding:4px 12px}}"
                              f"QPushButton:checked{{background:{colour};font-weight:bold}}")
            btn.setMinimumWidth(btn.fontMetrics().horizontalAdvance(words) + 40)
            btn.clicked.connect(lambda _=False, r=role: self._pick_role(r))
            self.role_group.addButton(btn)
            self.role_buttons[role] = btn
            roles.addWidget(btn)
        self.none_btn = QPushButton("Not in the name")
        self.none_btn.setToolTip("This name has no slide number or no replicate letter")
        self.none_btn.clicked.connect(self._not_there)
        roles.addWidget(self.none_btn)
        guess = QPushButton("Guess again")
        guess.setToolTip("Go back to the program's reading of the name")
        guess.clicked.connect(self._guess)
        roles.addWidget(guess)
        roles.addStretch(1)
        lay.addLayout(roles)
        self.question = QLabel("")
        self.question.setStyleSheet(T.QUIET)
        lay.addWidget(self.question)

        self.token_box = QWidget()
        self.token_lay = QHBoxLayout(self.token_box)
        self.token_lay.setContentsMargins(0, 0, 0, 0)
        self.token_lay.setSpacing(2)
        scroll = QScrollArea()
        scroll.setWidget(self.token_box)
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(64)
        lay.addWidget(scroll)
        self.reading = QLabel("")
        self.reading.setTextFormat(Qt.RichText)
        lay.addWidget(self.reading)

        lay.addWidget(self._step("3. Check every file."))
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.RichText)
        lay.addWidget(self.status)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["File that does not fit", "Likely reason"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 300)
        lay.addWidget(self.table, 1)
        lay.addStretch(0)

        self.show_pattern = QCheckBox("Show the pattern (advanced: edit it by hand)")
        self.show_pattern.toggled.connect(lambda on: self.regex.setVisible(on))
        lay.addWidget(self.show_pattern)
        self.regex = QLineEdit(current or "")
        self.regex.setFont(QFont(MONO, 10))
        self.regex.setVisible(False)
        self.regex.textEdited.connect(self._typed)
        lay.addWidget(self.regex)
        self.case = False

        self.accept_bad = QCheckBox("Leave the files that do not fit out of this study")
        self.accept_bad.stateChanged.connect(lambda _s: self._check())
        lay.addWidget(self.accept_bad)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)

        best = B.auto(self.filenames) if self.filenames else None
        if best:
            self.example.setCurrentText(best["example"])
        self.example.currentTextChanged.connect(lambda _t: self._guess())
        self._guess()
        self._pick_role(B.SUBJECT)

    @staticmethod
    def _step(text):
        lab = QLabel(f"<b>{text}</b>")
        lab.setContentsMargins(0, 8, 0, 0)
        return lab

    # ---- the example and its parts -------------------------------------------------
    def _text(self):
        return self.example.currentText().strip()

    def _guess(self):
        self.manual = False
        self.spans = {s.name: (s.start, s.end) for s in B.guess_spans(self._text())}
        self._rebuild()

    def _pick_role(self, role):
        self.role = role
        self.role_buttons[role].setChecked(True)
        q = next(q for r, _w, q, _c in ROLES if r == role)
        self.question.setText(q + " Click it in the name below.")
        self.none_btn.setEnabled(role != B.SUBJECT)

    def _not_there(self):
        self.spans.pop(self.role, None)
        self.manual = False
        self._rebuild()

    def _click(self, start, end):
        """Mark the piece for the chosen part; next to what is marked, it extends it."""
        mine = self.spans.get(self.role)
        if mine and mine[0] <= start and end <= mine[1]:
            # Clicking a marked piece unmarks it, from whichever end it sits at.
            if start == mine[0] and end < mine[1]:
                self.spans[self.role] = (end, mine[1])
            elif end == mine[1] and start > mine[0]:
                self.spans[self.role] = (mine[0], start)
            else:
                self.spans.pop(self.role)
        else:
            for other, (a, b) in list(self.spans.items()):
                if other != self.role and a < end and start < b:
                    self.spans.pop(other)
            if mine and (start == mine[1] or end == mine[0]):
                self.spans[self.role] = (min(start, mine[0]), max(end, mine[1]))
            else:
                self.spans[self.role] = (start, end)
        self.manual = False
        self._rebuild()

    def _span_list(self):
        return [B.Span(a, b, r) for r, (a, b) in self.spans.items()]

    def _role_at(self, start, end):
        for r, (a, b) in self.spans.items():
            if a <= start and end <= b:
                return r
        return None

    def _draw_tokens(self):
        while self.token_lay.count():
            item = self.token_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        text = self._text()
        for start, end, piece in B.tokens(text):
            btn = QPushButton(piece)
            btn.setFont(QFont(MONO, 12))
            btn.setFlat(piece in B.SEPARATORS)
            btn.setMinimumWidth(22)
            role = self._role_at(start, end)
            style = "padding:4px 6px;"
            if role:
                style += f"background:{COLOUR.get(role, '#eee')};font-weight:bold;"
            btn.setStyleSheet(f"QPushButton{{{style}}}")
            btn.setToolTip(f"{WORD.get(role, role)}" if role else "not marked")
            btn.clicked.connect(lambda _=False, a=start, b=end: self._click(a, b))
            self.token_lay.addWidget(btn)
        ext = B.split_ext(text)[1]
        if ext:
            lab = QLabel(ext)
            lab.setFont(QFont(MONO, 12))
            lab.setStyleSheet(T.QUIET)
            self.token_lay.addWidget(lab)
        self.token_lay.addStretch(1)

    # ---- the pattern -----------------------------------------------------------------
    def _rebuild(self):
        self._draw_tokens()
        text = self._text()
        spans = self._span_list()
        if spans:
            self.reading.setText("Read as: <b>%s</b>" % B.readout(text, spans))
        else:
            self.reading.setText("Nothing marked yet.")
        try:
            self.got = B.generalise(text, spans, self.filenames)
        except ValueError as exc:
            self.got = None
            self._block(str(exc)[:1].upper() + str(exc)[1:] + ".")
            return
        self.regex.setText(self.got["pattern"])
        self.case = self.got["ignore_case"]
        self._check()

    def _typed(self, _text):
        self.manual = True
        self._check()

    def _block(self, reason):
        self.status.setText(reason)
        self.buttons.button(QDialogButtonBox.Save).setEnabled(False)
        self.table.setRowCount(0)
        self.table.setVisible(False)

    def _check(self):
        pattern = self.regex.text().strip()
        if not pattern:
            self._block("Mark the animal ID first.")
            return
        result = B.preview(self.filenames, pattern, self.case)
        ok, reason = B.can_save(result, self.accept_bad.isChecked())
        lines = [("<span style='color:%s'>%s</span>" %
                  (T.OK if not result["failed"] and not result["problems"] else T.WARN,
                   B.summary(result).capitalize() + "."))]
        steps = [] if self.manual or not self.got else self.got["steps"]
        if steps:
            lines.append("To make the files fit, the program:")
            lines += ["&nbsp;&nbsp;• " + s for s in steps]
        if self.manual:
            lines.append("Using the pattern as typed.")
        if not ok:
            lines.append(reason)
        self.status.setText("<br>".join(lines))
        self.buttons.button(QDialogButtonBox.Save).setEnabled(ok)
        bad = [r for r in result["rows"] if not r["ok"]]
        self.table.setVisible(bool(bad))
        self.table.setRowCount(len(bad))
        for i, row in enumerate(bad):
            why = row.get("why")
            words = B.why_not(row["file"], self._text()) if why == "no match" else why
            for j, cell in enumerate((row["file"], words)):
                item = QTableWidgetItem(cell)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(i, j, item)
        self.result = result

    def _save(self):
        self.result_block = {"pattern": self.regex.text().strip(),
                             "example": self._text(),
                             "case_insensitive": bool(self.case)}
        self.accept()
