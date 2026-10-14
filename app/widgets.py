"""Small pieces the screens are built from: the (i) button, headers, stage rows.

Nothing here knows about the pipeline beyond what it is handed. A stage row is
given a stage, the words to show and the options to offer, and says when its
buttons are pressed; the screen decides what that means.
"""

import html

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QPushButton, QSizePolicy, QSpinBox, QToolButton, QVBoxLayout, QWidget)

#: Status colours, shared by every screen so one state is one colour everywhere.
COLOUR = {"done": "#3fb950", "todo": "#8b949e", "manual": "#a371f7",
          "running": "#4da3ff", "queued": "#4da3ff", "failed": "#f85149",
          "notused": "#484f58", "waiting": "#d29922"}

WORD = {"done": "Done", "todo": "Not run yet", "manual": "Run outside the app",
        "running": "Running…", "queued": "Queued", "failed": "Failed last time",
        "notused": "Not used in this study", "waiting": "Waiting for you"}

QUIET = "color:#8b949e"


def esc(text):
    return html.escape(str(text or ""), quote=False)


class InfoPopup(QFrame):
    """A small card that closes when you click anywhere else."""

    def __init__(self, title, body_html, parent=None):
        super().__init__(parent, Qt.Popup)
        self.setFrameShape(QFrame.StyledPanel)
        self.setObjectName("infoPopup")
        self.setStyleSheet("#infoPopup{border:1px solid #6e7681;border-radius:6px;}")
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
        self.setToolTip("What is this?")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(20, 20)
        f = self.font()
        f.setBold(True)
        f.setItalic(True)
        self.setFont(f)
        self.setStyleSheet("QToolButton{border:1px solid #6e7681;border-radius:10px;"
                           "color:#4da3ff;padding:0}"
                           "QToolButton:hover{background:#4da3ff;color:white}")
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
    """A screen's title, its (i), and two or three sentences of what it is for."""

    def __init__(self, screen, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 4)
        top = QHBoxLayout()
        self.title = QLabel(screen.title)
        f = self.title.font()
        f.setPointSize(f.pointSize() + 6)
        f.setBold(True)
        self.title.setFont(f)
        top.addWidget(self.title)
        self.info = InfoButton(screen.title, screen.info)
        top.addWidget(self.info)
        top.addStretch(1)
        lay.addLayout(top)
        self.intro = QLabel(screen.intro)
        self.intro.setWordWrap(True)
        self.intro.setStyleSheet(QUIET)
        lay.addWidget(self.intro)


class PartHeader(QWidget):
    """A sub-heading inside a screen, with a line of explanation."""

    def __init__(self, title, text="", parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 14, 0, 2)
        head = QLabel(title)
        f = head.font()
        f.setBold(True)
        f.setPointSize(f.pointSize() + 1)
        head.setFont(f)
        lay.addWidget(head)
        if text:
            line = QLabel(text)
            line.setWordWrap(True)
            line.setStyleSheet(QUIET)
            lay.addWidget(line)


def option_widget(opt, value):
    """(widget, getter) for one stage option."""
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
        self.setStyleSheet("#optionsPanel{border-left:2px solid #4da3ff;margin-left:18px}")
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
    """One stage: status, name, plain explanation, (i), Options, Run."""

    run_clicked = Signal(str)
    open_clicked = Signal(str)
    options_changed = Signal(str, dict)

    def __init__(self, stage, options=(), parent=None, open_label=None):
        super().__init__(parent)
        self.stage = stage
        self.setObjectName("stageRow")
        self.setStyleSheet("#stageRow{border-bottom:1px solid rgba(128,128,128,0.25)}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 6, 4, 6)
        outer.setSpacing(2)

        top = QHBoxLayout()
        self.dot = QLabel("●")
        self.dot.setFixedWidth(16)
        top.addWidget(self.dot)
        self.name = QLabel(stage.title)
        f = self.name.font()
        f.setBold(True)
        self.name.setFont(f)
        top.addWidget(self.name)
        self.code = QLabel("")
        self.code.setStyleSheet(QUIET)
        top.addWidget(self.code)
        self.mark = QLabel("")
        top.addWidget(self.mark)
        self.info = InfoButton(stage.title, "")
        top.addWidget(self.info)
        top.addStretch(1)
        self.status = QLabel("")
        self.status.setStyleSheet(QUIET)
        top.addWidget(self.status)
        self.opt_btn = QToolButton()
        self.opt_btn.setText("Options")
        self.opt_btn.setCheckable(True)
        self.opt_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.opt_btn.setArrowType(Qt.RightArrow)
        self.opt_btn.toggled.connect(self._toggle_options)
        self.opt_btn.setVisible(bool(options))
        top.addWidget(self.opt_btn)
        # A curator opens its page; a stage named in workflow.FOLDERS opens the
        # folder of pictures it made.
        self.open_btn = QPushButton(open_label or "Open")
        self.open_btn.setVisible(bool(stage.curator or open_label))
        self.open_btn.clicked.connect(lambda: self.open_clicked.emit(stage.sid))
        top.addWidget(self.open_btn)
        self.run_btn = QPushButton("Build page" if stage.curator else "Run")
        self.run_btn.setMinimumWidth(84)
        self.run_btn.clicked.connect(lambda: self.run_clicked.emit(stage.sid))
        top.addWidget(self.run_btn)
        outer.addLayout(top)

        self.plain = QLabel("")
        self.plain.setWordWrap(True)
        self.plain.setContentsMargins(20, 0, 0, 0)
        self.plain.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        outer.addWidget(self.plain)

        self.options = None
        if options:
            self.options = OptionsPanel(list(options), {})
            self.options.setVisible(False)
            self.options.changed.connect(lambda v: self.options_changed.emit(stage.sid, v))
            outer.addWidget(self.options)

    def _toggle_options(self, on):
        self.opt_btn.setArrowType(Qt.DownArrow if on else Qt.RightArrow)
        if self.options:
            self.options.setVisible(on)

    def refresh(self, *, title, code, plain, status, state, info_html, mark="",
             can_run=True, option_values=None, changed_count=0):
        """Refresh everything the row shows. Called on every status refresh."""
        self.name.setText(title)
        self.code.setText(code)
        self.plain.setText(plain)
        self.status.setText(status)
        self.dot.setStyleSheet(f"color:{COLOUR.get(state, '#8b949e')}")
        self.mark.setText(mark)
        self.mark.setStyleSheet("color:#f85149;font-weight:600" if "unblind" in mark.lower()
                                else "color:#d29922;font-weight:600")
        self.info.title = title
        self.info.set_body(info_html)
        self.run_btn.setEnabled(can_run)
        if self.options is not None and option_values is not None:
            self.options.set_values(option_values)
            self.opt_btn.setText(f"Options ({changed_count} changed)" if changed_count
                                 else "Options")
        dim = state == "notused"
        self.name.setStyleSheet(QUIET if dim else "")
        self.plain.setStyleSheet(QUIET)


def bold_font(widget, delta=0):
    f = QFont(widget.font())
    f.setBold(True)
    if delta:
        f.setPointSize(f.pointSize() + delta)
    return f


def colour(state):
    return QColor(COLOUR.get(state, "#8b949e"))
