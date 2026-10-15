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

## Finding your way around

- **Your part.** Every screen opens with a blue line that says what you do there. Usually it is one thing.
- **(i)** on every screen, stage and setting explains it in plain words: what a stage needs first, what it leaves behind, when it last ran, and the technical detail underneath.
- **Stage rows** have a status dot (green done, grey to do, purple waiting for you, blue running, red failed, amber waiting), a **Run** button (which says Redo once done, and Build page on a curator), **Open**, and **Options**.
- **Run all remaining** on Prepare, Measure and Results runs every unfinished stage on that screen, in order. It stops at the first failure, never runs past a curator you have not exported, and never unblinds.
- **The status bar** at the bottom shows what is running. **Show log** opens the full log; **Stop after this stage** finishes cleanly and stops.

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

## Three habits that save trouble

- **Press (i) whenever you are unsure.**
- **Always export from a curator.** Your work is saved as you go, but the next stages only see it after Export.
- **Re-running is safe.** Stages skip finished work, so running one again only fills the gaps.
