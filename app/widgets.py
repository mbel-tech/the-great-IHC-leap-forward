"""Small pieces the screens are built from: the (i) button, headers, stage rows.

Nothing here knows about the pipeline beyond what it is handed. A stage row is
given a stage, the words to show and the options to offer, and says when its
buttons are pressed; the screen decides what that means.
"""

import html

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget, QProgressBar, QPushButton,
    QSizePolicy, QSpinBox, QStyle, QStyledItemDelegate, QToolButton, QVBoxLayout, QWidget)

import theme as T

#: Status colours, shared by every screen so one state is one colour everywhere.
#: The values live in theme.py with the rest of the tokens.
COLOUR = T.STATUS

WORD = {"done": "Done", "todo": "Not run yet", "manual": "Run outside the app",
        "running": "Running…", "queued": "Queued", "failed": "Failed last time",
        "notused": "Not used in this study", "waiting": "Waiting for you",
        "outdated": "Out of date"}

QUIET = T.QUIET


def esc(text):
    return html.escape(str(text or ""), quote=False)


class InfoPopup(QFrame):
    """A small card that closes when you click anywhere else."""

    def __init__(self, title, body_html, parent=None):
        super().__init__(parent, Qt.Popup)
        self.setFrameShape(QFrame.StyledPanel)
        self.setObjectName("infoPopup")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        head = QLabel(f"<b>{esc(title)}</b>")
        lay.addWidget(head)
        self.body = QLabel(body_html)
        self.body.setTextFormat(Qt.RichText)
        self.body.setWordWrap(True)
        self.body.setOpenExternalLinks(True)
        self.body.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.body.setMinimumWidth(320)
        self.body.setMaximumWidth(460)
        lay.addWidget(self.body)


class InfoButton(QToolButton):
    """The (i): a plain explanation of the thing it sits beside, on click.

    `body` may be a string or a callable returning one, so a button made before
    the study is known still says what is true when it is pressed.
    """

    def __init__(self, title, body, parent=None):
        super().__init__(parent)
        self.setText("i")
        self.setObjectName("infoButton")
        self.setToolTip("What is this?")
        # An icon-only button: a screen reader needs to hear what it is about.
        self.setAccessibleName(f"About {title}")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(22, 22)
        f = self.font()
        f.setBold(True)
        f.setItalic(True)
        self.setFont(f)
        self.title, self._body = title, body
        self.popup = None
        self.clicked.connect(self.show_info)

    def body(self):
        return self._body() if callable(self._body) else self._body

    def set_body(self, body):
        self._body = body

    def show_info(self):
        self.popup = InfoPopup(self.title, self.body(), self)
        self.popup.adjustSize()
        self.popup.move(self.mapToGlobal(QPoint(0, self.height() + 4)))
        self.popup.show()


class ScreenHeader(QWidget):
    """A screen's title, its (i), what it is for, and the person's part in it."""

    def __init__(self, screen, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 6)
        lay.setSpacing(6)
        top = QHBoxLayout()
        self.title = QLabel(screen.title)
        self.title.setFont(T.font(24, QFont.DemiBold))
        top.addWidget(self.title)
        self.info = InfoButton(screen.title, screen.info)
        top.addWidget(self.info)
        top.addStretch(1)
        lay.addLayout(top)
        self.intro = QLabel(screen.intro)
        self.intro.setWordWrap(True)
        self.intro.setStyleSheet(QUIET)
        # About 80 characters a line: a paragraph the full width of a wide
        # window is hard to read back along. Done with a right margin in
        # resizeEvent, not setMaximumWidth: a capped word-wrapped label is given
        # the height for the full width and its last line is cut off.
        self._intro_cap = QFontMetrics(self.intro.font()).averageCharWidth() * 88
        lay.addWidget(self.intro)
        self.role = QLabel("")
        self.role.setObjectName("yourPart")
        self.role.setWordWrap(True)
        self.role.setTextFormat(Qt.RichText)
        self.set_role(getattr(screen, "role", ""))
        lay.addWidget(self.role)

    def resizeEvent(self, e):
        spare = max(0, self.width() - self._intro_cap)
        if self.intro.contentsMargins().right() != spare:
            self.intro.setContentsMargins(0, 0, spare, 0)
        super().resizeEvent(e)

    def set_role(self, text):
        self.role.setText(f"<b style='color:{T.ACCENT_STRONG}'>Your part</b>"
                          f"&nbsp;&nbsp;{esc(text)}" if text else "")
        self.role.setVisible(bool(text))


class PartHeader(QWidget):
    """A sub-heading inside a screen, with a line of explanation."""

    def __init__(self, title, text="", parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 16, 0, 4)
        lay.setSpacing(2)
        head = QLabel(title)
        head.setFont(T.font(16, QFont.DemiBold))
        lay.addWidget(head)
        if text:
            line = QLabel(text)
            line.setWordWrap(True)
            line.setStyleSheet(QUIET)
            lay.addWidget(line)


class FileField(QWidget):
    """A file path with a Browse button, for a stage option that names a file."""

    valueChanged = Signal(str)

    def __init__(self, value="", parent=None, button="Browse...", filter=""):
        super().__init__(parent)
        self.filter = filter
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit(value or "")
        self.edit.setPlaceholderText("(the stage's default)")
        self.edit.setMinimumWidth(240)
        self.edit.setFont(T.font(13, mono=True))
        self.edit.editingFinished.connect(lambda: self.valueChanged.emit(self.value()))
        row.addWidget(self.edit, 1)
        self.browse = QPushButton(button)
        self.browse.clicked.connect(self._browse)
        row.addWidget(self.browse)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose a file", self.edit.text(),
                                              self.filter)
        if path:
            self.setValue(path)
            self.valueChanged.emit(self.value())

    def value(self):
        return self.edit.text().strip()

    def setValue(self, value):
        self.edit.setText(value or "")


def option_widget(opt, value):
    """(widget, getter) for one stage option."""
    if opt.kind == "file":
        field = FileField(value or "")
        return field, field.value
    if opt.kind == "bool":
        box = QCheckBox(opt.label)
        box.setChecked(bool(value))
        return box, box.isChecked
    if opt.kind == "choice":
        combo = QComboBox()
        combo.addItems(opt.choices)
        if value in opt.choices:
            combo.setCurrentText(value)
        return combo, combo.currentText
    if opt.kind == "int":
        spin = QSpinBox()
        spin.setRange(opt.minimum if opt.minimum is not None else -10**9,
                      opt.maximum if opt.maximum is not None else 10**9)
        if opt.zero and opt.minimum == 0:
            spin.setSpecialValueText(opt.zero)
        spin.setValue(int(value or 0))
        return spin, spin.value
    spin = QDoubleSpinBox()
    spin.setDecimals(4)
    spin.setSingleStep(0.005)
    spin.setRange(opt.minimum if opt.minimum is not None else -1e9,
                  opt.maximum if opt.maximum is not None else 1e9)
    spin.setValue(float(value or 0))
    return spin, spin.value


class OptionsPanel(QFrame):
    """A stage's options as switches and fields, each with its (i)."""

    changed = Signal(dict)          # {flag: value}, everything on screen

    def __init__(self, options, values, parent=None):
        super().__init__(parent)
        self.options = options
        self.setObjectName("optionsPanel")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 4, 4, 4)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)
        self.getters, self.widgets = {}, {}
        for opt in options:
            w, get = option_widget(opt, values.get(opt.flag, opt.default))
            self.widgets[opt.flag], self.getters[opt.flag] = w, get
            row = QHBoxLayout()
            row.addWidget(w)
            row.addWidget(InfoButton(opt.label, self._info(opt)))
            row.addStretch(1)
            holder = QWidget()
            holder.setLayout(row)
            row.setContentsMargins(0, 0, 0, 0)
            form.addRow("" if opt.kind == "bool" else opt.label, holder)
            for sig in ("toggled", "currentTextChanged", "valueChanged"):
                if hasattr(w, sig):
                    getattr(w, sig).connect(self._emit)
                    break
        lay.addLayout(form)
        foot = QHBoxLayout()
        self.reset_btn = QPushButton("Reset to defaults")
        self.reset_btn.clicked.connect(self.reset)
        foot.addWidget(self.reset_btn)
        note = QLabel("Remembered in the project and used every time this stage runs.")
        note.setStyleSheet(QUIET)
        foot.addWidget(note, 1)
        lay.addLayout(foot)

    @staticmethod
    def _info(opt):
        default = opt.default
        if opt.kind == "bool":
            default = "on" if default else "off"
        return (f"<p>{esc(opt.help)}</p><p style='{QUIET}'>Default: {esc(default)}"
                f" &nbsp;·&nbsp; command-line flag <code>{esc(opt.flag)}</code></p>")

    def values(self):
        return {flag: get() for flag, get in self.getters.items()}

    def set_values(self, values):
        for opt in self.options:
            w = self.widgets[opt.flag]
            v = values.get(opt.flag, opt.default)
            w.blockSignals(True)
            if opt.kind == "bool":
                w.setChecked(bool(v))
            elif opt.kind == "choice":
                w.setCurrentText(str(v))
            elif opt.kind == "file":
                w.setValue(v or "")
            elif opt.kind == "int":
                w.setValue(int(v or 0))
            else:
                w.setValue(float(v or 0))
            w.blockSignals(False)

    def reset(self):
        self.set_values({})
        self._emit()

    def _emit(self, *_):
        self.changed.emit(self.values())


class StageRow(QFrame):
    """One stage: status dot, name over plain words, status word, then its buttons.

    The buttons sit in one group at the right that never wraps, so Run is in
    the same place on every row whatever the width. What the main button says
    follows the row's state (Run, Redo, Run..., Open curator); the screen
    decides that and hands it in with `refresh`.
    """

    run_clicked = Signal(str)
    open_clicked = Signal(str)
    goto_clicked = Signal(str)          # a screen id: "Open curator" goes to Curate
    options_changed = Signal(str, dict)

    def __init__(self, stage, options=(), parent=None, open_label=None):
        super().__init__(parent)
        self.stage = stage
        self.setObjectName("stageRow")
        self._goto = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 10, 14, 10)
        outer.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(10)
        dot_col = QVBoxLayout()
        dot_col.setContentsMargins(0, 6, 0, 0)
        self.dot = QLabel("")
        self.dot.setFixedSize(10, 10)
        dot_col.addWidget(self.dot)
        dot_col.addStretch(1)
        top.addLayout(dot_col)

        text = QVBoxLayout()
        text.setSpacing(2)
        line = QHBoxLayout()
        line.setSpacing(8)
        self.name = QLabel(stage.title)
        f = self.name.font()
        f.setWeight(QFont.DemiBold)
        self.name.setFont(f)
        self.name.setWordWrap(True)
        line.addWidget(self.name, 1)
        self.code = QLabel("")
        self.code.setStyleSheet(QUIET)
        self.code.setFont(T.font(12, mono=True))
        line.addWidget(self.code, 0, Qt.AlignTop)
        self.mark = QLabel("")
        line.addWidget(self.mark, 0, Qt.AlignTop)
        text.addLayout(line)
        self.plain = QLabel("")
        self.plain.setWordWrap(True)
        self.plain.setStyleSheet(QUIET)
        self.plain.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        text.addWidget(self.plain)
        self.status = QLabel("")
        self.status.setFont(T.font(13))
        self.status.setWordWrap(True)
        text.addWidget(self.status)
        top.addLayout(text, 1)

        # The right-hand group. Fixed, so a narrow window wraps the words on the
        # left instead of pushing Run off the row or onto a second line.
        actions = QWidget()
        actions.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        al = QHBoxLayout(actions)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(6)
        self.opt_btn = QToolButton()
        self.opt_btn.setText("Options")
        self.opt_btn.setCheckable(True)
        self.opt_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.opt_btn.setArrowType(Qt.RightArrow)
        self.opt_btn.toggled.connect(self._toggle_options)
        self.opt_btn.setVisible(bool(options))
        al.addWidget(self.opt_btn)
        # A curator opens its page; a stage named in workflow.FOLDERS opens the
        # folder of pictures it made, once there is something in it.
        self.open_btn = QPushButton(open_label or "Open")
        T.mark(self.open_btn, kind="small")
        self._has_open = bool(stage.curator or open_label)
        self.open_btn.setVisible(self._has_open)
        self.open_btn.clicked.connect(lambda: self.open_clicked.emit(stage.sid))
        al.addWidget(self.open_btn)
        self.info = InfoButton(stage.title, "")
        al.addWidget(self.info)
        self.run_btn = QPushButton("Build page" if stage.curator else "Run")
        self.run_btn.setMinimumWidth(96)
        self.run_btn.clicked.connect(self._action)
        al.addWidget(self.run_btn)
        top.addWidget(actions, 0, Qt.AlignTop)
        outer.addLayout(top)

        self.bar = QProgressBar()
        self.bar.setObjectName("rowProgress")
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 0)             # busy: a stage reports no percentage
        self.bar.setVisible(False)
        outer.addWidget(self.bar)

        self.options = None
        if options:
            self.options = OptionsPanel(list(options), {})
            self.options.setVisible(False)
            self.options.changed.connect(lambda v: self.options_changed.emit(stage.sid, v))
            outer.addWidget(self.options)

    def _action(self):
        if self._goto:
            self.goto_clicked.emit(self._goto)
        else:
            self.run_clicked.emit(self.stage.sid)

    def _toggle_options(self, on):
        self.opt_btn.setArrowType(Qt.DownArrow if on else Qt.RightArrow)
        if self.options:
            self.options.setVisible(on)

    def set_last(self, last):
        """The last row of a card draws no divider under itself."""
        T.mark(self, last=bool(last))

    def refresh(self, *, title, code, plain, status, state, info_html, mark="",
                can_run=True, option_values=None, changed_count=0, action=None,
                goto=None, open_visible=None):
        """Refresh everything the row shows. Called on every status refresh.

        `action` is the main button's label (default: Redo once done, else Run)
        and `goto` a screen id that button goes to instead of running.
        """
        colour = COLOUR.get(state, T.TEXT_MUTED)
        self.name.setText(title)
        self.code.setText(code)
        self.plain.setText(plain)
        self.status.setText(status)
        self.status.setStyleSheet(f"color:{colour}")
        self.dot.setStyleSheet(f"background:{colour};border-radius:5px")
        self.mark.setText(mark)
        self.mark.setStyleSheet(f"color:{T.DANGER};font-weight:600" if "unblind" in mark.lower()
                                else f"color:{T.WARN};font-weight:600")
        self.info.title = title
        self.info.setAccessibleName(f"About {title}")
        self.info.set_body(info_html)
        if action is None:
            if self.stage.curator:
                action = "Rebuild page" if state == "done" else "Build page"
            else:
                action = "Redo" if state == "done" else "Run"
        self._goto = goto
        self.run_btn.setText(action)
        primary = goto is not None or (state not in ("done", "notused", "manual")
                                       and action != "Redo")
        T.mark(self.run_btn, kind="primary" if primary else "")
        self.run_btn.setEnabled(True if goto else can_run)
        if open_visible is not None:
            self.open_btn.setVisible(self._has_open and open_visible)
        self.bar.setVisible(state == "running")
        if self.options is not None and option_values is not None:
            self.options.set_values(option_values)
            self.opt_btn.setText(f"Options ({changed_count} changed)" if changed_count
                                 else "Options")
        self.name.setStyleSheet(f"color:{T.NOTUSED}" if state == "notused" else "")


class StepDelegate(QStyledItemDelegate):
    """Draws a step-list item: numbered circle, title, one line of what, progress.

    The item carries its words as data (see the ROLE_ constants) and the
    delegate only paints them, so the list stays an ordinary QListWidget:
    Up/Down change screen, and count(), item() and setCurrentRow() behave as
    before.
    """

    ROLE_BADGE = Qt.UserRole + 1        # "1".."5", or the house for Project
    ROLE_SUB = Qt.UserRole + 2          # one line under the title
    ROLE_PROGRESS = Qt.UserRole + 3     # "5 of 6 cards", "Done", or ""
    ROLE_COMPLETE = Qt.UserRole + 4     # True: the circle turns green with a tick

    MARGIN, PAD, CIRCLE, GAP = 6, 10, 28, 12

    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.title_font = T.font(14, QFont.DemiBold)
        self.sub_font = T.font(13)
        self.prog_font = T.font(12, QFont.DemiBold)
        self.badge_font = T.font(13, QFont.DemiBold)
        self.compact = False

    def _text_width(self, width):
        return max(60, width - 2 * self.MARGIN - 2 * self.PAD - self.CIRCLE - self.GAP)

    def _sub_height(self, text, width):
        if not text:
            return 0
        fm = QFontMetrics(self.sub_font)
        return fm.boundingRect(QRect(0, 0, width, 1000), Qt.TextWordWrap, text).height()

    def _shows_sub(self, index):
        # On a short window (a laptop at 614 px) all six steps with their
        # descriptions do not fit, and a step list that scrolls hides Results.
        # Then only the current step keeps its description.
        return not self.compact or index.row() == self.view.currentRow()

    def item_height(self, index, width, full=False):
        tw = self._text_width(width)
        h = QFontMetrics(self.title_font).height()
        if full or self._shows_sub(index):
            h += 2 + self._sub_height(index.data(self.ROLE_SUB) or "", tw)
        if index.data(self.ROLE_PROGRESS):
            h += 4 + QFontMetrics(self.prog_font).height()
        return max(h + 2 * self.PAD, self.CIRCLE + 2 * self.PAD, 44) + 2 * 3

    def sizeHint(self, option, index):
        width = self.view.viewport().width() or 236
        return QSize(width, self.item_height(index, width))

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        r = option.rect.adjusted(self.MARGIN, 3, -self.MARGIN, -3)
        selected = bool(option.state & QStyle.State_Selected)
        complete = bool(index.data(self.ROLE_COMPLETE))
        if selected:
            painter.setPen(QPen(QColor(T.ACCENT_LINE), 1))
            painter.setBrush(QColor(T.ACCENT_BG))
            painter.drawRoundedRect(r, 8, 8)
        elif option.state & QStyle.State_MouseOver:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(T.SURFACE_HOVER))
            painter.drawRoundedRect(r, 8, 8)
        if option.state & QStyle.State_HasFocus and self.view.hasFocus():
            painter.setPen(QPen(QColor(T.ACCENT), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(r.adjusted(1, 1, -1, -1), 8, 8)

        circle = QRect(r.left() + self.PAD, r.top() + self.PAD, self.CIRCLE, self.CIRCLE)
        badge = index.data(self.ROLE_BADGE) or ""
        if complete:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(T.OK))
            fg, badge = QColor("#ffffff"), "✓"
        elif selected:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(T.ACCENT))
            fg = QColor("#ffffff")
        else:
            painter.setPen(QPen(QColor(T.LINE), 1.5))
            painter.setBrush(QColor(T.SURFACE))
            fg = QColor(T.TEXT_MUTED)
        painter.drawEllipse(circle)
        painter.setPen(fg)
        painter.setFont(self.badge_font)
        painter.drawText(circle, Qt.AlignCenter, badge)

        x = circle.right() + 1 + self.GAP
        tw = self._text_width(option.rect.width())
        y = r.top() + self.PAD
        painter.setPen(QColor(T.ACCENT_STRONG if selected else T.TEXT))
        painter.setFont(self.title_font)
        th = QFontMetrics(self.title_font).height()
        title = index.data(Qt.DisplayRole) or ""
        painter.drawText(QRect(x, y, tw, th), Qt.AlignLeft | Qt.AlignVCenter,
                         QFontMetrics(self.title_font).elidedText(title, Qt.ElideRight, tw))
        y += th + 2
        sub = (index.data(self.ROLE_SUB) or "") if self._shows_sub(index) else ""
        if sub:
            sh = self._sub_height(sub, tw)
            painter.setPen(QColor(T.TEXT_MUTED))
            painter.setFont(self.sub_font)
            painter.drawText(QRect(x, y, tw, sh), Qt.AlignLeft | Qt.TextWordWrap, sub)
            y += sh
        prog = index.data(self.ROLE_PROGRESS) or ""
        if prog:
            y += 4
            ph = QFontMetrics(self.prog_font).height()
            painter.setPen(QColor(T.OK if complete else (T.ACCENT_STRONG if selected
                                                         else T.TEXT_MUTED)))
            painter.setFont(self.prog_font)
            painter.drawText(QRect(x, y, tw, ph), Qt.AlignLeft | Qt.AlignVCenter, prog)
        painter.restore()


class StepList(QListWidget):
    """The step list on the left, drawn by StepDelegate."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("stepList")
        self.setItemDelegate(StepDelegate(self))
        self.setMouseTracking(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # Heights depend on how the descriptions wrap, so lay out again on resize.
        self.setResizeMode(QListView.Adjust)
        self.setUniformItemSizes(False)
        self.setFrameShape(QFrame.NoFrame)
        self.setContentsMargins(0, 6, 0, 6)

    def currentChanged(self, current, previous):
        # Overridden rather than connected to currentRowChanged: the window
        # moves the selection with signals blocked, and the current step's
        # height still has to change with it.
        super().currentChanged(current, previous)
        self._fit()

    def _fit(self):
        """Choose full or compact items for the height there is, and lay out again."""
        delegate = self.itemDelegate()
        width = self.viewport().width() or 236
        need = sum(delegate.item_height(self.model().index(i, 0), width, full=True)
                   for i in range(self.count()))
        delegate.compact = need > self.viewport().height()
        self.scheduleDelayedItemsLayout()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def set_step(self, row, *, title, badge, sub, progress="", complete=False):
        it = self.item(row)
        it.setText(title)
        it.setData(StepDelegate.ROLE_BADGE, badge)
        it.setData(StepDelegate.ROLE_SUB, sub)
        it.setData(StepDelegate.ROLE_PROGRESS, progress)
        it.setData(StepDelegate.ROLE_COMPLETE, complete)
        words = ". ".join(w for w in (title, sub, progress) if w)
        it.setData(Qt.AccessibleTextRole, words)
        it.setToolTip(words)
        self._fit()


class UnblindDialog(QDialog):
    """Asks before a stage reads the treatment groups, with the workbook it will read.

    Cancel has the focus, so Enter on a dialog that was not read stays blind.
    """

    def __init__(self, names, workbook="", parent=None):
        super().__init__(parent)
        self.setObjectName("unblind")
        self.setWindowTitle("Unblind the analysis?")
        self.setAccessibleName("Unblind the analysis?")
        self.setModal(True)
        self.setMinimumWidth(480)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        rule = QFrame()
        rule.setFixedHeight(4)
        rule.setStyleSheet(f"background:{T.DANGER}")
        lay.addWidget(rule)
        body = QVBoxLayout()
        body.setContentsMargins(22, 16, 22, 18)
        body.setSpacing(10)
        head = QLabel("Unblind the analysis?")
        head.setFont(T.font(18, QFont.DemiBold))
        body.addWidget(head)
        text = QLabel("This stage reads the treatment groups for the first time. "
                      "Everything before it was keyed by animal only.")
        text.setWordWrap(True)
        body.addWidget(text)
        if names:
            which = QLabel(f"Runs: {esc(names)}")
            which.setStyleSheet(QUIET)
            which.setWordWrap(True)
            body.addWidget(which)
        label = QLabel("<b>Sampling workbook (.xlsx)</b>")
        body.addWidget(label)
        self.field = FileField(workbook or "", button="Choose...",
                               filter="Excel workbook (*.xlsx);;All files (*)")
        self.field.edit.setPlaceholderText("(the workbook in the slide folder)")
        label.setBuddy(self.field.edit)
        body.addWidget(self.field)
        note = QLabel("Do this once the regions and detection settings are final. "
                      "Spreadsheets stay keyed by animal and can be rebuilt at any time.")
        note.setWordWrap(True)
        note.setStyleSheet(QUIET)
        body.addWidget(note)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        self.cancel_btn.setDefault(True)
        buttons.addWidget(self.cancel_btn)
        self.ok_btn = QPushButton("Unblind and run")
        self.ok_btn.setAutoDefault(False)
        T.mark(self.ok_btn, kind="danger")
        self.ok_btn.clicked.connect(self.accept)
        buttons.addWidget(self.ok_btn)
        body.addLayout(buttons)
        lay.addLayout(body)
        self.cancel_btn.setFocus()

    def workbook(self):
        return self.field.value()


def bold_font(widget, delta=0):
    f = QFont(widget.font())
    f.setBold(True)
    if delta:
        f.setPointSize(f.pointSize() + delta)
    return f


def colour(state):
    return QColor(COLOUR.get(state, T.TEXT_MUTED))
