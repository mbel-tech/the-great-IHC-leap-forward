"""Set up > Slides > Match the sheet: which column is the animal, which the treatment.

The sampling workbook is the person's own spreadsheet, so its columns are named
however they named them. This window shows its first rows with a menu above
each column (Animal ID, Treatment, Keep, Ignore) and, every time a menu
changes, joins the sheet to the animals the slide file names give - the same
join 06b makes at Unblind (ls_sampling.join), so what it says here is what
Unblind will do. Save writes the study's `sampling` block; nothing else is
written, and nothing reads the groups until Load the groups for Shotgun or
Unblind is run.

The sheet is read once, when the window opens. A workbook is small, so this
is done on the window's thread under a wait cursor rather than on a worker.
"""

import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

import ls_sampling as SAMPLING
from widgets import COLOUR, QUIET

ROLES = ("Keep", "Animal ID", "Treatment", "Ignore")
MATCH_WORDS = (("auto", "Automatically: by name, else by number"),
               ("exact", "By name exactly (LS22 = LS22)"),
               ("number", "By number (22 = LS22)"))
PREVIEW_ROWS = 20


def summary(headers, rows, animals, mapping):
    """What the join would do, for the window: a dict, never raising."""
    out = {"problems": SAMPLING.problems(mapping, headers), "rule": None, "missing": [],
           "duplicate": [], "unresolved": [], "groups": {}, "unused": 0,
           "matched": 0, "animals": len(animals)}
    if not mapping.get("id_column"):
        return out
    if out["problems"] and SAMPLING.column(headers, mapping["id_column"]) is None:
        return out
    probe = dict(mapping, group_columns=mapping.get("group_columns") or [])
    if not probe["group_columns"]:                  # still say how the IDs match
        found, rule, dup = SAMPLING.match_ids(
            [r.get(SAMPLING.column(headers, mapping["id_column"]), "") for r in rows],
            animals, mapping.get("match", "auto"))
        out.update(rule=rule, duplicate=dup, matched=len(found),
                   missing=[a for a in animals if a not in found and a not in dup])
        return out
    try:
        j = SAMPLING.join(list(animals), headers, rows, probe)
    except SystemExit as exc:
        out["problems"] = [str(exc)]
        return out
    out.update(rule=j["rule"], missing=j["missing"], duplicate=j["duplicate"],
               unresolved=j["unresolved"], unused=len(j["unused_ids"]),
               matched=len(j["meta"]))
    for m in j["meta"].values():
        if m["treatment"]:
            out["groups"][m["treatment"]] = out["groups"].get(m["treatment"], 0) + 1
    return out


class RuleDialog(QDialog):
    """One fill rule: when <column> is <values>, <treatment column> is <text>."""

    def __init__(self, headers, group_columns, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Fill a blank treatment")
        lay = QVBoxLayout(self)
        note = QLabel("For an animal whose treatment cell is blank, take the treatment "
                      "from another column. Example: when 'tank' is 303, 304, the "
                      "treatment is exercise.")
        note.setWordWrap(True)
        lay.addWidget(note)
        form = QFormLayout()
        self.when = QComboBox()
        self.when.addItems([h for h in headers if h])
        form.addRow("When the column", self.when)
        self.values = QLineEdit()
        self.values.setPlaceholderText("303, 304")
        form.addRow("is one of", self.values)
        self.set = QComboBox()
        self.set.addItems(group_columns)
        form.addRow("then", self.set)
        self.to = QLineEdit()
        self.to.setPlaceholderText("exercise")
        form.addRow("is", self.to)
        lay.addLayout(form)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        self.ok = box.button(QDialogButtonBox.Ok)
        self.ok.setEnabled(False)
        for w in (self.values, self.to):
            w.textChanged.connect(lambda _t: self.ok.setEnabled(bool(self.rule())))
        lay.addWidget(box)

    def rule(self):
        values = [v.strip() for v in self.values.text().split(",") if v.strip()]
        to = self.to.text().strip()
        if not values or not to or not self.set.currentText():
            return None
        return {"when": self.when.currentText(), "is": values,
                "set": self.set.currentText(), "to": to}


def rule_words(rule):
    return "When '%s' is %s, '%s' is %s" % (rule["when"], ", ".join(rule["is"]),
                                           rule["set"], rule["to"])


class SheetMatchDialog(QDialog):
    """The window. `mapping()` is the choice once accepted."""

    def __init__(self, path, animals, where, saved=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Match the sheet")
        self.setAccessibleName("Match the sheet")
        self.resize(1000, 680)
        self.path = path
        self.animals = sorted(animals, key=lambda a: (int(SAMPLING._number(a) or 0), a))
        self.where = where
        self.saved = dict(saved or {})
        self.headers, self.rows = [], []
        self.extra = {}                 # keys the window does not edit (lowercase)
        self.fill = []
        self._building = False

        lay = QVBoxLayout(self)
        intro = QLabel("Above each column, say what it holds: the <b>Animal ID</b> "
                       "(one column), the <b>Treatment</b> you want to compare (one or "
                       "more; several make one group, like exercise · 1), "
                       "<b>Keep</b> to carry it into the results at Unblind, or "
                       "<b>Ignore</b>. The check below compares the IDs with the "
                       "animals in the slide file names as you choose.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        top = QHBoxLayout()
        self.sheet_label = QLabel("Worksheet")
        self.sheet = QComboBox()
        self.sheet.currentTextChanged.connect(self._sheet_changed)
        top.addWidget(self.sheet_label)
        top.addWidget(self.sheet)
        top.addSpacing(16)
        top.addWidget(QLabel("Match IDs"))
        self.match = QComboBox()
        for key, words in MATCH_WORDS:
            self.match.addItem(words, key)
        self.match.currentIndexChanged.connect(lambda _i: self._update())
        top.addWidget(self.match)
        top.addStretch(1)
        lay.addLayout(top)

        self.table = QTableWidget()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        lay.addWidget(self.table, 1)

        self.check = QLabel("")
        self.check.setWordWrap(True)
        self.check.setTextFormat(Qt.RichText)
        self.check.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.check)

        rules = QHBoxLayout()
        self.rules = QListWidget()
        self.rules.setMaximumHeight(70)
        rules.addWidget(self.rules, 1)
        side = QVBoxLayout()
        self.add_rule_btn = QPushButton("Add rule…")
        self.add_rule_btn.setToolTip("Fill a blank treatment cell from another column")
        self.add_rule_btn.clicked.connect(self.add_rule)
        side.addWidget(self.add_rule_btn)
        self.remove_rule_btn = QPushButton("Remove rule")
        self.remove_rule_btn.clicked.connect(self.remove_rule)
        side.addWidget(self.remove_rule_btn)
        rules.addLayout(side)
        self.rules_label = QLabel("Blank treatment rules")
        self.rules_label.setStyleSheet(QUIET)
        lay.addWidget(self.rules_label)
        lay.addLayout(rules)

        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        self.save_btn = box.button(QDialogButtonBox.Save)
        lay.addWidget(box)

        self.error = None
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            names = [n for n, _m in SAMPLING.sheet_names(path)]
        except Exception as exc:                                # noqa: BLE001 - say why
            names, self.error = [], "%s could not be read as a workbook (%s)" % (path, exc)
        finally:
            QApplication.restoreOverrideCursor()
        self._building = True
        self.sheet.addItems(names)
        want = self.saved.get("sheet")
        if want in names:
            self.sheet.setCurrentText(want)
        self._building = False
        self.sheet.setVisible(len(names) > 1)
        self.sheet_label.setVisible(len(names) > 1)
        if names:
            self._sheet_changed(self.sheet.currentText())
        else:
            self._update()

    # ---- reading -------------------------------------------------------------

    def _sheet_changed(self, name):
        if self._building or not name:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.headers, self.rows = SAMPLING.table(self.path, name)
            self.error = None
        except (SystemExit, Exception) as exc:                  # noqa: BLE001 - say why
            self.headers, self.rows, self.error = [], [], str(exc)
        finally:
            QApplication.restoreOverrideCursor()
        self._start_from(self._starting_mapping())

    def _starting_mapping(self):
        """The saved choice if it fits this sheet, else the LS one, else a guess."""
        if self.saved.get("id_column") and not [
                p for p in SAMPLING.problems(self.saved, self.headers)
                if "no column" in p]:
            return self.saved
        return SAMPLING.legacy_mapping(self.headers) or \
            SAMPLING.guess(self.headers, self.rows, self.animals)

    def _start_from(self, mapping):
        self._building = True
        self.extra = {k: v for k, v in mapping.items()
                      if k not in ("id_column", "group_columns", "ignore_columns",
                                   "match", "fill", "sheet")}
        self.fill = list(mapping.get("fill") or [])
        idx = {key: i for i, (key, _w) in enumerate(MATCH_WORDS)}
        self.match.setCurrentIndex(idx.get(mapping.get("match", "auto"), 0))
        heads = self.headers
        self.table.clear()
        shown = self.rows[:PREVIEW_ROWS]
        self.table.setColumnCount(len(heads))
        self.table.setRowCount(len(shown) + 1)
        self.table.setHorizontalHeaderLabels([h or "(no header)" for h in heads])
        self.table.setVerticalHeaderLabels(["Use as"] + [str(i + 2) for i in range(len(shown))])
        self.roles = []
        groups = {SAMPLING._norm(h) for h in mapping.get("group_columns") or []}
        ignore = {SAMPLING._norm(h) for h in mapping.get("ignore_columns") or []}
        for c, h in enumerate(heads):
            combo = QComboBox()
            combo.addItems(ROLES)
            n = SAMPLING._norm(h)
            if not h:
                role = "Ignore"
            elif mapping.get("id_column") and n == SAMPLING._norm(mapping["id_column"]):
                role = "Animal ID"
            elif n in groups:
                role = "Treatment"
            elif n in ignore:
                role = "Ignore"
            else:
                role = "Keep"
            combo.setCurrentText(role)
            combo.setEnabled(bool(h))
            combo.setAccessibleName("Use column %s as" % (h or c + 1))
            combo.currentTextChanged.connect(lambda r, col=c: self._role_changed(col, r))
            self.table.setCellWidget(0, c, combo)
            self.roles.append(combo)
            for r, row in enumerate(shown):
                self.table.setItem(r + 1, c, QTableWidgetItem(str(row.get(c, ""))))
        self.table.resizeColumnsToContents()
        self._building = False
        self._show_rules()
        self._update()

    # ---- choosing ------------------------------------------------------------

    def _role_changed(self, col, role):
        if self._building:
            return
        if role == "Animal ID":         # one ID column: the old one goes back to Keep
            self._building = True
            for c, combo in enumerate(self.roles):
                if c != col and combo.currentText() == "Animal ID":
                    combo.setCurrentText("Keep")
            self._building = False
        groups = set(self._picked("Treatment"))
        self.fill = [f for f in self.fill if f["set"] in groups]
        self._show_rules()
        self._update()

    def _picked(self, role):
        return [self.headers[c] for c, combo in enumerate(self.roles)
                if combo.currentText() == role]

    def mapping(self):
        """The choice as the study's `sampling` block."""
        ids = self._picked("Animal ID")
        out = dict(self.extra)
        out.update({"id_column": ids[0] if ids else None,
                    "group_columns": self._picked("Treatment"),
                    "ignore_columns": self._picked("Ignore"),
                    "match": self.match.currentData() or "auto",
                    "fill": list(self.fill)})
        if self.sheet.count() > 1:
            out["sheet"] = self.sheet.currentText()
        return out

    def add_rule(self):
        groups = self._picked("Treatment")
        dlg = RuleDialog(self.headers, groups, self)
        if dlg.exec() and dlg.rule():
            self.fill.append(dlg.rule())
            self._show_rules()
            self._update()

    def remove_rule(self):
        row = self.rules.currentRow()
        if 0 <= row < len(self.fill):
            del self.fill[row]
            self._show_rules()
            self._update()

    def _show_rules(self):
        self.rules.clear()
        for f in self.fill:
            self.rules.addItem(rule_words(f))
        self.add_rule_btn.setEnabled(bool(self.roles and self._picked("Treatment")))
        self.remove_rule_btn.setEnabled(bool(self.fill))

    # ---- the check -----------------------------------------------------------

    def _update(self):
        red = COLOUR["failed"]
        if self.error:
            self.check.setText("<span style='color:%s'>%s</span>" % (red, html.escape(self.error)))
            self.save_btn.setEnabled(False)
            return
        m = self.mapping()
        s = summary(self.headers, self.rows, self.animals, m)
        lines = []
        if not self.animals:
            lines.append("The study has no animals yet: none were found in the %s, so "
                         "there is nothing to compare the IDs with." % self.where)
        elif m["id_column"]:
            how = {"exact": "by name", "number": "by number"}.get(s["rule"], "")
            lines.append("<b>%d of %d</b> animals in the %s have a row%s."
                         % (s["matched"], s["animals"], self.where,
                            " (matched %s)" % how if how else ""))
            if s["missing"]:
                lines.append("<span style='color:%s'>No row for %s. Unblind stops on "
                             "these.</span>" % (red, html.escape(", ".join(s["missing"]))))
            if s["duplicate"]:
                lines.append("<span style='color:%s'>On two or more rows: %s. Fix the "
                             "sheet first.</span>" % (red, html.escape(", ".join(s["duplicate"]))))
            if s["unused"]:
                lines.append("%d IDs in the sheet have no slide; that is normal when the "
                             "sheet lists more animals than were imaged." % s["unused"])
        else:
            lines.append("<span style='color:%s'>Choose the Animal ID column.</span>" % red)
        if m["id_column"] and not m["group_columns"]:
            lines.append("<span style='color:%s'>Choose the Treatment column, or "
                         "several.</span>" % red)
        if s["groups"]:
            lines.append("Groups: " + html.escape(" · ".join(
                "%s %d" % (g, n) for g, n in sorted(s["groups"].items()))) + ".")
        if s["unresolved"]:
            lines.append("<span style='color:%s'>Blank treatment, no rule fills it: %s. "
                         "Add a rule below, or fill the sheet.</span>"
                         % (COLOUR["waiting"], html.escape(
                             ", ".join(a for a, _why in s["unresolved"]))))
        other = [p for p in s["problems"] if "no animal ID" not in p
                 and "no treatment" not in p]
        for p in other:
            lines.append("<span style='color:%s'>%s.</span>" % (red, html.escape(p)))
        self.check.setText("<br>".join(lines))
        self.save_btn.setEnabled(bool(m["id_column"] and m["group_columns"]
                                      and not s["duplicate"] and not other))
