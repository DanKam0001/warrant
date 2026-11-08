"""FastAPI app: dispute queue, case analysis, PayPal responses, results board."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
# .env wins over a stale empty variable left in the shell.
load_dotenv(ROOT / ".env", override=True)

from .paypal import PayPalError  # noqa: E402
from .service import Warrant, analyse_claim  # noqa: E402

app = FastAPI(title="Warrant")
warrant = Warrant()
_busy = threading.Lock()


def _poll_loop():
    while True:
        time.sleep(int(os.environ.get("WARRANT_POLL_SECONDS", "15")))
        if not warrant.state["orders"]:
            continue
        try:
            warrant.sync()
        except Exception as e:  # noqa: BLE001
            warrant.event(f"Polling PayPal failed: {e}")


@app.on_event("startup")
def _start():
    if warrant.pp.configured and os.environ.get("WARRANT_POLL", "1") != "0":
        threading.Thread(target=_poll_loop, daemon=True).start()


@app.get("/api/health")
def health():
    return {"ok": True, "paypal_configured": warrant.pp.configured, "cases": len(warrant.state["cases"])}


@app.get("/api/state")
def state():
    s = warrant.state
    results = None
    p = ROOT / "proof_results" / "eval.json"
    if p.exists():
        results = json.loads(p.read_text(encoding="utf-8"))
    return {
        "orders": s["orders"],
        "cases": sorted(s["cases"].values(), key=lambda c: c["received"]),
        "events": s["events"][:40],
        "seeding": s.get("seeding"),
        "paypal_calls": warrant.pp.log[-30:],
        "model": {"name": warrant.splitter.model.model or None, "usage": warrant.splitter.model.usage},
        "eval": results,
    }


def _seed():
    try:
        warrant.seed_demo()
        for _ in range(12):  # chargebacks reach the disputes list after ~20-40 s
            time.sleep(10)
            warrant.sync()
            if len(warrant.state["cases"]) >= len(warrant.state["orders"]):
                break
    except Exception as e:  # noqa: BLE001
        warrant.event(f"Seeding failed: {e}")
        with warrant.lock:
            warrant.state["seeding"] = {"done": True, "error": str(e)}
            warrant.save()
    finally:
        _busy.release()


@app.post("/api/reset")
def reset(seed: bool = True):
    if not _busy.acquire(blocking=False):
        raise HTTPException(409, "seeding already running")
    warrant.reset()
    if seed and warrant.pp.configured:
        threading.Thread(target=_seed, daemon=True).start()
    else:
        _busy.release()
    return {"ok": True, "seeding": seed and warrant.pp.configured}


@app.post("/api/sync")
def sync():
    try:
        return {"new": warrant.sync()}
    except PayPalError as e:
        raise HTTPException(502, str(e)) from e


def _case(dispute_id: str):
    if dispute_id not in warrant.state["cases"]:
        raise HTTPException(404, "unknown dispute")


@app.post("/api/cases/{dispute_id}/analyse")
def analyse(dispute_id: str):
    _case(dispute_id)
    return warrant.analyse(dispute_id)


@app.post("/api/cases/{dispute_id}/respond")
def respond(dispute_id: str):
    _case(dispute_id)
    try:
        return warrant.respond(dispute_id)
    except PayPalError as e:
        raise HTTPException(502, str(e)) from e


class TryClaim(BaseModel):
    capture_id: str
    claim: str
    reason: str | None = None


@app.post("/api/try")
def try_claim(body: TryClaim):
    """Run any claim text against a stored order without touching PayPal (for 'what if the buyer said...')."""
    order = warrant.state["orders"].get(body.capture_id)
    if not order:
        raise HTTPException(404, "unknown order")
    ev = order["evidence"]
    return analyse_claim(warrant.splitter, body.claim[:4000], ev, body.reason, ev["paypal"]["captured_amount"])


@app.post("/api/webhooks/paypal")
async def webhook(request: Request):
    """CUSTOMER.DISPUTE.CREATED intake. Verified with PayPal when PAYPAL_WEBHOOK_ID is set; otherwise rejected."""
    event = await request.json()
    wid = os.environ.get("PAYPAL_WEBHOOK_ID")
    if not wid:
        raise HTTPException(400, "webhooks not configured (no PAYPAL_WEBHOOK_ID); intake uses polling")
    headers = {k.lower(): v for k, v in request.headers.items()}
    if not warrant.pp.verify_webhook(headers, event, wid):
        raise HTTPException(400, "signature verification failed")
    if event.get("event_type") == "CUSTOMER.DISPUTE.CREATED":
        did = event["resource"]["dispute_id"]
        d = warrant.pp.show_dispute(did)
        cap = next((t.get("seller_transaction_id") for t in d.get("disputed_transactions", []) if t.get("seller_transaction_id") in warrant.state["orders"]), None)
        if cap and did not in warrant.state["cases"]:
            warrant._add_case(d, cap, intake="webhook")
            warrant.save()
    return {"ok": True}


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")
