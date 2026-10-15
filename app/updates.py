"""Finding, choosing and fetching an update. No Qt here; update_ui.py is the window side.

An installed copy has VERSION.json beside runtime/ (written by
packaging/make_update.py at build time). Each release on GitHub carries
update-<v>.json and the code and atlas zips it names. Comparing the two gives
one of three answers:

    current    nothing newer (or the newest is older, or not a numbered version)
    code       same libraries: the zips can replace app/, scripts/, analysis/
               (and atlas/ when the plates changed) in place
    installer  the libraries, Python or R changed, or the release carries no
               update package: only the Setup .exe can bring it

stage() downloads and unpacks into <install>/updates/new-<v>/ without touching
the live copy; update_apply.py does the swap after the app has closed.
Nothing here ever writes outside <install>/updates/.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from dataclasses import dataclass, field

REPO_SLUG = "mbel-tech/the-great-IHC-leap-forward"
API_URL = f"https://api.github.com/repos/{REPO_SLUG}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO_SLUG}/releases/latest"
USER_AGENT = "The-Great-IHC-Leap-Forward-updater"
INSTALLER_PREFIX = "The-Great-IHC-Leap-Forward-Setup-"     # Windows: ...-Setup-<v>.exe
MAC_INSTALLER_SUFFIX = "-mac.dmg"                         # macOS: ...-<v>-mac.dmg


class UpdateError(Exception):
    """Anything that stops a check or a download, worded for the person reading it."""


@dataclass
class Decision:
    kind: str                       # "current" | "code" | "installer"
    version: str = ""
    notes: str = ""
    page_url: str = RELEASES_PAGE
    installer_url: str = None
    downloads: list = field(default_factory=list)   # [{name, url, size, sha256}]


# ---------------------------------------------------------------- what is installed

def runtime_python(root, platform=None):
    """The bundled interpreter: runtime\\python.exe on Windows, runtime/bin/python3 in the Mac app."""
    if (platform or sys.platform) == "win32":
        return os.path.join(root, "runtime", "python.exe")
    return os.path.join(root, "runtime", "bin", "python3")


def install_root():
    """The install folder when this copy was installed, else None (running from source).

    In the Mac app that is Contents/Resources, which holds the same layout.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if installed_info(root) and os.path.exists(runtime_python(root)):
        return root
    return None


def is_installer(name, platform=None):
    """Is this release asset the full installer for this platform?"""
    name = str(name or "")
    if (platform or sys.platform) == "darwin":
        return name.startswith("The-Great-IHC-Leap-Forward-") and name.endswith(MAC_INSTALLER_SUFFIX)
    return name.startswith(INSTALLER_PREFIX)


def installed_info(root):
    try:
        with open(os.path.join(root, "VERSION.json"), encoding="utf-8") as fh:
            info = json.load(fh)
    except (OSError, ValueError):
        return None
    return info if isinstance(info, dict) and info.get("version") else None


def parse_version(tag):
    """'v1.2.3' -> (1, 2, 3), trailing zeros dropped so v1.0 == v1; None if not numeric."""
    text = str(tag or "").strip().lstrip("vV")
    try:
        parts = [int(p) for p in text.split(".")]
    except ValueError:
        return None
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


# ---------------------------------------------------------------- asking GitHub

def _open(url, timeout):
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)


def _get_json(url, timeout):
    try:
        with _open(url, timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:                                  # noqa: BLE001
        raise UpdateError(f"Could not reach GitHub ({type(e).__name__}: {e})") from e


def fetch_latest(timeout=5, url=API_URL):
    return _get_json(url, timeout)


def _asset_url(release, name):
    for a in release.get("assets") or []:
        if a.get("name") == name:
            return a.get("browser_download_url")
    return None


def fetch_manifest(release, timeout=10):
    """The release's update-<v>.json, or None if it carries none."""
    v = str(release.get("tag_name", "")).lstrip("vV")
    url = _asset_url(release, f"update-{v}.json")
    return _get_json(url, timeout) if url else None


def decide(info, release, manifest, platform=None):
    tag = release.get("tag_name", "")
    new, have = parse_version(tag), parse_version(info.get("version"))
    version = str(tag).lstrip("vV")
    if new is None or have is None or new <= have:
        return Decision("current", version=info.get("version", ""))

    d = Decision("installer", version=version, notes=release.get("body") or "",
                 page_url=release.get("html_url") or RELEASES_PAGE)
    d.installer_url = next((a.get("browser_download_url") for a in release.get("assets") or []
                            if is_installer(a.get("name"), platform)), None)
    if (not manifest or parse_version(manifest.get("version")) != new
            or manifest.get("runtime_fingerprint") != info.get("runtime_fingerprint")):
        return d

    wanted = ["code"]
    if manifest.get("atlas_fingerprint") != info.get("atlas_fingerprint"):
        wanted.append("atlas")
    downloads = []
    for kind in wanted:
        entry = (manifest.get("packages") or {}).get(kind)
        url = entry and _asset_url(release, entry.get("name"))
        if not url:
            return d
        downloads.append(dict(entry, url=url))
    d.kind, d.downloads = "code", downloads
    return d


def _writable(root):
    """Can this account write the install folder? (Not under Program Files, say.)"""
    probe = os.path.join(root, "updates", ".write-test")
    try:
        os.makedirs(os.path.dirname(probe), exist_ok=True)
        with open(probe, "w") as fh:
            fh.write("")
        os.remove(probe)
        return True
    except OSError:
        return False


def check(root, timeout=5, url=API_URL):
    info = installed_info(root)
    if not info:
        raise UpdateError("This copy has no VERSION.json, so it cannot tell which version it is.")
    release = fetch_latest(timeout, url)
    try:
        manifest = fetch_manifest(release, timeout)
    except UpdateError:
        manifest = None             # the installer still works; say that rather than fail
    d = decide(info, release, manifest)
    if d.kind == "code" and not _writable(root):
        # Installed for all users: only the installer, run as administrator, can
        # write there.
        d.kind, d.downloads = "installer", []
    if d.kind == "code" and sys.platform == "darwin":
        # Swapping code inside a signed .app breaks its signature, and macOS
        # may then refuse to open it. Until that is tried on a real Mac, Mac
        # copies update by downloading the new disk image.
        d.kind, d.downloads = "installer", []
    return d


# ---------------------------------------------------------------- downloading

def updates_dir(root):
    return os.path.join(root, "updates")


def _download(entry, dest, progress, timeout=60):
    h = hashlib.sha256()
    size = 0
    part = dest + ".part"
    try:
        with _open(entry["url"], timeout) as r, open(part, "wb") as fh:
            while True:
                block = r.read(1 << 20)
                if not block:
                    break
                fh.write(block)
                h.update(block)
                size += len(block)
                if progress:
                    progress(entry["name"], size, entry.get("size") or 0)
    except Exception as e:                                  # noqa: BLE001
        raise UpdateError(f"Download of {entry['name']} failed ({type(e).__name__}: {e})") from e
    if size != entry.get("size") or h.hexdigest() != entry.get("sha256"):
        raise UpdateError(f"{entry['name']} arrived damaged (size or checksum does not match). "
                          "Nothing was changed; try again later.")
    os.replace(part, dest)


def _extract(zpath, dest):
    base = os.path.realpath(dest)
    with zipfile.ZipFile(zpath) as z:
        for name in z.namelist():
            target = os.path.realpath(os.path.join(dest, name))
            if not (target == base or target.startswith(base + os.sep)):
                raise UpdateError(f"{os.path.basename(zpath)} holds an unsafe path: {name}")
        z.extractall(dest)


def stage(root, decision, progress=None):
    """Download and unpack the update into updates/new-<v>; return that folder.

    On any failure the download and the half-made folder are removed and the
    live install is as it was.
    """
    up = updates_dir(root)
    dl = os.path.join(up, "download")
    new = os.path.join(up, f"new-{decision.version}")
    for stale in [dl] + [os.path.join(up, n) for n in (os.listdir(up) if os.path.isdir(up) else [])
                         if n.startswith("new-")]:
        shutil.rmtree(stale, ignore_errors=True)
    os.makedirs(dl)
    try:
        os.makedirs(new)
        for entry in decision.downloads:
            zpath = os.path.join(dl, os.path.basename(entry["name"]))
            _download(entry, zpath, progress)
            _extract(zpath, new)
        info = installed_info(new)
        if not info or parse_version(info["version"]) != parse_version(decision.version):
            raise UpdateError(f"The package says it is version "
                              f"{(info or {}).get('version', '?')}, not {decision.version}.")
        if not os.path.exists(os.path.join(new, "app", "__main__.py")):
            raise UpdateError("The package has no app/__main__.py.")
    except BaseException:
        shutil.rmtree(new, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(dl, ignore_errors=True)
    return new


# ---------------------------------------------------------------- handing over

def launch_apply(root, new_dir=None, mode="apply"):
    """Start update_apply.py from a copy outside app/ (which it replaces) and return.

    The caller quits the app straight after; the helper waits for this process
    to exit before it moves anything.
    """
    up = updates_dir(root)
    os.makedirs(up, exist_ok=True)
    helper = os.path.join(up, "update_apply.py")
    shutil.copyfile(os.path.join(os.path.dirname(os.path.abspath(__file__)), "update_apply.py"),
                    helper)
    python = runtime_python(root)
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([python, "-I", helper, mode, root, new_dir or "-", str(os.getpid())],
                     cwd=root, creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


def can_undo(root):
    return installed_info(os.path.join(updates_dir(root), "previous")) is not None


def last_result(root):
    """What the helper reported about the last update, once; None if nothing new."""
    path = os.path.join(updates_dir(root), "last-result.txt")
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read().strip()
        os.remove(path)
    except OSError:
        return None
    return text or None
