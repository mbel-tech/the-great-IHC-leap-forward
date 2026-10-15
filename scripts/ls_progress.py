"""Tell the desktop app how far a stage has got, so it can draw a progress bar.

    import ls_progress as PG
    for i, uid in enumerate(uids):
        PG.report(i, len(uids), "sections")
        ...
    PG.report(len(uids), len(uids), "sections")

Silent on the command line: the app sets LS_PROGRESS=1 before it runs a stage,
and only then does `report` print anything. What it prints is one line,
`@@progress 12/454 sections`, which the app turns into the bar and keeps out of
the log, so a stage can report on every item without filling the log pane.

Many stages already print their own `\\r  12/454` counters; the app reads those
too. This is for the long ones that print nothing until they finish.
"""

import os

MARK = "@@progress "


def report(done, total, what=""):
    """Say `done` of `total` items are finished. Cheap; call it every item."""
    if os.environ.get("LS_PROGRESS") != "1" or not total:
        return
    print(f"{MARK}{int(done)}/{int(total)} {what}".rstrip(), flush=True)
