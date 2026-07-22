import os
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from crawler import StockCrawler
from analyzer import StockAnalyzer
from excel_manager import ExcelManager
from news_collector import NewsCollector
from market_strength import MarketStrengthAnalyzer
from close_bet_scanner import CloseBetScanner
from close_bet_model3_scanner import CloseBetModel3Scanner
from close_bet_staged.rule_model_runner import run as run_close_bet_rule_model
from intraday_relative_strength import IntradayRelativeStrengthScanner
from bottom_detector import BottomDetector
from model_1_scanner import save_model_scan_history
from model_data_collector import ModelDataCollector
from model_features import ModelFeatureBuilder
from model_regime import MarketRegimeBuilder
from model_schema import init_model_tables


def run_market_strength(access_token=None):
    market_strength = MarketStrengthAnalyzer.from_environment()
    if access_token:
        market_strength.access_token = access_token
    return market_strength.run()


def report_market_strength_error(error):
    message = str(error).replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
    print(f"::error title=Market Strength Analysis Failed::{message}")


def run_bottom_model():
    universe_type = "market_cap_10000eok_plus"
    status_path = Path("reports") / "bottom_model_status.json"
    status_path.parent.mkdir(exist_ok=True)
    print("\n[Bottom Model] Collecting 180-day data for stocks over 1T KRW market cap.")
    status = {
        "status": "started",
        "universe_type": universe_type,
        "lookback_days": 180,
        "started_at_kst": datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="seconds"),
    }
    init_model_tables("stock_data.db")
    signal_date = datetime.now(timezone(timedelta(hours=9))).strftime("%Y%m%d")
    market_regime = None
    rule_signal_count = 0
    try:
        collector = ModelDataCollector(db_path="stock_data.db")
        summary = collector.collect_market_cap_threshold_ohlcv(
            min_market_cap=1_000_000_000_000,
            universe_type=universe_type,
            lookback_days=180,
        )
        status["collection"] = {
            "universe_count": summary["universe_count"],
            "ohlcv_rows": summary["ohlcv_rows"],
            "full_refresh_count": summary["full_refresh_count"],
            "incremental_count": summary["incremental_count"],
            "failure_count": len(summary["failures"]),
        }
        print(
            "[Bottom Model] Collected: "
            f"universe={summary['universe_count']}, "
            f"ohlcv_rows={summary['ohlcv_rows']}, "
            f"full={summary['full_refresh_count']}, "
            f"incremental={summary['incremental_count']}, "
            f"failures={len(summary['failures'])}"
        )
        ModelFeatureBuilder(db_path="stock_data.db").run(universe_type)
        MarketRegimeBuilder(db_path="stock_data.db").run(universe_type)
        signals = BottomDetector(db_path="stock_data.db").run(universe_type=universe_type, min_score=40)
        rule_signal_count = save_model_scan_history("stock_data.db", universe_type=universe_type)
        print(f"[Rule Bottom Models] Saved {rule_signal_count} date-fixed signals.")
        with sqlite3.connect("stock_data.db") as conn:
            latest = conn.execute(
                "SELECT MAX(date) FROM model_feature_daily WHERE universe_type=?",
                (universe_type,),
            ).fetchone()[0]
            if latest:
                signal_date = str(latest)
            regime_row = conn.execute(
                "SELECT regime FROM model_market_regimes WHERE date=? AND universe_type=?",
                (signal_date, universe_type),
            ).fetchone()
            market_regime = regime_row[0] if regime_row else None
        status["status"] = "success"
        status["signal_count"] = 0 if signals is None else len(signals)
        status["rule_signal_count"] = rule_signal_count
        status["signal_date"] = signal_date
        status["market_regime"] = market_regime
        print("[Bottom Model] Done.")
    except Exception as error:
        status["status"] = "failure"
        status["error"] = str(error)
        print(f"[Bottom Model Error] {error}")
        raise
    finally:
        finished_at = datetime.now(timezone(timedelta(hours=9)))
        status["finished_at_kst"] = finished_at.isoformat(timespec="seconds")
        status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        with sqlite3.connect("stock_data.db") as conn:
            conn.execute(
                """INSERT OR REPLACE INTO model_bottom_scan_runs
                (signal_date, universe_type, status, universe_count,
                 bottom_signal_count, rule_signal_count, market_regime,
                 error_message, completed_at_kst)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    signal_date,
                    universe_type,
                    status["status"],
                    int(status.get("collection", {}).get("universe_count", 0)),
                    int(status.get("signal_count", 0)),
                    int(rule_signal_count),
                    market_regime,
                    status.get("error"),
                    finished_at.strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )


def main():
    print("=== 주식 분석 자동화 시스템 시작 ===")

    scheduled_cron = os.environ.get("GITHUB_EVENT_SCHEDULE", "").strip()
    run_mode = os.environ.get("ANALYSIS_RUN_MODE", "").strip()

    if run_mode == "market_strength_only":
        print("\n[Market Strength Only] 수동 시장강도 데이터를 수집합니다.")
        run_market_strength()
        print("\n=== 수동 시장강도 분석이 완료되었습니다! ===")
        return

    if run_mode == "bottom_model":
        run_bottom_model()
        print("\n=== Bottom candidate model completed. ===")
        return

    use_latest_regular_data = False
    if run_mode == "full":
        analysis_started_at = datetime.now(timezone(timedelta(hours=9))).replace(second=0, microsecond=0)
        is_live_market_window = (
            analysis_started_at.weekday() < 5
            and 9 <= analysis_started_at.hour < 18
        )
        use_latest_regular_data = not is_live_market_window
        if use_latest_regular_data:
            os.environ["MARKET_STRENGTH_MODE"] = "closing"
            os.environ["MARKET_STRENGTH_REQUESTED_AT_KST"] = ""
        else:
            os.environ["MARKET_STRENGTH_MODE"] = "manual"
            os.environ["MARKET_STRENGTH_REQUESTED_AT_KST"] = analysis_started_at.isoformat(timespec="minutes")
        print(f"[Manual Analysis] 전체 분석 기준시각(KST): {analysis_started_at:%Y-%m-%d %H:%M}")
    
    # 1. 크롤링 및 DB 누적 저장
    print("\n[Step 1] 데이터 크롤링을 시작합니다.")
    crawler = StockCrawler()
    if use_latest_regular_data:
        print("[Manual Analysis] 장 운영시간이 아니므로 최신 정규장 DB를 기준으로 후속 분석을 실행합니다.")
    elif crawler.run() is False:
        print("[Skip] 시간외 데이터는 분석 대상에서 제외되어 이후 단계를 실행하지 않습니다.")
        return

    print("\n[Step 1-0-0] 장중 지수 대비 상대강도를 증분 분석합니다.")
    if use_latest_regular_data:
        print("[Intraday RS] 비거래일 수동 실행에서는 당일 분봉 수집을 건너뜁니다.")
    else:
        try:
            IntradayRelativeStrengthScanner(crawler).run()
        except Exception as e:
            print(f"[Intraday RS Warning] 상대강도 분석 오류가 발생했지만 주 분석은 계속합니다: {e}")

    print("\n[Step 1-0] 종가베팅 스캐너를 실행합니다.")
    try:
        CloseBetScanner(crawler).run()
        rule_result = run_close_bet_rule_model(crawler.db_path, persist=True)
        print(
            f"[Close Bet Rule Model] technical_pass="
            f"{int(rule_result['technical_pass'].sum())}/{len(rule_result)} "
            "market_state=pending"
        )
    except Exception as e:
        print(f"[Close Bet Warning] 종가베팅 스캔 중 오류가 발생했지만 주 분석은 계속합니다: {e}")

    print("\n[Step 1-0-3] 종가베팅 모델 3 스캔을 실행합니다.")
    try:
        CloseBetModel3Scanner(crawler).run()
    except Exception as e:
        print(f"[Close Bet Model 3 Warning] 스캔 오류가 발생했지만 주 분석은 계속합니다: {e}")

    print("\n[Step 1-1] 뉴스 이슈 데이터를 수집합니다.")
    try:
        news_collector = NewsCollector()
        news_collector.run()
    except Exception as e:
        print(f"[News Warning] 뉴스 수집 중 오류가 발생했지만 주가 분석은 계속 진행합니다: {e}")

    print("\n[Step 1-2] 시장강도 데이터를 수집합니다.")
    if os.environ.get("SKIP_MARKET_STRENGTH", "").strip() == "1":
        print("[Market Strength] 이 전체 분석에서는 기존 확정 시장강도 결과를 사용합니다.")
    else:
        try:
            run_market_strength(access_token=crawler.access_token)
        except Exception as e:
            report_market_strength_error(e)
            print(f"[Market Strength Warning] 시장강도 분석 중 오류가 발생했지만 주가 분석은 계속 진행합니다: {e}")
    
    # 2. 데이터 시계열 분석 및 스코어링 (엑셀/대시보드용 종합 분석)
    print("\n[Step 2] 데이터 스코어링 분석을 시작합니다.")
    analyzer = StockAnalyzer()
    df_analyzed = analyzer.run_analysis()
    
    # 3. 엑셀 리포트 저장
    print("\n[Step 3] 엑셀 리포트를 생성합니다.")
    excel_mgr = ExcelManager()
    excel_mgr.export_to_excel(df_analyzed)
    
    print("\n=== 모든 자동화 프로세스가 완료되었습니다! ===")

if __name__ == "__main__":
    main()
