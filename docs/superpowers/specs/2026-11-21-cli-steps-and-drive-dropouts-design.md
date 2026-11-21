# Every step from the command prompt; slide reading that survives a dropped drive

Date: 2026-11-21. Status: design, approved choices from the operator marked as such.

## Why

1. The operator wants to run **any single step** from a command prompt, not only a whole
   screen (`python -m app --run prepare study.json` exists today) or a bare script with
   `LS_CONFIG` set by hand. Unattended and scripted runs need one step at a time, with the
   same options, interpreter and run history the app's button gives.
2. Slides (CZI, often several GB each) are read from a **USB drive that drops for a few
   seconds** and sometimes for longer. Today a drop mid-run makes every remaining slide fail
   fast as "CZI missing", the run ends mostly failed and has to be restarted by hand; a PNG
   cut off mid-write can be mistaken for done on the next run. The operator wants a step to
   **pause in place and resume by itself** when the drive is back, however long that takes,
   and the app to **flag** a drop that lasts more than a few seconds.

## Goals

- `--stage <id>` runs one stage of a study from the command line, exactly as the app's Run
  button would (saved options, Redo-when-out-of-date, recorded in the study's run history).
- `--list` shows every stage of a study: id, title, done / out of date / not run / not for
  this study / CLI-only reason.
- Both work from source (`python -m app ...`) and from the installed app via a console
  launcher (operator's choice: "Add a console launcher").
- Every step that reads slide pixels pauses when the slides folder or the output folder
  disappears, waits for it with no time limit, then **redoes the slide it was on from its
  start** and carries on (operator's choice: "Retry whole slide").
- A drop longer than a short grace period (10 s) is flagged by: an amber banner on the run
  screen, a system notification, and a note in the run history (operator's choice: all three).
- Picture outputs (PNG/TIFF) are written atomically, so a half-written file never counts as done.

## Non-goals

- Copying slides to the local disk before reading (ruled out: drops are short, disk is limited).
- Handling a drive that comes back under a **different letter / mount point**: it stays
  "disconnected" and the operator stops the run. Noted in the banner text as a hint.
- Changing how stages decide what is already done (existing resumability is kept as is).
- Running several stages in parallel from the CLI.

## Anti-goals (would be a failure even if it "works")

- A slide partly read before a drop and finished after it contributing numbers as if read once.
- A run that waited out a drop showing nothing about it later.
- A pause that cannot be cancelled: the app's Stop and Ctrl+C on the command line must end
  the wait promptly and leave outputs as a normal interrupted run would.
- A genuinely corrupt CZI (drive present, file unreadable) waiting forever: that stays a
  per-slide failure, as today.

## Approaches considered

- **A. Retry the whole slide (chosen).** A shared helper wraps each step's per-slide unit of
  work. On an `OSError` (or a pylibCZIrw/czifile error) it checks whether the watched folders
  are reachable; if not it waits, then reopens the CZI and reruns that slide's unit from the
  beginning. Needs an edit at the per-slide loop of each reader, but nothing half-read can leak.
- **B. Retry each individual read.** A proxy around the open document retries every `read()`.
  Fewer edits, but libCZI may crash the process rather than raise when the volume vanishes,
  and a step part-way through a slide would continue silently. Rejected.
- **C. Stage each slide on local disk.** Robust for long outages but costs space and time for
  multi-GB files; not needed for the drops described. Rejected.

## Design

### 1. `scripts/ls_drive.py` (new, standard library only)

- `watched()` - the folders to watch: `source_dir` and `out_root` from the study config
  (operator's choice: "Slides and output").
- `reachable(paths)` - each folder exists and can be listed (`os.listdir` on the root folder,
  not only `os.path.exists`, since Windows can report a cached path for a vanished volume).
- `per_slide(fn, *, label)` - runs `fn()`. On an exception from `fn`:
  - if every watched folder is reachable -> re-raise (a real per-slide failure, existing
    handling records it);
  - otherwise -> `wait_for_drive(label)` and call `fn()` again (loop: a second drop during the
    redo waits again).
  `fn` must be self-contained: it opens the CZI itself and only commits results (adds rows,
  writes files) at its end, or writes files that the redo simply overwrites atomically.
- `wait_for_drive(label)` - polls every 2 s with no time limit. Prints, through a new marker
  in the style of `@@progress`:
  - `@@drive lost <folder> | <label>` once, when the wait starts;
  - `@@drive back <seconds>` when it ends.
  On the plain command line (no `LS_PROGRESS`) it prints a human line instead
  ("slides drive unreachable (E:\\...), paused at <label>; waiting, Ctrl+C to stop") and
  repeats it once a minute. `KeyboardInterrupt` and the app's stop flag end the wait by
  raising, so the step stops as an interrupted run.
- Also exposes `wait_if_gone()` for the start of a per-slide iteration, so a step does not
  march through "CZI missing" failures while the drive is away: a missing CZI whose folder
  is unreachable waits; a missing CZI in a reachable folder is a real miss, as today.

### 2. Readers that get the wrapper

The per-slide (per-CZI-file or per-scene, whichever the stage already checkpoints on) unit in:
`01_overviews` (sampling and export loops), `01h_tilefield_raw`, `01k_saturation_raw`,
`01l_scene_renders`, `01m_reagent_screen`, `04o_section_rgb`, `05a_roi_geometry`,
`05b_tune_detection`, `05c_detect_rois`, `06i_detection_overlays`; and the zip readers in
`00_manifest` / `00b_verify_extraction`. Where a loop accumulates results across scenes of
one file (01's `done_here`, `qc_rows`), the unit collects into a local and merges only after
it finishes, so a redo does not double-count. The builder confirms per script which unit is
safe to redo; the rule is "nothing committed until the unit is complete".

### 3. Atomic image writes

All PNG/TIFF/JPEG outputs in the above scripts go through `ls_io.atomic_save` (format passed
explicitly). This also benefits the output-folder case.

### 4. The app

- `app/progress.py` parses `@@drive` lines (kept out of the log, like `@@progress`), into a
  `DriveState` (lost since, folder, label / back).
- The run screen shows an amber banner once the drop has lasted **10 s**: "Slides drive
  disconnected (E:\\Slides). Paused at LS53_1a scene 3 - will resume automatically when it is
  back. If it comes back under another drive letter, stop the run and fix the study's folders."
  The banner clears on `@@drive back`; the log gets one line "drive back after 2 min 14 s,
  redoing LS53_1a scene 3".
- After the same 10 s the app raises a system notification (Qt `QSystemTrayIcon.showMessage`,
  works on Windows and macOS) once per drop, and another when it resumes.
- The run record in `study.json` gains `pauses: [{at, seconds, folder, label}]`; the run
  history view shows "paused 2x, 3 min total" when present. Recording waits for the output
  folder too (study.json lives in `out_root`).
- Stop during a pause ends the wait and the stage as a normal interrupted run.

### 5. Command line

- `python -m app --stage <id> <study.json> [--opt key=value ...]` - runs one stage via the
  same `Runner` as `headless.run_screen`, with `W.run_values` / `W.run_inputs`, records the run
  (including pauses). Refuses, with the reason and exit 2, a stage that does not apply to the
  study, is `cli_only`/not wrapped, unblinds without `--unblind`, or opens a curator page
  (`curator=`: it prints the page path instead of opening a window). Blocked stages (`needs=`
  not met) are refused with what is missing unless `--force`.
- `python -m app --list <study.json>` - table of id, title, group, state.
- Exit status: 0 ran, 1 failed, 2 refused/bad arguments, 130 stopped by Ctrl+C.
- `--run <screen>` keeps working unchanged and gains the same pause handling for free.
- Installed app: a **console launcher** is added to both builds - on Windows `ihc.cmd`
  beside `The Great IHC Leap Forward.cmd`, running `"%~dp0runtime\python.exe" -m app %*`
  (the console `python.exe`, not `pythonw.exe`, so output and exit codes reach the prompt);
  the installer offers to add the install folder to the user's PATH. A `.cmd` rather than a
  native `.exe` is a builder's call: the bundle has no PyInstaller, and a `.cmd` works from
  Task Scheduler and PATH and is replaced by the in-app updater like any other code file; on macOS the app's inner binary is console-capable and the README documents
  `"/Applications/The Great IHC Leap Forward.app/Contents/MacOS/<binary>" --stage ...`.
  The pylibCZIrw interpreter choice is whatever `Runner` already does in each build.

### 6. Docs and tests

- README "Pipeline" section, `docs/wiki/Running-from-Source.md`, `docs/wiki/Troubleshooting.md`
  ("the drive dropped") and the Windows/Mac packaging READMEs.
- Tests: `ls_drive` with a fake folder that disappears and reappears (thread renames it back),
  including a second drop during the redo and Ctrl+C during the wait; one reader (01) end to end
  on the existing fixture with a drop injected mid-file, asserting the outputs equal an
  uninterrupted run and no duplicate rows; `progress.parse` of `@@drive`; `--stage` / `--list`
  exit codes and refusals; packaging smoke test runs `ihc.exe --list`.

### 7. Calls made after the builder check (operator: check these)

- **Watchdog, not only exceptions.** A read can block for a long time on a vanished volume
  instead of raising. While a wrapped unit runs, a background thread polls `reachable()` every
  2 s; if a folder goes away it emits `@@drive lost` itself, so the banner and notification
  appear even while the read is stuck. When the read finally raises, `per_slide` waits and
  redoes the unit as above.
- **Writes between slides wait too.** `ls_io.retry` (used by every atomic CSV/JSON/image
  write) calls `wait_for_drive` instead of giving up after ~31 s when a watched folder is the
  one gone, so per-file checkpoints and `record_run` survive a long drop on `out_root`.
- **Stop during a pause** ends the stage at once (the wait raises), and the run is recorded
  like any failed run (`ok: false`) with `error: "stopped while the drive was disconnected"`
  and its `pauses`. No new "stopped" state; CLI exit code is 1 (130 only for Ctrl+C at the
  prompt). Stop outside a pause keeps today's meaning (after this stage).
- **Crash safety net (operator: "not sure" whether past drops crashed the app).** Stages whose
  `reader` is `pylibCZIrw` or `czifile` run in a **child process** - the same
  `python -m app --stage <id> <study.json>` the command line uses (console `python.exe` from
  the bundle's `runtime\` when installed, the czienv interpreter from source, as `Runner`
  chooses today) - with its output streamed into the log pane exactly as now. The app
  supervises it: if the child dies abnormally (native crash, non-zero exit without a normal
  failure report) while a watched folder is unreachable, the app shows the banner, waits for
  the drive and starts the stage again; the stage's existing resumability skips the slides
  already done. A child that dies with the drive present is an ordinary failure. Stages that
  read no pixels stay in-process as today. On the command line, `--stage` runs a reader stage
  under the same supervisor, so the CLI gets the same protection.

## Left to the builder

- Exact per-script unit of redo, as long as the "commit only when complete" rule holds.
- Poll interval and how the stop flag reaches `wait_for_drive` (env var / threading event in
  the in-process Runner).
- Wording of messages, banner styling within `app/theme.py`.
