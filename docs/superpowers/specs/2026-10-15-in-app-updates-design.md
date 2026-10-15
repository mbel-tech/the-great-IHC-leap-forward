# In-app updates: design

Date: 2026-10-15. Asked for in the project thread "in-app updates": the
installed app should pick up new changes without reinstalling from scratch.

## What the user decided

- Updates come from **published releases only** (a `v*` tag), not from every
  merge to main, so the laptop always runs a tested, named version.
- Design approved as presented in the thread (check on start and from the Help
  menu, small code-only updates with rollback, full installer when libraries
  change, study data never touched).

## Facts it rests on

- The repository is public, so release assets download with no sign-in. The
  unauthenticated GitHub API allows 60 requests an hour per IP; the app makes
  one per start.
- The installer is per-user into `%LOCALAPPDATA%\Programs\The Great IHC Leap
  Forward`, which the user can write to, so no administrator rights are needed
  to replace files there.
- Studies and settings live in `%USERPROFILE%\.ls-pipeline` and in each study's
  output folder. The updater only ever writes inside the install folder.
- The install folder holds `runtime\` (Python + libraries, ~700 MB), `R\`,
  `keras\` (StarDist model), and the repository parts `app\`, `scripts\`,
  `analysis\`, `atlas\`, `config.example.json`, `README.md`.

## How it works

### What a release carries

The tag workflow (`windows-package.yml`) already attaches the installer. It
also attaches:

- `The-Great-IHC-Leap-Forward-code-<version>.zip`: `app\`, `scripts\`,
  `analysis\`, `atlas\`, `config.example.json`, `README.md`, `VERSION.json`.
  A few MB.
- `update-<version>.json`: version, the zip's name, size and SHA-256, and the
  **runtime fingerprint**.

The runtime fingerprint is a SHA-256 of the built bundle's
`BUNDLE_VERSIONS.txt` (pip freeze + Python + R versions). (Revised after review:
the first version hashed the requirement files, but those say `>=`, so two
builds of the same files can differ.) `build.ps1` builds against
`packaging/windows/bundle-lock.txt` when present so releases share a runtime.
The atlas (~250 MB) ships as its own zip with its own fingerprint and is only
downloaded when it changed.

`build.ps1` writes the same `VERSION.json` (version + fingerprint) into the
bundle, so every installed copy knows what it is.

### Checking

On start, a background thread asks
`api.github.com/repos/mbel-tech/the-great-IHC-leap-forward/releases/latest`
(5 s timeout; offline or any error is silent). Help > Check for updates does
the same and does report errors. Versions compare as integer tuples
(`v1` < `v1.1` < `v1.2.3`). Three outcomes:

- **Up to date**: nothing shown on start; "You have the newest version" from
  the menu.
- **Code update**: same fingerprint. A bar under the menu says "Version X is
  ready to install" with **Update now…**, which opens a dialog with the
  release notes and Update / Later.
- **Installer needed**: different fingerprint, or no code package in the
  release. The dialog explains libraries changed and links the Setup .exe.

When running from source (no `VERSION.json` beside `runtime\`), checking is
off and Help > Check for updates says to use git.

### Installing a code update

1. Download the zip into `<install>\updates\download\`, check size and
   SHA-256, unzip into `<install>\updates\new-<version>\`, and check it holds
   `app\__main__.py` and a `VERSION.json` with the expected version.
2. Copy the stand-alone helper `app\update_apply.py` (standard library only)
   to `<install>\updates\`, start it detached with the bundled
   `runtime\python.exe -I`, and close the app. The app asks first if a stage is
   running or the project has unsaved changes, the same way closing does.
3. The helper waits for the app's process to exit, then for each part
   (`app`, `scripts`, `analysis`, `atlas`, the two files, `VERSION.json`)
   moves the current one to `<install>\updates\previous\` and the new one in.
   Whole folders are swapped, so files a release deleted do not linger.
4. It runs `runtime\python.exe -m app --check-install` (imports the window,
   stages and workflow modules without opening anything). On failure, or if
   any move failed, it puts every part back from `previous\`.
5. It precompiles the new code (quietly), writes `updates\last-update.log`,
   and starts the app again. The app says "Updated to X" (or "The update to X
   failed and was undone; see the log") once on that start.

**Help > Undo last update** (shown when `updates\previous\` exists) runs the
same helper in reverse. One previous version is kept.

The installer deletes `updates\` on upgrade and uninstall.

## Out of scope

- Updating `runtime\`, `R\` or `keras\` in place (the installer does that).
- Updates from main between releases (user chose releases only).
- Code signing of the update package (the download is over HTTPS from the
  project's own release and checked against the SHA-256 in the manifest).
- A "don't check on start" setting (one silent request per start).

## First upgrade

The copy installed from `v1` has no updater. The user installs the first
release that carries this feature with its Setup .exe, once; every later
release can arrive through the app.

## Testing

- `tests/test_updates.py` (stdlib, no Qt): version comparison, the three
  outcomes, package build (zip contents, manifest hash, fingerprint stable and
  sensitive to a requirements change), download check (bad hash refused),
  apply into a fake install tree, rollback when the check fails, undo, and
  that nothing outside the install folder is written.
- `--check-install` and the menu entries run in the Windows CI job's self-test
  step on the built bundle.
