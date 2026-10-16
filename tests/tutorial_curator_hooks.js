// The tutorial's curator hooks (app/tutorial_curator.js) against a stub page.
//
// Run by tests/test_tutorial_curator.py, which skips it when node is missing.
// The stub stands in for the page's globals the hooks use: drawSec, secGeom,
// secViewFor, img2can, uiScale, el, active, and a document with elements.
const fs = require("fs");
const path = require("path");
const vm = require("vm");

let fails = 0;
function chk(name, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  console.log((ok ? "ok   " : "FAIL ") + name + "  " + JSON.stringify(got) +
              (ok ? "" : "  (wanted " + JSON.stringify(want) + ")"));
  if (!ok) fails++;
}

const calls = [];
const ctx2d = new Proxy({}, {
  get: (t, k) => (k in t ? t[k] : (...a) => calls.push([k, a])),
  set: (t, k, v) => { t[k] = v; return true; }
});
const nodes = [];
function mkEl(id) {
  return { id, className: "", style: {}, textContent: "",
           getBoundingClientRect: () => ({ left: 10, top: 20, right: 60, bottom: 40,
                                           width: 50, height: 20 }),
           getContext: () => ctx2d,
           remove() { const i = nodes.indexOf(this); if (i >= 0) nodes.splice(i, 1); } };
}
const page = { guideBtn: mkEl("guideBtn"), cSec: mkEl("cSec") };
const sandbox = {
  addEventListener: () => {},
  document: {
    querySelector: sel => page[sel.replace(/^#/, "")] || null,
    querySelectorAll: sel => nodes.filter(n => sel === ".tut-point" &&
                                         n.className.split(" ").includes("tut-point")),
    createElement: () => mkEl(""),
    body: { appendChild: n => nodes.push(n) },
  },
  Math, JSON,
};
sandbox.window = sandbox;                // in a page, window is the global object
// The page's own globals, as a classic script would have them.
const pageSrc = `
var active = "s1"; var drawn = 0;
function el(id){ return document.querySelector("#" + id); }
function drawSec(){ drawn++; }
function secGeom(){ return {D: 100}; }
function secViewFor(){ return {k: 1, ox: 0, oy: 0}; }
function img2can(x, y){ return [x, y]; }
function uiScale(){ return 1; }
`;
vm.createContext(sandbox);
vm.runInContext(pageSrc, sandbox);
const hooks = fs.readFileSync(path.join(__dirname, "..", "app", "tutorial_curator.js"), "utf8");
vm.runInContext(hooks, sandbox);

vm.runInContext("drawSec()", sandbox);
chk("with nothing called, drawSec draws as before and nothing extra", [sandbox.drawn, calls.length], [1, 0]);

vm.runInContext('tutorialPoint("#guideBtn", "Guided mode")', sandbox);
chk("tutorialPoint adds an outline and a tip", nodes.length, 2);
chk("...the tip says the text", nodes[1].textContent, "Guided mode");
vm.runInContext('tutorialPoint("#nothing", "x")', sandbox);
chk("a selector with no element adds nothing", nodes.length, 0);
vm.runInContext('tutorialPoint("#guideBtn", "again"); tutorialClear()', sandbox);
chk("tutorialClear removes it", nodes.length, 0);

calls.length = 0;
vm.runInContext('tutorialShowKey("s1", [{v: [0,0, 10,0, 10,10]}])', sandbox);
chk("tutorialShowKey redraws", sandbox.drawn, 2);
chk("...dashed", calls.some(c => c[0] === "setLineDash" && c[1][0].length === 2), true);
chk("...along the outline", calls.filter(c => c[0] === "lineTo").length, 2);
calls.length = 0;
vm.runInContext('active = "s2"; drawSec()', sandbox);
chk("another section gets no key", calls.some(c => c[0] === "setLineDash"), false);
vm.runInContext('active = "s1"; tutorialClearKey()', sandbox);
calls.length = 0;
vm.runInContext("drawSec()", sandbox);
chk("tutorialClearKey stops it", calls.some(c => c[0] === "setLineDash"), false);
vm.runInContext(hooks, sandbox);
calls.length = 0;
vm.runInContext('tutorialShowKey("s1", [{v: [0,0, 10,0, 10,10]}])', sandbox);
chk("injecting twice does not draw the key twice",
    calls.filter(c => c[0] === "setLineDash").length, 1);

// A page that is not the curator (a "Not built yet" placeholder) has no drawSec.
const bare = { addEventListener: () => {}, document: sandbox.document, Math, JSON };
bare.window = bare;
vm.createContext(bare);
let threw = null;
try { vm.runInContext(hooks + '; tutorialShowKey("s1", []); tutorialClearKey();', bare); }
catch (e) { threw = String(e); }
chk("a page without a section canvas takes the hooks without an error", threw, null);

console.log("\n" + fails + " failure(s)");
process.exit(fails ? 1 : 0);
