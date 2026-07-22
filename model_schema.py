import sqlite3


MODEL_TABLES = (
    "model_universe_snapshots",
    "model_ohlcv_daily",
    "model_feature_daily",
    "model_backtest_labels",
    "model_bottom_signals",
    "model_bottom_weight_runs",
    "model_market_regimes",
    "model_strategy_registry",
    "model_rule_scan_signals",
    "model_bottom_scan_runs",
)


def init_model_tables(db_path="stock_data.db"):
    """Create model-only tables separated from service dashboard tables."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS model_universe_snapshots (
                snapshot_date TEXT,
                universe_type TEXT,
                rank INTEGER,
                ticker TEXT,
                name TEXT,
                market_cap INTEGER,
                current_price INTEGER,
                listed_shares INTEGER,
                source TEXT,
                collected_at_kst TEXT,
                PRIMARY KEY (snapshot_date, universe_type, ticker)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS model_ohlcv_daily (
                date TEXT,
                ticker TEXT,
                name TEXT,
                open INTEGER,
                high INTEGER,
                low INTEGER,
                close INTEGER,
                volume INTEGER,
                trading_value INTEGER,
                change_rate REAL,
                market_cap INTEGER,
                universe_type TEXT,
                universe_snapshot_date TEXT,
                source TEXT,
                collected_at_kst TEXT,
                PRIMARY KEY (date, ticker, universe_type)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS model_feature_daily (
                date TEXT,
                ticker TEXT,
                universe_type TEXT,
                rsi_14 REAL,
                ma_5 REAL,
                ma_20 REAL,
                ma_60 REAL,
                ma_120 REAL,
                macd REAL,
                macd_signal REAL,
                macd_hist REAL,
                bb_upper REAL,
                bb_mid REAL,
                bb_lower REAL,
                bb_position REAL,
                obv REAL,
                obv_ma_20 REAL,
                volume_ratio_20 REAL,
                return_5d REAL,
                return_20d REAL,
                drawdown_20d REAL,
                distance_from_20d_low REAL,
                distance_from_60d_low REAL,
                distance_from_52w_high REAL,
                calculated_at_kst TEXT,
                PRIMARY KEY (date, ticker, universe_type)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS model_backtest_labels (
                date TEXT,
                ticker TEXT,
                universe_type TEXT,
                label_type TEXT,
                future_return_5d REAL,
                future_return_10d REAL,
                future_return_20d REAL,
                future_max_gain_10d REAL,
                future_max_gain_20d REAL,
                future_max_drawdown_10d REAL,
                future_max_drawdown_20d REAL,
                market_relative_return_10d REAL,
                is_success INTEGER,
                calculated_at_kst TEXT,
                PRIMARY KEY (date, ticker, universe_type, label_type)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS model_bottom_signals (
                signal_date TEXT,
                ticker TEXT,
                name TEXT,
                universe_type TEXT,
                current_price INTEGER,
                bottom_score REAL,
                grade TEXT,
                chart_score REAL,
                supply_score REAL,
                sector_market_score REAL,
                news_score REAL,
                risk_penalty REAL,
                reasons TEXT,
                risk_reasons TEXT,
                similar_pattern_win_rate REAL,
                similar_pattern_count INTEGER,
                market_regime TEXT,
                created_at_kst TEXT,
                PRIMARY KEY (signal_date, ticker, universe_type)
            )
            """
        )
        existing_bottom_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(model_bottom_signals)").fetchall()
        }
        if "market_regime" not in existing_bottom_columns:
            conn.execute("ALTER TABLE model_bottom_signals ADD COLUMN market_regime TEXT")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS model_bottom_weight_runs (
                run_id TEXT PRIMARY KEY,
                universe_type TEXT,
                label_type TEXT,
                train_end_date TEXT,
                test_start_date TEXT,
                candidate_count INTEGER,
                selected_count INTEGER,
                train_precision REAL,
                test_precision REAL,
                test_avg_return_10d REAL,
                test_avg_max_gain_10d REAL,
                test_avg_drawdown_10d REAL,
                weights_json TEXT,
                created_at_kst TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS model_market_regimes (
                date TEXT,
                universe_type TEXT,
                avg_return_20d REAL,
                rising_ratio_20d REAL,
                avg_drawdown_20d REAL,
                regime TEXT,
                calculated_at_kst TEXT,
                PRIMARY KEY (date, universe_type)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS model_strategy_registry (
                model_id TEXT PRIMARY KEY,
                model_name TEXT NOT NULL,
                model_type TEXT NOT NULL,
                status TEXT NOT NULL,
                source_db TEXT,
                parameters_json TEXT NOT NULL,
                validation_json TEXT NOT NULL,
                description TEXT,
                created_at_kst TEXT NOT NULL,
                updated_at_kst TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS model_rule_scan_signals (
                signal_date TEXT NOT NULL, model_id TEXT NOT NULL, ticker TEXT NOT NULL,
                name TEXT, universe_type TEXT NOT NULL, current_price REAL, change_rate REAL,
                market_cap REAL, trend_score REAL, rsi_14 REAL, volume_ratio REAL,
                entry_price REAL, stop_price REAL, first_target_price REAL,
                target_room_pct REAL, signal_reason TEXT, created_at_kst TEXT,
                PRIMARY KEY (signal_date, model_id, ticker, universe_type)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS model_bottom_scan_runs (
                signal_date TEXT NOT NULL,
                universe_type TEXT NOT NULL,
                status TEXT NOT NULL,
                universe_count INTEGER DEFAULT 0,
                bottom_signal_count INTEGER DEFAULT 0,
                rule_signal_count INTEGER DEFAULT 0,
                market_regime TEXT,
                error_message TEXT,
                completed_at_kst TEXT,
                PRIMARY KEY (signal_date, universe_type)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_model_ohlcv_ticker_date "
            "ON model_ohlcv_daily (ticker, date)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_model_feature_ticker_date "
            "ON model_feature_daily (ticker, date)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_model_labels_type "
            "ON model_backtest_labels (label_type, is_success)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_model_bottom_score "
            "ON model_bottom_signals (signal_date, bottom_score)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_model_rule_scan_date "
            "ON model_rule_scan_signals (signal_date, model_id)"
        )


if __name__ == "__main__":
    init_model_tables()
    print("Model tables are ready.")
