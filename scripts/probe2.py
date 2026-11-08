"""Probe: can we pay+capture an order fully by API (card source), track it, and open a dispute on it?"""
import json
import os
import uuid

import httpx
from dotenv import load_dotenv

load_dotenv(".env")
B = "https://api-m.sandbox.paypal.com"
tok = httpx.post(
    B + "/v1/oauth2/token",
    auth=(os.environ["PAYPAL_CLIENT_ID"], os.environ["PAYPAL_CLIENT_SECRET"]),
    data={"grant_type": "client_credentials"},
    timeout=30,
).json()["access_token"]


def call(method, path, body=None, **kw):
    h = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json", "PayPal-Request-Id": str(uuid.uuid4())}
    h.update(kw.get("headers", {}))
    r = httpx.request(method, B + path, headers=h, json=body, timeout=60)
    print(method, path, r.status_code)
    print(r.text[:1500])
    return r


o = call("POST", "/v2/checkout/orders", {
    "intent": "CAPTURE",
    "purchase_units": [{"custom_id": "probe-1", "amount": {"currency_code": "USD", "value": "42.00"}}],
    "payment_source": {"card": {"number": "4111111111111111", "expiry": "2030-12", "security_code": "123", "name": "Probe Buyer"}},
})
oid = o.json().get("id")
st = o.json().get("status")
if st != "COMPLETED":
    c = call("POST", f"/v2/checkout/orders/{oid}/capture", {})
    cap = c.json()
else:
    cap = o.json()
try:
    cid = cap["purchase_units"][0]["payments"]["captures"][0]["id"]
except Exception:
    cid = None
print("capture id", cid)
if cid:
    call("POST", f"/v2/checkout/orders/{oid}/track", {"tracking_number": "1Z999AA10123456784", "carrier": "UPS", "capture_id": cid, "notify_payer": False})
    call("POST", "/v1/customer/disputes", {"disputed_transactions": [{"buyer_transaction_id": cid}], "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED", "dispute_amount": {"currency_code": "USD", "value": "42.00"}})
