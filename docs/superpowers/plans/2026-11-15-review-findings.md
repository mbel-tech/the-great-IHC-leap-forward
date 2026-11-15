# Review findings: fixes

Fixes for the findings of the 2026-11-15 code review that can be made in code without a
decision from the study's owner. Findings that need one (flat-field correction of measured
intensities, how Abercrombie's h is estimated, per-crop DAPI normalisation, running stages in a
subprocess, signing update packages, licensing of the shipped atlases, pinning dependencies) are
NOT in this plan.

There is no separate spec. `REVIEW.md` at the repo root is the binding authority.

## Global Constraints

- Read `REVIEW.md` first. Its eight rules bind every task. In particular: a stage refuses work
  with `return 1` from `main()` or `raise SystemExit(msg)`; outputs are written to a temp file
  and replaced (`ls_io.atomic_write_csv`, `ls_io.atomic_save`, `ls_jsonio.write_json_atomic`);
  curation files are never overwritten in place; joins are by `scene_uid` + `marker`
  (+ `roi_index`); no hard-coded study names, markers (AF568, AF488, pERK, PCNA), pixel sizes or
  paths; nothing may reveal treatment groups before 06b.
- Tests are plain scripts, not pytest: `python3 tests/test_x.py`. Run with
  `LS_CONFIG_STRICT=1 QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS=--no-sandbox`.
  The whole suite is `PY=python3 bash tests/run.sh` (about 8 minutes) and must end
  `ALL SUITES PASS` (the curator page suites are skipped without a `config.json`; that is
  expected). A bug fix comes with a check that fails without it. Never skip, disable or loosen
  a test to get green.
- Documentation gates are tests: a new stage needs a `{#stage-<sid>}` entry in
  `docs/guides/pipeline-guide.md`; a new script needs a `### \`<script>\`` entry in
  `docs/guides/technical-guide.md`; a new CSV name written by a script must be registered in
  `docs/guides/file_formats.json` with its columns; after any of these run
  `python3 docs/make_technical_tables.py`. When on-screen words or steps change, update
  `docs/guides/user-guide.md` and the matching `docs/wiki/` page.
- Each task adds one entry at the top of `LOGS.md` (newest first, under the `---` after the
  introduction) in the file's own format: **Changed**, **Why**, **Cost**.
- Follow the code's existing style: long explanatory comments that say WHY, helpers imported the
  way the neighbouring code imports them, no new dependencies.
- Commit on the current branch (`fatina-del-codice/lucid-hawking-alhh0c`). Do not push, do not
  create branches, do not open pull requests. End every commit message with these two lines:

  ```
  Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Hhzram2VPLZH2P9xsYkDgX
  ```

## Task 1: The positivity cut is per (section, marker)

**Defect.** `scripts/06a_roi_dataset.py` keys a section's background values and its cut by
`scene_uid` alone (`bg_by[uid]`, `cuts[uid]`, `bgstat[uid]`). Under the `paired` layout a section
has one marker, so this is right. Under `multiplex` both markers share the section's uid, so the
two markers' background nuclei are pooled into one cut. Reproduced: with marker M1 background
around 103 and marker M2 background around 5,035 on one section, the cut for both came out at
13,464, and three M1 nuclei at 200 were called 0 positive. The detector-specificity row for the
section also lumps both markers and is labelled with the first marker.

**Required.**
- In 06a, key background values, `cuts`, `bgstat` and everything derived from them by
  `(scene_uid, marker)`. The Abercrombie `h` for an own-channel marker reads the cut: it must read
  that marker's cut. `detector_specificity.csv` gets one row per (section, marker) that has a cut,
  each from that marker's background discs only. `positivity_cuts.csv` already has a
  `(scene_uid, marker, cut)` shape; each row must now carry that marker's own cut.
- Find every other reader that takes a cut by section alone and fix it the same way. Known:
  `scripts/06c_excel_dataset.py` (`cut_by[r["scene_uid"]]`). Check `scripts/06d_excel_by_slide.py`,
  `scripts/06i_detection_overlays.py`, `scripts/ls_overlay.py` (`section_values`, `section_cut`
  callers) and `scripts/05c_detect_rois.py`'s crop drawing (`CropDrawer`), which computes a
  section's cut to draw crops by the same rule as 06a.
- Under `paired` nothing may change: the existing suites must pass unmodified except where a
  test's own fixture has to say which marker a value belongs to.

**Tests.** In `tests/test_roi_dataset.py`, a multiplex section whose two markers have different
background levels (the existing `multiplex_fixture` gives both markers the same `BG_S1`, which
hides the defect): each marker's `section_positivity_cut`, `n_positive` and `section_bg_nuclei`
come from its own background only, there are two specificity rows, and `positivity_cuts.csv` has
two different cuts. Show the new check failing before the fix.

## Task 2: 05c exits non-zero when it skipped work

**Defect.** `scripts/05c_detect_rois.py` `main()` ends with `return 0` even when it SKIPPED
sections: `no_tissue_mask` (no tissue mask in `reformatted/`) and `no_norm` (no shared intensity
range). The app runs stages in-process (`app/runner.py`) and shows a stage as done on 0, so a
run that left sections unmeasured looks complete. REVIEW.md rule 2.

**Required.**
- After everything has been written, closed and reported exactly as now, `main()` returns 1 when
  any section was skipped for want of a tissue mask or of an intensity range. The last line
  printed must say how many sections were not measured and that the stage is therefore not
  complete, because `app/runner.py` shows the last printed line as the failure message.
- The sections that WERE measured stay measured and logged done; nothing is rolled back. A
  resume measures only what is left.
- ROIs that were not segmented inside a measured section (recorded in
  `results/roi_box_status.csv`) are NOT a reason to fail: they are reported and 06a leaves
  them out.
- Check `scripts/06e_refresh_loop.py` and anything else that calls 05c's `main()` or reads its
  exit code, and keep them correct.

**Tests.** `tests/test_detect_resume.py` drives `main()` with fakes (`_run_main`). Its
`zero_found_check` currently asserts `chk("a no_norm skip completes", rc, 0)`: that expectation
changes to 1, and it must still assert the section is not logged done and that the next resume
measures it and returns 0. Add the tissue-mask case: a section with no `<uid>_mask.npy` gives
exit 1 and no rows. Other suites that drive 05c (`tests/test_detect_backends.py`,
`tests/test_detect_overlay_crops.py`, `tests/test_native_censor.py`, others found by grep) may
depend on the old exit code; update only expectations that are about exactly this.

## Task 3: Importing curator exports never discards curation

**Defect.** `scripts/04q_import_curation.py --write` REPLACES this study's entry in
`curation/ls_roi_curator_v1.json` with what the three CSVs hold. Sections that are in the stored
state but not in the export are printed as `ONLY ON DISK, would be lost` and then lost. The
app's Import button runs the stage with `--write` unconditionally (`app/stages.py`,
`import_curation`). The one backup, `<out>.backup`, is overwritten by the next import. The file
is written with `open(args.out, "w")` and `json.dump`, not atomically. REVIEW.md rule 4.

**Required.**
- `--write` MERGES by default: every section in the export takes the export's record; every
  section that is only in the stored state is KEPT as it is. The report says how many were kept
  and names them (cap the list at a readable length and say how many more).
- A new flag `--replace` restores the old behaviour (stored-only sections are dropped), for an
  operator who means it. The app's stage does not pass it.
- The backup is never overwritten: each write that changes an existing file first copies it to
  a timestamped name beside it (`<name>.<YYYYMMDD-HHMMSS>.backup`; if that exists, add a
  counter). Nothing prunes old backups.
- The write is atomic, through `ls_jsonio.write_json_atomic` (read it first: use it as the
  neighbouring code does).
- The stored state is filed per study (`CU.file_under(stored, STUDY_ID, state, STUDY_TOKEN)`,
  `read_stored`, `compare`). Other studies' entries must be carried through untouched, as now.
  Read `scripts/ls_curation.py` before changing how the state is assembled.
- Update the stage's blurb in `app/stages.py`, the words in `app/workflow.py` if they describe
  the backup, and the user guide / wiki where the import is described.

**Tests.** `tests/test_import_curation.py`: a stored state with sections A and B, an export
holding A (changed) and C: after `--write` the state has A (from the export), B (kept) and C;
with `--replace` it has A and C only; two writes leave two distinct backups, the first one
holding the original state; another study's entry in the same file survives both modes; a
write interrupted before the replace leaves the previous file intact (simulate by making the
replace raise).

## Task 4: Early stages write atomically and fail honestly

Several stages write an output the app's done check trusts with a plain `open(path, "w")`, so a
crash mid-write leaves a partial file that looks done (REVIEW.md rule 3), and two return success
after doing nothing (rule 2).

**Required, file by file.** For each CSV below, write through `ls_io.atomic_write_csv` (or
`ls_io.atomic_save` where the writer is not a list of dict rows), imported the way that script
already imports its helpers (some use `import ls_io as IO`, some load it by path: follow the
file). Keep the columns, their order and the row order exactly as they are.

- `scripts/01e_tile_geometry.py`: `qc/tile_geometry.csv`. Also: `print("nothing measured")`
  followed by a bare `return` must become a failure (`return 1`) and `main()` must return 0 on
  success, with `raise SystemExit(main())` at the bottom if it is not already there.
- `scripts/01g_saturation_map.py`: `saturation_<marker><suffix>.csv`.
- `scripts/01k_saturation_raw.py`: around line 229 a branch prints a block and does a bare
  `return`. Read it: if that branch means the stage did not do its work, it must return non-zero;
  if it is a legitimate "nothing to do", leave it and say so in your report.
- `scripts/01b_pick_sections.py`: its `OUT_CSV`.
- `scripts/01i_multisection_review.py`: `REVIEW_CSV`.
- `scripts/04a_reformat.py`: the `lost` sections file (`paths["lost"]`).
- `scripts/04a3b_enforce_sections.py`: `BOXES_CSV` (it copies the old file to `PREV_CSV` first;
  keep that).
- `scripts/04a_atlas_extract.py`: the CSV written around line 574.
- `scripts/04b_atlas_match.py`: `atlas_proposals.csv`.
- `scripts/04e_register_elastix.py`: `roi_regions_refined.csv` and `registration_quality.csv`.
- `scripts/05b_tune_detection.py`: `TUNING_CSV` (and its report `.md` through
  `ls_io.atomic_save`).

HTML pages (`*.html`) are out of scope.

**Tests.** One new suite, `tests/test_atomic_outputs.py`, in the house style (see
`tests/_fixture.py` and any small existing suite for the `chk` pattern): for each script above
it asserts from the source that no plain `open(<expr>, "w"...)` writes a `.csv` path any more
(an AST walk over `open` calls whose mode is `"w"`, with an explicit allow-list for the HTML
writers that remain, each with its reason), so the next in-place CSV write in these files fails
the suite. Plus a behavioural check for 01e: with nothing to measure its `main()` returns
non-zero (drive it the way `tests/test_overview_failures.py` or a similar suite drives a stage;
if the stage cannot be driven without real CZI files, monkeypatch its file listing to empty).

## Task 5: An out-of-sample false-positive rate

**Defect.** `detector_specificity.csv` reports `false_positive_rate` as the share of a section's
background nuclei above that section's cut, but the cut is `median + 3 * 1.4826 * MAD` of those
same nuclei. The rate is in-sample: it describes the tail of the distribution the cut was fitted
to, and is not the independent check the README and 06a's docstring call it. A cut can also be
formed from as few as 5 nuclei, and nothing says so per section.

**Required (additive; no existing number changes).**
- In `scripts/06a_roi_dataset.py`, for every (section, marker) with two or more background discs
  that each hold at least `CUT_MIN_NUCLEI` usable nuclei, compute a leave-one-disc-out rate: for
  each disc, form the cut from the OTHER discs' usable background values with the same rule
  (`ls_overlay.section_cut`), and count this disc's nuclei that are positive by
  `ls_overlay.is_positive` against it. Sum over discs.
- Add to `detector_specificity.csv`, after `false_positive_rate`: `loo_false_positives`,
  `loo_nuclei`, `loo_false_positive_rate` (4 decimals), all blank where it cannot be formed
  (fewer than two qualifying discs, or the rate is withheld for the same reason
  `false_positive_rate` is). Register the columns in `docs/guides/file_formats.json`.
- The console summary prints, per marker, the median leave-one-disc-out rate beside the existing
  one, and how many sections had one.
- Correct the wording in 06a's module docstring and the README "Quantification" section: the
  existing rate is in-sample; the leave-one-disc-out rate is the out-of-sample one.
- This depends on Task 1 (cuts per section and marker). Build on it.

**Tests.** In `tests/test_roi_dataset.py`: a section with two background discs whose values are
known, with the expected leave-one-out counts worked out by hand in the test's comments; a
section with one background disc has blanks; a marker segmented on itself has blanks.

## Task 6: Statistics that do not depend on term order, and a multiplicity column

**Defects in `analysis/roi_stats.R` and `analysis/plot_roi_figures.R`.**
1. `term_line()` reads `anova(fit)`. For `lm` that is sequential (Type I) sums of squares: on the
   study's unbalanced cells (3/2/3/3 animals) the F for `treatment` in
   `value ~ treatment * environment` depends on the order of the terms. For `lmer`, lmerTest's
   `anova` is Type III. The two branches therefore test different hypotheses.
2. `stat_group4()` sets `p_overall` to the MINIMUM of the three term p values when the full
   model is used, which is used to decide the asterisk when only two groups are present.
3. Nothing adjusts for the number of regions tested: every ROI, measure and marker gets its own
   unadjusted p.

**Required.**
- Both branches report marginal (Type III-style) tests from one function. `emmeans` is already
  a dependency: use `emmeans::joint_tests(fit)` for both `lm` and `lmer` fits (read its
  documentation in the installed package if R is available; its output has `model term`, `df1`,
  `df2`, `F.ratio`, `p.value`). Keep the caption format, the `df too low to interpret` flag and
  the `p` attribute exactly as they are. Do not add a dependency.
- `p_overall` for the full model is the omnibus test of the one-way parameterisation
  (`value ~ group4`, which the code already fits for the letters), not a minimum.
- Wherever `plot_roi_figures.R` collects the per-ROI test results for one figure series (find
  where `stat_treatment` / `stat_group4` results are gathered per ROI), add a Benjamini-Hochberg
  adjusted p across the ROIs of that series, measure and marker (`p.adjust(p, "BH")`), written
  to the table/CSV the script already writes for those results, as an added column, and state in
  each figure's caption the adjusted p beside the raw one (`p = 0.012, BH-adjusted 0.048`). If
  the script writes no such table, add one CSV beside the figures (`roi_tests_<series>.csv`:
  roi, measure, term, F, df, p, p_bh) and register nothing else.
- Update the header comments of `roi_stats.R` to say what is tested.

**Verification.** Try `Rscript --version`. If R is not installed, try once to install it
(`apt-get install -y r-base-core r-cran-lme4 r-cran-lmertest r-cran-emmeans r-cran-multcomp
r-cran-multcompview`). If R can be had, write `analysis/tests/test_roi_stats.R` (plain R,
`stopifnot`): an unbalanced 2x2 data set where swapping the term order changes `anova()` but not
the reported line; `p_overall` equals the group4 omnibus p; BH adjustment matches `p.adjust`.
If R cannot be had in this environment, still write that test file, make the change by careful
reading, and report DONE_WITH_CONCERNS stating plainly that nothing was executed.

The R figure steps are switched off in the app (`R_FIGURES = False` in `app/stages.py`); leave
that as it is.

## Task 7: The curator page suites run in CI

**Defect.** The 21 suites in `tests/*.test.js` exercise the generated curator pages, the tools
where hours of hand curation are made. `tests/run.sh` builds those pages from the repo's
`config.json` and a real study's outputs, so on a fresh clone, and therefore in
`.github/workflows/tests.yml`, they are all skipped. `run.sh`'s summary also says "16" suites.

**Required.**
- A way to build the two pages (`04l_roi_curator.py --worklist --rgb --no-seed` and
  `04k_level_curator.py`) against a small synthetic study generated by a script under `tests/`
  (no operator data, nothing committed that came from the real study), so that `tests/run.sh`
  runs the page suites when there is no `config.json`. Look at `tests/_fixture.py`,
  `scripts/make_demo_pack.py` and `app/tutorial_pack.py` first: a synthetic study builder may
  already exist in a form that can be reused.
- Run each `tests/*.test.js` against the synthetic build. For every suite that passes, it runs
  in CI from now on. For every suite that fails ONLY because it asserts facts about the real
  study (a specific count of sections, a real scene_uid), do NOT rewrite its assertions and do
  NOT skip it silently: list it in `run.sh` as needing the real study, with the reason, and
  have `run.sh` print how many page suites ran and how many need the real study. A suite that
  fails for any other reason is a finding: report it, do not hide it.
- When a real `config.json` is present, behaviour is as today.
- Replace the hard-coded "16" in `run.sh`'s summary with a count taken from the files.
- `.github/workflows/tests.yml`: update its header comment; add a Node setup step if the runner
  needs one.

**Tests.** `PY=python3 bash tests/run.sh` on this checkout (no `config.json`) runs the page
suites that can run and ends with a summary line that says how many ran and how many were not.
Report the per-suite outcome.
