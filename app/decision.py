"""The decision: floor first, then the merchant's policy, then the evidence, then expected cost.

Outcomes:  fight | partial | concede | review

    fight    = P(lose) x (disputed amount + dispute fee) + handling time
    partial  = amount we are at fault for + P(lose) x (rest + dispute fee) + handling time
    concede  = refund amount            (refunding before PayPal rules avoids the dispute fee)
    review   = a person decides: some statement cannot be checked in code, or the evidence conflicts

"Fight" means submitting evidence to PayPal, which decides; Warrant never refuses a refund on its own. P(lose) comes from
the weakest evidence among the checked statements. Every parameter is a merchant setting shown next to the decision, and
every floor / policy rule that fires is written into `trace`.
"""
from __future__ import annotations

from .policy import DEFAULT_POLICY, normalise_policy

DEFAULT_PARAMS = {
    "dispute_fee": 20.00,  # USD charged when a dispute is lost/escalated (merchant setting)
    "handling_hours": 0.5,  # ops time to answer one dispute
    "hourly_rate": 30.00,  # cost of that time
    "p_lose": {"strong": 0.10, "medium": 0.35, "weak": 0.60, "unverifiable": 0.60, "true": 0.95},
}

FULL_TYPES = ("not_received", "not_authorized")
NEUTRAL = ("ignored", "noted")


def _r(x: float) -> float:
    return round(x + 1e-9, 2)


def _is_full(c: dict) -> bool:
    return c["verdict"] == "true" and c["type"] in FULL_TYPES and c.get("scope") != "partial"


def decide(checked: list[dict], disputed_amount: float, already_refunded: float = 0.0, params: dict | None = None,
           policy: dict | None = None, floor: dict | None = None) -> dict:
    policy = normalise_policy(policy) if policy else DEFAULT_POLICY
    p = {**DEFAULT_PARAMS, **policy["cost"], **(params or {})}
    pl = p["p_lose"]
    under = float(policy["refund_without_fight_under"])
    amount = _r(disputed_amount - already_refunded)
    time_cost = _r(p["handling_hours"] * p["hourly_rate"])
    facts = [c for c in checked if c["verdict"] not in NEUTRAL]
    floor = floor or {"hits": [], "no_auto_fight": False}

    trace = [dict(h) for h in floor["hits"]]
    seen = set()
    for c in checked:
        pr = c.get("policy_rule")
        if not pr or pr["rule"] in seen:
            continue
        seen.add(pr["rule"])
        if c.get("policy_settled"):
            effect = "settles the refund amount"
        elif c.get("policy_denial"):
            effect = "no refund on policy grounds: evidence goes to PayPal, which decides"
        else:
            effect = "applies, but the item has not come back yet"
        trace.append({"layer": "policy", "rule": pr["rule"], "text": pr["text"], "effect": effect})

    def out(action, refund, summary, p_lose, at_fault, options, **extra):
        return {"action": action, "refund_amount": _r(refund), "p_lose": p_lose, "at_fault": at_fault, "amount": amount, "summary": summary,
                "options": options, "params": p, "time_cost": time_cost, "trace": trace, "floor_hits": floor["hits"],
                "no_auto_fight": floor["no_auto_fight"], "review_reasons": extra.pop("review_reasons", []), **extra}

    if amount <= 0.005:
        trace.append({"layer": "evidence", "rule": "refund-on-file", "text": "The disputed amount has already been refunded.", "effect": "answer with proof of refund"})
        return {**out("fight", 0.0, "Already refunded in full; answer with proof of refund.", pl["strong"], 0.0, []), "amount": 0.0}

    real = [c for c in facts if c["type"] != "retraction"]
    retractions = [c for c in facts if c["type"] == "retraction"]
    full_fault = any(_is_full(c) for c in real)
    partial_fault = sum(c["at_fault"] for c in real if c["verdict"] == "true" and not _is_full(c))
    at_fault = amount if full_fault else _r(min(amount, partial_fault))
    unresolved = [c for c in real if c["verdict"] == "unverifiable"]
    trues = [c for c in real if c["verdict"] == "true"]

    if any(c["verdict"] == "true" for c in facts):
        p_lose = pl["true"]
    elif facts:
        p_lose = max(pl["unverifiable"] if c["verdict"] == "unverifiable" else pl[c["strength"]] for c in facts)
    else:
        p_lose = pl["weak"]
    open_risks = [c for c in facts if c["verdict"] != "false"]

    # ---- option costs (always shown) ----
    options = []
    fight = _r(p_lose * (amount + p["dispute_fee"]) + time_cost)
    options.append({"action": "fight", "refund": 0.0, "cost": fight,
                    "formula": f"{p_lose:.2f} x (${amount:.2f} + ${p['dispute_fee']:.2f} fee) + ${time_cost:.2f} time = ${fight:.2f}"})
    if 0.005 < at_fault < amount - 0.005:
        rest = _r(amount - at_fault)
        p_rest = max([pl[c["strength"]] for c in facts if c["verdict"] == "false"] + [pl["unverifiable"] for c in facts if c["verdict"] == "unverifiable"] or [pl["strong"]])
        part = _r(at_fault + p_rest * (rest + p["dispute_fee"]) + time_cost)
        options.append({"action": "partial", "refund": at_fault, "cost": part,
                        "formula": f"${at_fault:.2f} refund + {p_rest:.2f} x (${rest:.2f} + ${p['dispute_fee']:.2f} fee) + ${time_cost:.2f} time = ${part:.2f}"})
    options.append({"action": "concede", "refund": amount, "cost": amount, "formula": f"refund ${amount:.2f} now, no fee = ${amount:.2f}"})
    result = lambda action, refund, summary, **kw: out(action, refund, summary, p_lose, at_fault, options, **kw)  # noqa: E731

    def ev_line(text, effect):
        trace.append({"layer": "evidence", "rule": "checked statements", "text": text, "effect": effect})

    # ---- 1. a buyer who withdraws a claim the evidence supports: the facts conflict ----
    if retractions and trues:
        ev_line("The buyer withdrew the claim, but the evidence says it holds.", "review")
        return result("review", 0.0, "The buyer withdrew the claim but the records show we are at fault. A person should look.",
                      proposed_refund=at_fault, review_reasons=["buyer retraction conflicts with the evidence"])

    # ---- 2. amounts the merchant's own policy settles (restocking fee on a return) ----
    settled = [c for c in real if c.get("policy_settled") and c["verdict"] == "true"]
    others_open = [c for c in real if c not in settled and c["verdict"] != "false"]
    if settled and not others_open:
        refund = _r(min(amount, sum(c["at_fault"] for c in settled)))
        act = "concede" if refund >= amount - 0.005 else "partial"
        ev_line("The return was received and the policy sets the refund.", f"{act} ${refund:.2f}")
        return result(act, refund, f"Policy settles this one: refund ${refund:.2f}" + (" (the rest is the restocking fee)." if act == "partial" else "."))

    # ---- 3. statements code cannot check ----
    if unresolved and not full_fault:
        names = ", ".join(sorted({c["type"].replace("_", " ") for c in unresolved}))
        if amount <= under + 0.005:
            trace.append({"layer": "policy", "rule": "refund_without_fight_under", "text": f"${amount:.2f} is at or under the ${under:.2f} refund-without-fight limit.", "effect": "refund"})
            return result("concede", amount, f"Cannot check {names} in code, and ${amount:.2f} is under the ${under:.2f} limit. Refund without a fight.")
        ev_line(f"Cannot be checked in code: {names}.", "review")
        return result("review", 0.0, f"Cannot check {names} in code. A person looks at the photos or the goods and decides.",
                      proposed_refund=at_fault, review_reasons=[f"{names}: no evidence either way"])

    # ---- 4. expected cost, with the policy limit blocking a fight ----
    best = min(options, key=lambda o: (o["cost"], {"fight": 0, "partial": 1, "concede": 2}[o["action"]]))
    if best["action"] == "fight" and amount <= under + 0.005:
        fight_opt = options[0]
        fight_opt["blocked"] = "policy"
        best = min([o for o in options if o["action"] != "fight"], key=lambda o: o["cost"])
        trace.append({"layer": "policy", "rule": "refund_without_fight_under", "text": f"${amount:.2f} is at or under the ${under:.2f} refund-without-fight limit.", "effect": f"{best['action']} instead of fighting"})
        summary = f"Evidence supports a fight, but policy refunds disputes up to ${under:.2f} without one."
    elif best["action"] == "concede" and at_fault < amount - 0.005 and not open_risks:
        summary = f"Evidence beats the claim, but fighting is expected to cost ${fight:.2f}, more than the ${amount:.2f} refund. Refund early."
    elif best["action"] == "concede":
        summary = "The claim holds on the evidence. Refund."
    elif best["action"] == "partial":
        summary = f"Part of the claim holds (${at_fault:.2f}). Refund that part, contest the rest."
    else:
        summary = "Every statement is contradicted by the evidence. Fight with the evidence."
    ev_line(f"{len(facts)} statement(s) checked; expected cost picks {best['action']}.", best["action"])
    return result(best["action"], best["refund"], summary)
