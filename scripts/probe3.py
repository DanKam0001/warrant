"""Probe: open a sandbox card chargeback on a capture we made, then read it back via the Disputes API."""
import datetime as dt
import json
import os
import sys
import time
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
H = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}

cap_id = sys.argv[1] if len(sys.argv) > 1 else "8VJ23516B33731529"
now = dt.datetime.now(dt.timezone.utc)
iso = lambda t: t.strftime("%Y-%m-%dT%H:%M:%S.000Z")
body = {
    "adjacency": "PAYPAL",
    "file_layout": "ATCK_CB",
    "merchant_id": "U2S3N8YHBYH3C",
    "financial_institution": {"processor": "FDMS"},
    "transaction": {"id_enc": cap_id, "amount": {"currency_code": "USD", "value": "42.00"}},
    "instrument": {"instrument_type": "CARD", "card_brand": "VISA", "credit_card_transaction_id": str(int(time.time() * 1000)), "external": False},
    "dispute": {
        "id": None,
        "stage": "CHARGEBACK",
        "response_date": iso(now + dt.timedelta(days=20)),
        "status": "2",
        "amount": {"currency_code": "USD", "value": "42.00"},
        "receive_date": iso(now),
        "event_code": "CHARGEBACK_INITIATED",
        "money_movement_date": iso(now),
        "event_type": "DEBIT",
        "chargeback_type": "REPORTED_TO_PROCESSOR",
        "processor_info": {"reference_number": str(uuid.uuid4().int)[:23], "reason_code": "1310", "notes": "TEST_MEMO"},
    },
}
r = httpx.post(B + "/v2/customer-support/process-chargeback", headers=H, json=body, timeout=60)
print("chargeback", r.status_code, r.text[:1500])
for i in range(6):
    time.sleep(10)
    d = httpx.get(B + "/v1/customer/disputes", headers=H, timeout=30)
    items = d.json().get("items", [])
    print("list", d.status_code, len(items), json.dumps(items)[:800])
    if items:
        did = items[0]["dispute_id"]
        s = httpx.get(B + f"/v1/customer/disputes/{did}", headers=H, timeout=30)
        print("show", s.status_code, s.text[:3000])
        break
