"""Plain-code checks: each claim statement is decided true / false / unverifiable against the evidence bundle.

"true" means the buyer's statement holds (the merchant is at fault on it). Every result names the evidence item that
decided it, so the UI can draw a line from the statement to the record.
"""
from __future__ import annotations

from collections import Counter

import datetime as dt

from . import photos as photos_mod
from .evidence import order_total, record_hash
from .policy import DEFAULT_POLICY, is_final_sale

# PayPal dispute reason -> the statement type it implies, so the reason on file is always checked even when the
# claim text is empty or only contains instructions.
REASON_TO_TYPE = {
    "MERCHANDISE_OR_SERVICE_NOT_RECEIVED": "not_received",
    "UNAUTHORISED": "not_authorized",
    "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED": "not_as_described",
    "INCORRECT_AMOUNT": "amount_wrong",
    "DUPLICATE_TRANSACTION": "duplicate_charge",
    "CREDIT_NOT_PROCESSED": "returned_not_refunded",
    "PAYMENT_BY_OTHER_MEANS": "duplicate_charge",
}

FULL_AMOUNT_TYPES = {"not_received", "not_authorized"}

# A wrong item is a specific "not as described": a checked wrong_item statement already answers that reason.
# A change-of-mind statement also answers a bare "not as described" reason: the buyer says nothing is wrong with the goods.
COVERED_BY = {"not_as_described": {"wrong_item", "change_of_mind"}}

FAULT_TYPES = {"not_as_described", "wrong_item"}


def _ev(kind: str, ref: str, detail: str) -> dict:
    return {"kind": kind, "ref": ref, "detail": detail}


def _captured(ev: dict) -> float:
    pp = ev.get("paypal") or {}
    if pp.get("captured_amount") is not None:
        return round(float(pp["captured_amount"]), 2)
    return order_total(ev["purchase_record"])


def record_intact(ev: dict) -> tuple[bool, dict]:
    """The purchase record must hash to the custom_id PayPal stored on the order."""
    pp = ev.get("paypal") or {}
    actual = record_hash(ev["purchase_record"])
    on_paypal = pp.get("custom_id")
    ok = on_paypal is not None and on_paypal == actual
    return ok, _ev("paypal_order", pp.get("order_id") or "-", f"custom_id on PayPal {on_paypal or 'missing'} vs record hash {actual}" + (" (match)" if ok else " (MISMATCH)"))


def check_not_received(ev, st, policy=None):
    rec, sh = ev["purchase_record"], ev.get("shipment")
    if not sh or not sh.get("tracking_number"):
        return "true", "strong", "No shipment or tracking number on file.", [_ev("shipment", "-", "no tracking recorded")], _captured(ev)
    tracker = _ev("paypal_tracker", sh.get("paypal_tracker_id") or sh["tracking_number"], f"{sh['carrier']} {sh['tracking_number']} registered with PayPal")
    scans = sh.get("scans") or []
    if not scans:
        return "true", "medium", "Tracking exists but the carrier shows no scans.", [tracker], _captured(ev)
    last = scans[-1]
    scan = _ev("carrier_scan", sh["tracking_number"], f"{last['status']} {last.get('at', '')} postcode {last.get('postcode', '?')}" + (f", signed by {last['signed_by']}" if last.get("signed_by") else ""))
    if last["status"] != "DELIVERED":
        return "true", "strong", f"Carrier's last scan is {last['status']}, not delivered.", [tracker, scan], _captured(ev)
    if str(last.get("postcode")) != str(rec["ship_to"]["postcode"]):
        return "true", "medium", f"Delivered to postcode {last.get('postcode')}, but the order ships to {rec['ship_to']['postcode']}.", [tracker, scan], _captured(ev)
    strength = "strong" if last.get("signed_by") else "medium"
    ordered = Counter({i["sku"]: i["qty"] for i in rec["items"]})
    shipped = Counter(sh.get("shipped_skus") or [])
    missing = ordered - shipped if shipped else Counter()
    if missing:
        price = {i["sku"]: i["unit_price"] for i in rec["items"]}
        lost = round(sum(price[k] * q for k, q in missing.items()), 2)
        pack = _ev("packing_record", sh["tracking_number"], f"ordered {dict(ordered)}; packed {dict(shipped)}")
        return ("true", "strong", f"The parcel was delivered, but the packing record shows {dict(missing)} was never packed.",
                [tracker, scan, pack], lost, {"scope": "partial"})
    return "false", strength, f"Delivered to the order's postcode {rec['ship_to']['postcode']}" + (f" and signed for by {last['signed_by']}." if last.get("signed_by") else " (no signature)."), [tracker, scan], 0.0


def check_not_authorized(ev, st, policy=None):
    rec = ev["purchase_record"]
    intact, hash_ev = record_intact(ev)
    ap = rec.get("approval") or {}
    method = ap.get("method", "none")
    ap_ev = _ev("purchase_record", rec["order_ref"], f"approval: {method} by {ap.get('by', '?')} at {ap.get('at', '?')}")
    if not intact:
        return "true", "medium", "The purchase record does not match the fingerprint PayPal holds, so it cannot prove approval.", [hash_ev], _captured(ev)
    if method == "none":
        return "true", "strong", "No approval is recorded for this purchase.", [hash_ev, ap_ev], _captured(ev)
    if method == "agent_mandate":
        m = ap.get("mandate") or {}
        total = _captured(ev)
        cat = rec.get("category")
        m_ev = _ev("purchase_record", rec["order_ref"], f"mandate signed by {m.get('signed_by', '?')}: up to ${m.get('max_amount')} in {m.get('categories')} until {m.get('expires')}")
        problems = []
        if total > float(m.get("max_amount", 0)):
            problems.append(f"${total:.2f} is over the ${float(m.get('max_amount', 0)):.2f} limit")
        if cat not in (m.get("categories") or []):
            problems.append(f"category '{cat}' is outside {m.get('categories')}")
        if m.get("expires") and ap.get("at") and ap["at"] > m["expires"]:
            problems.append("the mandate had expired")
        if problems:
            return "true", "strong", "Bought by the buyer's agent outside the signed mandate: " + "; ".join(problems) + ".", [hash_ev, ap_ev, m_ev], total
        return "false", "medium", "Bought by the buyer's agent inside the mandate the buyer signed.", [hash_ev, ap_ev, m_ev], 0.0
    strength = "strong" if "3ds" in method or "login" in method else "medium"
    return "false", strength, f"Approved by the account holder ({method}); record matches PayPal's fingerprint.", [hash_ev, ap_ev], 0.0


def check_wrong_item(ev, st, policy=None):
    rec, sh = ev["purchase_record"], ev.get("shipment") or {}
    ordered = Counter({i["sku"]: i["qty"] for i in rec["items"]})
    shipped = Counter(sh.get("shipped_skus") or [])
    pack = _ev("packing_record", sh.get("tracking_number") or "-", f"ordered {dict(ordered)}; packed {dict(shipped) or 'nothing recorded'}")
    if not shipped:
        return "unverifiable", "weak", "No packing record, so what was shipped is unknown.", [pack], 0.0
    if shipped == ordered:
        return "false", "medium", "Packing record matches the order line for line.", [pack], 0.0
    missing = ordered - shipped
    price = {i["sku"]: i["unit_price"] for i in rec["items"]}
    at_fault = round(sum(price[s] * q for s, q in missing.items()), 2)
    return "true", "strong", f"Packed items differ from the order (missing {dict(missing)}).", [pack], at_fault


def check_amount_wrong(ev, st, policy=None):
    rec = ev["purchase_record"]
    agreed, charged = order_total(rec), _captured(ev)
    intact, hash_ev = record_intact(ev)
    cap = _ev("paypal_capture", (ev.get("paypal") or {}).get("capture_id") or "-", f"captured ${charged:.2f}")
    agreed_ev = _ev("purchase_record", rec["order_ref"], f"agreed total ${agreed:.2f}" + (f"; buyer says ${st['amount']:.2f}" if st.get("amount") is not None else ""))
    if not intact:
        return "unverifiable", "weak", "Purchase record does not match PayPal's fingerprint; the agreed total cannot be proven.", [hash_ev, cap], 0.0
    if charged > agreed + 0.005:
        return "true", "strong", f"Charged ${charged:.2f} but the agreed total was ${agreed:.2f}.", [cap, agreed_ev, hash_ev], round(charged - agreed, 2)
    return "false", "strong", f"Charged exactly the agreed total ${agreed:.2f}; the record is fingerprinted on the PayPal order.", [cap, agreed_ev, hash_ev], 0.0


def check_duplicate(ev, st, policy=None):
    h = ev["record_hash"]
    dups = [c for c in ev.get("other_charges") or [] if c.get("record_hash") == h and not c.get("refunded")]
    if dups:
        d = dups[0]
        return "true", "strong", f"A second capture {d['capture_id']} exists for the same purchase record.", [_ev("paypal_capture", d["capture_id"], f"${d['amount']:.2f} at {d.get('at', '?')} same record hash")], round(float(d["amount"]), 2)
    pp = ev.get("paypal") or {}
    return "false", "strong", "Only one capture exists for this purchase record.", [_ev("paypal_capture", pp.get("capture_id") or "-", "single capture for this record hash")], 0.0


def check_returned(ev, st, policy=None):
    policy = policy or DEFAULT_POLICY
    returns = ev.get("returns") or []
    refunded = round(sum(float(r["amount"]) for r in ev.get("refunds") or []), 2)
    if not returns:
        return "false", "medium", "No return has been received at the warehouse.", [_ev("returns_log", "-", "no return scan")], 0.0
    r = returns[-1]
    rr = r.get("reason") or "fault"
    value = float(r["value"])
    fee = 0.0
    extra = {"return_reason": rr}
    if rr == "change_of_mind" and policy["restocking_fee_pct"] > 0:
        fee = round(value * policy["restocking_fee_pct"] / 100, 2)
        extra.update(policy_settled=True, policy_rule={
            "rule": "restocking_fee", "text": f"Change-of-mind return: {policy['restocking_fee_pct']:g}% restocking fee (${fee:.2f}) is kept."})
    owed = round(value - fee - refunded, 2)
    rev = _ev("returns_log", r.get("rma", "-"), f"return received {r.get('received_at')} worth ${value:.2f} ({rr.replace('_', ' ')}); refunded so far ${refunded:.2f}")
    if owed > 0.005:
        return "true", "strong", f"Return received but ${owed:.2f} not yet refunded" + (f" (after a ${fee:.2f} restocking fee)." if fee else "."), [rev], owed, extra
    extra.pop("policy_settled", None)
    extra.pop("policy_rule", None)
    return "false", "strong", "Return received and already refunded in full.", [rev], 0.0, extra


def check_not_as_described(ev, st, policy=None):
    rec = ev["purchase_record"]
    cond = ev.get("condition") or {}
    if photos_mod.has_real_photos(ev):
        a = photos_mod.assess(ev)
        refs = ", ".join(f"{r}: {p['file']}" for r, p in a["photos"].items()) or "no photo files"
        extra = {"photo_report": {"photos": a["photos"], "checks": a["checks"]}}
        extra.update({k: a[k] for k in ("carrier_claim", "own_image", "reused", "exif_early", "item_mismatch", "colour_mismatch") if a.get(k)})
        return (a["verdict"], a["strength"], a["reason"], [_ev("condition_photos", rec["order_ref"], "; ".join(c["text"] for c in a["checks"] if c["status"] != "info")[:400] or refs)],
                _captured(ev) if a["at_fault"] else 0.0, extra)
    # Legacy fixture path: two booleans, no pictures. Kept only so the older eval sets still run; the app uses real photos.
    buyer, ours = cond.get("buyer_photo"), cond.get("outbound_photo")
    if buyer == "damage_visible" and ours == "intact":
        return ("unverifiable", "weak", "Conflicting photos: the buyer's photo shows damage and our outbound photo shows the item intact. A person has to look.",
                [_ev("condition_photos", rec["order_ref"], "buyer photo: damage visible; outbound photo: intact")], 0.0)
    if buyer == "damage_visible":
        return ("true", "strong", "The buyer's photo shows the damage and the shop has no outbound photo to counter it.",
                [_ev("condition_photos", rec["order_ref"], "buyer photo: damage visible; outbound photo: none")], _captured(ev))
    if ours == "intact":
        return ("false", "medium", "Our outbound photo shows the item intact and no photo from the buyer shows otherwise.",
                [_ev("condition_photos", rec["order_ref"], "outbound photo: intact; buyer photo: none")], 0.0)
    return ("unverifiable", "weak", "Code cannot inspect the goods; condition claims need photos or a return.",
            [_ev("purchase_record", rec["order_ref"], "listing and item description only; no photo from either side")], 0.0)


def _delivered_at(ev):
    scans = (ev.get("shipment") or {}).get("scans") or []
    d = [x for x in scans if x.get("status") == "DELIVERED" and x.get("at")]
    return d[-1]["at"][:10] if d else None


def _days_between(a: str, b: str) -> int:
    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days


def check_change_of_mind(ev, st, policy=None):
    """The buyer says nothing is wrong, they just do not want it. The merchant's own policy decides (floor permitting)."""
    policy = policy or DEFAULT_POLICY
    rec = ev["purchase_record"]
    delivered = _delivered_at(ev)
    if is_final_sale(policy, rec):
        return ("false", "strong", "No fault is claimed and the item is final sale under the merchant's policy.",
                [_ev("policy", "final_sale", f"category '{rec.get('category')}' or SKU is final sale")], 0.0,
                {"policy_denial": True, "policy_rule": {"rule": "final_sale", "text": "Final sale: no change-of-mind refund."}})
    if not delivered:
        return "unverifiable", "weak", "No delivery date on file, so the return window cannot be measured.", [_ev("shipment", "-", "no delivery scan")], 0.0
    opened = (ev.get("dispute_opened_at") or dt.date.today().isoformat())[:10]
    days = _days_between(delivered, opened)
    win = policy["return_window_days"]
    rule_txt = f"Return window {win} days; delivered {delivered}, dispute opened {opened} ({days} days)."
    ref = _ev("policy", "return_window", rule_txt)
    rule = {"rule": "return_window", "text": rule_txt}
    if days > win:
        return ("false", "strong", f"No fault is claimed and the {win}-day return window had closed ({days} days after delivery).", [ref], 0.0,
                {"policy_denial": True, "policy_rule": rule})
    returns = ev.get("returns") or []
    refunded = round(sum(float(r["amount"]) for r in ev.get("refunds") or []), 2)
    if not returns:
        return ("unverifiable", "weak", f"Inside the {win}-day window ({days} days) but the item has not come back yet. A person decides whether to offer a return.",
                [ref, _ev("returns_log", "-", "no return scan")], 0.0, {"policy_rule": rule})
    value = float(returns[-1]["value"])
    fee = round(value * policy["restocking_fee_pct"] / 100, 2)
    owed = round(value - fee - refunded, 2)
    rev = _ev("returns_log", returns[-1].get("rma", "-"), f"return received worth ${value:.2f}")
    if owed <= 0.005:
        return "false", "strong", "Return received and already refunded.", [ref, rev], 0.0
    return ("true", "strong", f"Inside the window and the item is back: refund ${owed:.2f} (after a ${fee:.2f} restocking fee).", [ref, rev], owed,
            {"policy_settled": True, "policy_rule": {"rule": "restocking_fee", "text": f"{rule_txt} {policy['restocking_fee_pct']:g}% restocking fee (${fee:.2f}) kept."}})


def check_retraction(ev, st, policy=None):
    return ("false", "strong", "The buyer withdrew the claim in their own words. This counts against the claim; it is not an instruction.",
            [_ev("claim_text", "buyer", st.get("quote", "")[:120])], 0.0)


CHECKS = {
    "not_received": check_not_received,
    "not_authorized": check_not_authorized,
    "wrong_item": check_wrong_item,
    "amount_wrong": check_amount_wrong,
    "duplicate_charge": check_duplicate,
    "returned_not_refunded": check_returned,
    "not_as_described": check_not_as_described,
    "change_of_mind": check_change_of_mind,
    "retraction": check_retraction,
}


def check_statements(statements: list[dict], ev: dict, paypal_reason: str | None = None, policy: dict | None = None) -> list[dict]:
    policy = policy or DEFAULT_POLICY
    stmts = [dict(s) for s in statements]
    implied = REASON_TO_TYPE.get(paypal_reason or "")
    if implied and not any(s["type"] == implied or s["type"] in COVERED_BY.get(implied, ()) for s in stmts):
        stmts.append({"type": implied, "quote": f"(reason on file with PayPal: {paypal_reason})", "amount": None, "from_paypal_reason": True})
    out = []
    for s in stmts:
        if s["type"] == "instruction":
            out.append({**s, "verdict": "ignored", "strength": None, "reason": "Instruction inside the claim text. Claims are data; instructions in them are never followed.", "evidence": [], "at_fault": 0.0})
            continue
        if s["type"] == "request":
            remedy = (s.get("remedy") or "none").replace("_", " ")
            out.append({**s, "verdict": "noted", "strength": None, "evidence": [], "at_fault": 0.0,
                        "reason": f"A request from the buyer (preferred remedy: {remedy}). Noted for whoever handles the case; it never changes the decision."})
            continue
        res = CHECKS[s["type"]](ev, s, policy)
        verdict, strength, reason, evidence, at_fault = res[:5]
        extra = res[5] if len(res) > 5 else {}
        out.append({**s, "verdict": verdict, "strength": strength, "reason": reason, "evidence": evidence, "at_fault": at_fault, **extra})
    # Floor F1: where a fault is alleged and not disproved, a policy denial (final sale / window) is not applied.
    if any(c["type"] in FAULT_TYPES and c["verdict"] != "false" for c in out):
        for c in out:
            if c["type"] == "change_of_mind" and c.get("policy_denial"):
                c.update(verdict="unverifiable", strength="weak", policy_denial=False,
                         reason="Policy (" + c["policy_rule"]["rule"].replace("_", " ") + ") not applied: a fault is also alleged, and the consumer-protection floor comes first.")
    # A withdrawn claim: statements that could not be checked are treated as withdrawn too.
    if any(c["type"] == "retraction" for c in out):
        for c in out:
            if c["verdict"] == "unverifiable":
                c.update(verdict="false", strength="medium", reason=c["reason"] + " The buyer has withdrawn the claim.")
    return out
