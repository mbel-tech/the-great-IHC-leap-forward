# Reviewing a change to The Great IHC Leap Forward

Read by the Claude review on every pull request (`.github/workflows/claude-review.yml`),
and meant for human reviewers too. Report bugs first, ranked by how much they could
change a result. Skip style unless it hides a bug.

## The rules that matter most

1. **Never lose resolution in a measurement.** Nuclei are detected and counted on
   native CZI pixels (about 0.65 µm). Anything that downsamples, resizes or decides a
   per-nucleus flag (artifact, off-tissue, clipped) on a coarser grid before measuring
   is a blocking finding. Pictures for people (overviews, scene renders, curator
   images) may be smaller; numbers may not.
2. **A failure must look like a failure.** Stages run in-process from the app
   (`app/runner.py`). A stage refuses work with `return 1` from `main()` or
   `raise SystemExit(msg)`. Printing an error and returning 0, catching an exception
   and carrying on, or skipping items without saying how many, makes the app show the
   step as done. Flag any of these.
3. **"Done" means complete.** `Stage.done()` only checks that outputs exist. A stage
   that writes its output early, appends to it, or creates its output folder before
   finishing will look done after a crash. Prefer writing to a temp file and
   replacing (`ls_io.atomic_write_csv`, `ls_jsonio.write_json_atomic`).
4. **Never overwrite curation work.** The curation store, `roi_regions*.csv`
   exports, overrides files and the project/study JSON hold hours of hand work. A
   partial run (`--limit`, a subset, a trial) must not replace a complete file. Any
   in-place `open(path, "w")` on these is a finding.
5. **Joins and units.** Joins are by `scene_uid` (+ `marker`, + `roi_index`). Check
   that a join cannot silently pair rows from different markers, sections or runs,
   and that µm, px and the overview/curation scales are not mixed.
6. **The study comes from the config.** Stages read `LS_CONFIG` through `ls_config`.
   No hard-coded study names, markers (AF568, AF488, pERK, PCNA), pixel sizes or
   paths, and no reading from `~/Downloads` or the repo's own `config.json` without
   checking the file belongs to the study.
7. **Windows first.** The user runs Windows. Watch for path separators, files held
   open by a viewer (`os.replace` fails), `cp1252` console output, and long paths.
8. **Blinding.** Nothing committed or printed may reveal which fish is in which
   treatment group before the unblinding stage (06b).

## Tests

- Suites are plain scripts, not pytest: `python tests/test_x.py`, all of them via
  `bash tests/run.sh`. They must name their own study (`tests/_fixture.py`), never the
  operator's.
- A bug fix should come with a check that fails without it.
- Never skip, disable or loosen a test to make CI green.

## Where things are

- `app/`: the PySide6 desktop app. `workflow.py` (screens, words, options),
  `stages.py` (run order, outputs), `runner.py` (runs stages in-process).
- `scripts/NN*_*.py`: numbered stages; `ls_*.py`: shared helpers.
- `scripts/04l_roi_curator.py`: generates the ROI curator page; most of it is
  JavaScript in a Python string. Check exports (`roi_regions`, `roi_plates`,
  `roi_landmarks`) for coordinate frames and for state that is saved and reloaded.
- `docs/user-guide.md`: keep it in step when on-screen words or steps change.
