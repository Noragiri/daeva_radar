"""One-time download of the world map images into data/map_tiles/<map>/<z>/<x>_<y>.webp (used by the Map window).

    python dev/fetch_map_tiles.py [map ...]

Source: Shugo.GG's public map tiles (the images its interactive map at https://shugo.gg/map/<map> shows, served
from its asset storage), 256 px WebP tiles, zoom 0 (whole map in one tile) to 4 (16 x 16 tiles = the 4096 px
map that data/maps and data/pet_spawns use: pixel = game units / 200 + 2048). Missing tiles (open sea, void) are
skipped. Map art © NCSOFT. Re-run after a game patch that changes the maps; existing tiles are kept.
"""
import concurrent.futures
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

MAPS = ("poeta", "verteron", "eltnen", "ishalgen", "altgard", "morheim",
        "reshanta-lower", "reshanta-middle", "reshanta-upper")
MAX_ZOOM = 4
BASE = "https://shugoggassets.blob.core.windows.net/assets/maps/%s/tiles/%d/%d/%d.webp"
OUT = Path(__file__).resolve().parent.parent / "data" / "map_tiles"
UA = {"User-Agent": "DaevaRadar/1.0 (personal map cache; credit Shugo.GG)"}


def fetch(key, z, x, y):
    path = OUT / key / str(z) / ("%d_%d.webp" % (x, y))
    if path.exists():
        return path.stat().st_size
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(BASE % (key, z, x, y), headers=UA), timeout=30) as resp:
                data = resp.read()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            time.sleep(0.05)
            return len(data)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return 0
        except OSError:
            time.sleep(3 * (attempt + 1))
    return 0


def main():
    keys = sys.argv[1:] or MAPS
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        for key in keys:
            jobs = [(key, z, x, y) for z in range(MAX_ZOOM + 1) for x in range(2 ** z) for y in range(2 ** z)]
            sizes = list(pool.map(lambda j: fetch(*j), jobs))
            print("%-16s %3d tiles, %5.1f MB" % (key, sum(1 for s in sizes if s), sum(sizes) / 1e6), flush=True)


if __name__ == "__main__":
    main()
