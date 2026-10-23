"""The screens of the step bar: Project, Set up, Prepare, Curate, Measure, Results.

Each screen is handed what it shows (`workflow.Study`, the open project, which
stages are running) on every refresh and reports what was pressed through
signals. Running a stage, saving and switching screens are the window's job;
nothing here starts a thread or writes a file.
"""

import csv
import os

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QGridLayout, QMessageBox, QHBoxLayout, QLabel, QProgressBar, QPushButton, QScrollArea,
    QTabBar, QTabWidget, QVBoxLayout, QWidget)

import progress as PR
import stages as S
import theme as T
import workflow as W
from widgets import (COLOUR, QUIET, WORD, InfoButton, PartHeader, ScreenHeader, StageRow,
                     esc)

#: Space around a screen's content (docs/ui-handoff.md 3.3).
PAGE_MARGINS = (24, 18, 24, 10)


def _scroll(inner):
    area = QScrollArea()
    area.setWidget(inner)
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    return area


def stage_info(st, study, block=None, values=None):
    """The (i) card of a stage: plain words first, the technical detail after."""
    parts = [f"<p>{esc(W.plain(st, study))}</p>"]
    if st.operator:
        parts.append("<p><b>You decide here.</b> The page opens on the Curate screen; "
                     "its Export button hands your decisions on.</p>")
    if st.unblinds:
        parts.append(f"<p style='color:{T.DANGER}'><b>This stage unblinds the analysis.</b> "
                     "It is the first one allowed to read the treatment groups.</p>")
    ok, why = W.applies(st, study)
    if not ok:
        parts.append(f"<p style='{QUIET}'>{esc(why)}</p>")
    reason = st.cli_reason()
    if reason:
        parts.append(f"<p style='color:{T.PURPLE_TEXT}'><b>Not run from the app:</b> "
                     f"{esc(reason)}</p>")
    if st.needs:
        needs = ", ".join(esc(W.display_title(S.BY_ID[n], study)) for n in st.needs
                          if n in S.BY_ID)
        parts.append(f"<p><b>Needs first:</b> {needs}</p>")
    if st.outputs:
        shown = st.output_names(study.out_root, study.names)
        missing = set(st.missing(study.out_root, study.names))
        files = ", ".join((f"<span style='color:{T.WARN}'>{esc(o)}</span>" if o in missing
                           else esc(o)) for o in shown)
        parts.append(f"<p><b>Leaves behind:</b> {files}"
                     + (f" <span style='color:{T.WARN}'>(amber: not there yet)</span>"
                        if missing else "") + "</p>")
    rec = W.last_run(block, st.sid)
    if rec:
        when = (rec.get("ended") or "")[:16].replace("T", " ")
        parts.append(f"<p><b>Last run:</b> {'ok' if rec.get('ok') else 'failed'}, {when}"
                     + (f", {W.duration_words(rec)}" if W.duration_words(rec) else "")
                     + "</p>")
    tech = [esc(st.blurb)]
    if st.reader != "none":
        tech.append(f"Pixels read by {esc(st.reader)}.")
    if st.script:
        argv = " ".join(S.fill_markers(a, study.markers)
                        for a in W.build_argv(st, values or {}))
        tech.append(f"<code>{esc(st.script)} {esc(argv)}</code>")
    parts.append(f"<p style='{QUIET}'><b>Technical detail.</b> " + " ".join(tech) + "</p>")
    return "".join(parts)


def stage_status(st, study, running, held=None):
    """(state, words) for a row's status.

    `held` is run_plan's {stage id: what it waits for}. A stage still to run
    whose prerequisite is not met says what it is waiting on (section 8.1:
    not used, running, queued and done all come first).
    """
    state = W.state(st, study, running)
    if st.operator and state == "done" and not W.decided(st.sid, study):
        return "waiting", "Page built; waiting for your export"
    if st.operator and state == "done":
        return "done", "Decisions exported"
    if state == "todo" and held and st.sid in held:
        return "waiting", waiting_words(held[st.sid], study)
    part = W.incomplete(st, study) if state == "todo" else None
    if part:
        # Its output exists after the first section; Run picks up where it stopped.
        return state, "Detection %s, resume" % part
    if state == "outdated":
        words = "Out of date: %s" % W.outdated(st, study)
        if "--force" in W.run_values(st.sid, {}, study):
            # Said here because for 05c it is hours: nothing is kept from before.
            words += ". Run redoes all of it"
        elif st.sid == "detect":
            # 05c's own resume: the rest of its sections are kept.
            words += ". Run re-measures the sections whose boxes changed"
        return state, words
    return state, WORD.get(state, state)


def waiting_words(need, study):
    """What a held stage says it is waiting on: your export, or a stage to finish."""
    n = S.BY_ID.get(need)
    if n is not None and n.operator:
        if need == "roi_curator":
            return "Waiting for your ROI export"
        return f"Waiting for your export from {W.display_title(n, study)}"
    if need == "detect":
        return "Needs detection to finish first"
    return (f"Needs {W.display_title(n, study)} to finish first" if n is not None
            else "Waiting for an earlier stage")


def row_action(st, state, held):
    """(label or None for the default, screen to go to or None) for a row's main button."""
    if state == "waiting" and held and st.sid in held:
        need = S.BY_ID.get(held[st.sid])
        if need is not None and need.operator:
            return "Open curator", "curate"
    if st.unblinds and state in ("todo", "failed"):
        # The dots say a question comes before anything runs.
        return "Run…", None
    return None, None


def technical_title(st, study):
    """'00d  CZI self-test': what the log, the guide and All stages call it."""
    code, _ = W.split_title(st.title)
    name = W.display_title(st, study, technical=True)
    return f"{code}  {name}" if code else name


def passes_html(study):
    """The ROI curator's passes, one line each with its own count, as HTML, or "".

    Under `paired` the second marker is a pass of its own (W.second_pass): its
    sections are curated in the same page, by choosing it in the Channel box.
    """
    lines = []
    for m, (n, total) in W.curation_passes(study):
        colour = COLOUR["done"] if n >= total else COLOUR["waiting"]
        state = "Done" if n >= total else f"{n:,} of {total:,} sections curated"
        how = ("" if m == 0 else
               f" <span style='{QUIET}'>Choose {esc(study.marker_label(m))} in the "
               "curator's Channel.</span>")
        lines.append(f"<b>{esc(study.marker_label(m))} curation</b> "
                     f"<span style='color:{colour}'>● {state}</span>{how}")
    return "<br>".join(lines)


def coverage_html(cov, label=""):
    """A compact table of curated sections per animal (rows) and level (columns).

    Cells under the target are amber, so the gaps a matched-level comparison
    cannot afford are what the eye lands on.
    """
    if not cov or not cov["levels"]:
        return ""
    t = cov["target"]
    under = set(cov["under"])
    cell = "padding:1px 6px;text-align:center"
    head = "".join(f"<th style='{cell}'>{esc(lv)}</th>" for lv in cov["levels"])
    body = []
    for a in cov["animals"]:
        tds = []
        for lv in cov["levels"]:
            n = cov["counts"].get((a, lv), 0)
            style = (f"{cell};background:{T.WARN_BG};color:{T.WARN}" if (a, lv) in under
                     else cell)
            tds.append(f"<td style='{style}'>{n}</td>")
        body.append(f"<tr><th style='{cell};text-align:left'>{esc(a or '?')}</th>"
                    + "".join(tds) + "</tr>")
    gaps = len(under)
    # What is counted is cells of the table, one animal at one level, so the
    # plural belongs on "cell": "6 animal and levels" read as broken English.
    say = (f"{gaps} animal/level cell{'s' if gaps != 1 else ''} under the target of {t}"
           if gaps else f"every animal at or over the target of {t} at every level")
    return (f"<b>{esc(label)} coverage</b> <span style='{QUIET}'>curated sections per "
            f"animal and atlas level; {say} (Set up &gt; Curation)</span>"
            f"<table cellspacing='0' style='margin-top:2px'><tr><th></th>{head}</tr>"
            + "".join(body) + "</table>")


def marks(st):
    if st.unblinds:
        return "⚠ unblinds"
    if st.operator:
        return "✎ you decide"
    return ""


class StageListScreen(QWidget):
    """A screen that is a list of stages under sub-headings, with Run all remaining."""

    run_requested = Signal(list)            # stage ids, in order
    open_requested = Signal(str)            # a curator stage id
    options_changed = Signal(str, dict)     # stage id, {flag: value}
    goto = Signal(str)                      # a screen id

    def __init__(self, screen, parent=None):
        super().__init__(parent)
        self.screen = screen
        self.rows = {}
        # The whole screen scrolls, header included: on a laptop at 614 px a
        # fixed header plus a separate list squeezes both until words overlap.
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(*PAGE_MARGINS)
        lay.setSpacing(8)
        lay.addWidget(ScreenHeader(screen))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(_scroll(page))

        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.run_all = QPushButton("Run all remaining")
        self.run_all.setObjectName(f"runAll_{screen.sid}")
        self.run_all.setMinimumHeight(34)
        T.mark(self.run_all, kind="primary")
        self.run_all.clicked.connect(self._run_all)
        bar.addWidget(self.run_all)
        bar.addWidget(InfoButton(
            "Run all remaining",
            "<p>Runs, in order, every stage on this screen whose results are not there "
            "yet. Stages this study does not use, stages that run outside the app, "
            "repairs and the unblinding stage are left out.</p>"
            "<p>It stops at the first failure, and it never runs past a place where you "
            "decide: a stage that needs a curator you have not exported yet waits, along "
            "with everything after it.</p>"
            "<p>The project is saved automatically before the first stage starts.</p>"))
        self.plan_note = QLabel("")
        self.plan_note.setWordWrap(True)
        self.plan_note.setStyleSheet(QUIET)
        bar.addWidget(self.plan_note, 1)
        lay.addLayout(bar)

        # The amber notice: older results that need measuring again. Dismiss
        # hides it for this session only; it comes back if the app restarts and
        # the results are still old, because the reminder is the point.
        self.notice_box = QFrame()
        self.notice_box.setObjectName("notice")
        nl = QHBoxLayout(self.notice_box)
        nl.setContentsMargins(14, 10, 10, 10)
        ncol = QVBoxLayout()
        ncol.setSpacing(2)
        self.notice_head = QLabel("These results are older than the ROI fixes.")
        f = self.notice_head.font()
        f.setBold(True)
        self.notice_head.setFont(f)
        ncol.addWidget(self.notice_head)
        self.notice = QLabel("")
        self.notice.setWordWrap(True)
        ncol.addWidget(self.notice)
        nl.addLayout(ncol, 1)
        self.dismiss_btn = QPushButton("Dismiss")
        T.mark(self.dismiss_btn, kind="small")
        self.dismiss_btn.clicked.connect(self._dismiss)
        nl.addWidget(self.dismiss_btn, 0, Qt.AlignTop)
        self.notice_box.setVisible(False)
        self._dismissed = None
        lay.addWidget(self.notice_box)
        self._plan = []
        # All stages spans the whole pipeline, unblinding included; running all of
        # it from one button is not something to offer.
        self._offers_run_all = screen.sid != "all"
        for i in range(bar.count()):
            item = bar.itemAt(i).widget()
            if item is not None and item is not self.plan_note:
                item.setVisible(self._offers_run_all)

        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 12)
        body.setSpacing(0)
        for heading, text, ids in screen.visible_parts():
            if heading:
                body.addWidget(PartHeader(heading, text))
            # Each part's rows sit together on one card, divided by soft lines.
            card = QFrame()
            card.setObjectName("card")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(1, 1, 1, 1)
            cl.setSpacing(0)
            for sid in ids:
                st = S.BY_ID[sid]
                folder = W.FOLDERS.get(sid) or W.REPORTS.get(sid)
                row = StageRow(st, W.options_for(sid),
                               open_label=folder[0] if folder else None)
                row.run_clicked.connect(lambda s: self.run_requested.emit([s]))
                row.open_clicked.connect(self._open)
                row.goto_clicked.connect(self.goto.emit)
                row.options_changed.connect(self.options_changed.emit)
                self.rows[sid] = row
                cl.addWidget(row)
            row.set_last(True)
            body.addSpacing(6)
            body.addWidget(card)
        lay.addLayout(body)
        lay.addStretch(1)
        self._study = None

    def _open(self, sid):
        """A report opens here, in the system's viewer; anything else is the window's."""
        if sid in W.REPORTS:
            path = W.report_file(sid, self._study) if self._study is not None else None
            if path:
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            return
        self.open_requested.emit(sid)

    def _run_all(self):
        if self._plan:
            self.run_requested.emit(list(self._plan))

    def _dismiss(self):
        self._dismissed = self.notice.text()
        self.notice_box.setVisible(False)

    def refresh(self, study, running, project, busy=False):
        block = project.block if project is not None else {}
        self._study = study
        self._plan, held = W.run_plan(self.screen.sid, study)
        for sid, row in self.rows.items():
            st = S.BY_ID[sid]
            values = project.stage_options(sid) if project is not None else {}
            state, words = stage_status(st, study, running, held)
            ok, _ = W.applies(st, study)
            code, _name = W.split_title(st.title)
            if sid in W.HIDDEN:
                words = "Superseded"
            elif sid == "tune_detection" and state == "done":
                # The verdict is the point of the signal check: say it on the row.
                verdict = W.signal_words(study)
                if verdict:
                    words = f"{words}: {verdict}"
            action, goto = row_action(st, state, held)
            can_run = bool(st.script) and ok and not st.cli_reason() and not busy
            if state == "waiting" and goto is None:
                can_run = False             # its prerequisite has not finished
            # The step screens are the basic view: plain names, with the script
            # number and the pipeline's own name kept for All stages and
            # Instrument (under Advanced) and for the name's tooltip.
            advanced = self.screen in W.EXTRA
            row.refresh(title=W.display_title(st, study, technical=advanced),
                        code=code if advanced else "",
                        tip="" if advanced else technical_title(st, study),
                        plain=W.plain(st, study), status=words, state=state,
                        info_html=stage_info(st, study, block, values), mark=marks(st),
                        can_run=can_run, option_values=values, changed_count=len(values),
                        action=action, goto=goto, took=PR.last_run_words(block, sid),
                        # A folder to open once the stage has made something.
                        open_visible=((state == "done") if sid in W.FOLDERS else
                                      bool(W.report_file(sid, study)) if sid in W.REPORTS
                                      else None))
        self.run_all.setEnabled(bool(self._plan) and not busy)
        now = next((sid for sid, what in (running or {}).items()
                    if what == "running" and sid in self.rows), None)
        if now:
            self.plan_note.setText(f"Running: {W.display_title(S.BY_ID[now], study)}")
        elif self._plan or held:
            n = len(self._plan)
            text = f"{n} stage{'s' if n != 1 else ''} left to run"
            # Held behind your export, or behind a stage that has not run yet.
            you = sum(1 for w in held.values() if getattr(S.BY_ID.get(w), "operator", False))
            if you:
                text += f"; {you} wait{'s' if you == 1 else ''} for you"
            if len(held) - you:
                text += f"; {len(held) - you} wait{'s' if len(held) - you == 1 else ''} " \
                        "for earlier stages"
            self.plan_note.setText(text + ".")
        else:
            self.plan_note.setText("Nothing left to run here.")
        shown = W.notices(self.screen.sid, study)
        text = "\n\n".join(shown)
        self.notice.setText(text)
        if not shown:
            self._dismissed = None
        self.notice_box.setVisible(bool(shown) and text != self._dismissed)


class CurateScreen(QWidget):
    """The decision points as tabs, with the curator page under them."""

    run_requested = Signal(list)
    options_changed = Signal(str, dict)
    goto = Signal(str)

    def __init__(self, curator_view, parent=None):
        super().__init__(parent)
        self.curator = curator_view
        self.sids = []
        self.current = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*PAGE_MARGINS)
        lay.addWidget(ScreenHeader(W.CURATE))

        self.tabs = QTabBar()
        self.tabs.setExpanding(False)
        self.tabs.currentChanged.connect(self._tab_changed)
        lay.addWidget(self.tabs)

        self.row_holder = QVBoxLayout()
        lay.addLayout(self.row_holder)
        self.rows = {}
        for sid in ("atlas_reframe", "roi_curator"):
            row = StageRow(S.BY_ID[sid], W.options_for(sid))
            row.open_btn.setVisible(False)
            row.run_clicked.connect(lambda s: self.run_requested.emit([s]))
            row.goto_clicked.connect(self.goto.emit)
            row.options_changed.connect(self.options_changed.emit)
            row.set_last(True)
            row.setVisible(False)
            self.rows[sid] = row
            self.row_holder.addWidget(row)

        # The ROI curator's passes (the second marker's under `paired`) and how
        # far each animal's curation reaches across the brain levels.
        self.passes = QLabel("")
        self.passes.setWordWrap(True)
        self.passes.setVisible(False)
        lay.addWidget(self.passes)
        self.coverage = QLabel("")
        self.coverage.setTextFormat(Qt.RichText)
        self.coverage_area = _scroll(self.coverage)
        self.coverage_area.setMaximumHeight(150)
        self.coverage_area.setVisible(False)
        lay.addWidget(self.coverage_area)

        actions = QHBoxLayout()
        self.decision = QLabel("")
        self.decision.setWordWrap(True)
        actions.addWidget(self.decision, 1)
        self.help = InfoButton("How to use this curator", "")
        actions.addWidget(self.help)
        self.continue_btn = QPushButton("Continue Prepare")
        self.continue_btn.setToolTip("Run what is left of Prepare now that your decisions "
                                     "are exported")
        self.continue_btn.clicked.connect(lambda: self.goto.emit("prepare:run"))
        actions.addWidget(self.continue_btn)
        self.import_btn = QPushButton("Bring in browser exports")
        self.import_btn.setToolTip(W.plain(S.BY_ID["import_curation"]))
        self.import_btn.clicked.connect(lambda: self.run_requested.emit(["import_curation"]))
        actions.addWidget(self.import_btn)
        lay.addLayout(actions)
        lay.addWidget(self.curator, 1)
        foot = QLabel("Your work in a curator is saved into the project as you go, so "
                      "closing the app loses nothing. Export is what hands the decisions "
                      "to the stages that come after.")
        foot.setWordWrap(True)
        foot.setStyleSheet(T.HINT)
        lay.addWidget(foot)
        self._study = None

        # Done: the one way on from the ROI curator. It presses the page's own
        # Export, so the decisions the next phase reads are the ones on screen
        # now, then goes to Measure once the regions file has landed. The button
        # lives in the curator's own bar, which keeps it beside the page when the
        # page has its own window.
        self.curator.done_requested.connect(self._finish)
        self.curator.exported.connect(self._landed)
        self._waiting = False
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.setInterval(20000)
        self._timeout.timeout.connect(self._export_timed_out)

    # ---- finishing ----------------------------------------------------------

    def _finish(self):
        if self.current != "roi_curator" or self._waiting:
            return
        self._waiting = True
        self.curator.done_btn.setEnabled(False)
        self.decision.setText("Exporting your decisions...")
        self.curator.run_export(self._export_started)

    def _export_started(self, ok):
        if not ok:
            self._stop_waiting(
                f"<span style='color:{COLOUR['waiting']}'>Could not export:</span> the "
                "curator page is not loaded. Build it on Prepare, then try again.")
            return
        self._timeout.start()

    def _landed(self, path):
        # exportCsv writes three files; the regions file is the one Measure reads.
        if not self._waiting or not os.path.basename(path).startswith("roi_regions"):
            return
        self._stop_waiting(None)
        self.curator.put_back()
        self.goto.emit("measure")

    def _export_timed_out(self):
        self._stop_waiting(
            f"<span style='color:{COLOUR['waiting']}'>The export did not arrive.</span> "
            "Press Export in the page, then Done again.")

    def _stop_waiting(self, words):
        self._waiting = False
        self._timeout.stop()
        self.curator.done_btn.setEnabled(True)
        self._update_actions()
        if words:
            self.decision.setText(words)

    def _tab_changed(self, i):
        if 0 <= i < len(self.sids):
            self.show_curator(self.sids[i], select=False)

    def show_curator(self, sid, select=True):
        """Bring one curator's page up. A hidden one (04k) loads without a tab."""
        self.current = sid
        if select and sid in self.sids:
            self.tabs.blockSignals(True)
            self.tabs.setCurrentIndex(self.sids.index(sid))
            self.tabs.blockSignals(False)
        st = S.BY_ID[sid]
        for k, row in self.rows.items():
            row.setVisible(k == sid)
        self.curator.show_page(st.curator, W.display_title(st, self._study))
        self._update_actions()

    def _update_actions(self):
        sid, study = self.current, self._study
        if not sid or study is None:
            return
        self.help.set_body(W.CURATOR_HELP.get(sid, W.plain(S.BY_ID[sid], study)))
        self.help.title = W.display_title(S.BY_ID[sid], study)
        latest = W.decision_file(sid, study)
        if sid in W.DECISION_FILES:
            if latest:
                where = (os.path.relpath(latest, study.out_root)
                         if latest.startswith(study.out_root) else latest)
                then = " Measure can now run." if sid == "roi_curator" else ""
                self.decision.setText(
                    f"<span style='color:{COLOUR['done']}'>● Exported and handed on.</span>"
                    f"{then} <span style='{QUIET}'>{esc(where)}</span>")
            else:
                self.decision.setText(
                    f"<span style='color:{COLOUR['waiting']}'>● Waiting for your "
                    "export.</span> Decide in the page, then press Export.")
        else:
            self.decision.setText(W.plain(S.BY_ID[sid], study))
        roi = sid == "roi_curator"
        passes = passes_html(study) if roi else ""
        self.passes.setText(passes)
        self.passes.setVisible(bool(passes))
        tables = [coverage_html(W.coverage(study, m), study.marker_label(m))
                  for m, _c in (W.curation_passes(study) if roi else [])]
        tables = [t for t in tables if t]
        self.coverage.setText("<br>".join(tables))
        self.coverage_area.setVisible(bool(tables))
        self.continue_btn.setVisible(sid == "atlas_reframe")
        self.continue_btn.setEnabled(bool(latest))
        self.import_btn.setVisible(sid == "roi_curator")
        T.mark(self.continue_btn, kind="primary" if latest else "")
        if sid == "roi_curator":
            self.curator.set_done(
                "Done: export and go to Measure",
                "Exports your regions, plates and exclusions, then opens Measure to "
                "place them on the slides and count nuclei")
        else:
            self.curator.set_done("")

    def refresh(self, study, running, project, busy=False):
        self._study = study
        wanted = W.curators(study)
        if wanted != self.sids:
            self.tabs.blockSignals(True)
            while self.tabs.count():
                self.tabs.removeTab(0)
            for sid in wanted:
                self.tabs.addTab(W.display_title(S.BY_ID[sid], study))
            self.sids = wanted
            self.tabs.blockSignals(False)
        block = project.block if project is not None else {}
        for i, sid in enumerate(self.sids):
            mark = "✓ " if W.curator_finished(sid, study) else ""
            self.tabs.setTabText(i, mark + W.display_title(S.BY_ID[sid], study))
        for sid, row in self.rows.items():
            st = S.BY_ID[sid]
            values = project.stage_options(sid) if project is not None else {}
            state, words = stage_status(st, study, running)
            row.refresh(title=W.display_title(st, study), code="",
                        tip=technical_title(st, study), plain=W.plain(st, study),
                        status=words, state=state, info_html=stage_info(st, study, block, values),
                        mark="", can_run=not busy, option_values=values,
                        changed_count=len(values))
        self._update_actions()

    def ensure_page(self):
        """Load a curator the first time the screen is shown, not at start-up.

        The ROI curator is a large page; loading it before anyone asks for it
        slowed every launch.
        """
        if self.current is None and self.sids and self._study is not None:
            first = next((s for s in self.sids if not W.decided(s, self._study)), self.sids[0])
            self.show_curator(first)


class HomeScreen(QWidget):
    """Where the study stands, and the one thing to do next."""

    goto = Signal(str)
    action = Signal(str)        # "switch", "open", "new", "copy", "history"

    STEP_IDS = ["setup", "prepare", "curate", "measure", "results"]

    def __init__(self, parent=None):
        super().__init__(parent)
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(*PAGE_MARGINS)
        top = QHBoxLayout()
        top.addWidget(ScreenHeader(W.HOME), 1)
        mark = QLabel()
        mark.setObjectName("appLogo")
        mark.setPixmap(T.logo(88))
        mark.setAccessibleName("The Great IHC Leap Forward logo")
        top.addWidget(mark, 0, Qt.AlignTop | Qt.AlignRight)
        lay.addLayout(top)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(_scroll(page))

        self.project_line = QLabel("")
        self.project_line.setWordWrap(True)
        self.project_line.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.project_line)

        card = QFrame()
        card.setObjectName("nextUp")
        cl = QHBoxLayout(card)
        cl.setContentsMargins(16, 12, 16, 12)
        cl.setSpacing(10)
        col = QVBoxLayout()
        col.setSpacing(4)
        head = QLabel("Next up")
        head.setObjectName("nextUpLabel")
        col.addWidget(head)
        self.next_text = QLabel("")
        self.next_text.setWordWrap(True)
        self.next_text.setFont(T.font(16))
        col.addWidget(self.next_text)
        cl.addLayout(col, 1)
        cl.addWidget(InfoButton("Next up", "<p>The single next thing to do, worked out "
                                "from what is already in the output folder: unfinished "
                                "Set up first, then Prepare, then any curator waiting "
                                "for your export, then Measure and Results.</p>"))
        self.next_btn = QPushButton("Go")
        self.next_btn.setMinimumWidth(90)
        self.next_btn.setMinimumHeight(36)
        T.mark(self.next_btn, kind="primary")
        self.next_btn.clicked.connect(lambda: self.goto.emit(self._next_target))
        cl.addWidget(self.next_btn)
        lay.addWidget(card)
        self._next_target = "home"

        grid = QGridLayout()
        grid.setColumnStretch(2, 1)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        self.bars, self.counts = {}, {}
        for r, sid in enumerate(self.STEP_IDS):
            scr = W.SCREENS[sid]
            name = QLabel(f"<b>{r + 1}. {scr.title}</b>")
            grid.addWidget(name, r, 0)
            grid.addWidget(InfoButton(scr.title, scr.info), r, 1)
            bar = QProgressBar()
            bar.setTextVisible(False)
            bar.setMaximumHeight(10)
            grid.addWidget(bar, r, 2)
            count = QLabel("")
            count.setStyleSheet(QUIET)
            count.setMinimumWidth(120)
            grid.addWidget(count, r, 3)
            btn = QPushButton("Open")
            T.mark(btn, kind="small")
            btn.clicked.connect(lambda _=False, s=sid: self.goto.emit(s))
            grid.addWidget(btn, r, 4)
            self.bars[sid], self.counts[sid] = bar, count
        lay.addSpacing(10)
        lay.addLayout(grid)

        lay.addWidget(PartHeader("Recent activity", "The last stages run in this project."))
        self.recent = QLabel("")
        self.recent.setWordWrap(True)
        self.recent.setTextFormat(Qt.RichText)
        lay.addWidget(self.recent)

        buttons = QHBoxLayout()
        self.tutorial_btn = QPushButton("Start the tutorial")
        self.tutorial_btn.setObjectName("tutorialStart")
        self.tutorial_btn.setToolTip("Learn the app on a small demo study, step by step. "
                                     "Your own studies are not touched.")
        T.mark(self.tutorial_btn, kind="primary")
        self.tutorial_btn.clicked.connect(lambda: self.action.emit("tutorial"))
        buttons.addWidget(self.tutorial_btn)
        for key, label in (("switch", "Switch study…"), ("open", "Open project…"),
                           ("new", "New project…"), ("copy", "Export a copy…"),
                           ("history", "Project history…")):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, k=key: self.action.emit(k))
            buttons.addWidget(b)
        buttons.addStretch(1)
        lay.addSpacing(8)
        lay.addLayout(buttons)
        lay.addStretch(1)

    @staticmethod
    def _rows(path):
        try:
            with open(path, newline="", encoding="utf-8") as fh:
                return max(0, sum(1 for _ in csv.reader(fh)) - 1)
        except OSError:
            return None

    def refresh(self, study, project, setup_done, setup_total, busy=False,
                setup_missing=()):
        name = project.name if project is not None else "?"
        files = self._rows(os.path.join(study.out_root, "manifest", "manifest_files.csv"))
        scenes = self._rows(os.path.join(study.out_root, "manifest", "manifest_scenes.csv"))
        size = (f" · {files:,} slide files · {scenes:,} sections"
                if files is not None and scenes is not None else " · slides not read yet")
        markers = (", ".join(study.marker_label(i) for i in range(len(study.markers)))
                   or "none declared (Set up > Microscope and channels)")
        self.project_line.setText(
            f"<b>{esc(name)}</b>{size} · markers: {esc(markers)} · atlas: "
            f"{esc(study.atlas_source)}<br><span style='{QUIET}'>project file "
            f"{esc(project.path if project is not None else '')}<br>outputs "
            f"{esc(study.out_root)}</span>")

        target, words, _sid = W.next_up(study, setup_ok=setup_done >= setup_total,
                                        setup_missing=setup_missing)
        self._next_target = target + (":run" if target in ("prepare", "measure") else "")
        self.next_text.setText(words)
        self.next_btn.setText("Go")
        self.next_btn.setToolTip("Runs what is left there" if target in ("prepare", "measure")
                                 else "Opens that step")
        self.next_btn.setEnabled(target != "home" and not busy)

        for sid in self.STEP_IDS:
            if sid == "setup":
                done, total = setup_done, setup_total
            else:
                done, total = W.step_progress(sid, study)
            bar = self.bars[sid]
            bar.setMaximum(max(total, 1))
            bar.setValue(done)
            T.mark(bar, complete=bool(total) and done >= total)
            self.counts[sid].setText(W.progress_words(sid, done, total, study))

        runs = list(reversed((project.block.get("runs") if project is not None else None) or []))[:6]
        if not runs:
            self.recent.setText(f"<span style='{QUIET}'>Nothing has been run in this project "
                                "yet.</span>")
        else:
            lines = []
            for rec in runs:
                st = S.BY_ID.get(rec.get("stage"))
                title = W.display_title(st, study) if st else rec.get("stage")
                ok = rec.get("ok")
                colour = COLOUR["done"] if ok else COLOUR["failed"]
                when = (rec.get("ended") or "")[:16].replace("T", " ")
                took = W.duration_words(rec)
                lines.append(f"<span style='color:{colour}'>● {'ok' if ok else 'failed'}"
                             f"</span>&nbsp; {esc(title)} <span style='{QUIET}'>· {esc(when)}"
                             f"{' · ' + took if took else ''}</span>")
            self.recent.setText("<br>".join(lines))


def _same_folder(a, b):
    """True when two folder paths name the same folder (both must be given)."""
    if not a or not b:
        return False
    norm = lambda p: os.path.normcase(os.path.realpath(os.path.abspath(p)))   # noqa: E731
    return norm(a) == norm(b)


class SetupScreen(QWidget):
    """Slides, then the study's settings as cards, each with Apply."""

    applied = Signal()
    logged = Signal(str)
    options_changed = Signal(str, dict)     # stage id, {flag: value}: the workbook

    def __init__(self, repo_root, importer, session, parent=None):
        super().__init__(parent)
        self.repo_root = repo_root
        self.session = session
        self.importer = importer
        self.forms = {}
        # Slide headers are read on a worker thread (slide_read.py): a slides
        # folder on a USB stick or OneDrive can take minutes, and on this
        # thread that greys the window out. What to do when the read lands is
        # kept here: fill the settings (Read settings), show the report, or both.
        import slide_read as SRD
        self.slide_read = SRD.SlideRead(self)
        self.slide_read.progress.connect(
            lambda k, n: self.detect_line.setText(SRD.words(k, n)))
        self.slide_read.finished.connect(self._slides_read)
        self.slide_read.failed.connect(
            lambda _src, why: self._read_failed("Could not read the slides: " + why))
        self._when_read = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*PAGE_MARGINS)
        lay.addWidget(ScreenHeader(W.SETUP))
        adv = QHBoxLayout()
        self.advanced_line = QLabel(
            "Only what the slide files cannot tell the program is asked here. Pixel size, "
            "file names and channels are read from the slides; folders sit inside the "
            "output folder.")
        self.advanced_line.setWordWrap(True)
        self.advanced_line.setStyleSheet(QUIET)
        adv.addWidget(self.advanced_line, 1)
        self.advanced = QCheckBox("Show advanced settings")
        self.advanced.setChecked(self._advanced_on())
        self.advanced.toggled.connect(self._toggle_advanced)
        adv.addWidget(self.advanced)
        adv.addWidget(InfoButton(
            "Advanced settings",
            "<p>Settings with one right answer that the slide files give (pixel size, "
            "how the files are named, which channel is the counterstain, how the markers "
            "were scanned) are read from the files by <b>Read settings from the slides</b> "
            "on the Slides tab, and the folders for exports and pictures default to "
            "folders inside the output folder.</p><p>They are all still here: tick "
            "<b>Show advanced settings</b> to see and change them. A required setting that "
            "is still empty is always shown.</p>"))
        lay.addLayout(adv)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("setupTabs")
        lay.addWidget(self.tabs, 1)
        self._slides_tab = QWidget()
        sl = QVBoxLayout(self._slides_tab)
        top = QHBoxLayout()
        line = QLabel("Choose the folder your slide files are in, or pick individual "
                      "files. The table shows what each file name was read as; a red row "
                      "is a file every stage would ignore.")
        line.setWordWrap(True)
        line.setStyleSheet(QUIET)
        top.addWidget(line, 1)
        top.addWidget(InfoButton(
            "Slides",
            "<p>Choosing a folder only records where the slides are; nothing is moved. "
            "Picking separate files links them into one folder, which costs no space on "
            "the same drive. Copying is never done without showing the size first.</p>"
            "<p>A file name has to fit the pattern under <b>Slide file names</b>, or the "
            "file is invisible to every stage.</p>"))
        sl.addLayout(top)
        read = QHBoxLayout()
        self.detect_btn = QPushButton("Read settings from the slides")
        self.detect_btn.setObjectName("readSettings")
        self.detect_btn.setToolTip("Read pixel size, file names and channels from the slide "
                                   "files' headers and fill in the settings not yet set")
        self.detect_btn.clicked.connect(self.detect_from_slides)
        read.addWidget(self.detect_btn)
        self.report_btn = QPushButton("What the slides say…")
        self.report_btn.setToolTip("Everything the slide headers say, and what the app did with it")
        self.report_btn.clicked.connect(self.show_slide_report)
        read.addWidget(self.report_btn)
        self.detect_line = QLabel("")
        self.detect_line.setWordWrap(True)
        self.detect_line.setStyleSheet(QUIET)
        read.addWidget(self.detect_line, 1)
        sl.addLayout(read)
        # The sampling workbook is 06b's --xlsx option, kept here too so it is
        # chosen, and checked, when the study is set up rather than first read
        # at Unblind. The check is 06b's own join (ls_sampling), writing nothing.
        import workbook_check as WC
        from widgets import FileField
        book = QHBoxLayout()
        book.addWidget(QLabel("Sampling workbook"))
        self.workbook_field = FileField("", button="Choose…",
                                        filter="Excel workbook (*.xlsx);;All files (*)")
        self.workbook_field.edit.setPlaceholderText("(the workbook in the slide folder)")
        self.workbook_field.valueChanged.connect(self._workbook_chosen)
        book.addWidget(self.workbook_field, 1)
        self.match_sheet_btn = QPushButton("Match the sheet…")
        self.match_sheet_btn.setToolTip(
            "Say which column is the animal and which hold the treatment, checked against "
            "the animals in the slide file names as you choose")
        self.match_sheet_btn.clicked.connect(self.match_sheet)
        book.addWidget(self.match_sheet_btn)
        self.check_workbook_btn = QPushButton("Check workbook")
        self.check_workbook_btn.setToolTip(
            "Match the study's animals to the workbook the way Unblind will, without "
            "writing anything: how many get a group, which have no row, which are blank")
        self.check_workbook_btn.clicked.connect(self.check_workbook)
        book.addWidget(self.check_workbook_btn)
        sl.addLayout(book)
        self.workbook_line = QLabel("")
        self.workbook_line.setWordWrap(True)
        self.workbook_line.setStyleSheet(QUIET)
        sl.addWidget(self.workbook_line)
        # The groups from that workbook, for the Shotgun deck, which lays the
        # favourites out by treatment while the study is still being curated.
        # Loading them writes groups.order / groups.by_animal and nothing else:
        # curation, counting and every screen stay keyed by animal, and the
        # per-fish groups are never shown here, only how many fish each has.
        groups = QHBoxLayout()
        self.groups_btn = QPushButton("Load the groups for Shotgun")
        self.groups_btn.setToolTip(
            "Check the workbook, then keep each fish's treatment group in the study so the "
            "ROI curator's Shotgun deck can lay slides out by group")
        self.groups_btn.clicked.connect(self.load_groups)
        groups.addWidget(self.groups_btn)
        self.clear_groups_btn = QPushButton("Remove the groups")
        self.clear_groups_btn.setToolTip("Take the treatment groups out of the study again; "
                                         "the Shotgun deck turns off")
        self.clear_groups_btn.clicked.connect(self.clear_groups)
        groups.addWidget(self.clear_groups_btn)
        groups.addWidget(InfoButton(
            "Treatment groups before Unblind",
            "<p>The Shotgun deck in the ROI curator puts each treatment on its own half "
            "of the slide, so it needs the groups while you are still curating. Loading "
            "them here reads the same workbook, the same way, as Unblind does.</p>"
            "<p><b>What sees the groups:</b> the Shotgun deck's layout, and the order "
            "05c measures sections in (so a part-finished run is balanced; the counts "
            "are the same either way). <b>What stays blind:</b> the curator's sections, "
            "plates and ROIs, every count and threshold, and this screen, which shows "
            "only how many fish each group has. Results are joined to the groups at "
            "Unblind (06b), as before.</p>"
            "<p>After loading, press <b>Build page</b> on the ROI curator so its "
            "Shotgun buttons turn on. <b>Remove the groups</b> makes the study blind "
            "again. A copy shared as blinded never carries them.</p>"))
        groups.addStretch(1)
        sl.addLayout(groups)
        self.groups_line = QLabel("")
        self.groups_line.setWordWrap(True)
        self.groups_line.setStyleSheet(QUIET)
        sl.addWidget(self.groups_line)
        self._load_groups_after_check = False
        self.workbook_check = WC.WorkbookCheck(self)
        self.workbook_check.finished.connect(self._workbook_checked)
        self._sync_report_btn()
        self.current = QLabel("")
        self.current.setWordWrap(True)
        self.current.setTextFormat(Qt.RichText)
        sl.addWidget(self.current)
        sl.addWidget(importer, 1)
        self.tabs.addTab(self._slides_tab, "Slides")
        self.build()

    def build(self):
        """(Re)make the settings cards from the open project."""
        import ls_config as LC
        from config_dialog import SettingsForm
        self._sync_report_btn()
        project = self.session.project
        self.workbook_field.setValue(project.stage_options("join_sampling").get("--xlsx", ""))
        self.workbook_field.setEnabled(not project.read_only)
        self.match_sheet_btn.setEnabled(not project.read_only)
        self._sync_groups()
        while self.tabs.count() > 1:
            w = self.tabs.widget(1)
            self.tabs.removeTab(1)
            w.deleteLater()
        self.forms = {}
        project = self.session.project
        advanced = self.advanced.isChecked()
        empty = {k.path for k in LC.SPEC
                 if k.required and project.get(k.path) in (None, "")}
        for sid, title, intro, keys in W.setting_sections(LC.dialog_fields(), advanced,
                                                          empty):
            page = QWidget()
            pl = QVBoxLayout(page)
            head = QHBoxLayout()
            text = QLabel(intro)
            text.setWordWrap(True)
            text.setStyleSheet(QUIET)
            head.addWidget(text, 1)
            pl.addLayout(head)
            form = SettingsForm(self.repo_root, project=project,
                                keys=[k.path for k in keys], parent=page,
                                headings=W.SECTION_HEADINGS,
                                channel_tools=(sid == "imaging"), advanced=advanced)
            form.setEnabled(not project.read_only)
            pl.addWidget(_scroll(form), 1)
            if sid == "atlas":
                self._add_fiji_row(pl, project)
            foot = QHBoxLayout()
            status = QLabel("")
            status.setWordWrap(True)
            status.setStyleSheet(QUIET)
            foot.addWidget(status, 1)
            apply_btn = QPushButton("Apply")
            apply_btn.setMinimumWidth(96)
            T.mark(apply_btn, kind="primary")
            apply_btn.setEnabled(not project.read_only)
            apply_btn.clicked.connect(lambda _=False, s=sid: self.apply(s))
            foot.addWidget(apply_btn)
            pl.addLayout(foot)
            self.forms[sid] = (form, status, title)
            self.tabs.addTab(page, title)
        self.update_marks()

    @staticmethod
    def _advanced_on():
        import ls_config as LC
        return bool(LC.read_settings().get("setup_advanced"))

    def _toggle_advanced(self, on):
        import ls_config as LC
        try:
            LC.write_settings({"setup_advanced": bool(on)})
        except OSError:
            pass
        current = self.tabs.currentIndex()
        self.build()
        self.tabs.setCurrentIndex(min(current, self.tabs.count() - 1))

    def _slide_report(self):
        """(summary, full-file path or None): the study's, else read from the folder."""
        project = self.session.project
        block = project.get("project") or {}
        out_root = project.get("out_root")
        full = os.path.join(out_root, "slide_metadata.json") if out_root else None
        if full and not os.path.isfile(full):
            full = None
        kept = block.get("slides")
        if kept:
            if _same_folder(kept.get("source_dir"), project.get("source_dir")):
                return kept, full
            # Read from another slides folder (or before the folder was recorded):
            # its full file describes that folder too.
            full = None
        import autodetect as AD
        found = AD.cached(project.get("source_dir"))
        if found and found.report:
            summary = {k: v for k, v in found.report.items() if k != "fields"}
            summary["notes"], summary["problems"] = list(found.notes), list(found.problems)
            return summary, full
        return None, full

    def _sync_report_btn(self):
        project = self.session.project
        source = project.get("source_dir")
        self.report_btn.setEnabled(bool((project.get("project") or {}).get("slides")
                                        or (source and os.path.isdir(source))))

    def show_slide_report(self):
        """The report window; when the folder has to be read first, once it is."""
        import autodetect as AD
        project = self.session.project
        if not (project.get("project") or {}).get("slides") \
                and not AD.is_cached(project.get("source_dir")):
            self._read_slides(report=True)
            return None
        return self._open_report()

    def _open_report(self):
        import slide_report as SR
        report, full = self._slide_report()
        if not report:
            self.detect_line.setText("No slide file in the slides folder could be read.")
            return None
        dlg = SR.SlideReportDialog(report, full, self)
        dlg.exec()
        return dlg

    def detect_from_slides(self, quiet=False):
        """Read the slide headers and fill in every setting the study has not set.

        Never overwrites: a value already set stays, and where the files say
        something else the difference is shown for a person to judge. The
        headers are read in the background; the settings are filled in when
        they land (_slides_read).
        """
        project = self.session.project
        source = project.get("source_dir")
        if not source or not os.path.isdir(source):
            self.detect_line.setText("Choose the slides folder first.")
            return None
        if project.read_only:
            return None
        self._read_slides(fill=True, quiet=quiet)
        return None

    def _read_slides(self, fill=False, report=False, quiet=False):
        """Start (or join) the background read of the study's slides folder.

        A second click while the same folder is being read joins that read
        rather than starting another; a read of a folder the study no longer
        points at is dropped when it lands.
        """
        project = self.session.project
        source = project.get("source_dir")
        want = self._when_read if self.slide_read.busy else None
        if want is None or want["project"] is not project or want["source"] != source:
            want = {"project": project, "source": source,
                    "fill": False, "report": False, "quiet": True}
        want["fill"] |= fill
        want["report"] |= report
        want["quiet"] &= quiet or not fill
        self._when_read = want
        if self.slide_read.start(source, fresh=want["fill"]):
            self.detect_line.setText("Reading the slides…")

    def _read_failed(self, words):
        self._when_read = None
        self.detect_line.setText(words)

    def _slides_read(self, source, found):
        want, self._when_read = self._when_read, None
        project = self.session.project
        if (not want or want["project"] is not project
                or not _same_folder(project.get("source_dir"), source)):
            return                      # the study or its folder changed meanwhile
        if found is None:
            self.detect_line.setText("Choose the slides folder first.")
            return
        if want["fill"] and not project.read_only:
            self._apply_detection(found, want["quiet"])
        else:
            self.detect_line.setText("")
        if want["report"]:
            self._open_report()

    def _apply_detection(self, found, quiet):
        """What detect_from_slides did with the headers once read (unchanged)."""
        import autodetect as AD
        project = self.session.project
        source = project.get("source_dir")
        if found.report and project.get("out_root"):
            project.note_slides(found.report, found.notes, found.problems, AD.REPORT_FILE,
                                source)
            AD.write_report(project.get("out_root"), found)
            self.session.touch()
        self._sync_report_btn()
        fills = AD.fill(project.cfg, found)
        for key, value in fills.items():
            project.set(key, value, by="read from the slides")
        differ = AD.compare(project.cfg, found)
        if fills:
            self.session.touch()
            self.build()
            self.applied.emit()
            self.logged.emit("settings read from the slides: " + ", ".join(sorted(fills)))
        lines = found.lines()
        if fills:
            lines.append("Filled in: %s." % ", ".join(sorted(fills)))
        else:
            lines.append("Nothing new to fill in: every setting the files give is already set.")
        for key, mine, theirs in differ:
            if key == "acquisition.markers":
                lines.append("The markers are named %s, but the scans name their marker "
                             "channel %s, so pairing cannot match them. In Microscope and "
                             "channels > Channels and markers, use the scan names and write "
                             "the antibody under 'What it stains'."
                             % (", ".join(map(str, mine)), ", ".join(theirs)))
                continue
            lines.append("The study says %s = %s, the files say %s. Check it under "
                         "Show advanced settings." % (key, mine, theirs))
        self.detect_line.setText(lines[0] + (" " + lines[-1] if len(lines) > 1 else ""))
        if not quiet:
            QMessageBox.information(self, "Read from the slides", "\n\n".join(lines))
        return found

    def _workbook_chosen(self, path):
        """Keep the workbook as 06b's --xlsx, as the Unblind question does."""
        values = dict(self.session.project.stage_options("join_sampling"))
        values["--xlsx"] = path
        self.workbook_line.setText("")
        self.options_changed.emit("join_sampling", values)

    def match_sheet(self):
        """Open Match the sheet on the chosen workbook; Save keeps the study's `sampling`."""
        import sheet_match as SM
        import workbook_check as WC
        project = self.session.project
        typed = self.workbook_field.value()
        path = WC.workbook_path(project.cfg, {"--xlsx": typed} if typed else
                                project.stage_options("join_sampling"))
        if not path or not os.path.isfile(path):
            self.workbook_line.setText("Choose the sampling workbook first: %s."
                                       % ("there is no file at " + path if path
                                          else "none is chosen"))
            self.workbook_line.setStyleSheet(f"color:{COLOUR['failed']}")
            return
        animals, where = WC.study_animals(project.cfg)
        dlg = SM.SheetMatchDialog(path, animals, where, project.get("sampling"), self)
        if not dlg.exec() or project.read_only:
            return
        if project.set("sampling", dlg.mapping(), by="Set up: Match the sheet"):
            self.session.touch()
            self.logged.emit("sampling sheet columns chosen: animal '%s', treatment %s"
                             % (dlg.mapping()["id_column"],
                                ", ".join("'%s'" % h for h in dlg.mapping()["group_columns"])))
        self.check_workbook()

    def check_workbook(self, load=False):
        """Start the dry run of 06b's join; the answer lands in _workbook_checked.

        With `load`, the groups it finds are then kept for the Shotgun deck."""
        self._load_groups_after_check = bool(load)
        import workbook_check as WC
        project = self.session.project
        typed = self.workbook_field.value()
        path = WC.workbook_path(project.cfg, {"--xlsx": typed} if typed else
                                project.stage_options("join_sampling"))
        self.workbook_check.start(project.cfg, path)
        self.workbook_line.setText("Checking the workbook…")

    def _workbook_checked(self, rep):
        import ls_sampling as SAMPLING
        if not rep.get("error") and not rep.get("animals"):
            self.workbook_line.setText(
                "The workbook can be read, but the study has no fish to match it to yet: "
                "none were found in the %s." % rep.get("from", "slide file names"))
            self.workbook_line.setStyleSheet(QUIET)
            if self._load_groups_after_check:
                self._load_groups_after_check = False
                self._sync_groups("No groups loaded: the study has no animals yet.", bad=True)
            return
        lines = SAMPLING.words(rep) if "matched" in rep else [
            "The workbook could not be checked: %s." % rep.get("error")]
        if rep.get("from") and not rep.get("error"):
            lines.append("Fish read from the %s." % rep["from"])
        bad = bool(rep.get("error") or rep.get("missing") or rep.get("blank"))
        self.workbook_line.setText(" ".join(lines))
        self.workbook_line.setStyleSheet(f"color:{COLOUR['failed']}" if bad else QUIET)
        self.logged.emit("workbook checked: " + lines[0])
        if self._load_groups_after_check:
            self._load_groups_after_check = False
            self._keep_groups(rep)

    def load_groups(self):
        """Check the workbook, then keep its groups (see _keep_groups)."""
        self.check_workbook(load=True)
        self.groups_line.setText("Reading the groups from the workbook…")
        self.groups_line.setStyleSheet(QUIET)

    def _keep_groups(self, rep):
        """Write groups.order / groups.by_animal from a finished check, after asking."""
        import ls_sampling as SAMPLING
        project = self.session.project
        key = SAMPLING.group_key(rep)
        if project.read_only or not key["by_animal"]:
            self._sync_groups("No groups loaded: the workbook gave no animal a group."
                              if not project.read_only else None, bad=True)
            return
        missing = (rep.get("missing") or []) + [a for a, _ in rep.get("blank") or []]
        text = ("This keeps each fish's treatment group in the study, for the Shotgun deck.\n\n"
                "%s\n\nThe curator's sections, plates and ROIs and every count stay keyed by "
                "animal; only the deck's layout and the order 05c measures sections in use "
                "the groups." % " ".join(SAMPLING.words(rep)))
        if missing:
            text += ("\n\nLeft out, with no group: %s. The deck skips them."
                     % ", ".join(missing))
        if QMessageBox.question(self, "Load the treatment groups?", text,
                                QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.No) != QMessageBox.Yes:
            self._sync_groups()
            return
        project.set("groups.order", key["order"], by="Set up: sampling workbook")
        project.set("groups.by_animal", key["by_animal"], by="Set up: sampling workbook")
        self.session.touch()
        self.build()
        self.applied.emit()
        self.logged.emit("treatment groups loaded from the workbook for the Shotgun deck "
                         "(%d animals)" % len(key["by_animal"]))
        self._sync_groups()

    def clear_groups(self):
        """Take the groups out of the study: blind again, Shotgun off."""
        project = self.session.project
        if project.read_only:
            return
        changed = project.set("groups.order", [], by="Set up: groups removed")
        changed = project.set("groups.by_animal", {}, by="Set up: groups removed") or changed
        if changed:
            self.session.touch()
            self.build()
            self.applied.emit()
            self.logged.emit("treatment groups removed from the study")
        self._sync_groups()

    def _sync_groups(self, note=None, bad=False):
        """The groups line and buttons from the study as it stands."""
        project = self.session.project
        by_animal = {a: g for a, g in (project.get("groups.by_animal") or {}).items()
                     if g and not str(a).startswith("_")}
        order = [g for g in project.get("groups.order") or [] if g]
        self.groups_btn.setEnabled(not project.read_only)
        self.clear_groups_btn.setEnabled(not project.read_only and bool(by_animal))
        if note:
            self.groups_line.setText(note)
        elif by_animal:
            counts = {}
            for g in by_animal.values():
                counts[g] = counts.get(g, 0) + 1
            self.groups_line.setText(
                "Groups kept for the Shotgun deck: %s (left to right: %s). Press Build page "
                "on the ROI curator to turn Shotgun on. Curation and counting stay blind."
                % (", ".join("%s %d animals" % (g, counts[g]) for g in sorted(counts)),
                   ", ".join(order) or "not set"))
        else:
            self.groups_line.setText("No treatment groups in the study: the analysis is "
                                     "blind until Unblind, and Shotgun is off.")
        self.groups_line.setStyleSheet(f"color:{COLOUR['failed']}" if bad else QUIET)

    def _add_fiji_row(self, layout, project):
        """'Add atlas from Fiji…', when this copy of the pipeline has the importer."""
        import atlas_dialog as AD
        if not AD.available():
            return
        row = QHBoxLayout()
        note = QLabel("Your own atlas: draw its regions in Fiji's ROI Manager, then add "
                      "it here.")
        note.setWordWrap(True)
        note.setStyleSheet(QUIET)
        row.addWidget(note, 1)
        row.addWidget(InfoButton(
            "Adding your own atlas",
            "<p>Put one image per plate in a folder, open each in Fiji, outline every "
            "region with the polygon or freehand tool, name it in the ROI Manager and "
            "save the ROI Manager as a .zip beside the image.</p>"
            "<p>The step-by-step guide is <code>docs/adding-your-own-atlas.md</code> in "
            "the pipeline folder; the dialog links to it.</p>"))
        self.fiji_btn = QPushButton("Add atlas from Fiji…")
        self.fiji_btn.setEnabled(not project.read_only)
        self.fiji_btn.clicked.connect(self.add_fiji_atlas)
        row.addWidget(self.fiji_btn)
        layout.addLayout(row)

    def add_fiji_atlas(self):
        import atlas_dialog as AD
        dlg = AD.FijiAtlasDialog(self.session, self)
        dlg.exec()
        if dlg.imported:
            self.build()
            self.logged.emit("atlas added from Fiji and selected")
            self.applied.emit()

    def apply(self, sid):
        form, status, title = self.forms[sid]
        if form.apply():
            self.session.touch()
            status.setText("Applied. It is saved with the project, and automatically "
                           "before the next stage runs.")
            status.setStyleSheet(f"color:{COLOUR['done']}")
            self.logged.emit(f"settings applied: {title}")
            self.update_marks()
            self.applied.emit()
            return True
        status.setText("Not applied: " + (form.message or "see the message."))
        status.setStyleSheet(f"color:{COLOUR['failed']}")
        return False

    def update_marks(self, study=None):
        for i, (sid, (form, _s, title)) in enumerate(self.forms.items(), start=1):
            self.tabs.setTabText(i, ("✓ " if form.complete() else "") + title)
        source = self.session.project.get("source_dir")
        left = self.slides_left()
        self.tabs.setTabText(0, ("" if left else "✓ ") + W.SLIDES_CARD)
        if not source:
            self.current.setText("No slide folder is set for this study yet.")
            return
        files = None
        if study is not None:
            files = HomeScreen._rows(os.path.join(study.out_root, "manifest",
                                                  "manifest_files.csv"))
        count = (f" The slide headers list {files:,} files from it." if files is not None
                 else " The slide headers have not been read yet (Prepare does that).")
        todo = ""
        if left:
            todo = (f"<br><span style='color:{COLOUR['failed']}'>Still to do on this tab: "
                    f"{esc('; '.join(left))}.</span>")
        self.current.setText(
            f"This study reads its slides from <b>{esc(source)}</b>.{count} "
            f"<span style='{QUIET}'>Choose a folder or files below only to change "
            f"that.</span>{todo}")

    def slides_left(self):
        """What the Slides tab still needs (workflow.slides_left); [] when done.

        Read means this folder's headers were read and kept in the study (New
        study and Read settings from the slides both do), or Prepare has listed
        its files in the manifest - a study made before the headers were kept.
        """
        import autodetect as AD
        project = self.session.project
        source = project.get("source_dir")
        kept = (project.get("project") or {}).get("slides") or {}
        out_root = project.get("out_root")
        read = (_same_folder(kept.get("source_dir"), source)
                or bool(out_root and os.path.isfile(
                    os.path.join(out_root, "manifest", "manifest_files.csv"))))
        return W.slides_left(project.cfg, read, AD.slide_files(source) if source else [])

    def missing(self):
        """[(card title, [required settings with no value])] for the incomplete cards.

        The Slides entry lists phrases (what is left to do there), not settings.
        """
        out = []
        left = self.slides_left()
        if left:
            out.append((W.SLIDES_CARD, left))
        for form, _s, title in self.forms.values():
            gaps = form.missing()
            if gaps:
                out.append((title, gaps))
        return out

    def completion(self):
        """(cards complete, cards) - Slides counts once nothing is left on it (slides_left)."""
        done = sum(1 for form, _s, _t in self.forms.values() if form.complete())
        return done + (not self.slides_left()), len(self.forms) + 1
