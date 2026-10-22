"""The tutorial's curation check: a learner's ROIs against the answer key.

    python tests/test_tutorial_check.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(REPO, "app"), os.path.join(REPO, "scripts")]

import tutorial_check as TC                                     # noqa: E402

failures = []


def chk(name, got, want=True):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name:<60} {got!r}" + ("" if ok else f"  (wanted {want!r})"))
    if not ok:
        failures.append(name)


def sq(x, y, w, h):
    return [x, y, x + w, y, x + w, y + h, x, y + h]


def poly(v, roi, region="Dm"):
    return {"v": v, "roi": roi, "region": region, "part": 0, "n": 0}


A = sq(0, 0, 100, 100)
print("--- overlap ---")
chk("a square with itself", TC.iou([A], [A]), 1.0)
chk("disjoint squares", TC.iou([A], [sq(300, 300, 50, 50)]), 0.0)
chk("half of a square", abs(TC.iou([sq(0, 0, 10, 10)], [sq(5, 0, 5, 10)]) - 0.5) < 0.02)
chk("empty side", TC.iou([], [A]), 0.0)

key = {"s1": {"polys": [poly(A, 4, "Dm"), poly(sq(200, 0, 80, 80), 5, "Dl")]},
       "s2": {"polys": [poly(sq(0, 0, 60, 60), 1, "Vd")]}}
SECS = ["s1", "s2"]


def kinds(res):
    return [(r.section, r.roi, r.kind) for r in res]


print("--- compare ---")
chk("identical: all good", {r.kind for r in TC.compare(key, key, SECS)}, {"good"})
chk("identical: one result per key ROI", len(TC.compare(key, key, SECS)), 3)
miss = {"s1": {"polys": [poly(sq(200, 0, 80, 80), 5, "Dl")]},
        "s2": key["s2"]}
r = TC.compare(miss, key, SECS)
chk("missing", [x for x in r if x.kind != "good"],
    [TC.Result("s1", 4, "Dm", "missing", 0.0)])
extra = {"s1": {"polys": key["s1"]["polys"] + [poly(sq(500, 500, 30, 30), 7, "Tel")]},
         "s2": key["s2"]}
r = TC.compare(extra, key, SECS)
chk("extra", [(x.roi, x.kind, x.region) for x in r if x.kind != "good"], [(7, "extra", "Tel")])
off = {"s1": {"polys": [poly(sq(70, 0, 100, 100), 4, "Dm"), key["s1"]["polys"][1]]},
       "s2": key["s2"]}
r = TC.compare(off, key, SECS)
low = [x for x in r if x.roi == 4][0]
chk("low overlap is off", low.kind, "off")
chk("...below the line", low.overlap < TC.GOOD)
chk("empty curation: all missing", {x.kind for x in TC.compare({}, key, SECS)}, {"missing"})
other = dict(key, s9={"polys": [poly(A, 2, "Hab")]})
chk("non-practice sections ignored", [x for x in TC.compare(other, key, SECS) if x.section == "s9"],
    [])
halves = {"s1": {"polys": [poly(sq(0, 0, 50, 100), 4, "Dm"), poly(sq(50, 0, 50, 100), 4, "Dm"),
                           key["s1"]["polys"][1]]},
          "s2": key["s2"]}
chk("an ROI in two parts matches it whole",
    [x.kind for x in TC.compare(halves, key, SECS) if x.roi == 4], ["good"])
chk("sorted by section then ROI", kinds(TC.compare(key, key, SECS)),
    [("s1", 4, "good"), ("s1", 5, "good"), ("s2", 1, "good")])
big = {u: {"frame": 768, "polys": [dict(p, v=[c * 3 for c in p["v"]]) for p in e["polys"]]}
       for u, e in key.items()}
canon = {u: dict(e, frame=256) for u, e in key.items()}
chk("outlines saved on the 768 px picture match the same outlines at 256",
    {x.kind for x in TC.compare(big, canon, SECS)}, {"good"})
chk("...and a record with no frame counts as 256",
    {x.kind for x in TC.compare(big, key, SECS)}, {"good"})
chk("a section entry with no polys key", len(TC.compare({"s1": {"plate": 3}}, key, SECS)), 3)

print("--- words ---")
chk("off names the region and says overlaps",
    all(w in TC.words(low) for w in ("Dm", "overlaps")))
chk("missing says not drawn", "not drawn" in TC.words(TC.Result("s1", 4, "Dm", "missing", 0.0)))
chk("extra says not in the expert's", "expert" in TC.words(TC.Result("s1", 7, "Tel", "extra", 0.0)))

print(f"\n{len(failures)} failure(s)")
sys.exit(1 if failures else 0)
