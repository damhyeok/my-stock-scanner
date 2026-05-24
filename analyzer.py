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
        """전체 분석 로직 실행 및 최종 스코어링"""
        print("데이터 분석을 시작합니다...")
        df_recent = self.load_data(days=5)
        
        if df_recent.empty:
            print("DB에 분석할 데이터가 없습니다. (크롤러를 먼저 실행해주세요)")
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
        # 상주 지수(1회당 10점) + 순유입 잔존 비율(%) + 눌림목 발생 시 가산점(20점)
        final_df['total_score'] = (final_df['presence_index'] * 10) + final_df['retention_ratio']
        final_df.loc[final_df['is_pullback'] == True, 'total_score'] += 20
        
        # 점수 순 정렬
        final_df = final_df.sort_values(by='total_score', ascending=False).round(2)
        
        print("분석 완료! 상위 5개 추천 종목:")
        print(final_df.head(5).to_string(index=False))
        
        return final_df

if __name__ == "__main__":
    analyzer = StockAnalyzer()
    # analyzer.run_analysis()
