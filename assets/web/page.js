// Feature pages (daevaradar/page.py): draws a tab's blocks, sends what's done here to the feature (on_<id>), and
// patches the page in place on each refresh, so typing, focus and list scrolling survive.
"use strict";
const FeaturePage = (() => {
  let ctx = null, host = null, busy = false;
  const timers = {};

  async function send(id, value) {
    const r = await fetch("/api/action", {
      method: "POST", headers: { "X-Token": TOKEN, "Content-Type": "application/json" },
      body: JSON.stringify({ action: "feature_act", args: { feature: ctx.feature, id, value } }),
    });
    const out = await r.json();
    const res = out.result || {};
    if (res.copy !== undefined) {
      try { await navigator.clipboard.writeText(res.copy); } catch (e) { prompt("Copy this:", res.copy); }
    }
    if (res.message) alert(res.message);
    if (!out.ok) console.warn(id, out.error);
    FeaturePage.poll(true);
  }
  const color = (c) => c || "";
  function labelled(label, el, unit) {
    if (!label && !unit) return el;
    const w = document.createElement("label");
    w.className = "p-labelled";
    if (label) { const s = document.createElement("span"); s.className = "muted"; s.textContent = label; w.appendChild(s); }
    w.appendChild(el);
    if (unit) { const s = document.createElement("span"); s.className = "muted"; s.textContent = unit; w.appendChild(s); }
    return w;
  }

  function build(b) {
    let el;
    switch (b.t) {
      case "h":
        el = document.createElement("div");
        el.className = "p-h";
        el.innerHTML = `<b></b>${b.sub ? "<p class='lead'></p>" : ""}`;
        el.firstChild.textContent = b.text;
        if (b.sub) el.lastChild.textContent = b.sub;
        break;
      case "section":
        el = document.createElement("div");
        el.className = "section";
        el.textContent = b.text;
        break;
      case "text":
        el = document.createElement("div");
        el.className = "p-text" + (b.mono ? " mono" : "") + (b.small ? " small" : "") + (b.bold ? " bold" : "");
        el.textContent = b.text;
        if (b.color) el.style.color = b.color;
        break;
      case "lines":
        el = document.createElement("div");
        el.className = "p-lines" + (b.mono ? " mono" : "");
        b.rows.forEach(([text, c, value]) => {
          const line = document.createElement(b.click && value != null ? "button" : "div");
          line.className = "p-line" + (b.click && value != null ? " click" : "");
          line.textContent = text || " ";
          if (c) line.style.color = c;
          if (b.click && value != null) line.onclick = () => send(b.click, value);
          el.appendChild(line);
        });
        break;
      case "toggle":
        el = document.createElement("label");
        el.className = "row p-toggle";
        el.innerHTML = `<div class="label"><span></span>${b.hint ? '<span class="hint"></span>' : ""}</div>
          <span class="switch"><input type="checkbox" ${b.on ? "checked" : ""} ${b.off ? "disabled" : ""}><span></span></span>`;
        el.querySelector(".label span").textContent = b.label;
        if (b.hint) el.querySelector(".hint").textContent = b.hint;
        el.querySelector("input").onchange = (e) => send(b.id, e.target.checked);
        break;
      case "choice":
        if (b.style === "select") {
          const s = document.createElement("select");
          b.options.forEach(([v, t]) => { const o = document.createElement("option"); o.value = JSON.stringify(v); o.textContent = t; o.selected = v === b.value; s.appendChild(o); });
          s.onchange = () => send(b.id, JSON.parse(s.value));
          el = labelled(b.label, s);
        } else {
          const g = document.createElement("div");
          g.className = "p-seg";
          b.options.forEach(([v, t]) => {
            const o = document.createElement("button");
            o.className = "btn seg" + (v === b.value ? " on" : "");
            o.textContent = t;
            o.onclick = () => send(b.id, v);
            g.appendChild(o);
          });
          el = labelled(b.label, g);
        }
        break;
      case "button":
        el = document.createElement("button");
        el.className = "btn" + (b.primary ? " primary" : "");
        el.textContent = b.label;
        el.disabled = !!b.off;
        el.onclick = () => { if (!b.confirm || confirm(b.confirm)) send(b.id, b.value); };
        break;
      case "slider": {
        const w = document.createElement("span");
        w.className = "p-slider";
        w.innerHTML = `<input type="range" min="${b.min}" max="${b.max}" step="${b.step}" value="${b.value}"><b></b>`;
        const show = () => { w.lastChild.textContent = w.firstChild.value + (b.unit || ""); };
        show();
        w.firstChild.oninput = show;
        w.firstChild.onchange = () => send(b.id, +w.firstChild.value);
        el = labelled(b.label, w);
        break;
      }
      case "number": {
        const i = document.createElement("input");
        i.type = "number"; i.className = "m-input m-spin";
        Object.assign(i, { min: b.min, max: b.max, step: b.step, value: b.value });
        i.onchange = () => send(b.id, +i.value);
        el = labelled(b.label, i, b.unit);
        break;
      }
      case "input": {
        const i = document.createElement("input");
        i.className = "m-input";
        i.value = b.value || "";
        i.placeholder = b.placeholder || "";
        if (b.width) i.style.width = b.width + "px";
        i.oninput = () => { clearTimeout(timers[b.id]); timers[b.id] = setTimeout(() => send(b.id, i.value), 150); };
        el = labelled(b.label, i);
        break;
      }
      case "list":
        el = document.createElement("div");
        el.className = "m-list" + (b.mono ? " mono" : "");
        el.style.maxHeight = Math.max(3, b.height) * 21 + 8 + "px";
        b.rows.forEach(([text, c], i) => {
          const r = document.createElement("div");
          r.className = "m-item" + (b.sel === i ? " sel" : "");
          r.textContent = text || " ";
          if (c) r.style.color = c;
          r.onclick = () => send(b.id, i);
          if (b.dbl) r.ondblclick = () => send(b.id + "_open", i);
          el.appendChild(r);
        });
        if (!b.rows.length) { const r = document.createElement("div"); r.className = "m-item muted"; r.textContent = b.empty; el.appendChild(r); }
        break;
      case "row": case "col":
        el = document.createElement("div");
        el.className = b.t === "row" ? "m-row" : "m-col";
        b.kids.forEach((k) => el.appendChild(build(k)));
        break;
      case "stats":
        el = document.createElement("div");
        el.className = "cards";
        b.cards.forEach(([big, small]) => {
          const c = document.createElement("div");
          c.className = "card stat";
          c.innerHTML = "<b></b><span class='muted'></span>";
          c.firstChild.textContent = big;
          c.lastChild.textContent = small;
          el.appendChild(c);
        });
        break;
      case "image":
        el = document.createElement("img");
        el.className = "m-canvas";
        Object.assign(el, { src: b.src, width: b.w, height: b.h });
        break;
      case "canvas":
        el = document.createElement("canvas");
        el.className = "m-canvas";
        draw(el, b);
        break;
      default:
        el = document.createElement("span");
    }
    el.dataset.key = JSON.stringify(b);
    el.dataset.t = b.t;
    return el;
  }

  // ---- patching by position: unchanged blocks stay; the input being typed in stays
  function patch(el, b) {
    if (!el) return null;
    const key = JSON.stringify(b);
    if (el.dataset.key === key) return el;
    if ((b.t === "row" || b.t === "col") && el.dataset.t === b.t && el.children.length === b.kids.length) {
      [...el.children].forEach((c, i) => patch(c, b.kids[i]));
      el.dataset.key = key;
      return el;
    }
    if (b.t === "canvas" && el.dataset.t === "canvas") { draw(el, b); el.dataset.key = key; return el; }
    if (el.contains(document.activeElement) && el.dataset.t === b.t && (b.t === "input" || b.t === "number" || b.t === "slider")) return el;
    const next = build(b);
    const top = el.scrollTop;
    el.replaceWith(next);
    if (b.t === "list") next.scrollTop = top;
    return next;
  }
  function render(blocks) {
    const kids = [...host.children];
    if (kids.length !== blocks.length || kids.some((k) => !k.dataset.t)) {
      host.innerHTML = "";
      blocks.forEach((b) => host.appendChild(build(b)));
    } else blocks.forEach((b, i) => patch(kids[i], b));
  }

  // ---- display list -> <canvas>
  const ANCHOR = { n: ["center", "top"], ne: ["right", "top"], e: ["right", "middle"], se: ["right", "bottom"],
    s: ["center", "bottom"], sw: ["left", "bottom"], w: ["left", "middle"], nw: ["left", "top"], center: ["center", "middle"] };
  function draw(cv, n) {
    const r = window.devicePixelRatio || 1;
    if (cv.width !== n.w * r) { cv.width = n.w * r; cv.height = n.h * r; cv.style.width = n.w + "px"; cv.style.height = n.h + "px"; }
    const g = cv.getContext("2d");
    g.setTransform(r, 0, 0, r, 0, 0);
    g.fillStyle = n.bg || "#1d2027";
    g.fillRect(0, 0, n.w, n.h);
    for (const [kind, c, o] of n.items) {
      g.setLineDash(o.dash ? String(o.dash).split(/[\s,()]+/).filter(Boolean).map(Number) : []);
      g.lineWidth = +o.width || 1;
      g.lineCap = o.capstyle === "round" ? "round" : "butt";
      g.beginPath();
      if (kind === "line" || kind === "polygon") {
        g.moveTo(c[0], c[1]);
        for (let i = 2; i < c.length; i += 2) g.lineTo(c[i], c[i + 1]);
        if (kind === "polygon") { g.closePath(); if (o.fill) { g.fillStyle = o.fill; g.fill(); } if (o.outline) { g.strokeStyle = o.outline; g.stroke(); } }
        else { g.strokeStyle = o.fill || "#000"; g.stroke(); }
      } else if (kind === "oval" || kind === "rectangle" || kind === "arc") {
        const [x0, y0, x1, y1] = c, cx = (x0 + x1) / 2, cy = (y0 + y1) / 2, rx = Math.abs(x1 - x0) / 2, ry = Math.abs(y1 - y0) / 2;
        if (kind === "rectangle") g.rect(x0, y0, x1 - x0, y1 - y0);
        else if (kind === "oval") g.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2);
        else {
          const a0 = -(+o.start || 0) * Math.PI / 180, a1 = -((+o.start || 0) + (+o.extent || 0)) * Math.PI / 180;
          if (o.style !== "arc" && o.style !== "chord") g.moveTo(cx, cy);
          g.ellipse(cx, cy, rx, ry, 0, a0, a1, (+o.extent || 0) > 0);
          if (o.style !== "arc") g.closePath();
        }
        if (o.fill && !(kind === "arc" && o.style === "arc")) { g.fillStyle = o.fill; g.fill(); }
        if (o.outline) { g.strokeStyle = o.outline; g.stroke(); }
      } else if (kind === "text") {
        const [size, bold, mono] = o.font || [10, false, false];
        g.font = `${bold ? "bold " : ""}${Math.round(size * 4 / 3)}px ${mono ? "Consolas, monospace" : '"Segoe UI", sans-serif'}`;
        const [align, base] = ANCHOR[o.anchor || "center"];
        g.textAlign = align;
        const rows = String(o.text).split("\n"), lh = Math.round(size * 4 / 3 * 1.2);
        const y0 = base === "top" ? c[1] : base === "middle" ? c[1] - (rows.length - 1) * lh / 2 : c[1] - (rows.length - 1) * lh;
        g.textBaseline = base;
        g.fillStyle = o.fill || "#000";
        rows.forEach((line, i) => g.fillText(line, c[0], y0 + i * lh));
      }
    }
  }

  return {
    show(el, feature, tab) {
      host = el;
      ctx = { feature, tab };
      host.innerHTML = '<p class="muted">Loading…</p>';
      this.poll(true);
    },
    stop() { host = null; ctx = null; },
    async poll(force) {
      if (!host || !ctx || (busy && !force)) return;
      busy = true;
      try {
        const want = ctx;
        const r = await fetch(`/api/page?feature=${encodeURIComponent(want.feature)}&tab=${encodeURIComponent(want.tab)}`, { headers: { "X-Token": TOKEN } });
        if (!r.ok) throw new Error(await r.text());
        const data = await r.json();
        if (host && ctx === want) render(data.blocks);
      } catch (e) {
        if (host) host.innerHTML = `<p class="warn">Couldn't load this page: ${esc(e.message)}</p>`;
      } finally { busy = false; }
    },
  };
})();
setInterval(() => FeaturePage.poll(false), 500);
