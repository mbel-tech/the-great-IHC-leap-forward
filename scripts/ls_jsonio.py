"""Writing a JSON file so that a crash cannot leave half of one behind.

Three places used to carry their own copy of "write `.tmp`, then `os.replace`":
the app settings, the study migration and the settings dialog. A fourth - the
project file - would have been a fourth copy, so there is one here.

`os.replace` is atomic on both Windows and POSIX when the temp file is beside the
target, which is why the temp file is `<path>.tmp` and not in a temp directory:
a replace across drives is a copy, and a copy is the half-written file again.

Standard library only: `ls_config` imports this, and `ls_config` is imported by
every stage.
"""

import json
import os


def write_json_atomic(path, obj, backups=0):
    """Write `obj` to `path` as indented UTF-8 JSON with a trailing newline.

    `backups` keeps that many previous versions beside the file as `<path>.bak1`
    (newest) ... `<path>.bakN`, rotated before the replace so a failed write
    leaves the old file and its backups as they were. The temp file is removed if
    the write fails.
    """
    path = os.path.abspath(path)
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(obj, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        if backups and os.path.exists(path):
            _rotate(path, backups)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return path


def _rotate(path, keep):
    """bak(N-1) -> bakN, ... , bak1 -> bak2, the live file copied to bak1."""
    import shutil
    for n in range(keep, 1, -1):
        older, newer = "%s.bak%d" % (path, n), "%s.bak%d" % (path, n - 1)
        if os.path.exists(newer):
            os.replace(newer, older)
    shutil.copy2(path, path + ".bak1")
