"""Evidence model: what the merchant knows about an order, and the hash that ties the purchase record to PayPal.

The purchase record is canonical JSON; its SHA-256 (first 32 hex chars) is sent to PayPal as the order's `custom_id`,
so PayPal itself holds a fingerprint of what was agreed. A later edit to the record no longer matches the dispute.
"""
from __future__ import annotations

import hashlib
import json


def record_hash(purchase_record: dict) -> str:
    canonical = json.dumps(purchase_record, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]


def order_total(purchase_record: dict) -> float:
    return round(sum(i["qty"] * i["unit_price"] for i in purchase_record["items"]) + purchase_record.get("shipping_fee", 0), 2)


def make_case_evidence(purchase_record: dict, shipment: dict | None, paypal: dict | None = None, refunds=None, other_charges=None, returns=None) -> dict:
    """One evidence bundle. `paypal` holds the real sandbox ids once the order exists."""
    return {
        "purchase_record": purchase_record,
        "record_hash": record_hash(purchase_record),
        "shipment": shipment,
        "paypal": paypal or {},
        "refunds": refunds or [],
        "other_charges": other_charges or [],
        "returns": returns or [],
    }
