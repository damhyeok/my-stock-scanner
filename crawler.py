import sqlite3
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup
import time
import os
import re
from dotenv import load_dotenv

# .env 파일에서 환경변수 로드
load_dotenv()

class StockCrawler:
    def __init__(self, db_path="stock_data.db"):
        self.db_path = db_path
        self.kst = ZoneInfo("Asia/Seoul")
        self.scheduled_cron = os.environ.get("GITHUB_EVENT_SCHEDULE", "").strip()
        
        # 한국투자증권(KIS) API 키 세팅
        self.kis_app_key = os.environ.get("KIS_APP_KEY", "")
        self.kis_app_secret = os.environ.get("KIS_APP_SECRET", "")
        self.kis_base_url = "https://openapi.koreainvestment.com:9443" # 실전투자 도메인
        self.access_token = None
        self.collected_at_kst = datetime.now(self.kst).strftime("%Y-%m-%d %H:%M:%S")
        
        self.target_date = self._resolve_target_date()
            
        self._init_db()

    def _resolve_target_date(self):
        """수집 대상 영업일을 한국 시간 기준으로 계산합니다."""
        now = datetime.now(self.kst)
        target_day = now

        b_days = pd.bdate_range(end=target_day, periods=1)
        return b_days[0].strftime("%Y%m%d")

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
                collected_at_kst TEXT,
                data_source TEXT,
                scheduled_cron TEXT,
                category TEXT,
                PRIMARY KEY (date, session, ticker, category)
            )
        ''')
        existing_columns = {row[1] for row in cursor.execute("PRAGMA table_info(daily_stocks)").fetchall()}
        metadata_columns = {
            "collected_at_kst": "TEXT",
            "data_source": "TEXT",
            "scheduled_cron": "TEXT",
        }
        for column_name, column_type in metadata_columns.items():
            if column_name not in existing_columns:
                cursor.execute(f"ALTER TABLE daily_stocks ADD COLUMN {column_name} {column_type}")
        conn.commit()
        conn.close()

    def get_market_data(self):
        """전체 종목의 시세, 거래대금, 시가총액, 등락률 데이터를 가져옵니다."""
        print(f"[{self.target_date}] 시장 데이터(OHLCV, 시가총액) 수집 중 (한국투자증권 API)...")
        
        try:
            token = self._get_kis_access_token()
            
            url = f"{self.kis_base_url}/uapi/domestic-stock/v1/quotations/volume-rank"
            headers = {
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {token}",
                "appkey": self.kis_app_key,
                "appsecret": self.kis_app_secret,
                "tr_id": "FHPST01710000",
                "custtype": "P"
            }
            
            # 거래량 순위 API의 J 코드는 KRX 주식 시장 기준입니다. V는 해당 API에서 유효하지 않습니다.
            market_codes = ['J']
            all_data = []
            
            for m_code in market_codes:
                params = {
                    "FID_COND_MRKT_DIV_CODE": m_code,
                    "FID_COND_SCR_DIV_CODE": "20171",
                    "FID_INPUT_ISCD": "0000",
                    "FID_DIV_CLS_CODE": "1",
                    "FID_BLNG_CLS_CODE": "3",
                    "FID_TRGT_CLS_CODE": "111111111",
                    "FID_TRGT_EXLS_CLS_CODE": "000000",
                    "FID_INPUT_PRICE_1": "",
                    "FID_INPUT_PRICE_2": "",
                    "FID_VOL_CNT": "",
                    "FID_INPUT_DATE_1": ""
                }
                
                res = requests.get(url, headers=headers, params=params)
                
                if res.status_code == 200 and res.json().get('rt_cd') == '0':
                    output = res.json().get('output', [])
                    all_data.extend(output)
                else:
                    print(f"[Warning] KIS API {m_code} 호출 실패: {res.text}")
            
            if not all_data:
                raise Exception("조회된 데이터가 없습니다.")
                
            # 받아온 JSON 데이터를 pandas DataFrame으로 변환
            df_merged = pd.DataFrame(all_data)
            df_merged = df_merged.rename(columns={
                'mksc_shrn_iscd': 'ticker',
                'hts_kor_isnm': 'name',
                'stck_prpr': 'close',
                'prdy_ctrt': 'fluctuation_rate',
                'acml_vol': 'volume',
                'acml_tr_pbmn': 'trading_value',
                'lstn_stcn': 'listed_shares'
            })
            
            # 타입 변환 (문자열 -> 숫자)
            df_merged['close'] = pd.to_numeric(df_merged['close'], errors='coerce')
            df_merged['fluctuation_rate'] = pd.to_numeric(df_merged['fluctuation_rate'], errors='coerce')
            df_merged['volume'] = pd.to_numeric(df_merged['volume'], errors='coerce')
            df_merged['trading_value'] = pd.to_numeric(df_merged['trading_value'], errors='coerce')
            df_merged['listed_shares'] = pd.to_numeric(df_merged.get('listed_shares', 0), errors='coerce').fillna(0)
            df_merged['market_cap'] = (df_merged['close'].fillna(0) * df_merged['listed_shares']).astype('int64')
            df_merged = df_merged.drop(columns=['listed_shares'])
            
            return df_merged
            
        except Exception as e:
            print(f"[Error] KIS API 시장 데이터 수집 실패: {e}")
            raise

    def _parse_nxt_number(self, value):
        text = str(value).strip()
        if text in ['', '-', 'nan', 'None']:
            return 0
        text = re.sub(r'[^0-9.\-]', '', text)
        if text in ['', '-', '.']:
            return 0
        return pd.to_numeric(text, errors='coerce')

    def _load_name_ticker_map(self):
        name_ticker_map = {
            '삼성전자': '005930',
            'SK하이닉스': '000660',
            'LG전자': '066570',
            'LG씨엔에스': '064400',
            'LG이노텍': '011070',
            '삼성전기': '009150',
            'NAVER': '035420',
            '현대차': '005380',
            '삼성에스디에스': '018260',
            '현대모비스': '012330',
            'LG에너지솔루션': '373220',
        }
        try:
            conn = sqlite3.connect(self.db_path)
            df_names = pd.read_sql(
                "SELECT ticker, name FROM daily_stocks WHERE name IS NOT NULL AND name != ''",
                conn
            )
            conn.close()
            for _, row in df_names.drop_duplicates('name').iterrows():
                name_ticker_map[str(row['name'])] = str(row['ticker']).zfill(6)
        except Exception:
            pass
        return name_ticker_map

    def get_nxt_aftermarket_data(self):
        """넥스트레이드 공개 페이지에서 NXT 시간외 거래대금 상위 데이터를 가져옵니다."""
        print(f"[{self.target_date}] NXT 시간외 거래대금 데이터를 수집 중 (넥스트레이드 공개 페이지)...")
        url = "https://www.nextrade.co.kr/main.do"
        headers = {'User-Agent': 'Mozilla/5.0'}

        try:
            res = requests.get(url, headers=headers, timeout=10)
            res.raise_for_status()
        except Exception as e:
            raise Exception(f"NXT 공개 페이지 수집 실패: {e}")

        soup = BeautifulSoup(res.text, 'html.parser')
        target_table = None
        for table in soup.find_all('table'):
            headers = [cell.get_text(strip=True) for cell in table.find_all('th')]
            if '종목명' not in headers or '거래대금' not in headers:
                continue

            table_rows = []
            for tr in table.find_all('tr'):
                cells = [cell.get_text(strip=True) for cell in tr.find_all('td')]
                if len(cells) == len(headers):
                    table_rows.append(dict(zip(headers, cells)))

            if table_rows:
                target_table = pd.DataFrame(table_rows)
                break

        if target_table is None or target_table.empty:
            raise Exception("NXT 거래대금 상위종목 표를 찾지 못했습니다.")

        name_ticker_map = self._load_name_ticker_map()
        rows = []
        skipped_names = []

        for _, row in target_table.iterrows():
            name = str(row.get('종목명', '')).strip()
            if not name:
                continue

            ticker = name_ticker_map.get(name)
            if not ticker:
                skipped_names.append(name)
                continue

            close = self._parse_nxt_number(row.get('현재가', 0))
            fluctuation_rate = self._parse_nxt_number(row.get('등락률', 0))
            volume = self._parse_nxt_number(row.get('거래량', 0))
            trading_value = self._parse_nxt_number(row.get('거래대금', 0))

            rows.append({
                'ticker': ticker,
                'name': name,
                'close': close,
                'fluctuation_rate': fluctuation_rate,
                'market_cap': 0,
                'volume': volume,
                'trading_value': trading_value,
                'foreign_net': 0,
                'inst_net': 0,
                'sector': '',
                'theme': '',
            })

        if skipped_names:
            print(f"[Warning] NXT 종목코드 매핑 실패로 제외된 종목: {', '.join(skipped_names[:10])}")
        if not rows:
            raise Exception("NXT 거래대금 상위종목 중 저장 가능한 데이터가 없습니다.")

        df_nxt = pd.DataFrame(rows)
        df_nxt = df_nxt.sort_values('trading_value', ascending=False).drop_duplicates('ticker')
        print(f"[NXT] 거래대금 상위종목 {len(df_nxt)}건을 수집했습니다.")
        return df_nxt

    def get_investor_data(self, tickers=None, names=None):
        """KIS API에서 종목별 외국인 및 기관 순매수 금액을 조회합니다."""
        print(f"[{self.target_date}] 종목별 외국인/기관 수급 데이터 수집 중 (한국투자증권 API)...")

        empty_investor_df = pd.DataFrame(columns=['ticker', 'foreign_net', 'inst_net'])
        
        try:
            token = self._get_kis_access_token()
            if tickers is None:
                print("[Warning] 종목별 수급 조회 대상이 없어 수급 금액을 0으로 대체합니다.")
                return empty_investor_df

            ticker_list = [str(t).zfill(6) for t in pd.Series(tickers).dropna().astype(str).unique() if str(t).strip()]
            name_map = names or {}
            url = f"{self.kis_base_url}/uapi/domestic-stock/v1/quotations/inquire-investor"
            headers = {
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {token}",
                "appkey": self.kis_app_key,
                "appsecret": self.kis_app_secret,
                "tr_id": "FHKST01010900",
                "custtype": "P"
            }

            investor_rows = []
            for ticker in ticker_list:
                params = {
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": ticker,
                }
                res = requests.get(url, headers=headers, params=params, timeout=10)
                if res.status_code != 200 or res.json().get('rt_cd') != '0':
                    print(f"[Warning] KIS 종목별 수급 API 호출 실패(ticker={ticker}): {res.text}")
                    continue

                rows = res.json().get('output', [])
                if not rows:
                    investor_rows.append({
                        'ticker': ticker,
                        'name': name_map.get(ticker, ''),
                        'foreign_net': 0,
                        'inst_net': 0,
                    })
                    continue

                row = next((item for item in rows if item.get('stck_bsop_date') == self.target_date), rows[0])
                investor_rows.append({
                    'ticker': ticker,
                    'name': name_map.get(ticker, ''),
                    'foreign_net': row.get('frgn_ntby_tr_pbmn', 0),
                    'inst_net': row.get('orgn_ntby_tr_pbmn', 0),
                })
                time.sleep(0.05)

            df_investor = pd.DataFrame(investor_rows)
            if df_investor.empty:
                print("[Warning] KIS 종목별 수급 데이터가 없습니다. 수급 금액을 0으로 대체하고 계속 진행합니다.")
                return empty_investor_df

            df_investor['foreign_net'] = pd.to_numeric(df_investor['foreign_net'], errors='coerce').fillna(0).astype(int)
            df_investor['inst_net'] = pd.to_numeric(df_investor['inst_net'], errors='coerce').fillna(0).astype(int)
            df_investor['name'] = df_investor['name'].fillna('')
            print(f"[Investor] 종목별 수급 {len(df_investor)}건 조회 완료")
            return df_investor
            
        except Exception as e:
            print(f"[Warning] KIS 수급 데이터 조회 실패: {e}")
            print("[Warning] 수급 금액을 0으로 대체하고 계속 진행합니다.")
            return empty_investor_df

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

    def _get_session_name(self):
        now = datetime.now(self.kst)
        hour, minute = now.hour, now.minute
        scheduled_sessions = {
            "0 3 * * 1-5": "장중(12:00)",
            "0 7 * * 1-5": "정규장(16:00)",
        }

        if self.scheduled_cron in scheduled_sessions:
            return scheduled_sessions[self.scheduled_cron]

        if hour < 15 or (hour == 15 and minute < 30):
            return f"장중({hour:02d}:{minute:02d})"
        if hour < 18:
            return "정규장(16:00)"
        if hour < 20 or (hour == 20 and minute < 30):
            return f"시간외진행({hour:02d}:{minute:02d})"
        return "시간외(20:30)"

    def _exclude_exchange_traded_products(self, df):
        """ETF/ETN/레버리지/인버스 등 상품형 종목을 분석 대상에서 제외합니다."""
        if df.empty or 'name' not in df.columns:
            return df

        product_keywords = [
            'KODEX', 'TIGER', 'ACE', 'SOL', 'PLUS', 'RISE', 'HANARO',
            'KOSEF', 'ARIRANG', 'KBSTAR', 'KINDEX', 'TREX', 'TIMEFOLIO',
            'FOCUS', 'WOORI', '1Q', '마이티', '히어로즈',
            'ETF', 'ETN', '레버리지', '인버스', '선물', '채권',
        ]
        name_upper = df['name'].fillna('').astype(str).str.upper()
        product_mask = name_upper.str.contains('|'.join(product_keywords), regex=True, na=False)
        excluded_count = int(product_mask.sum())

        if excluded_count > 0:
            print(f"[Filter] ETF/ETN 등 상품형 종목 {excluded_count}건을 분석 대상에서 제외했습니다.")

        return df[~product_mask].copy()

    def _normalize_sector(self, ticker, name, sector):
        """네이버 업종을 주요 주도 테마 기준 섹터로 보정합니다."""
        exact_name_sector_map = {
            '두산로보틱스': '로봇',
            '레인보우로보틱스': '로봇',
            '로보스타': '로봇',
            '로보티즈': '로봇',
            '에스피지': '로봇',
            '현대무벡스': '로봇',
            'LG에너지솔루션': '2차전지',
            '삼성SDI': '2차전지',
            '포스코퓨처엠': '2차전지',
            'POSCO홀딩스': '2차전지',
            '포스코홀딩스': '2차전지',
            'LG화학': '2차전지',
            '삼아알미늄': '2차전지',
            '이브이첨단소재': '2차전지',
            '한화솔루션': '에너지',
            '두산퓨얼셀': '에너지',
            'OCI홀딩스': '에너지',
            'HD현대에너지솔루션': '에너지',
            'SK하이닉스': '반도체 메모리',
            '삼성전자': '반도체 메모리',
            '삼성전자우': '반도체 메모리',
            '한미반도체': '반도체 메모리',
            '피에스케이홀딩스': '반도체 장비',
            '이오테크닉스': '반도체 메모리',
            '제주반도체': '온디바이스AI',
            '네패스': '온디바이스AI',
            '주성엔지니어링': '반도체 장비',
            'HPSP': '반도체 장비',
            '원익IPS': '반도체 장비',
            '테스': '반도체 장비',
            '피에스케이': '반도체 장비',
            '테크윙': '반도체 장비',
            '브이엠': '반도체 장비',
            '티에스이': '반도체 장비',
            '디앤디파마텍': '바이오',
            '유진테크': '반도체 장비',
            '기가비스': '반도체 장비',
            '두산테스나': '반도체 장비',
            '하나마이크론': '반도체 장비',
            'DB하이텍': '기판',
            '파두': '기판',
            '대덕전자': '기판',
            '코리아써키트': '기판',
            '심텍': '기판',
            '삼성전기': '기판',
            'LG이노텍': '기판',
            '빛과전자': '광통신',
            '대한광통신': '광통신',
            'LIG디펜스앤에어로스페이스': '방산·우주항공',
            'LIG넥스원': '방산·우주항공',
            '한화에어로스페이스': '방산·우주항공',
            '현대로템': '방산·우주항공',
            '한국항공우주': '방산·우주항공',
            'HD현대일렉트릭': '전력기기·전선',
            '효성중공업': '전력기기·전선',
            'LS ELECTRIC': '전력기기·전선',
            'LS': '전력기기·전선',
            '대한전선': '전력기기·전선',
            '현대차': '로봇',
            '현대차우': '로봇',
            '현대차2우B': '로봇',
            '현대차3우B': '로봇',
            '기아': '로봇',
            '현대모비스': '로봇',
            '현대오토에버': '로봇',
            '현대글로비스': '모빌리티',
            '한온시스템': '모빌리티',
            '삼성바이오로직스': '바이오',
            '셀트리온': '바이오',
            '한미약품': '바이오',
            '한올바이오파마': '바이오',
            '엘앤씨바이오': '바이오',
            '두산에너빌리티': '원전',
            '두산': '원전',
            'HD현대중공업': '조선',
            'HD한국조선해양': '조선',
            '삼성중공업': '조선',
            '한화오션': '조선',
            'HJ중공업': '조선',
            'KB금융': '밸류업금융',
            '신한지주': '밸류업금융',
            '하나금융지주': '밸류업금융',
            '우리금융지주': '밸류업금융',
            '한국금융지주': '밸류업금융',
            'NH투자증권': '밸류업금융',
            '미래에셋증권': '밸류업금융',
            '삼성화재': '밸류업금융',
            '삼성화재우': '밸류업금융',
            '삼성생명': '밸류업금융',
            '미래에셋생명': '밸류업금융',
            '삼성물산': '밸류업금융',
            'SK스퀘어': '밸류업금융',
            'SK': '밸류업금융',
            'LG': '밸류업금융',
            'NAVER': '플랫폼·IT',
            '카카오': '플랫폼·IT',
            '하이브': '플랫폼·IT',
            'NC': '플랫폼·IT',
            '엔씨소프트': '플랫폼·IT',
            'NHN': '플랫폼·IT',
            '삼성에스디에스': '플랫폼·IT',
            '삼성SDS': '플랫폼·IT',
            'LG씨엔에스': '로봇',
            'LG CNS': '플랫폼·IT',
            'LG전자': '로봇',
            'LG디스플레이': 'IT부품·산업재',
            '서진시스템': '2차전지',
            '삼화콘덴서': 'IT부품·산업재',
            '성호전자': 'IT부품·산업재',
            'KT&G': 'IT부품·산업재',
            '에이피알': '화장품',
            'S-Oil': 'IT부품·산업재',
            '현대건설': '건설',
            '대우건설': '건설',
            '신세계': 'IT부품·산업재',
            '롯데쇼핑': 'IT부품·산업재',
            '나무기술': 'IT부품·산업재',
            '미래에셋벤처투자': 'IT부품·산업재',
            'TS인베스트먼트': 'IT부품·산업재',
            'LG헬로비전': 'IT부품·산업재',
            '휴림로봇': '로봇',
            '아모레퍼시픽': '화장품',
            '아모레G': '화장품',
            'LG생활건강': '화장품',
            '한국콜마': '화장품',
            '코스맥스': '화장품',
            '클리오': '화장품',
            '토니모리': '화장품',
            '브이티': '화장품',
            '실리콘투': '화장품',
            '잉글우드랩': '화장품',
            '콜마비앤에이치': '화장품',
            '코스메카코리아': '화장품',
            '네오팜': '화장품',
            '마녀공장': '화장품',
        }
        name_text = str(name or '')
        if name_text in exact_name_sector_map:
            return exact_name_sector_map[name_text]

        ticker_sector_map = {
            '005930': '반도체',
            '000660': '반도체',
            '000990': '반도체',
            '042700': '반도체 장비',
            '036930': '반도체 장비',
            '440110': '반도체 설계',
            '108490': '로봇',
            '454910': '로봇',
            '277810': '로봇',
            '034020': '전력기기·전선',
            '298040': '전력기기·전선',
            '010120': '전력기기·전선',
            '012450': '방산',
            '064350': '방산',
            '079550': '방산',
            '005380': '자동차',
            '000270': '자동차',
            '012330': '자동차',
            '018880': '자동차',
            '005385': '자동차',
            '005387': '자동차',
            '035420': '인터넷/플랫폼',
            '402340': '지주/투자',
            '000150': '지주/투자',
            '028260': '지주/투자',
            '064400': 'IT서비스',
            '022100': 'IT서비스',
            '242040': 'IT서비스',
            '010170': '통신장비',
            '017670': '통신',
            '006400': '2차전지/배터리',
            '373220': '2차전지/배터리',
            '003670': '2차전지/배터리',
            '247540': '2차전지/배터리',
            '086520': '2차전지/배터리',
            '006110': '2차전지/배터리',
            '051910': '2차전지/배터리',
            '006260': '전력/전기장비',
            '267260': '전력/전기장비',
            '178320': '전력/전기장비',
            '009150': '기판',
            '011070': '기판',
            '353200': '기판',
            '007660': '기판',
            '042660': '조선',
            '443060': '조선',
            '329180': '조선',
            '097230': '조선',
            '105560': '금융',
            '086790': '금융',
            '316140': '금융',
            '138040': '금융',
            '032830': '보험',
            '000810': '보험',
            '336260': '에너지',
            '009830': '에너지',
            '034730': '에너지',
            '010950': '에너지',
            '010060': '화학/소재',
            '034220': '디스플레이',
            '128940': '바이오',
            '009420': '바이오',
            '347850': '바이오',
            '036570': '게임',
            '251270': '게임',
            '181710': 'IT서비스/인터넷',
            '037560': '미디어/콘텐츠',
            '004170': '소비/유통',
            '008770': '소비/유통',
            '047040': '건설',
            '000720': '건설',
            '267270': '건설기계',
            '489790': '통신장비',
            '069540': '통신장비',
            '100790': '창투/벤처',
            '246690': '창투/벤처',
            '004020': '철강',
            '001040': '지주/투자',
        }
        if ticker in ticker_sector_map:
            return ticker_sector_map[ticker]

        keyword_sector_map = [
            ('로봇', '로봇'),
            ('로보', '로봇'),
            ('레인보우로보틱스', '로봇'),
            ('건설', '건설'),
            ('생물공학', '바이오'),
            ('화장품', '화장품'),
            ('코스메', '화장품'),
            ('콜마', '화장품'),
            ('아모레', '화장품'),
            ('생활건강', '화장품'),
            ('클리오', '화장품'),
            ('토니모리', '화장품'),
            ('마녀공장', '화장품'),
            ('반도체', '반도체'),
            ('하이닉스', '반도체'),
            ('삼성전자', '반도체'),
            ('전기', '전력기기·전선'),
            ('중공업', '전력기기·전선'),
            ('에어로스페이스', '방산'),
            ('디펜스', '방산'),
            ('현대로템', '방산'),
            ('현대차', '자동차'),
            ('기아', '자동차'),
            ('모비스', '자동차'),
            ('삼성SDI', '2차전지/배터리'),
            ('LG에너지솔루션', '2차전지/배터리'),
            ('포스코퓨처엠', '2차전지/배터리'),
            ('에코프로', '2차전지/배터리'),
            ('삼아알미늄', '2차전지/배터리'),
            ('LG화학', '2차전지/배터리'),
            ('퓨얼셀', '에너지'),
            ('한화솔루션', '에너지'),
            ('태양광', '에너지'),
            ('수소', '에너지'),
            ('에너지장비및서비스', '에너지'),
            ('한화오션', '조선'),
            ('현대중공업', '조선'),
            ('마린솔루션', '조선'),
            ('HJ중공업', '조선'),
            ('이노텍', '기판'),
            ('삼성전기', '기판'),
            ('대덕전자', '기판'),
            ('이수페타시스', '기판'),
            ('디스플레이', '디스플레이'),
            ('금융', '금융'),
            ('은행', '금융'),
            ('삼성생명', '보험'),
            ('삼성화재', '보험'),
            ('바이오', '바이오'),
            ('파마', '바이오'),
            ('약품', '바이오'),
            ('게임', '게임'),
            ('넷마블', '게임'),
            ('S-Oil', '에너지/정유'),
            ('OCI', '화학/소재'),
            ('신세계', '소비/유통'),
            ('호텔신라', '소비/유통'),
            ('건설', '건설'),
            ('광통신', '통신장비'),
            ('빛과전자', '통신장비'),
        ]
        for keyword, normalized_sector in keyword_sector_map:
            if keyword in name_text:
                return normalized_sector

        sector_text = str(sector or '')
        canonical_sector = self._canonical_sector(sector_text)
        if canonical_sector != sector_text:
            return canonical_sector

        return sector if sector else '기타'

    def _canonical_sector(self, sector):
        sector_text = str(sector or '').strip()
        canonical_map = {
            '전기유틸리티': '전력기기·전선',
            '전력/전기장비': '전력기기·전선',
            '바이오/제약': '바이오',
            '바이오·헬스': '바이오',
            '건강관리장비와용품': '바이오',
            '생명과학도구및서비스': '바이오',
            '생물공학': '바이오',
            '전자부품/기판': '기판',
            '화장품주': '화장품',
            '태양광·수소': '에너지',
            '수소/신재생': '에너지',
            '에너지장비및서비스': '에너지',
        }
        return canonical_map.get(sector_text, sector_text)

    def save_to_db(self, df, category):
        """분석된 데이터프레임을 SQLite에 저장"""
        conn = sqlite3.connect(self.db_path)
        
        session = self._get_session_name()
        data_source = "NXT" if session == "시간외(20:30)" else "KIS"
        
        print(f"[{session}] 데이터를 DB에 저장 중...")
        print(
            f"[Metadata] collected_at_kst={self.collected_at_kst}, "
            f"data_source={data_source}, scheduled_cron={self.scheduled_cron or 'manual'}"
        )
        conn.execute(
            "DELETE FROM daily_stocks WHERE date = ? AND session = ? AND category = ?",
            (self.target_date, session, category)
        )
        row_count = len(df)
        empty_name_count = int(df['name'].fillna('').eq('').sum()) if 'name' in df.columns else row_count

        def count_zero_values(column_name):
            if column_name not in df.columns:
                return row_count
            values = pd.to_numeric(df[column_name], errors='coerce').fillna(0)
            return int(values.eq(0).sum())

        zero_price_count = count_zero_values('close')
        zero_value_count = count_zero_values('trading_value')
        print(
            f"[Data Check] category={category}, rows={row_count}, "
            f"empty_names={empty_name_count}, zero_close={zero_price_count}, "
            f"zero_trading_value={zero_value_count}"
        )
        if row_count == 0:
            print(f"[Warning] {category} 저장 대상 데이터가 0건입니다.")
        if empty_name_count > 0:
            print(f"[Warning] {category}에 종목명이 비어 있는 데이터가 {empty_name_count}건 있습니다.")
        if zero_price_count > 0 or zero_value_count > 0:
            print(
                f"[Warning] {category}에 가격 또는 거래대금이 0인 데이터가 있습니다 "
                f"(zero_close={zero_price_count}, zero_trading_value={zero_value_count})."
            )
        
        for _, row in df.iterrows():
            conn.execute('''
                INSERT OR REPLACE INTO daily_stocks 
                (date, session, ticker, name, close, fluctuation_rate, market_cap, volume, trading_value, foreign_net, inst_net, sector, theme, collected_at_kst, data_source, scheduled_cron, category)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                self.target_date, session, row['ticker'], row.get('name', ''), 
                row.get('close', 0), row.get('fluctuation_rate', 0.0), 
                row.get('market_cap', 0), row.get('volume', 0), 
                row.get('trading_value', 0), row.get('foreign_net', 0), 
                row.get('inst_net', 0), row.get('sector', ''), 
                row.get('theme', ''), self.collected_at_kst, data_source,
                self.scheduled_cron or 'manual', category
            ))
            
        conn.commit()
        conn.close()

    def run(self):
        print(f"========== {self.target_date} 데이터 크롤링 시작 ==========")
        session = self._get_session_name()
        if "시간외" in session:
            print(f"[Skip] {session} 데이터는 분석 대상에서 제외되어 수집하지 않습니다.")
            return False

        is_nxt_afterhours = session == "시간외(20:30)"
        
        # 1. 기본 시장 데이터 & 수급 데이터 수집
        if is_nxt_afterhours:
            df_all = self.get_nxt_aftermarket_data()
        else:
            df_market = self.get_market_data()
            market_names = dict(zip(df_market['ticker'].astype(str).str.zfill(6), df_market['name'].fillna('')))
            df_investor = self.get_investor_data(df_market['ticker'], market_names)
        
            # 데이터 병합 (how='outer'로 변경하여 수급 상위 종목이 누락되지 않도록 함)
            df_all = pd.merge(df_market, df_investor, on='ticker', how='outer')
            if 'name_x' in df_all.columns or 'name_y' in df_all.columns:
                market_names = df_all.get('name_x', pd.Series('', index=df_all.index)).fillna('')
                investor_names = df_all.get('name_y', pd.Series('', index=df_all.index)).fillna('')
                df_all['name'] = market_names.where(market_names != '', investor_names)
                df_all = df_all.drop(columns=[c for c in ['name_x', 'name_y'] if c in df_all.columns])
        
        # --- 누락된 가격 정보 개별 조회 (KIS API) ---
        # 거래량 상위에는 없지만 수급 상위에만 있는 종목들의 시세를 채워 넣습니다.
        missing_mask = df_all['close'].isna() | (df_all['close'] == 0)
        missing_tickers = df_all[missing_mask]['ticker'].tolist()
        
        if missing_tickers and not is_nxt_afterhours:
            print(f"시세 정보가 누락된 {len(missing_tickers)}개 종목의 데이터를 한국투자증권 API로 개별 조회합니다...")
            try:
                token = self._get_kis_access_token()
                url_price = f"{self.kis_base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
                headers_price = {
                    "content-type": "application/json; charset=utf-8",
                    "authorization": f"Bearer {token}",
                    "appkey": self.kis_app_key,
                    "appsecret": self.kis_app_secret,
                    "tr_id": "FHKST01010100"
                }
                
                for ticker in missing_tickers:
                    params_price = {
                        "FID_COND_MRKT_DIV_CODE": "J", 
                        "FID_INPUT_ISCD": ticker
                    }
                    res_price = requests.get(url_price, headers=headers_price, params=params_price)
                    if res_price.status_code == 200 and res_price.json().get('rt_cd') == '0':
                        out = res_price.json().get('output', {})
                        df_all.loc[df_all['ticker'] == ticker, 'close'] = pd.to_numeric(out.get('stck_prpr', 0), errors='coerce')
                        df_all.loc[df_all['ticker'] == ticker, 'fluctuation_rate'] = pd.to_numeric(out.get('prdy_ctrt', 0), errors='coerce')
                        df_all.loc[df_all['ticker'] == ticker, 'volume'] = pd.to_numeric(out.get('acml_vol', 0), errors='coerce')
                        df_all.loc[df_all['ticker'] == ticker, 'trading_value'] = pd.to_numeric(out.get('acml_tr_pbmn', 0), errors='coerce')
                        close_val = pd.to_numeric(out.get('stck_prpr', 0), errors='coerce')
                        listed_shares = pd.to_numeric(out.get('lstn_stcn', 0), errors='coerce')
                        if pd.notna(close_val) and pd.notna(listed_shares):
                            df_all.loc[df_all['ticker'] == ticker, 'market_cap'] = int(close_val * listed_shares)
                        if pd.isna(df_all.loc[df_all['ticker'] == ticker, 'name'].iloc[0]) or df_all.loc[df_all['ticker'] == ticker, 'name'].iloc[0] == '':
                            df_all.loc[df_all['ticker'] == ticker, 'name'] = out.get('hts_kor_isnm', '')
                    time.sleep(0.05) # KIS API rate limit 방지
            except Exception as e:
                print(f"[Warning] 누락 데이터 개별 조회 중 오류: {e}")
                
        text_columns = ['ticker', 'name', 'sector', 'theme']
        for col in text_columns:
            if col in df_all.columns:
                df_all[col] = df_all[col].fillna('')
        numeric_columns = [col for col in df_all.columns if col not in text_columns]
        for col in numeric_columns:
            df_all[col] = pd.to_numeric(df_all[col], errors='coerce').fillna(0)

        df_all = self._exclude_exchange_traded_products(df_all)
        
        # --- 카테고리별 추출 ---
        # 1) 거래대금 상위 60위
        df_vol_top = df_all.sort_values(by='trading_value', ascending=False).head(60).copy()
        
        # 2) 외국인 순매수 상위 30위
        if is_nxt_afterhours:
            df_for_top = df_all.iloc[0:0].copy()
        else:
            df_for_top = df_all.sort_values(by='foreign_net', ascending=False).head(30).copy()
        
        # 3) 기관 순매수 상위 30위
        if is_nxt_afterhours:
            df_inst_top = df_all.iloc[0:0].copy()
        else:
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
            df['sector'] = df.apply(
                lambda row: self._normalize_sector(row.get('ticker', ''), row.get('name', ''), row.get('sector', '')),
                axis=1
            )
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
        return True

if __name__ == "__main__":
    crawler = StockCrawler()
    # 주의: 실제로 실행하면 네이버 크롤링으로 인해 약 20~30초 정도 소요될 수 있습니다.
    # crawler.run()
