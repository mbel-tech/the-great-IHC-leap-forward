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

---

# Second wave: findings of the four follow-up reviews

Line numbers below are as the reviewers saw them at commit 14d7eb5 and may have drifted; find the
code by what it does. Each finding was traced or reproduced by a reviewer, but verify it yourself
before changing anything: if a finding does not hold, do not "fix" it — say so in your report.
Where a task lists several findings, fix all of them unless one needs a design decision with
several valid answers; then fix the rest, leave that one, and report it as a concern.

## Task 8: Partial and trial runs never replace a complete output

A run narrowed by `--limit` / `--tier` / `--force --limit` atomically replaces the FULL table
with its subset, and the stage then reads as done on a fraction of the study (REVIEW.md rule 4).

**Required.** The house pattern already exists: `scripts/04i_propagate_to_perk.py --limit` writes a
`.trial<N>` file beside the real one, and `scripts/04j_censor_clipped.py` has `guard_partial`.
Apply one of them, whichever fits each stage, so the complete file is never replaced by a subset:
- `scripts/05a_roi_geometry.py --limit N` (replaces `roi_boxes_<m>.csv` / `roi_geometry_<m>.csv`).
- `scripts/04n_roi_worklist.py --tier X` (replaces `roi_worklist.csv` with one tier).
- `scripts/04g_artifact_mask.py --limit` (replaces `artifact_summary*.csv`).
- `scripts/01_overviews.py --force --limit N` and `scripts/01k_saturation_raw.py --force --limit N`
  (the checkpoint writes N rows over the full `focus.csv` / `saturation_raw.csv`): a forced
  partial run must keep every other section's existing row.
A trial file must not satisfy the app's done check for the stage (`app/stages.py` outputs).

**Tests.** One suite `tests/test_partial_runs.py`: for each stage that can be driven without CZI
files, a full table exists, the narrowed run is made, and the full table still holds every
original row. For a stage that cannot be driven here, test the helper that decides the output
path / merges rows, and say so in the report.

## Task 9: Boxes carry the marker they were placed for; multiplex bookkeeping

**Defects.**
1. `scripts/05a_roi_geometry.py` stamps each box and geometry row with
   `marker = f["marker_channel"]` (the file's second channel) instead of the marker the run is
   placing. Under `multiplex` every marker's `roi_boxes_<m>.csv` then carries the same marker and
   06a's `split_by_marker` pools them — the failure 05a's own guard comment says it prevents.
2. `scripts/04p_section_provenance.py`: `aset.update` keeps only the last marker's analysis-set
   verdict, and `count_rois` double-counts under multiplex.
3. `scripts/04o_section_rgb.py` filters focus rows by `marker_channel == args.marker` and uses the
   `partner_scene_uid` column for `MARKERS[1]`, so under multiplex every marker but one finds no
   sections.
4. `scripts/06c_excel_dataset.py`'s coverage sheet treats `measured` as a set of `scene_uid`s, so a
   section measured for one marker counts as measured for all.

**Required.** Fix 1, 2 and 4 so each is keyed by the marker in hand, with `paired` behaviour
unchanged. For 3, fix it if the change is local; if it needs a new file-naming scheme, leave it and
report. NOT in this task: the per-marker clipping/censor mask files (`<uid>_clipped.png`,
`<uid>_censor.png` in 01k / 04j) — that needs a naming decision; list what you find in the report.

**Tests.** `tests/test_roi_geometry.py` (or a new suite): a multiplex fixture where two markers are
placed on one section — each marker's boxes file carries its own marker, and 06a over both gives
each marker its own discs. A 04p check for the per-marker verdict. A 06c check for coverage.

## Task 10: The curation chain keeps hand decisions (04a / 04f / 04i)

**Defects.**
1. `scripts/04i_propagate_to_perk.py`: a full run rewrites the target overrides file (the
   hand-entered `perk_overrides.csv`) from derived rows only, so a hand correction in it is lost.
2. 04i ignores the source pass's `manual_flip`: `goal` is the source mask rotated only and the
   target flip is hard-coded 0, so a section the operator flipped gets a partner in the mirror frame.
3. `scripts/04f_exclusion_candidates.py` measures only the current `reformat_index.csv`. After a
   final `04a --apply-overrides` the index no longer holds the excluded sections, so a re-run of
   04f drops them from `exclusion_candidates.csv`, and the next final reformat silently puts them
   back into the analysis.
4. `scripts/04a_reformat.py` with `--render-excluded`: an EXCLUDED section that fails to render is
   put in `lost` and reported as "NOT excluded but still did not survive".

**Required.**
- 04i: rows a person edited survive a re-run. Find how the file distinguishes hand rows from
  derived ones (the `reason` / `confidence` columns, or a marker the app writes); if nothing
  distinguishes them, add a `source` column (`derived` / `manual`), treat rows whose values differ
  from what 04i last derived as manual, keep them, and say how many were kept. Take a timestamped
  backup before any rewrite that changes the file (same naming as Task 3:
  `<name>.<YYYYMMDD-HHMMSS>.backup`).
- 04i: the source's `manual_flip` is applied to the goal mask in the order 04a applies it (04a
  flips after rotating), and the derived row carries the flip.
- 04f: a section already excluded (in the exclusions file) and absent from the index is carried
  through unchanged, not dropped; the run says how many were carried.
- 04a: an excluded section that fails to render is reported as that, in its own count, not in
  `lost`.

**Tests.** Extend the suites that cover these stages (`tests/test_propagate_frames.py`,
`tests/test_proposed_exclusions.py`, `tests/test_reformat_layout.py`, `tests/test_section_review.py`
— grep for the right one) with one check per defect, each failing before the fix.

## Task 11: Study-agnostic stages that fail honestly (04e / 04f / 04g / 04o / 05a)

**Defects.**
1. `scripts/04e_register_elastix.py` hard-codes `reformatted/sections/`, so under `paired` the
   non-default marker's sections are never found and the run ends "nothing refined".
2. Hard-coded pixel sizes: `UM_PX = 5.20e-3` in `scripts/04g_artifact_mask.py` and
   `scripts/04f_exclusion_candidates.py`; `scripts/04i_propagate_to_perk.py` assumes equal µm/px
   across passes. Every area gate is wrong for a study exported at another overview scale.
   `focus.csv` carries `um_px` per section and config carries the overview scale.
3. Failures that end in success (REVIEW.md rule 2): 04o (a marker with no colour; missing
   composites); 04e (exceptions per section are not counted; a section with no seeds is a silent
   `continue`); 05a (sections with ROIs dropped for an angle mismatch print "SKIPPED n" and return
   0); 04a (`lost` / `unaccounted` sections still return None); 04g (a missing overview or no
   tissue is a silent `continue`, and a stale `_artifact.png` from an earlier run stays on disk).

**Required.** Section directories come from the same resolver the other stages use (see how
`scripts/05c_detect_rois.py` `section_dirs()` and `scripts/ls_paths.py` name them). Pixel sizes
come from the data or the config, never a literal. Each silent skip is counted and printed, and a
run that skipped work it was asked to do returns non-zero with the reason as its last printed
line (see Task 2's pattern in 05c). A stale per-section output of a section the run could not
redo is removed or the run fails — never left looking current.

**Tests.** One check per stage in the suites that already drive them; where a stage cannot be
driven without images, test the helper and say so.

## Task 12: Ingest tells the truth (00 / 00b / 01*)

**Defects.**
1. `scripts/00_manifest.py`: a failed ingest returns 0 and leaves the old manifest in place — no
   CZIs found prints "nothing to write"; a CZI with no `Scenes` block or a name that does not
   match goes into `failures` with rc 0 while `manifest_files.csv` is rewritten and
   `manifest_scenes.csv` is left stale; `n_scenes != SizeS` is only printed; an unreadable zip is
   skipped uncounted.
2. `00_manifest.py`: `return 0` comes BEFORE `CH.require_manifest_markers`, so that check is dead.
3. `00_manifest.py` `_write_csv` skips writing when there are no rows, so last run's
   `slide_layout_review.csv` and other QC CSVs survive a clean run.
   (`scripts/04a_atlas_extract.py` `_write_csv` has the same stale-file behaviour for `gaps.csv`.)
4. `00_manifest.py`: `source_files` is matched against a zip's own name, so naming a CZI that
   lives inside a zip skips the whole zip without a count.
5. Stages that never report failure: `scripts/00b_verify_extraction.py` (exits 0 on corrupt,
   missing or unreadable archives); `scripts/01f_tilefield.py`, `01h_tilefield_raw.py`,
   `01c_measure_tile_artifact.py` (print errors, exit 0; 01c is documented as "the pass/fail
   test"); `scripts/01k_saturation_raw.py` (a run where only SOME files fail returns 0, and its
   failure message in the app is a row of `=` because that is the last line printed);
   `scripts/01g_saturation_map.py` (nothing measured writes an empty CSV and succeeds).
6. `scripts/01d_contactsheets.py` drops sections whose overview PNG is missing without a count.

**Required.** Each of these fails (non-zero, reason as the last printed line) when it did not do
its work, and counts what it skipped. A table with no rows is written header-only, replacing the
previous one. The dead marker check runs. `source_files` can name a CZI inside a zip.
A partial ingest must not leave `manifest_files.csv` and `manifest_scenes.csv` describing
different runs: write both or neither.

**Tests.** Extend `tests/test_overview_failures.py`, `tests/test_empty_guards.py`,
`tests/test_atomic_outputs.py` or the manifest suites (grep) with one check per defect.

## Task 13: Section order survives a re-scan; resumed QC rows are current

**Defects.**
1. `scripts/00_manifest.py` (around the `section_order` assignment): the series key includes
   `variant` (the replicate letter), so a re-scanned slide (e.g. `8c` beside `1b..9b`) numbers its
   sections 1..k, colliding with slide 1's. `scripts/01d_contactsheets.py` sorts by
   `section_order` then slide, and `scripts/04b_atlas_match.py` sorts by `section_order` alone
   for atlas matching, so the two slides interleave.
2. `scripts/01_overviews.py` resume keeps old `focus.csv` rows unchanged: after 00 is re-run
   (mounting convention confirmed, overrides changed) `section_order`, slide and variant in
   `focus.csv` and `saturation_raw.csv` stay stale, and rows for uids no longer in the manifest
   are never removed.

**Required.**
- `section_order` is continuous along an animal's series across slides whatever replicate letter
  each slide's chosen scan has. Read how the manifest and `02_pair_passes.choose_variants` decide
  which variant of a slide is used before changing the key. If the right ordering is not
  determinable from the code and LOGS.md (search LOGS.md for `section_order`), STOP and report
  NEEDS_CONTEXT with the options — do not guess.
- 01 and 01k on resume refresh the identity columns (`section_order`, slide, variant) of kept rows
  from the current manifest, and drop rows whose uid is no longer in it, saying how many of each.
- LOGS.md Cost must say that `section_order` changes for studies with re-scanned slides and what
  has to be re-run.

**Tests.** A manifest fixture with a re-scanned slide: orders do not collide and sort in slide
order. A resume fixture: a kept row's order is refreshed and a vanished uid's row is dropped.

## Task 14: The sampling join, staleness and study selection

**Defects.**
1. `scripts/ls_sampling.py`: one sheet row can be joined to two animals — under the number rule
   `LS1` and `LS01` both join row "1", `LS22` and `LS22b` both join "22", and a cell
   "Tank 3 fish 1" is read as 31. Only "one animal on two rows" is rejected. 06b then writes both
   animals with one group and exits 0.
2. `ls_sampling.py`: a fill rule whose `is` is a string (`"303"`) is iterated character by
   character (the set {"3","0"}).
3. `scripts/06b_join_sampling.py`: animals whose treatment is blank are written with an empty
   group and the stage exits 0.
4. `app/stages.py` / `app/workflow.py`: re-running 06b does not mark `build_results` (06e/06c/06d)
   out of date, although 06c takes its groups from 06b's `animal_metadata.csv`; and 06b does not
   fingerprint the workbook file it read.
5. `app/workflow.py`: a `--limit` trial run is recorded as an ok run with no inputs, and
   `_last_ok` then picks that record, so a stage made stale by a settings change shows Done after
   a 5-section trial.
6. `app/study_picker.py`: when `last_project` is gone and there is no `active_study` it falls back
   to `LC.resolve_path()`, which returns the repo's own `config.json` if present (REVIEW.md
   rule 6).

**Required.** A sheet row joined to more than one animal is an error naming both, exactly as an
animal on two rows is; a cell that is not a single number is not read as one. A string `is` means
that one value. Blank treatments make 06b fail with the animals named (unless the study declares
no groups at all — read how `groups.by_animal` empty is treated and keep that case). 06b's output
and the workbook's content are inputs of `build_results`. A narrowed run is never the record that
proves a stage current. The picker never opens the repo's own config as a study without the
operator choosing it.

**Tests.** `tests/test_join_metadata.py`, `tests/test_sampling_text.py`, `tests/test_stale_state.py`,
`tests/test_workflow.py`, `tests/test_studies.py`: one check per defect.

## Task 15: The study file and the installed app survive concurrency and crashes

**Defects.**
1. `scripts/ls_project.py`: `_patch_disk_curation`, `_mark_synced` and `record_run` reset
   `self._stamp` to the stamp on disk without first comparing it with the stamp they hold, so the
   `ProjectConflict` guard is defeated — reproduced: instance B saves a setting, instance A saves
   one curation decision and then its settings, no conflict is raised and B's change is gone.
2. `app/main_window.py` / `app/project_session.py`: File > Save As (and Save with a changed
   `out_root`) is allowed while a stage is running; it rebinds the runner and re-roots the curator
   under the running stage. Only Open / New / Switch / Import settings / tutorial check
   `_idle_for`.
3. `app/update_apply.py`: if the helper dies between moving the live `app` to `parked-*` and
   moving the new one in, there is no `app` folder and nothing at startup looks for `parked-*`.
   And a failed hand-over to `previous` (locked files on Windows) is only logged, after which
   `parked` — the version just replaced — is deleted.

**Required.**
- The partial writers detect that the file changed under them: they re-read, apply their own small
  change to what is on disk now (they already write only their own keys), and keep the object's
  conflict state so that a later full save still raises `ProjectConflict` when another writer
  changed settings in between. The reproduced scenario must end in `ProjectConflict`, not in B's
  change vanishing.
- Save As, and Save when it would move `out_root`, are refused while a stage runs, with the same
  message the other guarded actions give.
- The update swap is recoverable: the helper writes a small journal before it moves anything and
  removes it at the end; on start (`app/__main__.py` or wherever `last_result` is read — and in
  the launcher, if the app cannot start at all without an `app` folder, say in your report what
  can and cannot be recovered from inside the app) a leftover journal with no live `app` puts the
  parked copy back. `parked` is never deleted unless `previous` holds a complete copy.

**Tests.** `tests/test_project.py` / `tests/test_project_session.py` for 1 and 2;
`tests/test_updates.py` for 3 (kill the swap between the two moves by making the second raise;
recovery restores a working install; a failed hand-over keeps `parked`).

## Task 16: The ROI curator does not lose work

Depends on Task 7 (the curator page suites run against a synthetic build). If Task 7 could not
make any page suite run here, test with pure-function extractions under node the way the page
suites lift the script, and say so.

**Defects in `scripts/04l_roi_curator.py` (JavaScript in a Python string) and `app/state.py`.**
1. A second tab erases work: an idle tab's 60 s autosave (and its `beforeunload`) writes its stale
   `S` over the shared key. `saveStudy` re-reads the key only to keep OTHER studies' entries.
   There is no `storage` listener.
2. Revisiting a polygon-only section moves it to whatever plate is on the slider: `select()` and
   `exportCsv`'s `chosen` and `revAct` each re-derive "has work" without polygons, while
   `hasRoiWork` includes them.
3. Review "Clear" deletes a reinstated section's polygons (the empty-record test ignores
   `e.polys`) and takes no undo snapshot.
4. A failed save is invisible: `localStorage.setItem` throwing (quota) throws out of `save()`
   mid-handler; the app's mirror (`app/state.py`) calls the original `setItem` first so its
   bridge is never reached and its "NOT SAVED" banner never shows.
5. Restoring the backup (`roiRestoreBackup`) assigns `S = b.S` without passing the records
   through `resolvePlate` / `decided`, so a stale `verified: "ok"` survives an atlas change.
6. After the plate is changed, the guided walk skips a region: `usedRois` matches by ROI number
   only, not by plate.
7. ADD ROI names, uids and animal names go unescaped into `innerHTML` and a `title` attribute.

**Required.**
- Saving merges per section instead of overwriting: a tab writes only the sections it changed
  since it loaded or last synced, on top of what the key holds now, and a `storage` event brings
  another tab's changes into `S` for sections this tab has not touched (and re-renders if the
  active section changed). Two tabs editing the SAME section: last writer wins for that section
  only, and the losing tab is told in the header's notice area.
- One definition of "has work" (`hasRoiWork`) is used by `select()`, `exportCsv` and `revAct`.
- Clear keeps a record that holds polygons (it clears the review decision only) and is undoable.
- `save()` catches a failed write, shows a persistent visible "NOT SAVED" notice in the page's
  header notice area with the reason, and retries on the next change; the app-side mirror writes
  to disk even when the page's own `setItem` throws, and shows its banner.
- Restore goes through the same path as `initState`.
- `usedRois` is per plate.
- Every interpolation of a name, uid or animal into markup goes through the existing `esc`.

**Tests.** New checks in the page suites (`tests/*.test.js`), one per defect, each failing before
the fix; `tests/test_curation_save.py` for the app-side mirror.

## Task 17: Curator exports and review decisions reach the pipeline

Depends on Task 16.

**Defects.**
1. `exportCsv` loops over `DATA` (the sections in this build), not the saved state: after a
   rebuild with a different subset (worklist / analysis-set changed) the next export silently
   omits curated sections, and 05a takes the newest `roi_regions*`.
2. Review decisions never reach 04a: the page writes `section_review_<stamp>.csv` into
   `export_dir/<stamp>/`, while `04a_reformat.apply_review` reads only
   `reformatted/section_review.csv`; nothing bridges the two, and the page's confirm text says 04a
   will act on it.
3. Self-intersecting polygons: vertex drag and commit allow crossings; 05a takes the area by the
   shoelace formula while 05c counts nuclei by even-odd membership — a bowtie has shoelace area 0
   and even-odd area 50, so density is inflated or infinite.
4. "Open in browser" (`app/curator_view.py`) opens the page on an ephemeral port in an external
   browser that does not mirror to disk; work done there never reaches the curation files.
5. The browser download fallback clicks three downloads back to back; refusing Chrome's
   multiple-download prompt drops two of the three files.

**Required.**
- Export writes every section the saved state holds work for. Sections not in this build are
  exported from their stored record (their coordinates are already canonical once divided by the
  frame they were stored in — read how `frameIn` / the stored frame tag work) and the export
  status line says how many came from outside the build. If a stored record lacks what is needed
  to export it correctly, the export says so and names them rather than omitting them silently.
- 04a's `apply_review` finds the newest review export: resolve it the way 05a resolves the newest
  `roi_regions*` (read `scripts/05a_roi_geometry.py` and `ls_config.export_dir()`), still
  accepting `reformatted/section_review.csv`.
- A polygon that self-intersects cannot be committed: the page refuses the commit / reverts the
  drag with a message. 05a refuses a self-intersecting polygon it is handed anyway (an old
  export) with the section and region named, instead of computing an area for it.
- "Open in browser": the external page is told plainly, in its header notice area, that work here
  is NOT saved to the study until exported and imported — or the action is removed, if the code
  shows nothing depends on it. Choose the smaller change and say which.
- The fallback offers the three files as one download (a single zip is not available without a
  dependency: write one `.csv` bundle only if 05a/04q can already read it; otherwise space the
  clicks and verify each started, and tell the operator how many files to expect).

**Tests.** Page-suite checks for 1, 3 and 5; a Python check that `apply_review` picks up a review
export from the export directory; a 05a check for the bowtie.

## Task 18: Loose ends from the first wave's reviews

Small, independent corrections the task reviews parked.
- Three documents still call the in-sample false-positive rate "the only independent check":
  `docs/guides/pipeline-guide.md`, `docs/guides/pipeline-methods.md`,
  `docs/nuclei-detection-after-roi-curation.md`; and the figure caption in
  `analysis/plot_roi_figures.R` quotes it with no qualifier. Say in-sample, and name the
  leave-one-disc-out rate.
- `scripts/04q_import_curation.py`: a report-only run says "kept N" (say "would keep");
  `--replace` without `--write` is a silent no-op (say it needs `--write`); an unparseable stored
  file is replaced without a line saying it could not be read (print one).
- `tests/test_roi_dataset.py`: the `except Exception` around loading 06c in `multiplex_cut_check`
  turns any load error into a printed skip; catch only the missing-openpyxl case.
- `tests/test_roi_dataset.py`: a three-disc case that pins that a thin background disc (under 5
  usable values) still feeds the cut the other discs are counted against.
- `app/import_exports.py --merge` overwrites overlapping sections' old records with no backup:
  take the same timestamped backup 04q takes.
- `tests/run.sh`: nothing else; do not touch (Task 7 owns it).
