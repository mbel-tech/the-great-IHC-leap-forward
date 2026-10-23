# Guides update and technical guide: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move every guide into `docs/guides/`, bring the user guide, wiki and pipeline guide up to current main with fresh screenshots, and write a new two-part technical guide with code-generated reference tables and a drift test.

**Architecture:** The work is mostly Markdown. The only code is three pieces:

- an offscreen screenshot script for the app
- a table generator (`docs/make_technical_tables.py`) that splices tables between marker comments
- a drift test in the style of `tests/test_wiki.py` and `tests/test_stages.py`

It ships as three PRs, one after another, from the branch `claude/project-thread-b5rh37`. The branch is restarted from main after each merge, and no PR is stacked on another.

**Tech Stack:** Markdown, PySide6 (offscreen `QWidget.grab`), pandoc for the PDF, plain-script tests run by `tests/run.sh`.

**Spec:** `docs/superpowers/specs/2026-10-23-guides-and-technical-guide-design.md`

## Global Constraints

- **Location:** guides live in `docs/guides/` and pictures in `docs/guides/img/`. Build tools stay in `docs/`.
- **Pointers:** `docs/user-guide.md`, `docs/pipeline-guide.md` and `docs/pipeline-methods.md` remain as one-line pointer files.
- **Doc first:** the user guide's Claude Doc (https://claude.ai/code/artifact/3935b5bd-45c5-4a2c-8b13-005fa0d0e0df) is the source. Diff main's md against the Doc before editing, because other threads edit the md directly.
- **Screenshots:** use a made-up demo study. Never show real lab data, and never show the tutorial answer key, which is lab-only.
- **Technical guide format:** two parts (scientists, then programmers), Markdown plus a PDF, no Claude Doc.
- **Libraries:** for each one, give its role in the pipeline, the calls and settings we use, a plain explanation of its algorithm, a citation and the pinned version. No alternatives section.
- **Scope:** every file in `scripts/` gets a full entry. Every file the pipeline reads or writes gets every column or key, with type, units and meaning.
- **Never claim a count:** measured numbers stay in `pipeline-methods.md`, which `check_methods_claims.py` verifies.
- **Wording:** plain words, in the app's on-screen labels exactly as written in `app/workflow.py`.

## Review Focus

1. **An old Help link.** An installed v2.0.1 app opens `blob/main/docs/user-guide.md`. The pointer file must exist and link to `guides/user-guide.md` (Task 1 test).
2. **A wiki picture after the move.** `wiki.yml` must copy from `docs/guides/img/user-guide/`, and `test_wiki.py` must still resolve every `images/*.png` (Task 1).
3. **A guide image path relative to the new folder.** Every `![](...)` in the moved guides must still resolve on disk (Task 1 test `test_guide_images_resolve`).
4. **A stage added later without a technical-guide entry.** The drift test must fail on it (Task 9 test, checked in Task 10).
5. **A CSV column renamed in code.** The generated column table must differ, and the drift test must fail until the tables are regenerated (Task 9 test, checked in Task 10).

---

## PR A: one folder, updated user guide, wiki and pipeline guide

### Task 1: Move the guides to docs/guides/

**Files:**

- Move: `docs/user-guide.md`, `docs/pipeline-guide.md` and `docs/pipeline-methods.md` to `docs/guides/`
- Move: `docs/img/` to `docs/guides/img/`
- Create: pointer files `docs/user-guide.md`, `docs/pipeline-guide.md` and `docs/pipeline-methods.md`. Each holds a title line and "Moved to [guides/<name>.md](guides/<name>.md)."
- Modify these references to the new paths:
  - `app/main_window.py` (`GUIDE_URL` and `_open_guide` path)
  - `tests/test_workflow.py:921-931`
  - `tests/test_wiki.py:13-17`
  - `tests/test_stages.py` (`GUIDE` and line 335)
  - `.github/workflows/wiki.yml:43`
  - `docs/build_methods.sh` (render paths, cp)
  - `docs/check_methods_claims.py:26`
  - `docs/make_versions_table.py:109`
  - `docs/make_workflow_figure.py` (output dir)
  - `README.md`, `REVIEW.md`, `docs/wiki/*.md`
  - `scripts/04o_section_rgb.py:97` comment, `app/stages.py:4`, `app/workflow.py:234`
- Test: `tests/test_guides_folder.py` (new, plain-script style like `test_wiki.py`)

- [ ] **Step 1:** Write `tests/test_guides_folder.py` with these checks:
  - `test_pointers_exist`: each old path exists and contains `guides/<same name>`
  - `test_guide_images_resolve`: every `![..](path)` in `docs/guides/*.md` exists relative to that file
  - `test_no_stale_refs`: `git grep` finds no `docs/img/` and no `docs/(user|pipeline)-(guide|methods).md` outside the pointers, `docs/superpowers/` and `LOGS.md`
- [ ] **Step 2:** Run `python tests/test_guides_folder.py`. Expected: FAIL, because the pointers are missing.
- [ ] **Step 3:** Run `git mv` on the files, write the pointers, and update every reference listed above.
- [ ] **Step 4:** Run `python tests/test_guides_folder.py && python tests/test_wiki.py && python tests/test_stages.py && python tests/test_workflow.py`. Expected: all PASS. Under `QT_QPA_PLATFORM=offscreen`, test_workflow must pass the same as on main. Note any failures already present on main.
- [ ] **Step 5:** Add the new test to `tests/run.sh` and commit with the message "docs: move the guides into docs/guides/".

### Task 2: Gap audit (what the guides miss)

**Files:** Create the scratch note `/mnt/project-files/guides-work/gap-audit.md`. It is not committed.

- [ ] **Step 1:** List every merged PR from #116 to the latest on main, and every stage or label change since commit `3871b0e`. Use `git log` plus `git diff 3871b0e origin/main -- app scripts`.
- [ ] **Step 2:** Read the Claude Doc and diff it against `docs/guides/user-guide.md`. Note the paragraphs that exist only in the md, such as the 06i section that another thread added.
- [ ] **Step 3:** For each change, record which guide sections, which wiki pages and which pipeline-guide stage entries it touches. Also record which screenshots it makes stale.
- [ ] **Step 4:** Check the features named in the spec against the code: tutorial mode, the sampling sheet, 06i, crops during 05c, one-marker and multiplex studies, `--run`, in-app updates and the Mac app. If any of them is not in main, drop it from the guide and say so in the PR.

### Task 3: Screenshot script

**Files:**

- Create: `docs/guides/make_screenshots.py`
- Reuse: the demo-study fixtures in `tests/_fixture.py` and `tests/_tutorial_fixture.py`

**Interfaces:** Produces `capture(name: str, widget: QWidget, out_dir: str) -> str`, which returns the path of a PNG. The script's `main()` builds a made-up study and writes `docs/guides/img/user-guide/NN-name.png`.

- [ ] **Step 1:** Write the script. It sets `QT_QPA_PLATFORM=offscreen`, builds a demo study in a temp folder, opens `MainWindow`, steps through the screens, and calls `widget.grab().save()` at a fixed 1280x800 window size. It covers the 11 existing names, plus:
  - `12-tutorial.png`
  - `13-sheet-match.png`
  - `14-overlays.png`
  - `15-multiplex-setup.png`
  - `16-unblind.png`
- [ ] **Step 2:** Run `python docs/guides/make_screenshots.py --out /tmp/shots`. Expected: 16 PNGs. Read each image and check that it shows the intended screen with no real data.
- [ ] **Step 3:** Copy the PNGs to `docs/guides/img/user-guide/` and commit with the message "docs: screenshot script; retake the user guide pictures".

### Task 4: Update the user guide text

**Files:** Modify `docs/guides/user-guide.md`.

- [ ] **Step 1:** Work through the gap audit section by section:
  - §3 Tutorial
  - §4 Set up (sheet match, one-marker and multiplex)
  - §7 Measure (crops during 05c)
  - §8 Results (06i overlays, unblinding from the sheet)
  - §9 Running stages outside the app (`--run`)
  - §10 Troubleshooting
  - §11 Glossary
  - Quick start, if the flow changed

  Copy labels from `app/workflow.py` exactly.
- [ ] **Step 2:** Add the new images with alt text. Run `python tests/test_guides_folder.py`. Expected: PASS.
- [ ] **Step 3:** Commit with the message "docs(user guide): cover tutorial, sheet, overlays, multiplex, --run".

### Task 5: Sync the Claude Doc

- [ ] **Step 1:** Load the docs skill. Update the Claude Doc to match `docs/guides/user-guide.md`, and upload the 16 PNGs in place of the old ones.
- [ ] **Step 2:** Read the Doc back and check that every section heading matches the md.

### Task 6: Wiki

**Files:** Modify `docs/wiki/*.md`, as listed in the gap audit.

- [ ] **Step 1:** Bring the wiki pages in line with the guide, and reference the new images as `images/NN-name.png`.
- [ ] **Step 2:** Run `python tests/test_wiki.py`. Expected: PASS.
- [ ] **Step 3:** Commit with the message "docs(wiki): match the updated user guide".

### Task 7: Pipeline guide

**Files:** Modify `docs/guides/pipeline-guide.md`.

- [ ] **Step 1:** Add or update the stage entries for every stage that changed since `3871b0e`. These include 01l, 05b, 05c crops, 06i, the multiplex and one-marker branches, and `--run`. Each entry follows the existing "How to read a stage entry" shape.
- [ ] **Step 2:** Run `python tests/test_stages.py`. Expected: PASS. It holds the guide to the stage list.
- [ ] **Step 3:** Commit, push, and open draft PR A. Use the attribution block, the assignee and the reviewer, and subscribe to PR activity.

---

## PR B: technical guide, Part 1 (for scientists)

Start PR B after PR A merges. Restart the branch from main.

### Task 8: Part 1

**Files:** Create `docs/guides/technical-guide.md`, holding Part 1 plus the headings for Part 2.

- [ ] **Step 1:** Write chapters 1 to 3 from the spec: the data, the journey diagram, and stage by stage. Read each script before describing it, and never describe a stage from the old guides alone.
- [ ] **Step 2:** Write chapter 4, the libraries. Pin each version to the lines in `requirements*.txt` and the bundled installer's lock, and to `docs/software_versions.md`. Take citations from `REFERENCES.md` and add new ones only after checking them:
  - pylibCZIrw, czifile and imagecodecs
  - tifffile
  - Fiji and Bio-Formats
  - NumPy, SciPy and scikit-image
  - OpenCV and Pillow
  - StarDist and TensorFlow
  - itk-elastix
  - PyMuPDF and pytesseract
  - pandas, openpyxl and matplotlib
  - PySide6
  - the R packages: lmerTest, emmeans, multcomp, ggplot2, ggtext, officer and readxl
- [ ] **Step 3:** Check every function name or setting that the text quotes with `git grep`. Commit, push, and open draft PR B.

---

## PR C: technical guide, Part 2 (spec), tables, drift test, PDF

Start PR C after PR B merges. Restart the branch from main.

### Task 9: Table generator

**Files:**

- Create: `docs/make_technical_tables.py`
- Test: `tests/test_technical_guide.py`

**Interfaces:** Produces `build_tables() -> dict[str, str]`, which returns three Markdown tables, keyed `stages`, `options` and `files`. It also produces `splice(doc: str, tables: dict) -> str`, which replaces the text between `<!-- tables:<key> -->` and `<!-- /tables:<key> -->`.

- **Stages:** taken from `app/stages.py`.
- **Options:** each script's argparse parser, found by importing the script with its `main` replaced, or by calling its `build_parser()` where one exists. Scripts with no argparse are listed as such.
- **Files:** a hand-kept registry `docs/guides/file_formats.json`. Each file entry holds `path`, `writer`, `readers` and `columns`. Each column holds `name`, `type`, `units` and `meaning`. Its column lists are checked against the writer's code: the header list or DictWriter fieldnames found by grep.

- [ ] **Step 1:** Write the failing tests:
  - `test_tables_current` asserts `splice(doc, build_tables()) == doc`
  - `test_every_script_has_entry` asserts each `scripts/*` name appears as a `###` heading
  - `test_every_written_csv_registered` asserts each CSV name found in the writers is in `file_formats.json`
- [ ] **Step 2:** Run `python tests/test_technical_guide.py`. Expected: FAIL.
- [ ] **Step 3:** Implement the generator and the registry.
- [ ] **Step 4:** Run the test again. Expected: PASS. Add the test to `tests/run.sh` and commit.

### Task 10: Part 2 text

**Files:** Modify `docs/guides/technical-guide.md`.

- [ ] **Step 1:** Write chapters 5 to 8 and chapter 10 from the spec:
  - 5, architecture
  - 6, every script, under one `###` heading each with the fields the spec lists
  - 7, the curators' JavaScript
  - 8, the app
  - 10, tests and how to add a stage
- [ ] **Step 2:** Write chapter 9 from the generated `files` table, with prose around it.
- [ ] **Step 3:** Run `python tests/test_technical_guide.py`. Expected: PASS. Check the Review Focus items 4 and 5 by temporarily renaming a column and adding a fake script: the test must fail, then revert.

### Task 11: PDF and finish

**Files:** Modify `docs/build_methods.sh`, so that it renders `technical-guide` through the same `render()`, plus a PDF through LibreOffice as it already does.

- [ ] **Step 1:** Build the PDF here with pandoc and LibreOffice, and read pages 1, 10 and the last page to check them.
- [ ] **Step 2:** Commit `docs/guides/technical-guide.pdf`, push, and open draft PR C.
