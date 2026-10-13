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

#: Longest value recorded verbatim in a decision; a bigger one (a channel table)
#: is recorded as its size and a hash, so the trail stays readable and bounded.
BRIEF_LIMIT = 1500

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
    """A short hash of the config WITHOUT its `project` block.

    The run history lives in the block, so a hash that included it would change
    every time a stage finished and "the config changed" would be true forever.
    """
    return _sha({k: v for k, v in (cfg or {}).items() if k != "project"})


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


def _norm(path):
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def _inside(path, folder):
    """True if `path` is `folder` or below it (same drive; never raises across drives)."""
    try:
        return os.path.commonpath([_norm(path), _norm(folder)]) == _norm(folder)
    except ValueError:
        return False


def _stamp(path):
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


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
        return self._snapshot is None or _canon(self.cfg) != _canon(self._snapshot)

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

    def set_notes(self, text):
        self._check_writable()
        self.block["notes"] = str(text)

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
        ls_jsonio.write_json_atomic(self.path, self.cfg, backups=BACKUPS)
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

    def record_run(self, stage, ok, started, ended):
        """Append a run to `project.runs` on disk and in memory, saving nothing else.

        Stages read the file from disk, so a run is always of what is on disk; the
        history row must therefore not drag the person's unsaved edits along with
        it. This rewrites only the `runs` list of the file as it is.
        """
        rec = {"stage": stage, "started": started, "ended": ended, "ok": bool(ok),
               "config_sha": None, "code": code_version()}
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
        ls_jsonio.write_json_atomic(self.path, disk)
        # Memory follows disk for runs only; everything else the person has not saved stays unsaved.
        for target in (self.cfg, self._snapshot):
            if target is not None:
                tb = target.setdefault("project", _new_block())
                tb["runs"] = copy.deepcopy(block["runs"])
        self._stamp = _stamp(self.path)
        LC.clear_cache()
        return rec
