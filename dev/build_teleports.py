"""data/teleports.json: the obelisks (teleport points) of each map with their number in the game's "Complete List".

Positions: the 'Kibelisk' places of data/pet_spawns/<map>.json (map pixels). Numbers: read off the game's list by
hand (2026-10-22, Verteron; the list names are cut off with "..." so they're matched by their start). Maps without a
list here have no numbers yet (the routes then show the name). Where two places share a start (the two Cantas
Valley Mushroom Tree peaks, the two Garden of the Illusion God observatories) the order is a guess.
"""
import json
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
LISTS = {
    "verteron": """Dawn Legion Base|Valley Outpost|Western Cantas Valley C|Coastal Camp|Eastern Cantas Valley Ca|
Cantas Valley Mushroom|Cantas Valley Mushroom|Cantas Valley Sky Island|Marsh Outpost|Collapsed Verteron Bar|
Spirit's Rest|Ellun River Marsh Cliff|Illusion Cavaliers' Camp|Hideout in Front of the|Pamos Farm|
Ruin Exploration Campsi|Ellun River Midstream H|Ellun River Midstream P|Collapsed Pillar|Illusion Oratory Entrance|
Verteron Fortress Valley|Dawn Legion Temporary|Zumion Temple Pilgrima|Zumion Statue|Wind Breeze Merchants|
Southern Shugo Station|Astin Mercenaries Base|Eastern Shugo Station|Aullaeu Village Border|Tolbas Forest Outskirts|
Aullaeu Funeral Island|Abandoned Wind Breez|Aullaeu Village Valley|Forest of Exiles|Aullaeu Village Sky Island|
Zumion Village|Rock Hill Outpost|Canyon Campsite|Canyon Sky Island|Scar of the Wind|Alert Observation Post|
Hill of Purification|Altamia Highland Ruins|Ancient Zumion Temple|Beast Hunter Campsite|Highland's End Campsite|
Southern Altamia Coast|Kraka Footpath Campsite|Kraka Uphill Path|Kraka Footpath Wastew|Nahid Brigade Fortress|
Notos Legion Outpost|Laborer's Rest Stop|Drana Transport Hub|Scarlet Forest Observat|Red Maple Forest|
Nahid Disposal Plant|Pilgrim's Rest|Garden of the Illusion G|Garden of the Illusion G|Crimson Isle Outpost""",
}
out = {}
for path in sorted((DATA / "pet_spawns").glob("*.json")):
    places = [p for p in json.loads(path.read_text(encoding="utf-8"))["places"] if p[1] == "Kibelisk"]
    if not places:
        continue
    numbers = {}
    if path.stem in LISTS:
        left = list(places)
        for n, start in enumerate(LISTS[path.stem].replace("\n", "").split("|"), 1):
            hit = next(p for p in left if p[0].startswith(start))
            left.remove(hit)
            numbers[id(hit)] = n
        assert not left, left
    rows = [[numbers.get(id(p)), p[0], p[2], p[3]] for p in places]
    rows.sort(key=lambda r: (r[0] is None, r[0] or 0, r[1]))
    out[path.stem] = rows
(DATA / "teleports.json").write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding="utf-8")
print({k: len(v) for k, v in out.items()})
