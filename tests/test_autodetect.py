"""Settings read from the slide files, so Set up does not have to ask for them.

The files here are CZI headers written by hand: the metadata block czi_meta
reads, with no pixels. Two studies: one scan per marker (the LS layout, with
the LS file names, including the odd ones with a suffix), and one multi-channel
scan per section.

Run:  python tests/test_autodetect.py
"""

import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "app"))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import czi_meta                                             # noqa: E402
import autodetect as A                                      # noqa: E402
import ls_naming as NM                                      # noqa: E402

failures = []


def chk(label, got, want):
    ok = got == want
    shown = got if len(repr(got)) < 90 else f"<{type(got).__name__}>"
    print(("ok   " if ok else "FAIL ") + label.ljust(66) + " " + repr(shown))
    if not ok:
        print("     want %r" % (want,))
        failures.append(label)


def xml(channels, px_m=0.65e-6):
    chans = "".join(
        f'<Channel Id="Channel:{i}" Name="{name}"><EmissionWavelength>{em}'
        f'</EmissionWavelength></Channel>' for i, (name, em) in enumerate(channels))
    return (f"<ImageDocument><Metadata><Information><Image><SizeC>{len(channels)}</SizeC>"
            f"<Dimensions><Channels>{chans}</Channels></Dimensions></Image></Information>"
            f'<Scaling><Items><Distance Id="X"><Value>{px_m}</Value></Distance>'
            f'<Distance Id="Y"><Value>{px_m}</Value></Distance></Items></Scaling>'
            f"</Metadata></ImageDocument>")


def write_czi(path, text):
    body = text.encode("utf-8")
    head = bytearray(czi_meta.HEADER_LEN)
    head[:10] = b"ZISRAWFILE"
    struct.pack_into("<QQ", head, 84, 0, czi_meta.HEADER_LEN)
    seg = bytearray(czi_meta.SEGMENT_HEADER_LEN)
    seg[:14] = b"ZISRAWMETADATA"
    sizes = struct.pack("<ii", len(body), 0).ljust(czi_meta.METADATA_DATA_LEN, b"\0")
    with open(path, "wb") as fh:
        fh.write(bytes(head) + bytes(seg) + sizes + body)


DAPI = ("DAPI", 465)
AF568 = ("AF568", 603)
AF488 = ("AF488", 520)

print("--- one scan per marker (the LS layout) ---")
with tempfile.TemporaryDirectory() as tmp:
    for name, chans in (("LS22_1a.czi", [DAPI, AF568]), ("LS22_1b.czi", [DAPI, AF488]),
                        ("LS45_8b.czi", [DAPI, AF488]), ("LS45_8a.czi", [DAPI, AF568]),
                        ("LS53_2b-_A568.czi", [DAPI, AF568]), ("LS85_7b-.czi", [DAPI, AF488]),
                        ("LS105_10a.czi", [DAPI, AF568])):
        write_czi(os.path.join(tmp, name), xml(chans))
    open(os.path.join(tmp, "notes.txt"), "w").write("not a slide")
    d = A.detect(tmp)
    chk("only .czi files count", len(d.files), 7)
    chk("the pixel size comes from the files", d.values["pixel_size_um"], 0.65)
    chk("the overviews are 8 native pixels", d.values["overview_target_um_per_px"], 5.2)
    chk("one marker per scan, differing: paired", d.values["acquisition.layout"], "paired")
    chk("the markers are the second planes' names, first seen first",
        d.values["acquisition.markers"], ["AF568", "AF488"])
    chk("the counterstain is plane 0", d.values["channels.dapi_index"], 0)
    pat = NM.compile_pattern({"pattern": d.values["slide_naming.pattern"],
                              "case_insensitive": d.values["slide_naming.case_insensitive"]})
    chk("every file name is read", all(NM.parse_name(n, pat) for n in d.files), True)
    chk("the odd one keeps its suffix",
        NM.parse_name("LS53_2b-_A568.czi", pat)["name_suffix"], "-_A568")
    chk("...and reads as animal, slide and replicate",
        [NM.parse_name("LS53_2b-_A568.czi", pat)[k] for k in ("subject", "slide", "replicate")],
        ["LS53", 2, "b"])
    chk("nothing to look at", d.problems, [])

    cfg = {"pixel_size_um": 0.65, "acquisition": {"layout": "paired", "markers": ["AF568", "AF488"]}}
    filled = A.fill(cfg, d)
    chk("a study's own choices are never filled over",
        sorted(k for k in filled if k.startswith(("acquisition", "channels", "pixel"))), [])
    chk("...but what it leaves empty is", "slide_naming.pattern" in filled, True)
    chk("agreeing values are not reported", A.compare(cfg, d), [])
    chk("a disagreement is", A.compare({"pixel_size_um": 0.325}, d),
        [("pixel_size_um", 0.325, 0.65)])
    chk("paired markers named by protein are caught: no scan would pair with them",
        A.compare({"acquisition": {"layout": "paired", "markers": ["pERK", "PCNA"]}}, d),
        [("acquisition.markers", ["pERK", "PCNA"], ["AF568", "AF488"])])

print("--- one multi-channel scan per section ---")
with tempfile.TemporaryDirectory() as tmp:
    for i in range(3):
        write_czi(os.path.join(tmp, f"Mouse{i + 1}_s{i + 2}.czi"),
                  xml([DAPI, AF488, AF568], px_m=0.325e-6))
    d = A.detect(tmp)
    chk("multiplex", d.values["acquisition.layout"], "multiplex")
    chk("a channel table, counterstain first",
        [(c["name"], c["role"]) for c in d.values["acquisition.channels"]],
        [("DAPI", "nuclear"), ("AF488", "marker"), ("AF568", "marker")])
    chk("its own pixel size", d.values["pixel_size_um"], 0.325)
    chk("names read", d.naming["result"]["failed"], 0)

print("--- nothing there ---")
with tempfile.TemporaryDirectory() as tmp:
    d = A.detect(tmp)
    chk("an empty folder says so", d.lines(), ["No .czi files in this folder."])
    write_czi(os.path.join(tmp, "A1_1.czi"), "<ImageDocument/>")
    open(os.path.join(tmp, "B2_1.czi"), "wb").write(b"junk" * 100)
    d = A.detect(tmp)
    chk("an unreadable file is reported, not raised", len(d.unreadable), 1)
    chk("a file with no pixel size asks for one", any("pixel size" in p for p in d.problems), True)

print()
print("FAILED: " + ", ".join(failures) if failures else "ALL PASS")
sys.exit(1 if failures else 0)
