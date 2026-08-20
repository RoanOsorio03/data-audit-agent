"""
IOMETE Sentinel — multi-turn follow-up suite

Re-runs the 9 cases from test_agent_accuracy.py that failed only because the
question didn't name a table, this time seeding `chat_history` with a real
prior turn ("Audite a tabela sales." + the agent's real T01 response from the
first suite run), matching how app.py's chat tab actually accumulates
history within a session. Calls the real Groq API — not a replay.
"""

import os
import sys
import json
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dotenv import load_dotenv  # noqa: E402
from test_agent_accuracy import build_test_executor, CASES  # noqa: E402

load_dotenv()

FIRST_TURN_USER = "Audite a tabela sales."
with open(os.path.join(os.path.dirname(__file__), "results.json"), encoding="utf-8") as f:
    _prior = {r["id"]: r for r in json.load(f)}
FIRST_TURN_ASSISTANT = _prior["T01"]["output"]

FOLLOWUP_IDS = ["A05", "A09", "A10", "A11", "A12", "A13", "D01", "D03", "S02"]


def run_suite():
    executor = build_test_executor()
    case_by_id = {c["id"]: c for c in CASES}
    results = []
    for cid in FOLLOWUP_IDS:
        case = case_by_id[cid]
        print(f"[{cid}] {case['prompt'][:70]}...", flush=True)
        t0 = time.time()
        try:
            res = executor.invoke({
                "input": case["prompt"],
                "chat_history": [("human", FIRST_TURN_USER), ("ai", FIRST_TURN_ASSISTANT)],
            })
            output = res.get("output", "")
            steps = res.get("intermediate_steps", [])
            passed, reason = case["check"](output, steps)
            error = None
        except Exception as e:
            output, steps, passed, reason, error = "", [], False, "exception during invoke", str(e)
        elapsed = round(time.time() - t0, 2)
        results.append({
            "id": cid, "category": case["category"], "prompt": case["prompt"],
            "passed": passed, "reason": reason, "output": output, "elapsed_s": elapsed,
            "error": error,
        })
        print(f"   -> {'PASS' if passed else 'FAIL'} ({elapsed}s) {reason}", flush=True)

    n_pass = sum(1 for r in results if r["passed"])
    print(f"\n{n_pass}/{len(results)} passed ({round(n_pass/len(results)*100, 1)}%)")

    out_path = os.path.join(os.path.dirname(__file__), "results_multiturn.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"Full results written to {out_path}")
    return results


if __name__ == "__main__":
    run_suite()
