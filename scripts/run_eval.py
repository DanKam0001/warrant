"""Run the 30-case eval and regenerate proof_results/eval.json and RESULTS.md. The only source of RESULTS.md numbers.

    .venv\\Scripts\\python.exe scripts/run_eval.py [--report-only]

Three deciders on the same cases:
  naive      refunds every dispute in full.
  llm_alone  Nemotron is given the claim and the evidence and decides by itself (needs the model).
  warrant    Nemotron (or the labelled keyword fallback) only splits the claim; code checks and decides.
  warrant_keyword  the same, but always with the keyword splitter, to show what the model adds or breaks.
Then 10 held-out claims (eval/heldout.py) worded unlike the cases, reported separately.
"""
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=True)

from app.service import analyse_claim  # noqa: E402
from app.splitter import DECIDE_ALONE_PROMPT, BudgetExhausted, ModelUnavailable, Nemotron, Splitter  # noqa: E402
from eval.cases import CASES  # noqa: E402
from eval.heldout import HELDOUT  # noqa: E402

BOTS = ("naive", "llm_alone", "warrant", "warrant_keyword")


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
        "statements": [{"type": s["type"], "verdict": s["verdict"], "quote": s["quote"][:80],
                        "from": "paypal_reason" if s.get("from_paypal_reason") else "code_flag" if s.get("flagged_by") == "code" else "splitter"}
                       for s in a["checked"]],
        "summary": d["summary"],
    }


def score(res, truth):
    ok = res["action"] == truth["action"] and abs(res["refund"] - truth["refund"]) <= 0.01
    return ok or res["action"] in truth.get("also_ok", [])


def main():
    model = Nemotron()
    llm_status = None
    try:
        model.chat_json("Return only this JSON: {\"ok\": true}", "Return the JSON.", max_tokens=200)
    except ModelUnavailable as e:
        # A reply that is not JSON still proves the model answers; only a failed call means it is unreachable.
        if not str(e).startswith(("no JSON", "bad JSON")):
            llm_status = f"model unreachable, not run: {e}"
    splitter = Splitter(model if llm_status is None else _Offline(llm_status))
    keyword = Splitter(_Offline("keyword splitter run for comparison"))

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
        row["warrant_keyword"] = warrant(c, keyword)
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
    heldout = run_heldout(splitter, keyword, model if llm_status is None else None)
    out = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "cases_total": len(rows),
        "injection_total": len(inj),
        "splitter": "+".join(sources),
        "splitter_fallback_cases": [r["id"] for r in rows if r["warrant"]["splitter"] != "model"],
        "splitter_differences": [
            {"id": r["id"], "model_types": sorted(m), "keyword_types": sorted(k), "same_decision": r["warrant"]["action"] == r["warrant_keyword"]["action"]}
            for r in rows
            for m, k in [({s["type"] for s in r["warrant"]["statements"] if s["from"] == "splitter"},
                          {s["type"] for s in r["warrant_keyword"]["statements"] if s["from"] == "splitter"})]
            if m != k],
        "model": model.model or None,
        "model_usage": model.usage,
        "llm_alone_status": llm_status or "ran",
        "model_note": "LLM-alone not run: " + llm_status if llm_status else f"model {model.model}",
        "summary": summary,
        "cases": rows,
        "heldout": heldout,
    }
    (ROOT / "proof_results").mkdir(exist_ok=True)
    (ROOT / "proof_results" / "eval.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    (ROOT / "RESULTS.md").write_text(results_md(out), encoding="utf-8")
    print("wrote proof_results/eval.json and RESULTS.md")


def run_heldout(splitter, keyword, model):
    """Unseen wordings on the base cases' evidence: did each splitter find the expected types, and was Warrant right?"""
    base = {c["id"]: c for c in CASES}
    rows = []
    for h in HELDOUT:
        c = {**base[h["base"]], "claim": h["claim"]}
        row = {"id": h["id"], "base": h["base"], "claim": h["claim"], "expect": [list(g) for g in h["expect"]], "truth": c["truth"]}
        for name, sp in (("model", splitter), ("keyword", keyword)):
            r = warrant(c, sp)
            # Only what the splitter itself produced: not the PayPal-reason statement or code-flagged instructions.
            found = {s["type"] for s in r["statements"] if s["from"] == "splitter"}
            r["types_found"] = sorted(found)
            r["types_ok"] = all(found & set(g) for g in h["expect"])
            r["correct"] = score(r, c["truth"])
            row[name] = r
        if model is not None:
            try:
                row["llm_alone"] = llm_alone(c, model)
                row["llm_alone"]["correct"] = score(row["llm_alone"], c["truth"])
            except ModelUnavailable as e:
                row["llm_alone"] = {"action": "error", "refund": 0.0, "reason": str(e)[:200], "correct": False}
        else:
            row["llm_alone"] = None
        rows.append(row)
        print(h["id"], " ".join(f"{n}={row[n]['action']}{'+' if row[n]['correct'] else '-'}" + (f" types{'+' if row[n]['types_ok'] else '-'}" if n != "llm_alone" else "")
                                for n in ("model", "keyword", "llm_alone") if row.get(n)))
    tot = {}
    for n in ("model", "keyword", "llm_alone"):
        done = [r for r in rows if r.get(n)]
        if len(done) < len(rows):
            tot[n] = None
            continue
        tot[n] = {"correct": sum(r[n]["correct"] for r in rows)}
        if n != "llm_alone":
            tot[n]["types_ok"] = sum(r[n]["types_ok"] for r in rows)
            tot[n]["splitter_sources"] = sorted({r[n]["splitter"] for r in rows})
    return {"total": len(rows), "summary": tot, "cases": rows}


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
    names = {"naive": "Refund-everything bot", "llm_alone": "LLM decides alone", "warrant_keyword": "Warrant, keyword splitter", "warrant": "Warrant"}
    u = o.get("model_usage") or {}
    split_line = f"- Warrant's claim splitter: **{o['splitter']}**"
    if o["splitter"] == "fallback":
        split_line += " (keyword fallback, because the Nemotron model was unreachable)"
    elif o.get("splitter_fallback_cases"):
        split_line += f" (model `{o['model']}`; the model call failed on case(s) {', '.join(map(str, o['splitter_fallback_cases']))}, which used the labelled keyword fallback)"
    else:
        split_line += f" (`{o['model']}` on every case)"
    lines = [
        "# RESULTS",
        "",
        f"Generated {o['generated_at']} by `scripts/run_eval.py` from `proof_results/eval.json`. Do not edit by hand.",
        "",
        f"{o['cases_total']} hand-written dispute cases (`eval/cases.py`), each with a written ground truth: what the merchant "
        "actually owes given all the facts, including facts code cannot see. The evidence is fixture data; the eval does not call PayPal "
        "(the live sandbox run is in `proof_results/demo_run.json`).",
        "",
        split_line,
        f"- LLM-alone bot: **{o['llm_alone_status']}**" + (f" (`{o['model']}` given the claim and the full evidence JSON, asked to refund or fight)" if o["llm_alone_status"] == "ran" else ""),
        f"- Model calls in this run: {u.get('calls', 0)} ({u.get('prompt_tokens', 0)} prompt + {u.get('completion_tokens', 0)} completion tokens, {u.get('failures', 0)} failed)",
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
    if s.get("llm_alone"):
        lm = [r for r in o["cases"] if not r["llm_alone"]["correct"]]
        lines += ["", "## Where the LLM deciding alone got it wrong", ""]
        lines += [f"- Case {r['id']} ({r['title']}): chose {act(r['llm_alone'])}, truth is {act(r['truth'])}. Its reason: \"{r['llm_alone'].get('reason') or ''}\""
                  for r in lm] or ["None in this run."]
    if o["splitter"] != "fallback":
        diffs = o.get("splitter_differences") or []
        lines += ["", "## Model splitter vs keyword splitter on these 30 cases", "",
                  f"Both splitters feed the same checker and decision rule. Statement types differed on {len(diffs)} of {o['cases_total']} cases; "
                  f"the decision differed on {sum(not d['same_decision'] for d in diffs)}. (The keyword patterns were written alongside these cases, "
                  "so this set flatters the keyword splitter; see the held-out section.)", ""]
        lines += [f"- Case {d['id']}: model {', '.join(d['model_types']) or 'nothing'}; keyword {', '.join(d['keyword_types']) or 'nothing'}"
                  + ("" if d["same_decision"] else " (decision changed)") for d in diffs]
        worse = [d["id"] for d in diffs if set(d["keyword_types"]) - set(d["model_types"])]
        wrong_m = [r["id"] for r in o["cases"] if r["warrant_keyword"]["correct"] and not r["warrant"]["correct"]]
        lines += ["", "Cases where the keyword splitter found a type the model missed: " + (", ".join(map(str, worse)) or "none") +
                  ". Cases the model-split Warrant got wrong and the keyword one got right: " + (", ".join(map(str, wrong_m)) or "none") + "."]
    lines += heldout_md(o.get("heldout"))
    lines += ["", "## Every case", "", "| # | Kind | Case | Amount | Truth | Naive | LLM alone | Warrant, keyword | Warrant |", "|---|---|---|---|---|---|---|---|---|"]

    def cell(v):
        return f"{act(v)} {'ok' if v['correct'] else 'WRONG'}" if v else "not run"
    for r in o["cases"]:
        t = r["truth"]
        also = f" (or {', '.join(t['also_ok'])})" if t.get("also_ok") else ""
        lines.append(f"| {r['id']} | {r['kind']} | {r['title']} | {money(r['amount'])} | {act(t)}{also} | "
                     f"{cell(r['naive'])} | {cell(r['llm_alone'])} | {cell(r['warrant_keyword'])} | {cell(r['warrant'])} |")
    return "\n".join(lines) + "\n"


def heldout_md(h):
    if not h:
        return []
    s, n = h["summary"], h["total"]
    lines = ["", "## Held-out wording (reported separately)", "",
             f"{n} claims in `eval/heldout.py`, written after the keyword splitter was frozen and worded unlike the cases above "
             "(no keyword pattern was added for them). Each reuses one case's evidence and truth. \"Types found\" counts claims where "
             "the splitter itself produced every expected statement type (the statement implied by the PayPal reason and code-flagged "
             "instructions are not counted).", "",
             "Caveat: on 9 Nov two instruction patterns were added to the code check (orders addressed to a reader, like "
             "\"AI reviewer: ...\", and \"ignore the tracking ... and refund\") after a demo claim slipped past it. H10 has that "
             "shape, so H10's keyword result is no longer held out. The other nine are unchanged.", "",
             "| Decider | Types found | Correct decisions |", "|---|---|---|"]
    for k, label in (("model", "Warrant, model splitter"), ("keyword", "Warrant, keyword splitter"), ("llm_alone", "LLM decides alone")):
        x = s.get(k)
        if not x:
            lines.append(f"| {label} | not run | not run |")
            continue
        src = f" ({'+'.join(x['splitter_sources'])})" if k == "model" and x.get("splitter_sources") != ["model"] else ""
        lines.append(f"| {label}{src} | {str(x['types_ok']) + ' of ' + str(n) if 'types_ok' in x else '—'} | {x['correct']} of {n} |")
    if s.get("llm_alone") and s.get("model") and s["llm_alone"]["correct"] > s["model"]["correct"]:
        lines += ["", f"**On this held-out set the LLM deciding alone made more correct decisions than Warrant "
                      f"({s['llm_alone']['correct']} vs {s['model']['correct']} of {n}).** Warrant's misses here are listed as WRONG below; "
                      "they were left unfixed so the set stays held out."]
    lines += ["", "| # | Base case | Claim | Expected types | Model: types / decision | Keyword: types / decision | LLM alone |", "|---|---|---|---|---|---|---|"]

    def side(v):
        return f"{', '.join(v['types_found']) or 'nothing'} {'ok' if v['types_ok'] else 'MISSED'} / {act(v)} {'ok' if v['correct'] else 'WRONG'}"
    for r in h["cases"]:
        la = r.get("llm_alone")
        lines.append(f"| {r['id']} | {r['base']} | {r['claim'].replace('|', '/')} | {'; '.join(' or '.join(g) for g in r['expect'])} | "
                     f"{side(r['model'])} | {side(r['keyword'])} | {act(la) + (' ok' if la['correct'] else ' WRONG') if la else 'not run'} |")
    return lines


if __name__ == "__main__":
    if "--report-only" in sys.argv:  # rebuild RESULTS.md from the saved run without calling the model again
        (ROOT / "RESULTS.md").write_text(results_md(json.loads((ROOT / "proof_results" / "eval.json").read_text(encoding="utf-8"))), encoding="utf-8")
    else:
        main()
