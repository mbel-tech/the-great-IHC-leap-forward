# Windows installer

`LS-IHC-Setup-<version>.exe` installs the app for the current user, with no
administrator rights and nothing else to install first: Python, every library
the stages use (including StarDist and TensorFlow for Measure), StarDist's
model, and R with the figure packages all come inside it.

## Getting it

Download the newest `LS-IHC-Setup-*.exe` from the repository's Releases page,
run it, and start **LS IHC pipeline** from the Start menu. Installing a newer
one over it upgrades in place. Studies and settings live in
`%USERPROFILE%\.ls-pipeline` and survive upgrades and uninstalling.

## Making a release

Push a tag:

    git tag v1.0.0
    git push origin v1.0.0

The `Windows package` workflow builds the bundle, runs `smoke_test.py` and the
app's `--self-test`, builds the installer, installs it silently and tests the
installed copy, then publishes a GitHub Release with the installer attached.
Run it from the Actions tab (Run workflow) to get an installer as a build
artifact without releasing.

## Building locally

    pwsh packaging\windows\build.ps1            # -> dist\LS-IHC (runnable: "LS pipeline.cmd")
    iscc packaging\windows\installer.iss        # -> dist\LS-IHC-Setup-0.0.0-dev.exe

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
