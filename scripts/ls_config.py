"""The pipeline's configuration: one spec table, one loader, one validator.

Until now the loader was six lines copy-pasted into 36 of the 45 numbered
stages - the other 9 chained off a sibling module instead - with no schema, no
defaults and no validation. A mistyped key was a `KeyError` at import; a key
nobody read looked exactly like a key everybody read.

`SPEC` below is the single source of truth. Every entry names a key, its type,
whether it is required, its default, a one-line hint used in error messages and
in the app's settings dialog, and the long-form operator note that belongs
beside it in a written config. Four things are generated from that one table:
validation, `config.example.json`, the docs reference, and the dialog fields.
That is why this is hand-rolled rather than jsonschema, which would supply the
first and leave the other three to drift.

Stages use it as:

    from ls_config import CONFIG, OUT_ROOT, SOURCE_DIR

`LS_CONFIG` names the file explicitly; the repo-root `config.json` is the
fallback. Frozen, the scripts sit inside `_internal/` while config.json is
beside the executable, so the file-relative fallback would point at a file that
does not exist - which is why `LS_CONFIG` exists and why every stage must
honour it.

An unknown key WARNS rather than fails. A stale config should surface, not
abort a twelve-hour run.
"""

import json
import os
import re
import sys

try:
    import ls_channels
except ImportError:                                         # pragma: no cover
    # Deliberately not fatal, and deliberately not moved into the validator
    # either. ls_channels is a sibling in this same directory, so this import
    # succeeds wherever `import ls_config` did - but ls_config is imported at
    # the top of all 45 stages, and a hard dependency here would turn "the
    # channel rules could not be loaded" into "no stage can start". The
    # validator skips the channel rules when this is None, as it always has.
    ls_channels = None

try:
    import atlas_registry
except ImportError:                                         # pragma: no cover
    # Same reasoning as ls_channels above: a sibling module, and a hard
    # dependency would turn "the atlas list could not be loaded" into "no stage
    # can start".
    atlas_registry = None

#: The built-in atlases, from the registry that owns the concept, so the
#: settings dialog's dropdown, the generated docs and `ls_atlas.source()` are
#: one list. A study's own atlases (the `atlases` block) are judged by the
#: registry at use; this is the list a fresh study is offered.
ATLAS_SOURCES = (tuple(atlas_registry.BUILTIN) if atlas_registry
                 else ("salmon", "wullimann1996"))

#: The two acquisition layouts. From ls_channels, which owns the concept, so
#: the spec's `choices` and the validator agree with the module that judges
#: them. The literal is the fallback for the import above having failed, where
#: there is nothing left to disagree with.
LAYOUTS = tuple(ls_channels.LAYOUTS) if ls_channels else ("multiplex",
                                                          "paired")

#: The colours a marker or the counterstain may be drawn in. From ls_channels
#: for the same reason LAYOUTS is: the settings dialog's choice widget, the
#: generated docs and the parser that judges a written value have to be one
#: list, or a study can be offered a colour the pipeline then refuses.
COLOUR_NAMES = tuple(sorted(ls_channels.NAMED_COLOURS)) if ls_channels else (
    "blue", "cyan", "green", "grey", "magenta", "orange", "red", "white",
    "yellow")

SCHEMA_VERSION = 1

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#: The salmon atlas shipped with the code; `atlas_pdf` falls back to it.
BUNDLED_ATLAS_PDF = os.path.join(REPO, "atlas", "Salmon Atlas full.pdf")

# Status of a key, as reported by the generated docs.
#   live       - read by at least one stage
#   documented - read by nothing, but carries operator knowledge a later
#                sub-project will wire. Emitted into the example config.
LIVE, DOCUMENTED = "live", "documented"

#: Distinguishes "no example given" from an example that is legitimately None.
_UNSET = object()


class Key:
    """One configuration key.

    `path` is dotted: "detection.abercrombie.enabled". `note` is the long-form
    text emitted as a sibling `_note` in a generated config - it is where the
    reasoning lives, and several of them are the only written record of an
    operator decision, so they are data, not decoration.
    """

    def __init__(self, path, type, doc, required=False, default=None,
                 note=None, consumers=(), status=LIVE, choices=None,
                 materialise=False, example=_UNSET, consumers_pending=False,
                 ui=None, label=None, in_example=True, minimum=None,
                 maximum=None):
        # How the settings dialog offers this key. None infers it from `type`;
        # False keeps it out of the dialog entirely, for a key the app writes
        # itself or one whose value is a table rather than a setting.
        self._ui = ui
        self._label = label
        # False keeps a key the app writes itself out of the generated template:
        # a new study must not start with someone else's record in it.
        self.in_example = in_example
        # Declared, with the stages that WILL read it named, but not consumed
        # yet. Such a key must not be required: requiring one before anything
        # reads it only breaks configs that were working.
        self.consumers_pending = consumers_pending
        self.path = path
        self.type = type
        self.doc = doc
        self.required = required
        self.default = default
        # What a generated config.example.json shows. Falls back to the
        # default; a required key with neither gets a <PLACEHOLDER>.
        self.example = example
        self.note = note
        self.consumers = tuple(consumers)
        self.status = status
        self.choices = choices
        # The smallest value a number may take, when the stage reading it refuses
        # anything below - checked here so Set up refuses it before a run does.
        self.minimum = minimum
        # The largest, for the same reason.
        self.maximum = maximum
        # A key the Fiji/Groovy stages read straight out of the JSON. They
        # cannot see a Python default, so it must be present as a literal in
        # every written config or they get null.
        self.materialise = materialise

    #: type -> how the settings dialog edits it. A type that is not here is a
    #: table or a list, which is not a one-line setting and is left out.
    UI_FOR_TYPE = {
        "path_dir": "dir", "path_dir_opt": "dir", "path_dir_create": "outdir",
        "path_file": "file", "float": "number", "int": "int", "bool": "bool",
        "str": "text", "str_opt": "text", "regex": "text",
    }

    @property
    def ui(self):
        """The widget kind, or None when this key is not offered in settings."""
        if self._ui is not None:
            return self._ui or None
        if self.status != LIVE:
            return None
        kind = self.UI_FOR_TYPE.get(self.type)
        return "choice" if kind == "text" and self.choices else kind

    @property
    def label(self):
        if self._label:
            return self._label
        leaf = self.parts[-1].replace("_", " ").strip()
        return leaf[:1].upper() + leaf[1:]

    @property
    def parts(self):
        return self.path.split(".")

    def __repr__(self):
        return f"<Key {self.path} {self.type}{' required' if self.required else ''}>"


# --------------------------------------------------------------------------
# The spec. Order here is the order keys appear in a generated config.
# --------------------------------------------------------------------------

SPEC = [
    Key("schema_version", "int",
        "Config format version. Written by the app; do not edit by hand.",
        required=True, default=SCHEMA_VERSION, ui=False, consumers=["ls_config"]),

    Key("study.name", "str",
        "A short name for this study, shown in the app's study picker.",
        default="", label="Study name", consumers=["app"]),

    # ---- paths -----------------------------------------------------------
    Key("source_dir", "path_dir",
        "The folder holding the .czi files.",
        required=True, materialise=True, label="Slides folder",
        example="<ABSOLUTE PATH TO THE FOLDER OF .czi FILES>",
        consumers=["00_manifest", "00b", "00d", "01b_pick", "01e", "01h",
                   "01_overviews", "05a", "05c", "06b", "app"]),

    Key("out_root", "path_dir_create",
        "Where every result is written. Needs room - overviews alone run to "
        "several GB.",
        required=True, materialise=True, label="Analysis output",
        example="<ABSOLUTE PATH FOR PIPELINE OUTPUT>", consumers=["every stage"]),

    Key("atlas_pdf", "path_file",
        "The atlas the plates and region seeds are extracted from. Empty means "
        "the salmon atlas shipped in the repository's atlas/ folder.",
        label="Atlas PDF", example="<ABSOLUTE PATH TO THE ATLAS PDF, OR EMPTY>",
        note="Only 04a, 04a2 and 04a4 open it, to extract plates again. The "
             "extracted plates themselves ship in atlas/ and are copied into "
             "<out_root>/atlas/ the first time a study needs them.",
        consumers=["04a_atlas_extract", "04a2", "04a4", "app"]),

    Key("export_dir", "path_dir_opt",
        "Where the ROI curator files its exports. Empty means "
        "<out_root>/exports.",
        default=None, label="Curator exports", example="<ABSOLUTE PATH FOR CURATOR EXPORTS, OR EMPTY>",
        note="One folder per export, named DD.MM.YYYY_HH.MM, with the same "
             "stamp on every file in it. Read by 04l (shown in the page, and "
             "the default for --export-dir) and by the app's download handler, "
             "so the two cannot disagree. A browser writes here only after the "
             "operator picks the folder with the page's Folder... button - a "
             "web page cannot be handed a path.",
        consumers=["04l", "05a", "app/curator_view"]),

    Key("source_files", "list_str_opt",
        "Optional allowlist of filenames to process. Empty means every file "
        "in source_dir.",
        default=None, consumers=["00_manifest", "app/import_slides"]),

    # ---- slide naming ----------------------------------------------------
    Key("slide_naming.pattern", "regex",
        label="Filename pattern",
        doc="Regex matching your slide filenames, with a named group 'subject'. "
        "Optional named groups 'slide' and 'replicate'; any others are "
        "carried through as manifest columns.",
        required=True,
        example=r"^(?P<subject>[A-Za-z]+\d+)_(?P<slide>\d+)(?P<replicate>[a-z])?\.czi$",
        note="Built by the app's pattern builder, and editable by hand. A "
             "filename that does not match is REPORTED, never silently "
             "skipped - an invisible file is the failure mode this whole "
             "block exists to prevent. `subject` becomes the `animal` column "
             "and the first field of every scene_uid, so it is the key the "
             "metadata join later runs on.",
        consumers=["00_manifest", "app/slides"]),

    Key("slide_naming.example", "str",
        "One real filename, used by the app to preview the parse.",
        default="", example="AB12_3a.czi", label="Example filename",
        consumers=["app/slides"]),

    Key("slide_naming.case_insensitive", "bool",
        "Match filenames case-insensitively.",
        default=True, label="Case-insensitive names",
        consumers=["00_manifest", "app/slides"]),

    # ---- acquisition geometry -------------------------------------------
    Key("section_order_convention", "str",
        "How serial section order is recovered from the scan.",
        required=True, default="S_scan_order", choices=["S_scan_order"],
        note="Confirmed by user 2026-08-11 for the LS dataset. Serial order = "
             "scene index as acquired. The scanner ran column-major (s0 "
             "top-left, s1 below it, s2 next column top), and on staggered "
             "slides such as LS120_1a no grid model reproduces the physical "
             "layout at all.",
        consumers=["00_manifest"]),

    Key("pixel_size_um", "float",
        "Camera pixel size at the objective used, in micrometres.",
        required=True, materialise=True, example=0.65,
        note="The CZI carries this too - czi_meta.py reads it from "
             "Scaling/Items/Distance[@Id='X'] - so a future revision should "
             "check this value against the files rather than trust it.",
        consumers=["01c", "01e", "01f", "01_overviews", "05a", "05c", "06a"]),

    Key("section_thickness_um", "float",
        "How thick the sections were cut, in micrometres.",
        required=True, example=14.0,
        note="NOT recoverable from the CZIs - they carry no size_z, being "
             "single-plane images of a physical section - so it has to be "
             "supplied. This is the number the Abercrombie correction needs; "
             "see detection.abercrombie below. 14.0 for the LS dataset, "
             "operator-stated 2026-08-30.",
        consumers=["06a"]),

    Key("overview_target_um_per_px", "float",
        "Resolution the per-section overviews are exported at.",
        required=True, default=5.2, materialise=True,
        consumers=["01_overviews", "01_overviews.groovy"]),

    Key("curation_render.source", "str",
        "Where the curation composites take their pixels from.",
        default="czi", choices=["czi", "overview"], label="Curation images from",
        note="`czi` renders each composite from the CZI's own 16-bit pixels "
             "in ONE resampling, onto the same frame the landmarks are stored "
             "in. `overview` is the old route: the 8-bit 5.2 um/px overview, "
             "squashed square and resampled three times, which shows about "
             "8 um per screen pixel. Existing composites are kept until 04o "
             "runs with --force.",
        consumers=["04o"]),

    Key("curation_render.um_per_px", "float",
        "Resolution of the curation composites rendered from the CZI.",
        default=2.6, label="Curation image resolution (um/px)",
note="1.3 at the finest. The CZI is 0.65 um/px, but a whole "
             "section at that resolution is one picture of 10,000+ px a "
             "side - several GB to render and more than a browser holds "
             "comfortably - so 04o refuses anything finer than 1.3. 2.6 is "
             "already twice as sharp per pixel as the overview route, and the "
             "curator's zoom shows every pixel of it. Measurement (05c) always "
             "reads native pixels, whatever this says.",
        minimum=1.3, consumers=["04o"]),

    Key("scene_render.out_dir", "path_dir_opt",
        "Where the pictures of every scene go. Empty means "
        "<out_root>/scene_renders.",
        default=None, label="Scene pictures folder",
        example="<ABSOLUTE PATH FOR SCENE PICTURES, OR EMPTY>",
        note="01l writes <folder>/<marker>/stain+DAPI/ and "
             "<folder>/<marker>/stain_only/ here, one PNG per scene. Any "
             "folder will do, including one outside the study: nothing "
             "downstream reads these pictures. The table saying how each was "
             "drawn always stays at <out_root>/qc/scene_renders.csv, so the "
             "app can tell the step has run wherever the pictures went.",
        consumers=["01l"]),

    Key("scene_render.um_per_px", "float",
        "Resolution of the scene pictures, in micrometres per pixel.",
        default=2.6, label="Scene picture resolution (um/px)",
        note="0.65 is the scan's own pixels, the finest there is; larger "
             "numbers give smaller, softer pictures (5.2 is the overviews' "
             "scale). Each picture pixel is the average of whole native 0.65 "
             "um pixels, so the value is rounded to a multiple of 0.65 and the "
             "table records what was drawn. Memory: a whole scene at 0.65 can "
             "need several GB. Each scene is checked against the memory (RAM "
             "plus page file) free "
             "at the time, and one that does not fit is skipped with a "
             "message rather than crashing the run; 1.3 needs a quarter of "
             "that, 2.6 a sixteenth.",
        minimum=0.65, consumers=["01l"]),

    Key("scene_render.source", "str",
        "Where the scene pictures take their pixels from: `scan` is exact; "
        "`zeiss_preview` is about ten times faster and changes the pictures "
        "slightly, never what is measured.",
        default="scan", choices=["scan", "zeiss_preview"],
        label="Scene pictures from",
        note="`scan` (the default) averages the scan's own 0.65 um pixels into "
             "each picture pixel, here. `zeiss_preview` instead loads the "
             "smaller copy of each scan that the Zeiss software stored inside "
             "the CZI when the slide was scanned: about ten times faster "
             "(15 of every 16 pixels are never unpacked), same resolution, but "
             "Zeiss did the averaging and compressed that copy separately, so "
             "pixel values differ slightly from `scan` and the edges of "
             "saturated (clipped) patches can look a little softer. Only the "
             "scene pictures change. Nothing is measured from them: detection "
             "and counting (05c), clipping masks, the overviews and the curator "
             "pictures always read the scan's own pixels, whatever this says. "
             "Fastest at 1.3, 2.6 or 5.2 um/px, where Zeiss stored a copy; at "
             "0.65 both choices read the scan itself. Changing it redraws the "
             "pictures on the next run.",
        consumers=["01l"]),

    Key("scene_render.range", "str",
        "How the scene pictures are brightened.",
        default="dataset", choices=["dataset", "section"],
        label="Scene picture brightness",
        note="`dataset` uses the overviews' frozen range "
             "(qc/display_ranges.json), so the same brightness means the same "
             "signal in every picture; the brightest nuclei can saturate at "
             "fine resolutions. `section` stretches each scene to its own "
             "tissue: every picture well exposed, but brightness no longer "
             "compares between pictures. A frozen range pushed to the top by "
             "clipped pixels is stretched per scene in either mode.",
        consumers=["01l"]),

    # ---- channels (replaced by the channels/markers sub-project) ---------
    # ---- acquisition ------------------------------------------------------
    Key("acquisition.layout", "str",
        "How the markers were imaged: one multi-channel scan per section, or "
        "one scan per marker.",
        required=True, default="multiplex", label="Acquisition layout",
        choices=list(LAYOUTS),
        note="`multiplex` is one scan per section carrying every marker as a "
             "channel, which is what a new study should be. `paired` is the "
             "LS layout: each marker is a separate physical scan of the same "
             "section, joined by 02_pair_passes. Paired declares no channel "
             "table, because each of its scans carries the nuclear channel "
             "plus whichever marker that pass used.",
        consumers=["01_overviews", "02_pair_passes", "04a_reformat", "05c",
                   "app"]),

    Key("acquisition.channels", "raw",
        "What each CZI channel is: its name, what it is for, and how it is "
        "found in a file.",
        default=[], label="Channels",
        # Empty is the correct DEFAULT (paired declares no table), but it is
        # not a usable EXAMPLE: a multiplex study - the case this template is
        # mostly written for - fails ls_channels.validate on an empty table,
        # and that failure is not a "<PLACEHOLDER>" the example-vs-placeholder
        # check in test_config_example.py knows to excuse. Every suite that
        # builds a throwaway study from config.example.json (tests/_fixture.py
        # and tests/test_studies.py alike) would then fail to validate, for a
        # reason having nothing to do with what it is testing. A minimal
        # two-channel example keeps the shipped template self-consistent;
        # default=[] is untouched, so a real paired study still gets [].
        example=[{"name": "DAPI", "role": "nuclear", "czi_name": "DAPI",
                  "index": 0},
                 {"name": "Marker1", "role": "marker", "czi_name": "AF568",
                  "index": 1}],
        note="One entry per channel: `name` is the identity used in every "
             "output path and the `marker` column; `role` is nuclear, marker, "
             "registration or ignore; `czi_name` is what the microscope calls "
             "it, which is how it is located in a file; `index` is the plane "
             "to fall back on. A marker also carries `segment` (nuclear or "
             "own), and for own, a `backend` and whether its objects are "
             "`nucleus_shaped`. Empty for the paired layout.",
        consumers=["01_overviews", "05c", "app"]),

    Key("acquisition.markers", "raw",
        "The markers a `paired` study measures, in the order they should "
        "appear. Not used under `multiplex`.",
        default=[], label="Markers (paired only)",
        note="A paired study declares no channel table, because each of its "
             "scans carries the nuclear channel plus whichever marker that "
             "pass used - so this list is the only record of what the markers "
             "are. It is NOT derived from the manifest's marker_channel "
             "column: every constant built from it is evaluated at import, "
             "and the manifest lives under out_root, which may be absent. "
             "Output paths, CSV column values and workbook sheets are built "
             "from these names, so changing one renames real files.",
        example=[],
        # All seven now call ls_channels.marker_names(CONFIG) at import; the
        # hardcoded ("AF568", "AF488") tuples in 01k_saturation_raw and
        # 05a_roi_geometry that this flag was waiting on are gone. Leaving
        # consumers_pending set made docs/config-reference.md say "not read
        # yet" about a key seven stages build their output paths from.
        consumers=["01k_saturation_raw", "04a_reformat", "04g_artifact_mask",
                   "04j_censor_clipped", "04l_roi_curator", "04o_section_rgb",
                   "05a_roi_geometry"]),

    Key("acquisition.marker_segmentation", "raw",
        "Per marker, how a `paired` study finds the objects it measures: "
        "`segment` (`nuclear` or `own`), and for `own` a `backend` and whether "
        "the objects are `nucleus_shaped`. A marker not listed here is "
        "measured inside the nuclear mask, as always. Not used under "
        "`multiplex`.",
        default={}, ui=False,
        note="A paired study declares no channel table, so every marker takes "
             "the nuclear route: StarDist on the counterstain, the marker "
             "measured inside the mask. That is wrong for a pass whose "
             "counterstain plane is unusable. The LS PCNA (AF488) scans carry "
             "a DAPI plane that is noise at native resolution, so there is "
             "nothing to segment. Declaring `{\"AF488\": {\"segment\": "
             "\"own\", \"backend\": \"stardist\", \"nucleus_shaped\": true}}` "
             "segments PCNA on its own channel. Use it only for a marker whose "
             "signal is nuclear (PCNA is): StarDist is a nucleus detector. The "
             "cost is real and 06a records it: with no counterstain there is "
             "no count of all cells, so the fraction positive and the total "
             "cell density are withheld for that marker and the readout is "
             "positive density. See docs/nuclei-detection-after-roi-curation.md "
             "section 4.8.",
        example={},
        consumers=["05c", "06a"]),

    # ---- display ----------------------------------------------------------
    Key("display.composite", "raw",
        "Which markers are shown in the composite and the curator's Review "
        "pane, and in what order. What colour each one is drawn in is "
        "`display.colours`.",
        default=[], label="Composite channels",
        note="Empty means every declared marker, in declared order. This "
             "order is what the positional colour default reads - the first "
             "marker listed here is red, the second green - so a study can "
             "swap two markers' colours either by reordering this or by "
             "naming the colours in `display.colours`. A marker left out of a "
             "non-empty list is still measured; it is simply not drawn, which "
             "is visible rather than silently blended into another marker's "
             "colour.",
        example=[],
        consumers=["04l_roi_curator", "04o_section_rgb"]),

    Key("display.colours", "raw",
        "What colour each marker is drawn in, as marker -> colour name or "
        "#rrggbb. The nuclear counterstain has its own key below.",
        default={}, label="Marker colours", ui=False,
        note="Empty means positional: the first marker shown is red, the "
             "second green, and any marker beyond the second HAS NO COLOUR. "
             "A marker with no colour is measured but NOT DRAWN - absent from "
             "the section composites, which say so and stop rather than "
             "building them, and grey in the curator's Review pane, which "
             "labels it \"no colour set\". Both say so rather than picking "
             "one, because a marker silently wearing another marker's colour "
             "is exactly the fault this key was added to remove: the Review "
             "pane used to draw every marker but one in green while the "
             "composites beside it drew the first in red. Names: "
             + ", ".join(COLOUR_NAMES) +
             ". Anything else must be six hex digits.",
        example={},
        consumers=["04l_roi_curator", "04o_section_rgb"]),

    Key("display.nuclear_colour", "str",
        "What colour the nuclear counterstain is drawn in.",
        default="blue", label="Counterstain colour",
        choices=list(COLOUR_NAMES),
        note="Blue is what every composite and every curator page this "
             "pipeline has produced has used, and the geometry channel is "
             "the counterstain - so the blue plane of a composite is the "
             "byte-for-byte greyscale the landmarks were placed on, and "
             "`04o --verify` checks exactly that. Changing this changes what "
             "--verify is comparing. Where a marker shares a plane with the "
             "counterstain - a magenta marker has blue in it - the two are "
             "combined with max rather than added, so neither is clipped.",
        consumers=["04l_roi_curator", "04o_section_rgb"]),

    Key("channels.dapi_index", "int",
        "Which CZI channel plane is the nuclear counterstain.",
        required=True, default=0,
        note="Read only by 05c_detect_rois today; every earlier stage assumes "
             "plane 0 structurally. The channels/markers sub-project replaces "
             "this whole block with a per-channel role table, so it is left "
             "as-is rather than changed twice.",
        consumers=["05c"]),

    Key("channels.marker_index", "int",
        "Which CZI channel plane carries the marker.",
        required=True, default=1, consumers=["05c"]),

    Key("marker_identity", "raw",
        "Which fluorophore is which marker. Filled in by "
        "00c_channel_identity.py or under 'What it stains' in the app's channel "
        "editor, then confirmed by the operator.",
        default=None, status=DOCUMENTED, example={},
        note="Fluorophore -> marker name, e.g. {\"AF568\": \"pERK\"}. Written "
             "by 00c --accept and read by no stage. Until it is filled in, "
             "no fluorophore may be referred to by a biological name "
             "downstream. For the nuclear counterstain, record excitation and "
             "emission in nm by reading them off the CZI channel metadata "
             "(ExcitationWavelength / EmissionWavelength, exposed by "
             "scripts/czi_meta.py) rather than quoting a datasheet - what "
             "matters is the filter set actually used, not the dye in the "
             "abstract. Kept because it is the only written record of the "
             "confirmed assignment; the channels/markers sub-project wires it.",
        consumers=["00c (writes)"]),

    # ---- detection -------------------------------------------------------
    Key("detection.nucleus_diameter_um", "float",
        "Expected nucleus diameter, the scale StarDist is run at.",
        required=True, default=7.0, consumers=["05c"]),

    Key("detection.abercrombie.enabled", "bool",
        "Correct counted nuclear profiles to nuclei.",
        default=True, label="Abercrombie correction",
        note="A single-plane image of a section counts nuclear PROFILES, not "
             "nuclei: a nucleus straddling the cut face still appears in the "
             "section it is cut into. Abercrombie N = n * T/(T + h), with T "
             "the section thickness and h the mean diameter of the counted "
             "object along the axis perpendicular to the section. At T=14 and "
             "h=7 the factor is 0.667 - uncorrected counts are 50% too high. "
             "h is MEASURED per marker and region from the nuclear-counterstain "
             "segmentation, not taken from nucleus_diameter_um above: that "
             "value is a detection-scale assumption, and the factor moves from "
             "0.737 to 0.583 across a plausible 5-10 um range, so using it "
             "would bury an unmeasured constant in every density. Abercrombie "
             "assumes spherical, randomly positioned objects and applies no "
             "lost-caps correction; the unbiased alternative is the optical or "
             "physical disector, which needs z-stacks a single-plane dataset "
             "does not have. Report raw and corrected counts side by side and "
             "say which is which. This is also why it is enabled rather than "
             "optional: where sections are cut consecutively, adjacent "
             "sections are not independent tissue - a nucleus cut by the "
             "boundary appears in both - so summing or averaging raw profile "
             "counts across neighbours double-counts it.",
        consumers=["06a"]),

    Key("detection.threshold.mad_k", "float",
        "How many robust standard deviations above the background median a "
        "pixel must be to count as signal, for the `threshold` backend.",
        default=3.0, label="Threshold: MAD multiplier",
        note="3.0 is the same multiplier 06a uses for the positivity cut, so "
             "the pipeline has one definition of `above background` rather "
             "than two. Raising it finds fewer, brighter objects. It is "
             "applied to the frame being segmented rather than to curated "
             "background discs, because 05c runs before 06a and cannot read a "
             "file 06a has not written. Median and MAD rather than mean and "
             "SD because the objects are in the sample: a bright object would "
             "raise the very cut meant to find it.",
        consumers=["05c"]),

    Key("detection.threshold.min_area_um2", "float",
        "The smallest object the `threshold` backend will report, in square "
        "micrometres.",
        default=5.0, label="Threshold: minimum object area",
        note="Not tidying. At k=3 over a whole frame, thousands of "
             "single-pixel excursions clear the cut by chance and each one "
             "would be counted as an object. This is what makes the count "
             "mean `a stained region` rather than `a pixel that was noisy`. "
             "5 um2 is well below a nucleus (a 7 um nucleus is ~38 um2) and "
             "well above sensor noise; a study counting something smaller "
             "must lower it deliberately.",
        consumers=["05c"]),

    Key("detection.stardist.prob_thresh", "float",
        "StarDist's object-probability cut. Empty uses the tuned value from "
        "05b, or the model's own (0.479) when nothing was tuned.",
        default=None, label="StarDist: probability threshold", maximum=1.0,
        note="Setting it here overrides the tuned value. Lower finds more, "
             "dimmer objects and more noise; higher finds fewer. Changing it "
             "after detection has started changes what a nucleus is, so 05c "
             "refuses to add sections measured with a different value to the "
             "ones already measured: re-run detection with Redo finished "
             "sections.",
        consumers=["05c"]),

    Key("detection.stardist.nms_thresh", "float",
        "StarDist's overlap threshold: how much two candidate nuclei may "
        "overlap before the weaker is dropped. Empty uses the model's own "
        "(0.3).",
        default=None, label="StarDist: overlap threshold", maximum=1.0,
        note="Not tuned by 05b: the model was trained with its own value, and "
             "nothing measured on the slides says it should move. Raise it "
             "only if touching nuclei are being merged in the QC pictures.",
        consumers=["05c"]),

    Key("detection.stardist.use_auto", "bool",
        "Use the detection settings 05b tuned from the slides' signal quality.",
        default=True, label="Use auto-tuned detection settings",
        note="Off runs detection with the model's own thresholds (or the ones "
             "set above), which is what every count before 05b existed was "
             "made with. The measurements and the tuned values stay in "
             "detection.auto either way.",
        consumers=["05c"]),

    Key("detection.auto", "raw",
        "What 05b measured about each marker's signal and the detection "
        "settings it chose from that.",
        default=None, ui=False, in_example=False,
        note="Written by 05b_tune_detection.py, one entry per marker: the "
             "signal quality of a sample of sections (background, "
             "foreground, noise, signal-to-noise, contrast, saturation), a "
             "verdict, the object count at each probability cut tried, and the "
             "cut chosen. Edit the detection.stardist settings to override, "
             "not this.",
        consumers=["05c"]),

    # ---- atlas -----------------------------------------------------------
    Key("atlas_source", "str",
        "Which atlas the run uses: salmon (the default), wullimann1996, or one "
        "you imported.",
        default="salmon", label="Atlas", choices=list(ATLAS_SOURCES),
        note="salmon takes its plates from atlas_pdf and atlas_plate_set. "
             "wullimann1996 is the zebrafish cross-section atlas: 04w builds "
             "its plate set (plates_wullimann), 04x turns each plate's region "
             "outlines into numbered polygons, and atlas_plate_set is then "
             "ignored. Plate ids differ between the two (wplate_NNN), and the "
             "plate fingerprint the importers already check catches rows made "
             "against another plate set. An atlas made elsewhere is brought in "
             "with `python scripts/ls_atlas_import.py` or the Import atlas "
             "button, which registers it under `atlases` and is then a valid "
             "value here.",
        consumers=["04e", "04k", "04l", "04w", "04x"]),

    Key("atlas_rebuild_plates", "bool",
        "Build the built-in atlas's plates again from its source instead of using "
        "the plates that ship with the app.",
        default=False, label="Rebuild atlas plates",
        note="The salmon and Wullimann plates ship ready-made in the repository's "
             "atlas/ folder and are copied into <out_root>/atlas/ when a study first "
             "needs them, so the extraction stages (04a-04a4, 04w, 04x) are not part "
             "of a study that uses them. Turn this on only to remake a plate set from "
             "the PDF - for example plates_merged, which does not ship. Plates made "
             "again are different pictures: sections curated against the shipped "
             "ones then read as changed.",
        consumers=["app"]),

    Key("atlas_polygon_mirror", "bool",
        "Mirror the plate outlines about the midline, for an atlas whose "
        "outlines cover one hemisphere (Wullimann).",
        default=False, label="Mirror atlas polygons",
        note="Each Wullimann plate draws one hemisphere beside the micrograph "
             "of the other, so the outlines cover half the brain. Mirroring "
             "gives the numbered guide on both sides in 04l. Registration "
             "(04e) always mirrors, because it reads the micrograph half.",
        consumers=["04l"]),

    Key("atlases", "raw",
        "Atlases this study registered itself, beside the built-in salmon and "
        "wullimann1996: {name: {set_dir, region_kind, ...}}.",
        default={}, ui=False, example={},
        note="Written by `python scripts/ls_atlas_import.py` (or the app's "
             "Import atlas button), not by hand. Each entry names a plate set "
             "under <out_root>/atlas/ - its own folder, never one a built-in "
             "uses - and says how its regions are marked (`region_kind`: seeds "
             "or polygons), whether each plate carries a micrograph half "
             "(`micrograph_half`) and whether its polygons can be mirrored "
             "(`mirror_allowed`). `atlas_source` then accepts the entry's name. "
             "An entry is judged strictly when read: a bad one is an error, not "
             "a quiet fall back to salmon.",
        consumers=["04e", "04k", "04l"]),

    Key("atlas_plate_set.dir", "str",
        "Which plate set the ROI curator and the registration use.",
        default="plates", label="Atlas plate set", choices=["plates", "plates_merged", "plates_final"],
        note="Both read this, so they cannot drift apart. IDs COLLIDE between "
             "sets - the same plate_NNN name exists in more than one set and "
             "points at a different image - so a landmark file records the set "
             "it was made against. Set this once, before any landmark is "
             "placed; changing it later invalidates landmarks made against the "
             "old set. plates = one image per atlas page as extracted; "
             "plates_merged = one image per figure; plates_final = the curated "
             "set. Start at plates and move up as the atlas chain produces "
             "them.",
        consumers=["04e", "04k", "04l"]),

    Key("atlas_figure_sections.two_sections", "str_opt",
        "Inclusive figure-number range, as a string, of merged figures "
        "holding two sections each - e.g. \"31-47\". Empty leaves every "
        "figure at whatever 04a2 detected.",
        default=None, label="Two-section figures",
        note="Operator knowledge, not measurement: the band detector in 04a2 "
             "mis-counts when two sections touch (their legend text bridges "
             "every row, so no row is empty) and when one section is crossed "
             "by an internal white band (read as two). This declaration is the "
             "authority; the detector is not. 04a3b_enforce_sections.py takes "
             "the count from here and decides only WHERE to cut. Record the "
             "date and the reason alongside it when you fill it in.",
        consumers=["04a3b"]),

    Key("atlas_figure_sections.drop_inset_boxes", "raw",
        "Merged figure id -> the 1-based index of the box to drop, counted "
        "top to bottom.",
        default={},
        note="Boxes that are labelled INSETS, not sections - a detached "
             "callout of a structure that is also shown attached and labelled "
             "inside a section elsewhere in the atlas. An inset is not a "
             "plate, and holds no region seed. Empty by default on purpose: "
             "an entry is a claim about a figure in YOUR atlas, and a copied "
             "one would drop a real section box. Note that listing a figure "
             "here also exempts it from the two_sections count above, so list "
             "only the figures that really carry an inset.",
        consumers=["04a3b"]),

    Key("atlas_scope", "raw",
        "Which regions this study cares about: the ones the atlas already "
        "labels, and the ones still wanted.",
        default=None, ui=False,
        example={"mode": "sbn_nodes", "existing_regions": [],
                 "regions_to_add": []},
        note="Names are matched EXACTLY as the atlas writes them - no "
             "abbreviating, no reinterpreting - so the anatomy stays the atlas "
             "author's statement rather than this file's. In the ROI curator, "
             "`existing_regions` is the pre-ticked list (in that order) the first "
             "time the 'Which ROIs will you draw?' window opens; one that no "
             "plate of the atlas carries is flagged, since it is a typo or the "
             "wrong atlas. `regions_to_add` are regions you want that this atlas "
             "does not have: they are listed, greyed, under 'not in this atlas' "
             "so the gap is visible instead of silent. `mode` is a recorded "
             "label only.",
        consumers=["04l"]),

    Key("project", "raw",
        "The app's record of this project: when it was made, what the CZI files "
        "said, what was decided and when, and which stages have run.",
        default=None, ui=False, in_example=False,
        note="Written and read by the app only; no stage reads it, so editing "
             "or deleting it never changes a result. `detected` holds what was "
             "read from CZI files (evidence, not input), `slides` the whole-study "
             "summary of what the slide headers said (evidence, not input; the full table is in "
             "slide_metadata.json beside the study file), `decisions` the changes "
             "made in the app (the animal-to-treatment key is recorded by name "
             "only, never by value), `runs` the stages that ran with a hash of "
             "the config and the code version. `portable` records each path setting that "
             "lies inside the project's folder, relative to it, so the project can "
             "find its data again after the folder is moved (the settings themselves "
             "stay absolute). The lists are capped.",
        consumers=["app"]),

    Key("curation", "raw",
        "The curation decisions (ROI polygons, which ROIs to draw, rotations, levels), kept "
        "inside the project file.",
        default=None, ui=False, in_example=False,
        note="Written by the app as the curators save, so the project file is the one place "
             "your decisions live. {key: value} where each key names a curator's state "
             "(`ls_roi_curator_v1`, ...). The stages read plain copies the app writes to "
             "<out_root>/curation/ from this block; those copies are regenerated, so edit the "
             "project, not them. Names are restricted to letters, digits, `_`, `.` and `-` "
             "because they become file names.",
        consumers=["app"]),

    # ---- unblinding (replaced by the metadata-join sub-project) ----------
    Key("groups.order", "list_str",
        "Which treatment is the LEFT half of every Shotgun slide, and the "
        "order the halves are drawn in.",
        default=[], example=["<TREATMENT A>", "<TREATMENT B>"],
        note="Explicit because dict order is not a decision. Two entries: the "
             "slide splits in two.",
        consumers=["04l", "06c", "06d"]),

    Key("groups.by_animal", "raw",
        "Subject ID -> treatment, using exactly the labels in groups.order.",
        default={},
        note="UNBLINDING KEY. A DELIBERATE EXCEPTION to the blinding rule "
             "('no stage before 06b may read a group label'): a figure that "
             "compares groups cannot be built without knowing them. Nothing "
             "here reaches a count, a threshold or a curation decision - the "
             "curator's own state is written before this file is read and does "
             "not depend on it - but a deck laid out by group is, by "
             "construction, not blind. Leave empty to keep it that way; the "
             "Shotgun button disables itself when it is. An animal left out is "
             "reported and skipped rather than guessed at. The metadata-join "
             "sub-project replaces this with a real table join.",
        consumers=["04l", "05c", "06c", "06d"]),
]

BY_PATH = {k.path: k for k in SPEC}


#: Keys this schema version deliberately dropped, and why. A config written
#: before the drop is not broken and its author is not confused - saying so is
#: worth more than lumping them in with a typo.
RETIRED = {
    "triage_target_um_per_px":
        "no stage resamples for triage",
    "detection_target_um_per_px":
        "05c detects at pixel_size_um directly, without resampling",
    "section_interval_um":
        "never read; the argument it carried is now in the "
        "detection.abercrombie note",
    "channels.exposure_ms":
        "exposures are read per file from the CZI metadata by 00_manifest, so "
        "a hand-copied duplicate could only go stale",
    "flatfield":
        "read only by 01a_flatfield.groovy, which is retired and NOT_LISTED",
    "blinding":
        "a convention this file cannot enforce; the rule is recorded on "
        "groups.by_animal, and stage metadata is what marks the unblinding step",
    "detection.local_contrast_inner_factor":
        "the 03a calibration sweep is not a stage; StarDist replaced the "
        "local-contrast detector",
    "detection.local_contrast_outer_factor":
        "the 03a calibration sweep is not a stage; StarDist replaced the "
        "local-contrast detector",
    "detection.local_contrast_threshold":
        "the 03a calibration sweep is not a stage; StarDist replaced the "
        "local-contrast detector",
}


# --------------------------------------------------------------------------
# Type checking
# --------------------------------------------------------------------------

def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check_atlas_source(cfg, value):
    """`atlas_source` against the registry, which also knows the study's own atlases."""
    if not isinstance(value, str):
        return f"expected a string, got {type(value).__name__}"
    try:
        atlas_registry.names(cfg)
    except ValueError:
        return None                       # a bad `atlases` block is reported on its own
    if value not in atlas_registry.names(cfg):
        return f"must be one of {', '.join(atlas_registry.names(cfg))}"
    return None


def _check_atlas_scope(value):
    """Problems with an `atlas_scope` block, as a list of strings."""
    if value is None:
        return []
    if not isinstance(value, dict):
        return [f"must be a block of settings, not a {type(value).__name__}"]
    out = []
    mode = value.get("mode")
    if mode is not None and not isinstance(mode, str):
        out.append("`mode` must be text")
    lists = {}
    for k in ("existing_regions", "regions_to_add"):
        v = value.get(k)
        if v is None:
            continue
        if not isinstance(v, list) or not all(isinstance(n, str) and n.strip() for n in v):
            out.append(f"`{k}` must be a list of region names (non-empty text)")
            continue
        dup = sorted({n for n in v if v.count(n) > 1})
        if dup:
            out.append(f"`{k}` lists {', '.join(dup)} more than once")
        lists[k] = set(v)
    both = sorted(lists.get("existing_regions", set()) & lists.get("regions_to_add", set()))
    if both:
        out.append(f"{', '.join(both)} is in both `existing_regions` and "
                   f"`regions_to_add` - a region is either in the atlas or wanted")
    return out


PROJECT_CAPS = {"detected": 20, "decisions": 500, "runs": 200}

#: A curation key becomes a file name under <out_root>/curation/, so it is judged strictly:
#: nothing that could name another folder.
CURATION_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")


def _check_curation(value):
    """Problems with a `curation` block, as a list of strings."""
    if value is None:
        return []
    if not isinstance(value, dict):
        return [f"must be a block of curator states, not a {type(value).__name__}"]
    return [f"the name {k!r} is not usable (letters, digits, _ . - only, starting with a letter or digit)"
            for k in value if not isinstance(k, str) or not CURATION_KEY_RE.match(k) or ".." in k]


def _check_project(value):
    """Problems with a `project` block, as a list of strings."""
    if value is None:
        return []
    if not isinstance(value, dict):
        return [f"must be a block of settings, not a {type(value).__name__}"]
    out = []
    version = value.get("project_version")
    if version is not None and (not isinstance(version, int) or isinstance(version, bool)
                                or version < 1):
        out.append("`project_version` must be a whole number from 1")
    for field in ("id", "created", "modified", "app_version", "notes"):
        v = value.get(field)
        if v is not None and not isinstance(v, str):
            out.append(f"`{field}` must be text")
    synced = value.get("synced")
    if synced is not None and (not isinstance(synced, dict)
                               or not all(isinstance(k, str) and isinstance(v, str) for k, v in synced.items())):
        out.append("`synced` must map curation names to hashes")
    slides = value.get("slides")
    if slides is not None and not isinstance(slides, dict):
        out.append("`slides` must be a block")
    portable = value.get("portable")
    if portable is not None:
        if not isinstance(portable, dict) or not all(isinstance(k, str) and isinstance(v, str)
                                                     for k, v in portable.items()):
            out.append("`portable` must map setting names to relative paths")
        else:
            for k, v in portable.items():
                if os.path.isabs(v) or v.startswith(("/", "\\")) or ".." in v.replace("\\", "/").split("/"):
                    out.append(f"`portable` path for {k} must stay inside the project's folder: {v!r}")
    for field, cap in PROJECT_CAPS.items():
        v = value.get(field)
        if v is None:
            continue
        if not isinstance(v, list) or not all(isinstance(r, dict) for r in v):
            out.append(f"`{field}` must be a list of records")
        elif len(v) > cap:
            out.append(f"`{field}` holds {len(v)} records; the most kept is {cap}")
    return out


def _check(key, value):
    """Return an error string, or None if `value` is acceptable for `key`."""
    t = key.type

    if t in ("str", "str_opt"):
        if not isinstance(value, str):
            return f"expected a string, got {type(value).__name__}"
        if key.choices and value not in key.choices:
            return f"must be one of {', '.join(key.choices)}"
    elif t == "int":
        if not isinstance(value, int) or isinstance(value, bool):
            return f"expected a whole number, got {type(value).__name__}"
    elif t == "float":
        if not _is_num(value):
            return f"expected a number, got {type(value).__name__}"
        if value <= 0:
            return f"must be greater than zero, got {value}"
        if key.minimum is not None and value < key.minimum - 1e-9:
            return f"must be at least {key.minimum}, got {value}"
        if key.maximum is not None and value > key.maximum + 1e-9:
            return f"must be at most {key.maximum}, got {value}"
    elif t == "bool":
        if not isinstance(value, bool):
            return f"expected true or false, got {type(value).__name__}"
    elif t in ("list_str", "list_str_opt"):
        if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
            return "expected a list of strings"
    elif t == "regex":
        if not isinstance(value, str):
            return f"expected a regex string, got {type(value).__name__}"
        try:
            compiled = re.compile(value)
        except re.error as exc:
            return f"is not a valid regex: {exc}"
        if "subject" not in compiled.groupindex:
            return ("must contain a named group (?P<subject>...) - it is the "
                    "key every later table joins on")
    elif t in ("path_dir", "path_file", "path_dir_create", "path_dir_opt"):
        if not isinstance(value, str) or not value.strip():
            return "expected a path"
        # Existence is deliberately NOT checked here - see `missing_paths`.
        # path_dir_create and path_dir_opt are made on demand by whoever
        # writes into them - out_root by the app, export_dir by the curator's
        # first export - so neither is required to exist yet.
    elif t == "raw":
        pass
    else:                                                   # pragma: no cover
        return f"unknown spec type {t!r}"
    return None


def _get(tree, parts):
    """(found, value) for a dotted path in a nested dict."""
    node = tree
    for p in parts:
        if not isinstance(node, dict) or p not in node:
            return False, None
        node = node[p]
    return True, node


def _set(tree, parts, value):
    node = tree
    for p in parts[:-1]:
        node = node.setdefault(p, {})
    node[parts[-1]] = value


def _declared_prefixes():
    """Every dotted path the spec knows, including intermediate containers."""
    out = set()
    for key in SPEC:
        parts = key.parts
        for i in range(1, len(parts) + 1):
            out.add(".".join(parts[:i]))
    return out


def _unknown_paths(cfg):
    """Dotted paths present in `cfg` that the spec does not declare.

    Keys starting with `_` are notes and are never reported. Descent stops at
    a declared `raw` key, whose contents are the user's data, not our schema.
    """
    declared = _declared_prefixes()
    raw_paths = {k.path for k in SPEC if k.type == "raw"}
    found = []

    def walk(node, prefix):
        for name, value in node.items():
            if name.startswith("_"):
                continue
            path = f"{prefix}.{name}" if prefix else name
            if path in raw_paths:
                continue
            if path not in declared:
                found.append(path)
                continue
            if isinstance(value, dict):
                walk(value, path)

    walk(cfg, "")
    return sorted(found)


#: Path keys whose target must already exist to be usable. path_dir_create
#: and path_dir_opt are made on demand by whoever writes into them.
MUST_EXIST = {"path_dir": os.path.isdir, "path_file": os.path.isfile}


def missing_paths(cfg):
    """[(key, value)] for configured paths that are not there right now.

    Kept apart from validate() because absence is not the same fault as a
    malformed value. Forty of the forty-five stages never open `source_dir`,
    and the drive it lives on has gone away mid-run before - see ls_io.py. A
    stage list that refuses to load because a drive is unplugged is worse than
    a stage that says so when it is actually run, which is `require_path`.
    """
    out = []
    for key in SPEC:
        test = MUST_EXIST.get(key.type)
        if test is None:
            continue
        found, value = _get(cfg, key.parts)
        if found and isinstance(value, str) and value.strip() and not test(value):
            out.append((key, value))
    return out


def validate(cfg, path="<config>", strict_paths=False):
    """Return (errors, warnings) as lists of printable strings.

    Errors are fatal: a required key missing, or a value of the wrong shape.
    Warnings are not: an unknown key, and a configured path that is not there,
    are both reported and carried past - a stale config or an unplugged drive
    should surface, not abort a twelve-hour run.

    `strict_paths` promotes the path warnings to errors. The settings dialog
    passes it, because that is the one place the user can act on them.
    """
    errors, warnings = [], []

    for key in SPEC:
        found, value = _get(cfg, key.parts)
        # An optional path left as "" is the same as absent: the settings
        # dialog writes "" for a cleared field, and `atlas_pdf` then falls back
        # to the shipped atlas (apply_defaults).
        if (not key.required and key.type == "path_file"
                and isinstance(value, str) and not value.strip()):
            value = None
        if not found or value is None:
            if key.required and key.default is None:
                errors.append(f"{path}: `{key.path}` is required - {key.doc}")
            elif key.materialise:
                # The Fiji stages slurp this JSON directly and never see a
                # Python default, so a defaulted-but-absent key reaches them
                # as null.
                errors.append(
                    f"{path}: `{key.path}` must be written out, not left to "
                    f"its default - the Fiji stages read this file directly "
                    f"and cannot see Python defaults")
            continue
        if key.path == "atlas_source" and atlas_registry is not None:
            problem = _check_atlas_source(cfg, value)
        else:
            problem = _check(key, value)
        if problem:
            errors.append(f"{path}: `{key.path}` {problem}\n    {key.doc}")

    if isinstance(cfg, dict):
        for problem in _check_atlas_scope(cfg.get("atlas_scope")):
            errors.append(f"{path}: `atlas_scope` {problem}")
        for problem in _check_project(cfg.get("project")):
            errors.append(f"{path}: `project` {problem}")
        for problem in _check_curation(cfg.get("curation")):
            errors.append(f"{path}: `curation` {problem}")

    # The study's own atlases are judged by the registry, which owns the rules
    # (own folder, no shadowing, no path tricks). Reported here as a config error
    # so a bad entry is read as one rather than surfacing as a traceback in
    # whichever stage first asks which atlas it is.
    if atlas_registry is not None and isinstance(cfg, dict):
        try:
            atlas_registry.entries(cfg)
        except ValueError as e:
            errors.append(f"{path}: `atlases` {e}")

    # The channel table's rules live in ls_channels, which owns the concept.
    # Restating them here would let the validator and the reader disagree
    # about what is usable. A config with no acquisition block at all predates
    # the key and is left alone; apply_defaults gives it one.
    acquisition = cfg.get("acquisition")
    if acquisition is not None and not isinstance(acquisition, dict):
        errors.append(
            f"{path}: `acquisition` must be a block of settings, not a "
            f"{type(acquisition).__name__}.")
    elif isinstance(acquisition, dict):
        layout = acquisition.get("layout", LAYOUTS[0])
        if ls_channels is not None and layout in LAYOUTS:
            # The whole block, not just its channels: the marker rules live
            # off `acquisition.markers`, and handing over the table alone is
            # how they came to be checked by nothing at all.
            chan_errors, chan_warnings = ls_channels.validate(
                acquisition, layout)
            # Prefixed with the block, not a key inside it - these messages
            # name their own key, and hardcoding `acquisition.channels` would
            # file a marker fault under the channel table.
            for problem in chan_errors:
                errors.append(f"{path}: acquisition - {problem}")
            for problem in chan_warnings:
                warnings.append(f"{path}: acquisition - {problem}")

    # The `display` block names markers, so it can only be judged against the
    # marker list - and that list is read from the whole config, because which
    # source names the markers is itself decided by `acquisition.layout`.
    # ls_channels.validate() takes the `acquisition` block alone and cannot
    # see `display` from there, so the check is made here rather than by
    # widening that signature. Reported, not raised: the reading functions
    # raise because their callers are module-level constants, and this is the
    # one place that turns the same faults into lines among the others - a
    # marker with no colour among them, as a warning, because it is a
    # legitimate state and not a reason to refuse a config.
    if ls_channels is not None and isinstance(cfg, dict):
        disp_errors, disp_warnings = ls_channels.validate_display(cfg)
        for problem in disp_errors:
            errors.append(f"{path}: {problem}")
        for problem in disp_warnings:
            warnings.append(f"{path}: {problem}")

    for key, value in missing_paths(cfg):
        line = f"{path}: `{key.path}` does not exist: {value}"
        (errors if strict_paths else warnings).append(line)

    for unknown in _unknown_paths(cfg):
        if unknown in RETIRED:
            warnings.append(f"{path}: `{unknown}` was removed in schema "
                            f"v{SCHEMA_VERSION} - {RETIRED[unknown]}. It is "
                            f"ignored and can be deleted")
        else:
            warnings.append(f"{path}: `{unknown}` is not a setting this "
                            f"pipeline reads - it is ignored")

    return errors, warnings


def unexpected_keys(cfg):
    """Keys that are neither in the spec nor knowingly retired.

    This is the drift that still matters. The template can no longer fall
    behind the spec, because it is generated from it - but a study config can
    still carry a key nothing has ever read, and that one is a typo or a
    setting someone expected to work.
    """
    return [p for p in _unknown_paths(cfg) if p not in RETIRED]


def export_dir(cfg=None):
    """Where the ROI curator files its exports.

    `export_dir` when set, else `<out_root>/exports`. The rule was written out
    three times - 04l, 05a and the app's download handler - and the app's copy
    found config.json by guessing its own location, which stopped being right
    the moment a study could live somewhere else.
    """
    cfg = cfg if cfg is not None else load()
    return cfg.get("export_dir") or os.path.join(cfg["out_root"], "exports")


def scene_render_dir(cfg=None):
    """Where 01l puts the scene pictures: `scene_render.out_dir` when set,
    else `<out_root>/scene_renders`."""
    cfg = cfg if cfg is not None else load()
    return ((cfg.get("scene_render") or {}).get("out_dir")
            or os.path.join(cfg["out_root"], "scene_renders"))


def require_path(dotted, cfg=None):
    """A configured path that must exist NOW. Call it where the file is opened.

    The other half of missing_paths(): absence warns at import and fails here,
    in the one stage that actually needs the thing, with the remedy attached.
    """
    cfg = cfg if cfg is not None else load()
    found, value = _get(cfg, dotted.split("."))
    if not found or not value or not os.path.exists(value):
        try:
            where = resolve_path()
        except SystemExit:
            # resolve_path raises when nothing names a config. Letting that
            # escape here would replace the fault being reported with a
            # different one - an error handler must not destroy its own error.
            where = "<no config named>"
        raise SystemExit(
            f"`{dotted}` is {value!r}, which does not exist.\n"
            f"  config: {where}\n"
            f"  Fix it in the app under Pipeline > Settings, or edit the file.")
    return value



def apply_defaults(cfg):
    """Fill every absent key that has a default. Returns a new dict."""
    out = json.loads(json.dumps(cfg))
    for key in SPEC:
        if key.default is None:
            continue
        found, value = _get(out, key.parts)
        if not found or value is None:
            _set(out, key.parts, json.loads(json.dumps(key.default)))
    # Not a Key default: it is an absolute path on this machine, and a default
    # is printed into the generated example config and reference docs.
    if not out.get("atlas_pdf") and os.path.isfile(BUNDLED_ATLAS_PDF):
        out["atlas_pdf"] = BUNDLED_ATLAS_PDF
    return out


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Studies. A study is one config file with a name; the app keeps a small
# settings file beside them saying which was open last.
# --------------------------------------------------------------------------

def app_home():
    """Where settings.json and the studies live.

    One location on every platform rather than three conventional ones: it
    halves what has to be documented and supported, it is easy to tell someone
    to look at, and LS_HOME covers anyone who wants it elsewhere - including
    the tests, which must never touch a real home directory.
    """
    return os.path.abspath(os.environ.get("LS_HOME")
                           or os.path.join(os.path.expanduser("~"), ".ls-pipeline"))


def settings_path():
    return os.path.join(app_home(), "settings.json")


def studies_dir():
    """The folder holding the named study configs."""
    return (os.environ.get("LS_STUDIES_DIR")
            or read_settings().get("studies_dir")
            or os.path.join(app_home(), "studies"))


def read_settings():
    """App-level settings, or {} when there are none yet."""
    try:
        with open(settings_path(), encoding="utf-8") as fh:
            settings = json.load(fh)
        return settings if isinstance(settings, dict) else {}
    except (OSError, ValueError):
        return {}


def write_settings(changes):
    """Merge `changes` into settings.json, atomically. Never deletes."""
    settings = read_settings()
    settings.update(changes)
    settings.setdefault("schema_version", SCHEMA_VERSION)
    import ls_jsonio
    ls_jsonio.write_json_atomic(settings_path(), settings)
    return settings


#: How many project files "Open Recent" remembers.
RECENT_PROJECTS = 10


def recent_projects():
    """Project files opened lately, newest first, those that still exist."""
    listed = read_settings().get("recent_projects")
    if not isinstance(listed, list):
        return []
    return [p for p in listed if isinstance(p, str) and os.path.isfile(p)][:RECENT_PROJECTS]


def remember_project(path):
    """Put `path` at the head of the recent list (and make it the last-used one)."""
    path = os.path.abspath(path)
    rest = [p for p in (read_settings().get("recent_projects") or [])
            if isinstance(p, str) and os.path.abspath(p) != path]
    write_settings({"recent_projects": ([path] + rest)[:RECENT_PROJECTS],
                    "last_project": path})


def slugify(name):
    """A filename-safe study id from a human name."""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(name)).strip("-").lower()
    if not slug:
        raise SystemExit(f"{name!r} has no characters usable as a study name.")
    return slug


def study_path(slug):
    return os.path.join(studies_dir(), f"{slugify(slug)}.json")


def list_studies():
    """[(slug, display name, path)] for every study on disk."""
    root = studies_dir()
    out = []
    for entry in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        if not entry.endswith(".json"):
            continue
        path = os.path.join(root, entry)
        slug = entry[:-5]
        try:
            with open(path, encoding="utf-8") as fh:
                name = (json.load(fh).get("study") or {}).get("name") or slug
        except (OSError, ValueError):
            name = f"{slug}  (unreadable)"
        out.append((slug, name, path))
    return out


def migrate(name, source=None):
    """Copy a pre-study config into a named study. Returns its path.

    The original is COPIED, not moved, and left where it was. Everything that
    resolves to it keeps working - a bare `python scripts/XX.py`, run_all.sh, a
    fresh clone - so this is reversible by deleting one file, and nothing has to
    change in the same commit.
    """
    source = os.path.abspath(source or os.path.join(REPO, "config.json"))
    if not os.path.exists(source):
        raise SystemExit(f"there is no config at {source} to migrate.")

    slug = slugify(name)
    target = study_path(slug)
    if os.path.exists(target):
        raise SystemExit(
            f"a study called {slug!r} already exists at {target}.\n"
            f"  Pick another name, or delete that file first.")

    with open(source, encoding="utf-8") as fh:
        cfg = json.load(fh)
    errors, _ = validate(cfg, source)
    if errors:
        raise SystemExit(
            f"{source} cannot be migrated as it stands:\n  "
            + "\n  ".join(errors))

    cfg.setdefault("study", {})
    cfg["study"]["name"] = str(name)
    cfg["schema_version"] = SCHEMA_VERSION

    import ls_jsonio
    ls_jsonio.write_json_atomic(target, cfg)

    write_settings({"active_study": slug,
                    "recent": [slug] + [s for s in read_settings().get("recent", [])
                                        if s != slug][:9]})
    return target


def resolve_path(explicit=None):
    """Where the active config lives.

    `LS_CONFIG` names it explicitly; the repo-root `config.json` is the
    fallback, so a bare `python scripts/XX.py` and a fresh clone keep working.
    Frozen, the fallback points inside `_internal/` at a file that is not
    there, which is the whole reason `LS_CONFIG` exists.
    """
    named = explicit or os.environ.get("LS_CONFIG")
    if named:
        return os.path.abspath(named)

    if not os.environ.get("LS_CONFIG_STRICT"):
        # A study named on the command line, then the one the app last opened.
        # Both are conveniences above LS_CONFIG, never a replacement for it:
        # the app still exports LS_CONFIG before running anything, so a stage,
        # run_all.sh and the Groovy stages all see the same single answer.
        asked = os.environ.get("LS_STUDY")
        if asked:
            # Named explicitly, so a missing one is an error. Falling back to
            # whichever study happens to be active would run the whole pipeline
            # against different data than the caller asked for, and say nothing.
            path = study_path(asked)
            if not os.path.exists(path):
                known = ", ".join(s for s, _, _ in list_studies()) or "none"
                raise SystemExit(
                    f"LS_STUDY names {asked!r}, and there is no study by that "
                    f"name in {studies_dir()}\n  studies here: {known}")
            return path

        slug = read_settings().get("active_study")
        if slug and os.path.exists(study_path(slug)):
            return study_path(slug)

    if os.environ.get("LS_CONFIG_STRICT"):
        # tests/run.sh sets this. Without it a suite that forgets to name a
        # config quietly analyses whichever study the operator happens to have
        # open, and passes or fails on their data rather than on its own.
        raise SystemExit(
            "LS_CONFIG is not set and LS_CONFIG_STRICT forbids falling back to "
            f"{os.path.join(REPO, 'config.json')}.\n"
            "  A test must name its own config - see tests/_fixture.py.")

    return os.path.abspath(os.path.join(REPO, "config.json"))


_cache = {}


def load(path=None, strict=True):
    """The validated, defaulted config as a plain dict.

    Cached on (path, mtime, size), because `app/runner.py` re-execs stage
    modules on a config change but this module stays in `sys.modules` - an
    uncached read here would serve the previous config to every stage.
    """
    path = resolve_path(path)
    try:
        st = os.stat(path)
        stamp = (path, st.st_mtime_ns, st.st_size)
    except OSError:
        stamp = None

    if stamp is not None and _cache.get("stamp") == stamp:
        return _cache["cfg"]

    if not os.path.exists(path):
        raise SystemExit(
            f"no config at {path}\n"
            f"  Set LS_CONFIG to your study's config file, or copy "
            f"config.example.json to {os.path.join(REPO, 'config.json')} and "
            f"fill it in.")

    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} is not valid JSON: {exc}")

    errors, warnings = validate(raw, path)
    for w in warnings:
        print(f"  !! {w}", file=sys.stderr)
    if errors and strict:
        raise SystemExit("config is not usable:\n  " + "\n  ".join(errors))

    cfg = apply_defaults(raw)
    if stamp is not None:
        _cache["stamp"], _cache["cfg"] = stamp, cfg
    return cfg


def clear_cache():
    """Drop the memoised config. For tests, and for the app after a save."""
    _cache.clear()


# PEP 562: `from ls_config import CONFIG, OUT_ROOT` reads config on first use,
# so this module stays importable by the generators and the tests when no
# config exists at all.
_LAZY = {
    "CONFIG": lambda c: c,
    "OUT_ROOT": lambda c: c["out_root"],
    "SOURCE_DIR": lambda c: c["source_dir"],
    "CONFIG_PATH": lambda c: resolve_path(),
}


def __getattr__(name):
    if name in _LAZY:
        return _LAZY[name](load())
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# --------------------------------------------------------------------------
# Generation. SPEC is the source; the example config and the docs table are
# outputs, so neither can fall behind it the way a hand-kept copy did.
# --------------------------------------------------------------------------

EXAMPLE_HEADER = (
    "Copy this to a study config and fill in the placeholders. GENERATED from "
    "SPEC in scripts/ls_config.py by `python scripts/ls_config.py "
    "--write-example` - edit the spec, not this file."
)


def _example_value(key):
    if key.example is not _UNSET:
        return key.example
    return key.default


def to_example():
    """The contents of config.example.json, built from SPEC.

    A note is emitted as an `_<leaf>_note` sibling immediately before its key.
    The hand-written template mixed that with a `_note` inside the block; one
    rule reads more easily and cannot be applied inconsistently.
    """
    out = {"_comment": EXAMPLE_HEADER}
    for key in SPEC:
        if not key.in_example:
            continue
        parts = key.parts
        node = out
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        leaf = parts[-1]
        if key.note:
            node[f"_{leaf}_note"] = key.note
        node[leaf] = _example_value(key)
    return out


def example_text():
    return json.dumps(to_example(), indent=2, ensure_ascii=False) + "\n"


def write_example(path=None):
    path = path or os.path.join(REPO, "config.example.json")
    text = example_text()
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path, text


#: What the docs table prints in "Read by" for a key no stage reads.
NOT_READ = "recorded here, read by no stage"


def dialog_fields():
    """[Key] for the settings dialog, in spec order.

    The dialog used to carry three hand-written entries. Reading them from SPEC
    means a key added there shows up in settings without anyone remembering to
    edit a second list - which is the same reason config.example.json and the
    docs table are generated rather than kept in step.
    """
    return [k for k in SPEC if k.ui]


def docs_table():
    """A markdown table of every key, for the operator guide."""
    rows = ["| Key | Type | Default | Read by | What it is |",
            "|---|---|---|---|---|"]
    for key in SPEC:
        if key.required and key.default is None:
            default = "*required*"
        elif key.default is None:
            default = ""
        else:
            default = "`" + json.dumps(key.default) + "`"
        if key.status == DOCUMENTED:
            read_by = NOT_READ
        elif key.consumers_pending:
            read_by = "not read yet; will be " + ", ".join(
                "`" + c + "`" for c in key.consumers)
        else:
            read_by = ", ".join("`" + c + "`" for c in key.consumers)
        rows.append("| `{}` | {} | {} | {} | {} |".format(
            key.path, key.type, default, read_by, key.doc.replace("|", "\\|")))
    return "\n".join(rows)


BEGIN_DOCS = "<!-- BEGIN config-keys -->"
END_DOCS = "<!-- END config-keys -->"


DOCS_HEADER = """# Configuration reference

GENERATED from `SPEC` in `scripts/ls_config.py` by
`python scripts/ls_config.py --docs docs/config-reference.md`.
Edit the spec, not this file.

Every setting a study can carry. **Read by** names the stages that consume a
key; a key nothing reads says so, rather than looking like every other one.
A key marked *required* has to be present before any stage will run.

"""

DOCS_FOOTER = """
## Keys removed in schema v{version}

A config written before these were dropped is not broken and its author was not
confused. `python scripts/ls_config.py --check` reports them as removed, with
the reason, rather than as mistakes.

{retired}
"""


def docs_page():
    """The whole reference as a standalone markdown document."""
    retired = "\n".join(f"- `{k}` — {why}" for k, why in sorted(RETIRED.items()))
    return (DOCS_HEADER + docs_table() + "\n"
            + DOCS_FOOTER.format(version=SCHEMA_VERSION, retired=retired))


BEGIN_DOCS = "<!-- BEGIN config-keys -->"
END_DOCS = "<!-- END config-keys -->"


def splice_docs(path):
    """Write the reference to `path`.

    A file carrying the two sentinels has just its table replaced, the way
    docs/make_versions_table.py splices the software table into the methods
    document. Anything else is written whole, so a generated reference can be
    its own file without a hand-written frame to live inside.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            doc = fh.read()
    except OSError:
        doc = ""

    if BEGIN_DOCS in doc and END_DOCS in doc:
        head, _, rest = doc.partition(BEGIN_DOCS)
        _, _, tail = rest.partition(END_DOCS)
        text = head + BEGIN_DOCS + "\n\n" + docs_table() + "\n" + END_DOCS + tail
    else:
        text = docs_page()

    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0

    cmd = argv[0]
    if cmd == "--path":
        print(resolve_path())
        return 0
    if cmd == "--print":
        if len(argv) < 2:
            print("--print needs a key, e.g. --print out_root", file=sys.stderr)
            return 2
        found, value = _get(load(), argv[1].split("."))
        if not found:
            print(f"no such key: {argv[1]}", file=sys.stderr)
            return 2
        print(value if not isinstance(value, (dict, list))
              else json.dumps(value))
        return 0
    if cmd == "--check":
        path = resolve_path()
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        errors, warnings = validate(raw, path)
        for w in warnings:
            print("warning: " + w)
        for e in errors:
            print("ERROR:   " + e)
        live = [k for k in SPEC if k.status == LIVE]
        print(f"\n{len(SPEC)} keys in the spec, {len(live)} read by a stage, "
              f"{len(SPEC) - len(live)} recorded only.")
        print(f"{len(errors)} error(s), {len(warnings)} warning(s) in {path}")
        return 1 if errors else 0
    if cmd == "--studies":
        rows = list_studies()
        active = read_settings().get("active_study")
        print(f"studies in {studies_dir()}")
        for slug, name, path in rows:
            print(f"  {'*' if slug == active else ' '} {slug:24} {name}")
        if not rows:
            print("  (none yet - see --migrate)")
        print(f"\nactive config: {resolve_path()}")
        return 0
    if cmd == "--migrate":
        if len(argv) < 2:
            print('--migrate needs a name, e.g. --migrate "LS Dec 2025"',
                  file=sys.stderr)
            return 2
        target = migrate(argv[1], argv[2] if len(argv) > 2 else None)
        print(f"wrote {target}\nit is now the active study; the original "
              f"config is untouched")
        return 0
    if cmd == "--write-example":
        path, _ = write_example(argv[1] if len(argv) > 1 else None)
        print("wrote " + path)
        return 0
    if cmd == "--docs":
        if len(argv) > 1:
            print("spliced into " + splice_docs(argv[1]))
        else:
            print(docs_table())
        return 0

    print(f"unknown option {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
