// 04k: one study's anchors never show on, or get saved over, another study's page.
//
// The page keeps its anchors in localStorage under one fixed key. A page opened
// from file:// in Chrome shares one origin with every other file:// page, so
// two studies' level_curator.html read and write the SAME key - and scene uids
// are built from the subject name (LS45_s01_sc03), which a pilot and a main
// study easily share. Before the anchors were filed per study, study B's page
// showed study A's anchors on B's sections and wrote them back as B's.
//
// Self-building, like wullimann_level.test.js: two throwaway studies with the
// SAME uids and the SAME plate ids, built into two pages, run against one
// shared localStorage - the shared-origin case exactly.

const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const repo = path.dirname(__dirname);
const pys = [process.env.PY, "python3", "python"].filter(Boolean);
const tmps = [];

function buildStudy(name) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "lcs-"));
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
  let csv = "plate_id,image_file,regions,section_level\n";
  for (const l of [10, 20, 30, 40]) {
    const id = "wplate_0" + l;
    csv += `${id},${id}.png,Dm,${l}\n`;
    fs.writeFileSync(path.join(pdir, id + ".png"), "");
  }
  fs.writeFileSync(path.join(pdir, "plates.csv"), csv);
  buildPage(cfgPath, name);
  return tmp;
}

function buildPage(cfgPath, name) {
  const html = path.join(path.dirname(cfgPath), "level.html");
  let ran = false;
  for (const exe of pys) {
    try {
      execFileSync(exe, [path.join(repo, "scripts", "04k_level_curator.py"), "--out", html],
        { env: { ...process.env, LS_CONFIG: cfgPath }, stdio: "pipe" });
      ran = true;
      break;
    } catch (e) { /* next interpreter */ }
  }
  if (!ran) { console.error("could not build the 04k page"); process.exit(2); }
  const page = fs.readFileSync(html, "utf8");
  const scripts = [...page.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
  fs.mkdirSync(path.join(__dirname, "build"), { recursive: true });
  fs.writeFileSync(path.join(__dirname, "build", name + ".js"),
    scripts.reduce((a, b) => (b.length > a.length ? b : a), ""));
}

// The same study after its folder was moved to another drive: everything under it
// moves and its config is pointed at the new place; the page rebuilt there names
// itself by the NEW folder, while the browser holds its anchors under the old one.
function moveStudy(tmp, name) {
  const moved = tmp + "-moved";
  fs.renameSync(tmp, moved);
  tmps.push(moved);
  const cfgPath = path.join(moved, "study.json");
  fs.writeFileSync(cfgPath, fs.readFileSync(cfgPath, "utf8").split(tmp).join(moved));
  buildPage(cfgPath, name);
}

const tmpA = buildStudy("level_study_a");
buildStudy("level_study_b");
moveStudy(tmpA, "level_study_a_moved");
tmps.forEach(t => fs.rmSync(t, { recursive: true, force: true }));

const { env, load, chk, note, done } = require("./harness");
const { els, store } = env;
const EXP = `{KEY, STUDY, DATA, PLATES, anchors, droppedAnchors, rows, setAnchor,
  set active(v){active=v}}`;
const anchorAt = (X, uid, slot) => {
  els["animal"].value = "A1"; X.active = uid; els["slider"].value = slot; X.setAnchor();
};

// ---- study A curates, then study B's page opens in the same browser --------
const A = load(EXP, "level_study_a");
const uid = A.DATA[0].uid;
anchorAt(A, uid, 1);
chk("study A's anchor is set", A.anchors[uid] && A.anchors[uid].id, "wplate_020");

const B = load(EXP, "level_study_b");
chk("both studies have the same uid (the collision under test)", B.DATA[0].uid, uid);
chk("study B's page does not show study A's anchor", uid in B.anchors, false);
chk("...and does not count it as a dropped anchor of its own", B.droppedAnchors.length, 0);

anchorAt(B, uid, 3);
chk("study B anchors the same uid on a different plate", B.anchors[uid].id, "wplate_040");

const A2 = load(EXP, "level_study_a");
chk("study B's save did not overwrite study A's anchor", A2.anchors[uid] && A2.anchors[uid].id,
    "wplate_020");
const B2 = load(EXP, "level_study_b");
chk("...and study B's own anchor is kept for study B", B2.anchors[uid] && B2.anchors[uid].id,
    "wplate_040");

// ---- an existing store from before the anchors were filed per study --------
// The old value is a flat {uid: anchor}. It cannot say whose it is, so a page
// takes the anchors for ITS sections and leaves everything else in the store,
// for the page whose sections they are.
note("");
for (const k of Object.keys(store)) delete store[k];
store[A.KEY] = JSON.stringify({
  [uid]: { id: "wplate_030", at: 2 },
  "OTHER_s01_sc01": { id: "wplate_010", at: 0 },
});
const L = load(EXP, "level_study_a");
chk("a pre-existing anchor for this study's section still loads", L.anchors[uid] && L.anchors[uid].id,
    "wplate_030");
chk("...one for a section this page does not have is not shown", "OTHER_s01_sc01" in L.anchors, false);
chk("...nor counted as dropped", L.droppedAnchors.length, 0);
anchorAt(L, L.DATA[1].uid, 3);
chk("saving keeps the anchor this page does not own", store[A.KEY].includes("OTHER_s01_sc01"), true);
const L2 = load(EXP, "level_study_a");
chk("...and keeps this study's migrated anchor", L2.anchors[uid] && L2.anchors[uid].id, "wplate_030");
const LB = load(EXP, "level_study_b");
chk("another study's page does not claim what study A already took", uid in LB.anchors, false);

// ---- a study whose folder moved still opens with its anchors ----------------
// The anchors filed under the old folder are carried over - copied, the old entry
// left in place - because they carry this study's own mark (out_root/curation/
// study_id.txt, which moved with the folder). Study B's entry, under the same
// uids, does not, which is what keeps the collision checks above passing.
note("");
for (const k of Object.keys(store)) delete store[k];
const MA = load(EXP, "level_study_a");
anchorAt(MA, uid, 1);
const MB = load(EXP, "level_study_b");
anchorAt(MB, MB.DATA[1].uid, 3);
const M = load(EXP, "level_study_a_moved");
chk("the moved study names itself by its new folder", M.STUDY !== MA.STUDY, true);
chk("...and still opens with its anchors", M.anchors[uid] && M.anchors[uid].id, "wplate_020");
chk("...not the other study's, though it shares the uids", M.DATA[1].uid in M.anchors, false);
chk("the operator is told where they came from", els["studyNote"].textContent.includes(MA.STUDY), true);
anchorAt(M, M.DATA[2].uid, 2);
const filed = JSON.parse(store[M.KEY])._studies;
chk("the first save files them under the new folder", (filed[M.STUDY][uid] || {}).id, "wplate_020");
chk("...and leaves the old folder's entry in place", (filed[MA.STUDY][uid] || {}).id, "wplate_020");

// Two old folders carrying this study's mark: no guessing which is the real one.
note("");
const two = JSON.parse(store[M.KEY]);
two._studies["/old/copy_one"] = two._studies[MA.STUDY];
two._tokens = Object.assign({}, two._tokens, {"/old/copy_one": two._tokens[MA.STUDY]});
delete two._studies[M.STUDY];
store[M.KEY] = JSON.stringify(two);
els["studyNote"].textContent = "";
const M2 = load(EXP, "level_study_a_moved");
chk("two candidate folders: nothing is carried over", uid in M2.anchors, false);
chk("...and the operator is told anchors from another folder exist",
    els["studyNote"].textContent.includes("another folder"), true);

done();
