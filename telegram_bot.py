import asyncio
import os
from telegram import Bot

class TelegramNotifier:
    def __init__(self):
        # 환경변수에서 텔레그램 토큰과 채팅 ID를 가져옵니다. 
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "YOUR_TELEGRAM_CHAT_ID")
        self.bot = Bot(token=self.token) if self.token != "YOUR_TELEGRAM_BOT_TOKEN" else None

    async def _send_message(self, text):
        if not self.bot:
            print("[Warning] 텔레그램 토큰이 설정되지 않아 테스트 모드로 출력만 합니다.")
            print("="*40)
            print(f"[텔레그램 전송 예정 메시지]\n{text}")
            print("="*40)
            return
        
        try:
            await self.bot.send_message(chat_id=self.chat_id, text=text, parse_mode='HTML')
            print("[Success] 텔레그램 메시지 전송 완료!")
        except Exception as e:
            print(f"[Error] 텔레그램 메시지 전송 실패: {e}")

    def send_summary(self, report_text):
        """배치 스케줄러(GitHub Actions) 실행 시: 생성된 분석 리포트를 텔레그램으로 전송"""
        if not report_text:
            report_text = "📊 <b>주식 분석 요약</b>\n\n분석된 리포트 데이터가 없습니다."
            
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(self._send_message(report_text))
            else:
                loop.run_until_complete(self._send_message(report_text))
        except Exception:
            asyncio.run(self._send_message(report_text))

if __name__ == "__main__":
    notifier = TelegramNotifier()
    # notifier.send_summary("테스트 메시지입니다.")
