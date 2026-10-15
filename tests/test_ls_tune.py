"""ls_tune measures signal quality and picks StarDist's probability cut.

SYNTHETIC, AND SAY SO. The frames are disks on a noisy background, so what is
guaranteed is that each statistic recovers the numbers the frame was built
from, and that the cut is picked on the plateau of a count curve. Whether the
plateau of a real DAPI section sits where these say is for the report 05b
writes on real data.

When StarDist is installed the last block runs the real 2D_versatile_fluo
model and checks that the fast count (network once, suppression per cut)
gives exactly what predict_instances gives at each cut.

Run:  python tests/test_ls_tune.py
"""

import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import ls_tune as TU                                         # noqa: E402

failures = []


def chk(label, got, want=True):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + label.ljust(62) + " " + repr(got))
    if not ok:
        print("     want " + repr(want))
        failures.append(label)


def nuclei(shape=(400, 400), n=120, r=5, bg=200.0, fg=1200.0, sd=40.0, seed=1):
    """Disks of radius r at fg on bg, plus Gaussian noise of SD sd."""
    rng = np.random.default_rng(seed)
    a = np.full(shape, bg)
    yy, xx = np.mgrid[:shape[0], :shape[1]]
    for _ in range(n):
        cy, cx = rng.integers(r, shape[0] - r), rng.integers(r, shape[1] - r)
        a[(yy - cy) ** 2 + (xx - cx) ** 2 <= r * r] = fg
    return a + rng.normal(0, sd, shape)


print("--- quality recovers what the frame was built from ---")
q = TU.quality(nuclei())
chk("background is about 200", abs(q["background"] - 200) < 10)
chk("foreground is about 1200", abs(q["foreground"] - 1200) < 20)
chk("noise is about 40", abs(q["noise"] - 40) < 4, True)
chk("signal-to-noise is about 25", abs(q["snr"] - 25) < 3)
chk("contrast is (1200-200)/(1200+200)", abs(q["contrast"] - 1000 / 1400) < 0.02)
chk("coverage is small and positive", 0.05 < q["coverage"] < 0.4)
chk("no ceiling, no saturation", q["saturated"], 0.0)

dense = TU.quality(nuclei(n=1500, seed=2))
chk("noise does not move when the field is crowded",
    abs(dense["noise"] - 40) < 6)

sat = nuclei()
sat[:60, :60] = 4095
chk("saturation counts pixels at the ceiling",
    abs(TU.quality(sat, ceiling=4095)["saturated"] - 3600 / sat.size) < 1e-9)
weak = TU.quality(nuclei(fg=360))
chk("a weak signal (SNR 4) is still found as about 4", abs(weak["snr"] - 4) < 0.8)
chk("an empty frame gives zeros, not an error", TU.quality(np.zeros((0, 0)))["snr"], 0.0)
chk("a flat frame has no signal", TU.quality(np.full((50, 50), 7.0))["snr"], 0.0)

print()
print("--- summarise gives each section one vote ---")
rows = ([dict(TU.quality(nuclei(seed=s)), scene_uid="A_1") for s in range(8)]
        + [dict(TU.quality(nuclei(fg=400, seed=9)), scene_uid="B_1")])
sm = TU.summarise(rows)
chk("two sections", sm["sections"], 2)
chk("nine crops", sm["crops"], 9)
chk("the study median is between the two sections, not section A's",
    sm["foreground"] < 1000)
chk("their difference shows as between-section spread",
    sm["signal_cv_between_sections"] > 0.5)

print()
print("--- the verdict says what is wrong in words ---")
w, why = TU.verdict(TU.summarise([dict(q, scene_uid="A")]))
chk("a clean frame is good", w, "good")
w, why = TU.verdict(TU.summarise([dict(TU.quality(nuclei(fg=260)), scene_uid="A")]))
chk("SNR 1.5 is poor", w, "poor")
chk("...and the reason names signal-to-noise", "signal-to-noise" in why[0])
w, why = TU.verdict(TU.summarise([dict(TU.quality(nuclei(fg=360)), scene_uid="A")]))
chk("SNR 4 is marginal", w, "marginal")
w, why = TU.verdict(TU.summarise([dict(TU.quality(sat, ceiling=4095), scene_uid="A")]))
chk("saturation makes it marginal", w, "marginal")
chk("...and says so", any("saturated" in r for r in why))
chk("no pixels is unknown", TU.verdict({"snr": None})[0], "unknown")

print()
print("--- the probability cut lands on the plateau ---")
# Noise floods in below 0.45, real nuclei are lost above 0.65, flat between.
curve = [(0.30, 900), (0.35, 600), (0.40, 420), (0.45, 330), (0.479071, 322),
         (0.50, 320), (0.55, 318), (0.60, 316), (0.65, 300), (0.70, 220),
         (0.75, 150), (0.80, 90)]
p, why = TU.pick_prob_thresh(curve, 0.479071)
chk("a cut inside the flat stretch", 0.5 <= p <= 0.6)
chk("...with a reason", "changes least" in why)

flat = [(c, 500) for c in (0.3, 0.4, 0.479071, 0.5, 0.6, 0.7)]
chk("a curve flat everywhere keeps the model default",
    TU.pick_prob_thresh(flat, 0.479071)[0], 0.479071)
# An even decline everywhere, with the slope at 0.55 only 5% gentler.
nearly = [(c, round(40000 * math.exp(-0.5 * c * (0.95 if c == 0.55 else 1))))
          for c in (0.40, 0.45, 0.479071, 0.50, 0.55, 0.60)]
chk("a cut only slightly flatter does not move it",
    TU.pick_prob_thresh(nearly, 0.479071)[0], 0.479071)
chk("no objects anywhere says nothing",
    TU.pick_prob_thresh([(0.3, 0), (0.5, 0), (0.7, 0)], 0.479)[0], None)
chk("two points say nothing", TU.pick_prob_thresh([(0.3, 5), (0.5, 4)], 0.479)[0], None)
chk("the grid holds 0.30 to 0.80", (TU.PROB_GRID[0], TU.PROB_GRID[-1]), (0.3, 0.8))


print()
print("--- count_curve with a model stand-in ---")


class Stub:
    """predict_instances only: an object per pixel whose value clears the cut."""

    def __init__(self):
        self.calls = []

    def predict_instances(self, x, prob_thresh=None, **kw):
        self.calls.append(prob_thresh)
        n = int(np.count_nonzero(x.ravel() > prob_thresh))
        lab = np.zeros(x.shape, np.int32)
        lab.ravel()[:n] = np.arange(1, n + 1)
        return lab, {}


m = Stub()
x = np.linspace(0, 1, 100).reshape(10, 10)
cv = TU.count_curve(m, [x, x], (0.25, 0.5, 0.75))
chk("one point per cut, summed over images",
    cv, [(0.25, 150), (0.5, 100), (0.75, 50)])
chk("the slow route asks once per image per cut", len(m.calls), 6)


try:
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
    from stardist.models import StarDist2D
    import ls_segment as SG
    HAVE_STARDIST = True
except ImportError:
    HAVE_STARDIST = False

print()
if not HAVE_STARDIST:
    print("SKIP the real model (StarDist is not installed)")
else:
    print("--- the real 2D_versatile_fluo model ---")
    model = StarDist2D.from_pretrained("2D_versatile_fluo")
    img = nuclei(shape=(512, 512), n=150, r=6, fg=900, sd=60, seed=4)
    xin = SG.stardist_input(img)
    grid = (0.3, 0.5, 0.7)
    fast = TU.count_curve(model, [xin], grid, n_tiles_for=SG.tiles_for)
    slow = [(p, int(SG.stardist_labels(img, model, prob_thresh=p).max())) for p in grid]
    chk("the fast count equals predict_instances at every cut", fast, slow)
    chk("the model finds most of the 150 disks at its own cut",
        110 <= int(SG.stardist_labels(img, model).max()) <= 160)
    print("     counts:", fast)

print()
print("ALL PASS" if not failures else f"{len(failures)} FAILED")
sys.exit(1 if failures else 0)
