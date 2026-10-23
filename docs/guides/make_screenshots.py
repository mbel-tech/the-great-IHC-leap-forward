"""Retake the user guide's screenshots from the app as it is now.

    python docs/guides/make_screenshots.py [--out docs/guides/img/user-guide]

Builds a made-up study in a temporary folder (three animals, paired pERK/PCNA
scans, the bundled salmon plates) whose outputs are empty files standing in for
real ones, opens the app on it offscreen and saves each screen as a PNG. No real
slide, image or result is read or shown, so the pictures can be published.

The stand-in outputs only have to make the screens show a study part way
through: every Prepare stage done, the ROI curator part curated, detection part
measured. Stage.done() is existence-only, so an empty file is enough for most;
the few tables a screen counts rows of (manifest, worklist, export, 05a boxes,
the 05c done log) get rows.

Needs PySide6. Run with QT_QPA_PLATFORM=offscreen where there is no display
(set here when unset).
"""
import argparse
import csv
import json
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [os.path.join(REPO, "app"), os.path.join(REPO, "scripts"),
                os.path.join(REPO, "tests")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox")

SIZE = (1360, 860)
ANIMALS = ["LS01", "LS02", "LS03"]
SLIDES = [1, 2, 3]
PASSES = {"a": "AF568", "b": "AF488"}
MISNAMED = "LS03_3b old copy.czi"     # one file that does not fit the naming rule


def _czi(path, marker):
    """A CZI with only a metadata segment: enough for every header read."""
    import czi_meta
    text = ('<ImageDocument><Metadata><Information><Image><SizeC>2</SizeC><Dimensions>'
            '<Channels><Channel Name="DAPI"><EmissionWavelength>465</EmissionWavelength>'
            f'</Channel><Channel Name="{marker}"><EmissionWavelength>'
            f'{603 if marker == "AF568" else 519}</EmissionWavelength></Channel></Channels>'
            '</Dimensions></Image></Information><Scaling><Items><Distance Id="X"><Value>'
            '6.5e-07</Value></Distance><Distance Id="Y"><Value>6.5e-07</Value></Distance>'
            '</Items></Scaling></Metadata></ImageDocument>')
    body = text.encode("utf-8")
    head = bytearray(czi_meta.HEADER_LEN)
    head[:10] = b"ZISRAWFILE"
    struct.pack_into("<QQ", head, 84, 0, czi_meta.HEADER_LEN)
    seg = bytearray(czi_meta.SEGMENT_HEADER_LEN)
    seg[:14] = b"ZISRAWMETADATA"
    sizes = struct.pack("<ii", len(body), 0).ljust(czi_meta.METADATA_DATA_LEN, b"\0")
    with open(path, "wb") as fh:
        fh.write(bytes(head) + bytes(seg) + sizes + body)
        # Sparse: the Slides table shows a believable size, the disk holds a few KB.
        # NTFS does not make a file sparse unless it is flagged, so on Windows
        # this would write gigabytes of zeros: keep the stand-ins small there.
        if os.name != "nt":
            fh.truncate(int((1.9 + (sum(map(ord, os.path.basename(path))) % 15) / 10) * 1e9))


def _write(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)


def _touch(path):
    """An output path: a folder when it has no extension, else an empty file."""
    if os.path.splitext(path)[1]:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not os.path.exists(path):
            open(path, "w").close()
    else:
        os.makedirs(path, exist_ok=True)


def build_study(tmp):
    """Lay out the made-up study under `tmp`; return its project file."""
    from _fixture import write_study
    os.environ.pop("LS_ATLAS_BUNDLE", None)      # the fixture turns the shipped plates off
    slides = os.path.join(tmp, "slides")
    os.makedirs(slides, exist_ok=True)
    files, scenes = [], []
    for animal in ANIMALS:
        for slide in SLIDES:
            for variant, marker in PASSES.items():
                name = f"{animal}_{slide}{variant}.czi"
                _czi(os.path.join(slides, name), marker)
                files.append((name, animal, slide, variant, marker))
                for i in range(3 if slide < 3 else 2):
                    scenes.append((f"{animal}_s{slide:02d}{variant}_sc{i:02d}", name, animal,
                                   slide, variant, marker, i))
    _czi(os.path.join(slides, MISNAMED), "AF568")
    path = write_study(
        tmp,
        study={"name": "Demo study (made-up data)"},
        acquisition={"layout": "paired", "markers": ["AF568", "AF488"],
                     "marker_segmentation": {"AF488": {"segment": "own"}}},
        marker_identity={"AF568": "pERK", "AF488": "PCNA"},
        slide_naming={"pattern": r"^(?P<subject>[A-Za-z]+\d+)_(?P<slide>\d+)"
                                 r"(?P<replicate>[a-z])?\.czi$",
                      "example": "LS01_1a.czi", "case_insensitive": True},
        scene_render={"out_dir": os.path.join(tmp, "scene_pictures"), "um_per_px": 2.6,
                      "source": "scan", "range": "dataset"})
    out = os.path.join(tmp, "out")
    os.makedirs(out, exist_ok=True)
    import ls_atlas as AT
    AT.plate_dir({"out_root": out})          # the shipped salmon plates, before any stand-in
    _write(os.path.join(out, "manifest", "manifest_files.csv"),
           [["file", "animal", "slide", "variant", "marker_channel"]] + [list(f) for f in files])
    _write(os.path.join(out, "manifest", "manifest_scenes.csv"),
           [["scene_uid", "file", "animal", "slide", "variant", "marker_channel",
             "scene_index"]] + [list(s) for s in scenes])
    return path, [s for s in scenes if s[5] == "AF568"]


def _section_picture(path, seed, grey=False):
    """A made-up section: a two-lobed tissue outline with nuclei and a stained patch."""
    import numpy as np
    from PIL import Image
    rng = np.random.default_rng(seed)
    h, w = 420, 560
    yy, xx = np.mgrid[0:h, 0:w]
    cx, cy = w / 2 + rng.normal(0, 8), h / 2 + rng.normal(0, 6)
    tissue = ((xx - cx) / 230) ** 2 + ((yy - cy) / 170) ** 2 < 1
    tissue &= np.abs(xx - cx) > 4 + 10 * ((yy - cy) / 170 < -0.2)        # the midline cleft
    img = np.zeros((h, w, 3), np.float32)
    img[tissue] = (8, 10, 40)
    n = 2600
    px, py = rng.integers(0, w, n), rng.integers(0, h, n)
    keep = tissue[py, px]
    for x, y in zip(px[keep], py[keep]):
        img[max(y - 1, 0):y + 2, max(x - 1, 0):x + 2, 2] += 90
    for side in (-1, 1):                                   # stained cells, both lobes
        sx, sy = cx + side * 95, cy - 40
        m = 260
        qx = (sx + rng.normal(0, 38, m)).astype(int).clip(0, w - 2)
        qy = (sy + rng.normal(0, 30, m)).astype(int).clip(0, h - 2)
        for x, y in zip(qx, qy):
            if tissue[y, x]:
                img[y:y + 2, x:x + 2, 0] += 150
    img = img.clip(0, 255).astype("uint8")
    if grey:
        img = img.max(axis=2)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.fromarray(img).save(path)


def fill_curator(study, sections):
    """Index, section pictures and composites for both markers, then the 04l page."""
    import subprocess
    out = study.out_root
    for m, marker in enumerate(study.markers):
        rows = [["id", "kind", "animal", "section_order"]]
        for i, s in enumerate(sections):
            uid = s[0] if m == 0 else s[0].replace("a_sc", "b_sc")
            rows.append([uid, "section", s[2], i + 1])
            _section_picture(os.path.join(study.names.path("sections", marker), uid + ".png"),
                             i, grey=True)
            for kind in ("sections_rgb", "sections_rgb_thumb"):
                _section_picture(os.path.join(study.names.path(kind, marker), uid + ".png"), i)
        _write(study.names.path("index", marker), rows)
    subprocess.run([sys.executable, os.path.join(REPO, "scripts", "04l_roi_curator.py"),
                    "--worklist", os.path.join(out, "reformatted", "roi_worklist.csv"),
                    "--rgb", "--no-seed"], check=True, env=dict(os.environ))


def fill_outputs(w, sections):
    """Stand-in outputs: Prepare done, curation and detection part way."""
    import stages as S
    import workflow as W
    study = w.study()
    out = study.out_root
    for st in (S.BY_ID[x] if isinstance(x, str) else x for x in W.screen_stages("prepare")):
        if not W.applies(st, study)[0]:
            continue
        for entry in st.outputs:
            _shown, paths = st._alternatives(entry, out, study.names)
            _touch(paths[0])
    uids = [s[0] for s in sections]
    _write(os.path.join(out, "reformatted", "roi_worklist.csv"),
           [["scene_uid", "animal", "rank", "tier"]]
           + [[u, u.split("_")[0], i + 1, "1"] for i, u in enumerate(uids)])
    return uids


def settle(app, seconds):
    """Let the event loop run, so pages load and web views paint."""
    import time
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


#: The run history the Project screen lists: (stage, ended, minutes it took).
RUNS = [("rgb_pcna", "2026-10-20T11:20", 18), ("roi_curator", "2026-10-21T11:21", 1),
        ("roi_geometry", "2026-10-21T16:44", 2), ("tune_detection", "2026-10-21T16:58", 13),
        ("detect", "2026-10-21T20:12", 192)]


def fill_progress(w, sections):
    """Part of the curation exported, regions placed, detection part way, a run history."""
    import datetime as dt
    study = w.study()
    out = study.out_root
    m0 = study.markers[0]
    plates = [r[0] for r in csv.reader(open(os.path.join(out, "atlas", "plates_final",
                                                          "plates.csv"), encoding="utf-8"))][1:]
    first = [s[0] for s in sections]
    second = [u.replace("a_sc", "b_sc") for u in first]
    curated = first[:17] + second[:6]
    stamp = "21.10.2026_11.21"
    folder = os.path.join(study.export_root, stamp)
    _write(os.path.join(folder, f"roi_regions_{stamp}.csv"),
           [["scene_uid", "roi", "region"]] + [[u, 1, "Dl"] for u in curated])
    _write(os.path.join(folder, f"roi_plates_{stamp}.csv"),
           [["scene_uid", "plate_id", "excluded"]]
           + [[u, plates[18 + 4 * (i % 3)], 0] for i, u in enumerate(curated)])
    for name in ("roi_geometry", "roi_boxes"):
        _write(study.names.path(name, m0), [["scene_uid"]] + [[u] for u in first[:15]])
    _touch(os.path.join(out, "qc", "detection_tuning.md"))
    _touch(os.path.join(out, "results", "roi_nuclei.csv"))
    _write(os.path.join(out, "results", "roi_nuclei_sections_done.csv"),
           [[m0, "*"]] + [[m0, u] for u in first[:9]])
    for sid, ended, minutes in RUNS:
        end = dt.datetime.fromisoformat(ended)
        w.session.record_run(sid, True, (end - dt.timedelta(minutes=minutes)).isoformat(),
                             end.isoformat())


def _save(widget, out, name, shots):
    path = os.path.join(out, name + ".png")
    widget.grab().save(path)
    shots.append(path)


def _tab(tabs, text):
    for i in range(tabs.count()):
        if tabs.tabText(i).lstrip("✓ ").startswith(text):
            tabs.setCurrentIndex(i)
            return
    raise SystemExit(f"no Set up tab {text!r}")


def _workbook(path):
    """A made-up sampling workbook: fish numbered as the slides' animals are."""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["Fish ID", "Tank", "Treatment", "Weight (g)", "Sampled on"])
    for n, tank, treat, weight in ((1, 301, "control", 41.2), (2, 303, "exercise", 44.8),
                                   (3, 304, "", 39.5)):
        ws.append([n, tank, treat, weight, "2025-11-04"])
    wb.save(path)


def overlay_crop(path):
    """One detection-overlay crop drawn by ls_overlay, on made-up nuclei."""
    import numpy as np
    import ls_overlay as LO
    rng = np.random.default_rng(7)
    h, w = 420, 560
    yy, xx = np.mgrid[0:h, 0:w]
    plane = rng.normal(300, 40, (h, w))
    labels = np.zeros((h, w), np.int32)
    kinds = {}
    inside = lambda x, y: ((x - 280) / 230) ** 2 + ((y - 210) / 175) ** 2 < 1   # noqa: E731
    for i in range(1, 140):
        cx, cy = rng.integers(14, w - 14), rng.integers(14, h - 14)
        r = rng.integers(5, 9)
        disc = (xx - cx) ** 2 + (yy - cy) ** 2 <= r * r
        if labels[disc].any():
            continue
        labels[disc] = i
        pos = rng.random() < 0.3
        plane[disc] += 900 if pos else 150
        kinds[i] = ("outside" if not inside(cx, cy) else "positive" if pos else "negative")
    outline = LO.shape_mask_outline(h, w, inside)
    rec = {"picture": "roi", "region": "Dl", "marker": "pERK", "animal": "LS01", "slide": "1",
           "scene_uid": "LS01_s01a_sc00", "roi_index": "1", "cut": "612.0",
           "n_counted": str(sum(k != "outside" for k in kinds.values())),
           "n_positive": str(sum(k == "positive" for k in kinds.values())),
           "pct_positive": "", "n_outside": str(sum(k == "outside" for k in kinds.values())),
           "n_artifact": "0", "n_off_tissue": "0", "n_censored": "0"}
    rec["pct_positive"] = "%.1f" % (100 * int(rec["n_positive"]) / int(rec["n_counted"]))
    LO.save_png(path, LO.crop_image(plane, labels, kinds, outline, 200, 1400,
                                    LO.crop_lines(rec), 0.65, 50))


def shoot(app, w, tmp, out):
    """Every picture the guide shows, in the order it shows them."""
    from PySide6.QtWidgets import QTabWidget
    from import_slides import ImportScreen
    shots = []

    # Slides first, while the misnamed file is still there to show in red.
    w.goto("setup")
    imp = w.findChild(ImportScreen)
    imp.chose_folder = os.path.join(tmp, "slides")
    imp._load([imp.chose_folder])
    names = [imp.table.item(i, 0).text() for i in range(imp.table.rowCount())]
    settle(app, 0.5)
    imp.table.scrollToItem(imp.table.item(names.index(MISNAMED), 0),
                           imp.table.ScrollHint.PositionAtCenter)
    settle(app, 0.3)
    _save(w, out, "02-setup-slides", shots)
    os.remove(os.path.join(tmp, "slides", MISNAMED))
    imp._load([imp.chose_folder])
    w.refresh()

    tabs = w.findChild(QTabWidget, "setupTabs")
    _tab(tabs, "Microscope and channels")
    settle(app, 0.5)
    _save(w, out, "03-setup-microscope", shots)
    w.pages["setup"].advanced.setChecked(True)
    settle(app, 0.3)
    _tab(tabs, "Pictures")
    settle(app, 0.5)
    _save(w, out, "04-setup-pictures", shots)
    w.pages["setup"].advanced.setChecked(False)

    for sid, name in (("home", "01-project"), ("prepare", "05-prepare"),
                      ("curate", "06-curate"), ("measure", "07-measure")):
        w.goto(sid)
        settle(app, 6 if sid == "curate" else 0.5)
        if sid == "curate":
            confirm_rois(app, w)
        _save(w, out, name, shots)

    w.resize(SIZE[0], 1010)                 # Results runs on to Check detection
    w.goto("results")
    settle(app, 0.5)
    _save(w, out, "08-results", shots)
    w.resize(*SIZE)
    settle(app, 0.5)

    from widgets import UnblindDialog
    dlg = UnblindDialog("Join the experiment", "", w)
    dlg.show()
    settle(app, 0.3)
    _save(dlg, out, "09-unblind-dialog", shots)
    dlg.close()

    _save(w.nav.parentWidget(), out, "10-step-list", shots)

    view = w.curate.findChild(__import__("PySide6.QtWebEngineWidgets",
                                         fromlist=["QWebEngineView"]).QWebEngineView)
    holder = view.parentWidget()
    view.setParent(None)
    view.resize(*SIZE)
    view.show()
    settle(app, 3)
    view.page().runJavaScript("try { onSlideUser(18); } catch (e) {}")   # a plate with regions
    settle(app, 1.5)
    _save(view, out, "11-roi-curator", shots)
    view.setParent(holder)

    _workbook(os.path.join(tmp, "sampling.xlsx"))
    import sheet_match as SM
    import workbook_check as WC
    animals, where = WC.study_animals(w.session.project.cfg)
    dlg = SM.SheetMatchDialog(os.path.join(tmp, "sampling.xlsx"), animals, where, None, w)
    dlg.show()
    settle(app, 0.5)
    _save(dlg, out, "13-sheet-match", shots)
    dlg.close()

    overlay_crop(os.path.join(out, "14-overlay-crop.png"))
    shots.append(os.path.join(out, "14-overlay-crop.png"))

    import channel_editor as CE
    cfg = dict(w.session.project.cfg)
    cfg["acquisition"] = {"layout": "multiplex", "channels": [
        {"name": "DAPI", "role": "nuclear", "czi_name": "DAPI", "index": 0},
        {"name": "pERK", "role": "marker", "czi_name": "AF568", "index": 1,
         "segment": "nuclear"},
        {"name": "PCNA", "role": "marker", "czi_name": "AF488", "index": 2,
         "segment": "nuclear"},
        {"name": "AF647", "role": "ignore", "czi_name": "AF647", "index": 3}]}
    dlg = CE.ChannelEditor(cfg, "multiplex", w)
    dlg.show()
    settle(app, 0.5)
    _save(dlg, out, "15-multiplex-channels", shots)
    dlg.close()

    # The tutorial last: it closes the demo study and opens its own.
    import _tutorial_fixture as TF
    os.environ["LS_TUTORIAL_PACK"] = TF.make_pack(tmp)
    os.environ["LS_TUTORIAL_HOME"] = os.path.join(tmp, "Tutorial study")
    w.resize(1500, SIZE[1])                  # room for the tutorial panel
    w.start_tutorial()
    settle(app, 2)
    _save(w, out, "12-tutorial", shots)
    return shots


def confirm_rois(app, w):
    """Answer the ROI curator's first question (which ROIs) with all of them."""
    from PySide6.QtWebEngineWidgets import QWebEngineView
    for view in w.findChildren(QWebEngineView):
        view.page().runJavaScript("try { selAll(); selConfirm(); } catch (e) {}")
    settle(app, 1.5)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=os.path.join(HERE, "img", "user-guide"))
    args = ap.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)

    # A fixed, readable folder: the screens print the study's paths.
    tmp = os.path.join(tempfile.gettempdir(), "Demo study")
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    os.environ["LS_HOME"] = os.path.join(tmp, "home")
    project, sections = build_study(tmp)
    os.environ["LS_CONFIG"] = project

    from PySide6.QtWidgets import QApplication, QMessageBox
    app = QApplication.instance() or QApplication([])
    import theme
    theme.apply(app)
    for name in ("information", "warning"):
        setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Ok))
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
    import main_window as MW
    import stages as S
    # Draw detection as the installed app shows it, which carries StarDist.
    S.BY_ID["detect"].cli_only = None

    w = MW.MainWindow(REPO, config_path=project)
    w.resize(*SIZE)
    w.show()
    app.processEvents()
    fill_outputs(w, sections)
    fill_curator(w.study(), sections)
    fill_progress(w, sections)
    w.refresh()
    app.processEvents()

    shots = shoot(app, w, tmp, args.out)
    print("\n".join(shots))
    sys.stdout.flush()
    os._exit(0)                 # skip Qt teardown: the tutorial bubble outlives the window


if __name__ == "__main__":
    main()
