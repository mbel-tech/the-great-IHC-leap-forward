# Detection overlays: design

Date: 2026-10-22. Status: waiting for the user's review.

## What the user asked for

"Within every scene where detection runs, a render of the nuclei found, with
the ones detected for signal coloured in. Labelled and named accurately, in a
subfolder of the outputs."

Answers given in the thread, one question at a time:

| Question | Answer |
|---|---|
| How much of the scene per picture | Both: a whole-scene picture and one crop per ROI |
| What "coloured in" means | Positives filled (see-through) and outlined; negatives outlined only |
| Picture underneath | The marker channel only, in grey |
| Nuclei found but not counted | Each kind its own colour, with a legend |
| Text on the picture | Identity + counts + legend (and a scale bar) |
| Folder layout | By marker, then animal |
| Where outlines come from | 05c saves them from now on; older sections are re-segmented once by the overlay step and checked against roi_nuclei.csv |
| When it runs | On request, from a button on Results (not in the run order) |

Standing rule from the project: never lose image detail or resolution.

## Facts this design rests on

- 05c segments only inside each ROI's bounding box and keeps a nucleus when
  its centroid is inside the ROI shape. It writes centroids and measurements to
  `results/roi_nuclei.csv`, not outlines.
- Positivity is not decided in 05c. 06a decides it per section:
  `cut = median + 3 * 1.4826 * MAD` of the background discs' `marker_median`,
  and a nucleus is positive when it is `censored` or its `marker_median > cut`.
  Nuclei flagged `artifact` or `off_tissue` are dropped before counting. The
  cut is published as `section_positivity_cut` in `roi_measurements.csv` and
  `positivity_cut` in `detector_specificity.csv`.
- 05c's StarDist settings are recorded in `results/detection_settings.json`.
- PCNA is segmented on its own (green) channel; its "marker channel" picture is
  therefore the same plane outlines were found on.

## The pieces

### 1. 05c saves outlines (small change to an existing stage)

For every box it segments, 05c saves the label image it already has to
`<out_root>/detection_outlines/<marker>/<scene_uid>.npz`: one `uint32` array
per box index (`roi03`, ...) at native pixels, plus the box origin
(`czi_x0`, `czi_y0`) and the StarDist settings used. Compressed, this is a few
MB per scene. Written before the section is logged as done, alongside the
nucleus rows, and removed when a section is redone with `--force`.
Measurements do not change.

### 2. New stage `06i_detection_overlays.py`

Inputs: `roi_boxes`, `roi_nuclei.csv`, `roi_measurements.csv` (the cut), the
outlines from step 1, the CZI.

For each scene that has nucleus rows, per marker:

1. **Outlines.** Load the saved npz. If there is none (sections measured
   before this change), re-run segmentation exactly as 05c does, with the
   settings recorded in `detection_settings.json`, and save the npz.
2. **Match check.** For every box, the nuclei kept inside the ROI must match
   `roi_nuclei.csv` one to one (same `nucleus_id`, centroid within 0.5 px).
   A scene that fails is not drawn and is listed in
   `detection_overlays/failures.csv` with the reason. A picture that disagrees
   with the numbers is worse than no picture.
3. **Classify** every labelled object, using 06a's own rule (imported, not
   restated):

   | Kind | Drawn as |
   |---|---|
   | Positive (counted, above the cut) | magenta see-through fill (40%) + magenta outline |
   | Censored (counted as positive: clipped pixels) | magenta fill + white outline |
   | Negative (counted, below the cut) | cyan outline |
   | Outside the ROI shape (in the box only) | yellow outline |
   | On an artifact | orange outline |
   | Off the tissue | blue outline |

   Outlines are 1 pixel, the boundary pixels of each label, as the existing
   `--qc` crops draw them. Background discs are drawn the same way, so the
   nuclei that set the cut can be checked too.
4. **Base image.** The marker channel, read from the CZI at native 0.65 µm
   pixels, in grey. Brightness is stretched for viewing only (1st to 99.8th
   percentile of the scene, the same stretch for the scene picture and all its
   crops so they are comparable). Nothing is downscaled.
5. **Pictures.**
   - Whole scene: the scene's full rectangle at native pixels, read in bands
     the way 01l/04o do so memory stays bounded. Every ROI and background disc
     shape is outlined in white with its number in a small tag just outside
     the shape. Outside detection boxes there are no nucleus marks.
   - One crop per ROI and per background disc: the box 05c segmented, at
     native pixels, with the ROI shape in white.
6. **Text** goes in a strip above the image, never over the image:
   - Line 1: marker, animal, slide, scene, (crop: ROI number, kind, region).
   - Line 2 (crop): n counted, n positive, % positive, section cut, n excluded
     by kind. Scene picture: totals over its ROIs, plus a small table of
     ROI number, region, n counted, n positive, % positive.
   - Legend of the six colours, and a scale bar (50 µm crops, 500 µm scenes).
   Counts come from the same rows 06a counts, so the numbers match the
   spreadsheet.

### 3. Folder and names

```
<out_root>/detection_overlays/
    index.csv                  one row per picture
    failures.csv               scenes not drawn, and why
    <marker>/<animal>/
        <marker>_<scene_uid>_scene.png
        <marker>_<scene_uid>_roi03_<region>.png
        <marker>_<scene_uid>_bg01.png
```

Example: `detection_overlays/pERK/LS22/pERK_LS22_s01a_sc06_roi03_Dm.png`.
Region names are made file-safe (spaces and slashes to `_`).

`index.csv` columns: path, marker, animal, slide, scene_uid, picture
(scene/roi/background), roi_index, region, n_counted, n_positive,
pct_positive, cut, n_outside, n_artifact, n_off_tissue, n_censored,
um_per_px, outlines (saved/re-segmented), drawn_at, and a fingerprint of the
inputs. A rerun redraws only scenes whose fingerprint changed; `--force`
redraws all.

PNG, lossless, RGB. Whole-scene files are large (tens to a few hundred MB);
that is the cost of native pixels.

### 4. App

- Results screen: a **Draw detection overlays** button with options:
  all fish or chosen fish, ROI crops on/off, whole-scene pictures on/off
  (both on by default), and an "Open pictures" button for the folder.
- The stage sits outside the run order (like the "Repair older outputs"
  group), so "Run all" never starts it. It needs 06a's results; if they are
  missing or older than `roi_nuclei.csv`, the button says so and does not run.
- A run that skipped scenes reports "N not drawn" in amber (workflow
  SHORTFALL).

## Out of scope

- 05c's existing `--qc` crops stay as they are.
- No change to how nuclei are detected, measured or called positive.
- No multiplex colocalisation overlays.

## Testing

- Unit tests on small synthetic label images: classification of every kind,
  fill/outline pixels and colours, the text strip never overlapping image
  pixels, image size equals the native box size (no resampling).
- Match check: a deliberate mismatch skips the scene and writes failures.csv.
- 05c writes the npz and removes it on `--force`; measurements unchanged
  (existing 05c tests still pass).
- Naming and folder layout, `index.csv` contents, fingerprint skip/redraw.
- Stage registration: present in the app, absent from the run order
  (tests/test_stages.py).
