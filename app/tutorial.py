"""The tutorial: a controller that walks tutorial_steps.STEPS, and its side panel.

The controller listens to the main window (stage_finished, study_opened, the
clicks on a step's target) and decides when a step is done. It moves the window
to each step's screen with goto() and asks for the pointer bubble with the
`point` signal; it changes nothing else in the app. Progress is saved beside
the tutorial study after every change, so leaving and coming back resumes.
"""

import json
import os

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QAbstractButton, QDockWidget, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QPushButton, QVBoxLayout,
                               QWidget)

import tutorial_bubble as TB
import tutorial_check as TC
import tutorial_pack as TP
import tutorial_steps as TS


def _norm(path):
    return os.path.normcase(os.path.abspath(path)) if path else ""


class TutorialPanel(QDockWidget):
    """The steps with their ticks, what the current one is about, and the buttons."""

    def __init__(self, parent=None):
        super().__init__("Tutorial", parent)
        self.setObjectName("tutorialPanel")
        self.setFeatures(QDockWidget.DockWidgetMovable)
        self.setAllowedAreas(Qt.RightDockWidgetArea | Qt.LeftDockWidgetArea)
        body = QWidget()
        body.setMinimumWidth(280)
        body.setMaximumWidth(380)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(12, 8, 12, 12)
        self.steps = QListWidget()
        self.steps.setObjectName("tutSteps")
        self.steps.setSelectionMode(QListWidget.NoSelection)
        self.steps.setFocusPolicy(Qt.NoFocus)
        lay.addWidget(self.steps, 2)
        self.title = QLabel("")
        f = self.title.font()
        f.setBold(True)
        f.setPointSizeF(f.pointSizeF() * 1.15)
        self.title.setFont(f)
        self.title.setWordWrap(True)
        lay.addWidget(self.title)
        self.text = QLabel("")
        self.text.setObjectName("tutText")
        self.text.setWordWrap(True)
        self.text.setTextFormat(Qt.RichText)
        self.text.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        lay.addWidget(self.text, 3)

        def button(name, label, tip):
            b = QPushButton(label)
            b.setObjectName(name)
            b.setToolTip(tip)
            return b

        self.ready = button("tutReady", "Use ready-made results",
                            "Copy in results made earlier from the same demo slide, "
                            "so you do not have to wait for this slow stage")
        lay.addWidget(self.ready)
        nav = QHBoxLayout()
        self.back = button("tutBack", "Back", "The step before")
        self.skip = button("tutSkip", "Skip", "Leave this step undone and go on")
        self.next = button("tutNext", "Next", "The next step")
        for b in (self.back, self.skip, self.next):
            nav.addWidget(b)
        lay.addLayout(nav)
        more = QHBoxLayout()
        self.start_over = button("tutStartOver", "Start over",
                                 "Throw this tutorial study away and begin again "
                                 "with a fresh copy")
        self.leave = button("tutLeave", "Leave tutorial",
                            "Go back to the study you had open. Your place is kept.")
        more.addWidget(self.start_over)
        more.addWidget(self.leave)
        lay.addLayout(more)
        # Filled by the controller after the curation check (tutorial Task 6).
        self.extra = QVBoxLayout()
        lay.insertLayout(lay.indexOf(self.ready), self.extra)
        self.setWidget(body)

    def show_steps(self, steps, current, done, skipped):
        self.steps.clear()
        for s in steps:
            mark = "▶" if s.sid == current else "✓" if s.sid in done else \
                "–" if s.sid in skipped else "  "
            it = QListWidgetItem(f"{mark}  {s.title}")
            if s.sid == current:
                f = it.font()
                f.setBold(True)
                it.setFont(f)
            self.steps.addItem(it)
        row = next((i for i, s in enumerate(steps) if s.sid == current), 0)
        self.steps.scrollToItem(self.steps.item(row))


class TutorialController(QObject):
    changed = Signal()
    finished = Signal()
    point = Signal(str, str)        # target, bubble text ("" target clears it)

    def __init__(self, window, pack, study_path):
        super().__init__(window)
        self.w = window
        self.pack = pack
        self.study_path = _norm(study_path)
        self.out_root = os.path.dirname(os.path.abspath(study_path))
        self.progress = TP.Progress.load(self.out_root)
        if not self.progress.current or self.progress.current not in \
                {s.sid for s in TS.STEPS}:
            self.progress.current = TS.STEPS[0].sid
        self.paused = False
        self.problem = ""
        self.panel = None
        self.bubble = None
        self._clicker = None

    # ---- the current step ----------------------------------------------------

    @property
    def current(self):
        return TS.by_sid(self.progress.current)

    def _index(self):
        return next(i for i, s in enumerate(TS.STEPS) if s.sid == self.progress.current)

    def _kind(self):
        return TS.parse_done(self.current.done)

    def is_done(self, step=None):
        step = step or self.current
        return step.sid in self.progress.done

    # ---- start / stop ------------------------------------------------------------

    def start(self):
        if self.panel is None:
            self.panel = TutorialPanel(self.w)
            self.w.addDockWidget(Qt.RightDockWidgetArea, self.panel)
            p = self.panel
            p.next.clicked.connect(self.next)
            p.back.clicked.connect(self.back)
            p.skip.clicked.connect(self.skip)
            p.ready.clicked.connect(self.use_ready)
            self.bubble = TB.PointerOverlay(self.w)
            self.point.connect(self._point)
        self.w.stage_finished.connect(self._stage_finished)
        self.w.study_opened.connect(self._study_opened)
        self.w.curator.exported.connect(self._exported)
        self.panel.show()
        self._enter()

    def stop(self):
        """Disconnect from the window and hide the panel; progress stays saved."""
        for sig, slot in ((self.w.stage_finished, self._stage_finished),
                          (self.w.study_opened, self._study_opened),
                          (self.w.curator.exported, self._exported)):
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._unhook_click()
        self.point.emit("", "")
        self._clear_check()
        if self.panel is not None:
            self.panel.hide()

    # ---- moving -------------------------------------------------------------------

    def _enter(self):
        self.problem = ""
        self._unhook_click()
        self._clear_check()
        step = self.current
        if step.screen in self.w.pages:
            self.w.goto(step.screen)
        kind, arg = self._kind()
        if kind in ("stage", "file") and self._already(kind, arg):
            self.progress.done.add(step.sid)
        if kind == "click":
            self._hook_click(step.target)
        self.progress.save()
        self._show()
        self.point.emit(step.target, step.bubble)

    def _already(self, kind, arg):
        if kind == "file":
            return os.path.exists(os.path.join(self.out_root, arg))
        try:
            import stages as S
            import workflow as W
            st = S.BY_ID.get(arg)
            return bool(st) and W.state(st, self.w.study(), self.w._running) == "done"
        except Exception:                                   # noqa: BLE001
            return False

    def _go(self, i):
        i = max(0, min(i, len(TS.STEPS) - 1))
        self.progress.current = TS.STEPS[i].sid
        self._enter()

    def next(self):
        if self.paused:
            return
        if self._kind()[0] == "next":
            self.progress.done.add(self.current.sid)
        if not self.is_done():
            return
        if self._index() == len(TS.STEPS) - 1:
            self.progress.save()
            self._show()
            self.finished.emit()
            return
        self._go(self._index() + 1)

    def back(self):
        if not self.paused and self._index() > 0:
            self._go(self._index() - 1)

    def skip(self):
        if self.paused:
            return
        self.progress.skipped.add(self.current.sid)
        if self._index() == len(TS.STEPS) - 1:
            self.progress.save()
            self.finished.emit()
            return
        self._go(self._index() + 1)

    def mark_done(self, sid):
        """A step's done condition happened: tick it, and move on if it is the current one."""
        if self.paused:
            return
        self.progress.done.add(sid)
        self.progress.skipped.discard(sid)
        if sid == self.progress.current:
            self.next()
        else:
            self.progress.save()
            self._show()

    def use_ready(self):
        step = self.current
        if self.paused or not step.ready:
            return
        TP.use_ready(self.pack, self.out_root, step.ready)
        try:
            self.w.refresh()
        except Exception:                                   # noqa: BLE001
            pass
        self.mark_done(step.sid)

    # ---- events from the window -----------------------------------------------------

    def _stage_finished(self, sid, ok):
        if self.paused:
            return
        kind, arg = self._kind()
        if kind == "stage" and arg == sid:
            if ok:
                self.mark_done(self.current.sid)
            else:
                self.problem = sid
                self._show()
            return
        if kind == "file" and ok and self._already("file", arg):
            self.mark_done(self.current.sid)

    def _study_opened(self, path):
        self.paused = _norm(path) != self.study_path
        if self.panel is not None:
            self.panel.setVisible(not self.paused)
        if self.paused:
            self._unhook_click()
            self.point.emit("", "")
        else:
            self._enter()

    def _point(self, target, text):
        inside = target.startswith("curator:") and not self.paused
        if inside:
            self.w.curator.point(target[len("curator:"):], text)
        else:
            self.w.curator.clear_point()
        if self.bubble is None:
            return
        if target and not inside and not self.paused:
            self.bubble.show_for(target, text, self.w, self.panel)
        else:
            self.bubble.hide_pointer()

    # ---- the practice curation --------------------------------------------------------

    def _exported(self, path):
        """The curator's export landed: on the practice step, compare with the answer key."""
        if self.paused or self._kind()[0] != "curation-checked":
            return
        if not os.path.basename(path).startswith("roi_regions"):
            return
        try:
            learner = json.loads(self.w.curator.store.read(TP.ANSWER_KEY) or "{}")
        except ValueError:
            learner = {}
        key = self.pack.answer_curation()
        self.results = TC.compare(learner, key, self.pack.practice_sections)
        # Done also takes the window on to Measure; the comparison is shown on the curator.
        self.w.goto("curate")
        off = [r for r in self.results if r.kind != "good"]
        first = off[0].section if off else (self.pack.practice_sections or [""])[0]
        polys = ((key.get(first) or {}).get("polys")) or []
        if first:
            self.w.curator.show_key(first, polys)
        self._show_check(off, first)

    def _show_check(self, off, section):
        self._clear_box()
        p = self.panel
        good = sum(1 for r in self.results if r.kind == "good")
        lines = [f"<p><b>{good} of {len(self.results)} regions match the expert's.</b>"]
        if off:
            lines.append(f" The expert's outlines are drawn dashed on section "
                         f"{section}.</p><ul>")
            lines += [f"<li>{TC.words(r)}</li>" for r in off[:12]]
            lines.append("</ul>")
        else:
            lines.append(" Well done.</p>")
        box = QLabel("".join(lines))
        box.setObjectName("tutCheck")
        box.setWordWrap(True)
        box.setTextFormat(Qt.RichText)
        row = QHBoxLayout()
        retry = QPushButton("Try again")
        retry.setObjectName("tutRetry")
        retry.setToolTip("Clear your outlines on the practice sections and draw them again")
        retry.clicked.connect(self.retry_practice)
        carry = QPushButton("Carry on")
        carry.setObjectName("tutCarryOn")
        carry.setToolTip("Keep your outlines and go on to Measure")
        carry.clicked.connect(self.carry_on)
        row.addWidget(retry)
        row.addWidget(carry)
        holder = QWidget()
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(box)
        hl.addLayout(row)
        p.extra.addWidget(holder)
        self._check_box = holder

    def _clear_box(self):
        box = getattr(self, "_check_box", None)
        if box is not None:
            box.setParent(None)
            box.deleteLater()
            self._check_box = None

    def _clear_check(self):
        self._clear_box()
        try:
            self.w.curator.clear_key()
        except Exception:                                   # noqa: BLE001
            pass

    def retry_practice(self):
        """Take the learner's outlines off the practice sections only, and reload the page."""
        store = self.w.curator.store
        try:
            state = json.loads(store.read(TP.ANSWER_KEY) or "{}")
        except ValueError:
            state = {}
        for sec in self.pack.practice_sections:
            if isinstance(state.get(sec), dict):
                state[sec].pop("polys", None)
        store.write(TP.ANSWER_KEY, json.dumps(state))
        self._clear_check()
        self.w.curator.curation_replaced([TP.ANSWER_KEY])
        self.w.goto("curate")

    def carry_on(self):
        self._clear_check()
        self.mark_done(self.current.sid)

    def _hook_click(self, target):
        b = self.w.findChild(QAbstractButton, target) if target else None
        if b is None:
            return
        sid = self.current.sid
        slot = lambda *_a: self.mark_done(sid)              # noqa: E731
        b.clicked.connect(slot)
        self._clicker = (b, slot)

    def _unhook_click(self):
        if self._clicker:
            b, slot = self._clicker
            try:
                b.clicked.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
            self._clicker = None

    # ---- the panel -------------------------------------------------------------------

    def _show(self):
        if self.panel is None:
            return
        p, step = self.panel, self.current
        p.show_steps(TS.STEPS, step.sid, self.progress.done, self.progress.skipped)
        n = self._index() + 1
        p.title.setText(f"{n} of {len(TS.STEPS)}  ·  {step.title}")
        text = step.text
        if self.problem:
            text += ("<p style='color:#b3261e'><b>This stage did not finish.</b> The "
                     "reason is in the activity log (bottom of the window).")
            text += (" You can carry on with ready-made results.</p>"
                     if step.ready and TP.has_ready(self.pack, step.ready) else "</p>")
        elif self.is_done() and self._kind()[0] != "next":
            text += "<p><b>Done.</b> Press Next.</p>"
        p.text.setText(text)
        p.back.setEnabled(self._index() > 0)
        p.next.setEnabled(self._kind()[0] == "next" or self.is_done())
        p.next.setText("Finish" if self._index() == len(TS.STEPS) - 1 else "Next")
        p.ready.setVisible(bool(step.slow))
        p.ready.setEnabled(bool(step.ready) and TP.has_ready(self.pack, step.ready)
                           and not self.is_done())
        self.changed.emit()
