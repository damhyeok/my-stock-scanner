import sqlite3
import pandas as pd
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import time
import os
from dotenv import load_dotenv

# .env 파일에서 환경변수 로드
load_dotenv()

class StockCrawler:
    def __init__(self, db_path="stock_data.db"):
        self.db_path = db_path
        
        # 한국투자증권(KIS) API 키 세팅
        self.kis_app_key = os.environ.get("KIS_APP_KEY", "")
        self.kis_app_secret = os.environ.get("KIS_APP_SECRET", "")
        self.kis_base_url = "https://openapi.koreainvestment.com:9443" # 실전투자 도메인
        self.access_token = None
        
        # 주말/휴일을 고려하여 가장 최근 영업일(business day)을 타겟 날짜로 설정
        today = datetime.today()
        b_days = pd.bdate_range(end=today, periods=1)
        self.target_date = b_days[0].strftime("%Y%m%d")
            
        self._init_db()

    def _get_kis_access_token(self):
        """한국투자증권 API 접근을 위한 Oauth 토큰 발급"""
        if self.access_token:
            return self.access_token
            
        if not self.kis_app_key or not self.kis_app_secret:
            raise ValueError("KIS API 키가 .env 파일에 설정되지 않았습니다.")
            
        url = f"{self.kis_base_url}/oauth2/tokenP"
        headers = {"content-type": "application/json"}
        body = {
            "grant_type": "client_credentials",
            "appkey": self.kis_app_key,
            "appsecret": self.kis_app_secret
        }
        res = requests.post(url, headers=headers, json=body)
        if res.status_code == 200:
            self.access_token = res.json().get('access_token')
            return self.access_token
        else:
            raise Exception(f"KIS 토큰 발급 실패: {res.text}")

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
        print(f"[{self.target_date}] 시장 데이터(OHLCV, 시가총액) 수집 중 (한국투자증권 API)...")
        
        try:
            token = self._get_kis_access_token()
            
            # [수정] 한국투자증권(KIS) 거래대금 상위 API 호출 로직으로 교체
            # 실제 사용을 위해 KIS API의 '거래량/거래대금 상위' 엔드포인트를 호출합니다.
            url = f"{self.kis_base_url}/uapi/domestic-stock/v1/quotations/volume-rank"
            headers = {
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {token}",
                "appkey": self.kis_app_key,
                "appsecret": self.kis_app_secret,
                "tr_id": "FHPST01710000", # 거래량 상위 TR
                "custtype": "P"
            }
            # 파라미터 세팅 (코스피/코스닥 전체, 거래대금순 등)
            params = {
                "FID_COND_MRKT_DIV_CODE": "0000", # 전체
                "FID_COND_SCR_DIV_CODE": "20100", # 거래대금
                "FID_INPUT_ISCD": "0000",
                "FID_DIV_CLS_CODE": "0",
                "FID_BLNG_CLS_CODE": "0",
                "FID_TRGT_CLS_CODE": "111111111",
                "FID_TRGT_EXLS_CLS_CODE": "000000",
                "FID_INPUT_PRICE_1": "0",
                "FID_INPUT_PRICE_2": "0",
                "FID_VOL_CNT": "0",
                "FID_INPUT_DATE_1": "0"
            }
            
            res = requests.get(url, headers=headers, params=params)
            
            if res.status_code == 200 and res.json().get('rt_cd') == '0':
                output = res.json().get('output', [])
                
                # 받아온 JSON 데이터를 pandas DataFrame으로 변환
                df_merged = pd.DataFrame(output)
                # KIS API 필드명을 우리 DB 스키마에 맞게 맵핑
                df_merged = df_merged.rename(columns={
                    'mksc_shrn_iscd': 'ticker',
                    'hts_kor_isnm': 'name',
                    'stck_prpr': 'close',
                    'prdy_ctrt': 'fluctuation_rate',
                    'acml_vol': 'volume',
                    'acml_tr_pbmn': 'trading_value',
                    'stck_avls_val': 'market_cap' # 시가총액 (해당 API에서 제공하지 않을 경우 별도 조회 필요할 수 있음)
                })
                
                # 타입 변환 (문자열 -> 숫자)
                df_merged['close'] = pd.to_numeric(df_merged['close'], errors='coerce')
                df_merged['fluctuation_rate'] = pd.to_numeric(df_merged['fluctuation_rate'], errors='coerce')
                df_merged['volume'] = pd.to_numeric(df_merged['volume'], errors='coerce')
                df_merged['trading_value'] = pd.to_numeric(df_merged['trading_value'], errors='coerce')
                if 'market_cap' in df_merged.columns:
                    df_merged['market_cap'] = pd.to_numeric(df_merged['market_cap'], errors='coerce')
                else:
                    df_merged['market_cap'] = 0
                
                return df_merged
            else:
                raise Exception(f"KIS API 호출 실패: {res.text}")
            
        except Exception as e:
            print(f"[Warning] KIS 서버 접속 오류 또는 키 미설정으로 테스트용 가상 데이터를 생성합니다: {e}")
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
        print(f"[{self.target_date}] 외국인/기관 수급 데이터 수집 중 (한국투자증권 API)...")
        
        try:
            token = self._get_kis_access_token()
            
            # [수정] 한국투자증권(KIS) 투자자동향 API 호출 로직으로 교체
            url = f"{self.kis_base_url}/uapi/domestic-stock/v1/quotations/investor-trend"
            headers = {
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {token}",
                "appkey": self.kis_app_key,
                "appsecret": self.kis_app_secret,
                "tr_id": "FHPST02310000", # 투자자별 매매동향 TR
                "custtype": "P"
            }
            params = {
                "FID_COND_MRKT_DIV_CODE": "0000",
                "FID_COND_SCR_DIV_CODE": "20168",
                "FID_INPUT_ISCD": "0000",
                "FID_DIV_CLS_CODE": "0",
                "FID_BLNG_CLS_CODE": "0",
                "FID_TRGT_CLS_CODE": "111111111",
                "FID_TRGT_EXLS_CLS_CODE": "000000",
                "FID_INPUT_PRICE_1": "0",
                "FID_INPUT_PRICE_2": "0",
                "FID_VOL_CNT": "0",
                "FID_INPUT_DATE_1": "0"
            }
            
            res = requests.get(url, headers=headers, params=params)
            
            if res.status_code == 200 and res.json().get('rt_cd') == '0':
                output = res.json().get('output', [])
                df_investor = pd.DataFrame(output)
                
                df_investor = df_investor.rename(columns={
                    'mksc_shrn_iscd': 'ticker',
                    'frgn_ntby_qty': 'foreign_net', # 외국인 순매수 (거래대금이 없을 경우 수량 우선 매핑)
                    'orgn_ntby_qty': 'inst_net'    # 기관 순매수
                })
                
                df_investor['foreign_net'] = pd.to_numeric(df_investor['foreign_net'], errors='coerce')
                df_investor['inst_net'] = pd.to_numeric(df_investor['inst_net'], errors='coerce')
                
                return df_investor[['ticker', 'foreign_net', 'inst_net']]
            else:
                raise Exception(f"KIS 수급 API 호출 실패: {res.text}")
            
        except Exception as e:
            print(f"[Warning] KIS 수급 데이터 서버 오류 또는 키 미설정으로 테스트용 가상 데이터를 생성합니다: {e}")
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
        
        # 현재 시간에 따라 세션 결정 (깃허브 액션 지연 시간을 고려한 동적 세션명 부여)
        now = datetime.now()
        hour, minute = now.hour, now.minute
        
        if hour < 15 or (hour == 15 and minute < 30):
            session = f"장중({hour:02d}:{minute:02d})"
        elif hour < 18:
            session = "정규장(16:00)"
        else:
            session = "시간외(20:30)"
        
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
