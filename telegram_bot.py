import asyncio
import os
from telegram import Bot

class TelegramNotifier:
    def __init__(self):
        # 환경변수에서 텔레그램 토큰과 채팅 ID를 가져옵니다. 
        # (GitHub Secrets나 로컬 환경변수에 세팅해야 합니다)
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "YOUR_TELEGRAM_CHAT_ID")
        
        # 실제 토큰이 입력되지 않은 경우 테스트 모드로 동작
        self.bot = Bot(token=self.token) if self.token != "YOUR_TELEGRAM_BOT_TOKEN" else None

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
        """분석된 데이터프레임의 상위 5개 종목을 요약하여 텔레그램으로 전송합니다."""
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
            
        # 비동기 실행 처리 (Jupyter/Streamlit 등 이벤트 루프 충돌 방지)
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(self._send_message(text))
            else:
                loop.run_until_complete(self._send_message(text))
        except Exception:
            asyncio.run(self._send_message(text))

if __name__ == "__main__":
    notifier = TelegramNotifier()
    # notifier.send_summary(테스트데이터프레임)
