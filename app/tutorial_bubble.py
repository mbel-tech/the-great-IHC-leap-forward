"""The tutorial's pointer: an outline round the control to press, and a line beside it.

A transparent child laid over the whole main window. It never takes a click;
it follows its target when the window or the target moves, and hides when the
target is hidden (the learner went to another screen). A target that cannot be
found or is outside the window gets the bubble on the side panel instead, with
the control named in the text.
"""

from PySide6.QtCore import QEvent, QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QWidget

ACCENT = QColor("#e8590c")


class PointerOverlay(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.setObjectName("tutorialBubble")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.window_ = window
        self.target = None
        self.panel = None
        self.text = ""
        self.attached_to = ""
        self.outline = QRect()
        window.installEventFilter(self)
        self.hide()

    # ---- what to point at ----------------------------------------------------

    def show_for(self, target, text, window=None, panel=None):
        self._release()
        self.panel = panel
        self.text = text or ""
        w = window or self.window_
        self.target = w.findChild(QWidget, target) if target else None
        if self.target is not None:
            self.target.installEventFilter(self)
        self._place()

    def hide_pointer(self):
        self._release()
        self.text = ""
        self.attached_to = ""
        self.hide()

    def _release(self):
        if self.target is not None:
            try:
                self.target.removeEventFilter(self)
            except RuntimeError:
                pass
        self.target = None

    @staticmethod
    def _name(widget, fallback):
        if isinstance(widget, QAbstractButton) and widget.text():
            return widget.text().replace("&", "")
        return fallback

    def _place(self):
        if not self.text:
            self.hide()
            return
        self.setGeometry(self.window_.rect())
        t = self.target
        if t is not None and not t.isVisible():
            self.attached_to = ""
            self.hide()
            return
        if t is not None:
            top_left = t.mapTo(self.window_, QPoint(0, 0))
            r = QRect(top_left, t.size()).adjusted(-4, -4, 4, 4)
            if self.window_.rect().contains(r.center()):
                self.attached_to = "target"
                self.outline = r
                self.show()
                self.raise_()
                self.update()
                return
        # Not on screen: say it in the panel's corner instead.
        self.attached_to = "panel"
        if self.panel is not None and self.panel.isVisible():
            self.outline = QRect(self.panel.mapTo(self.window_, QPoint(0, 0)),
                                 self.panel.size()).adjusted(2, 2, -2, -2)
        else:
            self.outline = QRect()
        self.show()
        self.raise_()
        self.update()

    def label(self):
        if self.attached_to == "panel":
            name = self._name(self.target, "the control named in the panel")
            return f"Look for {name}: {self.text}"
        return self.text

    # ---- following ---------------------------------------------------------------

    def eventFilter(self, obj, ev):
        if ev.type() in (QEvent.Resize, QEvent.Move, QEvent.Show, QEvent.Hide,
                         QEvent.LayoutRequest):
            if self.text:
                self._place()
        return False

    # ---- drawing ------------------------------------------------------------------

    def paintEvent(self, _ev):
        if not self.text or self.outline.isNull():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(ACCENT, 3)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        if self.attached_to == "target":
            p.drawRoundedRect(self.outline, 6, 6)
        font = QFont(self.font())
        font.setBold(True)
        p.setFont(font)
        fm = QFontMetrics(font)
        msg = self.label()
        width = min(fm.horizontalAdvance(msg) + 20, 360)
        box = fm.boundingRect(QRect(0, 0, width - 20, 400), Qt.TextWordWrap, msg)
        box = QRect(0, 0, width, box.height() + 14)
        o = self.outline
        if self.attached_to == "panel":
            box.moveTopLeft(QPoint(o.left() + 6, o.bottom() - box.height() - 6))
        elif o.right() + 12 + box.width() < self.width():
            box.moveTopLeft(QPoint(o.right() + 12, o.center().y() - box.height() // 2))
        elif o.left() - 12 - box.width() > 0:
            box.moveTopLeft(QPoint(o.left() - 12 - box.width(), o.center().y() - box.height() // 2))
        else:
            box.moveTopLeft(QPoint(max(4, o.left()), o.bottom() + 10))
        box.moveTop(max(4, min(box.top(), self.height() - box.height() - 4)))
        p.setPen(Qt.NoPen)
        p.setBrush(ACCENT)
        p.drawRoundedRect(box, 8, 8)
        p.setPen(QColor("white"))
        p.drawText(box.adjusted(10, 7, -10, -7), Qt.TextWordWrap, msg)
        p.end()
