"""Run photo cases. Deciders:

    naive      a photo from the buyer means refund; no buyer photo means fight (what a rule-of-thumb desk does)
    vl_alone   the vision model is shown the claim and every photo and asked to decide refund / fight / review itself
    warrant    the vision model only DESCRIBES each photo (cached); plain code runs the four checks and decides

Cases skip the text splitter on purpose (the claim is a 'not as described' statement), so the table isolates the photo logic.
"""
from __future__ import annotations

import base64
import copy
import json
import re

import httpx

from app import floor as floor_mod
from app import photos as P
from app.checker import check_statements
from app.decision import decide
from eval.policy import EVAL_POLICY

ACTIONS = ("fight", "concede", "review")


def warrant(case, vision):
    ev = copy.deepcopy(case["evidence"])
    P.observe(ev, vision)
    stm = [{"type": "not_as_described", "quote": case["claim"], "amount": None}]
    checked = check_statements(stm, ev, case["reason"], EVAL_POLICY)
    fl = floor_mod.evaluate(checked, ev, EVAL_POLICY)
    dec = decide(checked, case["amount"], policy=EVAL_POLICY, floor=fl)
    c = checked[0]
    return {"action": dec["action"] if dec["action"] != "partial" else "concede", "verdict": c["verdict"], "reason": c["reason"],
            "report": (c.get("photo_report") or {}), "obs": copy.deepcopy((ev.get("condition") or {}).get("obs"))}


def naive(case, vision=None):
    has = bool(((case["evidence"].get("condition") or {}).get("photos") or {}).get("buyer"))
    return {"action": "concede" if has else "fight", "reason": "buyer attached a photo" if has else "no buyer photo"}


VL_ALONE_SYSTEM = (
    "You are a merchant's dispute assistant. A buyer disputes a payment and says the goods are damaged or not as described. "
    "You are shown the merchant's LISTING photo, the merchant's DISPATCH photo (taken at packing) and the BUYER's photo, as "
    "labelled, then the buyer's claim. Decide: REFUND (the buyer is owed), FIGHT (the claim is false or unsupported) or REVIEW "
    "(a person must look). Return ONLY JSON: {\"action\": \"refund\"|\"fight\"|\"review\", \"reason\": <short>}."
)


def _img(ref):
    data = P.load(ref)
    return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(data).decode()}}


def vl_alone(case, vision):
    ph = (case["evidence"].get("condition") or {}).get("photos") or {}
    content = []
    for role in ("listing", "outbound", "buyer"):
        if ph.get(role):
            content += [{"type": "text", "text": {"listing": "LISTING photo:", "outbound": "DISPATCH photo:", "buyer": "BUYER photo:"}[role]}, _img(ph[role])]
    content.append({"type": "text", "text": "Buyer's claim: " + case["claim"] + ("" if ph else "\n(No photos from anyone.)")})
    body = {"model": vision.models[0], "temperature": 0, "max_tokens": 900, "stream": False,
            "messages": [{"role": "system", "content": VL_ALONE_SYSTEM}, {"role": "user", "content": content}]}
    for attempt in range(3):
        r = httpx.post(vision.base_url + "/chat/completions", headers={"Authorization": f"Bearer {vision.api_key}"}, json=body, timeout=180)
        if r.status_code == 200:
            j = r.json()
            u = j.get("usage") or {}
            vision.usage["calls"] += 1
            vision.usage["prompt_tokens"] += u.get("prompt_tokens", 0) or 0
            vision.usage["completion_tokens"] += u.get("completion_tokens", 0) or 0
            text = re.sub(r"<think>.*?</think>", "", j["choices"][0]["message"].get("content") or "", flags=re.S)
            m = re.search(r"\{.*\}", text, re.S)
            try:
                d = json.loads(m.group(0))
                a = str(d.get("action", "")).lower()
                return {"action": {"refund": "concede", "fight": "fight", "review": "review"}.get(a, "review"), "reason": str(d.get("reason", ""))[:200]}
            except (ValueError, AttributeError):
                return {"action": "review", "reason": "unparseable"}
        if r.status_code == 402:
            raise RuntimeError("spend cap reached")
    return {"action": "review", "reason": "model unavailable"}


DECIDERS = {"naive": naive, "vl_alone": vl_alone, "warrant": warrant}


def run(cases, decider, vision):
    rows = []
    for c in cases:
        out = DECIDERS[decider](c, vision)
        want = c["truth"]["action"]
        got = out["action"]
        rows.append({"id": c["id"], "title": c["title"], "truth": want, "got": got, "ok": got == want, "reason": out.get("reason", ""),
                     "wrong_refund": (got == "concede" and want == "fight"), "wrongly_fought": (got == "fight" and want == "concede"), "out": out})
    return rows


def summary(rows):
    n = len(rows)
    return {"n": n, "correct": sum(r["ok"] for r in rows), "wrong_refunds": sum(r["wrong_refund"] for r in rows),
            "wrongly_fought": sum(r["wrongly_fought"] for r in rows),
            "to_review_when_not_review": sum(1 for r in rows if r["got"] == "review" and r["truth"] != "review"),
            "missed_review": sum(1 for r in rows if r["truth"] == "review" and r["got"] != "review")}
