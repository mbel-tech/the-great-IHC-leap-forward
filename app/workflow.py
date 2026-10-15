"""The pipeline as the operator works through it: screens, plain words, options.

`stages.py` says what the pipeline IS, in the order and the bands of the methods
figure. That order is right for a paper and wrong for a working day: the four
places a person decides are spread among dozens of automatic steps, and the one-time
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
import hashlib
import json
import os
import re

import stages as S


# ---------------------------------------------------------------------------
# Screens
# ---------------------------------------------------------------------------

class Screen:
    def __init__(self, sid, title, intro, info, parts=(), role="", subtitle=""):
        self.sid = sid
        self.title = title
        self.intro = intro          # two or three sentences under the screen title
        self.info = info            # the (i) text: what goes in, what comes out
        self.parts = list(parts)    # [(heading, explanation, [stage ids])]
        # "Your part": one sentence on what the person does here, shown in a
        # callout under the intro. The intro says what the screen is; this says
        # what to press, so a new lab member is never left reading a list.
        self.role = role
        # The step list's one line under the title: automatic, or you decide.
        self.subtitle = subtitle

    def visible_parts(self):
        """The parts, keeping only the stage ids the table in use defines."""
        out = []
        for heading, text, ids in self.parts:
            ids = [sid for sid in ids if sid in S.BY_ID]
            if ids:
                out.append((heading, text, ids))
        return out

    def stage_ids(self):
        return [sid for _h, _e, ids in self.visible_parts() for sid in ids]

    def __repr__(self):
        return f"<Screen {self.sid}>"


# Screen.stage_ids() drops any id the stage table does not define, so a part can
# name a stage from a pull request that has not merged yet.
#: Ids named below that the stage table on main does not define (yet). Listed
#: so a typo in a screen part is still caught by the tests.
OTHER_TABLE = set()


HOME = Screen(
    "home", "Project",
    "Where this study stands and what to do next. Every step below shows how far "
    "it has got; the Next up card names the one thing to do now.",
    "<p>A <b>project</b> is one study: its settings, the decisions you made in the "
    "app, and a record of every stage that ran. It lives in one file, which "
    "File &gt; Save writes.</p>"
    "<p>Progress is read from the output folder, so it is right even for work "
    "done outside the app.</p>",
    role="Read the Next up card and press Go. Come back here any time to see where "
         "the study stands.",
    subtitle="Where things stand")

SETUP = Screen(
    "setup", "Set up",
    "Tell the app about this study: where the slides are and how their files are "
    "named, how they were imaged, which atlas to use and how nuclei are counted. "
    "Do the cards in order on a new study; come back to change anything later.",
    "<p>Every setting here is stored in the project file and read by the stages "
    "when they run. Changes are kept in the project as you press <b>Apply</b> and "
    "are saved automatically before any stage runs.</p>"
    "<p>Each field shows a short hint; its (i) button gives the full reasoning "
    "and which stages read it. The ↺ button puts a field back to its default.</p>",
    role="Check each tab and press Apply on any you change. What the slide files say "
         "is already filled in. Nothing runs yet, so you cannot break anything.",
    subtitle="Tell the app about your slides")

PREPARE = Screen(
    "prepare", "Prepare",
    "The automatic part: read the slides, check them, build the atlas, and turn "
    "every section into a clean, upright, masked image ready for curation. "
    "Run all remaining does the stages in order and stops where it needs you.",
    "<p><b>Goes in:</b> the slide files and the settings from Set up.</p>"
    "<p><b>Comes out:</b> one standard image per section, the list of sections "
    "that can be measured, and the pages the curators show you.</p>"
    "<p>Prepare runs straight through to the ROI curator, with every section "
    "already turned upright. There you bring back any section the program set "
    "aside in its Review mode.</p>"
    "<p>Every stage can be re-run on its own. Stages skip work already done, so "
    "re-running is cheap.</p>",
    parts=[
        ("Read and check the slides",
         "Reads every slide file, checks it, exports a picture of every section and "
         "measures where the camera saturated.",
         ["manifest", "verify", "czi_selftest", "overviews", "contactsheets", "scene_renders", "pair",
          "channel_identity", "saturation_raw", "saturation_map"]),
        ("Build the atlas",
         "Turns the atlas into a set of plates with the regions marked. Only the "
         "stages for the atlas chosen in Set up are used.",
         ["atlas_extract", "atlas_remerge", "atlas_enforce", "atlas_rebuild",
          "atlas_wullimann_plates", "atlas_wullimann_polygons"]),
        ("Make the section images",
         "Masks bubbles and debris in both scans, makes a first standard image of "
         "every section, and sets aside the ones that cannot be measured.",
         ["artifact_pcna", "artifact_perk", "reformat_pcna", "exclusion"]),
        ("Final images",
         "Builds the final images of both scans with the exclusions applied and "
         "sets aside saturated sections.",
         ["censor_pcna", "reformat_pcna_final", "propagate_perk", "censor_perk",
          "reformat_perk_final", "sections_dataset"]),
        ("Get ready for the ROI curator",
         "Orders the curation queue and makes the colour pictures and Review "
         "thumbnails the ROI curator shows.",
         ["worklist", "rgb_perk", "rgb_pcna", "provenance"]),
    ],
    role="Press Run all remaining. It runs straight through to the ROI curator.",
    subtitle="Automatic: read, check, clean")

CURATE = Screen(
    "curate", "Curate",
    "The places where you decide. Each tab is one curator page: work in it, press "
    "its Export button, and the app picks your decisions up from there.",
    "<p>Your work in a curator is saved into the project as you go, so closing "
    "the app loses nothing. <b>Export</b> is what hands the decisions to the "
    "stages that come after.</p>"
    "<p>When the ROI curator is finished, press <b>Done: export and go to "
    "Measure</b> above the page. It exports for you and opens the next phase.</p>"
    "<p>The ROI curator's own <b>Review</b> button shows every scanned section and "
    "what happened to it, including the ones the pipeline excluded.</p>",
    parts=[
        ("Decisions", "", ["atlas_reframe", "roi_curator",
                           "import_curation"]),
    ],
    # 04a turns every section upright, so rotating is no longer a step of its
    # own; the curator's Rotate is there for the odd section it got wrong.
    role="In the ROI curator: bring back any sections that were set aside, place "
         "the regions, then press Done: export and go to Measure. Your work saves "
         "as you go.",
    subtitle="You decide: exclude, mark regions")

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
         ["tune_detection", "detect", "roi_dataset"]),
        ("Repairs for older results",
         "Only for a results file written by an older version of the pipeline. "
         "Each one backs the file up before changing it.",
         ["recensor", "off_tissue", "seg_provenance"]),
    ],
    role="Press Run all remaining and leave it. Detection takes hours; you can stop "
         "and resume.",
    subtitle="Automatic: find and count nuclei")

#: " and figures" while 06e draws them (S.R_FIGURES), else "": Results names
#: only what it makes.
_AND_FIGURES = " and figures" if S.R_FIGURES else ""

RESULTS = Screen(
    "results", "Results",
    "Joins the measurements to the experiment's groups and writes the "
    "spreadsheets%s. The first stage here unblinds the analysis." % _AND_FIGURES,
    "<p><b>Unblinding</b> means the treatment groups are read for the first time. "
    "The app asks you to confirm before it runs that stage.</p>"
    "<p>The spreadsheets themselves stay keyed by animal and can be rebuilt at "
    "any time, including while detection is still running.</p>",
    parts=[
        ("Unblind", "Reads the treatment groups for the first time.", ["join_sampling"]),
        ("Spreadsheets" + _AND_FIGURES,
         "The tables you analyse" + (" and the figures drawn from them."
                                     if S.R_FIGURES else "."),
         ["build_results", "excel_sample", "excel_slide", "refresh_loop"]),
    ],
    role="Press Run on Join the experiment once you are sure the regions are "
         "final, then build the spreadsheets.",
    subtitle="Unblind, then tables" + _AND_FIGURES)

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
    ],
    role="Nothing. This was done once for this microscope and does not need repeating.")

ALL = Screen(
    "all", "All stages",
    "Every stage of the pipeline in the order of the methods figure, with the "
    "same Run, Options and (i) buttons as the other screens. For when you know "
    "exactly which stage you want.",
    "<p>This is the app's previous layout. Stages are grouped by the bands of "
    "Figure 1 in <code>docs/pipeline-methods.md</code>.</p>",
    parts=[(g, "", [st.sid for st in S.in_group(g)]) for g in S.GROUPS],
    role="Only here if you know exactly which stage you want. New to the app? Use the "
         "steps on the left.")

#: The step bar, in order.
STEPS = [HOME, SETUP, PREPARE, CURATE, MEASURE, RESULTS]
#: Reached from the menus rather than the step bar.
EXTRA = [INSTRUMENT, ALL]
SCREENS = {s.sid: s for s in STEPS + EXTRA}

#: Shown on Measure but never part of Run all remaining: each brings a file an
#: older version wrote up to date, and is run by hand when that is the case.
REPAIRS = {"recensor", "off_tissue", "seg_provenance"}

#: Pictures for looking at that nothing downstream reads. Rendering every scene
#: takes long, so they get their own Run button instead of joining Run all
#: remaining, and they do not count towards a screen's progress.
VIEW_ONLY = {"scene_renders"}

#: Never planned by Run all remaining.
BY_HAND = REPAIRS | VIEW_ONLY

#: Run all remaining uses the one-pass Build results stage instead of the
#: spreadsheet stages it already runs. They keep their own Run buttons.
COVERED = {"excel_sample", "excel_slide"}

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
    "overviews": "Saves a picture of every section at one fixed size and brightness, "
                 "scores each for focus and saturation, and marks where the camera hit "
                 "its maximum.",
    "contactsheets": "Puts each animal's sections side by side in brain order, so you can "
                     "browse the whole dataset quickly.",
    "scene_renders": "A picture of every scene on every slide, drawn from the scan files for "
                     "looking at. Each marker gets two folders: the stain with the nuclei, and "
                     "the stain alone in grey. Nothing is measured from them. For a much "
                     "faster run, Set up > Pictures can draw them from the preview copy "
                     "Zeiss stored in each scan file instead.",
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
    "saturation_raw": "Repair for older studies: saves the per-section mask of pixels where the "
                      "camera hit its maximum. New studies get it from the overviews step.",
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
    "exclusion": "Finds sections that cannot be measured, such as torn or empty ones, "
                 "and sets them aside. You can bring any back in the ROI curator's Review.",
    "artifact_pcna": "Finds bubbles, clumps and fibres inside the {m1} sections and masks them "
                     "out. Works on the overviews, so it runs before you curate.",
    "censor_pcna": "Sets aside {m1} sections with too many saturated pixels and flags the "
                   "rest.",
    "reformat_pcna_final": "The final {m1} images: exclusions applied, artifacts masked, "
                           "saturation flagged.",
    "propagate_perk": "Copies the {m1} exclusions and framing onto the matching {m0} scans.",
    "artifact_perk": "Finds and masks artifacts in the {m0} sections.",
    "censor_perk": "Sets aside {m0} sections with too many saturated pixels and writes the "
                   "list of measurable sections.",
    "reformat_perk_final": "The final {m0} images with the exclusions carried over, ready "
                           "for measuring. Excluded sections are drawn too, for Review.",
    "sections_dataset": "A table of every {m0} section and what happened to it.",
    "worklist": "Orders the ROI curation queue so that stopping part way still leaves every "
                "animal equally covered.",
    "rgb_perk": "Colour pictures of the {m0} sections (nuclei blue, marker in colour) for "
                "the ROI curator, and the small ones for its Review grid.",
    "rgb_pcna": "The same colour pictures and Review thumbnails for {m1}.",
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
    "tune_detection": "Measures how bright, clean and even the signal is on a sample of "
    "sections, then picks the nucleus-detection setting that suits it. One setting for "
    "the whole study; you can override it in Set up.",
    "detect": "Finds every nucleus inside your regions and measures the marker in each. "
              "Takes hours; can be stopped and resumed.",
    "recensor": "Recomputes the saturation flag on an older results file.",
    "off_tissue": "Flags nuclei outside the tissue on an older results file.",
    "seg_provenance": "Adds missing detection details to an older results file.",
    "roi_dataset": "Turns the nuclei into per-region numbers: how many cells, how many are "
                   "positive, per square millimetre. Still blind.",
    "join_sampling": "Unblinds: joins each animal to its treatment group from the sampling "
                     "workbook.",
    "excel_sample": "The spreadsheet with one row per region per animal."
                    + (" The figures read this one." if S.R_FIGURES else ""),
    "excel_slide": "The same spreadsheet one level finer, per slide.",
    "build_results": "Rebuilds the per-region numbers and both spreadsheets in one go. "
                     "The R figures are switched off for now. Does not unblind; that "
                     "stays its own step.",
    "refresh_loop": "Rebuilds the spreadsheets every hour while detection runs (R figures "
                    "switched off for now). "
                    "Run from refresh_loop.bat.",
}

# The (i) text for a curator tab: what you do there and what Export hands on.
CURATOR_HELP = {
    "atlas_reframe": "<p>Each atlas figure may show several brain sections. Draw one box "
                     "around each; boxes are proposed from the tissue bands, so only "
                     "figures with several sections usually need a look.</p>"
                     "<p><b>Export</b> writes <code>atlas/plate_boxes.csv</code>. Then press "
                     "<b>Continue Prepare</b> to render the final plates.</p>",
    "roi_curator": "<p>For each section: choose the matching atlas plate, place the "
                   "numbered landmarks, draw each region as an outline, and place "
                   "background discs. Press <b>Rotate</b> and drag to turn a section "
                   "upright; that only changes the view, never where a region lands. "
                   "The queue is ordered so stopping part way still covers every "
                   "animal.</p>"
                   "<p><b>Review</b> in the page shows every scanned section and every "
                   "decision made about it. <b>Export</b> writes the three CSV files "
                   "Measure reads.</p>"
                   "<p>When you have finished, press <b>Done: export and go to "
                   "Measure</b> above the page: it exports and takes you on.</p>",
}

#: What counts as "you have exported your decisions" for each curator. done()
#: in stages.py only says the PAGE exists; what lets the next stage run is the
#: export, which lands beside the page (or, stamped, under the export folder).
DECISION_FILES = {
    "atlas_reframe": ["atlas/plate_boxes.csv"],
    "roi_curator": ["reformatted/roi_regions*.csv", "reformatted/*/roi_regions*.csv",
                    "{export}/roi_regions*.csv", "{export}/*/roi_regions*.csv"],
}

#: Curator stages that only apply to one atlas source.
ATLAS_ONLY = {
    "salmon": {"atlas_extract", "atlas_remerge", "atlas_reframe", "atlas_enforce",
               "atlas_rebuild"},
    "wullimann1996": {"atlas_wullimann_plates", "atlas_wullimann_polygons"},
}

#: The stages that make an atlas's plates. The built-in atlases' plates ship with
#: the app (the repository's atlas/ folder) and are copied into the study when it
#: first needs them, so for those these stages are not part of the study unless
#: Set up > Atlas > Rebuild atlas plates is on.
ATLAS_BUILD = ATLAS_ONLY["salmon"] | ATLAS_ONLY["wullimann1996"]

#: Where the shipped plate sets are; LS_ATLAS_BUNDLE moves them (empty: none ship).
BUNDLED_ATLAS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "atlas")


def plates_ship(study):
    """True when the study's atlas plates come ready-made with the app."""
    cfg = getattr(study, "cfg", None) or {}
    if cfg.get("atlas_rebuild_plates"):
        return False
    bundle = os.environ.get("LS_ATLAS_BUNDLE", BUNDLED_ATLAS)
    if not bundle:
        return False
    source = study.atlas_source
    if source == "wullimann1996":
        set_dir = "plates_wullimann"
    elif source == "salmon":
        set_dir = (cfg.get("atlas_plate_set") or {}).get("dir") or "plates"
    else:
        return False
    return os.path.isfile(os.path.join(bundle, set_dir, "plates.csv"))


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
        "How large the colour pictures are drawn when Set up > Pictures takes "
        "them from the overviews; 3 gives 768 px. Pictures drawn from the scan files "
        "use the resolution set there instead, and ignore this. Only the sampling "
        "changes; the section's frame and rotation stay exactly the same.",
        default=3, cli_default=3, minimum=1, maximum=8),
    Opt("--include-excluded", "bool", "Include excluded sections",
        "Also make pictures of sections the pipeline excluded, so Review can show them "
        "in colour."),
    Opt("--thumb-size", "int", "Thumbnail size (px)", "Edge length of the Review grid "
        "pictures made in the same run.", default=256, cli_default=256, minimum=64,
        maximum=1024),
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
    # Folder, resolution and brightness are study settings (Set up > Pictures).
    "scene_renders": [
        _force("pictures"), _limit("scenes"),
    ],
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
    "artifact_pcna": [_limit()],
    "artifact_perk": [_limit()],
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
    "tune_detection": [
        Opt("--sections", "int", "Sections to sample",
            "Per marker, spread evenly over the animals.", default=12, cli_default=12,
            minimum=1),
        Opt("--dry-run", "bool", "Report only",
            "Writes the report but does not change the study's settings."),
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
    "join_sampling": [
        Opt("--xlsx", "file", "Sampling spreadsheet",
            "The experiment's sampling workbook: one row per fish with its tanks and "
            "treatment. Empty uses 'large scale exp sampling data.xlsx' in the slide "
            "folder."),
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
    ("pictures", "Pictures",
     "The pictures Prepare draws for you to look at: the colour pictures in the ROI "
     "curator, and a picture of every scene. Measuring always reads the scan's own "
     "pixels, whatever this says.",
     ["curation_render.source", "curation_render.um_per_px",
      "scene_render.out_dir", "scene_render.um_per_px", "scene_render.source",
      "scene_render.range"]),
    ("atlas", "Atlas",
     "Which brain atlas the regions come from. Choose a built-in one or import your own.",
     ["atlas_source", "atlas_rebuild_plates", "atlas_pdf", "atlas_polygon_mirror",
      "atlas_plate_set.dir", "atlas_figure_sections.two_sections"]),
    ("detection", "Nucleus detection",
     "How nuclei are found and counted inside your regions.",
     ["detection.nucleus_diameter_um", "detection.abercrombie.enabled",
      "detection.stardist.use_auto", "detection.stardist.prob_thresh",
      "detection.stardist.nms_thresh",
      "detection.threshold.mad_k", "detection.threshold.min_area_um2"]),
]

#: Sub-headings inside a card, shown before the key they name: (heading, one line).
SECTION_HEADINGS = {
    "curation_render.source": (
        "Curation pictures",
        "How sharp the colour pictures in the ROI curator are, and where their pixels "
        "come from. Pictures already made keep their old look until the colour "
        "pictures run again with Redo."),
    "scene_render.out_dir": (
        "Scene pictures",
        "Where the pictures of every scene go, how sharp they are, where their "
        "pixels come from and how they are brightened."),
}

OTHER_SECTION = ("other", "Other settings",
                 "Settings not yet sorted into a card above.")

#: The settings Set up asks every study for. Everything else either has one right
#: answer the slide files give (pixel size, how files are named, the channels:
#: see autodetect.py), lives under the output folder by default, or is a method
#: choice with a sound default - and sits under "Show advanced settings". A
#: setting that is required and still empty is shown whatever this says, so
#: hiding never hides something Set up is waiting for.
BASIC = {
    "source_dir", "out_root",
    "slide_naming.pattern",
    "section_thickness_um",
    "atlas_source",
}


def is_basic(path):
    return path in BASIC


def setting_sections(fields, advanced=True, empty=()):
    """[(sid, title, intro, [Key])] for the given dialog fields; unsorted keys go last.

    A key added to ls_config.SPEC appears in Set up without anyone editing this
    file: it lands in Other settings until it is placed.

    With `advanced` False only the BASIC settings are offered, plus any key named
    in `empty` (required ones with no value yet); a card left with nothing to
    show is left out.
    """
    by_path = {k.path: k for k in fields}
    placed, out = set(), []
    for sid, title, intro, paths in SETTING_SECTIONS:
        keys = [by_path[p] for p in paths if p in by_path]
        placed.update(k.path for k in keys)
        if not advanced:
            keys = [k for k in keys if is_basic(k.path) or k.path in empty]
        if keys:  # a card whose settings this ls_config does not have yet stays hidden
            out.append((sid, title, intro, keys))
    rest = [k for k in fields if k.path not in placed]
    if not advanced:
        rest = [k for k in rest if k.path in empty]
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


#: The names the step screens use where the stage table's is the pipeline's own
#: shorthand (self-test, reformat, censor). The table's name stays what the log,
#: the guide and All stages say, so `technical=True` gives it back.
BASIC_TITLES = {
    "verify": "Check every slide file is complete",
    "czi_selftest": "Check the slide files for faults",
    "reformat_pcna": "Standard image of every PCNA section",
    "censor_pcna": "Leave out over-bright pixels - PCNA",
    "reformat_pcna_final": "Final PCNA images, cleaned",
    "censor_perk": "Leave out over-bright pixels - pERK",
    "reformat_perk_final": "Final pERK images, cleaned",
    "roi_geometry_verify": "Check the regions land back on the slides",
    "build_results": "Build results: datasets, spreadsheets"
                     + (", figures" if S.R_FIGURES else ""),
    "refresh_loop": "Refresh datasets%s hourly" % _AND_FIGURES,
}


def display_title(stage, study=None, technical=False):
    """The stage's name without its script number, in this study's marker names.

    The table spells the LS markers out (pERK is the first marker, PCNA the
    second); a study with other markers sees its own. Plain words from
    BASIC_TITLES unless `technical`, which is the advanced view's name.
    """
    _code, name = split_title(stage.title)
    if not technical:
        name = BASIC_TITLES.get(stage.sid, name)
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
        if stage.sid in ATLAS_BUILD and plates_ship(study):
            return False, ("Not needed: this atlas's plates come ready-made with the app. "
                           "Turn on Set up > Atlas > Rebuild atlas plates to make them again.")
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


_ROWS = {}


def _rows(path):
    """The rows of a small CSV as lists, or None when there is no file.

    Kept per (size, mtime), like _content's hashes: the status asks on every
    refresh, and the files here (the worklist, an export, 05c's done log) are a
    row per section, not per nucleus.
    """
    try:
        info = os.stat(path)
    except OSError:
        return None
    stamp = (info.st_size, info.st_mtime_ns)
    hit = _ROWS.get(path)
    if hit and hit[0] == stamp:
        return hit[1]
    import csv
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
    except (OSError, UnicodeDecodeError, csv.Error):
        return None
    _ROWS[path] = (stamp, rows)
    return rows


def _column(path, name, where=None):
    """The set of `name` values in a CSV with a header (rows passing `where`), or None."""
    rows = _rows(path)
    if not rows or name not in rows[0]:
        return None
    head = rows[0]
    i = head.index(name)
    return {r[i] for r in rows[1:] if len(r) > i and r[i]
            and (where is None or where(dict(zip(head, r))))}


def _first(stage_id, artifact, study):
    """The path of a stage's `artifact` output for marker 0 that exists, or None."""
    for e in S.BY_ID[stage_id].outputs:
        if isinstance(e, S.Art) and e.artifact == artifact:
            return next((p for p in e.resolve(study.names)[1] if os.path.exists(p)), None)
    return None


def curated_sections(study):
    """(sections decided, sections in the worklist) for the ROI curator, or None.

    Decided is curated (a region in the newest export) or excluded in the
    curator (roi_plates.csv beside it, excluded=1, as 05a reads it). None when
    there is no worklist (04n) to count against: then the export alone is all
    there is to go on.
    """
    queue = _column(os.path.join(study.out_root, "reformatted", "roi_worklist.csv"),
                    "scene_uid")
    if not queue:
        return None
    regions = decision_file("roi_curator", study)
    decided_ = set()
    if regions:
        decided_ |= _column(regions, "scene_uid") or set()
        plates = os.path.join(os.path.dirname(regions),
                              os.path.basename(regions).replace("roi_regions", "roi_plates"))
        decided_ |= _column(plates, "scene_uid",
                            lambda r: r.get("excluded") == "1") or set()
    return len(queue & decided_), len(queue)


def curator_finished(sid, study):
    """True when curator `sid` is exported and, for the ROI curator, every
    worklist section in that export is curated or excluded."""
    if not (decided(sid, study) and S.BY_ID[sid].done(study.out_root, study.names)):
        return False
    count = curated_sections(study) if sid == "roi_curator" else None
    return count is None or count[0] >= count[1]


def incomplete(stage, study):
    """'640 of 1,980 sections' when a stage's outputs exist but cover only part
    of what it measures, else None.

    Only 05c today: roi_nuclei.csv exists after its first section, and it resumes
    by section. Measured is what its done log (05c DONE_LOG) holds for marker 0,
    the marker the app runs it for; the total is the sections of 05a's boxes,
    the curated sections it will measure. A log with no `marker,*` line predates
    the log, and 05c trusts such rows, so nothing is counted against them.
    """
    if stage.sid != "detect" or not study.markers or not stage.done(study.out_root,
                                                                    study.names):
        return None
    boxes = _first("roi_geometry", "roi_boxes", study)
    want = _column(boxes, "scene_uid") if boxes else None
    log = _rows(os.path.join(study.out_root, "results", "roi_nuclei_sections_done.csv"))
    if not want or log is None:
        return None
    mine = {r[1] for r in log if len(r) >= 2 and r[0] == study.markers[0]}
    if "*" not in mine:
        return None
    done_ = len(want & mine)
    return None if done_ >= len(want) else "%s of %s sections" % (
        format(done_, ","), format(len(want), ","))


def state(stage, study, running=None):
    """One word for a stage's status on screen.

    running / failed   from this session
    notused            this study does not use it
    manual             the app cannot run it here (cli_only)
    done / todo        from the output folder
    outdated           done, but made from something that has changed since
    todo               also when its outputs cover only part of the job (incomplete)
    """
    running = running or {}
    if running.get(stage.sid) in ("running", "failed", "queued"):
        return running[stage.sid]
    ok, _why = applies(stage, study)
    if not ok:
        return "notused"
    if stage.cli_reason():
        return "manual"
    if not stage.done(study.out_root, study.names):
        return "todo"
    if outdated(stage, study):
        return "outdated"
    return "todo" if incomplete(stage, study) else "done"


def screen_stages(screen_sid, include_curator_pages=True):
    """The stage ids Run all remaining works through on a screen: its own, and on
    Prepare the curator pages too (see run_plan)."""
    wanted = set(SCREENS[screen_sid].stage_ids())
    if include_curator_pages and screen_sid == "prepare":
        wanted |= {st.sid for st in S.STAGES if st.curator and st.sid not in HIDDEN}
    return wanted


def run_plan(screen_sid, study, include_curator_pages=True):
    """(stages to run, in order; {stage id: what it waits for}) for Run all remaining.

    A stage is planned when it is used by this study, the app can run it, its
    outputs are missing or out of date, and everything it needs is done (and
    current) or planned ahead of it. A stage that needs a curator you have not exported yet is held, along
    with everything behind it, and named in the second value so the screen can
    say what it is waiting for. Prepare also builds the curator PAGES whose
    inputs are ready, because building a page is automatic - only using it is
    yours.
    """
    wanted = screen_stages(screen_sid, include_curator_pages)
    plan, held, planned = [], {}, set()
    for st in S.STAGES:
        if st.sid not in wanted or st.sid in HIDDEN or st.sid in BY_HAND | COVERED:
            continue
        # Unblinding is always a person's decision, made with its own Run and a
        # confirmation. No stage needs it, so leaving it out holds nothing back.
        if st.unblinds:
            continue
        ok, _ = applies(st, study)
        if not ok or st.cli_reason():
            continue
        if (st.done(study.out_root, study.names) and not outdated(st, study)
                and not incomplete(st, study)):
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
            # An out-of-date need this plan does not run (05c where StarDist is
            # missing) holds what is made from it, as a missing one does.
            if need not in planned and (not n.done(study.out_root, study.names)
                                        or outdated(n, study) or incomplete(n, study)):
                waits = need
                break
        if waits:
            held[st.sid] = waits
            continue
        plan.append(st.sid)
        planned.add(st.sid)
    return plan, held


def progress(screen_sid, study):
    """(done, total) over the stages of a screen that this study uses and the app runs.

    Over the same stages as run_plan, so Prepare's count includes the curator
    pages it builds: counted apart, "0 of 20 stages" sat beside "21 stages left
    to run". A page counts once it is built; the export is Curate's to count.
    """
    done = total = 0
    own = set(SCREENS[screen_sid].stage_ids())
    for sid in screen_stages(screen_sid):
        st = S.BY_ID[sid]
        if sid in BY_HAND or not applies(st, study)[0] or st.cli_reason():
            continue
        total += 1
        if st.operator and sid in own:
            done += decided(sid, study) and st.done(study.out_root, study.names)
        else:
            done += (st.done(study.out_root, study.names) and not outdated(st, study)
                     and not incomplete(st, study))
    return done, total


def _curate_by_section(study):
    """True when Curate's progress is the ROI curator's sections: every other
    curator is finished and there is a worklist to count against."""
    sids = curators(study)
    return ("roi_curator" in sids and curated_sections(study) is not None
            and all(curator_finished(s, study) for s in sids if s != "roi_curator"))


def step_progress(screen_sid, study):
    """(done, total) for a step, as the step list and Project show it.

    Curate counts curators exported rather than stages: building a curator's
    page is Prepare's work, and what Curate asks of you is the export. Once only
    the ROI curator is left, it counts sections against the worklist instead:
    an export of five sections is an export, and it is not the curation done.
    """
    if screen_sid == "curate":
        if _curate_by_section(study):
            return curated_sections(study)
        sids = curators(study)
        done = sum(1 for sid in sids
                   if decided(sid, study) and S.BY_ID[sid].done(study.out_root, study.names))
        return done, len(sids)
    return progress(screen_sid, study)


#: Set up's first tab, which holds no settings of its own: what it still needs
#: is said as whole phrases (slides_left), not as settings with no value.
SLIDES_CARD = "Slides"

#: The names a marker has before anyone has named it: the template's Marker1,
#: the Channel2 a header read gives a channel with no name, marker_label's
#: "marker 1". Any of these in a workbook or a folder name is a marker nobody
#: will recognise later.
_PLACEHOLDER_MARKER = re.compile(r"^(marker|channel)\s*\d*$", re.IGNORECASE)


def slides_left(cfg, read, files):
    """What the Slides card still needs, as plain phrases; [] when it is done.

    A folder being set is not the card being done. A file whose name the rule
    misses is invisible to every stage, and a marker still called Marker1 ends
    up as Marker1 in every output, so Set up said Done over both. `read` is
    whether this folder's headers were read (by New study, Read settings from
    the slides, or Prepare's manifest); `files` the slide file names in it.
    """
    import ls_channels as CH
    import naming_build as B
    if not cfg.get("source_dir"):
        return ["choose the slides folder"]
    out = []
    if not read:
        out.append("read the slides (Read settings from the slides)")
    naming = cfg.get("slide_naming") or {}
    if naming.get("pattern") and files:
        failed = B.preview(files, naming["pattern"],
                           bool(naming.get("case_insensitive")))["failed"]
        if failed:
            out.append("%d file name%s fit the rule (Slide file names)"
                       % (failed, "s don't" if failed != 1 else " doesn't"))
    ident = cfg.get("marker_identity")
    ident = ident if isinstance(ident, dict) else {}
    markers = CH.marker_names(cfg)
    labels = [ident.get(m) if isinstance(ident.get(m), str) and ident.get(m) else m
              for m in markers]
    if not labels or any(_PLACEHOLDER_MARKER.match(str(n).strip()) for n in labels):
        out.append("name the markers (Microscope and channels > Channels and markers...)")
    return out


def progress_words(screen_sid, done, total, study=None):
    """'5 of 6 cards', '10 of 19 stages', '0 of 1 exported', or 'Done'.

    With `study`, Curate's count is said in sections when step_progress counted
    sections: '640 of 1,980 sections curated'.
    """
    if not total:
        return "Nothing to do"
    if done >= total:
        return "Done"
    if screen_sid == "curate" and study is not None and _curate_by_section(study):
        return f"{done:,} of {total:,} sections curated"
    unit = {"setup": "cards", "curate": "exported"}.get(screen_sid, "stages")
    return f"{done} of {total} {unit}"


def curators(study):
    """The Curate tabs this study uses, in order."""
    return [sid for sid in ("atlas_reframe", "roi_curator")
            if applies(S.BY_ID[sid], study)[0]]


#: Stages that finish successfully with some items left undone, and the line in
#: their output that says how many. 01l skips a scene that does not fit in free
#: memory (or whose CZI is missing) and carries on, so its exit code alone
#: would read as a full run.
SHORTFALL = {
    "scene_renders": (re.compile(r"^\s*(\d+) not drawn:", re.M), "scene", "not drawn"),
    # 04o: a section whose CZI or scene is missing, or with no tissue mask.
    "rgb_perk": (re.compile(r"^\s*(\d+) not built:", re.M), "picture", "not built"),
    "rgb_pcna": (re.compile(r"^\s*(\d+) not built:", re.M), "picture", "not built"),
}


#: Stages whose real output is a folder of pictures to look through, and where
#: that folder is. The scene pictures may sit outside the study (Set up says).
FOLDERS = {
    "scene_renders": ("Open pictures", lambda cfg: _scene_dir(cfg)),
    "overviews": ("Open pictures", lambda cfg: os.path.join(cfg["out_root"], "overviews")),
    "contactsheets": ("Open sheets", lambda cfg: os.path.join(cfg["out_root"], "contactsheets")),
    "build_results": ("Open results", lambda cfg: os.path.join(cfg["out_root"], "results")),
}


def _scene_dir(cfg):
    import ls_config as LC
    return LC.scene_render_dir(cfg)


def output_folder(sid, cfg):
    """(button label, folder) for a stage in FOLDERS, else None."""
    entry = FOLDERS.get(sid)
    if not entry or not cfg or not cfg.get("out_root"):
        return None
    folder = entry[1](cfg)
    return (entry[0], folder) if folder else None


def shortfall(sid, output):
    """'3 scenes not drawn' when a finished stage left some undone, else None."""
    rule = SHORTFALL.get(sid)
    if not rule or not output:
        return None
    m = rule[0].search(output)
    if not m:
        return None
    n = int(m.group(1))
    return "%d %s%s %s" % (n, rule[1], "" if n == 1 else "s", rule[2])


#: Stages whose real output is a report to read, and where it is in out_root.
REPORTS = {
    "tune_detection": ("Open report", os.path.join("qc", "detection_tuning.md")),
}


def report_file(sid, study):
    """The report a stage in REPORTS wrote, or None while there is none."""
    entry = REPORTS.get(sid)
    if not entry or not study.out_root:
        return None
    path = os.path.join(study.out_root, entry[1])
    return path if os.path.isfile(path) else None


def _signal_checks(study):
    """[(marker as a person knows it, verdict, reasons)] from 05b's detection.auto.

    Only the study's own markers: a marker dropped from the study keeps its old
    entry in the file, and it is no reason to hold anything.
    """
    det = study.cfg.get("detection") if isinstance(study.cfg, dict) else None
    auto = det.get("auto") if isinstance(det, dict) else None
    if not isinstance(auto, dict):
        return []
    ident = study.cfg.get("marker_identity") or {}
    out = []
    for m in study.markers or list(auto):
        e = auto.get(m)
        if not isinstance(e, dict) or not e.get("verdict"):
            continue
        name = ident.get(m) if isinstance(ident, dict) else None
        out.append((name if isinstance(name, str) and name else m,
                    e["verdict"], [str(r) for r in e.get("reasons") or ()]))
    return out


def signal_words(study):
    """'AF568 signal poor, AF488 good' for the signal check's row, or None."""
    checks = _signal_checks(study)
    if not checks:
        return None
    return ", ".join(("%s signal %s" if i == 0 else "%s %s") % (name, verdict)
                     for i, (name, verdict, _r) in enumerate(checks))


def run_hold(done_sid, queue, study):
    """(title, message) when Run all remaining should stop after `done_sid`, else None.

    A "poor" signal check means dim nuclei will be missed whatever the settings,
    and detection after it takes hours. So a run that would go straight on into
    detection stops here for a person to read the report. The check itself has
    finished and its settings are written: it is a pause, not a failure, and
    pressing Run on detection carries on anyway.
    """
    if done_sid != "tune_detection" or "detect" not in queue:
        return None
    poor = [(name, reasons) for name, verdict, reasons in _signal_checks(study)
            if verdict == "poor"]
    if not poor:
        return None
    check = display_title(S.BY_ID["tune_detection"], study)
    detect = display_title(S.BY_ID["detect"], study)
    lines = "\n".join("  %s: %s" % (name, "; ".join(reasons) or "no reason given")
                       for name, reasons in poor)
    names = " and ".join(name for name, _r in poor)
    return ("Stopped before detection: weak signal",
            "The signal check found weak signal for %s:\n%s\n\n"
            "Detection takes hours, so the run stopped before it. Read the report "
            "(Open report on the \"%s\" row) and the QC pictures first. To carry on "
            "anyway, press Run on \"%s\"." % (names, lines, check, detect))


#: When the ROI fixes reached main (PRs #21 and #24, 2026-10-14 12:37 UTC): 05c
#: decides `censored` on the native pixels, and 05a/05c move curated shapes half
#: a grid pixel onto pixel centres. Nuclei measured before then keep the old
#: flag and the old boundary until ROI placement and detection run again.
ROI_FIX_AT = 1791981478   # 2026-10-14T12:37:58Z

#: Code changes that made a stage's earlier results wrong: {stage id: (when the
#: fix reached main, what to say)}. Results older than the fix are out of date,
#: like results older than what they read. Read off the outputs' age: nothing
#: inside them says which version wrote them, and a file copied since then would
#: hide an old run, so this is a reminder, not a guarantee.
FIXED_AT = {
    "roi_geometry": (ROI_FIX_AT, "placed before the 14 Oct fix that moves regions half "
                                 "a pixel onto where you drew them"),
    "detect": (ROI_FIX_AT, "measured before the 14 Oct fixes to region placement and to "
                           "the saturated-nucleus flag (now read from the scan's own pixels)"),
}

#: Settings a tracked stage's script reads that do NOT put it out of date, and why.
#: tests/test_workflow.py holds every other setting ls_config says a tracked stage
#: reads to that stage's `settings`, so a new one cannot be missed by omission.
NOT_TRACKED = {
    "source_dir": "where the slides are, not what is measured from them; moving "
                  "them to another drive changes no result",
    "export_dir": "where the curator's exports are; the export itself is what is "
                  "compared",
    "groups.by_animal": "05c reads it only for the order it measures sections in, "
                        "and the finished file is the same either way",
}


def script_codes(stage):
    """The script numbers ls_config's `consumers` name a stage by: ("05c",)."""
    if stage.sid == "build_results":
        return ("06a", "06c", "06d", "06e")    # one pass of all three, then the figures
    return (split_title(stage.title)[0],) if stage.script else ()


_HASHES = {}


def _content(path):
    """A short hash of a file's bytes, or None when there is no file.

    By content rather than date: saving the same export again, or 05a writing the
    same boxes again, must not cost hours of detection. Kept per (size, mtime), so
    a status refresh hashes only what has changed since the last one.
    """
    try:
        info = os.stat(path)
    except OSError:
        return None
    if os.path.isdir(path):
        return "folder"
    # ctime too: a file rewritten to the same size with its old date put back is
    # still a different file.
    stamp = (info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino)
    hit = _HASHES.get(path)
    if hit and hit[0] == stamp:
        return hit[1]
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    _HASHES[path] = (stamp, h.hexdigest()[:16])
    return _HASHES[path][1]


def _input_paths(need, study):
    """The files a stage reads from stage `need`: a curator's newest export, else its outputs."""
    n = S.BY_ID[need]
    if n.operator:
        f = decision_file(need, study)
        return [f] if f else []
    out = []
    for entry in n.outputs:
        _shown, paths = n._alternatives(entry, study.out_root, study.names)
        out += [p for p in paths if os.path.exists(p)][:1]
    return out


def _setting(cfg, key):
    node = cfg
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    if key == "detection.auto" and isinstance(node, dict):
        # 05b writes its whole report here (the date, the count curve), but 05c
        # takes only the chosen cut from it; a marker with none uses the model's own.
        node = {m: e["chosen"] for m, e in node.items()
                if isinstance(e, dict) and e.get("chosen")} or None
    return node


def _hash(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


def fingerprint(sid, study):
    """What stage `sid` reads, as it is now: {"files": {need: hash}, "settings": {key: hash}}.

    None for a stage that is not tracked (its `made_from` is None).
    """
    st = S.BY_ID[sid]
    if st.made_from is None:
        return None
    files = {need: _hash([_content(p) for p in _input_paths(need, study)])
             for need in st.made_from if applies(S.BY_ID[need], study)[0]}
    settings = {k: _hash(_setting(study.cfg, k)) for k in st.settings}
    return {"files": files, "settings": settings}


def run_inputs(sid, values, study):
    """The fingerprint a run of `sid` with these options records, or None.

    Taken when the run STARTS, because that is what it reads: an export made while
    detection runs for hours is not in its results. A trial run of the first N
    sections records nothing, so it cannot make the whole stage look current.
    """
    if (values or {}).get("--limit"):
        return None
    return fingerprint(sid, study)


def _last_ok(study, sid):
    for rec in reversed(((study.cfg or {}).get("project") or {}).get("runs") or []):
        if rec.get("stage") == sid and rec.get("ok"):
            return rec
    return None


def _changed_from(need, study):
    n = S.BY_ID[need]
    if need == "roi_curator":
        return "your ROI export"
    if n.operator:
        return "your export from %s" % display_title(n, study)
    return "the output of %s" % display_title(n, study)


def _own_reason(st, study):
    """Why `st`'s own results no longer follow from what it reads, or None."""
    mine = [p for e in st.outputs
            for p in st._alternatives(e, study.out_root, study.names)[1][:1]
            if os.path.exists(p)]
    made = min((os.path.getmtime(p) for p in mine), default=None)
    fixed = FIXED_AT.get(st.sid)
    if fixed and made is not None and made < fixed[0]:
        return fixed[1]
    rec = _last_ok(study, st.sid)
    then = rec.get("inputs") if rec else None
    if isinstance(then, dict):
        now = fingerprint(st.sid, study)
        was = then.get("files") or {}
        files = [k for k in now["files"] if now["files"][k] != was.get(k)]
        if files:
            return "%s changed since it ran" % " and ".join(
                _changed_from(k, study) for k in files)
        was = then.get("settings") or {}
        keys = [k for k in now["settings"] if now["settings"][k] != was.get(k)]
        if keys:
            import ls_config as LC
            label = {k.path: k.label for k in LC.SPEC}
            return "a setting it reads changed since it ran: %s" % ", ".join(
                label.get(k, k) for k in keys)
        return None
    # Run before runs recorded what they read, or outside the app: fall back on
    # file dates, which can say an input is newer but not what a setting was.
    if made is None:
        return None
    for need in st.made_from:
        if applies(S.BY_ID[need], study)[0] and any(
                os.path.getmtime(p) > made for p in _input_paths(need, study)):
            return "%s is newer than its results" % _changed_from(need, study)
    return None


def outdated(stage, study, _seen=None):
    """Why a finished stage's results no longer match what they were made from, or None.

    Its own inputs or settings changed since its last run, a fix since made its
    results wrong (FIXED_AT), or a stage it is made from is out of date itself - so
    a re-export marks 05a, 05c, 06a and the results at once, and Run all remaining
    runs them again in order. Only the status and the plan change: nothing is
    deleted, and the outputs stay where they are until the stage runs.
    """
    if stage.made_from is None or not study.out_root:
        return None
    seen = {} if _seen is None else _seen
    if stage.sid in seen:
        return seen[stage.sid]
    seen[stage.sid] = None
    why = None
    if stage.done(study.out_root, study.names):
        why = _own_reason(stage, study)
        for need in stage.made_from if why is None else ():
            n = S.BY_ID[need]
            if applies(n, study)[0] and outdated(n, study, seen):
                why = "%s is out of date" % display_title(n, study)
                break
    seen[stage.sid] = why
    return why


def run_values(sid, values, study):
    """The options a run of `sid` uses: the saved ones, plus Redo when it is out of date.

    A stage that skips finished work (05c keeps every section that has rows) would
    otherwise run, change nothing and record itself current. So an out-of-date run
    redoes everything, which is what the old advice to switch Redo on said by hand.
    The saved options are not changed.
    """
    values = dict(values or {})
    if any(o.flag == "--force" for o in options_for(sid)) and outdated(S.BY_ID[sid], study):
        values["--force"] = True
    return values


def notices(screen_sid, study):
    """Warnings a screen shows above its stages.

    None today: the one there was (results older than the 14 Oct fixes) is now the
    stages' own Out of date status, through FIXED_AT.
    """
    return []


def next_up(study, setup_ok=True, setup_missing=()):
    """(screen id, what to say, stage id or None) - the single next thing to do.

    `setup_missing` is [(card title, [settings with no value])], so the words
    can say which settings rather than only that some are missing. The Slides
    card's entries are already whole phrases (slides_left), said as they are.
    """
    if not setup_ok:
        if not setup_missing:
            return "setup", "Finish Set up: some required settings are missing.", None
        gaps = "; ".join("; ".join(keys) if title == SLIDES_CARD
                         else "%s needs %s" % (title, ", ".join(keys))
                         for title, keys in setup_missing)
        return "setup", "Finish Set up: %s." % gaps, None
    plan, held = run_plan("prepare", study)
    if plan:
        return "prepare", "Run Prepare. It goes straight through to the ROI curator.", None
    for sid in curators(study):
        st = S.BY_ID[sid]
        if not st.done(study.out_root, study.names):
            continue
        if not decided(sid, study):
            if sid == "roi_curator":
                return "curate", ("Check what was set aside, place the regions, "
                                  "then export from the ROI curator."), sid
            return "curate", "Curate: work through %s and export your decisions." % \
                display_title(st, study), sid
    if held:
        waits = sorted(set(held.values()))
        names = ", ".join(display_title(S.BY_ID[w], study) for w in waits if w in S.BY_ID)
        return "curate", "Prepare is waiting for: %s." % names, waits[0]
    m_plan, _ = run_plan("measure", study)
    part = incomplete(S.BY_ID["detect"], study) if "detect" in m_plan else None
    if part:
        # Run all remaining resumes it: 05c skips every section in its done log.
        return "measure", "Detection %s, resume." % part, None
    if m_plan:
        return "measure", "Run Measure: %d stage%s left." % (
            len(m_plan), "" if len(m_plan) == 1 else "s"), None
    # Run all remaining leaves unblinding out, so it is named here on its own.
    for sid in SCREENS["results"].stage_ids():
        st = S.BY_ID[sid]
        if st.unblinds and applies(st, study)[0] and (
                not st.done(study.out_root, study.names) or outdated(st, study)):
            return "results", "Measurements are in. Unblind and build the results.", sid
    r_plan, _ = run_plan("results", study)
    if r_plan and not S.R_FIGURES:
        # 06e draws no figures while they are switched off: name only the tables.
        return "results", "Build the spreadsheets on Results.", None
    if r_plan:
        return "results", "Build the spreadsheets and figures on Results.", None
    # Measuring part of the worklist is allowed (04n orders it so any prefix is
    # a dataset), so this holds nothing above; it only stops "everything done"
    # being said over a curation that is not.
    count = curated_sections(study) if "roi_curator" in curators(study) else None
    if count and count[0] < count[1]:
        n, m = count
        return "curate", (f"{n:,} of {m:,} sections curated. Curate or exclude the rest "
                          "in the ROI curator."), "roi_curator"
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
