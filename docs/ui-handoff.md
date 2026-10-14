# UI handoff: LS IHC pipeline app

Design for the desktop app (`app/`) and the ROI curator page (`scripts/04l_roi_curator.py`).
Written against `origin/main` at `daa8987` (after PR #30).

- **Interactive mockups:** https://claude.ai/artifact/AvGsXSw4xgRcTbMdbVxfwf (private; share from the page's Share menu)
- **Artboards:** `Main.dc.html` (app window, all steps), `RoiCurator.dc.html`, `StudyPicker.dc.html`, `ChannelEditor.dc.html`, `PatternBuilder.dc.html`, `UnblindDialog.dc.html`
- **Audience the design is for:** new lab members. Light theme. The ROI curator keeps the original page's per-function button colours.

The mockups are HTML/JS stand-ins. Measurements, copy and behaviour below are the spec; the sample data (animal IDs `LS120`-`LS122`, section counts, timestamps, "pERK / PCNA" progress) is illustrative.

---

## 1. What changes, in one screen

The app already has the right skeleton: a left step list, one screen at a time, stage rows with Run / (i) / options, an activity drawer. The redesign keeps that and changes:

| Area | Today (`main`) | Design |
|---|---|---|
| Theme | Inherits OS palette; hard-coded greys that assume dark (`QUIET #8b949e`) | Light palette, fixed token set (section 3) |
| Step list | Plain `QListWidget`, titles only, fixed 220 px | Numbered circle, title, one-line description, progress mark; "Advanced" group at the foot |
| Screen header | Title, (i), intro | Same, plus a blue **Your part** callout saying what the person does there |
| Curate | Tabs: ROI curator, Atlas plates, Import decisions | Same tabs; embedded curator frame gains status line, Export state, Own window, Open full size |
| Set up | Tabs per card | One new **Pictures** tab; channels table gets a per-marker "Find nuclei" selector |
| Prepare/Measure/Results | Stage rows | Rows get **Open** buttons for folders; stale-results banner; unblind dialog gets the workbook field |
| ROI curator page | One crowded header; long footer paragraph | Grouped tool rail, top filter bar, coach line, info column, animal-grouped strip, shortcuts popup, responsive page |

No new pipeline behaviour is needed except the items flagged **NEW** in section 7.

---

## 2. Design principles

1. **One next thing.** Every screen says what the person does there (Your part) and the Project screen's Next up card names the single next action.
2. **Automatic vs you decide.** Prepare and Measure are "press Run all remaining". Only Curate asks for judgement. The step list says so in each subtitle.
3. **Nothing hidden by width.** Every button stays reachable at 840 px; the page scrolls rather than clips (verified in Chromium at 840 and 1360 px).
4. **Explain the number.** Each row shows plain words first, technical detail behind (i). This already exists; keep the wording in `workflow.py` / `stages.py`.
5. **Colour carries function, not mood.** In the curator each hue means one kind of action (section 6.3) and is reused for its "on" state.

---

## 3. Tokens

### 3.1 Colour

| Token | Value | Use |
|---|---|---|
| `bg` | `#f6f8fa` | Window background |
| `surface` | `#ffffff` | Cards, rows, panels, step list |
| `surface-hover` | `#eef1f4` | Hover on step items |
| `line` | `#d0d7de` | Borders |
| `line-soft` | `#e6eaee` | Row dividers inside cards |
| `text` | `#1f2328` | Body |
| `text-muted` | `#59636e` | Hints, secondary. **Replaces `#8b949e`** (3.3:1 on white; fails AA) |
| `accent` | `#0969da` | Primary button, current step, links |
| `accent-bg` / `accent-line` | `#ddf4ff` / `#54aeff` | Current step, "Your part", selected tab |
| `ok` | `#1a7f37` | Done |
| `warn` | `#9a6700` | Waiting, amber notices (bg `#fff8c5`, line `#d4a72c`) |
| `danger` | `#cf222e` | Failed, unblind, exclude |
| `purple` | `#8250df` (text on light `#6639ba`) | Registered, rotation tools |
| `running` / `queued` | `#0969da` | In progress |
| `notused` | `#8c959f` | Stage not used in this study |

Stage status colours (replace `widgets.COLOUR`): done `ok`, todo `text-muted`, manual `purple`, running/queued `running`, failed `danger`, notused `notused`, waiting `warn`.

### 3.2 Type

System UI stack: `Segoe UI` on Windows, then `system-ui`. Monospace for file names, patterns and code: `Consolas`, `DejaVu Sans Mono`.

| Role | Size | Weight |
|---|---|---|
| Screen title | 24 px | 600 |
| Part heading | 16 px | 600 |
| Body | 14 px / 1.5 | 400 |
| Hint, status | 12.5-13 px | 400 |
| Group label (rail, step-list header) | 11-12 px, uppercase, 0.04 em | 600 |

### 3.3 Space, shape

- Radius: cards 8, buttons 6-7, pills 10, circles 50%.
- Page padding: 22-28 px; card padding 12-18; gaps 8/12/16.
- Minimum hit size 36 px (44 px for step-list items).
- Focus ring: 2 px `accent`, 2 px offset, on every control.

### 3.4 Qt note

Today widgets set colours inline (`QUIET`, `COLOUR`, `#nextUp{border:2px solid #4da3ff}`, `InfoPopup` border). Move these into one application stylesheet or a palette plus a small `theme.py` holding the tokens above, so a later dark theme is a token swap. Use the `Fusion` style with an explicit light `QPalette` so the OS dark mode does not repaint half the window.

---

## 4. App shell

Existing code: `app/main_window.py` (`MainWindow`, `ActivityDrawer`), `app/widgets.py`.

### 4.1 Window title and menus
- Title: `LS IHC pipeline  -  <project title>` (unchanged).
- Menus unchanged: File (Save, Open Recent, Export a copy…), View, Tools. The mockup's "File ▸ Save" and "View ▸ Activity" stand in for them.

### 4.2 Step list (left)
Replace the plain `QListWidget` with a custom item delegate. Width 236-280 px (was fixed 220).

Each item, top to bottom:
1. Circle 28 px with the step number. States: default (white, grey ring), current (`accent` fill, white number), complete (`ok` fill, ✓).
2. Title, 600.
3. Description, 12.5 px muted.
4. Progress mark, 12 px 600: `5 of 6 cards`, `10 of 19 stages`, `0 of 1 exported`, or `Done`.

Items, in order and with descriptions:

| Item | Description | Progress counts |
|---|---|---|
| Project (⌂) | Where things stand | none |
| 1 Set up | Tell the app about your slides | Set up cards applied of 6 |
| 2 Prepare | Automatic: read, check, clean | stages done of applicable |
| 3 Curate | You decide: rotate, exclude, mark regions | curators exported of 1 |
| 4 Measure | Automatic: find and count nuclei | stages done |
| 5 Results | Unblind, then tables and figures | stages done |

Below a spacer, a group labelled **Advanced**: `All stages`, `Instrument characterisation` (flat buttons, left-aligned).

Current item: `accent-bg` fill, `accent-line` border. Keyboard: Up/Down changes screen.

At narrow widths (under about 760 px) the list may wrap above the content. Between 760 and 1360 px it stays at the left (this was a real bug found at 840 px: set `flex: 1 1 236px; max-width: 280px` or the Qt equivalent minimum/maximum width).

### 4.3 Status bar and activity drawer
- Status bar: `Study: <name>` left, run state centre (`Idle` muted, `Running: <stage> (NN%)` in `running`), output path right.
- Drawer: unchanged behaviour (log, Stop button). Mono 12.5 px on `surface`.

---

## 5. Screens

Common layout for every screen (`ScreenHeader` in `widgets.py`):
1. Title (24 px) with (i) button at the right.
2. Intro paragraph (muted, max 80 characters wide).
3. **Your part** callout: `accent-bg`, `accent-line`, bold "Your part" in `#0550ae`, then one sentence. Added in `Screen` as a new `role` field next to `intro` and `info`.
4. (i) opens a card: **Goes in** / **Comes out** (already in `Screen.info`).

"Your part" copy:

| Screen | Text |
|---|---|
| Project | Read the Next up card and press Go. Come back here any time to see where the study stands. |
| Set up | Fill in each tab once, in order, and press Apply on each. Nothing runs yet, so you cannot break anything. |
| Prepare | Press Run all remaining. It runs straight through to the ROI curator. |
| Curate | In the ROI curator: turn sections upright, bring back any that were set aside, place the regions, then press Export. Your work saves as you go. |
| Measure | Press Run all remaining and leave it. Detection takes hours; you can stop and resume. |
| Results | Press Run on Join the treatment groups once you are sure the regions are final, then build the spreadsheets. |
| All stages | Only here if you know exactly which stage you want. New to the app? Use the steps on the left. |
| Instrument | Nothing. This was done once for this microscope and does not need repeating. |

### 5.1 Project (`HomeScreen`)
- Project strip: file name, then `222 slide files · 2,572 sections · 12 animals · 2 markers`.
- **Next up** card: 2 px `accent` border, label, one sentence at 16 px, primary **Go**.
- Five progress rows: name, bar (10 px, `accent`, `ok` when complete), count text, **Open**.
- Recent activity: last stages with `ok`/`failed` word, name, time.
- Button row: Switch study…, Open project…, New project…, Export a copy…, Project history…. "Switch study…" opens the study picker.

Next up priority (keeps the current rule, minus the rotation gate removed in PR #30):
1. Set up incomplete → name the missing card, go to Set up.
2. Prepare stages remain → "Run Prepare. It goes straight through to the ROI curator."
3. ROI curator not exported → "Turn the sections upright, check what was set aside, place the regions, then export from the ROI curator."
4. Measure stages remain → Measure.
5. Not unblinded → "Measurements are in. Unblind and build the results."
6. Otherwise → Results.

### 5.2 Set up (`SetupScreen`)
Tabs, each ending in **Apply** with a status line (green "Applied. It is saved with the project, and automatically before the next stage runs." or red "Not applied: …"). A completed tab shows ✓ in its label.

| Tab | Content |
|---|---|
| Slides | Current folder sentence; Choose folder… / Choose files…; table of files (Read as / "Does not fit the pattern; every stage would ignore it", red row tint); Slide file names field (mono) with **Build pattern…** |
| Study | Study name; Section order; Output folder; Curator exports folder |
| Microscope and channels | Pixel size, section thickness, overview scale, acquisition layout; marker table (Marker, Scan channel, Role, **Find nuclei** selector); **Edit channels…** |
| **Pictures (new)** | *Curation pictures*: Pixels come from (scan itself / 5.2 µm overview), Picture detail µm/px. *Scene pictures*: Folder, Detail µm/px, Brightness (same range for all / stretch per scene) |
| Atlas | Atlas source radios (built-in salmon, Wullimann, import PDF); mirror polygons checkbox |
| Nucleus detection | Nucleus diameter, Threshold (MAD multiplier), Smallest object µm², Abercrombie correction checkbox |

**Find nuclei** per marker (paired studies): `On the DAPI counterstain` or `On its own channel`; writes `acquisition.marker_segmentation`. Show the consequence under the control when "own channel" is chosen: "Total cell density is withheld for this marker."

Existing field metadata (hints, ↺ reset, (i) text) comes from `ls_config.dialog_fields()`; do not duplicate it in the layout. The tab split mirrors `workflow.setting_sections()`.

### 5.3 Prepare, Measure, Results, All stages, Instrument (`StageListScreen`)
Structure: **Run all remaining** button and one hint line, then parts. Each part: heading (16 px), explanation (muted), then a card of stage rows.

Part names (Prepare), with the current stage groups:
1. Read and check the slides
2. Build the atlas
3. Make the section images
4. Final images
5. Get ready for the ROI curator

Stage row (`StageRow`):
- Status dot 10 px, then title (600) over plain-words hint.
- Status text (13 px, same colour as the dot).
- Right-aligned group, never wrapping: optional **Open…** (secondary, 13 px), (i), primary action.
- Running: 6 px progress bar under the row.
- (i) expands a panel: plain words, **Leaves behind**, **Technical detail** (script, reader).

Row states and what the action button shows:

| State | Dot | Status text | Button |
|---|---|---|---|
| done | `ok` | Done | Redo |
| todo | muted | Not run yet | Run (primary) |
| running | `running` | Running… | disabled |
| queued | `running` | Queued | disabled |
| waiting on ROI export | `warn` | Waiting for your ROI export | **Open curator** (goes to Curate) |
| waiting on detection | `warn` | Needs detection to finish first | Run, disabled |
| not used | grey | Not used in this study | disabled |
| unblind stage, todo | muted | Not run yet | **Run…** (opens confirm) |

Open buttons (shown when the stage is done; opens a folder):

| Stage | Label |
|---|---|
| Export a picture of every section | Open pictures |
| Build contact sheets | Open sheets |
| Render a picture of every scene | Open pictures |
| Spreadsheet by sample (and results) | Open results |

**Run all remaining:** runs every todo stage of the current screen in order, skipping repairs and the unblind stage; stops at the first row waiting on you. Disabled while anything runs. Hint text: `N stages left to run; M wait for you`, `Running: <stage>`, or `Nothing left to run here.`

**Stale-results banner** (Measure and Results, when `results/roi_nuclei.csv` predates the ROI fixes of #21/#24): amber (`#fff8c5` / `#d4a72c`), bold "These results are older than the ROI fixes.", one sentence naming what to redo, **Dismiss**.

**Prepare "Get ready for the ROI curator"** has one colour-pictures stage per marker, each also writing thumbnails (`rgb_perk`, `rgb_pcna`).

### 5.4 Curate (`CurateScreen`)
Tabs: ROI curator, Atlas plates, Import decisions. The ROI tab carries a ✓ once exported.

ROI tab:
- Status line: dot plus "Waiting for your export. Decide in the page, then press Export." (amber) or "Exported and handed on. Measure can now run." (green).
- Buttons right: **Own window** (moves the same view into a maximised window and back, from PR #30), **Open full size**.
- Frame (the embedded `QWebEngineView` of the curator page) with its title strip: name, hint, "saved HH:MM:SS", Export button. Export shows `Exported ✓` after use.
- Footer line: "Your work in a curator is saved into the project as you go, so closing the app loses nothing. Export is what hands the decisions to the stages that come after."

The embedded frame in the mockup is a drawn preview, not the page itself.

Atlas plates: one-paragraph explanation plus **Open plate reframe**. Import decisions: one paragraph plus **Choose an export to import…** (checks section count and timestamp before replacing anything).

---

## 6. ROI curator page (`scripts/04l_roi_curator.py`)

This is HTML/CSS/JS inside `QWebEngineView`. The redesign changes markup and CSS; **do not rename element ids or JS handlers** (section 6.5), because tests lift and run the page's script, and the app and Shotgun deck depend on them.

### 6.1 Layout (top to bottom)

1. **Top bar** (wraps; min height 52 px)
   - Back to app (embedded: hide), title "ROI curator".
   - `Animal` select (`#animal`), `Marker` select (`#marker`).
   - Checkboxes: favourites (`#favOnly`), hide excluded (`#hideExcl`).
   - Right: six counts (shown, registered purple, plate only blue, no ROI, ★ gold, excluded red).
   - **Review** (`#revBtn`), **?** (shortcuts popup).
   - Export group: **Folder…** (`#expDirBtn`), **Export** (primary), **Shotgun** (`#shotBtn`), **By region** (`#shotRegBtn`).
2. **Body**, flex-wrap row:
   - **Tool rail** (basis 212, max 300) with labelled groups (6.2).
   - **Centre** (basis 560, grows): coach line, then two panes.
   - **Info column** (basis 250): cards in an auto-fit grid, so wrapped it spreads across the page.
3. **Section strip**, sticky to the bottom of the window, grouped by animal.
4. Modals: ROIs dialog, shortcuts popup.

Responsive rules (verified at 840 and 1360 px):
- Page scrolls; never `overflow:hidden` on the body or `height:100%` on the layout.
- Each pane `height: clamp(360px, 56vh, 540px)`, panes wrap and stack when the centre is narrow.
- Top bar wraps onto extra rows; the Export group must stay on screen.
- Strip is `position: sticky; bottom: 0`.

### 6.2 Tool rail groups and buttons

Each button: full rail width, left-aligned label, optional shortcut at the right in parentheses (`.k`, 12 px, 70% opacity). Colours in 6.3.

| Group | Button | Shortcut | Notes |
|---|---|---|---|
| This section | ★ Favourite | f | toggles; gold on |
| | No ROI here | | toggles; orange on |
| | Exclude | x | toggles; red on |
| Atlas plate | Assign plate / Plate assigned ✓ | a | blue on when assigned |
| Look | Hide DAPI / Show DAPI | | **label from `__NUCLEAR__`** |
| | Hide `<marker>` / Show `<marker>` | | **NEW**, marker's own colour |
| | Hulls | h | green on |
| | Rotate | | purple on; drag section left/right |
| | Restore tilt | r | |
| Draw ROIs | Guided | g | green on |
| | ROIs… | o | opens the ROIs dialog |
| | Skip ROI | s | |
| Background discs | Background | d | pink on; click to place a disc |
| | disc size slider | `[` `]` | 2-60 px (`#roiSize`) |
| Undo | Point | z | drop last corner |
| | Region | | undo last region |
| | Undo last | u | disabled until there is something to undo |
| | Clear section | | confirm text: takes back landmarks, unfinished outline and regions; `u` restores |

Saved-state text ("saved HH:MM:SS") and export status sit under the rail.

### 6.3 Button colours (from the original page)

Resting: coloured text and border on white. On: tinted fill, darker text, stronger border.

| Class | Meaning | Text | Border | On fill / text |
|---|---|---|---|---|
| `btn-plate` | plate assignment, DAPI | `#0969da` | `#54aeff` | `#ddf4ff` / `#0550ae` |
| `btn-rot` | rotation, undo last | `#8250df` | `#c297ff` | `#fbefff` / `#6639ba` |
| `btn-fav` | favourite, marker layer | `#9a6700` | `#d4a72c` | `#fff8c5` / `#9a6700` |
| `btn-excl` | no ROI, apply mask | `#bc4c00` | `#e8873a` | `#fff1e5` / `#bc4c00` |
| `btn-kill` | exclude, drop | `#cf222e` | `#ff8182` | `#ffebe9` / `#a40e26` |
| `btn-bg` | background discs | `#bf3989` | `#e68bc4` | `#ffeff7` / `#99286e` |
| `btn-region` | hulls, ROIs | `#1a7f37` | `#4ac26b` | `#dafbe1` / `#116329` |
| `btn-guide` | guided, skip, review | `#1a7f37` | `#4ac26b` | `#dafbe1` / `#116329` |
| `btn-edit` | point/region edit, folder | `#0a7280` | `#56c2cf` | |
| `btn-shot` | deliverables (Shotgun) | `#9a5b00` | `#e0a54a` | |
| `primary` | Export | white on `#0969da` | | |

Mapping from the page's current CSS variables: `--bg #14161a → #f6f8fa`, `--fg #e8e8ea → #1f2328`, `--dim #9aa0a8 → #59636e`, `--line #2a2f37 → #d0d7de`, `--accent #4da3ff → #0969da`, `--ok #3fb950 → #1a7f37`, `--warn #d29922 → #9a6700`, `--done #7c5cff → #6639ba`. Pane/card background `#0e1014 → #ffffff`.

**Keep dark:** the section and plate canvases and the Review preview. They show fluorescence images; the frame around them is light. Overlay text on them uses `#b6bec8`; nav arrows `rgba(14,16,20,.6)` with `#d0d7de`.

Disabled Shotgun / By region: keep amber and visible (not 40% faded) with the reason in the tooltip, as the current page does.

### 6.4 Coach line
One line above the panes, colour-coded to the active mode, naming the mode in bold:

| Mode | Colour | Text |
|---|---|---|
| Landmarks | green | Click the section, then the matching point on the plate. Wheel zooms; middle or right drag pans. |
| Guided | green | Click where ROI N belongs on the section. Enter or double-click closes the region. |
| Rotate | purple | Drag the section left or right to turn it upright. Shift for fine steps. This is the only rotation step. |
| Background | pink | Click tissue with no signal to place a disc. Two or three per section. |

### 6.5 Panes

**Section pane** (dark canvas, title strip overlay top-left): title text follows mode ("SECTION - click to place a point", etc.). Mode outline: pink 3 px in Background mode, green in Guided. Zoom controls top-right: `−`, level button (`100%` = fit), `+`. Wheel zooms about the cursor, middle/right drag pans, keys `+` `-` `0`. Previous/next arrows at the left and right edges (`#secPrev`, `#secNext`).

Layers drawn: nuclei (DAPI), stain dots in the marker's colour, landmarks (numbered, green), background discs (pink ring), hulls (dashed green). Hide DAPI and Hide stain remove their layer only.

**Plate pane**: numbered seeds in the atlas's own colours; placed seeds filled green; current guided target ringed in `#54aeff`; unplaced light grey. Arrows `#plPrev`, `#plNext`.

**Info column** cards: Section (name, marker, section number, rotation), Plate (slider, name, assigned state), Landmarks (count, list), ROIs placed (count, fit type affine/thin-plate spline, colour key), Background discs (count, advice).

### 6.6 Section strip
- Grouped by animal; group label `LS120 · 4 sections` (11 px, muted, uppercase tracking).
- Cell 74 px wide, 2 px border, thumbnail, `#N` label.
- Cell states: active (5-6 px `accent` border plus ring), registered (purple border, `#f1ebff`), plate only (blue border, `#ddf4ff`), excluded (red border, 50% opacity, grey thumbnail), no ROI (grey border, 55% opacity), favourite (gold inset 2 px).
- Empty filter result: "Nothing matches these filters. Untick favourites only or hide excluded." in `warn`.
- Sections are filtered by the Animal and Marker selects and the two checkboxes; grouping applies after filtering.

### 6.7 Review mode (`#revBtn`)
Replaces the panes with a card grid (left) and a side column (right, basis 300).

- Cards: name, flag chip (RS reinstated blue, DR dropped red, UM unusual mask purple), thumbnail, note.
- Side column: previous/next, filter select (`#revFilter`), preview, layer toggles (DAPI `d`, Marker `k`, Artifacts `v`, Censored `c`, Apply mask `m`), Full screen (`f`), "only sections I changed", then **Reinstate**, **Drop**, **Reject mask**, **Clear**, and **Export review decisions**.
- Message line under the actions says what was decided.

### 6.8 ROIs dialog (`#selDlg`)
Title "Which ROIs will you draw?", sentence, filter input, list of rows (numbered swatch in the region's colour, name, "N sections"), All / None / Cancel / Confirm. Row is a toggle with `aria-pressed`.

### 6.9 Shortcuts popup (`?`)
Two columns. Left: `g` guided, `s` skip, `z` drop corner, `u` undo last, `h` hide shapes on plate, `o` choose ROIs, `[` `]` disc size. Right: `+` `-` `0` zoom, `a` assign plate, `f` favourite, `x` exclude, `d` background disc, `←` `→` plate, `↑` `↓` section, `r` restore tilt. Below: how closing a region works (Enter, double-click, first corner), editing closed regions (drag a corner, click an edge to add one, Delete removes), and the colour legend (purple registered, blue plate only, gold edge favourite, pink background disc, faded no-ROI). "Everything autosaves." Replaces the long footer paragraph.

### 6.10 Must-keep ids and handlers (from `04l_roi_curator.py`)
`markAssigned`, `toggleRotMode`, `restoreTilt`, `toggleFav`, `toggleNoRoi`, `toggleExcl`, `toggleDapi` (`#dapiBtn`), `toggleBgMode` (`#bgBtn`), `toggleHulls` (`#hullBtn`), `selOpen` (`#selBtn`), `undoPoly`, `toggleGuided` (`#guideBtn`), `skipRoi` (`#skipBtn`), `undoPt`, `undoLast` (`#undoBtn`), `clearPts`, `toggleReview`, `chooseExportDir`, `exportCsv`, `shotgun()` / `shotgun('region')`, `revStep`, `revLayer('dapi'|'mark'|'art'|'cen'|'apply')`, `revAct('restore'|'drop'|'unmask'|'')`, `exportReview`, `selAll/selNone/selCancel/selConfirm`, `stepSection`, `stepPlate`, `render`, `onMarker`, `onRoiSize`. Canvases `#cSec`, `#cPl`. Do not call `getImageData` (see the module docstring in `curator_view.py`).

---

## 7. Genuinely new work (everything else is restyle)

| Item | Where | Notes |
|---|---|---|
| Light theme + token module | `app/` | Section 3.4 |
| Step list delegate (circle, subtitle, progress) | `main_window.py` / `widgets.py` | Progress counts already derivable from stage state |
| `Screen.role` ("Your part") | `workflow.py`, `ScreenHeader` | Copy in section 5 |
| Open buttons on stage rows | `stages.py` / `StageRow` | Folder opens exist for scene pictures and results; add overviews and contact sheets |
| Pictures tab in Set up | `workflow.setting_sections` | Fields exist (`curation_render.*`, `scene_render.*`); only the card grouping and wording |
| Per-marker Find nuclei selector in channels table | `channel_editor.py` / Set up | Writes `acquisition.marker_segmentation` (already added in #30 follow-ups; confirm present) |
| Curator: **Hide stain** toggle | `04l_roi_curator.py` | **NEW.** Today only the nuclear layer has a toggle (`toggleDapi`). Needs a marker-layer visibility flag and redraw; label uses the current marker name |
| Curator: strip grouped by animal | `04l_roi_curator.py` | Today a flat strip; group headers and counts |
| Curator: shortcut hints in button labels | `04l_roi_curator.py` | Keys already exist; labels only. Keep `title` tooltips |
| Curator: coach line | `04l_roi_curator.py` | Pure presentation of existing mode state |
| Curator: shortcuts popup replacing footer | `04l_roi_curator.py` | Content from the existing footer |
| Curator: responsive layout | `04l_roi_curator.py` | Section 6.1 rules; replaces the fixed grid |

---

## 8. States and behaviours to implement exactly

### 8.1 Stage state resolution (order matters)
1. not used in this study → `notused`
2. running → `running`
3. queued → `queued`
4. done → `done`
5. prerequisite not met → `waiting` (text from the gate: ROI export, or detection)
6. otherwise `todo`

The unblind stage is never run by Run all remaining and always asks first.

### 8.2 Unblind confirmation
Modal (`alertdialog`), red top rule, title "Unblind the analysis?", text: "This stage reads the treatment groups for the first time. Everything before it was keyed by animal only." Field: **Sampling workbook (.xlsx)** with Choose… (maps to the `--xlsx` option added in #30). Note: "Do this once the regions and detection settings are final. Spreadsheets stay keyed by animal and can be rebuilt at any time." Buttons: Cancel / **Unblind and run** (danger fill). Focus starts on Cancel.

### 8.3 Apply on Set up
Apply validates the tab (`SettingsForm.apply()`), updates the project, marks the tab ✓, emits `applied`. The Next up card and step-list progress refresh from it.

### 8.4 Study picker
Radio-style list (name, path, one-line note; unreadable study shows "Output folder not found. Is the drive connected?"), New study…, Find a config file…, Cancel / Open study. The chosen name stays in the status bar.

### 8.5 Channel editor
Columns: Name, Role (Nuclear stain / Marker / Ignore), In the scan, Plane, Finds nuclei (derived: "Using the nuclear stain" or "Needs its own channel"). **Guess from a slide** reads the first slide's channel names and shows what it found. Warning when no nuclear stain exists. Remove (✕, labelled) and **+ Add channel**. Save channels / Cancel.

### 8.6 File-name builder
One real example name in a mono input; three cards (Animal, Slide, Replicate) fill live; red cards and "This file name does not fit the pattern. Every stage would ignore it." when it fails; shows the generated pattern with the note that the animal is the join key. Use this pattern / Cancel.

---

## 9. Accessibility

- Every control reachable by Tab with the visible focus ring; no hover-only actions.
- Real buttons, inputs with labels; icon-only buttons have an accessible name (the ⓘ "About this stage", ✕ "Remove channel", `−`/`+` zoom).
- Step-list current item exposes `aria-current="step"` (web) / current selection (Qt).
- Text contrast 4.5:1 minimum; the old `#8b949e` on white does not pass, `#59636e` does. Status never relies on colour alone: every dot has a text word beside it.
- Do not signal state with red/green alone: done = green plus "Done", failed = red plus "Failed last time".
- Curator canvases keep keyboard equivalents (arrows, `a f x d g s z u r`); the new rail buttons duplicate them, they do not replace them.

---

## 10. Verified, and not

**Checked in Chromium** (Playwright, 840 and 1360 px; the mockups' own runtime):
- Mockup pages load with no script errors.
- ROI curator: nothing clipped at 840 px; page scrolls; all button rows reachable; panes clamp to 360 px minimum.
- Main window: step list stays left at 840 px; Prepare run-through, Curate export and Set up Pictures tab click through without errors.

**Not checked**
- Anything in Qt; this is a design spec, no app code changed.
- Real curator data, real zoom/pan behaviour, Review with real masks.
- Dark theme; the tokens make it a swap but no dark values are specified here.
- Windows high-DPI and font fallback.

## 11. Not designed yet

| Area | Why |
|---|---|
| Results charts and figures | Results is still stage rows plus Open buttons; no visual summary of the numbers |
| Project history window, File menu dialogs | Left as the existing Qt dialogs |
| Instrument characterisation, All stages | Reuse the stage-list pattern; no bespoke design |
| Atlas plate reframe tool | Opens its own existing page |
| Import decisions flow | One-paragraph placeholder |
| Shotgun deck output | Disabled button only; deck itself unchanged |
| Open or unmerged branches (e.g. `quantify-the-marker`) | Not reviewed against this design |

## 12. Open questions

1. Is a **Hide stain** toggle also wanted in Review mode (where `k` already toggles the marker)? Current design reuses `revLayer('mark')` there.
2. Should the Set up **Pictures** tab live under Prepare instead, since its settings only affect Prepare stages?
3. Which shortcuts should the buttons without one (Rotate, No ROI here, Hide DAPI, Hide stain, Undo region, Clear section) get? None are currently bound.
4. Light theme for the whole app: is a dark option required later? It affects whether tokens are stored in `QSS` or `QPalette`.
