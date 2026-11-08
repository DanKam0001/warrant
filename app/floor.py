"""Consumer-protection floor. Checked BEFORE the merchant's policy; no policy value can override it.

These are general consumer-protection principles, not legal advice. The jurisdiction is set in the policy; laws are named
only at a general level (e.g. Australian Consumer Law consumer guarantees) and no statute numbers are quoted.

"Fight" never means refusing a refund: it means submitting the merchant's evidence to PayPal, which decides. The buyer
keeps PayPal Buyer Protection and their chargeback rights. Warrant never refuses a refund on its own.
"""
from __future__ import annotations

from .policy import JURISDICTIONS, is_final_sale

DISCLAIMER = ("General consumer-protection principles, not legal advice. Jurisdiction is set in the policy; "
              "no statute numbers are quoted.")
FIGHT_NOTE = ("Fight means submitting the merchant's evidence to PayPal, which decides. The buyer keeps PayPal Buyer "
              "Protection and their chargeback rights. Warrant never refuses a refund on its own.")

FAULT_TYPES = {"not_as_described", "wrong_item"}


def principles(jurisdiction: str = "AU") -> list[dict]:
    law = JURISDICTIONS.get(jurisdiction, JURISDICTIONS["AU"])
    return [
        {"id": "F1", "text": f"Goods that are faulty, damaged, wrong or not as described are owed a remedy when the merchant has no "
                              f"counter-evidence ({law}). Final-sale, return-window and restocking-fee policy do not defeat that."},
        {"id": "F2", "text": "A parcel with no proof of delivery is owed a refund, whatever the policy says."},
        {"id": "F3", "text": "Unauthorized-charge claims are never auto-fought on policy grounds; any fight is a human decision."},
        {"id": "F4", "text": "A duplicate charge or an overcharge is always refundable."},
        {"id": "F5", "text": "A restocking fee applies only to a change-of-mind return, never to a faulty one."},
    ]


def _delivered(ev: dict) -> bool:
    scans = (ev.get("shipment") or {}).get("scans") or []
    return bool(scans) and scans[-1].get("status") == "DELIVERED"


def evaluate(checked: list[dict], evidence: dict, policy: dict) -> dict:
    """Return the floor rules that fire for this claim, as trace entries, plus flags the rest of the pipeline obeys."""
    hits, no_auto_fight = [], False

    def hit(rule, text, effect):
        hits.append({"layer": "floor", "rule": rule, "text": text, "effect": effect})

    final_sale = is_final_sale(policy, evidence.get("purchase_record") or {})
    for c in checked:
        t, v = c["type"], c["verdict"]
        if t in FAULT_TYPES and v != "false":
            note = " This item is marked final sale; that does not defeat the claim." if final_sale else ""
            hit("F1", "Faulty or not-as-described claim with no counter-evidence: policy limits (final sale, return window, "
                      "restocking fee) are not applied." + note, "policy_overridden" if final_sale else "policy_not_applied")
        elif t == "not_received" and v == "true" and c.get("scope") != "partial" and not _delivered(evidence):
            hit("F2", "No proof of delivery: the refund is owed regardless of policy.", "owed")
        elif t == "not_authorized":
            no_auto_fight = True
            hit("F3", "Unauthorized-charge claim: never auto-fought on policy grounds; a fight needs a person.", "no_auto_fight")
        elif t in ("duplicate_charge", "amount_wrong") and v == "true":
            hit("F4", "Duplicate charge or overcharge: always refundable.", "owed")
        elif t == "returned_not_refunded" and v == "true" and c.get("return_reason") == "fault":
            hit("F5", "Returned as faulty: no restocking fee is taken.", "no_restocking_fee")
    return {"hits": hits, "no_auto_fight": no_auto_fight}
