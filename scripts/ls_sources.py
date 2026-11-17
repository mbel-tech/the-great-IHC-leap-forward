"""Which slide files a study takes from its source folder: `source_files` matching.

One copy, for the two stages that ask: 00_manifest (which CZIs to read) and
00b_verify_extraction (which zip members to check against the extracted files).
They each had their own, written to agree, and nothing compared them - so the
day one learned a new kind of name the other would quietly have checked a
different set of slides than the study reads.

The rule. With no `source_files`, everything in the folder is the study's. With
one, a name is a loose CZI, a whole zip (every CZI in it) or a CZI inside a zip
(that member of whichever zip holds it), matched on the basename; anything else
in the folder is not this study's - including an unreadable archive nobody
named, unless a named CZI then turns out to be missing, in which case the
missing name says the archive could not be opened to look.

Standard library only, and reads no config: the caller passes the list.
"""

import os


def named(source_files):
    """The basenames a `source_files` list names, or None for the whole folder."""
    if not source_files:
        return None
    return {os.path.basename(n) for n in source_files}


def takes_loose(name, only):
    """Whether a loose CZI called `name` is this study's."""
    return not only or name in only


def takes_zip(zname, only):
    """Whether the archive `zname` is taken WHOLE (every CZI in it)."""
    return not only or zname in only


def takes_member(member, whole, only):
    """Whether zip member `member` (a path inside the archive) is this study's.

    `whole` is takes_zip() for the archive holding it.
    """
    if not member.lower().endswith(".czi"):
        return False
    return whole or os.path.basename(member) in only


def unfound(only, found, unreadable, source_dir):
    """[(name, reason)] for every named file found nowhere.

    `found` holds the names seen (loose CZIs, zip names taken whole, member
    basenames); `unreadable` the archives that could not be opened. A named CZI
    whose only zip is unreadable lands here, with the archives named.
    """
    where = (f"; {len(unreadable)} archive(s) could not be opened to look: "
             f"{', '.join(unreadable)}" if unreadable else "")
    return [(name, f"named in source_files but not found as a loose CZI, a zip, "
                   f"or a CZI inside a zip in {source_dir}{where}")
            for name in sorted((only or set()) - set(found))]
