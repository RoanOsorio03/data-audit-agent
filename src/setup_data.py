"""
IOMETE Sentinel — Data Setup
Generates a purposely 'dirty' Iceberg-simulated sales table in DuckDB.
Injects: nulls in critical columns, price outliers, and future dates.
"""

import duckdb
import pandas as pd
import numpy as np
import random
from datetime import datetime, timedelta
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "lakehouse.duckdb")


def generate_dirty_sales(n: int = 1000, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    random.seed(seed)

    today = datetime.now()

    # --- Base columns (clean) ---
    order_ids = [f"ORD-{i:05d}" for i in range(1, n + 1)]
    categories = rng.choice(["Electronics", "Clothing", "Food", "Books", "Sports"], n)
    quantities = rng.integers(1, 20, n).astype(float)
    unit_prices = rng.uniform(10.0, 500.0, n)

    # --- Inject nulls (~12% on order_id, ~8% on customer_id) ---
    null_order_mask = rng.random(n) < 0.12
    null_customer_mask = rng.random(n) < 0.08
    null_price_mask = rng.random(n) < 0.05

    order_ids_dirty = [None if null_order_mask[i] else order_ids[i] for i in range(n)]
    customer_ids = [
        None if null_customer_mask[i] else f"CUST-{rng.integers(1000, 9999)}"
        for i in range(n)
    ]
    unit_prices[null_price_mask] = np.nan

    # --- Inject outlier prices (~2% of rows) ---
    outlier_mask = rng.random(n) < 0.02
    unit_prices[outlier_mask] = rng.choice([999999.99, -50.0, 0.0], outlier_mask.sum())

    # --- Inject future dates (~5% of rows) ---
    sale_dates = []
    for i in range(n):
        if rng.random() < 0.05:
            future_days = rng.integers(1, 365)
            sale_dates.append(today + timedelta(days=int(future_days)))
        else:
            past_days = rng.integers(0, 730)
            sale_dates.append(today - timedelta(days=int(past_days)))

    # --- Inject duplicate order_ids (~3% of rows) ---
    dup_indices = rng.choice(n, size=int(n * 0.03), replace=False)
    for idx in dup_indices:
        if order_ids_dirty[idx] is not None:
            order_ids_dirty[idx] = order_ids_dirty[rng.integers(0, n)]

    total_amounts = quantities * np.where(np.isnan(unit_prices), 0, unit_prices)

    df = pd.DataFrame(
        {
            "order_id": order_ids_dirty,
            "customer_id": customer_ids,
            "category": categories,
            "quantity": quantities,
            "unit_price": unit_prices,
            "total_amount": total_amounts,
            "sale_date": sale_dates,
            "region": rng.choice(["North", "South", "East", "West", "Online"], n),
            "payment_method": rng.choice(
                ["Credit Card", "Debit Card", "PIX", "Boleto", None], n,
                p=[0.35, 0.25, 0.25, 0.10, 0.05],
            ),
        }
    )
    return df


def setup_lakehouse(verbose: bool = True) -> str:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    df = generate_dirty_sales()
    con = duckdb.connect(DB_PATH)
    try:
        con.execute("DROP TABLE IF EXISTS sales")
        con.execute("""
            CREATE TABLE sales (
                order_id       VARCHAR,
                customer_id    VARCHAR,
                category       VARCHAR,
                quantity       DOUBLE,
                unit_price     DOUBLE,
                total_amount   DOUBLE,
                sale_date      TIMESTAMP,
                region         VARCHAR,
                payment_method VARCHAR
            )
        """)
        con.register("df_view", df)
        con.execute("INSERT INTO sales SELECT * FROM df_view")
        total = con.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
    finally:
        con.close()

    if verbose:
        print(f"[setup_data] Lakehouse ready: {total} rows written to {DB_PATH}")
        print("  Nulls injected: ~12% order_id, ~8% customer_id, ~5% unit_price")
        print("  Outlier prices:  ~2% rows (R$ 999,999 / negatives / zeros)")
        print("  Future dates:    ~5% rows")

    return DB_PATH


if __name__ == "__main__":
    setup_lakehouse()
