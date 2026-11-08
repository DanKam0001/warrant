"""Warrant's workflow: seed sandbox orders, take in disputes, analyse a claim, answer PayPal."""
from __future__ import annotations

import copy
import datetime as dt
import json
import threading
from pathlib import Path

from .checker import check_statements
from .decision import decide
from .evidence import make_case_evidence, order_total, record_hash
from .paypal import PayPal, PayPalError
from .scenarios import DEMO
from .splitter import Splitter

DATA = Path(__file__).resolve().parent.parent / "data"


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
        self.lock = threading.RLock()
        self.state = {"orders": {}, "cases": {}, "events": [], "seeding": None}
        if self.path.exists():
            self.state = json.loads(self.path.read_text(encoding="utf-8"))

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
            self.state = {"orders": {}, "cases": {}, "events": [], "seeding": None}
            self.save()

    def seed_demo(self, keys: list[str] | None = None) -> list[dict]:
        """Create each demo order for real in the sandbox, register its tracking, then open a chargeback on it."""
        made = []
        with self.lock:
            self.state["seeding"] = {"started": now(), "done": False}
        for sc in DEMO:
            if keys and sc["key"] not in keys:
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
                d = self.pp.show_dispute(did)
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
    def analyse(self, dispute_id: str) -> dict:
        with self.lock:
            case = self.state["cases"][dispute_id]
            order = self.state["orders"][case["capture_id"]]
        result = analyse_claim(self.splitter, case["claim"], order["evidence"], case["reason"], case["amount"])
        if case.get("custom_on_dispute") and case["custom_on_dispute"] != order["evidence"]["record_hash"]:
            result["integrity_warning"] = "custom_id on the disputed PayPal transaction does not match our record hash"
        with self.lock:
            case["analysis"] = result
            case["stage"] = "analysed"
            self.save()
        self.event(f"Analysed {dispute_id}: {result['decision']['action']} (splitter: {result['split']['source']})")
        return result

    # ---- respond -----------------------------------------------------------------------------------------------
    def respond(self, dispute_id: str) -> dict:
        with self.lock:
            case = self.state["cases"][dispute_id]
            order = self.state["orders"][case["capture_id"]]
        if not case.get("analysis"):
            self.analyse(dispute_id)
        dec = case["analysis"]["decision"]
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
            self.save()
        self.event(f"Responded to {dispute_id}: {dec['action']} -> " + (", ".join(f"{s['call']} {'ok' if s['ok'] else 'failed'}" for s in steps)
                                                                         or "nothing sent: " + response["pending_note"]))
        return response


def analyse_claim(splitter: Splitter, claim: str, evidence: dict, reason: str | None, amount: float) -> dict:
    split = splitter.split(claim)
    checked = check_statements(split["statements"], evidence, reason)
    refunded = sum(float(r["amount"]) for r in evidence.get("refunds") or [])
    decision = decide(checked, amount, already_refunded=refunded)
    return {"split": split, "checked": checked, "decision": decision}


def evidence_notes(analysis: dict, ev: dict) -> str:
    lines = [f"Order {ev['purchase_record']['order_ref']}; purchase record hash {ev['record_hash']} (= custom_id on PayPal order {ev['paypal'].get('order_id')})."]
    for c in analysis["checked"]:
        if c["verdict"] == "ignored":
            continue
        refs = "; ".join(f"{e['kind']} {e['ref']}: {e['detail']}" for e in c["evidence"])
        lines.append(f"Claim '{c['quote'][:120]}' -> {c['verdict'].upper()}: {c['reason']} [{refs}]")
    return "\n".join(lines)
