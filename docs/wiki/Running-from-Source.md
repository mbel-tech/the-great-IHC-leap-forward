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

To run or list a single stage:

```
python -m app --list  D:\LS-analysis\study.json
python -m app --stage overviews D:\LS-analysis\study.json --opt limit=3
```

`--list` prints every stage's id, group, title and state, as the window shows it. `--stage <id>` runs one stage exactly as its **Run** button does: with the Options saved in the study (plus Redo when the stage is out of date), `--opt key=value` on top, and the run recorded in the study's history. It refuses a stage the window would hold, giving the window's reason and exit status 2. `--force` only overrides the window's waits (earlier stages not done, for example). A stage the study does not use, one the app does not run, a curator stage (its page path is printed, to open in a browser) and an unblinding without `--unblind` are refused whatever you add. Exit status: 0 ran, 1 failed or stopped, 2 refused, 130 Ctrl+C. In the installed Windows app, `ihc` does the same (`ihc --list study.json`); see the Windows installer notes for getting it on Path.

If the slides or output drive drops during a run, the step waits and carries on by itself when the drive is back. A terminal shows `slides drive unreachable (<folder>), paused at <label>; waiting, Ctrl+C to stop`, then `drive back after N s, carrying on`. Ctrl+C stops the run.

Common flags: `--limit N` (first N sections only), `--force` (redo finished work), `--dry-run` (repair tools: report only).

## Curators in a browser

`serve_curators.bat` serves the curator pages to an ordinary web browser. Work done there stays in that browser until you bring it into the app (**Tools > Migrate browser curation**).

## Instrument characterisation

**Tools > Instrument characterisation** holds the once-per-microscope stages (tile geometry and illumination correction). They are already done for the LS scanner, so a new study on the same microscope can skip them. One of them needs Fiji and runs outside the app (`scripts/run_all.sh`, steps 3 to 8).

## Tests

See the *Tests* section of the repository README.

## Making a release

Push a version tag (`git tag v1.0.0 && git push origin v1.0.0`). The *Windows package* workflow builds and tests the installer and attaches it to a GitHub Release. Details: `packaging/windows/README.md`.
