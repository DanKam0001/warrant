"""Builder for photo eval cases. A photo case is a 'not as described' claim whose evidence includes real image files.

`photos` names files under data/photos/ (real Commons photos or the derived/ stand-ins made by scripts/make_derived_photos.py):
    listing   the shop's listing photo of the item
    outbound  our dispatch photo, taken at packing (before the carrier's first scan)
    buyer     the photo the buyer uploaded with the dispute
Truth is written by hand from what the files really show, never from what the model says about them.
"""
from __future__ import annotations

from app.evidence import make_case_evidence, order_total, record_hash
from app.scenarios import _record
from eval.cases import LOGIN, REASON, item, scan, ship

DELIVERED_AT = "2026-10-31T11:20:00Z"
SHIPPED_AT = "2026-10-22T09:00:00Z"
PACKED_AT = "2026-10-21T16:30:00Z"


def pcase(n, title, claim, name, sku, price, photos, truth, why, other_disputes=None, outbound_at=PACKED_AT):
    rec = _record(f"PH-{n:03d}", {"name": "Buyer", "email": f"buyer{n}@example.com"}, [item(sku, name, price)], "94110", LOGIN, "kitchen", 0.0)
    h = record_hash(rec)
    total = order_total(rec)
    shipment = ship([sku], [scan("IN_TRANSIT", "94107", at=SHIPPED_AT), scan("DELIVERED", "94110", "B. BUYER", at=DELIVERED_AT)])
    ev = make_case_evidence(rec, shipment, paypal={"order_id": f"EVAL-ORDER-P{n:03d}", "capture_id": f"EVAL-CAP-P{n:03d}", "captured_amount": total, "custom_id": h})
    ev["dispute_opened_at"] = "2026-11-05"
    cond = {"photos": {k: v for k, v in photos.items() if v}, "outbound_taken_at": outbound_at if photos.get("outbound") else None}
    if other_disputes:
        cond["other_disputes"] = other_disputes
    ev["condition"] = cond
    return {"id": f"P{n}", "kind": "photo", "injection": False, "benign_request": False, "title": title, "reason": REASON["not_as_described"],
            "claim": claim, "evidence": ev, "amount": total, "truth": truth, "why": why}
