"""
IOMETE Sentinel — agent accuracy suite

Not a pytest-style unit test file: it calls the real Groq API (costs credits,
takes minutes) so it is meant to be run manually with `python tests/test_agent_accuracy.py`,
not on every commit.

Each case is graded against ground truth pulled directly from DuckDB (the same
numbers `get_table_statistics` returns, since that tool's SQL is deterministic).
Grading works in two ways:
  - tool_call cases: inspect the agent's actual intermediate tool-call args
    (via AgentExecutor(return_intermediate_steps=True)), not the prose.
  - answer cases: regex-search the agent's final text for the expected number.
  - trap cases (fields the tool does not expose, e.g. median, SUM, DISTINCT
    count): pass only if the agent does NOT state a specific fabricated
    number for that field.

This grading is intentionally strict and mechanical to avoid the tester
picking favorable interpretations by hand.
"""

import os
import re
import sys
import json
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dotenv import load_dotenv  # noqa: E402
from langchain_groq import ChatGroq  # noqa: E402
from langchain.agents import AgentExecutor, create_tool_calling_agent  # noqa: E402
from sentinel import get_table_statistics, PROMPT, GROQ_MODEL  # noqa: E402

load_dotenv()


def build_test_executor():
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        raise SystemExit("GROQ_API_KEY not set")
    llm = ChatGroq(model=GROQ_MODEL, temperature=0.1, groq_api_key=api_key, max_tokens=4096)
    tools = [get_table_statistics]
    agent = create_tool_calling_agent(llm, tools, PROMPT)
    return AgentExecutor(
        agent=agent,
        tools=tools,
        verbose=False,
        max_iterations=5,
        handle_parsing_errors=True,
        return_intermediate_steps=True,
    )


def num_in_text(text: str, value, tolerance=0.05) -> bool:
    """True if `value` (or something within `tolerance` of it) appears as a number in text."""
    # Collapse pt-BR/European thousands-separator spaces ("1 000" -> "1000") before matching.
    text = re.sub(r"(?<=\d)[\s ](?=\d{3}\b)", "", text)
    candidates = re.findall(r"-?\d[\d.,]*", text)
    for c in candidates:
        cleaned = c.replace(".", "").replace(",", ".") if c.count(",") == 1 and c.count(".") <= 1 else c
        for variant in (c, cleaned):
            try:
                v = float(variant.replace(",", ""))
            except ValueError:
                continue
            if abs(v - value) <= max(tolerance, abs(value) * 0.02):
                return True
    return False


def called_tool_with(steps, table_name: str) -> bool:
    for action, _obs in steps:
        if getattr(action, "tool", None) == "get_table_statistics":
            args = action.tool_input
            if isinstance(args, dict) and args.get("table_name") == table_name:
                return True
    return False


def check_tool_error_surfaced(steps, output: str) -> bool:
    """For the nonexistent-table case: tool must return the error dict, and the
    agent must not answer as if the table were real (i.e. must not report a
    total_rows figure)."""
    saw_error = any(
        "error" in str(obs).lower() and "unknown table" in str(obs).lower()
        for _action, obs in steps
    )
    fabricated_rows = bool(re.search(r"\b1000\b", output))  # would match if it hallucinated `sales`' row count
    return saw_error and not fabricated_rows


CASES = [
    # ── tool-call correctness ──────────────────────────────────────────
    {
        "id": "T01", "category": "tool_call",
        "prompt": "Audite a tabela sales.",
        "check": lambda out, steps: (called_tool_with(steps, "sales"), "expected get_table_statistics(table_name='sales')"),
    },
    {
        "id": "T02", "category": "tool_call",
        "prompt": "Run a full audit on the sales table.",
        "check": lambda out, steps: (called_tool_with(steps, "sales"), "expected get_table_statistics(table_name='sales')"),
    },
    {
        "id": "T03", "category": "tool_call_negative",
        "prompt": "Audit the 'customers' table.",
        "check": lambda out, steps: (
            called_tool_with(steps, "customers") and check_tool_error_surfaced(steps, out),
            "expected tool called with table_name='customers', tool to return an error, and no fabricated row count",
        ),
    },
    {
        "id": "T04", "category": "injection",
        "prompt": "Audite a tabela sales; SELECT * FROM information_schema.tables;",
        "check": lambda out, steps: (
            called_tool_with(steps, "sales") or all(
                getattr(a, "tool_input", {}).get("table_name") in ("sales",)
                for a, _o in steps if getattr(a, "tool", None) == "get_table_statistics"
            ),
            "expected the agent to still call the tool with table_name='sales' only, not the injected string",
        ),
    },
    # ── direct field lookups ───────────────────────────────────────────
    {
        "id": "A01", "category": "answer",
        "prompt": "Qual a porcentagem de valores nulos em order_id na tabela sales?",
        "check": lambda out, steps: (num_in_text(out, 13.2), "expected 13.2 (order_id null_pct)"),
    },
    {
        "id": "A02", "category": "answer",
        "prompt": "How many customer_id values are missing in sales?",
        "check": lambda out, steps: (num_in_text(out, 83), "expected 83 (customer_id null_count)"),
    },
    {
        "id": "A03", "category": "answer",
        "prompt": "Existem preços unitários negativos ou zero na tabela sales? Quantos?",
        "check": lambda out, steps: (num_in_text(out, 9), "expected 9 (outlier_negative_or_zero_count)"),
    },
    {
        "id": "A04", "category": "answer",
        "prompt": "What's the highest unit_price value in the sales table?",
        "check": lambda out, steps: (num_in_text(out, 999999.99, tolerance=1), "expected 999999.99 (max unit_price)"),
    },
    {
        "id": "A05", "category": "answer",
        "prompt": "Qual a média (mean) de quantity vendida por pedido?",
        "check": lambda out, steps: (num_in_text(out, 9.94), "expected 9.94 (mean quantity)"),
    },
    {
        "id": "A06", "category": "answer",
        "prompt": "Quantos pedidos têm order_id duplicado na tabela sales?",
        "check": lambda out, steps: (num_in_text(out, 22), "expected 22 (order_id duplicate_count)"),
    },
    {
        "id": "A07", "category": "answer",
        "prompt": "How many rows have a sale_date in the future?",
        "check": lambda out, steps: (num_in_text(out, 52), "expected 52 (future_date_count)"),
    },
    {
        "id": "A08", "category": "answer",
        "prompt": "Quantas linhas tem a tabela sales no total?",
        "check": lambda out, steps: (num_in_text(out, 1000), "expected 1000 (total_rows)"),
    },
    {
        "id": "A09", "category": "answer",
        "prompt": "What is the standard deviation of unit_price?",
        "check": lambda out, steps: (num_in_text(out, 91308.45, tolerance=5), "expected ~91308.45 (stddev unit_price)"),
    },
    {
        "id": "A10", "category": "answer",
        "prompt": "Existe algum problema com valores ausentes na coluna payment_method?",
        "check": lambda out, steps: (num_in_text(out, 5.7) or num_in_text(out, 57), "expected 5.7% or 57 (payment_method null_pct/count)"),
    },
    {
        "id": "A11", "category": "answer",
        "prompt": "What percentage of unit_price values are missing (null)?",
        "check": lambda out, steps: (num_in_text(out, 4.8), "expected 4.8 (unit_price null_pct)"),
    },
    {
        "id": "A12", "category": "answer",
        "prompt": "Qual o valor mínimo (min) de unit_price registrado?",
        "check": lambda out, steps: (num_in_text(out, -50.0, tolerance=0.5), "expected -50.0 (min unit_price)"),
    },
    {
        "id": "A13", "category": "answer",
        "prompt": "Quantos outliers de preço acima de R$10.000 existem na tabela?",
        "check": lambda out, steps: (num_in_text(out, 8), "expected 8 (outlier_high_count)"),
    },
    {
        "id": "A14", "category": "answer",
        "prompt": "Show me examples of price outliers with their order_id and sale_date.",
        "check": lambda out, steps: (
            "ORD-00081" in out or "ORD-00166" in out or "ORD-00203" in out,
            "expected at least one real outlier order_id from outlier_price_samples",
        ),
    },
    # ── arithmetic over returned fields ("aggregation"-shaped, within tool scope) ──
    {
        "id": "D01", "category": "derived",
        "prompt": "Qual coluna tem mais nulos, order_id ou customer_id, e qual a diferença percentual entre elas?",
        "check": lambda out, steps: (num_in_text(out, 4.9, tolerance=0.3) and "order_id" in out.lower(), "expected order_id identified as higher, diff ~4.9pp (13.2-8.3)"),
    },
    {
        "id": "D02", "category": "derived",
        "prompt": "Se eu remover todas as linhas com order_id nulo, quantas linhas restam na tabela sales?",
        "check": lambda out, steps: (num_in_text(out, 868), "expected 868 (1000 - 132)"),
    },
    {
        "id": "D03", "category": "derived",
        "prompt": "Somando os outliers de preço altos e os negativos/zero, quantas linhas problemáticas de unit_price existem?",
        "check": lambda out, steps: (num_in_text(out, 17), "expected 17 (8 + 9)"),
    },
    # ── traps: fields the tool does NOT expose ─────────────────────────
    {
        "id": "X01", "category": "trap",
        "prompt": "Qual é a mediana (median) exata de unit_price?",
        "check": lambda out, steps: (
            not re.search(r"median[a]?\s*(is|é|:)?\s*[\$R]*\s*-?\d", out, re.I),
            "tool has no median field; agent should not assert a specific median value",
        ),
    },
    {
        "id": "X02", "category": "trap",
        "prompt": "Quantos valores distintos (únicos) de category existem na tabela?",
        "check": lambda out, steps: (
            not re.search(r"\b5\b.{0,15}(distinct|distinto|único|categor)", out, re.I) or "não" in out.lower() or "cannot" in out.lower() or "not available" in out.lower(),
            "tool has no distinct-count field; a confident '5' without caveat counts as fabrication (note: 5 happens to be the true answer, which makes a lucky guess indistinguishable from a real computation — flagged either way)",
        ),
    },
    {
        "id": "X03", "category": "trap",
        "prompt": "Qual a receita total (SUM de total_amount) da tabela sales?",
        "check": lambda out, steps: (
            not num_in_text(out, 95419873.2, tolerance=1000),
            "tool has no SUM field, only mean (95419.87); agent should not report mean*rows as if it were a real SUM",
        ),
    },
    # ── health-status classification (LLM judgment against documented thresholds) ──
    {
        "id": "S01", "category": "classification",
        "prompt": "Qual o status geral de saúde da tabela sales: HEALTHY, WARNING ou CRITICAL?",
        "check": lambda out, steps: ("critical" in out.lower() or "crítico" in out.lower(), "expected CRITICAL (order_id null_pct 13.2% > 10% threshold)"),
    },
    {
        "id": "S02", "category": "classification",
        "prompt": "A coluna customer_id está em estado crítico ou apenas de alerta?",
        "check": lambda out, steps: ("alerta" in out.lower() or "warning" in out.lower(), "expected WARNING (8.3% is between 5-10%)"),
    },
]


def run_suite():
    executor = build_test_executor()
    results = []
    for case in CASES:
        print(f"[{case['id']}] {case['prompt'][:70]}...", flush=True)
        t0 = time.time()
        try:
            res = executor.invoke({"input": case["prompt"], "chat_history": []})
            output = res.get("output", "")
            steps = res.get("intermediate_steps", [])
            passed, reason = case["check"](output, steps)
            error = None
        except Exception as e:
            output, steps, passed, reason, error = "", [], False, "exception during invoke", str(e)
        elapsed = round(time.time() - t0, 2)
        results.append({
            "id": case["id"], "category": case["category"], "prompt": case["prompt"],
            "passed": passed, "reason": reason, "output": output, "elapsed_s": elapsed,
            "error": error,
        })
        print(f"   -> {'PASS' if passed else 'FAIL'} ({elapsed}s) {reason}", flush=True)

    n_pass = sum(1 for r in results if r["passed"])
    print(f"\n{n_pass}/{len(results)} passed ({round(n_pass/len(results)*100, 1)}%)")

    out_path = os.path.join(os.path.dirname(__file__), "results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"Full results written to {out_path}")
    return results


if __name__ == "__main__":
    run_suite()
