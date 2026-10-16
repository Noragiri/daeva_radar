"""One-time download of the appearance (wardrobe) sets into data/skins.json, for the Wardrobe feature.

    python dev/fetch_skin_data.py [--cache DIR]

Source: metabot.gg's public skin pages (global client data; credit metabot.gg). Pages are cached in --cache
(default dev/.cache/metabot) and requests are throttled, so re-runs only fetch what's missing.
- /en/aion-2/skins: every armour / cloak set with its id (data-a2-k="own:s:<set>"), name and piece count.
- /en/aion-2/skins/sets/<set>: the pieces ("7 pieces (Helm, Pauldrons, Top, Gloves, Legs, Shoes, and Cloak)").
- /en/aion-2/skins/weapons/<series>: a weapon series and its weapons, by item id ("variants": key + label).
Skin item ids are set id x 100 + piece (30063103 = Light of Genesis 300631, piece 03 = Helm; checked against a
character's equipped skins from the official API). The game sends the ids you own when you enter the world.
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
OUT = ROOT / "data" / "skins.json"
BASE = "https://metabot.gg/en/aion-2"
UA = {"User-Agent": "DaevaRadar/1.0 (personal wardrobe cache; credit metabot.gg)"}
# armour piece number -> name (01 Breastplate/Top ... 07 Cloak), as metabot names them
ARMOUR_PIECES = {1: "Top", 2: "Legs", 3: "Helm", 4: "Pauldrons", 5: "Gloves", 6: "Shoes", 7: "Cloak"}
# sets the Closet doesn't count as Armor (checked against its numbers, 2026-10-15): the special kind
SPECIAL_SETS = {"400481"}  # Leather Crop Fit
# weapon piece number -> weapon (from the series pages' variants; their 3D viewer leaves out the Orb, 06: a
# character's equipped "Black Moonlight (Orb)" is 10014106)
WEAPON_PIECES = {1: "Greatsword", 2: "Longsword", 3: "Dagger", 4: "Bow", 5: "Spellbook", 6: "Orb", 7: "Mace", 8: "Staff"}


def fetch(path, cache):
    f = cache / (path.replace("/", "__") + ".html")
    if f.exists():
        return f.read_text(encoding="utf-8")
    for attempt in range(4):
        time.sleep(1.0 * (1 + 4 * attempt))
        try:
            with urllib.request.urlopen(urllib.request.Request("%s/%s" % (BASE, path), headers=UA), timeout=30) as r:
                text = r.read().decode("utf-8", "replace")
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                text = ""
                break
            if attempt == 3:
                raise
        except OSError:
            if attempt == 3:
                raise
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")
    return text


def main():
    args = sys.argv[1:]
    cache = Path(args[args.index("--cache") + 1]) if "--cache" in args else ROOT / "dev" / ".cache" / "metabot"
    sets = {}
    index = fetch("skins", cache)
    for set_id, slug, name in re.findall(r'data-a2-k="own:s:(\d+)"><a[^>]*href="/en/aion-2/skins/sets/([^"]+)".*?'
                                         r'nameText[^>]*>([^<]+)<', index):
        page = fetch("skins/sets/" + slug, cache)
        m = re.search(r"(\d+) pieces? \(([^)]*)\)", html.unescape(page))
        labels = [l.strip() for l in re.split(r",| and ", m.group(2)) if l.strip()] if m else []
        pieces = {str(n): label for n, label in ARMOUR_PIECES.items() if label in labels} or \
                 {str(n): label for n, label in ARMOUR_PIECES.items()}
        sets[set_id] = {"name": html.unescape(name), "kind": "Special" if set_id in SPECIAL_SETS else "Armour",
                        "pieces": pieces, "slug": slug}
        print("%s %-28s %d pieces" % (set_id, html.unescape(name), len(pieces)), flush=True)
    sitemap = urllib.request.urlopen(urllib.request.Request("https://metabot.gg/sitemaps/aion-2-en.xml", headers=UA),
                                     timeout=60).read().decode("utf-8")
    for slug in sorted(set(re.findall(r"/skins/weapons/([^<\"/]+)<", sitemap))):
        page = fetch("skins/weapons/" + slug, cache).replace('\\"', '"')
        t = re.search(r"<title>([^<]+)</title>", page)
        name = html.unescape(t.group(1)).split(" – ")[0].split(" | ")[0].replace(" Weapon Skins", "").strip() if t else slug
        variants = dict(re.findall(r'\{"key":"(100\d{5})","label":"([^"]+)"', page))
        ids = re.findall(r"\b(100\d{5})\b", page)
        if not ids:
            continue
        # the series: its variants' ids, else the ids the page mentions most (it also lists related series)
        set_id = str(int(next(iter(variants))) // 100) if variants else \
            max(set(i[:6] for i in ids), key=lambda k: sum(1 for i in ids if i.startswith(k)))
        n = re.search(r"Weapon skin series · (\d+) weapons", page)
        total = int(n.group(1)) if n else 8
        pieces = {int(k) % 100: v for k, v in variants.items()}
        for num, weapon in WEAPON_PIECES.items():  # the ones the viewer leaves out (the Orb)
            if len(pieces) < total and num not in pieces and weapon not in pieces.values():
                pieces[num] = weapon
        sets[set_id] = {"name": name, "kind": "Weapon", "slug": slug,
                        "pieces": {str(k): v for k, v in sorted(pieces.items())}, "total": total}
        print("%s %-28s %d weapons" % (set_id, name, sets[set_id]["total"]), flush=True)
    OUT.write_text(json.dumps({
        "_source": "metabot.gg skin pages (global client data), fetched %s by dev/fetch_skin_data.py" % time.strftime("%Y-%m-%d"),
        "sets": sets}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("%d sets -> %s" % (len(sets), OUT))


if __name__ == "__main__":
    main()
