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
import json
import os

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton,
    QScrollArea, QVBoxLayout, QWidget)

import atlas_registry as AR
import ls_channel_edit as CE
import ls_atlas_import as AI
import ls_config as LC
import ls_jsonio
from channel_editor import ChannelEditor


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

    def __init__(self, value, source_getter, parent=None):
        super().__init__(parent)
        self.source_getter = source_getter
        self.edit = QLineEdit("" if value is None else str(value))
        btn = QPushButton("Build…")
        btn.clicked.connect(self._build)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.edit, 1)
        lay.addWidget(btn)

    def _build(self):
        from naming_dialog import NamingDialog, scan_names
        names = scan_names(self.source_getter() or "")
        if not names:
            QMessageBox.warning(
                self, "No files",
                "Set the slides folder first - the pattern is checked against "
                "the filenames that are actually there.")
            return
        dlg = NamingDialog(names, self.edit.text().strip(), self)
        if dlg.exec() == QDialog.Accepted and dlg.result_block:
            self.edit.setText(dlg.result_block["pattern"])
            self.built = dlg.result_block

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


class ConfigDialog(QDialog):
    """Edit one study's config. `path` names it; default is the active one."""

    def __init__(self, repo_root, path=None, study_name=None, parent=None, project=None):
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
        self.setWindowTitle(f"Settings — {os.path.basename(self.path)}")
        self.resize(760, 620)

        cfg = self._load()
        if study_name:
            cfg.setdefault("study", {})["name"] = study_name

        form = QFormLayout()
        self.rows = {}
        for key in LC.dialog_fields():
            found, value = LC._get(cfg, key.parts)
            if key.path == "slide_naming.pattern":
                widget = _PatternRow(
                    value if found else key.default,
                    lambda: self.rows["source_dir"][1]())
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
            form.addRow(key.label + ("  *" if key.required else ""), widget)
            hint = QLabel(key.doc)
            hint.setStyleSheet("color:#9aa0a8;font-size:11px")
            hint.setWordWrap(True)
            form.addRow("", hint)
            if key.path == "acquisition.layout":
                cb = QPushButton("Channels and markers...")
                cb.setToolTip("name each channel, say which are markers, and choose "
                              "the colour each marker is drawn in")
                cb.clicked.connect(self._edit_channels)
                form.addRow("", cb)
            if key.path == "atlas_source":
                btn = QPushButton("Import atlas...")
                btn.setToolTip("add an atlas you made elsewhere: a folder of plate "
                               "images with plates.csv and polygons.csv or seeds.csv")
                btn.clicked.connect(self._import_atlas)
                form.addRow("", btn)

        inner = QWidget()
        inner.setLayout(form)
        scroll = QScrollArea()
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)

        buttons = QDialogButtonBox((QDialogButtonBox.Apply if self.project else QDialogButtonBox.Save)
                                   | QDialogButtonBox.Cancel)
        self.ok_button = buttons.button(QDialogButtonBox.Apply if self.project else QDialogButtonBox.Save)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        if self.project:                    # Apply is not an AcceptRole button
            self.ok_button.clicked.connect(self._save)

        note = QLabel("Changes apply to the open project and are written when you choose "
                      "File > Save." if self.project else f"Writing to {self.path}")
        note.setStyleSheet("color:#9aa0a8;font-size:11px")
        note.setWordWrap(True)

        lay = QVBoxLayout(self)
        lay.addWidget(scroll, 1)
        lay.addWidget(note)
        lay.addWidget(buttons)

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
        dlg = ChannelEditor(cfg, layout, self, on_detected=lambda f, ch: self.detected.append((f, ch)))
        if dlg.exec() == QDialog.Accepted and dlg.result_edits is not None:
            self.channel_edits, self.channel_layout = dlg.result_edits, layout

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
            "'%s' is registered and selected. Press Save to keep your other changes." % name)

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

    def _save(self):
        """Validate before writing.

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
                return
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
            QMessageBox.warning(self, "Not saved yet",
                                "\n\n".join(errors[:6]))
            return

        cfg = LC.apply_defaults(cfg)
        out_root = cfg.get("out_root")
        if out_root:
            os.makedirs(out_root, exist_ok=True)

        if self.project:
            try:
                self._apply_to_project(cfg)
            except Exception as exc:                        # noqa: BLE001
                QMessageBox.warning(self, "Not applied", str(exc))
                return
            self.accept()
            return

        ls_jsonio.write_json_atomic(self.path, cfg)
        LC.clear_cache()
        self.accept()

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
