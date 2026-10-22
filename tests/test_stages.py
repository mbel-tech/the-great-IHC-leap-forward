"""The app's stage list must cover the pipeline and describe real scripts.

`app/stages.py` is the pipeline as data. It drifted once - the figure in
docs/pipeline-methods.md had 04f, 04g, 04j, 06f and the atlas chain on it and
the sidebar did not - so this pins the two together: every numbered script
in scripts/ is either a stage or is named in NOT_LISTED with a reason, every
stage's script exists and has a main(), and every box on the workflow figure
has a stage id.

docs/pipeline-guide.md is held to the same list, and for the same reason: it
walks the stages one by one, so a stage nobody wrote up is exactly the drift
above in a third document. Its entries are anchored `{#stage-<sid>}`.

Run:  python tests/test_stages.py
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SCRIPTS = os.path.join(REPO, "scripts")
sys.path.insert(0, os.path.join(REPO, "app"))

import stages as S                                          # noqa: E402

failures = []


def chk(name, got, want=True):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name:64} {'' if ok else got!r}")
    if not ok:
        failures.append(name)


ids = [st.sid for st in S.STAGES]
chk("stage ids are unique", len(ids), len(set(ids)))
chk("BY_ID indexes every stage", set(S.BY_ID), set(ids))

numbered = sorted(f for f in os.listdir(SCRIPTS)
                  if re.match(r"^\d", f) and f.endswith((".py", ".groovy")))
listed = {st.script for st in S.STAGES if st.script}
orphans = [f for f in numbered if f not in listed and f not in S.NOT_LISTED]
chk("every numbered script is a stage or explained in NOT_LISTED", orphans, [])
stale = [f for f in S.NOT_LISTED if f in listed or f not in numbered]
chk("NOT_LISTED names only real, unlisted scripts", stale, [])
chk("NOT_LISTED entries carry a reason",
    all(isinstance(v, str) and v.strip() for v in S.NOT_LISTED.values()))

for st in S.STAGES:
    if st.script:
        path = os.path.join(SCRIPTS, st.script)
        chk(f"{st.sid}: script exists", os.path.exists(path))
        if st.script.endswith(".py") and os.path.exists(path):
            src = open(path, encoding="utf-8").read()
            chk(f"{st.sid}: {st.script} has main()", "def main(" in src)
    chk(f"{st.sid}: group is known", st.group in S.GROUPS)
    chk(f"{st.sid}: has a blurb", bool(st.blurb.strip()))
    chk(f"{st.sid}: reader is known", st.reader in S.READERS)
    chk(f"{st.sid}: needs are stage ids", [n for n in st.needs if n not in S.BY_ID], [])
    # An Art is relative by construction - it is resolved against the study's
    # own out_root - so only the literal entries can get this wrong.
    chk(f"{st.sid}: outputs are relative",
        [o for o in st.outputs if isinstance(o, str) and os.path.isabs(o)], [])
    bad = [a for a in st.argv
           if "{" in a and not re.fullmatch(
               r"[^{}]*(\{(out_root|repo|scripts|marker\d+)\}[^{}]*)+", a)]
    chk(f"{st.sid}: argv placeholders are the ones the runner expands", bad, [])
    if st.curator:
        chk(f"{st.sid}: a curator stage is an operator step", st.operator)
        chk(f"{st.sid}: the curator page is among its outputs", st.curator in st.outputs)
    reason = st.cli_reason()
    chk(f"{st.sid}: cli_reason() is a string or None",
        reason is None or (isinstance(reason, str) and bool(reason)))

# The workflow figure, box by box. A box with no stage is the drift this
# file exists to catch; keep the names in step with docs/pipeline-methods.md.
FIGURE = {
    "manifest", "verify", "channel_identity",                    # 00, 00b/00c
    "overviews",                                                 # 01
    "tile_geometry", "tilefield_build", "tilefield_verify",      # 01c-01k
    "artifact_before", "artifact_after", "tilefield_raw",
    "saturation_raw", "saturation_map",
    "contactsheets",                                             # 01d
    "pair",                                                      # 02
    "atlas_extract", "atlas_remerge", "atlas_reframe",           # 04a atlas
    "atlas_enforce", "atlas_rebuild",
    "reformat_pcna", "reformat_pcna_final",                      # 04a reformat
    "propagate_perk", "reformat_perk_final",
    "exclusion",                                                 # 04f
    "artifact_pcna", "artifact_perk",                            # 04g
    "censor_perk", "censor_pcna", "recensor",                    # 04j / 06f
    "worklist", "provenance", "roi_curator", "import_curation",  # 04l
    "roi_geometry",                                              # 05a
    "detect",                                                    # 05c
    "off_tissue", "roi_dataset",                                 # 06a
    "join_sampling", "excel_sample", "excel_slide", "refresh_loop",   # 06b-06e
}
chk("every box on the workflow figure has a stage", sorted(FIGURE - set(ids)), [])

# Order: a stage never needs something listed after it.
pos = {sid: i for i, sid in enumerate(ids)}
back = [(st.sid, n) for st in S.STAGES for n in st.needs if pos[n] > pos[st.sid]]
chk("no stage needs a later stage", back, [])
chk("groups appear in sidebar order",
    [S.GROUPS.index(st.group) for st in S.STAGES]
    == sorted(S.GROUPS.index(st.group) for st in S.STAGES))

# The guide, entry by entry. Same drift the FIGURE set above exists to catch,
# one document further out: a stage nobody wrote up, or a write-up of a stage
# that no longer exists. Entries are anchored `{#stage-<sid>}`, which pandoc
# consumes into the heading id and does not print.
GUIDE = os.path.join(REPO, "docs", "pipeline-guide.md")
chk("docs/pipeline-guide.md exists", os.path.exists(GUIDE))
if os.path.exists(GUIDE):
    guide = open(GUIDE, encoding="utf-8").read()
    cited = re.findall(r"\{#stage-([A-Za-z0-9_]+)\}", guide)
    chk("every stage has an entry in the guide", sorted(set(ids) - set(cited)), [])
    chk("the guide names no stage that does not exist",
        sorted(set(cited) - set(ids)), [])
    chk("no stage is written up twice",
        sorted(s for s in set(cited) if cited.count(s) > 1), [])
    # The guide's whole claim is that reading it top to bottom follows what
    # actually runs, so its order is pinned to the pipeline's own.
    chk("the guide's entries follow the pipeline's order",
        cited, sorted(cited, key=pos.get))
    # Measured counts live in pipeline-methods.md, where check_methods_claims.py
    # verifies them. Anything shaped like one here is an unchecked second copy
    # that will go stale - the guide states settings and structure, not results.
    chk("the guide quotes no moving count of its own",
        sorted(set(re.findall(r"\d{1,3},\d{3}", guide))), [])

# Detection is CLI-only in the frozen build and wherever StarDist is missing,
# and runnable otherwise - the venv the app runs from has it.
chk("detect is CLI-only when frozen", isinstance(S.detect_cli_reason(True, True), str))
chk("detect is CLI-only without StarDist", isinstance(S.detect_cli_reason(False, False), str))
chk("detect runs from source with StarDist", S.detect_cli_reason(False, True), None)

# done() is existence-only and relative to out_root.
import tempfile                                                  # noqa: E402
with tempfile.TemporaryDirectory() as tmp:
    st = S.BY_ID["manifest"]
    chk("a stage with no outputs on disk is not done", st.done(tmp), False)
    for o in st.output_names(tmp):
        os.makedirs(os.path.dirname(os.path.join(tmp, o)) or tmp, exist_ok=True)
        open(os.path.join(tmp, o), "w").close()
    chk("...and is done once every output exists", st.done(tmp), True)
    chk("blocked_by names undone prerequisites",
        S.blocked_by(S.BY_ID["overviews"], tmp), [])


# --- which layouts a stage applies to -------------------------------------
print()
print("--- every stage applies to at least one layout ---")

chk("no stage is listed for no layout",
    [st.sid for st in S.STAGES if st.layouts is not None and not st.layouts], [])
chk("02_pair_passes is paired-only",
    next(st for st in S.STAGES if st.script == "02_pair_passes.py").layouts,
    ("paired",))
chk("04i_propagate_to_perk is paired-only",
    next(st for st in S.STAGES if st.script == "04i_propagate_to_perk.py").layouts,
    ("paired",))
chk("an ordinary stage applies to both",
    next(st for st in S.STAGES if st.script == "05c_detect_rois.py").layouts, None)
# The restriction is ls_layouts', not this file's. If the two ever disagree the
# sidebar offers a stage run_all.sh skips, or hides one it runs.
chk("...and the list is ls_layouts', not a copy of it",
    {st.script: st.layouts for st in S.STAGES if st.layouts is not None},
    {k: S.LY.layouts_for(k) for k in S.LY.RESTRICTED})


# --- markers are positions here, names only once a study is chosen ---------
print()
print("--- no stage names a marker ---")

# The table may not contain a fluorophore literal anywhere it decides
# something: argv is what runs, outputs is what `done` looks for.
AF = re.compile(r"AF\d+")
named = []
for st in S.STAGES:
    for a in st.argv:
        if AF.search(a):
            named.append(f"{st.sid} argv {a}")
    for o in st.outputs:
        if isinstance(o, str) and AF.search(o):
            named.append(f"{st.sid} outputs {o}")
chk("no argv or output holds a marker literal", named, [])

chk("every Art names an artifact ls_paths knows",
    sorted({o.artifact for st in S.STAGES for o in st.outputs
            if isinstance(o, S.Art)} - set(S.LP.ARTIFACTS)), [])

# A study resolves every token. A `{marker1}` left in a path is a stage that can
# never be done, so it must not survive for a study that HAS two markers.
two = S.LP.Names(os.path.join(tempfile.gettempdir(), "nostudy"),
                 ["Mk1", "Mk2"], "paired")
left = [f"{st.sid} {n}" for st in S.STAGES
        for n in st.output_names(two.out_root, two) + list(st.argv)
        if "{marker" in S.fill_markers(n, two.markers)]
chk("a two-marker study resolves every marker token", left, [])
chk("...and there were tokens to resolve",
    sum(1 for st in S.STAGES for a in st.argv if "{marker" in a) > 0, True)

# The token is LEFT IN when the study cannot answer it, rather than dropped.
chk("a one-marker study keeps the token it cannot fill",
    S.fill_markers("--marker {marker1}", ["Mk1"]), "--marker {marker1}")


# --- a renamed artifact keeps its tick -------------------------------------
print()
print("--- the pre-rename name still counts as done ---")

with tempfile.TemporaryDirectory() as tmp:
    names = S.LP.Names(tmp, ["AF568", "AF488"], "paired")
    st = S.BY_ID["propagate_perk"]
    chk("nothing on disk: not done", st.done(tmp, names), False)
    chk("...and it asks for the name a writer writes today",
        st.output_names(tmp, names), ["reformatted/rotation_overrides_AF568.csv"])
    legacy = names.legacy_path("overrides", "AF568")
    os.makedirs(os.path.dirname(legacy), exist_ok=True)
    open(legacy, "w").close()
    chk("the pre-rename file alone satisfies it", st.done(tmp, names), True)
    chk("...and nothing is reported missing", st.missing(tmp, names), [])
    # Without a study there is nothing to resolve against, and guessing would be
    # how a stage reports done for a file belonging to a different one.
    chk("with no study named, it is not done", st.done(tmp), False)

# 05a writes a pair of files per marker its export holds, and a paired study can
# curate the second marker before the first: that run is done too.
with tempfile.TemporaryDirectory() as tmp:
    names = S.LP.Names(tmp, ["AF568", "AF488"], "paired")
    st = S.BY_ID["roi_geometry"]
    for art in ("roi_geometry", "roi_boxes"):
        p = names.path(art, "AF488")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").close()
    chk("05a placed for the second marker only: done", st.done(tmp, names), True)
    chk("...and shown under the first marker's names, as before",
        st.output_names(tmp, names),
        ["reformatted/roi_geometry_AF568.csv", "reformatted/roi_boxes_AF568.csv"])

# --- one stage's work does not tick another --------------------------------
print()
print("--- no two stages are satisfied by the same file ---")

# done() is existence-only, so an output two stages both declare - or a
# directory one declares that another's file lands in - makes running either
# tick both, and "Run all remaining" then skips the one never run. 01c before
# and after correction both declared qc/tile_artifact, 01h declared the
# qc/flatfield directory 01f builds into, and 06e declared 06c's and 06d's
# workbooks. A pair that genuinely should tick together goes here, keyed by
# layout, with why.
#
# Under multiplex every marker answers with the same unsuffixed names
# (ls_paths.geometry_source), so the PCNA and pERK halves of a stage pair write
# one file between them.
SHARED_OK = {
    ("multiplex", frozenset(("artifact_pcna", "artifact_perk"))):
        "one scan per section: 04g masks the one DAPI it shares, once",
    ("multiplex", frozenset(("reformat_pcna", "reformat_perk_final"))):
        "the reformat is decided once per scene and shared by every marker",
    ("multiplex", frozenset(("reformat_pcna_final", "reformat_perk_final"))):
        "the reformat is decided once per scene and shared by every marker",
}


def _satisfiers(st, names):
    """Every relative path that would satisfy one of this stage's outputs."""
    return {os.path.relpath(p, names.out_root).replace(os.sep, "/").rstrip("/")
            for e in st.outputs for p in st._alternatives(e, names.out_root, names)[1]}


def _overlap(a, b):
    return sorted(x for x in a for y in b
                  if x == y or y.startswith(x + "/") or x.startswith(y + "/"))


for layout in S.LY.CH.LAYOUTS:
    names = S.LP.Names(os.path.join(tempfile.gettempdir(), "nostudy"),
                       ["Mk1", "Mk2"], layout)
    live = [st for st in S.STAGES if st.applies(layout)]
    shared = []
    for i, a in enumerate(live):
        for b in live[i + 1:]:
            hit = _overlap(_satisfiers(a, names), _satisfiers(b, names))
            if hit and (layout, frozenset((a.sid, b.sid))) not in SHARED_OK:
                shared.append(f"{a.sid}/{b.sid}: {hit}")
    chk(f"{layout}: no output satisfies two stages", shared, [])
chk("every allowed overlap carries a reason",
    all(isinstance(v, str) and v.strip() for v in SHARED_OK.values()))
chk("every allowed overlap names real stages and a layout",
    sorted(f"{lay} {sorted(pair)}" for lay, pair in SHARED_OK
           if lay not in S.LY.CH.LAYOUTS or not pair <= set(S.BY_ID)), [])


# The same thing as the operator meets it: run one, look at the other. A fresh
# tree per pair, so nothing ticks from an earlier pair's files.
def _touch(st, tmp, names):
    for e in st.outputs:
        p = st._alternatives(e, tmp, names)[1][0]
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").close()


for ran, other in (("artifact_before", "artifact_after"),
                   ("artifact_after", "artifact_before"),
                   ("tilefield_build", "tilefield_raw"),
                   ("excel_sample", "build_results"),
                   ("build_results", "excel_slide")):
    with tempfile.TemporaryDirectory() as tmp:
        names = S.LP.Names(tmp, ["Mk1", "Mk2"], "paired")
        _touch(S.BY_ID[ran], tmp, names)
        chk(f"running {ran} leaves {other} not done",
            S.BY_ID[other].done(tmp, names), False)

print("--- 06i detection overlays ---")
_do = S.BY_ID.get("detection_overlays")
chk("detection_overlays is a stage", _do is not None)
if _do is not None:
    chk("...that runs 06i", _do.script, "06i_detection_overlays.py")
    chk("...needs the per-ROI dataset", _do.needs, ["roi_dataset"])
    chk("...in the Beyond blinding band", _do.group, "Beyond blinding")
    chk("...reads pixels with pylibCZIrw", _do.reader, "pylibCZIrw")
chk("06i is no longer parked in NOT_LISTED", "06i_detection_overlays.py" in S.NOT_LISTED, False)

print()
print("ALL PASS" if not failures else f"{len(failures)} FAILED")
sys.exit(1 if failures else 0)
