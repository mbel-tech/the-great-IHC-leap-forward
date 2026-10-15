"""Curation decisions, kept in files instead of a browser profile.

The curators store their state in `localStorage`. That is right for a page opened
in a browser and wrong for a desktop app: the embedded view has its own profile,
so state saved in the app would be invisible to Chrome and vice versa, and either
copy is one cleared profile from being gone.

**The generators are not modified.** An earlier version of this plan had each of
them write through a bridge, which meant editing three tools that currently work.
Wrapping `Storage.prototype.setItem` at document-creation time does the same job
from outside: every existing `localStorage.setItem` in every curator - and any
added later - is mirrored to disk without the page knowing. Opened in a plain
browser there is no injection, so behaviour there is untouched by construction.

The read side needs no bridge either. Seeding `localStorage` before the page's own
script runs means `JSON.parse(localStorage.getItem(KEY))` finds what it expects,
and the pages stay synchronous.
"""

import datetime
import json
import os
import sys
import uuid

from PySide6.QtCore import QObject, Slot

# Every key the curators use. Listed rather than discovered because the point of
# the file store is that a key nobody wrote this session is still carried
# forward; discovering from localStorage would only ever find the current one.
KEYS = [
    "ls_roi_curator_v1",
    "ls_rotation_curator_v3",
    "ls_rotation_curator_v2",      # read by 04d to migrate its own older format
    "ls_level_curator_v1",
    "ls_plate_reframe_v1",
    "ls_roi_curator_v1_backup",    # the rolling snapshot, worth keeping too
    "ls_roi_curator_v1_size",      # the landmark radius; mirrored, so seed it back
    "ls_roi_curator_v1_rois",      # which ROIs the operator chose to draw (a JSON list)
    "ls_roi_curator_v1_custom",    # the ROIs the operator added (ADD ROI), filed per study
    "ls_atlas_curator_v1",         # 04b's page, retired but a browser may hold it
]


class CurationStore:
    """The curators' state: in the project file when there is one, else one JSON file per key.

    With a project (`project_source` returns the open `ls_project.Project`), the project file
    is where decisions live: every `write` goes into it at once (see
    `Project.write_curation`) and the plain copy under out_root/curation is rewritten too, so
    the stages and the curator seed keep reading files they already understand. Without one it
    is the file store it always was.
    """

    def __init__(self, out_root, project_source=None):
        self.dir = os.path.join(out_root, "curation")
        self.out_root = out_root
        self.project_source = project_source

    def _project(self):
        return self.project_source() if self.project_source else None

    def path(self, key):
        return os.path.join(self.dir, key + ".json")

    def read(self, key):
        project = self._project()
        if project is not None:
            text = project.curation_text(key)
            if text is not None:
                return text
        try:
            with open(self.path(key), encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return None

    def write(self, key, text):
        """Atomic, because a half-written state file is worse than none.

        The curators save on every decision, so this runs often and while the
        user is still working; a crash mid-write must not leave a truncated file
        where the day's curation used to be.
        """
        project = self._project()
        if project is not None and not project.read_only:
            project.write_curation(key, text, out_root=os.path.dirname(self.dir))
            return
        os.makedirs(self.dir, exist_ok=True)
        tmp = self.path(key) + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(tmp, self.path(key))
        except BaseException:
            # The old file is untouched; do not leave the half-written one beside it.
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    def where(self):
        """The file a write goes into: the project file when there is one, else the curation folder."""
        project = self._project()
        if project is not None and not project.read_only and getattr(project, "path", None):
            return project.path
        return self.dir

    def all_present(self):
        return {k: self.read(k) for k in KEYS if self.read(k) is not None}

    def set_aside(self, folder, key, text):
        """Keep `text` as curation/<folder>/<key>.json, out of the way. Returns the path.

        For curation that is being replaced rather than lost: what an import overwrote,
        and what a page still showing the old state tried to save after it. A subfolder,
        so neither the stages nor the project's reconcile (top-level files only) take it
        for the live copy.
        """
        path = os.path.join(self.dir, folder, key + ".json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
        return path

    def import_bundle(self, path, aside=None):
        """Load a JSON of {key: value} exported from a browser. Returns names.

        An import replaces curation wholesale, so what a key held before is kept first,
        under `aside` (a `backup_folder()` name, a fresh one when not given).
        """
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        aside = aside or backup_folder()
        written = []
        for key, value in data.items():
            if not isinstance(key, str):
                continue
            text = value if isinstance(value, str) else json.dumps(value)
            old = self.read(key)
            if old is not None and old != text:
                self.set_aside(aside, key, old)
            self.write(key, text)
            written.append(key)
        return written


def backup_folder():
    """`_backup_<time>_<tag>`, after the folder ls_project's reconcile keeps a loser in.

    The tag keeps two imports in the same second from sharing a folder, and so the
    second from overwriting what the first one kept.
    """
    return "_backup_%s_%s" % (datetime.datetime.now().strftime("%Y%m%d_%H%M%S"),
                              uuid.uuid4().hex[:6])


class SaveGuard:
    """Which page may save which key: what stops a page older than an import saving over it.

    Every page load is stamped with `gen`, baked into its injected script, and every save
    it makes carries the stamp. A page loaded before an import still holds the old
    curation in memory, and the ROI curator re-saves it on every decision, every 60 s and
    on unload; a reload alone does not help, because the seed only fills empty keys and
    that page's localStorage is not empty. So an import (`replaced`) issues a new `gen`:
    a save of a replaced key stamped with an older one is kept aside, not written, and
    the first load after it is seeded with the imported value over the old one (`force`).

    `holding` is for an import that takes a while (04q runs as a stage): saves of those
    keys wait in `held` until it is known whether the import wrote anything.

    `retired` is for opening another project: {gen: store} for the pages that were showing
    the one open before it. What such a page still sends - its unload save, a decision on
    its way to disk - is that project's work, so it is written to that project's store,
    never to the one open now and never dropped.
    """

    def __init__(self):
        self.gen = uuid.uuid4().hex
        self.superseded = set()
        self.force = []
        self.aside = None
        self.holding = set()
        self.held = {}
        self.retired = {}

    def replaced(self, keys, aside=None):
        """The store now holds an import of `keys`: retire every page loaded before it."""
        self.gen = uuid.uuid4().hex
        self.superseded |= set(keys)
        self.force = sorted(set(keys))
        self.aside = os.path.join(aside or backup_folder(), "from_open_page")


class Bridge(QObject):
    """What the page calls to persist. One write path, deliberately: `saveFrom` only decides
    whether the page saving is still allowed to, then goes through `save`."""

    def __init__(self, store, on_save=None, on_error=None, guard=None, on_aside=None):
        super().__init__()
        self.store = store
        self.on_save = on_save
        # `on_error(key, message)`: a save that did not reach disk. The page cannot be told -
        # its localStorage already holds the decision - so the app has to show it.
        self.on_error = on_error
        self.guard = guard or SaveGuard()
        # `on_aside(key, path)`: a save from a page older than an import, kept at `path`.
        self.on_aside = on_aside

    @Slot(str, str, str)
    def saveFrom(self, gen, key, text):
        """A save from the page loaded as `gen`: written, kept aside, or held back."""
        g = self.guard
        if gen in g.retired:
            self._write(g.retired[gen], key, text)
            return
        if gen != g.gen and key in g.superseded:
            # Not written - it is the curation the import replaced, plus anything done on
            # that page since - and not dropped either, because it is someone's work.
            try:
                path = self.store.set_aside(g.aside, key, text)
            except Exception as e:                          # noqa: BLE001
                msg = save_error_text(self.store, key, e)
                print(msg, file=sys.stderr)
                if self.on_error:
                    self.on_error(key, msg)
                return
            if self.on_aside:
                self.on_aside(key, path)
            return
        if key in g.holding:
            g.held[key] = text
            return
        self.save(key, text)

    @Slot(str, str)
    def save(self, key, text):
        self._write(self.store, key, text)

    def _write(self, store, key, text):
        try:
            store.write(key, text)
        except Exception as e:                              # noqa: BLE001
            # Everything, not only OSError: a project that cannot be read back raises
            # ProjectError, and an exception escaping a slot is only printed by Qt - to a
            # console the installed app does not have. So was the print() this used to be:
            # the operator went on curating for hours with nothing reaching disk.
            msg = save_error_text(store, key, e)
            print(msg, file=sys.stderr)
            if self.on_error:
                self.on_error(key, msg)
            return
        if self.on_save:
            self.on_save(key, len(text))


def save_error_text(store, key, exc):
    """One line for the operator: what was not saved, into which file, and why."""
    where = getattr(exc, "filename", None) or store.where()
    if isinstance(where, str) and where.endswith(".tmp"):
        where = where[:-len(".tmp")]
    text = f"{key} was not saved to {where}: {getattr(exc, 'strerror', None) or exc}"
    if isinstance(exc, PermissionError):
        # Windows refuses to replace a file another program has open; this is the usual cause.
        text += (" - the file may be open in another program (an editor, a sync client, "
                 "antivirus), or the folder is read-only")
    return text


def seed_script(store, guard=None):
    """JS run before any page script: seed state, then mirror every write.

    Writes are queued until the channel is up. The page saves on the first
    decision the user makes, which can easily beat the asynchronous QWebChannel
    handshake - without the queue those early saves would reach localStorage and
    never reach disk, which is exactly the silent half-loss this module exists to
    prevent.

    The seed only fills an empty key, so a decision whose save failed is not
    thrown away by a reload - except a key an import just replaced
    (`guard.force`), which the first load after the import takes from the store,
    or empties when the store has none (another project opened that never had it).
    Once: `ls_app_seed_gen` records that load, so a later reload of the same
    generation is back to filling only what is empty.
    """
    guard = guard or SaveGuard()
    seed = {k: v for k, v in ((k, store.read(k)) for k in KEYS) if v is not None}
    return """
(function () {
  var SEED = %s, GEN = %s, FORCE = %s;
  try {
    // Still the browser's own setItem here, so none of this is mirrored back.
    var fresh = localStorage.getItem("ls_app_seed_gen") !== GEN;
    for (var k in SEED) {
      if (localStorage.getItem(k) === null || (fresh && FORCE.indexOf(k) >= 0))
        localStorage.setItem(k, SEED[k]);
    }
    if (fresh) FORCE.forEach(function (k) { if (!(k in SEED)) localStorage.removeItem(k); });
    if (fresh) localStorage.setItem("ls_app_seed_gen", GEN);
  } catch (e) {}

  var queue = [], bridge = null;
  window.__lsQueueSize = function () { return queue.length; };

  var orig = Storage.prototype.setItem;
  Storage.prototype.setItem = function (k, v) {
    orig.call(this, k, v);
    if (this === window.localStorage) {
      if (bridge) { bridge.saveFrom(GEN, String(k), String(v)); }
      else { queue.push([String(k), String(v)]); }
    }
  };

  function connect() {
    if (!window.qt || !qt.webChannelTransport) { setTimeout(connect, 50); return; }
    new QWebChannel(qt.webChannelTransport, function (ch) {
      bridge = ch.objects.lsbridge;
      window.__lsbridge = bridge;
      while (queue.length) { var e = queue.shift(); bridge.saveFrom(GEN, e[0], e[1]); }
    });
  }
  connect();
})();
""" % (json.dumps(seed), json.dumps(guard.gen), json.dumps(guard.force))


EXPORT_PAGE = """<!doctype html>
<meta charset="utf-8"><title>Export curation state</title>
<style>
 body{background:#0e1014;color:#e6e6e6;font:15px system-ui;padding:32px;max-width:760px}
 h1{font-size:19px} code{background:#1c2027;padding:1px 5px;border-radius:4px}
 button{background:#4da3ff;border:0;color:#04121f;font:600 15px system-ui;
        padding:10px 18px;border-radius:8px;cursor:pointer}
 .none{color:#f85149} .found{color:#3fb950} li{margin:3px 0}
</style>
<h1>Export curation state</h1>
<p>This reads the curation decisions this browser is holding and saves them to one
file, so the desktop app can take them over. Open it in <b>the same browser you
have been curating in</b>.</p>
<ul id=list></ul>
<p><button id=go>Save curation_state.json</button></p>
<p id=note></p>
<script>
const KEYS = %s;
const found = {};
for (const k of KEYS) {
  const v = localStorage.getItem(k);
  const li = document.createElement("li");
  if (v === null) { li.innerHTML = `<span class=none>not present</span> &nbsp; ${k}`; }
  else { found[k] = v;
         li.innerHTML = `<span class=found>${(v.length/1024).toFixed(1)} KB</span> &nbsp; ${k}`; }
  document.getElementById("list").appendChild(li);
}
if (!Object.keys(found).length) {
  document.getElementById("note").innerHTML =
    "<span class=none>Nothing found in this browser's storage.</span> Pages opened " +
    "from <code>file://</code> may keep storage per file. Open the curator you " +
    "actually use, press F12, and run <code>copy(JSON.stringify(Object.fromEntries(" +
    "Object.keys(localStorage).map(k=>[k,localStorage.getItem(k)]))))</code> " +
    "to put the same JSON on your clipboard.";
}
document.getElementById("go").onclick = () => {
  const b = new Blob([JSON.stringify(found, null, 1)], {type: "application/json"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(b); a.download = "curation_state.json"; a.click();
};
</script>
"""


def write_export_page(out_root):
    """Drop the export helper next to the curators, where the user will find it."""
    path = os.path.join(out_root, "reformatted", "export_curation_state.html")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(EXPORT_PAGE % json.dumps(KEYS))
    return path
