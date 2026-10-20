# Daeva Radar

A companion radar for **AION 2** that runs next to the game and shows what's around you:

- 🧊 **Hidden Cubes** with a callout when one appears, and their spawn points
- ✧ **Empyrean Traces** (feathers), remembering the ones you've collected
- ☠ **Monsters** you're hunting, with a watch list
- ❀ **Pets**: your collection's progress to Lv3, a Pet tracker card over the game, and where the monsters that drop the
  souls you need spawn
- ⛏ **Gathering** nodes (ore, gems, Odyle, herbs…), including ones the game hasn't shown you yet
- ❖ **Wardrobe**: your Closet by set, and every piece you're still missing
- ⚖ **Shop**: the rotating shop's offers marked owned / missing, with a pop-up for the deals you care about
- 🗺 A **world map** of soul spawns and gathering nodes, **minimap overlays** that sit on top of the game's minimap,
  and timed **alerts** for recurring events

It only listens: it reads the game's network traffic on your PC and never changes, sends or uploads anything.

> ⚠️ **Use at your own risk.** NCSOFT's rules prohibit third-party tools that read the game's traffic, even
> read-only ones like this. Using it could get your account sanctioned.

## Quick start (Windows)

1. Download **`DaevaRadar-<version>-win64.zip`** from the
   [latest release](https://github.com/Noragiri/daeva_radar/releases/latest) and unzip it anywhere. Keep the folder
   together: the exe needs the files beside it. No Python needed.
2. Start **`DaevaRadar.exe`** and accept the administrator prompt (capturing the game's traffic needs it).
   - If Windows says *"Windows protected your PC"*, click **More info → Run anyway** (the exe isn't code-signed).
   - Want a Desktop shortcut? Run `DaevaRadar.exe --shortcut` once.
   - Just looking around? `DaevaRadar.exe --demo` runs the window on simulated data, without the game or admin rights.
3. The first time, Settings opens with a **notice** (an unofficial tool, at your own risk; what it keeps and sends). Nothing is
   read until you tick both boxes and press **I agree**.
4. Daeva Radar lives in the **tray** (bottom right of the taskbar): double-click its icon for **Settings**,
   right-click it for the overlays and **Quit**. Closing Settings keeps it running.
5. **Start the radar before you enter the world** (or teleport / change zone once it's running), so it learns your
   character, your position, your pets and your wardrobe.
6. Play in **borderless or windowed** mode if you want the overlays on top of the game.

From source, or on Linux under Wine / Proton: see [Running from source](docs/GUIDE.md#running-from-source).

## A quick tour

- **Settings** (double-click the tray icon) lists the features. Each has an **On/Off** switch, its tabs (radar,
  lists, settings) and its overlays; a feature that's off does nothing at all.
- **Overlay layout** (in Settings or the tray) puts a grid over the game: drag every overlay into place, then Esc.
- **The Pet tracker** card shows your target pet's souls to its next level and the pets whose soul monsters are
  around you.
- **The radar** is north-up with you as the white dot. Colour and shape show height: **cyan ▲** above you, **magenta ▼**
  below, **gold ■** level. Hollow circles are spawn points from the map data, solid pins are what's there right now.
- **Monsters → Pets** lists your pets. Double-click one to make it a *target*: the monsters that drop its souls then
  show on the radar and the overlay, in its colour.
- **Map ↗** (in Pets and in Gathering) opens a big world map with every soul spawn and gathering node; click a
  resource there to highlight it everywhere.
- **Overlays:** switch one on in its feature's page (or the tray), drag it onto the game's minimap, scale it with
  the mouse wheel until the rings match, then lock it with **Ctrl+Shift+O** so clicks go through to the game.
- **Settings → General → Map you're on:** **Auto** usually works it out; pick the map yourself if it's slow.

The full feature reference is in the **[user guide](docs/GUIDE.md)**.

## Updates

The app checks for a new version at start and every hour. When one is out, a pop-up says so and **Settings →
General** shows **Update and restart**: click it, and it updates itself and restarts. Your settings are kept. (The check only asks GitHub for the
latest version and sends nothing about you or the game.)

## Troubleshooting

- **Nothing shows up:** teleport, change zone or relog once with the radar running, so it learns your character.
  Check the status bar at the bottom: it should say "playing as …".
- **My position doesn't move when I walk:** the game only reports it when you teleport, dash or have an aura
  active; keep an aura on for steady updates. See
  [Reading the radar](docs/GUIDE.md#reading-the-radar).
- **No pets or wardrobe yet:** they're read when you enter the world: relog once with the radar running.
- **The shop list is out of date:** the game only sends it when you open the shop; open it again after x:00.
- **Overlays don't appear over the game:** switch the game to borderless or windowed mode.
- **Antivirus or SmartScreen warnings:** the exe isn't code-signed and captures network traffic, which some tools
  flag. It's built from this repository's source by GitHub's release workflow.
- **Wrong map:** pick it in **Settings → General** instead of Auto.

## More

- [User guide](docs/GUIDE.md): every feature, setting and option
- [How it works](docs/TECHNICAL.md): capture, the game messages it reads, the code layout, tests and releases
- [Data sources](data/SOURCE.md)

## Credits

AION 2 game data and map art © NCSOFT (official AION 2 web API), collected via **Shugo.GG** and **metabot.gg**; see
[data/SOURCE.md](data/SOURCE.md). Packet capture on Windows uses [WinDivert](https://reqrypt.org/windivert.html)
(LGPLv3 / GPLv2, `windivert/LICENSE`). The cube callouts in `sounds/` are synthetic voice clips from Grip Strength
Companion (`sounds/ATTRIBUTION.txt`). Daeva Radar started as the cube feature of Grip Strength Companion (formerly
**Cube Finder**). It's a fan-made tool, not affiliated with NCSOFT.
