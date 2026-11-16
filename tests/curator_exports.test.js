// What the ROI curator hands the pipeline: every curated section, outlines
// with one area, and three files the operator can count.
//
// (1) exportCsv looped over DATA - the sections THIS build carries - not over
//     the saved state. Rebuild the page with a different subset (the worklist,
//     the analysis set, one animal) and the next export silently left out
//     every curated section the build did not carry; 05a takes the newest
//     roi_regions*, so their ROIs were gone from the analysis.
// (3) A polygon could be closed, or a corner dragged, so that the outline
//     crossed itself. 05a takes the shoelace (a bowtie's lobes cancel to ~0)
//     and 05c counts nuclei by even-odd membership (both lobes count), so the
//     density between them was inflated or infinite.
// (5) The download fallback clicked three downloads back to back. Refusing the
//     browser's "download multiple files" prompt dropped two of the three, and
//     nothing on the page said how many files there should have been.

const { env, load, chk, note, done } = require("./harness");
const { els, blobs, fire } = env;   // els: the animal picker

const X = load(`{PLATES, DATA, PROWS, P_BY, S, st, rows, select, onSlideUser,
  exportCsv, drawSec, secDown, commitPoly, polysOf, ringCrosses, beginExport,
  dl, toggleGuided, dropVertex,
  get guided(){return guided}, get draft(){return draft},
  get expQueue(){return expQueue}, set expDir(v){expDir=v},
  set active(v){active=v}, get active(){return active}}`);

// Through getElementById, so a notice the page never touched reads as empty
// rather than as a missing stub.
const E = id => document.getElementById(id);
const parse = t => t.split("\n").map(l => l.split(","));
const col = (rs, name) => rs[0].indexOf(name);

// ---- (1) the export carries the saved state, not the build ------------------
note("(1) sections outside this build are exported from their stored record\n");
{
  const inBuild = new Set(X.DATA.map(d => d.uid));
  const out = X.PROWS.filter(p => !inBuild.has(p.scene_uid));
  // Five are used below; asserted, so a smaller study fails here rather than
  // quietly skipping the checks that need them.
  chk("the synthetic study has sections this build does not carry", out.length >= 5, true);
  const [O1, O2, NOFRAME, DROPPED] = out.map(p => p.scene_uid);
  // One the pipeline no longer carries (04a never reformatted it), and the
  // rest that it does - set here so the checks below do not depend on what
  // the synthetic study happens to say about them.
  const GONE = (out.find(p => p.status === "not_reformatted") || out[4]).scene_uid;
  for(const p of out) p.status = p.scene_uid === GONE ? "not_reformatted" : "reformatted";
  const pi = Math.max(0, X.PLATES.findIndex(P => (P.hulls || []).length));
  const P = X.PLATES[pi];
  // O1: curated on a 768 composite in an earlier build - frame stamped.
  X.S[O1] = {plate: pi, plate_id: P.id, assigned: true, frame: 768,
             pairs: [[300, 150, 5, 5, 0, 33], [330, 150, 0, 0, 0, 33, "bg"]],
             polys: [{v: [300, 150, 420, 150, 420, 270, 300, 270], roi: 1,
                      region: "Rx", part: 1}]};
  // O2: a favourite only - no positions, so no frame is needed.
  X.S[O2] = {plate: 0, fav: true, pairs: [], polys: []};
  // Records that cannot be exported correctly: one with a position and no
  // frame, one the provenance table has never heard of.
  X.S[NOFRAME] = {plate: pi, assigned: true,
                  pairs: [[10, 10, 0, 0, 0, 4, "bg"]], polys: []};
  // Curated in full (an ROI and a background disc), then dropped in Review -
  // so the next build does not carry it - and one with the same work that
  // 04a never reformatted. Exported live, 05a would try to place both, find no
  // reformat_index row and fail on every run.
  const full = () => ({plate: pi, plate_id: P.id, assigned: true, frame: 256,
    pairs: [[110, 50, 0, 0, 0, 11, "bg"]],
    polys: [{v: [100, 50, 140, 50, 140, 90, 100, 90], roi: 1, region: "Rx", part: 1}]});
  X.S[DROPPED] = Object.assign(full(), {rev: {act: "drop", why: "fold"}});
  X.S[GONE] = full();
  X.S["ZZ999_s99a_sc99"] = {plate: pi, assigned: true, frame: 256,
                             pairs: [[10, 10, 0, 0, 0, 4, "bg"]], polys: []};

  blobs.length = 0; X.exportCsv();
  const [pl, lm, rg] = blobs.slice(0, 3).map(parse);
  const uidsIn = rs => new Set(rs.slice(1).map(r => r[0]));
  chk("roi_plates names the curated section outside the build", uidsIn(pl).has(O1), true);
  chk("...and the favourite-only one", uidsIn(pl).has(O2), true);
  chk("roi_regions carries its region", rg.some(r => r[0] === O1 && r[col(rg, "roi_kind")] === "roi"), true);
  chk("roi_landmarks carries its landmark", uidsIn(lm).has(O1), true);
  const row = pl.find(r => r[0] === O1) || [];
  chk("its marker comes from the provenance table",
      row[col(pl, "marker")], X.P_BY[O1].marker);
  chk("...its animal too", row[col(pl, "animal")], X.P_BY[O1].animal);
  chk("...and the subset says where it came from", row[col(pl, "subset")], "outside_build");
  // Canonical = stored / (frame / 256): 300/3 = 100.
  const poly = (rg.find(r => r[0] === O1 && r[col(rg, "roi_kind")] === "roi") || [])[col(rg, "sec_poly")];
  chk("its polygon is divided by its OWN stored frame", poly, "100.00 50.00;140.00 50.00;140.00 90.00;100.00 90.00");
  const lmr = lm.find(r => r[0] === O1) || [];
  chk("...its landmark too", [lmr[col(lm, "sec_x")], lmr[col(lm, "sec_r")]].join(), "100.00,11.00");
  chk("the status line says how many came from outside the build",
      /4 of \d+ row\(s\) came from outside this build/.test(E("expStat").textContent), true);
  chk("...and how many of them the pipeline no longer carries",
      /2 section\(s\) the pipeline no longer carries, exported as excluded/.test(E("expStat").textContent), true);
  const plRow = u => pl.find(r => r[0] === u) || [];
  chk("a section dropped in Review is exported as excluded",
      [plRow(DROPPED)[col(pl, "excluded")], plRow(DROPPED)[col(pl, "status")]].join(), "1,excluded");
  chk("...and one 04a never reformatted", plRow(GONE)[col(pl, "excluded")], "1");
  chk("...while a live one outside the build is not", plRow(O1)[col(pl, "excluded")], "0");
  chk("...and the dropped one's regions still go out", rg.some(r => r[0] === DROPPED), true);

  // END TO END: 05a's own analysis_set, on these very files. The dropped and
  // never-reformatted sections leave it as "excluded by the operator" - they
  // reach build() not at all, so they cannot fail its reformat_index lookup -
  // while O1 is kept to be placed.
  {
    const fs = require("fs"), os = require("os"), path = require("path");
    const { spawnSync } = require("child_process");
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "lsexp_"));
    fs.writeFileSync(path.join(dir, "pl.csv"), blobs[0]);
    fs.writeFileSync(path.join(dir, "rg.csv"), blobs[2]);
    const py = `
import json, os, sys
sys.path.insert(0, ${JSON.stringify(__dirname)})
from _fixture import use_temp_study, load_stage
use_temp_study()
P5 = load_stage("05a_roi_geometry.py")
d = sys.argv[1]
keep, dropped, _, _ = P5.analysis_set(P5.load_csv(os.path.join(d, "rg.csv")),
                                      P5.load_csv(os.path.join(d, "pl.csv")))
print(json.dumps({"keep": keep, "dropped": dict(dropped)}))
`;
    let res = null;
    for(const exe of [process.env.PY, "python3", "python"].filter(Boolean)){
      const r = spawnSync(exe, ["-c", py, dir], {encoding: "utf8"});
      if(!r.error && r.status === 0){ res = JSON.parse(r.stdout.trim().split("\n").pop()); break; }
      if(!r.error) { note(r.stderr); break; }
    }
    fs.rmSync(dir, {recursive: true, force: true});
    chk("05a's analysis set could be run on the export", !!res, true);
    if(res){
      chk("05a: the Review-dropped section is excluded, not placed",
          res.dropped[DROPPED], "excluded by the operator");
      chk("05a: so is the never-reformatted one", res.dropped[GONE], "excluded by the operator");
      chk("05a: the live outside section is kept", res.keep.includes(O1), true);
    }
  }
  chk("a record with no provenance row is not exported", uidsIn(pl).has("ZZ999_s99a_sc99"), false);
  chk("...and is named in the header", E("expWarn").textContent.includes("ZZ999_s99a_sc99"), true);
  chk("...which is showing", E("expWarn").style.display, "");
  chk("a record with positions and no frame is not exported", uidsIn(pl).has(NOFRAME), false);
  chk("...and is named, with why",
      E("expWarn").textContent.includes(NOFRAME + " (no frame"), true);
  for(const u of [O1, O2, NOFRAME, DROPPED, GONE, "ZZ999_s99a_sc99"]) delete X.S[u];
  blobs.length = 0; X.exportCsv();
  chk("with nothing left out, the header notice goes", E("expWarn").style.display, "none");
}

// ---- (3) an outline that crosses itself is never stored -----------------------
note("\n(3) a self-intersecting polygon is refused\n");
{
  chk("a bowtie crosses", X.ringCrosses([0, 0, 10, 10, 10, 0, 0, 10]), true);
  chk("a square does not", X.ringCrosses([0, 0, 10, 0, 10, 10, 0, 10]), false);
  chk("a double-clicked corner is not a crossing",
      X.ringCrosses([0, 0, 10, 0, 10, 10, 10, 10, 0, 10]), false);
  chk("a ring pinched through one point is",
      X.ringCrosses([0, 0, 5, 5, 10, 10, 10, 0, 5, 5, 0, 10]), true);

  const pi = X.PLATES.findIndex(P => (P.hulls || []).length >= 3);
  els["animal"].value = "LS105";
  const uid = X.rows()[0].uid;
  X.select(uid, true); X.active = uid;
  X.onSlideUser(pi);
  X.drawSec();
  if(!X.guided) X.toggleGuided();
  const s = X.st(uid);
  const n0 = X.polysOf(s).length;
  const click = (cx, cy) => X.secDown({clientX: cx, clientY: cy, button: 0, preventDefault() {}});
  // A lopsided bowtie: the second and fourth edges cross. Lopsided so its
  // shoelace area is not ~0 - a symmetric one is already refused as having no
  // area, and this is the crossing that check lets through.
  click(100, 100); click(300, 300); click(300, 100); click(100, 140);
  X.commitPoly();
  chk("a bowtie cannot be closed", X.polysOf(X.st(uid)).length, n0);
  chk("...the ring is left open to be fixed", X.draft && X.draft.length, 8);
  chk("...and the header says why", /crosses itself/.test(E("polyWarn").textContent), true);
  // z takes the corners back; nothing was stored.
  X.dropVertex(); X.dropVertex(); X.dropVertex(); X.dropVertex();
  chk("z clears the refused ring", X.draft, null);

  // A SYMMETRIC bowtie's lobes cancel to an area of ~0. The area floor used
  // to catch it first and refuse it without a word; the crossing is tested
  // first now, so it is named for what it is.
  E("polyWarn").textContent = "";
  click(100, 100); click(220, 220); click(220, 100); click(100, 220);
  X.commitPoly();
  chk("a symmetric bowtie is refused as crossing, with a message",
      /crosses itself/.test(E("polyWarn").textContent), true);
  X.dropVertex(); X.dropVertex(); X.dropVertex(); X.dropVertex();
  // Three corners in a line enclose nothing - said, not ignored.
  click(100, 100); click(150, 150); click(200, 200);
  X.commitPoly();
  chk("a ring with no area is refused with its own message",
      /encloses no area/.test(E("polyWarn").textContent), true);
  X.dropVertex(); X.dropVertex(); X.dropVertex();

  // A crossed outline already in the store (drawn before the page refused
  // them, or imported) is named when its section is shown.
  const other = X.rows()[2].uid;
  X.S[other] = {plate: pi, assigned: true, frame: 768, pairs: [],
                polys: [{v: [100, 100, 300, 300, 300, 100, 100, 140], roi: 1,
                         region: "OldBow", part: 1}]};
  X.select(other, true);
  chk("a stored crossed region is flagged on arrival",
      /REDRAW.*OldBow/.test(E("polyWarn").textContent), true);
  X.select(uid, true);
  chk("...and the flag goes on a section without one", E("polyWarn").style.display, "none");
  delete X.S[other];
}
{
  // A clean square, then a corner dragged across the opposite edge.
  const uid = X.rows()[1].uid;
  const pi = X.PLATES.findIndex(P => (P.hulls || []).length >= 3);
  X.select(uid, true); X.active = uid; X.onSlideUser(pi); X.drawSec();
  if(!X.guided) X.toggleGuided();
  const click = (cx, cy) => X.secDown({clientX: cx, clientY: cy, button: 0, preventDefault() {}});
  click(100, 100); click(220, 100); click(220, 220); click(100, 220);
  X.commitPoly();
  const s = X.st(uid);
  chk("a square closes", X.polysOf(s).length, 1);
  chk("...and clears the notice", E("polyWarn").style.display, "none");
  const before = X.polysOf(X.st(uid))[0].v.join();
  click(100, 100);                                  // grab corner 0
  fire("mousemove", {clientX: 300, clientY: 160});   // across the far edge
  fire("mouseup", {});
  chk("a drag that crosses the outline is put back",
      X.polysOf(X.st(uid))[0].v.join(), before);
  chk("...and the header says so", /MOVE UNDONE/.test(E("polyWarn").textContent), true);
  click(100, 100);
  fire("mousemove", {clientX: 90, clientY: 90});     // a harmless move
  fire("mouseup", {});
  chk("a drag that keeps it simple is kept",
      X.polysOf(X.st(uid))[0].v.join() !== before, true);
}

// ---- (5) the download fallback --------------------------------------------
note("\n(5) the download fallback spaces the files and says how many\n");
(async () => {
  // The exports above are still queued; let them finish before counting.
  await X.expQueue;
  const clicks = [];
  const realCreate = global.document.createElement;
  global.document.createElement = () => {
    const a = realCreate();
    a.click = () => clicks.push({name: a.download, at: Date.now()});
    return a;
  };
  X.expDir = null;
  E("expStat").textContent = E("expStat").innerHTML = "";
  blobs.length = 0; X.exportCsv();
  await X.expQueue;
  global.document.createElement = realCreate;
  chk("three downloads", clicks.length, 3);
  const gaps = clicks.slice(1).map((c, i) => c.at - clicks[i].at);
  chk("...spaced, not back to back", gaps.every(g => g >= 700), true);
  const h = E("expStat").innerHTML;
  chk("the status line says how many files to expect", /expect 3 files/.test(h), true);
  chk("...counts the ones downloaded", /downloaded 3 of 3/.test(h), true);
  chk("...and links each one to download again",
      ["roi_plates_", "roi_landmarks_", "roi_regions_"].every(n => h.includes(`download="${n}`)), true);

  // A SECOND EXPORT PRESSED WHILE THE FIRST IS STILL DOWNLOADING. It is queued
  // behind the first, keeps its own date stamp (the app files by the stamp in
  // the name, so a first-export file renamed with the second's stamp lands in
  // a folder with none of its siblings) and its own count - never "5 of 3".
  clicks.length = 0;
  global.document.createElement = () => {
    const a = realCreate();
    a.click = () => clicks.push({name: a.download, at: Date.now()});
    return a;
  };
  const seen = [];
  let html = "";
  Object.defineProperty(E("expStat"), "innerHTML", {configurable: true,
    get: () => html, set: v => { html = v; seen.push(v); }});
  const realStamp = globalThis.expStampNow;
  X.exportCsv();                                     // the first, under today's stamp
  globalThis.expStampNow = () => "01.01.2030_00.00"; // the clock has moved on
  X.exportCsv();                                     // pressed again, mid-download
  globalThis.expStampNow = realStamp;
  await X.expQueue;
  global.document.createElement = realCreate;
  chk("both exports download, six files", clicks.length, 6);
  chk("...the first keeps its own stamp",
      clicks.slice(0, 3).every(c => !c.name.includes("01.01.2030_00.00")), true);
  chk("...the second has its own", clicks.slice(3).every(c => c.name.includes("01.01.2030_00.00")), true);
  chk("...still spaced across the two",
      clicks.slice(1).every((c, i) => c.at - clicks[i].at >= 700), true);
  const counts = seen.map(v => (v.match(/downloaded (\d+) of (\d+)/) || []).slice(1).map(Number))
                     .filter(x => x.length);
  chk("...and no count ever exceeds its export's total",
      counts.length === 6 && counts.every(([a, b]) => a <= b && b === 3), true);
  done();
})();
