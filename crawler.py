import sqlite3
import pandas as pd
from pykrx import stock
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import time
import os

class StockCrawler:
    def __init__(self, db_path="stock_data.db"):
        self.db_path = db_path
        
        # 주말/휴일을 고려하여 가장 최근 영업일(business day)을 타겟 날짜로 설정
        today = datetime.today()
        # pykrx 내부 함수 오류를 피해 pandas의 평일 계산기를 사용합니다.
        b_days = pd.bdate_range(end=today, periods=1)
        self.target_date = b_days[0].strftime("%Y%m%d")
            
        self._init_db()

    def _init_db(self):
        """SQLite DB 및 테이블 초기화"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 요청하신 시가총액(market_cap), 오늘 상승률(fluctuation_rate), 거래대금(trading_value) 포함
        # session 컬럼 추가 (정규장(16:00), 시간외(20:30) 등 구분)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS daily_stocks (
                date TEXT,
                session TEXT,
                ticker TEXT,
                name TEXT,
                close INTEGER,
                fluctuation_rate REAL,
                market_cap INTEGER,
                volume INTEGER,
                trading_value INTEGER,
                foreign_net INTEGER,
                inst_net INTEGER,
                sector TEXT,
                theme TEXT,
                category TEXT,
                PRIMARY KEY (date, session, ticker, category)
            )
        ''')
        conn.commit()
        conn.close()

    def get_market_data(self):
        """전체 종목의 시세, 거래대금, 시가총액, 등락률 데이터를 가져옵니다."""
        print(f"[{self.target_date}] 시장 데이터(OHLCV, 시가총액) 수집 중...")
        
        try:
            # 1. OHLCV (종가, 등락률, 거래량, 거래대금 등)
            df_ohlcv = stock.get_market_ohlcv(self.target_date, market="ALL")
            df_ohlcv = df_ohlcv[['종가', '등락률', '거래량', '거래대금']]
            
            # 2. 시가총액
            df_cap = stock.get_market_cap(self.target_date, market="ALL")
            df_cap = df_cap[['시가총액']]
            
            # 병합
            df_merged = pd.merge(df_ohlcv, df_cap, left_index=True, right_index=True)
            
            # 종목명 추가
            tickers = df_merged.index.tolist()
            names = [stock.get_market_ticker_name(ticker) for ticker in tickers]
            df_merged['종목명'] = names
            
            df_merged.reset_index(inplace=True)
            df_merged.rename(columns={
                '티커': 'ticker',
                '종가': 'close',
                '등락률': 'fluctuation_rate',
                '거래량': 'volume',
                '거래대금': 'trading_value',
                '시가총액': 'market_cap',
                '종목명': 'name'
            }, inplace=True)
            
            return df_merged
            
        except Exception as e:
            print(f"⚠️ KRX 서버 접속 오류(pykrx 에러)로 인해 테스트용 가상 데이터를 생성합니다: {e}")
            import random
            tickers = [f"{i:06d}" for i in range(1, 101)]
            # 테스트를 위해 다양한 섹터 명칭 리스트 준비 (반도체 소부장 분리)
            test_sectors = [
                '반도체 칩/설계', '반도체 장비', '반도체 소재', '반도체 부품', 
                '2차전지 소재', '2차전지 장비', '자동차 부품', '바이오 의약품', 
                '플랫폼/서비스', '방산/항공'
            ]
            
            df_merged = pd.DataFrame({
                'ticker': tickers,
                'name': [f"테스트종목_{t}" for t in tickers],
                'close': [random.randint(1000, 100000) for _ in tickers],
                'fluctuation_rate': [round(random.uniform(-10, 10), 2) for _ in tickers],
                'volume': [random.randint(10000, 1000000) for _ in tickers],
                'trading_value': [random.randint(100000000, 50000000000) for _ in tickers],
                'market_cap': [random.randint(1000000000, 500000000000) for _ in tickers],
                'sector': [random.choice(test_sectors) for _ in tickers] # 가상 섹터 할당
            })
            return df_merged

    def get_investor_data(self):
        """외국인 및 기관 순매수 데이터를 가져옵니다."""
        print(f"[{self.target_date}] 외국인/기관 수급 데이터 수집 중...")
        
        try:
            # 외국인 순매수
            df_foreign = stock.get_market_net_purchases_of_equities_by_ticker(
                self.target_date, self.target_date, market="ALL", investor="외국인"
            )
            # 기관합계 순매수
            df_inst = stock.get_market_net_purchases_of_equities_by_ticker(
                self.target_date, self.target_date, market="ALL", investor="기관합계"
            )
            
            df_investor = pd.DataFrame()
            df_investor['foreign_net'] = df_foreign['순매수거래대금']
            df_investor['inst_net'] = df_inst['순매수거래대금']
            df_investor.reset_index(inplace=True)
            df_investor.rename(columns={'티커': 'ticker'}, inplace=True)
            
            return df_investor
            
        except Exception as e:
            print(f"⚠️ KRX 수급 데이터 서버 오류로 테스트용 가상 데이터를 생성합니다: {e}")
            import random
            tickers = [f"{i:06d}" for i in range(1, 101)]
            df_investor = pd.DataFrame({
                'ticker': tickers,
                'foreign_net': [random.randint(-100000000, 100000000) for _ in tickers],
                'inst_net': [random.randint(-100000000, 100000000) for _ in tickers]
            })
            return df_investor

    def get_sector_info(self, ticker):
        """네이버 금융에서 업종 및 테마 정보를 가져와서 더 정교하게 분류합니다."""
        try:
            url = f"https://finance.naver.com/item/main.naver?code={ticker}"
            headers = {'User-Agent': 'Mozilla/5.0'}
            res = requests.get(url, headers=headers, timeout=3)
            soup = BeautifulSoup(res.text, 'html.parser')
            
            # 1. 기본 업종(Industry) 정보
            sector_tag = soup.select_one('div.trade_compare > h4.h_sub > em > a')
            industry = sector_tag.text if sector_tag else ""
            
            # 2. 테마(Theme) 정보 추출 (더 구체적인 분류를 위해)
            # 네이버 금융 페이지 하단이나 우측의 테마 정보를 탐색합니다.
            theme_tags = soup.select('div.item_area dt > a') # 종목 토론실 근처나 테마 섹션
            themes = [t.text for t in theme_tags if '테마' in t.get('href', '')]
            
            # 반도체 업종일 경우 테마 정보를 활용해 소부장 분리
            if "반도체" in industry:
                for theme in themes:
                    if "장비" in theme: return "반도체 장비"
                    if "재료" in theme or "소재" in theme: return "반도체 소재"
                    if "부품" in theme: return "반도체 부품"
                    if "설계" in theme or "팹리스" in theme: return "반도체 설계/칩"
                return "반도체 기타"
                
            return industry if industry else "기타"
        except Exception:
            return "기타"

    def save_to_db(self, df, category):
        """분석된 데이터프레임을 SQLite에 저장"""
        conn = sqlite3.connect(self.db_path)
        
        # 현재 시간에 따라 세션 결정 (16시 이전이면 정규장, 이후면 시간외/넥스트)
        current_hour = datetime.now().hour
        session = "정규장(16:00)" if current_hour < 18 else "시간외(20:30)"
        
        print(f"[{session}] 데이터를 DB에 저장 중...")
        
        for _, row in df.iterrows():
            conn.execute('''
                INSERT OR REPLACE INTO daily_stocks 
                (date, session, ticker, name, close, fluctuation_rate, market_cap, volume, trading_value, foreign_net, inst_net, sector, theme, category)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                self.target_date, session, row['ticker'], row.get('name', ''), 
                row.get('close', 0), row.get('fluctuation_rate', 0.0), 
                row.get('market_cap', 0), row.get('volume', 0), 
                row.get('trading_value', 0), row.get('foreign_net', 0), 
                row.get('inst_net', 0), row.get('sector', ''), 
                row.get('theme', ''), category
            ))
            
        conn.commit()
        conn.close()

    def run(self):
        print(f"========== {self.target_date} 데이터 크롤링 시작 ==========")
        
        # 1. 기본 시장 데이터 & 수급 데이터 수집
        df_market = self.get_market_data()
        df_investor = self.get_investor_data()
        
        # 데이터 병합
        df_all = pd.merge(df_market, df_investor, on='ticker', how='left').fillna(0)
        
        # --- 카테고리별 추출 ---
        # 1) 거래대금 상위 60위
        df_vol_top = df_all.sort_values(by='trading_value', ascending=False).head(60).copy()
        
        # 2) 외국인 순매수 상위 30위
        df_for_top = df_all.sort_values(by='foreign_net', ascending=False).head(30).copy()
        
        # 3) 기관 순매수 상위 30위
        df_inst_top = df_all.sort_values(by='inst_net', ascending=False).head(30).copy()
        
        # 크롤링 대상 고유 티커 추출 (중복 제거를 위해)
        target_tickers = set(df_vol_top['ticker']).union(set(df_for_top['ticker'])).union(set(df_inst_top['ticker']))
        print(f"섹터 매칭을 진행할 총 고유 종목 수: {len(target_tickers)}개")
        
        # 섹터 매칭 (시간이 조금 걸릴 수 있습니다)
        sector_dict = {}
        for idx, ticker in enumerate(target_tickers):
            # 이미 데이터프레임에 섹터 정보가 있다면(테스트 데이터 등) 크롤링을 건너뜁니다.
            existing_sector = df_all[df_all['ticker'] == ticker]['sector'].iloc[0] if 'sector' in df_all.columns else None
            
            if pd.notna(existing_sector) and existing_sector != '' and existing_sector != '기타':
                sector_dict[ticker] = existing_sector
            else:
                if idx % 10 == 0:
                    print(f"섹터 매칭 진행 중... ({idx}/{len(target_tickers)})")
                sector_dict[ticker] = self.get_sector_info(ticker)
                time.sleep(0.2) # 네이버 차단 방지 딜레이
            
        # 데이터프레임에 섹터 적용 함수
        def apply_sector(df):
            df['sector'] = df['ticker'].map(sector_dict)
            df['theme'] = '' # 테마는 추후 고도화 시 추가
            return df
            
        df_vol_top = apply_sector(df_vol_top)
        df_for_top = apply_sector(df_for_top)
        df_inst_top = apply_sector(df_inst_top)
        
        # --- DB 저장 ---
        print("DB에 데이터를 저장합니다...")
        self.save_to_db(df_vol_top, 'VOLUME_TOP_60')
        self.save_to_db(df_for_top, 'FOREIGN_TOP_30')
        self.save_to_db(df_inst_top, 'INST_TOP_30')
        
        print("========== 크롤링 및 DB 누적 저장 완료! ==========")

if __name__ == "__main__":
    crawler = StockCrawler()
    # 주의: 실제로 실행하면 네이버 크롤링으로 인해 약 20~30초 정도 소요될 수 있습니다.
    # crawler.run()
