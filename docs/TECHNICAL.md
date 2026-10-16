# Daeva Radar: how it works

For developers and the curious. The user guide is [GUIDE.md](GUIDE.md); the start page is the [README](../README.md).

## Folder layout

```
Daeva Radar.lnk      start it: icon, no console (create with: python daeva_radar.py --shortcut)
Daeva Radar.bat      same, without the icon
daeva_radar.sh       Linux launcher
daeva_radar.py       launcher (starts daevaradar/)
daevaradar/          the program
  common.py            platform flags, folders, settings file
  protocol.py          TCP stream reassembly, frames, LZ4
  game.py              game messages -> you, cubes, monsters, gathering nodes, pets, wardrobe, shop; local map data
  capture.py           WinDivert / Linux capture, finding the game's connections, the engine
  sound.py             alert sounds
  vision.py            reading the vision cone from the minimap
  follow.py            following your position between packets with your summoned pet
  debug.py             the Position debug window
  worldmap.py          the Map window (world map images: pets' soul spawns, hidden cubes, gathering nodes)
  ui.py                shared drawing, overlay window, widgets, popups
  feature.py           the Feature base (tabs, On/Off switch, overlay controls)
  app.py               the main window
  update.py            update check and in-place install from GitHub releases
  main.py              startup, demo, console mode, shortcut
  features/            cubes.py, feathers.py, monsters.py (+ pets.py: its Pets tab), gathering.py, wardrobe.py,
                       shop.py, alerts.py, viewcone.py
assets/              icons
sounds/              bundled cube callouts (+ ATTRIBUTION.txt)
sounds/custom/       your own WAV/MP3 alerts
windivert/           WinDivert.dll + WinDivert64.sys + LICENSE (LGPLv3 / GPLv2)
data/                monster/skill name tables, map data, pets and their soul monsters' spawns, wardrobe sets,
                     shop item names, world map images (data/map_tiles; see data/SOURCE.md)
docs/                this file and the user guide
VERSION              the release this copy is (release zips only)
dev/                 tests and tools, not needed to run:
  test_daeva_radar.py   offline decoder/tracker tests
  make_icon.py          regenerates assets/ (needs Pillow)
  fetch_map_data.py     refreshes data/maps/ (gathering nodes, cubes, feathers; one-time, after patches)
  fetch_pet_data.py     refreshes data/pets.json and data/pet_spawns/
  fetch_skin_data.py    refreshes data/skins.json (wardrobe sets and pieces)
  fetch_shop_items.py   refreshes data/shop_items.json (names of the items the shop sells)
  fetch_map_tiles.py    refreshes data/map_tiles/ (one-time, after patches that change maps)
  daeva_radar.spec      PyInstaller recipe for the Windows build (DaevaRadar.exe; the release workflow runs it)
  linux_test/           Docker end-to-end test with a fake game and server
```

Where each data file comes from, and its credits, is in [data/SOURCE.md](../data/SOURCE.md).

## Capture and decoding

- **Capture:** read-only, and it never delays, changes or injects traffic.
  - **Windows:** WinDivert copies the game's incoming TCP packets (`SNIFF | RECV_ONLY`).
  - **Linux:** a raw `AF_PACKET` socket does the same job, in a small helper that runs as root
    (`daeva_radar.py --capture-helper`, started through `pkexec` or `sudo`).
- **Routes:** the game's connections are found from the `aion2.exe` process every 3 s.
  - **Windows:** from `netstat`. Ports 13328 and 1111 plus loopback are always captured, so ExitLag and GearUP work.
  - **Linux:** from `/proc`, including Wine/Proton processes.
- **Decoding:** each TCP stream is reassembled into frames (varint length + body). Bodies starting with `ff ff` are
  LZ4 blocks of more frames. After packet loss, decoding resyncs on a fully validated compressed frame.

You only see what the server sends to your client, which means nearby objects. It's not a whole-map scan. All data
stays on this PC.

## Messages used

- `36` / `J6`: your entity id
- `*8` / `+8`: your position. The server doesn't send it for plain walking. It arrives when you dash or use a similar
  movement skill (`*8`), and about once a second while an **aura** is active (`+8`, 46 bytes, one per aura effect;
  checked live 2026-10-15). With no aura on, your position only changes on dashes and teleports, so between
  packets it's followed with your pet (see [Following your position](#following-your-position)).
- Your summoned pet (checked live 2026-10-15 with 4 different pets): it spawns with an `41 36` marked as a pet
  (`1f 10` right after the id; monsters differ) 0-2.5 m beside you right after your summon skill (`02 38`),
  moves with `28 37` / `29 37` (position + velocity, 33 bytes) and `1a 37` (stopped), and `42 36` removes it.
  Its type is remembered, so it's recognised again when it comes back with you.
- `#6`: teleport or zone-in (clears pins)
- `46` with a cube type id: a cube appears, with xyz. Known ids: 1200039 (Verteron, confirmed), 1301199 / 1301200
  (Altgard), 1301242-1301245 (Morheim, unconfirmed)
- `56` `04 03 00`: the cube was opened
- `05 8d` or `56` `01 01 00`: the cube left your range
- Pets (account-wide; checked live 2026-10-15 against the game's own login sync):
  - `00 90`: your whole collection when you enter the world: each pet's level, then souls in its current stage
  - `0d 90`: a soul drop (pet, souls gained); several pets can come in one message
  - `14 90` then `0e 90`: a level up (pet, new level) and the souls it used (5 to unlock, 25 to Lv2, 75 to Lv3)
- `5b 8d`: your wardrobe when you enter the world: every appearance piece you own (skin item id = set x 100 + piece;
  checked live 2026-10-15 against a character's equipped skins, 219 pieces, and the Closet's own counts)
- `91 56`: one tab of the rotating shop (Special `65`, Pets `68`, Skins `67`), sent when you open it: item id,
  quantity, discount code (0-3 = 10/20/30/50 %), original price and price. Each character has its own shop; the
  server doesn't push new offers at the hourly refresh.

### How monsters are read

- `41 36` "monster in range": id, monster type (u32), position. Names come from `data/monsters.json`. Types missing
  from it are NPCs such as townsfolk, hidden unless "Include NPCs" is on.
- `*8` / `+8` movement: the last 12 bytes are where the monster is or is heading. A monster chasing you reports your
  position, so it shows at ~0 m.
- `04 8d`: something died, so it's removed.
- No "left range" message is sent for monsters, so ones more than 150 m from you are dropped.

Checked live on 2026-10-14: Drana and Krall monsters at 11-29 m with the right names, and kills removed.

To find a cube id for a new zone, run with `--log`, open a cube, and look for `object event: ... message 3536 040300`
in the log. To work out a new message, `--record FILE` saves every decoded message as JSON lines.

## Following your position

The server sends your position only when it moves you or something on you: teleports, dashes and similar skills,
and aura ticks (about once a second). Plain walking is reported by your client (encrypted) and never sent back, so
without one of those your position only changes on dashes and teleports. Your summoned pet fills the gap:

- **Your pet:** the server sends where your pet is while it follows you (about 1–1.5 s behind), so its position
  projected about 1.25 s ahead along its velocity is where you are. In a live session (2026-10-15) it was
  1.6–11 m off when the last packet was 13–54 m off. It's left off the Monsters radar.
- **Packets win:** your own position packets are exact. The pet's estimate is only used once it's newer than
  the last packet and says you've moved more than 4 m from it.
- **Check it:** the **⌖ Position debug** window (sidebar, or `--debug`) shows where your position comes from
  (packets or pet), your pet, and how far off the pet estimate was when the next packet arrived.

## How the view cone is read

The game traffic doesn't contain your camera direction, so it's read from the screen. About 15 times a second, on
its own thread, the app captures only the small minimap area you located (about 6 ms) and reads it (about 5 ms).
Only the cone is redrawn, every 50 ms, so it follows the camera without waiting for the rest of the window:

- **White edges first:** AION 2 draws the cone with two white edge lines. The camera sits behind your character,
  so the lines pass beside the white disc around your arrow instead of starting at it. The app finds the two
  longest straight white lines around the disc (Hough transform, judged by length so short icon outlines don't
  count). Icons drawn over a line are tolerated, and the pair must be 80-150° apart, like the real cone. Your view
  is between them. If only one edge is visible, the cone goes on its lighter side. Clarity is 1.5-2.
- **Brightness fallback:** without clear edges, it looks for the lighter wedge instead (clarity about 0-1).

On two real minimap screenshots (2026-10-14) it read the cone within 3°, with the locate circle up to 3 px
off-centre. It only reads the screen while something shows the cone. The overlays can't confuse the reader,
because it only looks for near-white lines: the overlays draw their cone in cyan, and your white dot sits inside
the arrow area the reader skips. With a visible overlay on top of a test minimap, 24 angles read within 1°. In
tests on synthetic minimaps, the cone was read within about 3–16° and never invented where there was none.

## Updates

At launch and then every full hour (x:00), the app asks GitHub which release of
[Noragiri/daeva_radar](https://github.com/Noragiri/daeva_radar/releases) is the latest. That request sends nothing
about you or the game.

- Installing downloads the release zip (the `-win64` one for the exe, the source zip otherwise), checks its
  SHA-256, writes the changed files over this folder and restarts (already elevated, so no new prompt). Settings
  live in your profile and are kept; your own files such as `sounds/custom/` are left alone. A file that's in use
  (`WinDivert.dll`, the exe) is renamed to `.old` and removed on a later start. `VERSION` is written last, so a
  failed update never claims the new version.
- A git checkout (no `VERSION` file) is only told when the release's commit isn't in your branch; update it with
  `git pull`. Zips from before the updater have no `VERSION` and get the update offered.
- Turn the check off with `--no-update-check`, or `"check_updates": false` in the settings file.
- Releases: every push to `main` runs `.github/workflows/release.yml`: the offline tests, the Windows build
  (`dev/daeva_radar.spec`, PyInstaller), then a release with the source zip and `DaevaRadar-<version>-win64.zip`
  (uploaded second, because older updaters take the first `.zip`).

## Tests

`python dev/test_daeva_radar.py` runs the offline decoder and tracker tests (no game, capture or admin rights),
including real recorded messages for pets, the wardrobe and the shop. `python daeva_radar.py --demo` runs the
window on simulated messages.
