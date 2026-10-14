"""Stage 4v - cut the Wullimann 1996 cross sections out of the book's PDF.

Writes the folder 04w reads:

    atlas/wullimann1996/   pNN_cross_<level>.png   one image per cross section
                           legends.csv             cross_section, pdf_page,
                                                   abbreviation, definition
                           index.csv               file, pdf_page, label, px_w, px_h
                           p02_fig5_cross_levels.png   where each section was cut

The images are the ones embedded in the PDF, at their own resolution - pulled
out, not re-rendered, so no detail is lost or invented. Each is named from the
"Cross Section NN" heading printed above it; a page with two sections pairs
headings and images top to bottom.

The legends come from the PDF's text layer, which is OCR. A lower-case "l" in
an otherwise upper-case abbreviation is read as "L" ("lOT" -> "LOT", "ECl" ->
"ECL"), and where sections disagree on the abbreviation for the same
definition, the reading most of them give wins. What OCR got wrong everywhere
stays wrong; 04x only uses these names to propose a label a person reviews.

The book: Wullimann MF, Rupp B, Reichert H (1996). Neuroanatomy of the
Zebrafish Brain: A Topological Atlas. Birkhaeuser, Basel. See atlas/README.md.

Run:  python 04v_wullimann_extract.py PATH/TO/wullimann1996.pdf [--out DIR]
Needs pymupdf (requirements-wullimann.txt).
"""

import argparse
import csv
import os
import re
from collections import Counter

HEADING = re.compile(r"^Cross Section\s+([\d ]+?)\s*$")
FIG5_PAGE = 2          # the sagittal diagram marking every cross-section level
SCALE = re.compile(r"^\d+\s*[jJµu]?[jJµu]?m$|^\d+$")


def fix_abbreviation(text):
    """OCR reads a capital L as "l"; undo that only where it is unambiguous."""
    text = text.strip()
    rest = text.replace("l", "")
    if "l" in text and rest and all(c.isupper() or c.isdigit() or not c.isalpha()
                                    for c in rest):
        return text.replace("l", "L")
    return text


def headings(page):
    """[(y, level)] for each "Cross Section NN" heading on the page, top first."""
    out = []
    for b in page.get_text("blocks"):
        m = HEADING.match(b[4].strip())
        if m:
            out.append((b[1], int(m.group(1).replace(" ", ""))))
    return sorted(out)


def legend_rows(page, heads):
    """(level, abbreviation, definition) from the page's legend, top to bottom.

    The legend is set in columns: a column of abbreviations with their
    definitions beside them. The text layer returns each column as one block,
    so lines are paired by position instead - each abbreviation with the
    nearest line to its right on the same baseline. A page with two sections
    gives each line to the heading above it.
    """
    lines = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(span["text"] for span in line["spans"]).strip()
            if text:
                x0, y0, x1, y1 = line["bbox"]
                lines.append((x0, (y0 + y1) / 2, x1, text))
    if not heads:
        return []
    first = heads[0][0]
    abbrs = [l for l in lines if l[1] > first and len(l[3]) <= 12 and " " not in l[3]
             and not HEADING.match(l[3]) and not SCALE.match(l[3])]
    rows = []
    for x0, y, x1, text in abbrs:
        beside = [l for l in lines if l[0] > x1 and abs(l[1] - y) < 3.0
                  and l[0] - x1 < 40]
        if not beside:
            continue
        definition = min(beside, key=lambda l: l[0])[3]
        if " " not in definition and not definition.islower():
            continue                    # the next column's abbreviation, not a definition
        level = [lv for hy, lv in heads if hy <= y][-1]
        rows.append((y, x0, level, fix_abbreviation(text), definition))
    rows.sort(key=lambda r: (r[2] != heads[0][1], r[1], r[0]))
    return [(level, abbr, definition) for _, _, level, abbr, definition in rows]


def main(argv=None):
    import pymupdf

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("pdf", help="the Wullimann 1996 atlas PDF")
    ap.add_argument("--out", help="output folder (default: <repo>/atlas/wullimann1996)")
    args = ap.parse_args(argv)
    here = os.path.dirname(os.path.abspath(__file__))
    out = args.out or os.path.join(os.path.dirname(here), "atlas", "wullimann1996")
    os.makedirs(out, exist_ok=True)

    doc = pymupdf.open(args.pdf)
    index, legends = [], []

    def save(xref, name, page_no, label):
        pix = pymupdf.Pixmap(doc, xref)
        if pix.n - pix.alpha != 3:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        pix.save(os.path.join(out, name))
        index.append({"file": name, "pdf_page": page_no, "label": label,
                      "px_w": pix.width, "px_h": pix.height})

    for page_no, page in enumerate(doc, start=1):
        # The first image on every page is the full-page scan behind the text.
        figures = []
        for img in page.get_images(full=True)[1:]:
            rects = page.get_image_rects(img[0])
            if rects:
                figures.append((rects[0].y0, img[0]))
        figures.sort()
        if page_no == FIG5_PAGE and figures:
            save(figures[0][1], "p02_fig5_cross_levels.png", page_no, "Fig 5 cross-section levels")
            continue
        heads = headings(page)
        if not heads or len(heads) != len(figures):
            continue
        for (_, level), (_, xref) in zip(heads, figures):
            save(xref, "p%02d_cross_%d.png" % (page_no, level), page_no,
                 "Cross Section %d" % level)
        for level, abbr, definition in legend_rows(page, heads):
            legends.append({"cross_section": level, "pdf_page": page_no,
                            "abbreviation": abbr, "definition": definition})

    # The same region is listed on many sections and OCR misreads it on a few
    # ("OIL" for "DIL"): the reading most sections agree on wins.
    votes = {}
    for r in legends:
        votes.setdefault(r["definition"], Counter())[r["abbreviation"]] += 1
    for r in legends:
        best, n = votes[r["definition"]].most_common(1)[0]
        if n > votes[r["definition"]][r["abbreviation"]]:
            r["abbreviation"] = best

    with open(os.path.join(out, "index.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, ["file", "pdf_page", "label", "px_w", "px_h"])
        w.writeheader()
        w.writerows(index)
    with open(os.path.join(out, "legends.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, ["cross_section", "pdf_page", "abbreviation", "definition"])
        w.writeheader()
        w.writerows(legends)
    n = sum(1 for r in index if r["label"].startswith("Cross Section"))
    print("%d cross sections, %d legend entries -> %s" % (n, len(legends), out))


if __name__ == "__main__":
    main()
