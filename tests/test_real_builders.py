"""Offline tests for the Test B builders (no model calls, no network)."""
from eval.real.test_b import (build_all, build_case, expected_outcome, load_labelled, outcome_class, outcome_ok, primary_reason)


def test_builds_180_cases_two_states_each():
    cases = build_all()
    assert len(cases) == 180
    assert {c["state"] for c in cases} == {"supporting", "contradicting"}
    assert len({c["id"] for c in cases}) == 180


def test_labelled_set_is_90_with_kinds():
    lab = load_labelled()
    assert len(lab) == 90
    assert all(x["kinds"] for x in lab)


def test_supporting_flips_each_kind_and_contradicting_is_baseline():
    sup = build_case(1, ["not_received"], "supporting", "x")
    con = build_case(1, ["not_received"], "contradicting", "x")
    assert sup["evidence"]["shipment"]["scans"][-1]["status"] == "IN_TRANSIT"
    assert con["evidence"]["shipment"]["scans"][-1]["status"] == "DELIVERED"
    un = build_case(2, ["unauthorised"], "supporting", "x")
    assert un["evidence"]["purchase_record"]["approval"]["method"] == "none"
    assert build_case(2, ["unauthorised"], "contradicting", "x")["evidence"]["purchase_record"]["approval"]["method"] != "none"
    amt = build_case(3, ["amount_wrong"], "supporting", "x")
    assert amt["amount"] > 100
    assert build_case(3, ["amount_wrong"], "contradicting", "x")["amount"] == 100


def test_duplicate_support_adds_other_charge():
    assert build_case(4, ["duplicate"], "supporting", "x")["evidence"]["other_charges"]
    assert not build_case(4, ["duplicate"], "contradicting", "x")["evidence"].get("other_charges")


def test_expected_outcomes_follow_rules():
    assert expected_outcome(["not_received"], "supporting") == {"action": "pay", "also_ok": []}
    assert expected_outcome(["not_received", "other"], "supporting")["action"] == "pay"
    assert expected_outcome(["not_received"], "contradicting")["action"] == "fight"
    o = expected_outcome(["other"], "supporting")
    assert o["action"] == "fight" and "review" in o["also_ok"]


def test_outcome_class_and_ok():
    assert outcome_class("concede") == "pay" and outcome_class("partial") == "pay" and outcome_class("fight") == "fight"
    assert outcome_ok("partial", {"action": "pay", "also_ok": []})
    assert not outcome_ok("concede", {"action": "fight", "also_ok": []})
    assert outcome_ok("review", {"action": "fight", "also_ok": ["review"]})
    assert not outcome_ok("concede", {"action": "fight", "also_ok": ["review"]})


def test_primary_reason_other_has_reason_code():
    assert primary_reason(["other"]) == "OTHER"
    assert primary_reason(["duplicate", "other"]) == "DUPLICATE_TRANSACTION"
