"""Build and optionally persist the latest transparent close-bet rule-model evaluation."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .config import load_config
from .data_loader import load_daily, load_selected_minute_bars, open_read_only
from .features.available_extended import (
    build_daily_extended_features,
    build_sampled_afternoon_features,
)
from .pipeline import build_step4_dataset
from .rule_model import evaluate_rule_model, load_rule_config


KST = timezone(timedelta(hours=9))


def _load_sector_map(database_path: str, trade_date: str) -> pd.DataFrame:
    with open_read_only(database_path) as connection:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='daily_stocks'"
        ).fetchone()
        if exists is None:
            return pd.DataFrame(columns=["ticker", "sector"])
        frame = pd.read_sql_query(
            """
            SELECT ticker, sector, collected_at_kst
            FROM daily_stocks WHERE date=? AND sector IS NOT NULL AND trim(sector)<>''
            ORDER BY collected_at_kst
            """,
            connection,
            params=(trade_date,),
        )
    if frame.empty:
        return pd.DataFrame(columns=["ticker", "sector"])
    frame["ticker"] = frame["ticker"].astype("string").str.zfill(6)
    return frame.drop_duplicates("ticker", keep="last")[["ticker", "sector"]]


def build_rule_dataset(
    database_path: str,
    project_config_path: str = "close_bet_staged/configs/default.json",
) -> pd.DataFrame:
    # Reuse the leakage-safe dataset builder while temporarily pointing it to the requested DB.
    raw = json.loads(Path(project_config_path).read_text(encoding="utf-8"))
    raw["database"]["path"] = str(Path(database_path).resolve())
    temporary = Path("reports/close_bet_staged/.rule_model_runtime_config.json")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        config = load_config(temporary)
        daily = load_daily(config.database.path, config.database.daily_table, config.universe_type)
        dataset = build_step4_dataset(daily, config)
        extended = build_daily_extended_features(daily).rename(columns={"date": "signal_date"})
        minute = load_selected_minute_bars(config.database.path)
        afternoon = build_sampled_afternoon_features(minute, daily).rename(
            columns={"date": "signal_date"}
        )
        dataset = dataset.merge(
            extended,
            on=["signal_date", "ticker", "universe_type"],
            how="left",
            validate="one_to_one",
        )
        dataset = dataset.merge(
            afternoon,
            on=["signal_date", "ticker"],
            how="left",
            validate="one_to_one",
        )
    finally:
        temporary.unlink(missing_ok=True)
    latest = dataset["signal_date"].max()
    frame = dataset[dataset["signal_date"].eq(latest)].copy()
    trade_date = pd.Timestamp(latest).strftime("%Y%m%d")
    sectors = _load_sector_map(database_path, trade_date)
    return frame.merge(sectors, on="ticker", how="left", validate="one_to_one")


def _persist(database_path: str, evaluated: pd.DataFrame) -> None:
    trade_date = pd.Timestamp(evaluated["signal_date"].iloc[0]).strftime("%Y%m%d")
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    columns = [
        "ticker", "name", "decision", "market_manual_state", "price_action_types",
        "liquidity_pass", "price_action_pass", "location_pass", "afternoon_flow_pass",
        "relative_strength_pass", "volatility_pass", "technical_pass", "final_pass",
        "stage_reason", "relative_strength_quality", "clv", "return_after_1430",
        "sampled_afternoon_clv", "rs5_sector_proxy", "rs20_market_proxy", "atr14_pct",
    ]
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS close_bet_rule_model_evaluations (
                trade_date TEXT, ticker TEXT, name TEXT, decision TEXT,
                market_manual_state TEXT, price_action_types TEXT,
                liquidity_pass INTEGER, price_action_pass INTEGER, location_pass INTEGER,
                afternoon_flow_pass INTEGER, relative_strength_pass INTEGER,
                volatility_pass INTEGER, technical_pass INTEGER, final_pass INTEGER,
                stage_reason TEXT, relative_strength_quality TEXT, clv REAL,
                return_after_1430 REAL, sampled_afternoon_clv REAL,
                rs5_sector_proxy REAL, rs20_market_proxy REAL, atr14_pct REAL,
                evaluated_at_kst TEXT, PRIMARY KEY (trade_date, ticker)
            )
            """
        )
        connection.execute(
            "DELETE FROM close_bet_rule_model_evaluations WHERE trade_date=?", (trade_date,)
        )
        values = []
        for row in evaluated[columns].itertuples(index=False, name=None):
            cleaned = tuple(
                None
                if pd.isna(value)
                else int(value)
                if isinstance(value, (bool, np.bool_))
                else value.item()
                if isinstance(value, np.generic)
                else value
                for value in row
            )
            values.append((trade_date, *cleaned, now))
        connection.executemany(
            "INSERT INTO close_bet_rule_model_evaluations VALUES (" + ",".join("?" * 23) + ")",
            values,
        )


def run(
    database_path: str,
    rule_config_path: str = "close_bet_staged/configs/rule_model.json",
    manual_market_pass: bool | None = None,
    persist: bool = False,
) -> pd.DataFrame:
    dataset = build_rule_dataset(database_path)
    evaluated = evaluate_rule_model(
        dataset, load_rule_config(rule_config_path), manual_market_pass=manual_market_pass
    )
    if persist:
        _persist(database_path, evaluated)
    return evaluated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--config", default="close_bet_staged/configs/rule_model.json")
    market = parser.add_mutually_exclusive_group()
    market.add_argument("--market-pass", action="store_true")
    market.add_argument("--market-block", action="store_true")
    parser.add_argument("--persist", action="store_true")
    parser.add_argument("--output", default="reports/close_bet_staged/rule_model_latest.csv")
    args = parser.parse_args()
    manual = True if args.market_pass else False if args.market_block else None
    evaluated = run(args.db, args.config, manual_market_pass=manual, persist=args.persist)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    evaluated.to_csv(args.output, index=False, encoding="utf-8-sig")
    summary = {
        "trade_date": str(evaluated["signal_date"].max().date()),
        "evaluated": len(evaluated),
        "technical_pass": int(evaluated["technical_pass"].sum()),
        "final_pass": int(evaluated["final_pass"].sum()),
        "market_state": evaluated["market_manual_state"].iloc[0],
        "output": args.output,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
