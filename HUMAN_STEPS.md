# Human steps (optional)

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
