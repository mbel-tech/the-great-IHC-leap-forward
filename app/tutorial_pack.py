"""The tutorial's demo pack and the tutorial study made from it.

A pack is a folder (or a .zip of one):

    pack.json            {"version", "slide", "practice_sections", "credits"}
    slides/*.czi         a few whole sections cut from a real slide
    study/study.json     the study's settings, paths left blank
    study/sampling.xlsx  a sampling workbook with made-up groups
    ready/<stage id>/... ready-made results of the slow stages, laid out as
                         they sit in an output folder
    answer/ls_roi_curator_v1.json   the expert's curation of the practice sections

Starting the tutorial copies it into its own folder (tutorial_home) and opens
that as a normal study. Nothing here writes anywhere else, apart from the
tutorial settings file in the app's home folder.
"""

import json
import os
import shutil
import zipfile

ANSWER_KEY = "ls_roi_curator_v1"
PROGRESS_FILE = "tutorial.json"


class PackError(Exception):
    """A demo pack that cannot be used, with a message a person can act on."""


def tutorial_home():
    env = os.environ.get("LS_TUTORIAL_HOME")
    if env:
        return os.path.abspath(env)
    docs = None
    try:
        from PySide6.QtCore import QStandardPaths
        docs = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
    except Exception:                                   # noqa: BLE001
        docs = None
    docs = docs or os.path.join(os.path.expanduser("~"), "Documents")
    return os.path.join(docs, "The Great IHC Leap Forward", "Tutorial study")


# ---- settings (app home) -------------------------------------------------------

def _settings_path():
    import ls_config as LC
    return os.path.join(LC.app_home(), "tutorial.json")


def settings():
    try:
        with open(_settings_path(), encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(d):
    path = _settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    merged = settings()
    merged.update(d)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(merged, fh, indent=2)


# ---- finding and opening -----------------------------------------------------------

def find_pack(app_root):
    """The pack to use: LS_TUTORIAL_PACK, one shipped beside the app, or the one chosen before."""
    env = os.environ.get("LS_TUTORIAL_PACK")
    if env and os.path.exists(env):
        return env
    demo = os.path.join(app_root, "demo")
    if os.path.isfile(os.path.join(demo, "pack.json")):
        return demo
    if os.path.isdir(demo):
        zips = sorted(f for f in os.listdir(demo) if f.lower().endswith(".zip"))
        if zips:
            return os.path.join(demo, zips[0])
    chosen = settings().get("pack")
    if chosen and os.path.exists(chosen):
        return chosen
    return None


class Pack:
    def __init__(self, root, meta):
        self.root = root
        self.meta = meta

    @property
    def practice_sections(self):
        return list(self.meta.get("practice_sections") or [])

    def answer_curation(self):
        path = os.path.join(self.root, "answer", ANSWER_KEY + ".json")
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}


def _cache_dir():
    import ls_config as LC
    return os.path.join(LC.app_home(), "tutorial-pack")


def _extract(zpath):
    """Unzip the pack once into the app's home and reuse it while the zip is unchanged.

    Every start would otherwise leave a whole copy of the pack (slides and
    ready-made results) in the temp folder.
    """
    st = os.stat(zpath)
    stamp = f"{os.path.basename(zpath)}|{st.st_size}|{int(st.st_mtime)}"
    cache = _cache_dir()
    dest = os.path.join(cache, "pack")
    stamp_file = os.path.join(cache, "stamp.txt")
    try:
        with open(stamp_file, encoding="utf-8") as fh:
            if fh.read() == stamp:
                return _pack_root(dest)
    except OSError:
        pass
    if os.path.exists(stamp_file):
        os.remove(stamp_file)          # until this extraction is whole
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    real = os.path.realpath(dest)
    try:
        with zipfile.ZipFile(zpath) as z:
            for name in z.namelist():
                target = os.path.realpath(os.path.join(dest, name))
                if target != real and not target.startswith(real + os.sep):
                    raise PackError(f"{os.path.basename(zpath)} is not a usable demo pack: "
                                    f"it holds a file that would land outside its folder "
                                    f"({name}).")
            z.extractall(dest)
    except zipfile.BadZipFile:
        shutil.rmtree(dest, ignore_errors=True)
        raise PackError(f"{os.path.basename(zpath)} is not a zip file.")
    except PackError:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    with open(stamp_file, "w", encoding="utf-8") as fh:
        fh.write(stamp)
    return _pack_root(dest)


def _pack_root(dest):
    """Where pack.json is: the top, or the one folder a zip of the folder itself holds.

    A zip made by macOS Finder also holds __MACOSX/ and dot files beside it; they
    are not part of the pack.
    """
    if os.path.isfile(os.path.join(dest, "pack.json")):
        return dest
    subs = [d for d in os.listdir(dest) if os.path.isdir(os.path.join(dest, d))
            and d != "__MACOSX" and not d.startswith(".")]
    if len(subs) == 1 and os.path.isfile(os.path.join(dest, subs[0], "pack.json")):
        return os.path.join(dest, subs[0])
    return dest


def open_pack(path):
    if not path or not os.path.exists(path):
        raise PackError(f"No demo pack at {path}.")
    root = _extract(path) if os.path.isfile(path) else path
    meta_path = os.path.join(root, "pack.json")
    try:
        with open(meta_path, encoding="utf-8") as fh:
            meta = json.load(fh)
    except OSError:
        raise PackError(f"{path} is not a demo pack: it has no pack.json.")
    except ValueError:
        raise PackError(f"{path} is not a demo pack: its pack.json cannot be read.")
    for sub in ("slides", os.path.join("study", "study.json")):
        if not os.path.exists(os.path.join(root, sub)):
            raise PackError(f"{path} is not a complete demo pack: {sub} is missing.")
    return Pack(root, meta if isinstance(meta, dict) else {})


# ---- installing -------------------------------------------------------------------

KEEP_PATHS = ("source_dir", "out_root")


def clear_paths(cfg):
    """Drop every folder or file setting but the slides and the output folder.

    A study's settings can point outside the study (scene pictures written to a
    folder of their own, exports somewhere shared). Kept in the tutorial study,
    those would write into the real study's folders. Dropped rather than
    blanked: an empty optional folder is refused by the settings check, which
    would stop every stage. An allow-list, so a path setting added later is
    dropped too.
    """
    import ls_config as LC
    for key in LC.SPEC:
        if not key.type.startswith("path") or key.path in KEEP_PATHS:
            continue
        *parents, leaf = key.path.split(".")
        d = cfg
        for part in parents:
            d = d.get(part) if isinstance(d, dict) else None
        if isinstance(d, dict):
            d.pop(leaf, None)
    return cfg


def install(pack, home, fresh=False):
    """Make the tutorial study in `home` from the pack; returns its study file."""
    if os.path.isdir(home) and os.listdir(home):
        if not fresh:
            raise PackError(f"The tutorial study already exists in {home}.")
        old = home + ".old"
        if os.path.exists(old):
            shutil.rmtree(old)
        os.replace(home, old)
    slides, out = os.path.join(home, "slides"), os.path.join(home, "output")
    shutil.copytree(os.path.join(pack.root, "slides"), slides)
    os.makedirs(out, exist_ok=True)
    # What the pack carries from its source study (display ranges, tile fields,
    # the cut sections' hand rotations) starts the study's output folder.
    seed = os.path.join(pack.root, "output")
    if os.path.isdir(seed):
        shutil.copytree(seed, out, dirs_exist_ok=True)
    with open(os.path.join(pack.root, "study", "study.json"), encoding="utf-8") as fh:
        cfg = json.load(fh)
    cfg.pop("curation", None)
    # Again here, not only when the pack was cut: a pack can come from anywhere.
    clear_paths(cfg)
    cfg["source_dir"], cfg["out_root"] = slides, out
    study = os.path.join(out, "study.json")
    with open(study, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    book = os.path.join(pack.root, "study", "sampling.xlsx")
    if os.path.isfile(book):
        import ls_project as LP
        dst = os.path.join(home, "sampling.xlsx")
        shutil.copyfile(book, dst)
        proj = LP.Project.open(study)
        proj.set_stage_options("join_sampling", {"--xlsx": dst})
        proj.save(force=True)
    return study


def has_ready(pack, stage_ids):
    return any(os.path.isdir(os.path.join(pack.root, "ready", s)) for s in stage_ids)


def use_ready(pack, out_root, stage_ids):
    """Copy the pack's ready-made results for these stages in; returns what was copied.

    Copied without their old timestamps, so the stages read them as newer than
    their inputs rather than out of date.
    """
    copied = []
    for sid in stage_ids:
        base = os.path.join(pack.root, "ready", sid)
        for dirpath, _dirs, files in os.walk(base):
            for f in sorted(files):
                full = os.path.join(dirpath, f)
                rel = os.path.relpath(full, base)
                dst = os.path.join(out_root, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                # Whole or not at all: a stage counts as done once its file is there.
                shutil.copyfile(full, dst + ".part")
                os.replace(dst + ".part", dst)
                copied.append(rel)
    return copied


# ---- progress ---------------------------------------------------------------------

class Progress:
    """Where the learner is, saved beside the tutorial study so it resumes."""

    def __init__(self, path):
        self.path = path
        self.current = ""
        self.done = set()
        self.skipped = set()
        self.previous_study = ""

    @classmethod
    def load(cls, out_root):
        p = cls(os.path.join(out_root, PROGRESS_FILE))
        try:
            with open(p.path, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            return p
        p.current = d.get("current") or ""
        p.done = set(d.get("done") or [])
        p.skipped = set(d.get("skipped") or [])
        p.previous_study = d.get("previous_study") or ""
        return p

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"current": self.current, "done": sorted(self.done),
                       "skipped": sorted(self.skipped),
                       "previous_study": self.previous_study}, fh, indent=2)
        os.replace(tmp, self.path)
