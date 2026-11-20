# Reagent Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a study declare, find (by name or by signal), exclude, measure and report negative
reagent controls.

**Architecture:**
- Two new pure modules hold the logic, and the existing stages call them:
  - `scripts/ls_reagent_controls.py`: config, name scan, lookup.
  - `scripts/ls_reagent_screen.py`: signal features and scoring.
- One new stage, `01m_reagent_screen.py`, produces candidates.
- `06a` routes control rows into a new `results/reagent_controls.csv`, built by
  `scripts/ls_reagent_report.py`. `06c` adds a sheet.
- The app gets a three-way choice in New Study and a confirm dialog.

**Tech Stack:** Python 3, numpy/scipy (already used), pylibCZIrw via `czi_read`, PySide6 (app),
openpyxl (06c). Tests are standalone scripts using the repo's `chk()` pattern
(`python tests/test_x.py`, exit non-zero on any FAIL), registered in `tests/run.sh` like the others.

**Spec:** `docs/superpowers/specs/2026-11-20-reagent-controls-design.md` (approved). Read it before
any task. Section references (§n) below point into it.

## Global Constraints

- **Terminology:** UI, config, columns and files say "reagent control". Never bare "control" or
  "negative".
- **Scene uid:** its shape never changes, and the flag never lives in the uid.
- **Blinding:**
  - No reagent-control code path reads `groups.by_animal` before 06b.
  - The name scan may read `groups.order` labels only.
- **Positivity rule:** reuse `ls_overlay.section_cut` / `is_positive` unchanged. CUT_K = 3.0,
  MAD_SCALE = 1.4826, CUT_MIN_NUCLEI = 5.
- **Config keys:**
  - Every new key goes in `ls_config.SPEC`.
  - Regenerate `config.example.json` and `docs/config-reference.md`
    (`python scripts/ls_config.py --docs docs/config-reference.md`, plus the example writer
    `test_config_example.py` names).
  - Add each key to the reading stages' `settings=[...]` in `app/stages.py`, so the existing
    out-of-date tracking covers late confirmation (§5). `tests/test_workflow.py` enforces this.
- **Screen invariance:** no screen feature may depend on absolute intensity level or on
  tissue/background contrast (spec anti-goal).
- **Background discs:** no text anywhere calls a background disc a negative control.
- **Atomic writes:** every output goes through `ls_io` / `IO.atomic_write_csv`, like the other
  stages.

## Review Focus

1. **Control file in a folder or zip** (`Negative controls/LS45_3.czi`, a zip member path): the
   name scan must see the folder part. Tested in Task 1 (`folder_match`).
2. **Multiplex scene that is a control for one marker only:** its other marker's rows stay
   experimental everywhere. Tested in Tasks 1 (`lookup_per_marker`) and 5
   (`multiplex_partial_control`).
3. **Animal with both stained slides and a control slide:**
   - separate series in `assign_section_order`;
   - never chosen as the scan of a slide by `choose_variants`;
   - the animal still joins the workbook normally.

   Tested in Task 2 (`own_series`, `never_chosen_variant`) and Task 5 (`animal_with_both`).
4. **`declared: yes` with zero confirmed entries:** the screen must still run, and 05b/05c must warn.
   Tested in Task 3 (`warn_unanswered`) and Task 4 (`screen_runs_when`).
5. **A control file whose name parses normally** (`LS45_3nc.czi`): its uid is ordinary, so every
   exclusion must use the lookup and never the name. Tested in Task 2 (`parsed_control_lookup`).

---

### Task 1: Config key, name scan and lookup (`ls_reagent_controls.py`)

**Files:**
- Create: `scripts/ls_reagent_controls.py`
- Modify: `scripts/ls_config.py`
  - SPEC: new keys after `groups.by_animal`, around :840;
  - `_check`: validate them.
- Modify: `config.example.json`, `docs/config-reference.md` (regenerated)
- Test: `tests/test_reagent_names.py`

**Interfaces:**
- Produces:
  - `KINDS = ("no_primary", "unstained", "isotype", "preadsorbed", "other")`
  - `@dataclass Match(path: str, kind: str, tier: str  # "strong"|"weak", token: str, markers: list[str])`
  - `scan(paths: list[str], markers: list[str], group_labels: list[str] = (), extra: list[dict] = ()) -> list[Match]`
    - `paths` are relative to the slides folder and may contain `/` or `\`.
    - At most one Match per path: the strongest, first by tier and then by kind order.
  - `entries(config: dict) -> dict` normalises `reagent_controls`, filling missing sub-keys with
    `{}` / `[]` / `"no"`.
  - `expand(config: dict, scene_rows: list[dict]) -> dict[(str, str), dict]`
    - Rows need `scene_uid`, `file`, `marker_channel`.
    - Returns `(scene_uid, marker) -> entry` for every flagged scene × marker.
    - A file entry expands to all its scenes, and a scene entry overrides it.
    - `markers: []` means every marker in the rows.
  - `is_control(lookup: dict, uid: str, marker: str) -> bool`
  - `control_subject(config: dict, basename: str) -> dict | None` returns
    `{"subject", "slide", "replicate"}` from `files.<basename>`.
  - `unanswered(config: dict, matches: list[Match]) -> list[Match]` returns strong matches that are
    neither confirmed (in `files`/`scenes`) nor in `rejected`.
- SPEC keys (all `ui=False, in_example=False` except `declared`):
  - `reagent_controls.declared`: choices `no|yes|unsure`, default `"no"`.
  - `reagent_controls.files`, `reagent_controls.scenes`, `reagent_controls.paired_with`:
    `raw`, default `{}`.
  - `reagent_controls.rejected`: `list_str`, default `[]`.
  - `reagent_controls.extra_patterns`: `raw`, default `[]`, each `{"regex", "kind"}`.
  - Consumers: `["00", "01", "01m", "02", "05b", "05c", "06a", "06c", "app"]`.

- [ ] **Step 1: Write the failing tests.** Every name list is fixed and copied into the test.
  - `real_names_never_match`:
    `scan(["LS105_10a.czi","LS105_10b.czi","LS22_1a.czi","LS45_8a.czi","LS53_2b-_A568.czi","LS45_8b_A488.czi","LS85_7b-.czi","AB12_1a.czi","AB12_C_3.czi","LS01_s01a.czi","not_a_slide.czi","D:/slides/LS45_9b.czi","LS61_4c.czi"], ["pERK","PCNA"]) == []`
  - `strong_kinds` maps name to (kind, tier):
    - `no_primary`, strong:
      - `LS45_3a_noPrimary.czi`, `LS45 no 1ary.czi`, `LS45_w-o-primary.czi`
      - `LS45_3_-1Ab.czi`, `LS45_3_senza_primario.czi`, `LS45_uten_primaer.czi`
      - `LS45_ohne-1AK.czi`
      - `LS45_2ndOnly.czi`, `LS22_sec-only_A568.czi`
      - `NegCtrl_LS45_3.czi`, `LS45_controllo_negativo.czi`
    - `isotype`, strong: `LS45_isotype.czi`, `LS45_rbIgG.czi`
    - `preadsorbed`, strong: `LS45_preadsorbed.czi`
    - `unstained`, strong: `LS45_unstained.czi`, `LS45_noAb.czi`
  - `marker_specific`: `scan(["LS45_no-pERK.czi"], ["pERK","PCNA"])[0].markers == ["pERK"]`, with
    kind `no_primary`.
  - `folder_match`: `Negative controls/LS45_3a.czi` gives strong `no_primary`.
  - `weak`: `LS45_NC.czi`, `LS45_neg.czi` and `LS45_ctrl.czi` give tier `weak`.
  - `group_label_suppresses_weak`: `scan(["LS45_ctrl.czi"], [], group_labels=["ctrl","ex"]) == []`.
  - `never_tokens`: `["LS45_AF488.czi","LS45_IgG.czi","LS45_C_3.czi","LS45_3b-.czi"]` give `[]`.
  - `extra_patterns`: `extra=[{"regex": "blankslide", "kind": "unstained"}]` matches
    `LS45_blankslide.czi`.
  - `lookup_per_marker`:
    - Given config `files: {"A_1.czi": {"kind":"no_primary","markers":["pERK"]}}` and rows for
      `A_s01_sc01` with markers pERK and PCNA, `expand` returns only `("A_s01_sc01","pERK")`.
    - A `scenes` entry with `markers: []` overrides it to both markers.
  - `config_validation`:
    - `ls_config.validate` refuses `files.x.subject` containing `_`;
    - it refuses an unknown kind;
    - it refuses an `extra_patterns` regex that does not compile.

- [ ] **Step 2: Run** `python tests/test_reagent_names.py`. Expected: FAIL (module missing).

- [ ] **Step 3: Implement the module and the SPEC keys.**
  - The raster is spec §2 verbatim, lifted from the prototype in the session scratchpad, which
    passes these lists.
  - Delimiter lookarounds are `(?:^|(?<=[\s_\-.()/\\]))` and `(?=$|[\s_\-.()/\\])`, never `\b`.
  - Matching is case-insensitive.

- [ ] **Step 4: Regenerate the example and reference.** Run `python tests/test_reagent_names.py`
  and `python tests/test_config_example.py`. Expected: all `ok`.

- [ ] **Step 5: Commit** `feat: reagent-control config keys and name scan`.

---

### Task 2: Manifest and series (`00`, `02`)

**Files:**
- Modify: `scripts/00_manifest.py`
  - `parse_name` :187: fall back to `control_subject`;
  - row build :704-774: add a `reagent_control` column, holding the kind or `""`, per scene ×
    marker;
  - `assign_section_order` :389: series key;
  - `main`: warning.
- Modify: `scripts/02_pair_passes.py:119` `choose_variants`
- Test: `tests/test_reagent_manifest.py` (use `_fixture.use_temp_study` and the header writers the
  way `test_new_study.py` reuses `test_autodetect.py`)

**Interfaces:**
- Consumes: Task 1 `control_subject`, `expand`, `scan`, `unanswered`.
- Produces:
  - `manifest_scenes.csv` and `manifest_files.csv` gain the column `reagent_control`, holding a
    kind or `""`. Later tasks may use it for display only. Exclusion always goes through
    `expand` on config.
  - Series key `(animal + "#rc", marker)` for flagged scenes. Internal only.

- [ ] **Step 1: Write the failing tests:**
  - `nonparsing_with_subject`:
    - `NC_3.czi` with `files: {"NC_3.czi": {"kind":"no_primary","subject":"LS45","slide":3,"replicate":"nc"}}`
      gets uid `LS45_s3nc_sc01`;
    - it is not in the failures;
    - its `reagent_control == "no_primary"`.
  - `nonparsing_without_subject`: `NC_3.czi` with no entry is still a "filename did not match"
    failure.
  - `two_controls_no_collision`: `NC_3.czi` and `NC_4.czi`, both subject `LS45` with slides 3 and
    4, give `duplicate_uids == []`.
  - `parsed_control_lookup`: `LS45_3nc.czi` (parses normally) with a `files` entry gets
    `reagent_control` set.
  - `own_series`:
    - an animal with `LS45_1a`, `LS45_2a` and control `LS45_3nc` keeps `section_order` 1..n
      over 1a and 2a only;
    - the control is numbered within its own series.
  - `never_chosen_variant`: `choose_variants` on slide 3 with variants `a` (stained, 4 scenes) and
    `nc` (control, 6 scenes) picks `a`.
  - `unconfirmed_warns`: `declared: no` with `LS45_noPrimary.czi` present prints a line containing
    `reagent control` and the file name, and exits 0.

- [ ] **Step 2: Run** `python tests/test_reagent_manifest.py`. Expected: FAIL.
- [ ] **Step 3: Implement.** `choose_variants` drops flagged rows before choosing, and a slide whose
  every variant is a control still keeps its own entry.
- [ ] **Step 4: Run** the new test, plus `python tests/test_autodetect.py` and every
  `tests/test_*manifest*` / `test_pair*` that exists. Expected: PASS.
- [ ] **Step 5: Commit** `feat: manifest carries reagent controls, own series, never a chosen scan`.

---

### Task 3: Keep controls out of estimates; measure them first (`01`, `05b`, `05c`)

**Files:**
- Modify: `scripts/01_overviews.py:378` `sample_display_ranges`: draw `pool` from non-control
  scenes.
- Modify: `scripts/05b_tune_detection.py:78` `sample_sections`: the caller filters `uids`.
- Modify: `scripts/05c_detect_rois.py`
  - at :2139, controls go first, then `balanced_order(rest, done)`;
  - plus the warning.
- Modify: `app/stages.py`: add `reagent_controls.files`, `.scenes`, `.declared` to `settings` for
  `overviews`, `scene_renders`, `tune` (05b) and `detect` (05c).
- Test: `tests/test_reagent_exclusion.py`

**Interfaces:**
- Consumes: Task 1 `expand`, `is_control`, `entries`.
- Produces:
  - `reagent_order(uids: list[str], lookup) -> (controls: list[str], rest: list[str])` in
    `ls_reagent_controls.py`, keeping natural order inside each.
  - `warn_unanswered(config) -> str | None` in `ls_reagent_controls.py`, which returns the
    warning text when `declared` is `unsure` and `qc/reagent_screen.csv` has candidates not in
    `scenes`/`files`/`rejected`.

- [ ] **Step 1: Write the failing tests:**
  - `display_pool_excludes`: sampling with SEED fixed never returns a flagged scene, even when
    every other scene is removed. With no stained scene left, it raises a clear error.
  - `tune_sample_excludes`: the 05b sample over uids with one control never contains it.
  - `controls_first`: `05c` order for uids `[A_sc01, RC_sc01, B_sc01]` with RC flagged starts
    with `RC_sc01`, and the rest equal `balanced_order` of the others.
  - `warn_unanswered`: true for `declared: unsure` with an unanswered candidate row; None after
    the candidate is put in `rejected`.
  - `settings_tracked`: `python tests/test_workflow.py` passes, which means the keys are mapped.

- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** 05b and 05c print `warn_unanswered` and carry on.
- [ ] **Step 4: Run** the new test and `tests/test_workflow.py`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: reagent controls stay out of display ranges and tuning, measured first`.

---

### Task 4: Signal screen (`ls_reagent_screen.py`, `01m_reagent_screen.py`)

**Files:**
- Create: `scripts/ls_reagent_screen.py`: pure features and scoring, numpy only.
- Create: `scripts/01m_reagent_screen.py`: the stage, which reads tiles and writes outputs.
- Modify: `app/stages.py`: new `Stage("reagent_screen", "01m  Screen for reagent controls", "Ingest and QC", ...)`
  after `overviews`.
  - `made_from` the overviews QC.
  - `settings` = `reagent_controls.declared`, `.files`, `.scenes`, `.rejected`.
- Modify: `docs/guides/file_formats.json`: entries for `qc/reagent_screen.csv` and
  `qc/reagent_screen.png`.
- Test: `tests/test_reagent_screen.py`

**Interfaces:**
- Consumes:
  - Task 1 `expand`, `entries`;
  - `czi_read` (as `05b`/`05c` read native tiles);
  - `qc/focus.csv` and the overview tissue mask (`01_overviews.tissue_threshold`);
  - `manifest_files.exposures`.
- Produces in `ls_reagent_screen.py`:
  - `TILES = 12`, `TILE_PX = 512`, `SPOT_K = 5.0`, `Z_CANDIDATE = 3.0`
  - `f1_nuclear_enrichment(marker: ndarray, dapi: ndarray) -> float | None`
    - Spots are local maxima above median + SPOT_K·1.4826·MAD.
    - Nuclei come from Otsu on Gaussian-smoothed DAPI.
    - Returns (spots in nuclei / spots) / (nuclear area / tile area), or None with no spots.
  - `f2_channel_similarity(a: ndarray, b: ndarray, mask: ndarray) -> float`: Spearman over
    masked pixels.
  - `f3_tail_spread(x: ndarray, mask: ndarray) -> float`:
    log(p99.5−p50) − log(p50−p5).
  - `robust_z(values: dict[str, float]) -> dict[str, float]`: (x−median)/(1.4826·MAD), with
    MAD 0 giving 0.
  - `score(rows: list[dict]) -> list[dict]`
    - Input rows: `{scene_uid, marker, f1, f2, f3, guard_dapi_ok, guard_exposure_ok}`.
    - Adds `z1`, `z2`, `z3`, signed so that "control-like" is positive (−f1, +f2, −f3).
    - Adds `n_features`, `candidate` (every available z ≥ 3, n_features ≥ 2, both guards ok),
      `rank = min available z`, and `status` ∈ {`candidate`, `review`, `unusual`, ``}.
    - z is computed **excluding** scenes already confirmed, which are passed in with
      `confirmed=True` and are not scored.
  - `file_rollup(rows) -> dict[file, float]`: the fraction of a file's sections that are
    candidates.
- Stage CLI:
  - `python scripts/01m_reagent_screen.py [--force]`
  - Runs when `declared == "unsure"`, or `"yes"` with no confirmed entries, or with `--force`.
    Otherwise it prints why it skipped and exits 0.
  - F1 is skipped for a marker whose `segment` is `own` or whose DAPI is declared unusable
    (`acquisition.marker_segmentation`), and the row says so.
  - F2 is empty when the file has a single marker.

- [ ] **Step 1: Write the failing tests.** Use synthetic 512² tiles with seeded numpy.
  - `stained_vs_control`:
    - The stained tile has 40 bright spots, 90% inside DAPI discs, and a heavy tail.
    - The control tile has 40 bright puncta placed at random, the same puncta in both marker
      channels, and a light tail.
    - Assert f1 is above 2.0 for stained and below 1.5 for control.
    - Assert f2 is above 0.8 for control and below 0.4 for stained.
    - Assert f3 is larger for stained.
  - `offset_gain_invariant`: f1, f2 and f3 are unchanged to 1e-6 under `x*2.0 + 5000`.
  - `score_candidate`: 30 stained-like rows and 2 control-like rows give exactly the 2 control
    rows as `candidate`, with `rank ≥ 3`.
  - `one_feature_is_review`: with only f3 available, a row is never `candidate`, at most `review`.
  - `guards`: a control-like row with `guard_exposure_ok=False` has status `unusual`.
  - `confirmed_excluded`: confirmed rows do not enter `robust_z`.
  - `screen_runs_when`: a gate function `should_run(config, force)` returns True for `unsure`,
    True for `yes` with no entries, False for `yes` with entries, False for `no`, and True with
    `force`.

- [ ] **Step 2: Run** `python tests/test_reagent_screen.py`. Expected: FAIL.

- [ ] **Step 3: Implement the module, then the stage.**
  - The stage samples `TILES` tiles per section inside the tissue mask with
    `np.random.default_rng(seed=hash of scene_uid)`, and skips tiles touching clipped pixels.
  - It writes `qc/reagent_screen.csv` with columns
    `scene_uid, file, marker, f1, f2, f3, z1, z2, z3, n_features, guard_dapi_ok, guard_exposure_ok, candidate, rank, status, note`.
  - It writes `qc/reagent_screen.png`: the top 8 candidates beside 3 median sections of the same
    marker, all at that marker's `display_ranges.json` range.

- [ ] **Step 4: Run** the test, then `python scripts/01m_reagent_screen.py --force` on the
  tutorial/demo study (`tests/_tutorial_fixture.py`). Expected: PASS, and the CSV is written with
  zero candidates.

- [ ] **Step 5: Commit** `feat: 01m signal screen for reagent controls`.

---

### Task 5: Measure and report (`06a`, `ls_reagent_report.py`, `06c`)

**Files:**
- Create: `scripts/ls_reagent_report.py`
- Modify: `scripts/06a_roi_dataset.py`
  - In the streaming pass (~:560-640), nuclei of a flagged `(uid, mk)` go to `rc_idx` /
    `rc_bg` instead of `by_idx`, `bg_by`, `diam_by` and `uids_by_marker`.
  - So controls never reach `MEAS_CSV`, `SPEC_CSV`, `CUTS_CSV`, h or the Abercrombie fallbacks.
  - After `cuts` is computed (:676), call the report builder and write
    `results/reagent_controls.csv`.
- Modify: `scripts/06c_excel_dataset.py`
  - a "Reagent controls" sheet via `write_workbook`;
  - the limitation wording at :45/:363 switches when a `no_primary` row exists.
- Modify: `app/stages.py`: `build_results` settings gain `reagent_controls.files`, `.scenes`,
  `.paired_with`.
- Modify: `docs/guides/file_formats.json`: `results/reagent_controls.csv`.
- Test: `tests/test_reagent_report.py`

**Interfaces:**
- Consumes: Task 1 `expand`; `ls_overlay.section_cut`, `background_stats`, `is_positive`.
- Produces:
  - `pairing_for(uid: str, marker: str, lookup, scene_rows, config) -> (kind: str, uids: list[str])`
    - `kind` ∈ {`same_file`, `same_animal`, `study_wide`, `operator`}.
    - Order: `paired_with` override, then the same file's stained scenes, then the same animal and
      marker, then all stained sections of that marker.
  - `report_rows(control_nuclei, stained_nuclei, cuts, pairing) -> list[dict]`
    - Nuclei are dicts with `scene_uid, marker, region, roi_kind, marker_median, censored, own`.
    - `cuts` maps `(uid, marker)` to a cut, for both controls and stained sections.
    - Returns one row per control (uid, marker, region) with the columns
      `scene_uid, file, kind, marker, region, pairing, n_nuclei, n_positive, pct_positive, positive_per_mm2, stained_pct_median, stained_pct_min, stained_pct_max, specificity_margin, control_nuclear_median, stained_nuclear_median, level_ratio, pct_above_paired_cut, pct_above_paired_cut_min, pct_above_paired_cut_max, note`.
  - `limitation_text(rows) -> str`: the 06c header text. It returns the existing "no no-primary
    controls" text when no row has kind `no_primary`.

- [ ] **Step 1: Write the failing tests.** Fixtures are hand-built nuclei.
  - `own_cut_rate`:
    - The control's background discs have medians `[100]*10`, so the cut is 100. Its ROI has
      nuclei `[90, 95, 105, 200]`.
    - Expect `n_positive == 2` and `pct_positive == 50.0`.
  - `paired_each_cut`:
    - Two paired stained sections have cuts 150 and 300.
    - The control ROI nuclei are `[90, 95, 160, 200]`.
    - Expect `pct_above_paired_cut_min == 0.0`, `_max == 50.0` and median `25.0`. These are
      scored against each cut, never a pooled cut.
  - `study_wide_blanks_B`: with no same-file or same-animal stained scene,
    - `pairing == "study_wide"`;
    - `level_ratio`, `control_nuclear_median`, `stained_nuclear_median` and every
      `pct_above_paired_cut*` are `""`;
    - `note` contains `batch`.
  - `own_segment_density`: for a marker with `own=True`, `pct_positive == ""` and
    `positive_per_mm2` is set.
  - `multiplex_partial_control`:
    - The scene is flagged for pERK only.
    - After 06a, its PCNA rows are in `roi_measurements.csv` and its pERK rows are not.
    - `reagent_controls.csv` has pERK rows only.
  - `animal_with_both`:
    - LS45 has stained slides and a control slide.
    - 06b joins LS45 normally.
    - An animal whose only scenes are controls is not reported missing by 06b.
  - `limitation_switch`: `limitation_text` with no rows returns the existing text unchanged
    (compare to the string in 06c). With a `no_primary` row, it names the "Reagent controls"
    sheet.
  - `no_disc_language`: no new string in the changed files contains "negative control" next to
    "background disc".

- [ ] **Step 2: Run** `python tests/test_reagent_report.py`. Expected: FAIL.
- [ ] **Step 3: Implement** `ls_reagent_report.py`, then the 06a routing, then the 06c sheet and
  wording.
- [ ] **Step 4: Run** the new test and the existing 06a/06b/06c suites (`tests/test_join_metadata.py`
  and every `test_*06*` / `test_analysis_set.py`). Expected: PASS, and the existing suites are
  unchanged.
- [ ] **Step 5: Commit** `feat: reagent controls measured and reported (06a, 06c)`.

---

### Task 6: App: New Study choice, confirm dialog, badges, workbook check

**Files:**
- Modify: `app/new_study.py`
  - a `QButtonGroup` row "Reagent controls in this folder?" with No (default) / Yes /
    Not sure, check for me, built like Atlas (:36-40, :142-156);
  - `_create` (:309) writes `reagent_controls.declared` and opens the dialog when needed.
- Create: `app/reagent_dialog.py`: `ReagentControlsDialog(project, slides_dir, parent=None)`.
- Modify: the Set up > Slides screen (`app/screens.py` / `app/slides.py`, where "Check workbook"
  lives): a "Reagent controls…" button opens the same dialog.
- Modify: `app/workbook_check.py:45-57`: drop control-only animals from the manifest and filename
  fallbacks.
- Modify:
  - `scripts/04k_level_curator.py` and `scripts/04l_roi_curator.py`: pass `reagent_control` into
    each section's record;
  - `scripts/roi_curator_page.html` and the level curator page: show a "reagent control" badge in
    the section header when it is set. Text only, no logic.
- Modify: `scripts/01d_contactsheets.py`: label flagged sections "reagent control" in the
  sheet caption.
- Modify: the app's Results page text (where it quotes the no-no-primary limitation; find it with
  `grep -rn "no-primary" app/`): use `ls_reagent_report.limitation_text` on
  `results/reagent_controls.csv` when that file exists.
- Test: `tests/test_reagent_dialog.py` (offscreen Qt, like `tests/test_new_study.py`), plus a
  `*.test.js` case in the existing curator suites for the badge.

**Interfaces:**
- Consumes:
  - Task 1 `scan`, `unanswered`, `entries`, `KINDS`;
  - Task 4 `qc/reagent_screen.csv`, if present;
  - `project.set(key, value, by="reagent controls")`.
- Produces: the dialog writes `reagent_controls.files`, `.scenes`, `.rejected` and `.paired_with`
  only.

- [ ] **Step 1: Write the failing tests:**
  - `default_no`: a fresh dialog has No checked, and Create writes `declared == "no"`.
  - `unsure_saved`: choosing Not sure writes `"unsure"`.
  - `strong_match_opens_dialog`: with `declared: no` and `LS45_noPrimary.czi` in the folder,
    Create opens `ReagentControlsDialog`, and its row is pre-ticked with kind `no_primary`.
  - `weak_not_preticked`: `LS45_NC.czi` is listed unticked.
  - `subject_field_only_when_unparsed`: the Animal ID / slide fields show for `NC_3.czi` and stay
    hidden for `LS45_3nc.czi`.
  - `accept_and_reject_written`:
    - Accept writes `files["LS45_noPrimary.czi"] == {"kind":"no_primary","markers":[],"source":"name","pattern":"noPrimary",...}`.
    - Not a control writes the name to `rejected`.
  - `screen_candidates_listed`: with a `qc/reagent_screen.csv` holding one candidate, the dialog
    lists it with `source: signal`.
  - `pairing_override`: "Change pairing…" on a control writes
    `paired_with[<uid or file>] == [<chosen stained uids>]`. The dialog shows the default pairing
    kind from `pairing_for` beforehand.
  - `results_text_switches`: the Results page shows the existing limitation text with no report
    file, and names the "Reagent controls" sheet with a `no_primary` row present.
  - `workbook_skips_control_only_animal`: `workbook_check` does not list `RC1` as missing.
  - `no_groups_by_animal_written`: the existing assertion (`test_new_study.py:197`) still holds.

- [ ] **Step 2: Run** `python tests/test_reagent_dialog.py`. Expected: FAIL.
- [ ] **Step 3: Implement.** The help text names the kinds in plain words, per spec §3.
- [ ] **Step 4: Run** the new test, `tests/test_new_study.py` and `bash tests/run.sh`. Expected:
  PASS.
- [ ] **Step 5: Commit** `feat: app asks about reagent controls and confirms them`.

---

### Task 7: Documentation

**Files:**
- Modify: `README.md` "Limitations" (:720), `docs/wiki/Results.md:43`, and the user guide
  (`docs/guides/user-guide.md:597` and `docs/user-guide.md`).
  - Add a paragraph each covering both cases: a study without controls (text unchanged) and a
    study with them (points to the sheet).
- Modify: `docs/guides/pipeline-methods.md`: a "Reagent controls" subsection. It covers:
  - the kinds (Burry 2011; Hewitt 2014; Torlakovic 2014);
  - the name scan;
  - the screen's three features and their invariance;
  - the per-cut median/range of part B.
- Modify: `REFERENCES.md`: the three citations, plus Wizenty 2018 for broad-band autofluorescence.
- Modify: `LOGS.md`: a dated entry.
- Modify: `docs/guides/technical-guide.md`: regenerate via `docs/make_technical_tables.py` if it
  reads `file_formats.json`.

- [ ] **Step 1:** Run `python docs/check_methods_claims.py` before editing and note its state.
- [ ] **Step 2:** Edit the docs.
- [ ] **Step 3:** Run `python docs/check_methods_claims.py` and `python tests/test_guides_folder.py`.
  Expected: no new failures.
- [ ] **Step 4: Commit** `docs: reagent controls`.

---

### Task 8: Validation on the operator's study with known controls

Blocked until the operator shares the study. This task does not block merging Tasks 1-7: the
thresholds ship as provisional, as the spec says.

**Files:**
- Create: `docs/superpowers/specs/2026-11-20-reagent-controls-validation.md` (results).
- Possibly modify: `scripts/ls_reagent_screen.py` constants (`SPOT_K`, `Z_CANDIDATE`, `TILES`)
  and the raster in `scripts/ls_reagent_controls.py`.
- Test: extend `tests/test_reagent_names.py` with the study's real filenames (control and
  non-control), and add the expected results.

- [ ] **Step 1:** Run `scan` on the study's file list. Record hits per tier against the operator's
  truth. Target: every strong hit is a real control, and no real control is missed by both name
  and screen.
- [ ] **Step 2:** Run `01m --force` with the name-confirmed controls **unconfirmed**. Record per
  section F1-F3, z, candidate and rank against the truth.
- [ ] **Step 3:** Set the constants so that every known no-primary or unstained control is a
  candidate, and LS still gives zero candidates. If no setting does both, record that, and say
  which feature fails, rather than tuning to one study.
- [ ] **Step 4:** Run the LS acceptance (`01m --force` on LS). Expected: zero candidates.
- [ ] **Step 5:** Write the validation doc, update the spec's "provisional" lines, and commit
  `feat: reagent screen thresholds validated on <study>`.
