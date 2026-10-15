"""czi_report: every header field flattened, and summarised across files.

Run:  python tests/test_czi_report.py
"""

import os
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import czi_report as R                                      # noqa: E402

failures = []


def chk(label, got, want):
    ok = got == want
    shown = got if len(repr(got)) < 90 else f"<{type(got).__name__}>"
    print(("ok   " if ok else "FAIL ") + label.ljust(66) + " " + repr(shown))
    if not ok:
        print("     want %r" % (want,))
        failures.append(label)


def root(body):
    return ET.fromstring(f"<ImageDocument><Metadata>{body}</Metadata></ImageDocument>")


f = R.flatten(root(
    '<Information><Image><SizeC>2</SizeC><Dimensions><Channels>'
    '<Channel Id="Channel:0" Name="DAPI"/><Channel Id="Channel:1" Name="Cy5"/>'
    '</Channels></Dimensions></Image></Information>'
    '<List><Item>a</Item><Item>b</Item></List>'
    '<Solo><Item>x</Item></Solo>'))
chk("text", f["Information/Image/SizeC"], "2")
chk("attribute by Id", f["Information/Image/Dimensions/Channels/Channel[Channel:0]@Name"], "DAPI")
chk("second Id", f["Information/Image/Dimensions/Channels/Channel[Channel:1]@Name"], "Cy5")
chk("position 1-based", (f["List/Item[1]"], f["List/Item[2]"]), ("a", "b"))
chk("sole sibling bare", f["Solo/Item"], "x")
chk("empty element absent", "Information/Image/Dimensions" in f, False)

f = R.flatten(ET.fromstring("<A><B>1</B></A>"))
chk("no Metadata: from root", f, {"B": "1"})

f = R.flatten(root("<T>" + "x" * 300 + "</T>"), max_text=10)
chk("text cut", f["T"], "x" * 10 + "…")

f = R.flatten(root("".join(f"<F{i}>v</F{i}>" for i in range(20))), max_fields=5)
chk("max_fields", (len(f), f.get("_truncated")), (6, "yes"))

print("--- Information and Scaling come before the cap ---")
noise = "".join(f"<P{i}>{i}</P{i}>" for i in range(6000))
f = R.flatten(root(f"<Experiment>{noise}</Experiment><HardwareSetting>{noise}</HardwareSetting>"
                   "<CustomAttributes><A>1</A></CustomAttributes>"
                   "<Information><Image><SizeC>2</SizeC></Image></Information>"
                   '<Scaling><Items><Distance Id="X"><Value>6.5e-07</Value></Distance>'
                   "</Items></Scaling>"))
chk("Information kept though 12000 fields come before it",
    f.get("Information/Image/SizeC"), "2")
chk("...and Scaling", f.get("Scaling/Items/Distance@Id"), "X")
chk("...the rest fills up to the cap", (len(f), f["_truncated"]), (5001, "yes"))
f = R.flatten(root("<Experiment><E>1</E></Experiment><Information><I>2</I></Information>"))
chk("the paths are the same whatever the walking order",
    sorted(f), ["Experiment/E", "Information/I"])

print("--- one element with 4900 children ---")
import time                                                 # noqa: E402
many = root("<Things>" + "".join(f"<Thing>{i}</Thing>" for i in range(4900)) + "</Things>")
t0 = time.time()
f = R.flatten(many)
took = time.time() - t0
print("     flatten took %.3f s" % took)
chk("flatten of 4900 siblings under 0.2 s", took < 0.2, True)
chk("...positions still 1-based and in order",
    (f["Things/Thing[1]"], f["Things/Thing[4900]"]), ("0", "4899"))

c = R.combine({
    "a": {"p": "1", "q": "x", "AcquisitionDateAndTime": "t1"},
    "b": {"p": "1", "q": "y", "AcquisitionDateAndTime": "t2"},
    "c": {"p": "2", "q": "x", "AcquisitionDateAndTime": "t3"},
})
chk("combine files", c["p"]["files"], 3)
chk("combine values", c["p"]["values"], [["1", 2], ["2", 1]])
chk("combine distinct", c["q"]["distinct"], 2)
chk("varying ignores natural", sorted(R.varying(c)), ["p", "q"])
chk("stage and focus positions are natural",
    sorted(R.varying({p: {"distinct": 2} for p in (
        "Experiment/Tile/StagePosition", "Information/Image/FocusPosition",
        "HardwareSetting/Stage/Position/X", "Experiment/Region/PositionY", "Info/TimeStamp",
        "Info/Duration", "HardwareSetting/Focus/Z@Value", "Information/Image/SizeC")})),
    ["Information/Image/SizeC"])
chk("order: Information, then Scaling, then the rest",
    sorted(["Experiment/A", "Scaling/B", "Information/C", "AppliedSetting/D", "Information/A"],
           key=R.order),
    ["Information/A", "Information/C", "Scaling/B", "AppliedSetting/D", "Experiment/A"])
chk("constant", sorted(R.constant({"z": {"distinct": 1}, "y": {"distinct": 2}})), ["z"])

if failures:
    print("\nFAILED:", failures)
    sys.exit(1)
print("\nALL PASS")
