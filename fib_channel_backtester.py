"""Two-stage channel/Fibonacci backtest.

Stage 1 is deliberately independent from ML.  Stage 2 trains only on the
Stage-1 candidates and applies its filter strictly out-of-sample.
"""
import argparse
from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd


FEATURE_COLUMNS = [
    "close_to_channel_lower", "close_to_channel_upper", "channel_slope",
    "days_after_breakdown", "fib_382_crossed", "fib_500_near",
    "fib_618_near", "volume_ratio", "rsi_14", "ma_position",
    "return_5d", "volatility_20d",
]


class FibChannelBacktester:
    def __init__(self, db_path="stock_data.db", universe_type="market_cap_10000eok_plus", channel_window=20):
        self.db_path = db_path
        self.universe_type = universe_type
        self.channel_window = channel_window

    def load_prices(self):
        query = """
            SELECT date, ticker, name, open, high, low, close, volume, change_rate, market_cap
            FROM model_ohlcv_daily WHERE universe_type = ?
            ORDER BY ticker, date
        """
        with sqlite3.connect(self.db_path) as con:
            df = pd.read_sql_query(query, con, params=(self.universe_type,))
        if df.empty:
            raise ValueError("No model_ohlcv_daily data found for the selected universe.")
        df["date"] = pd.to_datetime(df["date"])
        for col in ("open", "high", "low", "close", "volume", "change_rate", "market_cap"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.dropna(subset=["high", "low", "close"])

    def candidates(self, prices, recovery_min=3, recovery_max=10, entry_fib=.382,
                   strategy="baseline", min_trend_score=3):
        """Create only the requested rule-based buy candidates, no ML involved."""
        all_candidates = []
        w = self.channel_window
        # A market proxy built solely from the same large-cap daily universe.
        # It is calculated per date, so no future market data leaks into signals.
        market_source = prices.sort_values(["ticker", "date"]).copy()
        market_source["market_ma20"] = market_source.groupby("ticker")["close"].transform(
            lambda s: s.rolling(20).mean()
        )
        market_source["market_ma20_slope"] = market_source.groupby("ticker")["market_ma20"].pct_change(
            5, fill_method=None
        )
        market_source["above_ma20"] = market_source.close > market_source.market_ma20
        market = market_source.groupby("date").agg(
            market_breadth=("above_ma20", "mean"), market_slope=("market_ma20_slope", "mean")
        )
        market_ok = (market.market_breadth >= .55) & (market.market_slope > 0)
        for ticker, g in prices.groupby("ticker", sort=False):
            g = g.sort_values("date").copy().reset_index(drop=True)
            # Shift avoids using today's high/low to decide today's channel breakout.
            g["channel_upper"] = g["high"].rolling(w).max().shift(1)
            g["channel_lower"] = g["low"].rolling(w).min().shift(1)
            g["channel_slope"] = g["channel_lower"].pct_change(5)
            g["ma_20"] = g["close"].rolling(20).mean()
            g["ma_5"] = g["close"].rolling(5).mean()
            delta = g["close"].diff()
            gain, loss = delta.clip(lower=0).rolling(14).mean(), (-delta.clip(upper=0)).rolling(14).mean()
            g["rsi_14"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
            g["volume_ratio"] = g["volume"] / g["volume"].rolling(20).mean()
            g["return_5d"] = g["close"].pct_change(5)
            g["volatility_20d"] = g["close"].pct_change().rolling(20).std()
            breakdown = (g["close"] < g["channel_lower"]).fillna(False)
            breakdown_indices = np.flatnonzero(breakdown.to_numpy())
            for i in range(w + 1, len(g)):
                previous = breakdown_indices[breakdown_indices < i]
                if len(previous) == 0:
                    continue
                b = previous[-1]
                days = i - b
                if not recovery_min <= days <= recovery_max:
                    continue
                upper = g.at[b, "channel_upper"]
                trough = g.loc[b:i, "low"].min()
                if not np.isfinite(upper) or upper <= trough:
                    continue
                fib382, fib500, fib618 = (trough + (upper - trough) * x for x in (.382, .5, .618))
                entry_level = trough + (upper - trough) * entry_fib
                row = g.loc[i].copy()
                # Recovery is evaluated after the breakdown and above the channel floor.
                if row["close"] <= row["channel_lower"] or row["close"] < entry_level:
                    continue
                row["breakdown_date"] = g.at[b, "date"]
                row["days_after_breakdown"] = days
                row["fib_382"], row["fib_500"], row["fib_618"] = fib382, fib500, fib618
                row["close_to_channel_lower"] = row["close"] / row["channel_lower"] - 1
                row["close_to_channel_upper"] = row["close"] / row["channel_upper"] - 1
                row["fib_382_crossed"] = int(row["close"] >= fib382)
                row["fib_500_near"] = int(abs(row["close"] / fib500 - 1) <= .015)
                row["fib_618_near"] = int(abs(row["close"] / fib618 - 1) <= .015)
                row["ma_position"] = row["close"] / row["ma_20"] - 1
                if strategy == "trend_confirmed":
                    previous = g.loc[i - 1]
                    higher_low = g.loc[max(b + 1, i - 2):i, "low"].min() > trough
                    confirmed = (
                        previous["close"] > previous["channel_lower"]
                        and row["close"] >= fib500 and row["close"] < fib618
                        and row["close"] > row["ma_20"]
                        and row["ma_20"] > g.at[i - 3, "ma_20"]
                        and row["rsi_14"] >= 50 and row["volume_ratio"] >= 1.2
                        and higher_low
                    )
                    if not confirmed:
                        continue
                elif strategy == "trend_score":
                    higher_low = g.loc[max(b + 1, i - 2):i, "low"].min() > trough
                    row["trend_score"] = sum((
                        row["close"] > row["ma_20"] and row["ma_20"] > g.at[i - 3, "ma_20"],
                        row["rsi_14"] >= 50,
                        row["volume_ratio"] >= 1.2,
                        higher_low,
                    ))
                    # A 61.8% target cannot be below the closing entry price.
                    if row["trend_score"] < min_trend_score or row["close"] >= fib618:
                        continue
                elif strategy == "market_trend_score":
                    higher_low = g.loc[max(b + 1, i - 2):i, "low"].min() > trough
                    row["trend_score"] = sum((
                        row["close"] > row["ma_20"] and row["ma_20"] > g.at[i - 3, "ma_20"],
                        row["rsi_14"] >= 50,
                        row["volume_ratio"] >= 1.2,
                        higher_low,
                    ))
                    target_room = fib618 / row["close"] - 1
                    if (row["trend_score"] < min_trend_score or not market_ok.get(row["date"], False)
                            or target_room < .03):
                        continue
                all_candidates.append(row)
        result = pd.DataFrame(all_candidates)
        return result.dropna(subset=FEATURE_COLUMNS).sort_values(["date", "ticker"]).reset_index(drop=True) if not result.empty else result

    @staticmethod
    def label_candidates(candidates, prices, horizon_days=5):
        """Success means +3% is reached before -3% after a closing-price entry."""
        closes = {t: g.sort_values("date").reset_index(drop=True) for t, g in prices.groupby("ticker")}
        labels = []
        for _, c in candidates.iterrows():
            g = closes[c.ticker]
            pos = g.index[g.date == c.date]
            future = g.iloc[pos[0] + 1:pos[0] + 1 + horizon_days] if len(pos) else g.iloc[0:0]
            label = 0
            for _, day in future.iterrows():
                stopped = day.low <= c.close * .97
                target = day.high >= c.close * 1.03
                # OHLC cannot tell which was first when both happen intraday;
                # count it as a failure to avoid optimistic labels.
                if stopped:
                    break
                if target:
                    label = 1
                    break
            labels.append(label)
        out = candidates.copy()
        out["label"] = labels
        return out

    @staticmethod
    def simulate(candidates, prices, probability_column=None, threshold=.60, max_positions=10,
                 strategy="baseline"):
        selected = candidates.copy()
        if probability_column:
            selected = selected[selected[probability_column] >= threshold]
        by_ticker = {}
        for ticker, group in prices.groupby("ticker"):
            group = group.sort_values("date").reset_index(drop=True).copy()
            group["ma_5"] = group.close.rolling(5).mean()
            by_ticker[ticker] = group
        trades = []
        next_entry_date = {}
        open_exit_dates = []
        for _, s in selected.sort_values(["ticker", "date"]).iterrows():
            # One position per ticker: signals while an earlier trade is open
            # are not independent trades and must not be counted twice.
            if s.date <= next_entry_date.get(s.ticker, pd.Timestamp.min):
                continue
            # A fixed-capital backtest needs a capacity limit.  Otherwise every
            # concurrent signal is incorrectly treated as a 100% investment.
            open_exit_dates = [d for d in open_exit_dates if d >= s.date]
            if len(open_exit_dates) >= max_positions:
                continue
            g = by_ticker[s.ticker]
            entry_pos = g.index[g.date == s.date]
            if not len(entry_pos):
                continue
            p = entry_pos[0]
            is_trend_strategy = strategy in ("trend_confirmed", "trend_score", "market_trend_score")
            max_holding_days = 10 if is_trend_strategy else 3
            future = g.iloc[p + 1:p + max_holding_days + 1]
            if future.empty:
                continue
            exit_row, reason = future.iloc[-1], "hold_10_days" if is_trend_strategy else "hold_3_days"
            first_target_hit = False
            for _, day in future.iterrows():
                if day.low / s.close - 1 <= -.03:
                    exit_row, reason = day, "stop_loss_-3pct"
                    break
                if strategy == "baseline" and day.high >= s.fib_618:
                    exit_row, reason = day, "fib_618"
                    break
                if is_trend_strategy:
                    if not first_target_hit and day.high >= s.fib_618:
                        first_target_hit = True
                    elif first_target_hit and day.close < day.ma_5:
                        exit_row, reason = day, "fib_618_partial_ma5_exit"
                        break
            # Do not count an unfinished last trade as if it were a timed exit.
            if len(future) < max_holding_days and reason.startswith("hold_"):
                continue
            if reason == "stop_loss_-3pct":
                # If half was already realised at 61.8%, only the remaining
                # half is exposed to the hard stop.
                exit_price = (s.close * .97 if not first_target_hit
                              else .5 * s.fib_618 + .5 * s.close * .97)
            elif reason == "fib_618":
                exit_price = s.fib_618
            elif is_trend_strategy and first_target_hit:
                exit_price = .5 * s.fib_618 + .5 * exit_row.close
            else:
                exit_price = exit_row.close
            next_entry_date[s.ticker] = exit_row.date
            open_exit_dates.append(exit_row.date)
            trades.append({"entry_date": s.date.date(), "exit_date": exit_row.date.date(), "ticker": s.ticker,
                           "name": s.get("name", ""), "entry_price": s.close, "exit_price": exit_price,
                           "return_pct": (exit_price / s.close - 1) * 100, "exit_reason": reason,
                           "ai_probability": s.get(probability_column, np.nan)})
        trades = pd.DataFrame(trades)
        if trades.empty:
            return trades, {"trades": 0, "win_rate_pct": np.nan, "total_return_pct": np.nan, "mdd_pct": np.nan}
        trades = trades.sort_values(["entry_date", "ticker"]).reset_index(drop=True)
        entry_dates = pd.to_datetime(trades.entry_date)
        exit_dates = pd.to_datetime(trades.exit_date)
        close_lookup = prices.set_index(["date", "ticker"])["close"]
        equity_values = []
        for day in sorted(prices.date.unique()):
            pnl = 0.0
            for idx, trade in trades.iterrows():
                if day < entry_dates.iat[idx]:
                    continue
                if day >= exit_dates.iat[idx]:
                    mark = trade.exit_price
                else:
                    mark = close_lookup.get((day, trade.ticker), trade.entry_price)
                pnl += (mark / trade.entry_price - 1) / max_positions
            equity_values.append(1 + pnl)
        equity = pd.Series(equity_values)
        mdd = (equity / equity.cummax() - 1).min() * 100
        return trades, {"trades": len(trades), "win_rate_pct": (trades.return_pct > 0).mean() * 100,
                        "total_return_pct": (equity.iat[-1] - 1) * 100, "mdd_pct": mdd}

    def run(self, output_dir="reports", threshold=.60, strategy="market_trend_score", min_trend_score=2):
        from sklearn.ensemble import RandomForestClassifier
        prices = self.load_prices()
        candidates = self.label_candidates(
            self.candidates(prices, strategy=strategy, min_trend_score=min_trend_score), prices
        )
        if len(candidates) < 20 or candidates.date.nunique() < 2:
            raise ValueError("Not enough rule candidates to perform a chronological AI split.")
        split_date = candidates.date.drop_duplicates().sort_values().iloc[int(candidates.date.nunique() * .7)]
        train, test = candidates[candidates.date < split_date], candidates[candidates.date >= split_date].copy()
        if train.label.nunique() < 2:
            raise ValueError("Training candidates contain one class only; collect more history.")
        # Keep observed class prevalence; the probability threshold, rather than
        # class rebalancing, is what makes this a conservative failure filter.
        model = RandomForestClassifier(n_estimators=400, min_samples_leaf=10, random_state=42, n_jobs=-1)
        model.fit(train[FEATURE_COLUMNS], train.label)
        test["ai_probability"] = model.predict_proba(test[FEATURE_COLUMNS])[:, 1]
        rule_trades, rule_metrics = self.simulate(test, prices, strategy=strategy)
        ai_trades, ai_metrics = self.simulate(test, prices, "ai_probability", threshold, strategy=strategy)
        # The label is the requested +3% within three trading days definition.
        # These fields make the reduction in failed candidates directly visible.
        for subset, metrics in ((test, rule_metrics), (test[test.ai_probability >= threshold], ai_metrics)):
            metrics["candidate_success_pct"] = subset.label.mean() * 100 if len(subset) else np.nan
            metrics["candidate_failure_pct"] = (1 - subset.label.mean()) * 100 if len(subset) else np.nan
        out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
        rule_trades.to_csv(out / "fib_channel_rule_trades.csv", index=False, encoding="utf-8-sig")
        ai_trades.to_csv(out / "fib_channel_ai_trades.csv", index=False, encoding="utf-8-sig")
        comparison = pd.DataFrame([{"strategy": "rule_based", **rule_metrics}, {"strategy": f"ai_filter_probability_gte_{threshold:.2f}", **ai_metrics}])
        comparison.to_csv(out / "fib_channel_comparison.csv", index=False, encoding="utf-8-sig")
        return comparison, candidates, test

    def run_rule_only(self, output_dir="reports", recovery_min=3, recovery_max=10, entry_fib=.382,
                      signal_start=None, signal_end=None, strategy="baseline", min_trend_score=3):
        """Phase 1 entry point: no labels, model, or sklearn dependency."""
        prices = self.load_prices()
        candidates = self.candidates(prices, recovery_min, recovery_max, entry_fib, strategy, min_trend_score)
        if signal_start:
            candidates = candidates[candidates.date >= pd.Timestamp(signal_start)]
        if signal_end:
            candidates = candidates[candidates.date <= pd.Timestamp(signal_end)]
        trades, metrics = self.simulate(candidates, prices, strategy=strategy)
        out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
        trades.to_csv(out / "fib_channel_rule_trades.csv", index=False, encoding="utf-8-sig")
        pd.DataFrame([{"strategy": "rule_based", **metrics}]).to_csv(
            out / "fib_channel_rule_summary.csv", index=False, encoding="utf-8-sig"
        )
        return metrics, candidates


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db-path", default="stock_data.db"); p.add_argument("--universe-type", default="market_cap_10000eok_plus")
    p.add_argument("--channel-window", type=int, default=20)
    p.add_argument("--output-dir", default="reports"); p.add_argument("--threshold", type=float, default=.60)
    p.add_argument("--recovery-min", type=int, default=3); p.add_argument("--recovery-max", type=int, default=10)
    # Entry must remain below the 61.8% profit target.
    p.add_argument("--entry-fib", type=float, default=.382, choices=(.382, .5))
    p.add_argument("--signal-start"); p.add_argument("--signal-end")
    p.add_argument("--strategy", choices=("baseline", "trend_confirmed", "trend_score", "market_trend_score"), default="baseline")
    p.add_argument("--min-trend-score", type=int, choices=(2, 3, 4), default=3)
    p.add_argument("--phase", choices=("rule", "ai"), default="rule",
                   help="Run and confirm rule backtest first; run ai only afterwards.")
    args = p.parse_args()
    engine = FibChannelBacktester(args.db_path, args.universe_type, args.channel_window)
    if args.phase == "rule":
        result, candidates = engine.run_rule_only(args.output_dir, args.recovery_min, args.recovery_max, args.entry_fib,
                                                  args.signal_start, args.signal_end, args.strategy, args.min_trend_score)
        print(pd.DataFrame([result]).to_string(index=False)); print(f"rule candidates={len(candidates)}")
    else:
        result, candidates, test = engine.run(args.output_dir, args.threshold, args.strategy, args.min_trend_score)
        print(result.to_string(index=False)); print(f"rule candidates={len(candidates)}, out-of-sample candidates={len(test)}")


if __name__ == "__main__":
    main()
