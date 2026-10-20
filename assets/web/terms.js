// The first-run notice: what Daeva Radar is, its risk, and what it keeps and sends. Shown over everything until it's
// agreed to (the app reads nothing before that); About can show it again, read-only.
"use strict";
const Terms = (() => {
  const SECTIONS = [
    ["What it does", [
      "It reads the network traffic AION 2 sends to <b>this PC</b>, through the WinDivert driver (that's why it needs administrator rights). It is read-only: it never changes, delays or sends anything to the game.",
      "From that it shows what's around you (cubes, monsters, gathering nodes, feathers), your pets, wardrobe and the shop's offers, and each character's name, class, level, currencies and counters. The View cone feature also reads a small part of your screen (the minimap), only while it's switched on.",
    ]],
    ["What it keeps on your PC", [
      "One settings file, <code>%APPDATA%\\DaevaRadar\\settings.json</code>: overlay positions, watch lists, your characters (name, class, level, faction, the last reading of their checklist), your pet collection, wardrobe and shop offers. Delete the folder and all of it is gone.",
    ]],
    ["What it sends, to whom and why", [
      "Only to <b>GitHub</b>: at start and every hour it asks which version is the latest, and downloads it when you choose to update. Those requests carry your IP address, as any web request does, and nothing about you or the game.",
      "Nothing else leaves your PC. There are no accounts, no analytics, no advertising and no tracking, and your game data is never uploaded. The Settings window and the map are served from your own PC (127.0.0.1, with a one-time token). If you switch on Lovense haptics, commands go to your phone on your own network.",
    ]],
    ["The code", [
      "It's open source, and the downloads are built from it by GitHub's release workflow. The exe isn't code-signed, so Windows and some antivirus tools may warn about it.",
    ]],
    ["When you agree", [
      "This PC records that you agreed to this version of the notice, and when, in the settings file. Nothing is sent anywhere. Until you agree, the app reads no game traffic and checks for no updates.",
    ]],
  ];
  let host = null;

  function body() {
    return SECTIONS.map(([title, items]) => `<h4>${title}</h4>${items.map((t) => `<p>${t}</p>`).join("")}`).join("");
  }
  function close() { if (host) { host.remove(); host = null; } }

  // required: the first-run gate (two ticks, Quit / I agree); otherwise a read-only copy with a Close button
  function open(required, agreedAt) {
    if (host) return;
    host = document.createElement("div");
    host.className = "terms-back";
    host.innerHTML = `<div class="terms" role="dialog" aria-modal="true" aria-label="Before you use Daeva Radar">
      <header><img src="/favicon.png" alt=""><div><h3>Before you use Daeva Radar</h3>
        <p>Read this once: it says what the app is, its risk, and what it does with your data.</p></div></header>
      <div class="terms-warn">⚠ <div><b>An unofficial tool, at your own risk.</b> NCSOFT has not approved it, and its rules prohibit tools that read the
        game's traffic, even read-only ones. It could act against an account that uses it.</div></div>
      <div class="terms-body">${body()}</div>
      ${required ? `<label class="terms-check"><input type="checkbox" id="terms-1"> <span>I understand that Daeva Radar is an unofficial tool, in a grey area of AION 2's terms, and that I use it at my own risk.</span></label>
        <label class="terms-check"><input type="checkbox" id="terms-2"> <span>I have read what the app keeps on my PC and what it sends, to whom and why.</span></label>
        <div class="terms-buttons"><button class="btn" id="terms-quit">Quit</button><button class="btn primary" id="terms-ok" disabled>I agree</button></div>
        <p class="terms-foot">Your agreement is saved on this PC only.</p>`
        : `<div class="terms-buttons"><span class="muted">${agreedAt ? "You agreed to this on " + agreedAt : ""}</span><button class="btn" id="terms-close">Close</button></div>`}
    </div>`;
    document.body.appendChild(host);
    if (!required) { host.querySelector("#terms-close").onclick = close; return; }
    const ok = host.querySelector("#terms-ok"), boxes = [1, 2].map((i) => host.querySelector("#terms-" + i));
    const sync = () => { ok.disabled = !boxes.every((b) => b.checked); };
    boxes.forEach((b) => b.addEventListener("change", sync));
    host.querySelector("#terms-quit").onclick = () => act("quit_app");
    ok.onclick = async () => { ok.disabled = true; await act("accept_terms"); close(); };
  }
  return { open, close, get shown() { return !!host; } };
})();
