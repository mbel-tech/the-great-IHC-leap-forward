"""Make the tutorial's demo pack from a few whole sections of a real slide.

Three steps, each its own command, because the middle one is a person running
the tutorial study once in the app:

    python scripts/make_demo_pack.py cut --czi SLIDE.czi --scenes 3,4,6 \\
        --study STUDY.json --practice LS22_s01a_sc03,LS22_s01a_sc04 --out PACK

        Writes PACK/slides/<same name>.czi holding only those scenes, at native
        pixels for every channel, losslessly compressed (zstd), with the
        slide's own metadata (channels, bit depth, pixel size) kept and its
        scene list cut to match. Scenes are renumbered from 0, so the section
        ids change; pack.json records old -> new. Also writes the study
        settings (paths blanked) and a sampling workbook with made-up groups.

    (open PACK as the tutorial study in the app and run Prepare through the
     ROI curator page, plus the slow stages; its output folder is TRIAL)

    python scripts/make_demo_pack.py check --pack PACK --source-out SRC_OUT --trial-out TRIAL

        Compares each practice section's reformatted greyscale picture in the
        trial run with the source study's: the tissue must overlap by 0.9 (IoU).
        Only when every one matches does it
        write the answer key (the source study's curation of those sections,
        under their new ids): otherwise the expert's outlines would sit in the
        wrong place on the learner's sections. Exit 2 and no answer key when
        they differ.

        With --regions and --plates, the answer key comes from a curator export
        (roi_regions_*.csv and the roi_plates_*.csv beside it) instead of the
        saved curation: the export is where drawn outlines survive. Its
        outlines are kept in the export's 256 px frame.

    python scripts/make_demo_pack.py calibrate --pack PACK --source-out SRC_OUT \
        --overrides SRC_OUT/reformatted/perk_overrides.csv --channel AF568

        Carries the source study's display ranges and tile-correction fields,
        and the hand rotations of the cut sections, into PACK/output/. Three
        sections are too few to sample brightness from, and without the
        rotations the sections reformat in another frame than the expert drew
        on. The tutorial study starts with these files in its output folder.

    python scripts/make_demo_pack.py align --pack PACK --source-out SRC_OUT --trial-out TRIAL

        After a trial run, sets each cut section's rotation so its final angle
        is the source's. The automatic angle can pick the other end of a
        section's long axis on the demo study (a nearly symmetric section
        turns 180 degrees), and the hand rotation alone cannot know that.
        Writes the pack's and the trial's rotation file; re-run the trial's
        reformat stages, then check.

    python scripts/make_demo_pack.py ready --pack PACK --trial-out TRIAL

        Copies the slow stages' results from the trial run into PACK/ready/.

    python scripts/make_demo_pack.py one-marker --pack PACK --channel AF568 --nuclear DAPI

        Makes the pack's study a one-scan, one-marker study: for a demo cut from
        one pass of a paired study, whose other marker is on another slide.
        The channel table follows the cut slide's own channel order.
"""

import argparse
import json
import os
import re
import shutil
import struct
import sys
import xml.etree.ElementTree as ET
from collections import namedtuple

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [HERE, os.path.join(REPO, "app")]

import czi_meta                                                 # noqa: E402

Rect = namedtuple("Rect", "x y w h")
SLOW = ("scene_renders", "rgb_perk", "rgb_pcna", "detect")
ANSWER_KEY = "ls_roi_curator_v1"


# ---- the slide ---------------------------------------------------------------------

def read_xml(path):
    with open(path, "rb") as fh:
        return czi_meta.read_metadata(fh)


def cut_scene_list(root, keep):
    """The metadata with only the scenes in `keep` (old indices), renumbered from 0."""
    root = ET.fromstring(ET.tostring(root))
    for scenes in root.iter("Scenes"):
        for sc in list(scenes):
            if sc.tag == "Scene" and int(sc.get("Index", -1)) not in keep:
                scenes.remove(sc)
        for sc in scenes:
            if sc.tag == "Scene":
                sc.set("Index", str(keep.index(int(sc.get("Index")))))
    for size in root.iter("SizeS"):
        size.text = str(len(keep))
    return root


def append_metadata(path, xml_text):
    """Point the CZI at a new metadata segment, appended at the end of the file.

    The old segment stays where it was, unreferenced; readers take the
    metadata position from the file header.
    """
    body = xml_text.encode("utf-8")
    data = struct.pack("<ii", len(body), 0).ljust(czi_meta.METADATA_DATA_LEN, b"\0") + body
    used = len(data)
    alloc = (used + 31) // 32 * 32
    seg = b"ZISRAWMETADATA".ljust(16, b"\0") + struct.pack("<qq", alloc, used)
    with open(path, "r+b") as fh:
        fh.seek(0, os.SEEK_END)
        pos = fh.tell()
        pos_aligned = (pos + 31) // 32 * 32
        fh.write(b"\0" * (pos_aligned - pos))
        fh.write(seg + data + b"\0" * (alloc - used))
        fh.seek(92)                     # FileHeader: metadata position, after the directory's
        fh.write(struct.pack("<Q", pos_aligned))


def crop_czi(src, dst, scenes, band=2048):
    """Copy whole scenes from `src` into a new CZI at native pixels; returns {old: new}."""
    from pylibCZIrw import czi as pyczi
    import czi_read as CR
    with pyczi.open_czi(src) as doc:
        rects = CR.scene_rects(doc)
        missing = [s for s in scenes if s not in rects]
        if missing:
            raise SystemExit(f"{src} has no scene {missing[0]} (it has {sorted(rects)}).")
        n_ch = doc.total_bounding_box["C"][1] - doc.total_bounding_box["C"][0]
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.exists(dst):
            os.remove(dst)
        with pyczi.create_czi(dst, compression_options="zstd1:ExplicitLevel=1") as w:
            for new, old in enumerate(scenes):
                x, y, width, height = rects[old]
                for c in range(n_ch):
                    for r0 in range(0, height, band):
                        n = min(band, height - r0)
                        arr = CR.read_plane(doc, Rect(x, y + r0, width, n), c, scene=old)
                        w.write(arr, location=(x, y + r0), plane={"C": c}, scene=new)
    root = cut_scene_list(read_xml(src), list(scenes))
    append_metadata(dst, ET.tostring(root, encoding="unicode"))
    return {old: new for new, old in enumerate(scenes)}


def new_uid(uid, scene_map):
    m = re.fullmatch(r"(.*_sc)(\d+)", uid)
    if not m or int(m.group(2)) not in scene_map:
        raise SystemExit(f"{uid} is not one of the scenes being cut.")
    return f"{m.group(1)}{scene_map[int(m.group(2))]:0{len(m.group(2))}d}"


# ---- the commands --------------------------------------------------------------------

def cmd_cut(a):
    scenes = [int(s) for s in a.scenes.split(",") if s.strip()]
    slides = os.path.join(a.out, "slides")
    scene_map = crop_czi(a.czi, os.path.join(slides, os.path.basename(a.czi)), scenes)
    practice = [p for p in a.practice.split(",") if p.strip()]
    renamed = {p: new_uid(p, scene_map) for p in practice}
    with open(a.study, encoding="utf-8") as fh:
        cfg = json.load(fh)
    import tutorial_pack as TP
    TP.clear_paths(cfg)
    for key in TP.KEEP_PATHS:
        cfg[key] = ""
    for key in ("curation", "project", "groups"):
        cfg.pop(key, None)
    cfg.setdefault("study", {})["name"] = "Tutorial study"
    os.makedirs(os.path.join(a.out, "study"), exist_ok=True)
    with open(os.path.join(a.out, "study", "study.json"), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    write_workbook(os.path.join(a.out, "study", "sampling.xlsx"), os.path.basename(a.czi),
                   [g.strip() for g in a.workbook_groups.split(",") if g.strip()])
    with open(os.path.join(a.out, "pack.json"), "w", encoding="utf-8") as fh:
        json.dump({"version": 1, "slide": os.path.basename(a.czi),
                   "source_scenes": scenes, "practice_sections": list(renamed.values()),
                   "practice_from": renamed, "credits": a.credits}, fh, indent=2)
    print(f"cut {len(scenes)} scenes into {slides}; practice sections {list(renamed.values())}")
    return 0


def write_workbook(path, slide_name, groups):
    """A sampling workbook whose one fish gets an invented group (never the real one)."""
    import openpyxl
    m = re.match(r"[A-Za-z]+(\d+)", slide_name)
    wb = openpyxl.Workbook()
    wb.active.append(["Fish ID", "treatment"])
    wb.active.append([int(m.group(1)) if m else 1, (groups or ["Group A"])[0]])
    wb.save(path)


MATCH = 0.9      # overlap (IoU) a practice section's two tissue masks must reach
TISSUE = 8       # grey level above which a frame pixel is tissue (the ground is black)


def _section_picture(out_root, uid):
    """The section's 256 px greyscale frame: (folder, path), or (None, None).

    Paired studies keep each marker's frames in sections_<marker>/, multiplex
    ones in sections/; the colour composites and thumbnails are not frames.
    """
    base = os.path.join(out_root, "reformatted")
    if not os.path.isdir(base):
        return None, None
    for name in sorted(os.listdir(base)):
        if name.startswith("sections") and not name.endswith(("_rgb", "_thumb")):
            path = os.path.join(base, name, uid + ".png")
            if os.path.isfile(path):
                return name, path
    return None, None


def frames_match(source_out, trial_out, old_uid, new):
    """(ok, why): the trial's reformatted frame of `new` is the source's of `old_uid`."""
    import numpy as np
    from PIL import Image
    _, src = _section_picture(source_out, old_uid)
    _, trial = _section_picture(trial_out, new)
    if not src:
        return False, f"the source study has no reformatted picture of {old_uid}"
    if not trial:
        return False, f"the trial run has no reformatted picture of {new}; run Prepare first"
    a = np.asarray(Image.open(src).convert("L"), float)
    b = np.asarray(Image.open(trial).convert("L"), float)
    if a.shape != b.shape:
        return False, f"{new} is {b.shape[1]}x{b.shape[0]} px, the source's {a.shape[1]}x{a.shape[0]}"
    # Where the outlines land is a question of geometry, not brightness: the
    # demo study renders from a cut slide and may grade a little differently,
    # but its tissue must sit where the source's did.
    ta, tb = a > TISSUE, b > TISSUE
    iou = float((ta & tb).sum()) / max(1, int((ta | tb).sum()))
    if iou < MATCH:
        return False, (f"{new}'s tissue overlaps the source's {old_uid} by {iou:.2f} "
                       f"(needs {MATCH}); see `align`")
    return True, ""


def cmd_check(a):
    with open(os.path.join(a.pack, "pack.json"), encoding="utf-8") as fh:
        meta = json.load(fh)
    renamed = meta.get("practice_from") or {}
    for old, new in renamed.items():
        ok, why = frames_match(a.source_out, a.trial_out, old, new)
        if not ok:
            print(f"section {old} does not reformat to the same frame ({why}); "
                  "the answer key would be misplaced, so none was written.")
            return 2
    if a.regions or a.plates:
        if not (a.regions and a.plates):
            print("--regions and --plates go together (the two files of one export).")
            return 2
        cur = curation_from_export(a.regions, a.plates)
        source = os.path.basename(a.regions)
    else:
        cur_path = a.source_curation or os.path.join(a.source_out, "curation",
                                                     ANSWER_KEY + ".json")
        with open(cur_path, encoding="utf-8") as fh:
            cur = json.load(fh)
        source = os.path.basename(cur_path)
    key = {new: cur[old] for old, new in renamed.items() if old in cur}
    missing = [old for old in renamed if old not in cur]
    if missing:
        print(f"the source has no entry for {missing[0]}; no answer key written.")
        return 2
    os.makedirs(os.path.join(a.pack, "answer"), exist_ok=True)
    tmp = os.path.join(a.pack, "answer", ANSWER_KEY + ".json.part")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(key, fh)
    os.replace(tmp, tmp[:-5])
    meta["answer_from"] = source
    _write_json(os.path.join(a.pack, "pack.json"), meta)
    print(f"frames match; answer key written for {len(key)} sections from {source}")
    return 0


def _write_json(path, data):
    with open(path + ".part", "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(path + ".part", path)


def curation_from_export(regions, plates):
    """Curator state for the sections an export names, in the export's 256 px frame.

    Only what the tutorial reads: the matched plate and the drawn outlines
    (`polys`, as 04l keeps them and 04q_import_curation rebuilds them). With
    the export's own scale the outlines stay as exported, and `frame` says so,
    so the tutorial can bring a learner's outlines (drawn on the 768 px
    composite) to the same size. Not 04q itself: it reads the study's
    settings when it is imported, and this runs outside any study.
    """
    import csv

    def rows(path):
        with open(path, encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))

    def cell(r, k):
        return (r.get(k) or "").strip()

    state = {}
    for r in rows(plates):
        uid = cell(r, "scene_uid")
        if uid:
            idx = cell(r, "plate_index")
            state[uid] = {"plate": int(float(idx)) if idx else 0,
                          "plate_id": cell(r, "plate_id"), "pairs": [], "polys": [],
                          "frame": 256}
    for r in rows(regions):
        uid = cell(r, "scene_uid")
        if cell(r, "roi_shape") != "polygon" or uid not in state:
            continue
        v = []
        for vtx in cell(r, "sec_poly").split(";"):
            xy = vtx.split()
            if len(xy) == 2:
                v += [_num(xy[0]), _num(xy[1])]
        if len(v) < 6:
            continue
        pt, rn = cell(r, "part"), cell(r, "roi_n")
        state[uid]["polys"].append({"v": v, "region": cell(r, "region"),
                                    "part": int(float(pt)) if pt else 1,
                                    "roi": int(float(rn)) if rn else 0})
    return state


def _num(text):
    x = float(text)
    return int(x) if x.is_integer() else x


def cmd_one_marker(a):
    slides = os.path.join(a.pack, "slides")
    czis = sorted(f for f in os.listdir(slides) if f.lower().endswith(".czi"))
    if not czis:
        print(f"no slide in {slides}")
        return 2
    names = [c["name"] for c in czi_meta.summarise(read_xml(os.path.join(slides, czis[0])))
             ["channels"]]
    channels = []
    for i, name in enumerate(names):
        role = ("nuclear" if name == a.nuclear else "marker" if name == a.channel else "ignore")
        channels.append({"name": name, "role": role, "czi_name": name, "index": i})
    if a.channel not in names or a.nuclear not in names:
        print(f"the slide's channels are {names}; it has no {a.channel if a.channel not in names else a.nuclear}.")
        return 2
    path = os.path.join(a.pack, "study", "study.json")
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    acq = cfg.setdefault("acquisition", {})
    acq.update(layout="multiplex", channels=channels, markers=[])
    acq.pop("marker_segmentation", None)
    _write_json(path, cfg)
    print(f"study is one scan per section: {[(c['name'], c['role']) for c in channels]}")
    return 0


def cmd_calibrate(a):
    import csv
    import ls_paths as LP
    with open(os.path.join(a.pack, "pack.json"), encoding="utf-8") as fh:
        meta = json.load(fh)
    with open(os.path.join(a.pack, "study", "study.json"), encoding="utf-8") as fh:
        cfg = json.load(fh)
    scene_map = {old: new for new, old in enumerate(meta["source_scenes"])}
    out = os.path.join(a.pack, "output")
    carried = []
    for rel in [os.path.join("qc", "display_ranges.json")] + [
            os.path.join("qc", "flatfield", f) for f in
            sorted(os.listdir(os.path.join(a.source_out, "qc", "flatfield"))
                   if os.path.isdir(os.path.join(a.source_out, "qc", "flatfield")) else [])
            if re.fullmatch(r"tilefield_c\d+\.npy", f)]:
        src = os.path.join(a.source_out, rel)
        if not os.path.isfile(src):
            print(f"the source study has no {rel}; nothing carried.")
            return 2
        os.makedirs(os.path.dirname(os.path.join(out, rel)), exist_ok=True)
        shutil.copyfile(src, os.path.join(out, rel))
        carried.append(rel.replace(os.sep, "/"))
    if not any("tilefield" in c for c in carried):
        print("the source study has no tile-correction fields (qc/flatfield); carrying ranges only.")

    # The cut scenes' hand rotations, under their new ids. Every cut scene gets
    # a row: an override file that exists is the complete record, so the demo
    # study's own exclusion proposals cannot drop a section the tutorial needs.
    prefix = re.fullmatch(r"(.*_sc)\d+", next(iter(meta["practice_from"]))).group(1)
    width = len(next(iter(meta["practice_from"]))) - len(prefix)
    with open(a.overrides, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    uid_col = next((c for c in ("perk_scene_uid", "scene_uid") if rows and c in rows[0]), None)
    if uid_col is None:
        print(f"{a.overrides} has no scene_uid column; is it a rotation override file?")
        return 2
    found = {r[uid_col]: r for r in rows}
    overrides = []
    for old_n, new_n in sorted(scene_map.items(), key=lambda kv: kv[1]):
        src = found.get(f"{prefix}{old_n:0{width}d}") or {}
        overrides.append({"scene_uid": f"{prefix}{new_n:0{width}d}",
                          "extra_rotation": src.get("extra_rotation") or "0",
                          "flip": src.get("flip") or "0", "excluded": "0",
                          "decision": "", "reason": ""})
    rel = os.path.normpath(LP.for_config(dict(cfg, out_root="")).path("overrides", a.channel))
    target = os.path.join(out, rel)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(overrides[0]))
        w.writeheader()
        w.writerows(overrides)
    carried.append(rel.replace(os.sep, "/"))
    meta["output_from_source"] = carried
    _write_json(os.path.join(a.pack, "pack.json"), meta)
    turned = [f"{o['scene_uid']} {float(o['extra_rotation']):g} deg" for o in overrides
              if float(o["extra_rotation"] or 0) or o["flip"] == "1"]
    print(f"carried {len(carried)} file(s) into {out}; rotations: {', '.join(turned) or 'none'}")
    return 0


def _index_angles(out_root):
    """{section id: final angle} from every reformat index under reformatted/."""
    import csv
    base, found = os.path.join(out_root, "reformatted"), {}
    for name in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        if re.fullmatch(r"reformat_index(_.+)?\.csv", name):
            with open(os.path.join(base, name), newline="", encoding="utf-8") as fh:
                for r in csv.DictReader(fh):
                    if r.get("kind") == "section" and r.get("angle"):
                        found.setdefault(r["id"], float(r["angle"]))
    return found


def cmd_align(a):
    import csv
    with open(os.path.join(a.pack, "pack.json"), encoding="utf-8") as fh:
        meta = json.load(fh)
    rel = next((r for r in meta.get("output_from_source", []) if "overrides" in r), None)
    if not rel:
        print("the pack carries no rotation file; run calibrate first.")
        return 2
    src_angle, trial_angle = _index_angles(a.source_out), _index_angles(a.trial_out)
    old_of = {new: old for old, new in _cut_uids(meta).items()}
    pack_file = os.path.join(a.pack, "output", *rel.split("/"))
    with open(pack_file, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    turned = []
    for r in rows:
        new, old = r["scene_uid"], old_of.get(r["scene_uid"])
        if old not in src_angle or new not in trial_angle:
            print(f"no reformat angle for {old if old not in src_angle else new}; "
                  "run the trial's Prepare first.")
            return 2
        delta = (src_angle[old] - trial_angle[new] + 180.0) % 360.0 - 180.0
        if abs(delta) >= 0.5:
            r["extra_rotation"] = f"{(float(r['extra_rotation'] or 0) + delta) % 360.0:g}"
            turned.append(f"{new} {delta:+.1f} deg")
    for path in (pack_file, os.path.join(a.trial_out, *rel.split("/"))):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    if turned:
        print(f"turned {', '.join(turned)}; re-run the trial's reformat stages, then check.")
    else:
        print("every section already has the source's angle.")
    return 0


def _cut_uids(meta):
    """{source uid: demo uid} for every cut scene."""
    first = next(iter(meta["practice_from"]))
    prefix = re.fullmatch(r"(.*_sc)\d+", first).group(1)
    width = len(first) - len(prefix)
    return {f"{prefix}{old:0{width}d}": f"{prefix}{new:0{width}d}"
            for new, old in enumerate(meta["source_scenes"])}


def cmd_ready(a):
    import ls_paths as LP
    import stages as S
    import workflow as W
    with open(os.path.join(a.trial_out, "study.json"), encoding="utf-8") as fh:
        cfg = json.load(fh)
    names = LP.for_config(cfg)
    copied = 0
    for sid in SLOW:
        st = S.BY_ID[sid]
        paths = [p for e in st.outputs for p in st._alternatives(e, a.trial_out, names)[1]
                 if os.path.exists(p)]
        folder = W.output_folder(sid, cfg)
        if folder and os.path.isdir(folder[1]):
            paths.append(folder[1])
        dest = os.path.join(a.pack, "ready", sid)
        if os.path.isdir(dest):
            shutil.rmtree(dest)
        for p in paths:
            rel = os.path.relpath(p, a.trial_out)
            if rel.startswith(".."):
                continue
            target = os.path.join(dest, rel)
            if os.path.isdir(p):
                if not any(fs for _d, _s, fs in os.walk(p)):
                    continue        # an empty folder is not a result
                shutil.copytree(p, target, dirs_exist_ok=True)
            else:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copyfile(p, target)
            copied += 1
        print(f"{sid}: {len(paths)} output(s)")
    return 0 if copied else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("cut")
    c.add_argument("--czi", required=True)
    c.add_argument("--scenes", required=True, help="scene indices to keep, e.g. 3,4,6")
    c.add_argument("--study", required=True, help="the source study's study.json")
    c.add_argument("--practice", required=True, help="section ids to practise on")
    c.add_argument("--out", required=True)
    c.add_argument("--workbook-groups", default="Group A,Group B")
    c.add_argument("--credits", default="")
    k = sub.add_parser("check")
    k.add_argument("--pack", required=True)
    k.add_argument("--source-out", required=True)
    k.add_argument("--trial-out", required=True)
    k.add_argument("--source-curation", default="")
    k.add_argument("--regions", default="", help="an export's roi_regions_*.csv")
    k.add_argument("--plates", default="", help="the roi_plates_*.csv beside it")
    r = sub.add_parser("ready")
    r.add_argument("--pack", required=True)
    r.add_argument("--trial-out", required=True)
    c = sub.add_parser("calibrate")
    c.add_argument("--pack", required=True)
    c.add_argument("--source-out", required=True)
    c.add_argument("--overrides", required=True,
                   help="the source study's rotation override file for the cut pass")
    c.add_argument("--channel", required=True, help="the slide channel that holds the marker")
    g = sub.add_parser("align")
    g.add_argument("--pack", required=True)
    g.add_argument("--source-out", required=True)
    g.add_argument("--trial-out", required=True)
    o = sub.add_parser("one-marker")
    o.add_argument("--pack", required=True)
    o.add_argument("--channel", required=True, help="the slide channel that holds the marker")
    o.add_argument("--nuclear", default="DAPI")
    a = ap.parse_args(argv)
    return {"cut": cmd_cut, "check": cmd_check, "ready": cmd_ready,
            "one-marker": cmd_one_marker, "calibrate": cmd_calibrate,
            "align": cmd_align}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
