"""Thin PayPal REST client for the sandbox: Orders v2, Payments v2 refunds, order tracking, Disputes v1.

Every call is recorded in `self.log` (method, path, status, short response) so the UI and proof files can show exactly
what was sent to PayPal.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
import uuid

import httpx

SANDBOX = "https://api-m.sandbox.paypal.com"

# Card-network reason codes the sandbox chargeback simulator maps onto Disputes API reasons.
CHARGEBACK_REASON_CODES = {
    "not_received": "1310",  # Visa 13.1 -> MERCHANDISE_OR_SERVICE_NOT_RECEIVED
    "not_as_described": "1330",  # Visa 13.3
    "not_authorized": "1040",  # Visa 10.4 card-absent fraud
    "amount_wrong": "1250",  # Visa 12.5 incorrect amount
    "duplicate_charge": "1260",  # Visa 12.6 duplicate processing
}


class PayPalError(RuntimeError):
    def __init__(self, status: int, body: str, path: str):
        super().__init__(f"PayPal {path} -> {status}: {body[:300]}")
        self.status = status
        self.body = body


class PayPal:
    def __init__(self, client_id: str | None = None, secret: str | None = None, base: str = SANDBOX):
        self.client_id = client_id or os.environ.get("PAYPAL_CLIENT_ID", "")
        self.secret = secret or os.environ.get("PAYPAL_CLIENT_SECRET", "")
        self.base = base
        self._token: str | None = None
        self._token_exp = 0.0
        self.merchant_id: str | None = os.environ.get("PAYPAL_MERCHANT_ID")
        self.log: list[dict] = []

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.secret)

    def token(self) -> str:
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        r = httpx.post(
            self.base + "/v1/oauth2/token",
            auth=(self.client_id, self.secret),
            data={"grant_type": "client_credentials"},
            timeout=30,
        )
        if r.status_code != 200:
            raise PayPalError(r.status_code, r.text, "/v1/oauth2/token")
        body = r.json()
        self._token = body["access_token"]
        self._token_exp = time.time() + int(body.get("expires_in", 3000))
        return self._token

    def _call(self, method: str, path: str, body: dict | None = None, multipart: dict | None = None, ok=(200, 201, 204)):
        # One request id for every attempt, so PayPal treats a retried POST as the same request (idempotent).
        headers = {"Authorization": f"Bearer {self.token()}", "PayPal-Request-Id": str(uuid.uuid4())}
        if multipart is None:
            headers["Content-Type"] = "application/json"
        for attempt in range(4):  # the sandbox returns 5xx/429 often; back off and retry
            try:
                if multipart is not None:
                    files = {"input": (None, json.dumps(multipart), "application/json")}
                    r = httpx.request(method, self.base + path, headers=headers, files=files, timeout=60)
                else:
                    r = httpx.request(method, self.base + path, headers=headers, json=body, timeout=60)
            except httpx.TransportError:
                if attempt == 3:
                    raise
                time.sleep(2 * 2 ** attempt)
                continue
            if r.status_code in ok or not (r.status_code == 429 or r.status_code >= 500) or attempt == 3:
                break
            time.sleep(2 * 2 ** attempt)
        entry = {
            "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "method": method,
            "path": path,
            "status": r.status_code,
            "debug_id": r.headers.get("Paypal-Debug-Id"),
        }
        self.log.append(entry)
        if r.status_code not in ok:
            entry["error"] = r.text[:400]
            raise PayPalError(r.status_code, r.text, path)
        return r.json() if r.content else {}

    # ---- Orders / payments -------------------------------------------------------------------------------------
    def create_paid_order(self, amount: str, custom_id: str, description: str, invoice_id: str | None = None) -> dict:
        """Create an Orders v2 order paid by a sandbox test card; PayPal captures it in the same call."""
        unit = {"custom_id": custom_id, "description": description[:127], "amount": {"currency_code": "USD", "value": amount}}
        if invoice_id:
            unit["invoice_id"] = invoice_id
        order = self._call("POST", "/v2/checkout/orders", {
            "intent": "CAPTURE",
            "purchase_units": [unit],
            "payment_source": {"card": {"number": "4111111111111111", "expiry": "2030-12", "security_code": "123", "name": "Sandbox Buyer"}},
        })
        if order.get("status") != "COMPLETED":
            order = self._call("POST", f"/v2/checkout/orders/{order['id']}/capture", {})
        unit = order["purchase_units"][0]
        capture = unit["payments"]["captures"][0]
        payee = unit.get("payee") or {}
        if payee.get("merchant_id"):
            self.merchant_id = payee["merchant_id"]
        return {"order_id": order["id"], "capture_id": capture["id"], "status": capture["status"], "amount": capture["amount"]["value"]}

    def get_order(self, order_id: str) -> dict:
        return self._call("GET", f"/v2/checkout/orders/{order_id}")

    def add_tracking(self, order_id: str, capture_id: str, carrier: str, number: str) -> dict:
        order = self._call("POST", f"/v2/checkout/orders/{order_id}/track", {
            "capture_id": capture_id, "tracking_number": number, "carrier": carrier, "notify_payer": False,
        })
        trackers = order.get("purchase_units", [{}])[0].get("shipping", {}).get("trackers", [])
        if not self.merchant_id:
            self.merchant_id = order.get("purchase_units", [{}])[0].get("payee", {}).get("merchant_id")
        return trackers[-1] if trackers else {}

    def refund(self, capture_id: str, amount: str, note: str) -> dict:
        r = self._call("POST", f"/v2/payments/captures/{capture_id}/refund", {
            "amount": {"currency_code": "USD", "value": amount}, "note_to_payer": note[:255],
        })
        return {"refund_id": r.get("id"), "status": r.get("status")}

    # ---- Disputes ----------------------------------------------------------------------------------------------
    def open_sandbox_chargeback(self, capture_id: str, amount: str, kind: str, claim_text: str) -> str:
        """Sandbox-only: have the card network raise a chargeback against our capture. The buyer's words travel in
        the processor notes and come back on the dispute as buyer-submitted evidence."""
        if not self.merchant_id:
            raise RuntimeError("merchant id unknown; create an order first")
        now = dt.datetime.now(dt.timezone.utc)
        iso = lambda t: t.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        r = self._call("POST", "/v2/customer-support/process-chargeback", {
            "adjacency": "PAYPAL",
            "file_layout": "ATCK_CB",
            "merchant_id": self.merchant_id,
            "financial_institution": {"processor": "FDMS"},
            "transaction": {"id_enc": capture_id, "amount": {"currency_code": "USD", "value": amount}},
            "instrument": {"instrument_type": "CARD", "card_brand": "VISA", "credit_card_transaction_id": str(int(time.time() * 1000)), "external": False},
            "dispute": {
                "id": None,
                "stage": "CHARGEBACK",
                "response_date": iso(now + dt.timedelta(days=20)),
                "status": "2",
                "amount": {"currency_code": "USD", "value": amount},
                "receive_date": iso(now),
                "event_code": "CHARGEBACK_INITIATED",
                "money_movement_date": iso(now),
                "event_type": "DEBIT",
                "chargeback_type": "REPORTED_TO_PROCESSOR",
                "processor_info": {
                    "reference_number": str(uuid.uuid4().int)[:23],
                    "reason_code": CHARGEBACK_REASON_CODES.get(kind, "1310"),
                    "notes": claim_text,
                },
            },
        })
        return r["dispute_id"]

    def list_disputes(self) -> list[dict]:
        items: list[dict] = []
        path = "/v1/customer/disputes?page_size=50"
        while path:
            body = self._call("GET", path)
            items.extend(body.get("items", []))
            nxt = [l["href"] for l in body.get("links", []) if l.get("rel") == "next"]
            path = nxt[0].replace(self.base, "") if nxt else None
        return items

    def show_dispute(self, dispute_id: str) -> dict:
        return self._call("GET", f"/v1/customer/disputes/{dispute_id}")

    def provide_evidence(self, dispute_id: str, evidences: list[dict]) -> dict:
        return self._call("POST", f"/v1/customer/disputes/{dispute_id}/provide-evidence", multipart={"evidences": evidences})

    def provide_supporting_info(self, dispute_id: str, notes: str) -> dict:
        return self._call("POST", f"/v1/customer/disputes/{dispute_id}/provide-supporting-info", multipart={"notes": notes[:2000]})

    def accept_claim(self, dispute_id: str, note: str, amount: str | None = None) -> dict:
        body = {"note": note[:2000], "accept_claim_type": "REFUND"}
        if amount:
            body = {"note": note[:2000], "accept_claim_type": "PARTIAL_REFUND", "refund_amount": {"currency_code": "USD", "value": amount}}
        return self._call("POST", f"/v1/customer/disputes/{dispute_id}/accept-claim", body)

    def make_offer(self, dispute_id: str, amount: str, note: str) -> dict:
        return self._call("POST", f"/v1/customer/disputes/{dispute_id}/make-offer", {
            "note": note[:2000], "offer_amount": {"currency_code": "USD", "value": amount}, "offer_type": "REFUND",
        })

    def verify_webhook(self, headers: dict, event: dict, webhook_id: str) -> bool:
        body = self._call("POST", "/v1/notifications/verify-webhook-signature", {
            "auth_algo": headers.get("paypal-auth-algo"),
            "cert_url": headers.get("paypal-cert-url"),
            "transmission_id": headers.get("paypal-transmission-id"),
            "transmission_sig": headers.get("paypal-transmission-sig"),
            "transmission_time": headers.get("paypal-transmission-time"),
            "webhook_id": webhook_id,
            "webhook_event": event,
        })
        return body.get("verification_status") == "SUCCESS"
