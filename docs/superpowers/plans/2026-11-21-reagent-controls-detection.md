# Reagent Controls in Detection: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Use confirmed reagent controls in four ways:
- check, and optionally set, the positivity-cut multiplier;
- report a corrected positive rate beside the raw one;
- constrain PCNA detection tuning;
- flag autofluorescence-like nuclei.

**Architecture:**
- Pure logic lives in `scripts/ls_reagent_report.py`, `scripts/ls_overlay.py` and two new modules:
  - `scripts/ls_af_model.py`: the numpy logistic model;
  - `scripts/ls_nucleus_features.py`: per-nucleus features.
- Stages:
  - 06a writes two new results files;
  - 05b and 05c gain the control-aware PCNA path;
  - two new stages: `06l_nucleus_features.py` (backfill) and `06k_af_score.py`.
- The app shows the numbers and adds one apply button.

**Tech Stack:** Python 3, numpy and scipy (already used), pylibCZIrw via `czi_read`, PySide6, openpyxl. No new dependency.

**Spec:** `docs/superpowers/specs/2026-11-21-reagent-controls-detection-design.md` (approved). It builds on `docs/superpowers/specs/2026-11-20-reagent-controls-design.md`.

## Global Constraints

- **The cut stays relative:** median + k × 1.4826 × MAD of the section's own background-disc nuclei (`ls_overlay`). A control's numbers are never used as an absolute intensity.
- **Pairing tiers:** only `paired_with` (the operator), `same_file` and `same_animal`. Study-wide pairing is never used for any number in this plan.
- **Control kinds:**
  - no_primary for false-positive rates, the k check, corrections and the PCNA constraint;
  - unstained, else no_primary, for the autofluorescence model, never pooled;
  - isotype and preadsorbed never used.
- **Raw values are never overwritten or removed.** New numbers are new files or new columns.
- **Blinding.** Nothing new reads `groups.by_animal`. The apply button warns and requires typed confirmation when 06b has already run.
- **No controls, no change.** A study with no confirmed reagent controls must produce byte-identical outputs to today for:
  - `roi_measurements.csv`;
  - `positivity_cuts.csv`;
  - `detection.auto`;
  - `roi_nuclei.csv`.
- **Defaults** (exact):
  - `positivity.fp_target = 0.01`
  - `positivity.cut_k = {"*": 3.0}`
  - `positivity.fp_min_nuclei = 50`
  - k grid {2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0}
  - 4000 Monte-Carlo draws, seed 20261121
  - autofluorescence gate 200 nuclei, flag at score ≥ 0.5
- **Small counts:** below a minimum, the cell is blank with a note, never 0.
- **Every new stage, script and CSV:**
  - has its technical-guide entry and `file_formats.json` registration (`tests/test_technical_guide.py`);
  - is registered in `app/stages.py` and keeps `tests/test_workflow.py` passing;
  - passes the full suite, `PY=python3 QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS=--no-sandbox bash tests/run.sh`, before each commit, with "ALL SUITES PASS" and no "Segmentation" in the output.
- Terminology: "reagent control". Every result is labelled provisional until the Task 8 validation.

## Review Focus

1. **Study with no controls (LS):** every existing output is byte-identical. Tested in Task 1 (`no_controls_identical`) and Task 5 (`no_controls_detection_auto_identical`).
2. **`cut_k` changed after unblinding:** the apply path warns and refuses without typed confirmation. Tested in Task 3 (`apply_after_unblinding_requires_confirmation`).
3. **A control section with fewer than 5 usable background nuclei (no cut):** it is excluded from the k check and the correction, and the note says why; it never counts as 0 false positives. Tested in Task 2 (`control_without_cut_excluded`) and Task 4 (`control_without_cut_not_used`).
4. **Multiplex, a scene that is a control for one marker only:** that marker's k, correction and PCNA constraint use it; the other marker treats it as stained. Tested in Task 2 (`multiplex_partial_control_k`) and Task 4 (`multiplex_partial_control_correction`).
5. **Per-marker `cut_k` with a marker missing from the map:** it falls back to `"*"`, then to 3.0. Tested in Task 1 (`cut_k_fallback_chain`).

---

### Task 1: Configurable cut multiplier

**Files:**
- Modify: `scripts/ls_config.py`
  - SPEC keys: `positivity.fp_target` (float, 0.001–0.2), `positivity.cut_k` (raw map), `positivity.cut_k_source` (raw), `positivity.fp_min_nuclei` (int ≥ 1);
  - validation of the `cut_k` map, with values in [1.0, 10.0] and keys a marker name or `"*"`;
  - `mad_k`'s note at :610 changed to say it is separate and not linked.
- Modify: `scripts/ls_overlay.py`:
  - keep `CUT_K = 3.0` as the fallback;
  - add `cut_k(config: dict, marker: str | None) -> float`;
  - `section_cut(values, k: float = CUT_K)`.
- Modify every `section_cut` caller to pass `k=LO.cut_k(CONFIG, marker)`:
  - `scripts/05c_detect_rois.py:1125`;
  - `scripts/06a_roi_dataset.py:506`, `:563` and `:785`;
  - `scripts/ls_reagent_report.py`, where it computes cuts;
  - `scripts/06i_detection_overlays.py`, if it computes one.
- Modify: `app/stages.py`: add `positivity.cut_k` to `settings` of `roi_dataset` and `build_results`; give `detection_overlays` (06i) `settings=["positivity.cut_k"]`.
- Modify: `app/workflow.py`: a Detection settings entry for `fp_target`, plus a read-only per-marker `cut_k` table; the apply button comes in Task 3.
- Regenerate `config.example.json` and `docs/config-reference.md`.
- Test: `tests/test_cut_k.py`

**Interfaces:**
- **Produces:** `LO.cut_k(config, marker) -> float`, and `LO.section_cut(values, k=3.0) -> float | None`.
- **Fallback chain:** `cut_k[marker]`, then `cut_k["*"]`, then `CUT_K`.

- [ ] **Step 1: Write the failing tests** in `tests/test_cut_k.py`:
  - `cut_k_fallback_chain`:
    - `cut_k({"positivity": {"cut_k": {"pERK": 3.5, "*": 2.5}}}, "pERK") == 3.5`
    - the same config with `"PCNA"` gives 2.5
    - `cut_k({}, "pERK") == 3.0`
  - `section_cut_default_unchanged`: for values `[100]*10 + [110]*5`, `section_cut(v) == section_cut(v, 3.0)`, and equals the old formula.
  - `validate_cut_k`: `ls_config.validate` refuses `{"pERK": 0.5}`, `{"pERK": 11}`, a non-dict, and `fp_target = 0.5`.
  - `no_controls_identical`: on the existing 06a test fixture with no `positivity` block, `roi_measurements.csv` and `positivity_cuts.csv` are byte-identical to the files produced at the base commit. Generate the golden files from the base in a temporary worktree, inside the test setup.
  - `stages_track_cut_k`: changing `positivity.cut_k` marks roi_dataset out of date and does not mark detect out of date. Use the `tests/test_workflow.py` helpers.
- [ ] **Step 2:** Run `python3 tests/test_cut_k.py`. Expected: FAIL.
- [ ] **Step 3:** Implement. 05c's cut affects only its overlay crops, and 05c must not list `positivity.cut_k`.
- [ ] **Step 4:** Run the new test, `tests/test_workflow.py`, `tests/test_config_example.py` and `tests/test_technical_guide.py`, then the full suite. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: per-marker positivity cut multiplier in config`.

---

### Task 2: The k check (`results/reagent_cut_check.csv`)

**Files:**
- Modify: `scripts/ls_reagent_report.py`: new functions.
- Modify: `scripts/06a_roi_dataset.py`:
  - generalise `leave_one_disc_out(discs, k=3.0)`;
  - call the check from the reagent-report block;
  - write the file atomically, and remove it when no no_primary control is measured.
- Modify: `docs/guides/file_formats.json` and the technical guide.
- Test: `tests/test_reagent_cut_check.py`

**Interfaces:**
- **Consumes:** Task 1's `section_cut(values, k)`.
- **Produces:**
  - `K_GRID = (2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0)`
  - `wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]`
  - `cut_check_rows(controls: dict, stained_discs: dict, fp_target: float, ks=K_GRID) -> list[dict]`
    - `controls` maps `(uid, marker)` to `{"bg": [values], "roi": [(median, censored)]}`, no_primary only.
    - `stained_discs` maps `(uid, marker)` to a list of per-disc value lists.
    - Output columns: `marker, k, n_control_sections, n_control_nuclei, control_fp, control_fp_lo, control_fp_hi, disc_fp, disc_fp_n, meets_target, suggested, note`.
- **Rates:**
  - `control_fp` is pooled over control sections: positives at that section's own cut at k, divided by nuclei.
  - `disc_fp` comes from `leave_one_disc_out` at k, pooled over stained sections.
  - `suggested = 1` on the smallest k with `meets_target`. If none meets it, no row is suggested and the note says "no k on the grid meets the target".

- [ ] **Step 1: Write the failing tests:**
  - `wilson_known`: `wilson(1, 100)` ≈ (0.0018, 0.0545) to 1e-3.
  - `fp_falls_with_k`: synthetic control bg `[100]*20` plus a spread, with roi medians spread above; `control_fp` is non-increasing across `K_GRID`.
  - `suggested_smallest_meeting_target`: with `fp_target=0.05`, exactly one row per marker has `suggested == 1`, and it is the first k with `control_fp ≤ 0.05`.
  - `control_without_cut_excluded`: a control with 4 bg values is not counted in `n_control_sections`, and the note names it.
  - `multiplex_partial_control_k`: a scene that is a control for pERK only appears in pERK's rows and not in PCNA's.
  - `isotype_not_used`: an isotype control contributes nothing.
  - `leave_one_disc_out_k3_unchanged`: `leave_one_disc_out(discs) == leave_one_disc_out(discs, 3.0)`, and equals the base behaviour on the existing 06a fixture.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement. 06a prints per marker the suggested k, and whether `disc_fp` falls inside `[control_fp_lo, control_fp_hi]` at the k in force ("discs agree with controls" / "discs understate" / "discs overstate").
- [ ] **Step 4:** Run the test, the 06a suites, `tests/test_reagent_report.py` and the full suite. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: check the positivity cut against no-primary reagent controls`.

---

### Task 3: Show the check, and apply a suggested k

**Files:**
- Modify: `scripts/06c_excel_dataset.py`: a "Cut check" block on the "Reagent controls" sheet, taken from `reagent_cut_check.csv`, with a provisional label.
- Modify: `app/workflow.py` and `app/screens.py`: the Results page shows each marker's suggested k against the k in force, plus a "Use the suggested multiplier…" button.
- Create: `app/cut_apply.py`, with `apply_suggested_k(project, out_root, marker: str, confirm_text: str | None) -> tuple[bool, str]`:
  - it writes `positivity.cut_k[marker]` and `positivity.cut_k_source[marker] = {"by": "controls", "check": <sha256 of the csv>, "at": <iso time>}`, with `by="cut check"`;
  - when 06b's output `results/` sampling join exists (find 06b's output file), it refuses unless `confirm_text == "apply after unblinding"`.
- Test: `tests/test_cut_apply.py` (offscreen Qt for the button, pure for `apply_suggested_k`).

**Interfaces:**
- **Consumes:** Task 2's CSV columns, and Task 1's config keys.

- [ ] **Step 1: Write the failing tests:**
  - `apply_writes_k_and_source`: `cut_k["pERK"]` equals the suggested k, `source["pERK"]["by"] == "controls"`, and the check sha matches the file.
  - `apply_after_unblinding_requires_confirmation`: with a fake 06b output present, `apply_suggested_k(..., confirm_text=None)` returns `(False, msg)` and the config is unchanged, while `"apply after unblinding"` succeeds.
  - `no_suggestion_no_button`: with no suggested row, the button is disabled and says why.
  - `sheet_has_cut_check`: the 06c workbook's "Reagent controls" sheet contains the k rows and the word "provisional".
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the tests and the full suite. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: show the cut check and apply a control-derived multiplier`.

---

### Task 4: Corrected positive rate (`results/roi_corrected.csv`)

**Files:**
- Modify: `scripts/ls_reagent_report.py`: new functions.
- Modify: `scripts/06a_roi_dataset.py`: build and write `results/roi_corrected.csv` atomically; remove it when no no_primary control is measured.
- Modify: `scripts/06c_excel_dataset.py`: the corrected columns beside the raw rate on the data sheet, plus a header sentence stating the sensitivity-1 assumption.
- Modify: `docs/guides/file_formats.json` and the technical guide.
- Test: `tests/test_reagent_correction.py`

**Interfaces:**
- **Consumes:** `pairing_for(uid, marker, lookup, scene_rows, config, boxed=None)` (`ls_reagent_report.py:126`); Task 1's cut.
- **Produces:**
  - `controls_for(uid, marker, lookup, scene_rows, config, boxed=None) -> tuple[str, list[str]]`: the inverse pairing for a stained section. It returns `(tier, control_uids)` with tier ∈ {operator, same_file, same_animal, none}, and never uses study-wide pairing.
  - `rogan_gladen(n_pos, n, f_pos, f_n, draws=4000, seed=20261121) -> dict`: keys `corrected, lo, hi, note`. It uses Jeffreys Beta(k+0.5, n−k+0.5) draws, and `corrected = (p − f)/(1 − f)` from the point estimates `p = n_pos/n`, `f = f_pos/f_n`. When `p ≤ f`, it returns blanks with the note "at or below the reagent control's false-positive rate".
  - `density_correction(pos, area_mm2, f_pos, f_area_mm2, draws=4000, seed=20261121) -> dict`: Gamma(k+0.5) Poisson draws, giving `corrected = d − d_f`.
  - `ROI_CORRECTED_COLUMNS = ["scene_uid","marker","region","n_nuclei","n_positive","frac_positive","frac_positive_corrected","frac_positive_corrected_lo","frac_positive_corrected_hi","fp_rate","fp_rate_n","fp_rate_source","correction_note"]`
- **f** is pooled over the paired no_primary controls' ROI nuclei in the same region at their own cut. If fewer than `positivity.fp_min_nuclei`, it falls back to all of those controls' regions, and `fp_rate_source` says "all regions".

- [ ] **Step 1: Write the failing tests:**
  - `rg_point`: `rogan_gladen(30, 100, 5, 100)["corrected"]` ≈ (0.30 − 0.05)/0.95 to 1e-9.
  - `rg_interval_contains_point`: `lo ≤ corrected ≤ hi`, and the result is deterministic with the seed.
  - `rg_below_fp_blank`: `rogan_gladen(3, 100, 5, 100)` gives `corrected == ""` and the note text.
  - `inverse_pairing_tiers`:
    - a same-file control wins over a same-animal one;
    - `paired_with` wins over both;
    - with no same-animal control, the result is `("none", [])`.
  - `control_without_cut_not_used`: a control with no cut is skipped, and the source says so.
  - `region_fallback`: with 20 control nuclei in the region and `fp_min_nuclei=50`, the source says "all regions".
  - `multiplex_partial_control_correction`: the PCNA rows of a scene that is a pERK-only control are corrected as stained rows.
  - `roi_measurements_unchanged`: `roi_measurements.csv` is byte-identical with or without this task.
  - `own_marker_density`: a `segment: own` marker gets `frac_positive_corrected` blank, and the density correction is computed.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the tests, `tests/test_reagent_report.py`, the 06a/06c suites and the full suite. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: corrected positive rate from paired no-primary reagent controls`.

---

### Task 5: PCNA tuning constraint, and control detection at the borrowed range

**Files:**
- Modify: `scripts/ls_tune.py`: add `constrain(curve_stained, curve_control, area_stained_mm2, area_control_mm2, chosen, fp_target) -> tuple[float | None, str]`.
- Modify: `scripts/05b_tune_detection.py` (`tune_marker` :179):
  - for `segment: own` markers with at least one pairable no_primary control, run `count_curve` on control ROI central windows, using the paired stained section's `section_range`, at the same grid;
  - call `constrain`;
  - add the new columns to `qc/detection_tuning.csv`: `control_density`, `stained_density`, `admissible`, `constraint_moved_from`.
- Modify: `scripts/05c_detect_rois.py`: for `segment: own` markers, a control section uses its paired stained section's `section_range`. It falls back to its own range when none is pairable, and the `roi_normalisation.csv` row says which was used (new column `range_from`).
- Test: `tests/test_pcna_constraint.py`

**Interfaces:**
- **Consumes:** `controls_for` (Task 4), and `pairing_for` for control → stained. `section_range(doc, boxes, marker, scene)` is at `05c:690`.
- **`constrain` semantics:**
  - A grid point p is admissible when `control_density(p) ≤ fp_target × stained_density(p)`.
  - If `chosen` (None means the model default, 0.479) is admissible, it is returned unchanged.
  - Otherwise the result is the lowest admissible grid point above it, with the reason "moved from X: control false detections above target".
  - If none is admissible, `chosen` is returned with the reason "no admissible threshold; kept".

- [ ] **Step 1: Write the failing tests:**
  - `admissible_unchanged`: when every point is admissible, the result is `chosen`.
  - `moves_up_to_lowest_admissible`: a synthetic curve where points 0.30–0.45 are inadmissible and 0.50+ admissible, with `chosen=0.40`, returns 0.50.
  - `none_admissible_keeps`.
  - `no_controls_detection_auto_identical`: on the existing 05b test fixture with no controls, `detection.auto` and `qc/detection_tuning.csv` are byte-identical to base, and the constraint code is not entered (assert through a spy).
  - `control_uses_borrowed_range`: 05c's `roi_normalisation.csv` shows `range_from == <stained uid>` for a control with a pair, and `"own"` without one. Stained sections are unchanged.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the tests, `tests/test_tune_detection.py`, the detect suites and the full suite. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: PCNA tuning constrained by reagent-control false detections`.

---

### Task 6: Per-nucleus features backfill (`06l_nucleus_features.py`)

**Files:**
- Create: `scripts/ls_nucleus_features.py`, with `features(label_img, nucleus_id, dapi, marker, other=None) -> dict`. Keys:
  - `roundness`: 4πA/P²;
  - `solidity`: area divided by convex hull area;
  - `marker_cv`;
  - `marker_spots`: local maxima above the nucleus median + 3 × 1.4826 × MAD;
  - `other_marker_median`: "" when `other` is None.
- Create: `scripts/06l_nucleus_features.py`:
  - re-reads each section's boxes and `detection_outlines/<marker>/<uid>.npz` with `czi_read`, and never runs StarDist;
  - writes `results/nucleus_features.csv`, keyed `scene_uid, marker, roi_index, nucleus_id`, plus the feature columns;
  - resumes per section with an atomic append checkpoint, as 01m does;
  - takes `--force`.
- Modify: `app/stages.py`: a new stage after `roi_dataset`, made_from detect; then `docs/guides/file_formats.json`, the technical guide and the pipeline guide.
- Test: `tests/test_nucleus_features.py`

**Interfaces:** produces `results/nucleus_features.csv`, consumed by Task 7.

- [ ] **Step 1: Write the failing tests:**
  - `disc_roundness`: a filled disc label has roundness > 0.85 and solidity > 0.95.
  - `granular_more_spots`: a granular synthetic marker inside a nucleus gives `marker_spots` ≥ 3 and a higher `marker_cv` than a smooth one.
  - `offset_gain_invariant_texture`: `marker_cv`'s ordering between two nuclei is unchanged under ×2 + 1000, and `marker_spots` is equal under the same transform.
  - `resume`: a `--limit`-style stop after k sections, then a re-run, reads only the rest (count the reader calls with a stand-in reader, as `tests/test_reagent_screen.py` does).
  - `roi_nuclei_untouched`: `roi_nuclei.csv` is byte-identical after the stage.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the tests, `tests/test_workflow.py`, `tests/test_technical_guide.py` and the full suite. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: 06l backfills per-nucleus shape and texture features`.

---

### Task 7: Autofluorescence flag (`06k_af_score.py`)

**Files:**
- Create: `scripts/ls_af_model.py`:
  - `fit(X: np.ndarray, y: np.ndarray, l2: float = 1.0, seed: int = 20261121) -> dict`: standardised features, L2 logistic regression by IRLS, balanced class weights;
  - `predict(model: dict, X) -> np.ndarray`;
  - `features_matrix(rows: list[dict], section_stats: dict) -> tuple[np.ndarray, list[str]]`, with section-relative features exactly as the spec lists them.
- Create: `scripts/06k_af_score.py`:
  - **Training class:** above-cut nuclei of unstained controls when there are at least 200 per marker (flag name `af_suspect`), else no_primary (flag name `control_like`). Never pooled.
  - **Unlabelled class:** above-cut stained nuclei.
  - **Writes `results/af_scores.csv`:** `scene_uid, marker, roi_index, nucleus_id, af_score, flag_name, flagged, trained_on`.
  - **Writes `qc/af_review.png`:** the 24 highest-scoring stained nuclei plus 24 random ones (seeded), as 64 px crops.
  - Below the gate it writes nothing and prints why.
- Modify: `scripts/06c_excel_dataset.py`: per region × marker, the positive rate without flagged nuclei, beside the raw rate, labelled provisional.
- Modify: `app/stages.py`, `docs/guides/file_formats.json`, the technical guide, the pipeline guide and `docs/guides/pipeline-methods.md`. The methods guide covers the reagent-controls-in-detection section, the provisional status, and the validation criterion: precision ≥ 0.8 on a hand-labelled strip.
- Test: `tests/test_af_score.py`

**Interfaces:**
- **Consumes:** `results/nucleus_features.csv` (Task 6), `roi_nuclei.csv`, and Task 1's cuts.

- [ ] **Step 1: Write the failing tests:**
  - `fit_separates`: on two Gaussian clouds, a fitted model gives AUC > 0.9, and is deterministic with the seed.
  - `unstained_preferred`: with 250 unstained and 300 no_primary above-cut nuclei, `trained_on == "unstained"` and `flag_name == "af_suspect"`. With 150 unstained, it is `"no_primary"` / `"control_like"`.
  - `gate_blocks`: with 150 nuclei of either kind, no file is written and the message names the count.
  - `counts_unchanged`: `roi_measurements.csv` and `roi_corrected.csv` are byte-identical after 06k.
  - `sheet_has_without_flagged`: the 06c workbook has the "without flagged" column, and the word provisional.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the tests and the full suite. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: 06k flags autofluorescence-like nuclei from reagent controls`.

---

### Task 8: Validation on the operator's study (blocked until the data arrives)

This runs on the study with known controls:
- the k check: record the suggested k per marker, and whether the discs agree with the controls;
- the coverage of `roi_corrected.csv`: how many regions have a pairable control;
- how far the PCNA constraint moved the threshold;
- the autofluorescence review strip, hand-labelled by the operator, with the precision at 0.5 recorded.

Write `docs/superpowers/specs/2026-11-21-reagent-controls-detection-validation.md`. The PR stays a draft until this is done (the operator's decision).
