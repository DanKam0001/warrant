"""Photo held-out set: 14 cases, written and committed BEFORE app/photos.py (the photo checks) existed.

Nothing in app/ was tuned against these. Truth is hand-written from what the image files actually show (I looked at every
source photo), under EVAL_POLICY: fight | concede | review. Files are real Commons photos or derived/ stand-ins
(data/photos/derived/DERIVED.md says exactly how each was made). The point of the set is whether the code's checks on
the model's descriptions reach the right outcome, including when the photos are unusable (-> review).
"""
from __future__ import annotations

from eval.cases import REVIEW, T_FIGHT, concede
from eval.photo_base import pcase

PLATE = ("PLATE-W", "Embossed white dinner plate", 60.0)
OWED = concede(60.0)
LIST = "plate_intact.jpg"

PHOTO_HELDOUT = [
    pcase(1, "Transit damage: intact when we packed it, smashed on arrival", "The plate arrived smashed. Photo attached.", *PLATE,
          {"listing": LIST, "outbound": "derived/plate_ship.jpg", "buyer": "derived/plate_dmg_after.jpg"}, OWED,
          "Our packing photo shows an intact white plate; the buyer's photo (taken after delivery) shows the same kind of plate in pieces. Owed; carrier claim."),
    pcase(2, "Both photos show an intact plate", "The plate arrived cracked, I want my money back.", *PLATE,
          {"listing": LIST, "outbound": "derived/plate_ship.jpg", "buyer": "derived/plate_buyer_intact.jpg"}, T_FIGHT,
          "The buyer's own photo shows an intact plate, same as ours. The damage claim is contradicted."),
    pcase(3, "Buyer photo of damage, shop has no photo", "The plate came broken.", *PLATE,
          {"listing": LIST, "buyer": "derived/plate_dmg_after.jpg"}, OWED,
          "Faulty goods, photo of the damage, nothing to counter it: owed (consumer floor)."),
    pcase(4, "No buyer photo, shop photo intact", "The plate was chipped when it arrived.", *PLATE,
          {"listing": LIST, "outbound": "derived/plate_ship.jpg"}, T_FIGHT,
          "No photo from the buyer; our packing photo shows it intact."),
    pcase(5, "Buyer 'photo' is our own listing picture", "Look at the photo, the plate is clearly cracked.", *PLATE,
          {"listing": LIST, "outbound": "derived/plate_ship.jpg", "buyer": "derived/plate_listing_copy.jpg"}, T_FIGHT,
          "The attachment is the shop's own listing photo, re-saved smaller. It shows no damage and was not taken by the buyer."),
    pcase(6, "Damage photo already used on another dispute", "The plate arrived broken, photo attached.", *PLATE,
          {"listing": LIST, "outbound": "derived/plate_ship.jpg", "buyer": "derived/plate_dmg_reused.jpg"}, REVIEW,
          "The same picture is attached to a different order's dispute. A person has to compare the two; not auto-refused, not auto-paid.",
          other_disputes=[{"order": "PH-900", "photo": "derived/plate_dmg_after.jpg"}]),
    pcase(7, "Photo metadata says it was taken before delivery", "The plate arrived smashed, photo attached.", *PLATE,
          {"listing": LIST, "outbound": "derived/plate_ship.jpg", "buyer": "derived/plate_dmg_before.jpg"}, REVIEW,
          "Capture time 12 Oct, delivery scan 31 Oct. The photo cannot show the delivered item; clocks can be wrong, so a person decides."),
    pcase(8, "Buyer photo too blurred to judge", "The plate arrived smashed, photo attached.", *PLATE,
          {"listing": LIST, "outbound": "derived/plate_ship.jpg", "buyer": "derived/plate_dmg_blur.jpg"}, REVIEW,
          "The only buyer photo is unreadable. Never refuse on a photo the code cannot read."),
    pcase(9, "Wrong colour in the buyer's photo", "I ordered the red mug and got a blue one.", "Red glazed mug", "MUG-RED", 60.0,
          {"listing": "derived/mug_red_listing.jpg", "buyer": "mug_blue.jpg"}, OWED,
          "Listing and order say red; the buyer's photo shows a dark blue mug. Not as described."),
    pcase(10, "A different object in the buyer's photo", "This is not what I ordered.", "Tall vase", "VASE-TALL", 60.0,
          {"listing": "vase_intact.jpg", "buyer": "derived/plate_buyer_intact.jpg"}, OWED,
          "The order is a vase; the buyer's photo shows a plate."),
    pcase(11, "We shipped it already cracked", "The plate arrived smashed.", *PLATE,
          {"listing": LIST, "outbound": "derived/plate_ship_cracked.jpg", "buyer": "derived/plate_dmg_after.jpg"}, OWED,
          "Our own dispatch photo shows a broken plate. We sent it damaged; nothing to fight."),
    pcase(12, "Buyer photo is a studio catalogue shot, not ours", "The teapot arrived cracked, see photo.", "Glazed teapot", "TEAPOT", 60.0,
          {"listing": "derived/teapot_listing.jpg", "buyer": "teapot_intact.jpg"}, REVIEW,
          "A plain-backdrop catalogue image (a museum's) that is not the shop's listing photo. It is a stock picture, not the buyer's parcel; a person decides."),
    pcase(13, "No photos from anyone", "The plate arrived damaged.", *PLATE, {}, REVIEW,
          "Nothing to look at either side; a person decides."),
    pcase(14, "Damage photo with no metadata (EXIF stripped)", "The plate came smashed.", *PLATE,
          {"listing": LIST, "outbound": "derived/plate_ship.jpg", "buyer": "plate_cracked_2.jpg"}, OWED,
          "Same transit damage as case 1, but the photo has no capture time (most apps strip it). Missing metadata is not a red flag."),
]
