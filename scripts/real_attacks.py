"""Write eval/real/injection_attacks.jsonl: the public attack/benign prompts used by scripts/run_real_eval.py.

Input is a JSON the builder made from the Hugging Face parquet files (kept outside the repo, on D:), shaped
{"hf_deepset": [{split,i,text,label}], "hf_lakera": [{split,i,text,similarity}]}.
All 662 deepset/prompt-injections rows (Apache-2.0) are kept. Lakera/gandalf_ignore_instructions (MIT) is sampled to 300
of its 1,000 rows with random.Random(20261109), so the committed file is exactly what the run used.
Run: python -I scripts/real_attacks.py <hf_all.json>
"""
import json
import random
import sys
from pathlib import Path

src = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
out = []
for r in src["hf_deepset"]:
    out.append({"source": "deepset/prompt-injections", "licence": "Apache-2.0", "split": r["split"], "i": r["i"], "label": int(r["label"]), "text": r["text"]})
lak = sorted(src["hf_lakera"], key=lambda r: (r["split"], r["i"]))
for r in random.Random(20261109).sample(lak, 300):
    out.append({"source": "Lakera/gandalf_ignore_instructions", "licence": "MIT", "split": r["split"], "i": r["i"], "label": 1, "text": r["text"]})
dest = Path(__file__).resolve().parents[1] / "eval" / "real" / "injection_attacks.jsonl"
dest.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in out) + "\n", encoding="utf-8")
print(len(out), "rows ->", dest)
