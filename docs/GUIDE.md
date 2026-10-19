# Daeva Radar: user guide

Everything the app does, feature by feature. New here? Start with the [README](../README.md). How it works under
the hood is in [TECHNICAL.md](TECHNICAL.md).

## The tray, Settings and overlays

Daeva Radar runs from the **tray icon** (bottom right of the taskbar; it may be under the ^ arrow). Double-click it
for **Settings**; right-click it for the menu: Settings, each overlay on / off, **Lock overlays**, **Edit overlay layout**, the
World map and **Quit**. Closing Settings keeps the radar running in the tray. (Without a tray icon, Settings is the
app: closing it quits.)

- **Settings** has a page per feature (its On/Off switch, its tabs: radar, lists, settings, and its overlays),
  **General** (map, start-up, updates, troubleshooting), **Overlay layout** and **About**. Turn off *Open Settings when Daeva Radar starts* to start
  straight in the tray.
- **Overlays:** every feature can have its minimap overlay and an info card (the **Pet tracker**: your target pet's
  souls to its next level and how many of its soul monsters are in range, then the other pets whose soul monsters
  are around you). Unlocked, drag them; right-click (or the pin on a card) locks them so clicks go to the game.
  **Ctrl+Shift+O** locks / unlocks every shown one.
- **Overlay layout** (Settings, or the tray's *Edit overlay layout*) puts a see-through grid over the game window
  (the main screen when the game isn't running) and unlocks every shown overlay: drag them, and a minimap's corner or
  a card's right edge to resize, snapping to the grid (off / 10-80 px in the bar at the top). Hidden ones are dashed
  boxes you can place too. **Done** or **Esc** locks everything back as it was.
- **Characters** (under General) lists every character the radar has seen log in, by name, with when you last
  played it and the maps it was detected on. Set each one's faction, **Elyos** or **Asmodian** (click again to
  unset); unset, it's guessed from those maps. **Forget** removes one until it logs in again.
- **On/Off switch** (top right of each feature's page, or the dot beside it in the sidebar): a feature that's off does nothing in the background: no
  alerts, its overlay hidden (it comes back when switched on), and View cone doesn't read the screen. The sidebar
  dims it. Its settings can still be changed.
- Every feature's background work keeps running whichever page is shown, and with Settings closed: alerts, pop-ups
  and overlays.
- **General:** the map you're on (see [Map picker](#map-picker)), start-up, *Keep the World map and Position debug
  on top*, updates, and **⌖ Position debug** (your position, its last packet and how often it updates).
- The last page and tab you used are remembered.

## Features

### Cubes

- **Radar:** radar and cube list, plus hidden-cube spawn points from the map data as hollow red circles, so you can
  fly around them before any cube shows up.
- **Overlay:** the cube minimap overlay.
- **Settings:** alert sound (a voice callout, a beep or your own file from `sounds/custom/`), volume, spawn points
  on/off, and the cube types being tracked.

### Feathers (Empyrean Traces)

- **Radar:** every feather spot from the map data (Verteron 559, Altgard 559, Lower Reshanta 200, Middle Reshanta 100)
  as bronze hollow circles, live feathers as solid bronze pins (shape = height, like the cube pins) with distance
  and height, and a list of the nearest ones. The last row counts what you've collected on this map.
- The first feather that shows up on a spot teaches its type on each map (Verteron's is built in), saved for good;
  Settings lists them, with **Forget learned ids**. Live feathers show solid and beep.
- **Collected:** picking a feather up marks its spot collected (saved per map) and it disappears from the radar,
  even though the game keeps showing it to other players. **✓ Mark nearest collected** / **Undo** do it by hand for
  the nearest spot within 10 m. **Collected spots too** in Settings shows them dimmed; **Reset collected on this
  map** starts over.
- **Overlay:** its own minimap overlay (bronze frame).

### Monsters (and pets)

- **Radar:** watched monsters as coloured dots, plus a "Nearby" list of every named monster in range (count, nearest
  distance, level). Click a name to watch or unwatch it. **Watched / All** switches between only watched monsters
  and every monster. Target pets add the monsters that drop their souls, in the pet's colour: solid when in range
  (`❀ Ridge Spider x5 30 m → Mutated Cadaver Insectoid`), hollow where they spawn, with the nearest spawn listed.
- **Pets:** your pet collection (account-wide), read from the game when you enter the world and updated by every
  soul drop: collected, Lv3 and soul totals, a list with each pet's stages (5 / 25 / 75 souls) and souls to Lv3,
  filters, and which monsters drop the selected pet's souls, where and how often. Double-click a pet to make it a
  target. **Map ↗** opens the [Map window](#map-window) on its Pets side.
- **Soul drops** show on the **Pet tracker** card (on by default): your target pet's souls to its next level, live,
  and the other unfinished pets whose soul monsters are around you.
- **Overlay:** its own minimap overlay (red frame), with target pets' soul monsters and their spawn points too.
- **Settings:** search all 7,545 monster names, build a watch list (several at once, each with its own colour),
  names on the overlay, include unnamed NPCs, beep when a watched monster (or a target pet's soul monster) appears,
  and your position on top. Pets: the soul-drop pop-up (every pet, target pets only, or off), a beep with it, and
  the soul monsters' spawn points / live dots on or off.

### Gathering

- **Radar:** live nodes as solid dots, and nodes from the map data that the game hasn't announced yet as hollow
  circles. A Nearby list shows "● live ○ on the map" counts and the nearest distance per resource. Click a name to
  watch it, and use **Watched / All** to switch. **Map ↗** opens the [Map window](#map-window) on its Resources side.
- **Overlay:** its own minimap overlay (green frame).
- **Settings:** search all resources (Diamond/Ruby/Sapphire Gemstone, Orichalcum Ore, Odyle, Aria, Yggdrasil Log…),
  watch several (each with its own colour) or a whole group with the **Ore / Odyle / Gem / Log / Herb / Ingredient**
  buttons (click again to unwatch it), map-only nodes on/off, your position on top, a beep when a watched resource
  appears, and objects without a known name.
- Names: the first time a node type shows up, its position is matched against the map data, and the name is saved
  for that type for good. Map data exists for Verteron, Altgard and Lower Reshanta so far.

### Wardrobe

Your appearance collection (the game's Closet, account-wide: any character's login updates it):

- Pieces owned, sets started and complete, Armour and Accessories against the Closet's totals (e.g. 134/766 · 17%),
  and each weapon type out of 56: the same numbers as the Closet's Collection Effect screen.
- A list of sets (armour, weapon series, accessories, the special kind) with owned / total pieces; select one for its
  pieces (✓ owned, · missing).
- Filters: kind, Owned / Incomplete / Complete / Not started / All sets, and **Missing pieces**: every piece you don't
  own, one per line, sets you've started first. **Copy list** puts the shown list on the clipboard.
- Accessory sets (Earrings, Necklace) show by number until their names are known.

### Shop

The rotating shop (Special, Pets, Skins; new offers every hour at x:00, and each character has its own):

- **Offers:** the offers you last saw in game, with discount, name, price and grade, and **✓ owned / ✗ missing /
  ★ target pet** from your pet collection and wardrobe. A character picker shows your other characters' shops.
  The game sends the offers only when you open the shop, so after x:00 the list is marked out of date until you
  open it again.
- **A pop-up for deals:** when you open the shop, the offers that pass your rules pop up once per shop hour.
- **Settings:** the minimum discount (−10 % to −50 %), only items you don't own, always your target pets, which
  kinds (pets, skins, theostones), a beep, and an optional reminder at x:00 that new offers are up.

### Alerts

Timed alarms for the game's recurring events (Shugo Festival, Dimensional Invasion, Spacetime Rift…) and your own.

### View cone (Windows)

Mirrors the camera cone from the game's minimap onto the radars (a soft wedge) and overlays (an outline). The game
traffic doesn't contain your camera direction, so it's read from the minimap on screen.

1. On **View cone → Setup**, turn the switch at the top right **On**.
2. Click **Locate minimap**. Drag the yellow circle onto your arrow, use the mouse wheel until it matches the cone's
   length, and right-click when done.
3. **What it sees** shows the captured area with the detected cone. **Sensitivity** sets how clear the cone must be
   before it's shown (default 0.6; above 1.0 only white-edge cones count).

It only reads the screen while something shows the cone (the View cone page, a Radar tab with the window not
minimised, or a shown overlay), and the minimap must be visible: borderless or windowed mode.

## Map picker

**Settings → General → Map you're on** picks the map the map data is for, shared by Cubes (spawn points), Feathers, Gathering (names,
map-only nodes) and Pets (spawns). **Auto** uses the detected map: live gathering nodes decide when there are any,
else live cubes (at least two must sit on spawn points); pets also detect it from soul monsters. Pick a map by hand
when detection is slow or wrong; the line under it shows what was detected.

## Map window

Opened with **Map ↗** from Monsters → Pets or Gathering → Radar, **World map** in the tray or Settings → General (one
window for all, in the Settings style with its own top bar): the world map with layers for
pets' soul spawns (coloured by genus), hidden cubes and each gathering group (Ore, Gems, Odyle, Herbs, Wood,
Ingredients). Wheel to zoom, drag to pan, click a point for what it is. The side panel has two sides:

- **Pets:** the pets with soul spawns on that map (type filter, "unfinished only", search by pet or monster), their
  level and souls; click one to see its spawns, double-click to target it. Totals: how many are Lv3 and the souls
  still to go.
- **Resources:** every resource on that map with its node count; click one to see its nodes, double-click (or ★) to
  highlight it. Highlighted resources are the ones watched in Gathering, so its radar and overlay follow; they're
  drawn bigger in their watch colour even with their layer off.

It reopens at start if it was open when the app closed, and stays above the game with **Keep the World map and
Position debug on top** (Settings → General).

## Reading the radar

- **Up is north on the map**, not your camera direction (the game traffic doesn't include it; the
  [view cone](#view-cone-windows) shows where you look). 1 map pixel = 1 m; distances are horizontal.
- **Colour and shape show height** (a legend is on the radar): **cyan ▲** 3 m or more above you, **magenta ▼** 3 m
  or more below, **gold ■** roughly level. Labels read like `50 m +12`, and the list rows use the same colours.
- **Solid:** in range. **Dimmed outline:** last seen (it left your range, or no update for 60 s). Opened cubes are
  hidden on the radar and greyed in the list. Pins are dropped after 20 minutes.
- An outline pin at the radar's edge is beyond the radar range. **You are the white dot.**
- **Hollow red circle:** a hidden-cube spawn point (they have no height; one with a tracked cube on it is hidden).
- Changing character, teleporting or losing the stream clears pins and position.

**Your position:** the server sends it only when you teleport, dash or have an aura ticking (about once a second).
Walking isn't sent, so without an aura your position only updates on dashes and teleports.

## Minimap overlays

Each feature's **Overlay** tab opens a borderless, see-through layer for placing over the game's minimap (north-up,
like the radar). The game must run in **borderless or windowed** mode.

1. **Unlocked:** a dashed frame, distance rings and a crosshair show. Drag to move it and line the crosshair up with
   your arrow; drag the bottom-right corner to resize it to the minimap; use the mouse wheel to scale it until the
   rings match the game's distances (the minimap's own numbers, e.g. "801m", help).
2. **Lock** with a right-click on the overlay, the **Lock** button, or **Ctrl+Shift+O** (works in game). Locked, only
   the pins show and clicks pass through to the game.
3. **Unlock** again with **Unlock** or Ctrl+Shift+O. **Ctrl+Shift+O** locks every shown overlay, or unlocks them all.

Things outside the overlay's area sit on its border as outline pins, pointing the right way. Each overlay has its
own **Show view cone on this overlay** checkbox. Position, size, scale, lock state and whether it's open are
remembered. On Linux, the overlay is a semi-transparent window and clicks don't pass through it; the hotkey is
Windows-only.

## Running from source

### Windows

1. Install Python 3.10+ for Windows x64 (tkinter included), then Pillow (the Map's images and the tray icon),
   pystray (the tray) and pywebview (the Settings window): `python -m pip install pillow pystray pywebview`.
   Without pywebview, Settings opens in your web browser instead.
2. The WinDivert 2.2 files `WinDivert.dll` and `WinDivert64.sys` are in `windivert/`.
3. Double-click the **Daeva Radar** shortcut (or `Daeva Radar.bat`) and accept the administrator prompt. If you move
   the folder, run `python daeva_radar.py --shortcut` once to recreate the shortcut, here and on the Desktop. For
   console output, run `python daeva_radar.py --console`.

### Linux (AION2 under Wine / Proton)

1. Install Python 3.10+ with tkinter (`python3-tk` on Debian/Ubuntu, `tk` on Arch/Fedora) and Pillow with Tk support
   (`python3-pil.imagetk` on Debian/Ubuntu, or `python3 -m pip install pillow`).
2. Run `sh daeva_radar.sh` (or `python3 daeva_radar.py`) as your normal user.
3. A small capture helper asks for your password through `pkexec` (or `sudo` in a terminal), because capturing needs
   root. Only the helper runs as root; the window, settings and sound stay with your user.
4. Sound uses the first player found: WAV with `paplay`, `pw-play` or `aplay`; MP3 with `mpv` (up to 200 %),
   `ffplay` or `mpg123` (up to 100 %).

Settings are kept in `%APPDATA%\DaevaRadar` on Windows and `~/.config/DaevaRadar` on Linux.

### Command-line options

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
--no-update-check  don't check GitHub for a new version
--demo             no game, capture or admin rights: simulated player, cubes, monsters and pets
--log FILE         write a log; it includes the type id of anything you open within 10 m
--record FILE      save every decoded game message (JSON lines) to work out new message layouts
--debug            open the Position debug window
--shortcut         Windows: create a 'Daeva Radar' shortcut here and on the Desktop
```

The exe takes the same options (`DaevaRadar.exe --demo`).
