// Which ROIs the operator will draw: a study-wide choice, made once.
//
// What this pins, and why each is a place it could go quietly wrong:
//
//  - The stored `roi` is the identity inside every curated polygon and the
//    `roi_n` column of the export. The selection renumbers what is SHOWN, never
//    what is STORED. A suite that only looked at the screen would miss a
//    renumbering that corrupted the export.
//  - With nothing chosen the page behaves exactly as it did before. The default
//    is the thing every existing curation relies on.
//  - A deselected region can be neither aimed at nor walked to, and the walk
//    skips it; but a polygon already drawn for it is left alone.
//  - The choice lives in its own key. `decided()` would drop it from the
//    curation store, and the importers would count it as an orphan section.
//
// Drawing (colours, dimming, labels on the canvas) is not tested here: canvas
// calls are swallowed by the stub. It is checked in a real browser instead.

const { env, load, chk, note, done } = require("./harness");

const X = load(`{PLATES, S, st, plateAt, roisOf, allHullsOf, usedRois, polysOf,
  hullOf, firstUnusedRoi, gAdvance, gSync, gRoi, commitPoly,
  isSel, selRank, selColour, dispLabel, selectedHulls, parseSel, polyLabel, selNote,
  selRegions, selOpen, selToggle, selAll, selNone, selConfirm, selCancel, selAsk,
  selIsOpen, SEL_KEY, SEL_PALETTE, SEL_GREY,
  get SEL(){return SEL}, set SEL(v){SEL=v},
  get selDraft(){return selDraft},
  get gTarget(){return gTarget}, set gTarget(v){gTarget=v},
  set active(v){active=v}, set draft(v){draft=v}}`);

// A plate standing in for a Wullimann one: A and C are single-lobed, B has two
// lobes (an original and its mirror), and `roi` runs in reading order. The names
// are deliberately NOT in alphabetical order so rank and roi can be told apart.
const hull = (region, part, n_parts, roi, x0) => ({
  region, part, n_parts, roi, hex: "#" + (roi * 111111).toString(16).padStart(6, "0").slice(0, 6),
  amb: "", unk: false, seeds: [], v: [[x0, .2], [x0 + .1, .2], [x0 + .1, .4], [x0, .4]],
});
const HULLS = [hull("C", 1, 1, 1, .05), hull("B", 1, 2, 2, .20), hull("A", 1, 1, 3, .35),
               hull("B", 2, 2, 4, .50)];
X.PLATES.length = 0;
X.PLATES.push({ id: "p1", fp: "f", w: 100, h: 100, missing: 0, seeds: [], hulls: HULLS });
X.PLATES.push({ id: "p2", fp: "g", w: 100, h: 100, missing: 0, seeds: [], hulls: [HULLS[0]] });

const UID = "S1";
X.S[UID] = { plate: 0, polys: [], pairs: [] };
X.active = UID;

// ---- the stored value -------------------------------------------------------

chk("[] means 'asked, chose all', not an empty selection", X.parseSel("[]"), null);
chk("junk is no selection", X.parseSel("{nope"), null);
chk("a non-list is no selection", X.parseSel('{"a":1}'), null);
chk("duplicates and non-strings are dropped, order kept",
    JSON.stringify(X.parseSel('["B","A","B",3,"",null]')), '["B","A"]');

// ---- nothing chosen: exactly the old behaviour -------------------------------

X.SEL = null;
chk("nothing chosen: every hull is selected", HULLS.every(X.isSel), true);
chk("...numbered by their stored roi", HULLS.map(X.dispLabel).join(), "1,2,3,4");
chk("...in the stored order", X.roisOf(X.st(UID)).map(h => h.roi).join(), "1,2,3,4");
chk("...in the atlas's own colour", X.selColour(HULLS[0]), HULLS[0].hex);
chk("the walk starts at roi 1", X.firstUnusedRoi(X.st(UID)), 1);

// ---- a choice: pick order, lobes lettered ------------------------------------

X.SEL = ["B", "A"];                      // C is not drawn; B before A
chk("rank is the pick order", [X.selRank(HULLS[1]), X.selRank(HULLS[2]), X.selRank(HULLS[0])].join(), "1,2,0");
chk("a deselected region is not selected", X.isSel(HULLS[0]), false);
chk("both lobes of a region are selected together", X.isSel(HULLS[1]) && X.isSel(HULLS[3]), true);
chk("a two-lobed region's lobes are lettered", [X.dispLabel(HULLS[1]), X.dispLabel(HULLS[3])].join(), "1a,1b");
chk("a one-lobed region is a bare number", X.dispLabel(HULLS[2]), "2");
chk("a deselected hull has no label", X.dispLabel(HULLS[0]), "");
chk("the walk order is pick order, then lobe", X.roisOf(X.st(UID)).map(h => h.region + h.part).join(), "B1,B2,A1");
chk("a deselected region is out of the walk", X.roisOf(X.st(UID)).some(h => h.region === "C"), false);
chk("...but still on the plate", X.allHullsOf(X.st(UID)).length, 4);
chk("selected hulls take palette colours by rank",
    [X.selColour(HULLS[1]), X.selColour(HULLS[2])].join(), X.SEL_PALETTE[0] + "," + X.SEL_PALETTE[1]);
chk("both lobes share a colour", X.selColour(HULLS[1]) === X.selColour(HULLS[3]), true);
chk("a deselected region is grey", X.selColour(HULLS[0]), X.SEL_GREY);
chk("a plate none of the choice is on has an empty walk", X.roisOf({ plate: 1 }).length, 0);

// ---- the walk ----------------------------------------------------------------

X.st(UID).polys.length = 0;
X.gSync();
chk("the cursor starts on the first SELECTED roi (B lobe 1 = roi 2)", X.gTarget, 2);
X.st(UID).polys.push({ v: [0, 0, 9, 0, 9, 9], roi: 2, region: "B", part: 1, n: 0 });
X.gAdvance();
chk("advancing goes to the next selected lobe (roi 4), not roi 3", X.gTarget, 4);
X.st(UID).polys.push({ v: [0, 0, 9, 0, 9, 9], roi: 4, region: "B", part: 2, n: 0 });
X.gAdvance();
chk("...then to A (roi 3)", X.gTarget, 3);
X.st(UID).polys.push({ v: [0, 0, 9, 0, 9, 9], roi: 3, region: "A", part: 1, n: 0 });
X.gAdvance();
chk("...and stops when the selection is done, though roi 1 is undrawn", X.gTarget, 0);
chk("gRoi is null once finished", X.gRoi(), null);

// ---- an existing polygon outside the selection is left alone -------------------

X.st(UID).polys.push({ v: [0, 0, 9, 0, 9, 9], roi: 1, region: "C", part: 1, n: 0 });
chk("a polygon for a deselected region is still drawn", X.polysOf(X.st(UID)).length, 4);
chk("...and still counted as used", X.usedRois(X.st(UID)).has(1), true);
chk("...labelled as outside the selection", X.polyLabel(X.plateAt(X.st(UID)), X.polysOf(X.st(UID))[3]), "-");
chk("a selected region's polygon carries its display label",
    X.polyLabel(X.plateAt(X.st(UID)), X.polysOf(X.st(UID))[0]), "1a");
chk("the operator is told one polygon is outside the selection",
    /1 drawn outside/.test(X.selNote(X.st(UID))), true);
chk("the STORED roi is untouched by any of this",
    X.polysOf(X.st(UID)).map(p => p.roi).join(), "2,4,3,1");
X.st(UID).polys.length = 0;

// ---- committing a polygon stores the atlas roi, not the display number ---------

X.SEL = ["A", "B"];                      // A is now 1, but its stored roi is 3
X.gSync();
chk("cursor on A (roi 3), the first selected", X.gTarget, 3);
X.draft = [10, 10, 90, 10, 90, 90, 10, 90];
X.commitPoly();
chk("commitPoly stored one polygon", X.polysOf(X.st(UID)).length, 1);
chk("...with the ORIGINAL roi (3), though it is shown as 1",
    X.polysOf(X.st(UID))[0].roi + "/" + X.dispLabel(X.hullOf(X.plateAt(X.st(UID)), X.polysOf(X.st(UID))[0])), "3/1");
X.st(UID).polys.length = 0;

// ---- the pop-up --------------------------------------------------------------

chk("the key sits beside, not inside, the curation store", X.SEL_KEY, "ls_roi_curator_v1_rois");
chk("regions are listed once each in plate order", X.selRegions().map(r => r.name).join(), "C,B,A");
chk("...with the number of plates each is on", X.selRegions().map(r => r.plates).join(), "2,1,1");

X.SEL = null;
X.selOpen();
chk("opening with no choice ticks every region", X.selDraft.join(), "C,B,A");
X.selNone();
chk("None clears the list", X.selDraft.length, 0);
X.selConfirm();
chk("Confirm with nothing ticked does nothing", X.SEL, null);
chk("...and keeps the pop-up open", X.selIsOpen(), true);
X.selToggle("A"); X.selToggle("C");
chk("ticking appends, so the order is the pick order", X.selDraft.join(), "A,C");
X.selToggle("A"); X.selToggle("A");
chk("unticking and re-ticking moves it to the end", X.selDraft.join(), "C,A");
X.selConfirm();
chk("Confirm applies the choice", X.SEL.join(), "C,A");
chk("...stores it under its own key", env.store.ls_roi_curator_v1_rois, '["C","A"]');
chk("...and closes the pop-up", X.selIsOpen(), false);
chk("...and re-aims the cursor at the first selected roi (C = roi 1)", X.gTarget, 1);

X.selOpen();
chk("reopening starts from the saved choice", X.selDraft.join(), "C,A");
X.selToggle("C");
X.selCancel();
chk("Cancel leaves the saved choice alone", X.SEL.join() + "|" + env.store.ls_roi_curator_v1_rois, 'C,A|["C","A"]');

X.SEL = null; delete env.store.ls_roi_curator_v1_rois;
X.selAsk();
chk("the first load asks when nothing is stored", X.selIsOpen(), true);
X.selCancel();
chk("cancelling the first question means 'all' ...", X.SEL, null);
chk("...and is remembered, so it is not asked again", env.store.ls_roi_curator_v1_rois, "[]");
X.selAsk();
chk("a stored answer is not asked again", X.selIsOpen(), false);

// ---- the choice is not part of the curation store ------------------------------

X.SEL = ["A"]; X.selOpen(); X.selConfirm();
chk("the curation store is untouched by the selection",
    "ls_roi_curator_v1_rois" in JSON.parse(env.store.ls_roi_curator_v1 || "{}"), false);

done();
