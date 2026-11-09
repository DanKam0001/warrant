r"""Build the derived test photos from the real Commons photos in data/photos/.

Every file written to data/photos/derived/ is the REAL photo, transformed with Pillow, so a test can say "the buyer sent a
new photo of the same item" or "the buyer re-saved our listing photo" without us owning two real photographs of one
plate. The transform of each file is written to data/photos/derived/DERIVED.md. They are stand-ins, not new evidence.

Run:  .venv/Scripts/python.exe scripts/make_derived_photos.py
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

PH = Path(__file__).resolve().parent.parent / "data" / "photos"
OUT = PH / "derived"


def _exif_bytes(when: str | None):
    if not when:
        return None
    ex = Image.Exif()
    ex[306] = when
    ex.get_ifd(0x8769)[36867] = when  # DateTimeOriginal
    return ex.tobytes()


def make(name, src, *, mirror=False, crop=1.0, rotate=0.0, bright=1.0, color=1.0, blur=0.0, width=800, quality=80, exif=None, note=""):
    im = Image.open(PH / src).convert("RGB")
    if mirror:
        im = ImageOps.mirror(im)
    if rotate:
        im = im.rotate(rotate, expand=False, resample=Image.BICUBIC)
    if crop < 1.0:
        w, h = im.size
        cw, ch = int(w * crop), int(h * crop)
        l, t = (w - cw) // 2, (h - ch) // 2
        im = im.crop((l, t, l + cw, t + ch))
    im = ImageEnhance.Brightness(im).enhance(bright)
    im = ImageEnhance.Color(im).enhance(color)
    if blur:
        im = im.filter(ImageFilter.GaussianBlur(blur))
    if width and im.width > width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    kw = {"quality": quality}
    eb = _exif_bytes(exif)
    if eb:
        kw["exif"] = eb
    im.save(OUT / name, "JPEG", **kw)
    bits = [f"{k}={v}" for k, v in dict(mirror=mirror, crop=crop, rotate=rotate, bright=bright, color=color, blur=blur, width=width, quality=quality, exif=exif).items() if v not in (False, 0, 0.0, 1.0, None)]
    return f"| derived/{name} | {src} | {', '.join(bits)} | {note} |"


SPECS = [
    # --- held-out set (eval/photo_heldout.py) ---
    dict(name="plate_ship.jpg", src="plate_intact.jpg", rotate=4, crop=0.85, bright=1.08, note="our dispatch photo of the plate, taken at packing"),
    dict(name="plate_buyer_intact.jpg", src="plate_intact.jpg", mirror=True, crop=0.8, color=0.9, bright=0.95, note="buyer's own photo of an intact plate"),
    dict(name="plate_dmg_after.jpg", src="plate_cracked_2.jpg", exif="2026-11-02 10:12:00", note="buyer's damage photo, taken 2 Nov (after delivery)"),
    dict(name="plate_dmg_before.jpg", src="plate_cracked_2.jpg", exif="2026-10-12 08:30:00", note="same damage photo but its EXIF says 12 Oct (before delivery)"),
    dict(name="plate_dmg_reused.jpg", src="plate_cracked_2.jpg", quality=55, width=700, exif="2026-11-02 10:12:00", note="the same damage photo re-saved smaller, sent on a second dispute"),
    dict(name="plate_listing_copy.jpg", src="plate_intact.jpg", quality=60, width=640, note="our own listing photo, re-saved by the buyer"),
    dict(name="plate_dmg_blur.jpg", src="plate_cracked_2.jpg", blur=30, exif="2026-11-02 10:12:00", note="buyer damage photo, heavily blurred (cannot be judged)"),
    dict(name="plate_ship_cracked.jpg", src="plate_cracked_1.jpg", note="a dispatch photo that itself shows a cracked plate"),
    dict(name="mug_red_listing.jpg", src="mug_red.jpg", note="listing photo for the red mug"),
    dict(name="teapot_listing.jpg", src="teapot_intact.jpg", mirror=True, crop=0.9, note="the shop's own (different) listing photo of the teapot"),
    # --- dev set (eval/photo_cases.py) ---
    dict(name="mug_ship.jpg", src="mug_white.jpg", rotate=-3, crop=0.9, bright=1.05, note="our dispatch photo of the white mug"),
    dict(name="mug_buyer_intact.jpg", src="mug_white.jpg", mirror=True, crop=0.85, color=0.92, note="buyer's own photo of an intact white mug"),
    dict(name="mug_listing_copy.jpg", src="mug_white.jpg", quality=55, width=600, note="our own white-mug listing photo, re-saved by the buyer"),
    dict(name="cup_dmg_after.jpg", src="cup_broken.jpg", exif="2026-11-03 18:40:00", note="buyer's broken-cup photo, taken after delivery"),
    dict(name="cup_dmg_before.jpg", src="cup_broken.jpg", exif="2026-10-01 18:40:00", note="same, EXIF says before delivery"),
    dict(name="cup_dmg_blur.jpg", src="cup_broken.jpg", blur=28, note="broken-cup photo, heavily blurred"),
    dict(name="vase_ship.jpg", src="vase_intact_2.jpg", rotate=3, crop=0.9, note="our dispatch photo of vase 2"),
    dict(name="vase_buyer_intact.jpg", src="vase_intact_2.jpg", mirror=True, crop=0.85, bright=0.95, note="buyer's own photo of vase 2, intact"),
    dict(name="vase_dmg_after.jpg", src="vase_broken.jpg", exif="2026-11-02 12:00:00", note="buyer's smashed-vase photo, after delivery"),
    dict(name="plate_dmg2_after.jpg", src="plate_cracked_1.jpg", exif="2026-11-04 09:00:00", note="buyer's second plate damage photo, after delivery"),
    dict(name="mug_blue_ship.jpg", src="mug_blue.jpg", rotate=2, crop=0.9, note="dispatch photo of the blue mug"),
    dict(name="mug_green_listing.jpg", src="mug_green.jpg", note="listing photo of the green mug"),
    dict(name="coffee_buyer.jpg", src="coffee_set.jpg", mirror=True, crop=0.9, note="buyer photo of a coffee set"),
]


def main():
    OUT.mkdir(exist_ok=True)
    rows = [make(**s) for s in SPECS]
    md = ["# Derived test photos", "",
          "Each file is a REAL openly licensed photo from `data/photos/` (credits in `../CREDITS.md`) transformed with Pillow by "
          "`scripts/make_derived_photos.py`. They stand in for 'a second photo of the same item' and 'the buyer re-saved our photo'. "
          "They are test inputs, not new evidence, and they inherit the licence of their source photo.", "",
          "| file | source | transform | role in the tests |", "|---|---|---|---|", *rows, ""]
    (OUT / "DERIVED.md").write_text("\n".join(md), encoding="utf-8")
    print(len(rows), "derived photos written to", OUT)


if __name__ == "__main__":
    main()
