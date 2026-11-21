# Single-stage CLI and drive-dropout resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run any stage from the command prompt, and make every slide-reading stage pause on a
dropped USB drive (slides or output folder), flag it, and resume by itself by redoing the slide
it was on.

**Architecture:** A stdlib-only `scripts/ls_drive.py` owns "is the drive there / wait for it /
redo this unit", speaking to the app through `@@drive` lines like `@@progress`. Reader stages wrap
their per-slide unit in it; `ls_io.retry` waits on it. The app runs reader stages in a supervised
child process (`python -m app --stage <id> <study> --child`) so a native crash during a drop is
recovered; `--stage`/`--list` are the same entry points for people.

**Tech Stack:** Python 3.13 stdlib, PySide6 (banner, `QSystemTrayIcon`), pylibCZIrw/czifile
(unchanged), PowerShell/Inno Setup packaging. Tests are script-style (`chk()`), run by `bash tests/run.sh`.

**Spec:** `docs/superpowers/specs/2026-11-21-cli-steps-and-drive-dropouts-design.md`

## Global Constraints

- Watched folders: the study's `source_dir` and `out_root`.
- Wait has **no time limit**; poll every **2 s**; banner and notification after **10 s** of a drop.
- Redo unit is the whole slide (or scene, where the stage checkpoints per scene); nothing from a
  unit is committed until the unit completes.
- A folder that is reachable but a file in it unreadable is an ordinary failure, never a wait.
- Stop/Ctrl+C ends a wait at once; a stopped run is recorded `ok: false`,
  `error: "stopped while the drive was disconnected"`, with its `pauses`.
- CLI exit codes: 0 ran, 1 failed/stopped, 2 refused/bad arguments, 130 Ctrl+C.
- Run record gains `pauses: [{"at": iso, "seconds": float, "folder": str, "label": str}]`.
- Marker lines: `@@drive lost <folder> | <label>` and `@@drive back <seconds>`.
- Stop signal across processes and module instances: the file named by env `LS_STOP_FILE` existing.
- Image outputs (PNG/TIFF/JPEG) written atomically via `ls_io`.
- Installed Windows console launcher: `ihc.cmd` = `@"%~dp0runtime\python.exe" -m app %*`.
- No model names or session ids in code/comments.

## Review Focus

1. **Drive drops a second time during the redo of the same slide** - expect another wait, then a
   clean redo; no duplicate rows. Pinned in Task 1 (`per_slide` loop) and Task 3 (01 end to end).
2. **Drive returns but a different slide file is now missing** (folder reachable) - expect that
   slide recorded as a normal failure, run continues. Pinned in Task 1 and Task 3.
3. **Only `out_root` drops while slides stay** - checkpoint CSV write waits instead of failing after
   ~31 s. Pinned in Task 2.
4. **Child process crashes (non-zero, no result line) with the drive present** - ordinary failure,
   no restart loop. Pinned in Task 6.
5. **`--stage` on a stage that unblinds, opens a curator, is `cli_only`, or does not apply to the
   layout** - exit 2 with the reason, nothing run or recorded. Pinned in Task 7.

---

### Task 1: `ls_drive` core

**Files:**
- Create: `scripts/ls_drive.py`
- Test: `tests/test_ls_drive.py`

**Interfaces:**
- Produces:
  - `watched() -> list[str]` - `LS_WATCH` (os.pathsep list) if set, else `source_dir` and `out_root`
    read with `json.load` from `LS_CONFIG` (no `ls_config` import); empty/missing values dropped.
  - `reachable(paths=None) -> tuple[bool, str|None]` - `(True, None)` or `(False, first_gone)`;
    a folder counts when `os.listdir` on it succeeds.
  - `class Stopped(Exception)` - raised when the stop file appears during a wait.
  - `wait_for_drive(label: str, poll: float = 2.0) -> float` - blocks until `reachable()`, returns
    seconds waited; prints the `@@drive` pair when `LS_PROGRESS=1`, else a human line at start and
    once a minute ("... unreachable (<folder>), paused at <label>; waiting, Ctrl+C to stop") and a
    "back after N s" line; raises `Stopped` if `LS_STOP_FILE` exists. Returns 0.0 immediately if
    reachable.
  - `wait_if_gone(label: str) -> float` - alias semantics: wait only when a watched folder is gone.
  - `per_slide(fn: Callable[[], T], *, label: str) -> T` - run `fn`; on `Exception` (not
    `Stopped`/`KeyboardInterrupt`) re-raise if reachable, else `wait_for_drive(label)` and call
    again, looping.
  - `class Watchdog` - context manager `Watchdog(label)`: daemon thread polling `reachable()` every
    `poll` s; on loss prints `@@drive lost` itself (once per loss) so the app flags a read that is
    blocked rather than raising. `per_slide` uses it around each attempt; when `wait_for_drive`
    follows a watchdog-announced loss it does not print `lost` twice.
  - Module-level `PAUSES: list[dict]` appended by each completed wait (`at`, `seconds`, `folder`, `label`).

- [ ] **Step 1: Write the failing tests** (`tests/test_ls_drive.py`, chk style, temp dirs, `LS_WATCH`)
  - `reachable` true for two existing dirs; after renaming one away -> `(False, that_path)`.
  - `wait_for_drive` with a thread that renames the folder back after 0.5 s (poll=0.1) returns
    `>= 0.4`; with `LS_PROGRESS=1` the captured stdout contains exactly one `@@drive lost ` and one
    `@@drive back ` line; `PAUSES[-1]["label"] == label`.
  - `per_slide`: fn raising `OSError` while folder gone, folder restored by thread, second call
    returns `"ok"` -> result `"ok"`, fn called 2 times.
  - Second drop during redo: fn removes folder on call 2 and raises, restorer thread restores twice
    -> fn called 3 times, `len(PAUSES)` grew by 2.
  - Folder reachable, fn raises `ValueError("bad czi")` -> re-raised, fn called once, no pause.
  - Stop: folder gone, `LS_STOP_FILE` created after 0.3 s -> `Stopped` raised within 1 s.
  - Watchdog: folder removed while fn sleeps 0.6 s (poll 0.1) -> `@@drive lost` printed before fn
    returns.

- [ ] **Step 2: Run** `python tests/test_ls_drive.py` - Expected: fails, `ls_drive` missing.
- [ ] **Step 3: Implement `scripts/ls_drive.py`** per Interfaces (stdlib only; module docstring in the repo's explanatory style).
- [ ] **Step 4: Run** `python tests/test_ls_drive.py` - Expected: every line `ok`, exit 0. Add it to `tests/run.sh` if suites are listed there explicitly.
- [ ] **Step 5: Commit** `feat: ls_drive - wait for a dropped drive and redo the unit`

### Task 2: `ls_io` waits for the drive; atomic image saves

**Files:**
- Modify: `scripts/ls_io.py` (`retry`, new `save_image`)
- Test: `tests/test_ls_io.py` (extend)

**Interfaces:**
- Consumes: `ls_drive.reachable`, `ls_drive.wait_for_drive` (loaded by path, as siblings are).
- Produces:
  - `retry(fn, path, attempts, what="write to")` - unchanged signature; on `OSError`, if
    `reachable()` is false, `wait_for_drive(f"{what} {path}")` and retry **without consuming an
    attempt**; otherwise today's backoff.
  - `save_image(im, path, format=None, **kw)` - PIL image (or ndarray -> `Image.fromarray`) saved
    through `atomic_save`; `format` inferred from the extension when None.

- [ ] **Step 1: Failing tests:** `atomic_write_csv` into a watched folder that is renamed away
  before the write and back after 0.5 s succeeds and the file holds the rows (attempts=1 proves no
  attempt was consumed); `save_image(np.zeros((4,4),np.uint8), p)` leaves `p` a valid PNG and no `p.tmp`;
  an exception inside the PIL save leaves the old file untouched.
- [ ] **Step 2: Run** `python tests/test_ls_io.py` - Expected: new checks FAIL.
- [ ] **Step 3: Implement** both.
- [ ] **Step 4: Run** `python tests/test_ls_io.py tests/test_atomic_outputs.py` - Expected: all ok.
- [ ] **Step 5: Commit** `feat: ls_io waits for a dropped drive; atomic image saves`

### Task 3: Readers - 01 overviews, 01h, 01k

**Files:**
- Modify: `scripts/01_overviews.py` (sampling loop ~L463, export loop ~L593), `scripts/01h_tilefield_raw.py`, `scripts/01k_saturation_raw.py`
- Test: `tests/test_overview_failures.py` (extend; it already stands in for pylibCZIrw), new `tests/test_drive_resume_readers.py` for 01h/01k if their existing tests don't fit

**Interfaces:**
- Consumes: `ls_drive.per_slide`, `ls_drive.wait_if_gone`, `ls_io.save_image`.

Rule for each loop: call `wait_if_gone(fname)` before the `os.path.exists(path)` check, so a
missing CZI in an unreachable folder waits rather than failing; move the body that opens the CZI
and processes its scenes into a local `unit()` that collects `qc_rows`/`sat_raw`/`done_here`
into locals and returns them; merge after `per_slide(unit, label=...)` returns. Images inside
the unit go through `save_image` (overwritten on redo).

- [ ] **Step 1: Failing test (01):** stand-in `open_czi` whose `read` raises `OSError` on the 2nd
  scene of the 1st file the first time, after renaming the watched source dir away; a thread renames
  it back after 0.3 s. Assert: exit 0, `focus.csv` rows equal those from an uninterrupted run (same
  uids, no duplicates), `overview_failures.csv` header only, a `@@drive back` line in output.
  Also: a second file missing while the folder is reachable is still listed in `overview_failures.csv`.
- [ ] **Step 2: Run** `python tests/test_overview_failures.py` - Expected: new checks FAIL.
- [ ] **Step 3: Implement** in 01, then the same pattern in 01h (czifile per file) and 01k (per file); add a one-drop check for each of 01h/01k using their existing stand-ins.
- [ ] **Step 4: Run** `python tests/test_overview_failures.py` and the 01h/01k suites - Expected: all ok.
- [ ] **Step 5: Commit** `feat: 01, 01h, 01k pause on a dropped drive and redo the slide`

### Task 4: Readers - 01l, 01m, 04o, 05a

**Files:** Modify `scripts/01l_scene_renders.py` (~L866), `scripts/01m_reagent_screen.py` (~L378, ~L549), `scripts/04o_section_rgb.py` (the reader class ~L675: reopen on redo), `scripts/05a_roi_geometry.py` (~L566, ~L1054). Tests: extend each script's existing suite (`tests/test_czi_render.py`, `tests/test_reagent*`, `tests/test_composite.py`, 05a's suite) with one injected drop each.

**Interfaces:** Consumes Task 1/2. Same rule as Task 3. In 04o the unit is one section render: the band reader object is created inside the unit so a redo reopens the file.

- [ ] **Step 1:** Failing one-drop check per script: output identical to the undropped run, one `@@drive back`.
- [ ] **Step 2:** Run the four suites - Expected: new checks FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the four suites - Expected: all ok.
- [ ] **Step 5: Commit** `feat: 01l, 01m, 04o, 05a pause on a dropped drive and redo the slide`

### Task 5: Readers - 05b, 05c, 06i, 00, 00b

**Files:** Modify `scripts/05b_tune_detection.py` (~L155), `scripts/05c_detect_rois.py` (~L2441, ~L2651; unit = one section's ROIs, committed by its existing per-section checkpoint), `scripts/06i_detection_overlays.py` (~L1087, ~L1231), `scripts/00_manifest.py` (zip/CZI header loop ~L158), `scripts/00b_verify_extraction.py` (~L87). Tests: `tests/test_detect_resume.py`, `tests/test_detection_overlays.py`, `tests/test_manifest_checks.py`, 05b's/00b's suites.

- [ ] **Step 1:** Failing one-drop check per script; for 05c additionally assert the per-ROI CSV has no duplicate `(scene_uid, roi)` rows after the redo.
- [ ] **Step 2:** Run - Expected: new checks FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run - Expected: all ok.
- [ ] **Step 5: Commit** `feat: 05b, 05c, 06i, 00, 00b pause on a dropped drive and redo the unit`

### Task 6: Supervised child process for reader stages; pauses in the run record

**Files:**
- Create: `app/supervise.py`
- Modify: `app/runner.py` (`StageResult.pauses`, `Runner.run` dispatch), `scripts/ls_project.py` (`record_run(..., pauses=None)`), `app/project_history.py` (Result column: "ok, paused 2x (3 min)")
- Test: `tests/test_supervise.py`, extend `tests/test_headless.py` for `pauses` recording

**Interfaces:**
- Consumes: `ls_drive.reachable`, `ls_drive.wait_for_drive`, `ls_drive.Stopped`; `progress.parse_drive` (Task 8 - define it here if Task 8 is not done yet: `parse_drive(line) -> ("lost", folder, label) | ("back", seconds) | None`).
- Produces:
  - `StageResult(..., pauses=None)` - `.pauses` list, default `[]`.
  - `supervise.child_argv(sid: str, study: str) -> list[str]` - `[python, "-m", "app", "--stage", sid, study, "--child"]`; `python` is `sys.executable`, swapped to sibling `python.exe` when it is `pythonw.exe`.
  - `supervise.run_child(argv, on_line, stop_file) -> (rc: int, pauses: list, tail: str)` - streams lines to `on_line`, collects pauses from `@@drive` pairs; if the child exits with an rc other than 0/1 (or by signal) while `reachable()` is false, emits the lost marker if none came, waits, and relaunches; with the drive present it returns.
  - `Runner.run`: when `stage.reader in ("pylibCZIrw", "czifile")` and env `LS_CHILD` is unset -> run via `run_child`, sets `LS_STOP_FILE` for the child; in-process otherwise (unchanged). `Runner.stop()` creates the stop file and terminates the child.
  - `Project.record_run(stage, ok, started, ended, options=None, inputs=None, pauses=None, error=None)` - stores `pauses` and `error` when given.
- [ ] **Step 1: Failing tests** with a fake child (`python -c` script printing lines): rc 0 -> ok, pauses parsed from `@@drive` lines; rc -9 while `LS_WATCH` folder is gone and restored by thread -> relaunched once, final rc 0, one pause; rc -9 with folder present -> no relaunch, failure; stop file during wait -> returns failure with error "stopped while the drive was disconnected"; `record_run(..., pauses=[...])` round-trips to disk.
- [ ] **Step 2:** Run `python tests/test_supervise.py` - Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run `python tests/test_supervise.py tests/test_headless.py tests/test_failed_run.py` - Expected: all ok.
- [ ] **Step 5: Commit** `feat: reader stages run in a supervised child that survives a drive dropout`

### Task 7: `--stage`, `--list`, `--child` on the command line

**Files:**
- Modify: `app/headless.py` (`run_stage`, `list_stages`, `main` dispatch), `app/__main__.py` (route `--stage`/`--list` like `--run`, before Qt)
- Test: `tests/test_headless.py` (extend)

**Interfaces:**
- Consumes: `Runner`, `W.run_values`, `W.run_inputs`, `W.with_options`, `S.BY_ID`, `Stage.applies/cli_reason/curator/unblinds`, `S.blocked_by`, `record_run(pauses=, error=)`.
- Produces:
  - `run_stage(path, sid, runner=None, out=None, force=False, unblind=False, opts=None, child=False) -> (ok: bool, rc: int)` - `child=True` runs in-process and does not record (the parent records); otherwise records with pauses.
  - `list_stages(path, out=None) -> int` - prints `id  title  state` rows; state one of `done`, `out of date`, `not run`, `blocked: <need>`, `not for this study`, `cli only: <reason>`.
  - CLI: `--stage <id> <study.json> [--force] [--unblind] [--opt key=value]... [--child]`, `--list <study.json>`; exit codes per Global Constraints; `KeyboardInterrupt` -> 130.
- [ ] **Step 1: Failing tests:** `--list` prints every applicable stage id; `--stage manifest` with stand-in runner records one run; refusals return 2 with the reason for: unknown id, `join_sampling` without `--unblind`, `roi_curator` (prints page path), a `cli_only` stage, a stage whose `layouts` exclude the fixture's; blocked stage refused without `--force`; subprocess `python -m app --stage nope study.json` exits 2.
- [ ] **Step 2:** Run `python tests/test_headless.py` - Expected: new checks FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run `python tests/test_headless.py` - Expected: all ok.
- [ ] **Step 5: Commit** `feat: run or list a single stage from the command line`

### Task 8: The app flags a dropped drive

**Files:**
- Modify: `app/progress.py` (`parse_drive`; `parse` returns None for `@@drive` lines), `app/main_window.py` (`_stage_line`, drawer banner, tray notification, Stop during pause)
- Test: `tests/test_progress.py` (or create), `tests/test_drive_banner.py` (offscreen Qt)

**Interfaces:**
- Produces: `progress.DRIVE_MARK = "@@drive "`, `parse_drive(line)` as in Task 6; `ActivityDrawer.show_drive_lost(folder, label)`, `.clear_drive()`.
- Behaviour: `@@drive` lines never reach the log as raw text; on `lost` start a 10 s `QTimer`; when it fires show the amber banner (theme warning colour) with the text from spec §4 and a `QSystemTrayIcon.showMessage` ("Slides drive disconnected", "...will resume automatically") once; on `back` cancel the timer, clear the banner, log "drive back after <words>, redoing <label>", and notify "Drive back - resuming" only if the first notification was shown. While a drop is flagged, the Stop button is visible and reads "Stop now" and calls `Runner.stop()`.
- [ ] **Step 1: Failing tests:** `parse_drive` on both forms and junk; `parse("@@drive lost X | y")` is None; offscreen window: feed `lost`, advance the 10 s timer (patch the interval to 0) -> banner visible with folder and label; feed `back` -> banner hidden and log line present.
- [ ] **Step 2:** Run - Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the two suites plus `tests/test_screens*.py` if present - Expected: all ok.
- [ ] **Step 5: Commit** `feat: the app flags a disconnected drive and resumes by itself`

### Task 9: Console launcher, docs

**Files:**
- Modify: `packaging/windows/build.ps1` (write `ihc.cmd` beside the `.cmd` at L133), `packaging/windows/installer.iss` (ship it; optional task "Add the ihc command to PATH" modifying HKCU `Path`), `packaging/windows/smoke_test.py` (run `ihc.cmd --list` against a fixture study, expect exit 0), `packaging/windows/README.md`, `packaging/macos/README.md` (CLI via the app's inner binary), `README.md` (Pipeline: `--stage`/`--list`, drive dropouts), `docs/wiki/Running-from-Source.md`, `docs/wiki/Troubleshooting.md` ("the drive dropped"), `LOGS.md` entry.
- [ ] **Step 1:** Implement launcher and installer change; extend smoke test.
- [ ] **Step 2:** Run `python packaging/windows/smoke_test.py --help` (syntax/import check; the full smoke runs in the Windows CI job).
- [ ] **Step 3:** Docs.
- [ ] **Step 4:** Run `bash tests/run.sh` - Expected: last line reports no failures.
- [ ] **Step 5: Commit** `feat: ihc console launcher; docs for single-stage runs and drive dropouts`
