# Full metadata detector — plan

Ask (user, 2026-10-15): "build a detector that reads all metadata possible and
reports it in the config file and uses each one to optimise configuration".

Builds on app/autodetect.py (branch claude/simplify-setup-njkyc5, PR #43), which
already reads CZI headers (scripts/czi_meta.py: read_metadata, summarise,
channel_info) and fills pixel size, overview resolution, naming, layout, markers
and channel indices.

## Global constraints

- Headers only: never decode pixels. A folder of 400 slides must stay a few seconds.
- Never overwrite a value a study already set: `fill` fills only absent/empty
  keys; disagreements go through `compare` for a person to judge.
- Never lose image detail: no setting may be made coarser than it is today for
  the LS data (0.65 µm/px: overview 5.2, curation and scene pictures 2.6).
- Do not change `czi_meta.summarise`'s output (other stages pin it); add new
  functions instead.
- Tests are plain scripts with chk() like tests/test_autodetect.py; run with
  `QT_QPA_PLATFORM=offscreen LS_CONFIG_STRICT=1 python tests/<file>.py`.
  tests/test_autodetect.py, tests/test_ls_config.py, tests/test_config_example.py,
  tests/test_project.py, tests/test_workflow.py must keep passing.
- If config spec docs change, regenerate config.example.json and
  docs/config-reference.md with the repo's own generator (see ls_config.py
  write_example / docs splice; tests/test_config_example.py says how).
- User-facing words are plain English, no regexes or jargon in messages.
- Commit on the current branch; do not push.

## Task 1: Every header field, flattened and summarised across files

New module `scripts/czi_report.py` (no Qt).

- `flatten(root, max_fields=5000, max_text=200) -> dict[str, str]`: every
  element's non-empty text and every attribute of the CZI metadata XML, as
  path -> value. Path is the tag chain from below `ImageDocument/Metadata`
  joined with "/", attributes as `.../Tag@Attr`. A repeated sibling is keyed by
  its `Id`, else `Name`, else `Index` attribute as `Tag[Id]`, else by position
  `Tag[2]` (first one unnumbered only when it is the sole sibling). Text longer
  than max_text is cut to max_text and marked with a trailing "…". Stop after
  max_fields (record `"_truncated": "yes"`).
- `combine(flat_by_file: dict[name, dict]) -> dict[path, {"files": n,
  "values": [[value, count], ...] (most common first, at most 5),
  "distinct": k}]`: what each field says across the study.
- `constant(combined)` and `varying(combined)` helpers (a field is varying when
  distinct > 1; ignore fields whose name says they differ by nature: anything
  under a path containing "AcquisitionDateAndTime", "CreationDate", "Guid",
  "Id@" ... keep the ignore list a module tuple `NATURALLY_DIFFERENT`, and
  include scene positions/`CenterPosition`, `Scenes`, `SizeS`, `SizeX`, `SizeY`,
  `Title`, `FileName`, `Name` of the document).
- Tests: tests/test_czi_report.py with synthetic XML (reuse the write_czi idea
  from tests/test_autodetect.py or call flatten on ElementTree roots directly):
  attributes, repeated siblings by Id and by position, text cut, max_fields,
  combine counts, varying vs constant with an ignored field.

## Task 2: The detector reads it all and uses each field

In app/autodetect.py.

- `read_headers` also returns each file's `czi_report.flatten(root)` (same single
  XML parse; keep returning the 3-tuples to existing callers by storing the flat
  dicts on the Detection or returning a 4th element and updating callers in this
  file only).
- `Detection.report` (dict, JSON-safe) built by a new `_report(d, headers)`:
  - `"read"`: files read / found, date range of AcquisitionDateAndTime.
  - `"instrument"`: microscope/system name, objective (name, magnification, NA,
    immersion), camera, ZEN/application version, pixel type, bit depth,
    online stitching, shading reference mode: each the most common value, with
    "(varies: …)" when files differ. Take them from summarise() where it has
    them and from the flat fields otherwise (search by tag name, e.g.
    `Objective/Immersion`, `Application/Version`, `Microscope@Name` or
    `Microscopes/Microscope/System`).
  - `"channels"`: per plane index: name, dye, excitation/emission nm, band,
    exposure ms min–max across files, bit depth.
  - `"scenes"`: scenes per file min/median/max, scene width/height µm range.
  - `"optics"`: lateral resolution per channel = 0.61 × emission / NA (µm,
    rounded 3) when NA and emission are known, and whether the pixel size meets
    Nyquist (pixel ≤ resolution / 2).
  - `"uses"`: list of `{"field", "value", "use"}` where `use` is plain words:
    "sets pixel_size_um", "checks: exposures must match across files",
    "recorded only". Every field in instrument/channels/scenes/optics appears
    once, so the report says what each one did.
  - `"varying"`: czi_report.varying() of the combined flat fields, first 50
    paths with their values.
  - `"fields"`: the full combine() result (goes to the separate file in Task 3,
    not the study file).
- New settings it sets (added to `d.values` with a `d.why` line, filled only
  when absent like the rest):
  - `curation_render.um_per_px` and `scene_render.um_per_px` = pixel size × k
    where k starts at 4, halves while the result > 2.6 and k > 1, doubles while
    the result < 1.3; rounded to 4 decimals. (0.65 -> 2.6, 0.325 -> 1.3,
    1.3 -> 2.6, 0.1625 -> 1.3, 5.2 -> 5.2.) Whole native-pixel steps keep the
    CZI pyramid reads exact.
  - `display.colours`: marker -> BAND_COLOUR of its band (paired: the
    acquisition.markers names; multiplex: the marker rows of the channel table);
    `display.nuclear_colour` from the counterstain's band. Skip a marker whose
    band is unknown. Add both keys as a GROUP in GROUPS so a study's own
    colours are never partly filled.
- New checks (appended to `d.problems`, plain words):
  - the same channel's exposure differs across files by more than 1 %:
    "AF568 was exposed for 120–300 ms across files; signal is not directly
    comparable between those files."
  - bit depth, pixel type or objective differ across files.
  - pixel size coarser than Nyquist for a channel (informational: "AF488 is
    sampled at 0.65 µm, coarser than half its 0.27 µm optical resolution").
    Only when NA is known.
- `lines()` adds one line naming the instrument (objective and camera) when known.
- Tests in tests/test_autodetect.py: extend `xml()` (keep its current callers
  working) so it can emit ExposureTime, Objective NominalMagnification/LensNA/
  Immersion, ComponentBitCount; check report sections, the render resolution
  rule for 0.65 and 0.325, colours (AF568 red, AF488 green, DAPI blue), the
  exposure-mismatch problem, fill() not touching set colours, and that the
  existing assertions still hold.

## Task 3: The report goes in the study file

- Study file (`project` block, scripts/ls_project.py): `Project.note_slides(report)`
  stores the report minus `"fields"` under `project.slides`, plus
  `"read_at"` and `"full_report"`: the relative file name below. Evidence only,
  nothing reads it back as a setting. ls_config `_check_project`: `slides`, when
  present, must be a block (dict). Update the `project` key's doc text to name
  `slides`; regenerate config.example.json / docs/config-reference.md if the
  doc text is spliced there.
- Full table: `autodetect.write_report(out_root, detection)` writes
  `<out_root>/slide_metadata.json` (indent 1, sorted keys, utf-8) holding the
  whole report including `"fields"`; returns the path. Writing never raises into
  the UI: return None on OSError.
- Call sites: `new_project` (note_slides on the new project; the file is written
  by NewStudyDialog after the output folder exists: add the call in
  app/new_study.py `_create` after save_as), and SetupScreen.detect_from_slides
  in app/screens.py (note_slides + write_report when the project is writable,
  then the existing fill/compare).
- Tests: tests/test_project.py or tests/test_autodetect.py: note_slides stores
  it, validate() passes, a non-dict `slides` is an error, write_report writes a
  readable JSON with `fields`, the study file has no `fields`.

## Task 4: Show it in Set up, and document it

- New app/slide_report.py: `SlideReportDialog(report, full_path=None)`: a
  read-only dialog: sections Instrument, Channels, Scenes, Optics as simple
  two/three-column tables; a "What each value did" table from `uses`
  (Field | Value | Used for); "Differs between files" list from `varying`;
  a line naming the full file when given. Close button only.
- Set up (app/screens.py): beside the existing "Read from the slides" control
  add a "What the slides say…" button that opens the dialog with
  `project.get("project")["slides"]` (or reads it with AD.cached(source) and
  shows it when the study has none yet). Disabled when no report and no
  readable slides folder.
- docs/user-guide.md: in the Set up section that describes reading settings
  from the slides, a short paragraph: everything the headers say is kept in the
  study file (summary) and slide_metadata.json (every field), what it sets
  (pixel size, picture resolutions, colours, naming, channels) and what it
  checks (exposure, bit depth, objective, sampling).
- Test: tests/test_slide_report.py constructs the dialog offscreen from a
  report made by Task 2's detect on synthetic files and checks table row
  counts; and the Set up button exists (follow the pattern of existing
  screen tests, e.g. tests/test_workflow.py / tests/test_project_session.py).
