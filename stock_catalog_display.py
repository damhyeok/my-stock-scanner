"""Display-ready symbol catalog for fast watchlist search."""

import pandas as pd


CATALOG_COLUMNS = ["display_order", "ticker", "name", "market_cap"]


def _columns(conn, table):
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def build_stock_catalog_display(conn):
    frames = []
    for table in ("model_universe_snapshots", "daily_stocks"):
        if {"ticker", "name", "market_cap"}.issubset(_columns(conn, table)):
            frames.append(pd.read_sql_query(
                f"SELECT ticker, name, MAX(COALESCE(market_cap, 0)) AS market_cap "
                f'FROM "{table}" GROUP BY ticker, name', conn,
            ))
    if frames:
        catalog = pd.concat(frames, ignore_index=True)
        catalog["ticker"] = catalog["ticker"].astype(str).str.zfill(6)
        catalog["market_cap"] = pd.to_numeric(
            catalog["market_cap"], errors="coerce"
        ).fillna(0)
        catalog = (
            catalog.sort_values("market_cap", ascending=False)
            .drop_duplicates("ticker")
            .sort_values(["name", "ticker"])
            .reset_index(drop=True)
        )
    else:
        catalog = pd.DataFrame(columns=CATALOG_COLUMNS[1:])
    catalog.insert(0, "display_order", range(len(catalog)))
    catalog[CATALOG_COLUMNS].to_sql(
        "web_stock_catalog", conn, if_exists="replace", index=False
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_web_stock_catalog_order "
        "ON web_stock_catalog(display_order)"
    )
    return len(catalog)


def read_stock_catalog_display(conn):
    return pd.read_sql_query(
        "SELECT ticker, name, market_cap FROM web_stock_catalog ORDER BY display_order",
        conn,
    )
