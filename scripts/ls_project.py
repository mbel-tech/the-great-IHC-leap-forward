"""A project: one study config plus the app's record of working on it.

The file a project lives in is the study config the stages already read, with one
extra top-level block, `project`, that only the app reads:

    project_version   this block's own schema (the config has its own)
    id, created, modified, app_version, notes
    detected          what was read from CZI files - evidence, never an input
    decisions         changes made in the app: key, from, to, by, when
    runs              stages that ran: stage, times, ok, config hash, code version

Nothing in `project` reaches a stage, so adding, editing or deleting it cannot
change a result. That is also why it is safe in the same file: the stages ignore
what they do not know.

This module has no Qt. The window layer (`app/project_session.py`) holds a
`Project`, and every rule about what a project is lives here so it can be tested
without one:

    Project.new(name)              an unsaved project from the stock template
    Project.open(path)             read, upgrade, and say if it is too new to edit
    p.set / p.apply_edits          change the config in memory, recording each decision
    p.note_detected(file, chans)   keep what a CZI said, apart from what was decided
    p.dirty                        differs from what is on disk
    p.save() / p.save_as(path)     atomic write, with a few backups
    p.revert()                     back to what is on disk
    p.record_run(...)              append to the run history WITHOUT saving other edits

`groups.*` holds the animal-to-treatment key, which must stay out of anything a
blinded analysis might read; a decision there is recorded by name, never by value.
"""

import copy
import datetime
import hashlib
import json
import os
import subprocess

import ls_config as LC
import ls_jsonio

PROJECT_VERSION = 1
CAPS = LC.PROJECT_CAPS                      # {"detected": 20, "decisions": 500, "runs": 200}
BACKUPS = 3

#: Top-level blocks written compactly (one value per line): the curation is bulky, machine-made data.
COMPACT = ("curation",)

#: Longest value recorded verbatim in a decision; a bigger one (a channel table)
#: is recorded as its size and a hash, so the trail stays readable and bounded.
BRIEF_LIMIT = 1500

#: Blocks a STAGE writes into the study file, as dotted paths. A run's history
#: row is written from disk, and these are carried into memory with it: left
#: behind, the next save of the person's working copy would put back the file
#: as it was before the stage ran and quietly drop what the stage wrote.
STAGE_WRITTEN = ("detection.auto",)

#: Keys whose values are never written into the trail.
REDACTED_PREFIXES = ("groups.",)

#: Keys whose value is a single path. Taken from the spec, so a path key added there is
#: portable without anyone remembering to list it here.
PATH_TYPES = ("path_dir", "path_dir_create", "path_dir_opt", "path_file")


def path_keys():
    return [k.path for k in LC.SPEC if k.type in PATH_TYPES]


#: {from_version: fn(block) -> block} - each step lifts a `project` block by one version.
UPGRADES = {}


class ProjectError(Exception):
    """The project could not be read, written or changed as asked."""


class ProjectConflict(ProjectError):
    """The file on disk changed since this project last read or wrote it."""


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _canon(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _sha(obj, n=12):
    return hashlib.sha256(_canon(obj).encode("utf-8")).hexdigest()[:n]


def config_sha(cfg):
    """A short hash of the config WITHOUT its `project` and `curation` blocks.

    The run history lives in `project` and the curators write `curation` on every
    decision; a hash that included either would change constantly and "the config
    changed" would be true forever.
    """
    return _sha({k: v for k, v in (cfg or {}).items() if k not in ("project", "curation")})


_code = {}


def code_version():
    """`<git short hash>` or `<hash>+dirty` for this checkout; None when git is not available."""
    if "v" not in _code:
        version = None
        try:
            kw = dict(cwd=LC.REPO, capture_output=True, text=True, timeout=5)
            head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], **kw)
            if head.returncode == 0 and head.stdout.strip():
                version = head.stdout.strip()
                dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], **kw)
                if dirty.returncode == 0 and dirty.stdout.strip():
                    version += "+dirty"
        except (OSError, subprocess.SubprocessError):
            version = None
        _code["v"] = version
    return _code["v"]


def _brief(value):
    text = _canon(value)
    if len(text) <= BRIEF_LIMIT:
        return value
    kind = "list of %d" % len(value) if isinstance(value, list) else type(value).__name__
    return {"large": kind, "sha": _sha(value)}


def _redacted(key):
    return key.startswith(REDACTED_PREFIXES)


def _get(cfg, parts):
    node = cfg
    for p in parts:
        if not isinstance(node, dict) or p not in node:
            return False, None
        node = node[p]
    return True, node


def _new_block():
    return {"project_version": PROJECT_VERSION, "id": _new_id(), "created": _now(),
            "modified": None, "app_version": None, "notes": "",
            "detected": [], "decisions": [], "runs": []}


def _new_id():
    import uuid
    return str(uuid.uuid4())


def _cap(block):
    for field, cap in CAPS.items():
        if isinstance(block.get(field), list) and len(block[field]) > cap:
            block[field] = block[field][-cap:]
    return block


def _upgrade(block):
    """Lift a `project` block to the current version (a copy). Raises on one from the future."""
    block = dict(block)
    version = block.get("project_version") or 1
    while version < PROJECT_VERSION:
        step = UPGRADES.get(version)
        if step is None:
            raise ProjectError("no upgrade from project version %d" % version)
        block = step(block)
        version = block["project_version"] = version + 1
    return block


def _core(cfg):
    return {k: v for k, v in cfg.items() if k != "curation"}


def _norm(path):
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def _inside(path, folder):
    """True if `path` is `folder` or below it (same drive; never raises across drives)."""
    try:
        return os.path.commonpath([_norm(path), _norm(folder)]) == _norm(folder)
    except ValueError:
        return False


def _carry(src, dst, parts):
    """Copy the value at `parts` from `src` into `dst`, or drop it from `dst` when absent."""
    node = src
    for p in parts:
        if not isinstance(node, dict) or p not in node:
            node = _MISSING
            break
        node = node[p]
    parent = dst
    for p in parts[:-1]:
        nxt = parent.get(p)
        if not isinstance(nxt, dict):
            if node is _MISSING:
                return
            nxt = parent[p] = {}
        parent = nxt
    if node is _MISSING:
        parent.pop(parts[-1], None)
    else:
        parent[parts[-1]] = copy.deepcopy(node)


_MISSING = object()


def _stamp(path):
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


# ---- curation inside the project -------------------------------------------------------
#
# The curators keep their state as JSON text under a handful of names. In the project
# file it is stored PARSED (readable, diffable); the stages and the curator pages still
# want plain files and text, so `curation_text` turns a value back into what they read.
# Text that is not JSON is kept as {"__text__": ...}.

TEXT_WRAP = "__text__"
CURATION_DIR = "curation"
README = ("These files are copies written by the app from the project file.\n"
          "The project file is where your curation lives: edit it there (in the app), not here.\n"
          "They are regenerated, so a change made here is either adopted by the app on the\n"
          "next open (if the project had not changed) or offered to you as a conflict.\n")


def curation_value(text):
    try:
        return json.loads(text)
    except ValueError:
        return {TEXT_WRAP: text}


def curation_text(value):
    if isinstance(value, dict) and set(value) == {TEXT_WRAP} and isinstance(value[TEXT_WRAP], str):
        return value[TEXT_WRAP]
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def curation_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _valid_key(key):
    return isinstance(key, str) and bool(LC.CURATION_KEY_RE.match(key)) and ".." not in key


def _curation_dir(out_root):
    return os.path.join(out_root, CURATION_DIR)


def _files_in(out_root):
    """{key: path} for the curation JSON files present, skipping temp files and backups."""
    folder = _curation_dir(out_root)
    out = {}
    if os.path.isdir(folder):
        for name in sorted(os.listdir(folder)):
            key = name[:-5] if name.endswith(".json") else None
            path = os.path.join(folder, name)
            if key and _valid_key(key) and os.path.isfile(path):
                out[key] = path
    return out


def _read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _write_text_atomic(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


class Reconcile:
    """What opening a project found between its curation and the copies in out_root.

    ok        identical already
    restore   the project is newer (or the copy is missing): rewrite the copy
    adopt     the copy is newer or only exists there: take it into the project
    conflict  both changed since they last agreed: a person decides
    """

    def __init__(self):
        self.ok, self.restore, self.adopt, self.conflict = [], [], [], []

    def __bool__(self):
        return bool(self.restore or self.adopt or self.conflict)

    def __repr__(self):
        return "<Reconcile ok=%d restore=%s adopt=%s conflict=%s>" % (
            len(self.ok), self.restore, self.adopt, self.conflict)


class Project:
    def __init__(self, cfg, path=None, read_only=False):
        self.reanchored = []          # path settings re-pointed on open, as [(key, old, new)]
        self.cfg = cfg
        self.path = path
        self.read_only = read_only
        self._snapshot = copy.deepcopy(cfg)
        self._stamp = _stamp(path) if path else None

    # ---- creating and opening -------------------------------------------------

    @classmethod
    def new(cls, name, template=None):
        """An unsaved project: the stock template, a name, a fresh `project` block.

        Not validated here: the template carries `<PLACEHOLDER>` paths the person
        fills in through Settings, and the settings dialog is where that is judged.
        """
        if template is None:
            with open(os.path.join(LC.REPO, "config.example.json"), encoding="utf-8") as fh:
                template = json.load(fh)
        cfg = copy.deepcopy(template)
        cfg.setdefault("study", {})["name"] = str(name)
        cfg["schema_version"] = LC.SCHEMA_VERSION
        cfg["project"] = _new_block()
        p = cls(cfg)
        p._snapshot = None                     # never saved, so it is dirty from the start
        return p

    @classmethod
    def open(cls, path):
        path = os.path.abspath(path)
        try:
            with open(path, encoding="utf-8") as fh:
                cfg = json.load(fh)
        except OSError as e:
            raise ProjectError("%s could not be read: %s" % (path, e.strerror or e))
        except ValueError as e:
            raise ProjectError("%s is not valid JSON: %s" % (path, e))
        if not isinstance(cfg, dict):
            raise ProjectError("%s is not a project: its top level is not an object" % path)
        block = cfg.get("project")
        read_only = False
        if block is None:
            block = _new_block()
        elif not isinstance(block, dict):
            raise ProjectError("%s: `project` must be a block of settings" % path)
        elif (block.get("project_version") or 1) > PROJECT_VERSION:
            read_only = True                   # a newer app wrote it; do not rewrite what we cannot read
        else:
            block = _upgrade(block)
        if not read_only:
            cfg["project"] = block
        project = cls(cfg, path, read_only)
        if not read_only:
            project.reanchored = project.reanchor()
        return project

    # ---- reading --------------------------------------------------------------

    @property
    def name(self):
        return (self.cfg.get("study") or {}).get("name") or (
            os.path.splitext(os.path.basename(self.path))[0] if self.path else "untitled")

    @property
    def block(self):
        return self.cfg.setdefault("project", _new_block())

    @property
    def dirty(self):
        """Whether the settings differ from what is saved.

        Curation is left out of the comparison: it is written to the file as each decision is made
        (and to memory with it), so it can never be the unsaved part - and comparing a few hundred
        KB of it every time a title or menu asks would be slow.
        """
        if self._snapshot is None:
            return True
        return _canon(_core(self.cfg)) != _canon(_core(self._snapshot))

    def get(self, key, default=None):
        found, value = _get(self.cfg, key.split("."))
        return value if found else default

    def validate(self):
        """(errors, warnings) from the same validator the stages run at import."""
        return LC.validate(self.cfg, self.path or "<project>")

    def config_sha(self):
        return config_sha(self.cfg)

    # ---- changing -------------------------------------------------------------

    def _check_writable(self):
        if self.read_only:
            raise ProjectError("this project was written by a newer version of the app and "
                               "is open read-only; update the app to edit it")

    def set(self, key, value, by="app", record=True):
        """Set one dotted key. Returns True if it changed; records the decision unless `record` is False."""
        self._check_writable()
        parts = key.split(".")
        found, old = _get(self.cfg, parts)
        value = copy.deepcopy(value)
        if found and _canon(old) == _canon(value):
            return False
        node = self.cfg
        for p in parts[:-1]:
            node = node.setdefault(p, {})
            if not isinstance(node, dict):
                raise ProjectError("%s is not a block, so %s cannot be set" % (p, key))
        node[parts[-1]] = value
        if not record:
            return True
        rec = {"at": _now(), "key": key, "by": by}
        if _redacted(key):
            rec["redacted"] = True
        else:
            rec["from"] = _brief(old) if found else None
            rec["to"] = _brief(value)
        block = self.block
        block.setdefault("decisions", []).append(rec)
        _cap(block)
        return True

    def unset(self, key, by="app", record=True):
        """Remove one dotted key. Returns True if it was there."""
        self._check_writable()
        parts = key.split(".")
        found, old = _get(self.cfg, parts)
        if not found:
            return False
        _, parent = _get(self.cfg, parts[:-1]) if len(parts) > 1 else (True, self.cfg)
        del parent[parts[-1]]
        if record:
            rec = {"at": _now(), "key": key, "by": by}
            if _redacted(key):
                rec["redacted"] = True
            else:
                rec["from"], rec["to"] = _brief(old), None
            self.block.setdefault("decisions", []).append(rec)
            _cap(self.block)
        return True

    def apply_edits(self, edits, by="app"):
        """Set several keys; returns the keys that changed."""
        return [k for k, v in edits.items() if self.set(k, v, by)]

    def note_detected(self, file, channels):
        """Keep what a CZI file said about its channels. Evidence only: nothing reads it back."""
        self._check_writable()
        entry = {"read_at": _now(), "file": os.path.abspath(file), "channels": copy.deepcopy(channels)}
        block = self.block
        block["detected"] = [d for d in block.get("detected", [])
                             if d.get("file") != entry["file"]] + [entry]
        _cap(block)

    def note_slides(self, report, notes=(), problems=(), full_report="slide_metadata.json",
                    source_dir=None):
        """Keep the summary of what the slide headers said. Evidence only.

        The report minus its `fields` table (which lives in `full_report`, a file
        name relative to the output folder), with the notes and problems found,
        and the slides folder it was read from (`source_dir`), so a summary of
        another folder is not shown as this one's. Over 64 KB, the list of fields
        that differ is dropped (`varying_dropped`), then the list of uses
        (`uses_dropped`); both stay in the full file.
        """
        self._check_writable()
        entry = {k: copy.deepcopy(v) for k, v in dict(report).items() if k != "fields"}
        entry["notes"] = [str(n) for n in notes]
        entry["problems"] = [str(n) for n in problems]
        entry["read_at"] = _now()
        entry["full_report"] = full_report
        if source_dir:
            entry["source_dir"] = str(source_dir)
        for key in ("varying", "uses"):
            if len(json.dumps(entry, default=str)) <= 64 * 1024:
                break
            if entry.pop(key, None) is not None:
                entry[key + "_dropped"] = True
        self.block["slides"] = entry

    def set_notes(self, text):
        self._check_writable()
        self.block["notes"] = str(text)

    # ---- stage options ----------------------------------------------------------

    def stage_options(self, stage):
        """{flag: value} the operator chose for one stage; {} when untouched.

        Kept in the `project` block, which no stage reads: the app turns them into
        command-line flags when it runs the stage, so a stage run from a terminal
        is unaffected by what was chosen here.
        """
        return dict(((self.block.get("stage_options") or {}).get(stage)) or {})

    def set_stage_options(self, stage, values):
        """Remember the options chosen for one stage; an empty dict forgets them."""
        self._check_writable()
        held = self.block.setdefault("stage_options", {})
        if values:
            held[stage] = dict(values)
        else:
            held.pop(stage, None)
        if not held:
            self.block.pop("stage_options", None)

    # ---- portable paths -------------------------------------------------------

    def folder(self):
        return os.path.dirname(self.path) if self.path else None

    def portable_record(self, folder=None):
        """{key: path relative to the project's folder} for every path setting that lies inside it.

        The settings themselves stay absolute - the stages, including the Fiji ones that
        read this JSON directly, never see anything else. This is the record that lets
        the project find its data again after the whole folder has moved.
        """
        folder = folder or self.folder()
        if not folder:
            return {}
        out = {}
        for key in path_keys():
            value = self.get(key)
            if isinstance(value, str) and value.strip() and os.path.isabs(value) and _inside(value, folder):
                out[key] = os.path.relpath(value, folder).replace("\\", "/")
        return out

    def reanchor(self):
        """Re-point absolute paths that no longer exist, using the portable record.

        For each recorded key: if the stored path is gone but the same place relative to
        this file exists, the project (and its data) was moved together, so the setting
        follows. A stored path that still exists is never touched - it may be a different
        copy on purpose. Returns [(key, old, new)]; each is a recorded decision, and the
        project is unsaved until the person saves.
        """
        folder = self.folder()
        record = self.block.get("portable")
        if not folder or not isinstance(record, dict):
            return []
        moved = []
        for key, rel in record.items():
            if key not in path_keys() or not isinstance(rel, str) or os.path.isabs(rel):
                continue
            current = self.get(key)
            candidate = os.path.normpath(os.path.join(folder, rel))
            if not _inside(candidate, folder):
                continue
            if isinstance(current, str) and current and os.path.exists(current):
                continue
            if os.path.exists(candidate) and (not isinstance(current, str) or _norm(current) != _norm(candidate)):
                self.set(key, candidate, by="portable paths")
                moved.append((key, current, candidate))
        return moved

    def _stamp_portable(self, folder):
        record = self.portable_record(folder)
        block = self.block
        if record or "portable" in block:
            block["portable"] = record

    # ---- saving ---------------------------------------------------------------

    def _check_disk_unchanged(self):
        if self.path and self._stamp is not None and _stamp(self.path) != self._stamp:
            raise ProjectConflict(
                "%s changed on disk since this project last read or wrote it. Reload it, or "
                "save over it with force=True." % self.path)

    def save(self, force=False):
        self._check_writable()
        if not self.path:
            raise ProjectError("this project has no file yet; use save_as")
        if not force:
            self._check_disk_unchanged()
        block = self.block
        block["modified"] = _now()
        block["app_version"] = code_version()
        self._stamp_portable(self.folder())
        ls_jsonio.write_json_atomic(self.path, self.cfg, backups=BACKUPS, compact=COMPACT)
        self._snapshot = copy.deepcopy(self.cfg)
        self._stamp = _stamp(self.path)
        LC.clear_cache()
        return self.path

    def save_as(self, path, overwrite=False):
        self._check_writable()
        path = os.path.abspath(path)
        if os.path.exists(path) and not overwrite and path != self.path:
            raise ProjectError("%s already exists" % path)
        self.path = path
        self._stamp = _stamp(path)             # whatever is there is ours to replace, by choice
        return self.save(force=True)

    def revert(self):
        """Throw away unsaved changes: the config goes back to what was last saved."""
        if self._snapshot is None:
            raise ProjectError("this project has never been saved, so there is nothing to revert to")
        self.cfg = copy.deepcopy(self._snapshot)

    # ---- curation -----------------------------------------------------------------

    def curation_keys(self):
        cur = self.cfg.get("curation")
        return sorted(cur) if isinstance(cur, dict) else []

    def curation_text(self, key):
        """What a curator reads for `key`, or None."""
        cur = self.cfg.get("curation")
        if isinstance(cur, dict) and key in cur:
            return curation_text(cur[key])
        return None

    def _synced(self):
        syn = self.block.get("synced")
        return syn if isinstance(syn, dict) else {}

    def write_curation(self, key, text, out_root=None):
        """Store one curator's state in the project file, now.

        Curators save on every decision, so this does not wait for File > Save and never
        commits anything else: it rewrites only the `curation` block (and the sync marker)
        of the file as it is on disk, like `record_run`. The in-memory project follows, so
        the project is not "unsaved" because a decision was made. With `out_root`, the plain
        copy the stages read is written too. Returns True if the value changed.
        """
        self._check_writable()
        if not _valid_key(key):
            raise ProjectError("%r is not a usable curation name" % (key,))
        value = curation_value(text)
        out_text = curation_text(value)
        sha = curation_hash(out_text)
        cur = self.cfg.get("curation")
        unchanged = isinstance(cur, dict) and key in cur and curation_text(cur[key]) == out_text
        # The plain copy first, then ONE write of the project file that records both the value and
        # (if the copy was written) that they agree. `synced` is the value the project and its copy
        # last AGREED on, so it moves only when the copy is written too; a write that leaves the
        # copy behind is exactly how a stale copy is told from an edited one.
        if out_root:
            _write_text_atomic(os.path.join(_curation_dir(out_root), key + ".json"), out_text)
        mark = sha if out_root else None
        if mark is not None and self._synced().get(key) == mark:
            mark = None
        if not unchanged or mark is not None:
            if self.path and os.path.exists(self.path):
                self._patch_disk_curation(key, value, mark, unchanged)
            else:
                self.cfg.setdefault("curation", {})[key] = value
                if mark is not None:
                    self.block.setdefault("synced", {})[key] = mark
        return not unchanged

    def _patch_disk_curation(self, key, value, mark, unchanged):
        try:
            with open(self.path, encoding="utf-8") as fh:
                disk = json.load(fh)
        except (OSError, ValueError) as e:
            raise ProjectError("%s could not be read to record curation: %s" % (self.path, e))
        if not isinstance(disk, dict):
            raise ProjectError("%s is not a project" % self.path)
        block = disk.get("project")
        if block is None:
            seed = (self._snapshot or self.cfg).get("project")
            block = disk["project"] = copy.deepcopy(seed) if isinstance(seed, dict) else _new_block()
        elif not isinstance(block, dict) or (block.get("project_version") or 1) > PROJECT_VERSION:
            raise ProjectError("%s: its project block cannot be extended by this version" % self.path)
        if not unchanged:
            disk.setdefault("curation", {})[key] = value
        if mark is not None:
            block.setdefault("synced", {})[key] = mark
        ls_jsonio.write_json_atomic(self.path, disk, compact=COMPACT)
        # Memory follows disk. The value object is shared between the working copy and the snapshot
        # on purpose (copying a few hundred KB per decision is the slow part); nothing edits a curation
        # value in place - it is only ever replaced whole.
        for target in (self.cfg, self._snapshot):
            if target is not None:
                if not unchanged:
                    target.setdefault("curation", {})[key] = value
                if mark is not None:
                    target.setdefault("project", _new_block()).setdefault("synced", {})[key] = mark
        self._stamp = _stamp(self.path)
        LC.clear_cache()

    def _mark_synced(self, key, sha):
        """Record that the copy on disk and the project agree on `sha`, without a decision or a dirty mark."""
        if self._synced().get(key) == sha:
            return
        if self.path and os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as fh:
                    disk = json.load(fh)
                blk = disk.get("project")
                if isinstance(blk, dict) and (blk.get("project_version") or 1) <= PROJECT_VERSION:
                    blk.setdefault("synced", {})[key] = sha
                    ls_jsonio.write_json_atomic(self.path, disk, compact=COMPACT)
                    self._stamp = _stamp(self.path)
            except (OSError, ValueError):
                return
        for target in (self.cfg, self._snapshot):
            if target is not None:
                target.setdefault("project", _new_block()).setdefault("synced", {})[key] = sha

    def reconcile_curation(self, out_root):
        """Compare the project's curation with the plain copies under out_root. Changes nothing.

        Three-way, against the hash recorded the last time they agreed (`project.synced`):
        a copy untouched since then is stale (restore it); a project untouched since then
        means the copy was edited by something else on purpose (adopt it); both moved is a
        conflict for a person to settle. Files that exist only in out_root (curation made
        before the project held it) are adopted.
        """
        result = Reconcile()
        files = _files_in(out_root)
        synced = self._synced()
        for key in sorted(set(self.curation_keys()) | set(files)):
            p_text = self.curation_text(key)
            f_text = None
            if key in files:
                try:
                    f_text = curation_text(curation_value(_read_text(files[key])))
                except (OSError, UnicodeDecodeError):
                    f_text = None
            if p_text is None and f_text is None:
                continue
            if p_text is None:
                result.adopt.append(key)
            elif f_text is None:
                result.restore.append(key)
            else:
                hp, hf, hs = curation_hash(p_text), curation_hash(f_text), synced.get(key)
                if hp == hf:
                    result.ok.append(key)
                elif hs == hf:
                    result.restore.append(key)
                elif hs == hp:
                    result.adopt.append(key)
                else:
                    result.conflict.append(key)
        return result

    def apply_reconcile(self, out_root, result=None, resolve=None):
        """Carry out `reconcile_curation`. `resolve` maps a conflicting key to "project" or "files".

        The version that loses is first saved in `curation/_backup_<stamp>/<key>.json`. A conflict
        with no answer is left alone. Returns {"restored": [...], "adopted": [...], "backup": dir|None}.
        """
        result = result or self.reconcile_curation(out_root)
        resolve = resolve or {}
        files = _files_in(out_root)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = os.path.join(_curation_dir(out_root), "_backup_" + stamp)
        used = []
        done = {"restored": [], "adopted": [], "backup": None}

        def back_up(key, text):
            _write_text_atomic(os.path.join(backup, key + ".json"), text)
            used.append(key)

        for key in result.restore:
            self.write_curation(key, self.curation_text(key), out_root)
            done["restored"].append(key)
        for key in result.adopt:
            self.write_curation(key, _read_text(files[key]), out_root)
            done["adopted"].append(key)
        for key in result.conflict:
            side = resolve.get(key)
            if side == "project":
                back_up(key, _read_text(files[key]))
                self.write_curation(key, self.curation_text(key), out_root)
                done["restored"].append(key)
            elif side == "files":
                back_up(key, self.curation_text(key))
                self.write_curation(key, _read_text(files[key]), out_root)
                done["adopted"].append(key)
        if used:
            done["backup"] = backup
        for key in result.ok:
            self._mark_synced(key, curation_hash(self.curation_text(key)))
        if done["restored"] or done["adopted"]:
            readme = os.path.join(_curation_dir(out_root), "README.txt")
            if not os.path.exists(readme):
                _write_text_atomic(readme, README)
        return done

    def materialize_curation(self, out_root):
        """Make the plain copies match the project (what stages and the curator seed read).

        Only rewrites a copy that is missing or stale; never overwrites one that someone edited
        (that is a reconcile conflict, reported by `reconcile_curation`). Returns the keys written.
        """
        result = self.reconcile_curation(out_root)
        wrote = []
        for key in result.restore:
            self.write_curation(key, self.curation_text(key), out_root)
            wrote.append(key)
        for key in result.ok:
            self._mark_synced(key, curation_hash(self.curation_text(key)))
        if wrote:
            readme = os.path.join(_curation_dir(out_root), "README.txt")
            if not os.path.exists(readme):
                _write_text_atomic(readme, README)
        return wrote

    def reload(self):
        """Read the file again, dropping anything unsaved. For when something else wrote it
        on purpose (the atlas import writes the config itself)."""
        if not self.path:
            raise ProjectError("this project has no file to reload")
        fresh = Project.open(self.path)
        self.cfg, self.read_only = fresh.cfg, fresh.read_only
        self._snapshot, self._stamp = fresh._snapshot, fresh._stamp
        self.reanchored = fresh.reanchored

    # ---- the run history ------------------------------------------------------

    def record_run(self, stage, ok, started, ended, options=None, inputs=None):
        """Append a run to `project.runs` on disk and in memory, saving nothing else.

        Stages read the file from disk, so a run is always of what is on disk; the
        history row must therefore not drag the person's unsaved edits along with
        it. This rewrites only the `runs` list of the file as it is.

        `inputs` is what the stage read when it started (app/workflow.fingerprint),
        which is how the app later tells that it is out of date.
        """
        rec = {"stage": stage, "started": started, "ended": ended, "ok": bool(ok),
               "config_sha": None, "code": code_version()}
        if options:
            rec["options"] = dict(options)
        if inputs:
            rec["inputs"] = inputs
        if not self.path or not os.path.exists(self.path):
            rec["config_sha"] = self.config_sha()
            self.block.setdefault("runs", []).append(rec)
            _cap(self.block)
            return rec
        try:
            with open(self.path, encoding="utf-8") as fh:
                disk = json.load(fh)
        except (OSError, ValueError) as e:
            raise ProjectError("%s could not be read to record the run: %s" % (self.path, e))
        if not isinstance(disk, dict):
            raise ProjectError("%s is not a project" % self.path)
        rec["config_sha"] = config_sha(disk)
        block = disk.get("project")
        if block is None:
            # A file with no record yet gets the identity this project already has,
            # so memory and disk agree about who they are.
            seed = (self._snapshot or self.cfg).get("project")
            block = disk["project"] = copy.deepcopy(seed) if isinstance(seed, dict) else _new_block()
        elif not isinstance(block, dict) or (block.get("project_version") or 1) > PROJECT_VERSION:
            raise ProjectError("%s: its project block cannot be extended by this version" % self.path)
        block.setdefault("runs", []).append(rec)
        _cap(block)
        ls_jsonio.write_json_atomic(self.path, disk, compact=COMPACT)
        # Memory follows disk for runs, and for what a stage wrote; everything else the person
        # has not saved stays unsaved.
        for target in (self.cfg, self._snapshot):
            if target is not None:
                tb = target.setdefault("project", _new_block())
                tb["runs"] = copy.deepcopy(block["runs"])
                for dotted in STAGE_WRITTEN:
                    _carry(disk, target, dotted.split("."))
        self._stamp = _stamp(self.path)
        LC.clear_cache()
        return rec
