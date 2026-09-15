"""Bounded, observation-time news issues. Titles are evidence, not verified facts."""
import hashlib
import html
import json
import re
import sqlite3
from contextlib import closing
from difflib import SequenceMatcher
from datetime import datetime, timedelta

import pandas as pd
from news_price_response import build_price_display, render_price_context, columns


def init_issues(conn):
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS news_issue_articles (
            article_id TEXT PRIMARY KEY, event_id TEXT, ticker TEXT,
            title TEXT, link TEXT, source TEXT, published_at TEXT,
            first_seen TEXT, observed_date TEXT);
        CREATE INDEX IF NOT EXISTS idx_issue_article_event ON news_issue_articles(event_id);
        CREATE TABLE IF NOT EXISTS news_issue_versions (
            version_id TEXT PRIMARY KEY, event_id TEXT, ticker TEXT, name TEXT,
            sector TEXT, observed_date TEXT, first_seen TEXT, changed_at TEXT,
            title TEXT, topic TEXT, direction TEXT, confidence TEXT,
            importance REAL, spread_bonus REAL, source_count INTEGER,
            article_count INTEGER, reason TEXT, evidence_json TEXT);
        CREATE INDEX IF NOT EXISTS idx_issue_version_event ON news_issue_versions(event_id,changed_at);
        CREATE TABLE IF NOT EXISTS news_issue_snapshots (
            date TEXT, session TEXT, event_id TEXT, version_id TEXT,
            change_kind TEXT, observed_at TEXT,
            PRIMARY KEY(date,session,event_id));
        CREATE TABLE IF NOT EXISTS news_issue_runs (
            date TEXT, session TEXT, observed_at TEXT, status TEXT,
            queried_count INTEGER, failed_count INTEGER,
            PRIMARY KEY(date,session));
    ''')


def classify(title):
    topics = [('계약·수주', r'계약|수주|낙찰'), ('실적', r'실적|매출|영업이익|흑자|적자'),
              ('바이오·허가', r'임상|FDA|허가|기술수출'), ('주주환원', r'자사주|소각|배당'),
              ('자금조달', r'증자|전환사채|유상|CB발행'), ('사업·투자', r'투자|증설|MOU|협약'),
              ('위험', r'횡령|배임|상장폐지|거래정지')]
    topic = next((name for name, pattern in topics if re.search(pattern, title, re.I)), '기타')
    uncertain = bool(re.search(r'기대|전망|추진|검토|인수설|합병설|루머|가능성|예정', title))
    negative = bool(re.search(r'해지|취소|실패|거절|적자|쇼크|횡령|배임|폐지|하향|감소|손실', title))
    positive = bool(re.search(r'체결|수주|승인|성공|흑자전환|소각|상향|최대|확대|증가', title))
    direction = '혼재' if negative and positive else '악재 가능' if negative else '호재 가능' if positive else '방향 미확인'
    confidence = '기대·검토 보도' if uncertain else '제목 보도·원문 확인 필요'
    importance = 3 if topic in ('계약·수주', '실적', '바이오·허가', '위험', '자금조달') else 2 if topic != '기타' else 1
    if uncertain or re.search(r'급등|강세|특징주|목표주가', title):
        importance = min(importance, 1)
    return topic, direction, confidence, importance


def normalized(title, name='', source=''):
    text = re.sub(r' - [^-]+$', '', str(title))
    text = re.sub(r'\[[^]]*\]', '', text).replace(name, '')
    return re.sub(r'[^가-힣a-zA-Z0-9]', '', text).lower()


def record_issues(conn, stocks, articles, date, session, observed_at, failures=0, window_start=None):
    """Commit completed observations; rerunning a session never rewrites its history."""
    init_issues(conn)
    if conn.execute('SELECT 1 FROM news_issue_runs WHERE date=? AND session=?', (date, session)).fetchone():
        return
    conn.row_factory = sqlite3.Row
    cutoff = (datetime.strptime(date, '%Y%m%d') - timedelta(days=7)).strftime('%Y-%m-%d')
    old = conn.execute('SELECT * FROM news_issue_versions WHERE changed_at>=? AND changed_at<=? ORDER BY changed_at, rowid', (cutoff, observed_at)).fetchall()
    latest = {r['event_id']: dict(r) for r in old}
    changes = {}
    with conn:
        for stock, item in sorted(articles, key=lambda pair: (pair[1].get('published_at', ''), pair[1].get('link', ''))):
            ticker, name = str(stock['ticker']), str(stock['name'])
            title, source, link = item['title'], item.get('source', ''), item['link']
            published = item.get('published_at', '')
            if not published or published > observed_at:
                continue
            aid = hashlib.sha256((ticker + '|' + link).encode()).hexdigest()
            if conn.execute('SELECT 1 FROM news_issue_articles WHERE article_id=?', (aid,)).fetchone():
                continue
            topic, direction, confidence, importance = classify(title)
            norm = normalized(title, name, source)
            candidates = [r for r in latest.values() if r['ticker'] == ticker and r['topic'] == topic]
            event = None
            for candidate in candidates:
                ratio = SequenceMatcher(None, norm, normalized(candidate['title'], name)).ratio()
                # Avoid conflating unrelated contracts or distinct financial periods.
                nums = re.findall(r'\d+', norm)
                old_nums = re.findall(r'\d+', normalized(candidate['title'], name))
                if ratio >= .80 and nums == old_nums:
                    event = candidate
                    break
            eid = event['event_id'] if event else aid
            conn.execute('INSERT INTO news_issue_articles VALUES (?,?,?,?,?,?,?,?,?)',
                         (aid, eid, ticker, title, link, source, published, observed_at, date))
            evidence = [dict(r) for r in conn.execute('SELECT title,link,source,published_at,first_seen FROM news_issue_articles WHERE event_id=? ORDER BY first_seen DESC,article_id DESC LIMIT 50', (eid,))]
            # Keep the initial report in the bounded evidence list, so repeat
            # coverage never shifts the price comparison anchor forward.
            if event:
                initial = min(json.loads(event['evidence_json']), key=lambda a: a['published_at'])
                if not any(a['link'] == initial['link'] for a in evidence):
                    evidence = evidence[:49] + [initial]
            sources = {r['source'].strip().lower() for r in evidence if r['source'].strip()}
            # RSS titles cannot establish independent reporting. Only a small capped
            # reach bonus is allowed, never an increase in verification confidence.
            bonus = min(.5, max(0, len(sources) - 1) * .1)
            newest_prior = max((a['published_at'] for a in json.loads(event['evidence_json'])), default='') if event else ''
            material = event is not None and published >= newest_prior and (direction != event['direction'] or confidence != event['confidence'])
            if event and not material:
                title, direction, confidence, importance = event['title'], event['direction'], event['confidence'], event['importance']
            first_seen = event['first_seen'] if event else observed_at
            reason = f'{topic} · 제목 기준 중요도 {importance}/3. 금액의 실적 기여·기대 대비 변화·독립 취재 여부는 미확인.'
            payload = dict(event_id=eid, ticker=ticker, name=name, sector=str(stock.get('sector', '')),
                           observed_date=date, first_seen=first_seen, changed_at=observed_at, title=title,
                           topic=topic, direction=direction, confidence=confidence, importance=importance,
                           spread_bonus=bonus, source_count=len(sources), article_count=len(evidence), reason=reason,
                           evidence_json=json.dumps(evidence, ensure_ascii=False))
            vid = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            payload = dict(version_id=vid, **payload)
            conn.execute('INSERT OR IGNORE INTO news_issue_versions VALUES (' + ','.join('?' for _ in payload) + ')', tuple(payload.values()))
            latest[eid] = payload
            kind = '신규' if event is None else '중요 내용 변경' if material else '관련 보도 추가'
            rank = {'신규': 3, '중요 내용 변경': 2, '관련 보도 추가': 1}
            if rank.get(changes.get(eid), 0) < rank[kind]:
                changes[eid] = kind
        # Carry issues observed today, including tickers that left TOP60.
        for eid, event in latest.items():
            if event['observed_date'] != date:
                if not window_start or not any(a['published_at'] >= window_start for a in json.loads(event['evidence_json'])):
                    continue
            conn.execute('INSERT INTO news_issue_snapshots VALUES (?,?,?,?,?,?)',
                         (date, session, eid, event['version_id'], changes.get(eid, '기존 주요 이슈'), observed_at))
        conn.execute('INSERT INTO news_issue_runs VALUES (?,?,?,?,?,?)',
                     (date, session, observed_at, 'partial' if failures else 'success', len(stocks), failures))


def build_issue_display(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='news_issue_snapshots'").fetchone():
        return
    # Existing web retention is 15 saved dates. Keep version references before
    # removing backend tables; do not repeat evidence across every session.
    conn.executescript('''
        DROP TABLE IF EXISTS web_news_issue_snapshots;
        CREATE TABLE web_news_issue_snapshots AS SELECT * FROM news_issue_snapshots
          WHERE date IN (SELECT DISTINCT date FROM news_issue_runs ORDER BY date DESC LIMIT 15);
        CREATE INDEX idx_web_issue_session ON web_news_issue_snapshots(date,session);
        DROP TABLE IF EXISTS web_news_issue_versions;
        CREATE TABLE web_news_issue_versions AS SELECT * FROM news_issue_versions
          WHERE version_id IN (SELECT version_id FROM web_news_issue_snapshots);
        CREATE UNIQUE INDEX idx_web_issue_version ON web_news_issue_versions(version_id);
        DROP TABLE IF EXISTS web_news_issue_runs;
        CREATE TABLE web_news_issue_runs AS SELECT * FROM news_issue_runs
          WHERE date IN (SELECT DISTINCT date FROM news_issue_runs ORDER BY date DESC LIMIT 15);
    ''')
    build_price_display(conn)


def prune_issues(conn):
    init_issues(conn)
    conn.execute('DELETE FROM news_issue_snapshots WHERE date NOT IN (SELECT DISTINCT date FROM news_issue_runs ORDER BY date DESC LIMIT 90)')
    conn.execute('DELETE FROM news_issue_runs WHERE date NOT IN (SELECT DISTINCT date FROM news_issue_runs ORDER BY date DESC LIMIT 90)')
    conn.execute('DELETE FROM news_issue_versions WHERE version_id NOT IN (SELECT version_id FROM news_issue_snapshots)')
    conn.execute('DELETE FROM news_issue_articles WHERE event_id NOT IN (SELECT event_id FROM news_issue_versions)')
    conn.execute('DELETE FROM news_issue_articles WHERE observed_date NOT IN (SELECT DISTINCT date FROM news_issue_runs ORDER BY date DESC LIMIT 90)')
    if columns(conn, 'news_issue_price_context'):
        conn.execute('DELETE FROM news_issue_price_context WHERE date NOT IN (SELECT DISTINCT date FROM news_issue_runs)')


def render_issue_tab(st, db_path, date, session):
    """Return False for historical snapshots, preserving the legacy tab."""
    with closing(sqlite3.connect(db_path)) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='web_news_issue_runs'").fetchone():
            return False
        run = conn.execute('SELECT status,queried_count,failed_count,observed_at FROM web_news_issue_runs WHERE date=? AND session=?', (date, session)).fetchone()
        if run is None:
            return False
        frame = pd.read_sql_query('SELECT s.change_kind,v.* FROM web_news_issue_snapshots s JOIN web_news_issue_versions v USING(version_id) WHERE s.date=? AND s.session=?', conn, params=(date, session))
        if not frame.empty:
            render_price_context(st, conn, date, session, frame)
    st.caption(f'선택 분석 회차까지 확인한 누적 이슈(전 거래일 장후 포함) · 수집 기준 {run[3]}')
    st.caption('제목 기반 1차 분류입니다. 본문·공시 대조 전에는 확정 호재로 표시하지 않습니다. 중요도는 검토 순서이며 상승 확률이 아닙니다.')
    if run[2]:
        st.warning(f'뉴스 수집 {run[1]}종목 중 {run[2]}종목 실패. 기존 이슈는 유지합니다.')
    if frame.empty:
        st.info('현재까지 새로 확인한 이슈가 없습니다.')
        return True
    frame['검토 우선순위'] = frame['importance'] + frame['spread_bonus']
    frame = frame.sort_values(['검토 우선순위','changed_at'], ascending=False)
    for label, part in [('신규·중요 내용 변경', frame[frame.change_kind.isin(['신규','중요 내용 변경'])]), ('오늘 누적 주요 이슈', frame)]:
        st.subheader(label)
        display = part[['name','title','direction','confidence','검토 우선순위','change_kind','source_count','first_seen','changed_at']].rename(columns={'name':'종목','title':'핵심 이슈','direction':'방향','confidence':'확인 상태','change_kind':'변경','source_count':'매체 수','first_seen':'최초 확인','changed_at':'최근 갱신'})
        st.dataframe(display, hide_index=True, use_container_width=True)
    with st.expander('점수·보도 확산 기준'):
        st.write('이슈별 제목 중요도 1~3점 + 보도 확산 최대 0.5점입니다. 서로 다른 매체가 추가될 때 0.1점씩 반영합니다. 같은 링크·같은 매체 반복은 가점이 없고, 재검색만으로 점수가 오르지 않습니다. 제목만으로 보도자료 재전송·독립 취재를 확정할 수 없어 확산을 신뢰도에 반영하지 않습니다. 악재에도 검토 우선순위는 높을 수 있으며 호재 점수와 합산·상쇄하지 않습니다.')
    selected = st.selectbox('원문과 판단 근거를 볼 이슈', list(range(len(frame))),
                            format_func=lambda i: f"{frame.iloc[i]['name']} · {frame.iloc[i]['title']}")
    st.caption('원문 목록은 선택한 이슈의 최근 확인 기사 최대 50건입니다. 매체 수·확산 가점도 이 목록 기준입니다.')
    for _, issue in frame.iloc[[selected]].iterrows():
        with st.expander(f"{issue['name']} · {issue['topic']} · {issue['change_kind']} · {issue['direction']}"):
            st.write(issue['reason'])
            st.caption(f"내용 {issue['importance']:.1f}점 + 확산 {issue['spread_bonus']:.1f}점 / 원문을 확인해 주세요.")
            for article in json.loads(issue['evidence_json']):
                title = html.escape(article['title'])
                link = html.escape(article['link'], quote=True)
                if link.startswith(('https://','http://')):
                    st.markdown(f'<a href="{link}" target="_blank" rel="noopener noreferrer">{title}</a>', unsafe_allow_html=True)
                else:
                    st.write(article['title'])
                st.caption(f"{article['source']} · 발행 {article['published_at']} · 최초 확인 {article['first_seen']}")
    return True
