// Zoom and pan on the section pane.
//
// The view is a transform on top of the canvas coordinates every point is
// stored in, so what matters is that it never leaks into them: a click while
// zoomed has to land on the image pixel under the cursor, the point under the
// cursor has to stay there while zooming, and annotations have to keep their
// SCREEN size. None of that is visible to a stub that does not render, but all
// of it is arithmetic the page exposes.

const { env, load, chk, done } = require("./harness");
const { fire } = env;

// A native-resolution composite: far more pixels than the 600 px pane.
env.imgSize = () => 3000;

const X = load(`{rows, select, drawSec, secZoom, secFit, secDown, secPanStart, canvasXY, uiScale,
  can2img, secGeom, el, toggleGuided,
  get SV(){return SV}, get secView(){return secView},
  get guided(){return guided}, get pending(){return pending},
  set active(v){active=v}, get active(){return active}}`);

const uids = X.rows().map(r => r.uid);
X.select(uids[0], true);
if (X.guided) X.toggleGuided();              // free mode: a click makes `pending`
X.drawSec();
const c = X.el("cSec");
const D = X.secGeom().D;
const ev = (x, y, extra) => Object.assign({ clientX: x, clientY: y, button: 0,
                                            preventDefault() {} }, extra || {});
const near = (a, b, tol) => Math.abs(a - b) <= (tol || 1e-6);

chk("the canvas is the pane, not the image", c.width, 600);
const [x1, y1] = X.canvasXY(c, ev(300, 300));
chk("unzoomed, the pane centre is the frame centre",
    near(x1, D / 2, 1e-6) && near(y1, D / 2, 1e-6), true);
const u1 = X.uiScale(c);
chk("unzoomed, one screen px is D/600 canvas units", near(u1, D / 600, 1e-9), true);

// Zoom 4x about a point off-centre: that point stays under the cursor.
const [ax, ay] = X.canvasXY(c, ev(150, 420));
X.secZoom(4, ax, ay);
const [bx, by] = X.canvasXY(c, ev(150, 420));
chk("the point under the cursor stays there", near(ax, bx, 1e-6) && near(ay, by, 1e-6),
    true);
chk("annotations keep their screen size", near(X.uiScale(c), u1 / 4, 1e-9), true);

// A click while zoomed stores the image pixel under the cursor.
X.secDown(ev(150, 420));
fire("mouseup", {});
const want = X.can2img(ax, ay, X.secGeom());
chk("a click while zoomed lands on that image pixel",
    X.pending && near(X.pending[0], want[0], 1e-6) && near(X.pending[1], want[1], 1e-6),
    true);

// Pan with the middle button: dragging 100 screen px right brings content
// from the left into view, so the same screen point now shows a canvas point
// 100 screen px further left.
const before = X.canvasXY(c, ev(300, 300));
X.secPanStart(ev(300, 300, { button: 1 }));
fire("mousemove", ev(400, 300));
fire("mouseup", {});
X.drawSec();
const after = X.canvasXY(c, ev(300, 300));
chk("a middle-drag pans by the distance dragged",
    near(before[0] - after[0], 100 * X.uiScale(c), 1e-6) && near(before[1], after[1], 1e-6),
    true);

// The view cannot leave the frame.
X.secZoom(100);
chk("zoom is capped", X.secView.z, 64);
X.secFit();
chk("0 fits the whole section again", X.secView.z, 1);
X.secZoom(1 / 3);
chk("cannot zoom out past the whole section", X.secView.z, 1);

X.secZoom(3);
X.select(uids[1], true);
X.drawSec();
chk("a new section opens fitted", X.secView.z, 1);

done();
