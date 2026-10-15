# Cube Finder

A standalone radar for AION2 Hidden Cubes (Verteron, Altgard; more by type id). It is the cube feature of Grip Strength Companion on its own: no damage meter and no uploads, plus a radar window and a minimap overlay.

## Folder layout

```
Cube Finder.lnk      start it: icon, no console (recreate with: python cube_finder.py --shortcut)
Cube Finder.bat      same, without the icon
cube_finder.sh       Linux launcher
cube_finder.py       launcher (starts cubefinder/; the command-line options are unchanged)
cubefinder/          the program
  common.py            platform flags, folders, settings file
  protocol.py          TCP stream reassembly, frames, LZ4
  game.py              game messages -> you, cubes, monsters, gathering nodes; local map data
  capture.py           WinDivert / Linux capture, finding the game's connections, the engine
  sound.py             alert sounds
  vision.py            reading the vision cone from the minimap
  ui.py                shared drawing, overlay window, widgets, popups
  feature.py           the Feature base (tabs, On/Off switch, overlay controls)
  app.py               the main window
  main.py              startup, demo, console mode, shortcut
  features/            cubes.py, monsters.py, gathering.py, alerts.py, viewcone.py
assets/              icons
sounds/              bundled cube callouts (+ ATTRIBUTION.txt)
sounds/custom/       your own WAV/MP3 alerts
windivert/           WinDivert.dll + WinDivert64.sys + LICENSE (LGPLv3 / GPLv2)
data/                monster/skill name tables and map data (see data/SOURCE.md)
dev/                 tests and tools, not needed to run:
  test_cube_finder.py   offline decoder/tracker tests
  make_icon.py          regenerates assets/ (needs Pillow)
  fetch_map_data.py     refreshes data/maps/ from Shugo.GG's public marker files (one-time, after patches)
  linux_test/           Docker end-to-end test with a fake game and server
```

## How it works

- **Capture:** read-only, and it never delays, changes or injects traffic.
  - **Windows:** WinDivert copies the game's incoming TCP packets (`SNIFF | RECV_ONLY`).
  - **Linux:** a raw `AF_PACKET` socket does the same job.
- **Routes:** the game's connections are found from the `aion2.exe` process every 3 s.
  - **Windows:** from `netstat`. Ports 13328 and 1111 plus loopback are always captured, so ExitLag and GearUP work.
  - **Linux:** from `/proc`, including Wine/Proton processes.
- **Decoding:** each TCP stream is reassembled into frames (varint length + body). Bodies starting with `ff ff` are LZ4 blocks of more frames. After packet loss, decoding resyncs on a fully validated compressed frame.
- **Messages used:**
  - `36` / `J6`: your entity id
  - `*8` / `+8`: your position
  - `#6`: teleport or zone-in (clears pins)
  - `46` with a cube type id: a cube appears, with xyz. Known ids: 1200039 (Verteron, confirmed), 1301199 / 1301200 (Altgard), 1301242-1301245 (Morheim, unconfirmed)
  - `56` `04 03 00`: the cube was opened
  - `05 8d` or `56` `01 01 00`: the cube left your range

You only see cubes the server sends to your client, which means nearby objects. This is not a whole-map scan. All data stays on this PC.

## Run on Windows

1. Install Python 3.10+ for Windows x64 (tkinter included).
2. The WinDivert 2.2 files `WinDivert.dll` and `WinDivert64.sys` go in `windivert/` (already there). If they are missing, the copy that Grip Strength Companion leaves in `%APPDATA%\GripStrengthMeter\windivert` is used instead.
3. Double-click the **Cube Finder** shortcut (or `Cube Finder.bat`) and accept the administrator prompt. It runs without a console window, and errors appear in a message box. If you move the folder, run `python cube_finder.py --shortcut` once to recreate the shortcut, both here and on the Desktop. For console output, run `python cube_finder.py --console`.
4. Start the radar, then teleport (or change zone / relog) so it learns your character and position.

## Run on Linux (AION2 under Wine / Proton)

1. Install Python 3.10+ with tkinter (`python3-tk` on Debian/Ubuntu, `tk` on Arch/Fedora).
2. Run `sh cube_finder.sh` (or `python3 cube_finder.py`) as your normal user.
3. A small capture helper (`cube_finder.py --capture-helper`) then asks for your password through `pkexec` (or `sudo` in a terminal), because a raw socket needs root. Only the helper runs as root. The window, settings and sound stay with your user, and the helper exits when the window closes. Running the whole program with `sudo` also works, but then sound may not reach your desktop.
4. Sound uses the first player found:
   - WAV: `paplay`, `pw-play` or `aplay`
   - MP3: `mpv` (up to 200 %), `ffplay` or `mpg123` (up to 100 %)

   Settings are kept in `~/.config/CubeFinder/settings.json`.

Options:

```
--range 150        radar radius in metres (also the − / + buttons)
--voice NAME       cube callout from sounds/ (calm, urgent, deep, fenrir, ja-female, ...,
                   "system beep", off). The window's Sound menu does the same and remembers it.
--sound FILE       play your own WAV or MP3 instead (or Sound > File… in the window)
--volume 0-200     alert volume in % (or the Volume slider). WAVs can go up to 200 %;
                   MP3s stop at 100 % (Windows MCI; on Linux mpv allows 200 %)
--no-sound         silent
--no-topmost       don't keep the window above the game
--console          no window; print "Cube! 50 m NE (height +12 m)" lines
--port N           capture an extra server port
--windivert DIR    Windows: folder containing the WinDivert files
--template ID      also track this cube type id (repeatable)
--demo             no game, capture or admin rights: simulated player and cubes, to try the window
--log FILE         write a log; it includes the type id of anything you open within 10 m
--record FILE      save every decoded game message (JSON lines) to work out new message layouts
```

To find a cube id for a new zone, run with `--log`, open a cube, and look for `object event: ... message 3536 040300` in the log.

## Window

The sidebar lists features. Each feature has its own tabs, overlay and settings.

Each feature has an **On/Off switch** at the right of its tab bar. A feature that's off does nothing in the background: its game messages aren't parsed, there are no alerts, its overlay is hidden (it comes back when switched on), and View cone doesn't read the screen. The sidebar marks it "· off". Its settings can still be changed. For View cone, the switch replaces the old "Show the vision cone" checkbox.

- **Cubes**
  - **Radar:** radar and cube list.
  - **Overlay:** the cube minimap overlay.
  - **Settings:** alert sound, volume, and the cube types being tracked.
- **Monsters**
  - **Radar:** watched monsters as coloured dots, plus a "Nearby" list of every named monster in range (count, nearest distance, level). Click a name to watch or unwatch it. **Watched / All** switches between only watched monsters and every monster.
  - **Overlay:** its own minimap overlay (red frame), separate from the cube one, with its own position and scale.
  - **Settings:** search all 7,545 monster names, build a watch list (several at once, each with its own colour), names on the overlay (off by default: clutter), include unnamed NPCs, beep when a watched monster appears, and **your position on top** (on by default: your white dot is drawn above every monster dot, on the radar and at the overlay's centre).

Every feature's background work keeps running whichever feature or tab is shown: capture, alerts and its overlay. **Ctrl+Shift+O** locks every shown overlay, or unlocks them all if all are locked. The sidebar shows short states like "Monsters: overlay locked", and **Keep on top** at its bottom applies to the whole window. The last feature and tab you used are remembered.

- **Gathering**
  - **Radar:** live nodes as solid dots, and nodes from the map data that the game hasn't announced yet as hollow circles. A Nearby list shows "● live ○ on the map" counts and the nearest distance per resource. Click a name to watch it, and use **Watched / All** to switch.
  - **Overlay:** its own minimap overlay (green frame).
  - **Settings:** search all resources (Diamond/Ruby/Sapphire Gemstone, Orichalcum Ore, Odyle, Aria, Yggdrasil Log...), watch several (each with its own colour), map-only nodes on/off, your position on top, a beep when a watched resource appears, and objects without a known name.

  Names: the game announces each node with a type id (Diamond = 1500021) but no name. The first time a type shows up, its position is matched against the local map data (`data/maps`, credit Shugo.GG). That also tells which map you're on, and the name is then saved for that type for good. Map data exists for Verteron, Altgard and Lower Reshanta so far.
- **View cone** (Windows): mirrors the camera cone from the game's minimap onto the Cubes and Monsters radars (a soft wedge) and overlays (an outline, so the minimap stays visible).

### How the view cone is read

The game traffic doesn't contain your camera direction, so it's read from the screen. About 15 times a second, on its own thread, the app captures only the small minimap area you located (about 6 ms) and reads it (about 5 ms). Only the cone is redrawn, every 50 ms, so it follows the camera without waiting for the rest of the window:

- **White edges first:** AION 2 draws the cone with two white edge lines. The camera sits behind your character, so the lines pass beside the white disc around your arrow instead of starting at it. The app finds the two longest straight white lines around the disc (Hough transform, judged by length so short icon outlines don't count). Icons drawn over a line are tolerated, and the pair must be 80-150° apart, like the real cone. Your view is between them. If only one edge is visible, the cone goes on its lighter side. Clarity is 1.5-2.
- **Brightness fallback:** without clear edges, it looks for the lighter wedge instead (clarity about 0-1).

On two real minimap screenshots (2026-10-14) it read the cone within 3°, with the locate circle up to 3 px off-centre.

1. On **View cone → Setup**, turn the switch at the top right **On**.
2. Click **Locate minimap**. Drag the yellow circle onto your arrow, use the mouse wheel until it matches the cone's length, and right-click when done.
3. **What it sees** shows the captured area with the detected cone drawn on it. **Sensitivity** sets how clear the cone must be before it's shown (default 0.6; above 1.0 only white-edge cones count).

It only reads the screen while something shows the cone: the View cone page, a Radar tab with the window not minimised, or a shown overlay. Otherwise, and whenever it's switched off, it captures and analyses nothing. The overlays show up in screenshots and recordings. They can't confuse the reader, because it only looks for near-white lines: the overlays draw their cone in cyan, and your white dot sits inside the arrow area the reader skips. With a visible overlay on top of a test minimap, 24 angles read within 1°. The minimap must be visible, which means borderless or windowed mode. In tests on synthetic minimaps, the cone was read within about 3–16° and never invented where there was none.

### How monsters are read

- `41 36` "monster in range": id, monster type (u32), position. Names come from `data/monsters.json`. Types missing from it are NPCs such as townsfolk, hidden unless "Include NPCs" is on.
- `*8` / `+8` movement: the last 12 bytes are where the monster is or is heading. A monster chasing you reports your position, so it shows at ~0 m.
- `04 8d`: something died, so it's removed.
- No "left range" message is sent for monsters, so ones more than 150 m from you are dropped.

Checked live on 2026-10-14: Drana and Krall monsters at 11-29 m with the right names, and kills removed.

## Radar

- **Up is north on the map.** It is not your camera direction, because the game traffic does not include it.
- 1 map pixel = 1 m, and distances are horizontal.
- **Colour and shape show height** (a legend is on the radar):
  - **Cyan ▲:** 3 m or more above you
  - **Magenta ▼:** 3 m or more below you
  - **Gold ■:** roughly level

  Labels read like `50 m +12`, and the list rows use the same colours.
- **Solid:** the cube is in range. **Dimmed outline:** last seen (it left your range, or no update for 60 s). Opened cubes are hidden on the radar and greyed in the list. Pins are dropped after 20 minutes.
- An outline pin at the radar's edge is a cube beyond the radar range. You are the white dot.
- Changing character, teleporting or losing the stream clears pins and position.

## Minimap overlay

The **Overlay** page opens a borderless, see-through layer that shows only the cube pins, for placing over the game's minimap. The minimap is north-up, like the pins. The game must run in **borderless or windowed** mode, because no overlay can draw over exclusive fullscreen.

1. **Unlocked:** a dashed frame, distance rings and a crosshair show.
   - Drag to move it, and line the crosshair up with your arrow.
   - Drag the bottom-right corner to resize it to the minimap.
   - Use the mouse wheel to change the scale until the rings match the game's distances. The minimap's own distance numbers (e.g. "801m") help here.
2. **Lock** with a right-click on the overlay, the **Lock** button, or **Ctrl+Shift+O**, which works while the game has focus. Locked, only pins show and clicks pass through to the game.
3. **Unlock** again with **Unlock** or Ctrl+Shift+O.

Cubes outside the overlay's area sit on its border as outline pins, pointing the right way. Each overlay has its own **Show view cone on this overlay** checkbox on its Overlay tab. When it's off, that overlay doesn't draw the cone, and if nothing else shows the cone, the screen isn't read at all. Position, size, scale, lock state and whether it's open are remembered.

On Linux, the overlay is a semi-transparent window instead of a cut-out, and clicks don't pass through it. Lock it to stop moving it by accident. The Ctrl+Shift+O hotkey is Windows-only.

Cube callouts in `sounds/` are the synthetic voice clips from Grip Strength Companion; see `sounds/ATTRIBUTION.txt`.
