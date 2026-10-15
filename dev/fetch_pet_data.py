"""One-time download of pet reference data into data/pets.json and data/pet_spawns/<map>.json.

    python dev/fetch_pet_data.py [--seed path/to/pets_global.json] [--cache DIR]

- Pets (id, name, genus) of the Global client: kept from the existing data/pets.json (first run: --seed, a
  {"pets": {id: {"name", "genus"}}} file built from gegebase.com and wikily.gg).
- Which monsters drop each pet's soul, with level and chance: metabot.gg's public item pages
  (/en/aion-2/items/soul-<pet>-bound, "Dropped by" table; global client data). Pages are cached in --cache and
  requests are throttled, so re-runs only fetch what's missing.
- Where those monsters spawn: Shugo.GG's public marker files (https://shugo.gg/maps/<map>/markers.json, the same
  files dev/fetch_map_data.py reads; its guide for AI agents, https://shugo.gg/llms.txt, points to them). Each
  monster template there carries its type id (the id the game sends in '41 36'), joined to the drop tables by
  name and level. Map pixels on a 4096 px map: pixel = game units / 200 + 2048.
The app only reads the local copies this script writes.
"""
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PETS = ROOT / "data" / "pets.json"
SPAWNS = ROOT / "data" / "pet_spawns"
MAPS = ("poeta", "verteron", "eltnen", "ishalgen", "altgard", "morheim",
        "reshanta-lower", "reshanta-middle", "reshanta-upper")
PLACES = ("Village", "BattleField", "Kibelisk")  # labels for the Pets map
METABOT = "https://metabot.gg/en/aion-2"
UA = {"User-Agent": "DaevaRadar/1.0 (personal map cache; credit Shugo.GG, metabot.gg)"}


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower().replace("'", "")).strip("-")


def get(url, timeout=30):
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return ""
            if attempt == 3:
                raise
        except OSError:
            if attempt == 3:
                raise
        time.sleep(5 * (attempt + 1))


def metabot_page(path, cache):
    f = cache / (path.replace("/", "__") + ".html")
    if f.exists():
        return f.read_text(encoding="utf-8")
    time.sleep(1.0)
    text = get("%s/%s" % (METABOT, path))
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")
    return text


def soul_drops(page):
    """[(monster, level, chance %)] from a soul item page's 'Dropped by' table."""
    s = re.sub(r"<(script|style)\b.*?</\1>", "", page, flags=re.S)
    lines = [l.strip() for l in html.unescape(re.sub(r"<[^>]+>", "\n", s)).split("\n") if l.strip()]
    try:
        k = lines.index("Kills for 90%", next(i for i, l in enumerate(lines) if l.startswith("Dropped by"))) + 1
    except (StopIteration, ValueError):
        return []
    rows, seen = [], set()
    while k + 4 < len(lines) and lines[k + 1].isdigit() and lines[k + 2].endswith("%"):
        row = (lines[k], int(lines[k + 1]), float(lines[k + 2].rstrip("%")))
        if row[:2] not in seen:
            seen.add(row[:2])
            rows.append(row)
        k += 5
    return rows


def known_types():
    """data/monsters.json: (name, level) -> type id, for marker files whose templates don't link one."""
    table = json.loads((ROOT / "data" / "monsters.json").read_text(encoding="utf-8"))
    out = {}
    for type_id, (name, level, rank) in table.items():
        out.setdefault((name, level), int(type_id))
    return out


def monster_templates(markers, types):
    """Shugo markers -> {type id: {"name", "level", "points"}} for every monster template with a known id."""
    cats = {c["id"] for c in markers["categories"] if c.get("group") == "Monsters"}
    by_index = {}
    for i, t in enumerate(markers["templates"]):
        if t["c"] not in cats:
            continue
        lv = re.search(r"Lv (\d+)", t.get("s", ""))
        level = int(lv.group(1)) if lv else None
        m = re.search(r"/monsters/(\d+)", t.get("l", ""))
        type_id = int(m.group(1)) if m else types.get((t["n"], level))
        if type_id:
            by_index[i] = (type_id, t["n"], level)
    out = {}
    for i, x, y in markers["points"]:
        if i in by_index:
            type_id, name, level = by_index[i]
            out.setdefault(type_id, {"name": name, "level": level, "points": []})["points"].append([x, y])
    return out


def places(markers):
    keep = {i: (t["n"], t["c"]) for i, t in enumerate(markers["templates"]) if t["c"] in PLACES}
    return [[keep[i][0], keep[i][1], x, y] for i, x, y in markers["points"] if i in keep]


def main():
    args = sys.argv[1:]
    seed = Path(args[args.index("--seed") + 1]) if "--seed" in args else PETS
    cache = Path(args[args.index("--cache") + 1]) if "--cache" in args else ROOT / "dev" / ".cache" / "metabot"
    pets = {k: {"name": v["name"], "genus": v["genus"]} for k, v in json.loads(seed.read_text(encoding="utf-8"))["pets"].items()}

    print("Soul drops (metabot.gg)...")
    for pid, pet in pets.items():
        page = metabot_page("items/soul-%s-bound" % slug(pet["name"]), cache) if pet["genus"] != "Special" else ""
        pet["souls"] = [{"monster": m, "level": lv, "chance": ch, "types": []} for m, lv, ch in soul_drops(page)]

    print("Spawns (Shugo.GG)...")
    wanted = {(s["monster"], s["level"]): s for p in pets.values() for s in p["souls"]}
    names = {}
    for s in wanted.values():
        names.setdefault(s["monster"], []).append(s)
    SPAWNS.mkdir(parents=True, exist_ok=True)
    types = known_types()
    for key in MAPS:
        try:
            markers = json.loads(get("https://shugo.gg/maps/%s/markers.json" % key))
        except Exception as e:  # keep what we have for maps that fail
            print("%-16s failed (%s); keeping the existing copy if any" % (key, e))
            continue
        monsters = {}
        for type_id, m in monster_templates(markers, types).items():
            souls = [s for s in names.get(m["name"], ()) if s["level"] == m["level"]] or names.get(m["name"], [])
            if not souls:
                continue
            monsters[str(type_id)] = m
            for s in souls:
                if type_id not in s["types"]:
                    s["types"].append(type_id)
        out = {"map": key, "source": "https://shugo.gg/maps/%s/markers.json" % key,
               "fetched": time.strftime("%Y-%m-%d"), "monsters": monsters, "places": places(markers)}
        (SPAWNS / ("%s.json" % key)).write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
        print("%-16s %4d soul monster types, %6d spawn points" % (
            key, len(monsters), sum(len(m["points"]) for m in monsters.values())))

    PETS.write_text(json.dumps({
        "_source": "Pets: gegebase.com / wikily.gg (Global client). Soul drops: metabot.gg item pages. "
                   "Monster type ids: Shugo.GG map markers. Fetched %s by dev/fetch_pet_data.py." % time.strftime("%Y-%m-%d"),
        "pets": pets}, ensure_ascii=False, indent=1), encoding="utf-8")
    lost = sorted({s["monster"] for p in pets.values() for s in p["souls"] if not s["types"]})
    print("pets.json: %d pets; soul monsters without a map spawn (bosses, dungeons): %d" % (len(pets), len(lost)))


if __name__ == "__main__":
    main()
