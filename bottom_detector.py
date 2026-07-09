import argparse
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from model_schema import init_model_tables


KST = timezone(timedelta(hours=9))


def _score_between(value, low, high, score):
    if value is None:
        return 0
    return score if low <= value <= high else 0


def _score_lte(value, threshold, score):
    if value is None:
        return 0
    return score if value <= threshold else 0


def _score_gte(value, threshold, score):
    if value is None:
        return 0
    return score if value >= threshold else 0


class ChartAgent:
    def score(self, feature, previous):
        score = 0
        reasons = []

        rsi = feature.get("rsi_14")
        if rsi is not None:
            if 30 <= rsi <= 45:
                score += 18
                reasons.append(f"RSI {rsi:.1f}: 과매도권 이후 반등 대기 구간")
            elif rsi < 30:
                score += 12
                reasons.append(f"RSI {rsi:.1f}: 강한 과매도")
            elif rsi <= 50:
                score += 8
                reasons.append(f"RSI {rsi:.1f}: 아직 부담 낮은 구간")

        distance_20 = feature.get("distance_from_20d_low")
        distance_60 = feature.get("distance_from_60d_low")
        if distance_20 is not None and distance_20 <= 6:
            score += 16
            reasons.append(f"20일 저점 대비 {distance_20:.1f}%: 저점권")
        if distance_60 is not None and distance_60 <= 10:
            score += 12
            reasons.append(f"60일 저점 대비 {distance_60:.1f}%: 중기 저점권")

        return_20d = feature.get("return_20d")
        if return_20d is not None and return_20d <= -10:
            score += 14
            reasons.append(f"20일 수익률 {return_20d:.1f}%: 충분한 가격 조정")
        elif return_20d is not None and return_20d <= -5:
            score += 8
            reasons.append(f"20일 수익률 {return_20d:.1f}%: 단기 조정")

        bb_position = feature.get("bb_position")
        if bb_position is not None and 0.05 <= bb_position <= 0.35:
            score += 12
            reasons.append(f"볼린저 위치 {bb_position:.2f}: 하단권 복귀")

        volume_ratio = feature.get("volume_ratio_20")
        if volume_ratio is not None and volume_ratio >= 1.2:
            score += 12
            reasons.append(f"거래량 {volume_ratio:.2f}배: 저점권 관심 증가")
        elif volume_ratio is not None and volume_ratio >= 0.8:
            score += 6
            reasons.append(f"거래량 {volume_ratio:.2f}배: 거래량 유지")

        macd_hist = feature.get("macd_hist")
        previous_macd_hist = previous.get("macd_hist") if previous else None
        if macd_hist is not None and previous_macd_hist is not None and macd_hist > previous_macd_hist:
            score += 10
            reasons.append("MACD 히스토그램 개선: 하락 압력 둔화")

        obv = feature.get("obv")
        obv_ma_20 = feature.get("obv_ma_20")
        if obv is not None and obv_ma_20 is not None and obv >= obv_ma_20:
            score += 6
            reasons.append("OBV가 20일 평균 이상: 거래량 흐름 방어")

        return min(score, 100), reasons


class SupplyAgent:
    def score(self, context):
        flow = context.get("daily_flow")
        if not flow:
            return 0, []
        score = 0
        reasons = []
        foreign_net = flow.get("foreign_net") or 0
        inst_net = flow.get("inst_net") or 0
        trading_value = flow.get("trading_value") or 0
        if foreign_net > 0:
            score += 10
            reasons.append("외국인 순매수 유입")
        if inst_net > 0:
            score += 10
            reasons.append("기관 순매수 유입")
        if foreign_net + inst_net > 0 and trading_value > 0:
            ratio = (foreign_net + inst_net) / trading_value * 100
            if ratio >= 2:
                score += 10
                reasons.append(f"합산 수급/거래대금 {ratio:.1f}%")
        return min(score, 30), reasons


class SectorMarketAgent:
    def score(self, context):
        score = 0
        reasons = []
        regime = context.get("market_regime")
        if regime == "strong_uptrend":
            score += 8
            reasons.append("시장 레짐 strong_uptrend: 바닥 반등 성공률 우호적")
        elif regime == "uptrend":
            score += 5
            reasons.append("시장 레짐 uptrend: 우호적")
        elif regime == "strong_downtrend":
            score -= 14
            reasons.append("시장 레짐 strong_downtrend: 바닥 실패 위험 높음")
        elif regime == "high_risk_pullback":
            score -= 12
            reasons.append("시장 레짐 high_risk_pullback: 낙폭 확대 위험")
        elif regime == "downtrend":
            score -= 6
            reasons.append("시장 레짐 downtrend: 보수적 접근")
        market = context.get("market_strength")
        sector = context.get("sector_flow")
        if market is not None:
            market_score = market.get("market_strength_score")
            if market_score is not None and market_score >= 55:
                score += 10
                reasons.append(f"시장강도 {market_score}: 우호적")
            elif market_score is not None and market_score < 40:
                score -= 8
                reasons.append(f"시장강도 {market_score}: 약세")
        if sector is not None:
            if (sector.get("relative_signed_flow") or 0) > 0:
                score += 8
                reasons.append("섹터 자금 흐름 양호")
            if (sector.get("sector_return") or 0) > 0:
                score += 5
                reasons.append("섹터 수익률 플러스")
        return max(-10, min(score, 25)), reasons


class NewsAgent:
    def score(self, context):
        items = context.get("news") or []
        if not items:
            return 0, []
        total = sum(item.get("sentiment_score") or 0 for item in items)
        reasons = []
        if total > 0:
            reasons.append(f"뉴스 감성 합계 +{total}")
        elif total < 0:
            reasons.append(f"뉴스 감성 합계 {total}")
        return max(-20, min(total * 3, 20)), reasons


class AdversarialAgent:
    def score(self, feature, context):
        penalty = 0
        risks = []
        return_20d = feature.get("return_20d")
        distance_20 = feature.get("distance_from_20d_low")
        volume_ratio = feature.get("volume_ratio_20")
        rsi = feature.get("rsi_14")
        drawdown_20d = feature.get("drawdown_20d")

        if return_20d is not None and return_20d <= -20:
            penalty += 12
            risks.append(f"20일 급락 {return_20d:.1f}%: 하락 추세 지속 위험")
        if distance_20 is not None and distance_20 <= 1.0:
            penalty += 8
            risks.append("20일 저점에 너무 근접: 지지선 이탈 확인 필요")
        if volume_ratio is not None and volume_ratio < 0.6:
            penalty += 10
            risks.append(f"거래량 {volume_ratio:.2f}배: 반등 에너지 부족")
        if rsi is not None and rsi < 25:
            penalty += 8
            risks.append(f"RSI {rsi:.1f}: 과매도 지속 위험")
        if drawdown_20d is not None and drawdown_20d <= -18:
            penalty += 8
            risks.append(f"20일 고점 대비 {drawdown_20d:.1f}%: 낙폭 과대 리스크")

        news_score = context.get("news_score", 0)
        if news_score < 0:
            penalty += abs(news_score)
            risks.append("부정 뉴스 감점")
        regime = context.get("market_regime")
        if regime == "strong_downtrend":
            penalty += 14
            risks.append("강한 하락장 레짐: 데드캣 바운스 위험")
        elif regime == "high_risk_pullback":
            penalty += 12
            risks.append("고위험 조정 레짐: 추가 하락 위험")
        elif regime == "downtrend":
            penalty += 6
            risks.append("하락장 레짐: 추세 지속 위험")
        return min(penalty, 40), risks


class BottomDetector:
    def __init__(self, db_path="stock_data.db"):
        self.db_path = db_path
        self.created_at_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
        init_model_tables(db_path=db_path)
        self.chart_agent = ChartAgent()
        self.supply_agent = SupplyAgent()
        self.sector_market_agent = SectorMarketAgent()
        self.news_agent = NewsAgent()
        self.adversarial_agent = AdversarialAgent()

    def _latest_signal_date(self, universe_type):
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute(
                "SELECT MAX(date) FROM model_feature_daily WHERE universe_type = ?",
                (universe_type,),
            ).fetchone()[0]

    def _load_features(self, signal_date, universe_type):
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT
                    f.*, o.name, o.close
                FROM model_feature_daily f
                JOIN model_ohlcv_daily o
                  ON o.date = f.date
                 AND o.ticker = f.ticker
                 AND o.universe_type = f.universe_type
                WHERE f.date = ? AND f.universe_type = ?
                """,
                (signal_date, universe_type),
            ).fetchall()
            columns = [item[1] for item in conn.execute("PRAGMA table_info(model_feature_daily)").fetchall()]
        feature_count = len(columns)
        result = []
        for row in rows:
            item = dict(zip(columns, row[:feature_count]))
            item["name"] = row[feature_count]
            item["current_price"] = row[feature_count + 1]
            result.append(item)
        return result

    def _load_previous_features(self, signal_date, universe_type):
        with sqlite3.connect(self.db_path) as conn:
            previous_date = conn.execute(
                """
                SELECT MAX(date)
                FROM model_feature_daily
                WHERE universe_type = ? AND date < ?
                """,
                (universe_type, signal_date),
            ).fetchone()[0]
            if not previous_date:
                return {}
            rows = conn.execute(
                "SELECT * FROM model_feature_daily WHERE date = ? AND universe_type = ?",
                (previous_date, universe_type),
            ).fetchall()
            columns = [item[1] for item in conn.execute("PRAGMA table_info(model_feature_daily)").fetchall()]
        return {row[1]: dict(zip(columns, row)) for row in rows}

    def _load_context(self, feature):
        ticker = feature["ticker"]
        date = feature["date"]
        with sqlite3.connect(self.db_path) as conn:
            daily_flow = conn.execute(
                """
                SELECT foreign_net, inst_net, trading_value, sector
                FROM daily_stocks
                WHERE date = ? AND ticker = ?
                ORDER BY CASE WHEN category = 'VOLUME_TOP_60' THEN 0 ELSE 1 END
                LIMIT 1
                """,
                (date, ticker),
            ).fetchone()
            market = conn.execute(
                """
                SELECT market_strength_score
                FROM market_strength_snapshots
                WHERE trade_date = ?
                ORDER BY snapshot_time DESC
                LIMIT 1
                """,
                (date,),
            ).fetchone()
            news_rows = conn.execute(
                """
                SELECT sentiment_score, title
                FROM stock_news
                WHERE date = ? AND ticker = ?
                """,
                (date, ticker),
            ).fetchall()
            regime = conn.execute(
                """
                SELECT regime
                FROM model_market_regimes
                WHERE date = ? AND universe_type = ?
                """,
                (date, feature["universe_type"]),
            ).fetchone()

            sector_flow = None
            if daily_flow and daily_flow[3]:
                sector_flow = conn.execute(
                    """
                    SELECT relative_signed_flow, sector_return
                    FROM sector_flow_windows
                    WHERE trade_date = ? AND sector = ?
                    ORDER BY window_key DESC
                    LIMIT 1
                    """,
                    (date, daily_flow[3]),
                ).fetchone()

        return {
            "daily_flow": None if not daily_flow else {
                "foreign_net": daily_flow[0],
                "inst_net": daily_flow[1],
                "trading_value": daily_flow[2],
                "sector": daily_flow[3],
            },
            "market_strength": None if not market else {"market_strength_score": market[0]},
            "sector_flow": None if not sector_flow else {
                "relative_signed_flow": sector_flow[0],
                "sector_return": sector_flow[1],
            },
            "news": [{"sentiment_score": row[0], "title": row[1]} for row in news_rows],
            "market_regime": None if not regime else regime[0],
        }

    def _similar_pattern_stats(self, feature, universe_type):
        conditions = []
        params = [universe_type, "bottom_10d_5pct_safe"]

        if feature.get("rsi_14") is not None:
            conditions.append("ABS(f.rsi_14 - ?) <= 5")
            params.append(feature["rsi_14"])
        if feature.get("return_20d") is not None:
            conditions.append("ABS(f.return_20d - ?) <= 5")
            params.append(feature["return_20d"])
        if feature.get("distance_from_20d_low") is not None:
            conditions.append("ABS(f.distance_from_20d_low - ?) <= 3")
            params.append(feature["distance_from_20d_low"])

        if not conditions:
            return None, 0

        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                f"""
                SELECT COUNT(*), SUM(l.is_success)
                FROM model_feature_daily f
                JOIN model_backtest_labels l
                  ON l.date = f.date
                 AND l.ticker = f.ticker
                 AND l.universe_type = f.universe_type
                WHERE f.universe_type = ?
                  AND l.label_type = ?
                  AND f.date < ?
                  AND {' AND '.join(conditions)}
                """,
                params[:2] + [feature["date"]] + params[2:],
            ).fetchone()
        count = row[0] or 0
        wins = row[1] or 0
        return (None if count == 0 else wins * 100 / count), count

    def _grade(self, score):
        if score >= 85:
            return "강한 바닥 후보"
        if score >= 70:
            return "관심 후보"
        if score >= 55:
            return "관찰"
        return "제외"

    def _save_signals(self, signal_date, universe_type, rows):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                DELETE FROM model_bottom_signals
                WHERE signal_date = ? AND universe_type = ?
                """,
                (signal_date, universe_type),
            )
            for row in rows:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO model_bottom_signals (
                        signal_date, ticker, name, universe_type, current_price,
                        bottom_score, grade, chart_score, supply_score,
                        sector_market_score, news_score, risk_penalty, reasons,
                        risk_reasons, similar_pattern_win_rate,
                        similar_pattern_count, market_regime, created_at_kst
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["signal_date"], row["ticker"], row["name"], row["universe_type"],
                        row["current_price"], row["bottom_score"], row["grade"],
                        row["chart_score"], row["supply_score"], row["sector_market_score"],
                        row["news_score"], row["risk_penalty"], row["reasons"],
                        row["risk_reasons"], row["similar_pattern_win_rate"],
                        row["similar_pattern_count"], row["market_regime"],
                        self.created_at_kst,
                    ),
                )

    def run(self, universe_type="market_cap_10000eok_plus", signal_date=None, min_score=55):
        signal_date = signal_date or self._latest_signal_date(universe_type)
        features = self._load_features(signal_date, universe_type)
        previous_by_ticker = self._load_previous_features(signal_date, universe_type)
        signals = []

        for feature in features:
            previous = previous_by_ticker.get(feature["ticker"], {})
            context = self._load_context(feature)
            chart_score, chart_reasons = self.chart_agent.score(feature, previous)
            supply_score, supply_reasons = self.supply_agent.score(context)
            sector_score, sector_reasons = self.sector_market_agent.score(context)
            news_score, news_reasons = self.news_agent.score(context)
            context["news_score"] = news_score
            risk_penalty, risk_reasons = self.adversarial_agent.score(feature, context)

            total = (chart_score * 0.72) + supply_score + sector_score + news_score - risk_penalty
            total = max(0, min(100, total))
            grade = self._grade(total)
            if total < min_score:
                continue

            similar_win_rate, similar_count = self._similar_pattern_stats(feature, universe_type)
            reasons = chart_reasons + supply_reasons + sector_reasons + news_reasons
            signals.append({
                "signal_date": signal_date,
                "ticker": feature["ticker"],
                "name": feature["name"],
                "universe_type": universe_type,
                "current_price": feature["current_price"],
                "bottom_score": round(total, 2),
                "grade": grade,
                "chart_score": round(chart_score, 2),
                "supply_score": round(supply_score, 2),
                "sector_market_score": round(sector_score, 2),
                "news_score": round(news_score, 2),
                "risk_penalty": round(risk_penalty, 2),
                "reasons": json.dumps(reasons, ensure_ascii=False),
                "risk_reasons": json.dumps(risk_reasons, ensure_ascii=False),
                "similar_pattern_win_rate": None if similar_win_rate is None else round(similar_win_rate, 2),
                "similar_pattern_count": similar_count,
                "market_regime": context.get("market_regime"),
            })

        signals.sort(key=lambda item: item["bottom_score"], reverse=True)
        self._save_signals(signal_date, universe_type, signals)
        return signals


def main():
    parser = argparse.ArgumentParser(description="Generate model-only bottom candidate signals.")
    parser.add_argument("--db-path", default="stock_data.db")
    parser.add_argument("--universe-type", default="market_cap_10000eok_plus")
    parser.add_argument("--signal-date")
    parser.add_argument("--min-score", type=float, default=55)
    args = parser.parse_args()

    detector = BottomDetector(db_path=args.db_path)
    signals = detector.run(
        universe_type=args.universe_type,
        signal_date=args.signal_date,
        min_score=args.min_score,
    )
    print(f"[BottomDetector] signals={len(signals)}")
    for row in signals[:20]:
        print(
            f"{row['signal_date']} {row['ticker']} {row['name']} "
            f"score={row['bottom_score']} grade={row['grade']} "
            f"chart={row['chart_score']} risk={row['risk_penalty']} "
            f"regime={row['market_regime']} "
            f"similar={row['similar_pattern_win_rate']}%/{row['similar_pattern_count']}"
        )


if __name__ == "__main__":
    main()
