"""Add an atlas whose regions were drawn in Fiji's ROI Manager.

This is a frame around `scripts/ls_atlas_imagej.py`, which does all the work:
it reads each plate image's ROI Manager .zip, writes a plate set, checks it and
registers it with the study. The dialog only collects the four things that
command takes and shows what it printed, so the app and the command line cannot
disagree about what counts as a good atlas.

It runs in this process, as the stages do (see runner.py): the frozen build has
no separate Python to start.

The command writes the study's config itself, to register the new atlas and
select it. So the project is saved first, and read back from disk afterwards;
otherwise the app's copy would overwrite the new atlas on the next save.
"""

import contextlib
import importlib.util
import io
import os
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                               QPushButton, QVBoxLayout)

import theme as T
from widgets import QUIET

#: The walk-through for drawing the regions in Fiji.
GUIDE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "docs", "adding-your-own-atlas.md")

NAME_RE = r"[a-z0-9_]+"


def available():
    """True when this copy of the pipeline has the Fiji importer."""
    try:
        return importlib.util.find_spec("ls_atlas_imagej") is not None
    except (ImportError, ValueError):
        return False


def argv_for(folder, name, label="", mirror=False, config=None):
    """The command line ls_atlas_imagej is run with."""
    argv = [folder, "--name", name]
    if label:
        argv += ["--label", label]
    if mirror:
        argv.append("--mirror")
    if config:
        argv += ["--config", config]
    return argv


def run_import(argv):
    """(exit code, printed lines) from ls_atlas_imagej.main(argv)."""
    import ls_atlas_imagej as IJ
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            code = IJ.main(argv)
        except SystemExit as exc:          # argparse refusing an argument
            code = exc.code if isinstance(exc.code, int) else 1
        except Exception as exc:           # noqa: BLE001 - shown, not raised
            print("could not build the atlas: %s" % exc)
            code = 1
    return code or 0, [ln for ln in out.getvalue().splitlines() if ln.strip()]


class FijiAtlasDialog(QDialog):
    """Pick the folder, name the atlas, build and import it."""

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.imported = False
        self.setWindowTitle("Add atlas from Fiji")
        self.setMinimumWidth(560)

        lay = QVBoxLayout(self)
        intro = QLabel(
            "Choose a folder with one image per atlas plate and, beside each image, "
            "the ROI Manager .zip with its regions drawn and named in Fiji. The atlas "
            "is checked, added to this study and selected.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        guide = QLabel(
            "How to draw the regions: <a href='%s'>Adding your own atlas</a>."
            % _file_url(GUIDE) if os.path.isfile(GUIDE) else
            "How to draw the regions: docs/adding-your-own-atlas.md in the pipeline folder.")
        guide.setOpenExternalLinks(True)
        guide.setTextFormat(Qt.RichText)
        guide.setStyleSheet(QUIET)
        lay.addWidget(guide)

        form = QFormLayout()
        row = QHBoxLayout()
        self.folder = QLineEdit()
        self.folder.setPlaceholderText("folder of plate images and their .zip files")
        pick = QPushButton("Choose…")
        pick.clicked.connect(self._choose)
        row.addWidget(self.folder, 1)
        row.addWidget(pick)
        form.addRow("Folder", row)
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. zebrafish_adult")
        self.name.setValidator(QRegularExpressionValidator(NAME_RE))
        self.name.setToolTip("Lower-case letters, digits and _. Stored with the study.")
        form.addRow("Short name", self.name)
        self.label = QLineEdit()
        self.label.setPlaceholderText("optional, e.g. Adult zebrafish (Wullimann 1996)")
        form.addRow("Name in menus", self.label)
        self.mirror = QCheckBox("The regions cover one side; they may be mirrored across "
                                "the midline")
        form.addRow("", self.mirror)
        lay.addLayout(form)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("What the check finds is shown here.")
        self.output.setMinimumHeight(140)
        lay.addWidget(self.output, 1)

        buttons = QDialogButtonBox()
        self.go = buttons.addButton("Build and add", QDialogButtonBox.AcceptRole)
        T.mark(self.go, kind="primary")
        buttons.addButton(QDialogButtonBox.Close)
        self.go.clicked.connect(self.build)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.folder.textChanged.connect(self._ready)
        self.name.textChanged.connect(self._ready)
        self._ready()

    def _choose(self):
        path = QFileDialog.getExistingDirectory(self, "Folder of plate images",
                                                self.folder.text() or os.path.expanduser("~"))
        if path:
            self.folder.setText(path)

    def _ready(self):
        self.go.setEnabled(bool(self.folder.text().strip())
                           and bool(re.fullmatch(NAME_RE, self.name.text())))

    def show_lines(self, lines):
        self.output.setPlainText("\n".join(lines))

    def build(self):
        """Run the importer; on success read the project back from disk."""
        if not self.session.ensure_saved_for_run(self, auto=True):
            self.show_lines(["Not run: the project could not be saved first."])
            return False
        path = self.session.project.path
        code, lines = run_import(argv_for(self.folder.text().strip(), self.name.text(),
                                          self.label.text().strip(), self.mirror.isChecked(),
                                          path))
        self.show_lines(lines or ["(nothing was printed)"])
        if code != 0:
            return False
        import ls_project as LP
        self.session.set_project(LP.Project.open(path))
        self.imported = True
        self.go.setEnabled(False)
        return True


def _file_url(path):
    from PySide6.QtCore import QUrl
    return QUrl.fromLocalFile(path).toString()
