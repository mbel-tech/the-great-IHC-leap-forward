# Tutorial mode: design

Date: 2026-10-16. Asked for in the project thread "tutorial mode": add a
tutorial to the app, with the design worked out by questions first.

## What the user decided

- **Audience**: a new lab member running a study start to finish, and a demo
  for others (talks, supervisors, reviewers). One tutorial serves both; a
  presenter uses Skip.
- **Data**: a bundled demo study made from a **cut-down real slide** (a few
  whole sections cropped from one of the user's CZIs).
- **Where the demo files live**: decided **after the user has seen the crop**
  (public inside the app, or a lab-only download). The code accepts a demo
  pack from either place, so this choice does not block building.
- **Guidance**: both a **side panel** (the journey, ticks, explanations) and
  **pointer bubbles** (the exact control to press).
- **What runs**: fast stages run for real; slow stages offer **"Use
  ready-made results"**, with the choice to run them anyway.
- **Starting it**: a button on the Project screen and in the Help menu, and
  the **first launch offers it once**.
- **ROI curator**: learners **practise with feedback** - they curate a couple
  of sections, and the tutorial compares their ROIs with the user's own
  curation of the same sections (the answer key).
- **Build**: **one step-list file** holds every step, so the tutorial is
  edited in words, not in code spread across screens.
- Design approved in three parts in the thread (pieces, walk-through, errors
  and testing).

## Facts it rests on

- The app (`app/`) is PySide6 with six screens (Project, Set up, Prepare,
  Curate, Measure, Results) in a `QStackedWidget` driven by a step list in
  `MainWindow`; screens and their stages come from `app/workflow.py`
  (`W.STEPS`, `W.SCREENS`).
- The ROI curator is an HTML page (`scripts/04l_roi_curator.py` output) shown
  in a `QWebEngineView` (`app/curator_view.py`) and served from the study's
  output folder by `LocalServer`. Curation is saved through `app/state.py`
  (`CurationStore`, `Bridge`). The page already has a "guided" mode that walks
  a plate's ROIs one at a time; curator behaviour is tested with the Node
  harness in `tests/*.test.js`.
- A new study is "slides + output folder" with `study.json` in the output
  folder; settings are read from CZI headers; built-in atlas plates skip the
  atlas-building stages.
- Slow stages on real data: `scene_renders` (01l), `rgb_perk`/`rgb_pcna`
  (04o), `detect` (05c, StarDist). Results needs a sampling workbook to unblind
  (`join_sampling`, 06b).
- The repository is public: anything committed is visible to anyone.

## How it works

### 1. The step list (`app/tutorial_steps.py`)

A plain list of `Step` records, in order. Each step has:

| field | meaning |
|---|---|
| `sid` | stable id, used to save progress |
| `screen` | the screen id the step lives on (`home`, `setup`, ...); the tutorial switches to it |
| `title` | short line shown in the panel's list |
| `text` | the explanation shown in the panel (a few sentences, plain words) |
| `target` | object name of the control to point at, or `curator:<selector>` for an element inside the curator page, or empty |
| `bubble` | one line shown in the pointer bubble |
| `done` | what counts as done: `stage:<id>` (stage finished), `file:<relpath>` (file exists in the tutorial study), `click` (the target was pressed), `curation-checked`, or `next` (the learner presses Next) |
| `slow` | true when the step's stage is slow; enables "Use ready-made results" |
| `ready` | the files in the pack's `ready/` folder to copy in when ready-made is chosen |

Controls the tutorial points at get stable `setObjectName(...)` names where
they lack one today. Nothing else in the screens changes.

### 2. The tutorial panel (`app/tutorial.py`, `TutorialPanel`)

A `QDockWidget` on the right of the main window, shown only while the
tutorial runs. It shows:

- the step list with ticks (done), dashes (skipped) and the current step;
- the current step's title and explanation;
- buttons: **Back**, **Next** (only for `next` steps or once done), **Skip**,
  **Use ready-made results** (slow steps only), **Start over**, **Leave
  tutorial**.

A `TutorialController` owns the state: the current step, done/skipped sets,
and the watchers that turn app events into "done" (stage finished signals from
the runner, a file check on refresh, a click filter on the target). Progress
is saved to `tutorial.json` in the tutorial study's output folder after every
change, so leaving and coming back resumes at the same step.

### 3. The pointer bubble

- **Qt screens**: a frameless overlay child of the main window that outlines
  the target widget's rectangle and shows the bubble line beside it. It
  follows the target on resize and screen changes, and hides when the target
  is not visible. If the target cannot be found or is off screen (window too
  small), the bubble attaches to the panel and names the control instead.
- **Inside the ROI curator**: the page gets a small `tutorialPoint(selector,
  text)` / `tutorialClear()` pair that draws the same outline and line over a
  page element. `CuratorView` calls them with `runJavaScript`. The functions do
  nothing unless called, so the curator outside the tutorial is unchanged.

### 4. The demo study pack

A folder (or a `.zip` of it) with:

```
demo-pack/
  pack.json            version, slide name, practice section ids, credits
  slides/<name>.czi    the cropped CZI: a few whole sections
  study/study.json     ready settings (file-name rule, markers, atlas)
  study/sampling.xlsx  a made-up sampling workbook (invented group names)
  ready/...            ready-made outputs of the slow stages, laid out as
                       they sit in an output folder
  answer/              the answer-key curation for the practice sections
```

**Starting the tutorial** copies the pack into a fresh folder
`Documents/The Great IHC Leap Forward/Tutorial study/` (slides and output
inside it), opens it as a normal study, remembers which study was open before,
and shows the panel. Every screen then behaves as it does for real work.

**Where the pack comes from**: the app looks for a pack shipped beside the app
(`demo/` in the install folder) first; if none, the panel offers "Choose demo
file..." to point at a lab-shared `.zip`. Which of these is used is the
user's later decision; both paths are built.

**Making the pack**: a script `scripts/make_demo_pack.py` crops the chosen
whole scenes from a source CZI losslessly (native pixels of every channel
written with lossless zstd compression, never JPEG-XR or downsampled), writes `study.json`, runs the slow stages once to
fill `ready/`, and copies the answer-key curation for the practice sections.
It records the source slide and scene ids in `pack.json`. Before the answer
key is trusted, the script checks that the cropped sections reformat to the
same frame as in the source study (same section image digests); if they do
not, it stops and says so instead of shipping misplaced ROIs.

### 5. The curation check (`app/tutorial_check.py`)

After the learner presses Done in the curator on the practice sections, the
check compares their ROIs with the answer key, ROI by ROI (matched by ROI
number on the same section):

- **missing**: in the key, not drawn;
- **extra**: drawn, not in the key;
- **off**: drawn, but overlap (intersection over union of the polygons) below
  0.5;
- **good**: overlap 0.5 or more.

The panel lists the results in plain words ("ROI 4 (Dm): about a third
overlaps the expert's outline"), and the curator shows both outlines for a
chosen ROI (the key dashed). The learner can **Try again** (their practice
curation is cleared) or **Carry on** (their curation is kept and used for the
rest of the tutorial). Overlap is computed by rasterising both polygons on
the section's own pixel grid (numpy only; `shapely` is not a dependency and is
not added).

### 6. The walk-through (`tutorial_steps.py` contents)

About fifteen steps, in the app's own order:

1. **Project**: what a study is; start the tutorial study.
2. **Set up**: point at and explain the slides list, the file-name rule, the
   markers read from the CZI headers, and the built-in salmon atlas. Nothing
   needs typing; the pack arrives filled in.
3. **Prepare**: press "Run all remaining". Quick stages run for real; scene
   pictures and colour composites offer ready-made results. Then open the
   Pictures card and look at a good and a clipped scene.
4. **Curate**: open the ROI curator; a short tour of the plate picker, guided
   mode, drawing a region, Rotate and Review; curate the two practice
   sections; Done; see the comparison; try again or carry on.
5. **Measure**: place the ROIs on the slide (runs for real); look at signal
   quality and the detection settings; nuclei detection with the choice of
   ready-made results or running StarDist.
6. **Results**: join the treatment groups using the made-up workbook; build the
   tables; open the per-sample spreadsheet.
7. **Finish**: how to start a real study, with links to the user guide.

Instrument characterisation and the "All stages" list get one sentence that
they exist, no steps.

### 7. Starting, the first-run offer, leaving

- **Project screen**: a "Start the tutorial" button (and "Continue the
  tutorial" when progress exists).
- **Help menu**: "Tutorial..." with the same action.
- **First launch**: once, when no `tutorial_offered` flag is in the app's
  settings (`~/.ls-pipeline`), a small dialog offers "Take the tutorial" /
  "Not now". The flag is set either way.
- **Leave tutorial**: hides the panel and bubble and reopens the study that
  was open before. The tutorial study stays on disk for resuming.

## Errors

- A stage failing during a step: the panel says so in plain words, links the
  stage's log, and offers "Use ready-made results" (when the pack has them) so
  the tour can continue.
- Tutorial study missing or damaged on resume: the panel offers "Start over",
  which makes a fresh copy from the pack.
- No demo pack found: the Project button opens a short dialog with "Choose
  demo file..." and nothing else changes.
- The tutorial never writes into any study other than its own folder; leaving
  restores the previous study.
- A step whose target is not on screen: the bubble attaches to the panel and
  names the control.

## Testing

- `tests/test_tutorial_targets.py`: every `target` in the step list names a
  control that exists in a constructed `MainWindow` (offscreen Qt), and every
  `screen` is a real screen id. This is what stops other threads breaking the
  tutorial silently.
- `tests/test_tutorial_walk.py`: walks the whole tutorial headless on a
  synthetic mini-pack (the synthetic JPEG-XR study trick from the speed work),
  ticking each step by firing its done condition; checks panel state, Skip,
  Back, ready-made copying, saving and resuming from `tutorial.json`, and
  that leaving restores the previous study.
- `tests/test_tutorial_check.py`: the ROI comparison - identical, missing,
  extra, low overlap, and empty curation.
- `tests/tutorial_point.test.js`: the curator's `tutorialPoint` /
  `tutorialClear` in the existing Node harness, and that the page without
  them called behaves as before.
- `tests/test_demo_pack.py`: `make_demo_pack.py` on a synthetic CZI keeps the
  cropped scenes byte-identical and refuses when the frame check fails.
- Screenshots of each screen with the panel and bubble are posted in the
  thread before the user tries it.

## Needed from the user later (each asked as a card)

1. Which slide, and which two or three sections, to crop for the demo.
2. A yes that their existing curation of those sections is the answer key.
3. After seeing the crop: ship it publicly inside the app, or as a lab-only
   download.

## Not in scope

- Tutorials for instrument characterisation, repair stages or atlas building
  from Fiji.
- Translating the tutorial.
- Scoring or certifying learners beyond the curation comparison.
