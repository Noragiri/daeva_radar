"""One-time download of the names of the items the rotating shop sells, into data/shop_items.json.

    python dev/fetch_shop_items.py

The shop message ('91 56') carries item ids, not names. Names come from Shugo.GG's item database (one page per
item, /items/<id>, all listed in https://shugo.gg/sitemap-items.xml; its guide for AI agents,
https://shugo.gg/llms.txt, points to both). Only the ranges the shop sells are read: pets (53476xxxx), skin
reward vouchers (5357/5358/5359xxxxx) and theostones (5338/5339xxxxx). The sitemap misses some items that have a
page (Nameless Master (Skin: Staff) 535710008, 2026-10-15), so every id from xxx001 to the highest listed one of
each block (the first six digits) is tried too. Shugo.GG allows 300 pages per 10 minutes from one connection, so
pages are fetched one at a time, one per 2.1 s (about 45 minutes for a full run), and its "Too many pages" answer
waits and retries. Titles are cached in dev/.cache/shugo (failed fetches aren't), so a re-run after a patch only
fetches what's new. The app only reads the local copy.
"""
import concurrent.futures
import html
import json
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "shop_items.json"
CACHE = ROOT / "dev" / ".cache" / "shugo"
PREFIXES = ("53476", "5357", "5358", "5359", "5338", "5339")
UA = {"User-Agent": "DaevaRadar/1.0 (personal shop names cache; credit Shugo.GG)"}
lock = threading.Lock()


def get(url):
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return ""
        except OSError:
            pass
        time.sleep(3 * (attempt + 1))
    return ""


PACE = 2.1  # seconds between pages: Shugo.GG allows 300 per 10 minutes


def title(item_id):
    f = CACHE / ("%s.txt" % item_id)
    if f.exists():
        return f.read_text(encoding="utf-8")
    for attempt in range(10):
        time.sleep(PACE)
        page = get("https://shugo.gg/items/%s" % item_id)
        if "Too many pages" not in page:
            break
        print("  rate limited: waiting 2 minutes", flush=True)
        time.sleep(120)
    else:
        return ""
    t = re.search(r"<title>(.*?)</title>", page, re.S)
    text = html.unescape(t.group(1)).split(" | ")[0].strip() if t else ""
    if page:  # a failed fetch isn't cached: the next run tries again
        f.write_text(text, encoding="utf-8")
    return text


def parse(text):
    """'Pet: Giant Bee - Common Pet' / 'Sky Orb (Skin: Earrings) (Bound) - Epic Skin Reward Voucher' /
    'Abyss Points, AION 2 common valuables: where to get it' (currencies). None for pages that aren't an item."""
    m = re.match(r"(.+), AION 2 (\w+) valuables", text)
    if m:
        return dict(name=m.group(1), kind="valuable", grade=m.group(2).title())
    if " - " not in text:
        return None
    name, _, kind = text.rpartition(" - ")
    grade = kind.split(" ")[0] if kind else ""
    kind = "pet" if kind.endswith("Pet") else "skin" if "Skin" in kind else "theostone" if "Theostone" in kind else "other"
    return dict(name=name.replace(" (Bound)", ""), kind=kind, grade=grade)


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    listed = set(i for i in re.findall(r"/items/(\d+)<", get("https://shugo.gg/sitemap-items.xml"))
                 if i.startswith(PREFIXES))
    top = {}
    for i in listed:  # the highest listed id of each block (first six digits)
        top[i[:6]] = max(top.get(i[:6], 0), int(i))
    ids = sorted(listed | {str(n) for block, high in top.items() for n in range(int(block) * 1000 + 1, high + 1)})
    print("%d items to name" % len(ids), flush=True)
    out, done = {}, [0]

    def one(item_id):
        text = title(item_id)
        text = text if parse(text) else ""
        with lock:
            done[0] += 1
            if done[0] % 200 == 0:
                print("  %d / %d" % (done[0], len(ids)), flush=True)
        return item_id, text

    with concurrent.futures.ThreadPoolExecutor(1) as pool:  # one at a time (the rate limit)
        for item_id, text in pool.map(one, ids):
            if text:
                out[item_id] = parse(text)
    OUT.write_text(json.dumps({"_source": "Shugo.GG item database (names), fetched %s by dev/fetch_shop_items.py"
                                          % time.strftime("%Y-%m-%d"), "items": out}, ensure_ascii=False, indent=0),
                   encoding="utf-8")
    print("%d named -> %s" % (len(out), OUT))


if __name__ == "__main__":
    main()
