"""One-time download of map reference data (gathering nodes, hidden cubes and Empyrean Traces) into data/maps/.

    python dev/fetch_map_data.py

Source: the public marker files of Shugo.GG (https://shugo.gg/maps/<map>/markers.json), which its guide for
AI agents (https://shugo.gg/llms.txt) points to. Only those 9 public files are read: never the site's API.
The app only reads the local copies this script writes (its only online request is the update check). Re-run after a game
patch if the site is still there; if it isn't, the existing copies keep working.

Coordinates in the files are map pixels on a 4096 px map: pixel = game units / 200 + 2048 (checked live:
four Diamond nodes landed within 1.5 px of the markers in total).
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

MAPS = ("poeta", "verteron", "eltnen", "ishalgen", "altgard", "morheim",
        "reshanta-lower", "reshanta-middle", "reshanta-upper")
KEEP = ("Resources",)  # marker groups kept (category ids starting with Gather_)
KEEP_IDS = ("HiddenCube", "EmpyreanTrace")
OUT = Path(__file__).resolve().parent.parent / "data" / "maps"


def fetch(key):
    url = "https://shugo.gg/maps/%s/markers.json" % key
    req = urllib.request.Request(url, headers={"User-Agent": "DaevaRadar/1.0 (personal map cache; credit Shugo.GG)"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def slim(data):
    """Keep only resource, hidden-cube and Empyrean Trace markers: {category: {name, item, points: [[x, y], ...]}}."""
    cats = {c["id"]: c for c in data["categories"] if c.get("group") in KEEP or c["id"] in KEEP_IDS}
    out = {}
    for t_index, t in enumerate(data["templates"]):
        if t["c"] not in cats:
            continue
        entry = out.setdefault(t["c"], {"name": cats[t["c"]]["label"], "item": None, "points": []})
        if t["c"].startswith("Gather_"):
            entry["item"] = int(t["c"].split("_", 1)[1])
        entry.setdefault("_templates", set()).add(t_index)
    for t_index, x, y in data["points"]:
        for entry in out.values():
            if t_index in entry["_templates"]:
                entry["points"].append([x, y])
                break
    for entry in out.values():
        del entry["_templates"]
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for key in MAPS:
        try:
            data = fetch(key)
        except Exception as e:  # keep what we have for maps that fail
            print("%-16s failed (%s); keeping the existing copy if any" % (key, e))
            continue
        slimmed = {"map": key, "generated": data.get("generatedAt"), "fetched": time.strftime("%Y-%m-%d"),
                   "source": "https://shugo.gg/maps/%s/markers.json (credit: Shugo.GG)" % key,
                   "categories": slim(data)}
        path = OUT / (key + ".json")
        if not slimmed["categories"]:
            print("%-16s no gathering data on the site yet" % key)
            continue
        path.write_text(json.dumps(slimmed, separators=(",", ":")), encoding="utf-8")
        counts = {v["name"]: len(v["points"]) for v in slimmed["categories"].values()}
        print("%-16s %5d markers  %s" % (key, sum(counts.values()), ", ".join("%s %d" % kv for kv in sorted(counts.items()))))
        time.sleep(1)  # be gentle
    return 0


if __name__ == "__main__":
    sys.exit(main())
