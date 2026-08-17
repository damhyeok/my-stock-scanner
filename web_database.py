"""Build the bounded SQLite snapshot deployed with the Streamlit app."""
import argparse
import gzip
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path


RETENTION = {
    "daily_stocks": ("date", 30),
    "stock_news": ("date", 15),
    "market_strength_snapshots": ("trade_date", 30),
    "market_program_snapshots": ("trade_date", 30),
    "stock_program_net_snapshots": ("trade_date", 30),
    "stock_program_net_runs": ("trade_date", 30),
    "sector_flow_windows": ("trade_date", 30),
    "intraday_stock_bars": ("trade_date", 5),
    "intraday_index_bars": ("trade_date", 5),
    "intraday_relative_strength_snapshots": ("trade_date", 30),
    "intraday_relative_strength_runs": ("trade_date", 30),
    "close_bet_scans": ("trade_date", 30),
    "close_bet_scan_runs": ("trade_date", 30),
    "model_universe_snapshots": ("snapshot_date", 5),
    "model_ohlcv_daily": ("date", 180),
    "model_feature_daily": ("date", 180),
    "model_market_regimes": ("date", 180),
    "model_bottom_signals": ("signal_date", 30),
    "model_rule_scan_signals": ("signal_date", 30),
    "model_bottom_scan_runs": ("signal_date", 30),
    "market_betting_runs": ("target_trade_date", 30),
}

DROP_TABLES = {
    "model_backtest_labels",
    "model_bottom_weight_runs",
    # The dashboard reads compact judgments/derived evidence, not raw minute observations.
    "market_betting_observations",
}


def _table_exists(conn, table):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _trim_to_latest_dates(conn, table, date_column, keep_dates):
    if not _table_exists(conn, table):
        return
    cutoff = conn.execute(
        f'''SELECT MIN("{date_column}") FROM (
                SELECT DISTINCT "{date_column}" FROM "{table}"
                WHERE "{date_column}" IS NOT NULL
                ORDER BY "{date_column}" DESC LIMIT ?
            )''',
        (keep_dates,),
    ).fetchone()[0]
    if cutoff is not None:
        conn.execute(
            f'DELETE FROM "{table}" WHERE "{date_column}" < ?', (cutoff,)
        )


def build_web_database(source="stock_data.db", target="web_data.db"):
    source_path = Path(source)
    target_path = Path(target)
    if not source_path.is_file():
        raise FileNotFoundError(f"Source database not found: {source_path}")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f"{target_path.stem}_", suffix=".db")
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        source_conn = sqlite3.connect(source_path)
        target_conn = sqlite3.connect(temp_path)
        try:
            source_conn.backup(target_conn)
        finally:
            target_conn.close()
            source_conn.close()
        conn = sqlite3.connect(temp_path)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            for table in DROP_TABLES:
                conn.execute(f'DROP TABLE IF EXISTS "{table}"')
            for table, (date_column, keep_dates) in RETENTION.items():
                _trim_to_latest_dates(conn, table, date_column, keep_dates)
            conn.commit()
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise RuntimeError(f"Web database integrity check failed: {integrity}")
            conn.execute("VACUUM").close()
        finally:
            conn.close()
        shutil.copyfile(temp_path, target_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    return {
        "source_bytes": source_path.stat().st_size,
        "target_bytes": target_path.stat().st_size,
    }


def compress_web_database(source="web_data.db", target="web_data.db.gz"):
    """Write a deterministic, atomically replaced gzip copy of a web DB."""

    source_path = Path(source)
    target_path = Path(target)
    if not source_path.is_file():
        raise FileNotFoundError(f"Web database not found: {source_path}")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f"{target_path.stem}_", suffix=".gz", dir=target_path.parent
    )
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        with source_path.open("rb") as source_file, temp_path.open("wb") as raw_target:
            with gzip.GzipFile(
                filename=source_path.name,
                mode="wb",
                fileobj=raw_target,
                mtime=0,
            ) as compressed_file:
                shutil.copyfileobj(source_file, compressed_file, length=1024 * 1024)
        os.replace(temp_path, target_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    return {
        "source_bytes": source_path.stat().st_size,
        "compressed_bytes": target_path.stat().st_size,
    }


def decompress_web_database(source="web_data.db.gz", target="web_data.db"):
    """Restore a gzip snapshot atomically and verify its SQLite header."""

    source_path = Path(source)
    target_path = Path(target)
    if not source_path.is_file():
        raise FileNotFoundError(f"Compressed web database not found: {source_path}")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f"{target_path.stem}_", suffix=".db", dir=target_path.parent
    )
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        with gzip.open(source_path, "rb") as compressed_file, temp_path.open("wb") as db_file:
            shutil.copyfileobj(compressed_file, db_file, length=1024 * 1024)
        with temp_path.open("rb") as db_file:
            if db_file.read(16) != b"SQLite format 3\000":
                raise ValueError("Decompressed file is not a SQLite database.")
        os.replace(temp_path, target_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return target_path


def restore_working_database(
    web_db="web_data.db",
    working_db="stock_data.db",
    compressed_web_db="web_data.db.gz",
    bootstrap_web_db="web_data.bootstrap.db.gz",
):
    web_path = Path(web_db)
    working_path = Path(working_db)
    if working_path.is_file():
        return False
    if not web_path.is_file() and Path(compressed_web_db).is_file():
        decompress_web_database(compressed_web_db, web_path)
    if not web_path.is_file() and Path(bootstrap_web_db).is_file():
        decompress_web_database(bootstrap_web_db, web_path)
    if not web_path.is_file():
        raise FileNotFoundError(f"Neither {working_path} nor {web_path} exists")
    shutil.copy2(web_path, working_path)
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="stock_data.db")
    parser.add_argument("--target", default="web_data.db")
    parser.add_argument("--compressed-target", default="")
    args = parser.parse_args()
    summary = build_web_database(args.source, args.target)
    print(
        "Web DB built: "
        f"{summary['source_bytes'] / 1048576:.1f} MB -> "
        f"{summary['target_bytes'] / 1048576:.1f} MB"
    )
    if args.compressed_target:
        compressed = compress_web_database(args.target, args.compressed_target)
        print(
            "Web DB compressed: "
            f"{compressed['source_bytes'] / 1048576:.1f} MB -> "
            f"{compressed['compressed_bytes'] / 1048576:.1f} MB"
        )
