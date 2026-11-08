# Human steps

## Record the video (needed; the owner accepts about 4 minutes)

Start the app (`.venv\Scripts\python.exe -m uvicorn app.main:app --port 8765`) and open http://127.0.0.1:8765.
Press **Reset demo** about two minutes before recording, so PayPal has listed the four disputes and offers
accept-claim. The figures below come from `proof_results/demo_run.json` and `proof_results/eval.json`. If a fresh run
shows different ones, say what is on screen.

1. (0:00-0:20) Who it is for: a small shop's operations person who answers PayPal disputes by hand. Each lost one
   costs the goods plus a fee, and each wrongly fought one costs time.
2. (0:20-0:60) Open KK-2041, "I never received my package". Press **Check claim**. Both statements turn green, each
   with a line to the tracker registered with PayPal and the delivery scan. The scan is a fixture, so say so. Then the
   cost table appears: fight $23.40 against concede $64.00. Press **Send answer to PayPal** and show the dispute ID
   and the status after.
3. (1:00-1:25) Open KK-2042, wrong item. The statement turns red: the order was for sage mugs and the shipment record shows blue ones went
   out, so the buyer is right. Warrant
   concedes through accept-claim. Say that PayPal refused a plain refund on a chargeback.
4. (1:25-1:50) Open KK-2043, the injected claim. The sentence telling the reviewer to approve a full refund is struck through and
   ignored. The
   real statement is checked and fought.
5. (1:50-2:30) Open the **Policy** tab: the consumer-protection floor box, then the merchant's settings. Say that
   "fight" means sending evidence to PayPal, which decides, and the buyer keeps Buyer Protection. Then the
   **Results board**, held-out set 2: Warrant 22 of 23 with one wrong partial ($28 over); the LLM deciding alone 14 of 23,
   $151.50 wrong refunds. Say plainly that set 2 was written by the same author before the logic existed.
   Also show Auto mode: turn it on, watch sure cases run, and show the per-order log. Say it is off by default.
6. (2:30-2:45) Close: the model only reads the claim, code decides, and every answer shows its evidence.

## PayPal-wallet dispute (optional)

Everything in the demo runs without a person. The steps below are only needed to test a **PayPal-wallet dispute**
opened by a buyer, rather than a card chargeback.

1. In the PayPal Developer Dashboard, open Sandbox > Accounts and note a Personal (buyer) account's email and password.
2. Create an order for that buyer to approve. Use `POST /v2/checkout/orders` with `intent: CAPTURE` and a `custom_id`
   equal to the purchase record hash. The orders in `app/scenarios.py` show the fields.
3. Open the order's `approve` link in a browser, log in as the sandbox buyer and approve. Then capture it with
   `POST /v2/checkout/orders/{id}/capture`.
4. Log in at https://www.sandbox.paypal.com as the buyer, open the payment in Activity, and choose
   "Report a problem". Write the claim in the buyer's own words.
5. Add that capture to Warrant's store (an order entry keyed by capture ID with its evidence), or start from a reset
   store. Warrant's poll loop (every 15 s) picks the dispute up from `GET /v1/customer/disputes`.

PayPal-wallet disputes offer more seller actions than chargebacks (`make-offer`, `provide-evidence`). Warrant
already uses them when PayPal's links offer them.
