"""Guess what a channel is from what the CZI says about it, and offer markers for it.

A CZI records the dye and the wavelengths of each channel, not the antibody. The
dye says which colour band the channel is in, and the band narrows the likely
targets (the reference list in docs/channel-markers.md); it never says which
marker it is. So this module suggests, and the person chooses:

    band(info)            "blue" | "green" | "red" | "far-red" | None
    looks_nuclear(info)   DAPI / Hoechst, or a blue band (blue is almost always
                          the counterstain)
    describe(info)        one line for the table: dye, wavelengths, band
    detect(infos)         the rows a CZI suggests, ready for the channel editor
    markers_for(band)     (typical for that band, everything else)

`info` is one dict from `czi_meta.channel_info`. Marker names here are what the
pipeline will use in file paths, so each one satisfies `ls_channels.NAME_RE`
(alpha-SMA is `aSMA`, F4/80 is `F4-80`).
"""

import re

import ls_channels as CH

BANDS = ("blue", "green", "red", "far-red")

#: Colour name each band is drawn in by default. Far-red is invisible to the eye,
#: so by convention it is shown magenta.
BAND_COLOUR = {"blue": "blue", "green": "green", "red": "red", "far-red": "magenta"}

# Dye text, checked when the file gives no wavelengths. The digits are matched as
# whole numbers so `AF568` is red and `AF5680` is nothing.
_DYE_BAND = (
    ("blue", r"dapi|hoechst|(?<!\d)(405|420|450)(?!\d)|pacific blue|bv4\d\d|efluor ?450"),
    ("green", r"fitc|gfp|yfp|venus|(?<!\d)(488|500|514)(?!\d)|cf488|atto ?488"),
    ("red", r"cy3|tritc|texas red|rhodamine|mcherry|tdtomato|dsred|"
            r"(?<!\d)(546|555|561|568|594|596)(?!\d)"),
    ("far-red", r"cy5|draq5|(?<!\d)(633|635|647|660|680|700|750)(?!\d)|far.?red"),
)
_NUCLEAR = re.compile(r"dapi|hoechst|draq5|nuclear|nuclei", re.I)


def _text(info):
    return " ".join(str(info.get(k) or "") for k in
                    ("name", "fluor", "dye_name", "short_name")).strip()


def _band_from_nm(emission, excitation):
    if emission:
        return ("blue" if emission < 490 else "green" if emission < 560
                else "red" if emission < 640 else "far-red")
    if excitation:
        return ("blue" if excitation < 430 else "green" if excitation < 530
                else "red" if excitation < 620 else "far-red")
    return None


def band(info):
    """The colour band of a channel: wavelengths first, then the dye's name."""
    b = _band_from_nm(info.get("emission_nm"), info.get("excitation_nm"))
    if b:
        return b
    text = _text(info)
    for name, pat in _DYE_BAND:
        if re.search(pat, text, re.I):
            return name
    return None


def looks_nuclear(info):
    return bool(_NUCLEAR.search(_text(info))) or band(info) == "blue"


def dye(info):
    """The most specific dye label the file gives."""
    for k in ("dye_name", "fluor", "short_name", "name"):
        v = (info.get(k) or "").strip()
        if v:
            return v
    return ""


def describe(info):
    parts = [dye(info) or "no dye recorded"]
    ex, em = info.get("excitation_nm"), info.get("emission_nm")
    if ex and em:
        parts.append("ex %g / em %g nm" % (ex, em))
    elif ex or em:
        parts.append("ex %g nm" % ex if ex else "em %g nm" % em)
    b = band(info)
    parts.append(b + " band" if b else "band unknown")
    return " | ".join(parts)


def default_name(info):
    """What to call the channel before the person chooses: its own name."""
    if looks_nuclear(info):
        t = _text(info)
        if re.search("hoechst", t, re.I):
            return "Hoechst"
        if re.search("dapi", t, re.I):
            return "DAPI"
    name = (info.get("name") or dye(info) or "").strip()
    return name if CH.NAME_RE.match(name) else re.sub(r"[^A-Za-z0-9 _.+-]", "", name).strip()


def detect(infos):
    """Rows the channels of one CZI suggest: [{name, czi_name, index, role, colour, detected, band}].

    One channel is nuclear at most (the validator allows one); a second blue
    channel is offered as a marker rather than refused.
    """
    out, have_nuclear = [], False
    for i, info in enumerate(infos):
        nuclear = looks_nuclear(info) and not have_nuclear
        have_nuclear = have_nuclear or nuclear
        b = band(info)
        out.append({"name": default_name(info), "czi_name": info.get("name") or "",
                    "index": i, "role": CH.NUCLEAR if nuclear else CH.MARKER,
                    "colour": None if nuclear else BAND_COLOUR.get(b),
                    "detected": describe(info), "band": b})
    return out


# ---- the marker list (docs/channel-markers.md) -----------------------------------

BLUE = ["DAPI", "Hoechst 33342", "Lamin B1", "Histone H3", "pHistone H3",
        "Pan-cytokeratin", "Collagen IV"]
GREEN = ["Ki-67", "Pan-cytokeratin", "E-cadherin", "CD3", "CD8", "CD20", "GFAP", "NeuN",
         "MAP2", "bIII-tubulin", "Iba1", "aSMA", "Vimentin", "CD31", "Phalloidin",
         "BrdU", "EdU", "GFP", "YFP", "Venus"]
RED = ["CD68", "CD163", "F4-80", "CD45", "CD8", "CD4", "Pan-cytokeratin", "GFAP", "Iba1",
       "Synaptophysin", "PGP9.5", "Chromogranin A", "Desmin", "Myosin heavy chain", "S100",
       "SOX10", "Melan-A", "Collagen I", "Collagen IV", "Fibronectin", "TOM20", "MTCO1",
       "HSP60", "LAMP1", "LC3B", "Phalloidin-TRITC", "Propidium iodide", "tdTomato",
       "mCherry", "DsRed"]
BY_BAND = {"blue": BLUE, "green": GREEN, "red": RED, "far-red": []}


def markers_for(band_name):
    """(typical for this band, every other marker), each in list order, no repeats."""
    typical = list(BY_BAND.get(band_name, []))
    seen = set(typical)
    rest = []
    for b in BANDS:
        for m in BY_BAND[b]:
            if m not in seen:
                seen.add(m)
                rest.append(m)
    return typical, rest


def all_markers():
    return markers_for(None)[1]
