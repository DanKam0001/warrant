"""Probe the concede path on a fresh sandbox chargeback: accept-claim, then refund+supporting-info as fallback."""
import sys
import time

from dotenv import load_dotenv

sys.path.insert(0, ".")
load_dotenv(".env")
from app.paypal import PayPal, PayPalError  # noqa: E402

pp = PayPal()
o = pp.create_paid_order("19.00", "probe-concede", "Probe concede")
print(o)
t = pp.add_tracking(o["order_id"], o["capture_id"], "USPS", "9400100000000000000001")
print("tracker", t.get("id"))
claim = 'Got a blue mug, I ordered the green one. "Quotes" & <tags> survive?'
did = pp.open_sandbox_chargeback(o["capture_id"], "19.00", "not_as_described", claim)
print("dispute", did)
for _ in range(12):
    time.sleep(10)
    try:
        d = pp.show_dispute(did)
        break
    except PayPalError as e:
        print("not yet", e.status)
print(d.get("status"), d.get("reason"), [l["rel"] for l in d["links"]], [e.get("notes") for e in d.get("evidences", [])])
for name, fn in [("make_offer", lambda: pp.make_offer(did, "5.00", "Partial refund for wrong colour.")),
                 ("accept_claim", lambda: pp.accept_claim(did, "We sent the wrong colour."))]:
    try:
        print(name, fn())
    except PayPalError as e:
        print(name, "FAILED", e.status, e.body[:300])
try:
    print("refund", pp.refund(o["capture_id"], "19.00", "Refund for wrong item"))
except PayPalError as e:
    print("refund FAILED", e.status, e.body[:300])
d = pp.show_dispute(did)
print(d.get("status"), d.get("dispute_state"), [l["rel"] for l in d["links"]])
