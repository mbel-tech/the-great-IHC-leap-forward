#!/usr/bin/env bash
# Rebuild pipeline-methods.docx, pipeline-guide.docx and technical-guide.docx from
# their .md sources, and put copies beside the slide data.
#
# Six steps, in order, because each feeds the next:
#   0. check the stage list against the scripts, the figure and the guide - a
#      guide missing a stage must not be shippable
#   1. redraw the workflow figure
#   2. regenerate the software table FROM the installed environments and splice
#      it into the .md, so a version cannot drift between what is installed and
#      what the document claims
#   3. regenerate the technical guide's stage, option and file tables from the
#      code (make_technical_tables.py), for the same reason
#   4. render every DOCX through one render() so their flags cannot drift
#   5. copy .md, .docx and the figure to the data drive
#
# ref_styled.docx is pandoc's default reference doc with the theme font
# references (minorHAnsi / majorHAnsi) replaced by named families - LibreOffice
# resolves the theme refs to a slab serif, so the PDF came out looking nothing
# like the DOCX until they were spelled out. Same file the ROI curator guide uses.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
PANDOC="${PANDOC:-/c/Users/marti/Downloads/pandoc-3.10.2-windows-x86_64/pandoc-3.10.2/pandoc}"
PYTHON="${PYTHON:-py -3.14}"
SOFFICE="${SOFFICE:-/c/Program Files/LibreOffice/program/soffice.exe}"
# Where the slides live. The document is written here as well as into the repo so
# it sits beside the data it describes.
DATA_DIR="${LS_DATA_DIR:-/d/SLIDES HE DEC 2025 LS}"

cd "$HERE"

echo "[1/6] stage list vs scripts, figure and guide"
$PYTHON "$ROOT/tests/test_stages.py" > /dev/null

echo "[2/6] workflow figure"
$PYTHON make_workflow_figure.py

echo "[3/6] software versions, read from the installed environments"
$PYTHON make_versions_table.py

echo "[4/6] technical guide tables, read from the code"
$PYTHON make_technical_tables.py
$PYTHON "$ROOT/tests/test_technical_guide.py" > /dev/null

# The reference doc is tracked under roi_curator_guide/; a copy beside this
# script, where older checkouts kept it, still wins.
REF_DOCX="$HERE/ref_styled.docx"
[ -f "$REF_DOCX" ] || REF_DOCX="$HERE/roi_curator_guide/ref_styled.docx"

# One function, every document: copies of this flag list would drift, and a
# reader comparing the files would read the difference as a decision.
render() {
    "$PANDOC" "$1.md" -o "$1.docx" \
        --reference-doc="$REF_DOCX" --toc --toc-depth=2 \
        --resource-path=. -f markdown+raw_html
    if [ "${WITH_PDF:-0}" = "1" ]; then
        rm -f "$1.pdf"
        "$SOFFICE" --headless --convert-to pdf --outdir . "$1.docx" >/dev/null 2>&1
        echo "      also wrote $1.pdf"
    fi
}

# The guides and their pictures live in guides/; the tools stay here.
cd "$HERE/guides"

echo "[5/6] pandoc -> pipeline-methods.docx, pipeline-guide.docx, technical-guide.docx"
render pipeline-methods
render pipeline-guide
render technical-guide

echo "[6/6] copy to $DATA_DIR"
if [ -d "$DATA_DIR" ]; then
    mkdir -p "$DATA_DIR/img"
    cp pipeline-methods.md pipeline-guide.md technical-guide.md "$DATA_DIR/"
    cp pipeline-methods.docx pipeline-guide.docx technical-guide.docx "$DATA_DIR/"
    cp img/workflow.png "$DATA_DIR/img/"
    for f in pipeline-methods.pdf pipeline-guide.pdf technical-guide.pdf; do
        [ -f "$f" ] && cp "$f" "$DATA_DIR/"
    done
    echo "      copied"
else
    echo "      SKIPPED - $DATA_DIR is not mounted" >&2
fi

echo "built pipeline-methods.docx, pipeline-guide.docx and technical-guide.docx"
