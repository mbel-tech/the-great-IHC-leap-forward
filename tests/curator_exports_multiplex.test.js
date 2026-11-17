// Under MULTIPLEX, one record in the store serves every marker of its scene,
// and an export must write it under each of them - including the markers this
// build of the page does not carry it for.
//
// The build set used to be a set of uids. A curated scene outside the build
// was written once, under the provenance table's marker (under multiplex that
// is one channel for every marker), so 05a placed nothing for the others. And
// --worklist / --analysis-set narrow only the FIRST marker, so a scene could be
// in the build through marker 2 and missing for marker 1 - the quantified one -
// and, being "in the build", got no outside row for marker 1 at all.
//
// The synthetic study is paired, so this suite loads the same built page with
// its layout set to multiplex: the export logic is what is under test, and the
// layout is the one input that decides it.

const fs = require("fs");
const path = require("path");
const { env, load, chk, note, done } = require("./harness");
const { blobs } = env;

const src = path.join(__dirname, "build", "curator.js");
if (!fs.existsSync(src)) {
  console.error(`missing ${src}\nRun tests/run.sh, which builds it.`);
  process.exit(2);
}
const js = fs.readFileSync(src, "utf8");
const mx = js.replace(/const ACQ_LAYOUT = "[a-z]+";/, 'const ACQ_LAYOUT = "multiplex";');
chk("the page declares its layout", /const ACQ_LAYOUT = "[a-z]+";/.test(js), true);
fs.writeFileSync(path.join(__dirname, "build", "curator_mx.js"), mx);

const X = load(`{DATA, PROWS, MARKERS, S, exportCsv, ACQ_LAYOUT}`, "curator_mx");
chk("loaded as multiplex", X.ACQ_LAYOUT, "multiplex");

const parse = t => t.split("\n").map(l => l.split(","));
const col = (rs, name) => rs[0].indexOf(name);
const markers = X.MARKERS.map(m => m.id);
chk("the study declares two markers", markers.length, 2);
const [M1, M2] = markers;

// IN through marker 1 only: the build does not carry this uid for marker 2.
const IN = X.DATA.find(d => d.m === M1 && !X.DATA.some(e => e.uid === d.uid && e.m === M2)).uid;
// Not in the build under any marker.
const inBuild = new Set(X.DATA.map(d => d.uid));
const OUT = X.PROWS.find(p => !inBuild.has(p.scene_uid) && p.status !== "not_reformatted").scene_uid;

const rec = () => ({plate: 0, assigned: true, frame: 256,
  pairs: [[110, 50, 0, 0, 0, 11, "bg"]],
  polys: [{v: [100, 50, 140, 50, 140, 90, 100, 90], roi: 1, region: "Rx", part: 1}]});
X.S[IN] = rec();
X.S[OUT] = rec();

blobs.length = 0; X.exportCsv();
const [pl, , rg] = blobs.slice(0, 3).map(parse);
const markersOf = (rs, uid) => rs.slice(1).filter(r => r[0] === uid).map(r => r[col(rs, "marker")]);
const uniq = a => [...new Set(a)].sort().join();

note(`\nin the build for ${M1} only: ${IN}`);
chk("roi_plates has a row for each marker", uniq(markersOf(pl, IN)), [M1, M2].sort().join());
chk("...one each, not two for the marker the build carries",
    markersOf(pl, IN).length, 2);
chk("roi_regions carries its region under each marker",
    uniq(markersOf(rg, IN)), [M1, M2].sort().join());
const rowM2 = pl.find(r => r[0] === IN && r[col(pl, "marker")] === M2) || [];
chk("...the marker the build lacks is marked outside_build",
    rowM2[col(pl, "subset")], "outside_build");

note(`\nnot in the build at all: ${OUT}`);
chk("roi_plates has a row for each marker", uniq(markersOf(pl, OUT)), [M1, M2].sort().join());
chk("roi_regions too", uniq(markersOf(rg, OUT)), [M1, M2].sort().join());
// The same canonical coordinates under both: one record, one frame.
const polys = rg.slice(1).filter(r => r[0] === OUT && r[col(rg, "roi_kind")] === "roi")
                .map(r => r[col(rg, "sec_poly")]);
chk("...the same outline under both", polys.length === 2 && polys[0] === polys[1], true);

done();
