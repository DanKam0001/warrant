"""The merchant's own policy: what they have decided about returns, final sale, small refunds and auto mode.

The policy is data (data/policy.json, editable in the UI). It sits BELOW the consumer-protection floor (app/floor.py):
no policy value can override the floor. A default lives here so a missing or damaged file never stops the app.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

JURISDICTIONS = {
    "AU": "Australian Consumer Law (consumer guarantees)",
    "UK": "UK Consumer Rights Act (goods must be as described and of satisfactory quality)",
    "EU": "EU consumer sales rules (legal guarantee of conformity)",
    "US": "general US consumer-protection practice (state law varies)",
}

DEFAULT_POLICY = {
    "jurisdiction": "AU",
    "return_window_days": 30,
    "final_sale_categories": ["clearance"],
    "final_sale_skus": [],
    "restocking_fee_pct": 10.0,
    "refund_without_fight_under": 10.0,
    "cost": {"dispute_fee": 20.0, "handling_hours": 0.5, "hourly_rate": 30.0},
    "auto": {"enabled": False, "max_refund": 100.0, "daily_refund_cap": 300.0, "max_fight_amount": 250.0},
}


def _num(v, default, lo, hi):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return min(hi, max(lo, x))


def _strs(v, default):
    if not isinstance(v, list):
        return list(default)
    return [str(x).strip().lower() for x in v if str(x).strip()][:50]


def normalise_policy(raw: dict | None) -> dict:
    """Merge over the defaults and clamp every value, so a bad edit can never produce a nonsense policy."""
    raw = raw if isinstance(raw, dict) else {}
    d = DEFAULT_POLICY
    cost = raw.get("cost") if isinstance(raw.get("cost"), dict) else {}
    auto = raw.get("auto") if isinstance(raw.get("auto"), dict) else {}
    j = str(raw.get("jurisdiction", d["jurisdiction"])).upper()
    return {
        "jurisdiction": j if j in JURISDICTIONS else d["jurisdiction"],
        "return_window_days": int(_num(raw.get("return_window_days"), d["return_window_days"], 0, 365)),
        "final_sale_categories": _strs(raw.get("final_sale_categories"), d["final_sale_categories"]),
        "final_sale_skus": [s.upper() for s in _strs(raw.get("final_sale_skus"), d["final_sale_skus"])],
        "restocking_fee_pct": _num(raw.get("restocking_fee_pct"), d["restocking_fee_pct"], 0, 100),
        "refund_without_fight_under": _num(raw.get("refund_without_fight_under"), d["refund_without_fight_under"], 0, 1000),
        "cost": {
            "dispute_fee": _num(cost.get("dispute_fee"), d["cost"]["dispute_fee"], 0, 1000),
            "handling_hours": _num(cost.get("handling_hours"), d["cost"]["handling_hours"], 0, 100),
            "hourly_rate": _num(cost.get("hourly_rate"), d["cost"]["hourly_rate"], 0, 1000),
        },
        "auto": {
            "enabled": bool(auto.get("enabled", False)),
            "max_refund": _num(auto.get("max_refund"), d["auto"]["max_refund"], 0, 100000),
            "daily_refund_cap": _num(auto.get("daily_refund_cap"), d["auto"]["daily_refund_cap"], 0, 1000000),
            "max_fight_amount": _num(auto.get("max_fight_amount"), d["auto"]["max_fight_amount"], 0, 100000),
        },
    }


def load_policy(path: Path | None) -> dict:
    if path and path.exists():
        try:
            return normalise_policy(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    return copy.deepcopy(DEFAULT_POLICY)


def save_policy(path: Path, policy: dict) -> dict:
    policy = normalise_policy(policy)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(policy, indent=2), encoding="utf-8")
    return policy


def is_final_sale(policy: dict, record: dict) -> bool:
    cat = str(record.get("category") or "").lower()
    skus = {str(i.get("sku", "")).upper() for i in record.get("items") or []}
    return cat in policy["final_sale_categories"] or bool(skus & set(policy["final_sale_skus"]))
