"""The settings of one study, offered from the spec that defines them.

This used to carry three hand-written entries - the slides folder, the output
root and the atlas - with a comment saying everything else had a working
default. Several of those defaults were not settings anyone had chosen; they
were whatever the code fell back to.

The fields now come from `ls_config.SPEC`, so a key added there appears here
without anyone remembering to edit a second list, its hint is the same sentence
the validator uses when it rejects a value, and the dialog cannot offer a key
the pipeline does not read.
"""

import copy
import html
import json
import os

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton,
    QScrollArea, QToolButton, QVBoxLayout, QWidget)

import atlas_registry as AR
import ls_channel_edit as CE
import ls_atlas_import as AI
import ls_config as LC
import ls_jsonio
from channel_editor import ChannelEditor
import theme as T
from widgets import InfoButton


#: How a paired study finds the objects each marker is measured in, as Set up
#: offers it: the words shown, and the `acquisition.marker_segmentation` entry
#: written (None: the marker is not listed, so it takes the nuclear route).
NUCLEAR_ROUTE = "On the counterstain (DAPI)"
OWN_ROUTE = "On this marker's own channel"
OWN_ENTRY = {"segment": "own", "backend": "stardist", "nucleus_shaped": True}
#: Said under a marker whose nuclei are found on its own channel: what that costs.
OWN_CONSEQUENCE = "Total cell density is withheld for this marker."


class _SegmentationRow(QWidget):
    """Per marker of a paired study: are its nuclei found on DAPI or on itself?

    The LS PCNA (AF488) scans carry a DAPI plane that is noise, so PCNA has to be
    segmented on its own green channel. Without this row that was only possible
    by editing config.json. An entry already in the file with settings of its
    own (another backend) is kept as it is while its marker stays on its own
    channel.
    """

    def __init__(self, markers, block, parent=None):
        super().__init__(parent)
        self.block = dict(block) if isinstance(block, dict) else {}
        self.lay = QFormLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.combos = {}
        self.set_markers(markers)

    def set_markers(self, markers):
        current = (self.value() or {}) if self.combos else self.block
        while self.lay.rowCount():
            self.lay.removeRow(0)
        self.combos, self.notes = {}, {}
        for m in markers:
            combo = QComboBox()
            combo.addItems([NUCLEAR_ROUTE, OWN_ROUTE])
            entry = current.get(m)
            own = isinstance(entry, dict) and (entry.get("segment") or "own") == "own"
            combo.setCurrentText(OWN_ROUTE if own else NUCLEAR_ROUTE)
            # The choice changes what the results can report, so say so beside
            # it rather than only in the (i).
            note = QLabel(OWN_CONSEQUENCE)
            note.setStyleSheet(f"color:{T.WARN};font-size:12px")
            note.setVisible(own)
            combo.currentTextChanged.connect(
                lambda text, n=note: n.setVisible(text == OWN_ROUTE))
            cell = QWidget()
            cl = QVBoxLayout(cell)
            cl.setContentsMargins(0, 0, 0, 0)
            cl.setSpacing(2)
            cl.addWidget(combo)
            cl.addWidget(note)
            self.combos[m], self.notes[m] = combo, note
            self.lay.addRow(m, cell)
        if not markers:
            note = QLabel("No markers declared yet (Channels and markers...).")
            note.setStyleSheet(T.QUIET)
            self.lay.addRow(note)

    def value(self):
        out = {}
        for m, combo in self.combos.items():
            if combo.currentText() == OWN_ROUTE:
                kept = self.block.get(m)
                out[m] = dict(kept) if isinstance(kept, dict) and \
                    (kept.get("segment") or "own") == "own" else dict(OWN_ENTRY)
        return out or None

    def reset(self):
        for combo in self.combos.values():
            combo.setCurrentText(NUCLEAR_ROUTE)


class _PathRow(QWidget):
    """A line edit with a Browse button, for the three path kinds."""

    def __init__(self, kind, value, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.edit = QLineEdit("" if value is None else str(value))
        btn = QPushButton("Browse…")
        btn.clicked.connect(self._browse)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.edit, 1)
        lay.addWidget(btn)

    def _browse(self):
        if self.kind == "file":
            p, _ = QFileDialog.getOpenFileName(self, "Choose file",
                                               self.edit.text(),
                                               "All files (*)")
        else:
            p = QFileDialog.getExistingDirectory(self, "Choose folder",
                                                 self.edit.text())
        if p:
            self.edit.setText(p)

    def value(self):
        # Blank is "not set", like every other optional field: `_save` drops an
        # optional key that is None. Returning "" made a blank export folder an
        # empty-path error, so a study without one could never be applied.
        return self.edit.text().strip() or None


class _PatternRow(QWidget):
    """The filename pattern, with the builder behind a button.

    The builder needs real filenames to preview against, which is why it is
    offered here rather than as a bare text box: the folder is one field above.
    """

    def __init__(self, value, source_getter, parent=None, advanced=True, example=None,
                 ignore_case=False):
        super().__init__(parent)
        self.source_getter = source_getter
        self.edit = QLineEdit("" if value is None else str(value))
        # Basic Set up shows the rule as the example it reads, not as the regex:
        # the regex is the advanced view's, and the edit still holds the value.
        self.words = QLabel("")
        self.words.setWordWrap(True)
        self.edit.setVisible(advanced)
        self.words.setVisible(not advanced)
        self._show_reading(example, ignore_case)
        btn = QPushButton("Set up from an example…")
        btn.setToolTip("Type one of your file names and say which part is which; "
                       "the pattern is written for you and checked against every file")
        btn.clicked.connect(self._build)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.words, 1)
        lay.addWidget(btn)

    def _show_reading(self, example, ignore_case):
        import naming_build as B
        pattern = self.edit.text().strip()
        read = B.reading(example, pattern, ignore_case) if example and pattern else None
        if read:
            self.words.setText(f"{_esc(example)} reads as {_esc(read)}")
        elif pattern:
            self.words.setText("A rule is set. Press Set up from an example… to see or "
                               "change it, or Show advanced settings for the pattern.")
        else:
            self.words.setText("No rule yet. Press Set up from an example…")

    def _build(self):
        from naming_dialog import scan_names
        from naming_guide import GuidedNamingDialog
        names = scan_names(self.source_getter() or "")
        if not names:
            QMessageBox.warning(
                self, "No files",
                "Set the slides folder first - the pattern is checked against "
                "the filenames that are actually there.")
            return
        dlg = GuidedNamingDialog(names, self.edit.text().strip(), self)
        if dlg.exec() == QDialog.Accepted and dlg.result_block:
            self.edit.setText(dlg.result_block["pattern"])
            self.built = dlg.result_block
            self._show_reading(dlg.result_block.get("example"),
                               bool(dlg.result_block.get("case_insensitive")))

    def value(self):
        return self.edit.text().strip() or None


def _widget_for(key, value, choices=None):
    """The editor for one spec key, and a callable giving its typed value.

    `choices` overrides the spec's list for a key whose valid values depend on the
    study: `atlas_source` also offers the atlases that study registered itself.
    """
    if key.ui in ("dir", "file", "outdir"):
        row = _PathRow(key.ui, value)
        return row, row.value
    if key.ui == "bool":
        box = QCheckBox()
        box.setChecked(bool(value))
        return box, box.isChecked
    if key.ui == "choice":
        combo = QComboBox()
        offered = list(choices if choices is not None else (key.choices or []))
        combo.addItems(offered)
        if value in offered:
            combo.setCurrentText(value)
        return combo, combo.currentText
    edit = QLineEdit("" if value is None else str(value))

    if key.ui in ("number", "int"):
        def typed():
            text = edit.text().strip()
            if not text:
                return None
            try:
                return int(text) if key.ui == "int" else float(text)
            except ValueError:
                # Handed back as written, so validate() reports it against the
                # key's own doc rather than this dialog inventing a message.
                return text
        return edit, typed

    return edit, lambda: (edit.text().strip() or None)


def _set_widget(widget, value):
    """Put `value` into an editor made by `_widget_for` - the Reset button's half."""
    if isinstance(widget, _SegmentationRow):
        widget.reset()
    elif isinstance(widget, (_PathRow, _PatternRow)):
        widget.edit.setText("" if value is None else str(value))
    elif isinstance(widget, QCheckBox):
        widget.setChecked(bool(value))
    elif isinstance(widget, QComboBox):
        if value is not None:
            widget.setCurrentText(str(value))
    elif isinstance(widget, QLineEdit):
        widget.setText("" if value is None else str(value))


def _esc(text):
    return html.escape(str(text if text is not None else ""), quote=False)


def key_info(key):
    """The (i) text for one setting: the hint, the reasoning, the default, who reads it."""
    parts = [f"<p>{_esc(key.doc)}</p>"]
    if key.note:
        parts.append(f"<p>{_esc(key.note)}</p>")
    if key.default is not None:
        shown = json.dumps(key.default) if not isinstance(key.default, str) else key.default
        parts.append(f"<p style='{T.QUIET}'>Default: {_esc(shown)}</p>")
    elif key.required:
        parts.append(f"<p style='{T.QUIET}'>Required: there is no default.</p>")
    if getattr(key, "minimum", None) is not None:
        parts.append(f"<p style='{T.QUIET}'>Smallest allowed: %s</p>" % _esc(key.minimum))
    if key.consumers:
        parts.append(f"<p style='{T.QUIET}'>Read by: %s</p>" % _esc(", ".join(key.consumers)))
    parts.append(f"<p style='{T.QUIET}'>Stored as <code>{_esc(key.path)}</code></p>")
    return "".join(parts)


class SettingsForm(QWidget):
    """The settings of one study as a form: one row per key, with (i) and Reset.

    Shared by the Settings dialog (every key) and the Set up screen's cards (a
    few keys each). `keys` names the dotted keys to show, in order; None shows
    every key the spec offers. Validation always covers the whole study, so a
    card that shows four keys still cannot apply a study the pipeline would
    refuse. `advanced` False is Set up's basic view: the file name rule is shown
    as the example it reads rather than as its regex.
    """

    def __init__(self, repo_root, path=None, study_name=None, parent=None, project=None,
                 keys=None, headings=None, channel_tools=False, advanced=True):
        super().__init__(parent)
        self.repo_root = repo_root
        # With a project, this edits the project IN MEMORY: Apply changes it and
        # marks it unsaved, and File > Save is what writes. Without one it is the
        # old behaviour - validate, then write the file - which the study picker's
        # "new study" still uses.
        self.project = project
        self.detected = []                  # CZI readings made in the channel editor, kept on Apply
        self.path = os.path.abspath(path or (project.path if project and project.path else None)
                                    or LC.resolve_path())
        self.study_name = study_name
        self.channel_edits = None           # {key: value} from the channel editor
        self.channel_layout = None          # the layout those edits were made for
        self.message = None                 # the last reason Apply refused, for a caller to show

        cfg = self._load()
        if study_name:
            cfg.setdefault("study", {})["name"] = study_name

        fields = LC.dialog_fields()
        if keys is not None:
            by_path = {k.path: k for k in fields}
            fields = [by_path[k] for k in keys if k in by_path]

        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        self.rows = {}
        self.widgets = {}
        for key in fields:
            # A sub-heading inside a card that holds two groups of settings.
            if headings and key.path in headings:
                title, line = headings[key.path]
                head = QLabel(f"<b>{_esc(title)}</b><br><span style='{T.QUIET}'>"
                              f"{_esc(line)}</span>")
                head.setWordWrap(True)
                head.setContentsMargins(0, 10 if self.rows else 0, 0, 4)
                form.addRow(head)
            found, value = LC._get(cfg, key.parts)
            if key.path == "slide_naming.pattern":
                naming = cfg.get("slide_naming") or {}
                widget = _PatternRow(
                    value if found else key.default,
                    self._source_dir, advanced=advanced,
                    example=naming.get("example"),
                    ignore_case=bool(naming.get("case_insensitive")))
                getter = widget.value
                self.pattern_row = widget
            elif key.path == "atlas_source":
                # The study's own atlases are offered beside the built-in ones.
                widget, getter = _widget_for(key, value if found else key.default,
                                             self._atlas_names(cfg))
                self.atlas_combo = widget
            else:
                widget, getter = _widget_for(key, value if found else key.default)
            self.rows[key.path] = (key, getter)
            self.widgets[key.path] = widget
            line = QHBoxLayout()
            line.setContentsMargins(0, 0, 0, 0)
            line.addWidget(widget, 1)
            reset = QToolButton()
            reset.setText("↺")
            reset.setToolTip("Back to the default" if key.default is not None
                             else "Clear (this setting has no default)")
            reset.clicked.connect(lambda _=False, w=widget, k=key: _set_widget(w, k.default))
            line.addWidget(reset)
            line.addWidget(InfoButton(key.label, key_info(key)))
            holder = QWidget()
            holder.setLayout(line)
            form.addRow(key.label + ("  *" if key.required else ""), holder)
            hint = QLabel(key.doc)
            hint.setStyleSheet(T.HINT)
            hint.setWordWrap(True)
            form.addRow("", hint)
            if key.path == "acquisition.layout":
                self._add_channel_tools(form, cfg)
            if key.path == "atlas_source":
                btn = QPushButton("Import atlas...")
                btn.setToolTip("add an atlas you made elsewhere: a folder of plate "
                               "images with plates.csv and polygons.csv or seeds.csv")
                btn.clicked.connect(self._import_atlas)
                form.addRow("", btn)

        # Set up's basic view hides the layout (the slides say what it is) but
        # still offers the channel names and where each marker's nuclei are found.
        if channel_tools and "acquisition.layout" not in self.rows:
            self._add_channel_tools(form, cfg)

    def _add_channel_tools(self, form, cfg):
        cb = QPushButton("Channels and markers...")
        cb.setToolTip("name each channel, say which are markers, and choose "
                      "the colour each marker is drawn in")
        cb.clicked.connect(self._edit_channels)
        form.addRow("", cb)
        self._add_segmentation_row(form, cfg)

    def _add_segmentation_row(self, form, cfg):
        """Where each marker's nuclei are found - shown for a paired study only."""
        key = LC.BY_PATH["acquisition.marker_segmentation"]
        found, value = LC._get(cfg, key.parts)
        self.seg_row = _SegmentationRow(self._markers_now(), value if found else {})
        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.addWidget(self.seg_row, 1)
        reset = QToolButton()
        reset.setText("↺")
        reset.setToolTip("Back to the default: every marker on the counterstain")
        reset.clicked.connect(self.seg_row.reset)
        line.addWidget(reset)
        line.addWidget(InfoButton("Where nuclei are found", key_info(key)))
        holder = QWidget()
        holder.setLayout(line)
        label = QLabel("Find nuclei")
        form.addRow(label, holder)
        hint = QLabel("For a marker whose scan has no usable DAPI (the LS PCNA scans), "
                      "find its nuclei on the marker itself. Its fraction positive and "
                      "total cell density are then not reported; positive density is.")
        hint.setStyleSheet(T.HINT)
        hint.setWordWrap(True)
        form.addRow("", hint)
        self._seg_parts = (label, holder, hint)
        self.rows[key.path] = (key, self._segmentation_value)
        self.widgets[key.path] = self.seg_row
        layout_widget = self.widgets.get("acquisition.layout")
        if isinstance(layout_widget, QComboBox):
            layout_widget.currentTextChanged.connect(lambda _t: self._show_segmentation())
        self._show_segmentation()

    def _segmentation_value(self):
        # Only a paired study may carry the block; anywhere else it is dropped.
        return self.seg_row.value() if self._layout_now() == "paired" else None

    def _show_segmentation(self):
        on = self._layout_now() == "paired"
        for w in self._seg_parts:
            w.setVisible(on)

    def _markers_now(self):
        cfg = self._load()
        if self.channel_edits is not None:
            cfg = CE.apply(cfg, self.channel_edits)
        markers = (cfg.get("acquisition") or {}).get("markers") or []
        return [m for m in markers if isinstance(m, str)]

    def _source_dir(self):
        """The slides folder as it would be applied: this form's, else the study's."""
        row = self.rows.get("source_dir")
        if row:
            return row[1]()
        return self._load().get("source_dir")

    def complete(self):
        """True when every required key this form shows has a value."""
        return not self.missing()

    def missing(self):
        """The labels of the required keys this form shows that have no value."""
        return [key.label or key.path for key, get in self.rows.values()
                if key.required and get() in (None, "")]

    @staticmethod
    def _atlas_names(cfg):
        """Every atlas the study can use; the built-ins if its own block is bad."""
        try:
            return list(AR.names(cfg))
        except ValueError:
            return list(AR.BUILTIN)

    def _layout_now(self):
        row = self.rows.get("acquisition.layout")
        return (row[1]() if row else None) or CE.layout_of(self._load())

    def _edit_channels(self):
        """Open the channel table on the study as it would be saved right now."""
        layout = self._layout_now()
        cfg = self._load()
        if self.channel_edits is not None and self.channel_layout == layout:
            cfg = CE.apply(cfg, self.channel_edits)         # carry on from earlier edits
        # A paired study's scan names come from the slide headers; the editor
        # reads them itself, off the window's thread, as the folder may be slow.
        dlg = ChannelEditor(cfg, layout, self, on_detected=lambda f, ch: self.detected.append((f, ch)),
                            source_dir=self._source_dir() if layout == "paired" else None)
        if dlg.exec() == QDialog.Accepted and dlg.result_edits is not None:
            self.channel_edits, self.channel_layout = dlg.result_edits, layout
            if getattr(self, "seg_row", None) is not None:
                self.seg_row.set_markers(self._markers_now())

    def _import_atlas(self):
        """Pick a folder, have it judged, and register it as an atlas.

        All the rules live in ls_atlas_import; this only asks the questions and
        shows its answer, so what the dialog accepts is what the command line
        accepts.
        """
        if not os.path.exists(self.path):
            QMessageBox.information(
                self, "Save the study first",
                "The study has no config file yet. Save it, then import the atlas.")
            return
        if self.project and self.project.dirty:
            # The importer writes the config file itself, so unsaved changes would
            # be overwritten by it or make the next Save look like a conflict.
            QMessageBox.information(
                self, "Save the project first",
                "Importing an atlas changes the project file directly. Apply your "
                "changes here, close Settings, choose File > Save, then import the atlas.")
            return
        folder = QFileDialog.getExistingDirectory(
            self, "Folder with the atlas's plates.csv, images and polygons.csv or seeds.csv")
        if not folder:
            return
        kinds = ["polygons - each region is a closed outline",
                 "seeds - each region is marked by dots"]
        pick, ok = QInputDialog.getItem(self, "How are its regions marked?",
                                        "Region markings:", kinds, 0, False)
        if not ok:
            return
        kind = pick.split(" ", 1)[0]
        name, ok = QInputDialog.getText(
            self, "Name this atlas",
            "A short name (lower-case letters, digits and _, e.g. mouse_brain):")
        name = (name or "").strip()
        if not ok or not name:
            return
        half = QMessageBox.question(
            self, "Micrograph beside the drawing?",
            "Does each plate draw the atlas beside a micrograph of the other half "
            "(as the Wullimann plates do)?\n\nChoose No if the whole plate is the atlas.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes
        mirror = False
        if kind == "polygons":
            mirror = QMessageBox.question(
                self, "Mirror the outlines?",
                "Do the outlines cover one hemisphere only, so they can be mirrored "
                "to the other side?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes
        rep = AI.check(folder, kind, half)
        if not rep.ok:
            QMessageBox.warning(self, "This folder cannot be imported yet", rep.text())
            return
        if QMessageBox.question(
                self, "Import this atlas?",
                rep.text() + "\n\nImport it as '%s' and use it for this study?" % name,
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes) != QMessageBox.Yes:
            return
        try:
            AI.import_atlas(self.path, folder, name, kind, label=name,
                            micrograph_half=half, mirror_allowed=mirror)
        except ValueError as e:
            QMessageBox.warning(self, "Not imported", str(e))
            return
        # The file now names the atlas; refresh what this dialog offers and select it.
        if self.project:
            self.project.reload()
        names = self._atlas_names(self._load())
        self.atlas_combo.clear()
        self.atlas_combo.addItems(names)
        self.atlas_combo.setCurrentText(name)
        QMessageBox.information(
            self, "Atlas imported",
            "'%s' is registered and selected. Press Apply to keep your other changes." % name)

    def _load(self):
        """The study being edited, or the template for one that is new."""
        if self.project:
            return copy.deepcopy(self.project.cfg)
        for candidate in (self.path,
                          os.path.join(self.repo_root, "config.example.json")):
            try:
                with open(candidate, encoding="utf-8") as fh:
                    return json.load(fh)
            except (OSError, ValueError):
                continue
        return {}

    def apply(self):
        """Validate before writing. True when the settings were applied or written.

        A wrong value written here surfaces much later, inside a stage, as a
        message about a missing file rather than a wrong setting. This is the
        one place the user can act on it, so paths must exist HERE even though
        they only warn everywhere else.
        """
        cfg = self._load()
        for path, (key, getter) in self.rows.items():
            value = getter()
            if value is None and not key.required:
                # An optional key left blank is absent, not empty - export_dir
                # blank has to mean "derive it", not "write to ''".
                node, parts = cfg, key.parts
                for part in parts[:-1]:
                    node = node.get(part) if isinstance(node, dict) else None
                    if node is None:
                        break
                if isinstance(node, dict):
                    node.pop(parts[-1], None)
                continue
            LC._set(cfg, key.parts, value)

        if self.channel_edits is not None:
            if self.channel_layout != self._layout_now():
                QMessageBox.warning(
                    self, "Channels need another look",
                    "The channels were edited for the %s layout, and the layout has "
                    "since changed. Open 'Channels and markers...' again." % self.channel_layout)
                return False
            for path, value in self.channel_edits.items():
                LC._set(cfg, path.split("."), value)

        built = getattr(getattr(self, "pattern_row", None), "built", None)
        if built:
            # The builder settled the example and the case rule too; saving
            # only the regex would leave the other two describing a pattern
            # that is no longer there.
            LC._set(cfg, ["slide_naming", "example"], built["example"])
            LC._set(cfg, ["slide_naming", "case_insensitive"],
                    built["case_insensitive"])

        errors, _ = LC.validate(cfg, self.path, strict_paths=True)
        if errors:
            self.message = "\n\n".join(errors[:6])
            QMessageBox.warning(self, "Not saved yet", self.message)
            return False

        cfg = LC.apply_defaults(cfg)
        out_root = cfg.get("out_root")
        if out_root:
            os.makedirs(out_root, exist_ok=True)

        if self.project:
            try:
                self._apply_to_project(cfg)
            except Exception as exc:                        # noqa: BLE001
                QMessageBox.warning(self, "Not applied", str(exc))
                return False
            return True

        ls_jsonio.write_json_atomic(self.path, cfg)
        LC.clear_cache()
        return True

    def _touched(self):
        """Dotted keys the person actually edited here, as opposed to defaults filled in."""
        touched = [path for path in self.rows]
        if self.channel_edits:
            touched += list(self.channel_edits)
        if getattr(getattr(self, "pattern_row", None), "built", None):
            touched += ["slide_naming"]
        return touched

    def _apply_to_project(self, cfg):
        """Move the validated settings into the open project, key by key.

        Each changed key is a decision in the project's trail. Keys that only
        changed because a default was filled in are applied without being
        recorded: they are the program's doing, not the person's.
        """
        touched = self._touched()

        def mine(key):
            return any(key == t or key.startswith(t + ".") or t.startswith(key + ".")
                       for t in touched)

        for op, key, value in _diff(self.project.cfg, cfg):
            if key == "project" or key.startswith("project."):
                continue
            if op == "set":
                self.project.set(key, value, by="settings", record=mine(key))
            else:
                self.project.unset(key, by="settings", record=mine(key))
        for file, channels in self.detected:
            self.project.note_detected(file, channels)
        self.detected = []



class ConfigDialog(QDialog):
    """Edit one study's config. `path` names it; default is the active one.

    A frame around a `SettingsForm` showing every key. `rows`, `detected` and
    `project` are the form's, so a caller can reach them as before.
    """

    def __init__(self, repo_root, path=None, study_name=None, parent=None, project=None):
        super().__init__(parent)
        self.form = SettingsForm(repo_root, path=path, study_name=study_name, parent=self,
                                 project=project)
        self.project = project
        self.path = self.form.path
        self.rows = self.form.rows
        self.detected = self.form.detected
        self.setWindowTitle(f"Settings — {os.path.basename(self.path)}")
        self.resize(760, 620)

        scroll = QScrollArea()
        scroll.setWidget(self.form)
        scroll.setWidgetResizable(True)

        buttons = QDialogButtonBox((QDialogButtonBox.Apply if self.project else QDialogButtonBox.Save)
                                   | QDialogButtonBox.Cancel)
        self.ok_button = buttons.button(QDialogButtonBox.Apply if self.project else QDialogButtonBox.Save)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        if self.project:                    # Apply is not an AcceptRole button
            self.ok_button.clicked.connect(self._save)

        note = QLabel("Changes apply to the open project and are saved with it, or "
                      "automatically before a stage runs." if self.project
                      else f"Writing to {self.path}")
        note.setStyleSheet(T.HINT)
        note.setWordWrap(True)

        lay = QVBoxLayout(self)
        lay.addWidget(scroll, 1)
        lay.addWidget(note)
        lay.addWidget(buttons)

    def __getattr__(self, name):
        # Anything else the dialog used to own (pattern_row, atlas_combo,
        # channel_edits, ...) now lives on the form; reach it there.
        form = self.__dict__.get("form")
        if form is None:
            raise AttributeError(name)
        return getattr(form, name)

    def _save(self):
        # The form's lists are replaced, not mutated, in a few places; keep the
        # caller's view of them in step before applying.
        self.form.detected = self.detected
        if self.form.apply():
            self.accept()


def _canon(v):
    return json.dumps(v, sort_keys=True, ensure_ascii=False)


def _diff(old, new, prefix=""):
    """[(op, dotted key, value)] turning dict `old` into dict `new`.

    Recurses into blocks so a decision names the setting that changed, not the
    whole block it sits in. A block with a "." in any key (a marker called
    "Ki.67" is legal) is replaced whole, because a dotted path could not say
    which dot is the separator.
    """
    out = []
    for k, v in new.items():
        key = prefix + k
        if k not in old:
            out.append(("set", key, v))
        elif isinstance(old[k], dict) and isinstance(v, dict):
            if any("." in str(x) for x in list(old[k]) + list(v)):
                if _canon(old[k]) != _canon(v):
                    out.append(("set", key, v))
            else:
                out += _diff(old[k], v, key + ".")
        elif _canon(old[k]) != _canon(v):
            out.append(("set", key, v))
    for k in old:
        # A null that the form leaves out is not a change: the template ships
        # optional settings as null, and Apply with nothing edited must be a no-op.
        if k not in new and old[k] is not None:
            out.append(("unset", prefix + k, None))
    return out
