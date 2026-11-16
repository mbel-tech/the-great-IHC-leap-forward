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
  chk("the synthetic study has sections this build does not carry", out.length >= 2, true);
  const [O1, O2] = out.map(p => p.scene_uid);
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
  const NOFRAME = out[2] ? out[2].scene_uid : null;
  if(NOFRAME) X.S[NOFRAME] = {plate: pi, assigned: true,
                              pairs: [[10, 10, 0, 0, 0, 4, "bg"]], polys: []};
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
      /2 of \d+ section\(s\) came from outside this build/.test(E("expStat").textContent), true);
  chk("a record with no provenance row is not exported", uidsIn(pl).has("ZZ999_s99a_sc99"), false);
  chk("...and is named in the header", E("expWarn").textContent.includes("ZZ999_s99a_sc99"), true);
  chk("...which is showing", E("expWarn").style.display, "");
  if(NOFRAME){
    chk("a record with positions and no frame is not exported", uidsIn(pl).has(NOFRAME), false);
    chk("...and is named, with why",
        E("expWarn").textContent.includes(NOFRAME + " (no frame"), true);
  }
  for(const u of [O1, O2, NOFRAME, "ZZ999_s99a_sc99"]) if(u) delete X.S[u];
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
  done();
})();
