"""The app's look: one set of colour tokens, one palette, one stylesheet.

Every colour the app shows comes from the tokens below (docs/ui-handoff.md,
section 3), so a later dark theme is a swap of this table rather than a hunt
through every widget. Widgets ask for a role (an object name, or the `kind`
property on a button) and the stylesheet here decides what that looks like.

The app uses the Fusion style with an explicit light palette. Left to the
platform style, Windows or a desktop in dark mode repaints the frames and
inputs while the text colours chosen here stay dark, which leaves half the
window unreadable.
"""

from PySide6.QtGui import QColor, QFont, QPalette

# ---- colour tokens (section 3.1) -------------------------------------------

BG = "#f6f8fa"              # window background
SURFACE = "#ffffff"         # cards, rows, panels, step list
SURFACE_HOVER = "#eef1f4"   # hover on step items
LINE = "#d0d7de"            # borders
LINE_SOFT = "#e6eaee"       # row dividers inside cards
TEXT = "#1f2328"            # body
TEXT_MUTED = "#59636e"      # hints. Replaces #8b949e, which fails AA contrast on white.
ACCENT = "#0969da"          # primary button, current step, links
ACCENT_STRONG = "#0550ae"   # "Your part" label, pressed primary
ACCENT_BG = "#ddf4ff"       # current step, "Your part", selected tab
ACCENT_LINE = "#54aeff"
OK = "#1a7f37"              # done
OK_BG = "#dafbe1"
WARN = "#9a6700"            # waiting, amber notices
WARN_BG = "#fff8c5"
WARN_LINE = "#d4a72c"
DANGER = "#cf222e"          # failed, unblind, exclude
DANGER_STRONG = "#a40e26"
PURPLE = "#8250df"          # run outside the app
PURPLE_TEXT = "#6639ba"     # purple as text on a light background
RUNNING = ACCENT            # running and queued
NOTUSED = "#8c959f"         # a stage this study does not use

#: Stage status colours, shared by every screen so one state is one colour everywhere.
STATUS = {"done": OK, "todo": TEXT_MUTED, "manual": PURPLE_TEXT,
          "running": RUNNING, "queued": RUNNING, "failed": DANGER,
          "notused": NOTUSED, "waiting": WARN, "outdated": WARN}

#: Inline style for a hint or secondary line, for rich text and setStyleSheet alike.
QUIET = f"color:{TEXT_MUTED}"
#: A small hint under a field.
HINT = f"color:{TEXT_MUTED};font-size:12px"

# ---- type (section 3.2) -----------------------------------------------------

UI_FONTS = ["Segoe UI", "system-ui", "Ubuntu", "Cantarell", "DejaVu Sans", "Arial"]
MONO_FONTS = ["Consolas", "DejaVu Sans Mono", "Menlo", "monospace"]
BODY_PX = 14


def font(px=BODY_PX, weight=None, mono=False):
    f = QFont()
    f.setFamilies(MONO_FONTS if mono else UI_FONTS)
    f.setPixelSize(px)
    if weight is not None:
        f.setWeight(weight)
    return f


def palette():
    p = QPalette()
    c = QColor
    roles = {
        QPalette.Window: BG, QPalette.WindowText: TEXT,
        QPalette.Base: SURFACE, QPalette.AlternateBase: BG,
        QPalette.Text: TEXT, QPalette.BrightText: SURFACE,
        QPalette.Button: SURFACE, QPalette.ButtonText: TEXT,
        QPalette.Highlight: ACCENT, QPalette.HighlightedText: SURFACE,
        QPalette.ToolTipBase: SURFACE, QPalette.ToolTipText: TEXT,
        QPalette.PlaceholderText: TEXT_MUTED,
        QPalette.Link: ACCENT, QPalette.LinkVisited: ACCENT_STRONG,
        QPalette.Light: SURFACE, QPalette.Midlight: LINE_SOFT,
        QPalette.Mid: LINE, QPalette.Dark: NOTUSED, QPalette.Shadow: TEXT_MUTED,
    }
    for role, value in roles.items():
        p.setColor(role, c(value))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, c(NOTUSED))
    p.setColor(QPalette.Disabled, QPalette.Base, c(BG))
    p.setColor(QPalette.Disabled, QPalette.Button, c(BG))
    return p


def stylesheet():
    """The one application stylesheet. Roles, not widgets, are styled."""
    return f"""
QToolTip {{ background:{SURFACE}; color:{TEXT}; border:1px solid {LINE}; padding:4px 6px; }}

QPushButton {{
    background:{SURFACE}; color:{TEXT}; border:1px solid {LINE}; border-radius:6px;
    padding:6px 12px; min-height:20px;
}}
QPushButton:hover {{ background:{SURFACE_HOVER}; }}
QPushButton:pressed {{ background:{LINE_SOFT}; }}
QPushButton:checked {{ background:{ACCENT_BG}; border-color:{ACCENT_LINE}; color:{ACCENT_STRONG}; }}
QPushButton:disabled {{ background:{BG}; color:{NOTUSED}; border-color:{LINE_SOFT}; }}
QPushButton:focus {{ border:2px solid {ACCENT}; padding:5px 11px; }}
QPushButton[kind="primary"] {{
    background:{ACCENT}; color:#ffffff; border:1px solid {ACCENT}; font-weight:600;
}}
QPushButton[kind="primary"]:hover {{ background:{ACCENT_STRONG}; border-color:{ACCENT_STRONG}; }}
QPushButton[kind="primary"]:focus {{ border:2px solid {ACCENT_STRONG}; }}
QPushButton[kind="primary"]:disabled {{ background:{LINE_SOFT}; color:{NOTUSED}; border-color:{LINE_SOFT}; }}
QPushButton[kind="danger"] {{
    background:{DANGER}; color:#ffffff; border:1px solid {DANGER}; font-weight:600;
}}
QPushButton[kind="danger"]:hover {{ background:{DANGER_STRONG}; border-color:{DANGER_STRONG}; }}
QPushButton[kind="danger"]:focus {{ border:2px solid {DANGER_STRONG}; }}
QPushButton[kind="small"] {{ padding:4px 10px; font-size:13px; }}
QPushButton[kind="small"]:focus {{ padding:3px 9px; }}

QPushButton#advButton {{
    text-align:left; border:1px solid transparent; background:transparent; padding:6px 10px;
}}
QPushButton#advButton:hover {{ background:{SURFACE_HOVER}; }}
QPushButton#advButton:checked {{ background:{ACCENT_BG}; border-color:{ACCENT_LINE}; color:{ACCENT_STRONG}; }}
QPushButton#advButton:focus {{ border:2px solid {ACCENT}; padding:5px 9px; }}

QToolButton {{
    background:{SURFACE}; color:{TEXT}; border:1px solid {LINE}; border-radius:6px; padding:4px 8px;
}}
QToolButton:hover {{ background:{SURFACE_HOVER}; }}
QToolButton:checked {{ background:{ACCENT_BG}; border-color:{ACCENT_LINE}; }}
QToolButton:focus {{ border:2px solid {ACCENT}; }}
QToolButton#infoButton {{
    border:1px solid {LINE}; border-radius:10px; color:{ACCENT}; padding:0; background:{SURFACE};
}}
QToolButton#infoButton:hover {{ background:{ACCENT}; color:#ffffff; border-color:{ACCENT}; }}
QToolButton#infoButton:focus {{ border:2px solid {ACCENT}; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
    background:{SURFACE}; color:{TEXT}; border:1px solid {LINE}; border-radius:6px;
    padding:4px 6px; selection-background-color:{ACCENT}; selection-color:#ffffff;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {{ border:2px solid {ACCENT}; padding:3px 5px; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    background:{BG}; color:{NOTUSED};
}}
QComboBox QAbstractItemView {{
    background:{SURFACE}; border:1px solid {LINE}; selection-background-color:{ACCENT_BG};
    selection-color:{TEXT};
}}
QCheckBox:focus, QRadioButton:focus {{ color:{ACCENT_STRONG}; }}

QTabWidget::pane {{
    background:{SURFACE}; border:1px solid {LINE}; border-radius:8px; top:-1px;
}}
QTabBar::tab {{
    background:transparent; color:{TEXT_MUTED}; border:1px solid transparent;
    border-top-left-radius:6px; border-top-right-radius:6px; padding:7px 14px; margin-right:2px;
}}
QTabBar::tab:hover {{ background:{SURFACE_HOVER}; color:{TEXT}; }}
QTabBar::tab:selected {{
    background:{ACCENT_BG}; color:{ACCENT_STRONG}; border-color:{ACCENT_LINE};
}}
QTabBar::tab:focus {{ border:2px solid {ACCENT}; }}

QScrollArea {{ background:transparent; border:none; }}
QScrollArea > QWidget#qt_scrollarea_viewport > QWidget {{ background:transparent; }}
QProgressBar {{
    background:{LINE_SOFT}; border:none; border-radius:5px; max-height:10px; min-height:10px;
}}
QProgressBar::chunk {{ background:{ACCENT}; border-radius:5px; }}
QProgressBar[complete="true"]::chunk {{ background:{OK}; }}
QProgressBar#rowProgress {{ max-height:6px; min-height:6px; border-radius:3px; }}
QProgressBar#rowProgress::chunk {{ border-radius:3px; }}

QStatusBar {{ background:{SURFACE}; border-top:1px solid {LINE}; color:{TEXT_MUTED}; }}
QStatusBar::item {{ border:none; }}
QMenuBar {{ background:{SURFACE}; border-bottom:1px solid {LINE_SOFT}; }}
QMenuBar::item:selected {{ background:{SURFACE_HOVER}; }}
QMenu {{ background:{SURFACE}; border:1px solid {LINE}; }}
QMenu::item:selected {{ background:{ACCENT_BG}; color:{TEXT}; }}

QFrame#stepColumn {{ background:{SURFACE}; border:none; border-right:1px solid {LINE}; }}
QListWidget#stepList {{ background:{SURFACE}; border:none; outline:none; }}
QLabel#groupLabel {{
    color:{TEXT_MUTED}; font-size:11px; font-weight:600; letter-spacing:0.5px;
}}
QFrame#card {{ background:{SURFACE}; border:1px solid {LINE}; border-radius:8px; }}
QFrame#nextUp {{ background:{SURFACE}; border:2px solid {ACCENT}; border-radius:8px; }}
QLabel#nextUpLabel {{ color:{ACCENT}; font-weight:600; }}
QLabel#yourPart {{
    background:{ACCENT_BG}; border:1px solid {ACCENT_LINE}; border-radius:8px; padding:8px 12px;
    color:{TEXT};
}}
QFrame#notice {{ background:{WARN_BG}; border:1px solid {WARN_LINE}; border-radius:8px; }}
QFrame#notice QLabel {{ background:transparent; color:{TEXT}; }}
QFrame#stageRow {{ background:{SURFACE}; border:none; border-bottom:1px solid {LINE_SOFT}; }}
QFrame#stageRow[last="true"] {{ border-bottom:none; }}
QFrame#stageRow QLabel {{ background:transparent; }}
QFrame#optionsPanel {{ border:none; border-left:2px solid {ACCENT_LINE}; margin-left:18px; }}
QFrame#infoPopup {{ background:{SURFACE}; border:1px solid {LINE}; border-radius:8px; }}
QFrame#drawer {{ background:{SURFACE}; border:none; border-top:1px solid {LINE}; }}
QPlainTextEdit#log {{ background:{SURFACE}; border:1px solid {LINE_SOFT}; }}
QDialog#unblind {{ border-top:4px solid {DANGER}; }}
"""


def mark(widget, **props):
    """Set style properties on a widget and have the stylesheet look again.

    Qt reads dynamic properties when a widget is polished, so a property that
    changes later (a button turning primary, a bar completing) needs this.
    """
    for key, value in props.items():
        widget.setProperty(key, value)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


def apply(app):
    """Fusion, the light palette, the body font and the stylesheet, on the whole app."""
    app.setStyle("Fusion")
    app.setPalette(palette())
    app.setFont(font())
    app.setStyleSheet(stylesheet())
