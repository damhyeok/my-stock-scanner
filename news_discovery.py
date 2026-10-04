"""Experimental news-first discovery; independent of price/rank filtering.

RSS headlines are unverified evidence. No body/official-source confirmation is
claimed. Collection is bounded and isolated from the original news tables.
"""
import hashlib
import json
import re
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET

import pandas as pd
import requests

from news_ui import render_news_table
from sector_overrides import NAME_SECTOR_OVERRIDES

QUERIES = (
    '(수주 OR 공급계약 OR 낙찰 OR 기술수출) when:3d',
    '(흑자전환 OR 어닝서프라이즈 OR 최대실적 OR 가이던스상향) when:3d',
    '(FDA승인 OR 품목허가 OR 임상성공 OR 임상결과) when:3d',
    '(자사주소각 OR 자사주취득 OR 배당확대) when:3d',
    '(양산 OR 증설 OR 신규사업 OR 전략적제휴) when:3d',
)
KST = timezone(timedelta(hours=9))


def exists(conn, table):
    return bool(conn.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone())


def init_discovery(conn):
    conn.executescript('''
      CREATE TABLE IF NOT EXISTS news_discovery_articles (
        article_id TEXT PRIMARY KEY, ticker TEXT, name TEXT, sector TEXT,
        title TEXT, link TEXT, source TEXT, published_at TEXT, first_seen TEXT);
      CREATE INDEX IF NOT EXISTS idx_discovery_published ON news_discovery_articles(published_at);
      CREATE TABLE IF NOT EXISTS news_discovery_runs (
        date TEXT, session TEXT, observed_at TEXT, feed_count INTEGER,
        failed_count INTEGER, catalog_count INTEGER, matched_count INTEGER,
        PRIMARY KEY(date,session));
      CREATE TABLE IF NOT EXISTS news_discovery_snapshots (
        date TEXT, session TEXT, display_order INTEGER, version_id TEXT,
        PRIMARY KEY(date,session,display_order));
      CREATE TABLE IF NOT EXISTS news_discovery_versions (
        version_id TEXT PRIMARY KEY, payload TEXT);
    ''')


def catalog(conn):
    """Registered universe, not just today's TOP60; no all-market claim."""
    stocks = {}
    for table in ('model_universe_snapshots', 'daily_stocks'):
        cols = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
        if not {'ticker', 'name'}.issubset(cols):
            continue
        sector = 'sector' if 'sector' in cols else "'' AS sector"
        for ticker, name, sector in conn.execute(
                f'SELECT ticker,name,{sector} FROM {table} ORDER BY rowid'):
            if not name or not re.fullmatch(r'\d{6}', str(ticker).zfill(6)):
                continue
            name = str(name).strip()
            # Exclude obvious funds; ordinary shares still need a known symbol.
            if re.search(r'ETF|ETN|KODEX|TIGER|ACE |RISE |SOL |HANARO|KOSEF', name, re.I):
                continue
            stocks[str(ticker).zfill(6)] = dict(ticker=str(ticker).zfill(6), name=name,
                sector=NAME_SECTOR_OVERRIDES.get(name, sector or '기타'))
    return list(stocks.values())


def name_pattern(name):
    ending = r'(?=$|[^가-힣A-Za-z0-9&]|(?:은|는|이|가|의|와|과|도|에|서)(?:[^가-힣A-Za-z0-9]|$))'
    return re.compile(r'(?<![가-힣A-Za-z0-9])' + re.escape(name) + ending, re.I)


def match_stocks(title, stocks):
    text = re.sub(r' - [^-]+$', '', title)
    matches = []
    for stock in stocks:
        name = stock['name']
        # Names such as LS/KT/테스 must not match inside another company/word.
        pattern = stock.get('_pattern') or name_pattern(name)
        if pattern.search(text):
            matches.append(stock)
    # A shorter issuer name contained in a longer issuer is not a second hit.
    return [s for s in matches if not any(s['name'] != t['name'] and
            s['name'] in t['name'] for t in matches)]


def headline_features(title):
    text = re.sub(r'\s+', '', title)
    uncertain = bool(re.search(r'기대|전망|추진|검토|루머|가능성|예정|인수설|합병설', text))
    commentary = bool(re.search(r'특징주|급등|강세|목표주가|추천주', text))
    negative_text = re.sub(r'적자축소|적자감소|손실축소|손실감소', '', text)
    negative = bool(re.search(r'해지|취소|실패|거절|적자전환|횡령|배임|상장폐지|거래정지|쇼크|손실확대', negative_text))
    rules = [
        ('계약·수주', r'수주|공급계약|낙찰|기술수출|라이선스아웃', 5),
        ('실적 개선', r'흑자전환|어닝서프라이즈|최대실적|역대최대|사상최대|가이던스상향|적자축소', 4),
        ('허가·임상', r'FDA.*승인|품목허가|임상.*성공|임상.*유효성|허가획득', 5),
        ('주주환원', r'자사주.*소각|자사주.*취득|자사주.*매입|배당.*확대|배당.*증가', 4),
        ('사업·투자', r'양산|증설|신규사업|전략적제휴|MOU|업무협약', 2),
    ]
    topic, pattern, content = next((r for r in rules if re.search(r[1], text, re.I)), ('기타', '', 0))
    if negative:
        group = '악재·혼재 확인'
    elif not content:
        group = '방향 확인 필요'
    elif uncertain or commentary or re.search(r'MOU|업무협약', text, re.I):
        group = '기대·해설 보도'
    else:
        group = '호재 후보'
    concrete = bool(re.search(r'체결|공시|획득|승인|완료|결정|달성|발표', text))
    clarity = 0 if group != '호재 후보' else 2 if concrete else 1
    return dict(topic=topic, group=group, content_score=content,
                clarity_score=clarity, evidence='제목만 확인 · 원문/공시 미검증')


def normalize(title, name):
    title = re.sub(r' - [^-]+$', '', title).replace(name, '')
    return re.sub(r'[^가-힣a-zA-Z0-9]', '', title).lower()


def build_candidates(articles, observed_at, limit=80):
    observed = datetime.fromisoformat(observed_at)
    events = []
    for item in sorted(articles, key=lambda a: (a['published_at'], a['article_id'])):
        if not item['published_at'] or item['published_at'] > observed_at:
            continue
        features = headline_features(item['title'])
        norm = normalize(item['title'], item['name'])
        event = next((e for e in events if e['ticker'] == item['ticker'] and
            e['topic'] == features['topic'] and
            re.findall(r'\d+', normalize(e['title'], e['name'])) == re.findall(r'\d+', norm) and
            SequenceMatcher(None, normalize(e['title'], e['name']), norm).ratio() >= .80), None)
        if event is None:
            event = dict(item, **features, articles=[])
            events.append(event)
        elif features['group'] == '악재·혼재 확인':
            event['group'] = features['group']
        event['articles'].append({k: item[k] for k in ('title','link','source','published_at')})
        event['first_seen'] = min(event['first_seen'], item['first_seen'])
    for event in events:
        age = max(0, (observed - datetime.fromisoformat(event['published_at'])).total_seconds()/3600)
        event['freshness_score'] = 2 if age <= 6 else 1 if age <= 24 else 0
        event['source_count'] = len({a['source'].strip().lower() for a in event['articles'] if a['source'].strip()})
        event['spread_bonus'] = min(.5, max(0, event['source_count']-1)*.1)
        event['score'] = event['content_score'] + event['clarity_score'] + event['freshness_score'] + event['spread_bonus']
        event['freshness'] = '발행6시간 이내' if age <= 6 else '발행24시간 이내' if age <= 24 else '이전 보도'
        # Repeated coverage never changes the first publication/freshness anchor.
        event['articles'] = event['articles'][:1] + event['articles'][-5:] if len(event['articles']) > 6 else event['articles']
    # Keep each class represented even when the feed contains many noisy stories.
    ranked = sorted(events, key=lambda e: e['article_id'])
    ranked.sort(key=lambda e: e['published_at'], reverse=True)
    ranked.sort(key=lambda e: e['score'], reverse=True)
    selected = []
    for group, cap in [('호재 후보', 50), ('기대·해설 보도', 20), ('악재·혼재 확인', 5), ('방향 확인 필요', 5)]:
        selected.extend([e for e in ranked if e['group'] == group][:cap])
    return selected[:limit]


def fetch_feed(query):
    url = 'https://news.google.com/rss/search?q=' + quote_plus(query) + '&hl=ko&gl=KR&ceid=KR:ko'
    response = requests.get(url, headers={'User-Agent':'Mozilla/5.0'}, timeout=6)
    response.raise_for_status()
    items = []
    for node in ET.fromstring(response.content).findall('./channel/item')[:50]:
        try:
            published = parsedate_to_datetime(node.findtext('pubDate', '')).astimezone(KST).strftime('%Y-%m-%d %H:%M:%S')
        except (ValueError, TypeError):
            continue
        items.append(dict(title=node.findtext('title', '').strip(), link=node.findtext('link', '').strip(),
                          source=node.findtext('source', '').strip(), published_at=published))
    return sorted(items, key=lambda a: a['published_at'], reverse=True)


def collect_discovery(db_path, date, session, observed_at=None, fetch=fetch_feed):
    observed_at = observed_at or datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')
    # Never attach today's news to an older price snapshot.
    if observed_at[:10].replace('-', '') != str(date):
        return {'status':'skipped_old_date'}
    with closing(sqlite3.connect(db_path)) as conn:
        init_discovery(conn)
        if conn.execute('SELECT 1 FROM news_discovery_runs WHERE date=? AND session=?', (date,session)).fetchone():
            return {'status':'already_saved'}
        stocks = catalog(conn)
        if not stocks:
            return {'status':'empty_catalog'}
        for stock in stocks:
            stock['_pattern'] = name_pattern(stock['name'])
        start = time.monotonic()
        feeds, failed, matched = 0, 0, []
        for query in QUERIES:
            if time.monotonic() - start >= 32:
                failed += len(QUERIES)-feeds
                break
            feeds += 1
            try:
                items = fetch(query)
            except Exception:
                failed += 1
                continue
            for item in items:
                if not item['title'] or not item['link'] or not item['published_at'] or item['published_at'] > observed_at:
                    continue
                for stock in match_stocks(item['title'], stocks):
                    aid = hashlib.sha256((stock['ticker']+'|'+item['link']).encode()).hexdigest()
                    matched.append((aid, stock['ticker'], stock['name'], stock['sector'], item['title'],
                                    item['link'], item['source'], item['published_at'], observed_at))
        with conn:
            conn.executemany('INSERT OR IGNORE INTO news_discovery_articles VALUES (?,?,?,?,?,?,?,?,?)', matched)
            # Include the prior trading day's post-close issues across weekends.
            previous = conn.execute("SELECT MAX(date) FROM daily_stocks WHERE date<?", (date,)).fetchone()[0]
            window = datetime.strptime(previous, '%Y%m%d').strftime('%Y-%m-%d')+' 15:30:00' if previous else observed_at[:10]+' 00:00:00'
            conn.row_factory = sqlite3.Row
            articles = [dict(r) for r in conn.execute('SELECT * FROM news_discovery_articles WHERE published_at>=? AND first_seen<=? ORDER BY published_at DESC LIMIT 2000', (window,observed_at))]
            events = build_candidates(articles, observed_at)
            for i, event in enumerate(events):
                payload = json.dumps(event,ensure_ascii=False,sort_keys=True)
                vid = hashlib.sha256(payload.encode()).hexdigest()
                conn.execute('INSERT OR IGNORE INTO news_discovery_versions VALUES (?,?)', (vid,payload))
                conn.execute('INSERT INTO news_discovery_snapshots VALUES (?,?,?,?)', (date,session,i,vid))
            conn.execute('INSERT INTO news_discovery_runs VALUES (?,?,?,?,?,?,?)',
                         (date,session,observed_at,feeds,failed,len(stocks),len({r[0] for r in matched})))
            prune_discovery(conn)
        return {'status':'partial' if failed else 'success', 'events':len(events), 'matched':len({r[0] for r in matched}), 'failed':failed}


def prune_discovery(conn):
    if not exists(conn, 'news_discovery_runs'):
        return
    conn.execute('DELETE FROM news_discovery_snapshots WHERE date NOT IN (SELECT DISTINCT date FROM news_discovery_runs ORDER BY date DESC LIMIT 90)')
    conn.execute('DELETE FROM news_discovery_runs WHERE date NOT IN (SELECT DISTINCT date FROM news_discovery_runs ORDER BY date DESC LIMIT 90)')
    conn.execute('DELETE FROM news_discovery_versions WHERE version_id NOT IN (SELECT version_id FROM news_discovery_snapshots)')
    # Article pool has a hard rolling time bound, including partially matched feeds.
    latest = conn.execute('SELECT MAX(observed_at) FROM news_discovery_runs').fetchone()[0]
    if latest:
        cutoff = (datetime.fromisoformat(latest)-timedelta(days=10)).strftime('%Y-%m-%d %H:%M:%S')
        conn.execute('DELETE FROM news_discovery_articles WHERE first_seen<?', (cutoff,))


def build_discovery_display(conn):
    if not exists(conn, 'news_discovery_runs'):
        return
    for source in ('news_discovery_runs', 'news_discovery_snapshots'):
        target = 'web_'+source
        conn.execute(f'DROP TABLE IF EXISTS {target}')
        conn.execute(f'CREATE TABLE {target} AS SELECT * FROM {source} WHERE date IN (SELECT DISTINCT date FROM news_discovery_runs ORDER BY date DESC LIMIT 15)')
        conn.execute(f'CREATE INDEX idx_{target}_session ON {target}(date,session)')
    conn.execute('DROP TABLE IF EXISTS web_news_discovery_versions')
    conn.execute('CREATE TABLE web_news_discovery_versions AS SELECT * FROM news_discovery_versions WHERE version_id IN (SELECT version_id FROM web_news_discovery_snapshots)')
    conn.execute('CREATE UNIQUE INDEX idx_web_news_discovery_version ON web_news_discovery_versions(version_id)')


def render_discovery_tab(st, db_path, date, session, prices=None):
    st.caption('비교용 실험 탭 · 주가 상승률은 점수·정렬·제외 조건에 반영하지 않습니다.')
    reused = False
    with closing(sqlite3.connect(db_path)) as conn:
        run = conn.execute('SELECT * FROM web_news_discovery_runs WHERE date=? AND session=?', (date,session)).fetchone() if exists(conn, 'web_news_discovery_runs') else None
        records = []
        if run is not None:
            records = [json.loads(r[0]) for r in conn.execute('SELECT v.payload FROM web_news_discovery_snapshots s JOIN web_news_discovery_versions v USING(version_id) WHERE s.date=? AND s.session=? ORDER BY s.display_order', (date,session))]
        elif all(exists(conn, t) for t in ('web_news_issue_runs','web_news_issue_versions','web_news_issue_snapshots')):
            old_run = conn.execute('SELECT observed_at FROM web_news_issue_runs WHERE date=? AND session=?', (date,session)).fetchone()
            if old_run:
                articles = {}
                for ticker,name,sector,evidence in conn.execute('SELECT v.ticker,v.name,v.sector,v.evidence_json FROM web_news_issue_snapshots s JOIN web_news_issue_versions v USING(version_id) WHERE s.date=? AND s.session=?', (date,session)):
                    for a in json.loads(evidence):
                        aid = hashlib.sha256((ticker+'|'+a['link']).encode()).hexdigest()
                        articles[aid] = dict(a,article_id=aid,ticker=ticker,name=name,sector=sector)
                records = build_candidates(list(articles.values()), old_run[0])
                run = (date,session,old_run[0],0,0,0,len(articles))
                reused = True
        if run is None:
            st.info('새 뉴스 탐색 방식의 결과는 다음 자동 분석부터 쌓입니다. 선택한 회차에는 비교할 뉴스 자료가 없습니다.')
            return
    if reused:
        st.info('이 회차는 기존 수집 기사를 새 기준으로 재정리한 비교 자료입니다. TOP60 밖의 이슈 검색은 다음 자동 분석부터 반영됩니다.')
        st.caption(f'기존 수집 기준 {run[2]} · 기사 {run[6]}건 재평가')
    else:
        st.caption(f'수집 {run[2]} · 이슈 검색 {run[3]}회 · DB 등록 {run[5]:,}종목과 제목 대조 · 기사 연결 {run[6]}건')
    if run[4]:
        st.warning(f'이슈 검색 {run[4]}회 실패/미완료. 이번 결과는 일부만 수집됐습니다.')
    st.caption('TOP60 밖의 등록 종목도 탐색합니다. 전체 상장종목·모든 기사 탐색을 보장하지 않습니다. 기사 본문·공시 대조 전이므로 확정 호재가 아닙니다.')
    if not records:
        st.info('이번 회차까지 연결된 뉴스 후보가 없습니다.')
        return
    frame = pd.DataFrame(records)
    rates = {}
    if prices is not None and not prices.empty and {'ticker','fluctuation_rate'}.issubset(prices.columns):
        rates = prices.drop_duplicates('ticker').set_index('ticker')['fluctuation_rate'].to_dict()
    frame['참고 등락률(%)'] = frame['ticker'].map(rates)
    show = frame.rename(columns={'name':'종목','sector':'업종','title':'핵심 이슈','topic':'이슈 유형',
        'score':'뉴스 검토점수','freshness':'신규성','source_count':'매체 수','published_at':'최초 발행'})
    for label, group in [('새 호재 검토 후보','호재 후보'), ('기대·해설 보도 — 보조 자료','기대·해설 보도')]:
        st.subheader(label)
        part = show[show['group'] == group]
        render_news_table(st, part[['종목','업종','핵심 이슈','뉴스 검토점수','신규성','참고 등락률(%)','매체 수','최초 발행']], f'news2-{group}-{date}-{session}')
    with st.expander('판단 근거·원문 / 점수 설명'):
        st.write('내용 0~5점 + 제목의 구체적 발표 표현 0~2점 + 최초 기사 발행 후 6시간 이내 2점/24시간 이내 1점 + 매체 확산 최대 0.5점. 기대·추측·주가 해설은 별도로 분리합니다. 이 점수는 상승 확률이 아닙니다. 반복 보도는 최초 발행 시점을 새로 만들지 않습니다.')
        st.caption('등락률은 선택 회차의 기존 가격 자료가 있는 종목만 표시합니다. 빈 값 때문에 뉴스 후보가 제외되지는 않습니다. 중복 이슈는 제목 유사도 기준이라 완전하지 않습니다.')
        index = st.selectbox('원문 확인', range(len(records)), format_func=lambda i: records[i]['name']+' · '+records[i]['title'], key=f'news2-evidence-{date}-{session}')
        issue = records[index]
        st.write(f"{issue['group']} · {issue['topic']} · {issue['evidence']}")
        st.caption(f"내용 {issue['content_score']} + 표현 {issue['clarity_score']} + 신규성 {issue['freshness_score']} + 확산 {issue['spread_bonus']:.1f} / 최초 탐지 {issue['first_seen']}")
        for article in issue['articles']:
            st.write(article['title'])
            if article['link'].startswith(('https://','http://')):
                st.link_button('기사 열기', article['link'])
            st.caption(article['source']+' · '+article['published_at'])
    with st.expander('악재·혼재 / 방향 확인 필요'):
        part = show[show['group'].isin(['악재·혼재 확인','방향 확인 필요'])]
        render_news_table(st, part[['종목','핵심 이슈','뉴스 검토점수','최초 발행']], f'news2-risk-{date}-{session}')
