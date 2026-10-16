# Choosing the sampling sheet's columns in Set up

Date: 2026-10-16. Builds on PR #116 ("Load the groups for Shotgun").

## What the user asked for

- The treatment sheet is loaded in **Set up**, because the Shotgun deck needs
  the groups while curating.
- In that step the user says, intuitively, **which column identifies the
  animal** and **which column(s) hold the treatment** to compare.
- The animal ID column is **automatically compared with the animal names parsed
  from the slide file names**, so mismatches are caught.
- Answers to the design questions (decision cards, 2026-10-16):
  1. The choice drives Shotgun, Unblind and the results, **but Unblind and the
     results only use it when that stage runs** (nothing is joined early).
  2. Columns are picked in a **sheet preview window**.
  3. IDs match **exactly, else by number** (`22` matches `LS22`).
  4. Two or more treatment columns are **combined into one group**
     (`exercise · 1`).
  5. A blank treatment is left out and listed, with an **optional fill-in rule**
     per study (this study keeps its tank rule).
  6. **All other columns** not marked Ignore are carried into the results.

## What it replaces

`ls_sampling.py` has this study's sheet built in: `COLUMN_PREFIX` (Fish ID,
treatment, brackish tank, `slicing IHC July 2025`, ...), `LSnn -> nn`
matching, and the tank sets that fill blank treatments. Another study's sheet
cannot be read at all.

## The saved choice: `sampling` in the study

```json
"sampling": {
  "id_column": "Fish ID",
  "group_columns": ["treatment"],
  "ignore_columns": [],
  "match": "auto",
  "fill": [
    {"when": "brackish Tank", "is": ["303", "304", "307", "308"], "set": "treatment", "to": "exercise"},
    {"when": "brackish Tank", "is": ["305", "306", "309", "310"], "set": "treatment", "to": "control"}
  ]
}
```

- Columns are named by their header text (whitespace and case ignored). A
  header renamed later is reported by name; nothing is guessed.
- `match`: `auto` = exact text first, then by number if exact matches nothing;
  `exact` and `number` force one. The check always says which one was used.
- A `fill` rule gives one blank treatment cell (`set`) a value (`to`) when
  another column (`when`) holds one of `is`; the first rule that fits wins.
  An animal is grouped only when every treatment cell has a value. Each animal
  so filled is marked `treatment_source = rule` in the results.
- The workbook path stays where it is today (06b's `--xlsx` option).
- **Studies with no `sampling` block:** if the sheet has this study's headers,
  the old mapping is used, tank rule included, so the laptop study's groups
  and sources do not change. The carried columns are now named from the
  headers (`brackish_tank`, `slicing_ihc_july_2025`), and the sheet's ID is
  `sheet_id` rather than `fish_id`; nothing downstream reads those names. Otherwise Check and Unblind stop with "choose the animal and
  treatment columns in Set up > Slides > Match the sheet...".
- `sampling` is in `ls_config.SPEC` (validated, exported with settings). It is
  not the unblinding key itself, so a blinded copy keeps it.

## The window: Set up > Slides > "Match the sheet..."

A button beside **Check workbook** opens it. From top to bottom:

1. **Sheet**: which worksheet, shown only if the workbook has more than one.
   Row 1 is the header.
2. **Preview table**: the first 20 rows. Above each column is a menu:
   **Animal ID**, **Treatment**, **Keep** (the default) or **Ignore**. Only one
   column can be Animal ID; choosing another moves it.
3. **Starting guess**: the column whose values match the most slide animals is
   pre-set as Animal ID. A column headed like "treatment" or "group" is
   pre-set as Treatment. Both can be changed.
4. **Slide check**, updated as soon as a menu changes:
   - "12 of 12 animals in the slide names have a row (matched by number)."
   - Slides with no row are listed in red. Saving is still allowed, but Unblind
     will stop on them, as it does today.
   - Sheet IDs with no slide are a count, with a "show" link. This is normal:
     most sheets list more animals than were imaged.
   - Duplicate IDs in the sheet are listed in red. That column cannot be saved
     as Animal ID until they are fixed.
5. **Groups**: counts only, such as "control 6 · exercise 6", plus the animals
   with no group.
6. **Blank treatment**: lists those animals, with **Add rule...** ("when
   [column] is [values], the group is [text]"). Rules are listed and removable.
7. **Save** writes the `sampling` block. **Cancel** changes nothing.

The animals come from the same place the current check reads them: the
measurements, else Prepare's manifest, else the slide file names with the
study's naming rule.

The preview shows the sheet as it is, since it is the user's own file. Blinding
is unchanged elsewhere: Set up's status line and the Shotgun confirmation give
counts per group, never which animal is in which group.

## What uses the choice, and when

| Where | When | What it reads |
|---|---|---|
| Match window, Check workbook | Set up, any time | the mapping, dry run, writes nothing |
| Load the groups for Shotgun | Set up, when pressed | `groups.by_animal` = combined group per animal |
| 06b Unblind | when it runs | the mapping; writes `animal_metadata.csv` and `roi_dataset.csv` |

- **06b output.** `animal_metadata.csv` has `animal`, `sheet_id`, `treatment`
  (the combined group, kept under this name so 06c and the R code still find
  it), `treatment_source` (`sheet` / `rule` / `unresolved`), then every Keep
  column under its header made safe for a CSV (lower case, `_` for spaces).
  `roi_dataset.csv` carries the same columns. The LS-only cohort check
  (`slicing IHC July 2025`) goes, replaced by the slide-name check. That
  column is carried like any other.
- **Unchanged:** 06c's `animal_environment()` still reads this study's
  `enviroment` column directly from the sheet, and the R figures still model
  environment by treatment. Making those generic is a separate job.

## Code

- `scripts/ls_sampling.py`: the generic reader (`sheets`, `read_table(path,
  sheet)`), `guess(headers, rows, animals)`, `match_ids(...)`,
  `join(animals, rows, mapping)` and `check(path, animals, mapping)`. The
  legacy mapping becomes `legacy_mapping(headers)`. `group_key` stays.
- `app/sheet_match.py` (new): the window, a `QDialog` with a `QTableWidget`
  and a `QComboBox` per column header. The check runs on the existing
  `WorkbookCheck` thread.
- `app/screens.py`: the "Match the sheet..." button. Check workbook and Load
  groups pass the saved mapping.
- `scripts/06b_join_sampling.py`: uses the mapping, and the generic columns
  as above.
- `scripts/ls_config.py`: the `sampling` key.

## Tests

- `test_join_metadata.py`: exact, by-number and auto matching; duplicate IDs
  refused; combined groups; fill rules and `treatment_source`; the legacy
  mapping gives the same `animal_metadata.csv` as today for a sheet with the
  LS headers; a renamed header is named in the error.
- `test_new_study.py`: the window opens on a workbook, guesses the ID column,
  updates the slide check when a menu changes, and saves the block. Load
  groups then uses the chosen columns.
- User guide section 4 and the wiki page describe the window.
