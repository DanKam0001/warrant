# Derived test photos

Each file is a REAL openly licensed photo from `data/photos/` (credits in `../CREDITS.md`) transformed with Pillow by `scripts/make_derived_photos.py`. They stand in for 'a second photo of the same item' and 'the buyer re-saved our photo'. They are test inputs, not new evidence, and they inherit the licence of their source photo.

| file | source | transform | role in the tests |
|---|---|---|---|
| derived/plate_ship.jpg | plate_intact.jpg | crop=0.85, rotate=4, bright=1.08, width=800, quality=80 | our dispatch photo of the plate, taken at packing |
| derived/plate_buyer_intact.jpg | plate_intact.jpg | crop=0.8, bright=0.95, color=0.9, width=800, quality=80 | buyer's own photo of an intact plate |
| derived/plate_dmg_after.jpg | plate_cracked_2.jpg | width=800, quality=80, exif=2026-11-02 10:12:00 | buyer's damage photo, taken 2 Nov (after delivery) |
| derived/plate_dmg_before.jpg | plate_cracked_2.jpg | width=800, quality=80, exif=2026-10-12 08:30:00 | same damage photo but its EXIF says 12 Oct (before delivery) |
| derived/plate_dmg_reused.jpg | plate_cracked_2.jpg | width=700, quality=55, exif=2026-11-02 10:12:00 | the same damage photo re-saved smaller, sent on a second dispute |
| derived/plate_listing_copy.jpg | plate_intact.jpg | width=640, quality=60 | our own listing photo, re-saved by the buyer |
| derived/plate_dmg_blur.jpg | plate_cracked_2.jpg | blur=30, width=800, quality=80, exif=2026-11-02 10:12:00 | buyer damage photo, heavily blurred (cannot be judged) |
| derived/plate_ship_cracked.jpg | plate_cracked_1.jpg | width=800, quality=80 | a dispatch photo that itself shows a cracked plate |
| derived/mug_red_listing.jpg | mug_red.jpg | width=800, quality=80 | listing photo for the red mug |
| derived/teapot_listing.jpg | teapot_intact.jpg | crop=0.9, width=800, quality=80 | the shop's own (different) listing photo of the teapot |
| derived/mug_ship.jpg | mug_white.jpg | crop=0.9, rotate=-3, bright=1.05, width=800, quality=80 | our dispatch photo of the white mug |
| derived/mug_buyer_intact.jpg | mug_white.jpg | crop=0.85, color=0.92, width=800, quality=80 | buyer's own photo of an intact white mug |
| derived/mug_listing_copy.jpg | mug_white.jpg | width=600, quality=55 | our own white-mug listing photo, re-saved by the buyer |
| derived/cup_dmg_after.jpg | cup_broken.jpg | width=800, quality=80, exif=2026-11-03 18:40:00 | buyer's broken-cup photo, taken after delivery |
| derived/cup_dmg_before.jpg | cup_broken.jpg | width=800, quality=80, exif=2026-10-01 18:40:00 | same, EXIF says before delivery |
| derived/cup_dmg_blur.jpg | cup_broken.jpg | blur=28, width=800, quality=80 | broken-cup photo, heavily blurred |
| derived/vase_ship.jpg | vase_intact_2.jpg | crop=0.9, rotate=3, width=800, quality=80 | our dispatch photo of vase 2 |
| derived/vase_buyer_intact.jpg | vase_intact_2.jpg | crop=0.85, bright=0.95, width=800, quality=80 | buyer's own photo of vase 2, intact |
| derived/vase_dmg_after.jpg | vase_broken.jpg | width=800, quality=80, exif=2026-11-02 12:00:00 | buyer's smashed-vase photo, after delivery |
| derived/plate_dmg2_after.jpg | plate_cracked_1.jpg | width=800, quality=80, exif=2026-11-04 09:00:00 | buyer's second plate damage photo, after delivery |
| derived/mug_blue_ship.jpg | mug_blue.jpg | crop=0.9, rotate=2, width=800, quality=80 | dispatch photo of the blue mug |
| derived/mug_green_listing.jpg | mug_green.jpg | width=800, quality=80 | listing photo of the green mug |
| derived/coffee_buyer.jpg | coffee_set.jpg | crop=0.9, width=800, quality=80 | buyer photo of a coffee set |
