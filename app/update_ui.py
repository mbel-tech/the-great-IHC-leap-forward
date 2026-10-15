"""The window side of updates: the Help menu, the bar that says one is ready, the dialog.

updates.py decides and downloads; update_apply.py swaps after the app closes.
This file only asks, shows and hands over. Running from source (no VERSION.json
beside runtime/) there is nothing to update in place, and the menu says to use
git instead.
"""

import os

from PySide6.QtCore import QObject, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel,
    QMessageBox, QProgressDialog, QPushButton, QTextBrowser, QVBoxLayout,
)

import theme as T
import updates as UP

TITLE = "Updates"


class _Job(QObject):
    """Runs one function off the window's thread and reports back."""

    progress = Signal(str, int, int)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn(self.progress.emit))
        except UP.UpdateError as e:
            self.failed.emit(str(e))
        except Exception as e:                              # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {e}")


class UpdateDialog(QDialog):
    """What the new version brings, and Update / Later (or Download installer / Later)."""

    def __init__(self, decision, installed, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Version {decision.version}")
        self.resize(560, 420)
        lay = QVBoxLayout(self)
        if decision.kind == "code":
            head = (f"<b>Version {decision.version} is ready to install.</b> You have "
                    f"{installed}. The app downloads it, closes, swaps it in and starts "
                    f"again. Your studies and results are not touched, and "
                    f"Help &gt; Undo last update brings this version back.")
        else:
            head = (f"<b>Version {decision.version} is out.</b> You have {installed}. "
                    f"It changes the libraries the app runs on, so it needs the full "
                    f"installer: download it and run it. It upgrades this copy in place; "
                    f"your studies and results are not touched.")
        label = QLabel(head)
        label.setWordWrap(True)
        lay.addWidget(label)
        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setMarkdown(decision.notes or "_No release notes._")
        lay.addWidget(notes, 1)
        box = QDialogButtonBox()
        go = box.addButton("Update now" if decision.kind == "code" else "Download installer",
                           QDialogButtonBox.AcceptRole)
        go.setDefault(True)
        box.addButton("Later", QDialogButtonBox.RejectRole)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)


class Updater(QObject):
    """Owns the Help menu, the update bar and the background check for one window."""

    def __init__(self, window, root=None):
        super().__init__(window)
        self.window = window
        self.root = root
        self.decision = None
        self._threads = []
        self._build_banner()

    # ---- what the window shows -------------------------------------------

    def _build_banner(self):
        self.banner = QFrame()
        self.banner.setStyleSheet(
            f"QFrame{{background:{T.ACCENT_BG};border-bottom:1px solid {T.ACCENT_LINE}}}"
            f"QLabel{{border:none;background:transparent}}")
        lay = QHBoxLayout(self.banner)
        lay.setContentsMargins(12, 6, 12, 6)
        self.banner_text = QLabel("")
        lay.addWidget(self.banner_text, 1)
        self.banner_button = QPushButton("Update now…")
        self.banner_button.clicked.connect(self.open_dialog)
        lay.addWidget(self.banner_button)
        hide = QPushButton("Later")
        hide.setFlat(True)
        hide.clicked.connect(self.banner.hide)
        lay.addWidget(hide)
        self.banner.hide()

    def build_menu(self, menubar):
        self.menu = menubar.addMenu("&Help")
        self.check_action = self.menu.addAction("Check for updates…")
        self.check_action.triggered.connect(self.check_now)
        self.undo_action = self.menu.addAction("Undo last update")
        self.undo_action.triggered.connect(self.undo)
        self.menu.addSeparator()
        self.about_action = self.menu.addAction("About…")
        self.about_action.triggered.connect(self.about)
        self.menu.aboutToShow.connect(self._refresh_menu)
        self._refresh_menu()
        return self.menu

    def _refresh_menu(self):
        self.undo_action.setEnabled(bool(self.root) and UP.can_undo(self.root))

    def _say(self, title, text):
        QMessageBox.information(self.window, title, text)

    def _installed(self):
        return (UP.installed_info(self.root) or {}).get("version", "?") if self.root else "source"

    # ---- checking ---------------------------------------------------------

    def _run(self, fn, done, failed, progress=None):
        thread, job = QThread(self), _Job(fn)
        job.moveToThread(thread)
        thread.started.connect(job.run)
        job.done.connect(done)
        job.failed.connect(failed)
        if progress:
            job.progress.connect(progress)
        for sig in (job.done, job.failed):
            sig.connect(thread.quit)
        thread.finished.connect(lambda: self._threads.remove((thread, job)))
        self._threads.append((thread, job))
        thread.start()

    def check_in_background(self):
        """On start: one quiet request; offline or any error shows nothing."""
        if self.root:
            self._run(lambda _p: UP.check(self.root), self.show_decision, lambda _e: None)

    def check_now(self):
        if not self.root:
            self._say(TITLE, "This copy runs from the source folder, not an install, "
                             "so it does not update itself. Update it with git pull.")
            return
        self.check_action.setEnabled(False)

        def done(d):
            self.check_action.setEnabled(True)
            self.show_decision(d, quiet=False)

        def failed(err):
            self.check_action.setEnabled(True)
            self._say(TITLE, f"Could not check for updates.\n\n{err}")

        self._run(lambda _p: UP.check(self.root, timeout=15), done, failed)

    def show_decision(self, decision, quiet=True):
        self.decision = decision
        if decision.kind == "current":
            self.banner.hide()
            if not quiet:
                self._say(TITLE, f"You have the newest version ({self._installed()}).")
            return
        if decision.kind == "code":
            self.banner_text.setText(f"Version {decision.version} is ready to install.")
            self.banner_button.setText("Update now…")
        else:
            self.banner_text.setText(f"Version {decision.version} is out. "
                                     f"It needs the full installer this time.")
            self.banner_button.setText("Details…")
        self.banner.show()
        if not quiet:
            self.open_dialog()

    # ---- updating ---------------------------------------------------------

    def open_dialog(self):
        d = self.decision
        if not d or d.kind == "current":
            return
        if UpdateDialog(d, self._installed(), self.window).exec() != QDialog.Accepted:
            return
        if d.kind == "installer":
            QDesktopServices.openUrl(QUrl(d.installer_url or d.page_url))
            return
        if not self._ready_to_close("update"):
            return
        self._stage(d)

    def _ready_to_close(self, verb):
        if getattr(self.window, "_thread", None) is not None:
            self._say(TITLE, f"A step is running. Wait for it to finish (or stop it), "
                             f"then {verb} again.")
            return False
        session = getattr(self.window, "session", None)
        return session is None or session.confirm_discard(self.window, verb)

    def _stage(self, decision):
        total = sum(x.get("size") or 0 for x in decision.downloads)
        dlg = QProgressDialog(f"Downloading version {decision.version}…", None, 0, 1000,
                              self.window)
        dlg.setWindowTitle(TITLE)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setValue(0)
        got = {}

        def progress(name, done, _size):
            got[name] = done
            if total:
                dlg.setValue(min(999, int(1000 * sum(got.values()) / total)))

        def done(new_dir):
            dlg.close()
            self._hand_over(new_dir, "apply")

        def failed(err):
            dlg.close()
            self._say(TITLE, f"The update could not be downloaded. Nothing was changed.\n\n{err}")

        self._run(lambda p: UP.stage(self.root, decision, p), done, failed, progress)

    def _hand_over(self, new_dir, mode):
        try:
            UP.launch_apply(self.root, new_dir, mode)
        except OSError as e:
            self._say(TITLE, f"Could not start the updater. Nothing was changed.\n\n{e}")
            return
        server = getattr(self.window, "server", None)
        if server is not None:
            server.stop()
        # exit(), not quit() or close(): the person already answered the
        # unsaved-changes question, and close() would ask it again.
        QApplication.exit(0)

    def undo(self):
        if not (self.root and UP.can_undo(self.root)):
            return
        prev = UP.installed_info(os.path.join(UP.updates_dir(self.root), "previous"))
        ans = QMessageBox.question(
            self.window, TITLE,
            f"Go back from {self._installed()} to {prev['version']}? "
            f"The app closes and starts again.")
        if ans == QMessageBox.Yes and self._ready_to_close("undo the update"):
            self._hand_over(None, "undo")

    def report_last_result(self):
        """Once, on the first start after an update or an undo."""
        text = self.root and UP.last_result(self.root)
        if text:
            log = getattr(self.window, "_log", None)
            if log:
                log(f"update: {text}")
            self._say(TITLE, text)

    def about(self):
        where = self.root or "the source folder"
        self._say("About", f"The Great IHC Leap Forward\nVersion {self._installed()}\n\n"
                           f"Installed in {where}")
