"""Settings to and from a JSON file, so a study's set-up can be reused or shared.

An export holds the study's settings and the app's own preferences, and nothing
that belongs to one machine or one dataset:

    left out always      the `project` block (history, runs, detected), `curation`,
                         stage-written blocks (detection.auto), `_comment` notes,
                         app lists such as recent projects
    left out by default  folder and file paths (they only make sense on the machine
                         that wrote them) and `groups` (the treatment key; a blinded
                         analysis must not read it)

Importing is a merge: every setting in the file replaces the open study's, a setting
the file does not hold stays as it is. Folders and the treatment key are kept unless
the person asks for the file's. The result is checked with the same validator the
stages run before anything changes, and the study file as it was is copied aside.

No Qt here; the dialogs are in app/settings_transfer.py.
"""

import copy
import datetime
import json
import os
import shutil

import ls_config as LC
import ls_project as LP

KIND = "ihc-settings"
VERSION = 1

#: Top-level blocks that are never settings to carry.
NEVER = ("project", "curation", "schema_version")
#: Blocks a stage writes; carrying them would claim a run that did not happen here.
STAGE_WRITTEN = LP.STAGE_WRITTEN
GROUP_KEYS = ("groups",)
#: settings.json entries that describe this machine, not a preference.
APP_MACHINE = ("schema_version", "recent_projects", "recent", "last_project",
               "active_study", "studies_dir")


class SettingsError(Exception):
    pass


def _drop(cfg, dotted):
    parts = dotted.split(".")
    node = cfg
    for p in parts[:-1]:
        node = node.get(p) if isinstance(node, dict) else None
        if node is None:
            return
    if isinstance(node, dict):
        node.pop(parts[-1], None)


def _strip_notes(obj):
    if isinstance(obj, dict):
        return {k: _strip_notes(v) for k, v in obj.items() if not str(k).startswith("_")}
    if isinstance(obj, list):
        return [_strip_notes(v) for v in obj]
    return obj


def study_settings(cfg, include_paths=False, include_groups=False):
    """The carryable part of a study config."""
    out = _strip_notes({k: copy.deepcopy(v) for k, v in cfg.items() if k not in NEVER})
    for key in STAGE_WRITTEN:
        _drop(out, key)
    if not include_paths:
        for key in LP.path_keys():
            _drop(out, key)
    if not include_groups:
        for key in GROUP_KEYS:
            out.pop(key, None)
    for key in [k for k, v in out.items() if v == {}]:
        del out[key]                         # a block emptied by the stripping says nothing
    return out


def app_preferences(settings=None):
    settings = LC.read_settings() if settings is None else settings
    return {k: copy.deepcopy(v) for k, v in settings.items() if k not in APP_MACHINE}


def build_export(project, include_paths=False, include_groups=False, include_app=True):
    data = {
        "kind": KIND,
        "settings_version": VERSION,
        "schema_version": LC.SCHEMA_VERSION,
        "exported": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "app_version": LP.code_version(),
        "from_study": project.name,
        "includes": {"paths": bool(include_paths), "groups": bool(include_groups)},
        "study": study_settings(project.cfg, include_paths, include_groups),
    }
    if include_app:
        data["app"] = app_preferences()
    return data


def write_export(path, data):
    import ls_jsonio
    ls_jsonio.write_json_atomic(path, data)
    return path


def read_export(path):
    """The file's contents, checked to be a settings export (or a bare study config)."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as e:
        raise SettingsError("%s could not be read: %s" % (path, e.strerror or e))
    except ValueError as e:
        raise SettingsError("%s is not valid JSON: %s" % (path, e))
    if not isinstance(data, dict):
        raise SettingsError("%s does not hold settings: its top level is not an object" % path)
    if data.get("kind") == KIND:
        if (data.get("settings_version") or 1) > VERSION:
            raise SettingsError("%s was written by a newer version of the app; update the app "
                                "to import it" % path)
        if not isinstance(data.get("study", {}), dict) or not isinstance(data.get("app", {}), dict):
            raise SettingsError("%s: `study` and `app` must be blocks of settings" % path)
        return data
    # A study config (or another project's study.json) works too: take its settings part.
    if "study" in data and isinstance(data.get("study"), dict) and "schema_version" in data:
        has_paths = any(LP._get(data, k.split("."))[0] for k in LP.path_keys())
        return {"kind": KIND, "settings_version": VERSION, "from_study":
                (data.get("study") or {}).get("name"),
                "includes": {"paths": has_paths, "groups": "groups" in data},
                "study": study_settings(data, include_paths=True, include_groups=True)}
    raise SettingsError("%s is not a settings export or a study file" % path)


def _flat(obj, prefix=""):
    """{dotted key: leaf value}; lists and empty blocks are leaves."""
    if isinstance(obj, dict) and obj:
        out = {}
        for k, v in obj.items():
            out.update(_flat(v, prefix + str(k) + "."))
        return out
    return {prefix[:-1]: obj}


def _merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge(dst[k], v)
        else:
            dst[k] = copy.deepcopy(v)


class Plan:
    """What an import would change, worked out before anything changes."""

    def __init__(self, changes, cfg, app, errors, warnings, kept):
        self.changes = changes       # [(dotted key, old or None, new)]
        self.cfg = cfg               # the study config as it would be
        self.app = app               # app preferences that would change, {key: value}
        self.errors = errors
        self.warnings = warnings
        self.kept = kept             # what was deliberately not taken from the file

    def __bool__(self):
        return bool(self.changes or self.app)


def plan_import(project, data, use_paths=False, use_groups=False, use_app=True):
    incoming = copy.deepcopy(data.get("study") or {})
    for k in NEVER:
        incoming.pop(k, None)
    for key in STAGE_WRITTEN:
        _drop(incoming, key)
    kept = []
    if not use_paths:
        for key in LP.path_keys():
            if LP._get(incoming, key.split("."))[0]:
                _drop(incoming, key)
                kept.append("folders")
    if not use_groups and "groups" in incoming:
        del incoming["groups"]
        kept.append("treatment groups")
    cfg = copy.deepcopy(project.cfg)
    _merge(cfg, incoming)

    before, after = _flat(_strip_notes({k: v for k, v in project.cfg.items() if k not in NEVER})), \
        _flat(_strip_notes({k: v for k, v in cfg.items() if k not in NEVER}))
    changes = []
    for key in sorted(after):
        if key not in before or LP._canon(before[key]) != LP._canon(after[key]):
            changes.append((key, before.get(key), after[key]))

    errors, warnings = LC.validate(cfg, project.path or "<import>")
    app = {}
    if use_app:
        current = LC.read_settings()
        for k, v in (data.get("app") or {}).items():
            if k not in APP_MACHINE and LP._canon(current.get(k)) != LP._canon(v):
                app[k] = v
    return Plan(changes, cfg, app, list(errors), list(warnings), sorted(set(kept)))


def backup_path(project, now=None):
    if not project.path:
        return None
    stamp = (now or datetime.datetime.now()).strftime("%Y%m%d-%H%M%S")
    stem, ext = os.path.splitext(project.path)
    return "%s.before-import-%s%s" % (stem, stamp, ext or ".json")


def apply_import(project, plan, by="settings import"):
    """Apply a plan that validated. Returns the backup's path (None for an unsaved study).

    The study changes in memory, one recorded decision per setting, and is saved by
    the person as any other edit is; the copy of the file as it was is written first.
    """
    if plan.errors:
        raise SettingsError("the imported settings do not validate:\n" + "\n".join(plan.errors))
    backup = backup_path(project)
    if backup and os.path.exists(project.path):
        shutil.copy2(project.path, backup)
    else:
        backup = None
    # Set the top-level blocks that changed: one decision per block keeps the trail readable.
    tops = []
    for key, _, _ in plan.changes:
        top = key.split(".")[0]
        if top not in tops:
            tops.append(top)
    for top in tops:
        project.set(top, plan.cfg[top], by=by)
    if plan.app:
        LC.write_settings(plan.app)
    return backup
