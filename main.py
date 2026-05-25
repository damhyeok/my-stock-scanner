from crawler import StockCrawler
from analyzer import StockAnalyzer
from excel_manager import ExcelManager
from telegram_bot import TelegramNotifier

def main():
    print("🚀 === 주식 분석 자동화 시스템 시작 ===")
    
    # 1. 크롤링 및 DB 누적 저장
    print("\n[Step 1] 데이터 크롤링을 시작합니다.")
    crawler = StockCrawler()
    crawler.run()
    
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
    
    print("\n✨ === 모든 자동화 프로세스가 완료되었습니다! ===")

if __name__ == "__main__":
    main()
