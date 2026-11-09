"""Test A, code layer: run app.injection over every filtered CFPB narrative and list every flagged sentence.

Ground truth here is simple: a real consumer complaint is not an attack on a decision engine, so every sentence the
classifier marks as an injection (rule I1-I4) is counted as a false positive. A reviewer can read every one in the
output (the full list is written, not a sample). Sentences marked N1-N5 are the intended non-injection kinds.
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import injection as inj  # noqa: E402


def load_jsonl(path) -> list[dict]:
    return [json.loads(line) for line in open(path, encoding="utf8") if line.strip()]


def scan(rows: list[dict]) -> dict:
    sentences = 0
    flagged: list[dict] = []
    rule_counts: collections.Counter = collections.Counter()
    for r in rows:
        for s in inj.classify(r["narrative"]):
            sentences += 1
            for m in s["marks"]:
                rule_counts[m["rule"]] += 1
            if s["injection"]:
                flagged.append({
                    "complaint_id": r["complaint_id"], "rules": s["rules"], "sentence": s["text"].strip(),
                    "phrases": [m["phrase"] for m in s["marks"] if m["rule"].startswith("I")],
                })
    ids = {f["complaint_id"] for f in flagged}
    return {
        "narratives": len(rows), "sentences": sentences, "flagged_sentences": len(flagged), "flagged_narratives": len(ids),
        "fp_rate_narratives": round(len(ids) / max(1, len(rows)), 5), "rule_counts": dict(rule_counts),
        "flagged": flagged,
    }


def phrase_counts(flagged: list[dict], top: int = 25) -> list[list]:
    c: collections.Counter = collections.Counter()
    for f in flagged:
        for rule, ph in zip([x for x in f["rules"] if x.startswith("I")], f["phrases"]):
            c[(rule, ph.lower()[:40])] += 1
    return [[k[0], k[1], v] for k, v in c.most_common(top)]


if __name__ == "__main__":
    src, out = sys.argv[1], sys.argv[2]
    res = scan(load_jsonl(src))
    res["top_phrases"] = phrase_counts(res["flagged"])
    Path(out).write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf8")
    print({k: v for k, v in res.items() if k not in ("flagged",)})
