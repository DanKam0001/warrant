"""Warrant's workflow: seed sandbox orders, take in disputes, analyse a claim, answer PayPal."""
from __future__ import annotations

import copy
import datetime as dt
import json
import os
import threading
from pathlib import Path

from . import floor as floor_mod
from .checker import check_statements
from .decision import decide
from .evidence import make_case_evidence, order_total, record_hash
from .paypal import PayPal, PayPalError
from .policy import load_policy, save_policy
from .scenarios import DEMO
from .splitter import Splitter

# WARRANT_DATA_DIR lets a second instance (a test run, a sandbox pass) keep its own store beside the real one.
DATA = Path(os.environ.get("WARRANT_DATA_DIR") or Path(__file__).resolve().parent.parent / "data")
RETRY_SECONDS = 60
HUMAN_ACTIONS = ("concede", "partial", "fight")


class ReviewRequired(ValueError):
    """Raised when something tries to send a REVIEW case without a person's decision."""


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def claim_from_dispute(d: dict) -> str:
    for e in d.get("evidences") or []:
        if e.get("source") == "SUBMITTED_BY_BUYER" and e.get("notes"):
            return e["notes"]
    msgs = [m.get("content") for m in d.get("messages") or [] if m.get("posted_by") == "BUYER"]
    return "\n".join(m for m in msgs if m) or ""


def actions_offered(d: dict) -> list[str]:
    return [l["rel"] for l in d.get("links") or [] if l.get("rel") != "self"]


class Warrant:
    def __init__(self, paypal: PayPal | None = None, splitter: Splitter | None = None, path: Path | None = None):
        self.pp = paypal or PayPal()
        self.splitter = splitter or Splitter()
        self.path = path or DATA / "store.json"
        self.policy_path = self.path.parent / "policy.json"
        self.activity_path = self.path.parent / "activity.jsonl"
        self.lock = threading.RLock()
        self.auto_lock = threading.Lock()
        self.state = {"orders": {}, "cases": {}, "events": [], "seeding": None}
        if self.path.exists():
            self.state = json.loads(self.path.read_text(encoding="utf-8"))
        self.state.setdefault("auto", {"enabled": False})
        self.policy = load_policy(self.policy_path)

    # ---- policy -------------------------------------------------------------------------------------------------
    def set_policy(self, raw: dict) -> dict:
        """Save an edited policy and re-analyse every case that has not been answered yet (the rules changed under them)."""
        with self.lock:
            raw = {**raw, "auto": {**(raw.get("auto") or {}), "enabled": False}}  # the on/off switch lives in the store, not the policy file
            self.policy = save_policy(self.policy_path, raw)
            todo = [d for d, c in self.state["cases"].items() if c.get("stage") != "answered" and c.get("analysis")]
        self.log_action(None, "policy_changed", "human", "Merchant policy edited and saved.")
        for d in todo:
            try:
                self.analyse(d, quiet=True)
            except Exception as e:  # noqa: BLE001
                self.event(f"Re-analysis of {d} after the policy change failed: {e}")
        return self.policy

    # ---- activity log (append-only, one JSON object per line) ---------------------------------------------------
    def log_action(self, dispute_id: str | None, action: str, by: str, reason: str, **detail) -> dict:
        entry = {"at": now(), "dispute_id": dispute_id, "action": action, "by": by, "reason": reason, "detail": detail}
        with self.lock:
            self.activity_path.parent.mkdir(parents=True, exist_ok=True)
            with self.activity_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
            case = self.state["cases"].get(dispute_id) if dispute_id else None
            if case is not None:
                order = self.state["orders"].get(case["capture_id"]) or {}
                entry["order"] = (order.get("evidence") or {}).get("purchase_record", {}).get("order_ref")
                case["last_action"] = {"at": entry["at"], "action": action, "by": by, "reason": reason}
                self.save()
        return entry

    def activity(self, dispute_id: str | None = None, limit: int = 200) -> list[dict]:
        if not self.activity_path.exists():
            return []
        rows = []
        for line in self.activity_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if dispute_id is None or row.get("dispute_id") == dispute_id:
                rows.append(row)
        return rows[-limit:][::-1]

    # ---- persistence -------------------------------------------------------------------------------------------
    def save(self):
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.state, indent=2), encoding="utf-8")

    def event(self, text: str, **kw):
        with self.lock:
            self.state["events"].insert(0, {"at": now(), "text": text, **kw})
            del self.state["events"][200:]
            self.save()

    # ---- seeding ----------------------------------------------------------------------------------------------
    def reset(self):
        with self.lock:
            self.state = {"orders": {}, "cases": {}, "events": [], "seeding": None, "auto": {"enabled": False}}  # a fresh demo also clears an old kill-switch note
            self.save()

    def seed_demo(self, keys: list[str] | None = None, photos: bool = False) -> list[dict]:
        """Create each demo order for real in the sandbox, register its tracking, then open a chargeback on it."""
        made = []
        with self.lock:
            self.state["seeding"] = {"started": now(), "done": False}
        for sc in DEMO:
            if keys and sc["key"] not in keys:
                continue
            if sc.get("optional") and not photos and not (keys and sc["key"] in keys):
                continue
            rec = sc["record"]
            h = record_hash(rec)
            amount = f"{order_total(rec):.2f}"
            # PayPal rejects a reused invoice id, and every reset re-creates the same records.
            invoice = f"{rec['order_ref']}-{h[:8]}-{dt.datetime.now(dt.timezone.utc):%Y%m%d%H%M%S}"
            o = self.pp.create_paid_order(amount, h, f"{rec['order_ref']} {rec['items'][0]['name']}", invoice_id=invoice)
            ship = copy.deepcopy(sc["shipment"])
            tr = self.pp.add_tracking(o["order_id"], o["capture_id"], ship["carrier"], ship["tracking_number"])
            ship["paypal_tracker_id"] = tr.get("id")
            ev = make_case_evidence(rec, ship, paypal={
                "order_id": o["order_id"], "capture_id": o["capture_id"], "captured_amount": float(o["amount"]), "custom_id": h,
            })
            if sc.get("condition"):
                ev["condition"] = copy.deepcopy(sc["condition"])
            dispute_id = self.pp.open_sandbox_chargeback(o["capture_id"], amount, sc["reason_kind"], sc["claim"])
            with self.lock:
                self.state["orders"][o["capture_id"]] = {"scenario": sc["key"], "label": sc["label"], "evidence": ev, "created": now(), "opened_dispute": dispute_id}
                self.save()
            self.event(f"Order {rec['order_ref']} paid (PayPal order {o['order_id']}), tracking {ship['tracking_number']} added; buyer opened dispute {dispute_id}")
            made.append({"scenario": sc["key"], "order_id": o["order_id"], "capture_id": o["capture_id"], "dispute_id": dispute_id})
        with self.lock:
            self.state["seeding"] = {"started": self.state["seeding"]["started"], "done": True, "finished": now()}
            self.save()
        return made

    # ---- intake -------------------------------------------------------------------------------------------------
    def sync(self) -> int:
        """Poll PayPal for disputes on our captures (webhooks need a public URL; polling works anywhere)."""
        new = 0
        items = self.pp.list_disputes()
        for it in items:
            did = it["dispute_id"]
            with self.lock:
                known = did in self.state["cases"]
            caps = [t.get("seller_transaction_id") for t in it.get("disputed_transactions", [])]
            if not known:
                try:
                    d = self.pp.show_dispute(did)
                except PayPalError:
                    continue  # an old or unrelated dispute the sandbox won't serve right now must not stop intake
                caps = [t.get("seller_transaction_id") for t in d.get("disputed_transactions", [])]
                cap = next((c for c in caps if c in self.state["orders"]), None)
                if not cap:
                    continue
                self._add_case(d, cap)
                new += 1
            else:
                with self.lock:
                    c = self.state["cases"][did]
                    c["paypal_status"] = it.get("status")
                    c["paypal_state"] = it.get("dispute_state")
                    need_outcome = it.get("status") == "RESOLVED" and not c.get("outcome")
                if need_outcome:
                    self._record_outcome(did)
        # The sandbox list endpoint can lag minutes behind; disputes our seeder opened are also fetched by id.
        with self.lock:
            pending = [(cap, o["opened_dispute"]) for cap, o in self.state["orders"].items()
                       if o.get("opened_dispute") and o["opened_dispute"] not in self.state["cases"]]
        for cap, did in pending:
            try:
                d = self.pp.show_dispute(did)
            except PayPalError:
                continue
            self._add_case(d, cap, intake="lookup by id (sandbox list lagging)")
            new += 1
        self.save()
        return new

    def _record_outcome(self, did: str):
        """When PayPal resolves a dispute, note who it went to, so the status strip can show won / lost."""
        try:
            d = self.pp.show_dispute(did)
        except PayPalError:
            return
        code = str((d.get("dispute_outcome") or {}).get("outcome_code") or "")
        if not code:
            return
        won = code == "RESOLVED_SELLER_FAVOUR"
        lost = code in ("RESOLVED_BUYER_FAVOUR", "RESOLVED_WITH_PAYOUT")
        with self.lock:
            c = self.state["cases"][did]
            c["outcome"] = {"code": code, "result": "won" if won else "lost" if lost else "other"}
            self.save()
        self.log_action(did, "paypal_resolved", "system", f"PayPal resolved the dispute: {code}.", outcome=code)

    def _add_case(self, d: dict, cap: str, intake: str = "polling"):
        with self.lock:
            # Two overlapping syncs can both see the dispute as new; the second must not wipe the first's work.
            if d["dispute_id"] in self.state["cases"]:
                return
            self.state["cases"][d["dispute_id"]] = {
                "dispute_id": d["dispute_id"],
                "capture_id": cap,
                "reason": d.get("reason"),
                "amount": float(d["dispute_amount"]["value"]),
                "claim": claim_from_dispute(d),
                "paypal_status": d.get("status"),
                "paypal_state": d.get("dispute_state"),
                "custom_on_dispute": next((t.get("custom") for t in d.get("disputed_transactions", [])), None),
                "intake": intake,
                "received": now(),
                "analysis": None,
                "response": None,
                "stage": "new",
            }
        self.event(f"Dispute {d['dispute_id']} received via {intake}: {d.get('reason')} ${d['dispute_amount']['value']}")

    # ---- analysis ----------------------------------------------------------------------------------------------
    def analyse(self, dispute_id: str, quiet: bool = False) -> dict:
        with self.lock:
            case = self.state["cases"][dispute_id]
            order = self.state["orders"][case["capture_id"]]
            policy = self.policy
        result = analyse_claim(self.splitter, case["claim"], order["evidence"], case["reason"], case["amount"], policy)
        if case.get("custom_on_dispute") and case["custom_on_dispute"] != order["evidence"]["record_hash"]:
            result["integrity_warning"] = "custom_id on the disputed PayPal transaction does not match our record hash"
        with self.lock:
            case["analysis"] = result
            if case.get("stage") != "answered":
                case["stage"] = "analysed"
            self.save()
        dec = result["decision"]
        if not quiet:
            self.event(f"Analysed {dispute_id}: {dec['action']} (splitter: {result['split']['source']})")
        self.log_action(dispute_id, "analysed", "system", dec["summary"], decision=dec["action"], refund=dec["refund_amount"],
                        rules=[f"{t['layer']}:{t['rule']}" for t in dec["trace"]])
        return result

    # ---- respond -----------------------------------------------------------------------------------------------
    def _effective_decision(self, dispute_id: str, override: dict | None) -> dict:
        """The decision to execute: the analysed one, or a person's choice. REVIEW can never be sent without a person."""
        case = self.state["cases"][dispute_id]
        dec = dict(case["analysis"]["decision"])
        if override:
            act = override.get("action")
            if act not in HUMAN_ACTIONS:
                raise ValueError(f"unknown action '{act}'")
            amount = dec["amount"]
            if act == "concede":
                refund = amount
            elif act == "fight":
                refund = 0.0
            else:
                refund = override.get("refund", dec.get("proposed_refund") or dec["refund_amount"])
                refund = round(float(refund), 2)
                if not 0 < refund < amount:
                    raise ValueError("a partial refund must be more than 0 and less than the disputed amount")
            dec.update(action=act, refund_amount=refund, summary=f"Decided by a person: {act} ${refund:.2f}." if act != "fight" else "Decided by a person: fight with the evidence.")
        elif dec["action"] == "review":
            raise ReviewRequired("this case needs a person's decision (refund, partial refund or fight)")
        return dec

    def respond(self, dispute_id: str, by: str = "human", override: dict | None = None, reason: str | None = None) -> dict:
        with self.lock:
            case = self.state["cases"][dispute_id]
            order = self.state["orders"][case["capture_id"]]
        if not case.get("analysis"):
            self.analyse(dispute_id)
        dec = self._effective_decision(dispute_id, override)
        ev = order["evidence"]
        d = self.pp.show_dispute(dispute_id)
        offered = actions_offered(d)
        steps = []

        def attempt(name, fn):
            try:
                out = fn()
                steps.append({"call": name, "ok": True, "result": out})
                return out
            except PayPalError as e:
                steps.append({"call": name, "ok": False, "status": e.status, "error": e.body[:300]})
                return None

        notes = evidence_notes(case["analysis"], ev)
        if dec["action"] == "fight":
            if "provide_evidence" in offered:
                sh = ev.get("shipment") or {}
                evidences = [{"evidence_type": "PROOF_OF_FULFILLMENT", "evidence_info": {"tracking_info": [{"carrier_name": sh.get("carrier", "OTHER"), "tracking_number": sh.get("tracking_number", "")}]}, "notes": notes[:2000]}]
                attempt("provide-evidence", lambda: self.pp.provide_evidence(dispute_id, evidences))
            if not any(s["ok"] for s in steps) and "provide_supporting_info" in offered:
                attempt("provide-supporting-info", lambda: self.pp.provide_supporting_info(dispute_id, notes))
        else:
            amount = f"{dec['refund_amount']:.2f}"
            label = "Full refund" if dec["action"] == "concede" else "Partial refund"
            # On a card chargeback PayPal blocks Payments refunds (422 REFUND_NOT_PERMITTED_DUE_TO_CHARGEBACK);
            # accepting the claim is how the money goes back, once PayPal offers that action.
            if dec["action"] == "partial" and "make_offer" in offered:
                attempt("make-offer", lambda: self.pp.make_offer(dispute_id, amount, f"{label}: {dec['summary']}"))
            if not any(s["ok"] for s in steps) and "accept_claim" in offered:
                part = amount if dec["action"] == "partial" else None
                attempt("accept-claim", lambda: self.pp.accept_claim(dispute_id, f"{label}: {dec['summary']}", part))
            if not any(s["ok"] for s in steps) and not case.get("refund_blocked"):
                refund = attempt("refund", lambda: self.pp.refund(case["capture_id"], amount, f"{label} for {ev['purchase_record']['order_ref']}"))
                if refund:
                    with self.lock:
                        ev["refunds"].append({"refund_id": refund["refund_id"], "amount": float(amount), "at": now()})
                    if "provide_supporting_info" in offered:
                        attempt("provide-supporting-info", lambda: self.pp.provide_supporting_info(dispute_id, f"{label} of ${amount} issued, PayPal refund id {refund['refund_id']}. {notes}"))
                elif "REFUND_NOT_PERMITTED_DUE_TO_CHARGEBACK" in (steps[-1].get("error") or ""):
                    with self.lock:
                        case["refund_blocked"] = "chargeback"
        after = self.pp.show_dispute(dispute_id)
        response = {
            "at": now(),
            "action": dec["action"],
            "refund_amount": dec["refund_amount"],
            "by": by,
            "offered_by_paypal": offered,
            "steps": steps,
            "answered": any(s["ok"] for s in steps),
            "status_after": after.get("status"),
            "state_after": after.get("dispute_state"),
            "pending_note": None if any(s["ok"] for s in steps) else
            ("Refund blocked by the chargeback; waiting for PayPal to offer accept-claim." if case.get("refund_blocked") else
             "PayPal offered no response action yet; answer will be retried on next respond."),
        }
        with self.lock:
            case["response"] = response
            case["paypal_status"] = after.get("status")
            case["paypal_state"] = after.get("dispute_state")
            case["stage"] = "answered" if response["answered"] else "waiting_on_paypal"
            case["final"] = {"action": dec["action"], "refund_amount": dec["refund_amount"], "by": by, "at": response["at"]}
            case["last_attempt"] = response["at"]
            case.pop("auto_blocked", None)
            self.save()
        self.event(f"Responded to {dispute_id}: {dec['action']} -> " + (", ".join(f"{s['call']} {'ok' if s['ok'] else 'failed'}" for s in steps)
                                                                         or "nothing sent: " + response["pending_note"]))
        why = reason or dec["summary"]
        verb = {"concede": "Refunded in full", "partial": "Partial refund", "fight": "Evidence sent to PayPal"}[dec["action"]]
        if not response["answered"]:
            verb = "Tried to answer, waiting on PayPal"
        self.log_action(dispute_id, dec["action"] if response["answered"] else "waiting_on_paypal", by, f"{verb}: {why}",
                        refund=dec["refund_amount"], answered=response["answered"], calls=[s["call"] for s in steps if s["ok"]])
        return response

    # ---- auto mode ---------------------------------------------------------------------------------------------
    def auto_enabled(self) -> bool:
        return bool(self.state.get("auto", {}).get("enabled"))

    def set_auto(self, enabled: bool, by: str = "human") -> dict:
        with self.lock:
            self.state.setdefault("auto", {})["enabled"] = bool(enabled)
            self.state["auto"]["changed_at"] = now()
            self.save()
        self.log_action(None, "auto_on" if enabled else "auto_off", by,
                        "Auto mode switched ON: sure decisions run without a click." if enabled else "Auto mode switched OFF.")
        return self.state["auto"]

    def kill_switch(self) -> dict:
        """Stop auto mode immediately. An auto_tick already running re-checks the flag before each action."""
        with self.lock:
            self.state.setdefault("auto", {})["enabled"] = False
            self.state["auto"]["changed_at"] = now()
            self.state["auto"]["killed_at"] = now()
            self.save()
        self.log_action(None, "kill_switch", "human", "Kill switch pressed: auto mode is off and nothing more runs on its own.")
        return self.state["auto"]

    def auto_refunded_today(self) -> float:
        today = now()[:10]
        return round(sum(float(r["detail"].get("refund") or 0) for r in self.activity(limit=100000)
                         if r["by"] == "auto" and r["at"][:10] == today and r["action"] in ("concede", "partial") and r["detail"].get("answered")), 2)

    def auto_check(self, case: dict, spent_today: float) -> tuple[bool, str]:
        """Is this case SURE enough to run without a person? Returns (ok, reason it is or is not)."""
        a = self.policy["auto"]
        analysis = case.get("analysis")
        if not analysis:
            return False, "not analysed yet"
        dec = analysis["decision"]
        checked = analysis["checked"]
        if analysis.get("integrity_warning"):
            return False, "the PayPal record does not match ours (integrity warning)"
        if dec["action"] == "review":
            return False, "needs a person: " + "; ".join(dec.get("review_reasons") or ["unclear evidence"])
        if any(c["type"] == "request" and c.get("remedy") in ("replacement", "store_credit") for c in checked):
            return False, "the buyer asked for a replacement or store credit; a person arranges that"
        if dec["action"] in ("concede", "partial"):
            if dec["refund_amount"] > a["max_refund"] + 0.005:
                return False, f"refund ${dec['refund_amount']:.2f} is over the auto limit ${a['max_refund']:.2f}"
            if spent_today + dec["refund_amount"] > a["daily_refund_cap"] + 0.005:
                return False, f"would pass today's auto cap (${spent_today:.2f} of ${a['daily_refund_cap']:.2f} used)"
            return True, f"{dec['action']} ${dec['refund_amount']:.2f} is inside the auto limits"
        facts = [c for c in checked if c["verdict"] not in ("ignored", "noted")]
        if dec.get("no_auto_fight") or any(c["type"] == "not_authorized" for c in facts):
            return False, "unauthorized-charge claims are never auto-fought"
        if dec["amount"] > a["max_fight_amount"] + 0.005:
            return False, f"${dec['amount']:.2f} is over the auto fight limit ${a['max_fight_amount']:.2f}"
        if dec["amount"] > 0 and not (facts and all(c["verdict"] == "false" and c["strength"] == "strong" for c in facts)):
            return False, "the evidence is not strong on every statement"
        if any(c.get("policy_denial") for c in facts):
            return False, "this fight rests on a merchant policy rather than evidence; a person confirms it"
        return True, "every statement is contradicted by strong evidence" if dec["amount"] > 0 else "already refunded; answer with proof"

    def auto_tick(self) -> list[dict]:
        """Run every sure decision. Safe to call often: it does nothing when auto mode is off or another tick is running."""
        if not self.auto_enabled() or not self.auto_lock.acquire(blocking=False):
            return []
        done = []
        try:
            with self.lock:
                order_ids = list(self.state["cases"])
            for did in order_ids:
                if not self.auto_enabled():  # kill switch pressed mid-run
                    break
                with self.lock:
                    case = self.state["cases"][did]
                    stage = case.get("stage")
                if stage == "answered":
                    continue
                if stage == "waiting_on_paypal":
                    last = case.get("last_attempt") or ""
                    if last and (dt.datetime.fromisoformat(now()) - dt.datetime.fromisoformat(last)).total_seconds() < RETRY_SECONDS:
                        continue
                if not case.get("analysis"):
                    try:
                        self.analyse(did)
                    except Exception as e:  # noqa: BLE001
                        self.event(f"Auto mode could not analyse {did}: {e}")
                        continue
                ok, why = self.auto_check(case, self.auto_refunded_today())
                if not ok:
                    if case.get("auto_blocked") != why:
                        with self.lock:
                            case["auto_blocked"] = why
                            self.save()
                        self.log_action(did, "auto_skipped", "auto", f"Left in the queue: {why}.")
                    continue
                if not self.auto_enabled():
                    break
                try:
                    r = self.respond(did, by="auto", reason=why)
                    done.append({"dispute_id": did, "action": r["action"], "answered": r["answered"]})
                except Exception as e:  # noqa: BLE001
                    self.log_action(did, "auto_failed", "auto", f"Auto action failed: {e}")
            return done
        finally:
            self.auto_lock.release()


def analyse_claim(splitter: Splitter, claim: str, evidence: dict, reason: str | None, amount: float, policy: dict | None = None) -> dict:
    """Split (model) -> check (code) -> consumer-protection floor (code) -> decide with the merchant's policy (code)."""
    from .policy import DEFAULT_POLICY, normalise_policy

    policy = normalise_policy(policy) if policy else DEFAULT_POLICY
    split = splitter.split(claim)
    checked = check_statements(split["statements"], evidence, reason, policy)
    fl = floor_mod.evaluate(checked, evidence, policy)
    refunded = sum(float(r["amount"]) for r in evidence.get("refunds") or [])
    decision = decide(checked, amount, already_refunded=refunded, policy=policy, floor=fl)
    return {"split": split, "checked": checked, "decision": decision}


def evidence_notes(analysis: dict, ev: dict) -> str:
    lines = [f"Order {ev['purchase_record']['order_ref']}; purchase record hash {ev['record_hash']} (= custom_id on PayPal order {ev['paypal'].get('order_id')})."]
    for c in analysis["checked"]:
        if c["verdict"] in ("ignored", "noted"):
            continue
        refs = "; ".join(f"{e['kind']} {e['ref']}: {e['detail']}" for e in c["evidence"])
        lines.append(f"Claim '{c['quote'][:120]}' -> {c['verdict'].upper()}: {c['reason']} [{refs}]")
    return "\n".join(lines)
