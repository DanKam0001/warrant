"""Photo checks: the vision model is replaced by hand-written observations, so these tests are offline and free."""
import copy
import io

from PIL import Image

from app import photos as P
from eval.photo_base import pcase

PLATE = ("PLATE-W", "Embossed white dinner plate", 60.0)
OK = {"object": "plate", "main_colour": "white", "visible_damage": False, "damage_kind": None, "damage_where": None, "item_count": 1,
      "looks_like_screen": False, "catalogue_style": False, "legible": True, "confidence": "high"}
BROKEN = {**OK, "visible_damage": True, "damage_kind": "shattered"}
FILES = {"listing": "plate_intact.jpg", "outbound": "derived/plate_ship.jpg", "buyer": "derived/plate_dmg_after.jpg"}


def run(obs, photos=None, **kw):
    c = pcase(1, "t", "claim", *PLATE, photos or FILES, None, "", **kw)
    ev = copy.deepcopy(c["evidence"])
    ev["condition"]["obs"] = obs
    return P.assess(ev)


def test_dhash_survives_resize_and_differs_for_other_images():
    a = P.load("plate_intact.jpg")
    im = Image.open(io.BytesIO(a)).resize((400, 300))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=60)
    assert P.hamming(P.dhash(a), P.dhash(buf.getvalue())) <= 8
    assert P.hamming(P.dhash(a), P.dhash(P.load("mug_blue.jpg"))) > 8


def test_exif_capture_time_read_from_derived_photo():
    assert P.exif_taken(P.load("derived/plate_dmg_after.jpg")).year == 2026
    assert P.exif_taken(P.load("plate_intact.jpg")) is None


def test_transit_damage_is_owed_with_carrier_claim():
    r = run({"listing": OK, "outbound": OK, "buyer": BROKEN})
    assert r["verdict"] == "true" and r["at_fault"] and r["carrier_claim"]


def test_both_intact_contradicts_claim():
    r = run({"listing": OK, "outbound": OK, "buyer": OK}, {**FILES, "buyer": "derived/plate_buyer_intact.jpg"})
    assert r["verdict"] == "false"


def test_buyer_photo_that_is_our_listing_image_is_flagged_own():
    r = run({"listing": OK, "outbound": OK, "buyer": OK}, {**FILES, "buyer": "derived/plate_listing_copy.jpg"})
    assert r["verdict"] == "false" and r.get("own_image")


def test_reused_photo_goes_to_review():
    r = run({"listing": OK, "outbound": OK, "buyer": BROKEN}, {**FILES, "buyer": "derived/plate_dmg_reused.jpg"},
            other_disputes=[{"order": "PH-900", "photo": "derived/plate_dmg_after.jpg"}])
    assert r["verdict"] == "unverifiable" and r.get("reused")


def test_exif_before_delivery_goes_to_review():
    r = run({"listing": OK, "outbound": OK, "buyer": BROKEN}, {**FILES, "buyer": "derived/plate_dmg_before.jpg"})
    assert r["verdict"] == "unverifiable" and r.get("exif_early")


def test_unreadable_photo_never_refuses():
    r = run({"listing": OK, "outbound": OK, "buyer": {**BROKEN, "legible": False, "confidence": "low"}})
    assert r["verdict"] == "unverifiable"


def test_wrong_colour_supports_not_as_described():
    r = run({"listing": {**OK, "object": "mug", "main_colour": "red"}, "buyer": {**OK, "object": "mug", "main_colour": "blue"}},
            {"listing": "mug_red.jpg", "buyer": "mug_blue.jpg"})
    assert r["verdict"] == "true" and r["at_fault"]


def test_no_photos_is_not_handled_as_real_photo_path():
    r = run({}, {})
    assert r["verdict"] == "unverifiable"


def test_dispatch_photo_showing_damage_concedes():
    r = run({"listing": OK, "outbound": BROKEN, "buyer": BROKEN})
    assert r["verdict"] == "true" and not r.get("carrier_claim")
