"""Audit and remove false KRX market rows for the 2026 Chuseok closure.

Default mode only prints matching row counts. --apply backs up the entire
database first, then deletes only the allowlisted market-derived tables.
News and user-maintained watchlists are intentionally untouched.
"""

import argparse
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path


DATES = ("20260924", "20260925")
DATE_COLUMNS = {
    "close_bet_model3_runs": "trade_date",
    "close_bet_model3_scans": "trade_date",
    "close_bet_scan_runs": "trade_date",
    "close_bet_scans": "trade_date",
    "daily_stocks": "date",
    "intraday_relative_strength_runs": "trade_date",
    "intraday_stock_bars": "trade_date",
    "market_betting_runs": "target_trade_date",
    "market_strength_snapshots": "trade_date",
    "model_universe_snapshots": "snapshot_date",
    "news_issue_price_context": "date",
    "sector_flow_windows": "trade_date",
    "stock_program_net_runs": "trade_date",
    "stock_program_net_snapshots": "trade_date",
}


def matching_count(conn, table, column):
    return conn.execute(
        'SELECT COUNT(*) FROM "{}" WHERE REPLACE("{}", "-", "") IN (?, ?)'.format(table, column),
        DATES,
    ).fetchone()[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("db", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--audit-all", action="store_true")
    args = parser.parse_args()
    db = args.db.resolve()
    if not db.is_file():
        parser.error("database not found: {}".format(db))
    conn = sqlite3.connect(str(db), timeout=60)
    try:
        existing = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counts = {table: matching_count(conn, table, column)
                  for table, column in DATE_COLUMNS.items() if table in existing}
        for table, count in counts.items():
            print("{}: {}".format(table, count))
        print("total market-derived rows: {}".format(sum(counts.values())))
        if args.audit_all:
            print("other date-like matches:")
            for table in sorted(existing):
                for info in conn.execute('PRAGMA table_info("{}")'.format(table)):
                    column = info[1]
                    if not any(word in column.lower() for word in ("date", "day", "time", "_at")):
                        continue
                    matches = conn.execute(
                        'SELECT COUNT(*) FROM "{}" WHERE REPLACE(SUBSTR(CAST("{}" AS TEXT),1,10),"-","") IN (?, ?)'.format(table, column),
                        DATES,
                    ).fetchone()[0]
                    if matches:
                        print("{}.{}, {}".format(table, column, matches))
        if not args.apply:
            return
        backup = db.with_name("{}_pre_holiday_cleanup_{}.db".format(
            db.stem, datetime.now().strftime("%Y%m%d_%H%M%S")))
        with closing(sqlite3.connect(str(backup))) as backup_conn:
            conn.backup(backup_conn)
            result = backup_conn.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                raise RuntimeError("backup integrity check failed: {}".format(result))
        print("backup: {}".format(backup))
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            for table, column in DATE_COLUMNS.items():
                if table in existing:
                    conn.execute(
                        'DELETE FROM "{}" WHERE REPLACE("{}", "-", "") IN (?, ?)'.format(table, column),
                        DATES,
                    )
            for table, column in DATE_COLUMNS.items():
                if table in existing and matching_count(conn, table, column):
                    raise RuntimeError("cleanup verification failed: {}".format(table))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        print("integrity_check: {}".format(conn.execute("PRAGMA integrity_check").fetchone()[0]))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
