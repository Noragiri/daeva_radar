# Game data tables

- `monsters.json`: monster type id -> `[name, level, rank]` (7,545 entries).
- `skills.json`: skill id -> name (412 entries).

Copied unchanged on 2026-10-14 from the bundle of Grip Strength Companion (version 2026.10.12). That app's
notes do not say where these tables come from. Names are AION 2 game content © NCSOFT. Treat the tables as
reference data for this personal tool and don't redistribute them as a dataset of your own.

Type ids are the numbers the game sends in its `41 36` "monster in range" message (see cube_finder.py).
Tables may be out of date after a game patch.

## Map data (`maps/*.json`)

Gathering-node and hidden-cube positions per map (Verteron, Altgard, Lower Reshanta), downloaded once on
2026-10-14 by `dev/fetch_map_data.py` from the public marker files of **Shugo.GG**
(https://shugo.gg/maps/<map>/markers.json, which the site's guide for AI agents, https://shugo.gg/llms.txt,
points to). Credit: Shugo.GG, https://shugo.gg. The app only reads these local copies and never goes online.
Re-run the script after a game patch to refresh them; if the site is gone, the copies keep working.
Positions are map pixels on a 4096 px map: pixel = game units / 200 + 2048.
