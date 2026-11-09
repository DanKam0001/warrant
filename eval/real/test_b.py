"""Test B: real CFPB narratives, hand labels, CONSTRUCTED evidence, expected outcome derived by rule.

Nothing here calls a model. It builds the two evidence states for each labelled narrative and says what the right
outcome is. `scripts/run_real_eval.py` runs the deciders against these cases.

Every evidence state starts from the same baseline in which NOTHING the consumer could claim is true (delivered and
signed, packed exactly as ordered, approved with login + 3-D Secure, one capture, no return, intact outbound photo,
charged the agreed total). Then:

- CONTRADICTING state = the baseline.
- SUPPORTING state   = the baseline with each labelled kind flipped so that the consumer's claim is true.

So a decider that reads the wrong kind out of the narrative (or none) fails the supporting state, and one that
invents a claim the consumer did not make cannot make the contradicting state go wrong, because the baseline
contradicts every kind.

Expected outcome (`expected_outcome`), derived from the label and the state only, never from any decider's output:

- supporting, any labelled kind other than `other`  -> the merchant owes money: `pay` (concede or partial both count)
- contradicting                                      -> `fight`
- label `other` only                                 -> `fight` (nothing is claimed that the evidence could support); `review` is
  also acceptable, because a person looking at an unclear complaint is a sound answer. A refund is not.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from eval.cases import case, item, scan, ship, T_FIGHT, LOGIN

HERE = Path(__file__).resolve().parent
KINDS = ("not_received", "not_as_described", "wrong_item", "unauthorised", "duplicate", "amount_wrong", "refund_not_given", "other")

# The PayPal reason a buyer would have picked for each label. `other` has no matching reason, so none is sent.
KIND_REASON = {
    "not_received": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
    "not_as_described": "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED",
    "wrong_item": "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED",
    "unauthorised": "UNAUTHORISED",
    "duplicate": "DUPLICATE_TRANSACTION",
    "amount_wrong": "INCORRECT_AMOUNT",
    "refund_not_given": "CREDIT_NOT_PROCESSED",
    "other": "OTHER",
}
ORDER_PRICE = 100.0
SKU = "ITEM-100"


def load_labelled() -> list[dict]:
    """The 90 hand-labelled narratives, in label-pool order, with their kinds. Unclear ones stay in and are flagged."""
    labels = json.loads((HERE / "labels.json").read_text(encoding="utf-8"))
    pool = json.loads((HERE / "label_pool.json").read_text(encoding="utf-8"))["ids"]
    rows = {}
    for line in (HERE / "cfpb_sample.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            rows[r["complaint_id"]] = r
    out = []
    for cid in pool:
        r = rows[cid]
        out.append({"complaint_id": cid, "narrative": r["narrative"], "kinds": labels["kinds"][cid], "unclear": cid in labels["unclear"],
                    "company_response": r.get("company_response")})
    return out


def primary_reason(kinds: list[str]) -> str:
    return KIND_REASON[kinds[0]]


def expected_outcome(kinds: list[str], state: str) -> dict:
    """The right class of outcome for these labels in this evidence state. `pay` = concede or partial."""
    held = [k for k in kinds if k != "other"]
    if state == "supporting" and held:
        return {"action": "pay", "also_ok": []}
    if state in ("supporting", "contradicting") and not held:
        return {"action": "fight", "also_ok": ["review"]}
    return {"action": "fight", "also_ok": []}


def outcome_class(action: str) -> str:
    return "pay" if action in ("concede", "partial") else action


def outcome_ok(action: str, expected: dict) -> bool:
    return outcome_class(action) == expected["action"] or outcome_class(action) in expected["also_ok"]


def build_case(n: int, kinds: list[str], state: str, narrative: str) -> dict:
    """An eval-style case (same shape as eval/cases.py) for one labelled narrative in one evidence state."""
    sup = state == "supporting"
    has = lambda k: sup and k in kinds  # noqa: E731
    skus = ["WRONG-SKU"] if has("wrong_item") else [SKU]
    if has("not_received"):
        sh = ship(skus, [scan("IN_TRANSIT", "94107")])
    else:
        sh = ship(skus, [scan("DELIVERED", "94110", "B. BUYER")])
    approval = {"method": "none"} if has("unauthorised") else LOGIN
    condition = ({"buyer_photo": "damage_visible", "outbound_photo": "none"} if has("not_as_described")
                 else {"buyer_photo": "none", "outbound_photo": "intact"})
    captured = round(ORDER_PRICE * 1.2, 2) if has("amount_wrong") else None
    returns = refunds = None
    if "refund_not_given" in kinds:
        returns = [{"rma": f"RMA-{n}", "received_at": "2026-10-30", "value": ORDER_PRICE, "reason": "fault"}]
        refunds = None if sup else [{"amount": ORDER_PRICE, "at": "2026-11-02"}]
    c = case(n, "real", f"CFPB narrative, {state}", "not_received", narrative, [item(SKU, "Item", ORDER_PRICE)], sh, T_FIGHT,
             f"constructed {state} evidence for labels {kinds}", approval=approval, captured=captured, returns=returns,
             refunds=refunds, condition=condition, cid=f"{n}-{state[:3]}")
    c["reason"] = primary_reason(kinds)
    if has("duplicate"):
        c["evidence"]["other_charges"] = [{"capture_id": f"EVAL-CAP-DUP-{n}", "record_hash": c["evidence"]["record_hash"],
                                           "amount": ORDER_PRICE, "at": "2026-10-20T10:01:00Z"}]
    c["kinds"] = list(kinds)
    c["state"] = state
    c["expected"] = expected_outcome(kinds, state)
    # the amount the dispute is for: the captured amount (the over-charge case disputes the larger capture)
    c["amount"] = round(captured if captured is not None else ORDER_PRICE, 2)
    return c


def build_all(labelled: list[dict] | None = None) -> list[dict]:
    labelled = labelled if labelled is not None else load_labelled()
    cases = []
    for i, it in enumerate(labelled, 1):
        for state in ("supporting", "contradicting"):
            c = build_case(i, it["kinds"], state, it["narrative"])
            c["complaint_id"], c["unclear"] = it["complaint_id"], it["unclear"]
            cases.append(copy.deepcopy(c))
    return cases
