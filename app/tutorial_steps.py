"""The tutorial, step by step: the one file to edit when the tutorial changes.

Each step names the screen it lives on, what the side panel says, the control
the pointer bubble points at, and what counts as done. The controller
(tutorial.py) does the rest; screens know nothing about the tutorial beyond the
object names their controls carry.

`target` is a Qt object name (findChild on the main window), or
`curator:<css selector>` for an element of the ROI curator page.

`done` is one of
    next                 the learner presses Next
    click                the target was pressed
    stage:<id>           that stage finished (or its results are already there)
    file:<relpath>       that file exists under the tutorial study's output folder
    curation-checked     the practice curation was compared with the answer key

`ready` names the stages whose ready-made results the demo pack carries
(pack ready/<stage id>/...), offered on slow steps as "Use ready-made results".

tests/test_tutorial_targets.py checks every target here still exists.
"""

from dataclasses import dataclass

DONE_KINDS = ("next", "click", "stage", "file", "curation-checked")
SCREENS = ("home", "setup", "prepare", "curate", "measure", "results", "finish")
SLOW = ("scene_renders", "rgb_perk", "rgb_pcna", "detect")


@dataclass(frozen=True)
class Step:
    sid: str
    screen: str
    title: str
    text: str
    target: str = ""
    bubble: str = ""
    done: str = "next"
    slow: bool = False
    ready: tuple = ()


def parse_done(done):
    """'stage:detect' -> ('stage', 'detect'); 'next' -> ('next', '')."""
    kind, _, arg = done.partition(":")
    return kind, arg


STEPS = [
    Step("welcome", "home", "Welcome",
         "This tutorial walks you through a whole study on a small demo slide: a few "
         "salmon brain sections cut from a real scan. Nothing you do here touches your "
         "own studies. The six steps down the left are the order every study follows.",
         target="stepList", bubble="The six steps of every study, top to bottom"),
    Step("project", "home", "The Project screen",
         "Project shows where a study stands: what is done, what is next, and what was "
         "run recently. Next up always names the one thing to do now.",
         target="nextUp", bubble="Next up: the one thing to do now"),
    Step("slides", "setup", "Your slides",
         "Set up is where the app learns about the slides. The demo study is already "
         "filled in. The Slides tab lists every slide file and what its name was read "
         "as; a red row would be a file every stage ignores.",
         target="setupTabs", bubble="Every slide file, and what its name says"),
    Step("read_settings", "setup", "Settings from the slides",
         "Pixel size, the file-name rule and the channels are read from the slide "
         "files themselves, so there is little to type. Press Read settings from the "
         "slides to see it happen.",
         target="readSettings", bubble="Press this to read the slide headers",
         done="click"),
    Step("setup_cards", "setup", "Markers and atlas",
         "The other tabs hold the study's settings: which marker is in which channel, "
         "and the atlas the sections are matched to. This study uses the salmon atlas "
         "that ships with the app, so no atlas needs building.",
         target="setupTabs", bubble="Markers, atlas and the rest, one tab each"),
    Step("prepare_run", "prepare", "Prepare runs by itself",
         "Prepare reads, checks and cleans every section without you. Press Run all "
         "remaining. The quick stages run for real; this step is done when the slide "
         "headers have been read.",
         target="runAll_prepare", bubble="Press Run all remaining",
         done="stage:manifest"),
    Step("scene_pictures", "prepare", "Pictures of every scene",
         "Drawing a picture of every scene is one of the slow stages. You can wait for "
         "it, or press Use ready-made results to carry on with pictures made earlier "
         "from the same slide.",
         target="run_scene_renders", bubble="The slow one: wait, or use ready-made results",
         done="stage:scene_renders", slow=True, ready=("scene_renders",)),
    Step("composites", "prepare", "Colour composites",
         "The colour composites are what you will curate on. They are slow too, so "
         "ready-made ones are offered.",
         target="run_rgb_perk", bubble="Also slow: ready-made results are offered",
         done="stage:rgb_perk", slow=True, ready=("rgb_perk", "rgb_pcna")),
    Step("look_pictures", "prepare", "Look at the pictures",
         "Open the pictures and look at a few scenes. Good tissue is evenly lit; "
         "clipped areas (pure white) cannot be measured and are censored later.",
         target="open_scene_renders", bubble="Open the scene pictures", done="click"),
    Step("open_curator", "curate", "The ROI curator",
         "Curate is the one place where your judgement shapes the results. Press Build "
         "page to open the ROI curator for the demo sections.",
         target="run_roi_curator", bubble="Press Build page", done="stage:roi_curator"),
    Step("curator_tour", "curate", "Around the curator",
         "Pick the atlas plate that matches the section with the arrows, then use "
         "guided mode: it walks the plate's regions one at a time and asks you to "
         "outline each on the section. Rotate only turns the view; Review is where "
         "whole sections are kept or excluded.",
         target="curator:#guideBtn", bubble="Guided mode walks the regions one by one"),
    Step("practice", "curate", "Practise on two sections",
         "Now curate the two practice sections yourself: match the plate, then outline "
         "every region guided mode asks for. When you are done, press Done in the "
         "curator's bar and your outlines are compared with an expert's.",
         target="curatorDone", bubble="Press Done when both sections are outlined",
         done="curation-checked"),
    Step("place_rois", "measure", "Place the regions on the slide",
         "Measure is automatic again. Press Run all remaining: first your outlines are "
         "placed back on the full-resolution slide.",
         target="runAll_measure", bubble="Press Run all remaining",
         done="stage:roi_geometry"),
    Step("tune", "measure", "Signal quality and detection settings",
         "This report shows how bright the signal is in each section and which "
         "detection settings suit it. Open it to see what a healthy section looks like.",
         target="open_tune_detection", bubble="Open the report"),
    Step("detect", "measure", "Find and count nuclei",
         "Finding nuclei runs StarDist on every region and is the slowest stage of "
         "all. Use ready-made results to carry on, or run it and wait.",
         target="run_detect", bubble="The slowest stage: ready-made results are offered",
         done="stage:detect", slow=True, ready=("detect",)),
    Step("unblind", "results", "Join the treatment groups",
         "Up to now you worked blind: no fish's treatment was shown. Joining the "
         "groups reads the sampling workbook. The demo's workbook has made-up groups.",
         target="run_join_sampling", bubble="Press Run to join the groups",
         done="stage:join_sampling"),
    Step("tables", "results", "Build the tables",
         "Build the results: one row per region with its counts, and spreadsheets per "
         "sample and per slide. Open results shows the folder.",
         target="run_build_results", bubble="Press Run to build the tables",
         done="stage:build_results"),
    Step("finish", "finish", "Done",
         "That is a whole study. To start your own, use New project on the Project "
         "screen and choose your slides and an output folder. The user guide (Help "
         "menu) covers every option, and the (i) buttons explain each part where it "
         "is. Instrument characterisation and All stages, under Advanced, are for "
         "special cases and are not part of a normal study."),
]

_BY_SID = {s.sid: s for s in STEPS}


def by_sid(sid):
    return _BY_SID[sid]
