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

# Keys whose refusal by the page's own storage is not news to the operator: the rolling
# snapshot is a second copy of the curation, the largest write and the first refused near
# the quota, and the page already treats losing it as harmless. On disk it is still kept.
QUIET_KEYS = {"ls_roi_curator_v1_backup"}


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

    `behind` is the keys whose file on disk is NEWER than the page's localStorage: the page's
    own setItem refused a save (a full store) that the mirror wrote to disk anyway. The page
    that made it holds the newest state in memory and goes on saving it, which is fine; a
    page LOADED from that localStorage holds an older copy, and its first save would send it
    over the newer file. So while a key is behind every load is seeded with it from disk over
    whatever localStorage holds, and a load where even that is refused marks the key stale
    and its saves of it are kept aside, never written (see `seed_script`). A save of the key
    that the page's storage accepts means the two agree again and ends it.
    """

    def __init__(self):
        self.gen = uuid.uuid4().hex
        self.superseded = set()
        self.force = []
        self.aside = None
        self.holding = set()
        self.held = {}
        self.retired = {}
        self.behind = set()
        self.stale_aside = None

    def replaced(self, keys, aside=None):
        """The store now holds an import of `keys`: retire every page loaded before it."""
        self.gen = uuid.uuid4().hex
        self.superseded |= set(keys)
        self.force = sorted(set(keys))
        self.aside = os.path.join(aside or backup_folder(), "from_open_page")


class Bridge(QObject):
    """What the page calls to persist. One write path, deliberately: `saveFrom` only decides
    whether the page saving is still allowed to, then goes through `save`."""

    def __init__(self, store, on_save=None, on_error=None, guard=None, on_aside=None,
                 on_page_error=None, on_behind=None):
        super().__init__()
        self.store = store
        self.on_save = on_save
        # `on_page_error(key, message)`: the save reached disk but the page's OWN storage
        # refused it (a full localStorage). Shown like a failed save: the page now holds
        # less than the file, and a reload would show the page's older copy.
        self.on_page_error = on_page_error
        # `on_error(key, message)`: a save that did not reach disk. The page cannot be told -
        # its localStorage already holds the decision - so the app has to show it.
        self.on_error = on_error
        self.guard = guard or SaveGuard()
        # `on_aside(key, path)`: a save from a page older than an import, kept at `path`.
        self.on_aside = on_aside
        # `on_behind()`: `guard.behind` changed, or the file it is seeded from did: the
        # injected script has to be rebuilt so the NEXT load starts from the file.
        self.on_behind = on_behind

    @Slot(str, str, str)
    def saveFrom(self, gen, key, text):
        """A save from the page loaded as `gen`: written, kept aside, or held back."""
        self._route(gen, key, text, None)

    @Slot(str, str, str, str)
    def saveFromRefused(self, gen, key, text, why):
        """The same save, which the page's own localStorage refused with `why`.

        The disk copy is written all the same - it is the one that matters, and the only one
        left - and the refusal is reported rather than taken for success: the curator saves
        on every click, so a full browser store would otherwise go unnoticed until a reload
        showed the older copy the page still holds.
        """
        self._route(gen, key, text, why)

    @Slot(str, str, str)
    def saveFromStale(self, gen, key, text):
        """A save from a load whose localStorage is older than the file (`SaveGuard.behind`).

        Kept aside, never written: it is the older copy plus whatever was done since, and
        writing it would put the older copy back over the newer file. Kept, because the part
        done since is someone's work.
        """
        g = self.guard
        g.stale_aside = g.stale_aside or os.path.join(backup_folder(), "from_stale_page")
        try:
            path = self.store.set_aside(g.stale_aside, key, text)
        except Exception as e:                              # noqa: BLE001
            msg = save_error_text(self.store, key, e)
        else:
            msg = (f"{key} was NOT saved: this page loaded an older copy than the file on "
                   f"disk, because its own storage is full. What it sent is kept in {path}. "
                   "Free the browser storage, or restart the app, then reload")
        print(msg, file=sys.stderr)
        if key in QUIET_KEYS:
            return
        report = self.on_page_error or self.on_error
        if report:
            report(key, msg)

    def _route(self, gen, key, text, page_error):
        g = self.guard
        if gen in g.retired:
            self._write(g.retired[gen], key, text, page_error)
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
        self._write(self.store, key, text, page_error)

    @Slot(str, str)
    def save(self, key, text):
        self._write(self.store, key, text)

    def _write(self, store, key, text, page_error=None):
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
        g = self.guard
        if page_error:
            # The file is now ahead of the page's storage: the next load must start from it.
            g.behind.add(key)
            if self.on_behind:
                self.on_behind()
            msg = page_store_error_text(store, key, page_error)
            print(msg, file=sys.stderr)
            if key in QUIET_KEYS:
                # On disk, which is what a snapshot is for; not a decision the page lacks.
                return
            report = self.on_page_error or self.on_error
            if report:
                report(key, msg)
            return
        if key in g.behind:
            # The page's storage took this one: it and the file agree again.
            g.behind.discard(key)
            if self.on_behind:
                self.on_behind()
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


def page_store_error_text(store, key, why):
    """One line for a save that reached disk while the page's own storage refused it."""
    return (f"{key} was saved to {store.where()}, but the curator page's own storage refused "
            f"it ({why}). A reload starts from the file; if the browser storage is still "
            "full then, that page's saves are kept aside rather than written. Free the "
            "browser storage, or restart the app (its browser storage is not kept)")


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
    behind = sorted(k for k in guard.behind if k in seed)
    return """
(function () {
  var SEED = %s, GEN = %s, FORCE = %s, BEHIND = %s, STALE = {};
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
  // A key whose file is newer than this localStorage (SaveGuard.behind): taken from the
  // file over what is here, on every load until they agree. Refused again (still full),
  // this load holds the older copy, and its saves of the key go aside, not to disk.
  BEHIND.forEach(function (k) {
    try { localStorage.setItem(k, SEED[k]); } catch (e) { STALE[k] = 1; }
  });

  var queue = [], bridge = null;
  window.__lsQueueSize = function () { return queue.length; };

  // Mirrored even when the browser's own setItem throws (a full localStorage): the
  // disk copy is the one that matters, and a mirror reached only after a successful
  // setItem lost exactly the saves that most needed it. The throw still reaches the
  // page afterwards, which has its own NOT SAVED notice; the app shows its own.
  var orig = Storage.prototype.setItem;
  function send(k, v, why) {
    if (STALE[k]) bridge.saveFromStale(GEN, k, v);
    else if (why) bridge.saveFromRefused(GEN, k, v, why);
    else bridge.saveFrom(GEN, k, v);
  }
  Storage.prototype.setItem = function (k, v) {
    var err = null;
    try { orig.call(this, k, v); } catch (e) { err = e; }
    if (this === window.localStorage) {
      var why = err ? String((err.name ? err.name + ": " : "") + (err.message || err)) : "";
      if (bridge) { send(String(k), String(v), why); }
      else { queue.push([String(k), String(v), why]); }
    }
    if (err) throw err;
  };

  function connect() {
    if (!window.qt || !qt.webChannelTransport) { setTimeout(connect, 50); return; }
    new QWebChannel(qt.webChannelTransport, function (ch) {
      bridge = ch.objects.lsbridge;
      window.__lsbridge = bridge;
      while (queue.length) { var e = queue.shift(); send(e[0], e[1], e[2]); }
    });
  }
  connect();
})();
""" % (json.dumps(seed), json.dumps(guard.gen), json.dumps(guard.force), json.dumps(behind))


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
