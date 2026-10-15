"""A new study: two folders, the section thickness and the atlas. Nothing else.

The slides folder is read straight away (headers only), so the pixel size, how
the files are named and how the markers were scanned are filled in from the
files rather than asked. Everything the study makes goes in the output folder,
including the study file itself (study.json), so the whole study is one folder.

What cannot be read from the files is asked: how thick the sections were cut
(a CZI is one plane of a physical section; it does not know), and which atlas
the regions come from. The rest is under Set up > Show advanced settings.
"""

import os

from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QRadioButton, QVBoxLayout, QWidget)

import atlas_registry as AR
import autodetect as AD
import ls_project as LP
import slide_read as SRD
import theme as T

ATLASES = (
    (AR.SALMON, "Salmon (Vindas), ready-made plates ship with the app"),
    (AR.WULLIMANN, "Zebrafish, Wullimann 1996, ready-made plates ship with the app"),
    (AD.OWN_ATLAS, "My own atlas: add it in Set up > Atlas afterwards"),
)


class _Folder(QWidget):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.title = title
        self.edit = QLineEdit()
        btn = QPushButton("Choose…")
        btn.clicked.connect(self._browse)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.edit, 1)
        lay.addWidget(btn)

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, self.title, self.edit.text())
        if path:
            self.edit.setText(path)
            self.edit.editingFinished.emit()

    def value(self):
        return self.edit.text().strip()


class NewStudyDialog(QDialog):
    """Ask for the two folders; `path` is the study file afterwards, `opened` if it existed."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New study")
        self.resize(700, 600)
        self.setMinimumHeight(560)
        self.path = None
        self.project = None
        self.opened = False
        self.detection = None
        self._read_for = None
        # Read on a worker thread: a slides folder on a USB stick or OneDrive
        # can take minutes, and the dialog must not freeze meanwhile. Create
        # pressed before the read lands goes ahead once it does.
        self.slide_read = SRD.SlideRead(self)
        self.slide_read.progress.connect(lambda k, n: self.found.setText(SRD.words(k, n)))
        self.slide_read.finished.connect(self._slides_read)
        self.slide_read.failed.connect(self._read_failed)
        self._create_when_read = False

        self.slides = _Folder("Folder with the slide files (.czi)")
        self.slides.edit.editingFinished.connect(self._read_slides)
        self.found = QLabel("")
        self.found.setWordWrap(True)
        self.found.setStyleSheet(T.HINT)
        self.output = _Folder("Folder for everything this study makes")
        self.output.edit.editingFinished.connect(self._check)
        self.output.edit.textChanged.connect(lambda _t: self._check())
        out_note = QLabel("The study's settings file, your curation, the pictures and the "
                          "results all go here, so the whole study is one folder.")
        out_note.setWordWrap(True)
        out_note.setStyleSheet(T.HINT)

        self.thickness = QDoubleSpinBox()
        self.thickness.setRange(0, 500)
        self.thickness.setDecimals(1)
        self.thickness.setSuffix(" µm")
        self.thickness.setSpecialValueText("not set")
        self.thickness.valueChanged.connect(lambda _v: self._check())
        thick_note = QLabel("How thick the sections were cut. The slide files do not record "
                            "it, and nuclei counts are corrected with it.")
        thick_note.setWordWrap(True)
        thick_note.setStyleSheet(T.HINT)

        self.atlas_group = QButtonGroup(self)
        atlas_box = QWidget()
        al = QVBoxLayout(atlas_box)
        al.setContentsMargins(0, 0, 0, 0)
        for i, (value, words) in enumerate(ATLASES):
            rb = QRadioButton(words)
            rb.setProperty("atlas", value)
            rb.setChecked(i == 0)
            self.atlas_group.addButton(rb)
            al.addWidget(rb)

        self.name = QLineEdit()
        self.name.setPlaceholderText("the output folder's name")

        form = QFormLayout()
        form.addRow("<b>Slides folder</b>", self.slides)
        form.addRow("", self.found)
        form.addRow("<b>Output folder</b>", self.output)
        form.addRow("", out_note)
        form.addRow("Section thickness", self.thickness)
        form.addRow("", thick_note)
        form.addRow("Atlas", atlas_box)
        form.addRow("Name (optional)", self.name)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok = self.buttons.button(QDialogButtonBox.Ok)
        self.buttons.accepted.connect(self._create)
        self.buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lead = QLabel("Choose where the slides are and where the study's output goes. "
                      "Pixel size, file names and channels are read from the slides.")
        lead.setWordWrap(True)
        lay.addWidget(lead)
        lay.addLayout(form)
        lay.addStretch(1)
        lay.addWidget(self.status)
        lay.addWidget(self.buttons)
        self._check()

    # ------------------------------------------------------------------
    def _atlas(self):
        btn = self.atlas_group.checkedButton()
        return btn.property("atlas") if btn else AR.SALMON

    def _read_slides(self):
        src = self.slides.value()
        if src == self._read_for:
            return
        self._read_for = src
        self.detection = None
        if not src or not os.path.isdir(src):
            self.slide_read.cancel()        # a read of the folder before is stale now
            self._create_when_read = False
            self.found.setText("That folder does not exist." if src else "")
            self._check()
            return
        # Replaces any read of an earlier folder still running; that one's
        # result is dropped, so it cannot land on top of this one.
        self.slide_read.start(src)
        self.found.setText("Reading slides…")
        self._check()

    def _slides_read(self, src, found):
        if src != self._read_for:
            return
        self.detection = found
        self.found.setText("<br>".join(found.lines()) if found is not None
                           else "That folder does not exist.")
        self._check()
        if self._create_when_read:
            self._create_when_read = False
            if self.ok.isEnabled():         # what the files said may still need a look
                self._create()

    def _read_failed(self, src, why):
        if src != self._read_for:
            return
        self._read_for = None               # so the next try reads it again
        self._create_when_read = False
        self.found.setText("Could not read the slides: " + why)
        self._check()

    def done(self, result):
        self.slide_read.cancel()            # closed mid-read: stop reading
        super().done(result)

    def _existing(self):
        out = self.output.value()
        return out and os.path.isfile(AD.study_file(out))

    def _check(self):
        src, out = self.slides.value(), self.output.value()
        if self._existing():
            self.ok.setText("Open the study in this folder")
            self.ok.setEnabled(True)
            self.status.setText("This output folder already holds a study.")
            return
        self.ok.setText("Create study")
        why = None
        if not src:
            why = "Choose the slides folder."
        elif not os.path.isdir(src):
            why = "The slides folder does not exist."
        elif self.slide_read.busy:
            why = "Reading the slides…"
        elif self.detection is not None and not self.detection.files:
            why = "There are no .czi files in the slides folder."
        elif not out:
            why = "Choose the output folder."
        elif os.path.normcase(os.path.abspath(out)) == os.path.normcase(os.path.abspath(src)):
            why = "Keep the output apart from the slides: choose another output folder."
        elif self.thickness.value() <= 0:
            why = "Enter the section thickness."
        self.status.setText(why or "")
        self.status.setStyleSheet(f"color:{T.WARN}" if why else "")
        self.ok.setEnabled(why is None)

    def _go_ahead(self):
        """True to make the study: the read found nothing wrong, or the person said so.

        Files that could not be read, no pixel size, names the rule misses: each
        is listed under the folder, but Create used to go ahead without a word,
        and the study then started with files every stage ignores. It is still
        the person's call - those can be fixed in Set up - so this asks rather
        than refuses.
        """
        problems = list(getattr(self.detection, "problems", None) or [])
        if not problems:
            return True
        answer = QMessageBox.question(
            self, "Create the study anyway?",
            "Reading the slides found things to look at:\n\n"
            + "\n\n".join("• " + p for p in problems)
            + "\n\nCreate the study anyway? You can fix these afterwards in Set up. "
              "No goes back to change the folders.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return answer == QMessageBox.Yes

    def _create(self):
        out = self.output.value()
        if self._existing():
            self.path = AD.study_file(out)
            self.opened = True
            self.accept()
            return
        self._read_slides()
        if self.slide_read.busy:
            self._create_when_read = True   # _slides_read comes back here
            self.ok.setEnabled(False)
            return
        if not self._go_ahead():
            return
        try:
            os.makedirs(out, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "Output folder", "Could not make %s: %s" % (out, exc))
            return
        project, found = AD.new_project(self.slides.value(), out, self.thickness.value(),
                                        self._atlas(), self.name.text(), self.detection)
        errors, _ = project.validate()
        if errors:
            QMessageBox.warning(self, "Not made yet",
                                "These settings need a look first:\n\n" + "\n\n".join(errors[:5]))
            return
        try:
            project.save_as(AD.study_file(out))
        except LP.ProjectError as exc:
            QMessageBox.warning(self, "Not saved", str(exc))
            return
        AD.write_report(out, found)
        self.project, self.detection = project, found
        self.path = project.path
        self.accept()
