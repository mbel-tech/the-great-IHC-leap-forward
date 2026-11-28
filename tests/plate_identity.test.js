// A section curated NOW carries the identity of the plate it was curated
// against - whichever gesture did the curating.
//
// The failure this pins: only moving the plate (slider, prev/next, Assign
// plate) stamped plate_id / plate_fp. The slider deliberately does not follow
// the section, so on a run of serial sections the plate is usually already
// right and the operator just draws. Those records were saved with no
// identity, and on the next load read as "recorded before plate ids were
// kept": counted in the re-confirm notice, their landmarks withheld from the
// fit. Reported from a study where every reagent control was matched that way.
//
// And its neighbour: a record with NO plate work at all - a favourite, a tilt,
// an exclusion - was counted too, though it names no plate to re-confirm.

const { env, load, chk, note, done } = require("./harness");
const { els } = env;

const X = load(`{PLATES, DATA, st, rows, select, onSlideUser, markAssigned,
  toggleGuided, secDown, drawSec, commitPoly, commitPoint, clickPl, toggleNoRoi,
  toggleFav, toggleExcl, toggleBgMode, undoLast, initState, plateNotice,
  transform, save, hasPlateWork,
  get S(){return S}, set S(v){S=v}, get guided(){return guided},
  set active(v){active=v}, get active(){return active}}`);

const pi = X.PLATES.findIndex(P => P.labelled);
const PL = X.PLATES[pi];
els["animal"].value = "LS105";
const uids = X.rows().map(d => d.uid);
const [first, lm, poly, noroi, fav, tilt, excl, bg, old] = uids;
chk("the study has enough sections for this suite", uids.length >= 9, true);

const ev = (cx, cy) => ({ clientX: cx, clientY: cy, button: 0, preventDefault() {} });
const guide = on => { if (X.guided !== on) X.toggleGuided(); };
const reload = () => { X.save(); X.S = X.initState(JSON.parse(JSON.stringify(X.S)), null); };

// The operator scrubs to a plate once, on the first section...
X.select(first); els["slider"].value = pi; X.onSlideUser(pi);
chk("moving the plate stamps its identity (as before)", X.st(first).plate_id, PL.id);

// ---- ...and then only draws, section after section -------------------------

note("\nlandmarks, the slider untouched");
X.select(lm); guide(false);
for (const [x, y] of [[10, 10], [60, 10], [10, 60]]) {
  X.commitPoint(x, y, 5); X.clickPl(ev(x, y));
}
chk("three landmarks landed", X.st(lm).pairs.length, 3);
chk("the record names the plate on screen", X.st(lm).plate_id, PL.id);
chk("...with that plate's fingerprint", X.st(lm).plate_fp, PL.fp);
chk("...and verifies", X.st(lm).verified, "ok");

note("\na guided region, the slider untouched");
X.select(poly); guide(true); X.drawSec();
X.secDown(ev(100, 100)); X.secDown(ev(200, 100)); X.secDown(ev(200, 200));
X.secDown(ev(100, 200)); X.secDown(ev(100, 100));      // back on the first corner
guide(false);
chk("the region closed", (X.st(poly).polys || []).length, 1);
chk("the record names the plate on screen", X.st(poly).plate_id, PL.id);
chk("...and verifies", X.st(poly).verified, "ok");

note("\n'no ROI', which sets assigned");
X.select(noroi); X.toggleNoRoi();
chk("the record names the plate on screen", X.st(noroi).plate_id, PL.id);

// ---- things that are NOT plate work stamp nothing ---------------------------

X.select(fav);  X.toggleFav();
X.select(tilt); X.st(tilt).rot = 12;
X.select(excl); X.toggleExcl();
X.select(bg);   X.toggleBgMode(); X.commitPoint(30, 30, 5); X.toggleBgMode();
chk("a favourite names no plate", X.st(fav).plate_id, undefined);
chk("a background disc names no plate", X.st(bg).plate_id, undefined);

// ---- the next page load ------------------------------------------------------

note("\nafter a reload");
reload();
chk("nothing needs re-confirming", X.plateNotice(), 0);
chk("the landmarked section is still verified", X.S[lm].verified, "ok");
chk("...and its landmarks are in the fit", !!X.transform(X.S[lm]), true);
chk("the region section is still verified", X.S[poly].verified, "ok");
chk("a favourite carries no verdict about a plate", X.S[fav].verified, undefined);
chk("nor a tilt", X.S[tilt].verified, undefined);
chk("nor an exclusion", X.S[excl].verified, undefined);
chk("nor a background disc", X.S[bg].verified, undefined);

// A favourite starred in one session and landmarked in the next is curated
// against the atlas loaded THEN, like any other.
X.select(fav);
for (const [x, y] of [[10, 10], [60, 10], [10, 60]]) {
  X.commitPoint(x, y, 5); X.clickPl(ev(x, y));
}
chk("a favourite landmarked later is stamped then", X.S[fav].verified, "ok");

// ---- what must NOT be laundered ---------------------------------------------

note("\na record that really does predate plate ids");
X.S[old] = { plate: pi, assigned: true, fav: false, noroi: false, excl: false, rot: 0,
             pairs: [[10, 10, 20, 20, 0, 5], [60, 10, 70, 20, 0, 5], [10, 60, 20, 70, 0, 5]] };
reload();
chk("it is still flagged", X.S[old].verified, "by_index");
chk("...and counted", X.plateNotice(), 1);
X.select(old);
X.commitPoint(80, 80, 5); X.clickPl(ev(80, 80));
chk("adding a landmark does not confirm it", X.S[old].verified, "by_index");
chk("...or give it an identity", X.S[old].plate_id || "", "");
chk("...or release its landmarks", X.transform(X.S[old]), null);
X.markAssigned();
chk("Assign plate does - the operator's own confirmation", X.S[old].verified, "ok");
chk("...and the notice clears", X.plateNotice(), 0);

// ---- undo takes the stamp back with the work --------------------------------

note("\nundo");
const u = uids[9] || uids[0];
if (uids[9]) {
  X.select(u); X.toggleNoRoi();
  chk("stamped by 'no ROI'", X.st(u).plate_id, PL.id);
  X.undoLast();
  chk("undo removes the identity with the decision", X.st(u).plate_id, undefined);
}

done();
