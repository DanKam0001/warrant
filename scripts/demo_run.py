"""Drive the running Warrant app through the scripted demo path against the real PayPal sandbox and save the proof.

    .venv\\Scripts\\python.exe scripts/demo_run.py [--base http://127.0.0.1:8765] [--reset]

--reset creates fresh sandbox orders + chargebacks first (takes ~1-3 min for PayPal to list them).
Writes proof_results/demo_run.json.
"""
import argparse
import datetime as dt
import json
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://127.0.0.1:8765")
ap.add_argument("--reset", action="store_true")
ap.add_argument("--wait", type=int, default=300, help="seconds to wait for disputes / response links")
args = ap.parse_args()
c = httpx.Client(base_url=args.base, timeout=180)


def state():
    return c.get("/api/state").json()


if args.reset:
    c.post("/api/reset").raise_for_status()
    t0 = time.time()
    while time.time() - t0 < args.wait:
        time.sleep(10)
        s = state()
        print(f"orders {len(s['orders'])} cases {len(s['cases'])}")
        if (s.get("seeding") or {}).get("error"):
            raise SystemExit("seeding failed: " + s["seeding"]["error"][:300])
        if s["orders"] and len(s["cases"]) >= len(s["orders"]) and (s.get("seeding") or {}).get("done"):
            break
        c.post("/api/sync")

s = state()
runs = {}
for case in s["cases"]:
    did = case["dispute_id"]
    if case["stage"] != "answered":
        a = c.post(f"/api/cases/{did}/analyse").json()
        print(did, a["split"]["source"], [(x["type"], x["verdict"]) for x in a["checked"]], a["decision"]["action"])
    runs[did] = None

t0 = time.time()
while True:
    s = state()
    todo = [x for x in s["cases"] if x["stage"] != "answered"]
    for case in todo:
        r = c.post(f"/api/cases/{case['dispute_id']}/respond")
        body = r.json() if r.status_code == 200 else {"error": r.text[:300]}
        print(case["dispute_id"], r.status_code, body.get("answered"), [(st["call"], st["ok"]) for st in body.get("steps", [])])
    s = state()
    if all(x["stage"] == "answered" for x in s["cases"]) or time.time() - t0 > args.wait:
        break
    time.sleep(20)

s = state()
out = {
    "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    "environment": "PayPal sandbox (api-m.sandbox.paypal.com)",
    "dispute_intake": "real sandbox chargebacks opened via /v2/customer-support/process-chargeback (standing in for the buyer's bank), found by polling GET /v1/customer/disputes",
    "model": s["model"],
    "cases": [],
    "paypal_calls": s["paypal_calls"],
}
for case in s["cases"]:
    order = s["orders"][case["capture_id"]]
    ev = order["evidence"]
    a = case.get("analysis") or {}
    out["cases"].append({
        "scenario": order.get("scenario"),
        "dispute_id": case["dispute_id"],
        "paypal_order_id": ev["paypal"]["order_id"],
        "capture_id": case["capture_id"],
        "custom_id_on_paypal": case.get("custom_on_dispute"),
        "record_hash": ev["record_hash"],
        "tracker_id": (ev.get("shipment") or {}).get("paypal_tracker_id"),
        "reason": case["reason"],
        "amount": case["amount"],
        "claim": case["claim"],
        "splitter": (a.get("split") or {}).get("source"),
        "splitter_model": (a.get("split") or {}).get("model"),
        "statements": [{k: x.get(k) for k in ("type", "quote", "verdict", "strength", "reason", "evidence", "flagged_by")} for x in a.get("checked", [])],
        "decision": {k: (a.get("decision") or {}).get(k) for k in ("action", "refund_amount", "summary", "options", "p_lose")},
        "response": case.get("response"),
        "refunds": ev.get("refunds"),
        "paypal_status_after": case.get("paypal_status"),
        "stage": case["stage"],
    })
(ROOT / "proof_results").mkdir(exist_ok=True)
(ROOT / "proof_results" / "demo_run.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
print("wrote proof_results/demo_run.json with", len(out["cases"]), "cases")
