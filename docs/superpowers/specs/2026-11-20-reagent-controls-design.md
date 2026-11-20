# Reagent controls: declare, identify, keep out, measure and report

Status: design, awaiting approval. Date: 2026-11-20.

## Why

Some studies run through the pipeline include **negative reagent control** slides or sections:
no primary antibody, secondary only, isotype / non-immune IgG, pre-adsorbed antibody, or
unstained. Today the pipeline has no notion of them, so they are treated as ordinary
sections. That means four things go wrong:

- they enter `qc/display_ranges.json`;
- they enter the 05b tuning sample;
- they enter the group datasets in 06a-06c, as an animal with very low signal;
- they stop 06b when their animal has no row in the sampling workbook.

The pipeline also cannot use them for what they are for. `README.md` ("Limitations"),
`docs/wiki/Results.md` and `06c` all say that without no-primary controls absolute positivity
is not defensible. A study that has controls should get that statement replaced by a number.

The operator decided:

- Approach 1 (below).
- Controls are **excluded from the experimental data AND measured and reported**.

## Terminology (fixed)

"Control" already means the control *treatment group* (`ls_sampling.py:52`, 05c/06c wording).
This feature therefore uses **reagent control** everywhere: UI text, config, columns and file
names. It never uses bare "control" or "negative".

The kinds of reagent control follow Burry 2011 (doi:10.1369/jhc.2010.956920), Hewitt et al. 2014
(doi:10.1369/0022155414545224) and Torlakovic et al. 2014 (doi:10.1097/PAI.0000000000000069):

| kind | what was left out | can signal find it? |
|---|---|---|
| `no_primary` (incl. secondary-only) | primary antibody | yes: specific pattern absent |
| `unstained` | primary and secondary | yes |
| `isotype` | specific primary, replaced by non-immune IgG | no: looks like a weak real stain |
| `preadsorbed` | antibody pre-blocked with peptide | no |
| `other` | operator's description | no |

Negative *tissue* controls (knockout / non-expressing tissue) are out of scope. They are
experimental tissue stained normally.

## Goals

1. At study creation, the operator states whether the slide folder holds reagent controls:
   **No** (default), **Yes**, or **Not sure, check for me**. The same choice is reachable
   later in Set up > Slides.
2. File and folder names are scanned against a tiered pattern set on every answer. The operator
   confirms each match, its kind, and which markers it controls.
3. With **Not sure**, or **Yes** with no name matches, a signal screen proposes a **ranked list of
   candidate sections** for the operator to confirm. It never assigns on its own.
4. Confirmed reagent controls are excluded from every experimental output and from every
   dataset-wide estimate. They are still curated and detected like any section.
5. A new results table, plus a sheet in the 06c workbook, reports what the positivity rule calls
   on reagent controls, per marker and region, beside the matched stained sections.

## Non-goals

- Auto-classifying anything without operator confirmation.
- Finding isotype or pre-adsorbed controls by signal.
- Subtracting control signal from experimental measurements (background correction). The report
  gives the numbers; using them to correct is a later decision.
- Positive controls.

## Anti-goals (a failure even if it works)

- The screen flags a batch rather than controls. `LOGS.md` (2026-08-12) records a ~2× AF568
  background split between animal batches, and inverted contrast on 93% of sections. **Any
  feature based on absolute level or tissue/background contrast is forbidden.**
- A reagent-control flag leaks group information, or a stage before 06b reads the group key to
  handle controls.
- A reagent control silently disappears: unreported, unmeasured, or dropped by the manifest
  because its name does not fit the grammar.
- The scene uid changes shape, or a control's flag lives inside the uid.
- Any text calls a background disc a negative control. The existing statements stay true.

## Constraints from the codebase

- `ls_naming.parse_name` matches the **basename** only, and returns None for a name without a
  subject. `00_manifest.py:688` reports such a file as a failure. Control files are often named
  `NC_3.czi` with no subject.
- The scene uid is `SUBJECT[_sNNx]_scNN` and must not change (`ls_naming.py` docstring).
- LS scans one marker per file (`LS53_2b-_A568.czi`). Other studies can be multiplex, where a
  slide can omit only one primary. **So a flag is per (file or scene) × marker.**
- A no-primary section may sit on the same slide as stained sections (one section with the
  primary omitted). **So flags are stored per scene.** A file-level flag is shorthand that
  expands to all its scenes.
- The trailing `-` in `LS85_7b-` marks a re-scan (`02_pair_passes.py:122`). It is not a control
  marker.
- New config keys are declared once in `scripts/ls_config.py` `SPEC`. `config.example.json` and
  `docs/config-reference.md` are then regenerated, and `tests/test_config_example.py` enforces
  this.
- The per-section positivity cut is median + 3 × 1.4826 × MAD of the section's own background-disc
  nuclei (`ls_overlay.py:92-132`), applied in `06a`.
- `segment: own` markers (PCNA on LS) segment on the marker channel itself. 06a withholds
  fraction-positive for them and reports positive density instead (05c docstring).
- Stages before 06b must not read the group key (`05b:22`, `05c:1680`).
- The sampling workbook is per animal (`ls_sampling.join`). 06b stops on an animal with no row
  (`06b_join_sampling.py:116`).

## Approaches considered

1. **Name patterns, plus a ranked signal screen the operator confirms.** Chosen. Names catch every
   kind cheaply and with no false positives on real data. Signal catches unlabelled no-primary and
   unstained sections, which names cannot.
2. **Name patterns, plus a hand-tick list.** Rejected. "Not sure" would mean the operator scrolls
   contact sheets with no help.
3. **Drop controls at 06b.** Rejected. Controls would still contaminate display ranges, the tuning
   sample and the measurement order, and nothing would be reported.

## Design

### 1. Config: `reagent_controls` (new SPEC key)

```json
"reagent_controls": {
  "declared": "no | yes | unsure",
  "scenes": {
    "<scene_uid>": {"kind": "no_primary", "markers": ["pERK"], "source": "name|signal|manual",
                    "pattern": "<matched token or null>", "note": ""}
  },
  "files": {
    "<file basename>": {"kind": "...", "markers": [...], "source": "...",
                        "subject": "LS45", "slide": 3, "replicate": "nc"}
  },
  "paired_with": {"<scene_uid or file>": ["<stained scene_uid or file>", "..."]},
  "rejected": ["<scene_uid or file the operator said is NOT a control>"]
}
```

- `declared` records the operator's answer and nothing else. Behaviour depends on the confirmed
  entries.
- `markers: []` is read as all markers.
- `files` entries expand to scenes when the manifest is built. A scene entry overrides its file's
  entry.
- `files.<name>.subject` / `slide` / `replicate` give a control file whose name does not parse
  the parts its uid needs. `subject` is the **donor animal's real ID** when the operator knows it,
  which lets pairing rule 2 work. Otherwise it is a made-up ID such as `RC1`. `slide` is required
  and `replicate` is optional, so two control files from one animal cannot collide in
  `duplicate_uids`. The confirm list fills these from the name where it can.
- Old wording kept for reference: the subject field gives an ID to a control file whose name does not parse. The manifest uses
  it instead of reporting the file as a failure, and the uid stays the standard shape. It must
  satisfy the subject rules (no `_`).
- `rejected` stops the name scan and the screen from proposing the same item again.
- Written with `by="reagent controls"` provenance, like other settings.

The flag is a property of how a section was stained, not of the animal or the treatment.
**It is not blinding information.** Stages before 06b may read it.

### 2. Name scan (`scripts/ls_reagent_controls.py`, new)

- **Input:** the path relative to the slides folder, so folder names count (`Negative controls/x.czi`).
  Matching is case-insensitive, and every token is bounded by `^ $ space _ - . ( ) / \`. It must
  not use `\b`, because `_` is a word character.
- **Strong tiers.** A match is proposed with its kind and pre-ticked in the confirm list:
  - `no_primary`: `no[ _-.]?(primary|prim|1st|1ary|1°|pAb|1Ab)`, `(w/o|w-o|wo|without)[ _-.]?(primary|1st|1ary|pAb)`,
    `minus[ _-.]?primary`, `-1Ab`, `-1°`, `omit(ted)?[ _-.]?primary`, `primary[ _-.]?omi(tted|ssion)`,
    `(senza|sans|ohne|uten|sin)[ _-.]?(prim…|1…)`, `noPrimary`.
  - **marker-specific `no_primary`**: `(no|w/o|minus|without|senza|ohne|uten)[ _-.]?<marker>`, built from the
    study's marker names (e.g. `no-pERK`). The control applies to that marker only.
  - `secondary_only` (stored as `no_primary`): `(sec|secondary|2nd|2ary|2°|2Ab)[ _-.]?only`, `only[ _-.]?(sec|2nd)`.
  - generic negative control (kind left for the operator, default `no_primary`):
    `neg(ative)?[ _-.]?(ctrl|ctl|control|controls|kontrolle|kontroll|controllo)`, `controllo[ _-.]?negativo`,
    `negativ[ _-.]?kontroll`, `negativkontrolle`.
  - `isotype`: `isotype`, `iso[ _-.]?ctrl`, `(rb|rabbit|ms|mouse|n|normal|non[ _-.]?immune)[ _-.]?IgG`,
    `pre[ _-.]?immune`.
  - `preadsorbed`: `pre[ _-.]?(adsorbed|absorbed|ads|abs)`, `peptide[ _-.]?block(ed)?`, `blocking[ _-.]?peptide`.
  - `unstained`: `unstained`, `no[ _-.]?stain`, `no[ _-.]?(Ab|antibody|antibodies)`, `auto[ _-.]?fluo…`.
- **Weak tier.** Proposed but **not pre-ticked**, with the reason "ambiguous: could be a sample or
  treatment code": `NC\d*`, `CN`, `NK`, `neg\d*`, `ctrl`, `ctl`, `control`, `kontroll`. A weak token
  that is also a group label in `groups.order` is never proposed. That check reads only the group
  *labels*, never the animal-to-group key.
- **Never.** These are not in the pattern set, and a test asserts they produce no match:
  - `AF` (`AF488`/`AF568` dye names)
  - single letters (`AB12_C_3.czi` is real)
  - a trailing `-`
  - `IgG` without a qualifier
- **Acceptance.** Zero matches on every filename in `manifest/` and the test fixtures:
  `LS105_10a`, `LS53_2b-_A568`, `LS85_7b-`, `AB12_C_3`, `LS01_s01a`, `not_a_slide`, …
  A prototype of the raster already gives zero matches on these and hits all 22 synthetic
  positives.
- **Extensibility.** Config `reagent_controls.extra_patterns: [{"regex", "kind"}]` for lab-specific
  names. Each entry is validated as a regex in `_check`.

The scan runs from:
- the New Study dialog, on whatever answer;
- Set up > Slides > Reagent controls…;
- `00_manifest`, which **warns** (non-fatal) when a strong match is neither confirmed nor rejected,
  so a study with `declared: no` still hears about `LS45_noPrimary.czi`.

### 3. New Study dialog and Set up > Slides

- `app/new_study.py` gets a row "Reagent controls in this folder?" with three radio buttons, using
  the same `QButtonGroup` pattern as Atlas: **No** (default) / **Yes** / **Not sure, check for me**.
  The help text names the kinds in plain words ("slides or sections stained without the primary
  antibody, with an isotype/IgG instead, or unstained").
- On Create, with Yes or Not sure, or with No when the scan found a strong match, a **confirm list**
  opens. It is also available later from Set up > Slides. Each row shows:
  - file or folder;
  - the matched token, highlighted;
  - kind (dropdown);
  - markers (checkboxes, default all);
  - an "Animal ID" field, shown only when the name does not parse;
  - Accept / Not a control.

  The same widget lists signal candidates when the screen has run (§4).
- **Pairing.** Each confirmed control gets a default pairing (§5). The confirm list shows it with an
  "Change pairing…" button.

### 4. Signal screen (new stage `01m_reagent_screen.py`)

**When it runs.** After `01_overviews`, since it needs the tissue mask and the section boxes. It
runs when `declared` is `unsure`, or `yes` with no confirmed entries. It can also be run by hand at
any time. It is cheap and never blocks later stages.

**What it reads.**
- Per section, K = 12 tiles of 512×512 px at native resolution (0.65 µm/px), sampled uniformly
  inside the DAPI tissue mask from the overview, read with `czi_read`.
- Tiles touching clipped pixels (`01k`) are skipped.
- The tile count is a constant in the module.

**Features.** Per section × marker. Each is invariant to an additive offset and to a
multiplicative gain, so a background batch cannot move it:

- **F1, nuclear spot enrichment.**
  - Spots are local maxima of the marker above the tile's median + 5 × 1.4826 × MAD.
  - F1 is the fraction of those spots that fall inside a DAPI nucleus mask (Otsu on smoothed
    DAPI, per tile), divided by the DAPI-mask area fraction.
  - Stained nuclear markers sit well above 1. With no primary, spots are lipofuscin and red blood
    cells, which are not enriched in nuclei.
  - Skipped for a marker whose `segment` is `own` or whose DAPI plane is declared unusable
    (LS AF488). The screen then uses F2 and F3 only and says so per row.
- **F2, between-marker similarity.**
  - Spearman correlation of the two marker channels' tissue pixels, per tile, median over tiles.
  - Autofluorescence is broad-band (emission 425-738 nm in Wizenty et al. 2018), so on a
    no-primary multiplex section the channels look alike.
  - Undefined for single-marker files (LS); reported as empty.
- **F3, tail spread.**
  - log(p99.5 − p50) − log(p50 − p5) of tissue pixels: the bright tail relative to the body of
    the distribution.
  - A specific stain has a heavy right tail. Autofluorescence plus diffuse secondary binding has a
    light one.

**Scoring.**
- Per marker, each feature becomes a robust z across all sections of the study:
  (x − median) / (1.4826 × MAD). Signs are set so that "control-like" is positive: −F1, +F2, −F3.
- A section is a **candidate** when every available feature has z ≥ 3. With one feature
  available, it is reported for review but never as a candidate.
- **Rank** = the minimum z over the available features: the weakest evidence decides.
- **File rollup:** the fraction of the file's sections that are candidates.

**Guards**, stated in each candidate row:
- DAPI tissue fraction within the study's normal range, i.e. not a failed or empty section;
- the file's exposure equals the modal exposure for that marker (`manifest_files.exposures`), i.e.
  not a scan-time mistake.

A section failing a guard is listed as "unusual, not a control candidate".

**Output.**
- `qc/reagent_screen.csv`: one row per section × marker, with features, z, guard results,
  candidate flag and rank.
- `qc/reagent_screen.png`: a contact strip of the top candidates beside three median sections of
  the same marker, at the same display range. The operator decides by eye.

**Acceptance test.** On the LS study, which has no controls, zero candidates. If that fails, the
thresholds are wrong, not the study.

**Limits, said in the UI and the guide:**
- The screen cannot see isotype or pre-adsorbed controls.
- It cannot tell a no-primary section from a failed stain or from a genuinely non-expressing animal.
- Thresholds stay provisional until it has run on a study with known controls. That validation is
  a recorded follow-up.

### 5. Keeping controls out, and pairing

**Excluded from:**
- `01_overviews` display-range sampling (`display_ranges.json`);
- the 05b tuning sample;
- the experimental rows of 06a-06c and 06d;
- the workbook check's "missing" list.

**Still processed:**
- contact sheets and QC (labelled "reagent control");
- level and ROI curation;
- 05a geometry;
- 05c detection.

05c measures reagent-control sections **first**, ahead of the balanced order. They are few, and
a partial run should still report them. This does not touch the group key.

**Curation.** The level and ROI curators show a "reagent control" badge on these sections. They
are curated exactly like the others, except that stage 04 places a control in **its own series**
(key `subject` + `#rc`, internal only, never in the uid). It is not merged into the donor animal's
series, so it cannot disturb that animal's level order or atlas matching.

**Where control rows go (per scene × marker).**
- 06a writes them only to `results/reagent_controls.csv` and drops them from its main ROI
  dataset.
- 06b, 06c (except the new sheet) and 06d never see them.
- In a multiplex study, a scene that is a control for marker A and stained for marker B keeps its
  B rows as normal experimental rows.
- No per-animal "reagent control" treatment exists, so `animal_metadata.csv` is unchanged.

**Confirming late.** Confirming or un-confirming a control after a stage has run marks that
stage's outputs out of date, through the app's existing out-of-date tracking (Task 18). The stages
are 01 display ranges / 01l renders, 05b, 05c, 06a-06d, and the app lists them to re-run. 05b and
05c **warn**, but do not refuse, while `declared` is `unsure` and the screen's candidates are
unanswered.

**Scope of "dataset-wide estimates".** The list above is complete, plus the screen's own robust z,
which leaves out sections already confirmed as controls. `00c`, `01g`/`01k`, `04f` and `06g` are
per-section QC, and controls appear in them, labelled.

**06b.** An animal whose every scene is a reagent control is exempt from the missing-row stop
and is absent from 06b outputs.

**Pairing.** This decides which stained sections a control is compared with. Default, in order:
1. stained scenes on the **same file**;
2. otherwise stained scenes of the **same animal and marker**;
3. otherwise **all stained sections of that marker**, flagged `pairing = study-wide` in the
   report, because then a level comparison crosses batches.

The operator can override per control in the confirm list (`paired_with`).

### 6. Measurement and report (`06a`, new output; `06c`, new sheet)

`results/reagent_controls.csv`, one row per reagent-control section × marker × region.

The same 05c nuclei and the same 06a cut machinery are used, so nothing is re-read. Columns:

- `scene_uid, file, kind, marker, region, pairing`
- **A, the rule's call on no specific signal.**
  - `n_nuclei, n_positive, pct_positive`, under the section's **own** per-section cut
    (`ls_overlay.section_cut` on its own background discs).
  - This is the rate at which the pipeline's positivity rule calls nuclei positive when no primary
    is present: its false-positive rate in that region, including region-specific
    autofluorescence and nonspecific secondary binding that is not uniform.
  - Beside it, the paired stained sections' `pct_positive` in the same region (median and range),
    and `specificity_margin = stained_pct − control_pct`.
  - For `segment: own` markers, `positive_per_mm2` replaces pct, mirroring 06a.
- **B, the level comparison, only within a pairing.**
  - `control_nuclear_median`: the median of ROI nuclei `marker_median` on the control.
  - `stained_nuclear_median`: the same over its paired stained sections.
  - `level_ratio` = control / stained.
  - `pct_above_paired_cut` (median) and `pct_above_paired_cut_min`/`_max`. The control's nuclei
    are scored against **each** paired stained section's own cut, and the median and range across
    those sections are reported. This is the borrowed cut. It answers how much of the positive
    call level the secondary alone reaches. *(Proposed; awaiting the operator's choice, see the
    open question below.)*
  - **Left empty when `pairing = study-wide`**, with the reason in a `note` column. Across batches
    the ratio measures the batch, not the reagent (LOGS 2026-08-12).

**06c** gets a "Reagent controls" sheet with the same table plus a plain-language header:
- what A and B mean;
- that isotype / pre-adsorbed rows test antibody specificity while no-primary rows test the
  secondary;
- that a background disc remains not a negative control.

**Wording.** When a study has at least one confirmed no-primary control with a report row, the
"no no-primary controls" limitation text in the 06c header and the app's Results page is replaced
by a pointer to this sheet. The README and wiki get a paragraph explaining both cases. The text for
studies without controls is unchanged.

### 7. Tests (each a failing test first)

- **Raster:** zero matches on all real and fixture names; every listed positive maps to the stated
  kind; the "never" list produces nothing; folder-name matches work; group-label suppression of
  weak tokens works.
- **Config:** the SPEC key validates; `config.example.json` and `config-reference.md` are
  regenerated; a `files.subject` with `_` is refused.
- **Manifest:**
  - a non-parsing control file with a `subject` gets a standard uid;
  - without a `subject` it is still reported as a failure;
  - a strong unconfirmed match warns;
  - a file entry expands to scenes, and a scene entry overrides.
- **Screen:** features on synthetic tiles. A stained-like tile has nuclear-enriched bright spots
  and a heavy tail; a control-like tile has shared autofluorescent puncta in both channels. An
  additive offset or gain change leaves features unchanged. Guards trigger.
- **Exclusion:** controls absent from display-range sampling, the 05b sample and the 06a-06d
  experimental rows; 06b does not stop on a control's animal; 05c orders controls first.
- **Report:**
  - the A and B columns on a fixture with known nuclei;
  - B is empty with a note under study-wide pairing;
  - the limitation wording switches only when a no-primary row exists.
- **Blinding:** no reagent-control code path imports or reads `groups.by_animal` before 06b. This
  extends the existing assertion.

## Left to the builder

- Widget layout of the confirm list, beyond the columns above.
- Exact tile sampling (seed, minimum tissue fraction per tile) and Otsu smoothing sigma, within the
  stated K and resolution.
- Whether `01m` reuses `00c_channel_identity.detect()` for spot finding.
- Wording of the user guide, wiki and technical-guide entries (`docs/guides/file_formats.json` gets
  the two new files).

## Open question for the operator

How should part B's borrowed cut combine several paired stained sections?
- **(a)** Score against each section's cut, then report the median and range. This is the
  proposed default.
- **(b)** Pool all their background discs into one cut.

## Calls made without the operator (open to correction)

- Control-file IDs are a real donor ID when known, else made up, and always carry a slide number.
  Controls get their own series in stage 04.
- Control rows are dropped from the main datasets per scene × marker. There is no per-animal
  "reagent control" treatment.
- A late confirmation marks outputs out of date and warns. It does not block.

- The term "reagent control", and scene-level storage with file-level shorthand.
- The F1/F2/F3 features and the "all available features z ≥ 3" candidate rule.
- Controls are measured first in 05c.
- Default pairing order, and leaving B empty for study-wide pairing.
- Weak name tokens are never pre-ticked.
