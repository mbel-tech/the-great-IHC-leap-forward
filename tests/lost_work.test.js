// The ROI curator does not lose work.
//
// Seven ways a day's curation used to go missing without anything saying so,
// one check each (the review of 2026-11-15):
//
//   1. a second tab: the idle one's autosave (and its unload save) wrote its
//      stale copy of the store over what the busy one had saved;
//   2. a section curated only with polygons: revisiting it moved it onto
//      whatever plate the slider was on;
//   3. Review "Clear" on a reinstated section deleted its polygons, with no way
//      back;
//   4. a save that failed (a full browser store) threw out of the click and
//      nothing said so;
//   5. restoring the backup trusted a stale "verified: ok" across an atlas
//      change;
//   6. the guided walk skipped a region after the plate was changed, because a
//      polygon drawn on the old plate answered the same ROI number on the new;
//   7. a region name typed into ADD ROI went into the page as markup.
//
// The other tab is simulated, because the harness is one page: what it does to
// the shared key is written straight into the store, and what a browser would
// then tell this page - the `storage` event, a broadcast - is delivered by hand.

const { env, load, chk, note, done } = require("./harness");
const { els, store, blobs, fire } = env;

// BroadcastChannel is how the tab that won a same-section race tells the tab
// that lost it. Node has one, but between threads; the stub keeps what the page
// posts and lets the suite deliver a message as if another tab had sent it.
const channels = [];
const posted = [];
global.BroadcastChannel = function (name) {
  const ch = { name, onmessage: null, postMessage: m => posted.push(m), close() {} };
  channels.push(ch);
  return ch;
};
const deliver = data => channels.forEach(c => c.onmessage && c.onmessage({ data }));

const X = load(`{KEY, STUDY, BACKUP_KEY, PLATES, DATA, PROWS, st, rows, select, save,
  onSlideUser, exportCsv, revAct, revPick, undoLast, usedRois, firstUnusedRoi, polysOf,
  commitPoly, secDown, drawSec, toggleFav, toggleHard, addRoi, status, roiRestoreBackup, openStudy,
  get S(){return S}, set S(v){S=v},
  set active(v){active=v}, get active(){return active},
  get gTarget(){return gTarget}, set gTarget(v){gTarget=v}}`);

els["animal"].value = "LS105";
const list = X.rows();
const uidAt = i => list[i].uid;
const byId = id => document.getElementById(id);
const mine = () => (X.openStudy(X.KEY)._studies[X.STUDY] || {});
const csvRows = t => t.trim().split("\n").map(l => l.split(","));
// What another tab does to the shared key: its own edit of this study's entry.
const otherTab = (edit, announce) => {
  const v = JSON.parse(store[X.KEY] || "{}");
  v._studies = v._studies || {};
  v._studies[X.STUDY] = v._studies[X.STUDY] || {};
  edit(v._studies[X.STUDY]);
  store[X.KEY] = JSON.stringify(v);
  if(announce) fire("storage", { key: X.KEY, newValue: store[X.KEY] });
};
const record = (extra) => Object.assign({ plate: 3, pairs: [], assigned: true, noroi: false,
  fav: false, rot: 0, excl: false }, extra || {});

// A part that throws is a failure of that part, not the end of the run: each of
// the seven is its own defect, and RED should show all seven.
const part = (title, fn) => {
  note(title);
  try { fn(); } catch(e){ chk("...ran to the end", e && e.message, "no error"); }
};

// ---- 1. two tabs -----------------------------------------------------------
part("--- 1. a second tab does not erase work ---", () => {
const [a, b, c, d] = [uidAt(0), uidAt(1), uidAt(2), uidAt(3)];
X.select(a, true);
X.toggleFav();                                   // this tab's own decision
chk("this tab's decision is saved", !!mine()[a] && mine()[a].fav, true);

otherTab(e => { e[b] = record({ fav: true }); }, true);
chk("another tab's section arrives through the storage event", !!X.S[b] && X.S[b].fav, true);

otherTab(e => { e[c] = record({ hard: true }); }, false);   // event not yet delivered
X.save();                                        // this tab's autosave / unload save
chk("an idle tab's save keeps the other tab's section", !!mine()[c] && mine()[c].hard, true);
chk("...and its own", !!mine()[a] && mine()[a].fav, true);

// Same section, both tabs: last writer wins for that section only.
X.select(d, true);
X.toggleFav();
otherTab(e => { e[d] = record({ hard: true }); e[b] = Object.assign(e[b] || record({ fav: true }), { noroi: true }); }, false);
// Before this tab has heard of it, it changes d too - twice, so its own record
// ends where it started (a favourite) and is written last.
X.toggleFav(); X.toggleFav();
chk("same section: the last writer's version is kept", !!mine()[d].fav && !mine()[d].hard, true);
chk("...the other section the other tab changed is kept", mine()[b].noroi, true);
chk("...and the other tab is told which section it lost",
    posted.some(m => m && m.study === X.STUDY && (m.uids || []).includes(d)), true);
deliver({ type: "overwrote", study: X.STUDY, uids: [a], from: "another tab" });
chk("the losing tab says so in the header", /another tab/i.test(byId("syncNote").textContent)
    && byId("syncNote").textContent.includes(a), true);

});

// ---- 2. a polygon-only section keeps its plate ----------------------------
part("--- 2. revisiting a polygon-only section ---", () => {
const pp = X.PLATES.findIndex(P => (P.hulls || []).length);
const poly = uidAt(4);
X.S[poly] = { plate: pp, pairs: [], assigned: false, noroi: false, fav: false, rot: 0,
  excl: false, polys: [{ v: [10, 10, 60, 10, 60, 60], roi: 1, region: X.PLATES[pp].hulls[0].region,
  part: 1, n: 0, pl: X.PLATES[pp].id }] };
X.save();
els["slider"].value = String(pp + 20);
X.select(uidAt(5), true);
X.select(poly, true);
chk("selecting it leaves its plate where it was drawn", X.S[poly].plate, pp);
blobs.length = 0;
X.exportCsv();
const plRow = csvRows(blobs[0]).slice(1).find(r => r[0] === poly) || [];
const hdr = csvRows(blobs[0])[0];
chk("the export names its plate", plRow[hdr.indexOf("plate_id")], X.PLATES[pp].id);
chk("...and calls it a plate decision, not a favourite", plRow[hdr.indexOf("status")], "plate_only");

});

// ---- 3. Review Clear keeps polygons, and is undoable -----------------------
part("--- 3. Review Clear ---", () => {
const rv = X.PROWS.find(p => !X.S[p.scene_uid]);
X.revPick(rv.scene_uid);
X.revAct("restore");
const rs = X.st(rv.scene_uid);
rs.polys = [{ v: [10, 10, 60, 10, 60, 60], roi: 1, region: "Dl", part: 1, n: 0 }];
X.save();
X.revAct("");
chk("Clear keeps a record that holds polygons", !!X.S[rv.scene_uid] && X.S[rv.scene_uid].polys.length, 1);
chk("...clearing the review decision only", !!(X.S[rv.scene_uid] || {}).rev, false);
X.undoLast();
chk("...and undo puts the decision back", ((X.S[rv.scene_uid] || {}).rev || {}).act, "restore");

});

// ---- 4. a failed save is visible ------------------------------------------
part("--- 4. a save that fails says so ---", () => {
const realSet = global.localStorage.setItem;
const q = uidAt(6);
X.select(q, true);
// Full from here: every write the page tries is refused, as a browser does
// once the origin's quota is used up.
global.localStorage.setItem = () => {
  const e = new Error("The quota has been exceeded."); e.name = "QuotaExceededError"; throw e;
};
let threw = null;
try {
  try { X.toggleFav(); } catch(e){ threw = e.name; }
  chk("a full store does not throw out of the click", threw, null);
  chk("...the header says NOT SAVED, with the reason",
      /NOT SAVED/.test(byId("saveWarn").textContent) && /quota/i.test(byId("saveWarn").textContent), true);
} finally {
  global.localStorage.setItem = realSet;
}
X.toggleHard();
chk("the next change retries, and saves the earlier one too", !!mine()[q] && mine()[q].fav, true);
chk("...and the notice goes", byId("saveWarn").textContent, "");

});

// ---- 5. restore goes through initState -------------------------------------
part("--- 5. restoring the backup ---", () => {
const rb = uidAt(7);
const P0 = X.PLATES.find(P => P.fp);
store[X.BACKUP_KEY] = JSON.stringify({ _studies: { [X.STUDY]: { at: "earlier", marker: "",
  S: { [rb]: record({ plate_id: P0.id, plate_fp: "not-this-atlas", plate_px: "10x1000",
                      verified: "ok" }) } } } });
X.roiRestoreBackup();
chk("a stale 'ok' does not survive an atlas change", X.S[rb] && X.S[rb].verified, "changed");

});

// ---- 6. the guided walk per plate -------------------------------------------
part("--- 6. the walk after a plate change ---", () => {
const pA = X.PLATES.findIndex(P => (P.hulls || []).some(h => h.roi === 1));
const pB = X.PLATES.findIndex(P => (P.hulls || []).some(h => h.roi === 1)
  && P.hulls.find(h => h.roi === 1).region !== X.PLATES[pA].hulls.find(h => h.roi === 1).region);
const g = uidAt(8);
X.select(g, true); X.active = g; X.onSlideUser(pA); X.drawSec();
X.gTarget = 1;
const click = (cx, cy) => X.secDown({ clientX: cx, clientY: cy, button: 0, preventDefault() {} });
click(100, 100); click(220, 100); click(220, 220); click(100, 220);
X.commitPoly();
chk("ROI 1 is drawn on the first plate", X.usedRois(X.st(g)).has(1), true);
X.onSlideUser(pB);
chk("on another plate, its ROI 1 is still to draw", X.usedRois(X.st(g)).has(1), false);
chk("...so the walk starts there", X.firstUnusedRoi(X.st(g)), 1);

});

// ---- 7. names are escaped ---------------------------------------------------
part("--- 7. ADD ROI names are text, not markup ---", () => {
const h = uidAt(9);
const pA = X.PLATES.findIndex(P => (P.hulls || []).length);
X.select(h, true); X.active = h; X.onSlideUser(pA);
global.prompt = () => "<img src=x onerror=alert(1)>";
X.addRoi();
X.status();
chk("the hint shows the name as text", els["lmHint"].innerHTML.includes("<img"), false);
chk("...and so does the region key", els["regKey"].innerHTML.includes("<img"), false);

});

done();
