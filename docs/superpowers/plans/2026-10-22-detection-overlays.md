# Detection Overlays Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Draw, on request, native-resolution pictures of every detected nucleus (whole scene and one crop per ROI/background disc), with signal-positive nuclei filled in, labelled, in `<out_root>/detection_overlays/<marker>/<animal>/`.

**Architecture:** A pure drawing/naming module (`scripts/ls_overlay.py`) holds the colour table, the positivity rule, the boundary drawing, the text strip and a streaming PNG writer. 05c saves each box's label image (`detection_outlines/<marker>/<uid>.npz`) through a per-box segmentation function that the new stage `06i_detection_overlays.py` reuses to re-segment sections measured before outlines were saved. 06i checks the outlines against `roi_nuclei.csv`, classifies with 06a's rule and the cut from `roi_measurements.csv`, and writes pictures, `index.csv` and `failures.csv`. The app shows it on Results as a view-only stage with its own Run button.

**Tech Stack:** Python 3, numpy, Pillow (>=10.1 for `ImageFont.load_default(size=)`), zlib, pylibCZIrw via `czi_read`, PySide6 app (`app/stages.py`, `app/workflow.py`). Tests are plain scripts run as `python tests/test_x.py` using the `chk()` pattern and `tests/_fixture.py` (`use_temp_study`, `load_stage`).

**Spec:** `docs/superpowers/specs/2026-10-22-detection-overlays-design.md`

## Global Constraints

- Native pixels only (0.65 µm/px, `CONFIG["pixel_size_um"]`): every picture is the CZI's own pixels, never resampled. A crop's image area is exactly `czi_w x czi_h`; a scene's is the scene rectangle.
- Base image: the marker channel (`plane_index_for(marker)` in 05c), grey.
- Brightness: one stretch per (scene, marker), 1st to 99.8th percentile, used for the scene picture and all its crops. Refinement of the spec: the percentiles are taken over the pixels of that scene's detection boxes (ROIs and background discs), not the whole rectangle, so crops-only runs need no full-scene read and the glass/PAP-pen ring cannot set the range.
- Colours (RGB): positive fill `(255, 0, 255)` at alpha 0.4 + outline `(255, 0, 255)`; censored fill as positive + outline `(255, 255, 255)`; negative outline `(0, 255, 255)`; outside ROI `(255, 255, 0)`; artifact `(255, 128, 0)`; off tissue `(64, 128, 255)`. ROI/disc shape outline `(255, 255, 255)`.
- Outlines are 1 px: label pixels whose 4-neighbour differs (as 05c `write_overlay` does).
- Positive = `censored or marker_median > cut`; artifact and off_tissue rows are not counted; artifact wins over off_tissue when both are set (06a checks artifact first).
- Text only in a strip above the image; image pixels are never covered (except the scene picture's small ROI-number tags just outside each shape).
- Layout: `<out_root>/detection_overlays/index.csv`, `failures.csv`, `<marker>/<animal>/<marker>_<uid>_scene.png`, `<marker>_<uid>_roi<NN>_<region>.png`, `<marker>_<uid>_bg<NN>.png`; region made file-safe (space and `/` to `_`). `NN` is 05c's `roi_index`, two digits.
- Outlines: `<out_root>/detection_outlines/<marker>/<uid>.npz`, keys `roi<NN>` (uint32 labels), `roi<NN>_origin` (int64 `[czi_x0, czi_y0]`), `settings` (JSON string of `stardist_settings(marker)`).
- PNG, lossless, RGB 8-bit.
- Not in the run order: never planned by Run all remaining.

## Review Focus

- A scene with an ROI that had zero nuclei (05c `continue`s and writes no rows, no label image): the crop is still drawn, with "0 counted", and the match check passes.
- A section with no cut (fewer than 5 background nuclei, 06a skips it): pictures are drawn with every counted nucleus outlined as "not called" (negative colour) and the strip says "no cut: too few background nuclei"; never silently all-negative without saying so.
- `roi_measurements.csv` older than `roi_nuclei.csv` (06a not re-run after 05c): the stage refuses with a message naming 06a, draws nothing.
- Re-segmentation that does not reproduce `roi_nuclei.csv` (different StarDist version or settings): scene skipped, reason in `failures.csv`, counted in the "N not drawn:" line.
- A very large scene (e.g. 25,000 x 18,000 px): memory stays at one band plus the box labels, because the scene PNG is written row-band by row-band.

---

### Task 1: Drawing core and positivity rule (`ls_overlay.py`)

**Files:**
- Create: `scripts/ls_overlay.py`
- Modify: `scripts/06a_roi_dataset.py` (the two `c or v > cut` tests, ~lines 610 and 661)
- Test: `tests/test_overlay_draw.py`

**Interfaces:**
- Produces:
  - `KINDS = ("positive", "censored", "negative", "outside", "artifact", "off_tissue")`, `COLOURS: dict[str, tuple[int,int,int]]`, `FILL_ALPHA = 0.4`, `SHAPE_COLOUR = (255,255,255)`
  - `is_positive(median: float, censored: bool, cut: float) -> bool`
  - `kind_of(row: dict | None, cut: float | None) -> str` — `row` is a roi_nuclei.csv row (strings); `None` means outside the ROI. With `cut is None`, a counted nucleus is `"negative"`.
  - `stretch(sample: np.ndarray) -> tuple[float, float]` — (p1, p99.8).
  - `grey(plane: np.ndarray, lo: float, hi: float) -> np.ndarray` (uint8 HxW)
  - `edges(labels: np.ndarray) -> np.ndarray` (bool)
  - `paint(rgb: np.ndarray, labels: np.ndarray, kinds: dict[int, str]) -> None` — in place, fills then outlines.
  - `shape_mask_outline(h, w, inside: Callable[[np.ndarray, np.ndarray], np.ndarray]) -> np.ndarray` (bool, 1-px edge of the ROI shape over pixel centres).

- [ ] **Step 1: Write the failing tests** in `tests/test_overlay_draw.py`:
  - `is_positive(10, False, 9.9)` True; `(9.9, False, 9.9)` False; `(0, True, 1e9)` True.
  - `kind_of` on rows: artifact=1 and off_tissue=1 → `"artifact"`; off_tissue=1 → `"off_tissue"`; censored=1 → `"censored"`; median 50 cut 40 → `"positive"`; median 30 → `"negative"`; `None` → `"outside"`; cut None, counted → `"negative"`.
  - `paint` on a 20x20 labels image with label 1 a 6x6 square (positive) and label 2 a 6x6 square (negative) over grey 100: label 1 edge pixels == `(255,0,255)`, its interior == `round(0.6*100 + 0.4*(255,0,255))`; label 2 edge == `(0,255,255)`, interior unchanged `(100,100,100)`; background unchanged; output shape (20,20,3) uint8.
  - `grey` maps lo→0, hi→255, clips outside; `stretch` of `np.arange(1001)` returns `(10.0, 998.0)`.
  - 06a still gives the same `n_positive` on its existing test (`python tests/test_abercrombie_gate.py` unchanged).
- [ ] **Step 2: Run** `python tests/test_overlay_draw.py` — Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `scripts/ls_overlay.py` with the signatures above (no skimage import; edges by array shifts as in 05c `write_overlay`). In 06a replace the two inline tests with `LO.is_positive(v, c, cut)`.
- [ ] **Step 4: Run** `python tests/test_overlay_draw.py && python tests/test_abercrombie_gate.py` — Expected: all PASS, exit 0.
- [ ] **Step 5: Commit** `git commit -m "ls_overlay: colours, positivity rule, nucleus drawing"`

### Task 2: Text strip, names and streaming PNG writer

**Files:**
- Modify: `scripts/ls_overlay.py`, `requirements.txt` (`pillow>=10.1`)
- Test: `tests/test_overlay_draw.py` (extend)

**Interfaces:**
- Produces:
  - `strip(width: int, lines: list[str], legend: bool, um_px: float, bar_um: int) -> np.ndarray` — uint8 RGB, black background, white text; font `ImageFont.load_default(size=max(14, width // 90))`; legend = a swatch + word per kind ("positive", "censored (clipped)", "negative", "outside ROI", "artifact", "off tissue"); scale bar `round(bar_um / um_px)` px long with "<bar_um> µm".
  - `overlay_dir(out_root: str, marker: str, animal: str) -> str`
  - `overlay_name(marker: str, uid: str, picture: str, roi_index: int | None = None, region: str = "") -> str` — `picture` in `{"scene","roi","background"}`.
  - `class PngRows: __init__(path, width, height)`, `write(rows: np.ndarray)` (k x width x 3 uint8), `close()` — writes IHDR (8-bit RGB), IDAT through one `zlib.compressobj(6)`, filter byte 0 per row, IEND; refuses (ValueError) if total rows written != height on close; writes to `path + ".part"` and renames on close.

- [ ] **Step 1: Write the failing tests:**
  - `overlay_name("pERK","LS22_s01a_sc06","roi",3,"Dm")` == `"pERK_LS22_s01a_sc06_roi03_Dm.png"`; region `"OB/Tel x"` → `"..._roi03_OB_Tel_x.png"`; background 1 → `"pERK_LS22_s01a_sc06_bg01.png"`; scene → `"pERK_LS22_s01a_sc06_scene.png"`.
  - `overlay_dir("/o","pERK","LS22")` == `os.path.join("/o","detection_overlays","pERK","LS22")`.
  - `strip(800, ["a","b"], True, 0.65, 50)`: width 800, height > 0, contains a run of exactly `round(50/0.65)` = 77 white pixels in one row (scale bar), contains each kind colour.
  - `PngRows` writing a random 37x53 RGB image in bands of 10 rows reads back identical with `PIL.Image.open`; closing after too few rows raises ValueError and leaves no `.png`.
- [ ] **Step 2: Run** `python tests/test_overlay_draw.py` — Expected: FAIL on the new names.
- [ ] **Step 3: Implement** the four functions/class above.
- [ ] **Step 4: Run** `python tests/test_overlay_draw.py` — Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "ls_overlay: text strip, file names, streaming PNG"`

### Task 3: 05c saves outlines through a reusable per-box segmentation

**Files:**
- Modify: `scripts/05c_detect_rois.py` (the per-box block inside the section loop, ~lines 1684-1735; the `--force` drop path; module docstring "Run:" block)
- Test: `tests/test_detect_outlines.py` (driver copied from `tests/test_detect_own_marker.py`'s `FakeCzi`/`FakeModel`/`drive`)

**Interfaces:**
- Produces (in 05c):
  - `OUTLINE_DIR = os.path.join(OUT_ROOT, "detection_outlines")`
  - `segment_box(doc, b: dict, scene: int, model, min_area_px: int, section_norm: dict, partners=()) -> dict | None` — returns `{"labels", "seg_img", "dapi", "mark", "planes", "report", "seg_on", "backend", "shaped"}` exactly as the loop computes them today, or None where the loop `continue`s before segmenting (box < 8 px, missing shared norm, plane shape mismatch). The loop calls it; behaviour and rows unchanged.
  - `outline_path(marker: str, uid: str) -> str`
  - `save_outlines(marker: str, uid: str, boxes: dict[int, tuple[np.ndarray, int, int]]) -> None` — `np.savez_compressed` to `.part` then `os.replace`; keys as in Global Constraints; labels with no object are still saved (all zeros) so 06i can tell "measured, empty" from "not measured".
- Write order per section: outlines npz, then geometry rows, nucleus rows, fsync, done log. `--force` deletes the npz of each section it redoes before redoing it.

- [ ] **Step 1: Write the failing tests:**
  - After `drive(...)` on one section with two boxes: `outline_path(marker, uid)` exists; keys `roi01`, `roi02`, `roi01_origin`, `roi02_origin`, `settings`; for each roi, `set(np.unique(labels)) - {0}` ⊇ the `nucleus_id`s in `roi_nuclei.csv` for that `roi_index`, and each row's `(czi_x, czi_y)` is within 0.5 px of the centroid of its label + origin.
  - Rows from this run equal the rows the existing `test_detect_own_marker.py` expects (run it unchanged: PASS).
  - `--force` on the same section rewrites the npz (mtime changes) and leaves another section's npz untouched.
- [ ] **Step 2: Run** `python tests/test_detect_outlines.py` — Expected: FAIL (no npz).
- [ ] **Step 3: Implement** `segment_box`, `outline_path`, `save_outlines`; collect `(labels, x0, y0)` per `bi` during the box loop (also for boxes whose labels are empty); call `save_outlines` before `gw.writerows`.
- [ ] **Step 4: Run** `python tests/test_detect_outlines.py && python tests/test_detect_own_marker.py && python tests/test_detect_resume.py && python tests/test_detect_backends.py` — Expected: all PASS.
- [ ] **Step 5: Commit** `git commit -m "05c: save each box's nucleus outlines for the overlays"`

### Task 4: Stage 06i, ROI and background crops

**Files:**
- Create: `scripts/06i_detection_overlays.py`
- Test: `tests/test_detection_overlays.py` (same fake CZI/model; builds outputs by running 05c then 06a on the fixture, then 06i)

**Interfaces:**
- Consumes: Task 1/2 `ls_overlay` API; Task 3 `segment_box`, `outline_path`, `save_outlines`, `stardist_settings`, `load_model`, `plane_index_for`, plus 05c's `inside` construction (move it to `05c.roi_inside(b) -> Callable[[float, float], bool]` in grid coordinates and use it in both places).
- Produces:
  - CLI: `--marker` (default as 05c), `--animal A [A ...]`, `--no-scenes`, `--no-crops`, `--limit N` (scenes), `--force`.
  - `OUT_DIR = <out_root>/detection_overlays`; `INDEX_CSV`, `FAILURES_CSV` there.
  - `INDEX_KEYS = ["path","marker","animal","slide","scene_uid","picture","roi_index","region","n_counted","n_positive","pct_positive","cut","n_outside","n_artifact","n_off_tissue","n_censored","um_per_px","outlines","drawn_at","fingerprint"]`
  - `load_outlines(marker, uid, boxes, doc, model) -> tuple[dict[int, tuple[labels, x0, y0]], str]` — second item `"saved"` or `"re-segmented"`; re-segmentation uses `segment_box` with the settings in `results/detection_settings.json` and then `save_outlines`.
  - `match(outlines, rows_by_roi, inside_by_roi, Minv) -> str | None` — None when, for every roi, the set of labels whose centroid maps inside the ROI equals the set of `nucleus_id` rows and each centroid is within 0.5 px; otherwise the reason (first mismatch, e.g. `"roi03: 212 nuclei in outlines, 211 in roi_nuclei.csv"`).
  - `fingerprint(uid, marker) -> str` — sha1 of that scene's rows in roi_nuclei.csv, its cut, its box rows, and the npz bytes.
- Behaviour: refuse (exit 2, message names 06a) when `roi_measurements.csv` is missing or older than `roi_nuclei.csv`. Per scene: base range from `ls_overlay.stretch` over all its box pixels; each box drawn with `paint` + the shape outline in `SHAPE_COLOUR`; strip line 1 `"<marker> | fish <animal> | slide <slide> | scene <uid> | ROI <NN> <roi_kind> <region>"`, line 2 `"<n_counted> counted, <n_positive> positive (<pct>%), cut <cut> | not counted: <n_outside> outside ROI, <n_artifact> artifact, <n_off> off tissue | <n_censored> censored"`, legend, 50 µm bar; strip stacked above the crop. A scene that fails `match` writes one `failures.csv` row and no pictures. Prints `"<N> not drawn: see <failures.csv>"` when N > 0. Skips a scene whose fingerprint equals its index row unless `--force`. `index.csv` rewritten atomically, keyed by path.

- [ ] **Step 1: Write the failing tests:**
  - Crop PNG for roi01 exists at `overlay_dir(...)/overlay_name(...)`; its height == strip height + `czi_h`, width == `czi_w`; the image area below the strip, where no label is, equals `grey(mark, lo, hi)` replicated to RGB (no resampling, no blanking).
  - A positive nucleus's interior pixels match the 0.4 magenta blend; a negative's edge is cyan; a nucleus in the box but outside the disc has a yellow edge; a row set `artifact=1` in the fixture has an orange edge.
  - `index.csv` row for the crop: `n_counted`, `n_positive`, `cut` equal the same ROI's `n_nuclei`, `n_positive`, `section_positivity_cut` in `roi_measurements.csv`.
  - Delete the npz, rerun with `--force`: outlines column `"re-segmented"`, identical pixels.
  - Tamper one row's `nucleus_id` in roi_nuclei.csv (and re-run 06a): scene absent from pictures, one `failures.csv` row naming the roi, stdout contains `"1 not drawn:"`.
  - Second run without `--force` draws nothing (mtimes unchanged).
  - Review Focus: an ROI with zero nuclei gets a crop with `"0 counted"`; a section with < 5 background nuclei draws with strip text `"no cut: too few background nuclei"`; `roi_measurements.csv` older than `roi_nuclei.csv` → exit 2 and no output folder.
- [ ] **Step 2: Run** `python tests/test_detection_overlays.py` — Expected: FAIL (script missing).
- [ ] **Step 3: Implement** `06i_detection_overlays.py` (crop path; `--no-scenes` path complete).
- [ ] **Step 4: Run** `python tests/test_detection_overlays.py` — Expected: PASS for all crop tests.
- [ ] **Step 5: Commit** `git commit -m "06i: detection overlay crops per ROI and background disc"`

### Task 5: Stage 06i, whole-scene picture

**Files:**
- Modify: `scripts/06i_detection_overlays.py`
- Test: `tests/test_detection_overlays.py` (extend)

**Interfaces:**
- Consumes: `ls_overlay.PngRows`, `strip`, `paint`; 04o's banded reading pattern (read `BAND_ROWS = 1024` native rows of the marker plane at a time through `czi_read`, scene named).
- Produces: `draw_scene(doc, g, marker, boxes, outlines, kinds_by_roi, lo, hi, path) -> None` — writes the strip rows first (strip line 2 = scene totals; then one line per ROI: `"ROI <NN> <region>: <n_counted> counted, <n_positive> positive (<pct>%)"`), then for each band: `grey` the band, `paint` the part of every box that intersects it, draw shape outlines, draw each box's number tag (white text on black, placed just above-left of the box, clipped to the image), `PngRows.write`. Scale bar 500 µm.

- [ ] **Step 1: Write the failing tests:**
  - Scene PNG height == strip height + scene rect height, width == scene width.
  - With band size patched to 7 rows, the scene image equals the one drawn with band size 10,000 (pixel-identical).
  - The crop region inside the scene picture equals the crop picture's image area (same stretch, same colours) except the tag pixels.
  - `--no-scenes` writes no `_scene.png`; `--no-crops` writes only `_scene.png`; `--animal` limits to that fish.
- [ ] **Step 2: Run** `python tests/test_detection_overlays.py` — Expected: FAIL on scene tests.
- [ ] **Step 3: Implement** `draw_scene` and wire it in.
- [ ] **Step 4: Run** `python tests/test_detection_overlays.py` — Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "06i: whole-scene detection overlay, written band by band"`

### Task 6: App: Results button, options, folder; docs

**Files:**
- Modify: `app/stages.py` (new `Stage("detection_overlays", "06i  Detection overlays, for checking", "Beyond blinding", script="06i_detection_overlays.py", reader="pylibCZIrw", outputs=["detection_overlays/index.csv"], needs=["roi_dataset"], made_from=["detect", "roi_dataset"], blurb=...)`)
- Modify: `app/workflow.py` — add `"detection_overlays"` to `VIEW_ONLY`; add a Results part `("Check detection", "Pictures of every nucleus found, positives filled in.", ["detection_overlays"])`; plain-words entry; `STAGE_OPTIONS["detection_overlays"] = [_force("pictures"), _limit("scenes"), Opt("--no-scenes", "flag", "Skip whole-scene pictures", ...), Opt("--no-crops", "flag", "Skip ROI crops", ...), Opt("--animal", "text", "Only these fish", ...)]` (use the existing Opt kinds; check `workflow.Opt` for the flag/text kind names); `SHORTFALL["detection_overlays"] = (re.compile(r"^\s*(\d+) not drawn:", re.M), "scene", "not drawn")`; `FOLDERS["detection_overlays"] = ("Open pictures", lambda cfg: os.path.join(cfg["out_root"], "detection_overlays"))`.
- Modify: `docs/pipeline-guide.md` (short section "Detection overlays (06i)": what each colour means, folder layout, that it needs 06a, on request only), `README.md` stage list line.
- Test: `tests/test_stages.py` (extend), `tests/test_workflow*.py` if a screen-parts test exists.

- [ ] **Step 1: Write the failing tests:** `"detection_overlays"` in `BY_ID`; in `workflow.BY_HAND`; not in `run_plan("results", study)`; listed in the Results screen's stage ids; `FOLDERS` path ends with `detection_overlays`; SHORTFALL regex matches `"3 not drawn: see x"` → 3; the existing run-order test still passes.
- [ ] **Step 2: Run** `python tests/test_stages.py` — Expected: FAIL.
- [ ] **Step 3: Implement** the entries and docs.
- [ ] **Step 4: Run** `bash tests/run.sh` — Expected: every suite PASS (record the summary line).
- [ ] **Step 5: Commit** `git commit -m "App: Draw detection overlays on Results; guide section"`
