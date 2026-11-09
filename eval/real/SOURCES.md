# Sources and licences for the real-data tests

Nothing here is a copy of the full datasets. Big files live outside the repo; only small, documented subsets are committed.

## CFPB Consumer Complaint Database (card purchase disputes)

- Origin: the US Consumer Financial Protection Bureau's public complaint database (US government data, public domain).
- What I actually used: the Hugging Face mirror `BEE-spoke-data/consumer-finance-complaints`, config `has-text`, licence
  CC0-1.0 on its dataset card. It carries the "Consumer complaint narrative" column and the Complaint ID. The latest
  `Date received` in it is 2024-02-09, so it is a snapshot, not today's database.
- Why not the official file: I downloaded `https://files.consumerfinance.gov/ccdb/complaints.csv.zip` (364 MB) first. In
  the copy I received there is NO narrative column (15 columns, none holding the consumer's text), so it could not be used.
  The CFPB search API returned "Access Denied" to a plain client. That is why the mirror is used, and it is why the
  snapshot ends in Feb 2024.
- Filter (exact): Product contains "credit card" (case-insensitive), Issue equals "Problem with a purchase shown on your
  statement", narrative non-empty after trimming. Result: 28,898 narratives (all unique Complaint IDs).
- Sub-issue mix of those 28,898 (descriptive only): "isn't resolving a dispute about a purchase" 19,614; "charged for
  something you did not purchase" 8,429; "overcharged for something you did purchase" 855.
- Committed subset: `cfpb_sample.jsonl`, 300 rows drawn with `random.Random(20261109)` from the filtered rows sorted by
  Complaint ID (`scripts/real_sample.py`). Narratives are untouched, redaction tokens (XXXX, {$...}) included.
  Fields: complaint_id, date_received, product, issue, sub_issue, company_response, narrative.
- `label_pool.json`: the first 90 sampled rows whose narrative is 250 to 1,500 characters, in sample order. Those 90 are
  the Test B items. The length cap is a selection choice (the splitter's output limit is 900 tokens) and it is reported
  as a bias in RESULTS.md.
- `company_response` is the bank's response category recorded by the CFPB. It is descriptive context. It is never used
  as ground truth for who was right.
- The full filtered file (about 28,898 rows) and the source parquet are NOT committed.

## deepset/prompt-injections

- Licence: Apache-2.0. 662 rows (546 train + 116 test), label 1 = injection, label 0 = benign. Mixed English and German.
- Used for: (a) embedding label-1 rows into real narratives, (b) the label-0 rows as a second false-positive check.
- Committed: `injection_attacks.jsonl` (the rows actually used, text + label + split + index) so the test is reproducible.

## Lakera/gandalf_ignore_instructions

- Licence: MIT (dataset card tag; the dataset is not gated). 1,000 rows (777 train, 111 test, 112 validation), each a
  real player prompt from the Gandalf game that tried to make a model ignore its instructions, with a similarity score.
- Used for: embedding a sample into real narratives. Committed: the rows actually used, in `injection_attacks.jsonl`.

## Attribution

Prompts belong to their respective authors and are redistributed here in small subsets for evaluation under the licences
above. No claim is made that either dataset is a measure of every prompt injection; they are public, known attacks.
