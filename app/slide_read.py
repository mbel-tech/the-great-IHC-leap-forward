"""Reading a slides folder's headers without freezing the window.

autodetect reads up to 400 CZI headers. From a local disk that is seconds, but
from a USB stick or a OneDrive folder each header can take a second, and done
on the window's thread Windows greys the window out as "Not responding". So the
read runs here, on a worker thread, and the window is told how far it got
("Reading slide 12 of 400") and gets the same Detection it would have had.

One read at a time per reader. A new request for the same folder while one is
running is ignored (it would read the same files again); a request for another
folder cancels the running one, which stops before its next file, and anything
it still sends is dropped, so a slow stale read can never land on top of a
newer one.

A plain daemon thread, not a QThread, as in update_ui._Job: a QThread still
running when its owner is destroyed aborts the whole app, and a dialog closed
mid-read must not do that. The signals are queued to the window's thread
because this object lives there.
"""

import threading

from PySide6.QtCore import QCoreApplication, QElapsedTimer, QObject, Signal

import autodetect as AD


def words(done, total):
    """"Reading slide 12 of 400"."""
    return "Reading slide %d of %d…" % (done, total)


class SlideRead(QObject):
    """Runs autodetect off the window's thread; `finished(source_dir, Detection)` when done."""

    progress = Signal(int, int)             # slide being read, slides to read
    finished = Signal(str, object)          # source_dir, Detection
    failed = Signal(str, str)               # source_dir, why

    # From the worker: tagged with the read's number, so a stale read is told
    # apart from the current one on the window's side.
    _step = Signal(int, int, int)
    _done = Signal(int, str, object)
    _error = Signal(int, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.source = None
        self._n = 0
        self._cancel = None
        self._step.connect(self._on_step)
        self._done.connect(self._on_done)
        self._error.connect(self._on_error)

    @property
    def busy(self):
        return self._cancel is not None

    def start(self, source_dir, fresh=True):
        """Read `source_dir`. False when that folder is already being read.

        Through autodetect.cached, so a later "What the slides say…" does not
        read the folder again. `fresh` (the default) re-reads it even when it is
        remembered, as autodetect.detect did; False takes the remembered one.
        """
        if self.busy and source_dir == self.source:
            return False
        self.cancel()
        self._n += 1
        self.source = source_dir
        self._cancel = threading.Event()
        threading.Thread(target=self._run, args=(self._n, source_dir, fresh, self._cancel),
                         daemon=True, name="slide-headers").start()
        return True

    def cancel(self):
        """Give up the running read, if any: it stops before its next file."""
        if self._cancel is not None:
            self._cancel.set()
        self._cancel = None
        self._n += 1

    def wait(self, ms=60000):
        """Keep the event loop turning until the read is done (tests, and a caller that must have it)."""
        clock = QElapsedTimer()
        clock.start()
        while self.busy and clock.elapsed() < ms:
            QCoreApplication.processEvents()
        return not self.busy

    # -- worker thread ---------------------------------------------------
    def _run(self, n, source_dir, fresh, cancel):
        def step(done, total):
            self._emit(self._step, n, done, total)
        try:
            found = AD.cached(source_dir, progress=step, cancel=cancel, fresh=fresh)
        except AD.Cancelled:
            return
        except Exception as exc:                            # noqa: BLE001 - say why
            self._emit(self._error, n, source_dir, "%s: %s" % (type(exc).__name__, exc))
            return
        self._emit(self._done, n, source_dir, found)

    @staticmethod
    def _emit(signal, *args):
        try:
            signal.emit(*args)
        except RuntimeError:            # the window went away mid-read; nobody to tell
            pass

    # -- window's thread -------------------------------------------------
    def _on_step(self, n, done, total):
        if n == self._n:
            self.progress.emit(done, total)

    def _on_done(self, n, source_dir, found):
        if n != self._n:
            return
        self._cancel = None
        self.finished.emit(source_dir, found)

    def _on_error(self, n, source_dir, why):
        if n != self._n:
            return
        self._cancel = None
        self.failed.emit(source_dir, why)
