# Setting up a study

A **study** is one dataset and the settings that describe it: where the slides
are, where results go, which atlas, how a filename is read. It is a single JSON
file with a name. Everything the pipeline does is rooted in that file, so the
first question is always which study you are working on, and the app keeps the
answer in the status bar rather than leaving it to be inferred.

Every setting a study can carry is listed in
[`config-reference.md`](config-reference.md), which is generated from the same
table that validates it.

## Where studies live

A study made in the app lives in its own output folder, as `study.json`, next
to everything the study produces: curation, pictures, results. The whole study
is one folder you can move or back up.

```
D:\LS-analysis\
  study.json             the study (and project) file
  curation\  qc\  results\  ...

~/.ls-pipeline/
  settings.json          the last study open, and File > Open Recent
  studies/               studies made before they lived in their output folder
    ls-dec-2025.json
```

The app reopens the study you had open last. **Tools ▸ Switch study…** lists
both kinds: the named studies in `~/.ls-pipeline/studies/` and the recent ones
kept in their own folders. Set `LS_HOME` to put `~/.ls-pipeline` somewhere else
(a shared drive, or a USB stick for a portable install). The tests set it to a
temp directory, which is how running them can never change which study you have
open.

## Bringing an existing config in

If you already have a `config.json` at the repo root, it keeps working exactly
as before; nothing has to change. To give it a name, use **Import existing
config…** in **Tools ▸ Switch study…** (enabled only when that file exists), or
from a terminal:

```bash
python scripts/ls_config.py --migrate "LS Dec 2025"
```

That **copies** it into `~/.ls-pipeline/studies/`. The original is left where
it was, so the move is reversible by deleting one file, and anything still
resolving to it (a bare `python scripts/XX.py`, `run_all.sh`, a fresh clone)
carries on working.

`python scripts/ls_config.py --studies` lists the named studies and which is
active.

## Making a new one

In the app: **File ▸ New project…** (Ctrl+N), or **New study…** in **Tools ▸
Switch study…**. The dialog asks only what the slide files cannot say:

- **Slides folder**: where the `.czi` files are. It is read straight away,
  headers only, so the pixel size, how the files are named and how the markers
  were scanned are filled in from the files.
- **Output folder**: where everything is written, `study.json` included.
  Overviews alone run to several GB; put it on a drive with room, and not in
  the slides folder. Choosing a folder that already holds a study opens that
  study instead of making a new one.
- **Section thickness**: how thick the sections were cut. A CZI is one plane of
  a physical section and does not know.
- **Atlas**: the salmon (Vindas) or zebrafish (Wullimann 1996) plates that ship
  with the app, or your own, added afterwards in **Set up ▸ Atlas** (see
  [`adding-your-own-atlas.md`](adding-your-own-atlas.md)).
- **Name** (optional): defaults to the output folder's name.

The study opens on **Set up**, where every setting has a default that is stated
in the reference rather than buried in the code. Only the cards with something
left to ask are shown; tick **Show advanced settings** to see the rest.
**Tools ▸ All settings…** has every setting in one dialog.

## The filename pattern

The pipeline reads the subject, the slide and the replicate out of each
filename, and builds the `scene_uid` that every later table joins on. It has to
be told how your names are shaped. Usually it already is: the pattern is worked
out from the slides when the study is made, or by **Read settings from the
slides** on the Set up ▸ Slides tab.

To set it yourself: **Set up ▸ Slide file names ▸ Set up from an example…**.
Type or pick one of your file names. The app splits it into pieces and shows its
reading, for example *animal LS53 · slide 2 · replicate b*; to change it, press
**Animal ID**, **Slide number** or **Replicate letter** and click the piece
(**Not in the name** if there is none). It writes the pattern and checks it
against every file in the slides folder. Where files do not fit it tries the
usual causes one at a time (case, a varying ending such as `-_A568`, a missing
replicate letter, other separators) and says what it changed and how many files
each change brought in. The pattern itself stays hidden unless you tick **Show
the pattern**, where it can be edited by hand, because most naming schemes have
an exception no example will cover.

What matters is the check. **A file the pattern misses never reaches any stage
and nothing else will tell you**, so the files that still do not fit are listed
with the likely reason, and Save stays off until every file fits or you tick
**Leave the files that do not fit out of this study**. The Slides tab shows the
same files as red rows.

One named group is required, `subject` (the animal). `slide` and `replicate`
are optional; any other group you add by hand rides along as an extra column. A
subject cannot contain `_`, because the uid separates on it.

Studies whose filenames carry less degrade on their own:

| filename groups | scene uid |
|---|---|
| subject, slide, replicate | `LS105_s10a_sc03` |
| subject, slide | `AB12_s03_sc05` |
| subject only | `Fish07_sc05` |

## The project file

A study's config file is also its project file. Besides the settings it carries one
extra block, `project`, that only the app reads (no stage does, so editing or
deleting it never changes a result):

| field | what it holds |
|---|---|
| `project_version`, `id`, `created`, `modified`, `app_version`, `notes` | who and when |
| `detected` | what was read from CZI files (channel names, dyes, wavelengths): evidence, not input |
| `decisions` | changes made in the app: the key, old and new value, who, when. The animal-to-treatment key (`groups.*`) is recorded by name only, never by value |
| `runs` | stages that ran: times, success, a hash of the config, the code version |

The lists are capped (200 runs, 500 decisions, 20 detected files). `portable` records each path setting that lies inside the project's folder, relative to it. Saving writes the
file atomically and keeps the last three versions beside it as `.bak1`-`.bak3`; a
file written by a newer version of the app opens read-only and is never rewritten.
The logic is in `scripts/ls_project.py` (`Project.new / open / set / save / save_as /
revert / record_run`), which the app's File menu is built on.

### One file: your decisions live in the project

Your curation (ROI polygons, which ROIs to draw, rotations, levels) is saved **into the project file** as you make each decision, under `curation`: it never waits for File > Save, so a crash loses nothing, and it does not mark the project unsaved or commit your other unsaved edits. The stages still read plain files, so the app writes copies to `<out_root>/curation/` from the project and keeps them current (a `README.txt` there says so). **Edit the project, not those copies.**

When a project is opened, saved to a new `out_root`, or a stage is about to run, the app compares the two against what they last agreed on:

- a copy that is simply stale is rewritten;
- a project that is older than a copy someone else edited (for example `import_exports.py`, which still writes the files) takes the copy in;
- curation that exists only as files (made before the project held it) is taken in;
- only if **both** changed does it ask: *use the project's* or *use the files'*. The version you do not pick is kept in `curation/_backup_<time>/`.

### Sharing a project

**File > Export a copy...** writes the project file (curation inside) with the paths on your machine blanked, your run history left out, and **the animal-to-treatment key (`groups.*`) stripped**. Unticking "Blinded" asks for confirmation, and the file name says `-UNBLINDED-`; never commit one. Tick *operator CSVs* or *imported atlases* to make a `.zip` that also carries those (they live outside the project file). **File > Open project...** opens a `.json` copy directly (set your own paths in Settings) or a `.zip` as a new project: you choose where to save it, set the data paths, and the extras are brought into your data folder (anything that differs is asked about, and replaced files are backed up first). A zip is verified before anything is read: every file's hash, and no path that could leave the folder. From a terminal: `python scripts/ls_bundle.py copy | export | inspect | import`.

### Moving a project

The path settings (`source_dir`, `out_root`, `atlas_pdf`, `export_dir`) stay absolute in the file, because the stages - including the Fiji ones that read the JSON directly - need them that way. When a project is saved, those that lie **inside the project file's folder** are also recorded, relative to it, under `project.portable`. If you then move the whole folder (say from `D:` to `E:`), the stored absolute paths no longer exist; on opening, any setting whose recorded place exists beside the project file is re-pointed there, the change is listed, and the project is left unsaved for you to confirm with File > Save. A path that still exists is never changed (it may be a deliberate choice), and a path outside the project's folder stays as written.

### Working with projects in the app

The **File** menu is the project's own:

- **New project...** asks for the two folders, the section thickness and the atlas (see *Making a new one* above) and writes `study.json` into the output folder; nothing is written until those are valid.
- **Open project...** and **Open Recent** open any project file, from any folder. One the pipeline would refuse opens with the reasons listed; one written by a newer app opens read-only.
- **Save** (Ctrl+S), **Save As...**, **Revert**.
- **Project history...** shows what the project remembers, newest first: every setting changed (was and now), every stage run (result, config hash, code version), the CZI files read and what they said, and a Notes box. The animal-to-treatment key appears only as "changed", never its values.

Set up, All settings and the channel editor change the open project **in memory**: the title shows `name*` until you choose Save, and every change is added to the project's decision trail. Stages read the *saved* file, so running one asks to save first. Closing or switching with unsaved changes asks Save / Discard / Cancel. Each finished stage is added to the project's run history without saving your other unsaved edits. Detecting channels from a CZI keeps what the file said under `detected`, separate from what you decided.

## Channels, markers and their colours

In the app, **Set up ▸ Microscope and channels ▸ Channels and markers…** (also in
**Tools ▸ All settings…**) edits all of this as a table, so
the config file does not have to be written by hand. Under `multiplex` each row is a
channel: its name (what the outputs call it), its role (nuclear, marker,
registration, ignore), the name the CZI file gives it, its plane as a fallback,
and for a marker where its objects come from (see below). Under `paired`, where
each scan carries the counterstain plus one marker, the table is just the marker
names.

**Detect from a CZI file…** reads what the scan records about each channel (its
name, dye, excitation and emission) and fills in the CZI channel name, plane,
role and colour: a blue channel is suggested as the counterstain, and a marker
channel is coloured by its band (blue, green, red; far-red is drawn magenta). The
file records the dye, not the antibody, so the **marker name is your choice**: each
Name box is a drop-down that opens with *Custom (type your own)*, then the markers
usually seen in that channel's colour band, then every other marker in
[`docs/channel-markers.md`](channel-markers.md); you can also just type a name.
Detecting never overwrites a name, colour or role you have already set, and a
channel that is already in the table only gets its detection note refreshed.

Each marker also gets a **Colour** and a **Show** box. *Show* controls whether it
is drawn in the composite and the curator's Review pane; *(default)* means the
first shown marker is red, the second green, and any further one has no colour
and is not drawn, which the pipeline says rather than guessing. The counterstain's
colour is the separate **Counterstain colour** setting.

The table is judged by the same rules the pipeline applies when it loads the
config, live, and OK stays disabled while it would be refused. Nothing is written
until you press Apply on the Set up card (or OK in All settings). A marker's name
becomes folder and file names in
every output, so renaming asks you to confirm; colours and the composite follow a
rename. The same keys (`acquisition.channels`, `acquisition.markers`,
`display.colours`, `display.composite`) can still be edited by hand.

## Choosing a segmentation backend

Under `multiplex`, `acquisition.channels` says what each channel is. For a
marker it also says **where its objects come from and what finds them** — three
settings that decide what gets counted, and one of which changes how the
numbers are corrected downstream.

```json
{"name": "pERK", "role": "marker", "czi_name": "AF568", "index": 1,
 "segment": "nuclear"},
{"name": "GFAP", "role": "marker", "czi_name": "AF647", "index": 2,
 "segment": "own", "backend": "threshold", "nucleus_shaped": false}
```

**`segment`: whose pixels the objects come from.**

- `nuclear` — the default wherever the study declares a nuclear channel. Nuclei
  are segmented on the counterstain and the marker is measured *inside* each
  nuclear mask. The count and the positivity cut then come off different
  channels, so the cut can be changed later without re-reading a single CZI.
  This is what the pipeline has always done and what every existing number came
  from.
- `own` — the marker's own channel is segmented. Use it when the objects are
  not cells with nuclei: fibre staining, processes, plaques. The cost is real
  and unavoidable for such a marker: the count and the positivity cut now both
  come off one channel, so they cannot be varied independently. A study with no
  nuclear channel at all gets `own` for every marker automatically, because
  there is no nuclear mask to measure inside.

**`backend`: what turns pixels into objects.** Only read under `segment: own`;
a nuclear-segmented marker inherits the counterstain's segmentation, which is
StarDist.

- `stardist` — for anything nucleus-shaped. It separates touching nuclei, which
  a threshold cannot, and that matters most in periventricular regions where an
  under-segmentation error would look like an anatomical finding.
- `threshold` — for everything else. Median + `detection.threshold.mad_k` ×
  1.4826 × MAD of the frame being segmented, then connected components above
  `detection.threshold.min_area_um2`. It finds stained **regions** and does not
  pretend they are cells: two touching objects are one object here. That is a
  limitation of connected components and it is the honest count for the things
  this backend is for.

**`backend: stardist` together with `nucleus_shaped: false` is refused**, at
settings time, by `ls_config.py --check` and by the app. StarDist segments
star-convex, nucleus-shaped objects; pointed at fibre or process staining it
finds few objects and **reports no error at all**. The failure is a quiet
undercount that announces itself nowhere later — not in a log, not in a count
that looks wrong, not in a figure. There is no point downstream at which it
could be caught, so it is caught here.

**`nucleus_shaped` also decides whether the Abercrombie correction applies.**
Sections are cut at a finite thickness and imaged in one plane, so what is
counted is object *profiles*, and `N = n × T/(T+h)` converts them to objects.
That formula assumes spherical, randomly positioned objects. A threshold region
on a fibre-stained channel is neither, so for a marker declared
`nucleus_shaped: false` the correction is **withheld**: the factor is 1.0, the
corrected density equals the raw one, and
`abercrombie_withheld_reason` — a column in `roi_measurements.csv` and on the
workbook's `by_roi` sheet — says why, in the row itself. A blank there means
the correction was applied. Withholding is recorded rather than silent
precisely so that an uncorrected density is never mistaken for an omission.

A `roi_nuclei.csv` written before these columns existed has none of them, and
is read as nuclear / StarDist / nucleus-shaped — which is what every row in
such a file is, since that was the only route detection had.

**Co-localisation** is computed only under `multiplex`, and only between
markers whose objects are actually different. **A is co-localised with B when
A's centroid falls inside B's mask.** There is no overlap fraction and so no
cutoff to tune: objects from two markers are not the same shapes — one may be a
nuclear mask and another a threshold region — and a fraction would not be
comparable between pairs. Containment is asymmetric, since a small object's
centroid can sit inside a large one while the reverse is false, so **both
directions are computed and both are written**, to
`results/roi_colocalisation.csv`, with the direction named.

Two cases produce no file rather than an empty one:

- A `paired` study. Its markers are separate physical scans of *different*
  sections, so their objects are not in one coordinate frame and relating them
  would mean nothing. An empty `roi_colocalisation.csv` would read as "nothing
  overlaps", which is a different claim from "the question does not apply".
- Two markers that are both `segment: nuclear`. They share the same nuclear
  objects, so every object would contain itself and the table would be a
  diagonal.

None of the `segment: own` path has met a real study yet. The backends, the
co-localisation pairs and the withheld correction are covered by synthetic
fixtures that check the rules are what is written above; they cannot check that
the threshold backend segments a real fibre stain usefully. The first study to
declare `segment: own` should expect to find things.

## Which config a stage actually reads

In order:

1. `LS_CONFIG` — an absolute path. This is what the app exports before running
   anything, and what `run_all.sh` and the Fiji stages read directly.
2. `LS_STUDY` — a study name. Naming one that does not exist is an **error**,
   not a fallback: quietly analysing a different study than you asked for is
   the failure this whole layer exists to prevent.
3. The study last opened in the app.
4. The repo's own `config.json`, if there is one.

`LS_CONFIG_STRICT=1` stops at step 1. `tests/run.sh` sets it so a test that
forgets to name its own config fails loudly instead of quietly analysing
whatever you have open.

## Checking a config

```bash
python scripts/ls_config.py --check
```

Errors are things no stage can run on — a required key missing, a value of the
wrong shape. Warnings are not fatal: an unknown key, or a path that is not
there right now. A drive being unplugged should not stop 45 stages from
loading, so it warns at import and fails in the one stage that opens the file.

Keys removed in this schema version are reported as removed, with the reason,
rather than as mistakes.

## What is not generic yet

Be aware of what this layer does and does not cover. The configuration,
filename grammar and study store are general. Three things are still shaped
around the LS salmon experiment and are being worked on separately:

- **Markers and channels.** `channels.dapi_index` / `marker_index` assume one
  nuclear channel and one marker, and the marker names `AF568`/`AF488` are
  written into stage ids, output paths and CSV column names.
- **The atlas.** Plate images can be swapped, but the region seeds are read
  from the Salmon Atlas PDF's vector drawing list and its text layer, neither
  of which a different atlas will reproduce.
- **The metadata join.** Stage 06b parses one specific workbook's sheet XML
  with eleven mandatory columns, and treatment currently reaches the figures
  from `groups.by_animal` in the config rather than from that join.

Each has its own design document under `docs/superpowers/specs/`.
