import sqlite3
import unittest

from stock_catalog_display import (
    build_stock_catalog_display,
    read_stock_catalog_display,
)


class StockCatalogDisplayTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        schema = "(ticker TEXT, name TEXT, market_cap REAL)"
        self.conn.execute("CREATE TABLE model_universe_snapshots " + schema)
        self.conn.execute("CREATE TABLE daily_stocks " + schema)

    def tearDown(self):
        self.conn.close()

    def test_matches_existing_union_dedup_and_sort(self):
        self.conn.executemany(
            "INSERT INTO model_universe_snapshots VALUES (?,?,?)",
            [("1", "알파", 100), ("2", "가나다", 200)],
        )
        self.conn.executemany(
            "INSERT INTO daily_stocks VALUES (?,?,?)",
            [("1", "알파", 300), ("1", "알파", 250), ("3", "라마", None)],
        )
        build_stock_catalog_display(self.conn)
        result = read_stock_catalog_display(self.conn)
        self.assertEqual(result["ticker"].tolist(), ["000002", "000003", "000001"])
        self.assertEqual(result.set_index("ticker").loc["000001", "market_cap"], 300)

    def test_missing_source_tables_produces_empty_catalog(self):
        conn = sqlite3.connect(":memory:")
        try:
            self.assertEqual(build_stock_catalog_display(conn), 0)
            self.assertTrue(read_stock_catalog_display(conn).empty)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
