"""The one place this pipeline reads pixels out of a CZI.

It exists because of two findings in docs/czi-reading-audit.md.

**Finding 1, confirmed and widespread.** `scene=` was passed nowhere. 219 of
the 222 files have at least one overlapping pair of scene rectangles, and 2241
of 2572 scenes are involved in an overlap, so reading a scene's rectangle
without naming the scene composites whichever neighbours overlap it - up to
20.6% foreign pixels, and clipped fractions overstated by up to 2.6x. `scene`
is a required argument here, not a defaulted one: a default of None would
restore exactly the behaviour this module exists to end.

**Finding 8.** `np.squeeze` was assuming the plane is grayscale. A channel with
an RGB pixel type squeezes to 3-D, and every downstream shape comparison would
then be comparing the wrong thing. The result is checked, and says which scene
and channel when it is not what was expected.

Which plane a named channel IS lives in `ls_channels`; this module takes an
index and does not know what it means.
"""

import numpy as np


class ReadError(Exception):
    """A read returned something that is not a single 2-D plane."""


def read_plane(doc, rect, channel, *, scene, zoom=1.0):
    """One scene, one channel, at one zoom, as a 2-D array.

    `scene` is keyword-only and has no default, so a caller that has not
    thought about it fails at the call rather than reading the wrong pixels.
    """
    arr = doc.read(roi=(rect.x, rect.y, rect.w, rect.h),
                   plane={"C": int(channel)}, scene=int(scene), zoom=float(zoom))
    out = np.squeeze(arr)
    if out.ndim != 2:
        raise ReadError(
            f"reading channel {channel} of scene {scene} gave a "
            f"{out.ndim}-D array of shape {out.shape}, not a single plane. A "
            f"channel with an RGB pixel type does this; every shape check "
            f"downstream assumes 2-D.")
    return out


def read_planes(doc, rect, planes, *, scene, zoom=1.0):
    """{name: 2-D array} for several channels of the SAME scene.

    Named channels rather than a list, so a caller cannot mix up which array
    it got - which is the mistake `dapi, mark = read(0), read(1)` invites.
    """
    if not planes:
        raise ReadError("read_planes was asked for no channels.")
    return {name: read_plane(doc, rect, index, scene=scene, zoom=zoom)
            for name, index in planes.items()}


# ---------------------------------------------------------------- speed
#
# The scanner stores each channel as JPEG-XR tiles of 2040 x 2040 px on an
# 1836 px pitch (docs/czi-reading-audit.md, probe A), and libCZI decodes a
# whole tile for any read that touches it. A scene read in bands of 1024 rows
# therefore decoded every tile about three times - once per band it crosses,
# plus the 204 px overlap with the next tile row - and decoding is most of what
# a native-resolution read costs. libCZI keeps decoded tiles if asked
# (`cache_options`); the pixels returned are the same bytes either way, only
# the second decode is skipped. tests/test_czi_speed.py pins that the arrays
# are identical with and without the cache.

#: The scanner's tile, and its pitch, in native pixels.
TILE_PX = 2040
TILE_PITCH_PX = 1836
#: One decoded 16-bit tile.
TILE_BYTES = TILE_PX * TILE_PX * 2


def tile_cache_bytes(width_px, rows=2):
    """Bytes a cache needs to hold `rows` rows of tiles across `width_px`.

    Two rows is what a band of up to one pitch (1836 rows) can touch, so a band
    read after it finds every tile it shares with the one before still decoded.
    """
    across = -(-max(int(width_px), 1) // TILE_PITCH_PX) + 1
    return rows * across * TILE_BYTES


def open_czi(path, width_px=None, rows=2, pyczi=None):
    """pylibCZIrw's `open_czi`, keeping decoded tiles for reads of a scene up
    to `width_px` wide (no cache when it is None).

    A pylibCZIrw without `CacheOptions` - or a stand-in for one in a test -
    opens the file without a cache: slower, the same pixels. `pyczi` is the
    module to open with, for a caller that was handed one.
    """
    if pyczi is None:
        from pylibCZIrw import czi as pyczi
    opts_cls = getattr(pyczi, "CacheOptions", None)
    kind = getattr(getattr(pyczi, "CacheType", None), "Standard", None)
    if width_px is None or opts_cls is None or kind is None:
        return pyczi.open_czi(path)
    nbytes = tile_cache_bytes(width_px, rows)
    opts = opts_cls(type=kind, max_memory_usage=int(nbytes),
                    max_sub_block_count=int(nbytes // TILE_BYTES))
    try:
        return pyczi.open_czi(path, cache_options=opts)
    except TypeError:       # an open_czi that takes no cache options
        return pyczi.open_czi(path)


def scene_rects(doc):
    """{scene index: (x, y, w, h)} of an open document, header only."""
    return {int(k): (int(v.x), int(v.y), int(v.w), int(v.h))
            for k, v in doc.scenes_bounding_rectangle.items()}
