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
  "Boss timers": '<circle cx="12" cy="13" r="8"/><path d="M12 8v5l3 2M9 2h6"/>',
  Wardrobe: '<path d="M8 3l4 3 4-3 5 4-3 3v10H6V10L3 7z"/>',
  Shop: '<path d="M4 8h16l-1.5 12h-13z"/><path d="M9 8V6a3 3 0 0 1 6 0v2"/>',
  Alerts: '<path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4z"/><path d="M10 21h4"/>',
  "View cone": '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  Layout: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M10 10v10"/>',
  About: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
  Debug: '<circle cx="12" cy="12" r="7"/><path d="M12 2v4M12 18v4M2 12h4M18 12h4"/><circle cx="12" cy="12" r="1.5"/>',
  FAQ: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6v.4M12 17v.5"/>',
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
  return [["General"], ["Characters"], ["Checklist"], ...state.features.map((f) => [f.title, f]), ["Layout"], ["Debug"], ["FAQ"], ["About"]];
}
function renderNav() {
  const nav = document.getElementById("nav");
  const item = (name, f) => `<button data-page="${esc(name)}" class="${name === page ? "active" : ""} ${f && !f.enabled ? "off" : ""}">
    ${icon(name)}<span>${esc(name === "Layout" ? "Overlay layout" : name === "Debug" ? "Position debug" : name)}</span>${f ? `<i class="dot" data-switch="${esc(f.title)}" title="${f.enabled ? "On: click to turn off" : "Off: click to turn on"}"></i>` : ""}</button>`;
  nav.innerHTML = item("Characters") + item("Checklist") + '<div class="group">Features</div>' +
    state.features.map((f) => item(f.title, f)).join("") + '<div class="group">Overlays</div>' + item("Layout") +
    '<div class="group">Daeva Radar</div>' + item("General") + item("Debug") + item("FAQ") + item("About");
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
  <div class="row"><div class="label">Map you're on<span class="hint">${s.map.detected ? "Detected: " + esc(label(s.map.detected)) : "Not detected yet: read from the boss list, or pick one."}</span></div>
    <select data-h="${reg((v) => act("set_map", { key: v }))}">${s.map.choices.map(([k, t]) => `<option value="${esc(k)}" ${k === s.map.choice ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></div>
  <div class="section">Windows</div>
  <div class="row"><div class="label">Open Settings when Daeva Radar starts<span class="hint">Else it starts in the tray.</span></div>
    ${toggle(g.settings_at_start, (v) => act("set_settings_at_start", { on: v }))}</div>
  <div class="row"><div class="label">Keep the World map on top</div>${toggle(g.topmost, (v) => act("set_topmost", { on: v }))}</div>
  <div class="row"><div class="label">World map<span class="hint">Pet spawns, cubes, gathering nodes.</span></div>${btn("Open map", () => act("open_map"))}</div>
  <div class="section">Updates</div>
  <div class="row"><div class="label">This is ${mine}.<span class="hint">${upd}${s.git && u.state === "available" ? " Update it with git pull." : ""}</span></div>
    ${u.state === "available" && !s.git ? btn("Update and restart", () => confirm(`Update to ${u.release} now? The app restarts; your settings are kept.`) && act("install_update"), "primary") : ""}
    ${btn("Check now", () => act("check_updates"), "", ["checking", "installing", "installed"].includes(u.state))}</div>
  <div class="section">Troubleshooting</div>
  <div class="row"><div class="label">Position debug<span class="hint">Your position and how it updates.</span></div>${btn("Open", () => { page = "Debug"; store.set("page", page); render(true); })}</div>
  <div class="divider"></div>
  <div class="btns">${btn("Restart Daeva Radar", () => confirm("Restart Daeva Radar? It closes and starts again (this window too); your settings are kept.") && act("restart_app"))}
  ${btn("Quit Daeva Radar", () => confirm("Quit Daeva Radar? Capture, overlays and alerts stop.") && act("quit_app"), "danger")}</div>`;
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
    <p class="lead">${f.enabled ? esc(f.status || "Running.") : "Off: nothing is tracked or drawn."}</p>
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
  let html = `<div class="row"><div class="label">${esc(o.title)}<span class="hint">${o.kind === "radar" ? "Minimap overlay." : "Info card."} ${o.shown ? (o.locked ? "Locked." : "Unlocked: drag to move.") : "Hidden."}</span></div>
    <span class="switch-label">Locked${toggle(o.locked, (v) => act("overlay_lock", { id: o.id, on: v }), !o.shown)}</span>
    <span class="switch-label">Shown${toggle(o.shown, (v) => act("overlay_show", { id: o.id, on: v }), !o.allowed)}</span>
    ${o.allowed ? btn("Place", place) : ""}</div>`;
  if (detail && o.kind === "radar") {
    html += `<p class="lead">${esc(o.text)} Needs borderless or windowed mode.</p>
    <div class="row"><div class="label">Scale<span class="hint">${o.across} m across. Mouse wheel works too.</span></div>
      <div class="btns">${btn("−", () => act("overlay_scale", { id: o.id, factor: 1 / 1.1 }))}${btn("+", () => act("overlay_scale", { id: o.id, factor: 1.1 }))}</div></div>
    <div class="row"><div class="label">Show the view cone on this overlay<span class="hint">Needs View cone on.</span></div>${toggle(o.cone, (v) => act("overlay_cone", { id: o.id, on: v }))}</div>
    <div class="row"><div class="label">Lost it off-screen?</div>${btn("Bring it here", () => act("overlay_recenter", { id: o.id }), "", !o.allowed)}</div>
    <div class="section">How to line it up</div>
    <p class="lead">1. Drag the overlay so its crosshair sits on your arrow.<br>2. Drag its bottom-right corner to the minimap's size.<br>
    3. Mouse wheel (or − / +) until its rings match the game's distances.<br>4. Lock it: right-click, the switch above, or Ctrl+Shift+O (locks all).</p>`;
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
  <p class="lead">Set each character's faction (unset: guessed from its maps). Keys and currencies are on <b>Checklist</b>.</p>
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
// the counters aren't items, so they get drawn icons (the currencies use the game's own, assets/web/icons)
const CL_ICONS = {
  Nightmare: ['#b48cff', '<path d="M15 4a8 8 0 1 0 5 13.5A7 7 0 0 1 15 4z"/><path d="M17 8l.6 1.4L19 10l-1.4.6L17 12l-.6-1.4L15 10l1.4-.6z"/>'],
  "Shugo keys": ['#ffd24a', '<circle cx="8" cy="12" r="4"/><path d="M12 12h9M17 12v3M20 12v2"/>'],
  "Invasion keys": ['#ff8a5c', '<circle cx="8" cy="12" r="4"/><path d="M12 12h9M17 12v3M20 12v2"/><path d="M6 6l-2-2M10 6l2-2"/>'],
  "Unknown Fissure": ['#5ad1ff', '<path d="M12 3l3 6-2 2 4 4-3 6M9 6l-2 5 3 2-2 6"/>'],
  "Ascension Trial": ['#7cf08a', '<path d="M12 3l3 5h-2v5h-2V8H9zM5 21h14M7 17h10"/>'],
  "Abyss time": ['#4fa3ff', '<circle cx="12" cy="12" r="8"/><path d="M12 7v5l3 2"/>'],
  "Abyss Corridors": ['#b48cff', '<path d="M12 3c3 3 3 6 0 9s-3 6 0 9"/><path d="M6 6c4 2 8 2 12 0M6 18c4-2 8-2 12 0"/>'],
  "Odyle energy": ['#9fd8ff', '<path d="M12 3l6 9-6 9-6-9z"/><path d="M12 8l3 4-3 4-3-4z"/>'],
};
const clIcon = (name) => {
  const [color, path] = CL_ICONS[name] || ["#8a95a5", '<circle cx="12" cy="12" r="7"/>'];
  return `<span class="cl-icon" style="--c:${color}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${path}</svg></span>`;
};
const ENERGY_CAP = 840, ENERGY_WARN = 700;  // Odyle energy's limit, and where it's close
// Duty Commands: 4 tiers x 5 a week per server. The game doesn't send the count, so you fill it in (cleared at the weekly reset)
function commandsBlock() {
  const c = state.commands, total = c.t.reduce((a, b) => a + b, 0), all = c.t.every((n) => n >= c.max), reset = untilReset(true);
  const kin = (n) => Number(n).toLocaleString("en-US");
  const toBuy = c.t.reduce((a, n, i) => a + (c.max - n) * c.cost[i], 0), weekly = c.cost.reduce((a, k) => a + k * c.max, 0);
  const box = (on, onchange, label) => `<label class="cm-box"><input type="checkbox" ${on ? "checked" : ""} data-h="${reg(onchange)}"><span></span>${label || ""}</label>`;
  const tiers = c.t.map((n, i) => `<div class="cm-tier cm-t${i + 1} ${n >= c.max ? "done" : ""}">
      <img src="/icons/${c.ids[i]}.png" alt="" width="40" height="40"><div class="cm-info"><b>T${i + 1}</b> <small class="muted cm-cost">${kin(c.cost[i])} each</small>
      <span class="cm-count"><button class="cm-step" data-h="${reg(() => act("commands_fill", { tier: i, value: n - 1 }))}" ${n <= 0 ? "disabled" : ""}>−</button>
        <b>${n}</b> / ${c.max}
        <button class="cm-step" data-h="${reg(() => act("commands_fill", { tier: i, value: n + 1 }))}" ${n >= c.max ? "disabled" : ""}>+</button></span></div>
      ${box(n >= c.max, (v) => act("commands_fill", { tier: i, value: v ? c.max : 0 }))}</div>`).join("");
  const duty = `<div class="cm-tier ${c.duty >= c.duty_max ? "done" : ""}">
      <img src="/icons/duty_command.png" alt="" width="40" height="40"><div class="cm-info"><b>Duty Command</b> <small class="muted cm-cost">${kin(c.duty_cost)} each · ${kin((c.duty_max - c.duty) * c.duty_cost)} left to buy</small>
      <span class="cm-count"><button class="cm-step" data-h="${reg(() => act("duty_fill", { value: c.duty - 1 }))}" ${c.duty <= 0 ? "disabled" : ""}>−</button>
        <b>${c.duty}</b> / ${c.duty_max}
        <button class="cm-step" data-h="${reg(() => act("duty_fill", { value: c.duty + 1 }))}" ${c.duty >= c.duty_max ? "disabled" : ""}>+</button></span></div>
      ${box(c.duty >= c.duty_max, (v) => act("duty_fill", { value: v ? c.duty_max : 0 }))}</div>`;
  return `<div class="section">Duty Commands <small class="muted">· weekly, per server, set by hand</small></div>
    <div class="cm"><div class="cm-tiers">${duty}</div></div>
    <div class="section" style="margin-top:4px">Duty Command: Abyss <small class="muted">· 4 tiers x 5</small></div>
    <div class="cm"><div class="cm-tiers">${tiers}</div>
      <div class="cm-all">${box(all, (v) => act("commands_fill", { tier: -1, value: v ? c.max : 0 }), "<b>All</b>")}
        <span class="cm-total ${all ? "done" : ""}"><b>${total}</b> / ${c.max * c.t.length}</span>
        <small class="muted">${toBuy ? `${kin(toBuy)} kinah left to buy (${kin(weekly)} for all)` : `all bought (${kin(weekly)} kinah)`} · resets ${reset.when} (in ${reset.text})</small></div></div>`;
}
function checklist() {
  // one grid: a column per character, a row per item; what's shared on the server shown once, from the newest reading
  const chars = state.characters.filter((c) => c.checklist && c.checklist.read);
  if (!chars.length) return `<h2>${icon("Checklist")}Checklist</h2><p class="lead">Nothing read yet: log in with a character while Daeva Radar runs.</p>`;
  chars.sort((x, y) => (y.playing - x.playing) || x.name.localeCompare(y.name));
  const fmt = (n) => Number(n).toLocaleString("en-US");
  // 3h19m for hours of time, 2m10s for minutes (the corridors' 6m30s)
  const dur = (ms, max) => { const t = Math.round(ms / 1000);
    return max >= 3600000 ? `${Math.floor(t / 3600)}h${String(Math.floor(t / 60) % 60).padStart(2, "0")}m` : `${Math.floor(t / 60)}m${String(t % 60).padStart(2, "0")}s`; };
  const hms = (ms) => { const t = Math.round(ms / 1000); return `${Math.floor(t / 3600)}h${String(Math.floor(t / 60) % 60).padStart(2, "0")}m`; };
  const staleFor = (c, weekly) => !c.playing && c.checklist.read_at && c.checklist.read_at * 1000 < lastReset(weekly).getTime();
  const daily = untilReset(false), weekly = untilReset(true);
  // a counter's cell: what's left (refilled to full when read before the list's last reset)
  const cell = (c, r, group) => {
    if (!r) return '<td class="cl-na">—</td>';
    const refilled = (group === "weekly" || group === "daily") && r.max && staleFor(c, group === "weekly");
    const left = refilled ? r.max : r.left;
    const done = left <= 0;  // the bonus (+x) doesn't count: 0 (+3) / 3 is done too
    const text = r.unit === "ms" ? `${dur(left, r.max)}${r.bonus ? ` <small>(+${dur(r.bonus, r.max)})</small>` : ""} / ${dur(r.max, r.max)}`
      : `${left}${r.bonus ? ` <small>(+${fmt(r.bonus)})</small>` : ""}${r.max ? ` / ${r.max}` : ""}`;
    const part = r.max ? Math.min(100, 100 * left / r.max) : null;
    const title = refilled ? "Refilled at the reset (last read before it)" : r.proj ? `Estimated: +${r.proj} since it was read (daily recovery)` : "";
    return `<td class="${done ? "cl-ok" : ""}${refilled ? " cl-refilled" : ""}${r.proj ? " cl-proj" : ""}" title="${title}">
      ${done ? '<span class="cl-tick">✓</span>' : ""}${text}${part !== null ? `<i class="cl-bar" style="--p:${part}%"></i>` : ""}</td>`;
  };
  const find = (c, group, name) => (c.checklist[group] || []).find((r) => r.name === name);
  // rows: [label, icon html, group, name, weekly?] for the per-character counters
  const charRows = [];
  const serverRows = [];
  for (const group of ["stock", "daily", "weekly"])
    for (const c of chars)
      for (const r of c.checklist[group] || []) {
        const list = r.scope === "server" ? serverRows : charRows;
        if (!list.some((x) => x.name === r.name)) list.push({ name: r.name, group, weekly: group === "weekly", daily: group === "daily" });
      }
  const tag = (row) => row.weekly ? '<span class="cl-when">weekly</span>' : row.daily ? '<span class="cl-when">daily</span>' : "";
  const head = `<tr><th></th>${chars.map((c) => `<th class="cl-col ${c.playing ? "on" : ""}">
      ${c.checklist.icon ? `<img src="/classes/${c.checklist.icon}.png" alt="">` : ""}
      <b>${esc(c.name)}</b>${c.playing ? '<em>in game</em>' : ""}
      <small>${esc([c.checklist.cls, c.checklist.level && "Lv " + c.checklist.level].filter(Boolean).join(" · "))}</small></th>`).join("")}</tr>`;
  const energy = `<tr><th>${clIcon("Odyle energy")}Odyle energy</th>${chars.map((c) => {
    const e = c.checklist.energy;
    if (!e) return "<td>—</td>";
    // it stops filling at ENERGY_CAP: orange when close (over 700), red when full (spend it before it's wasted)
    const level = e[0] >= ENERGY_CAP ? "cl-full" : e[0] > ENERGY_WARN ? "cl-near" : "";
    const est = c.checklist.energy_proj;
    return `<td class="${est ? "cl-proj" : ""}" title="${est ? `Estimated: +${est} since it was read (15 every 3 hours)` : ""}"><span class="${level}">${fmt(e[0])}</span>${e[1] ? ` <small>(+${fmt(e[1])})</small>` : ""} / ${ENERGY_CAP}</td>`;
  }).join("")}</tr>`;
  const counterRows = charRows.map((row) => `<tr><th>${clIcon(row.name)}${esc(row.name)}${tag(row)}</th>${chars.map((c) => cell(c, find(c, row.group, row.name), row.group)).join("")}</tr>`).join("");
  const coins = [];
  for (const c of chars) for (const [n, , id] of c.checklist.currencies) if (!coins.some((x) => x[0] === n)) coins.push([n, id]);
  const coinRows = coins.map(([n, id]) => `<tr><th><img class="cl-coin-icon" src="/icons/${id}.png" alt="" width="24" height="24">${esc(n)}</th>${chars.map((c) => {
    const v = c.checklist.currencies.find((x) => x[0] === n);
    return `<td>${v ? fmt(v[1]) : "—"}</td>`;
  }).join("")}</tr>`).join("");
  const readRow = `<tr class="cl-read"><th>Last read</th>${chars.map((c) => `<td>${c.playing ? "live" : esc(c.checklist.read)}</td>`).join("")}</tr>`;
  // server-wide: once, from the character read most recently
  const newest = [...chars].sort((x, y) => (y.playing - x.playing) || ((y.checklist.read_at || 0) - (x.checklist.read_at || 0)))[0];
  const st = state.storage;
  const storageRow = st ? `<tr><th><img class="cl-coin-icon" src="/icons/930100031.png" alt="" width="24" height="24">Kinah in storage</th>
      <td>${fmt(st.kinah)}</td><td class="cl-src">${st.by ? `from ${esc(st.by)}, ` : ""}${esc(st.read)} (read when the storage is used)</td></tr>` : "";
  const serverTable = serverRows.length || st ? `<table class="cl-grid cl-server"><tbody>${storageRow}${serverRows.map((row) => {
    const owner = chars.find((c) => find(c, row.group, row.name) && c === newest) || chars.find((c) => find(c, row.group, row.name));
    return `<tr><th>${clIcon(row.name)}${esc(row.name)}${tag(row)}</th>${cell(owner, find(owner, row.group, row.name), row.group)}
      <td class="cl-src">from ${esc(owner.name)}, ${owner.playing ? "live" : esc(owner.checklist.read)}</td></tr>`;
  }).join("")}</tbody></table>` : "";
  return `<h2>${icon("Checklist")}Checklist</h2>
  <p class="lead">What's left, from each character's last login. Weekly items read before the reset show refilled; energy,
  Nightmare and Shugo keys are estimated since the reading (dotted underline).</p>
  <div class="cl-resets"><span>☀ Daily reset in <b>${daily.text}</b> <small>(${daily.when})</small></span>
    <span>▦ Weekly reset in <b>${weekly.text}</b> <small>(${weekly.when})</small></span>
    <label class="cm-box" title="Subscriber: Shugo keys come 3 a day instead of 2"><input type="checkbox" ${state.subscriber ? "checked" : ""} data-h="${reg((v) => act("set_subscriber", { value: v }))}"><span></span>Sub</label></div>
  <div class="section">Server <small class="muted">· shared by your characters on the server</small></div>
  ${serverTable || '<p class="muted">Not read yet.</p>'}
  ${commandsBlock()}
  <div class="section">Characters</div>
  <div class="cl-scroll"><table class="cl-grid"><thead>${head}</thead><tbody>
    ${energy}${counterRows}
    <tr class="cl-sep"><th colspan="${chars.length + 1}">Currencies</th></tr>
    ${coinRows}${readRow}</tbody></table></div>
  <p class="muted cl-foot">More lists are added as they're decoded.</p>`;
}

// ---- FAQ: the problems people actually run into, with what to do
const FAQ = [
  ["Getting started", [
    ["It asks for administrator rights. Why?",
     "Reading the game's network traffic needs the WinDivert driver, and Windows only allows that to programs running as administrator. Daeva Radar only reads: it never changes, sends or uploads anything."],
    ["Windows says \"Windows protected your PC\" or my antivirus warns about it.",
     "The exe isn't code-signed and it captures network traffic, which some tools flag. Click <b>More info → Run anyway</b>. It's built from the public source on GitHub by GitHub's own release workflow."],
    ["Is it safe for my account?",
     "<span class=\"warn\">Use at your own risk.</span> NCSOFT's rules prohibit third-party tools that read the game's traffic, even read-only ones like this one, so using it could get your account sanctioned."],
  ]],
  ["The Settings window", [
    ["Settings opens in my web browser instead of its own window.",
     "Its own window needs <b>Microsoft Edge WebView2 Runtime</b> and <b>.NET Framework 4.7.2</b> or newer. A pop-up says which one is missing; click it to get it from Microsoft, then restart Daeva Radar. If you unzipped the download with Windows Explorer, the files may also be blocked: delete the folder, right-click the zip → <b>Properties → Unblock</b>, and extract it again. The exact reason is saved in <code>%APPDATA%\\DaevaRadar\\window_error.log</code>."],
    ["I closed Settings. How do I get it back?",
     "Daeva Radar keeps running in the tray (bottom right of the taskbar, maybe under the ^ arrow). Double-click its icon, or right-click → <b>Settings</b>."],
    ["How do I quit or restart it?",
     "Tray icon → <b>Quit Daeva Radar</b>, or the <b>Restart</b> / <b>Quit</b> buttons at the bottom of General."],
  ]],
  ["Seeing things", [
    ["It says \"Character not identified yet\" and nothing shows up.",
     "Start Daeva Radar <b>before</b> you enter the world, or teleport, change zone or relog once while it runs: it learns your character from the login."],
    ["My position doesn't move when I walk.",
     "The game only sends your position when you teleport, dash or have an <b>aura</b> active (about once a second). Plain walking isn't sent, so keep an aura on for steady updates."],
    ["The overlays don't show over the game.",
     "Play in <b>borderless or windowed</b> mode (fullscreen covers them). Overlays only show while AION 2 is the active window, so click into the game. Check the feature is on (the dot in the sidebar) and its overlay is shown (the feature's Overlays tab, or the tray)."],
    ["An overlay is in the wrong place or I lost it.",
     "Use <b>Overlay layout</b> (sidebar or tray) to drag everything into place over the game, or <b>Bring it here</b> in the feature's Overlays tab. Unlocked overlays can be dragged; <b>Ctrl+Shift+O</b> locks / unlocks them all."],
    ["The Pet tracker card disappeared.",
     "It fades out 60 seconds after your last soul drop and comes back with the next one. Turn that off in Monsters → Settings → Pets."],
    ["The map is wrong (cube spawns, feathers or gathering nodes don't line up).",
     "Pick the map yourself in General → <b>Map you're on</b> instead of Auto."],
  ]],
  ["Game data", [
    ["Pets, wardrobe or the checklist are empty.",
     "They're read when a character enters the world: relog once with Daeva Radar running. Each character is saved separately, so log in with each one once."],
    ["The checklist shows old numbers for a character.",
     "Each column is saved from that character's last login (live for the one you're playing). Weekly items read before the last reset show refilled; keys and Nightmare refill over time, so they may be higher than saved. Logging in with it updates the column."],
    ["The shop list is out of date.",
     "The game only sends the offers when you open the shop, and they change every hour at x:00: open the shop in game again after x:00."],
  ]],
  ["Updates and settings", [
    ["How do I update?",
     "Daeva Radar checks at start and every hour. When there's a new version, a pop-up says so: click it, then <b>Update and restart</b> in General. Your settings are kept."],
    ["Where are my settings?",
     "In <code>%APPDATA%\\DaevaRadar\\settings.json</code> (overlay positions, watch lists, characters, the checklist). Updates and reinstalls keep it."],
  ]],
];
function faq() {
  return `<h2>${icon("FAQ")}FAQ</h2>
  <p class="lead">Common problems and what to do. Still stuck? Ask on <a href="https://github.com/Noragiri/daeva_radar/issues" target="_blank" rel="noopener">GitHub</a>.</p>
  ${FAQ.map(([group, items]) => `<div class="section">${esc(group)}</div>
    ${items.map(([q, a]) => `<details class="faq"><summary>${esc(q)}</summary><p>${a}</p></details>`).join("")}`).join("")}`;
}

function about() {
  return `<h2>${icon("About")}About</h2>
  <p class="lead">Reads AION 2's network traffic on this PC. Read-only: it never changes, sends or uploads anything.</p>
  <p class="lead warn">NCSOFT's rules prohibit tools that read the game's traffic. Use at your own risk.</p>
  <div class="row"><div class="label">The notice you agreed to<span class="hint">Its risk, and what it keeps and sends.</span></div>${btn("Read it again", () => Terms.open(false, state.terms.when))}</div>
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
  <p class="lead">Place overlays and info cards on the game window: drag them on a grid (hidden ones show dashed).
  <b>Done</b> or <b>Esc</b> locks them again.</p>
  <div class="btns">${state.layout ? btn("Finish layout", () => act("edit_layout"), "primary") : btn("Edit layout over the game", startLayout, "primary")}
    ${btn("Lock all", () => act("lock_all", { on: true }))}${btn("Unlock all", () => act("lock_all", { on: false }))}</div>
  <p class="muted">Ctrl+Shift+O locks / unlocks every overlay.</p>
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
  if (page === "Debug") {  // a page drawn by the app like a feature tab (daevaradar/debug.py)
    if (fresh || el.dataset.view !== "Debug") {
      el.dataset.view = "Debug";
      el.innerHTML = '<div id="mhost" class="fpage"></div>';
      FeaturePage.show(document.getElementById("mhost"), "Debug", "Position");
    }
  } else if (f) {
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
    const open = new Set([...el.querySelectorAll("details[open] > summary")].map((x) => x.textContent));  // kept across redraws
    el.innerHTML = page === "General" ? general() : page === "Characters" ? characters() : page === "Checklist" ? checklist() : page === "Layout" ? layout() : page === "FAQ" ? faq() : about();
    el.querySelectorAll("details > summary").forEach((x) => { if (open.has(x.textContent)) x.parentElement.open = true; });
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
    if (focusSeen !== null && next.focus !== focusSeen && window.pywebview) window.pywebview.api.focus();
    if (next.goto && next.goto !== page) { page = next.goto; store.set("page", page); lastJson = ""; state = next; render(true); }
    focusSeen = next.focus;
    const json = JSON.stringify({ ...next, status: null });
    state = next;
    if (!next.terms.agreed && !Terms.shown) Terms.open(true);  // the first-run notice, over everything
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
