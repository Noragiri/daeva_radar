# Game data tables

## Credits

AION 2 game data and map art © NCSOFT (official AION 2 web API), collected via Shugo.GG and metabot.gg. The data comes from NCSOFT's official AION 2 web endpoints and the game client;
the sites below are where these copies were downloaded from, per file. Names, data and art are AION 2 game
content © NCSOFT, kept here as reference data for this personal tool, not to be redistributed as a dataset of
its own.

## Monster and skill names (`monsters.json`, `skills.json`)

- `monsters.json`: monster type id -> `[name, level, rank]` (7,545 entries).
- `skills.json`: skill id -> name (412 entries).

Copied unchanged on 2026-10-14 from the bundle of Grip Strength Companion (version 2026.10.12). That app's
notes do not say where these tables come from. Names are AION 2 game content © NCSOFT. Treat the tables as
reference data for this personal tool and don't redistribute them as a dataset of your own.

Type ids are the numbers the game sends in its `41 36` "monster in range" message (see daeva_radar.py).
Tables may be out of date after a game patch.

## Map data (`maps/*.json`)

Gathering-node and hidden-cube positions per map (Verteron, Altgard, Lower Reshanta), downloaded once on
2026-10-14 by `dev/fetch_map_data.py` from the public marker files of **Shugo.GG**
(https://shugo.gg/maps/<map>/markers.json, which the site's guide for AI agents, https://shugo.gg/llms.txt,
points to). Credit: Shugo.GG, https://shugo.gg. The app only reads these local copies and never goes online.
Re-run the script after a game patch to refresh them; if the site is gone, the copies keep working.
Positions are map pixels on a 4096 px map: pixel = game units / 200 + 2048.

## Pets (`pets.json`, `pet_spawns/*.json`)

Written once on 2026-10-15 by `dev/fetch_pet_data.py`; re-run it after a game patch.

- The 207 pets of the Global client (200 collection pets + 7 Special), with names and genus: gegebase.com and
  wikily.gg.
- Which monsters drop each pet's soul, with level and drop chance: metabot.gg's public item pages
  (`/en/aion-2/items/soul-<pet>-bound`, global client data). Credit: metabot.gg.
- Where those monsters spawn (`pet_spawns/<map>.json`, map pixels like `maps/`) and named places for the Pets map:
  the same public Shugo.GG marker files as above. Their monster templates carry the type id the game sends in
  `41 36`; where a map's templates don't, the id comes from `monsters.json` by name and level. Shugo.GG had no
  monster markers for Eltnen or Morheim yet, so pets that only drop there show no spawn points.

## Wardrobe (`skins.json`)

Written once on 2026-10-15 by `dev/fetch_skin_data.py` from metabot.gg's public skin pages (global client data;
credit metabot.gg): armour and cloak sets (`/en/aion-2/skins`, `/skins/sets/<set>`) and weapon skin series
(`/skins/weapons/<series>`). A skin's item id is set id x 100 + piece: armour 01 Top, 02 Legs, 03 Helm,
04 Pauldrons, 05 Gloves, 06 Shoes, 07 Cloak; weapons 01 Greatsword, 02 Longsword, 03 Dagger, 04 Bow,
05 Spellbook, 06 Orb, 07 Mace, 08 Staff. Accessories (ids 500..., 01 Earrings, 03 Necklace) aren't on those pages
yet and show by id; 700... ids are weapon skins numbered like the series. Leather Crop Fit (400481) is the special
kind: the Closet doesn't count it as Armor. All checked against the in-game Closet's counts on 2026-10-15.

## World map images (`map_tiles/<map>/<z>/<x>_<y>.webp`)

Downloaded once on 2026-10-15 by `dev/fetch_map_tiles.py` from Shugo.GG's public map tiles (the images of its
interactive map, https://shugo.gg/map/<map>). 256 px WebP tiles, zoom 0-4; zoom 4 is the same 4096 px map as the
pixel coordinates above (Middle and Upper Reshanta fill its top-left 2048 px). Credit: Shugo.GG. The map art is
AION 2 game content © NCSOFT; it's here for this personal tool's Map window, not to be redistributed on its own.
The Map window shows them with Pillow (`pip install pillow`).

## Class icons (`assets/classes/<class>.png`)

Downloaded on 2026-10-18 from NCSOFT's official AION 2 site CDN
(`https://assets.playnccdn.com/static-aion2/characters/img/class/class_icon_<class>.png`, the icons of the
official character pages; Shugo.GG links the same files). 256 px PNGs with transparency, for gladiator, templar,
assassin, ranger, sorcerer, elementalist (Spiritmaster), cleric, chanter and fighter (Brawler). Art © NCSOFT.
`64/` holds 64 px copies (transparent margins trimmed, centred on a square) for Discord emoji.
