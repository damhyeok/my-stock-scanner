"""Independent, bounded KRX broker-quantity collection (never an order API)."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextlib import closing
from datetime import datetime, timedelta
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import tempfile
import time

import requests
from market_calendar import KST, is_krx_closed


def numeric(value):
    try:
        value = float(str(value).replace(',', ''))
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def rows(value):
    return value if isinstance(value, list) else [value] if isinstance(value, dict) else []


def init_tables(conn):
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS foreign_broker_members (
        broker_code TEXT PRIMARY KEY, broker_name TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS foreign_broker_universe (
        trade_date TEXT NOT NULL, ticker TEXT NOT NULL, name TEXT NOT NULL,
        PRIMARY KEY(trade_date,ticker));
    CREATE TABLE IF NOT EXISTS foreign_broker_daily (
        trade_date TEXT NOT NULL, ticker TEXT NOT NULL, broker_code TEXT NOT NULL,
        buy_qty INTEGER NOT NULL, sell_qty INTEGER NOT NULL, net_qty INTEGER NOT NULL,
        is_final INTEGER NOT NULL, collected_at TEXT NOT NULL,
        PRIMARY KEY(trade_date,ticker,broker_code));
    CREATE TABLE IF NOT EXISTS foreign_broker_closes (
        trade_date TEXT NOT NULL, ticker TEXT NOT NULL, close REAL NOT NULL,
        collected_at TEXT NOT NULL, PRIMARY KEY(trade_date,ticker));
    CREATE TABLE IF NOT EXISTS foreign_broker_attempts (
        trade_date TEXT NOT NULL, ticker TEXT NOT NULL, broker_code TEXT NOT NULL,
        status TEXT NOT NULL, checked_at TEXT NOT NULL,
        PRIMARY KEY(trade_date,ticker,broker_code));
    CREATE TABLE IF NOT EXISTS foreign_broker_runs (
        started_at TEXT PRIMARY KEY, trade_date TEXT NOT NULL, status TEXT NOT NULL,
        expected_pairs INTEGER NOT NULL, completed_pairs INTEGER NOT NULL,
        requests_done INTEGER NOT NULL, errors INTEGER NOT NULL, finished_at TEXT);
    CREATE INDEX IF NOT EXISTS foreign_broker_daily_stock ON foreign_broker_daily(ticker,trade_date);
    ''')


def parse_members(text):
    result = {}
    # The official master uses the last character before newline as 0/1.
    for raw in text.splitlines():
        line = raw.rstrip()
        if len(line) > 6 and line[-1] == '1':
            code, name = line[:5].strip(), line[5:-1].strip()
            if code.isdigit() and code != '99999' and name:
                result[code] = name
    if not result:
        raise ValueError('foreign broker master unavailable')
    return result


def parse_daily(payload, trade_date):
    for row in rows(payload.get('output')):
        if str(row.get('stck_bsop_date')) != trade_date:
            continue
        buy, sell, net = [numeric(row.get(k)) for k in ['total_shnu_qty', 'total_seln_qty', 'ntby_qty']]
        if any(v is None or v != int(v) for v in (buy, sell, net)) or buy < 0 or sell < 0:
            raise ValueError('invalid quantities')
        if int(buy) - int(sell) != int(net):
            raise ValueError('net quantity mismatch')
        return int(buy), int(sell), int(net)
    return None  # Empty/another day's response is NOT zero activity.


def table_exists(conn, name):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def collect_universe(conn, day):
    """Union of all scanner query rows, model universe, ETF proxies and watchlist."""
    result = {}
    for table, date_col, code_col, name_col in [
        ('daily_stocks', 'date', 'ticker', 'name'),
        ('model_universe_snapshots', 'snapshot_date', 'ticker', 'name'),
        ('etf_sector_snapshots', 'trade_date', 'ticker', 'name'),
    ]:
        if not table_exists(conn, table):
            continue
        columns = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
        if not {date_col, code_col, name_col}.issubset(columns):
            continue
        # Model candidates/ETF queries may complete later; use latest known query set.
        source_day = conn.execute(f'SELECT MAX({date_col}) FROM {table} WHERE {date_col}<=?', (day,)).fetchone()[0]
        if source_day:
            for ticker, name in conn.execute(f'SELECT {code_col},{name_col} FROM {table} WHERE {date_col}=?', (source_day,)):
                if ticker:
                    result[str(ticker).zfill(6)] = str(name or ticker)
    if table_exists(conn, 'watchlist_items'):
        for ticker, name in conn.execute('SELECT ticker,name FROM watchlist_items WHERE added_date<=?', (day,)):
            result[str(ticker).zfill(6)] = str(name or ticker)
    return {code: name for code, name in result.items() if len(code) == 6 and code.isalnum()}


class ForeignBrokerCollector:
    def __init__(self, crawler, budget_seconds=480, now=None):
        self.crawler = crawler
        self.db_path = str(crawler.db_path)
        self.now = now or datetime.now(KST)
        self.budget = max(1, min(float(budget_seconds), 900))
        self.rate_lock = threading.Lock()
        self.next_request = 0.0
        self.token = None

    def request(self, path, tr, params):
        # At most four requests/s across workers; leaves room for existing jobs.
        with self.rate_lock:
            delay = max(0, self.next_request - time.monotonic())
            if delay:
                time.sleep(delay)
            self.next_request = time.monotonic() + .25
        response = requests.get(self.crawler.kis_base_url + path, params=params,
                                headers={'authorization': 'Bearer ' + self.token,
                                         'appkey': self.crawler.kis_app_key,
                                         'appsecret': self.crawler.kis_app_secret,
                                         'tr_id': tr, 'custtype': 'P'}, timeout=(3, 10))
        response.raise_for_status()
        data = response.json()
        if str(data.get('rt_cd')) != '0':
            raise RuntimeError('KIS quote error: ' + str(data.get('msg_cd', 'unknown')))
        return data

    def refresh_members(self, conn, stamp):
        recent = conn.execute('SELECT MAX(updated_at) FROM foreign_broker_members').fetchone()[0]
        if recent and recent >= (self.now - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S'):
            return dict(conn.execute('SELECT broker_code,broker_name FROM foreign_broker_members'))
        try:
            response = requests.get('https://new.real.download.dws.co.kr/common/master/memcode.mst', timeout=(3, 10))
            response.raise_for_status()
            members = parse_members(response.content.decode('cp949'))
            # Preserve names for historical records, never add the aggregate pseudo-member.
            conn.executemany('INSERT OR REPLACE INTO foreign_broker_members VALUES (?,?,?)',
                             [(code, name, stamp) for code, name in members.items()])
            conn.commit()
            return members
        except Exception:
            members = dict(conn.execute('SELECT broker_code,broker_name FROM foreign_broker_members'))
            if not members:
                raise
            return members

    def fetch(self, item):
        day, ticker, broker = item
        if broker == '__CLOSE__':
            payload = self.request('/uapi/domestic-stock/v1/quotations/inquire-daily-price', 'FHKST01010400',
                                   {'FID_COND_MRKT_DIV_CODE': 'J', 'FID_INPUT_ISCD': ticker,
                                    'FID_PERIOD_DIV_CODE': 'D', 'FID_ORG_ADJ_PRC': '1'})
            price = next((numeric(r.get('stck_clpr')) for r in rows(payload.get('output'))
                          if str(r.get('stck_bsop_date')) == day), None)
            return price if price is not None and price > 0 else None
        payload = self.request('/uapi/domestic-stock/v1/quotations/inquire-member-daily', 'FHPST04540000',
                               {'FID_COND_MRKT_DIV_CODE': 'J', 'FID_INPUT_ISCD': ticker,
                                'FID_INPUT_ISCD_2': broker, 'FID_INPUT_DATE_1': day,
                                'FID_INPUT_DATE_2': day, 'FID_SCTN_CLS_CODE': ''})
        return parse_daily(payload, day)

    def run(self):
        stamp = self.now.strftime('%Y-%m-%d %H:%M:%S')
        today = self.now.strftime('%Y%m%d')
        with closing(sqlite3.connect(self.db_path, timeout=15)) as conn:
            init_tables(conn)
            if not table_exists(conn, 'daily_stocks'):
                return {'status': 'no_universe'}
            day = conn.execute('SELECT MAX(date) FROM daily_stocks WHERE date<=?', (today,)).fetchone()[0]
            if not day:
                return {'status': 'no_universe'}
            members = self.refresh_members(conn, stamp)
            universe = collect_universe(conn, day)
            conn.executemany('INSERT OR REPLACE INTO foreign_broker_universe VALUES (?,?,?)',
                             [(day, code, name) for code, name in universe.items()])
            conn.commit()
            expected = len(universe) * len(members)
            # Previous recorded days keep their own target sets, even after leaving a ranking.
            targets = conn.execute('SELECT u.trade_date,u.ticker FROM foreign_broker_universe u WHERE u.trade_date IN '
                                   '(SELECT DISTINCT trade_date FROM foreign_broker_universe ORDER BY trade_date DESC LIMIT 3) '
                                   'OR EXISTS (SELECT 1 FROM foreign_broker_daily d WHERE d.trade_date=u.trade_date '
                                   'AND d.ticker=u.ticker AND d.is_final=0) ORDER BY u.trade_date DESC,u.ticker').fetchall()
            known = {(d, t, b): (final, collected) for d,t,b,final,collected in conn.execute(
                'SELECT trade_date,ticker,broker_code,is_final,collected_at FROM foreign_broker_daily WHERE trade_date>=?', (min(d for d,t in targets),))}
            attempts = {(d,t,b): at for d,t,b,at in conn.execute('SELECT trade_date,ticker,broker_code,checked_at FROM foreign_broker_attempts')}
            refresh_before = (self.now - timedelta(minutes=30)).strftime('%Y-%m-%d %H:%M:%S')
            work = []
            for date, ticker in targets:
                for code in members:
                    key = (date, ticker, code)
                    previous = known.get(key)
                    if previous and previous[0]:
                        continue
                    last = attempts.get(key, '')
                    if last and last > refresh_before:
                        continue
                    work.append((date, ticker, code))
            # New/unqueried pairs first, then oldest observations. No repeat starvation.
            work.sort(key=lambda k: (0 if k not in known else 1, attempts.get(k, ''), -int(k[0]), k[1], k[2]))
            close_keys = set(conn.execute('SELECT trade_date,ticker FROM foreign_broker_closes'))
            close_work = [(d,t,'__CLOSE__') for d,t in targets if (d,t) not in close_keys and
                          (d < today or self.now.hour * 60 + self.now.minute >= 16 * 60 + 40)]
            # Prices are optional references, collected first once, never instead of quantities.
            work = close_work + work
            conn.execute('INSERT OR REPLACE INTO foreign_broker_runs VALUES (?,?,?,?,?,?,?,?)',
                         (stamp, day, 'running', expected, 0, 0, 0, None)); conn.commit()
            self.token = self.crawler._get_kis_access_token()
            deadline = time.monotonic() + self.budget
            done, failures = 0, 0
            iterator = iter(work)
            with ThreadPoolExecutor(max_workers=3) as pool:
                pending = {}
                def submit():
                    if time.monotonic() >= deadline:
                        return
                    item = next(iterator, None)
                    if item:
                        pending[pool.submit(self.fetch, item)] = item
                for _ in range(3):
                    submit()
                while pending:
                    finished, _ = wait(pending, return_when=FIRST_COMPLETED)
                    for future in finished:
                        date, ticker, broker = pending.pop(future)
                        observed = datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')
                        try:
                            value = future.result()
                            if value is None:
                                status = 'empty'
                                failures += 1
                            else:
                                status = 'success'
                                if broker == '__CLOSE__':
                                    conn.execute('INSERT OR REPLACE INTO foreign_broker_closes VALUES (?,?,?,?)', (date,ticker,value,observed))
                                else:
                                    conn.execute('INSERT OR REPLACE INTO foreign_broker_daily VALUES (?,?,?,?,?,?,?,?)',
                                                 (date,ticker,broker,*value,int(date < today),observed))
                        except Exception:
                            status = 'error'
                            failures += 1
                        conn.execute('INSERT OR REPLACE INTO foreign_broker_attempts VALUES (?,?,?,?,?)',
                                     (date,ticker,broker,status,observed)); conn.commit()
                        done += 1
                        if done % 100 == 0:
                            print(f'[Foreign Broker] requests={done}, errors_or_empty={failures}', flush=True)
                        submit()
            completed = conn.execute('SELECT COUNT(*) FROM foreign_broker_daily d JOIN foreign_broker_universe u '
                                     'ON d.trade_date=u.trade_date AND d.ticker=u.ticker WHERE d.trade_date=?', (day,)).fetchone()[0]
            status = 'success' if completed >= expected and not failures else 'partial'
            conn.execute('UPDATE foreign_broker_runs SET status=?,completed_pairs=?,requests_done=?,errors=?,finished_at=? WHERE started_at=?',
                         (status,completed,done,failures,datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S'),stamp))
            conn.commit()
            return {'status': status, 'trade_date': day, 'expected_pairs': expected,
                    'completed_pairs': completed, 'requests_done': done, 'errors': failures}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default='stock_data.db')
    parser.add_argument('--budget-seconds', type=int, default=480)
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--publish-only', action='store_true')
    args = parser.parse_args()
    # A timer never fabricates a new business date on a holiday.
    if is_krx_closed() and not args.publish_only:
        print('[Foreign Broker] market closed; skipped')
        return
    result = {'requests_done': 0}
    if not args.publish_only:
        from crawler import StockCrawler
        result = ForeignBrokerCollector(StockCrawler(db_path=args.db), args.budget_seconds).run()
        print(result, flush=True)
    if args.publish_only:
        publish_broker_snapshot(args.db, Path(__file__).resolve().parent / 'web_data.db')
    elif args.publish and result.get('requests_done', 0):
        project = Path(__file__).resolve().parent
        # API collection does not hold the legacy analysis lock. Acquire it only
        # for atomic web publication and defer if an existing analysis is busy.
        subprocess.run(['flock', '-w', '30', str(project / '.cloud_data.lock'), sys.executable,
                        str(project / 'foreign_broker_flow.py'), '--db', args.db, '--publish-only'], timeout=300, check=True)


def check_web_snapshot(conn):
    if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
        raise ValueError('web database integrity error')


def publish_broker_snapshot(source, target):
    """Refresh only broker tables; never rerun stock scoring or other collectors."""
    from web_database import compress_web_database
    target = Path(target)
    if not target.is_file():
        raise FileNotFoundError('Existing web snapshot required')
    fd, temporary = tempfile.mkstemp(prefix='broker_web_', suffix='.db', dir=target.parent)
    os.close(fd)
    try:
        with closing(sqlite3.connect(target)) as old, closing(sqlite3.connect(temporary)) as conn:
            old.backup(conn)
            init_tables(conn)
            conn.execute('ATTACH DATABASE ? AS broker_source', (str(Path(source).resolve()),))
            cutoff = conn.execute('SELECT MIN(trade_date) FROM (SELECT DISTINCT trade_date FROM broker_source.foreign_broker_universe ORDER BY trade_date DESC LIMIT 60)').fetchone()[0]
            for table in ['foreign_broker_members','foreign_broker_universe','foreign_broker_daily','foreign_broker_closes','foreign_broker_runs']:
                conn.execute(f'DELETE FROM {table}')
                if table in ['foreign_broker_members', 'foreign_broker_runs']:
                    tail = ' ORDER BY started_at DESC LIMIT 60' if table.endswith('runs') else ''
                    conn.execute(f'INSERT INTO {table} SELECT * FROM broker_source.{table}' + tail)
                elif cutoff:
                    conn.execute(f'INSERT INTO {table} SELECT * FROM broker_source.{table} WHERE trade_date>=?', (cutoff,))
            conn.execute('DROP TABLE IF EXISTS foreign_broker_attempts')
            conn.commit()
            # Release the source read lock before checking the much larger web
            # copy. Otherwise a concurrent collector cannot commit quantities.
            conn.execute('DETACH DATABASE broker_source')
            check_web_snapshot(conn)
        os.replace(temporary, target)
        compress_web_database(target, str(target) + '.gz')
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


if __name__ == '__main__':
    main()
