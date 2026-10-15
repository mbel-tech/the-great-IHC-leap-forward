"""Host the existing HTML curators inside the app window.

The curators are working tools with their own tested behaviour - guided mode,
autosave, undo, the numbered seed order. Nothing here reimplements any of that.
This is a frame around them, and it exists to solve exactly two problems that
appear the moment a page moves from a browser into an embedded view.

**Serve over http, never file://.** A `file://` page cannot read its own images
back: every local image taints the canvas, so `toBlob()` throws and `fetch()` is
refused. It also cannot reach the plate images at all, since they are referenced
as `../atlas/<set>/...`, above the page's own directory. Serving `out_root` as
the document root makes both go away and costs one thread.

*What this is NOT still working around.* An early version split the DAPI channel
with `getImageData`, whose SecurityError killed `drawSec` partway and left two
empty panels; that was fixed in the page itself, by compositing instead of
reading pixels back, and the curator has not called `getImageData` since. So
from disk the page is fully usable for landmarking, ROI placement, rotation, the
filters and all three CSV exports. The one capability that genuinely needs http
is the **Shotgun deck**, which has to read bitmaps back to build a .pptx - the
page checks `location.protocol` and disables the button with a reason rather
than failing at the click.

`scripts/serve_curators.py` is this same server without the app, for a browser
that wants the same terms.

**Catch the download.** Export builds a Blob and clicks an `<a download>`. A
browser saves it; QtWebEngine raises `downloadRequested` and, with nothing
attached, silently discards it. Export would appear to do nothing at all.
"""

import functools
import http.server
import json
import os
import re
import socketserver
import threading

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import (
    QWebEngineDownloadRequest, QWebEngineScript, QWebEngineProfile)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QLabel, QMessageBox, QPushButton, QVBoxLayout, QHBoxLayout, QWidget)

import state as ST
import theme as T

HERE = os.path.dirname(os.path.abspath(__file__))

# A curator export is named `<what>_DD.MM.YYYY_HH.MM.<ext>`. Matched strictly -
# a loose pattern would pull the date out of an ordinary filename and file it
# somewhere the operator never asked for.
_STAMP_RE = re.compile(r"_(\d{2}\.\d{2}\.\d{4}_\d{2}\.\d{2})(?:\.[A-Za-z0-9]+)?$")


def _export_stamp(name):
    """The DD.MM.YYYY_HH.MM stamp a curator export carries, or None."""
    m = _STAMP_RE.search(name)
    return m.group(1) if m else None


def _export_root(out_root):
    """Where stamped exports are filed. `config.export_dir`, else out_root/exports.

    This used to find config.json by guessing its own location - beside the app
    from source, beside the executable when frozen - and ignored LS_CONFIG
    entirely. That was merely fragile while there was one config at a known
    path; with named studies it is wrong, because the active study is not
    beside the app at all. It asks ls_config now, which is the same answer 04l
    and 05a get, so the page and the handler that catches its downloads cannot
    disagree about where an export went.
    """
    try:
        import ls_config as LC
        return LC.export_dir()
    except BaseException:                                   # noqa: BLE001
        # A download must still land somewhere if config cannot be read at all.
        return os.path.join(out_root, "exports")


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """Static files, without logging every tile request to the console."""

    def log_message(self, fmt, *args):
        pass


class LocalServer:
    """Serve out_root on an ephemeral loopback port for the lifetime of the app."""

    def __init__(self, root):
        self.root = root
        handler = functools.partial(_QuietHandler, directory=root)
        # Port 0 lets the OS pick, so two copies of the app do not collide.
        self._srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
        self._srv.daemon_threads = True
        self.port = self._srv.server_address[1]
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()

    def url_for(self, rel):
        return QUrl(f"http://127.0.0.1:{self.port}/{rel.replace(os.sep, '/')}")

    def stop(self):
        try:
            self._srv.shutdown()
            self._srv.server_close()
        except Exception:                                   # noqa: BLE001
            pass


class CuratorView(QWidget):
    """One curator page, with a reload and a note of where exports land."""

    logged = Signal(str)
    #: The full path of each export that finished downloading.
    exported = Signal(str)
    #: The Done button was pressed.
    done_requested = Signal()

    def __init__(self, server, out_root, parent=None, project_source=None):
        super().__init__(parent)
        self.server = server
        self.out_root = out_root
        self.rel = None
        # `project_source` returns the open project, so curation is saved into the
        # project file rather than only into loose files.
        self.store = ST.CurationStore(out_root, project_source)

        self.view = QWebEngineView(self)
        self.title = QLabel("", self)
        self.title.setStyleSheet(f"{T.QUIET};padding:2px 6px")

        reload_btn = QPushButton("Reload", self)
        reload_btn.clicked.connect(self.view.reload)
        browser_btn = QPushButton("Open in browser", self)
        browser_btn.clicked.connect(self._open_external)
        # The page is one screen of controls above two canvases. Inside the app
        # window on a laptop it gets about 840 x 320 px, which its header alone
        # fills, so the canvases vanish and the last row of buttons is cut off.
        # Its own maximised window gives it the whole screen.
        self.window_btn = QPushButton("Own window", self)
        self.window_btn.setCheckable(True)
        self.window_btn.setToolTip("Open the curator in its own full-size window; "
                                   "close that window to put it back here")
        self.window_btn.toggled.connect(self._own_window)
        self._home = None
        # Shown only for a curator with a next phase (set_done). It sits in this
        # bar rather than on the screen around it so it moves with the page into
        # its own window.
        self.done_btn = QPushButton("", self)
        T.mark(self.done_btn, kind="primary")
        self.done_btn.setVisible(False)
        self.done_btn.clicked.connect(self.done_requested.emit)

        # A curation save that failed. It stays up until a save succeeds, in this widget so it
        # goes with the page into its own window: the operator is looking at the page, not at
        # the log drawer, and every decision made while it shows is in the page only.
        self.save_warning = QLabel("", self)
        self.save_warning.setWordWrap(True)
        self.save_warning.setStyleSheet(
            f"background:{T.DANGER};color:#ffffff;font-weight:600;padding:6px 8px")
        self.save_warning.setVisible(False)
        self._save_failing = False

        bar = QHBoxLayout()
        bar.addWidget(self.title, 1)
        bar.addWidget(self.done_btn)
        bar.addWidget(self.window_btn)
        bar.addWidget(reload_btn)
        bar.addWidget(browser_btn)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(bar)
        lay.addWidget(self.save_warning)
        lay.addWidget(self.view, 1)

        prof = self.view.page().profile()
        prof.downloadRequested.connect(self._on_download)
        self._install_state_bridge()

    # ---- own window -------------------------------------------------------

    def _own_window(self, on):
        """Move this view into a maximised window of its own, or back home.

        The same widget moves, not a copy, so the page, its unsaved state and
        the bridge that saves it all stay as they are.
        """
        if on and self._home is None:
            parent = self.parentWidget()
            layout = parent.layout() if parent is not None else None
            if layout is None:
                self.window_btn.setChecked(False)
                return
            self._home = (layout, layout.indexOf(self))
            layout.removeWidget(self)
            self.setParent(None)
            self.setWindowTitle(self.title.text() or "Curator")
            self.window_btn.setText("Back to the app")
            self.showMaximized()
        elif not on and self._home is not None:
            layout, index = self._home
            self._home = None
            self.hide()
            layout.insertWidget(index, self, 1)
            self.window_btn.setText("Own window")
            self.show()

    def closeEvent(self, e):
        # Closing the separate window puts the curator back rather than losing it.
        if self._home is not None:
            e.ignore()
            self.window_btn.setChecked(False)
            return
        super().closeEvent(e)

    def set_done(self, label, tip=""):
        """Offer the Done button with this label, or hide it for an empty one."""
        self.done_btn.setText(label)
        self.done_btn.setToolTip(tip)
        self.done_btn.setVisible(bool(label))

    def put_back(self):
        """Return from its own window, if it is in one."""
        self.window_btn.setChecked(False)

    def run_export(self, on_started):
        """Press the page's own Export, so its usual files are written.

        `on_started(bool)` says whether the page had an Export to press: a page
        that is not built yet, or not loaded, has none.
        """
        self.view.page().runJavaScript(
            "typeof exportCsv === 'function' ? (exportCsv(), true) : false",
            0, lambda ok: on_started(bool(ok)))

    # ---- curation state ---------------------------------------------------

    def _install_state_bridge(self):
        """Seed the page's storage from disk, and mirror its writes back.

        The seeding script has to run at DocumentCreation - before the page's own
        script reads localStorage - or the curator starts empty and then
        overwrites the file with that emptiness on the first save.

        qwebchannel.js is Qt's own, loaded from the resource bundle rather than
        shipped as a copy, so it cannot drift from the QWebChannel on this side.
        """
        page = self.view.page()
        self.bridge = ST.Bridge(self.store, on_save=self._saved, on_error=self._save_failed)
        self.channel = QWebChannel(page)
        self.channel.registerObject("lsbridge", self.bridge)
        page.setWebChannel(self.channel)

        from PySide6.QtCore import QFile, QIODevice
        f = QFile(":/qtwebchannel/qwebchannel.js")
        qwc = ""
        if f.open(QIODevice.ReadOnly):
            qwc = bytes(f.readAll()).decode("utf-8")
            f.close()

        src = qwc + "\n" + ST.seed_script(self.store)
        sc = QWebEngineScript()
        sc.setName("ls_state_bridge")
        sc.setInjectionPoint(QWebEngineScript.DocumentCreation)
        sc.setWorldId(QWebEngineScript.MainWorld)
        sc.setRunsOnSubFrames(False)
        sc.setSourceCode(src)
        page.scripts().insert(sc)

    def _saved(self, key, n):
        self.logged.emit(f"saved   {key}  ({n/1024:.1f} KB)")
        if self._save_failing:
            self._save_failing = False
            self.save_warning.setVisible(False)
            self.logged.emit("curation saves are reaching disk again")

    def _save_failed(self, key, message):
        """Show a failed save now, and keep showing it until one succeeds.

        The strip and the log line repeat for every failure; the message box comes once per
        run of failures, since the curators save on every click and a box per click would
        stop the work the operator is trying to save.
        """
        self.logged.emit(f"NOT SAVED  {message}")
        self.save_warning.setText(
            "Curation is NOT being saved to disk - decisions made now are in this page only. "
            + message)
        self.save_warning.setVisible(True)
        if not self._save_failing:
            self._save_failing = True
            box = QMessageBox(QMessageBox.Warning, "Curation not saved",
                              message + "\n\nThe warning above the curator stays until a "
                              "save succeeds. Fix the cause, then make any decision to "
                              "save again.", QMessageBox.Ok, self)
            box.setAttribute(Qt.WA_DeleteOnClose)
            # Not exec(): a modal loop inside the page's save call would stall the page.
            box.open()

    def refresh_seed(self):
        """Rebuild the injected script so a later import is picked up on reload."""
        page = self.view.page()
        for sc in page.scripts().find("ls_state_bridge"):
            page.scripts().remove(sc)
        self._install_state_bridge()

    # ---- downloads --------------------------------------------------------

    def _on_download(self, item: QWebEngineDownloadRequest):
        """Send Export to where it belongs, and say where it went.

        Accepting without a directory would drop the file in the profile's
        default download path, which is not anywhere the pipeline reads. The
        page's own folder is where its consumers look: the ROI curator's three
        CSVs in reformatted/, the plate reframer's plate_boxes.csv in atlas/.

        A name ending in a `DD.MM.YYYY_HH.MM` stamp is one export of a set that
        belongs together, and it goes into a folder of that name under
        `config.export_dir` instead. QtWebEngine has no File System Access API,
        so the page cannot make that folder itself in here - it puts the stamp
        in the name and this rebuilds the folder from it, which is what gets the
        app and a browser to the same layout.
        """
        name = item.downloadFileName() or "export.csv"
        stamp = _export_stamp(name)
        if stamp:
            target = os.path.join(_export_root(self.out_root), stamp)
        else:
            target = os.path.join(self.out_root, os.path.dirname(self.rel or "reformatted/"))
        os.makedirs(target, exist_ok=True)
        item.setDownloadDirectory(target)
        item.setDownloadFileName(name)
        item.accept()

        def finished():
            # isFinishedChanged also fires for a cancelled or failed download,
            # and "exported" would then name a file that is not there.
            done = QWebEngineDownloadRequest.DownloadState.DownloadCompleted
            if item.state() == done:
                self.logged.emit(f"exported  {os.path.join(target, name)}")
                self.exported.emit(os.path.join(target, name))
            else:
                self.logged.emit(f"export of {name} did not complete ({item.state().name})")
        item.isFinishedChanged.connect(finished)

    def rebase(self, server, out_root):
        """Point at a different out_root after Settings changed it."""
        self.server = server
        self.out_root = out_root
        self.store.dir = os.path.join(out_root, "curation")
        self.store.out_root = out_root
        self.refresh_seed()
        if self.rel:
            self.show_page(self.rel, self.title.text())

    def _open_external(self):
        import webbrowser
        if self.rel:
            webbrowser.open(self.server.url_for(self.rel).toString())

    # ---- loading ----------------------------------------------------------

    def show_page(self, rel, label=""):
        self.rel = rel
        self.title.setText(label or rel)
        path = os.path.join(self.out_root, rel)
        if not os.path.exists(path):
            self.view.setHtml(
                f"<body style='background:{T.BG};color:{T.TEXT};font:14px system-ui;"
                "padding:24px'><h2>Not built yet</h2>"
                f"<p><code>{rel}</code> does not exist. Run the stage that "
                "generates it, then reload.</p></body>")
            return
        self.view.setUrl(self.server.url_for(rel))
