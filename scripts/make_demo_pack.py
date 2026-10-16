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

        Compares each practice section's reformatted images in the trial run
        with the source study's. Only when every one is byte-identical does it
        write the answer key (the source study's curation of those sections,
        under their new ids): otherwise the expert's outlines would sit in the
        wrong place on the learner's sections. Exit 2 and no answer key when
        they differ.

    python scripts/make_demo_pack.py ready --pack PACK --trial-out TRIAL

        Copies the slow stages' results from the trial run into PACK/ready/.
"""

import argparse
import hashlib
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
    for key in ("source_dir", "out_root", "export_dir", "atlas_pdf"):
        if key in cfg:
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


def _digests(out_root, uid):
    """{relative path: sha256} of every file under reformatted/ named after this section."""
    found = {}
    base = os.path.join(out_root, "reformatted")
    for dirpath, _dirs, files in os.walk(base):
        for f in files:
            if os.path.splitext(f)[0] == uid:
                full = os.path.join(dirpath, f)
                with open(full, "rb") as fh:
                    found[os.path.relpath(dirpath, base)] = hashlib.sha256(fh.read()).hexdigest()
    return found


def frames_match(source_out, trial_out, old_uid, new):
    """(ok, why): the trial's reformatted images of `new` equal the source's of `old_uid`."""
    src, trial = _digests(source_out, old_uid), _digests(trial_out, new)
    if not src:
        return False, f"the source study has no reformatted images of {old_uid}"
    if not trial:
        return False, f"the trial run has no reformatted images of {new}; run Prepare first"
    for folder, digest in src.items():
        if folder in trial and trial[folder] != digest:
            return False, f"{folder}/{new} differs from the source's {old_uid}"
    if not set(src) & set(trial):
        return False, f"no folder holds both {old_uid} and {new}"
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
    cur_path = a.source_curation or os.path.join(a.source_out, "curation", ANSWER_KEY + ".json")
    with open(cur_path, encoding="utf-8") as fh:
        cur = json.load(fh)
    key = {new: cur[old] for old, new in renamed.items() if old in cur}
    missing = [old for old in renamed if old not in cur]
    if missing:
        print(f"the source curation has no entry for {missing[0]}; no answer key written.")
        return 2
    os.makedirs(os.path.join(a.pack, "answer"), exist_ok=True)
    tmp = os.path.join(a.pack, "answer", ANSWER_KEY + ".json.part")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(key, fh)
    os.replace(tmp, tmp[:-5])
    print(f"frames match; answer key written for {len(key)} sections")
    return 0


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
    r = sub.add_parser("ready")
    r.add_argument("--pack", required=True)
    r.add_argument("--trial-out", required=True)
    a = ap.parse_args(argv)
    return {"cut": cmd_cut, "check": cmd_check, "ready": cmd_ready}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
