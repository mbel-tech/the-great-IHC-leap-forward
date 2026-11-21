# Running the steps from the command prompt

Every step the app runs from its Run buttons can also be run by typing a command, with no window open.
This is useful for:

- **Long or overnight runs:** start one step or a whole screen, then leave it.
- **Scripts:** a batch file that runs several steps in order and stops at the first failure.
- **Remote machines:** a computer reached over Remote Desktop or SSH, where you would rather not keep the window open.

The command line does exactly what the window does. It uses the options saved in the study, it holds back a
step the window would hold back, and it records every run in the study's run history. A run started
from the command prompt therefore shows up in the window afterwards like any other.

This guide covers Windows (installed app and from source) and the Mac. You need a study that already
exists: create it in the app with **File > New project...** first. The command line runs steps; it does
not set studies up.

---

## 1. The command for your computer

Everything in this guide is written as `ihc ...`. Replace `ihc` with the line that matches how you have
the software.

| You have | Type this in place of `ihc` |
|---|---|
| The installed Windows app, with **Add the ihc command to PATH** ticked during install | `ihc` |
| The installed Windows app, without that box ticked | `"%LOCALAPPDATA%\Programs\The Great IHC Leap Forward\ihc.cmd"` |
| A copy run from source (Windows, Mac or Linux) | `python -m app`, typed in the repository folder |
| The Mac app | `"/Applications/The Great IHC Leap Forward.app/Contents/MacOS/The Great IHC Leap Forward"` |

Notes:

- **Opening a command prompt on Windows:** press the Windows key, type `cmd` and press Enter. On the Mac, open
  **Terminal**.
- **Ticking the box later:** run the installer again and tick *Add the ihc command to PATH*. Open a *new*
  command prompt afterwards; one that was already open does not see the change.
- **If the app updated itself from inside:** it writes `ihc.cmd` the next time it starts. Open the app once
  before you use the command.
- **Paths with spaces:** put the study path in double quotes, for example
  `"D:\My studies\pERK 2026\study.json"`. Without quotes the command reads only the part before the first space.

**Which file is "the study"?** It is `study.json` in the study's output folder, the folder you chose as
*output folder* when you made the study. Every command takes its full path. The examples below use
`D:\LS-analysis\study.json`.

---

## 2. Look before you run: `--list`

```bat
ihc --list D:\LS-analysis\study.json
```

`--list` prints one line per step with four columns: the step's **id**, its **group**, its **title** as the
window shows it, and its **state**. The id is the short name you type to run a step. For example:

```text
manifest          Ingest and QC   00  Read slide headers                    done
overviews         Ingest and QC   01  Export section overviews              out of date
reagent_screen    Ingest and QC   01m  Screen for reagent controls          blocked: overviews - Needs Export section overviews to finish first
pair              Ingest and QC   02  Pair the two marker passes            not for this study
detect            Quantification  05c  Detect and measure nuclei            not run
```

What the states mean:

| State | Meaning |
|---|---|
| `done` | Ran and finished. Running it again skips what is done unless you ask for a redo. |
| `out of date` | Ran, but something it reads has changed since. Running it redoes it. |
| `not run` | Ready to run. |
| `blocked: <reason>` | The window would hold this step back. The reason says what it is waiting for: an earlier step, or your export from a curator. |
| `not for this study` | This study's layout does not use the step. |
| `cli only: <reason>` | The app does not run this step. The reason says how to run it instead (section 9). |

`--list` changes nothing, so you can run it as often as you like.

---

## 3. Run one step: `--stage`

```bat
ihc --stage overviews D:\LS-analysis\study.json
```

This runs **01 Export section overviews** exactly as its Run button would:

- with the options saved in the study (Set up, or the step's options in the window);
- redoing it from scratch if it is out of date;
- printing its progress as it goes;
- recording the run in the run history when it ends.

The output finishes with one of these lines:

```text
--- 01  Export section overviews: done in 412s
--- 01  Export section overviews: FAILED after 37s
```

followed, for a failure, by the reason.

### Options: `--opt name=value`

Most steps have a few options, the same ones the window shows next to the Run button. Give any of them
with `--opt`, as many times as you need. An option given here applies to this run only and is not saved
in the study.

```bat
ihc --stage overviews D:\LS-analysis\study.json --opt limit=3
ihc --stage detect D:\LS-analysis\study.json --opt order=uid --opt qc=true
```

- **On/off options:** use `true` or `false` (`yes`/`no` and `1`/`0` work too).
- **Number options:** use a whole number.
- **Unknown names:** an option a step does not have is refused, and the message lists the options it does have.

Options you are likely to use:

| Step | Option | What it does |
|---|---|---|
| `overviews`, `scene_renders`, `detect`, `detection_overlays` | `limit=N` | Do only the first N sections or scenes: a quick trial before a long run |
| `overviews`, `scene_renders`, `detect`, `rgb_perk`, `detection_overlays` | `force=true` | Redo work already done, not only what is missing |
| `detect` | `order=balanced` or `order=uid` | The order sections are measured in (balanced by default; `uid` is fully blind) |
| `detect` | `qc=true` | Also save check pictures |
| `tune_detection` | `sections=N`, `dry-run=true` | How many sections to sample; report only |
| `roi_geometry` | `all-sections=true` | Place regions on every section that has one |
| `reagent_screen` | `reread=true` | Read every section again |
| `join_sampling` | `xlsx=D:\path\sampling.xlsx` | The sampling spreadsheet to join |

### `--force` is not the same as `--opt force=true`

Both look alike, but they mean different things.

- **`--opt force=true`** tells the step to *redo work it has already done*. It is the step's own **Redo**
  option.
- **`--force`**, on its own, tells the command to *run a step the window would hold back*, for example one
  whose earlier steps are not finished. Use it only when you know why the step is waiting and that it is
  safe to go ahead.

`--force` does not override everything. These are refused whatever you add:

- a step that is **not for this study**;
- a step the app does not run (`cli only`);
- a **curator**. A curator is a page you work in, not a step to run, so the command prints the page's path
  for you to open in a browser:

  ```text
  04l  ROI curator is a page you work in, not a stage to run. Open it in a browser: D:\LS-analysis\reformatted\roi_curator.html
  ```

- **06b Join the experiment** unless you also add `--unblind`. This is the step that unblinds the study, so
  it never runs without you saying so explicitly:

  ```bat
  ihc --stage join_sampling D:\LS-analysis\study.json --unblind
  ```

---

## 4. Run a whole screen: `--run`

```bat
ihc --run prepare D:\LS-analysis\study.json
```

This is the window's **Run all remaining** for one screen. The screens are `prepare`, `curate`, `measure`
and `results`. The command:

1. prints the steps it is going to run, and any it is holding back with the reason;
2. runs them one at a time, in order;
3. stops at the first failure.

It also stops where the window would ask you something first, such as the weak-signal check before
detection. Unblinding is never part of a screen's run.

```text
4 stage(s) to run: manifest, overviews, contactsheets, scene_renders
  held: roi_curator waits for Order the ROI queue
=== [1/4] 00  Read slide headers
...
```

---

## 5. When the drive drops

Slides are large and usually sit on an external drive, and a USB drive can drop out for a few seconds or
longer. The steps are built for this:

- **The step waits.** It does not fail and move on. A step reading slides, or writing to the output folder,
  pauses where it is. There is no time limit: it waits as long as it takes.
- **You see one line when it starts waiting:**

  ```text
  slides drive unreachable (E:\Slides), paused at LS53_1a.czi; waiting, Ctrl+C to stop
  ```

- **When the drive comes back, it carries on by itself:**

  ```text
  drive back after 74 s, carrying on
  ```

  It redoes the slide or section it was on from its start. Nothing read before the drop is mixed with
  what is read after, so results are the same as if the drive had never dropped.
- **The run history notes each pause** (when, for how long and where), so you can see later that a run
  was interrupted.

If the drive comes back under a **different drive letter**, the step cannot find it and keeps waiting.
Stop it with Ctrl+C, change the folders in the app's Set up, and run the step again. Work already
finished is skipped.

The same happens in the window, with an amber banner and a notification instead of the lines above.

---

## 6. Stopping a run

Press **Ctrl+C** in the command prompt.

- During a wait for the drive, the step stops at once and is recorded as *stopped while the drive was
  disconnected*.
- At any other time, the step stops and is recorded as failed. Windows may ask *Terminate batch job (Y/N)?*.
  Answer `Y`.

Stopping is safe. Every step writes its results so that a half-written file is never kept. Run the step
again later and it picks up where it left off, skipping what is done.

---

## 7. In scripts: exit codes

Every command ends with a number a script can test.

| Exit code | Meaning |
|---|---|
| `0` | It ran: the step finished, or every step of the screen did. `--list` always ends 0. |
| `1` | The step failed, was stopped during a drive wait, or a screen's run stopped at a failure or a question. |
| `2` | Nothing was run: a step was refused (with the reason printed), or the command or study path was wrong. |
| `130` | You pressed Ctrl+C. |

A batch file for an overnight run might look like this. Because `ihc` is itself a batch file, call it with
`call`. Without `call`, the script ends after the first line.

```bat
@echo off
set STUDY=D:\LS-analysis\study.json

call ihc --stage overviews "%STUDY%"        || goto failed
call ihc --stage contactsheets "%STUDY%"    || goto failed
call ihc --stage scene_renders "%STUDY%"    || goto failed
echo All done.
goto :eof

:failed
echo A step did not finish (exit code %errorlevel%). See the lines above.
exit /b 1
```

To keep a copy of everything a run prints, send it to a file:

```bat
call ihc --run measure "%STUDY%" > D:\LS-analysis\measure-log.txt 2>&1
```

---

## 8. Seeing what ran

Every run started from the command prompt is recorded in the study, just as runs from the window are:

- **In the window:** open **File > Project history...** to see each run with
  its start and end, whether it worked, the options it used, and any pauses for the drive.
- **From the command prompt:** `ihc --list` shows the effect, with steps you have run now showing `done`.

---

## 9. Steps that the command line does not run

`--list` marks these `cli only`, with the way to run them:

- **01b Export the sample (Fiji):** a one-time instrument characterisation done in Fiji. See
  `scripts/run_all.sh`, steps 3 to 8.
- **06e Refresh datasets and figures hourly:** a loop that keeps running for hours. Start it with
  `refresh_loop.bat` instead. **06e Build results** (`build_results`) is the one-off version, and runs
  normally.
- **05c Detect and measure nuclei,** but only in a copy whose Python has no StarDist. The installed app
  includes it. From source, the message says which Python to use.

The curators (**04l ROI curator**, **04k Level curator**) are pages you work in, in a browser. The command
gives you the page's path. Importing a curator's export (**04q**) needs the window, which hands the export
over to the open curator.

---

## 10. Quick reference

```bat
ihc --help                                         the commands, in short
ihc --list   STUDY                                 every step and its state
ihc --stage  ID STUDY [--opt name=value]...        run one step
ihc --stage  ID STUDY --force                      ...even though the window would hold it back
ihc --stage  join_sampling STUDY --unblind         the unblinding step
ihc --run    prepare|curate|measure|results STUDY  run all remaining on a screen
```

Step ids by screen (`--list` shows which apply to your study):

| Screen | Step ids |
|---|---|
| Prepare | `manifest`, `verify`, `czi_selftest`, `overviews`, `reagent_screen`, `contactsheets`, `scene_renders`, `pair`, `channel_identity`, `saturation_raw`, `saturation_map`, `artifact_perk`, `artifact_pcna`, `reformat_pcna`, `exclusion`, `censor_perk`, `censor_pcna`, `propagate_perk`, `reformat_perk_final`, `reformat_pcna_final`, `sections_dataset`, `worklist`, `rgb_perk`, `rgb_pcna`, `provenance`, and the atlas steps (`atlas_*`, only when rebuilding a plate set) |
| Curate | `roi_curator` (a page), `import_curation` (window only), `atlas_reframe` |
| Measure | `roi_geometry_verify`, `roi_geometry`, `tune_detection`, `detect`, `roi_dataset`, and the repairs `recensor`, `off_tissue`, `seg_provenance`, `roi_area` |
| Results | `join_sampling` (unblinds), `build_results`, `excel_sample`, `excel_slide`, `detection_overlays` |

The `_perk`/`_pcna` ids are the study's first and second marker. `--list` shows them under your own
marker names.

---

## 11. Troubleshooting

**"'ihc' is not recognized as an internal or external command"**
: The install folder is not on your Path. Use the full path from section 1, or run the installer again
  with *Add the ihc command to PATH* ticked and open a new command prompt.

**"no stage called '...'"**
: The id is mistyped. `ihc --list STUDY` shows the ids. They are the short names in the first column,
  not the titles.

**"... is waiting for ...: Needs ... to finish first. Add --force to run it anyway."**
: An earlier step has not finished. Run that one first. Use `--force` only if you know it is safe.

**"no study file at ..."**
: The path to `study.json` is wrong, or has spaces and no quotes. If the study is on a drive that is not
  plugged in, plug it in first.

**The batch file stops after the first `ihc` line**
: Write `call ihc ...` instead of `ihc ...` (section 7).

**It has said "slides drive unreachable" for a long time**
: The drive is still disconnected, or it came back under another letter. Check in File Explorer. If the
  letter changed, press Ctrl+C, fix the folders in the app's Set up, and run the step again.

**Odd characters or a step failing straight away on the Mac**
: Run the commands from the app's `Resources` folder as shown in `packaging/macos/README.md`, or use the
  launcher path from section 1.
