"""Settings read from the slide files, so Set up does not have to ask for them.

The files here are CZI headers written by hand: the metadata block czi_meta
reads, with no pixels. Two studies: one scan per marker (the LS layout, with
the LS file names, including the odd ones with a suffix), and one multi-channel
scan per section.

Run:  python tests/test_autodetect.py
"""

import json
import os
import struct
import sys
import tempfile
import time

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


def xml(channels, px_m=0.65e-6, exposure_ms=None, objective=None, bits=None, camera=None):
    """A header. `exposure_ms`: {channel name: ms}; `objective`: (magnification, NA,
    immersion); `bits`: ComponentBitCount for the image and every channel."""
    exposure_ms = exposure_ms or {}

    def chan(i, name, em):
        extra = ""
        if name in exposure_ms:
            extra += f"<ExposureTime>{exposure_ms[name] * 1e6:g}</ExposureTime>"
        if bits:
            extra += f"<ComponentBitCount>{bits}</ComponentBitCount>"
        return (f'<Channel Id="Channel:{i}" Name="{name}"><EmissionWavelength>{em}'
                f'</EmissionWavelength>{extra}</Channel>')
    chans = "".join(chan(i, name, em) for i, (name, em) in enumerate(channels))
    inst = ""
    if objective or camera:
        inst = "<Instrument>"
        if objective:
            mag, na, imm = objective
            inst += (f'<Objectives><Objective Id="Objective:1" Name="Plan-Apochromat {mag}x/{na}">'
                     f"<NominalMagnification>{mag}</NominalMagnification><LensNA>{na}</LensNA>"
                     f"<Immersion>{imm}</Immersion></Objective></Objectives>")
        if camera:
            inst += (f'<Detectors><Detector Id="Detector:1"><Manufacturer><Model>{camera}'
                     f"</Model></Manufacturer></Detector></Detectors>")
        inst += "</Instrument>"
    img_bits = f"<ComponentBitCount>{bits}</ComponentBitCount>" if bits else ""
    return (f"<ImageDocument><Metadata><Information><Image><SizeC>{len(channels)}</SizeC>"
            f"{img_bits}"
            f"<Dimensions><Channels>{chans}</Channels></Dimensions></Image>{inst}</Information>"
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

print("--- the render resolution is whole native pixels near 2.6 ---")
for px, want in ((0.65, 2.6), (0.325, 1.3), (1.3, 2.6), (0.1625, 1.3), (5.2, 5.2)):
    chk("render at %g µm pixels" % px, A.render_um_per_px(px), want)

print("--- every header field, and what it did ---")
with tempfile.TemporaryDirectory() as tmp:
    for name, chans, exp in (("LS22_1a.czi", [DAPI, AF568], {"DAPI": 20, "AF568": 120}),
                             ("LS22_1b.czi", [DAPI, AF488], {"DAPI": 20, "AF488": 80}),
                             ("LS45_8a.czi", [DAPI, AF568], {"DAPI": 20, "AF568": 300}),
                             ("LS45_8b.czi", [DAPI, AF488], {"DAPI": 20.1, "AF488": 80})):
        write_czi(os.path.join(tmp, name),
                  xml(chans, exposure_ms=exp, objective=(20, 0.8, "Air"), bits=14,
                      camera="Axiocam 712 mono"))
    d = A.detect(tmp)
    rep = d.report
    chk("the report is JSON", bool(json.dumps(rep)), True)
    chk("its sections", sorted(rep), ["channels", "fields", "instrument", "optics", "read",
                                      "scenes", "uses", "varying"])
    chk("files read and found", (rep["read"]["files_read"], rep["read"]["files_found"]), (4, 4))
    chk("the objective's NA", rep["instrument"]["objective_na"]["value"], "0.8")
    chk("the immersion, from the flat fields", rep["instrument"]["objective_immersion"]["value"],
        "Air")
    chk("the objective's name", rep["instrument"]["objective_name"]["value"],
        "Plan-Apochromat 20x/0.8")
    chk("the camera", rep["instrument"]["camera"]["value"], "Axiocam 712 mono")
    chk("the bit depth", rep["instrument"]["bit_depth"]["value"], "14")
    chk("a row per plane and channel",
        [(c["index"], c["name"], c["band"]) for c in rep["channels"]],
        [(0, "DAPI", "blue"), (1, "AF488", "green"), (1, "AF568", "red")])
    chk("AF568's exposure range", next(c for c in rep["channels"] if c["name"] == "AF568")
        ["exposure_ms"], [120.0, 300.0])
    chk("optical resolution: 0.61 × 603 nm / 0.8",
        next(c for c in rep["optics"]["channels"] if c["name"] == "AF568")["resolution_um"],
        round(0.61 * 603 / 1000 / 0.8, 3))
    chk("every use names a field, a value and a use",
        all(set(u) == {"field", "value", "use"} for u in rep["uses"]), True)
    chk("each field appears once",
        len({u["field"] for u in rep["uses"]}) == len(rep["uses"]), True)
    chk("the exposure says what it checks",
        next(u["use"] for u in rep["uses"] if u["field"] == "channels[1 AF568].exposure_ms"),
        "checks: exposures must match across files")
    chk("the full field table is kept",
        "Information/Instrument/Objectives/Objective/LensNA" in rep["fields"], True)
    chk("an exposure that differs is a problem",
        [p for p in d.problems if "exposed" in p],
        ["AF568 was exposed for 120–300 ms across files; signal is not directly "
         "comparable between those files."])
    chk("...but 0.5 % is not", any(p.startswith("DAPI was exposed") for p in d.problems), False)
    chk("0.65 µm meets Nyquist at NA 0.8 for none of these, and says so in the notes",
        sum("coarser than half" in n for n in d.notes), 3)
    chk("...not in the problems", any("coarser than half" in p for p in d.problems), False)
    chk("...nor in lines()", any("coarser than half" in x for x in d.lines()), False)
    chk("...and the optics section keeps them",
        [c["nyquist"] for c in rep["optics"]["channels"]], [False, False, False])
    chk("render resolutions from 0.65",
        (d.values["curation_render.um_per_px"], d.values["scene_render.um_per_px"]), (2.6, 2.6))
    chk("colours from the bands", d.values["display.colours"], {"AF568": "red", "AF488": "green"})
    chk("the counterstain is blue", d.values["display.nuclear_colour"], "blue")
    chk("the instrument is one line", [x for x in d.lines() if x.startswith("Instrument")],
        ["Instrument: objective Plan-Apochromat 20x/0.8, camera Axiocam 712 mono."])
    own = {"acquisition": {"layout": "paired", "markers": ["AF568", "AF488"]},
           "display": {"colours": {"AF568": "magenta"}}}
    filled = A.fill(own, d)
    chk("a study's own colours are never filled over, not even partly",
        [k for k in filled if k.startswith("display")], [])
    filled = A.fill({"acquisition": {"layout": "paired", "markers": ["AF568"]}}, d)
    chk("detected colours keep only the study's markers", filled["display.colours"],
        {"AF568": "red"})

with tempfile.TemporaryDirectory() as tmp:
    for i, bits in enumerate((14, 12)):
        write_czi(os.path.join(tmp, f"Mouse{i + 1}_s1.czi"),
                  xml([DAPI, AF488, AF568], px_m=0.325e-6, bits=bits,
                      objective=((20, 0.8, "Air"), (40, 0.95, "Air"))[i]))
    d = A.detect(tmp)
    chk("multiplex colours from the channel table", d.values["display.colours"],
        {"AF488": "green", "AF568": "red"})
    chk("render resolutions from 0.325", d.values["curation_render.um_per_px"], 1.3)
    chk("differing bit depth is a problem", any("bit depth" in p for p in d.problems), True)
    chk("differing objective is a problem", any("objective" in p for p in d.problems), True)
    chk("varying fields are listed",
        "Information/Instrument/Objectives/Objective/LensNA" in d.report["varying"], True)
    chk("the report is JSON", bool(json.dumps(d.report)), True)

print("--- 400 headers of 3000 fields are read in seconds ---")
def big_flat(i):
    flat = {}
    for j in range(2990):
        flat["Information/Instrument/Things/Thing[Thing:%d]/Part[%d]/Value" % (j // 10, j % 10 + 1)] = str(j)
    flat["Information/Instrument/Objectives/Objective[Objective:1]@Name"] = "Plan-Apochromat 20x/0.8"
    flat["Information/Instrument/Objectives/Objective[Objective:1]/LensNA"] = "0.8"
    flat["Information/Instrument/Objectives/Objective[Objective:1]/Immersion"] = "Air"
    flat["Information/Instrument/Microscopes/Microscope[Microscope:1]@Name"] = "Axioscan 7"
    flat["Information/Application/Version"] = "3.%d" % (i % 2)
    flat["Information/Image/AcquisitionDateAndTime"] = "2025-01-%02dT10:00:00" % (i % 28 + 1)
    return flat
many = [("F%d.czi" % i, {"channels": [], "scenes": []}, [], big_flat(i)) for i in range(400)]
d = A.Detection()
d.files = [m[0] for m in many]
t0 = time.time()
A._report(d, many)
took = time.time() - t0
print("     report of 400 x 3000 fields took %.2f s" % took)
chk("400 headers of 3000 fields report in under 5 s", took < 5, True)
chk("a bracket key with a slash in the last segment still matches",
    A.by_tag(A.tag_index({"Objectives/Objective[Plan 20x/0.8]@Name": "x", "Objectives/"
                          "Objective[Plan 40x/0.95]@Name": "y"}, ("Objective@Name",)),
             "Objective@Name"), "x")
chk("...and found the immersion", d.report["instrument"]["objective_immersion"]["value"], "Air")
chk("...the microscope", d.report["instrument"]["microscope"]["value"], "Axioscan 7")
chk("...and the NA from the top value", d.report["optics"]["na"], 0.8)
chk("a varying version is shown as varying",
    d.report["instrument"]["application_version"]["value"].startswith("3."), True)

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
