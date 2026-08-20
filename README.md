# IOMETE Sentinel

[![CI](https://github.com/RoanOsorio03/data-audit-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/RoanOsorio03/data-audit-agent/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An LLM agent that profiles a DuckDB table for null rates, price outliers, future-dated rows, and duplicate keys, then writes a Markdown health report and a corrective SQL script.

> **Not affiliated with [IOMETE](https://iomete.com).** The name references IOMETE's lakehouse product for context only; this project uses DuckDB to simulate a lakehouse table locally and has no connection to IOMETE the company or its infrastructure.

## Demo

Run the CLI entry point against the bundled synthetic dataset:

```bash
python src/setup_data.py   # generates data/lakehouse.duckdb (1000-row `sales` table, seed=42)
python src/sentinel.py     # runs a full audit, prints + saves the report
```

**Input** (the fixed prompt `run_full_audit()` sends to the agent):

```
Realize uma auditoria completa da tabela 'sales'. Gere o Health Report em Markdown e o script SQL de limpeza.
```

**Output** — captured verbatim from a real run on 2026-08-19 (`gpt-oss-120b` via Groq, 20.7s end to end, 1 tool call):

````markdown
# IOMETE Sentinel — Health Report
**Tabela:** `sales`  |  **Data:** 2026-08-20  |  **Status:** 🔴 CRITICAL

## Executive Summary
A auditoria revelou falhas críticas que comprometem a confiabilidade dos dados de vendas:
13,2% dos registros têm `order_id` nulo, há duplicidade de IDs (2,2%) e 0,9% das linhas
apresentam preços unitários negativos. Além disso, 5,2% das datas de venda são futuras e
5,7% dos métodos de pagamento estão ausentes.

## Anomalies Detected
| Coluna          | Problema                     | Severidade  | Contagem | %    |
|------------------|-------------------------------|-------------|----------|------|
| order_id         | Valores nulos                 | 🔴 CRITICAL | 132      | 13.2 |
| order_id         | Duplicatas                    | 🔴 CRITICAL | 22       | 2.2  |
| customer_id      | Valores nulos                 | 🟡 WARNING  | 83       | 8.3  |
| unit_price       | Preço negativo ou zero        | 🔴 CRITICAL | 9        | 0.9  |
| unit_price       | Outliers altos (> R$ 10.000)  | 🟡 WARNING  | 8        | 0.8  |
| sale_date        | Datas futuras                 | 🟡 WARNING  | 52       | 5.2  |

## Self-Healing SQL
```sql
DELETE FROM sales WHERE order_id IS NULL;
-- ... full script deduplicates order_id, nulls out price outliers,
-- imputes the median, clamps future dates, recomputes total_amount
```
````

`reports/` is gitignored (each run writes a new timestamped file, so nothing to commit) — the example above is the verbatim, unedited output of the run described, not a re-typed excerpt. The agent's response language matches the input language (Portuguese here) because the system prompt asks for that; nothing hardcodes it.

## Why this exists

Synthetic-but-realistic dirty data is common in Lakehouse pipelines and usually gets caught by dashboards *after* it reaches a report. This project profiles the table with deterministic SQL first, then hands the numbers (not the raw rows) to an LLM to interpret severity and draft a fix — the LLM never invents the statistics, it only reasons over `get_table_statistics`'s JSON output.

## Stack and why

| Choice | Reason |
|---|---|
| **DuckDB** | Embedded, zero-ops OLAP engine with a `pandas`-native API — no cluster or catalog service needed to simulate a lakehouse table locally. |
| **LangChain (`create_tool_calling_agent` + `AgentExecutor`)** | Gives the LLM a single typed tool (`get_table_statistics`) instead of raw SQL access, so the model can't touch the database directly — see [Limitations](#known-limitations) for what this does and doesn't prevent. |
| **Groq (`langchain-groq`)** | Fast inference for an interactive Streamlit chat loop; report generation completes in ~20s end to end. |
| **Streamlit** | Ships a dashboard + chat UI from pure Python, no separate frontend build step, which fits a single-maintainer project. |
| **Plotly** | Interactive histograms/bar charts inside Streamlit without extra JS glue. |

## Architecture

```
setup_data.py                    sentinel.py                        app.py
─────────────                    ───────────                        ──────
generate_dirty_sales()   ─────►  get_table_statistics (tool)  ◄────  Streamlit dashboard
  seeded, deterministic          runs COUNT/MIN/MAX/AVG/STDDEV       (independent pandas
  null/outlier/future-date       + outlier/dup checks via DuckDB      metrics, no LLM)
  injection                              │
        │                                ▼
        ▼                       AgentExecutor (Groq LLM)     ◄────  Streamlit chat tab
data/lakehouse.duckdb           writes Markdown report +             (same agent, invoked
  (DuckDB file, gitignored)     SQL cleaning script                  per user message)
                                        │
                                        ▼
                                reports/*.md (gitignored)
```

**This is tool-calling, not natural-language-to-SQL.** The LLM never writes or executes a query against the data — `get_table_statistics` is one fixed, parameterized SQL template (parameterized only by `table_name`), and the agent's only real decision is *whether and with what table name* to call it. The "Self-Healing SQL" block in the report *is* LLM-generated free text, but it is written to a Markdown file and never executed by the system — a human is expected to review and run it manually.

The dashboard's KPI cards and charts are computed directly in pandas (`compute_health_metrics` in `app.py`) — they do **not** go through the LLM. Only the chat tab and "Gerar Relatório Agora" button invoke the agent. The two severity-threshold definitions (dashboard vs. agent system prompt) are kept in sync by hand, not by shared code — see Limitations.

## Installation and usage

Requires Python 3.10+ (tested locally on 3.10; the Docker image uses `python:3.11-slim`) and a [Groq API key](https://console.groq.com).

```bash
git clone https://github.com/RoanOsorio03/data-audit-agent.git
cd data-audit-agent
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # then paste your GROQ_API_KEY into .env
python src/setup_data.py           # generates data/lakehouse.duckdb
streamlit run src/app.py           # dashboard at http://localhost:8501
```

CLI-only audit (no Streamlit):

```bash
python src/sentinel.py
```

### Docker

```bash
docker build -t iomete-sentinel .
docker run -p 8501:8501 -e GROQ_API_KEY=your_key_here iomete-sentinel
```

The key is passed at container-run time (`-e` or `--env-file`), never baked into the image.

## Results

Measured on this machine, 2026-08-19, `sales` table with `seed=42` (1000 rows):

| Step | Measured |
|---|---|
| `setup_data.py` (generate + write 1000 rows to DuckDB) | 2.97s |
| `lakehouse.duckdb` file size | 524 KB |
| Full audit (`get_table_statistics` call + report generation, `gpt-oss-120b`) | 20.71s, 1 tool call |
| Injected vs. detected anomaly rate (this run) | order_id null: target ~12% → measured 13.2% · customer_id null: target ~8% → measured 8.3% · unit_price null: target ~5% → measured 4.8% · price outliers (high+neg/zero): target ~2% → measured 1.7% · future dates: target ~5% → measured 5.2% · duplicate order_id: target ~3% → measured 2.2% |

The detection SQL (`get_table_statistics`) is deterministic and exact by construction — it's a `COUNT(*) WHERE` query, not a model prediction. The only non-deterministic part is the LLM's severity classification and prose. TODO: p50/p95 latency and cost-per-audit haven't been measured across multiple runs — only this single run is real data.

### Agent accuracy

Test files: [`tests/test_agent_accuracy.py`](tests/test_agent_accuracy.py), [`tests/test_agent_accuracy_multiturn.py`](tests/test_agent_accuracy_multiturn.py).

26 real-call test cases against `openai/gpt-oss-120b`, cold (no prior chat history), graded mechanically (tool-call inspection + regex number-matching, not manual judgment): **16/26 passed (61.5%)**. 9 of the 10 failures share one cause — the agent asked which table to use when the question didn't name one, rather than guessing (the tool has no default table). It never fabricated a wrong statistic or guessed an unnamed table across any of the 26 cases, including under a prompt-injection attempt.

Re-running those 9 "no table named" cases with `chat_history` seeded from a real prior "audit sales" turn — matching how the Streamlit chat tab actually accumulates context within a session — **8/9 passed (88.9%)**; the 1 failure is a grading-regex bug (agent answered correctly using a Unicode hyphen my grader didn't recognize as a minus sign), not an agent error. So the cold-start 61.5% understates realistic in-session accuracy; the real bottleneck is missing default-table context on the first mention, not arithmetic or hallucination.

Full per-case breakdown, both runs, and two grader-bug post-mortems (thousands-separator and Unicode-hyphen matching): [`tests/RESULTS.md`](tests/RESULTS.md). There is no JOIN/GROUP BY/subquery coverage — the system has one table and one fixed profiling tool (see [Architecture](#architecture)), so those aren't meaningful to test yet.

## Security

**`get_table_statistics` had a SQL injection vulnerability in its `table_name` parameter — found and fixed during this review.**

The tool is invoked by the LLM, not by a trusted caller: the agent decides what string to pass as `table_name` based on user input and its own reasoning, which makes that parameter attacker-influenced input, not a static config value. The original implementation interpolated it straight into f-string SQL:

```python
# before — table_name flows from LLM output straight into the query text
total = con.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
```

A prompt like *"Audit the table `sales; DROP TABLE sales;--`"* — or any input engineered to make the model emit a malicious string as its tool-call argument — would have reached this string unmodified. The fix (`src/sentinel.py`, `_quote_ident` + the `get_table_statistics` catalog check):

```python
# after — table_name must already exist in the catalog before it's ever used in SQL text
known_tables = {row[0] for row in con.execute(
    "SELECT table_name FROM information_schema.tables"
).fetchall()}
if table_name not in known_tables:
    return json.dumps({"error": f"Unknown table: {table_name!r}"})
tbl = _quote_ident(table_name)  # then quoted, with embedded quotes escaped
```

This is an allowlist against the live catalog, not string-blacklisting or escaping alone — a payload can't reference a table that doesn't exist, and a valid table name gets its identifier-quoting escaped before use. `tests/test_deterministic.py::test_get_table_statistics_blocks_sql_injection` is a regression test: it sends `"sales; DROP TABLE sales;--"` as `table_name` and asserts both that the call returns a JSON error and that the real `sales` table still has all 1000 rows afterward — so a future change that reintroduces raw interpolation breaks CI, not just a manual check.

**What this does not fix:** column names (`col`) skip the catalog check because they come from `DESCRIBE {tbl}` — i.e. from the database's own schema, not from the LLM — so they were never attacker-controlled in the first place; quoting them is defense in depth, not the primary fix. The query strings elsewhere in the tool are still built with f-strings rather than a query builder or parameter binding, so any *new* tool argument added later needs the same allowlist-before-interpolation treatment — it isn't automatic. See [Known limitations](#known-limitations).

## Continuous Integration

Two separate GitHub Actions workflows, deliberately not merged into one:

- **[`ci.yml`](.github/workflows/ci.yml)** — runs on every push and pull request, including from forks. Lints with `ruff`, verifies `setup_data`/`sentinel`/`app` all import cleanly, and runs `tests/test_deterministic.py` (the SQL-injection regression test above, oracle-value checks against known-seed statistics, catalog-validation checks). No network calls, no secrets required — this is what the badge at the top of this README reflects.
- **[`llm-eval.yml`](.github/workflows/llm-eval.yml)** — runs `tests/test_agent_accuracy.py` and `tests/test_agent_accuracy_multiturn.py` (the 26+9 real-call suites behind the [Agent accuracy](#agent-accuracy) numbers above) against the live Groq API. Triggered manually (`workflow_dispatch`) or weekly on a schedule, **not** on every push.

The second workflow is separate on purpose, not because LLM tests matter less: they cost real API credits per run, take several minutes, depend on Groq being reachable, and need `GROQ_API_KEY` as a repository secret — a secret GitHub does not expose to pull requests opened from forks, so a contributor's PR could never pass a `llm-eval`-gated check anyway. Running it on every push would make the main CI signal flaky for reasons unrelated to code correctness. To enable it in your own fork, add `GROQ_API_KEY` under **Settings → Secrets and variables → Actions**; results are uploaded as a workflow artifact (`tests/results.json`, `tests/results_multiturn.json`), not written back to the repo. Neither workflow prints the key — it's only ever read from `os.environ` inside `ChatGroq(...)`, and GitHub additionally masks any exact match of a registered secret value in logs by default.

## Known limitations

- **SQL injection was present and is now mitigated, not eliminated** — see [Security](#security) above for what was found, the fix, and what's still not covered.
- **No authentication or multi-tenancy.** Anyone with the Streamlit URL can read the API key field and trigger LLM calls; not designed for shared deployment as-is.
- **Model availability isn't pinned defensively.** The original code targeted `llama-3.3-70b-versatile`, which Groq had already deprecated by the time of writing (`404 model_not_found`) — this was caught and fixed during this review, but nothing in the code guards against the *next* deprecation.
- **Two independent severity-threshold definitions** (`app.py`'s `compute_health_metrics` and `sentinel.py`'s `SYSTEM_PROMPT`) that can drift out of sync since neither reads from the other.
- **Dependency pins were broken until this review.** `requirements.txt` previously allowed `langchain>=0.2.0`, which resolves to `langchain 1.x` today — a version where `AgentExecutor` and `create_tool_calling_agent` no longer exist. Pinned to the last version confirmed to work (`langchain==0.3.30`); migrating to LangChain's current agent API is unstarted.
- **Synthetic data only.** `generate_dirty_sales()` is deterministic (fixed seed) and was designed to be caught by these exact checks — it is not evidence the agent generalizes to real, messier data.
- **Single table, single tool.** The agent can only call `get_table_statistics` on `sales`; there's no schema discovery, no multi-table joins, no incremental/streaming profiling.
- **No default table.** The tool requires an explicit `table_name` and the system prompt doesn't set one, so a question that doesn't name a table (e.g. "what's the mean quantity?") gets a clarifying question back instead of an answer — confirmed in 9/26 accuracy-suite cases. In the Streamlit chat this is usually masked by accumulated `chat_history` from earlier turns in the same session; a cold single-turn question is not.
- **Report/SQL correctness isn't independently validated** — the LLM's cleaning script is not executed or type-checked against the schema before being written to `reports/`; a human is expected to review it before running it against real data.

## Project status

Solo project, built as a portfolio piece. Not production-hardened; see Limitations above for what that means concretely.
