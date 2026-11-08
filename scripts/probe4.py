"""Probe: what seller actions does a sandbox chargeback dispute accept? Usage: probe4.py DISPUTE_ID [action]"""
import json
import os
import sys

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
did = sys.argv[1]
action = sys.argv[2] if len(sys.argv) > 2 else "show"


def show():
    s = httpx.get(B + f"/v1/customer/disputes/{did}", headers=H, timeout=30).json()
    print(s.get("status"), s.get("dispute_state"), [l["rel"] for l in s.get("links", [])])
    return s


show()
if action == "require":
    r = httpx.post(B + f"/v1/customer/disputes/{did}/require-evidence", headers=H, json={"action": "SELLER_EVIDENCE"}, timeout=60)
    print("require-evidence", r.status_code, r.text[:600])
elif action == "evidence":
    files = {"input": (None, json.dumps({"evidences": [{"evidence_type": "PROOF_OF_FULFILLMENT", "evidence_info": {"tracking_info": [{"carrier_name": "UPS", "tracking_number": "1Z999AA10123456784"}]}, "notes": "Delivered."}]}), "application/json")}
    r = httpx.post(B + f"/v1/customer/disputes/{did}/provide-evidence", headers={"Authorization": H["Authorization"]}, files=files, timeout=60)
    print("provide-evidence", r.status_code, r.text[:600])
elif action == "info":
    files = {"input": (None, json.dumps({"notes": "Tracking 1Z999AA10123456784 shows delivered."}), "application/json")}
    r = httpx.post(B + f"/v1/customer/disputes/{did}/provide-supporting-info", headers={"Authorization": H["Authorization"]}, files=files, timeout=60)
    print("supporting-info", r.status_code, r.text[:600])
elif action == "accept":
    r = httpx.post(B + f"/v1/customer/disputes/{did}/accept-claim", headers=H, json={"note": "Accepting claim.", "accept_claim_type": "REFUND"}, timeout=60)
    print("accept-claim", r.status_code, r.text[:600])
elif action == "offer":
    r = httpx.post(B + f"/v1/customer/disputes/{did}/make-offer", headers=H, json={"note": "Partial refund offered.", "offer_amount": {"currency_code": "USD", "value": "10.00"}, "offer_type": "REFUND"}, timeout=60)
    print("make-offer", r.status_code, r.text[:600])
show()
