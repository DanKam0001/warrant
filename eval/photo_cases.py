"""Photo dev set: 15 cases used while building the photo checks (the held-out set is eval/photo_heldout.py).

Different source photos from the held-out set (mugs, cups, vases, a second plate shot). Truth is hand-written from what the
files show. Files are real Commons photos or derived/ stand-ins (data/photos/derived/DERIVED.md).
"""
from __future__ import annotations

from eval.cases import REVIEW, T_FIGHT, concede
from eval.photo_base import pcase

MUG = ("MUG-W", "White ceramic mug", 60.0)
VASE = ("VASE-2", "Hand-glazed vase", 60.0)
OWED = concede(60.0)

PHOTO_CASES = [
    pcase(101, "Mug transit damage", "My mug arrived in pieces, photo attached.", *MUG,
          {"listing": "mug_white.jpg", "outbound": "derived/mug_ship.jpg", "buyer": "derived/cup_dmg_after.jpg"}, OWED,
          "Intact when packed, broken cup in the buyer's photo (taken after delivery). Owed."),
    pcase(102, "Mug intact in both photos", "The mug is cracked, refund please.", *MUG,
          {"listing": "mug_white.jpg", "outbound": "derived/mug_ship.jpg", "buyer": "derived/mug_buyer_intact.jpg"}, T_FIGHT,
          "The buyer's photo shows an intact mug."),
    pcase(103, "Buyer attaches our listing photo", "See photo, the mug is damaged.", *MUG,
          {"listing": "mug_white.jpg", "outbound": "derived/mug_ship.jpg", "buyer": "derived/mug_listing_copy.jpg"}, T_FIGHT,
          "The attachment is our own listing photo re-saved smaller."),
    pcase(104, "Cup photo stamped before delivery", "It arrived broken.", *MUG,
          {"listing": "mug_white.jpg", "outbound": "derived/mug_ship.jpg", "buyer": "derived/cup_dmg_before.jpg"}, REVIEW,
          "EXIF capture time is a month before delivery."),
    pcase(105, "Blurred cup photo", "It arrived broken.", *MUG,
          {"listing": "mug_white.jpg", "outbound": "derived/mug_ship.jpg", "buyer": "derived/cup_dmg_blur.jpg"}, REVIEW,
          "The photo is unreadable."),
    pcase(106, "Vase transit damage", "The vase was smashed when I opened the box.", *VASE,
          {"listing": "vase_intact_2.jpg", "outbound": "derived/vase_ship.jpg", "buyer": "derived/vase_dmg_after.jpg"}, OWED,
          "Intact when packed, smashed vase in the buyer's photo. Owed."),
    pcase(107, "Vase intact in both photos", "The vase has a big crack.", *VASE,
          {"listing": "vase_intact_2.jpg", "outbound": "derived/vase_ship.jpg", "buyer": "derived/vase_buyer_intact.jpg"}, T_FIGHT,
          "The buyer's photo shows an intact vase."),
    pcase(108, "Colour looks right, nothing wrong visible", "The mug is not the colour in the listing.", "Sage green mug", "MUG-SAGE", 60.0,
          {"buyer": "mug_green.jpg"}, REVIEW,
          "The buyer's photo shows a green mug like the order and nothing is wrong in it; the complaint cannot be settled from it. A person decides."),
    pcase(109, "Red mug sent for a blue order", "I ordered blue, this is white.", "Blue glazed mug", "MUG-BLUE", 60.0,
          {"listing": "mug_blue.jpg", "buyer": "mug_white.jpg"}, OWED,
          "Order and listing say blue; the buyer's photo shows a white mug."),
    pcase(110, "Smashed plate, shop has no photo", "The plate came broken.", "Embossed white dinner plate", "PLATE-W", 60.0,
          {"listing": "plate_intact.jpg", "buyer": "derived/plate_dmg2_after.jpg"}, OWED,
          "A photo of broken plate pieces and nothing to counter it. Owed."),
    pcase(111, "We shipped it cracked", "The plate arrived in pieces.", "Embossed white dinner plate", "PLATE-W", 60.0,
          {"listing": "plate_intact.jpg", "outbound": "derived/plate_ship_cracked.jpg", "buyer": "plate_cracked_2.jpg"}, OWED,
          "Our own dispatch photo shows a broken plate."),
    pcase(112, "No photos at all", "The mug has a chip.", *MUG, {}, REVIEW, "Nothing to look at."),
    pcase(113, "Shop photo intact, buyer sends none", "The mug had a chip in the rim.", *MUG,
          {"listing": "mug_white.jpg", "outbound": "derived/mug_ship.jpg"}, T_FIGHT, "Our packing photo shows it intact; the buyer sent no photo."),
    pcase(114, "Cup photo reused on another dispute", "It arrived broken.", *MUG,
          {"listing": "mug_white.jpg", "outbound": "derived/mug_ship.jpg", "buyer": "derived/cup_dmg_after.jpg"}, REVIEW,
          "The same photo is on another order's dispute.", other_disputes=[{"order": "PH-901", "photo": "derived/cup_dmg_after.jpg"}]),
    pcase(115, "Dispatch photo stamped after the carrier took it", "It arrived broken.", *MUG,
          {"listing": "mug_white.jpg", "outbound": "derived/mug_ship.jpg", "buyer": "derived/cup_dmg_after.jpg"}, OWED,
          "Our own photo is dated after the carrier's first scan, so it cannot show the item as packed. With nothing admissible to counter the buyer's photo of damage, it is owed.", outbound_at="2026-10-23T10:00:00Z"),
]
