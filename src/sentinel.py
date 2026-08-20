"""
IOMETE Sentinel — Agentic Profiler
openai/gpt-oss-120b via Groq + LangChain tool-calling agent.
Responsibilities:
  1. Extract table statistics via DuckDB
  2. Generate a Markdown Health Report
  3. Produce a self-healing SQL cleaning script
"""

import os
import json
import textwrap
from datetime import datetime
from typing import Any

import duckdb
from langchain_groq import ChatGroq
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool

# ── Config ──────────────────────────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "lakehouse.duckdb")
REPORTS_DIR = os.path.join(os.path.dirname(__file__), "..", "reports")
GROQ_MODEL = "openai/gpt-oss-120b"

THRESHOLDS = {
    "null_pct_warning": 5.0,   # % → yellow
    "null_pct_critical": 10.0, # % → red
    "price_outlier_abs": 10000.0,
    "price_negative": 0.0,
    "future_date_col": "sale_date",
}


# ── Tool: get_table_statistics ───────────────────────────────────────────────
def _quote_ident(name: str) -> str:
    """Safely quote a SQL identifier already verified against the catalog."""
    return '"' + name.replace('"', '""') + '"'


@tool
def get_table_statistics(table_name: str) -> str:
    """
    Extracts comprehensive statistics from a DuckDB table.
    Returns JSON with: total_rows, column-level null_count, null_pct,
    min, max, mean, stddev, and domain-specific checks (outliers, future dates).

    Args:
        table_name: Name of the table to profile (e.g. 'sales').
    """
    con = duckdb.connect(DB_PATH, read_only=True)
    try:
        # Reject anything that isn't a real table before it ever touches an f-string.
        known_tables = {
            row[0]
            for row in con.execute(
                "SELECT table_name FROM information_schema.tables"
            ).fetchall()
        }
        if table_name not in known_tables:
            return json.dumps({"error": f"Unknown table: {table_name!r}"})

        tbl = _quote_ident(table_name)

        # Total rows
        total = con.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]

        # Column metadata (identifiers come from DESCRIBE, i.e. the catalog itself)
        cols_info = con.execute(f"DESCRIBE {tbl}").fetchall()
        columns = {row[0]: row[1] for row in cols_info}  # name -> type

        stats: dict[str, Any] = {"table": table_name, "total_rows": total, "columns": {}}

        for col, dtype in columns.items():
            col_q = _quote_ident(col)
            col_stats: dict[str, Any] = {"type": dtype}

            # Null analysis
            null_count = con.execute(
                f"SELECT COUNT(*) FROM {tbl} WHERE {col_q} IS NULL"
            ).fetchone()[0]
            col_stats["null_count"] = null_count
            col_stats["null_pct"] = round(null_count / total * 100, 2) if total > 0 else 0

            # Numeric columns — descriptive stats
            if any(t in dtype.upper() for t in ["INT", "DOUBLE", "FLOAT", "DECIMAL", "BIGINT"]):
                row = con.execute(f"""
                    SELECT
                        MIN({col_q}), MAX({col_q}),
                        AVG({col_q}), STDDEV({col_q})
                    FROM {tbl}
                    WHERE {col_q} IS NOT NULL
                """).fetchone()
                col_stats["min"] = round(row[0], 4) if row[0] is not None else None
                col_stats["max"] = round(row[1], 4) if row[1] is not None else None
                col_stats["mean"] = round(row[2], 4) if row[2] is not None else None
                col_stats["stddev"] = round(row[3], 4) if row[3] is not None else None

                # Outlier counts (unit_price specific logic)
                if col == "unit_price":
                    outlier_high = con.execute(
                        f"SELECT COUNT(*) FROM {tbl} WHERE {col_q} > {THRESHOLDS['price_outlier_abs']}"
                    ).fetchone()[0]
                    outlier_neg = con.execute(
                        f"SELECT COUNT(*) FROM {tbl} WHERE {col_q} <= {THRESHOLDS['price_negative']}"
                    ).fetchone()[0]
                    col_stats["outlier_high_count"] = outlier_high
                    col_stats["outlier_negative_or_zero_count"] = outlier_neg

            # Timestamp columns — future date check
            if "TIMESTAMP" in dtype.upper() or "DATE" in dtype.upper():
                future_count = con.execute(
                    f"SELECT COUNT(*) FROM {tbl} WHERE {col_q} > CURRENT_TIMESTAMP"
                ).fetchone()[0]
                col_stats["future_date_count"] = future_count
                col_stats["future_date_pct"] = round(future_count / total * 100, 2) if total > 0 else 0

            # Duplicate order_id check
            if col == "order_id":
                dup_count = con.execute(f"""
                    SELECT COUNT(*) FROM (
                        SELECT {col_q}, COUNT(*) AS cnt
                        FROM {tbl}
                        WHERE {col_q} IS NOT NULL
                        GROUP BY {col_q} HAVING cnt > 1
                    )
                """).fetchone()[0]
                col_stats["duplicate_count"] = dup_count

            stats["columns"][col] = col_stats

        # Sample of worst outlier prices
        try:
            outlier_samples = con.execute(f"""
                SELECT order_id, unit_price, sale_date
                FROM {tbl}
                WHERE unit_price > 10000 OR unit_price <= 0
                ORDER BY unit_price DESC
                LIMIT 5
            """).fetchall()
            stats["outlier_price_samples"] = [
                {"order_id": r[0], "unit_price": r[1], "sale_date": str(r[2])}
                for r in outlier_samples
            ]
        except Exception:
            stats["outlier_price_samples"] = []

        return json.dumps(stats, indent=2, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})
    finally:
        con.close()


# ── Prompt ───────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = textwrap.dedent("""
    You are IOMETE Sentinel, an elite Data Reliability Engineer agent specialized in
    Lakehouse observability. Your mission is to guard data quality with precision.

    When asked to audit a table you MUST:
    1. Call `get_table_statistics` to retrieve raw metrics.
    2. Analyze every anomaly — nulls, outliers, future dates, duplicates.
    3. Produce a detailed **Health Report** in Markdown (see format below).
    4. Generate a self-healing **SQL Cleaning Script** that fixes detected issues.

    ## Health Report Format
    ```
    # IOMETE Sentinel — Health Report
    **Table:** <name>  |  **Date:** <today>  |  **Status:** 🟢 HEALTHY / 🟡 WARNING / 🔴 CRITICAL

    ## Executive Summary
    <2-3 sentences for the CEO>

    ## Anomalies Detected
    | Column | Issue | Severity | Count | % |
    |--------|-------|----------|-------|---|
    ...

    ## Statistical Profile
    (key metrics per numeric column)

    ## Self-Healing SQL
    ```sql
    -- paste the cleaning script here
    ```

    ## Recommendations
    (ordered by business impact)
    ```

    ## Severity Rules
    - CRITICAL (🔴): null_pct > 10% on key columns, negative prices, duplicates > 1%
    - WARNING  (🟡): null_pct 5-10%, future dates > 2%, price > R$ 10,000
    - HEALTHY  (🟢): no anomalies above thresholds

    Always respond in the same language the user uses.
    Be direct, data-driven, and actionable.
""")

PROMPT = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_PROMPT),
    MessagesPlaceholder("chat_history", optional=True),
    ("human", "{input}"),
    MessagesPlaceholder("agent_scratchpad"),
])


# ── Agent factory ─────────────────────────────────────────────────────────────
def build_sentinel_agent(api_key: str | None = None) -> AgentExecutor:
    groq_key = api_key or os.environ.get("GROQ_API_KEY", "")
    if not groq_key:
        raise ValueError(
            "GROQ_API_KEY not set. Pass api_key= or set it in your environment/.env."
        )
    llm = ChatGroq(
        model=GROQ_MODEL,
        temperature=0.1,
        groq_api_key=groq_key,
        max_tokens=4096,
    )
    tools = [get_table_statistics]
    agent = create_tool_calling_agent(llm, tools, PROMPT)
    return AgentExecutor(
        agent=agent,
        tools=tools,
        verbose=True,
        max_iterations=5,
        handle_parsing_errors=True,
    )


# ── Convenience runner ────────────────────────────────────────────────────────
def run_full_audit(table: str = "sales", api_key: str | None = None) -> dict[str, str]:
    """
    Runs a full audit and returns {'report': markdown, 'sql': cleaning_sql}.
    Also saves both to the reports/ directory.
    """
    executor = build_sentinel_agent(api_key)

    question = (
        f"Realize uma auditoria completa da tabela '{table}'. "
        "Gere o Health Report em Markdown e o script SQL de limpeza."
    )
    result = executor.invoke({"input": question})
    output: str = result.get("output", "")

    # Persist report
    os.makedirs(REPORTS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(REPORTS_DIR, f"health_report_{ts}.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(output)

    return {"report": output, "report_path": report_path}


if __name__ == "__main__":
    result = run_full_audit()
    print(result["report"])
