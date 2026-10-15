"""Set up's "Check workbook": 06b's join to the sampling workbook, run early.

06b first read the workbook at Unblind, the end of a study, so a renamed
column or a fish nobody typed in was found there. This runs the same join
(ls_sampling, which 06b calls) on the fish the study has so far, and writes
nothing: it says how many fish and slides would get a group, which fish have
no row, and which rows have a blank group no tank recovers.

The fish come from where 06b would take them once it can: the measurements
when there are some, else Prepare's manifest, else the slide file names read
with the study's naming rule.

The workbook may be on a network drive or OneDrive, so the check runs on a
plain daemon thread, as slide_read does (a QThread still running when its
owner goes away aborts the app).
"""

import copy
import csv
import os
import threading

from PySide6.QtCore import QObject, Signal

import ls_sampling as SAMPLING


def workbook_path(cfg, options):
    """The workbook 06b would read: its --xlsx, else the default in the slides folder."""
    chosen = (options or {}).get("--xlsx") or ""
    if chosen:
        return chosen
    source = (cfg or {}).get("source_dir") or ""
    return os.path.join(source, SAMPLING.DEFAULT_NAME) if source else ""


def _csv_rows(path):
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except (OSError, ValueError, csv.Error):
        return None


def study_animals(cfg):
    """({animal: slide files}, where they were read from) for the open study."""
    cfg = cfg or {}
    out_root = cfg.get("out_root") or ""
    manifest = _csv_rows(os.path.join(out_root, "manifest", "manifest_files.csv")) \
        if out_root else None
    slides = {}
    for r in manifest or []:
        if r.get("animal"):
            slides[r["animal"]] = slides.get(r["animal"], 0) + 1
    meas = _csv_rows(os.path.join(out_root, "results", "roi_measurements.csv")) \
        if out_root else None
    if meas:
        return ({a: slides.get(a, 0) for a in {r["animal"] for r in meas if r.get("animal")}},
                "measurements")
    if slides:
        return slides, "manifest"
    import autodetect as AD
    import ls_naming as NM
    naming = cfg.get("slide_naming") or {}
    try:
        pattern = NM.compile_pattern(naming)
    except (NM.NamingError, TypeError, ValueError, KeyError):
        return {}, "slide file names"
    for name in AD.slide_files(cfg.get("source_dir") or ""):
        parsed = NM.parse_name(name, pattern)
        if parsed and parsed.get("subject"):
            slides[parsed["subject"]] = slides.get(parsed["subject"], 0) + 1
    return slides, "slide file names"


def run(cfg, path):
    """The report (ls_sampling.check), with where the fish were read from."""
    animals, where = study_animals(cfg)
    rep = SAMPLING.check(path, animals)
    rep["from"] = where
    return rep


class WorkbookCheck(QObject):
    """Runs `run` off the window's thread; `finished(report)` when done."""

    finished = Signal(object)
    _done = Signal(int, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._n = 0
        self._running = None
        self._done.connect(self._on_done)

    @property
    def busy(self):
        return self._running is not None

    def start(self, cfg, path):
        """Check `path` against `cfg`'s fish; a check still running is dropped."""
        self._n += 1
        self._running = self._n
        threading.Thread(target=self._run, args=(self._n, copy.deepcopy(cfg), path),
                         daemon=True, name="workbook-check").start()

    def _run(self, n, cfg, path):
        try:
            rep = run(cfg, path)
        except Exception as exc:                            # noqa: BLE001 - say why
            rep = {"error": "%s: %s" % (type(exc).__name__, exc)}
        try:
            self._done.emit(n, rep)
        except RuntimeError:            # the window went away; nobody to tell
            pass

    def _on_done(self, n, rep):
        if n != self._running:
            return
        self._running = None
        self.finished.emit(rep)
