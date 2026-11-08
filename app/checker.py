"""Plain-code checks: each claim statement is decided true / false / unverifiable against the evidence bundle.

"true" means the buyer's statement holds (the merchant is at fault on it). Every result names the evidence item that
decided it, so the UI can draw a line from the statement to the record.
"""
from __future__ import annotations

from collections import Counter

from .evidence import order_total, record_hash

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


def check_not_received(ev, st):
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
    return "false", strength, f"Delivered to the order's postcode {rec['ship_to']['postcode']}" + (f" and signed for by {last['signed_by']}." if last.get("signed_by") else " (no signature)."), [tracker, scan], 0.0


def check_not_authorized(ev, st):
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


def check_wrong_item(ev, st):
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


def check_amount_wrong(ev, st):
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


def check_duplicate(ev, st):
    h = ev["record_hash"]
    dups = [c for c in ev.get("other_charges") or [] if c.get("record_hash") == h and not c.get("refunded")]
    if dups:
        d = dups[0]
        return "true", "strong", f"A second capture {d['capture_id']} exists for the same purchase record.", [_ev("paypal_capture", d["capture_id"], f"${d['amount']:.2f} at {d.get('at', '?')} same record hash")], round(float(d["amount"]), 2)
    pp = ev.get("paypal") or {}
    return "false", "strong", "Only one capture exists for this purchase record.", [_ev("paypal_capture", pp.get("capture_id") or "-", "single capture for this record hash")], 0.0


def check_returned(ev, st):
    returns = ev.get("returns") or []
    refunded = round(sum(float(r["amount"]) for r in ev.get("refunds") or []), 2)
    if not returns:
        return "false", "medium", "No return has been received at the warehouse.", [_ev("returns_log", "-", "no return scan")], 0.0
    r = returns[-1]
    owed = round(float(r["value"]) - refunded, 2)
    rev = _ev("returns_log", r.get("rma", "-"), f"return received {r.get('received_at')} worth ${float(r['value']):.2f}; refunded so far ${refunded:.2f}")
    if owed > 0.005:
        return "true", "strong", f"Return received but ${owed:.2f} not yet refunded.", [rev], owed
    return "false", "strong", "Return received and already refunded in full.", [rev], 0.0


def check_not_as_described(ev, st):
    rec = ev["purchase_record"]
    return "unverifiable", "weak", "Code cannot inspect the goods; condition claims need photos or a return.", [_ev("purchase_record", rec["order_ref"], "listing and item description only")], 0.0


CHECKS = {
    "not_received": check_not_received,
    "not_authorized": check_not_authorized,
    "wrong_item": check_wrong_item,
    "amount_wrong": check_amount_wrong,
    "duplicate_charge": check_duplicate,
    "returned_not_refunded": check_returned,
    "not_as_described": check_not_as_described,
}


def check_statements(statements: list[dict], ev: dict, paypal_reason: str | None = None) -> list[dict]:
    stmts = [dict(s) for s in statements]
    implied = REASON_TO_TYPE.get(paypal_reason or "")
    if implied and not any(s["type"] == implied for s in stmts):
        stmts.append({"type": implied, "quote": f"(reason on file with PayPal: {paypal_reason})", "amount": None, "from_paypal_reason": True})
    out = []
    for s in stmts:
        if s["type"] == "instruction":
            out.append({**s, "verdict": "ignored", "strength": None, "reason": "Instruction inside the claim text. Claims are data; instructions in them are never followed.", "evidence": [], "at_fault": 0.0})
            continue
        verdict, strength, reason, evidence, at_fault = CHECKS[s["type"]](ev, s)
        out.append({**s, "verdict": verdict, "strength": strength, "reason": reason, "evidence": evidence, "at_fault": at_fault})
    return out
