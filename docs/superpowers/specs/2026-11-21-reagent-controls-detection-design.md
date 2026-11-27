# Reagent controls in detection: check the cut, correct the rate, tune PCNA, flag autofluorescence

**Status:** approved by the operator, 2026-11-21. Built now, merged only after validation on the operator's study with known controls (PR stays draft until then).
**Builds on:** `docs/superpowers/specs/2026-11-20-reagent-controls-design.md` (merged). This is a separate PR.

## Why

Reagent controls now exist in the pipeline: they are confirmed, kept out of the experimental data, measured and reported in `results/reagent_controls.csv`. The operator asked whether they can also improve detection.

Nuclei are segmented on DAPI, so controls cannot improve *finding* nuclei. They can improve three other things:
- **calling a nucleus positive** for every marker;
- **finding objects** for markers segmented on their own channel (PCNA on LS);
- **spotting autofluorescent nuclei**.

The research found no validated percentile for immunofluorescence imaging, no published rule for excluding specks by comparing channels, and no classifier trained on dedicated control slides. Every number here is therefore provisional until it has been validated on the operator's study with known controls (plan Task 8 of the first spec).

## Decisions taken by the operator (2026-11-21)

- **Scope.** All four uses are in scope. They get their own spec and their own PR, after PR 146.
- **1. Checking the cut.** Report the result, plus an optional setting that applies a control-derived multiplier.
- **FP target.** The false-positive target defaults to 1%. The operator can change it in settings, and it is recorded in the config (`study.json`).
- **2. Corrected rate.** Reported beside the raw rate, per region and marker, with a 95% interval. It is computed only from controls paired on the same slide or the same animal. It is left blank with a reason when it cannot be computed.
- **3. PCNA tuning.** A constraint on 05b's existing plateau choice, not a replacement for it. The intensity range for a control is borrowed from a paired stained section.
- **4. Autofluorescence.** Flag only, with no censoring until validated. Allowed features:
  - shape and size;
  - marker texture;
  - the second marker channel (multiplex only);
  - DAPI brightness relative to the section.
- **Process.** This spec gets a reviewer check before it goes to the operator.

## Constraints that shape every part

- **The cut is relative.** Today's cut is median + `CUT_K` × 1.4826 × MAD of the section's own background-disc nuclei (`scripts/ls_overlay.py:94`, `CUT_K = 3.0`). AF568 background differs about 2× between animal batches (LOGS 2026-08-12). So anything taken from a control is expressed **in spreads above the section's own discs**, never as an absolute intensity. Where two sections are compared directly, they must be paired on the same slide or the same animal.
- **Kinds are separate.**
  - Only `no_primary` controls (including secondary-only) estimate false positives from secondary binding plus autofluorescence.
  - `unstained` controls estimate autofluorescence alone.
  - `isotype` and `preadsorbed` controls are never pooled with the others in these calculations.
- **Blinding.** Every quantity here is computed from controls and background discs only, never from group labels. A cut multiplier that is *applied* must be fixed before unblinding, and the app records when it was set. No new code reads `groups.by_animal`.
- **Raw values are never overwritten.** Every new number is an extra column or an extra file.
- **Small counts.** Rates from few control nuclei carry intervals and minimum counts, and are blank, never zero, when below the minimum.

## 1. Checking the cut (report + optional apply)

**Config.** New keys in `ls_config.SPEC`:

| Key | Type | Default | Meaning |
|---|---|---|---|
| `positivity.fp_target` | float | 0.01 | acceptable false-positive rate on no-primary controls |
| `positivity.cut_k` | raw map | `{"*": 3.0}` | the multiplier every stage uses, per marker, with `"*"` as the default; replaces the `CUT_K` constant |
| `positivity.cut_k_source` | raw | `{"by": "default"}` | provenance of `cut_k`: `default` / `operator` / `controls`, plus when it was set and from which check file |

- **Constraints.** Every value in `cut_k` must lie between 1.0 and 10.0, and `fp_target` between 0.001 and 0.2. `cut_k` is validated as a map whose keys are marker names or `"*"`.
- **Readers.** `ls_overlay.section_cut(values, marker=None)` gains an optional marker argument and reads `cut_k` from config, with 3.0 as the fallback. All its callers pass the marker: 05c, 06a, 06i and `ls_reagent_report`.
- **Settings panel.** `fp_target` is a number field under Detection settings. `cut_k` is shown as a small per-marker table there, alongside the apply button described below.
- **Note to fix.** `detection.threshold.mad_k`'s note (`ls_config.py:610`) says it is "the same multiplier 06a uses". It is updated to say the two keys are separate and not linked.
- **A change to `cut_k` never forces re-detection.** 05c's own cut (`05c_detect_rois.py:1125`) only decides how its overlay crops are coloured. When `cut_k` changes, 06i must redraw from 06a's cuts. Making that true is the builder's job, but 05c is never put out of date by `cut_k`.

**Check.** It runs in 06a, as part of the reagent report, and writes `results/reagent_cut_check.csv`.
- Rows: per marker × k, for k in {2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0}.
- Columns:
  - `n_control_nuclei`, `control_fp_rate`: the rate on no-primary control ROI nuclei, each control section's cut formed at k from its own discs;
  - `disc_fp_rate`: the same k applied leave-one-disc-out on **stained** sections' discs, generalising `leave_one_disc_out` in 06a over k;
  - `meets_target` (control_fp_rate ≤ fp_target);
  - `suggested`, 1 on the smallest k that meets the target.
- Interval: a Wilson 95% interval on `control_fp_rate`.

Two diagnostic lines are printed per marker:
- the suggested k;
- whether the disc rate agrees with the control rate within the interval. If it does, the discs are a fair stand-in for studies without controls; if not, the report says the discs understate or overstate.

**Apply.** The 06c "Reagent controls" sheet and the app's Results page show the suggestion. In the app, "Use the suggested multiplier":
- writes `positivity.cut_k` with `cut_k_source = {"by": "controls", "check": <sha of reagent_cut_check.csv>, "at": <time>}`;
- marks 06a and `build_results` (06e) out of date, since `cut_k` is in their `settings`. 06c, 06d and 06i are downstream of those and follow from them; 06i is given `settings` including `cut_k` if it needs them to redraw.

If 06b has already run, the button warns that the study is already unblinded, and it requires a typed confirmation. That is the blinding guard.

**Rules.**
- **Never automatic.** The suggestion never changes the cut by itself.
- **One k per marker.** If markers disagree, the operator picks per marker: `cut_k` may also be a `{marker: k}` map.

## 2. Corrected positive rate (beside raw, with interval)

**Where (operator decision: section × region).** Placed ROIs are pooled per stained (section, region, marker), and the result goes to a new file, `results/roi_corrected.csv`. Its columns:
- `scene_uid`, `marker`, `region`
- `n_nuclei`, `n_positive`, `frac_positive` (the raw pooled rate)
- `frac_positive_corrected`, `frac_positive_corrected_lo`, `frac_positive_corrected_hi`
- `fp_rate`, `fp_rate_n`, `fp_rate_source`
- `correction_note`

The 06c data sheet shows these beside the raw values. `roi_measurements.csv` is unchanged. Note that the raw column in this codebase is `frac_positive`, a fraction, not a percentage (`06a_roi_dataset.py:932`).

**Which control.** For each stained (section, marker, region), the no-primary controls paired to it are found with the **inverse** of `ls_reagent_report.pairing_for`, using only these tiers:
1. the operator's override (`paired_with`);
2. the same file;
3. the same animal and marker.

Study-wide pairing is never used, so batch effects stay out (`fp_rate_source = "none: no same-slide or same-animal control"`). f is the pooled false-positive rate of those controls' ROI nuclei in the same region, at the cut in force. If that region has fewer than `positivity.fp_min_nuclei` (new key, default 50) control nuclei, f falls back to all of those controls' regions, and the source says so.

**Formula.** Rogan–Gladen, assuming nothing real is missed (sensitivity 1): `p_corr = (p_obs − f) / (1 − f)`.
- **Interval.** A Monte-Carlo propagation of Jeffreys Beta posteriors for `p_obs` and `f`: 4000 draws with a fixed seed, taking the 2.5–97.5 percentiles of p_corr, clipped to [0, 1].
- **Blank cases**, each with a note:
  - p_obs ≤ f (the note says "at or below the control's false-positive rate");
  - no pairable control;
  - a marker segmented on its own channel. Its density correction is `positive_density_per_mm2 − control positive_density_per_mm2` (both Abercrombie-corrected, `06a_roi_dataset.py:953`), using the same pairing rule. The interval comes from Poisson rates for both densities.

The sensitivity-1 assumption and its consequence (the correction is a lower-bound adjustment) are stated in the sheet header and the methods guide.

## 3. PCNA detection tuning (constraint on the plateau)

This applies to markers with `segment: own` only. 05b's `ls_tune.pick_prob_thresh` (`ls_tune.py:223`) keeps its current behaviour: it picks a point on a fixed grid, and keeps the model's default (0.479, recorded as None) unless another point is clearly flatter. New is a constraint: a candidate `prob_thresh` is **inadmissible** when, at that threshold, the false-detection density on paired no-primary controls is more than `fp_target` × the detection density on the stained sample.

- **Measuring control density.**
  - Run StarDist on the same kind of crops for control and stained sections, so like is compared with like: ROI central windows, as 05b already samples (`05b_tune_detection.py:150-153`). Controls use the **paired stained section's `section_range`**. Today 05c stretches each section to its own range, which on a control stretches noise to full scale and would overstate false detections.
  - Pairing uses the same tiers as section 2; with no pairable stained section, that control is skipped and the report says so.
  - Densities are in objects per mm² of crop area.
- **Choosing the threshold.**
  - If the current choice is inadmissible, 05b picks the lowest admissible grid point above it.
  - If none is admissible, it keeps the current choice, flags the marker, and says why.
  - With no usable control, the constraint code path is not entered at all. Behaviour and `detection.auto` are then byte-identical to today, and the report says the constraint was not applied.
- **05c detects at the same borrowed range (operator decision).** For `segment: own` markers, 05c detects **control sections** at their paired stained section's `section_range`, the same range 05b used. Stained sections are unchanged, and only control sections need re-detecting. 05c's `settings` gain the reagent-control keys it already tracks; no new ones are needed. A control with no pairable stained section is detected at its own range, and its rows say so.
- **Report.** New columns in `qc/detection_tuning.csv` and the 05b text: `control_density`, `stained_density`, `admissible`, `constraint_moved_from`.

## 4. Autofluorescence flag (flag only)

**New per-nucleus measurements, added by a backfill stage (operator decision).** A new stage `06l_nucleus_features.py` re-reads only the existing nuclei boxes and saved outlines. It never runs StarDist again. It writes `results/nucleus_features.csv`, keyed like `roi_nuclei.csv` (scene_uid, marker, roi_index, nucleus_id), following the repo's backfill pattern (06f, 06g, 06h, 06j). `roi_nuclei.csv` itself is unchanged. The stage resumes per section. The columns are:
- **shape:** `roundness` and `solidity` from the label image;
- **marker texture:** `marker_cv` (coefficient of variation inside the nucleus) and `marker_spots` (count of local maxima above the nucleus median + 3 × MAD);
- **second channel:** `other_marker_median`, under multiplex only, otherwise blank.

Area and DAPI already exist.

**Features at scoring time.** All are section-relative, so they are safe against batch differences:
- area and equivalent diameter, roundness, solidity;
- DAPI mean as a robust z within the section;
- marker_median in spreads above the section's discs;
- marker_p90 / marker_median, marker_cv, marker_spots;
- under multiplex, the other marker's level in spreads above its own discs.

**Model.** This is positive-unlabelled logistic regression, trained in a new stage `06k_af_score.py` after 06a and 06l.
- **Labelled class (operator decision: unstained first).**
  - When the study has at least the minimum number of above-cut nuclei on **unstained** controls, train on those alone. The flag is then named `af_suspect`, "autofluorescent".
  - Otherwise, train on **no-primary** controls, and name the flag `control_like` ("looks like a reagent-control false positive: autofluorescence or non-specific secondary").
  - Never pool the two kinds. The output records which kind it was trained on.
- **Unlabelled class:** above-cut nuclei on stained sections.
- **Class weights:** balanced.
- **Model:** standardised features, L2, fixed seed, written in numpy (Newton or IRLS). scikit-learn is **not** a dependency (`requirements.txt`), and none is added.
- **Output:** each stained above-cut nucleus gets `af_score` in [0, 1].

**Outputs.**
- `results/af_scores.csv`: one row per scored nucleus, with `af_score`, the flag (`af_suspect` or `control_like`, set at score ≥ 0.5) and `trained_on` (unstained / no_primary).
- The 06c sheet shows, per region × marker, the positive rate with and without `af_suspect` nuclei, beside the raw rate. Counts are never changed.
- `qc/af_review.png`: a strip of the 24 highest-scoring stained nuclei, for orientation, and 24 drawn at random (same seed) from the flagged nuclei (score ≥ 0.5) not among them, all of them when fewer are left, for the operator to look at (operator ruling 2026-11-22).

**Gate.** Training needs at least 200 above-cut control nuclei of the chosen kind per marker. Below that the stage writes nothing and says why.

**Validation (required before any censoring PR).** On the Task 8 study, the operator hand-labels the review strip. The flag must reach precision ≥ 0.8 at the 0.5 score, measured on the strip's random flagged half (operator ruling 2026-11-22). Until then it stays a flag.

## Validation plan (all four)

Everything runs on the operator's study with known controls:
- Report the k check per marker.
- Report whether the disc and control false-positive rates agree.
- Report corrected-rate coverage: how many regions have a pairable control.
- Report how far the PCNA constraint moves the threshold.
- Run the autofluorescence review strip.

The LS study has no controls, so on LS:
- sections 1, 2 and 4 must change nothing;
- section 3 must leave `prob_thresh` exactly as today, a byte-identical `detection.auto`.

## Non-goals

- Changing segmentation on DAPI.
- Unmixing or subtracting autofluorescence: there is no autofluorescence channel in the paired layout.
- Automatic censoring.
- Absolute thresholds taken from controls.
- Any use of isotype or pre-adsorbed controls in these calculations.

## Settled by the controller after the reviewer check

- `cut_k` is always a `{marker or "*": k}` map, edited through the per-marker table and the apply button, not a bare float.
- 05b compares like with like: ROI central windows for both stained and control sections.
- No new Python dependency: the logistic model is written in numpy.
- Stale tracking only works through stages that record their inputs (`made_from`). So `cut_k` goes on 06a and `build_results`, and 06i is given settings if it needs them to redraw. 06c and 06d follow from those.
- The `mad_k` note is corrected, and `mad_k` stays a separate key.

## Left to the builder

- Widget placement.
- Exact plot styling.
- Where the k-check plot appears in the app.
- The scikit-learn import location.
- File layout of the new stage, following existing stage patterns.
