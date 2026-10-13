"""Bring an atlas you made elsewhere into a study.

An atlas the pipeline can use is a PLATE SET: a folder of plate images and the
tables that say where the regions are on them. The salmon atlas gets that from a
PDF (04a..04a4) and Wullimann from outlined cross sections (04w, 04x). An atlas
that did not come out of either already HAS the three files those scripts write,
so nothing needs extracting - it only needs checking and filing:

    plates.csv     plate_id, image_file, px_w, px_h            (+ midline_frac if each
                   plate draws the atlas beside a micrograph)
    polygons.csv   plate_id, roi_number, region, status, label_conf, vertices
                   (closed outlines; vertices "x y;x y;..." as FRACTIONS of the plate)
        or
    seeds.csv      plate_id, region, x_frac, y_frac [, is_unknown, colour_hex]
                   (one dot per sample of a region; 04l groups them into hulls)

`check` judges a folder against that contract and says what is wrong in plain
words; `import_atlas` then copies it into `<out_root>/atlas/plates_<name>/` and
registers it under `atlases` in the study's config, so `atlas_source: "<name>"`
works in every stage with no code change (see atlas_registry.py).

Nothing is overwritten. A name that is already registered, or a folder that
already exists, is refused: curation made against a plate set is keyed to its
images, and replacing them under an operator is exactly what the plate
fingerprints exist to catch.

    python scripts/ls_atlas_import.py SRC_DIR --name mouse --kind polygons
    python scripts/ls_atlas_import.py SRC_DIR --kind seeds --check      # judge only
"""

import argparse
import csv
import json
import math
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import atlas_registry as REG                                    # noqa: E402
import ls_atlas as AT                                           # noqa: E402

PLATE_REQUIRED = ["plate_id", "image_file", "px_w", "px_h"]
POLYGON_REQUIRED = ["plate_id", "roi_number", "region", "vertices"]
SEED_REQUIRED = ["plate_id", "region", "x_frac", "y_frac"]
STATUSES = ("auto", "reviewed", "unassigned")
SEED_HEADER = ["plate_id", "page", "plate_seq", "region", "region_raw", "is_unknown",
               "colour_hex", "from_raster", "x_px", "y_px", "x_frac", "y_frac"]

# A fraction a hair outside 0..1 is rounding in whatever tool wrote it; further
# out than this is a vertex off the plate.
FRAC_SLACK = 1e-6
SALMON_ID = re.compile(r"^plate_\d+$")


class Report:
    """What `check` found. `errors` block an import; `warnings` do not."""

    def __init__(self):
        self.errors, self.warnings, self.facts = [], [], {}

    @property
    def ok(self):
        return not self.errors

    def error(self, text):
        self.errors.append(text)

    def warn(self, text):
        self.warnings.append(text)

    def text(self):
        lines = []
        for k in ("plates", "regions", "region_names"):
            if k in self.facts:
                v = self.facts[k]
                lines.append("  %s: %s" % (k.replace("_", " "),
                                           ", ".join(v) if isinstance(v, list) else v))
        lines += ["  ERROR   " + e for e in self.errors]
        lines += ["  warning " + w for w in self.warnings]
        lines.append("  -> " + ("ready to import" if self.ok else
                                 "%d problem(s) to fix first" % len(self.errors)))
        return "\n".join(lines)


def _read_csv(path, rep, required, label):
    """(rows, ok). A BOM is tolerated here and removed when the file is copied."""
    if not os.path.isfile(path):
        rep.error("%s is missing" % label)
        return [], False
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            rd = csv.DictReader(fh)
            cols = rd.fieldnames or []
            rows = list(rd)
    except (OSError, UnicodeDecodeError) as e:
        rep.error("%s cannot be read as UTF-8 text (%s)" % (label, e))
        return [], False
    missing = [c for c in required if c not in cols]
    if missing:
        rep.error("%s has no column %s (it has: %s)"
                  % (label, ", ".join(missing), ", ".join(cols) or "none"))
        return rows, False
    return rows, True


def _frac(text):
    try:
        v = float(text)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _image_size(path):
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(path) as im:
            return im.size
    except Exception:
        return False


def check(src_dir, region_kind, micrograph_half=False):
    """Judge a folder against the plate-set contract. Returns a Report."""
    rep = Report()
    if region_kind not in REG.KINDS:
        rep.error("region kind must be one of %s" % ", ".join(REG.KINDS))
        return rep
    if not os.path.isdir(src_dir):
        rep.error("%s is not a folder" % src_dir)
        return rep

    plates, ok = _read_csv(os.path.join(src_dir, "plates.csv"), rep, PLATE_REQUIRED, "plates.csv")
    ids = {}
    if ok:
        if not plates:
            rep.error("plates.csv has no plates")
        root = os.path.realpath(src_dir)
        for n, p in enumerate(plates, 2):                      # n = the line in the file
            pid = (p["plate_id"] or "").strip()
            if not pid:
                rep.error("plates.csv line %d: plate_id is empty" % n)
                continue
            if pid in ids:
                rep.error("plates.csv line %d: plate_id %r is used twice" % (n, pid))
            ids[pid] = p
            f = (p["image_file"] or "").strip()
            full = os.path.realpath(os.path.join(src_dir, f)) if f else ""
            if (not f or os.path.isabs(f) or ".." in f.replace("\\", "/").split("/")
                    or not full.startswith(root + os.sep)):
                rep.error("plate %s: image_file %r must be a path inside the folder" % (pid, f))
                continue
            if not os.path.isfile(full):
                rep.error("plate %s: image %r is not in the folder" % (pid, f))
                continue
            try:
                w, h = int(p["px_w"]), int(p["px_h"])
                if w <= 0 or h <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                rep.error("plate %s: px_w and px_h must be whole numbers above zero" % pid)
                continue
            size = _image_size(full)
            if size is False:
                rep.error("plate %s: %r is not an image that can be opened" % (pid, f))
            elif size is not None and size != (w, h):
                rep.error("plate %s: plates.csv says %dx%d but %r is %dx%d"
                          % (pid, w, h, f, size[0], size[1]))
            if micrograph_half:
                m = _frac(p.get("midline_frac"))
                if m is None or not 0.2 <= m <= 0.8:
                    rep.error("plate %s: midline_frac must be between 0.2 and 0.8 for an atlas "
                              "with a micrograph half (where the drawing ends)" % pid)
        files = [os.path.normcase(os.path.realpath(os.path.join(src_dir, (p["image_file"] or "").strip())))
                 for p in plates if (p["image_file"] or "").strip()]
        if len(set(files)) < len(files):
            rep.warn("two plates share one image file; a plate's fingerprint is its image, so "
                     "they will be treated as the same picture")
        numbers = [AT._TRAILING_INT.search(p["plate_id"]) for p in plates]
        if plates and not (all(numbers) and len({m.group(1).lstrip("0") for m in numbers}) == len(plates)):
            rep.warn("plate ids do not each end in a different number, so they sort as text. "
                     "Name them like mouse_001, mouse_002 ... to keep atlas order")
        if any(SALMON_ID.match(i) for i in ids):
            rep.warn("some plate ids look like the salmon atlas's (plate_012). The same id points "
                     "at a different picture in each set; curation is protected by the image "
                     "fingerprint, but a prefix of your own (mouse_012) avoids the confusion")
        rep.facts["plates"] = len(ids)

    names = set()
    if region_kind == "polygons":
        polys, pok = _read_csv(os.path.join(src_dir, "polygons.csv"), rep, POLYGON_REQUIRED, "polygons.csv")
        if pok:
            reviewed_on = set()
            for n, r in enumerate(polys, 2):
                pid = (r["plate_id"] or "").strip()
                if ids and pid not in ids:
                    rep.error("polygons.csv line %d: plate %r is not in plates.csv" % (n, pid))
                    continue
                status = (r.get("status") or "auto").strip()
                if status not in STATUSES:
                    rep.error("polygons.csv line %d: status %r must be one of %s"
                              % (n, status, ", ".join(STATUSES)))
                region = (r["region"] or "").strip()
                if status != "unassigned" and not region:
                    rep.error("polygons.csv line %d: a %s polygon needs a region name" % (n, status))
                try:
                    int(r["roi_number"] or 0)
                except ValueError:
                    rep.error("polygons.csv line %d: roi_number must be a whole number" % n)
                pts = []
                for pair in (r["vertices"] or "").split(";"):
                    pair = pair.strip()
                    if not pair:
                        continue
                    xy = pair.split()
                    x, y = (_frac(xy[0]), _frac(xy[1])) if len(xy) == 2 else (None, None)
                    if x is None or y is None:
                        rep.error("polygons.csv line %d: %r is not an 'x y' pair" % (n, pair))
                        pts = None
                        break
                    if not (-FRAC_SLACK <= x <= 1 + FRAC_SLACK and -FRAC_SLACK <= y <= 1 + FRAC_SLACK):
                        rep.error("polygons.csv line %d: vertex (%s, %s) is off the plate - vertices "
                                  "are fractions of the plate image, between 0 and 1" % (n, pair.split()[0], pair.split()[1]))
                        pts = None
                        break
                    pts.append((x, y))
                if pts is not None and len(pts) < 3:
                    rep.error("polygons.csv line %d: an outline needs at least 3 vertices" % n)
                if status == "reviewed":
                    reviewed_on.add(pid)
                if region:
                    names.add(region)
            if polys and not reviewed_on:
                rep.warn("no polygon is marked 'reviewed', and the ROI curator shows reviewed polygons "
                         "only (an unreviewed outline's name becomes a measurement key)")
            elif ids:
                bare = sorted(set(ids) - reviewed_on)
                if bare:
                    rep.warn("%d plate(s) have no reviewed polygon and will carry no regions: %s%s"
                             % (len(bare), ", ".join(bare[:5]), " ..." if len(bare) > 5 else ""))
    else:
        seeds, sok = _read_csv(os.path.join(src_dir, "seeds.csv"), rep, SEED_REQUIRED, "seeds.csv")
        if sok:
            if not seeds:
                rep.error("seeds.csv has no seeds")
            for n, r in enumerate(seeds, 2):
                pid = (r["plate_id"] or "").strip()
                if ids and pid not in ids:
                    rep.error("seeds.csv line %d: plate %r is not in plates.csv" % (n, pid))
                    continue
                x, y = _frac(r["x_frac"]), _frac(r["y_frac"])
                if x is None or y is None or not (-FRAC_SLACK <= x <= 1 + FRAC_SLACK
                                                  and -FRAC_SLACK <= y <= 1 + FRAC_SLACK):
                    rep.error("seeds.csv line %d: x_frac and y_frac must be fractions of the plate "
                              "between 0 and 1" % n)
                if not (r["region"] or "").strip():
                    rep.error("seeds.csv line %d: region is empty" % n)
                else:
                    names.add(r["region"].strip())

    rep.facts["regions"] = len(names)
    if names:
        rep.facts["region_names"] = sorted(names)
    return rep


def _atomic_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def _copy_csv(src, dst):
    """The same text with any BOM removed: the readers open these as plain UTF-8."""
    with open(src, newline="", encoding="utf-8-sig") as fh:
        text = fh.read()
    with open(dst, "w", newline="", encoding="utf-8") as fh:
        fh.write(text)


def import_atlas(config_path, src_dir, name, region_kind, label=None,
                 micrograph_half=False, mirror_allowed=False, select=True, dry_run=False):
    """Check, copy and register. Returns {"name", "set_dir", "dest", "report"}.

    Raises ValueError with a plain-language reason when it cannot proceed; on any
    failure nothing is left behind in the study.
    """
    config_path = os.path.abspath(config_path)
    with open(config_path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    if not cfg.get("out_root"):
        raise ValueError("the study's config has no out_root to put the atlas under")

    set_dir = "plates_%s" % name
    entry = {"label": label or name, "set_dir": set_dir, "region_kind": region_kind,
             "micrograph_half": bool(micrograph_half), "mirror_allowed": bool(mirror_allowed)}
    atlases = dict(cfg.get("atlases") or {})
    if name in atlases:
        raise ValueError("an atlas called %r is already registered; choose another name "
                         "(an existing atlas is never replaced)" % name)
    atlases[name] = entry
    REG.entries(dict(cfg, atlases=atlases))             # name / folder / flags, strictly

    dest = os.path.join(cfg["out_root"], "atlas", set_dir)
    if os.path.exists(dest):
        raise ValueError("%s already exists; nothing was changed" % dest)

    rep = check(src_dir, region_kind, micrograph_half)
    out = {"name": name, "set_dir": set_dir, "dest": dest, "report": rep}
    if not rep.ok:
        raise ValueError("the folder does not meet the plate-set contract:\n" + rep.text())
    if dry_run:
        return out

    tmp = dest + ".importing"
    if os.path.exists(tmp):
        shutil.rmtree(tmp)
    created = False
    try:
        os.makedirs(tmp)
        _copy_csv(os.path.join(src_dir, "plates.csv"), os.path.join(tmp, "plates.csv"))
        if region_kind == "polygons":
            _copy_csv(os.path.join(src_dir, "polygons.csv"), os.path.join(tmp, "polygons.csv"))
            seeds = os.path.join(src_dir, "seeds.csv")
            if os.path.isfile(seeds):
                _copy_csv(seeds, os.path.join(tmp, "seeds.csv"))
            else:               # the curator opens seeds.csv unconditionally; header only
                with open(os.path.join(tmp, "seeds.csv"), "w", newline="", encoding="utf-8") as fh:
                    csv.writer(fh).writerow(SEED_HEADER)
        else:
            _copy_csv(os.path.join(src_dir, "seeds.csv"), os.path.join(tmp, "seeds.csv"))
        with open(os.path.join(src_dir, "plates.csv"), newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                f = row["image_file"].strip()
                target = os.path.join(tmp, f)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copy2(os.path.join(src_dir, f), target)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        os.rename(tmp, dest)
        created = True
        cfg["atlases"] = atlases
        if select:
            cfg["atlas_source"] = name
        _atomic_json(config_path, cfg)
    except BaseException:
        # Leave nothing half-registered: neither the staging folder nor a copied
        # set that the config never learned about.
        shutil.rmtree(tmp, ignore_errors=True)
        if created:
            shutil.rmtree(dest, ignore_errors=True)
        raise
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Check a plate set and register it as an atlas.")
    ap.add_argument("src_dir", help="folder with plates.csv, the plate images and polygons.csv or seeds.csv")
    ap.add_argument("--kind", required=True, choices=REG.KINDS, help="how its regions are marked")
    ap.add_argument("--name", help="short name to register it under (lower-case letters, digits, _)")
    ap.add_argument("--label", help="a readable name for menus")
    ap.add_argument("--micrograph-half", action="store_true",
                    help="each plate draws the atlas beside a micrograph (needs midline_frac)")
    ap.add_argument("--mirror", action="store_true", help="polygons cover one hemisphere and may be mirrored")
    ap.add_argument("--no-select", action="store_true", help="register it but keep the study's current atlas")
    ap.add_argument("--check", action="store_true", help="only judge the folder; change nothing")
    ap.add_argument("--config", help="the study's config file (default: the active study)")
    a = ap.parse_args(argv)

    if a.check or not a.name:
        rep = check(a.src_dir, a.kind, a.micrograph_half)
        print(rep.text())
        if not a.check:
            print("  (give --name to import it)")
        return 0 if rep.ok else 1
    import ls_config as LC
    try:
        res = import_atlas(LC.resolve_path(a.config), a.src_dir, a.name, a.kind, a.label,
                           a.micrograph_half, a.mirror, not a.no_select)
    except ValueError as e:
        print("not imported: %s" % e)
        return 1
    print(res["report"].text())
    print("imported %r into %s%s" % (res["name"], res["dest"],
                                     "" if a.no_select else " and selected it as atlas_source"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
