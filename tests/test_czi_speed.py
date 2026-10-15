"""The speed-ups in reading CZIs change no pixel.

Three things were added to make the native-resolution stages faster, and each
could quietly change what they draw or measure if it were wrong:

  * libCZI's cache of decoded tiles (`czi_read.open_czi`): a band read through
    it must be the same array as one read without it;
  * reading two channels at once on two threads: each must be the same array
    a one-at-a-time read gives;
  * 01l's memory gate, which lets a second scene start only while RAM alone
    has room: it must never refuse the only scene running for want of RAM
    the page file would cover, and must let a second one wait, not fail.

The first two need the real pylibCZIrw and write a small CZI of overlapping
2040 px tiles on the scanner's 1836 px pitch; without the library they are
skipped and said so. The JPEG-XR files themselves cannot be written here
(libCZI does not encode JPEG-XR), so the file is zstd - the cache sits above
the decoder and does not know which codec filled it.

Run:  python tests/test_czi_speed.py
"""

import os
import shutil
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "scripts"))

import czi_read as CR                                       # noqa: E402

fails = 0


def chk(label, ok, got=""):
    global fails
    if not ok:
        fails += 1
    print(("ok   " if ok else "FAIL ") + label.ljust(64) + " " + str(got))


class _Rect:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h


def banded(doc, rect, channel, scene, band=1024, b=4):
    """01l's read: bands of native rows, each block-averaged b x b."""
    rx, ry, rw, rh = rect
    w, h = rw // b * b, rh // b * b
    out = np.empty((h // b, w // b), np.float32)
    for r0 in range(0, h, band):
        n = min(band, h - r0)
        raw = CR.read_plane(doc, _Rect(rx, ry + r0, w, n), channel, scene=scene)
        out[r0 // b:(r0 + n) // b] = raw.reshape(n // b, b, w // b, b).mean(
            axis=(1, 3), dtype=np.float32)
    return out


print("--- the cache size ---")
chk("two rows of tiles across a scene", CR.tile_cache_bytes(1836 * 4)
    == 2 * 5 * CR.TILE_BYTES)
chk("...grows with the width", CR.tile_cache_bytes(20000) > CR.tile_cache_bytes(5000))

try:
    from pylibCZIrw import czi as pyczi
except ImportError:
    pyczi = None
    print("SKIP pylibCZIrw is not installed - the pixel comparisons need it")

if pyczi is not None:
    tmp = tempfile.mkdtemp(prefix="czi_speed_")
    path = os.path.join(tmp, "tiles.czi")
    rng = np.random.default_rng(7)
    T, P = CR.TILE_PX, CR.TILE_PITCH_PX
    with pyczi.create_czi(path, compression_options="zstd1:ExplicitLevel=1") as w:
        for c in (0, 1):
            for j in range(3):
                for i in range(3):
                    # Each tile its own values, so the 204 px overlaps show
                    # which tile won and a cache that mixed them up would too.
                    tile = rng.integers(100 + 1000 * c, 3000 + 1000 * c, (T, T),
                                        dtype=np.uint16)
                    w.write(tile, location=(i * P, j * P), plane={"C": c}, scene=0)
    try:
        with pyczi.open_czi(path) as doc:
            rect = CR.scene_rects(doc)[0]
            plain = [banded(doc, rect, c, 0) for c in (0, 1)]

        print()
        print("--- the tile cache ---")
        with CR.open_czi(path, rect[2]) as doc:
            cached = [banded(doc, rect, c, 0) for c in (0, 1)]
            again = banded(doc, rect, 0, 0)
        chk("banded read through the cache is the same array",
            all(np.array_equal(a, b) for a, b in zip(plain, cached)))
        chk("...and a second read, from the cache, too", np.array_equal(again, plain[0]))
        with CR.open_czi(path, rect[2], rows=1) as doc:
            small = banded(doc, rect, 1, 0, band=8)
        chk("8-row bands through a one-row cache equal 1024 without",
            np.array_equal(small, plain[1]))
        with CR.open_czi(path) as doc:
            chk("no width: no cache, same read", np.array_equal(banded(doc, rect, 1, 0),
                                                               plain[1]))

        print()
        print("--- two channels at once ---")
        with CR.open_czi(path, rect[2]) as dn, CR.open_czi(path, rect[2]) as dm:
            with ThreadPoolExecutor(2) as ex:
                got = list(ex.map(lambda a: banded(a[0], rect, a[1], 0),
                                  ((dn, 0), (dm, 1))))
        chk("each channel read on its own thread is the same array",
            all(np.array_equal(a, b) for a, b in zip(plain, got)))
    finally:
        shutil.rmtree(tmp, True)

print()
print("--- 01l's memory gate ---")
sys.path.insert(0, HERE)
from _fixture import use_temp_study, load_stage           # noqa: E402
use_temp_study()
L = load_stage("01l_scene_renders.py")
GB = 10 ** 9
L.available_memory = lambda: 2 * GB          # RAM + page file
L.available_ram = lambda: int(0.1 * GB)      # RAM alone: nearly none
gate = L.MemoryGate()
chk("the only scene is held to RAM + page file, as before",
    gate.acquire(1 * GB) is None)
started = threading.Event()
result = []


def second():
    started.set()
    result.append(gate.acquire(1 * GB))


th = threading.Thread(target=second)
th.start()
started.wait()
time.sleep(0.3)
chk("a second scene waits while RAM alone has no room", not result)
gate.release(1 * GB)
th.join(timeout=10)
chk("...and starts once the first is done", result == [None], result)
gate.release(1 * GB)
L.available_memory = lambda: int(0.5 * GB)
chk("a scene too big for RAM + page file is still refused",
    gate.acquire(1 * GB) == int(0.5 * GB))
L.available_ram = lambda: 8 * GB
L.available_memory = lambda: 10 * GB
chk("with RAM to spare, two run at once",
    gate.acquire(1 * GB) is None and gate.acquire(1 * GB) is None, gate.inflight)

print()
print("ALL PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
