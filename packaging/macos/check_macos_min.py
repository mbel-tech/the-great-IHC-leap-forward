"""Fail the build if any library in the app needs a newer macOS than promised.

    python check_macos_min.py "<the .app>" 14.0

Reads the minimum macOS each Mach-O file was built for (LC_BUILD_VERSION, or
LC_VERSION_MIN_MACOSX in older files) and lists every file that needs more
than the given version. A wheel tagged for a newer macOS installs fine on the
build machine and then refuses to load on the user's, with an error naming a
library they have never heard of - so it is caught here instead.
"""

import os
import struct
import sys

MH_MAGIC_64 = 0xFEEDFACF
FAT_MAGIC, FAT_MAGIC_64 = 0xCAFEBABE, 0xCAFEBABF
LC_VERSION_MIN_MACOSX, LC_BUILD_VERSION = 0x24, 0x32
CPU_TYPE_ARM64 = 0x0100000C
PLATFORM_MACOS = 1


def _version(packed):
    return (packed >> 16, (packed >> 8) & 0xFF, packed & 0xFF)


def _thin_minos(fh, base):
    fh.seek(base)
    magic, cputype, _, _, ncmds, _, _, _ = struct.unpack("<IiiIIIII", fh.read(32))
    if magic != MH_MAGIC_64:
        return None
    off = base + 32
    for _ in range(ncmds):
        fh.seek(off)
        cmd, size = struct.unpack("<II", fh.read(8))
        if cmd == LC_BUILD_VERSION:
            platform, minos = struct.unpack("<II", fh.read(8))
            if platform == PLATFORM_MACOS:
                return _version(minos)
        elif cmd == LC_VERSION_MIN_MACOSX:
            (minos,) = struct.unpack("<I", fh.read(4))
            return _version(minos)
        off += size
    return None


def minos(path):
    """(major, minor, patch) for the arm64 slice, or None when not a Mach-O."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
            if len(head) < 8:
                return None
            (be,) = struct.unpack(">I", head[:4])
            if be in (FAT_MAGIC, FAT_MAGIC_64):
                (n,) = struct.unpack(">I", head[4:8])
                for i in range(n):
                    if be == FAT_MAGIC:
                        fh.seek(8 + i * 20)
                        cputype, _, offset, _, _ = struct.unpack(">iiIII", fh.read(20))
                    else:
                        fh.seek(8 + i * 32)
                        cputype, _, offset, _, _, _ = struct.unpack(">iiQQII", fh.read(32))
                    if cputype == CPU_TYPE_ARM64:
                        return _thin_minos(fh, offset)
                return None
            (le,) = struct.unpack("<I", head[:4])
            return _thin_minos(fh, 0) if le == MH_MAGIC_64 else None
    except (OSError, struct.error):
        return None


def main(app, limit):
    want = tuple(int(p) for p in (limit.split(".") + ["0", "0"])[:3])
    too_new, highest, seen = [], (0, 0, 0), 0
    for dirpath, _, files in os.walk(app):
        for name in files:
            path = os.path.join(dirpath, name)
            if os.path.islink(path):
                continue
            v = minos(path)
            if v is None:
                continue
            seen += 1
            highest = max(highest, v)
            if v > want:
                too_new.append((v, os.path.relpath(path, app)))
    print(f"{seen} Mach-O files; the newest macOS any of them needs is "
          f"{'.'.join(map(str, highest))} (promised: {limit})")
    for v, rel in sorted(too_new, reverse=True)[:40]:
        print(f"  needs macOS {'.'.join(map(str, v))}: {rel}")
    if too_new:
        print(f"{len(too_new)} file(s) need a newer macOS than {limit}: hold the library back "
              f"in packaging/macos/constraints.txt, or raise MIN_MACOS in build.sh.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
