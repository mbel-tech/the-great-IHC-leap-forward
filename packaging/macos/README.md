# Mac app: The Great IHC Leap Forward

`The-Great-IHC-Leap-Forward-<version>-mac.dmg` holds the app for Apple Silicon
Macs (M1 or later) on macOS 14 (Sonoma) or newer. Like the Windows installer it
brings everything with it: Python, every library the stages use (including
StarDist and TensorFlow for Measure) and StarDist's model, so Measure works
offline. Intel Macs are not supported: TensorFlow no longer publishes builds
for them.

## Getting it

Download the `.dmg` from the repository's Releases page, open it, and drag
**The Great IHC Leap Forward** onto **Applications**. The disk image carries a
`How to open.txt` with the steps below.

The app is signed ad hoc, not with an Apple Developer ID, and is not notarized,
so the first time it is opened macOS blocks it:

1. Open it from Applications. macOS says it cannot verify the app. Click
   **Done** (not Move to Trash).
2. **System Settings > Privacy & Security**, scroll to the message about the
   app, click **Open Anyway** and confirm. From then on it opens normally.
   (On macOS 14 right-click > Open > Open also works; macOS 15 removed that.)

If macOS says the app "is damaged", the quarantine flag can be cleared by hand:

    xattr -dr com.apple.quarantine "/Applications/The Great IHC Leap Forward.app"

Signing with a Developer ID and notarizing would remove these steps; it needs a
paid Apple Developer account (99 USD a year) and its certificate stored as
repository secrets. The build is written so that only the `codesign` line in
`build.sh` and a notarization step in the workflow would change.

Studies and settings live in `~/.ls-pipeline`, as on every platform, and are
kept when the app is replaced with a newer one.

## Differences from the Windows installer

- **No R.** The R figures are switched off in the app, so R is not bundled.
- **Updates come as a new disk image.** The app still checks for new releases
  and says when one is out, but its button downloads the new `.dmg` instead of
  swapping code in place: changing files inside a signed `.app` breaks its
  signature, and that has not been tried on a real Mac yet (`app/updates.py`,
  `check`).
- Detection runs on the CPU, as on Windows (stock TensorFlow does not use the
  Apple GPU).

## Making a release

The same tag that builds the Windows installer builds the Mac app: the
`Mac package` workflow runs on `v*` tags and attaches the `.dmg` to the same
release. Run it from the Actions tab to get a disk image as a build artifact
without releasing; it also runs on pull requests that touch `packaging/`.

Each run builds the app, makes the disk image, copies the app back out of the
image the way a person installs it, and on that copy runs
`packaging/windows/smoke_test.py` (every library, StarDist offline), the app's
`--check-install` and its `--self-test` through the app's own launcher.

## Building locally

On an Apple Silicon Mac with the Xcode command line tools:

    bash packaging/macos/build.sh             # -> dist/mac/The Great IHC Leap Forward.app and the .dmg
    bash packaging/macos/build.sh --no-dmg    # just the app

Downloads are cached in `work/packaging-cache/mac`.

## How it is put together

| In the app | What |
|---|---|
| `Contents/MacOS/The Great IHC Leap Forward` | a three-line script that starts `runtime/bin/python3 -m app` |
| `Contents/Resources/runtime/` | CPython 3.13 from [python-build-standalone](https://github.com/astral-sh/python-build-standalone) and its `site-packages` |
| `Contents/Resources/app/`, `scripts/`, `analysis/`, `atlas/` | the repository, unchanged |
| `Contents/Resources/keras/` | StarDist `2D_versatile_fluo` |

`Contents/Resources` has the same layout as the Windows install folder, so the
app finds itself the same way (`app/updates.py`, `install_root`).
`runtime/.../site-packages/sitecustomize.py` points `KERAS_HOME` at the bundled
model and `SSL_CERT_FILE` at certifi (the bundled OpenSSL does not read the
macOS keychain). python-build-standalone is used for the same reason the
Windows build embeds python.org's Python rather than freezing with PyInstaller:
the stages are loaded by file path, so a freezer cannot see their imports.

`check_macos_min.py` reads the minimum macOS of every library in the app and
fails the build if one needs newer than `MIN_MACOS` (14.0);
`constraints.txt` holds back libraries whose newest builds need macOS 15.
`bundle-lock.txt`, when committed, pins the runtime as on Windows.
