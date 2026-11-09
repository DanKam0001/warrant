"""Run the photo cases (dev + held-out) through naive / vl_alone / warrant and write eval/photo_results.json.

Vision observations are cached in data/photos/observations.json, so Warrant re-runs cost nothing once described.
Usage: python scripts/run_photo_eval.py [--sets dev,heldout]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
from app import photos as P  # noqa: E402
from eval import photo_eval as E  # noqa: E402
from eval.photo_cases import PHOTO_CASES  # noqa: E402
from eval.photo_heldout import PHOTO_HELDOUT  # noqa: E402

SETS = {"dev": PHOTO_CASES, "heldout": PHOTO_HELDOUT}


def main():
    want = (sys.argv[sys.argv.index("--sets") + 1] if "--sets" in sys.argv else "dev,heldout").split(",")
    v = P.Vision()
    out = {}
    for name in want:
        out[name] = {}
        for dec in ("naive", "vl_alone", "warrant"):
            rows = E.run(SETS[name], dec, v)
            out[name][dec] = {"summary": E.summary(rows), "rows": [{k: r[k] for k in ("id", "title", "truth", "got", "ok", "reason")} for r in rows]}
            print(name, dec, out[name][dec]["summary"])
    out["usage"] = v.usage
    (ROOT / "eval" / "photo_results.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(v.usage)


if __name__ == "__main__":
    main()
