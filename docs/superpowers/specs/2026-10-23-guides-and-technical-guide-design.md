# Guides update and technical guide: design

Approved 23 October 2026 (decision cards in the project thread).

## What you get

| Document | What changes |
|---|---|
| User guide (Claude Doc, `docs/user-guide.md`) | Full update to current main. Covers everything merged since 16 October (#116 to #127). All 11 screenshots are retaken, and new ones are added |
| Wiki (`docs/wiki/`) | Brought into line with the updated user guide |
| Pipeline guide (`docs/pipeline-guide.md`) | Stage entries added or changed for 01l, 05b, 06i, the crops drawn during 05c, one-marker and multiplex studies, and `--run` |
| **Technical guide (new)** | `docs/guides/technical-guide.md` plus a built PDF |

What the user guide update adds:

- Tutorial mode and the demo pack (the answer key stays lab-only)
- The sampling sheet, matched in Set up
- Detection overlays (06i) and the crops drawn during 05c
- One-marker and multiplex studies
- Headless `--run`
- In-app updates and the Mac app, checked against what is merged

The new screenshots are: the tutorial bubble, the sheet match window, an overlay picture, multiplex setup, and the unblinding step.

## One folder for the guides

All four guides move to `docs/guides/`. That means `user-guide.md`, `pipeline-guide.md`, `pipeline-methods.md` and the new `technical-guide.md`. Their pictures move to `docs/guides/img/`, and the built PDFs go in the same folder.

The build tools stay in `docs/`, with their paths updated:

- `build_methods.sh`
- `check_methods_claims.py`
- `make_versions_table.py`
- `make_workflow_figure.py`

Each old path keeps a one-line pointer `.md`, because installed apps open `docs/user-guide.md` on GitHub from Help. Every reference is updated to the new folder:

- the app's Help (`GUIDE_URL` and the local path)
- README and REVIEW
- the wiki pages and `wiki.yml`
- `tests/test_wiki.py`, `tests/test_workflow.py` and `tests/test_stages.py`
- `04o`'s comment and `app/stages.py`'s docstring

The ROI curator guide stays where it is. This move is decided on the user's card from 23 October.

## Technical guide outline

### Part 1: How a slide becomes numbers (for scientists)

1. **The data.** What a CZI file holds: scenes, tiles, the pyramid levels, channels, JPEG-XR compression, and pixel size.
2. **The journey.** One diagram from CZI to workbook, and the three rules the pipeline never breaks.
3. **Stage by stage.** What each stage group does to the pixels and the numbers, in order: ingest and QC, instrument characterisation, atlas, normalisation and censoring, curation, geometry, detection and measurement, results and statistics.
4. **Libraries and how they work.** Each library gets the same four things: which stages use it, which calls we make and with what settings, a plain explanation of the algorithm inside, and a citation with the pinned version.
   - **Image reading:** pylibCZIrw, czifile and imagecodecs, tifffile, Fiji/Bio-Formats (Groovy scripts)
   - **Image maths:** NumPy, SciPy, scikit-image (thresholds, morphology), OpenCV, Pillow
   - **Nucleus detection:** StarDist on TensorFlow (star-convex polygons, non-maximum suppression, the pretrained model)
   - **Registration:** itk-elastix (affine and B-spline transforms, mutual information)
   - **Atlas PDFs:** PyMuPDF and pytesseract (OCR)
   - **Tables and output:** pandas, openpyxl, matplotlib
   - **The app:** PySide6
   - **R:** lmerTest, emmeans, multcomp, ggplot2, ggtext, officer, readxl (mixed models, contrasts, figures)

### Part 2: Technical spec (for programmers)

5. **Architecture.**
   - How `app/` and `scripts/` divide the work
   - The `stages.py` run order, the runner subprocess and its exit codes
   - Headless `--run`, the project and `study.json`, the `ls_config` keys, and `ls_paths`
   - Atomic writes, done and stale rules, fingerprints, and progress reporting
6. **Every script in full.** All 87 files in `scripts/`. Each entry gives:
   - its purpose
   - its command-line options
   - what it reads and writes
   - its algorithm, step by step
   - its settings and defaults
   - how it fails and how to re-run it safely

   This includes the Tools, the repair scripts (06f/06g/06h), atlas building, the Fiji import, the demo pack, and the shared `ls_*` modules.
7. **The browser curators.** The 04l ROI curator and the 04k level curator: their JavaScript structure, the export format, and the JS tests.
8. **The app.**
   - The screens and workflow model
   - The tutorial engine
   - Updates and the installer/.dmg build
   - The crash log
9. **File formats.** Every file the pipeline writes or reads. For each file: its path, which stage writes it, which stages read it, and every column or key with its type, units and meaning.
10. **Tests.** What `tests/run.sh` covers, and how to add a stage (a checklist).

### Kept current

`docs/make_technical_tables.py` generates three tables from the code: the stage list, each script's options (from argparse), and the file columns. The guide holds these between marker comments. `tests/test_technical_guide.py` fails when the guide and the code disagree, the same idea as `check_methods_claims.py`. The PDF is built with pandoc, like `build_methods.sh`.

## How it ships

There are three PRs, so each one can be reviewed on its own:

- **A.** The move to `docs/guides/`, then the user guide, wiki and pipeline guide.
- **B.** Technical guide Part 1.
- **C.** Technical guide Part 2, the table generator and the drift test, and the PDF.

The methods doc (`pipeline-methods.md`) is linked from the technical guide, not duplicated.
