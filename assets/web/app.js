// Daeva Radar Settings: talks to the radar's local server (webui.py). The token comes from this page's URL.
"use strict";
const TOKEN = new URLSearchParams(location.search).get("t") || "";
const SOLO = new URLSearchParams(location.search).get("solo");  // "map": this window is the World map (map.js)
let state = null, page = null, focusSeen = null;
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private window */ } },
};
{
  const [p, t] = decodeURIComponent(location.hash.slice(1)).split("/");  // #Page or #Feature/Tab
  page = p || store.get("page", "General");
  if (p && t) store.set("tab:" + p, t);
}

async function getState() {
  const r = await fetch("/api/state", { headers: { "X-Token": TOKEN } });
  if (!r.ok) throw new Error("HTTP " + r.status);
  return r.json();
}
async function act(action, args) {
  const r = await fetch("/api/action", {
    method: "POST", headers: { "X-Token": TOKEN, "Content-Type": "application/json" },
    body: JSON.stringify({ action, args: args || {} }),
  });
  const out = await r.json();
  if (!out.ok) console.warn(action, out.error);
  await refresh();
  return out.result;
}

// ---- icons (simple line icons)
const P = {
  General: '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
  Cubes: '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M12 12l8-4.5M12 12v9M12 12L4 7.5"/>',
  Feathers: '<path d="M20 4c-6 0-12 4-13 13l-3 3"/><path d="M20 4c0 7-5 12-13 13M10 14h5"/>',
  Monsters: '<path d="M5 11a7 7 0 0 1 14 0v4l-2 2v3h-3v-2h-4v2H7v-3l-2-2z"/><circle cx="9" cy="12" r="1.3"/><circle cx="15" cy="12" r="1.3"/>',
  Gathering: '<path d="M14 4l6 6M17 7L7 17M4 20l3-3"/><path d="M12 4c3 0 6 1 8 3"/>',
  Wardrobe: '<path d="M8 3l4 3 4-3 5 4-3 3v10H6V10L3 7z"/>',
  Shop: '<path d="M4 8h16l-1.5 12h-13z"/><path d="M9 8V6a3 3 0 0 1 6 0v2"/>',
  Alerts: '<path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4z"/><path d="M10 21h4"/>',
  "View cone": '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  Layout: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M10 10v10"/>',
  About: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
  Characters: '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-7 8-7s8 3 8 7"/>',
  Checklist: '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8l1.5 1.5L12 7M8 14l1.5 1.5L12 13M14 8h3M14 14h3"/>',
};
const icon = (name) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${P[name] || P.About}</svg>`;
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function toggle(checked, onchange, disabled) {
  const id = "t" + Math.random().toString(36).slice(2);
  handlers[id] = onchange;
  return `<label class="switch"><input type="checkbox" data-h="${id}" ${checked ? "checked" : ""} ${disabled ? "disabled" : ""}><span></span></label>`;
}
function btn(text, onclick, cls, disabled) {
  const id = "b" + Math.random().toString(36).slice(2);
  handlers[id] = onclick;
  return `<button class="btn ${cls || ""}" data-h="${id}" ${disabled ? "disabled" : ""}>${text}</button>`;
}
let handlers = {};
document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-h]");
  if (el && el.tagName === "BUTTON" && handlers[el.dataset.h]) handlers[el.dataset.h](e);
});
document.addEventListener("change", (e) => {
  const el = e.target.closest("[data-h]");
  if (el && handlers[el.dataset.h]) handlers[el.dataset.h](el.type === "checkbox" ? el.checked : el.value);
});

// ---- navigation
function pages() {
  return [["General"], ["Characters"], ["Checklist"], ...state.features.map((f) => [f.title, f]), ["Layout"], ["About"]];
}
function renderNav() {
  const nav = document.getElementById("nav");
  const item = (name, f) => `<button data-page="${esc(name)}" class="${name === page ? "active" : ""} ${f && !f.enabled ? "off" : ""}">
    ${icon(name)}<span>${esc(name === "Layout" ? "Overlay layout" : name)}</span>${f ? `<i class="dot" data-switch="${esc(f.title)}" title="${f.enabled ? "On: click to turn off" : "Off: click to turn on"}"></i>` : ""}</button>`;
  nav.innerHTML = item("General") + item("Characters") + item("Checklist") + '<div class="group">Features</div>' +
    state.features.map((f) => item(f.title, f)).join("") + '<div class="group">Overlays</div>' + item("Layout") +
    '<div class="group">Daeva Radar</div>' + item("About");
}
document.getElementById("nav").addEventListener("click", (e) => {
  const dot = e.target.closest("[data-switch]");
  if (dot) {  // the dot switches the feature on / off without opening its page
    const f = state.features.find((x) => x.title === dot.dataset.switch);
    if (f) act("set_feature", { title: f.title, on: !f.enabled });
    return;
  }
  const b = e.target.closest("[data-page]");
  if (!b) return;
  page = b.dataset.page;
  store.set("page", page);
  render(true);
});

// ---- pages
function general() {
  const s = state, u = s.update, g = s.general;
  const upd = {
    idle: "", checking: "Checking for updates…", current: `Up to date (checked ${u.checked || ""}).`,
    failed: "The update check failed.", available: `<b>${esc(u.release)}</b> is out.`,
    installing: `Updating to ${esc(u.release)}…`, install_failed: "The update failed.", installed: "Updated, restarting…",
  }[u.state] || "";
  const mine = s.version ? "v" + esc(s.version) : (s.git ? "a git checkout" : "version unknown");
  return `<h2>${icon("General")}General</h2>
  <div class="section">Map</div>
  <div class="row"><div class="label">Map you're on<span class="hint">${s.map.detected ? "Detected: " + esc(label(s.map.detected)) : "Not detected yet: Auto works it out from gathering nodes and cubes."}</span></div>
    <select data-h="${reg((v) => act("set_map", { key: v }))}">${s.map.choices.map(([k, t]) => `<option value="${esc(k)}" ${k === s.map.choice ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></div>
  <div class="section">Windows</div>
  <div class="row"><div class="label">Open Settings when Daeva Radar starts<span class="hint">Otherwise it starts in the tray: double-click the tray icon for Settings.</span></div>
    ${toggle(g.settings_at_start, (v) => act("set_settings_at_start", { on: v }))}</div>
  <div class="row"><div class="label">Keep the World map and Position debug on top</div>${toggle(g.topmost, (v) => act("set_topmost", { on: v }))}</div>
  <div class="row"><div class="label">World map<span class="hint">Pet soul spawns, hidden cubes and gathering nodes.</span></div>${btn("Open map", () => act("open_map"))}</div>
  <div class="section">Updates</div>
  <div class="row"><div class="label">This is ${mine}.<span class="hint">${upd}${s.git && u.state === "available" ? " Update it with git pull." : ""}</span></div>
    ${u.state === "available" && !s.git ? btn("Update and restart", () => confirm(`Update to ${u.release} now? The app restarts; your settings are kept.`) && act("install_update"), "primary") : ""}
    ${btn("Check now", () => act("check_updates"), "", ["checking", "installing", "installed"].includes(u.state))}</div>
  <div class="section">Troubleshooting</div>
  <div class="row"><div class="label">Position debug<span class="hint">Your position, its last packet and how often it updates.</span></div>${btn(g.debug ? "Close" : "Open", () => act("toggle_debug"))}</div>
  <div class="divider"></div>
  ${btn("Quit Daeva Radar", () => confirm("Quit Daeva Radar? Capture, overlays and alerts stop.") && act("quit_app"), "danger")}`;
}
let regs = 0;
function reg(fn) { const id = "r" + (regs++); handlers[id] = fn; return id; }
const label = (key) => (state.map.choices.find(([k]) => k === key) || [key, key])[1];

// A feature page: its header (On/Off, tabs) is redrawn with the state; the tab below is the feature's own page
// (page.js), refreshed on its own and left alone by those redraws.
function featureTabs(f) {
  return f.tabs.concat(state.overlays.some((o) => o.feature === f.title) ? ["Overlays"] : []);
}
function featureTab(f) {
  const tabs = featureTabs(f), saved = store.get("tab:" + f.title, tabs[0]);
  return tabs.includes(saved) ? saved : tabs[0];
}
function featureHead(f) {
  const tab = featureTab(f);
  return `<h2>${icon(f.title)}${esc(f.title)} <span style="margin-left:auto"></span>
      <span class="switch-label">${f.enabled ? "On" : "Off"}${toggle(f.enabled, (v) => act("set_feature", { title: f.title, on: v }))}</span></h2>
    <p class="lead">${f.enabled ? esc(f.status || "Running.") : "Off: nothing is tracked, alerted or drawn. Its settings can still be changed."}</p>
    <div class="tabs">${featureTabs(f).map((t) => `<button class="tab ${t === tab ? "on" : ""}" data-tab="${esc(t)}">${esc(t)}</button>`).join("")}</div>
    ${tab === "Overlays" ? state.overlays.filter((o) => o.feature === f.title).map((o) => overlayRow(o, true)).join("") : ""}`;
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-tab]");
  if (!b) return;
  store.set("tab:" + page, b.dataset.tab);
  render(true);
});
function overlayRow(o, detail) {
  const place = async () => { await act("edit_layout"); if (window.pywebview && window.pywebview.api.minimize) window.pywebview.api.minimize(); };
  let html = `<div class="row"><div class="label">${esc(o.title)}<span class="hint">${o.kind === "radar" ? "Sits on the game's minimap." : "Info card."} ${o.shown ? (o.locked ? "Locked: clicks go to the game." : "Unlocked: drag it to move.") : "Hidden."}</span></div>
    <span class="switch-label">Locked${toggle(o.locked, (v) => act("overlay_lock", { id: o.id, on: v }), !o.shown)}</span>
    <span class="switch-label">Shown${toggle(o.shown, (v) => act("overlay_show", { id: o.id, on: v }), !o.allowed)}</span>
    ${o.allowed ? btn("Place", place) : ""}</div>`;
  if (detail && o.kind === "radar") {
    html += `<p class="lead">${esc(o.text)} It keeps running whichever page is open. The game must be in borderless or windowed mode.</p>
    <div class="row"><div class="label">Scale<span class="hint">${o.across} m across. The mouse wheel on the unlocked overlay does the same.</span></div>
      <div class="btns">${btn("−", () => act("overlay_scale", { id: o.id, factor: 1 / 1.1 }))}${btn("+", () => act("overlay_scale", { id: o.id, factor: 1.1 }))}</div></div>
    <div class="row"><div class="label">Show the view cone on this overlay<span class="hint">Needs View cone on.</span></div>${toggle(o.cone, (v) => act("overlay_cone", { id: o.id, on: v }))}</div>
    <div class="row"><div class="label">Lost it off-screen?</div>${btn("Bring it here", () => act("overlay_recenter", { id: o.id }), "", !o.allowed)}</div>
    <div class="section">How to line it up</div>
    <p class="lead">1. Unlocked, drag the overlay so its crosshair sits on your arrow.<br>2. Drag its bottom-right corner to the minimap's size.<br>
    3. Mouse wheel (or − / + here) until its rings match the game's distances.<br>4. Lock it: right-click it, the switch here, or Ctrl+Shift+O (works in game).
    Locked, clicks go through to the game. Ctrl+Shift+O locks / unlocks every shown overlay.</p>`;
  }
  return html;
}

// ---- the characters seen logging in, and their faction
function characters() {
  const rows = state.characters;
  const side = (c, f) => btn(f, () => act("set_faction", { name: c.name, faction: c.faction === f ? null : f }),
    "seg faction " + f.toLowerCase() + (c.faction === f ? " on" : ""));
  const where = (c) => c.maps.length ? "seen in " + c.maps.map(label).join(", ") : "no map detected yet";
  return `<h2>${icon("Characters")}Characters</h2>
  <p class="lead">Every character Daeva Radar has seen log in (by name). Set each one's faction: Elyos or Asmodian
  (unset, it's guessed from the maps the character was detected on). Their keys, entries and currencies are on the
  <b>Checklist</b> page.</p>
  ${rows.length ? rows.map((c) => `<div class="row"><div class="label"><b>${esc(c.name)}</b>
      ${c.playing ? '<span class="badge on">playing now</span>' : ""}
      <span class="hint">${c.last ? "Last played " + esc(c.last) : "Seen before this page existed"} · ${esc(where(c))}${
        !c.faction && c.guess ? ` · looks <b>${esc(c.guess)}</b>` : ""}</span></div>
      <div class="btns">${side(c, "Elyos")}${side(c, "Asmodian")}</div>
      ${btn("Checklist", () => { store.set("checklist", c.name); page = "Checklist"; store.set("page", page); render(true); })}
      ${btn("Forget", () => confirm(`Forget ${c.name}? It comes back the next time it logs in.`) && act("forget_character", { name: c.name }), "danger")}
    </div>`).join("") : '<p class="muted">None yet: log in with Daeva Radar running.</p>'}`;
}

// ---- the checklist: each character's keys, entries and currencies, read from the game (like the game's own lists)
const RESET_HOUR = 9, WEEKLY_DAY = 3;  // daily at 09:00, weekly on Wednesday 09:00 (EU servers)
function lastReset(weekly, now = new Date()) {
  const r = new Date(now);
  r.setHours(RESET_HOUR, 0, 0, 0);
  if (weekly) r.setDate(r.getDate() - ((r.getDay() - WEEKLY_DAY + 7) % 7));
  if (r > now) r.setDate(r.getDate() - (weekly ? 7 : 1));
  return r;
}
function untilReset(weekly) {
  const next = lastReset(weekly), now = new Date();
  next.setDate(next.getDate() + (weekly ? 7 : 1));
  const s = Math.max(0, (next - now) / 1000), d = Math.floor(s / 86400), h = Math.floor(s / 3600) % 24, m = Math.floor(s / 60) % 60;
  return { text: d ? `${d}d ${h}h` : `${h}h${String(m).padStart(2, "0")}m`, when: next.toLocaleString("en-GB", { weekday: "short", hour: "2-digit", minute: "2-digit" }) };
}
function checklist() {
  const chars = state.characters.filter((c) => c.checklist && c.checklist.read);
  if (!chars.length) return `<h2>${icon("Checklist")}Checklist</h2><p class="lead">Nothing read yet: log in with a character while Daeva Radar runs. Its keys, entries and currencies are read from the game at login.</p>`;
  let pick = store.get("checklist", null);
  if (!chars.some((c) => c.name === pick)) pick = (chars.find((c) => c.playing) || chars[0]).name;
  const c = chars.find((x) => x.name === pick), k = c.checklist;
  const fmt = (n) => Number(n).toLocaleString("en-US");
  const hms = (ms) => { const s = Math.round(ms / 1000); return `${Math.floor(s / 3600)}h${String(Math.floor(s / 60) % 60).padStart(2, "0")}m${String(s % 60).padStart(2, "0")}s`; };
  const stale = (weekly) => !c.playing && k.read_at && k.read_at * 1000 < lastReset(weekly).getTime();
  const tag = (scope) => `<span class="cl-tag ${scope}">${scope === "server" ? "SERVER" : "CHAR"}</span>`;
  const amount = (r) => r.unit === "ms" ? `${hms(r.left)} / ${r.max / 3600000}h`
    : `${r.left}${r.bonus ? ` (+${fmt(r.bonus)})` : ""}${r.max ? ` / ${r.max}` : ""}`;
  const bar = (r) => r.max ? `<i class="cl-bar" style="--p:${Math.min(100, 100 * r.left / r.max)}%"></i>` : "";
  const tabs = chars.map((x) => `<button class="cl-char ${x.name === pick ? "on" : ""}" data-char="${esc(x.name)}">
      ${x.checklist.icon ? `<img src="/classes/${x.checklist.icon}.png" alt="">` : '<span class="cl-noicon"></span>'}
      <span><b>${esc(x.name)}</b>${x.playing ? ' <em>in game</em>' : ""}<small>${esc([x.checklist.cls, x.checklist.level && "Level " + x.checklist.level].filter(Boolean).join(" · ") || "class not read yet")}</small>
      ${x.checklist.energy ? `<span class="cl-energy" title="Odyle energy">◆ ${fmt(x.checklist.energy[0])}${x.checklist.energy[1] ? ` (+${fmt(x.checklist.energy[1])})` : ""}</span>` : ""}</span></button>`).join("");
  const stock = k.stock.map((r) => `<div class="cl-stock"><div class="cl-row-head"><b>${esc(r.name)}</b>${tag(r.scope)}<span class="cl-amount">${amount(r)}</span></div>${bar(r)}</div>`).join("");
  const rows = (list, weekly) => list.map((r) => {
    const done = r.left <= 0 && !r.bonus;  // bonus entries (the "+26") still count as left
    return `<div class="cl-row ${done ? "done" : ""}"><span class="cl-check">${done ? "✓" : ""}</span>
      <div class="cl-what"><b>${esc(r.name)}</b>${tag(r.scope)}<small>${esc(r.note || "")}</small></div>
      <div class="cl-right"><span class="cl-amount">${amount(r)}</span>${bar(r)}${done ? '<span class="cl-done">Done</span>' : ""}</div></div>`;
  }).join("") || '<p class="muted">Nothing decoded for this list yet.</p>';
  const panel = (title, list, weekly, sym) => {
    const reset = untilReset(weekly), doneN = list.filter((r) => r.left <= 0 && !r.bonus).length;
    return `<section class="cl-panel"><header><span class="cl-sym">${sym}</span><div><h3>${title} · ${esc(c.name)}</h3>
      <small>${doneN} of ${list.length} done · resets ${reset.when}${stale(weekly) ? ' · <span class="warn">read before this reset: refilled since</span>' : ""}</small></div>
      <span class="cl-timer">⏱ ${reset.text}</span></header>${rows(list, weekly)}</section>`;
  };
  return `<h2>${icon("Checklist")}Characters checklist</h2>
  <div class="cl-chars">${tabs}</div>
  <section class="cl-panel"><header><div><h3>Stock and keys</h3><small>Read from the game ${esc(k.read)}${c.playing ? " · live while you play" : ""}</small></div></header>
    <div class="cl-stocks">${stock || '<p class="muted">Not read yet.</p>'}</div></section>
  <div class="cl-two">${panel("Daily", k.daily, false, "☀")}${panel("Weekly", k.weekly, true, "▦")}</div>
  <section class="cl-panel"><header><div><h3>Currencies</h3></div></header>
    <div class="ck-money">${k.currencies.map(([n, v]) => `<span><small>${esc(n)}</small><b>${fmt(v)}</b></span>`).join("")}</div></section>
  <p class="muted cl-foot">Counts are what's left, read from the game when the character logs in and live while you play it. More of the game's lists (Abyss Corridors, Supply Requests, Daeva Pass…) will be added as they're decoded.</p>`;
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-char]");
  if (!b) return;
  store.set("checklist", b.dataset.char);
  render(true);
});

function about() {
  return `<h2>${icon("About")}About</h2>
  <p class="lead">Daeva Radar reads AION 2's network traffic on this PC to show what's around you. It never changes, sends or uploads anything.</p>
  <p class="lead warn">NCSOFT's rules prohibit third-party tools that read the game's traffic, even read-only ones. Use at your own risk.</p>
  <div class="section">Credits</div>
  <p class="lead">AION 2 game data and map art © NCSOFT (official AION 2 web API), collected via Shugo.GG and metabot.gg.
  Packet capture on Windows uses WinDivert (LGPLv3 / GPLv2). A fan-made tool, not affiliated with NCSOFT.</p>`;
}

// ---- overlay layout: placed on the real screen (layout.py: a grid over the game window)
function layout() {
  const startLayout = async () => {
    await act("edit_layout");
    if (window.pywebview && window.pywebview.api.minimize) window.pywebview.api.minimize();  // out of the way
  };
  return `<h2>${icon("Layout")}Overlay layout</h2>
  <p class="lead">Place every overlay and info card on the real screen: a see-through grid covers the game window
  (or your main screen if the game isn't running), everything shown is unlocked, and what you drag snaps to the grid.
  Hidden ones show as dashed boxes you can place too. <b>Done</b> or <b>Esc</b> locks them back as they were.</p>
  <div class="btns">${state.layout ? btn("Finish layout", () => act("edit_layout"), "primary") : btn("Edit layout over the game", startLayout, "primary")}
    ${btn("Lock all", () => act("lock_all", { on: true }))}${btn("Unlock all", () => act("lock_all", { on: false }))}</div>
  <p class="muted">Ctrl+Shift+O locks / unlocks every shown overlay in game. Also in the tray menu: Edit overlay layout.</p>
  <div class="section">Overlays</div>
  ${state.overlays.map((o) => overlayRow(o)).join("")}`;
}

// ---- render + poll
function render(fresh) {
  if (!state) return;
  handlers = {};
  regs = 0;
  if (!pages().some(([n]) => n === page)) page = "General";
  renderNav();
  const f = state.features.find((x) => x.title === page);
  const el = document.getElementById("content");
  const scroll = el.scrollTop;
  if (f) {
    const tab = featureTab(f), view = page + "/" + tab;
    if (fresh || el.dataset.view !== view) {
      el.dataset.view = view;
      el.innerHTML = '<div id="fhead"></div><div id="mhost" class="fpage"></div>';
      if (tab === "Overlays") FeaturePage.stop(); else FeaturePage.show(document.getElementById("mhost"), f.title, tab);
      if (fresh) el.scrollTop = 0;
    }
    document.getElementById("fhead").innerHTML = featureHead(f);
  } else {
    el.dataset.view = "";
    FeaturePage.stop();
    el.innerHTML = page === "General" ? general() : page === "Characters" ? characters() : page === "Checklist" ? checklist() : page === "Layout" ? layout() : about();
    el.scrollTop = scroll;
  }
  renderFooter();
}
function renderFooter() {
  const s = state.status;
  document.getElementById("who").innerHTML = s.who ? `Playing as <b>${esc(s.who)}</b>` : "Character not identified yet: relog or change zone";
  document.getElementById("version").textContent = state.version ? "v" + state.version : (state.git ? "git checkout" : "");
  document.getElementById("status").textContent = `${s.text} · ${s.messages} messages · ${s.packets} packets`;
}
let lastJson = "";
async function refresh() {
  try {
    const next = await getState();
    if (focusSeen !== null && next.focus !== focusSeen) {
      if (window.pywebview) window.pywebview.api.focus();
      if (next.goto && next.goto !== page) { page = next.goto; store.set("page", page); lastJson = ""; state = next; render(true); }
    }
    focusSeen = next.focus;
    const json = JSON.stringify({ ...next, status: null });
    state = next;
    const busy = document.activeElement && document.activeElement.tagName === "SELECT";
    if (json !== lastJson && !busy) { lastJson = json; render(); } else renderFooter();
  } catch (e) {
    document.getElementById("status").textContent = "Daeva Radar isn't running (" + e.message + ").";
  }
}
// ---- the window's own top bar and resize edges (the Settings window is frameless; not in a browser)
function windowControls() {
  const api = window.pywebview && window.pywebview.api;
  if (!api || !api.minimize) return;
  const root = document.documentElement;
  root.classList.add("framed");
  const maximize = async () => root.classList.toggle("maximized", await api.toggle_maximize());
  document.getElementById("win-min").onclick = () => api.minimize();
  document.getElementById("win-max").onclick = maximize;
  document.getElementById("win-close").onclick = () => api.close();
  document.getElementById("titlebar").addEventListener("dblclick", (e) => { if (!e.target.closest("button")) maximize(); });
  let resize = null;
  document.querySelectorAll(".edge").forEach((el) => el.addEventListener("pointerdown", async (e) => {
    e.preventDefault();
    el.setPointerCapture(e.pointerId);
    const side = [...el.classList].find((c) => c.startsWith("e-")).slice(2);
    resize = { side, sx: e.screenX, sy: e.screenY, g: await api.size(), busy: false, next: null };
  }));
  document.addEventListener("pointermove", (e) => {
    if (!resize) return;
    const { side, sx, sy, g } = resize, dx = e.screenX - sx, dy = e.screenY - sy;
    let { w, h } = g;
    if (side.includes("e")) w = g.w + dx;
    if (side.includes("s")) h = g.h + dy;
    if (side.includes("w")) w = g.w - dx;
    if (side.includes("n")) h = g.h - dy;
    resize.next = { w, h };
    if (!resize.busy) send();
  });
  async function send() {
    if (!resize || !resize.next) return;
    const r = resize, n = r.next;
    r.next = null;
    r.busy = true;
    await api.resize(n.w, n.h, r.side);
    r.busy = false;
    if (r.next) send();
  }
  document.addEventListener("pointerup", () => { resize = null; });
}
window.addEventListener("pywebviewready", windowControls);
if (window.pywebview && window.pywebview.api) windowControls();

if (!SOLO) {
  refresh();
  setInterval(refresh, 1000);
}
