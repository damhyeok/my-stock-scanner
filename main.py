import os
from datetime import datetime, timedelta, timezone

from crawler import StockCrawler
from analyzer import StockAnalyzer
from excel_manager import ExcelManager
from telegram_bot import TelegramNotifier
from news_collector import NewsCollector
from market_strength import MarketStrengthAnalyzer
from close_bet_scanner import CloseBetScanner


def run_market_strength(access_token=None):
    market_strength = MarketStrengthAnalyzer.from_environment()
    if access_token:
        market_strength.access_token = access_token
    return market_strength.run()


def report_market_strength_error(error):
    message = str(error).replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
    print(f"::error title=Market Strength Analysis Failed::{message}")


def main():
    print("=== 주식 분석 자동화 시스템 시작 ===")

    scheduled_cron = os.environ.get("GITHUB_EVENT_SCHEDULE", "").strip()
    run_mode = os.environ.get("ANALYSIS_RUN_MODE", "").strip()

    if scheduled_cron == "50 0 * * 1-5" or run_mode == "market_strength_only":
        analysis_label = "오전" if scheduled_cron == "50 0 * * 1-5" else "수동"
        print(f"\n[Market Strength Only] {analysis_label} 시장강도 데이터를 수집합니다.")
        run_market_strength()
        print(f"\n=== {analysis_label} 시장강도 분석이 완료되었습니다! ===")
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

    print("\n[Step 1-0] 종가베팅 스캐너를 실행합니다.")
    try:
        CloseBetScanner(crawler).run()
    except Exception as e:
        print(f"[Close Bet Warning] 종가베팅 스캔 중 오류가 발생했지만 주 분석은 계속합니다: {e}")

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
    
    # 4. 텔레그램 요약 전송 (직전 세션 비교 브리핑 리포트 생성 및 전송)
    print("\n[Step 4] 텔레그램 브리핑 리포트를 생성 및 전송합니다.")
    report_text = analyzer.generate_telegram_report()
    notifier = TelegramNotifier()
    notifier.send_summary(report_text)
    
    print("\n=== 모든 자동화 프로세스가 완료되었습니다! ===")

if __name__ == "__main__":
    main()
