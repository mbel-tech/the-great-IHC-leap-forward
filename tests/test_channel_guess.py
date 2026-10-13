"""Guessing a channel from what the CZI records, and the marker list it offers.

A CZI records the dye and wavelengths, not the antibody; so what is pinned here is
that the band and the counterstain are read sensibly from either the numbers or the
dye text, that a file silent about a field is not a crash, that a person's table is
never overwritten by a detection, and that every offered marker is a legal name.

The synthetic file is built byte-for-byte to the layout `czi_meta.read_metadata`
reads; no real Zeiss file ships with the tests, so the XML mirrors what ZEN writes
(Channel under Dimensions, DyeName/ShortName/Color under DisplaySetting).

Run:  python tests/test_channel_guess.py
"""

import os
import struct
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

failures = []


def chk(label, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + label.ljust(70) + " " + repr(got))
    if not ok:
        print("     want " + repr(want))
        failures.append(label)


import czi_meta                                                      # noqa: E402
import ls_channel_edit as E                                          # noqa: E402
import ls_channel_guess as G                                         # noqa: E402
import ls_channels as CH                                             # noqa: E402

XML = """<ImageDocument><Metadata><Information><Image><Dimensions><Channels>
<Channel Id="Channel:0" Name="DAPI"><ExcitationWavelength>353</ExcitationWavelength>
  <EmissionWavelength>465</EmissionWavelength></Channel>
<Channel Id="Channel:1" Name="AF568"><ExcitationWavelength>577</ExcitationWavelength>
  <EmissionWavelength>603</EmissionWavelength></Channel>
<Channel Id="Channel:2" Name="Ch3"><Fluor>Alexa Fluor 488</Fluor></Channel>
<Channel Id="Channel:3" Name="Ch4"/>
</Channels></Dimensions></Image></Information>
<DisplaySetting><Channels>
<Channel Id="Channel:1"><DyeName>Alexa Fluor 568</DyeName><ShortName>AF568</ShortName>
  <Color>#FFFF0000</Color></Channel>
</Channels></DisplaySetting></Metadata></ImageDocument>"""

print("--- reading the metadata ---")
info = czi_meta.channel_info(ET.fromstring(XML))
chk("one entry per channel, in plane order", [i["name"] for i in info], ["DAPI", "AF568", "Ch3", "Ch4"])
chk("wavelengths are read", (info[1]["excitation_nm"], info[1]["emission_nm"]), (577.0, 603.0))
chk("the dye comes from the display setting too", (info[1]["dye_name"], info[1]["short_name"]),
    ("Alexa Fluor 568", "AF568"))
chk("a Fluor element is read", info[2]["fluor"], "Alexa Fluor 488")
chk("a silent channel is None, not a crash", (info[3]["fluor"], info[3]["excitation_nm"]), (None, None))
chk("a wavelength written in metres is scaled",
    czi_meta._wavelength("5.77e-07"), 577.0)

print("--- band and counterstain ---")
chk("emission decides the band", [G.band(i) for i in info], ["blue", "red", "green", None])
chk("the dye text decides when there are no wavelengths",
    [G.band({"name": n}) for n in ("AF488", "Cy3", "AF647", "Hoechst 33342", "Mystery")],
    ["green", "red", "far-red", "blue", None])
chk("whole numbers only: AF5680 is nothing", G.band({"name": "AF5680"}), None)
chk("a blue band is the counterstain", [G.looks_nuclear(i) for i in info], [True, False, False, False])
chk("a description says dye, wavelengths and band", G.describe(info[1]),
    "Alexa Fluor 568 | ex 577 / em 603 nm | red band")
chk("a channel with nothing recorded is described, not hidden", G.describe(info[3]),
    "Ch4 | band unknown")

print("--- detection ---")
rows = G.detect(info)
chk("roles: one counterstain, the rest markers", [r["role"] for r in rows],
    ["nuclear", "marker", "marker", "marker"])
chk("names default to the file's own, DAPI kept", [r["name"] for r in rows], ["DAPI", "AF568", "Ch3", "Ch4"])
chk("markers get their band's colour, far-red is magenta",
    [r["colour"] for r in rows], [None, "red", "green", None])
chk("far-red maps to magenta", G.BAND_COLOUR["far-red"], "magenta")
two_blue = G.detect([{"name": "DAPI", "emission_nm": 461}, {"name": "Hoechst", "emission_nm": 460}])
chk("a second blue channel is a marker, never a second counterstain",
    [r["role"] for r in two_blue], ["nuclear", "marker"])

print("--- never overwrite the person's table ---")
layout = "multiplex"
mine = [{"orig": "Fos", "name": "Fos", "role": "marker", "czi_name": "AF568", "index": 1,
         "segment": "nuclear", "colour": "yellow", "show": True, "extra": {}}]
merged, added = E.merge_detected(mine, rows, layout)
chk("a channel already there keeps its name and colour",
    (merged[0]["name"], merged[0]["colour"]), ("Fos", "yellow"))
chk("...and gains its detection note", merged[0]["detected"], "Alexa Fluor 568 | ex 577 / em 603 nm | red band")
chk("the other channels are appended", (added, [r["czi_name"] for r in merged]), (3, ["AF568", "DAPI", "Ch3", "Ch4"]))
chk("the counterstain is suggested only when there is none",
    [r["role"] for r in merged], ["marker", "nuclear", "marker", "marker"])
merged2, _ = E.merge_detected(merged, rows, layout)
chk("detecting twice adds nothing", len(merged2), len(merged))
withnuc = [{"orig": "Hoechst", "name": "Hoechst", "role": "nuclear", "czi_name": "Hoechst", "index": 0,
            "extra": {}}]
m3, _ = E.merge_detected(withnuc, rows, layout)
chk("an existing counterstain turns a detected DAPI into 'ignore', not a second counterstain",
    sorted(r["role"] for r in m3), ["ignore", "marker", "marker", "marker", "nuclear"])

print("--- a CZI on disk ---")


def write_czi(path, xml):
    body = xml.encode("utf-8")
    head = bytearray(czi_meta.HEADER_LEN)
    head[:10] = b"ZISRAWFILE"
    struct.pack_into("<QQ", head, 84, 0, czi_meta.HEADER_LEN)
    seg = bytearray(czi_meta.SEGMENT_HEADER_LEN)
    seg[:14] = b"ZISRAWMETADATA"
    sizes = struct.pack("<ii", len(body), 0).ljust(czi_meta.METADATA_DATA_LEN, b"\0")
    with open(path, "wb") as fh:
        fh.write(bytes(head) + bytes(seg) + sizes + body)


with tempfile.TemporaryDirectory() as tmp:
    p = os.path.join(tmp, "s.czi")
    write_czi(p, XML)
    found = E.czi_channels(p)
    chk("a file is read end to end", [(f["czi_name"], f["role"], f["band"]) for f in found],
        [("DAPI", "nuclear", "blue"), ("AF568", "marker", "red"), ("Ch3", "marker", "green"),
         ("Ch4", "marker", None)])
    bad = os.path.join(tmp, "x.czi")
    open(bad, "wb").write(b"not a czi" * 50)
    try:
        E.czi_channels(bad)
        chk("a non-CZI is refused", False, True)
    except ValueError as e:
        chk("a non-CZI is refused with its name", "x.czi" in str(e), True)
    write_czi(p, "<ImageDocument><Metadata/></ImageDocument>")
    try:
        E.czi_channels(p)
        chk("a file with no channels is refused", False, True)
    except ValueError as e:
        chk("a file with no channels is refused", "no channels" in str(e), True)

print("--- the marker list ---")
every = [m for b in G.BY_BAND.values() for m in b]
chk("every offered marker is a legal name", [m for m in every if not CH.NAME_RE.match(m)], [])
typ, rest = G.markers_for("green")
chk("green opens with its own markers", typ[:3], ["Ki-67", "Pan-cytokeratin", "E-cadherin"])
chk("...and the rest do not repeat them", set(typ) & set(rest), set())
chk("nothing is offered twice", len(typ) + len(rest), len(set(typ) | set(rest)))
chk("an unknown band offers everything, none 'typical'", (G.markers_for(None)[0], len(G.all_markers()) > 30), ([], True))
chk("the blue list leads with the counterstains", G.BLUE[:2], ["DAPI", "Hoechst 33342"])
chk("the reference list ships with the repo", os.path.exists(os.path.join(os.path.dirname(HERE), "docs", "channel-markers.md")), True)

print()
print("FAILED: " + ", ".join(failures) if failures else "ALL PASS")
sys.exit(1 if failures else 0)
