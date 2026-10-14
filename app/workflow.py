"""The pipeline as the operator works through it: screens, plain words, options.

`stages.py` says what the pipeline IS, in the order and the bands of the methods
figure. That order is right for a paper and wrong for a working day: the four
places a person decides are spread among 55 automatic steps, and the one-time
instrument work sits between QC and the atlas. This file lays the same stages out
by job - Set up, Prepare, Curate, Measure, Results - and says in plain words what
each one is for.

It adds no facts about the pipeline. Which stage needs which, what it leaves
behind and what it reads all stay in `stages.py`; this only decides where a stage
is shown, how it is explained to someone who has not read the methods, and which
of its command-line options are offered as switches.

No Qt here, for the same reason `slides.py` and `naming_build.py` have none: the
judgement is tested without a window (`tests/test_workflow.py`).
"""

import glob
import os

import stages as S


# ---------------------------------------------------------------------------
# Screens
# ---------------------------------------------------------------------------

class Screen:
    def __init__(self, sid, title, intro, info, parts=()):
        self.sid = sid
        self.title = title
        self.intro = intro          # two or three sentences under the screen title
        self.info = info            # the (i) text: what goes in, what comes out
        self.parts = list(parts)    # [(heading, explanation, [stage ids])]

    def stage_ids(self):
        return [sid for _h, _e, ids in self.parts for sid in ids]

    def __repr__(self):
        return f"<Screen {self.sid}>"


HOME = Screen(
    "home", "Project",
    "Where this study stands and what to do next. Every step below shows how far "
    "it has got; the Next up card names the one thing to do now.",
    "<p>A <b>project</b> is one study: its settings, the decisions you made in the "
    "app, and a record of every stage that ran. It lives in one file, which "
    "File &gt; Save writes.</p>"
    "<p>Progress is read from the output folder, so it is right even for work "
    "done outside the app.</p>")

SETUP = Screen(
    "setup", "Set up",
    "Tell the app about this study: where the slides are and how their files are "
    "named, how they were imaged, which atlas to use and how nuclei are counted. "
    "Do the cards in order on a new study; come back to change anything later.",
    "<p>Every setting here is stored in the project file and read by the stages "
    "when they run. Changes are kept in the project as you press <b>Apply</b> and "
    "are saved automatically before any stage runs.</p>"
    "<p>Each field shows a short hint; its (i) button gives the full reasoning "
    "and which stages read it. The ↺ button puts a field back to its default.</p>")

PREPARE = Screen(
    "prepare", "Prepare",
    "The automatic part: read the slides, check them, build the atlas, and turn "
    "every section into a clean, upright, masked image ready for curation. "
    "Run all remaining does the stages in order and stops where it needs you.",
    "<p><b>Goes in:</b> the slide files and the settings from Set up.</p>"
    "<p><b>Comes out:</b> one standard image per section, the list of sections "
    "that can be measured, and the pages the curators show you.</p>"
    "<p>Prepare runs in two halves. The first stops at the rotation and "
    "exclusion curator; once you have exported your decisions there, the "
    "second half carries them through.</p>"
    "<p>Every stage can be re-run on its own. Stages skip work already done, so "
    "re-running is cheap.</p>",
    parts=[
        ("Read and check the slides",
         "Reads every slide file, checks it, exports a picture of every section and "
         "measures where the camera saturated.",
         ["manifest", "verify", "czi_selftest", "overviews", "contactsheets", "pair",
          "channel_identity", "saturation_raw", "saturation_map"]),
        ("Build the atlas",
         "Turns the atlas into a set of plates with the regions marked. Only the "
         "stages for the atlas chosen in Set up are used.",
         ["atlas_extract", "atlas_remerge", "atlas_enforce", "atlas_rebuild",
          "atlas_wullimann_plates", "atlas_wullimann_polygons"]),
        ("Before rotation curation",
         "A first standard image of every section, plus the program's proposals "
         "for which sections to drop and how to rotate the rest.",
         ["reformat_pcna", "exclusion", "symmetry"]),
        ("After rotation curation",
         "Carries your rotation and exclusion decisions into the final images, "
         "masks bubbles and debris, and sets aside saturated sections.",
         ["reformat_pcna_curated", "artifact_pcna", "censor_pcna", "reformat_pcna_final",
          "propagate_perk", "reformat_perk", "artifact_perk", "censor_perk",
          "reformat_perk_final", "render_excluded_perk", "sections_dataset"]),
        ("Get ready for the ROI curator",
         "Orders the curation queue and makes the colour pictures the ROI curator "
         "shows.",
         ["worklist", "rgb_perk", "rgb_pcna", "thumbs_perk", "thumbs_pcna", "provenance"]),
    ])

CURATE = Screen(
    "curate", "Curate",
    "The places where you decide. Each tab is one curator page: work in it, press "
    "its Export button, and the app picks your decisions up from there.",
    "<p>Your work in a curator is saved into the project as you go, so closing "
    "the app loses nothing. <b>Export</b> is what hands the decisions to the "
    "stages that come after.</p>"
    "<p>The ROI curator's own <b>Review</b> button shows every scanned section and "
    "what happened to it, including the ones the pipeline excluded.</p>",
    parts=[
        ("Decisions", "", ["atlas_reframe", "rotation_curator", "roi_curator",
                           "import_curation"]),
    ])

MEASURE = Screen(
    "measure", "Measure",
    "Puts your regions back on the original slides, finds every nucleus inside "
    "them and measures the marker in each. Still blind: nothing here knows which "
    "animal is in which group.",
    "<p><b>Goes in:</b> the regions you drew in the ROI curator.</p>"
    "<p><b>Comes out:</b> <code>results/roi_nuclei.csv</code>, one row per nucleus, "
    "and the per-region numbers built from it.</p>"
    "<p>Nucleus detection takes hours and needs StarDist. It can be stopped and "
    "resumed; finished sections are not redone.</p>",
    parts=[
        ("Place the regions on the slides",
         "Maps each region from the curated image back to the slide's own pixels.",
         ["roi_geometry_verify", "roi_geometry"]),
        ("Count and measure nuclei",
         "Finds nuclei, measures the marker in each, then builds the per-region table.",
         ["detect", "roi_dataset"]),
        ("Repairs for older results",
         "Only for a results file written by an older version of the pipeline. "
         "Each one backs the file up before changing it.",
         ["recensor", "off_tissue", "seg_provenance"]),
    ])

RESULTS = Screen(
    "results", "Results",
    "Joins the measurements to the experiment's groups and writes the "
    "spreadsheets and figures. The first stage here unblinds the analysis.",
    "<p><b>Unblinding</b> means the treatment groups are read for the first time. "
    "The app asks you to confirm before it runs that stage.</p>"
    "<p>The spreadsheets themselves stay keyed by animal and can be rebuilt at "
    "any time, including while detection is still running.</p>",
    parts=[
        ("Unblind", "Reads the treatment groups for the first time.", ["join_sampling"]),
        ("Spreadsheets and figures", "The tables you analyse and the figures drawn from them.",
         ["excel_sample", "excel_slide", "refresh_loop"]),
    ])

INSTRUMENT = Screen(
    "instrument", "Instrument characterisation",
    "Measures the microscope rather than the tissue: the tile pattern and the "
    "uneven illumination. Done once per instrument, and already done for the LS "
    "dataset.",
    "<p>None of these numbers reaches a result directly. The tile field built "
    "here is applied by the section export when it exists, and the export works "
    "without it.</p>"
    "<p>One stage needs Fiji, which the app does not drive; run "
    "<code>scripts/run_all.sh</code> steps 3 to 8 for it.</p>",
    parts=[
        ("Tile geometry and illumination",
         "Where the tiles are and how bright each part of a tile comes out.",
         ["tile_geometry", "pick_sections", "fiji_export", "tilefield_build",
          "tilefield_verify", "artifact_before", "artifact_after", "tilefield_raw"]),
    ])

ALL = Screen(
    "all", "All stages",
    "Every stage of the pipeline in the order of the methods figure, with the "
    "same Run, Options and (i) buttons as the other screens. For when you know "
    "exactly which stage you want.",
    "<p>This is the app's previous layout. Stages are grouped by the bands of "
    "Figure 1 in <code>docs/pipeline-methods.md</code>.</p>",
    parts=[(g, "", [st.sid for st in S.in_group(g)]) for g in S.GROUPS])

#: The step bar, in order.
STEPS = [HOME, SETUP, PREPARE, CURATE, MEASURE, RESULTS]
#: Reached from the menus rather than the step bar.
EXTRA = [INSTRUMENT, ALL]
SCREENS = {s.sid: s for s in STEPS + EXTRA}

#: Kept only on All stages: superseded, and not to be used by accident.
HIDDEN = {"level_curator": "Superseded by the ROI curator's own plate slider; kept "
                           "for the record. Only listed under View > All stages."}


def screen_of(sid):
    """The step-bar or menu screen a stage is shown on, or None for HIDDEN ones."""
    for scr in STEPS + [INSTRUMENT]:
        if sid in scr.stage_ids():
            return scr.sid
    return None


# ---------------------------------------------------------------------------
# Plain words
# ---------------------------------------------------------------------------

# One or two sentences per stage, for someone who has not read the methods.
# `{m0}` and `{m1}` are the study's first and second marker. The technical
# description stays in stages.py as `blurb` and is shown under it.
PLAIN = {
    "manifest": "Reads the header of every slide file to list the sections on it, without "
                "loading any image. Everything later starts from this list.",
    "verify": "Checks that every slide file was copied or unzipped completely.",
    "czi_selftest": "Checks the slide files for the few things that would make every later "
                    "measurement wrong, such as images shifted between zoom levels.",
    "overviews": "Saves a picture of every section at one fixed size and brightness, and "
                 "scores each for focus and saturation.",
    "contactsheets": "Puts each animal's sections side by side in brain order, so you can "
                     "browse the whole dataset quickly.",
    "pair": "Works out which {m0} scan and which {m1} scan show the same physical section.",
    "channel_identity": "Suggests which dye carries which marker from how the signal looks. "
                        "It only reports; it changes no setting.",
    "tile_geometry": "Reads where the microscope's tiles sit on each slide.",
    "pick_sections": "Chooses the sample of sections used to measure the illumination pattern.",
    "fiji_export": "Exports that sample at higher resolution. Needs Fiji, so it is run outside "
                   "the app.",
    "tilefield_build": "Builds a correction for the uneven brightness across each tile.",
    "tilefield_verify": "Applies that correction to the sample so it can be checked.",
    "artifact_before": "Measures how strong the tile pattern is before correction.",
    "artifact_after": "Measures the tile pattern again after correction, for comparison.",
    "tilefield_raw": "A second illumination measurement from the raw tiles, for checking only.",
    "saturation_raw": "Finds pixels where the camera hit its maximum, and saves a mask of them "
                      "per section.",
    "saturation_map": "Tells harmless saturation (edges, debris) apart from saturation inside "
                      "the tissue.",
    "atlas_extract": "Pulls the plate pictures and region markers out of the atlas PDF.",
    "atlas_remerge": "Joins atlas figures the PDF stores as several strips back into one picture.",
    "atlas_reframe": "You draw one box per brain section on each atlas figure; most are "
                     "proposed for you.",
    "atlas_enforce": "Corrects the number of boxes on figures where the automatic count was "
                     "wrong, using the counts in the settings.",
    "atlas_rebuild": "Renders the final atlas plates at full resolution. This is the set the "
                     "ROI curator shows.",
    "atlas_wullimann_plates": "Builds the plate set from the Wullimann 1996 zebrafish atlas.",
    "atlas_wullimann_polygons": "Turns each Wullimann plate's region outlines into numbered "
                                "shapes and guesses their names. A person reviews the names.",
    "reformat_pcna": "Makes a first standard image of every {m1} section: cleaned, turned "
                     "upright, centred and cropped.",
    "exclusion": "Proposes sections that cannot be measured, such as torn or empty ones. "
                 "Only proposals; you decide.",
    "symmetry": "Proposes a small rotation for each section so its midline is vertical.",
    "rotation_curator": "You check each section's rotation and accept or overrule the "
                        "proposed exclusions.",
    "reformat_pcna_curated": "Remakes the {m1} images with your rotations and exclusions "
                             "applied.",
    "artifact_pcna": "Finds bubbles, clumps and fibres inside the {m1} sections and masks them "
                     "out.",
    "censor_pcna": "Sets aside {m1} sections with too many saturated pixels and flags the "
                   "rest.",
    "reformat_pcna_final": "The final {m1} images: your decisions applied, artifacts masked, "
                           "saturation flagged.",
    "propagate_perk": "Copies your {m1} decisions onto the matching {m0} scans.",
    "reformat_perk": "Makes the standard {m0} images with those decisions applied.",
    "artifact_perk": "Finds and masks artifacts in the {m0} sections.",
    "censor_perk": "Sets aside {m0} sections with too many saturated pixels and writes the "
                   "list of measurable sections.",
    "reformat_perk_final": "The final {m0} images, ready for measuring.",
    "render_excluded_perk": "Makes pictures of the excluded {m0} sections so you can look at "
                            "them in Review.",
    "sections_dataset": "A table of every {m0} section and what happened to it.",
    "worklist": "Orders the ROI curation queue so that stopping part way still leaves every "
                "animal equally covered.",
    "rgb_perk": "Colour pictures of the {m0} sections (nuclei blue, marker in colour) for "
                "the ROI curator.",
    "rgb_pcna": "The same colour pictures for {m1}.",
    "thumbs_perk": "Small {m0} pictures for the curator's Review grid.",
    "thumbs_pcna": "Small {m1} pictures for the Review grid.",
    "provenance": "Traces every scanned section through every decision, for the curator's "
                  "Review mode.",
    "roi_curator": "You match each section to an atlas plate and draw the brain regions to "
                   "measure, plus background spots for comparison.",
    "import_curation": "Brings ROI curation exported from a web browser into the app.",
    "level_curator": "An older way of matching sections to plates. Superseded by the ROI "
                     "curator.",
    "roi_geometry_verify": "Checks that regions map back onto the slides accurately before "
                           "anything is measured.",
    "roi_geometry": "Maps every region you drew back onto the original slide.",
    "detect": "Finds every nucleus inside your regions and measures the marker in each. "
              "Takes hours; can be stopped and resumed.",
    "recensor": "Recomputes the saturation flag on an older results file.",
    "off_tissue": "Flags nuclei outside the tissue on an older results file.",
    "seg_provenance": "Adds missing detection details to an older results file.",
    "roi_dataset": "Turns the nuclei into per-region numbers: how many cells, how many are "
                   "positive, per square millimetre. Still blind.",
    "join_sampling": "Unblinds: joins each animal to its treatment group from the sampling "
                     "workbook.",
    "excel_sample": "The spreadsheet with one row per region per animal. The figures read "
                    "this one.",
    "excel_slide": "The same spreadsheet one level finer, per slide.",
    "refresh_loop": "Rebuilds the spreadsheets and figures every hour while detection runs. "
                    "Run from refresh_loop.bat.",
}

# The (i) text for a curator tab: what you do there and what Export hands on.
CURATOR_HELP = {
    "atlas_reframe": "<p>Each atlas figure may show several brain sections. Draw one box "
                     "around each; boxes are proposed from the tissue bands, so only "
                     "figures with several sections usually need a look.</p>"
                     "<p><b>Export</b> writes <code>atlas/plate_boxes.csv</code>. Then press "
                     "<b>Continue Prepare</b> to render the final plates.</p>",
    "rotation_curator": "<p>Every section is shown with the program's proposed rotation "
                        "already applied. Drag any that are not upright, and accept or "
                        "overrule each proposed exclusion.</p>"
                        "<p><b>Export</b> writes <code>reformatted/rotation_overrides.csv</code>. "
                        "Then press <b>Continue Prepare</b> to carry your decisions into "
                        "the final images.</p>",
    "roi_curator": "<p>For each section: choose the matching atlas plate, place the "
                   "numbered landmarks, draw each region as an outline, and place "
                   "background discs. The queue is ordered so stopping part way still "
                   "covers every animal.</p>"
                   "<p><b>Review</b> in the page shows every scanned section and every "
                   "decision made about it. <b>Export</b> writes the three CSV files "
                   "Measure reads.</p>",
}

#: What counts as "you have exported your decisions" for each curator. done()
#: in stages.py only says the PAGE exists; what lets the next stage run is the
#: export, which lands beside the page (or, stamped, under the export folder).
DECISION_FILES = {
    "atlas_reframe": ["atlas/plate_boxes.csv"],
    "rotation_curator": ["reformatted/rotation_overrides.csv"],
    "roi_curator": ["reformatted/roi_regions*.csv", "reformatted/*/roi_regions*.csv",
                    "{export}/roi_regions*.csv", "{export}/*/roi_regions*.csv"],
}

#: Curator stages that only apply to one atlas source.
ATLAS_ONLY = {
    "salmon": {"atlas_extract", "atlas_remerge", "atlas_reframe", "atlas_enforce",
               "atlas_rebuild"},
    "wullimann1996": {"atlas_wullimann_plates", "atlas_wullimann_polygons"},
}


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------

class Opt:
    """One command-line option of a stage, offered as a switch or a field.

    `default` is what the app passes when the operator has not touched it - the
    stage table's own argv for a flag already there, otherwise the script's.
    `cli_default` is the script's own default: a value equal to it is left off
    the command line, so an untouched option runs the stage exactly as before.
    """

    def __init__(self, flag, kind, label, help, default=None, cli_default=None,
                 choices=None, minimum=None, maximum=None, zero=None):
        self.flag, self.kind, self.label, self.help = flag, kind, label, help
        self.zero = zero            # what 0 means, shown in place of the number
        self.default = default if default is not None else (False if kind == "bool" else None)
        self.cli_default = cli_default if cli_default is not None else (
            False if kind == "bool" else None)
        self.choices = list(choices or [])
        self.minimum, self.maximum = minimum, maximum

    def coerce(self, value):
        """The value as this option's type, or ValueError."""
        if self.kind == "bool":
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        if value is None or value == "":
            return None
        if self.kind == "int":
            v = int(value)
        elif self.kind == "float":
            v = float(value)
        elif self.kind == "choice":
            v = str(value)
            if v not in self.choices:
                raise ValueError(f"{self.label}: choose one of {', '.join(self.choices)}")
            return v
        else:
            v = str(value)
        if self.minimum is not None and v < self.minimum:
            raise ValueError(f"{self.label}: at least {self.minimum}")
        if self.maximum is not None and v > self.maximum:
            raise ValueError(f"{self.label}: at most {self.maximum}")
        return v

    def __repr__(self):
        return f"<Opt {self.flag}>"


def _limit(what="sections"):
    return Opt("--limit", "int", f"Only the first N {what}",
               f"For a quick trial run. 0 means all {what}.", default=0, cli_default=0,
               minimum=0, zero="all")


def _force(what):
    return Opt("--force", "bool", f"Redo {what} already done",
               f"Normally {what} that already exist are skipped, so a re-run only fills "
               "the gaps. Turn this on after changing something that affects them.")


_RGB = [
    Opt("--scale", "int", "Picture size (x 256 px)",
        "How large the colour pictures are drawn. 3 gives 768 px. Only the sampling "
        "changes; the section's frame and rotation stay exactly the same.",
        default=3, cli_default=3, minimum=1, maximum=8),
    Opt("--include-excluded", "bool", "Include excluded sections",
        "Also make pictures of sections the pipeline excluded, so Review can show them "
        "in colour."),
    _force("pictures"),
]

OPTIONS = {
    "czi_selftest": [
        Opt("--pixels", "int", "Also read N files' pixels",
            "Besides the header checks, read this many files spread across the dataset "
            "and compare their real brightest pixel with the sensor's limit. 0 reads none.",
            default=0, cli_default=0, minimum=0, zero="none"),
        _limit("files"),
    ],
    "overviews": [_force("sections"), _limit()],
    "contactsheets": [
        Opt("--kind", "choice", "Which picture",
            "RGB shows nuclei and marker together; MARK only the marker; DAPI only the "
            "nuclei.", default="RGB", cli_default="RGB", choices=["RGB", "MARK", "DAPI"]),
        Opt("--cols", "int", "Sections per row", "How many sections each contact-sheet row "
            "holds.", default=8, cli_default=8, minimum=1, maximum=40),
        Opt("--thumb", "int", "Thumbnail size (px)", "Edge length of each section's picture.",
            default=200, cli_default=200, minimum=50, maximum=1000),
    ],
    "pick_sections": [
        Opt("--n", "int", "Sections in the sample", "How many sections the illumination "
            "measurement uses. More is slower and steadier.", default=96, cli_default=72,
            minimum=4),
        Opt("--scenes-per-file", "int", "At most N per slide file",
            "Spreads the sample across slides instead of taking many from one.",
            default=4, cli_default=4, minimum=1),
    ],
    "tilefield_raw": [
        Opt("--files", "int", "Slide files to sample", "How many files the raw tiles are "
            "taken from.", default=16, cli_default=16, minimum=1),
        Opt("--tiles", "int", "Tiles per file", "How many raw tiles are read from each "
            "file.", default=60, cli_default=60, minimum=1),
    ],
    "saturation_raw": [
        _force("sections"),
        Opt("--native-sample", "int", "Full-resolution check on N sections",
            "Also measures this many sections at full resolution, to confirm the overview "
            "count is right. 0 skips the check.", default=30, cli_default=30, minimum=0,
            zero="skip"),
        _limit(),
    ],
    "saturation_map": [
        Opt("--figures", "int", "Example figures", "How many example sections are drawn "
            "with their saturation marked.", default=8, cli_default=8, minimum=0),
    ],
    "atlas_remerge": [
        Opt("--dpi", "int", "Render resolution (dpi)", "How finely the atlas pages are "
            "rendered. 300 matches the plates the curators were built on.",
            default=300, cli_default=300, minimum=72, maximum=1200),
    ],
    "atlas_rebuild": [
        Opt("--dpi", "int", "Render resolution (dpi)", "How finely the final plates are "
            "rendered.", default=300, cli_default=300, minimum=72, maximum=1200),
    ],
    "atlas_wullimann_polygons": [
        Opt("--qc", "bool", "Save check pictures", "Writes one picture per plate with the "
            "found outlines drawn on it.", default=True),
        Opt("--keep-reviewed", "bool", "Keep reviewed names",
            "Does not overwrite outlines a person has already reviewed. Turn on when "
            "re-running after a review."),
        Opt("--no-ocr", "bool", "Do not guess names",
            "Leaves every outline unnamed instead of reading names off the plate."),
    ],
    "exclusion": [
        Opt("--montage", "int", "Sections in the proposal sheet",
            "How many proposed exclusions are shown in the summary picture.",
            default=32, cli_default=32, minimum=0),
    ],
    "symmetry": [_limit()],
    "rotation_curator": [
        Opt("--no-proposals", "bool", "Start without proposals",
            "Builds the page with no rotation or exclusion pre-marked, so every "
            "section is judged from scratch."),
        Opt("--thumb", "int", "Picture size (px)", "Bigger pictures give finer control "
            "when dragging a rotation.", default=170, cli_default=170, minimum=80,
            maximum=600),
    ],
    "artifact_pcna": [
        Opt("--include-excluded", "bool", "Also mask excluded sections",
            "Masks the excluded sections too, so Review shows them like every other.",
            default=True),
        _limit(),
    ],
    "artifact_perk": [
        Opt("--include-excluded", "bool", "Also mask excluded sections",
            "Masks the excluded sections too, so Review shows them like every other.",
            default=True),
        _limit(),
    ],
    "censor_pcna": [
        Opt("--tolerance", "float", "Set aside above this saturated fraction",
            "A section with at least this fraction of its tissue saturated is set aside. "
            "0.01 is 1%. Saturated pixels in the sections that stay are flagged, not "
            "removed.", default=0.01, cli_default=0.01, minimum=0.0, maximum=1.0),
    ],
    "censor_perk": [
        Opt("--tolerance", "float", "Set aside above this saturated fraction",
            "A section with at least this fraction of its tissue saturated is set aside. "
            "0.01 is 1%. Saturated pixels in the sections that stay are flagged, not "
            "removed.", default=0.01, cli_default=0.01, minimum=0.0, maximum=1.0),
    ],
    "propagate_perk": [_limit()],
    "worklist": [
        Opt("--min-sections", "int", "Hold out animals with fewer sections",
            "Animals with fewer measurable sections than this go to the end of the "
            "queue. 0 keeps every animal in the main queue.", default=10, cli_default=10,
            minimum=0, zero="keep all"),
    ],
    "rgb_perk": _RGB,
    "rgb_pcna": _RGB,
    "thumbs_perk": [
        Opt("--thumb-size", "int", "Thumbnail size (px)", "Edge length of the Review grid "
            "pictures.", default=256, cli_default=256, minimum=64, maximum=1024),
    ],
    "thumbs_pcna": [
        Opt("--thumb-size", "int", "Thumbnail size (px)", "Edge length of the Review grid "
            "pictures.", default=256, cli_default=256, minimum=64, maximum=1024),
    ],
    "provenance": [
        Opt("--tissue-masks", "bool", "Build tissue outlines",
            "Draws each section's tissue outline for Review. Slow, since it opens every "
            "section, and only needed once.", default=True),
        _force("tissue outlines"),
    ],
    "roi_curator": [
        Opt("--worklist", "bool", "Use the ordered queue",
            "Orders sections so stopping part way still covers every animal and every "
            "brain level. Off shows them in plain order.", default=True),
        Opt("--rgb", "bool", "Colour pictures",
            "Shows nuclei in blue with the marker in colour. Off shows the grey nuclear "
            "image the geometry was computed on. The frame is the same either way.",
            default=True),
        Opt("--analysis-set", "bool", "Measurable sections only",
            "Leaves out sections set aside for saturation."),
        Opt("--use-auto-polygons", "bool", "Show unreviewed atlas outlines",
            "Wullimann atlas only: also show outlines whose names nobody has checked. "
            "Off by default, because the name becomes the region's label in the results."),
    ],
    "roi_geometry": [
        Opt("--all-sections", "bool", "Every section with a region",
            "Includes sections without a background disc, which the positivity cut "
            "cannot use."),
        _limit(),
    ],
    "detect": [
        Opt("--order", "choice", "Order sections are measured in",
            "balanced alternates treatment and control so a partial run is already a "
            "comparison; it reads the group key to do that. uid keeps the run fully "
            "blind. The finished file is the same either way.",
            default="balanced", cli_default="balanced", choices=["balanced", "uid"]),
        Opt("--qc", "bool", "Save check pictures",
            "One picture per region with the found nuclei outlined: green counted, red "
            "found but outside the region."),
        _force("sections"),
        _limit(),
    ],
    "recensor": [Opt("--dry-run", "bool", "Report only", "Says what would change and "
                     "writes nothing.")],
    "off_tissue": [Opt("--dry-run", "bool", "Report only", "Says what would change and "
                       "writes nothing.")],
    "seg_provenance": [Opt("--dry-run", "bool", "Report only", "Says what would change and "
                           "writes nothing.")],
}


def options_for(sid):
    return list(OPTIONS.get(sid, ()))


def clean_options(sid, values):
    """Only known options, coerced, and only where they differ from the default.

    What is stored in the project is what the operator changed - nothing else -
    so a default improved later reaches a study that never touched it.
    """
    out = {}
    for opt in options_for(sid):
        if opt.flag not in (values or {}):
            continue
        v = opt.coerce(values[opt.flag])
        if v != opt.default and v is not None:
            out[opt.flag] = v
    return out


def build_argv(stage, values=None):
    """The stage's argv with the chosen options applied.

    Flags the option list manages are taken out of the stage table's argv and
    put back from the options, so switching off a flag the table sets (04l's
    `--rgb`) removes it, and an untouched option leaves the command exactly as
    the table wrote it.
    """
    opts = {o.flag: o for o in options_for(stage.sid)}
    values = values or {}
    base, skip = [], False
    for tok in stage.argv:
        if skip:
            skip = False
            continue
        if tok in opts:
            skip = opts[tok].kind != "bool"
            continue
        base.append(tok)
    extra = []
    for flag, opt in opts.items():
        v = opt.coerce(values[flag]) if flag in values else opt.default
        if opt.kind == "bool":
            if v:
                extra.append(flag)
        elif v is not None and (v != opt.cli_default or flag in stage.argv):
            extra += [flag, str(v)]
    return base + extra


def with_options(stage, values=None):
    """A shallow copy of `stage` whose argv carries the chosen options.

    The runner appends `extra_argv` to the table's argv and cannot take a flag
    away, so a run with options goes through a copy instead.
    """
    import copy
    st = copy.copy(stage)
    st.argv = build_argv(stage, values)
    return st


# ---------------------------------------------------------------------------
# Settings, grouped for Set up
# ---------------------------------------------------------------------------

#: Set up's cards after Slides, and the settings each holds, in display order.
SETTING_SECTIONS = [
    ("folders", "Study and folders",
     "The study's name, where the slide files are, and where every output is written.",
     ["study.name", "source_dir", "out_root", "export_dir"]),
    ("naming", "Slide file names",
     "How a file name says which animal and slide it is. A file whose name does not "
     "fit is invisible to every stage, so the pattern is checked against the real "
     "files.",
     ["slide_naming.pattern", "slide_naming.example", "slide_naming.case_insensitive",
      "section_order_convention"]),
    ("imaging", "Microscope and channels",
     "How the slides were imaged: pixel size, section thickness, how the markers were "
     "scanned and which channel is which.",
     ["pixel_size_um", "section_thickness_um", "overview_target_um_per_px",
      "acquisition.layout", "display.nuclear_colour", "channels.dapi_index",
      "channels.marker_index"]),
    ("atlas", "Atlas",
     "Which brain atlas the regions come from. Choose a built-in one or import your own.",
     ["atlas_source", "atlas_pdf", "atlas_polygon_mirror", "atlas_plate_set.dir",
      "atlas_figure_sections.two_sections"]),
    ("detection", "Nucleus detection",
     "How nuclei are found and counted inside your regions.",
     ["detection.nucleus_diameter_um", "detection.abercrombie.enabled",
      "detection.threshold.mad_k", "detection.threshold.min_area_um2"]),
]

OTHER_SECTION = ("other", "Other settings",
                 "Settings not yet sorted into a card above.")


def setting_sections(fields):
    """[(sid, title, intro, [Key])] for the given dialog fields; unsorted keys go last.

    A key added to ls_config.SPEC appears in Set up without anyone editing this
    file: it lands in Other settings until it is placed.
    """
    by_path = {k.path: k for k in fields}
    placed, out = set(), []
    for sid, title, intro, paths in SETTING_SECTIONS:
        keys = [by_path[p] for p in paths if p in by_path]
        placed.update(k.path for k in keys)
        out.append((sid, title, intro, keys))
    rest = [k for k in fields if k.path not in placed]
    if rest:
        out.append(OTHER_SECTION + (rest,))
    return out


# ---------------------------------------------------------------------------
# The study, as the screens need it
# ---------------------------------------------------------------------------

class Study:
    """What the screens ask about the open study, resolved once per refresh."""

    def __init__(self, out_root, names=None, cfg=None):
        self.out_root = out_root or ""
        self.names = names
        self.cfg = cfg or {}

    @property
    def markers(self):
        return list(getattr(self.names, "markers", None) or [])

    @property
    def layout(self):
        return getattr(self.names, "layout", None)

    @property
    def atlas_source(self):
        return self.cfg.get("atlas_source") or "salmon"

    @property
    def export_root(self):
        return self.cfg.get("export_dir") or os.path.join(self.out_root, "exports")

    def marker_label(self, i):
        """The name a person knows marker i by: its confirmed antibody, else the dye."""
        ms = self.markers
        if i >= len(ms):
            return f"marker {i + 1}"
        ident = self.cfg.get("marker_identity") or {}
        name = ident.get(ms[i]) if isinstance(ident, dict) else None
        return name if isinstance(name, str) and name else ms[i]

    def words(self, text):
        return (text or "").replace("{m0}", self.marker_label(0)).replace(
            "{m1}", self.marker_label(1))


def split_title(title):
    """("04a", "Reformat PCNA sections") from the table's "04a  Reformat ..."."""
    if "  " in title:
        code, rest = title.split("  ", 1)
        return code.strip(), rest.strip()
    return "", title.strip()


def display_title(stage, study=None):
    """The stage's name without its script number, in this study's marker names.

    The table spells the LS markers out (pERK is the first marker, PCNA the
    second); a study with other markers sees its own.
    """
    _code, name = split_title(stage.title)
    if study is not None and study.markers:
        name = name.replace("pERK", "{m0}").replace("PCNA", "{m1}")
        name = study.words(name)
    return name


def plain(stage, study=None):
    text = PLAIN.get(stage.sid) or stage.blurb
    return study.words(text) if study is not None else text


def applies(stage, study):
    """(True, "") or (False, why) - whether this study uses the stage at all."""
    if study is not None and study.layout and not stage.applies(study.layout):
        return False, f"Not part of this study: it is for {' and '.join(stage.layouts)} " \
                      f"acquisition, and this study is {study.layout}."
    if study is not None:
        for source, ids in ATLAS_ONLY.items():
            if stage.sid in ids and study.atlas_source != source:
                return False, f"Not used with the {study.atlas_source} atlas."
    return True, ""


def decided(sid, study):
    """True when the curator `sid` has an export the next stages can read."""
    pats = DECISION_FILES.get(sid)
    if not pats:
        return True
    for pat in pats:
        if pat.startswith("{export}"):
            full = os.path.join(study.export_root, pat[len("{export}/"):])
        else:
            full = os.path.join(study.out_root, pat)
        if glob.glob(full):
            return True
    return False


def decision_file(sid, study):
    """The newest export for curator `sid`, or None."""
    found = []
    for pat in DECISION_FILES.get(sid, ()):
        if pat.startswith("{export}"):
            full = os.path.join(study.export_root, pat[len("{export}/"):])
        else:
            full = os.path.join(study.out_root, pat)
        found += glob.glob(full)
    return max(found, key=os.path.getmtime) if found else None


def state(stage, study, running=None):
    """One word for a stage's status on screen.

    running / failed   from this session
    notused            this study does not use it
    manual             the app cannot run it here (cli_only)
    done / todo        from the output folder
    """
    running = running or {}
    if running.get(stage.sid) in ("running", "failed", "queued"):
        return running[stage.sid]
    ok, _why = applies(stage, study)
    if not ok:
        return "notused"
    if stage.cli_reason():
        return "manual"
    return "done" if stage.done(study.out_root, study.names) else "todo"


def run_plan(screen_sid, study, include_curator_pages=True):
    """(stages to run, in order; {stage id: what it waits for}) for Run all remaining.

    A stage is planned when it is used by this study, the app can run it, its
    outputs are missing, and everything it needs is done or planned ahead of
    it. A stage that needs a curator you have not exported yet is held, along
    with everything behind it, and named in the second value so the screen can
    say what it is waiting for. Prepare also builds the curator PAGES whose
    inputs are ready, because building a page is automatic - only using it is
    yours.
    """
    scr = SCREENS[screen_sid]
    wanted = set(scr.stage_ids())
    if include_curator_pages and screen_sid == "prepare":
        wanted |= {st.sid for st in S.STAGES if st.curator and st.sid not in HIDDEN}
    plan, held, planned = [], {}, set()
    for st in S.STAGES:
        if st.sid not in wanted or st.sid in HIDDEN:
            continue
        ok, _ = applies(st, study)
        if not ok or st.cli_reason():
            continue
        if st.done(study.out_root, study.names):
            continue
        waits = None
        for need in st.needs:
            n = S.BY_ID.get(need)
            if n is None:
                continue
            if need in held:
                waits = held[need]
                break
            n_ok, _ = applies(n, study)
            if not n_ok:
                continue
            if n.operator and not decided(need, study):
                waits = need
                break
            if need not in planned and not n.done(study.out_root, study.names):
                waits = need
                break
        if waits:
            held[st.sid] = waits
            continue
        plan.append(st.sid)
        planned.add(st.sid)
    return plan, held


def progress(screen_sid, study):
    """(done, total) over the stages of a screen that this study uses and the app runs."""
    done = total = 0
    for sid in SCREENS[screen_sid].stage_ids():
        st = S.BY_ID[sid]
        if not applies(st, study)[0] or st.cli_reason():
            continue
        total += 1
        if st.operator:
            done += decided(sid, study) and st.done(study.out_root, study.names)
        else:
            done += st.done(study.out_root, study.names)
    return done, total


def curators(study):
    """The Curate tabs this study uses, in order."""
    return [sid for sid in ("atlas_reframe", "rotation_curator", "roi_curator")
            if applies(S.BY_ID[sid], study)[0]]


def next_up(study, setup_ok=True):
    """(screen id, what to say, stage id or None) - the single next thing to do."""
    if not setup_ok:
        return "setup", "Finish Set up: some required settings are missing.", None
    plan, held = run_plan("prepare", study)
    if plan:
        first = S.BY_ID[plan[0]]
        if first.curator:
            return "prepare", "Run Prepare to build the %s page." % display_title(first, study), None
        return "prepare", "Run Prepare: %d stage%s left." % (len(plan), "" if len(plan) == 1 else "s"), None
    for sid in curators(study):
        st = S.BY_ID[sid]
        if not st.done(study.out_root, study.names):
            continue
        if not decided(sid, study):
            return "curate", "Curate: work through %s and export your decisions." % \
                display_title(st, study), sid
    if held:
        waits = sorted(set(held.values()))
        names = ", ".join(display_title(S.BY_ID[w], study) for w in waits if w in S.BY_ID)
        return "curate", "Prepare is waiting for: %s." % names, waits[0]
    m_plan, _ = run_plan("measure", study)
    if m_plan:
        return "measure", "Run Measure: %d stage%s left." % (
            len(m_plan), "" if len(m_plan) == 1 else "s"), None
    r_plan, _ = run_plan("results", study)
    if r_plan:
        return "results", "Results: unblind and build the spreadsheets.", None
    return "home", "Everything the app can run is done.", None


def last_run(project_block, sid):
    """The newest run record for a stage, or None."""
    for rec in reversed((project_block or {}).get("runs") or []):
        if rec.get("stage") == sid:
            return rec
    return None


def duration_words(rec):
    """'took 3 min' from a run record's ISO times, or ''."""
    import datetime
    try:
        a = datetime.datetime.fromisoformat(rec["started"])
        b = datetime.datetime.fromisoformat(rec["ended"])
    except (KeyError, TypeError, ValueError):
        return ""
    secs = max(0, int((b - a).total_seconds()))
    if secs < 60:
        return f"took {secs} s"
    if secs < 3600:
        return f"took {secs // 60} min"
    return f"took {secs / 3600:.1f} h"
