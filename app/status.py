"""One status per dispute, derived from what Warrant actually did, plus the counts and dollars the strip shows.

The status is never stored: it is computed from the case record (analysis, response, PayPal outcome), so it cannot drift.
"""
from __future__ import annotations

# order = how the strip and the queue sort them: what needs the merchant comes first
STATUSES = [
    ("needs_review", "Needs you"),
    ("waiting_paypal", "Blocked, waiting on PayPal"),
    ("new", "New"),
    ("ready", "Decided, not sent"),
    ("refunded", "Refunded"),
    ("partial", "Partially refunded"),
    ("evidence_sent", "Evidence sent, awaiting PayPal"),
    ("won", "Won"),
    ("lost", "Lost"),
]
LABELS = dict(STATUSES)
RANK = {k: i for i, (k, _) in enumerate(STATUSES)}


def status_of(case: dict) -> str:
    outcome = (case.get("outcome") or {}).get("result")
    resp = case.get("response") or {}
    final = case.get("final") or {}
    if case.get("stage") == "answered" or resp.get("answered"):
        act = final.get("action") or resp.get("action")
        if act == "fight":
            return outcome if outcome in ("won", "lost") else "evidence_sent"
        return "partial" if act == "partial" else "refunded"
    if case.get("stage") == "waiting_on_paypal":
        return "waiting_paypal"
    analysis = case.get("analysis")
    if not analysis:
        return "new"
    return "needs_review" if analysis["decision"]["action"] == "review" else "ready"


def is_auto(case: dict) -> bool:
    return (case.get("final") or {}).get("by") == "auto"


def annotate(case: dict) -> dict:
    """The small per-case fields the queue grid needs without shipping the whole analysis."""
    s = status_of(case)
    dec = (case.get("analysis") or {}).get("decision") or {}
    return {"status": s, "status_label": LABELS[s], "status_rank": RANK[s], "auto_handled": is_auto(case),
            "decision": dec.get("action"), "last_action": case.get("last_action")}


def summary(cases: list[dict]) -> dict:
    by = {k: {"count": 0, "usd": 0.0} for k, _ in STATUSES}
    auto = {"count": 0, "usd": 0.0}
    for c in cases:
        s = status_of(c)
        by[s]["count"] += 1
        by[s]["usd"] = round(by[s]["usd"] + float(c.get("amount") or 0), 2)
        if is_auto(c):
            auto["count"] += 1
            auto["usd"] = round(auto["usd"] + float(c.get("amount") or 0), 2)
    return {"total": len(cases), "total_usd": round(sum(float(c.get("amount") or 0) for c in cases), 2),
            "statuses": [{"key": k, "label": LABELS[k], **by[k]} for k, _ in STATUSES], "auto_handled": auto}
