// 04l: one study's ROI curation never shows on, or gets saved over, another study's page.
//
// The page keeps its curation in localStorage under fixed keys - the section
// records, the rolling backup, the landmark radius and the ROI selection. A page
// opened from file:// in Chrome shares one origin with every other file:// page
// (and every page serve_curators.py serves shares that server's origin), so two
// studies' roi_curator.html read and write the SAME keys - and scene uids are
// built from the subject name (A1_s01_sc01), which a pilot and a main study
// easily share. Before the curation was filed per study, study B's page opened
// on study A's placements and saved them back as B's.
//
// Self-building, like level_curator_study.test.js: two throwaway studies with
// the SAME uids and the SAME plate ids, built into two pages, run against one
// shared localStorage - the shared-origin case exactly.

const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const repo = path.dirname(__dirname);
const pys = [process.env.PY, "python3", "python"].filter(Boolean);
const tmps = [];

// `seeded`: also leave the app's mirror of the curation in out_root/curation, filed
// the way the page files it, and build WITH the seed, so the Python side that
// unwraps it for the page is exercised too.
function buildStudy(name, seeded) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "rcs-"));
  tmps.push(tmp);
  let cfgPath = null;
  for (const exe of pys) {
    try {
      cfgPath = execFileSync(exe, ["-c",
        "import sys; sys.path.insert(0, sys.argv[1]); import _fixture; " +
        "print(_fixture.write_study(sys.argv[2], atlas_source='wullimann1996'))",
        __dirname, tmp], { stdio: "pipe" }).toString().trim();
      break;
    } catch (e) { /* next interpreter */ }
  }
  if (!cfgPath) { console.error("could not write a study config"); process.exit(2); }
  const out = JSON.parse(fs.readFileSync(cfgPath, "utf8")).out_root;
  fs.mkdirSync(path.join(out, "reformatted"), { recursive: true });
  const pdir = path.join(out, "atlas", "plates_wullimann");
  fs.mkdirSync(pdir, { recursive: true });
  // Same uids in both studies: the collision this suite is about.
  fs.writeFileSync(path.join(out, "reformatted", "reformat_index.csv"),
    "id,kind,animal,section_order\n" +
    [1, 2, 3].map(i => `A1_s01_sc0${i},section,A1,${i}`).join("\n") + "\n");
  let csv = "plate_id,image_file,px_w,px_h,regions,section_level\n";
  for (const l of [10, 20, 30]) {
    const id = "wplate_0" + l;
    csv += `${id},${id}.png,400,300,Dm,${l}\n`;
    fs.writeFileSync(path.join(pdir, id + ".png"), "");
  }
  fs.writeFileSync(path.join(pdir, "plates.csv"), csv);
  fs.writeFileSync(path.join(pdir, "seeds.csv"),
    "plate_id,page,plate_seq,region,region_raw,is_unknown,colour_hex,from_raster," +
    "x_px,y_px,x_frac,y_frac\n");
  if (seeded) {
    let id = null;
    for (const exe of pys) {
      try {
        id = execFileSync(exe, ["-c",
          "import sys; sys.path.insert(0, sys.argv[1]); import ls_curation as CU; " +
          "print(CU.study_id(sys.argv[2]))", path.join(repo, "scripts"), out],
          { stdio: "pipe" }).toString().trim();
        break;
      } catch (e) { /* next interpreter */ }
    }
    if (!id) { console.error("could not name the study"); process.exit(2); }
    const cur = path.join(out, "curation");
    fs.mkdirSync(cur, { recursive: true });
    const r = o => Object.assign({plate: 0, pairs: [], assigned: false, noroi: false,
                                  fav: false, rot: 0, excl: false}, o);
    fs.writeFileSync(path.join(cur, "ls_roi_curator_v1.json"), JSON.stringify({
      _studies: {[id]: {A1_s01_sc01: r({excl: true})},
                 "/elsewhere/other_study": {A1_s01_sc02: r({fav: true})}},
      _legacy: {A1_s01_sc03: r({noroi: true})}}));
    fs.writeFileSync(path.join(cur, "ls_roi_curator_v1_rois.json"), JSON.stringify({
      _studies: {"/elsewhere/other_study": ["Xx"]}, _legacy: ["Dm"]}));
  }
  const html = path.join(tmp, "roi.html");
  let ran = false;
  for (const exe of pys) {
    try {
      execFileSync(exe, [path.join(repo, "scripts", "04l_roi_curator.py"),
        ...(seeded ? [] : ["--no-seed"]), "--out", html], { env: { ...process.env, LS_CONFIG: cfgPath }, stdio: "pipe" });
      ran = true;
      break;
    } catch (e) { /* next interpreter */ }
  }
  if (!ran) { console.error("could not build the 04l page"); process.exit(2); }
  const page = fs.readFileSync(html, "utf8");
  const scripts = [...page.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
  fs.mkdirSync(path.join(__dirname, "build"), { recursive: true });
  fs.writeFileSync(path.join(__dirname, "build", name + ".js"),
    scripts.reduce((a, b) => (b.length > a.length ? b : a), ""));
}

buildStudy("roi_study_a");
buildStudy("roi_study_b");
buildStudy("roi_study_seeded", true);
tmps.forEach(t => fs.rmSync(t, { recursive: true, force: true }));

const { env, load, chk, note, done } = require("./harness");
const { store } = env;
const EXP = `{KEY, STUDY, BACKUP_KEY, SEL_KEY, PT_R_KEY, DATA, get S(){return S}, toggleExcl, toggleFav, save,
  autosave, roiRestoreBackup, onRoiSize, selStore,
  get SEL(){return SEL}, get PT_R_CANON(){return PT_R_CANON},
  set active(v){active=v}}`;
const decide = (X, uid, how) => { X.active = uid; how === "fav" ? X.toggleFav() : X.toggleExcl(); };
const rec = (o) => Object.assign({plate: 0, pairs: [], assigned: false, noroi: false,
                                  fav: false, rot: 0, excl: false}, o);
const clear = () => { for (const k of Object.keys(store)) delete store[k]; };

// ---- study A curates, then study B's page opens in the same browser --------
clear();
const A = load(EXP, "roi_study_a");
const uid = A.DATA[0].uid, uid2 = A.DATA[1].uid;
decide(A, uid, "excl");
A.onRoiSize(13);
A.selStore(["Dm"]);
A.autosave();
chk("study A's decision is made", !!(A.S[uid] && A.S[uid].excl), true);

const B = load(EXP, "roi_study_b");
chk("both studies have the same uid (the collision under test)", B.DATA[0].uid, uid);
chk("study B's page does not show study A's decision", !!(B.S[uid] && B.S[uid].excl), false);
chk("...nor study A's landmark radius", B.PT_R_CANON, 8);
chk("...nor study A's ROI selection", B.SEL, null);

decide(B, uid, "fav");
B.onRoiSize(21);
B.selStore([]);
B.autosave();

const A2 = load(EXP, "roi_study_a");
chk("study B's save did not overwrite study A's decision",
    !!(A2.S[uid] && A2.S[uid].excl && !A2.S[uid].fav), true);
chk("...nor study A's radius", A2.PT_R_CANON, 13);
chk("...nor study A's selection", JSON.stringify(A2.SEL), '["Dm"]');
const B2 = load(EXP, "roi_study_b");
chk("...and study B's own decision is kept for study B",
    !!(B2.S[uid] && B2.S[uid].fav && !B2.S[uid].excl), true);
chk("...and its own radius", B2.PT_R_CANON, 21);

// The rolling backup is per study too: restoring it on A brings back A's work.
A2.S[uid].excl = false;
A2.roiRestoreBackup();
chk("study A's backup restores study A's state, not study B's",
    !!(A2.S[uid] && A2.S[uid].excl && !A2.S[uid].fav), true);

// Two tabs open at once, one per study: each save must carry the OTHER study's
// newest work, not the copy it read when it loaded. The other tab is the store
// changing under this one (two pages loaded here would share their function
// declarations, which a real second tab does not).
note("");
const TA = load(EXP, "roi_study_a");
const v = JSON.parse(store[TA.KEY]);
v._studies[B.STUDY][uid2] = rec({excl: true});
store[TA.KEY] = JSON.stringify(v);
decide(TA, uid2, "fav");
chk("a save on study A's open tab keeps what study B saved after A loaded",
    !!JSON.parse(store[TA.KEY])._studies[B.STUDY][uid2].excl, true);
chk("...and saves study A's own decision", !!JSON.parse(store[TA.KEY])._studies[TA.STUDY][uid2].fav, true);

// ---- existing stores from before the curation was filed per study ---------
// The old values are flat: {uid: record}, a backup {at, marker, S}, a number
// and a list. None can say whose it is. A page takes the records for ITS
// sections and leaves the rest in the store for the page they belong to; the
// radius, the selection and the backup stay readable and are never dropped.
note("");
clear();
store[A.KEY] = JSON.stringify({[uid]: rec({excl: true}), "OTHER_s01_sc01": rec({fav: true})});
store[A.BACKUP_KEY] = JSON.stringify({at: "2026-01-01T00:00:00Z", marker: "x",
  S: {[uid]: rec({fav: true}), "OTHER_s01_sc01": rec({excl: true})}});
store[A.PT_R_KEY] = "17";
store[A.SEL_KEY] = '["Dm"]';
const L = load(EXP, "roi_study_a");
chk("a pre-existing record for this study's section still loads", !!(L.S[uid] && L.S[uid].excl), true);
chk("...one for a section this page does not have is not shown", "OTHER_s01_sc01" in L.S, false);
chk("a pre-existing radius still reads", L.PT_R_CANON, 17);
chk("a pre-existing selection still reads", JSON.stringify(L.SEL), '["Dm"]');
decide(L, uid2, "fav");
chk("saving keeps the record this page does not own", store[A.KEY].includes("OTHER_s01_sc01"), true);
const L2 = load(EXP, "roi_study_a");
chk("...and keeps this study's migrated record", !!(L2.S[uid] && L2.S[uid].excl), true);
L2.roiRestoreBackup();
chk("a pre-existing backup still restores this study's sections", !!(L2.S[uid] && L2.S[uid].fav), true);
chk("...and not another study's", "OTHER_s01_sc01" in L2.S, false);
L2.autosave();
chk("a new backup leaves the old one in the store", store[A.BACKUP_KEY].includes("2026-01-01"), true);
const LB = load(EXP, "roi_study_b");
chk("another study's page does not claim what study A already took", uid in LB.S, false);

// ---- the seed a page carries comes from its own study's part of the file -----
// The app mirrors the page's key to out_root/curation/<key>.json, filed per study,
// and 04l embeds that file in the page it builds. It must embed this study's part
// (and what is unclaimed from before filing - that file has only been this
// study's), never another study's entry the shared origin left in it.
note("");
clear();
const SD = load(`{SEED_STATE, get SEL(){return SEL}}`, "roi_study_seeded");
chk("the page is seeded with this study's records",
    !!(SD.SEED_STATE.A1_s01_sc01 && SD.SEED_STATE.A1_s01_sc01.excl), true);
chk("...and what is unclaimed from before filing", !!(SD.SEED_STATE.A1_s01_sc03 || {}).noroi, true);
chk("...not another study's", "A1_s01_sc02" in SD.SEED_STATE, false);
chk("the ROI selection seed reads one from before filing, not another study's",
    JSON.stringify(SD.SEL), '["Dm"]');

done();
