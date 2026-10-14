"""The screens of the step bar: Project, Set up, Prepare, Curate, Measure, Results.

Each screen is handed what it shows (`workflow.Study`, the open project, which
stages are running) on every refresh and reports what was pressed through
signals. Running a stage, saving and switching screens are the window's job;
nothing here starts a thread or writes a file.
"""

import csv
import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton, QScrollArea,
    QTabBar, QTabWidget, QVBoxLayout, QWidget)

import stages as S
import workflow as W
from widgets import (COLOUR, QUIET, WORD, InfoButton, PartHeader, ScreenHeader, StageRow,
                     esc)


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
        parts.append("<p style='color:#f85149'><b>This stage unblinds the analysis.</b> "
                     "It is the first one allowed to read the treatment groups.</p>")
    ok, why = W.applies(st, study)
    if not ok:
        parts.append(f"<p style='{QUIET}'>{esc(why)}</p>")
    reason = st.cli_reason()
    if reason:
        parts.append(f"<p style='color:#a371f7'><b>Not run from the app:</b> {esc(reason)}</p>")
    if st.needs:
        needs = ", ".join(esc(W.display_title(S.BY_ID[n], study)) for n in st.needs
                          if n in S.BY_ID)
        parts.append(f"<p><b>Needs first:</b> {needs}</p>")
    if st.outputs:
        shown = st.output_names(study.out_root, study.names)
        missing = set(st.missing(study.out_root, study.names))
        files = ", ".join((f"<span style='color:#d29922'>{esc(o)}</span>" if o in missing
                           else esc(o)) for o in shown)
        parts.append(f"<p><b>Leaves behind:</b> {files}"
                     + (" <span style='color:#d29922'>(amber: not there yet)</span>"
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


def stage_status(st, study, running):
    """(state, words) for a row's right-hand status."""
    state = W.state(st, study, running)
    if st.operator and state == "done" and not W.decided(st.sid, study):
        return "waiting", "Page built; waiting for your export"
    if st.operator and state == "done":
        return "done", "Decisions exported"
    return state, WORD.get(state, state)


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
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 8)
        lay.addWidget(ScreenHeader(screen))

        bar = QHBoxLayout()
        self.run_all = QPushButton("Run all remaining")
        self.run_all.setMinimumHeight(30)
        f = self.run_all.font()
        f.setBold(True)
        self.run_all.setFont(f)
        self.run_all.clicked.connect(self._run_all)
        bar.addWidget(self.run_all)
        bar.addWidget(InfoButton(
            "Run all remaining",
            "<p>Runs, in order, every stage on this screen whose results are not there "
            "yet. Stages this study does not use, and stages that run outside the app, "
            "are left out.</p>"
            "<p>It stops at the first failure, and it never runs past a place where you "
            "decide: a stage that needs a curator you have not exported yet waits, along "
            "with everything after it.</p>"
            "<p>The project is saved automatically before the first stage starts.</p>"))
        self.plan_note = QLabel("")
        self.plan_note.setWordWrap(True)
        self.plan_note.setStyleSheet(QUIET)
        bar.addWidget(self.plan_note, 1)
        lay.addLayout(bar)
        self._plan = []
        # All stages spans the whole pipeline, unblinding included; running all of
        # it from one button is not something to offer.
        self._offers_run_all = screen.sid != "all"
        for i in range(bar.count()):
            item = bar.itemAt(i).widget()
            if item is not None and item is not self.plan_note:
                item.setVisible(self._offers_run_all)

        inner = QWidget()
        body = QVBoxLayout(inner)
        body.setContentsMargins(0, 0, 8, 0)
        for heading, text, ids in screen.visible_parts():
            if heading:
                body.addWidget(PartHeader(heading, text))
            for sid in ids:
                st = S.BY_ID[sid]
                row = StageRow(st, W.options_for(sid))
                row.run_clicked.connect(lambda s: self.run_requested.emit([s]))
                row.open_clicked.connect(self.open_requested.emit)
                row.options_changed.connect(self.options_changed.emit)
                self.rows[sid] = row
                body.addWidget(row)
        body.addStretch(1)
        lay.addWidget(_scroll(inner), 1)

    def _run_all(self):
        if self._plan:
            self.run_requested.emit(list(self._plan))

    def refresh(self, study, running, project, busy=False):
        block = project.block if project is not None else {}
        for sid, row in self.rows.items():
            st = S.BY_ID[sid]
            values = project.stage_options(sid) if project is not None else {}
            state, words = stage_status(st, study, running)
            ok, _ = W.applies(st, study)
            code, _name = W.split_title(st.title)
            if sid in W.HIDDEN:
                words = "Superseded"
            row.refresh(title=W.display_title(st, study), code=code,
                        plain=W.plain(st, study), status=words, state=state,
                        info_html=stage_info(st, study, block, values), mark=marks(st),
                        can_run=bool(st.script) and ok and not st.cli_reason() and not busy,
                        option_values=values, changed_count=len(values))
        self._plan, held = W.run_plan(self.screen.sid, study)
        self.run_all.setEnabled(bool(self._plan) and not busy)
        bits = []
        if self._plan:
            bits.append(f"{len(self._plan)} stage{'s' if len(self._plan) != 1 else ''} to run")
        else:
            bits.append("Nothing left to run here")
        if held:
            waits = sorted(set(held.values()))
            names = ", ".join(W.display_title(S.BY_ID[w], study) for w in waits)
            bits.append(f"{len(held)} waiting for: {names}")
        self.plan_note.setText(" · ".join(bits))


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
        lay.setContentsMargins(16, 12, 16, 8)
        lay.addWidget(ScreenHeader(W.CURATE))

        self.tabs = QTabBar()
        self.tabs.setExpanding(False)
        self.tabs.currentChanged.connect(self._tab_changed)
        lay.addWidget(self.tabs)

        self.row_holder = QVBoxLayout()
        lay.addLayout(self.row_holder)
        self.rows = {}
        for sid in ("atlas_reframe", "rotation_curator", "roi_curator"):
            row = StageRow(S.BY_ID[sid], W.options_for(sid))
            row.open_btn.setVisible(False)
            row.run_clicked.connect(lambda s: self.run_requested.emit([s]))
            row.options_changed.connect(self.options_changed.emit)
            row.setVisible(False)
            self.rows[sid] = row
            self.row_holder.addWidget(row)

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
        self.measure_btn = QPushButton("Go to Measure")
        self.measure_btn.clicked.connect(lambda: self.goto.emit("measure"))
        actions.addWidget(self.measure_btn)
        lay.addLayout(actions)
        lay.addWidget(self.curator, 1)
        self._study = None

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
                self.decision.setText(
                    f"<span style='color:{COLOUR['done']}'>Decisions exported:</span> "
                    f"{esc(os.path.relpath(latest, study.out_root) if latest.startswith(study.out_root) else latest)}")
            else:
                self.decision.setText(
                    f"<span style='color:{COLOUR['waiting']}'>No decisions exported "
                    "yet.</span> Work in the page below, then press its Export button.")
        else:
            self.decision.setText(W.plain(S.BY_ID[sid], study))
        self.continue_btn.setVisible(sid in ("atlas_reframe", "rotation_curator"))
        self.continue_btn.setEnabled(bool(latest))
        self.import_btn.setVisible(sid == "roi_curator")
        self.measure_btn.setVisible(sid == "roi_curator")

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
            mark = "✓ " if W.decided(sid, study) and S.BY_ID[sid].done(study.out_root, study.names) else ""
            self.tabs.setTabText(i, mark + W.display_title(S.BY_ID[sid], study))
        for sid, row in self.rows.items():
            st = S.BY_ID[sid]
            values = project.stage_options(sid) if project is not None else {}
            state, words = stage_status(st, study, running)
            code, _ = W.split_title(st.title)
            row.refresh(title=W.display_title(st, study), code=code, plain=W.plain(st, study),
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
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 8)
        lay.addWidget(ScreenHeader(W.HOME))

        self.project_line = QLabel("")
        self.project_line.setWordWrap(True)
        self.project_line.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.project_line)

        card = QFrame()
        card.setObjectName("nextUp")
        card.setStyleSheet("#nextUp{border:2px solid #4da3ff;border-radius:8px}")
        cl = QHBoxLayout(card)
        cl.setContentsMargins(14, 10, 14, 10)
        col = QVBoxLayout()
        head = QLabel("Next up")
        head.setStyleSheet("color:#4da3ff;font-weight:600")
        col.addWidget(head)
        self.next_text = QLabel("")
        self.next_text.setWordWrap(True)
        f = self.next_text.font()
        f.setPointSize(f.pointSize() + 2)
        self.next_text.setFont(f)
        col.addWidget(self.next_text)
        cl.addLayout(col, 1)
        cl.addWidget(InfoButton("Next up", "<p>The single next thing to do, worked out "
                                "from what is already in the output folder: unfinished "
                                "Set up first, then Prepare, then any curator waiting "
                                "for your export, then Measure and Results.</p>"))
        self.next_btn = QPushButton("Go")
        self.next_btn.setMinimumWidth(90)
        self.next_btn.clicked.connect(lambda: self.goto.emit(self._next_target))
        cl.addWidget(self.next_btn)
        lay.addWidget(card)
        self._next_target = "home"

        grid = QGridLayout()
        grid.setColumnStretch(2, 1)
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
            count.setMinimumWidth(150)
            grid.addWidget(count, r, 3)
            btn = QPushButton("Open")
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
        self.next_btn.setText("Open" if target in ("setup", "curate", "results", "home")
                              else "Run it")
        self.next_btn.setEnabled(target != "home" and not busy)

        for sid in self.STEP_IDS:
            if sid == "setup":
                done, total = setup_done, setup_total
            else:
                done, total = W.progress(sid, study)
            bar = self.bars[sid]
            bar.setMaximum(max(total, 1))
            bar.setValue(done)
            self.counts[sid].setText(f"{done} of {total} done" if total else "nothing to do")

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
                lines.append(f"<span style='color:{colour}'>●</span> {esc(title)} "
                             f"<span style='{QUIET}'>{'ok' if ok else 'failed'} · {esc(when)}"
                             f"{' · ' + took if took else ''}</span>")
            self.recent.setText("<br>".join(lines))


class SetupScreen(QWidget):
    """Slides, then the study's settings as cards, each with Apply."""

    applied = Signal()
    logged = Signal(str)

    def __init__(self, repo_root, importer, session, parent=None):
        super().__init__(parent)
        self.repo_root = repo_root
        self.session = session
        self.importer = importer
        self.forms = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 8)
        lay.addWidget(ScreenHeader(W.SETUP))
        self.tabs = QTabWidget()
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
        while self.tabs.count() > 1:
            w = self.tabs.widget(1)
            self.tabs.removeTab(1)
            w.deleteLater()
        self.forms = {}
        project = self.session.project
        for sid, title, intro, keys in W.setting_sections(LC.dialog_fields()):
            page = QWidget()
            pl = QVBoxLayout(page)
            head = QHBoxLayout()
            text = QLabel(intro)
            text.setWordWrap(True)
            text.setStyleSheet(QUIET)
            head.addWidget(text, 1)
            pl.addLayout(head)
            form = SettingsForm(self.repo_root, project=project,
                                keys=[k.path for k in keys], parent=page)
            form.setEnabled(not project.read_only)
            pl.addWidget(_scroll(form), 1)
            foot = QHBoxLayout()
            status = QLabel("")
            status.setWordWrap(True)
            status.setStyleSheet(QUIET)
            foot.addWidget(status, 1)
            apply_btn = QPushButton("Apply")
            apply_btn.setEnabled(not project.read_only)
            apply_btn.clicked.connect(lambda _=False, s=sid: self.apply(s))
            foot.addWidget(apply_btn)
            pl.addLayout(foot)
            self.forms[sid] = (form, status, title)
            self.tabs.addTab(page, title)
        self.update_marks()

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
        self.tabs.setTabText(0, ("✓ " if source else "") + "Slides")
        if not source:
            self.current.setText("No slide folder is set for this study yet.")
            return
        files = None
        if study is not None:
            files = HomeScreen._rows(os.path.join(study.out_root, "manifest",
                                                  "manifest_files.csv"))
        count = (f" The slide headers list {files:,} files from it." if files is not None
                 else " The slide headers have not been read yet (Prepare does that).")
        self.current.setText(
            f"This study reads its slides from <b>{esc(source)}</b>.{count} "
            f"<span style='{QUIET}'>Choose a folder or files below only to change "
            f"that.</span>")

    def missing(self):
        """[(card title, [required settings with no value])] for the incomplete cards."""
        out = []
        if not self.session.project.get("source_dir"):
            out.append(("Slides", ["the slide folder"]))
        for form, _s, title in self.forms.values():
            gaps = form.missing()
            if gaps:
                out.append((title, gaps))
        return out

    def completion(self):
        """(cards complete, cards) - Slides counts as complete when a slides folder is set."""
        done = sum(1 for form, _s, _t in self.forms.values() if form.complete())
        has_slides = bool(self.session.project.get("source_dir"))
        return done + has_slides, len(self.forms) + 1
