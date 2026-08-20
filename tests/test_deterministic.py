"""
IOMETE Sentinel — deterministic test suite.

No network calls, no API key required — safe to run on every push, including
from a contributor's fork (GitHub doesn't expose repo secrets to fork PRs
anyway, which is one more reason this suite is kept separate from
tests/test_agent_accuracy*.py).

Ground-truth numbers below come from generate_dirty_sales(seed=42, n=1000);
that seed makes numpy's default_rng output reproducible across machines, so
these exact counts should hold on any runner.
"""

import os
import sys
import json

import duckdb

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import setup_data  # noqa: E402
from sentinel import get_table_statistics  # noqa: E402


def _fresh_db(tmp_path):
    """Build a lakehouse.duckdb in an isolated temp location so this suite
    never touches a developer's real data/lakehouse.duckdb."""
    db_path = os.path.join(tmp_path, "lakehouse.duckdb")
    df = setup_data.generate_dirty_sales(n=1000, seed=42)
    con = duckdb.connect(db_path)
    con.execute("""
        CREATE TABLE sales (
            order_id VARCHAR, customer_id VARCHAR, category VARCHAR,
            quantity DOUBLE, unit_price DOUBLE, total_amount DOUBLE,
            sale_date TIMESTAMP, region VARCHAR, payment_method VARCHAR
        )
    """)
    con.register("df_view", df)
    con.execute("INSERT INTO sales SELECT * FROM df_view")
    con.close()
    return db_path


def test_generate_dirty_sales_is_deterministic():
    import numpy as np
    df1 = setup_data.generate_dirty_sales(n=1000, seed=42)
    df2 = setup_data.generate_dirty_sales(n=1000, seed=42)
    assert df1["order_id"].tolist() == df2["order_id"].tolist()
    assert np.array_equal(df1["unit_price"].to_numpy(), df2["unit_price"].to_numpy(), equal_nan=True)


def test_generate_dirty_sales_known_anomaly_counts():
    df = setup_data.generate_dirty_sales(n=1000, seed=42)
    assert len(df) == 1000
    assert int(df["order_id"].isna().sum()) == 132
    assert int(df["customer_id"].isna().sum()) == 83
    assert int(df["unit_price"].isna().sum()) == 48


def test_get_table_statistics_matches_oracle(tmp_path):
    db_path = _fresh_db(str(tmp_path))
    original_db_path = get_table_statistics.func.__globals__.get("DB_PATH")
    import sentinel
    sentinel.DB_PATH = db_path
    try:
        result = json.loads(get_table_statistics.invoke({"table_name": "sales"}))
    finally:
        sentinel.DB_PATH = original_db_path

    assert result["total_rows"] == 1000
    assert result["columns"]["order_id"]["null_count"] == 132
    assert result["columns"]["order_id"]["duplicate_count"] == 22
    assert result["columns"]["customer_id"]["null_count"] == 83
    assert result["columns"]["unit_price"]["outlier_high_count"] == 8
    assert result["columns"]["unit_price"]["outlier_negative_or_zero_count"] == 9


def test_get_table_statistics_rejects_unknown_table(tmp_path):
    db_path = _fresh_db(str(tmp_path))
    import sentinel
    original_db_path = sentinel.DB_PATH
    sentinel.DB_PATH = db_path
    try:
        result = json.loads(get_table_statistics.invoke({"table_name": "customers"}))
    finally:
        sentinel.DB_PATH = original_db_path

    assert "error" in result
    assert "Unknown table" in result["error"]


def test_get_table_statistics_blocks_sql_injection(tmp_path):
    """Regression test for the table_name SQL-injection issue found and
    fixed during the portfolio review (README > Security)."""
    db_path = _fresh_db(str(tmp_path))
    import sentinel
    original_db_path = sentinel.DB_PATH
    sentinel.DB_PATH = db_path
    try:
        payload = "sales; DROP TABLE sales;--"
        result = json.loads(get_table_statistics.invoke({"table_name": payload}))
        assert "error" in result

        # Confirm the injected DROP TABLE never executed: `sales` must still exist and be intact.
        con = duckdb.connect(db_path, read_only=True)
        try:
            row_count = con.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
        finally:
            con.close()
        assert row_count == 1000
    finally:
        sentinel.DB_PATH = original_db_path


def test_quote_ident_escapes_double_quotes():
    from sentinel import _quote_ident
    assert _quote_ident('sales') == '"sales"'
    assert _quote_ident('a"b') == '"a""b"'
