"""The merchant policy every eval case is judged under. Written down here so the ground truths in eval/cases.py and
eval/heldout2.py do not depend on whatever policy.json holds at run time. Mirrors the shipped default except for the
two final-sale entries, which the demo shop's catalogue defines."""

EVAL_POLICY = {
    "jurisdiction": "AU",
    "return_window_days": 30,
    "final_sale_categories": ["clearance"],
    "final_sale_skus": ["SECONDS-VASE"],
    "restocking_fee_pct": 15.0,
    "refund_without_fight_under": 25.0,
    "cost": {"dispute_fee": 20.0, "handling_hours": 0.5, "hourly_rate": 30.0},
    "auto": {"enabled": False, "max_refund": 150.0, "daily_refund_cap": 400.0, "max_fight_amount": 300.0},
}
