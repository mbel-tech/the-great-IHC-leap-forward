# Install and first run

## Install on Windows

1. Open the [latest release](https://github.com/mbel-tech/the-great-IHC-leap-forward/releases/latest) and, under *Assets*, download `The-Great-IHC-Leap-Forward-Setup-<version>.exe`.
2. Run it. It installs for your user only and needs no administrator rights.
3. Start **The Great IHC Leap Forward** from the Start menu.

The installer brings everything with it: Python and every library, StarDist and its model (so Measure works offline), and R. It needs Windows 10 or 11, 64-bit. Nucleus detection runs on the CPU.

**Upgrading.** Install a newer version over the old one. Your studies and settings live in `%USERPROFILE%\.ls-pipeline` and are kept through upgrades and uninstalling.

**Checking an install.** In the install folder, run `runtime\python.exe -m app --self-test`. It opens the window, starts the local server, loads a curator, and reports PASS or FAIL for each check in `lsapp-selftest.log`.

To run from the code instead of the installer, see [Running from source](Running-from-Source).

## What you need besides the app

- **Your slides**: the `.czi` files, in one folder or picked one by one. The app records where they are and never moves them.
- **Space for the output.** Every result is written under one *Analysis output* folder. The overviews alone take several GB.
- **The sampling workbook**: the spreadsheet that says which animal is in which treatment group. It is read only at the very end, when you unblind.
- **Fiji**, only if you want to draw your own atlas (see [Adding your own atlas](Adding-Your-Own-Atlas)).

## The first time you open it

The window has a step list down the left with six screens, in the order you work: **Project**, **Set up**, **Prepare**, **Curate**, **Measure** and **Results**. Each shows how far it has got, read from the output folder, so it is right even for work done outside the app.

1. Use **File > New project** and give the study a name. A project is one study in one file: its settings, every decision you make in a curator, and a record of each stage run.
2. Go to **Set up** and follow [Setting up a study](Setting-Up-a-Study).
3. Come back to the **Project** screen whenever you are unsure what to do next. Its **Next up** card names the one thing to do now; press **Go**.

![The Project screen: the study strip, the Next up card with Go, progress for each step and recent activity](images/01-project.png)

## Finding your way around

- **Your part.** Every screen opens with a blue line that says what you do there. Usually it is one thing.
- **(i)** on every screen, stage and setting explains it in plain words: what a stage needs first, what it leaves behind, when it last ran, and the technical detail underneath.
- **Stage rows** have a status dot (green done, grey to do, purple waiting for you, blue running, red failed, amber waiting or out of date), the stage's name in plain words, a **Run** button (which says Redo once done, and Build page on a curator), **Open**, and **Options**. The stage's number and the pipeline's own name are not on the row: hold the pointer over the name to see them (for example `00d  CZI self-test`), or open *All stages*, which shows both. A stage whose last complete run took a minute or more says how long, for example *Last run took 3 h 12 min*.
- **Run all remaining** on Prepare, Measure and Results runs every unfinished stage on that screen, in order. It stops at the first failure, never runs past a curator you have not exported, and never unblinds.
- **The status bar** at the bottom shows what is running, with a progress bar. Once a stage has been counting for a little while it adds the time left, for example *12 of 454 sections (3%), about 2 h 10 min left*. **Show log** opens the full log; **Stop after this stage** finishes cleanly and stops.

![The step list: six steps with their descriptions and progress, and the Advanced group at the foot](images/10-step-list.png)

### Menus

| Menu | Item | What it does |
| --- | --- | --- |
| File | New, Open, Open Recent, Save, Save As, Revert | Work with the project file |
| File | Project history | Every stage that ran in this project, and when |
| File | Export a copy | A copy of the project to share or archive |
| View | One item per screen, All stages, Refresh status | Jump to a screen; see every stage in one list; re-read progress from disk |
| Tools | Instrument characterisation | The once-per-microscope stages |
| Tools | All settings | Every setting in one dialog |
| Tools | Switch study | Move to another study's project |
| Tools | Migrate browser curation | Bring in curation done earlier in a web browser |
| Help | User guide, Wiki | The full guide, and these pages |
| Help | Tutorial… | Start or continue the tutorial on the demo study (below) |
| Help | Show the activity log, Open the log folder | The full log, and the folder holding `lsapp-crash.log` |
| Help | Check for updates, Undo last update, About | Install a newer version, go back one, see which version this is |

## The tutorial

The tutorial walks you through a whole study on a small demo study: a few sections cut from a real slide, at full resolution. Press **Start the tutorial** on the Project screen, or **Help > Tutorial…**; the first time the app opens it also offers it once. Your own study is closed while it runs and reopened when you leave, and the tutorial study lives in its own folder, so your studies are never touched.

- A panel on the right lists the 18 steps and says what to do; an orange box points at the button to press. **Next** stays greyed out until the step is done; **Skip** and **Back** move without doing it.
- On the slow steps (scene pictures, composites, nucleus detection) **Use ready-made results** copies in results made earlier from the same slide, so you need not wait.
- In the ROI curator you outline the regions on two practice sections yourself. The tutorial then compares your outlines with an expert's and draws the expert's dashed on the section; **Try again** or **Carry on**.
- **Leave tutorial** reopens your study; the Project screen then offers **Continue the tutorial**, and **Start over** makes a fresh tutorial study.
- **The demo study** is looked for in a `demo` folder beside the app; otherwise the app asks you to choose the demo pack (a folder or a `.zip`). The pack holds real, unpublished sections, so it is not part of the app's download: it is shared privately within the lab. Ask whoever set up the app for the link, download the `.zip`, and choose it as it is.

![The tutorial: the panel on the right lists the steps and says what to do, and an orange box points at the button to press](images/12-tutorial.png)

## Three habits that save trouble

- **Press (i) whenever you are unsure.**
- **Always export from a curator.** Your work is saved as you go, but the next stages only see it after Export.
- **Re-running is safe.** Stages skip finished work, so running one again only fills the gaps.
