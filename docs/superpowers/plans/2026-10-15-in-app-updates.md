# In-app updates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The installed Windows app finds new releases on GitHub and installs code-only updates itself, with rollback, and asks for the installer only when libraries changed.

**Architecture:** The tag workflow attaches a code zip and an `update-<v>.json` manifest to each release. `app/updates.py` (stdlib, no Qt) checks the latest release, decides, downloads and stages. `app/update_apply.py` (stdlib, stand-alone) runs after the app exits, swaps folders, checks, rolls back on failure and restarts. `app/update_ui.py` is the Qt layer (Help menu, banner, dialog).

**Tech Stack:** Python 3.13 stdlib (`urllib`, `zipfile`, `hashlib`, `json`, `subprocess`), PySide6, PowerShell, GitHub Actions, Inno Setup.

**Spec:** `docs/superpowers/specs/2026-10-15-in-app-updates-design.md`

## Global Constraints

- Repo for the API: `mbel-tech/the-great-IHC-leap-forward`; endpoint `https://api.github.com/repos/mbel-tech/the-great-IHC-leap-forward/releases/latest`; header `User-Agent: The-Great-IHC-Leap-Forward-updater`; start-up check timeout 5 s, silent on any error.
- Asset names: `The-Great-IHC-Leap-Forward-code-<version>.zip`, `update-<version>.json`, installer `The-Great-IHC-Leap-Forward-Setup-<version>.exe`. `<version>` is the tag without the leading `v`.
- Managed parts (the only things swapped): `app`, `scripts`, `analysis`, `atlas`, `config.example.json`, `README.md`, `VERSION.json`.
- Fingerprint files, in this order: `requirements.txt`, `requirements-czi.txt`, `packaging/windows/requirements-bundle.txt`, `packaging/windows/build.ps1`, `packaging/windows/sitecustomize.py`. SHA-256 over `name\0content\0` for each, line endings normalised to `\n`.
- `VERSION.json` = `{"version": "<v>", "runtime_fingerprint": "<hex>"}`.
- Updater writes only under `<install>\updates\` and the managed parts. One previous version kept in `updates\previous\`.
- Versions compare as tuples of ints split on `.` after stripping `v`; a non-numeric part makes the release ignored.
- Installed = `<root>/VERSION.json` and `<root>/runtime/python.exe` both exist. Otherwise updates are off.
- User-facing words: "Version X is ready to install", "Update now…", "You have the newest version", "Updated to X", "The update to X failed and was undone".

## Review Focus

- Laptop offline or GitHub rate-limited on start: no dialog, no delay to the window (check runs in a thread). Test in Task 2.
- Download cut off half way or hash mismatch: nothing in the install changes, staged files removed, clear error. Test in Task 2.
- A managed part locked or missing mid-swap (antivirus, Explorer open on the folder): every part already moved is put back. Test in Task 3.
- The new code fails to import: rollback, the app restarts on the old version and says so. Test in Task 3.
- A release with no code package (or an older one than installed): treated as installer-needed or up to date, never a downgrade. Test in Task 2.

---

### Task 1: Update package builder

**Files:**
- Create: `packaging/make_update.py`
- Test: `tests/test_updates.py` (package section)

**Interfaces:**
- Produces: `runtime_fingerprint(repo: str) -> str`; `write_version_file(dest_dir: str, version: str, repo: str) -> str` (path); `build_package(repo: str, version: str, out_dir: str) -> tuple[str, str]` (zip path, manifest path). CLI: `python packaging/make_update.py --version V --out DIR` and `--version-file-only DIR`.
- Manifest keys: `version`, `package`, `size`, `sha256`, `runtime_fingerprint`.

- [ ] **Step 1: Tests** `package_contents` (zip holds `app/__main__.py`, `scripts/`, `atlas/`, `VERSION.json`, no `__pycache__`), `manifest_matches_zip` (size and sha256 equal the file), `fingerprint_stable` (same tree twice → same value; CRLF copy → same), `fingerprint_sensitive` (append a line to a copy of requirements.txt in a temp repo → different).
- [ ] **Step 2:** Run `python3 tests/test_updates.py`, expect import failure.
- [ ] **Step 3:** Implement with `zipfile.ZIP_DEFLATED`, walking the managed parts, skipping `__pycache__` and `*.pyc`.
- [ ] **Step 4:** Run tests, expect pass. Commit.

### Task 2: Check, decide, download, stage (`app/updates.py`)

**Files:**
- Create: `app/updates.py`
- Test: `tests/test_updates.py` (check section)

**Interfaces:**
- Consumes: manifest keys from Task 1.
- Produces:
  - `install_root() -> str | None` (folder above `app/` when installed, else None); `installed_info(root) -> dict | None` (VERSION.json).
  - `parse_version(tag: str) -> tuple[int, ...] | None`.
  - `fetch_latest(timeout=5, url=API_URL) -> dict` (the release JSON; raises `UpdateError`).
  - `Decision` dataclass: `kind: str` (`"current" | "code" | "installer"`), `version: str`, `notes: str`, `page_url: str`, `package_url: str | None`, `manifest_url: str | None`, `installer_url: str | None`.
  - `decide(info: dict, release: dict, manifest: dict | None) -> Decision`; `fetch_manifest(release, timeout) -> dict | None`.
  - `check(root, timeout=5) -> Decision` (fetch + manifest + decide).
  - `stage(root, decision, progress=None) -> str` (path of `updates/new-<v>`; raises `UpdateError` and leaves nothing behind on failure).
  - `launch_apply(root, new_dir, mode="apply") -> None` (copies `update_apply.py` to `updates/`, starts `runtime/python.exe -I updates/update_apply.py <mode> <root> <new_dir> <pid>` detached with `CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS` on Windows).
  - `last_result(root) -> str | None` reads and deletes `updates/last-result.txt`.

- [ ] **Step 1: Tests** `version_order` (`v1` < `v1.1` < `v1.2.3`, `v1.0` == `v1`, `"nightly"` → None), `decide_current` (same version → current), `decide_older_release` (release older → current), `decide_code` (newer, same fingerprint → code), `decide_installer_fingerprint` (different → installer, installer_url set), `decide_installer_no_package` (manifest None → installer), `check_offline` (url to closed port → `UpdateError`, under 6 s), `stage_ok` (file:// URLs to a Task 1 package → `new-<v>/app/__main__.py` exists), `stage_bad_hash` (manifest sha256 altered → `UpdateError`, `updates/` holds no `new-*` and no download), `stage_wrong_version` (zip VERSION.json differs from manifest → `UpdateError`).
- [ ] **Step 2:** Run, expect fail.
- [ ] **Step 3:** Implement with `urllib.request` (accepts `file://`), download to `updates/download/<name>.part`, rename after hash check.
- [ ] **Step 4:** Run, expect pass. Commit.

### Task 3: Apply, check, roll back, undo (`app/update_apply.py`)

**Files:**
- Create: `app/update_apply.py`
- Modify: `app/__main__.py` (add `--check-install`: import `main_window`, `stages`, `workflow`, `update_ui`, print `ok`, exit 0, before `QApplication` is created)
- Test: `tests/test_updates.py` (apply section)

**Interfaces:**
- Consumes: `new_dir` layout from Task 2.
- Produces: `apply(root, new_dir, check=default_check) -> bool`; `undo(root, check=default_check) -> bool`; `default_check(root) -> bool` runs `runtime/python.exe -m app --check-install` with cwd root, 120 s timeout; `main(argv)` = `apply|undo <root> <new_dir|-> <pid>`: wait for pid to exit (poll, max 60 s), act, write `updates/last-result.txt` (`Updated to X` / `The update to X failed and was undone; see updates\last-update.log` / `Undid the update; back on X`), append to `updates/last-update.log`, compileall the managed parts quietly, start `runtime/pythonw.exe -m app` detached.

- [ ] **Step 1: Tests** on a fake install (managed parts with marker files, a `runtime/` dir and a `studies/` dir outside the managed parts): `apply_ok` (new markers in place, old ones in `updates/previous/`, a file deleted in the new release is gone), `apply_check_fails` (check returns False → old markers back, `previous/` holds nothing half-moved), `apply_move_fails` (make `scripts` un-movable via a monkeypatched `os.replace` raising on that part → all parts back), `undo_ok` (after apply, undo restores old markers), `untouched` (snapshot of every file outside managed parts and `updates/` equal before and after), `check_install_flag` (`--check-install` present in `app/__main__.py` argument handling; full run is on Windows CI).
- [ ] **Step 2:** Run, expect fail.
- [ ] **Step 3:** Implement: move current part to `updates/previous/<part>` (clearing old `previous/` first), then new part in; record each move so rollback reverses exactly what happened. Undo swaps `previous/` and the live parts.
- [ ] **Step 4:** Run, expect pass. Commit.

### Task 4: The app side (`app/update_ui.py`, main window)

**Files:**
- Create: `app/update_ui.py`
- Modify: `app/main_window.py` (`_build_menu`: add `&Help` menu; after window shown, start the background check; show `last_result` once in the log and a message box)

**Interfaces:**
- Consumes: Task 2 functions, `update_apply` modes.
- Produces: `class Updater(QObject)` with `__init__(window)`, `menu(menubar) -> QMenu` (Check for updates…, Undo last update (enabled when `updates/previous/VERSION.json` exists), About), `check_in_background()`, signal `found(Decision)` → banner (a `QLabel` + `Update now…` button inserted at the top of the central widget, hidden until found). `UpdateDialog(QDialog)`: notes in a read-only `QTextBrowser`, buttons Update / Later for code, Download installer (opens `installer_url` or `page_url` with `QDesktopServices`) / Later for installer. Update: `window.close()`-style confirmation reused (`MainWindow.closeEvent` checks), progress dialog during `stage` in a `QThread`, then `launch_apply` and `QApplication.quit()`.
- From source (`install_root()` None): Check for updates says "Running from source: update with git pull." and no background check.

- [ ] **Step 1:** Install PySide6 into a scratch venv; offscreen smoke test in `tests/test_updates.py` (skipped when PySide6 missing): `Updater` builds the Help menu with three actions; `found` with a code Decision shows the banner; with source tree, menu check gives the source message.
- [ ] **Step 2:** Implement; run `QT_QPA_PLATFORM=offscreen` test, expect pass. Commit.

### Task 5: Packaging, workflow, installer, docs

**Files:**
- Modify: `packaging/windows/build.ps1` (param `-Version`, default `0.0.0-dev`; call `make_update.py --version-file-only $Out --version $Version`)
- Modify: `.github/workflows/windows-package.yml` (pass `-Version`; after the self-test step run `python -m app --check-install` on the bundle; build the package with `make_update.py --version V --out dist`; Release step uploads the zip and manifest with the installer)
- Modify: `packaging/windows/installer.iss` (`[InstallDelete]` and `[UninstallDelete]` add `{app}\updates`)
- Modify: `packaging/windows/README.md` (Updates section: what ships, how the app decides, first upgrade from v1 is by installer)

- [ ] **Step 1:** Edit; `python3 packaging/make_update.py --version 9.9.9 --out /tmp/x` works from Linux; YAML parses (`python3 -c "import yaml…"` or a careful read).
- [ ] **Step 2:** Run the whole `tests/test_updates.py` plus `tests/test_workflow.py`, `tests/test_stages.py` where runnable. Commit, push, draft PR; the Windows workflow runs on the PR (it touches `packaging/`) and proves `--check-install` and the build on Windows.
