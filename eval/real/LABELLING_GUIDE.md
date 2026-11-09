# Labelling guide for the real-complaint test (Test B)

Written BEFORE any narrative was labelled, and not changed after labelling started (the git history shows the order).

## What is being labelled

Each item is one real consumer complaint narrative from the CFPB Consumer Complaint Database (credit-card purchase
disputes). The label is the claim kind or kinds the consumer ACTUALLY MAKES in their own words. It is not what the
company did, not what the CFPB recorded, and not whether the consumer is right.

## Claim kinds (one or more per narrative)

| label | the consumer says | typical wording |
|---|---|---|
| not_received | goods or service never arrived or was never provided | "never received", "never showed up", "did not deliver", "service was never rendered" |
| not_as_described | arrived but damaged, defective, fake, or not what was advertised | "broken", "defective", "not as described", "counterfeit", "did not work" |
| wrong_item | a different item, size, colour or model arrived than ordered | "wrong item", "sent me X instead of Y" |
| unauthorised | the consumer did not make or approve the charge | "I did not make this purchase", "fraudulent charge", "unauthorized" |
| duplicate | charged more than once for one purchase | "charged twice", "double billed" |
| amount_wrong | the amount charged is not the amount agreed or advertised | "charged $X instead of $Y", "overcharged" |
| refund_not_given | returned or cancelled and was promised or is owed a refund that did not come | "returned it", "cancelled", "was told I would get a refund and never did" |
| other | none of the above is clearly claimed (a billing-dispute process complaint, a subscription they forgot, a change of mind, unclear) |

Rules:

1. Label what is claimed, not what is implied. If the text does not say it, do not label it.
2. A narrative may carry several kinds; list all that are clearly claimed. Maximum three. If more than three, keep the
   three most central.
3. A narrative about the bank's handling of the dispute (slow, rude, lost documents) with no claim about the purchase
   itself is `other`.
4. A change of mind ("I decided I did not want it") is `other` (the warrant pipeline calls it change_of_mind; it is never
   a held claim).
5. If after one careful read the claim is genuinely unclear, label `other` and add the id to `unclear`. Unclear items stay
   in the table and are reported separately; they are not removed.
6. The narrative text has the CFPB redaction token `XXXX` for names, dates and amounts. Keep it as is.

## Evidence states (constructed, not real)

CFPB narratives come with no merchant evidence. For each labelled narrative the harness builds TWO evidence states for a
fictional order whose amount is taken from nothing in the narrative:

- SUPPORTING: the evidence agrees with the consumer's claim kind (for example, for not_received the carrier shows the
  parcel returned to sender; for not_as_described a shop record that the item was shipped damaged; for unauthorised a
  login from a new device; and so on).
- CONTRADICTING: the evidence disagrees (for example, delivered with a signature and a matching postcode; an intact
  packing photo; a login from the buyer's usual device after a two-factor confirmation).

The EXPECTED OUTCOME is derived by the same rules the product states (a held claim means the merchant is at fault and
should refund; a contradicted claim means fight; a claim that cannot be checked in code goes to review), from the label
and the evidence state, not from what any decider output. The mapping is in `eval/real/test_b.py`
(`expected_outcome`).

## What this does and does not show

- The narratives are real; the evidence is constructed; the labels are the builder's. This tests whether the splitter
  reads real, messy human writing into the right claim kinds, and whether the downstream decision follows. It does not
  test whether Warrant would have won real disputes.
- The CFPB `company_response` field is reported as descriptive context only. It is a response category, not ground truth
  about who was right.
- Unlabelled narratives are never used to make the labelled set look better; the sample is fixed by seed before labelling.
