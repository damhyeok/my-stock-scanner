import sqlite3
import pandas as pd

class StockAnalyzer:
    def __init__(self, db_path="stock_data.db"):
        self.db_path = db_path
        
    def _get_recent_dates(self, days=5):
        """DB에 저장된 가장 최근 영업일 N개를 가져옵니다."""
        try:
            conn = sqlite3.connect(self.db_path)
            query = "SELECT DISTINCT date FROM daily_stocks ORDER BY date DESC LIMIT ?"
            dates_df = pd.read_sql(query, conn, params=(days,))
            conn.close()
            return dates_df['date'].tolist()
        except sqlite3.OperationalError:
            # 테이블이 아직 없는 경우
            return []

    def load_data(self, days=5):
        """최근 N일치의 데이터를 로드합니다."""
        dates = self._get_recent_dates(days)
        if not dates:
            return pd.DataFrame()
            
        placeholders = ','.join('?' * len(dates))
        conn = sqlite3.connect(self.db_path)
        query = f"SELECT * FROM daily_stocks WHERE date IN ({placeholders})"
        df = pd.read_sql(query, conn, params=dates)
        conn.close()
        return df

    def calc_presence_index(self, df):
        """1. 상주 지수(Presence Index): 최근 5일 내 거래대금/수급 상위권 등장 횟수"""
        presence_counts = df.groupby(['ticker', 'name']).size().reset_index(name='presence_index')
        return presence_counts

    def calc_net_inflow_ratio(self, df):
        """2. 순유입 잔존 비율: (최근 5일 외인+기관 순매수 합계) / (최근 5일 거래대금 합계) * 100"""
        grouped = df.groupby(['ticker', 'name']).agg({
            'foreign_net': 'sum',
            'inst_net': 'sum',
            'trading_value': 'sum'
        }).reset_index()
        
        # 0으로 나누기 방지
        grouped['net_inflow_sum'] = grouped['foreign_net'] + grouped['inst_net']
        grouped['retention_ratio'] = grouped.apply(
            lambda x: (x['net_inflow_sum'] / x['trading_value'] * 100) if x['trading_value'] > 0 else 0, 
            axis=1
        )
        return grouped[['ticker', 'name', 'retention_ratio']]

    def find_pullback_stocks(self, df):
        """3. 눌림목 종목 추출: 어제 대비 거래량이 감소하면서 주가는 -5% ~ 0% 사이로 소폭 조정을 받은 종목"""
        dates = sorted(df['date'].unique())
        if len(dates) < 2:
            return pd.DataFrame(columns=['ticker', 'is_pullback'])
        
        latest_date = dates[-1]
        prev_date = dates[-2]
        
        latest_df = df[df['date'] == latest_date].drop_duplicates(subset=['ticker']).set_index('ticker')
        prev_df = df[df['date'] == prev_date].drop_duplicates(subset=['ticker']).set_index('ticker')
        
        pullback_tickers = []
        for ticker in latest_df.index:
            if ticker in prev_df.index:
                latest_vol = latest_df.loc[ticker, 'volume']
                prev_vol = prev_df.loc[ticker, 'volume']
                latest_fluct = latest_df.loc[ticker, 'fluctuation_rate']
                
                # 거래량 감소 & 약한 하락장 (눌림목 조건)
                if latest_vol < prev_vol and -5.0 <= latest_fluct <= 0:
                    pullback_tickers.append(ticker)
                    
        return pd.DataFrame({'ticker': pullback_tickers, 'is_pullback': True})

    def run_analysis(self):
        """전체 종합 분석 로직 실행 및 최종 스코어링 (웹 대시보드 및 엑셀용)"""
        print("데이터 종합 스코어링 분석을 시작합니다...")
        df_recent = self.load_data(days=5)
        
        if df_recent.empty:
            print("DB에 분석할 데이터가 없습니다.")
            return pd.DataFrame()
            
        # 개별 알고리즘 모듈 실행
        df_presence = self.calc_presence_index(df_recent)
        df_retention = self.calc_net_inflow_ratio(df_recent)
        df_pullback = self.find_pullback_stocks(df_recent)
        
        # 분석 결과 병합
        final_df = pd.merge(df_presence, df_retention, on=['ticker', 'name'], how='outer')
        
        if not df_pullback.empty:
            final_df = pd.merge(final_df, df_pullback, on='ticker', how='left')
            final_df['is_pullback'] = final_df['is_pullback'].fillna(False)
        else:
            final_df['is_pullback'] = False
            
        # --- 스코어링 로직 ---
        final_df['total_score'] = (final_df['presence_index'] * 10) + final_df['retention_ratio']
        final_df.loc[final_df['is_pullback'] == True, 'total_score'] += 20
        final_df = final_df.sort_values(by='total_score', ascending=False).round(2)
        return final_df

    def generate_telegram_report(self):
        """지정된 스케줄마다 실행되어 직전 세션과 비교하는 텔레그램용 브리핑 리포트 생성"""
        df_raw = self.load_data(days=2)
        if df_raw.empty: return "분석할 데이터가 없습니다."
        
        dates = sorted(df_raw['date'].unique(), reverse=True)
        today = dates[0]
        df_today = df_raw[df_raw['date'] == today]
        sessions = sorted(df_today['session'].unique(), reverse=True)
        
        if not sessions: return "오늘 수집된 세션 데이터가 없습니다."
        
        current_session = sessions[0]
        
        # 1. 이전 세션 데이터 찾기 (비교용)
        prev_session_df = pd.DataFrame()
        if len(sessions) > 1:
            prev_session_df = df_today[df_today['session'] == sessions[1]]
        elif len(dates) > 1:
            df_yest = df_raw[df_raw['date'] == dates[1]]
            yest_sessions = sorted(df_yest['session'].unique(), reverse=True)
            if yest_sessions:
                prev_session_df = df_yest[df_yest['session'] == yest_sessions[0]]
                
        # 현재 Top 60 데이터
        curr_vol = df_today[(df_today['session'] == current_session) & (df_today['category'] == 'VOLUME_TOP_60')]
        
        # [분석 1] 거래대금 강세 섹터 순위 및 포함 종목
        sector_vol = curr_vol.groupby('sector')['trading_value'].sum().sort_values(ascending=False)
        sector_vol = sector_vol[sector_vol.index != '기타'].head(3)
        
        report = f"🔥 <b>[{current_session} 브리핑]</b>\n\n"
        report += "<b>1. 💰 거래대금 강세 섹터 Top 3</b>\n"
        if sector_vol.empty:
            report += "- 강세 섹터 없음\n"
        else:
            for i, (sec, val) in enumerate(sector_vol.items()):
                sector_stocks = curr_vol[curr_vol['sector']==sec]['name'].tolist()
                count = len(sector_stocks)
                stocks_str = ", ".join(sector_stocks)
                # 메시지 길이를 위해 너무 길면 자르기
                if len(stocks_str) > 40:
                    stocks_str = stocks_str[:40] + "..."
                report += f" {i+1}. {sec} ({count}종목: {stocks_str})\n"
                
        # [분석 2 & 3] 이전 세션과 비교
        if not prev_session_df.empty:
            prev_vol = prev_session_df[prev_session_df['category'] == 'VOLUME_TOP_60']
            prev_tickers = set(prev_vol['ticker'])
            curr_tickers = set(curr_vol['ticker'])
            
            # [분석 2] 신규 진입 종목
            new_entries = curr_tickers - prev_tickers
            new_df = curr_vol[curr_vol['ticker'].isin(new_entries)].sort_values('trading_value', ascending=False).head(3)
            
            report += "\n<b>2. 🚀 이전 세션 대비 신규 진입 종목 (Top 60)</b>\n"
            if new_df.empty:
                report += "- 이전 세션 대비 신규 진입 없음\n"
            else:
                for _, r in new_df.iterrows():
                    report += f" - {r['name']} ({r['sector']}, {r['fluctuation_rate']}%) \n"
                    
            # [분석 3] 거래급증 활발 종목
            common_tickers = curr_tickers.intersection(prev_tickers)
            common_curr = curr_vol[curr_vol['ticker'].isin(common_tickers)][['ticker', 'name', 'trading_value', 'sector']]
            common_prev = prev_vol[prev_vol['ticker'].isin(common_tickers)][['ticker', 'trading_value']]
            
            merged = pd.merge(common_curr, common_prev, on='ticker', suffixes=('_curr', '_prev'))
            merged['vol_growth'] = merged.apply(lambda x: ((x['trading_value_curr'] - x['trading_value_prev']) / x['trading_value_prev'] * 100) if x['trading_value_prev'] > 0 else 0, axis=1)
            fast_grow = merged.sort_values('vol_growth', ascending=False).head(3)
            
            report += "\n<b>3. ⚡ 이전 분석 대비 거래급증 종목</b>\n"
            if fast_grow.empty or fast_grow['vol_growth'].max() <= 0:
                report += "- 거래 급증 종목 없음\n"
            else:
                for _, r in fast_grow.iterrows():
                    if r['vol_growth'] > 0:
                        report += f" - {r['name']} ({r['sector']}, 급증률: +{r['vol_growth']:.1f}%)\n"
                        
        report += "\n💡 상세 데이터는 대시보드(웹)에서 확인하세요!"
        return report

if __name__ == "__main__":
    analyzer = StockAnalyzer()
