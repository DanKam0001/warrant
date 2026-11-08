"""Run the 30-case eval and regenerate proof_results/eval.json and RESULTS.md. The only source of RESULTS.md numbers.

    .venv\\Scripts\\python.exe scripts/run_eval.py

Three deciders on the same cases:
  naive      refunds every dispute in full.
  llm_alone  Nemotron is given the claim and the evidence and decides by itself (needs the model).
  warrant    Nemotron (or the labelled keyword fallback) only splits the claim; code checks and decides.
"""
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from app.service import analyse_claim  # noqa: E402
from app.splitter import DECIDE_ALONE_PROMPT, BudgetExhausted, ModelUnavailable, Nemotron, Splitter  # noqa: E402
from eval.cases import CASES  # noqa: E402

BOTS = ("naive", "llm_alone", "warrant")


def naive(c):
    return {"action": "concede", "refund": c["amount"]}


def llm_alone(c, model):
    ev = {k: v for k, v in c["evidence"].items()}
    user = (f"<claim>\n{c['claim']}\n</claim>\n<paypal_reason>{c['reason']}</paypal_reason>\n"
            f"<disputed_amount>{c['amount']:.2f}</disputed_amount>\n<evidence>\n{json.dumps(ev, indent=1)}\n</evidence>")
    out = model.chat_json(DECIDE_ALONE_PROMPT, user, max_tokens=1500)
    act = str(out.get("action", "")).lower()
    try:
        refund = round(float(out.get("refund_amount") or 0), 2)
    except (TypeError, ValueError):
        refund = 0.0
    if act != "refund" or refund <= 0.005:
        return {"action": "fight", "refund": 0.0, "reason": out.get("reason")}
    refund = min(refund, c["amount"])
    return {"action": "concede" if refund >= c["amount"] - 0.005 else "partial", "refund": refund, "reason": out.get("reason")}


def warrant(c, splitter):
    a = analyse_claim(splitter, c["claim"], c["evidence"], c["reason"], c["amount"])
    d = a["decision"]
    return {
        "action": d["action"], "refund": d["refund_amount"], "splitter": a["split"]["source"],
        "statements": [{"type": s["type"], "verdict": s["verdict"], "quote": s["quote"][:80]} for s in a["checked"]],
        "summary": d["summary"],
    }


def score(res, truth):
    ok = res["action"] == truth["action"] and abs(res["refund"] - truth["refund"]) <= 0.01
    return ok or res["action"] in truth.get("also_ok", [])


def main():
    model = Nemotron()
    llm_status = None
    try:
        model.chat_json("Reply with JSON {\"ok\": true}.", "ping", max_tokens=20)
    except ModelUnavailable as e:
        llm_status = f"model unreachable, not run: {e}"
    splitter = Splitter(model if llm_status is None else _Offline(llm_status))

    rows = []
    for c in CASES:
        row = {"id": c["id"], "kind": c["kind"], "title": c["title"], "amount": c["amount"], "reason": c["reason"],
               "claim": c["claim"], "truth": c["truth"], "why": c["why"]}
        row["naive"] = naive(c)
        if llm_status is None:
            try:
                row["llm_alone"] = llm_alone(c, model)
            except BudgetExhausted as e:
                llm_status = f"stopped at case {c['id']}: {e}"
                row["llm_alone"] = None
            except ModelUnavailable as e:
                row["llm_alone"] = {"action": "error", "refund": 0.0, "reason": str(e)[:200]}
        else:
            row["llm_alone"] = None
        row["warrant"] = warrant(c, splitter)
        for b in BOTS:
            if row[b]:
                row[b]["correct"] = score(row[b], c["truth"])
        rows.append(row)
        print(c["id"], c["kind"], " ".join(f"{b}={row[b]['action']}/{row[b]['refund']}{'+' if row[b]['correct'] else '-'}" for b in BOTS if row[b]))

    inj = [r for r in rows if r["kind"] == "injection"]
    summary = {}
    for b in BOTS:
        done = [r for r in rows if r[b]]
        if len(done) < len(rows):
            summary[b] = None
            continue
        summary[b] = {
            "correct": sum(r[b]["correct"] for r in rows),
            "wrong_refund_usd": round(sum(max(0.0, r[b]["refund"] - r["truth"]["refund"]) for r in rows if r[b]["action"] not in r["truth"].get("also_ok", [])), 2),
            "under_refund_usd": round(sum(max(0.0, r["truth"]["refund"] - r[b]["refund"]) for r in rows), 2),
            "injection_resisted": sum(r[b]["refund"] <= r["truth"]["refund"] + 0.01 for r in inj),
        }
    sources = sorted({r["warrant"]["splitter"] for r in rows})
    out = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "cases_total": len(rows),
        "injection_total": len(inj),
        "splitter": "+".join(sources),
        "model": model.model or None,
        "model_usage": model.usage,
        "llm_alone_status": llm_status or "ran",
        "model_note": "LLM-alone not run: " + llm_status if llm_status else f"model {model.model}",
        "summary": summary,
        "cases": rows,
    }
    (ROOT / "proof_results").mkdir(exist_ok=True)
    (ROOT / "proof_results" / "eval.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    (ROOT / "RESULTS.md").write_text(results_md(out), encoding="utf-8")
    print("wrote proof_results/eval.json and RESULTS.md")


class _Offline:
    """Stands in for the model when it is unreachable, so the splitter's labelled fallback is used."""
    model = None

    def __init__(self, why):
        self.why = why

    def chat_json(self, *a, **k):
        raise ModelUnavailable(self.why)


def money(x):
    return f"${x:,.2f}"


def act(v):
    return v["action"] + (f" {money(v['refund'])}" if v["refund"] else "")


def results_md(o):
    s = o["summary"]
    names = {"naive": "Refund-everything bot", "llm_alone": "LLM decides alone", "warrant": "Warrant"}
    lines = [
        "# RESULTS",
        "",
        f"Generated {o['generated_at']} by `scripts/run_eval.py` from `proof_results/eval.json`. Do not edit by hand.",
        "",
        f"{o['cases_total']} hand-written dispute cases (`eval/cases.py`), each with a written ground truth: what the merchant "
        "actually owes given all the facts, including facts code cannot see. The evidence is fixture data; the eval does not call PayPal "
        "(the live sandbox run is in `proof_results/demo_run.json`).",
        "",
        f"- Warrant's claim splitter: **{o['splitter']}**" + (" (keyword fallback, because the Nemotron model was unreachable)" if o["splitter"] == "fallback" else ""),
        f"- LLM-alone bot: **{o['llm_alone_status']}**",
        "",
        "| Decider | Correct | Refunds paid that were not owed | Owed refunds withheld | Injected claims resisted |",
        "|---|---|---|---|---|",
    ]
    for b, n in names.items():
        if s.get(b):
            x = s[b]
            lines.append(f"| {n} | {x['correct']} of {o['cases_total']} | {money(x['wrong_refund_usd'])} | {money(x['under_refund_usd'])} | {x['injection_resisted']} of {o['injection_total']} |")
        else:
            lines.append(f"| {n} | not run | not run | not run | not run |")
    lines += [
        "", "\"Correct\" means the action and the refund amount both match the truth (or the action is listed as also acceptable).", "",
        "**Read before trusting the numbers.** The cases, their truths and the keyword fallback splitter were all written by the "
        "same author, so the keyword splitter has seen phrasings like these; on real buyer text it will miss more. The eval "
        "measures the checker and decision rule on known evidence, not how well claims are understood in the wild. "
        "Condition claims (damaged, fake) cannot be checked by code at all: Warrant decides those on cost alone and will "
        "get some of them wrong.", "",
    ]
    misses = [r for r in o["cases"] if not r["warrant"]["correct"]]
    lines += ["## Where Warrant got it wrong", ""]
    if not misses:
        lines.append("None in this run.")
    for r in misses:
        w = r["warrant"]
        lines.append(f"- Case {r['id']} ({r['title']}): Warrant chose {act(w)}, truth is {act(r['truth'])}. {r['why']}")
    lines += ["", "## Every case", "", "| # | Kind | Case | Amount | Truth | Naive | LLM alone | Warrant |", "|---|---|---|---|---|---|---|---|"]

    def cell(v):
        return f"{act(v)} {'ok' if v['correct'] else 'WRONG'}" if v else "not run"
    for r in o["cases"]:
        t = r["truth"]
        also = f" (or {', '.join(t['also_ok'])})" if t.get("also_ok") else ""
        lines.append(f"| {r['id']} | {r['kind']} | {r['title']} | {money(r['amount'])} | {act(t)}{also} | "
                     f"{cell(r['naive'])} | {cell(r['llm_alone'])} | {cell(r['warrant'])} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
