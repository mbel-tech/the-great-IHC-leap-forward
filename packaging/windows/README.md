# Windows installer: The Great IHC Leap Forward

`The-Great-IHC-Leap-Forward-Setup-<version>.exe` installs the app for the current user, with no
administrator rights and nothing else to install first: Python, every library
the stages use (including StarDist and TensorFlow for Measure), StarDist's
model, and R with the figure packages all come inside it.

## Getting it

Download the newest `The-Great-IHC-Leap-Forward-Setup-*.exe` from the repository's Releases page,
run it, and start **The Great IHC Leap Forward** from the Start menu. Installing a newer
one over it upgrades in place. Studies and settings live in
`%USERPROFILE%\.ls-pipeline` and survive upgrades and uninstalling.

## Updates from inside the app

An installed copy checks GitHub for a newer release each time it starts (quietly;
offline it just carries on) and from **Help > Check for updates**. When one is
out, a bar under the menu says so.

- **Code-only releases** (app, stage scripts, analysis, atlas) install from
  inside the app: it downloads a small package, closes, swaps the new code in,
  checks that it imports, and starts again. If anything fails the old version
  is put back on its own. **Help > Undo last update** goes back one version.
  The atlas plates (~250 MB) are downloaded only in releases that change them.
- **Releases that change libraries, Python or R** need the installer; the app
  says so and links it.
- Nothing outside the install folder is touched: studies, settings and results
  stay where they are. The updater's own files live in `<install>\updates\`.

How it decides: every release carries `update-<version>.json` beside the
installer. Its `runtime_fingerprint` is a hash of `requirements*.txt`,
`packaging/windows/requirements-bundle.txt`, `build.ps1` and `sitecustomize.py`;
the installed copy has the same hash in its `VERSION.json`. Same hash: code
update. Different: installer. Editing any of those files therefore makes the
next release an installer release, which is the safe direction.

Updates come only from published releases (a `v*` tag), never from main
between releases. Copies installed from `v1` predate the updater and need the
next release's installer once.

`packaging/make_update.py` builds the packages; `app/updates.py`,
`app/update_apply.py` and `app/update_ui.py` are the app side.

## Making a release

Push a tag:

    git tag v1.0.0
    git push origin v1.0.0

The `Windows package` workflow builds the bundle, runs `smoke_test.py` and the
app's `--self-test`, builds the installer, installs it silently and tests the
installed copy, then publishes a GitHub Release with the installer and the
in-app update files attached. Tags are numbered (`v1.1`, `v1.2.3`); the app
ignores tags it cannot read as a version.
Run it from the Actions tab (Run workflow) to get an installer as a build
artifact without releasing.

## Building locally

    pwsh packaging\windows\build.ps1            # -> dist\LS-IHC (runnable: "The Great IHC Leap Forward.cmd")
    iscc packaging\windows\installer.iss        # -> dist\The-Great-IHC-Leap-Forward-Setup-0.0.0-dev.exe

`-NoR` leaves R out. Downloads are cached in `work\packaging-cache`.

## How it is put together

| Folder in the install | What |
|---|---|
| `runtime\` | python.org embeddable Python 3.13 and its `site-packages` |
| `app\`, `scripts\`, `analysis\` | the repository, unchanged |
| `R\` | R and ggplot2, ggtext, lmerTest, emmeans, multcomp, officer, readxl |
| `keras\` | StarDist `2D_versatile_fluo`, so the first Measure run needs no network |

`runtime\sitecustomize.py` points `RSCRIPT` and `KERAS_HOME` at the bundled
copies; nothing in `app\` or `scripts\` knows it is installed. The interpreter
is embedded rather than frozen with PyInstaller because the stages are loaded
by file path, so a freezer cannot see their imports. Python 3.13 because
pylibCZIrw has no 3.14 wheel. TensorFlow on native Windows is CPU-only.
