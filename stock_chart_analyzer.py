import json
import sqlite3

import plotly.graph_objects as go

from bottom_detector import BottomDetector
from model_schema import init_model_tables


DEFAULT_UNIVERSE_TYPES = (
    "market_cap_10000eok_plus",
    "market_cap_top_100",
    "custom_5000eok_plus",
)


class StockChartAnalyzer:
    def __init__(self, db_path="stock_data.db"):
        self.db_path = db_path
        init_model_tables(db_path)
        self.detector = BottomDetector(db_path=db_path)

    def resolve_stock(self, query, universe_types=DEFAULT_UNIVERSE_TYPES):
        query_text = str(query or "").strip()
        if not query_text:
            return None
        query_ticker = query_text.zfill(6) if query_text.isdigit() else None
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            params = []
            universe_filter = ""
            if universe_types:
                universe_filter = "AND universe_type IN ({})".format(
                    ",".join("?" for _ in universe_types)
                )
                params.extend(universe_types)

            if query_ticker:
                row = conn.execute(
                    f"""
                    SELECT ticker, MAX(name) AS name, universe_type, MAX(market_cap) AS market_cap
                    FROM model_ohlcv_daily
                    WHERE ticker = ? {universe_filter}
                    GROUP BY ticker, universe_type
                    ORDER BY CASE universe_type
                        WHEN 'market_cap_10000eok_plus' THEN 0
                        WHEN 'market_cap_top_100' THEN 1
                        ELSE 2
                    END
                    LIMIT 1
                    """,
                    [query_ticker] + params,
                ).fetchone()
                if row:
                    return dict(row)

            exact = conn.execute(
                f"""
                SELECT ticker, MAX(name) AS name, universe_type, MAX(market_cap) AS market_cap
                FROM model_ohlcv_daily
                WHERE name = ? {universe_filter}
                GROUP BY ticker, universe_type
                ORDER BY CASE universe_type
                    WHEN 'market_cap_10000eok_plus' THEN 0
                    WHEN 'market_cap_top_100' THEN 1
                    ELSE 2
                END
                LIMIT 1
                """,
                [query_text] + params,
            ).fetchone()
            if exact:
                return dict(exact)

            partial = conn.execute(
                f"""
                SELECT ticker, MAX(name) AS name, universe_type, MAX(market_cap) AS market_cap
                FROM model_ohlcv_daily
                WHERE name LIKE ? {universe_filter}
                GROUP BY ticker, universe_type
                ORDER BY market_cap DESC
                LIMIT 1
                """,
                [f"%{query_text}%"] + params,
            ).fetchone()
            return dict(partial) if partial else None

    def load_ohlcv(self, ticker, universe_type):
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT date, open, high, low, close, volume
                    FROM model_ohlcv_daily
                    WHERE ticker = ? AND universe_type = ?
                    ORDER BY date
                    """,
                    (ticker, universe_type),
                ).fetchall()
            ]

    def load_features(self, ticker, universe_type):
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT *
                    FROM model_feature_daily
                    WHERE ticker = ? AND universe_type = ?
                    ORDER BY date
                    """,
                    (ticker, universe_type),
                ).fetchall()
            ]

    def load_labels(self, ticker, universe_type, label_type="bottom_10d_5pct_safe"):
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return {
                row["date"]: dict(row)
                for row in conn.execute(
                    """
                    SELECT *
                    FROM model_backtest_labels
                    WHERE ticker = ? AND universe_type = ? AND label_type = ?
                    """,
                    (ticker, universe_type, label_type),
                ).fetchall()
            }

    def _previous_feature(self, features):
        return features[-2] if len(features) >= 2 else {}

    def _bottom_marker_dates(self, features, labels):
        markers = []
        for feature in features:
            if feature.get("rsi_14") is None or feature.get("distance_from_20d_low") is None:
                continue
            is_candidate = (
                feature.get("rsi_14") <= 45
                and feature.get("distance_from_20d_low") <= 8
                and (feature.get("return_20d") or 0) <= 0
            )
            if not is_candidate:
                continue
            label = labels.get(feature["date"], {})
            markers.append({
                "date": feature["date"],
                "success": bool(label.get("is_success")),
                "future_return_10d": label.get("future_return_10d"),
            })
        return markers

    def _moving_average(self, values, window):
        result = []
        for index in range(len(values)):
            start = index - window + 1
            if start < 0:
                result.append(None)
            else:
                result.append(sum(values[start:index + 1]) / window)
        return result

    def make_chart(self, stock, ohlcv, markers):
        dates = [row["date"] for row in ohlcv]
        closes = [row["close"] for row in ohlcv]
        lows_by_date = {row["date"]: row["low"] for row in ohlcv}
        fig = go.Figure()
        fig.add_trace(go.Candlestick(
            x=dates,
            open=[row["open"] for row in ohlcv],
            high=[row["high"] for row in ohlcv],
            low=[row["low"] for row in ohlcv],
            close=closes,
            name="Price",
        ))
        fig.add_trace(go.Scatter(x=dates, y=self._moving_average(closes, 20), name="MA20", line=dict(width=1.4)))
        fig.add_trace(go.Scatter(x=dates, y=self._moving_average(closes, 60), name="MA60", line=dict(width=1.4)))
        fig.add_trace(go.Scatter(x=dates, y=self._moving_average(closes, 120), name="MA120", line=dict(width=1.2)))

        success_dates = [item["date"] for item in markers if item["success"]]
        failed_dates = [item["date"] for item in markers if not item["success"]]
        if success_dates:
            fig.add_trace(go.Scatter(
                x=success_dates,
                y=[lows_by_date[date] * 0.985 for date in success_dates],
                mode="markers",
                name="Successful bottom",
                marker=dict(color="#18a058", size=10, symbol="triangle-up"),
            ))
        if failed_dates:
            fig.add_trace(go.Scatter(
                x=failed_dates,
                y=[lows_by_date[date] * 0.985 for date in failed_dates],
                mode="markers",
                name="Failed candidate",
                marker=dict(color="#d03050", size=8, symbol="x"),
            ))
        if dates:
            fig.add_trace(go.Scatter(
                x=[dates[-1]],
                y=[closes[-1]],
                mode="markers",
                name="Current",
                marker=dict(color="#f0a020", size=14, symbol="star"),
            ))
        fig.update_layout(
            title=f"{stock['name']} ({stock['ticker']})",
            height=620,
            xaxis_rangeslider_visible=False,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            margin=dict(l=20, r=20, t=70, b=30),
        )
        return fig

    def analyze(self, query):
        stock = self.resolve_stock(query)
        if stock is None:
            return None
        ohlcv = self.load_ohlcv(stock["ticker"], stock["universe_type"])
        features = self.load_features(stock["ticker"], stock["universe_type"])
        labels = self.load_labels(stock["ticker"], stock["universe_type"])
        if not ohlcv or not features:
            return None

        latest = features[-1]
        previous = self._previous_feature(features)
        context = self.detector._load_context(latest)
        if not context.get("market_regime"):
            with sqlite3.connect(self.db_path) as conn:
                fallback = conn.execute(
                    """
                    SELECT regime
                    FROM model_market_regimes
                    WHERE date = ? AND universe_type IN ('market_cap_10000eok_plus', 'market_cap_top_100')
                    ORDER BY CASE universe_type
                        WHEN 'market_cap_10000eok_plus' THEN 0
                        ELSE 1
                    END
                    LIMIT 1
                    """,
                    (latest["date"],),
                ).fetchone()
            if fallback:
                context["market_regime"] = fallback[0]
        chart_score, chart_reasons = self.detector.chart_agent.score(latest, previous)
        supply_score, supply_reasons = self.detector.supply_agent.score(context)
        sector_score, sector_reasons = self.detector.sector_market_agent.score(context)
        news_score, news_reasons = self.detector.news_agent.score(context)
        context["news_score"] = news_score
        risk_penalty, risk_reasons = self.detector.adversarial_agent.score(latest, context)
        total = max(0, min(100, (chart_score * 0.72) + supply_score + sector_score + news_score - risk_penalty))
        similar_win_rate, similar_count = self.detector._similar_pattern_stats(latest, stock["universe_type"])
        markers = self._bottom_marker_dates(features, labels)

        return {
            "stock": stock,
            "latest": latest,
            "score": round(total, 2),
            "grade": self.detector._grade(total),
            "chart_score": chart_score,
            "supply_score": supply_score,
            "sector_market_score": sector_score,
            "news_score": news_score,
            "risk_penalty": risk_penalty,
            "market_regime": context.get("market_regime"),
            "reasons": chart_reasons + supply_reasons + sector_reasons + news_reasons,
            "risk_reasons": risk_reasons,
            "similar_pattern_win_rate": None if similar_win_rate is None else round(similar_win_rate, 2),
            "similar_pattern_count": similar_count,
            "markers": markers,
            "figure": self.make_chart(stock, ohlcv, markers),
        }


def compact_reasons(items):
    return json.dumps(items, ensure_ascii=False)
