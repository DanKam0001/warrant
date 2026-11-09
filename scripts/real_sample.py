"""Fixed-seed sample of the filtered CFPB narratives (run with python -I).

Input: the filtered jsonl (see eval/real/SOURCES.md for the filter). Output: eval/real/cfpb_sample.jsonl (300 rows) and
eval/real/label_pool.json (ids of the first 90 sampled rows whose narrative is 250-1500 characters, in seed order).
The sample is fixed BEFORE any narrative is read for labelling.
"""
import json, random, sys

SEED = 20261109
SRC = sys.argv[1] if len(sys.argv) > 1 else "D:/Hackathon_Machine/paypal-ai-hackathon/paypal-2/cfpb_filtered.jsonl"
rows = [json.loads(l) for l in open(SRC, encoding="utf8")]
rows.sort(key=lambda r: int(r["complaint_id"]))
rnd = random.Random(SEED)
sample = rnd.sample(rows, 300)
with open("eval/real/cfpb_sample.jsonl", "w", encoding="utf8") as o:
    for r in sample:
        o.write(json.dumps(r, ensure_ascii=False) + "\n")
pool = [r["complaint_id"] for r in sample if 250 <= len(r["narrative"]) <= 1500][:90]
json.dump({"seed": SEED, "ids": pool}, open("eval/real/label_pool.json", "w"), indent=1)
print(len(rows), len(sample), len(pool), sum(len(r["narrative"]) for r in sample) // 300)
