# Tutorial Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A guided tutorial inside the app that walks a newcomer (or a demo audience) through a whole study on a small demo study, with a side panel, pointer bubbles, ready-made results for slow stages, and ROI-curator practice checked against an answer key.

**Architecture:** One data file (`app/tutorial_steps.py`) lists the steps. A controller (`app/tutorial.py`) turns app events into step completion and drives a dock panel and a pointer overlay; it never reaches into screens except through object names. Demo-pack handling (`app/tutorial_pack.py`) and the ROI comparison (`app/tutorial_check.py`) are pure-Python modules with no Qt, so they test alone. The curator page gains two small JS hooks the view calls.

**Tech Stack:** Python 3.13, PySide6 (QtWidgets, QtWebEngine), numpy, Pillow (`ImageDraw.polygon` for rasterising), pylibCZIrw (demo-pack crop), the repo's script-style tests (`chk(...)`, run by `tests/run.sh`) and the Node curator harness (`tests/harness.js`).

**Spec:** `docs/superpowers/specs/2026-10-16-tutorial-mode-design.md`

## Global Constraints

- Never lose image detail or resolution: the demo crop keeps native pixels; compression, if any, is lossless (`zstd1`), never JPEG-XR re-encoding or downsampling.
- The tutorial writes only inside its own study folder; it never writes into another study, and leaving restores the study that was open before.
- No demo tissue or answer-key curation is committed to the repo until the user decides public vs lab-only. Tests use synthetic packs only.
- No new dependencies (`shapely` is not added). Overlap is rasterised on the section's pixel grid.
- Tutorial study folder: `<Documents>/The Great IHC Leap Forward/Tutorial study/`; tests override with `LS_TUTORIAL_HOME`.
- App settings folder for the first-run flag: `ls_config`'s home (`LS_HOME`, default `~/.ls-pipeline`), file `tutorial.json`, key `offered`.
- Curation check thresholds: overlap (IoU) `>= 0.5` is good, `< 0.5` is off; missing and extra are by ROI number on the same section.
- Slow stages that offer ready-made results: `scene_renders`, `rgb_perk`, `rgb_pcna`, `detect`.
- Every user-visible string is plain words, no stage codes as the first word (match `W.display_title` style).
- Tests follow the repo's style: a script with `chk(name, got, want)`, `failures` list, offscreen Qt, `_fixture.use_temp_study()`, and are added to nothing extra: `tests/run.sh` already runs every `tests/test_*.py`.

## Review Focus

- A learner closes the app mid-step and reopens it: the tutorial resumes at the same step in the tutorial study, not in their real study (Task 4 test `resume after restart`).
- The learner switches study (Switch study…) while the tutorial runs: the tutorial pauses and the panel hides, it does not tick steps from the other study (Task 4 test `other study pauses`).
- A stage is already running when the learner presses Leave tutorial: leaving waits through `_idle_for`, never re-roots mid-write (Task 7 test `leave while running is refused`).
- The learner draws a polygon for an ROI on a section that is not a practice section: it is ignored by the check, not reported as extra (Task 2 test `non-practice sections ignored`).
- The demo pack is a `.zip` whose entries escape the folder (`../`): install refuses it (Task 3 test `zip slip refused`).

---

### Task 1: The step list and stable target names

**Files:**
- Create: `app/tutorial_steps.py`
- Modify: `app/screens.py`, `app/main_window.py`, `app/curator_view.py` (add `setObjectName` on each control a step targets; no behaviour change)
- Test: `tests/test_tutorial_targets.py`

**Interfaces:**
- Produces: `Step` (frozen dataclass) with fields `sid: str, screen: str, title: str, text: str, target: str = "", bubble: str = "", done: str = "next", slow: bool = False, ready: tuple[str, ...] = ()`; `STEPS: list[Step]`; `by_sid(sid) -> Step`; `DONE_KINDS = ("next", "click", "stage", "file", "curation-checked")`; `parse_done(done: str) -> tuple[str, str]` (kind, argument; `"stage:detect"` -> `("stage", "detect")`, `"next"` -> `("next", "")`).
- `target` is a Qt object name, or `curator:<css selector>` for the curator page.

- [ ] **Step 1: Write the failing test** `tests/test_tutorial_targets.py`:
  - `chk("step ids unique", len({s.sid for s in TS.STEPS}), len(TS.STEPS))`
  - every `s.screen` is in `{"home","setup","prepare","curate","measure","results","finish"}`
  - every `parse_done(s.done)[0]` is in `DONE_KINDS`; `stage:` arguments are in `stages.BY_ID`
  - every `slow` step's stage is one of the four slow stages, and only those are `slow`
  - build `MainWindow(REPO, config_path=STUDY.path)` offscreen; for every step whose `target` is a Qt name: `chk(f"{s.sid}: {s.target} exists", w.findChild(QWidget, s.target) is not None)`
  - for `curator:` targets: the selector's id appears in the built 04l page source (`load_stage("04l_roi_curator.py")` build into a temp dir with `--no-seed`, read the HTML, `chk("#plateSel" in html)` style)
  - first step is on `home`, last on `finish`; screens appear in the app's order (home, setup, prepare, curate, measure, results, finish) without going back
- [ ] **Step 2: Run** `QT_QPA_PLATFORM=offscreen python tests/test_tutorial_targets.py` — Expected: ImportError on `tutorial_steps`.
- [ ] **Step 3: Write `app/tutorial_steps.py`** with the dataclass, `parse_done`, `by_sid`, and the ~15 steps of spec section 6, in order. Step texts are 2-4 plain sentences each. Slow steps carry `ready` = the output folders those stages write (take them from each stage's `outputs` in `app/stages.py`). Then add `setObjectName` on each targeted control: Home's "Start the tutorial" button is added in Task 7, so Task 1's first step targets the step list (`stepList`) and the Home "Next up" card (`nextUp`, already named). Name the rest after what they are (`setupSlides`, `setupNaming`, `setupMarkers`, `setupAtlas`, `runAllPrepare`, `picturesCard`, `openCurator`, `runAllMeasure`, `tuneDetection`, `joinGroups`, `buildResults`, `openSpreadsheet`). Curator selectors use the page's existing element ids; read them from 04l before choosing.
- [ ] **Step 4: Run the test** — Expected: all `ok`, exit 0.
- [ ] **Step 5: Run** `PY=python bash tests/run.sh` subset `tests/test_workflow.py tests/test_stages.py` — Expected: unchanged pass.
- [ ] **Step 6: Commit** `Tutorial: step list and stable names for what it points at`

### Task 2: The curation check

**Files:**
- Create: `app/tutorial_check.py`
- Test: `tests/test_tutorial_check.py`

**Interfaces:**
- Consumes: curator state JSON: `{section_uid: {"polys": [{"v": [x0,y0,x1,y1,...], "roi": int, "region": str, ...}], ...}}`.
- Produces: `Result` dataclass `section: str, roi: int, region: str, kind: str ("good"|"off"|"missing"|"extra"), overlap: float` (0.0 for missing/extra); `compare(learner: dict, key: dict, sections: list[str]) -> list[Result]` sorted by (section, roi); `iou(a: list[float], b: list[float]) -> float`; `words(r: Result) -> str`; `GOOD = 0.5`.
- An ROI with several polygons (parts) is the union of its parts on both sides.

- [ ] **Step 1: Write the failing tests:**
  - `iou` of a square with itself is `1.0`; of two disjoint squares `0.0`; of a 10x10 and its right half `0.5` within `0.02`
  - `identical`: `compare(k, k, ["s1"])` all `kind == "good"`
  - `missing`: key has ROI 4, learner none -> one `Result("s1", 4, "Dm", "missing", 0.0)`
  - `extra`: learner ROI 7 not in key -> `kind == "extra"`
  - `low overlap`: learner square shifted by 70% -> `kind == "off"`, `overlap < 0.5`
  - `empty curation`: learner `{}` -> every key ROI `missing`
  - `non-practice sections ignored`: learner polys on `"s9"` not in `sections` -> no result mentions `"s9"`
  - `two parts`: an ROI drawn as two halves matching a key drawn whole -> `good`
  - `words` for an off result contains the region name and "overlaps"; for missing contains "not drawn"
- [ ] **Step 2: Run** `python tests/test_tutorial_check.py` — Expected: ImportError.
- [ ] **Step 3: Implement** `iou` by rasterising both polygon sets with `PIL.ImageDraw.polygon` on a shared bounding-box grid (1 px = 1 section pixel; scale up by 4 if the box is under 64 px a side so tiny ROIs are not all edge), then `numpy` logical and/or counts. `compare` groups polys by roi per section and classifies.
- [ ] **Step 4: Run** — Expected: all ok.
- [ ] **Step 5: Commit** `Tutorial: compare practice ROIs with the answer key`

### Task 3: The demo pack and the tutorial study folder

**Files:**
- Create: `app/tutorial_pack.py`
- Test: `tests/test_tutorial_pack.py`

**Interfaces:**
- Pack layout as spec section 4 (`pack.json`, `slides/`, `study/study.json`, `study/sampling.xlsx`, `ready/`, `answer/`). `pack.json`: `{"version": int, "slide": str, "practice_sections": [str], "credits": str}`.
- Produces: `tutorial_home() -> str` (env `LS_TUTORIAL_HOME`, else `<Documents>/The Great IHC Leap Forward/Tutorial study`; Documents from `QStandardPaths.DocumentsLocation` when Qt is up, else `~/Documents`); `find_pack(app_root: str) -> str | None` (`<app_root>/demo` dir or `<app_root>/demo/*.zip`, else the path saved under `pack` in the tutorial settings); `Pack` with `.root, .meta, .practice_sections, .answer_curation() -> dict`; `open_pack(path: str) -> Pack` (dir or zip; zip is extracted to a temp dir; raises `PackError` with a plain message on a bad or unsafe pack); `install(pack: Pack, home: str) -> str` (copies `slides/` to `home/slides`, writes `home/output/study.json` from the pack's with `source_dir` and `out_root` rewritten to `home`, copies the workbook and sets it as `join_sampling`'s `--xlsx` option; returns the study file path; refuses if `home` exists and is non-empty unless `fresh=True`, which first moves the old folder to `home + ".old"`); `use_ready(pack: Pack, study_out: str, rel_paths: tuple[str, ...]) -> list[str]` (copies those relative paths from `ready/` into the output folder, returns what was copied); `Progress` with `load(out_root) -> Progress`, `.save()`, fields `current: str, done: set[str], skipped: set[str], previous_study: str`, stored as `<out_root>/tutorial.json`; `settings() -> dict` and `save_settings(d)` for `<LS_HOME>/tutorial.json`.

- [ ] **Step 1: Write the failing tests** using a synthetic pack built in the test (a tiny CZI from `tests/test_autodetect.py`'s `write_czi` helper, a `study.json` from `_fixture.write_study`, an `openpyxl` workbook, a `ready/scene_renders/x.png`):
  - `open dir pack`, `open zip pack` -> same `.meta`
  - `install` -> `study.json` exists; reopened with `ls_project.Project.open`, `source_dir` and `out_root` are inside `home`; `join_sampling` option `--xlsx` points at the copied workbook
  - `install twice refuses` without `fresh`; `fresh=True` moves the old one to `.old` and installs
  - `use_ready` copies exactly the named paths, returns them
  - `Progress` round-trips through save/load; missing file -> empty progress
  - `zip slip refused`: a zip with `../evil.txt` -> `PackError`, nothing written outside the temp dir
  - `bad pack`: no `pack.json` -> `PackError` whose message says what is missing
- [ ] **Step 2: Run** — Expected: ImportError.
- [ ] **Step 3: Implement** with `shutil`, `zipfile` (check every member's resolved path stays inside the target), `json`, and `ls_project` for the study rewrite.
- [ ] **Step 4: Run** — Expected: all ok.
- [ ] **Step 5: Commit** `Tutorial: demo pack, tutorial study folder and saved progress`

### Task 4: The controller and the panel

**Files:**
- Create: `app/tutorial.py`
- Modify: `app/main_window.py` (add `stage_finished = Signal(str, bool)` emitted at the end of `_done` for ok and failed; add `study_opened = Signal(str)` emitted at the end of `_project_opened`)
- Test: `tests/test_tutorial_panel.py`

**Interfaces:**
- Consumes: `tutorial_steps.STEPS/parse_done`, `tutorial_pack.Progress/use_ready/Pack`, `MainWindow.goto(sid)`, `MainWindow.stage_finished`, `MainWindow.study_opened`, `MainWindow.config_path`, `MainWindow.out_root`.
- Produces: `TutorialController(QObject)`: `__init__(window, pack: Pack, study_path: str)`, `start()`, `next()`, `back()`, `skip()`, `use_ready()`, `mark_done(sid)`, `current -> Step`, `paused -> bool`, signals `changed()`, `finished()`, `point(str target, str bubble)`; `TutorialPanel(QDockWidget)` with object name `tutorialPanel`, child buttons named `tutBack`, `tutNext`, `tutSkip`, `tutReady`, `tutStartOver`, `tutLeave`, list `tutSteps`, text `tutText`.
- Done rules: `next` -> Next enabled at once; `click` -> an event filter on the target fires `mark_done`; `stage:<id>` -> `stage_finished(id, True)` or `W.state(BY_ID[id], window.study(), window._running) == "done"` on entering the step; `file:<rel>` -> checked on entering the step and after every `stage_finished`; `curation-checked` -> set by Task 6.
- A failed `stage_finished(id, False)` for the current step's stage shows the plain failure line and enables `tutReady` when the step has `ready`.
- `study_opened` with a path other than the tutorial study -> `paused = True`, panel hidden; back to the tutorial study -> resumes.

- [ ] **Step 1: Write the failing tests** (offscreen, synthetic pack from Task 3's helpers moved to `tests/_tutorial_fixture.py`):
  - `start goes to the first step's screen`: `w.stack.currentWidget() is w.pages["home"]`
  - `next on a next-step advances`, `back returns`, `skip marks skipped and advances`
  - `stage step ticks on stage_finished`: emit `w.stage_finished.emit("manifest", True)` while on that step -> step in `progress.done`, current moved on
  - `failure offers ready-made`: on a slow step, emit `(sid, False)` -> `tutReady` enabled and visible
  - `use_ready copies and ticks`: press `tutReady` -> the `ready` files exist under `out_root`, step done
  - `resume after restart`: save, build a new controller from the same study -> same `current`, same `done`
  - `other study pauses`: emit `w.study_opened.emit("/elsewhere/study.json")` -> `paused`, panel hidden, emitting a stage_finished does not tick
- [ ] **Step 2: Run** — Expected: ImportError.
- [ ] **Step 3: Implement** the controller (state in `Progress`, saved after each change) and the panel (a `QListWidget` with ✓, – and ▶ prefixes; a word-wrapped `QLabel` for text; a button row). The two `MainWindow` signals are one `emit` line each.
- [ ] **Step 4: Run** the new test and `tests/test_project_session.py tests/test_curation_save.py` — Expected: all ok.
- [ ] **Step 5: Commit** `Tutorial: controller and side panel`

### Task 5: The pointer bubble on Qt screens

**Files:**
- Create: `app/tutorial_bubble.py`
- Test: add a section to `tests/test_tutorial_panel.py`

**Interfaces:**
- Consumes: `TutorialController.point(target, bubble)`.
- Produces: `PointerOverlay(QWidget)` (object name `tutorialBubble`), child of the main window, transparent to mouse events except its own label; `show_for(target: str, text: str, window, panel)`; `hide_pointer()`; property `attached_to -> str` = `"target"` or `"panel"`.
- Fallback: target not found, not visible, or its rectangle not inside the window -> attached to the panel, text prefixed with the control's visible text or name ("Press **Run all remaining**: ...").

- [ ] **Step 1: Write the failing tests:** pointing at a visible named button -> `attached_to == "target"` and the overlay's outline rect (mapped to window coordinates) contains the button's centre; pointing at a missing name -> `attached_to == "panel"`; resizing the window re-places the outline (rect changes); switching screens hides it when the target is not on the current page.
- [ ] **Step 2: Run** — Expected: fail.
- [ ] **Step 3: Implement** with an event filter on the window and the target (Resize/Move/Show/Hide) to re-place; paint a rounded outline and a small label box beside the target, flipping side when there is no room.
- [ ] **Step 4: Run** — Expected: ok.
- [ ] **Step 5: Commit** `Tutorial: pointer bubble`

### Task 6: Inside the ROI curator: pointer, practice check and both outlines

**Files:**
- Modify: `scripts/04l_roi_curator.py` (add `tutorialPoint(selector, text)`, `tutorialClear()`, `tutorialShowKey(uid, polys)`, `tutorialClearKey()` to the page script; the key outlines are drawn dashed in the section canvas's draw pass only while set)
- Modify: `app/curator_view.py` (`point(selector, text)`, `clear_point()`, `show_key(uid, polys)`, `clear_key()` via `runJavaScript`)
- Modify: `app/tutorial.py` (curate step: on the curator's Done/export for the tutorial study, run `tutorial_check.compare(learner, pack.answer_curation(), pack.practice_sections)`, show results in the panel with **Try again** (`tutRetry`) and **Carry on** (`tutCarryOn`), set `curation-checked`)
- Test: `tests/tutorial_point.test.js`, add a section to `tests/test_tutorial_panel.py`

**Interfaces:**
- Consumes: `tutorial_check.compare/words`, `Pack.answer_curation()`, `CurationStore.read("ls_roi_curator_v1")` for the learner's state, `CuratorView`'s existing export-finished path (the same place `set_done` is called).
- Produces: the four JS functions above, defined on `window`; they are no-ops unless called.
- Try again: removes `polys` from the practice sections only in the learner's curation (through `CurationStore.write`), reloads the curator page.

- [ ] **Step 1: Write the failing JS test** (`tests/harness.js` style): `tutorialPoint("#plateSel","x")` adds one element with class `tut-point`; `tutorialClear()` removes it; `tutorialShowKey(uid, polys)` then a draw call strokes with a dash pattern set (the stub records `setLineDash`); `tutorialClearKey()` stops that; with none called, the existing suites' exports are unchanged (run `tests/roi_curator_study.test.js` as the no-regression check).
- [ ] **Step 2: Write the failing Python tests:** with a fake learner curation where ROI 4 is shifted, finishing the export on the curate step shows a panel line containing the region name and "overlaps", and `tutRetry`/`tutCarryOn` are visible; `tutRetry` clears only practice-section polys; `tutCarryOn` ticks the step.
- [ ] **Step 3: Run both** — Expected: fail.
- [ ] **Step 4: Implement** the JS (small, beside the existing guided-mode code), the `CuratorView` wrappers and the controller hook.
- [ ] **Step 5: Run** `bash tests/run.sh` (the curator suites skip without a real worklist on CI; run locally against the synthetic study) — Expected: ok.
- [ ] **Step 6: Commit** `Tutorial: practice in the ROI curator, checked against the answer key`

### Task 7: Starting, the first-run offer, leaving

**Files:**
- Modify: `app/screens.py` (HomeScreen: button `tutorialStart`, text "Start the tutorial" or "Continue the tutorial", emits `action("tutorial")`)
- Modify: `app/main_window.py` (`_home_action("tutorial")`; Help menu "Tutorial…" above "User guide"; `start_tutorial()`; `leave_tutorial()`; `_offer_tutorial_once()` called with `QTimer.singleShot(0, ...)` after the window is shown, from `app/__main__.py`)
- Test: `tests/test_tutorial_start.py`

**Interfaces:**
- Consumes: `tutorial_pack.find_pack/open_pack/install/tutorial_home/Progress/settings/save_settings`, `TutorialController`, `PointerOverlay`, `session.open_project(self, path, confirmed=True)`, `_idle_for`.
- Produces: `MainWindow.start_tutorial(fresh: bool = False)`, `MainWindow.leave_tutorial()`, `MainWindow.tutorial -> TutorialController | None`.
- No pack found -> a message box with "Choose demo file…" (QFileDialog for a folder or `.zip`; the choice is saved under `pack` in the settings) and Cancel.
- Start: remembers `config_path` in `Progress.previous_study`, installs if the tutorial study is missing, opens it, builds the controller.
- Leave: `_idle_for("leave the tutorial")` first; hides panel and pointer; reopens `previous_study` if it still exists.
- First run: if `settings().get("offered")` is falsy -> a two-button box "Take the tutorial" / "Not now"; sets `offered = True` either way.

- [ ] **Step 1: Write the failing tests:** Home shows `tutorialStart`; Help menu has an action whose text starts "Tutorial"; `start_tutorial()` with a synthetic pack (found via `LS_TUTORIAL_PACK` env pointing at it, added to `find_pack` as the first place looked) opens the tutorial study (`w.config_path` under `LS_TUTORIAL_HOME`) and shows `tutorialPanel`; `leave_tutorial()` restores the previous `config_path`; `leave while running is refused` (set `w._thread = object()`, patch `QMessageBox.information`) leaves `config_path` unchanged; first-run offer appears once (patch `QMessageBox.question`, call `_offer_tutorial_once()` twice, count 1); after a restart the Home button reads "Continue the tutorial".
- [ ] **Step 2: Run** — Expected: fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the test and `tests/test_new_study.py tests/test_project_session.py` — Expected: ok.
- [ ] **Step 5: Commit** `Tutorial: start from Project or Help, offer once on first run, leave cleanly`

### Task 8: Making the demo pack

**Files:**
- Create: `scripts/make_demo_pack.py`
- Test: `tests/test_demo_pack.py`

**Interfaces:**
- CLI: `python scripts/make_demo_pack.py --czi <source.czi> --scenes 3,4,5 --study <source study.json> --practice <uid>,<uid> --out <pack dir> [--workbook-groups A,B]`.
- Produces a pack per Task 3's layout. The crop writes the chosen scenes' native pixels for every channel with `pylibCZIrw` `create_czi(..., compression_options="zstd1:ExplicitLevel=1")` (lossless), copying channel metadata and scaling so `czi_meta` reads the same `um_per_px` and channel names. `study.json` is the source's with paths blanked (Task 3 fills them). The workbook has the fish ids from the cropped slide name with invented group names (`--workbook-groups`, default `Group A,Group B`). `answer/ls_roi_curator_v1.json` holds only the practice sections' entries from the source study's curation.
- Frame check: runs the Prepare stages up to reformat on the pack's own study in a temp folder and compares each practice section's reformatted image digest with the source study's; any mismatch -> exit 2 with "section <uid> does not reformat to the same frame; the answer key would be misplaced" and no `answer/` written. `--ready` runs the four slow stages and copies their outputs into `ready/`.

- [ ] **Step 1: Write the failing tests** (skip with a SKIP line when `pylibCZIrw` is missing, as `test_czi_speed.py` does): on a synthetic 2-channel CZI with 4 scenes, cropping scenes 1,2 gives a CZI whose `czi_read.read_plane` pixels equal the source's for those scenes exactly (`numpy.array_equal`), whose scene count is 2, and whose `czi_meta` `um_per_px` and channel names match; `answer/` holds exactly the practice uids; a forced digest mismatch (monkeypatched digest function) exits 2 and writes no `answer/`.
- [ ] **Step 2: Run** — Expected: fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — Expected: ok (or SKIP without pylibCZIrw).
- [ ] **Step 5: Commit** `Tutorial: script that cuts a demo pack from a real slide`

### Task 9: Whole walk, docs and screenshots

**Files:**
- Create: `tests/test_tutorial_walk.py`
- Modify: `docs/user-guide.md` (a short "Tutorial" section: how to start, leave, start over), `README.md` (one line)
- Screenshots: `/mnt/project-files/tutorial-preview/*.png` (not committed)

- [ ] **Step 1: Write the walk test:** start the tutorial on the synthetic pack and, for each step, fire its done condition (`next()` for `next`, `target.click()` for `click`, `stage_finished.emit(id, True)` for `stage`, create the file for `file`, call the Task 6 hook with a matching learner curation for `curation-checked`), asserting the panel's current step and screen at each point; at the end `finished` was emitted and the panel shows the finish text; skipping every step from the start also reaches `finish`.
- [ ] **Step 2: Run** — Expected: ok.
- [ ] **Step 3: Grab screenshots** offscreen (`w.grab().save(...)`) of each screen with the panel and bubble, into `/mnt/project-files/tutorial-preview/`.
- [ ] **Step 4: Docs** — add the user-guide section and README line.
- [ ] **Step 5: Run** `PY=python bash tests/run.sh` — Expected: the whole suite passes.
- [ ] **Step 6: Commit** `Tutorial: whole walk test and guide section`
