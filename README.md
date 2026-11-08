# Warrant

Warrant answers PayPal disputes for a small shop. It splits the buyer's claim into statements and checks each one
against the evidence in plain code. Then it fights, concedes or refunds part of the order, whichever costs the
merchant least. Every decision shows its sums.

## What it does

1. **Takes in the dispute.** Warrant polls the PayPal Disputes API for disputes on the shop's captures. The buyer's
   claim text is read from the dispute (`evidences[].notes`, source `SUBMITTED_BY_BUYER`).
2. **Splits the claim.** Nemotron, through Nebius Token Factory, cuts the claim into typed statements. The types
   are fixed: not received, not authorised, wrong item, wrong amount, duplicate charge, not as described,
   returned-not-refunded, and instruction. This is the model's only job. It never sees the evidence and never
   decides anything. Its output is checked in code:
   - Unknown types are dropped.
   - A statement whose quote is not in the claim is discarded.
   - Instruction-like sentences are flagged by regex even if the model misses them.
3. **Checks each statement in code** against four sources:
   - the purchase record;
   - the PayPal order, whose `custom_id` is a SHA-256 fingerprint of that purchase record, so a record edited after
     the sale no longer matches what PayPal holds;
   - the capture;
   - the tracking number registered through the PayPal tracking API, plus carrier scans, the packing record, the
     returns log and refunds.

   Each statement turns out true, false or unverifiable, and names the evidence that decided it. Instructions inside
   the claim are shown struck through and are never followed. The reason on file with PayPal is always checked as an
   extra statement, so a claim made of nothing but instructions cannot dodge the check. Purchases made by a buyer's
   AI agent are checked against the mandate the buyer signed: the spend limit, the category and the expiry.
4. **Decides by expected cost** (`app/decision.py`):
   - fight = P(lose) × (amount + dispute fee) + handling time
   - partial = the amount the merchant is at fault for + P(lose on the rest) × (rest + fee) + time
   - concede = the refund

   The fee, the time cost and the P(lose) for each evidence strength are merchant settings, shown next to every
   decision. They are assumptions, not measured rates.
5. **Answers PayPal** in the sandbox. To fight, it sends `provide-supporting-info` (or `provide-evidence` when PayPal
   offers it), listing each statement, its verdict and the evidence. To concede, it uses `accept-claim`
   (`PARTIAL_REFUND` with an amount for a partial). On a card chargeback PayPal refuses a plain Payments refund
   (`REFUND_NOT_PERMITTED_DUE_TO_CHARGEBACK`). Warrant remembers that, and waits until PayPal offers accept-claim.

The one-page UI (`/`) shows:
- the queue;
- the claim, with statements turning green or red;
- lines from each statement to its evidence;
- the cost table;
- the PayPal dispute ID and status;
- an activity log;
- a box to try your own claim against a demo order;
- the results board from the eval (AG Grid).

## Run it

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env        (fill in sandbox PayPal and Nebius values)
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8765
```

Open http://127.0.0.1:8765. The **Reset demo** button (`POST /api/reset`) clears the store and creates four real
sandbox orders. Each is paid by test card, has tracking registered with PayPal, and has a chargeback opened on it.
PayPal takes a minute or two to list new disputes.

- Scripted demo: `.venv\Scripts\python.exe scripts\demo_run.py --reset`. It runs reset, analyse and respond on all
  four cases (one fought, one conceded, one injected claim ignored and fought, one cheap order refunded early), then
  writes `proof_results/demo_run.json`.
- Eval: `.venv\Scripts\python.exe scripts\run_eval.py` regenerates `proof_results/eval.json` and `RESULTS.md`.
- Tests: `.venv\Scripts\python.exe -m pytest -q` covers the checker, the decision rule, the injection cases and the
  PayPal response wiring against a fake PayPal.

## AI used

- **Nemotron on Nebius Token Factory**: OpenAI-compatible chat call, temperature 0, no streaming. It only splits the
  claim. The model is picked from the endpoint's model list, or set with `NEBIUS_MODEL`.
- When the model cannot be reached, a keyword splitter takes its place. Every result says which one ran
  (`splitter: model` or `splitter: fallback`).
- The "LLM decides alone" baseline in the eval gives the same model the claim and the evidence, and lets it choose.

## Honest limits

- **The model did not run in the recorded results.** The Nebius endpoint refused connections every time it was tried
  while these results were made. The saved demo run and eval used the keyword fallback splitter, and the LLM-alone
  baseline was not run. `RESULTS.md` and the proof files say so. With the model up, rerun both scripts.
- **The buyer is simulated.** Disputes are real sandbox disputes on real sandbox orders, but they are opened by
  Warrant's own seeding script through PayPal's sandbox chargeback simulator
  (`/v2/customer-support/process-chargeback`). That script stands in for the buyer's bank. PayPal-wallet disputes
  need a buyer to log in and approve in a browser (see `HUMAN_STEPS.md`).
- **Carrier scans, packing records and the returns log are fixtures.** Tracking numbers are registered with PayPal for
  real, but no carrier API is called.
- **The eval is small and hand-made.** It has 30 cases. The truths and the keyword splitter were written by the same
  author, so the fallback has seen phrasings like these. The eval tests the checker and the decision rule on known
  evidence, not how well real claims are understood. Its evidence is fixture data, and it does not call PayPal.
- **Code cannot see the goods.** "Arrived damaged" and "it's fake" come out unverifiable, and Warrant decides them on
  cost alone, so it gets some wrong. RESULTS.md lists every miss.
- **Webhooks are wired up but not used.** The endpoint `/api/webhooks/paypal` verifies signatures with PayPal, but no
  public URL existed locally, so intake is by polling.
- The sandbox seller actions are gated by PayPal's own links. Some (`make-offer`, `require-evidence`) were never
  offered on chargebacks during testing.

## Proof

- `proof_results/demo_run.json`: the live sandbox run. It has the PayPal order, capture, tracker and dispute IDs,
  the statements with their verdicts and evidence, the decision with its formulas, the PayPal calls made, and the
  status PayPal reported afterwards.
- `proof_results/eval.json`, and `RESULTS.md` generated from it: the eval.

## License

MIT, see `LICENSE`.
