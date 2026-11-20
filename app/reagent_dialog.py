"""Reagent controls: the confirm list, opened from New study and Set up > Slides.

A reagent control is a slide or section stained without the primary antibody
(or with the secondary only), with an isotype / non-immune IgG instead of it,
with an antibody pre-adsorbed with its peptide, or not stained at all. The
pipeline keeps confirmed ones out of every experimental output and reports
them on their own; this list is where the operator confirms which they are.

Each row is one candidate: a slide file whose name the scan matched
(ls_reagent_controls.scan), one already confirmed, one added by hand, or a
section the signal screen (01m, qc/reagent_screen.csv) proposed. The operator
picks the kind, which markers it controls, and Accept / Not a control. A file
whose name the study's naming rule cannot read gets an Animal ID and slide, so
00 can still give its scenes a standard uid.

The list proposes; it never decides. A weak name match (NC, ctrl) and every
signal-screen candidate start undecided. Save writes `reagent_controls.files`,
`.scenes`, `.rejected` and `.paired_with` and nothing else (`declared` is the
New study / Set up answer, not this list's). Nothing here reads which animal is
in which group: the scan is handed the group labels only.
"""

import copy
import csv
import html
import os
import re
import zipfile

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QRadioButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

import ls_channels as CH
import ls_naming as NM
import ls_reagent_controls as RC
import theme as T

BY = "reagent controls"

#: The kinds, in plain words, in ls_reagent_controls.KINDS order.
KIND_WORDS = {
    "no_primary": "No primary antibody (or secondary only)",
    "unstained": "Unstained (no antibodies at all)",
    "isotype": "Isotype / non-immune IgG instead of the primary",
    "preadsorbed": "Primary pre-adsorbed with its peptide",
    "other": "Other (say what in the note)",
}

#: How each default pairing (ls_reagent_report.pairing_for) is said on a row.
PAIRING_WORDS = {
    "operator": "the sections you chose",
    "same_file": "the stained sections on the same slide file",
    "same_animal": "the same animal's stained sections",
    "study_wide": "every stained section of the marker (study-wide: this crosses "
                  "batches, so the level comparison is left out)",
}

HELP = ("Reagent controls are slides or sections stained without the primary antibody "
        "(or with the secondary only), with an isotype / non-immune IgG instead of it, "
        "with an antibody pre-adsorbed with its peptide, or unstained. Confirmed ones are "
        "kept out of every experimental result and reported on their own sheet. A strong "
        "name match is ticked for you; a name that could also be a sample code, and "
        "anything the signal screen proposes, waits for your answer. Nothing is decided "
        "until you press Save.")


def slide_paths(source_dir):
    """The slide names 00 reads, as the scan sees them: each loose .czi by its name,
    and each .czi inside a .zip as `<zip>/<member>`."""
    out = []
    try:
        names = sorted(os.listdir(source_dir or ""))
    except OSError:
        return out
    for n in names:
        p = os.path.join(source_dir, n)
        if not os.path.isfile(p):
            continue
        low = n.lower()
        if low.endswith(".czi"):
            out.append(n)
        elif low.endswith(".zip"):
            try:
                with zipfile.ZipFile(p) as zf:
                    out += [f"{n}/{m}" for m in zf.namelist() if m.lower().endswith(".czi")]
            except (zipfile.BadZipFile, OSError):
                continue
    return out


def scan_folder(cfg, source_dir):
    """ls_reagent_controls.scan over the slides folder, with the study's markers,
    group labels (groups.order only) and extra patterns."""
    labels = ((cfg or {}).get("groups") or {}).get("order") or []
    return RC.scan(slide_paths(source_dir), CH.marker_names(cfg or {}), labels,
                   RC.entries(cfg)["extra_patterns"])


def needs_confirming(cfg, source_dir):
    """True when Create should open the list: the answer was yes or unsure, or a
    strong name match is neither confirmed nor rejected."""
    if RC.entries(cfg)["declared"] in ("yes", "unsure"):
        return True
    return bool(RC.unanswered(cfg, scan_folder(cfg, source_dir)))


def _base(p):
    return os.path.basename(str(p or "").replace("\\", "/"))


def _highlight(path, token):
    """`path` escaped, the matched token in bold on a tint."""
    if token:
        i = path.lower().find(token.lower())
        if i >= 0:
            return (html.escape(path[:i])
                    + f"<b style='background:{T.WARN};color:white'>&nbsp;"
                    + html.escape(path[i:i + len(token)]) + "&nbsp;</b>"
                    + html.escape(path[i + len(token):]))
    return html.escape(path)


class _Row(QFrame):
    """One candidate: what it is, its kind and markers, and the answer."""

    def __init__(self, dlg, key, where, path, source, token="", tier="strong", entry=None,
                 ticked=False, needs_subject=False, markers_on=None, extra_words=""):
        super().__init__()
        self.setObjectName("card")
        self.key, self.where, self.path, self.source = key, where, path, source
        self.token, self.tier = token or "", tier
        self.old = dict(entry or {})
        self.pairing_kind = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)

        words = _highlight(path, self.token)
        if where == "scenes":
            words = "<b>section</b> " + words
        if source == "signal":
            words += " &middot; proposed by the signal screen"
        elif tier == "weak":
            words += " &middot; ambiguous: could be a sample or treatment code"
        elif source == "manual":
            words += " &middot; added by hand"
        if extra_words:
            words += " &middot; " + html.escape(extra_words)
        self.label = QLabel(words)
        self.label.setTextFormat(Qt.RichText)
        self.label.setWordWrap(True)
        lay.addWidget(self.label)

        top = QHBoxLayout()
        self.kind = QComboBox()
        for k in RC.KINDS:
            self.kind.addItem(KIND_WORDS[k], k)
        kind = self.old.get("kind") or "no_primary"
        self.kind.setCurrentIndex(max(0, self.kind.findData(kind)))
        top.addWidget(QLabel("Kind"))
        top.addWidget(self.kind, 1)
        lay.addLayout(top)

        mrow = QHBoxLayout()
        mrow.addWidget(QLabel("Controls the markers"))
        self.markers = {}
        stored = self.old.get("markers")
        on = (markers_on if markers_on is not None
              else (stored if stored else None))
        for m in dlg.study_markers:
            cb = QCheckBox(m)
            cb.setChecked(not on or m in on)
            self.markers[m] = cb
            mrow.addWidget(cb)
        mrow.addStretch(1)
        lay.addLayout(mrow)

        # Only for a file the study's naming rule cannot read: 00 builds the
        # uid from these instead of failing on the name.
        self.subject_box = QWidget()
        sl = QHBoxLayout(self.subject_box)
        sl.setContentsMargins(0, 0, 0, 0)
        self.subject = QLineEdit(str(self.old.get("subject") or ""))
        self.subject.setPlaceholderText("the donor animal's ID, or a made-up one")
        self.slide = QSpinBox()
        self.slide.setRange(0, 9999)
        self.slide.setSpecialValueText("not set")
        self.replicate = QLineEdit(str(self.old.get("replicate") or ""))
        self.replicate.setPlaceholderText("optional")
        self.replicate.setMaximumWidth(90)
        sl.addWidget(QLabel("Animal ID"))
        sl.addWidget(self.subject, 1)
        sl.addWidget(QLabel("Slide"))
        sl.addWidget(self.slide)
        sl.addWidget(QLabel("Replicate"))
        sl.addWidget(self.replicate)
        lay.addWidget(self.subject_box)
        self.subject_box.setHidden(not needs_subject)
        if needs_subject:
            if not self.subject.text():
                self.subject.setText(dlg.guess_subject(key))
            self.slide.setValue(int(self.old.get("slide") or dlg.guess_slide(key)))

        self.note = QLineEdit(str(self.old.get("note") or ""))
        self.note.setPlaceholderText("note (optional)")
        lay.addWidget(self.note)

        ans = QHBoxLayout()
        self.answer = QButtonGroup(self)
        self.accept = QRadioButton("Accept")
        self.reject = QRadioButton("Not a control")
        self.later = QRadioButton("Decide later")
        for b in (self.accept, self.reject, self.later):
            self.answer.addButton(b)
            ans.addWidget(b)
        (self.accept if ticked else self.later).setChecked(True)
        ans.addStretch(1)
        lay.addLayout(ans)

        prow = QHBoxLayout()
        self.pairing_label = QLabel("")
        self.pairing_label.setWordWrap(True)
        self.pairing_label.setStyleSheet(T.HINT)
        self.pairing_btn = QPushButton("Change pairing…")
        self.pairing_btn.clicked.connect(lambda _c=False: dlg.change_pairing(self.key))
        prow.addWidget(self.pairing_label, 1)
        prow.addWidget(self.pairing_btn)
        lay.addLayout(prow)

    def chosen_markers(self):
        """[] when every marker is ticked (the config's "all")."""
        on = [m for m, b in self.markers.items() if b.isChecked()]
        return [] if len(on) == len(self.markers) else on

    def entry(self):
        out = dict(self.old)
        out.update({"kind": self.kind.currentData(), "markers": self.chosen_markers(),
                    "source": self.source, "pattern": self.token or None,
                    "note": self.note.text().strip()})
        if not self.subject_box.isHidden():
            out["subject"] = self.subject.text().strip()
            out["slide"] = int(self.slide.value())
            rep = self.replicate.text().strip()
            if rep:
                out["replicate"] = rep
            else:
                out.pop("replicate", None)
        return out


class _PairingPicker(QDialog):
    """Pick the stained sections a reagent control is compared with."""

    def __init__(self, choices, chosen, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Change pairing")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("The stained sections this reagent control is compared with. "
                             "Choose none to go back to the default."))
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.MultiSelection)
        for uid in choices:
            it = QListWidgetItem(uid)
            self.list.addItem(it)
            it.setSelected(uid in chosen)
        lay.addWidget(self.list, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def chosen(self):
        return [it.text() for it in self.list.selectedItems()]


class ReagentControlsDialog(QDialog):
    """The confirm list. `save()` writes the four keys; `changed` says if it did."""

    def __init__(self, project, slides_dir, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Reagent controls")
        self.resize(820, 680)
        self.project, self.slides_dir = project, slides_dir
        self.changed = False
        cfg = project.cfg
        self.e = RC.entries(cfg)
        self.study_markers = list(CH.marker_names(cfg))
        try:
            self._pattern = NM.compile_pattern(cfg.get("slide_naming") or {})
        except (NM.NamingError, TypeError, ValueError):
            self._pattern = None
        self.paired_with = copy.deepcopy(self.e["paired_with"])
        self._scene_rows = self._manifest_rows(cfg)
        self.rows, self._by_key = [], {}
        self._made_up = 0

        lay = QVBoxLayout(self)
        self.help = QLabel(HELP)
        self.help.setWordWrap(True)
        lay.addWidget(self.help)
        png = os.path.join(cfg.get("out_root") or "", "qc", "reagent_screen.png")
        if os.path.isfile(png):
            pic = QLabel()
            pm = QPixmap(png)
            if not pm.isNull():
                pic.setPixmap(pm.scaledToWidth(min(760, pm.width()), Qt.SmoothTransformation))
                pic.setToolTip("The signal screen's contact strip (qc/reagent_screen.png)")
                lay.addWidget(pic)

        self.body = QWidget()
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(0, 0, 0, 0)
        self.body_lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.body)
        lay.addWidget(scroll, 1)

        self.empty = QLabel("Nothing in the slide names looks like a reagent control, and "
                            "the signal screen has proposed none. Add a file by hand if one "
                            "is.")
        self.empty.setWordWrap(True)
        self.empty.setStyleSheet(T.HINT)
        lay.addWidget(self.empty)

        bar = QHBoxLayout()
        add = QPushButton("Add a file…")
        add.setToolTip("A slide file that is a reagent control although its name does not say so")
        add.clicked.connect(self._add_file)
        bar.addWidget(add)
        bar.addStretch(1)
        lay.addLayout(bar)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._save_and_close)
        self.buttons.rejected.connect(self.reject)
        if project.read_only:
            self.buttons.button(QDialogButtonBox.Save).setEnabled(False)
        lay.addWidget(self.buttons)

        self._populate(cfg)

    # ------------------------------------------------------------- building
    @staticmethod
    def _manifest_rows(cfg):
        try:
            import ls_reagent_report as LR
            return LR.study_rows(cfg)
        except (OSError, ValueError, KeyError, csv.Error, ImportError):
            return []

    def _needs_subject(self, key):
        return self._pattern is None or NM.parse_name(key, self._pattern) is None

    def guess_subject(self, key):
        """A leading `<letters><digits>_` is likely the donor animal; else RC1, RC2..."""
        m = re.match(r"^([A-Za-z]+\d+)(?=[_\-\s.])", key)
        if m:
            return m.group(1)
        taken = {str((e or {}).get("subject") or "") for e in self.e["files"].values()}
        taken |= {r.subject.text() for r in self.rows}
        while True:
            self._made_up += 1
            name = f"RC{self._made_up}"
            if name not in taken:
                return name

    @staticmethod
    def guess_slide(key):
        """The last number in the name after any leading animal ID, else 0 (not set)."""
        stem = os.path.splitext(key)[0]
        stem = re.sub(r"^[A-Za-z]+\d+(?=[_\-\s.])", "", stem)
        nums = re.findall(r"\d+", stem)
        return int(nums[-1]) if nums else 0

    def _add(self, key, where, path, source, **kw):
        if key in self._by_key:
            return self._by_key[key]
        if where == "files":
            kw.setdefault("needs_subject", self._needs_subject(key))
        row = _Row(self, key, where, path, source, **kw)
        self.rows.append(row)
        self._by_key[key] = row
        self.body_lay.insertWidget(self.body_lay.count() - 1, row)
        row.answer.buttonToggled.connect(lambda *_a: self._refresh_pairings())
        for cb in row.markers.values():
            cb.toggled.connect(lambda *_a: self._refresh_pairings())
        self.empty.setVisible(False)
        self._refresh_pairings()
        return row

    def _populate(self, cfg):
        e = self.e
        rejected = set(e["rejected"])
        for m in scan_folder(cfg, self.slides_dir):
            key = _base(m.path)
            if key in rejected or m.path in rejected:
                continue
            old = e["files"].get(key)
            self._add(key, "files", m.path, (old or {}).get("source") or "name",
                      token=m.token, tier=m.tier, entry=old or {"kind": m.kind},
                      ticked=bool(old) or m.tier == "strong",
                      markers_on=None if old else (m.markers or None))
        for key, old in e["files"].items():
            self._add(key, "files", key, (old or {}).get("source") or "manual",
                      token=(old or {}).get("pattern") or "", entry=old, ticked=True)
        for uid, old in e["scenes"].items():
            self._add(uid, "scenes", uid, (old or {}).get("source") or "manual",
                      token="", entry=old, ticked=True)
        for uid, info in self._screen_candidates(cfg).items():
            self._add(uid, "scenes", uid, "signal", entry={"kind": "no_primary"},
                      ticked=False, markers_on=info["markers"],
                      extra_words="%s, rank %s" % (info["file"], info["rank"] or "?"))
        self.empty.setVisible(not self.rows)

    def _screen_candidates(self, cfg):
        """{uid: {file, markers, rank}} from qc/reagent_screen.csv: status candidate,
        not already confirmed or rejected."""
        path = os.path.join(cfg.get("out_root") or "", *RC.SCREEN_CSV)
        if not os.path.isfile(path):
            return {}
        e = self.e
        answered = set(e["files"]) | set(e["scenes"]) | set(e["rejected"])
        out = {}
        try:
            with open(path, newline="", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
        except (OSError, csv.Error, ValueError):
            return {}
        for r in rows:
            if (r.get("status") or "").strip() != "candidate":
                continue
            uid, f = r.get("scene_uid") or "", r.get("file") or ""
            if not uid or uid in answered or f in answered or _base(f) in answered:
                continue
            got = out.setdefault(uid, {"file": _base(f), "markers": [], "rank": r.get("rank")})
            if r.get("marker") and r["marker"] not in got["markers"]:
                got["markers"].append(r["marker"])
        return out

    def row_for(self, key):
        return self._by_key.get(key)

    def add_manual(self, name):
        """A row for a slide file added by hand (or the one already listed)."""
        name = _base(name)
        return self._add(name, "files", name, "manual", entry=self.e["files"].get(name),
                         ticked=False)

    def _add_file(self):
        start = self.slides_dir if self.slides_dir and os.path.isdir(self.slides_dir) else ""
        path, _ = QFileDialog.getOpenFileName(self, "A slide file that is a reagent control",
                                              start, "Slide files (*.czi)")
        if path:
            self.add_manual(path).accept.setChecked(True)

    # ------------------------------------------------------------- pairing
    def _pending_cfg(self):
        """The study's config with this list's current answers in it."""
        cfg = copy.deepcopy(self.project.cfg)
        rc = cfg.setdefault("reagent_controls", {})
        files, scenes = dict(self.e["files"]), dict(self.e["scenes"])
        for r in self.rows:
            target = files if r.where == "files" else scenes
            if r.accept.isChecked():
                target[r.key] = r.entry()
            else:
                target.pop(r.key, None)
        rc["files"], rc["scenes"] = files, scenes
        rc["paired_with"] = copy.deepcopy(self.paired_with)
        return cfg

    def _uids_of(self, row):
        if row.where == "scenes":
            return [row.key] if any(r["scene_uid"] == row.key for r in self._scene_rows) else []
        out = []
        for r in self._scene_rows:
            if _base(r.get("file")) == row.key and r["scene_uid"] not in out:
                out.append(r["scene_uid"])
        return out

    def _control_marker(self, row, uid):
        want = row.chosen_markers()
        have = [r["marker_channel"] for r in self._scene_rows if r["scene_uid"] == uid]
        for m in have:
            if not want or m in want:
                return m
        return have[0] if have else None

    def _refresh_pairings(self):
        if not self._scene_rows:
            for r in self.rows:
                r.pairing_kind = None
                r.pairing_label.setText("Compared with: known once Prepare has listed the "
                                        "sections (00).")
                r.pairing_btn.setEnabled(False)
            return
        import ls_reagent_report as LR
        cfg = self._pending_cfg()
        lookup = RC.expand(cfg, self._scene_rows)
        for r in self.rows:
            uids = self._uids_of(r)
            if not uids:
                r.pairing_kind = None
                r.pairing_label.setText("Compared with: not in the manifest yet; run "
                                        "Prepare (00) again.")
                r.pairing_btn.setEnabled(False)
                continue
            marker = self._control_marker(r, uids[0])
            kind, got = LR.pairing_for(uids[0], marker, lookup, self._scene_rows, cfg)
            r.pairing_kind = kind
            r.pairing_label.setText("Compared with %s (%d section%s)."
                                    % (PAIRING_WORDS.get(kind, kind), len(got),
                                       "" if len(got) == 1 else "s"))
            r.pairing_btn.setEnabled(not self.project.read_only)

    def stained_choices(self, key):
        """The stained sections a control can be paired with: its markers, not a
        reagent control, not its own."""
        row = self._by_key.get(key)
        if row is None:
            return []
        lookup = RC.expand(self._pending_cfg(), self._scene_rows)
        mine = set(self._uids_of(row))
        want = row.chosen_markers()
        out = []
        for r in self._scene_rows:
            uid, m = r["scene_uid"], r["marker_channel"]
            if uid in mine or (want and m not in want) or RC.is_control(lookup, uid, m):
                continue
            if uid not in out:
                out.append(uid)
        return sorted(out, key=NM.natural_key)

    def change_pairing(self, key, chosen=None):
        """Pair `key` with `chosen` (stained uids); None asks. [] goes back to the default."""
        if chosen is None:
            pick = _PairingPicker(self.stained_choices(key), self.paired_with.get(key) or [],
                                  self)
            if not pick.exec():
                return
            chosen = pick.chosen()
        if chosen:
            self.paired_with[key] = list(chosen)
        else:
            self.paired_with.pop(key, None)
        self._refresh_pairings()

    # ------------------------------------------------------------- saving
    def _problems(self):
        out = []
        for r in self.rows:
            if not r.accept.isChecked() or r.subject_box.isHidden():
                continue
            subj = r.subject.text().strip()
            if not subj or "_" in subj:
                out.append(f"{r.key}: give an Animal ID without '_' (the donor animal, or a "
                           "made-up one such as RC1)")
            if r.slide.value() < 1:
                out.append(f"{r.key}: give its slide number")
        return out

    def save(self):
        """Write the answers. False (and a message) when an answer is incomplete."""
        if self.project.read_only:
            return False
        problems = self._problems()
        if problems:
            QMessageBox.warning(self, "Reagent controls", "These need a look first:\n\n"
                                + "\n".join("• " + p for p in problems))
            return False
        e = self.e
        files, scenes = dict(e["files"]), dict(e["scenes"])
        rejected = list(e["rejected"])
        for r in self.rows:
            target = files if r.where == "files" else scenes
            if r.accept.isChecked():
                target[r.key] = r.entry()
                rejected = [x for x in rejected if x != r.key]
            elif r.reject.isChecked():
                target.pop(r.key, None)
                if r.key not in rejected:
                    rejected.append(r.key)
            else:
                target.pop(r.key, None)
        have = self.project.get("reagent_controls") or {}
        for name, value in (("files", files), ("scenes", scenes), ("rejected", rejected),
                            ("paired_with", self.paired_with)):
            if not value and name not in have:
                continue
            if self.project.set("reagent_controls." + name, value, by=BY):
                self.changed = True
        self.e = RC.entries(self.project.cfg)
        return True

    def _save_and_close(self):
        if self.save():
            self.accept()
