"""Expected-cost decision: fight, concede (full refund) or partial refund, whichever costs the merchant least.

    fight    = P(lose) x (disputed amount + dispute fee) + handling time
    partial  = amount we are at fault for + P(lose) x (rest + dispute fee) + handling time
    concede  = refund amount            (refunding before PayPal rules avoids the dispute fee)

P(lose) comes from the weakest evidence among the checked statements. All parameters are assumptions the merchant
sets; they are shown next to every decision.
"""
from __future__ import annotations

DEFAULT_PARAMS = {
    "dispute_fee": 20.00,  # USD charged when a dispute is lost/escalated (merchant setting)
    "handling_hours": 0.5,  # ops time to answer one dispute
    "hourly_rate": 30.00,  # cost of that time
    "p_lose": {"strong": 0.10, "medium": 0.35, "weak": 0.60, "unverifiable": 0.60, "true": 0.95},
}


def _r(x: float) -> float:
    return round(x + 1e-9, 2)


def decide(checked: list[dict], disputed_amount: float, already_refunded: float = 0.0, params: dict | None = None) -> dict:
    p = {**DEFAULT_PARAMS, **(params or {})}
    pl = p["p_lose"]
    amount = _r(disputed_amount - already_refunded)
    time_cost = _r(p["handling_hours"] * p["hourly_rate"])
    facts = [c for c in checked if c["verdict"] != "ignored"]

    if amount <= 0.005:
        return {
            "action": "fight", "refund_amount": 0.0, "p_lose": pl["strong"], "at_fault": 0.0, "amount": 0.0,
            "summary": "Already refunded in full; answer with proof of refund.",
            "options": [], "params": p, "time_cost": time_cost,
        }

    full_fault = any(c["verdict"] == "true" and c["type"] in ("not_received", "not_authorized") for c in facts)
    partial_fault = sum(c["at_fault"] for c in facts if c["verdict"] == "true" and c["type"] not in ("not_received", "not_authorized"))
    at_fault = amount if full_fault else _r(min(amount, partial_fault))

    open_risks = [c for c in facts if c["verdict"] != "false"]
    if any(c["verdict"] == "true" for c in facts):
        p_lose = pl["true"]
    elif facts:
        p_lose = max(pl["unverifiable"] if c["verdict"] == "unverifiable" else pl[c["strength"]] for c in facts)
    else:
        p_lose = pl["weak"]

    options = []
    fight = _r(p_lose * (amount + p["dispute_fee"]) + time_cost)
    options.append({"action": "fight", "refund": 0.0, "cost": fight,
                    "formula": f"{p_lose:.2f} x (${amount:.2f} + ${p['dispute_fee']:.2f} fee) + ${time_cost:.2f} time = ${fight:.2f}"})
    if 0.005 < at_fault < amount - 0.005:
        rest = _r(amount - at_fault)
        p_rest = max([pl[c["strength"]] for c in facts if c["verdict"] == "false"] + [pl["unverifiable"] for c in facts if c["verdict"] == "unverifiable"] or [pl["strong"]])
        partial = _r(at_fault + p_rest * (rest + p["dispute_fee"]) + time_cost)
        options.append({"action": "partial", "refund": at_fault, "cost": partial,
                        "formula": f"${at_fault:.2f} refund + {p_rest:.2f} x (${rest:.2f} + ${p['dispute_fee']:.2f} fee) + ${time_cost:.2f} time = ${partial:.2f}"})
    options.append({"action": "concede", "refund": amount, "cost": amount, "formula": f"refund ${amount:.2f} now, no fee = ${amount:.2f}"})

    best = min(options, key=lambda o: (o["cost"], {"fight": 0, "partial": 1, "concede": 2}[o["action"]]))
    if best["action"] == "concede" and at_fault < amount - 0.005 and not open_risks:
        summary = f"Evidence beats the claim, but fighting is expected to cost ${fight:.2f}, more than the ${amount:.2f} refund. Refund early."
    elif best["action"] == "concede":
        summary = "The claim holds on the evidence. Refund."
    elif best["action"] == "partial":
        summary = f"Part of the claim holds (${at_fault:.2f}). Refund that part, contest the rest."
    else:
        summary = "Every statement is contradicted by the evidence. Fight with the evidence."
    return {
        "action": best["action"], "refund_amount": best["refund"], "p_lose": p_lose, "at_fault": at_fault, "amount": amount,
        "summary": summary, "options": options, "params": p, "time_cost": time_cost,
    }
