# PCNA (AF488) quantification without a usable DAPI - design

## Context

On the LS PCNA scans, the DAPI plane (5 ms exposure, plane 0 of every AF488 CZI, `size_c = 2` in
`manifest/manifest_files.csv`) is **blank/noise at native 0.65 µm/px**. It is still good enough at
5.2 µm/px for tissue silhouettes, and all curation so far used it at that scale. So everything up to and
including 05a (rotations, exclusions, artifact masks, reformat geometry, `roi_boxes_AF488.csv`)
**stays valid and is not touched**.

What breaks is 05c. It segments nuclei with StarDist on the DAPI plane and measures the marker
inside them. On AF488 that would segment noise. The PCNA pass has never been run (`roi_nuclei.csv`
is all AF568), so no existing numbers change.

**Agreed readout (user):** PCNA+ **density** per ROI (Abercrombie-corrected, per mm²). No
fraction-of-all-cells. Without a nuclear counterstain there is no denominator, and the readout
should not pretend otherwise.

**Why segmenting PCNA on its own channel is acceptable when it was rejected for pERK**
(`docs/nuclei-detection-after-roi-curation.md` §4.5): PCNA is a nuclear antigen, so its objects
*are* nuclei (star-convex and nucleus-sized), which is what `2D_versatile_fluo` is trained on. pERK was
rejected mainly as "a poor silhouette". The other costs in §4.5 still apply and must be stated, not
hidden:
- (1) there is no denominator, so the fraction is withheld;
- (2) object size may track staining, so h needs checking;
- (3) the background discs stay a check only because positivity is still decided in 06a by
  median + 3·MAD of the background objects, not by the segmentation.

## Design

### 1. Declare it in config, not in code

The LS study is `paired` and has no channel table, so today every marker goes the nuclear route
(`05c_detect_rois.py:157` `_segments_own` → False when `_BY_NAME` is empty).

Add an optional per-marker block for paired studies:

```json
"acquisition": {
  "markers": ["AF568", "AF488"],
  "marker_segmentation": {
    "AF488": {"segment": "own", "backend": "stardist", "nucleus_shaped": true}
  }
}
```

- `scripts/ls_config.py` (~line 317, next to `acquisition.markers`): add a `Key("acquisition.marker_segmentation", "raw", …)` with a note. Validate it:
  - keys must be declared markers;
  - `segment` must be in `CH.SEGMENTS`;
  - the backend must be known;
  - reuse the existing rule in `ls_channels.py:598-615`, which rejects StarDist on objects that are not nucleus-shaped.
- `scripts/ls_channels.py`: add `paired_overrides(cfg) -> list[Channel]`. It builds `Channel(name, MARKER, segment=…, backend=…, nucleus_shaped=…)` from that block, so the existing `Channel`/`parse` defaults apply.
- `05c_detect_rois.py:137`: `_BY_NAME` also takes these paired overrides. All the dispatch helpers (`_segments_own`, `segment_plane_for`, `backend_for`, `nucleus_shaped_for`) then work unchanged. `plane_index_for` already falls back to `channels.marker_index` (= 1 = AF488).
- `config.json` / `config.example.json`: add the block. The example gets an `_note` that explains when to use it.

pERK (AF568) is untouched: there is no entry for it, so it keeps the nuclear route.

### 2. Normalise per section, not per crop (05c + `ls_segment`)

`ls_segment.stardist_labels` (`ls_segment.py:243`) runs `normalize(a, 1, 99.8)` on each crop. On DAPI
that is harmless. On a marker channel it is a bias: an ROI that contains a bright proliferative zone
stretches its own range, so dim PCNA+ nuclei fall below StarDist's view. A background disc, on the
other hand, gets its noise stretched to full range. Sensitivity would then depend on the ROI's own
content, which is region-correlated, and periventricular zones are exactly where PCNA lives.

- `stardist_labels(image, model, n_tiles=None, norm=None)`: if `norm=(lo, hi)` is given, apply
  `clip((a-lo)/(hi-lo))`. Otherwise keep the current percentile call, so the nuclear route is
  byte-identical.
- In 05c, only for markers segmented on their own channel: before the per-ROI loop, read the
  section's own-channel crops (all ROI and background boxes), compute p1/p99.8 over their union,
  and pass that as `norm`. Thread it through `SG.segment(...)` as a pass-through kwarg.
- Record it in a **sidecar** `results/roi_normalisation.csv` (`scene_uid, marker, segmented_on,
  norm_lo, norm_hi, n_crops`), one row per section and marker. *Changed from the first draft,
  which added `norm_lo`/`norm_hi` columns to `roi_nuclei.csv`: that file is ~900k rows shared with
  06g/06h, and widening its header means another backfill migration for no benefit over a table
  whose rows are per section.*

### 3. 05c output for own-segmented AF488

The dispatch already exists (`05c:1000` `seg_img = mark if _segments_own(marker) else dapi`).
Each row records `segmented_on=AF488`, `backend=stardist`, `nucleus_shaped=1`. `dapi_mean` is still
written, because it is a real measurement of plane 0, and 06a never uses it for AF488. The `--qc`
overlays already draw the marker plane under `own` (`05c:704`).

Run: `05c_detect_rois.py --marker AF488`. Its scoped `drop_rows` and resume logic already keep the
895k pERK rows safe.

### 4. 06a: density as the readout, fraction withheld (`06a_roi_dataset.py`)

When a marker's rows were segmented on the marker itself (`segmented_on == marker`):

- **`n_positive` / `positive_density_per_mm2`**: these stay as they are, and they are the primary
  readout. The cut is still the section's background-object `median + 3·1.4826·MAD` of
  `marker_median`, as for pERK.
- **`frac_positive`, `cell_density_per_mm2`**: write these blank. Add a new
  `denominator_withheld_reason` column, carried on every row and blank for pERK (follow the
  `abercrombie_withheld_reason` pattern at `06a:430`, since the header is taken from the first
  row's keys). Reason text: "objects segmented on AF488 itself; there is no nuclear counterstain, so
  there is no count of all cells". `n_nuclei` keeps its value but means "objects StarDist found on
  green".
- **h for Abercrombie**: for these markers, compute `diam_by` / `h_by_marker` (`06a:~350-372`)
  over **positive** ROI objects only (above their section's cut), because those are the objects
  being counted. Cuts are already computed before this point (`cuts`); check the order and move it
  if needed.
- **`detector_specificity.csv`**: for these markers, report background `positive_density_per_mm2`
  alongside the ROI value. The background *fraction* positive is withheld for the same reason
  (`06a:~456-473`).
- **06c/06d**: check that blank `frac_positive` and `cell_density_per_mm2` flow through the
  spreadsheets and R plots (`analysis/*.R`) without errors and without being read as zero.

### 5. Docs + log

- `docs/nuclei-detection-after-roi-curation.md`: add a new §4.8 "PCNA: segmenting the marker
  because there is no counterstain". It covers:
  - why §4.5's rule is relaxed for this one nuclear marker;
  - what is lost (the fraction);
  - per-section normalisation;
  - h taken from positive objects;
  - the pilot's numbers (step 6).

  Also update the §2.4 config table and the §5 column table (`norm_lo`, `norm_hi`).
- `docs/config-reference.md`: add `acquisition.marker_segmentation`.
- `LOGS.md`: dated entry covering the finding (AF488's DAPI plane is noise at native resolution), the
  decision, and the readout.
- Spec: `docs/superpowers/specs/2026-10-14-pcna-green-only-detection-design.md` (this design),
  committed first, per the existing specs convention.

### 6. Pilot before the full run (operator, on the real data)

1. `05c --marker AF488 --limit 3 --qc`, then inspect `qc/roi_detections/` overlays for
   missed and false nuclei, especially in periventricular ROIs and in background discs.
2. Hand-count PCNA+ nuclei in about 6 ROIs (a mix of regions plus 2 background discs) and
   compare them with 06a's `n_positive`.
3. Size check, in the spirit of §4.5 point 2: report the median `equiv_diam_um` of positive objects
   (expect about 7–9 µm, against pERK's DAPI 8.3 µm) and the Pearson r between `equiv_diam_um`
   and `marker_median`. If r is clearly greater than 0, h is staining-driven, and that goes in the doc
   as a stated limitation.

Only after this goes well: the full `05c --marker AF488` run.

## Tests (`bash tests/run.sh`; Python suites run directly)

- `tests/test_detect_backends.py`: a new **paired + `marker_segmentation`** case. Use the fake
  reader and fake model from that file and check:
  - AF488 segments the marker plane;
  - rows carry `segmented_on=AF488` and `nucleus_shaped=1`;
  - AF568 in the same study still segments plane 0 and its row is byte-identical to before;
  - `norm` is passed through and `norm_lo` / `norm_hi` are written.
- `tests/test_ls_segment.py`: `stardist_labels(norm=…)` uses the given range (with a fake model
  that captures its input), and the default path is unchanged.
- `tests/test_ls_config.py` / `test_ls_channels.py`: validation of the override block (unknown
  marker, bad segment, StarDist with `nucleus_shaped: false` are each rejected), and the paired
  overrides are parsed.
- `tests/test_abercrombie_gate.py` (or a new 06a test): an own-segmented marker gets blank
  `frac_positive` and `cell_density_per_mm2`, a filled `denominator_withheld_reason`, and h taken
  from positive objects only. pERK rows are unchanged.
- `tests/test_ls_regression.py` and the existing suites must stay green, so the live pERK path does
  not move.

## Verification

1. `bash tests/run.sh`: all suites pass.
2. A synthetic end to end run through the `_fixture.temp_study` paired study with the override:
   run 05c and then 06a, and check that the AF488 rows in `roi_measurements.csv` have blank fraction
   columns and a filled positive density.
3. Real data (operator, Windows `work/appenv`): the step 6 pilot, whose numbers go into §4.8.
