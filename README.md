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
- the status strip, auto toggle, and a Policy tab;
- the queue;
- the claim, with statements turning green or red;
- lines from each statement to its evidence;
- the cost table;
- the PayPal dispute ID and status;
- an activity log;
- a box to try your own claim against a demo order;
- the results board from the eval (AG Grid).

## What "fight" means, and what Warrant never does

"Fight" means **submitting evidence to PayPal, which decides.** The buyer keeps PayPal Buyer Protection and their
chargeback rights. Warrant never refuses a refund on its own. The general consumer-protection principles it applies
(see `app/floor.py`) are not legal advice, and the jurisdiction is set in the merchant policy (default AU).

Decision layers, in order. Each rule that fires is cited in the decision trace shown in the UI:

1. **Consumer-protection floor** (`app/floor.py`). Checked first. Policy can never override it.
   - A final-sale rule cannot defeat a faulty, damaged or not-as-described claim when the merchant has no
     counter-evidence (in Australia, consumer guarantees under the Australian Consumer Law work this way).
   - A "not delivered" claim with no proof of delivery is owed.
   - An unauthorized-charge claim is never auto-fought on policy grounds.
2. **Merchant policy** (`data/policy.json`, editable in the **Policy** tab): return window in days, final-sale
   categories and SKUs, restocking fee, "refund without a fight under $X" (folded into the expected-cost rule),
   and the auto-mode limits.
3. **Evidence**, checked in code against the claim's statements, then the **expected-cost** rule.

Three outcomes, not two: **refund** (full or partial), **fight**, or **review**, which puts the case in front of a
person. Review is chosen when evidence is partial or conflicting, when a floor rule applies but the facts are
unclear, or when the amount is over an auto limit. A person can refund in full, refund a chosen amount, or fight.

**Requests versus injections.** A buyer asking for a remedy ("I would prefer a replacement", "please send a receipt")
becomes a `request` statement. It is shown, noted, and never changes the decision. A buyer retracting a claim counts
*against* the claim and is not an injection. Only text that directs the decider, the AI or the system ("approve
this", "ignore the evidence") is an `instruction`; it is shown struck through and ignored.

**Status and activity.** A strip above the queue counts disputes and dollars by status (new, needs you, refunded,
partially refunded, evidence sent and awaiting PayPal, won, lost, handled by auto). Chips filter the grid, a search
box filters by order or last action, and "needs you" sorts first. Each order shows its last action and time.
Every action is appended to `data/activity.jsonl` and shown per order.

**Auto mode** is off by default and persisted. When on, it acts only on sure decisions through the real sandbox path:
a refund inside the per-refund limit and the daily cap, or a fight on clear evidence under the fight limit. Review,
over-limit, replacement or store-credit requests, unauthorized-charge claims and anything not strongly evidenced stay
queued. Every auto action is logged with its reason. A kill switch stops it.

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
- Eval: `.venv\Scripts\python.exe scripts\run_eval.py` regenerates `proof_results/eval.json` and `RESULTS.md`
  (60 dev cases, 23 held-out set 2 cases, and the 10-claim wording set in `eval/heldout.py`). Add `--report-only` to rebuild `RESULTS.md` from
  the saved run without calling the model.
- Screenshots: `.venv\Scripts\python.exe scripts\screenshot_ui.py` (Playwright driving the installed Edge, with the
  app running; `pip install playwright` first, it is not in `requirements.txt`) saves `proof_results/ui_*.png`.
- Tests: `.venv\Scripts\python.exe -m pytest -q` (61 tests) covers the checker, the decision rule, the floor and
  policy, review and partial refunds, request-versus-injection pairs, status and the activity log, auto mode with a
  mock PayPal (including the daily cap and the kill switch), and the HTTP API.

## AI used

- **Nemotron on Nebius Token Factory**: OpenAI-compatible chat call, temperature 0, no streaming. It only splits the
  claim. The model is picked from the endpoint's model list, or set with `NEBIUS_MODEL`. The recorded runs used
  `nvidia/nemotron-3-super-120b-a12b`. Token counts are saved in `proof_results/eval.json` and `demo_run.json`.
- When the model cannot be reached, a keyword splitter takes its place. Every result says which one ran
  (`splitter: model` or `splitter: fallback`).
- The "LLM decides alone" baseline in the eval gives the same model the claim and the evidence, and lets it choose.

## Honest limits

- **Held-out set 2 was written by the same author, before the new logic existed.** It was committed first
  (`eval/heldout2.py`) and nothing in `app/` was tuned against it, but it is not independent. Result with the live
  model: Warrant 22 of 23 correct, with one wrong partial refund ($56 instead of the $28 owed, $28 over); the keyword
  splitter 23 of 23; the LLM deciding alone 14 of 23, $151.50 of wrong refunds, $22 of owed refunds withheld and
  $110 of owed refunds sent to review. Refund-everything: 9 of 23.
- **Dev set (60 cases) is tuned against.** Warrant 58 of 60 with the model, 60 of 60 with the keyword splitter, LLM
  alone 38 of 60, refund-everything 22 of 60. Warrant's two misses send a faulty return and a missing-item case to
  review instead of paying ($95 owed, held for a person). Dev case 16's truth was changed to "review" after I saw a
  result, because under the three-outcome model nobody can tell what was packed; it is marked in `eval/cases.py`.
- **The model splitter missed the same kinds of wording as before.** On the separate 10-claim wording set, the model
  found the right statement types on 9 of 10 and made 9 correct decisions; the keyword splitter found the types on
  1 of 10 but still made 9 correct decisions (the reason on file covers it); the LLM alone made 7.
- **The LLM-alone baseline is not repeatable.** At temperature 0 it gives different answers between runs. The saved
  run is the only one reported for this version of the eval (one live run, no cherry-picking).
- **Auto mode was run against the real PayPal sandbox once** (`proof_results/auto_sandbox_run.json`): three sure
  cases ran, one hit a transient 503 from PayPal and was logged as a failed auto action, and a retry succeeded.
  Auto mode has not been run against live money or a real merchant.
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
- `proof_results/auto_sandbox_run.json`: the activity log of an auto-mode pass against the sandbox.
- `proof_results/ui_case.png`, `ui_policy.png`, `ui_board.png`, `ui_phone.png`: the UI in a real browser.
- `proof_results/demo_run.json` predates the policy, review and auto work.

## License

MIT, see `LICENSE`.
