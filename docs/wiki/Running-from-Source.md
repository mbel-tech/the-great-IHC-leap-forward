# Running from source

For working on the code, or on a computer where the installer is not wanted.

## Start the app

```bat
build_app.bat    :: once: makes work/appenv, a Python 3.13 environment with StarDist
run_app.bat      :: every time
```

**Python 3.13, not 3.14.** The library that reads Zeiss CZI files (`pylibCZIrw`) has no 3.14 version yet, and the app runs every stage in one interpreter.

Check a copy with `python -m app --self-test`; it writes PASS or FAIL per check to `lsapp-selftest.log`.

## Run stages from a terminal

Every stage is a script in `scripts/`, numbered in pipeline order. The **(i)** panel on each stage in the app shows its exact command line. `scripts/run_all.sh` runs the whole pipeline in order. The app and the scripts read the same settings file, so you can mix the two.

To run a whole screen without the window, for example overnight on a machine with no display:

```
python -m app --run prepare  D:\LS-analysis\study.json
python -m app --run measure  D:\LS-analysis\study.json
```

It makes the same plan as that screen's **Run all remaining**, with the Options saved in the study and Redo where a stage is out of date, and records each run in the study. It stops at the first failure or a *poor* signal check, never unblinds, and exits 0 when every planned stage ran, 1 when one failed or a hold stopped it, and 2 when the screen or study file is not there.

Common flags: `--limit N` (first N sections only), `--force` (redo finished work), `--dry-run` (repair tools: report only).

## Curators in a browser

`serve_curators.bat` serves the curator pages to an ordinary web browser. Work done there stays in that browser until you bring it into the app (**Tools > Migrate browser curation**).

## Instrument characterisation

**Tools > Instrument characterisation** holds the once-per-microscope stages (tile geometry and illumination correction). They are already done for the LS scanner, so a new study on the same microscope can skip them. One of them needs Fiji and runs outside the app (`scripts/run_all.sh`, steps 3 to 8).

## Tests

See the *Tests* section of the repository README.

## Making a release

Push a version tag (`git tag v1.0.0 && git push origin v1.0.0`). The *Windows package* workflow builds and tests the installer and attaches it to a GitHub Release. Details: `packaging/windows/README.md`.
