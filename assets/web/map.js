// The World map (daevaradar/worldmap.py), in its own window (?solo=map): the map images, every pet's soul monster
// spawns by genus, hidden cubes and gathering nodes as layers, highlighted (= watched in Gathering) resources, and you.
"use strict";
const WorldMapPage = (() => {
  const MAP_PX = 4096, TILE = 256, MAX_ZOOM = 4;
  const GENUS = { Cogni: ["#4fa3ff", "◉"], Fera: ["#ff5c5c", "▼"], Natura: ["#7cf08a", "❦"], Varian: ["#ffd24a", "◆"], Special: ["#c58cff", "★"] };
  const LAYERS = [["Pets", "Pets", "#ff9ad9"], ["Cube", "Hidden cube", "#c58cff"], ["Ore", "Ore", "#b0b8c8"], ["Gem", "Gems", "#ff9ad9"],
    ["Odyle", "Odyle", "#4fd8ff"], ["Herb", "Herbs", "#9be37c"], ["Log", "Wood", "#c8955a"], ["Ingredient", "Ingredients", "#ffb347"]];
  const LAYER_COLOR = Object.fromEntries(LAYERS.map(([k, , c]) => [k, c]));
  let data = null, live = null, cfg = null, mapKey = null, view = null, selected = null, search = "";
  let focusSeen = null, drawn = [], dirty = true, canvas, ctx, fitted = false;
  const tiles = new Map();
  const $ = (id) => document.getElementById(id);
  const get = (path) => fetch(path, { headers: { "X-Token": TOKEN } }).then((r) => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); });
  const post = (action, args) => fetch("/api/action", { method: "POST", headers: { "X-Token": TOKEN, "Content-Type": "application/json" },
    body: JSON.stringify({ action, args }) }).then((r) => r.json());
  let saveTimer = null;
  function saveCfg() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => post("map_cfg", { values: { layers: cfg.layers, genus: cfg.genus, unfinished: cfg.unfinished, not_home: cfg.not_home, mode: cfg.mode, map: mapKey } }), 200);
  }
  const on = (layer) => cfg.layers.includes(layer);

  // ---- the page
  function build() {
    document.documentElement.classList.add("solo");
    document.querySelector(".titlebar h1").textContent = "World map";
    document.title = "Daeva Radar · World map";
    $("content").innerHTML = `<div class="wm">
      <div class="wm-top">
        <select id="wm-map"></select>
        <select id="wm-genus"><option>All types</option>${Object.keys(GENUS).slice(0, 4).map((g) => `<option>${g}</option>`).join("")}</select>
        <input id="wm-search" class="m-input" placeholder="Search pets, monsters, resources">
        <label class="switch-label">Unfinished pets only <span class="switch"><input type="checkbox" id="wm-unfinished"><span></span></span></label>
        <label class="switch-label" id="wm-nothome-label"><span id="wm-nothome-text">Not farmable at home</span> <span class="switch"><input type="checkbox" id="wm-nothome"><span></span></span></label>
      </div>
      <div class="wm-layers" id="wm-layers"></div>
      <div class="wm-body">
        <div class="wm-stage" id="wm-stage"><canvas id="wm-canvas"></canvas>
          <div class="wm-zoom"><button data-z="in" title="Zoom in">+</button><button data-z="out" title="Zoom out">−</button>
            <button data-z="fit" title="Fit">⤢</button><button data-z="me" title="Centre on you">⌖</button></div>
          <div class="wm-scale" id="wm-scale"></div></div>
        <aside class="wm-side">
          <div class="tabs" id="wm-modes"><button class="tab" data-mode="Pets">Pets</button><button class="tab" data-mode="Resources">Resources</button></div>
          <div class="wm-head" id="wm-head"></div>
          <div class="m-list mono wm-list" id="wm-list"></div>
          <button class="btn" id="wm-act"></button>
          <div class="wm-info" id="wm-info"></div>
        </aside>
      </div>
      <div class="wm-bottom"><span id="wm-legend"></span><span id="wm-totals"></span></div>
      <div class="wm-credit">AION 2 data and map art © NCSOFT (official AION 2 web API), via Shugo.GG and metabot.gg ·
        wheel: zoom · drag: pan · click a point · double-click a row: target / highlight</div>
    </div>`;
    $("wm-legend").innerHTML = Object.entries(GENUS).map(([g, [c, i]]) => `<b style="color:${c}">${i} ${g}</b>`).join("");
    canvas = $("wm-canvas");
    ctx = canvas.getContext("2d");
    $("wm-map").onchange = (e) => loadMap(e.target.value);
    $("wm-genus").onchange = (e) => { cfg.genus = e.target.value; changed(); };
    $("wm-search").oninput = (e) => { search = e.target.value.trim().toLowerCase(); changed(); };
    $("wm-unfinished").onchange = (e) => { cfg.unfinished = e.target.checked; changed(); };
    $("wm-nothome").onchange = (e) => { cfg.not_home = e.target.checked; changed(); };
    $("wm-modes").onclick = (e) => { const b = e.target.closest("[data-mode]"); if (b) setMode(b.dataset.mode); };
    $("wm-act").onclick = toggleSelected;
    document.querySelector(".wm-zoom").onclick = (e) => {
      const z = e.target.closest("[data-z]");
      if (!z) return;
      if (z.dataset.z === "in") zoomAt(null, 1.5); else if (z.dataset.z === "out") zoomAt(null, 1 / 1.5);
      else if (z.dataset.z === "fit") fit(); else centerMe();
    };
    pointer();
    new ResizeObserver(() => { dirty = true; if (!fitted) fit(); }).observe($("wm-stage"));
  }
  function changed() { saveCfg(); side(); dirty = true; }
  function setMode(mode) {
    if (mode !== cfg.mode) {
      selected = null;
      const pets = cfg.layers.filter((l) => l !== "Pets");
      cfg.layers = mode === "Pets" ? pets.concat("Pets") : pets;
      if (mode === "Resources" && !data.groups.some(on) && !live.watch.length) cfg.layers = cfg.layers.concat(data.groups);
    }
    cfg.mode = mode;
    changed();
  }
  function layersBar() {
    $("wm-layers").innerHTML = LAYERS.map(([k, label, c]) => `<button class="btn seg wm-layer ${on(k) ? "on" : ""}" data-layer="${k}" style="--c:${c}">
      ${k === "Pets" ? "❀" : "●"} ${label}</button>`).join("");
    $("wm-layers").onclick = (e) => {
      const b = e.target.closest("[data-layer]");
      if (!b) return;
      const k = b.dataset.layer;
      cfg.layers = on(k) ? cfg.layers.filter((l) => l !== k) : cfg.layers.concat(k);
      layersBar();
      changed();
    };
  }

  // ---- what's shown
  const progress = (pid) => (live.progress[pid] || [0, 0, 0, 0]);
  const isTarget = (pid) => live.targets.includes(pid);
  function petsHere() {
    if (!data) return [];
    return data.pets.filter((p) => {
      if (cfg.genus !== "All types" && p.genus !== cfg.genus) return false;
      if (cfg.unfinished && progress(p.id)[0] >= live.max_level) return false;
      if (notHome() && (p.farm || []).includes(live.home.map)) return false;  // you can farm it on your own map
      if (!search) return true;
      return p.name.toLowerCase().includes(search) || p.points.some(([, , t]) => ((data.monsters[t] || {}).name || "").toLowerCase().includes(search));
    });
  }
  // "Not farmable in <your map>": only while the playing character's home map is known
  const notHome = () => cfg.not_home && live && live.home && live.home.map;
  function homeSwitch() {
    const h = live && live.home, known = !!(h && h.map);
    $("wm-nothome-text").textContent = known ? `Not farmable in ${h.name}` : "Not farmable at home";
    $("wm-nothome").disabled = !known;
    $("wm-nothome-label").classList.toggle("muted", !known);
    $("wm-nothome-label").title = known
      ? `Only the pets whose souls ${h.char} can't farm in ${h.name}, its faction's land`
      : h ? `Set ${h.char}'s faction on Settings → Characters` : "Log in with a character first";
  }
  function resourcesHere() {
    return data ? data.resources.filter((r) => !search || r.name.toLowerCase().includes(search)) : [];
  }
  function highlightsShown() {
    if (cfg.mode === "Resources") return live.watch;
    const group = Object.fromEntries(data.resources.map((r) => [r.name, r.group]));
    return live.watch.filter((n) => on(group[n]));
  }
  const watchColor = (name) => live.watch_colors[name] || "#4fd8ff";

  // ---- side panel
  let rows = [];
  function side() {
    if (!data || !live) return;
    document.querySelectorAll("#wm-modes .tab").forEach((b) => b.classList.toggle("on", b.dataset.mode === cfg.mode));
    const list = $("wm-list"), top = list.scrollTop;
    let html = "";
    if (cfg.mode === "Pets") {
      const pets = on("Pets") ? petsHere() : [];
      pets.sort((a, b) => (isTarget(b.id) - isTarget(a.id)) || a.name.localeCompare(b.name));
      rows = pets.map((p) => p.id);
      $("wm-head").textContent = `${pets.length} pets · ${pets.reduce((n, p) => n + p.points.length, 0)} spawn points`;
      html = pets.map((p) => {
        const [level, souls, need] = progress(p.id), [c, i] = GENUS[p.genus] || ["#ccc", "•"];
        const state = level >= live.max_level ? "Lv3 ✓" : `Lv${level} ${souls}/${need}`;
        return `<div class="m-item ${p.id === selected ? "sel" : ""}" style="color:${c}">${isTarget(p.id) ? "★" : i} ${esc(p.name.padEnd(21).slice(0, 21))} ${state}</div>`;
      }).join("") || `<div class="m-item muted" style="white-space:normal">${!live.synced ? "Waiting for the pet collection."
        : notHome() && mapKey === live.home.map ? `This is your map: every pet here is farmable in ${esc(live.home.name)}.` : "No pets match."}</div>`;
      const maxed = pets.filter((p) => progress(p.id)[0] >= live.max_level).length;
      const left = pets.reduce((n, p) => n + live.soul_cap - progress(p.id)[3], 0);
      $("wm-totals").textContent = pets.length ? `Level 3: ${maxed}/${pets.length} · To finish: ${left} souls` : "";
      $("wm-act").textContent = "★ Target / untarget the selected pet";
    } else {
      const res = resourcesHere();
      res.sort((a, b) => (live.watch.includes(b.name) - live.watch.includes(a.name)));
      rows = res.map((r) => r.name);
      $("wm-head").textContent = `${res.length} resources · ${res.reduce((n, r) => n + r.points.length, 0)} nodes`;
      html = res.map((r) => {
        const lit = live.watch.includes(r.name);
        return `<div class="m-item ${r.name === selected ? "sel" : ""}" style="color:${lit ? watchColor(r.name) : LAYER_COLOR[r.group] || "#ccc"}">${lit ? "★" : "●"} ${esc(r.name.padEnd(22).slice(0, 22))} ${String(r.points.length).padStart(4)}</div>`;
      }).join("") || `<div class="m-item muted">${search ? "Nothing matches." : "No gathering data for this map."}</div>`;
      const here = live.watch.filter((n) => rows.includes(n));
      $("wm-totals").textContent = here.length ? `Highlighted: ${here.length} · ${here.reduce((n, name) => n + data.resources.find((r) => r.name === name).points.length, 0)} nodes on this map`
        : "Highlight resources: double-click them in the list";
      $("wm-act").textContent = "★ Highlight / unhighlight (= watch in Gathering)";
    }
    list.innerHTML = html;
    list.scrollTop = top;
    [...list.children].forEach((el, i) => {
      if (i >= rows.length) return;
      el.onclick = () => pick(rows[i], true);
      el.ondblclick = () => { pick(rows[i], false); toggleSelected(); };
    });
    $("wm-act").disabled = selected == null;
    info();
  }
  function pick(id, zoom) {
    selected = id;
    clicked = null;
    side();
    const pts = cfg.mode === "Pets" ? (data.pets.find((p) => p.id === id) || { points: [] }).points
      : (data.resources.find((r) => r.name === id) || { points: [] }).points;
    if (zoom && pts.length) fit(pts);
    dirty = true;
  }
  async function toggleSelected() {
    if (selected == null) return;
    await post(cfg.mode === "Pets" ? "map_target" : "map_watch", cfg.mode === "Pets" ? { pid: selected } : { name: selected });
    await poll();
  }
  let clicked = null;  // a point clicked on the map: its monster / "Hidden cube"
  function info() {
    const el = $("wm-info");
    if (clicked === "cube") { el.innerHTML = `<b style="color:${LAYER_COLOR.Cube}">Hidden cube spawn point</b>`; return; }
    if (selected == null) { el.textContent = cfg.mode === "Pets" ? "Select a pet (list or map) for its souls." : "Select a resource (list or map)."; return; }
    if (cfg.mode === "Pets") {
      const p = data.pets.find((x) => x.id === selected);
      if (!p) { el.textContent = ""; return; }
      const [level, souls, need, done] = progress(p.id), c = (GENUS[p.genus] || ["#ccc"])[0];
      const monster = clicked && data.monsters[clicked];
      el.innerHTML = (monster ? `<div>${esc(monster.name)} (L${monster.level}) · ${monster.count} spawn points here</div>` : "") +
        `<b style="color:${c}">${esc(p.name)}</b> <span class="muted">${p.genus}${isTarget(p.id) ? " · ★ target" : ""}</span><br>` +
        (level >= live.max_level ? "Lv3 reached" : `Lv${level} · ${souls}/${need} to Lv${level + 1} · ${done}/${live.soul_cap} (${live.soul_cap - done} left)`) +
        p.souls.filter((s) => !clicked || s.types.includes(+clicked)).slice(0, 4).map((s) => `<div class="muted">${esc(s.monster)} L${s.level} · ${s.chance.toFixed(1)}%</div>`).join("");
    } else {
      const r = data.resources.find((x) => x.name === selected);
      if (!r) { el.textContent = ""; return; }
      const lit = live.watch.includes(r.name);
      el.innerHTML = `<b style="color:${lit ? watchColor(r.name) : LAYER_COLOR[r.group] || "#ccc"}">${esc(r.name)}</b> <span class="muted">${r.group}${lit ? " · ★ highlighted" : ""}</span><br>
        ${r.points.length} nodes on this map${r.elsewhere ? `<div class="muted">Also: ${esc(r.elsewhere)}</div>` : ""}`;
    }
  }

  // ---- drawing
  const W = () => canvas.clientWidth, H = () => canvas.clientHeight;
  const toScreen = (x, y) => [(x - view.cx) * view.s + W() / 2, (y - view.cy) * view.s + H() / 2];
  const toMap = (sx, sy) => [(sx - W() / 2) / view.s + view.cx, (sy - H() / 2) / view.s + view.cy];
  function tile(z, x, y) {
    const url = `/tiles/${mapKey}/${z}/${x}_${y}.webp`;
    let t = tiles.get(url);
    if (!t) {
      t = { img: new Image(), ok: false, bad: false };
      t.img.onload = () => { t.ok = true; dirty = true; };
      t.img.onerror = () => { t.bad = true; };
      t.img.src = url;
      tiles.set(url, t);
    }
    return t;
  }
  function draw() {
    if (!view || !data || !live) return;
    const r = window.devicePixelRatio || 1, w = W(), h = H();
    if (canvas.width !== Math.round(w * r) || canvas.height !== Math.round(h * r)) { canvas.width = Math.round(w * r); canvas.height = Math.round(h * r); }
    ctx.setTransform(r, 0, 0, r, 0, 0);
    ctx.fillStyle = "#0b0c0f";
    ctx.fillRect(0, 0, w, h);
    // tiles: the coarsest zoom level whose tiles aren't stretched more than 1.5x; coarser ones under them while loading
    const zWant = Math.max(0, Math.min(MAX_ZOOM, Math.ceil(Math.log2(Math.max(1e-6, view.s * MAP_PX / TILE / 1.5)))));
    const [x0, y0] = toMap(0, 0), [x1, y1] = toMap(w, h);
    for (const z of zWant > 0 ? [zWant - 1, zWant] : [0]) {
      const span = MAP_PX / 2 ** z;
      for (let tx = Math.max(0, Math.floor(x0 / span)); tx < Math.min(2 ** z, Math.floor(x1 / span) + 1); tx++)
        for (let ty = Math.max(0, Math.floor(y0 / span)); ty < Math.min(2 ** z, Math.floor(y1 / span) + 1); ty++) {
          const t = tile(z, tx, ty);
          if (!t.ok) continue;
          const [sx, sy] = toScreen(tx * span, ty * span), [ex, ey] = toScreen((tx + 1) * span, (ty + 1) * span);
          ctx.drawImage(t.img, Math.floor(sx), Math.floor(sy), Math.ceil(ex - sx) + 1, Math.ceil(ey - sy) + 1);
        }
    }
    drawn = [];
    const pad = 6 / view.s, vis = (x, y) => x >= x0 - pad && x <= x1 + pad && y >= y0 - pad && y <= y1 + pad;
    const sq = (x, y, rr, fill, stroke, lw) => { ctx.fillStyle = fill; ctx.fillRect(x - rr, y - rr, rr * 2, rr * 2); ctx.lineWidth = lw || 1; ctx.strokeStyle = stroke; ctx.strokeRect(x - rr, y - rr, rr * 2, rr * 2); };
    const rad = view.s < 0.3 ? 2 : 3;
    if (on("Cube")) for (const [x, y] of data.cubes) if (vis(x, y)) { const [sx, sy] = toScreen(x, y); sq(sx, sy, rad, LAYER_COLOR.Cube, "#000"); drawn.push([sx, sy, "cube"]); }
    const lit = highlightsShown();
    for (const res of data.resources) {
      if (lit.includes(res.name) || !on(res.group)) continue;
      for (const [x, y] of res.points) if (vis(x, y)) { const [sx, sy] = toScreen(x, y); sq(sx, sy, rad, LAYER_COLOR[res.group] || "#ccc", "#000"); drawn.push([sx, sy, "res", res.name]); }
    }
    if (on("Pets")) {
      const pr = view.s < 0.3 ? 3 : view.s < 1 ? 4 : 5, chosen = cfg.mode === "Pets" ? selected : null;
      const pets = petsHere().sort((a, b) => ((a.id === chosen) - (b.id === chosen)) || (isTarget(a.id) - isTarget(b.id)));
      for (const p of pets) {
        const c = (GENUS[p.genus] || ["#ccc"])[0], big = pr + (p.id === chosen || isTarget(p.id) ? 2 : 0);
        for (const [x, y, t] of p.points) {
          if (!vis(x, y)) continue;
          const [sx, sy] = toScreen(x, y);
          ctx.beginPath(); ctx.arc(sx, sy, big, 0, Math.PI * 2); ctx.fillStyle = c; ctx.fill();
          ctx.lineWidth = p.id === chosen || isTarget(p.id) ? 2 : 1; ctx.strokeStyle = p.id === chosen ? "#fff" : "#000"; ctx.stroke();
          drawn.push([sx, sy, "pet", p.id, t]);
        }
      }
    }
    const chosen = cfg.mode === "Resources" ? selected : null;
    const show = [...new Set(lit.concat(chosen && data.resources.some((r) => r.name === chosen) ? [chosen] : []))].sort((a, b) => (a === chosen) - (b === chosen));
    for (const name of show) {
      const res = data.resources.find((r) => r.name === name);
      if (!res) continue;
      const c = live.watch.includes(name) ? watchColor(name) : LAYER_COLOR[res.group] || "#4fd8ff";
      for (const [x, y] of res.points) if (vis(x, y)) { const [sx, sy] = toScreen(x, y); sq(sx, sy, rad + 2, c, name === chosen ? "#fff" : "#000", 2); drawn.push([sx, sy, "res", name]); }
    }
    if (live.position && (live.current == null || live.current === mapKey)) {  // you
      const [sx, sy] = toScreen(live.position[0], live.position[1]);
      ctx.beginPath(); ctx.arc(sx, sy, 9, 0, Math.PI * 2); ctx.lineWidth = 2; ctx.strokeStyle = "#2fd0b5"; ctx.stroke();
      ctx.beginPath(); ctx.arc(sx, sy, 7, 0, Math.PI * 2); ctx.fillStyle = "#000"; ctx.fill();
      ctx.beginPath(); ctx.arc(sx, sy, 5, 0, Math.PI * 2); ctx.fillStyle = "#fff"; ctx.fill();
    }
    $("wm-scale").textContent = `${Math.round(w / view.s * 2)} m across`;
  }
  function loop() { if (dirty) { dirty = false; draw(); } requestAnimationFrame(loop); }

  // ---- navigation
  function fit(points) {
    if (!data || W() < 50) return;
    let pts = points;
    if (!pts) {
      if (cfg.mode === "Pets" && on("Pets")) pts = petsHere().flatMap((p) => p.points);
      else pts = data.resources.filter((r) => live.watch.includes(r.name)).flatMap((r) => r.points);
      if (!pts.length) pts = data.pets.flatMap((p) => p.points).concat(data.resources.flatMap((r) => r.points), data.cubes);
      if (!pts.length) pts = [[0, 0], [MAP_PX, MAP_PX]];
    }
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const sx = Math.max(Math.max(...xs) - Math.min(...xs), 60), sy = Math.max(Math.max(...ys) - Math.min(...ys), 60);
    view = { cx: (Math.min(...xs) + Math.max(...xs)) / 2, cy: (Math.min(...ys) + Math.max(...ys)) / 2, s: Math.min(W() / sx, H() / sy) * 0.85 };
    fitted = true;
    dirty = true;
  }
  function centerMe() {
    if (!live.position) return;
    const here = live.current || mapKey;
    if (here !== mapKey) return loadMap(here).then(centerMe);
    view = { cx: live.position[0], cy: live.position[1], s: Math.max(view.s, 1) };
    dirty = true;
  }
  function zoomAt(e, factor) {
    const sx = e ? e.offsetX : W() / 2, sy = e ? e.offsetY : H() / 2, [px, py] = toMap(sx, sy);
    view.s = Math.max(0.05, Math.min(4, view.s * factor));
    view.cx = px - (sx - W() / 2) / view.s;
    view.cy = py - (sy - H() / 2) / view.s;
    dirty = true;
  }
  function pointer() {
    let down = null;
    canvas.addEventListener("wheel", (e) => { e.preventDefault(); zoomAt(e, e.deltaY < 0 ? 1.25 : 1 / 1.25); }, { passive: false });
    canvas.addEventListener("pointerdown", (e) => { down = { x: e.offsetX, y: e.offsetY, lx: e.offsetX, ly: e.offsetY }; canvas.setPointerCapture(e.pointerId); });
    canvas.addEventListener("pointermove", (e) => {
      if (!down) return;
      view.cx -= (e.offsetX - down.lx) / view.s;
      view.cy -= (e.offsetY - down.ly) / view.s;
      down.lx = e.offsetX; down.ly = e.offsetY;
      dirty = true;
    });
    canvas.addEventListener("pointerup", (e) => {
      if (down && Math.hypot(e.offsetX - down.x, e.offsetY - down.y) < 4) click(e.offsetX, e.offsetY);
      down = null;
    });
  }
  function click(x, y) {
    let best = null;
    for (const d of drawn) { const dist = Math.hypot(d[0] - x, d[1] - y); if (dist <= 9 && (!best || dist < best[0])) best = [dist, d]; }
    if (!best) return;
    const d = best[1];
    if (d[2] === "cube") { clicked = "cube"; info(); return; }
    if (d[2] === "res") { if (cfg.mode !== "Resources") setMode("Resources"); clicked = null; selected = d[3]; }
    else { if (cfg.mode !== "Pets") setMode("Pets"); clicked = String(d[4]); selected = d[3]; }
    side();
    dirty = true;
  }

  // ---- loading and the live part
  async function loadMap(key) {
    data = await get("/api/map/data?map=" + encodeURIComponent(key));
    for (const [g, c] of Object.entries(data.genus_colors || {})) if (GENUS[g]) GENUS[g][0] = c;  // the app's colours
    $("wm-legend").innerHTML = Object.entries(GENUS).map(([g, [c, i]]) => `<b style="color:${c}">${i} ${g}</b>`).join("");
    mapKey = key;
    selected = null; clicked = null; fitted = false;
    $("wm-map").innerHTML = data.keys.map(([k, n]) => `<option value="${k}" ${k === key ? "selected" : ""}>${esc(n)}</option>`).join("");
    saveCfg();
    side();
    fit();
  }
  async function poll() {
    try {
      const next = await get("/api/map/state");
      if (focusSeen !== null && next.focus !== focusSeen) {  // opened again (Map ↗): come forward, take its side
        if (window.pywebview && window.pywebview.api.focus) window.pywebview.api.focus();
        if (next.cfg.mode !== cfg.mode) { live = next; setMode(next.cfg.mode); }
      }
      if (live && next.topmost !== live.topmost && window.pywebview && window.pywebview.api.on_top) window.pywebview.api.on_top(next.topmost);
      focusSeen = next.focus;
      const before = live && JSON.stringify([live.progress, live.targets, live.watch, live.home]);
      live = next;
      homeSwitch();
      if (before !== JSON.stringify([live.progress, live.targets, live.watch, live.home])) side();
      dirty = true;
    } catch (e) {
      $("wm-totals").textContent = "Daeva Radar isn't running (" + e.message + ").";
    }
  }
  async function start() {
    build();
    live = await get("/api/map/state");
    focusSeen = live.focus;
    cfg = { ...live.cfg };
    $("wm-genus").value = cfg.genus;
    $("wm-unfinished").checked = cfg.unfinished;
    $("wm-nothome").checked = !!cfg.not_home;
    homeSwitch();
    layersBar();
    await loadMap(live.initial);
    requestAnimationFrame(loop);
    setInterval(poll, 1000);
  }
  return { start };
})();
if (SOLO === "map") WorldMapPage.start();
