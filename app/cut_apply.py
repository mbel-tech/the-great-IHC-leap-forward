"""Apply the reagent k check's suggested multiplier, on purpose and on the record.

06a writes results/reagent_cut_check.csv (ls_reagent_report.cut_check_rows):
per marker x k, the false-positive rate on no-primary reagent controls, with
`suggested` = 1 on the smallest k that meets positivity.fp_target. Nothing
applies it by itself. `apply_suggested_k` is the one way the app does: it sets
positivity.cut_k[marker] to that k and records where it came from in
positivity.cut_k_source[marker] = {"by": "controls", "check": <sha256 of the
csv>, "at": <time>}, the decision itself attributed to "cut check".

**What was shown is what is applied.** The hourly 06e loop can rewrite the
check between the page showing it and the click. The caller passes the k it
showed and the checksum of the check it showed it from (cut_check_view's
"ks" and "sha"); apply refuses when either differs from the file now, so a k
the operator never saw is never applied.

**The blinding guard.** A multiplier that is applied must be fixed before
unblinding. Once 06b has written its join (any of its outputs is under
out_root), apply refuses unless the operator types CONFIRM_TEXT, so a cut
moved after the groups are known is a deliberate, typed act.

No Qt here: the Results page (screens.StageListScreen) asks, and this decides.
"""

import csv
import hashlib
import io
import os

import ls_project as LP
import ls_reagent_report as RR
import stages as S

#: What the operator types to apply a multiplier after 06b has run.
CONFIRM_TEXT = "apply after unblinding"
#: The decision record's `by` (ls_project.Project.set).
BY = "cut check"
#: positivity.cut_k_source[marker]["by"] for a k taken from the check.
SOURCE = "controls"
#: The refusal when the check is not the one the page showed.
CHANGED = "the cut check changed since it was shown - reopen the page"


def check_path(out_root):
    return os.path.join(out_root or "", "results", RR.CUT_CHECK_CSV)


def unblinded(out_root):
    """Whether 06b (join_sampling), the unblinding join, has written anything."""
    if not out_root:
        return False
    return any(os.path.exists(os.path.join(out_root, *p.split("/")))
               for p in S.BY_ID["join_sampling"].outputs)


def read_check(out_root):
    """(rows, sha256 hex of the file's bytes), or (None, None) without a file.

    The rows are parsed from the same bytes the sha is taken of, so the record
    names exactly the table the k came from."""
    try:
        with open(check_path(out_root), "rb") as fh:
            data = fh.read()
    except OSError:
        return None, None
    try:
        rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline="")))
    except (UnicodeDecodeError, csv.Error):
        return None, None
    return rows, hashlib.sha256(data).hexdigest()


def _flag(v):
    return str(v if v is not None else "").strip() in ("1", "1.0", "True", "true")


def suggestion(rows, marker):
    """(k, None) for the marker's one suggested row, else (None, why)."""
    mine = [r for r in rows or () if (r.get("marker") or "") == marker]
    if not mine:
        return None, f"the cut check has no rows for {marker}"
    hit = [r for r in mine if _flag(r.get("suggested"))]
    if len(hit) != 1:
        notes = [n for n in dict.fromkeys((r.get("note") or "").strip() for r in mine) if n]
        return None, (f"no suggested k for {marker}" + (": " + "; ".join(notes) if notes else ""))
    try:
        return float(hit[0]["k"]), None
    except (TypeError, ValueError, KeyError):
        return None, f"the suggested row for {marker} has no usable k"


def k_in_force(cfg, marker):
    """The k marker's cut is formed at (ls_overlay.cut_k, without numpy)."""
    table = ((cfg or {}).get("positivity") or {}).get("cut_k")
    table = table if isinstance(table, dict) else {}
    try:
        return float(table.get(marker, table.get("*", RR.LO.CUT_K)))
    except (TypeError, ValueError):
        return float(RR.LO.CUT_K)


def summary(out_root, cfg):
    """[(marker, suggested k or None, k in force, why not)] for the Results page,
    in the check's marker order; [] without a check file."""
    rows, _sha = read_check(out_root)
    return summary_of(rows, cfg)


def summary_of(rows, cfg):
    """summary() of check rows already read (read_check), so a caller can keep
    the checksum of exactly the rows it shows."""
    if not rows:
        return []
    out = []
    for mk in dict.fromkeys(r.get("marker") or "" for r in rows):
        if not mk:
            continue
        k, why = suggestion(rows, mk)
        out.append((mk, k, k_in_force(cfg, mk), why))
    return out


def apply_suggested_k(project, out_root, marker, confirm_text=None, *, shown_k, shown_sha):
    """Set positivity.cut_k[marker] to the check's suggested k. -> (applied, message).

    `shown_k` and `shown_sha` are the suggested k the page showed for the
    marker and the checksum of the check it showed (cut_check_view's "ks" and
    "sha"): a file that is not that one now is refused (CHANGED).

    Every refusal comes before anything is written, so a refused apply leaves
    the config and its decision log as they were."""
    if getattr(project, "read_only", False):
        return False, "this project is open read-only; the multiplier was not changed"
    rows, sha = read_check(out_root)
    if rows is None:
        return False, (f"no {RR.CUT_CHECK_CSV} under {out_root or '(no output folder)'}: "
                       "run the dataset step (06a) with a no-primary reagent control measured")
    if sha != shown_sha:
        return False, CHANGED
    k, why = suggestion(rows, marker)
    if k is None:
        return False, why
    if shown_k is None or k != float(shown_k):
        return False, CHANGED
    if unblinded(out_root) and (confirm_text or "").strip() != CONFIRM_TEXT:
        return False, ("this study is already unblinded (06b has joined the groups). "
                       "A multiplier should be fixed before unblinding; to apply it "
                       f"anyway, type \"{CONFIRM_TEXT}\"")
    table = project.get("positivity.cut_k")
    table = dict(table) if isinstance(table, dict) else {"*": float(RR.LO.CUT_K)}
    table[marker] = k
    source = project.get("positivity.cut_k_source")
    # The default record is flat ({"by": "default"}); per marker, only the
    # markers' own records are kept.
    source = {m: dict(v) for m, v in (source or {}).items()
              if isinstance(v, dict)} if isinstance(source, dict) else {}
    source[marker] = {"by": SOURCE, "check": sha, "at": LP._now()}
    try:
        project.set("positivity.cut_k", table, by=BY)
        project.set("positivity.cut_k_source", source, by=BY)
    except LP.ProjectError as e:
        return False, str(e)
    return True, (f"positivity cut multiplier for {marker} set to {k:g} from the reagent "
                  f"k check (provisional). The dataset and the results are now out of "
                  f"date: run them again.")
