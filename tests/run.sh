#!/usr/bin/env bash
# Run the curator suites and the Python suites.
#
#   bash tests/run.sh
#
# The curators are generated pages, so there is a build step: each page is
# regenerated INTO tests/build/ with --out, its <script> is lifted out, and the
# suites run against that. The operator's live pages under out_root are never
# touched, so a test run cannot rewrite or downgrade a file someone is curating
# in.
#
# --no-seed matters for the ROI curator. The page normally embeds whatever
# curation is in out_root/curation; a suite reading that would start with
# hundreds of sections it knows nothing about, and its counts would change every
# time someone curated.
# Tests must not depend on the operator's data.
#
# What these cover: state, ordering and what reaches the exports. What they do
# not: anything visual. Canvas calls are swallowed by the stub, so rendering is
# checked in a real browser instead.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TESTS="$REPO/tests"
BUILD="$TESTS/build"

# Prefer the app venv, since that is the interpreter the pipeline is built on.
PY="${PY:-$REPO/work/appenv/Scripts/python.exe}"

# A suite that does not name its own config would fall through to the
# operator's live config.json and then pass or fail on their data rather
# than on its own. This makes that fallback an error instead. Suites that
# want a config write one - see tests/_fixture.py.
export LS_CONFIG_STRICT=1
[ -x "$PY" ] || PY="python"
# Exported for the page suites that run a stage function on what the page
# exported (curator_exports.test.js runs 05a's analysis set).
export PY

mkdir -p "$BUILD"

# build_page <script> <name> [args...]: generate scripts/<script> to
# tests/build/<name>.html and lift its largest <script> block to <name>.js.
build_page () {
  local script="$1" name="$2"; shift 2
  echo "building $name..."
  LS_CONFIG="$BUILD_CONFIG" "$PY" "$REPO/scripts/$script" "$@" --out "$BUILD/$name.html" > /dev/null || {
    echo "could not generate $name"; exit 2; }
  "$PY" - "$BUILD/$name.html" "$BUILD/$name.js" <<'PYEOF' || exit 2
import io, re, sys
html = io.open(sys.argv[1], encoding="utf-8").read()
js = max(re.findall(r"<script>(.*?)</script>", html, re.S), key=len)
io.open(sys.argv[2], "w", encoding="utf-8", newline="").write(js)
print(f"  {len(js)} chars -> {sys.argv[2]}")
PYEOF
}

# Which study the pages are built from. A real one when there is a config:
# --worklist reads reformatted/roi_worklist.csv, so the page is generated from
# pipeline outputs, and the operator's are the richest there are. They are
# handed the repo config EXPLICITLY rather than inheriting it, so that
# dependency is stated rather than accidental.
#
# With NO config - a fresh clone, and therefore CI - the pages are built from a
# synthetic study instead: tests/_curator_study.py writes the smallest set of
# pipeline outputs the two generators read (no operator data; the atlas is the
# shipped one) and the REAL 04l/04k run against it end to end. Until that
# existed every page suite was skipped on every machine without a study, which
# is every machine CI has.
BUILD_CONFIG="${LS_BUILD_CONFIG:-$REPO/config.json}"
PAGE_STUDY=real
SKIP_PAGES=0
if [ ! -f "$BUILD_CONFIG" ]; then
  if [ -n "${LS_BUILD_CONFIG:-}" ]; then
    # Named and absent is a mistake, not a fresh clone: say so rather than
    # quietly testing a different study than the one asked for.
    echo "no $BUILD_CONFIG - skipping the curator page suites"
    SKIP_PAGES=1
  else
    SYNTH="$(mktemp -d "${TMPDIR:-/tmp}/ls-curator-study-XXXXXX")"
    trap 'rm -rf "$SYNTH"' EXIT
    echo "no $BUILD_CONFIG - building the curator pages from a synthetic study"
    BUILD_CONFIG="$("$PY" "$TESTS/_curator_study.py" "$SYNTH")" || {
      echo "could not write the synthetic study"; exit 2; }
    PAGE_STUDY=synthetic
  fi
else
  # A config that exists but points at an out_root that does not is the state
  # this machine is in whenever the data drive is unplugged, and the drive here
  # does not merely drop writes - it goes away (see scripts/ls_io.py). The
  # pages are generated from real pipeline outputs, so there is nothing to
  # build; the Python suites do not need them and must still run.
  #
  # LS_CONFIG is named here for the same reason build_page names it: this
  # question is about the config the pages are built against, not about
  # whichever config a bare ls_config would find. Naming it is also what makes
  # the read work at all under the LS_CONFIG_STRICT set above - strict mode
  # forbids the repo-config fallback, so the un-named form exited 1 and printed
  # nothing on EVERY machine, which made BUILD_OUT always empty and skipped
  # these three suites unconditionally from 3e6c250 until now.
  BUILD_OUT="$(LS_CONFIG="$BUILD_CONFIG" "$PY" "$REPO/scripts/ls_config.py" \
                 --print out_root 2>/dev/null)"
  if [ -z "$BUILD_OUT" ]; then
    # Two different faults, two different remedies, so say which one it is.
    echo "could not read out_root from $BUILD_CONFIG - skipping the curator page suites"
    SKIP_PAGES=1
  elif [ ! -d "$BUILD_OUT" ]; then
    echo "out_root $BUILD_OUT does not exist - skipping the curator page suites"
    SKIP_PAGES=1
  fi
fi

if [ "$SKIP_PAGES" = "0" ]; then
  # NO --marker. It used to say `--marker AF568`, and 04l's choices are now
  # derived from the study - so a second study makes argparse REJECT that
  # value, build_page takes the `exit 2` branch, and the whole run ends there,
  # taking the Python suites down with it. Those suites have nothing to do
  # with the curator pages and must still run.
  #
  # Dropping it rather than deriving one: 04l already opens on MARKERS[0], the
  # first declared marker, which for this study IS AF568. Checked, not
  # assumed - the page built with and without the flag is byte-identical.
  build_page 04l_roi_curator.py curator --worklist --rgb --no-seed
  build_page 04k_level_curator.py level_curator
fi

# Page suites that assert facts only the real study has - a count of its
# sections, one of its scene_uids - and so cannot pass against the synthetic
# one. They run whenever the pages are built from a real study, and are listed
# here, with the reason, rather than edited to pass or dropped without a word.
# One line each: "<file> <reason>". Empty today: every page suite passes
# against the synthetic study.
NEEDS_REAL_STUDY=(
)
needs_real () {
  local entry
  for entry in ${NEEDS_REAL_STUDY[@]+"${NEEDS_REAL_STUDY[@]}"}; do
    [ "${entry%% *}" = "$1" ] && { echo "${entry#* }"; return 0; }
  done
  return 1
}

fail=0
# Counted from the files, not written down: the summary used to say "16" while
# there were 21.
PAGE_SUITES=("$TESTS"/*.test.js)
N_PAGE=${#PAGE_SUITES[@]}
PAGE_RAN=0
PAGE_NOT_RUN=()
if [ "$SKIP_PAGES" = "0" ]; then
for t in "${PAGE_SUITES[@]}"; do
  echo
  echo "=== $(basename "$t") ==="
  if [ "$PAGE_STUDY" = "synthetic" ] && why="$(needs_real "$(basename "$t")")"; then
    echo "not run: needs the real study - $why"
    PAGE_NOT_RUN+=("$(basename "$t")")
    continue
  fi
  PAGE_RAN=$((PAGE_RAN + 1))
  node "$t" || fail=1
done
fi

# The Python suites run alongside rather than inside the Node harness: they
# exercise the scripts directly and have no browser stub to share.
for t in "$TESTS"/test_*.py; do
  [ -e "$t" ] || continue
  echo
  echo "=== $(basename "$t") ==="
  "$PY" "$t" || fail=1
done

echo
# A run that skipped the curator pages has not tested the curators AT ALL.
# Printing a bare "ALL SUITES PASS" over them is how this file already went
# green once over 222 files it never opened, and how the page suites themselves
# went unrun from 3e6c250 onward. The summary line says what it actually
# covered: how many page suites ran, against which study, and how many did not.
if [ "$SKIP_PAGES" = "1" ]; then
  pages="THE $N_PAGE CURATOR PAGE SUITES WERE SKIPPED (reason above)"
else
  pages="$PAGE_RAN of $N_PAGE curator page suites ran against the $PAGE_STUDY study"
  if [ "${#PAGE_NOT_RUN[@]}" -gt 0 ]; then
    pages="$pages; ${#PAGE_NOT_RUN[@]} NEED THE REAL STUDY AND WERE NOT RUN: ${PAGE_NOT_RUN[*]}"
  else
    pages="$pages, 0 not run"
  fi
fi
if [ "$fail" -eq 0 ]; then
  if [ "$SKIP_PAGES" = "1" ] || [ "${#PAGE_NOT_RUN[@]}" -gt 0 ]; then
    echo "ALL SUITES PASS - BUT $pages"
  else
    echo "ALL SUITES PASS ($pages)"
  fi
else
  echo "SUITE FAILURES ($pages)"
fi
exit "$fail"
