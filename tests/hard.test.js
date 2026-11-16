// "Hard to attribute": a per-section flag for sections whose regions or level
// are hard to call. It must behave like the favourite - orthogonal, saved,
// undoable, exported in its own column - and "difficult only" must narrow the
// strip to it without deleting anything.

const { env, load, chk, note, done } = require("./harness");
const { els, blobs } = env;

const X = load(`{DATA, st, rows, select, exportCsv, toggleHard, toggleFav,
  cellClass, counts, render, undoLast, hasRoiWork,
  set active(v){active=v}, get active(){return active}}`);

els["animal"].value = "LS105";
const all = X.rows();
const a = all[0].uid, b = all[1].uid;

note("\nflagging a section\n");
X.select(a, true); X.active = a;
X.toggleHard();
chk("the flag is set", !!X.st(a).hard, true);
chk("...and sets no other flag", [X.st(a).fav, X.st(a).assigned, X.st(a).excl].some(Boolean), false);
chk("...and counts as work, so it survives a reload", X.hasRoiWork(X.st(a)), true);
chk("the strip cell is marked", X.cellClass(a).split(" ").includes("hard"), true);
chk("the button says so", els["hardBtn"].textContent, "Hard to attribute ⚑");
X.counts();
chk("the counter shows it", String(els["nhard"].textContent), "1");

note("\nthe filter\n");
els["hardOnly"].checked = true;
chk("difficult only narrows the strip to it", X.rows().map(d => d.uid), [a]);
els["hardOnly"].checked = false;
chk("unticking brings everything back", X.rows().length, all.length);

note("\nthe export\n");
X.select(b, true); X.active = b;
X.toggleFav();
blobs.length = 0;
X.exportCsv();
const pl = blobs[0].split("\n").map(l => l.split(","));
const col = name => pl[0].indexOf(name);
const row = u => pl.slice(1).find(r => r[0] === u);
chk("roi_plates.csv has a difficult column", col("difficult") > -1, true);
chk("...appended after excluded", col("difficult") > col("excluded"), true);
chk("the flagged section is 1", row(a)[col("difficult")], "1");
chk("...and reported as difficult_only", row(a)[col("status")], "difficult_only");
chk("an unflagged section is 0", row(b)[col("difficult")], "0");

note("\nundo and untoggle\n");
X.select(a, true); X.active = a;
X.toggleHard();
chk("pressing it again clears it", !!X.st(a).hard, false);
chk("...and the cell is no longer marked", X.cellClass(a).split(" ").includes("hard"), false);

done();
