r"""Download the demo's real photographs from Wikimedia Commons (CC0 / CC-BY / CC-BY-SA / public domain only), resize
to <= 1280 px, and write data/photos/CREDITS.md. Re-runnable: it overwrites the files with the same selection.

    .venv\Scripts\python.exe scripts/fetch_photos.py
"""
import io
import re
import sys
import time
from pathlib import Path

import httpx
from PIL import Image

OUT = Path(__file__).resolve().parent.parent / "data" / "photos"
H = {"User-Agent": "WarrantHackathon/0.1 (dankam0001@gmail.com)"}
OK_LICENSES = ("CC0", "CC BY", "CC BY-SA", "Public domain")

# local name -> (Commons file title, what it shows)
PHOTOS = {
    "plate_cracked_1": ("A cracked plate after inadvertently putting small pieces of metal into the microwave.jpg", "white plate shattered in a microwave"),
    "plate_cracked_2": ("A cracked plate after inadvertently putting small pieces of metal into the microwave (1).jpg", "the same shattered plate held up"),
    "cup_broken": ("Tassa trencada.jpg", "a broken cup held in a hand"),
    "vase_broken": ("Broken vase on a grave J1.jpg", "a vase smashed into pieces"),
    "plate_intact": ("2024.01.21 Rosenthal White Porcelain Louise XIV Plate.jpg", "an intact white porcelain plate"),
    "teapot_intact": ("Teapot MET DP-13600-032.jpg", "an intact cream teapot on a grey studio backdrop"),
    "vase_intact": ("Ceramic vase with uranium glaze.jpg", "an intact two-handled glazed vase"),
    "vase_intact_2": ("Flower Vase.jpg", "an intact dark glazed vase (a second, different vase)"),
    "mug_red": ("Coffee - Massachusetts.jpg", "an intact red mug"),
    "mug_blue": ("Dark Blue Coffee mug.jpg", "an intact dark blue mug"),
    "mug_green": ("Green Mug (47346044602).jpg", "an intact green mug on a wooden table"),
    "mug_white": ("Herbal tea in white mug.jpg", "an intact white cup with holly print"),
    "coffee_set": ("Heirloom range coffee pot and mug, V&A London.jpg", "an intact brown coffee pot and mug"),
}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    meta = {}
    titles = [v[0] for v in PHOTOS.values()]
    for i in range(0, len(titles), 20):
        r = httpx.get("https://commons.wikimedia.org/w/api.php", headers=H, timeout=40, params={
            "action": "query", "format": "json", "titles": "|".join("File:" + t for t in titles[i:i + 20]),
            "prop": "imageinfo", "iiprop": "url|extmetadata", "iiurlwidth": 1280})
        for p in r.json()["query"]["pages"].values():
            ii = p["imageinfo"][0]
            m = ii["extmetadata"]
            meta[p["title"][5:]] = {
                "thumb": ii["thumburl"], "page": ii["descriptionurl"], "licence": m.get("LicenseShortName", {}).get("value", "?"),
                "author": re.sub(r"\s+", " ", re.sub("<.*?>", "", m.get("Artist", {}).get("value", ""))).strip()[:140] or "unknown"}
    lines = ["# Photo credits", "",
             "Every image in this folder is a real photograph downloaded from Wikimedia Commons (via its API, `scripts/fetch_photos.py`) "
             "under CC0, CC BY, CC BY-SA or public domain. None is generated. Each is resized to at most 1280 px on the long side. "
             "In the demo these stand in for a buyer's, the shop's and a listing's photos of ceramics: they are different physical objects "
             "from the ones the demo orders describe.", "",
             "| File | Shows | Source | Author | Licence |", "|---|---|---|---|---|"]
    for name, (title, shows) in PHOTOS.items():
        m = meta[title]
        if not any(m["licence"].startswith(l) for l in OK_LICENSES):
            sys.exit(f"refusing {title}: licence {m['licence']}")
        for a in range(4):
            try:
                b = httpx.get(m["thumb"], headers=H, timeout=60, follow_redirects=True).content
                im = Image.open(io.BytesIO(b)).convert("RGB")
                break
            except Exception:  # noqa: BLE001
                time.sleep(3)
        else:
            sys.exit(f"could not fetch {title}")
        im.thumbnail((1280, 1280))
        im.save(OUT / f"{name}.jpg", quality=82)
        lines.append(f"| `{name}.jpg` | {shows} | [{title}]({m['page']}) | {m['author']} | {m['licence']} |")
        print(name, im.size, m["licence"])
    (OUT / "CREDITS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
