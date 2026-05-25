import asyncio
import os
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

class TelegramNotifier:
    def __init__(self):
        # 환경변수에서 텔레그램 토큰과 채팅 ID를 가져옵니다. 
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "YOUR_TELEGRAM_CHAT_ID")
        self.bot = Bot(token=self.token) if self.token != "YOUR_TELEGRAM_BOT_TOKEN" else None

    # ---------- [배치 작업용 일방향 전송] ----------
    async def _send_message(self, text):
        if not self.bot:
            print("⚠️ 텔레그램 토큰이 설정되지 않아 테스트 모드로 출력만 합니다.")
            print("="*40)
            print(f"[텔레그램 전송 예정 메시지]\n{text}")
            print("="*40)
            return
        
        try:
            await self.bot.send_message(chat_id=self.chat_id, text=text, parse_mode='HTML')
            print("✅ 텔레그램 메시지 전송 완료!")
        except Exception as e:
            print(f"❌ 텔레그램 메시지 전송 실패: {e}")

    def send_summary(self, df_analyzed):
        """배치 스케줄러(GitHub Actions) 실행용: 일일 종가 분석 요약 전송"""
        if df_analyzed is None or df_analyzed.empty:
            text = "📊 <b>오늘의 주식 분석 요약</b>\n\n분석된 추천 종목 데이터가 없습니다."
        else:
            text = "📊 <b>오늘의 주식 분석 요약 (상위 5종목)</b>\n\n"
            top5 = df_analyzed.head(5).reset_index(drop=True)
            
            for idx, row in top5.iterrows():
                name = row.get('name', 'N/A')
                score = row.get('total_score', 0)
                pullback = "✅" if row.get('is_pullback', False) else "❌"
                presence = row.get('presence_index', 0)
                
                text += f"{idx+1}. <b>{name}</b> (총점: {score:.1f}점)\n"
                text += f"   - 상주지수: {presence}회 | 눌림목: {pullback}\n\n"
                
            text += "💡 자세한 정보는 Streamlit 대시보드 및 엑셀 리포트를 확인해주세요!"
            
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(self._send_message(text))
            else:
                loop.run_until_complete(self._send_message(text))
        except Exception:
            asyncio.run(self._send_message(text))

    # ---------- [양방향 챗봇 서버용] ----------
    async def _handle_now_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """사용자가 /now 또는 '지금'을 입력했을 때 실행될 콜백 함수"""
        await update.message.reply_text("🔄 실시간 장중 데이터를 수집하고 분석 중입니다. 잠시만 기다려주세요 (약 10~30초 소요)...")
        
        try:
            from crawler import StockCrawler
            from analyzer import StockAnalyzer
            
            # 1. 즉시 크롤링 및 DB 저장
            crawler = StockCrawler()
            crawler.run()
            
            # 2. 실시간 장중 분석 리포트 생성
            analyzer = StockAnalyzer()
            intraday_report = analyzer.analyze_intraday()
            
            # 3. 결과 답장
            await update.message.reply_text(intraday_report, parse_mode='HTML')
            
        except Exception as e:
            await update.message.reply_text(f"❌ 분석 중 오류가 발생했습니다: {e}")

    def run_bot_server(self):
        """양방향 명령어 수신을 위한 텔레그램 챗봇 무한루프 실행"""
        if self.token == "YOUR_TELEGRAM_BOT_TOKEN":
            print("⚠️ 텔레그램 토큰이 설정되지 않아 챗봇 서버를 실행할 수 없습니다.")
            return
            
        # python-telegram-bot v20 챗봇 빌더
        application = Application.builder().token(self.token).build()
        
        # 명령어 핸들러 등록
        application.add_handler(CommandHandler("now", self._handle_now_command))
        
        # 텍스트 '지금' 핸들러 등록 (정규식 필터)
        application.add_handler(MessageHandler(filters.Regex(r'^지금$'), self._handle_now_command))
        
        print("🤖 텔레그램 챗봇 서버가 시작되었습니다. (종료하려면 Ctrl+C)")
        print("- 텔레그램 창에서 '/now' 또는 '지금'을 입력해보세요!")
        
        # 챗봇 폴링 시작 (여기서 멈춰서 계속 대기함)
        application.run_polling()

if __name__ == "__main__":
    notifier = TelegramNotifier()
    # 양방향 통신 봇으로 실행하고 싶다면 아래 주석을 해제하고 터미널에서 python telegram_bot.py 를 실행하세요.
    # notifier.run_bot_server()
