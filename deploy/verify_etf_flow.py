"""Read-only quote smoke test; optional real daily history seed, no fake sessions."""
import argparse
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crawler import StockCrawler
from etf_sector_flow import EtfSectorCollector, ETF_UNIVERSE, number
from market_calendar import KST


def verify(seed=False):
    crawler = StockCrawler()
    collector = EtfSectorCollector(crawler)
    day = datetime.now(KST).strftime("%Y%m%d")
    passed = 0
    for sector, ticker, name, _ in ETF_UNIVERSE:
        try:
            latest, records = collector.history(ticker, day)
            quote = collector.request(
                "/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100",
                {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker},
            ).get("output") or {}
            rows = collector.request(
                "/uapi/etfetn/v1/quotations/inquire-component-stock-price", "FHKST121600C0",
                {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker, "FID_COND_SCR_DIV_CODE": "11216"},
            ).get("output2") or []
            if not records or not number(quote.get("stck_prpr")) or number(quote.get("acml_tr_pbmn")) is None or not rows:
                raise RuntimeError("incomplete data")
            if seed:
                import sqlite3
                with sqlite3.connect(crawler.db_path) as conn:
                    conn.executemany("INSERT OR REPLACE INTO etf_sector_daily VALUES (?,?,?,?)",
                                     [r for r in records if r[0] < day])
            passed += 1
            print(f"OK {sector} {ticker}: latest={latest}, daily={len(records)}, holdings={len(rows)}")
        except Exception as error:
            print(f"FAIL {sector} {ticker}: {type(error).__name__}")
    print(f"ETF smoke test: {passed}/{len(ETF_UNIVERSE)}; no intraday snapshots created")
    return passed == len(ETF_UNIVERSE)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed-history", action="store_true")
    args = parser.parse_args()
    from cloud_job import file_lock
    with file_lock(".cloud_data.lock"):
        ok = verify(args.seed_history)
    raise SystemExit(0 if ok else 1)
