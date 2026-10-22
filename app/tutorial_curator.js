// The tutorial's hooks inside the ROI curator page.
//
// Injected by app/curator_view.py with runJavaScript only while the tutorial
// runs; the page itself is not changed, so the curator outside the tutorial is
// exactly what it was. Runs in the page's own global scope, so it can see the
// page's drawSec, secGeom, secViewFor, img2can, uiScale, el and `active`.
//
//   tutorialPoint(selector, text)   outline an element and show a line beside it
//   tutorialClear()                 remove that
//   tutorialShowKey(uid, polys, frame)  draw the expert's outlines (dashed) on that section;
//                                   `frame` is the picture width they were saved at
//   tutorialClearKey()              stop drawing them
(function(){
  if (window.__tutorialHooks) return;
  window.__tutorialHooks = true;
  const ACCENT = "#e8590c";
  let pointed = null, keyed = null;

  function place(){
    const old = document.querySelectorAll(".tut-point");
    old.forEach(n => n.remove());
    if (!pointed) return;
    const target = document.querySelector(pointed.sel);
    if (!target) return;
    const r = target.getBoundingClientRect();
    const box = document.createElement("div");
    box.className = "tut-point";
    box.style.cssText = "position:fixed;pointer-events:none;z-index:99999;border:3px solid " +
      ACCENT + ";border-radius:6px;left:" + (r.left - 4) + "px;top:" + (r.top - 4) +
      "px;width:" + (r.width + 2) + "px;height:" + (r.height + 2) + "px";
    const tip = document.createElement("div");
    tip.className = "tut-point tut-tip";
    tip.textContent = pointed.text;
    tip.style.cssText = "position:fixed;pointer-events:none;z-index:99999;background:" +
      ACCENT + ";color:#fff;font:bold 13px system-ui;padding:6px 10px;border-radius:8px;" +
      "max-width:320px;left:" + (r.right + 12) + "px;top:" + Math.max(4, r.top) + "px";
    document.body.appendChild(box);
    document.body.appendChild(tip);
  }

  window.tutorialPoint = function(sel, text){
    pointed = {sel: sel, text: text || ""};
    place();
  };
  window.tutorialClear = function(){ pointed = null; place(); };
  window.addEventListener("resize", place);

  // A page that is not the ROI curator (not built yet, a placeholder) has no
  // section canvas: pointing still works, the answer key has nothing to draw on.
  if (typeof drawSec !== "function") {
    window.tutorialShowKey = function(){};
    window.tutorialClearKey = function(){};
    return;
  }
  // The expert's outlines go on top of whatever drawSec drew, dashed, in white
  // over a dark casing so they read on pale and dark tissue alike.
  const drawOriginal = drawSec;
  drawSec = function(){
    const out = drawOriginal.apply(this, arguments);
    if (!keyed || active !== keyed.uid) return out;
    const c = el("cSec"), x = c.getContext("2d");
    const g = secGeom(), V = secViewFor(c, g.D), u = uiScale(c);
    x.save();
    x.setTransform(V.k, 0, 0, V.k, -V.ox * V.k, -V.oy * V.k);
    x.setLineDash([8 * u, 6 * u]);
    // Saved at one picture size, shown on whichever the page loaded (256 or 768).
    const w = (typeof secImg !== "undefined" && secImg && secImg.naturalWidth) || 0;
    const s = keyed.frame && w ? w / keyed.frame : 1;
    keyed.polys.forEach(pg => {
      x.beginPath();
      for (let i = 0; i < pg.v.length; i += 2) {
        const p = img2can(pg.v[i] * s, pg.v[i + 1] * s, g);
        i ? x.lineTo(p[0], p[1]) : x.moveTo(p[0], p[1]);
      }
      x.closePath();
      x.lineWidth = 4.5 * u; x.strokeStyle = "rgba(4,18,31,.8)"; x.stroke();
      x.lineWidth = 2.5 * u; x.strokeStyle = "#ffffff"; x.stroke();
    });
    x.restore();
    return out;
  };
  window.tutorialShowKey = function(uid, polys, frame){
    keyed = {uid: uid, polys: polys || [], frame: frame || 0};
    drawSec();
  };
  window.tutorialClearKey = function(){ keyed = null; drawSec(); };
})();
