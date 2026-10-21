from __future__ import annotations

import datetime as dt
import html
import json
import re
import sqlite3
import urllib.parse
import urllib.request
from pathlib import Path

API = 'https://www.ebi.ac.uk/europepmc/webservices/rest/search'
WATCHLIST = [
    ('6ppd-q', '6PPD-quinone', ['6PPD-quinone', '6PPD quinone']),
    ('bps', 'Bisphenol S', ['bisphenol S']),
    ('6ppd', '6PPD', ['6PPD', "N-(1,3-dimethylbutyl)-N'-phenyl-p-phenylenediamine"]),
    ('hfpo-da', 'HFPO-DA', ['HFPO-DA', 'hexafluoropropylene oxide dimer acid']),
]
# GenX alone is deliberately excluded: a trade name does not establish chemical identity.


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def init_db(path, kind='live'):
    with connect(path) as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS chemicals(
            id TEXT PRIMARY KEY, name TEXT NOT NULL, terms TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS runs(
            id INTEGER PRIMARY KEY, chemical_id TEXT REFERENCES chemicals(id),
            started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
            query TEXT NOT NULL, source TEXT NOT NULL, start_year INTEGER, end_year INTEGER,
            source_total INTEGER, fetched INTEGER DEFAULT 0, unique_count INTEGER DEFAULT 0,
            error TEXT, query_version TEXT NOT NULL DEFAULT 'watchlist-v1');
        CREATE TABLE IF NOT EXISTS papers(
            run_id INTEGER REFERENCES runs(id), record_key TEXT NOT NULL,
            source_id TEXT, title TEXT NOT NULL, authors TEXT, journal TEXT,
            year INTEGER, date TEXT, date_basis TEXT, doi TEXT, pmid TEXT,
            abstract TEXT, pub_types TEXT, is_open_access INTEGER,
            PRIMARY KEY(run_id, record_key));
        ''')
        row = db.execute("SELECT value FROM metadata WHERE key='dataset_kind'").fetchone()
        if row and row['value'] != kind:
            raise ValueError('실자료와 데모 DB를 혼합할 수 없습니다. 별도 DB 경로를 사용하세요.')
        db.execute('INSERT OR IGNORE INTO metadata VALUES (?, ?)', ('dataset_kind', kind))
        screening_only=db.execute("SELECT value FROM metadata WHERE key='screening_only'").fetchone()
        if not screening_only or screening_only['value']!='true':
            for cid, name, terms in WATCHLIST:
                db.execute('INSERT OR IGNORE INTO chemicals VALUES (?, ?, ?)', (cid, name, json.dumps(terms)))
    from .research import init_research
    init_research(path)
    with connect(path) as db:
        db.execute("UPDATE chemicals SET name='HFPO-DA' WHERE id='hfpo-da' AND name='HFPO-DA / GenX'")


def query_for(terms, start, end):
    if not 1900 <= start <= end <= dt.date.today().year:
        raise ValueError('연도는 1900년부터 올해까지의 오름차순 범위여야 합니다.')
    # Literal quoted aliases only, never interpolate free-form search expressions.
    names = ' OR '.join('TITLE_ABS:"' + t.replace('"', '').replace('\\', '') + '"' for t in terms)
    cutoff = dt.datetime.now(dt.timezone.utc).date().isoformat() if end == dt.date.today().year else f'{end}-12-31'
    return f'({names}) AND FIRST_PDATE:[{start}-01-01 TO {cutoff}]'


def plain(value):
    return re.sub(r'</?[A-Za-z][^>]*>', ' ', html.unescape(str(value or ''))).strip()


def normalize(item):
    src = str(item.get('source') or '')
    sid = str(item.get('id') or '')
    doi = str(item.get('doi') or '').lower().strip()
    doi = re.sub(r'^https?://(?:dx\.)?doi\.org/', '', doi)
    if doi and not re.fullmatch(r'10\.\d{4,9}/\S+', doi):
        doi = ''
    if not (src and sid):
        raise ValueError('자료원 또는 레코드 ID가 없는 문헌입니다.')
    date = str(item.get('firstPublicationDate') or '')
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', date):
        dt.date.fromisoformat(date)
        year, basis = int(date[:4]), 'firstPublicationDate'
    elif re.fullmatch(r'\d{4}', str(item.get('pubYear') or '')):
        year, basis, date = int(item['pubYear']), 'pubYear (fallback)', ''
    else:
        year, basis, date = None, 'unknown', ''
    types = item.get('pubTypeList') or {}
    return dict(record_key=f'{src}:{sid}', source_id=f'{src}:{sid}',
                title=plain(item.get('title')) or '(제목 미제공)',
                authors=plain(item.get('authorString')), journal=plain(item.get('journalTitle') or
                    (item.get('journalInfo') or {}).get('journal', {}).get('title')),
                year=year, date=date, date_basis=basis, doi=doi,
                pmid=sid if src == 'MED' and sid.isdigit() else '',
                abstract=plain(item.get('abstractText')),
                pub_types=json.dumps(types.get('pubType') or [], ensure_ascii=False),
                is_open_access=int(item.get('isOpenAccess') == 'Y'))


def read_page(params):
    from .research import get_json
    return get_json(API + '?' + urllib.parse.urlencode(params))


def fetch(terms, start, end, max_pages=10, fetch_page=read_page):
    if not 1 <= max_pages <= 50:
        raise ValueError('max_pages 범위는 1–50입니다.')
    query = query_for(terms, start, end)
    cursor, seen_cursors = '*', set()
    papers, doi_seen, id_seen = [], set(), set()
    fetched = 0
    total = None
    for _ in range(max_pages):
        page = fetch_page(dict(query=query, format='json', resultType='core', pageSize=100,
                               cursorMark=cursor))
        if not isinstance(page.get('hitCount'), int) or page['hitCount'] < 0:
            raise ValueError('유효한 hitCount가 없는 API 응답입니다.')
        if total is None:
            total = page['hitCount']
        elif page['hitCount'] != total:
            raise ValueError('페이지 사이 검색 건수가 변했습니다. 다시 수집해야 합니다.')
        raw = page.get('resultList', {}).get('result')
        if not isinstance(raw, list):
            raise ValueError('유효한 resultList가 없는 API 응답입니다.')
        fetched += len(raw)
        for item in raw:
            p = normalize(item)
            duplicate = p['source_id'] in id_seen or bool(p['doi'] and p['doi'] in doi_seen)
            id_seen.add(p['source_id'])
            if p['doi']:
                doi_seen.add(p['doi'])
            if not duplicate:
                papers.append(p)
        if fetched >= total:
            return query, total, fetched, papers, 'complete'
        next_cursor = page.get('nextCursorMark')
        if not raw or not next_cursor or next_cursor == cursor or next_cursor in seen_cursors:
            raise ValueError('검색 총건수에 도달하기 전에 페이지 진행이 중단됐습니다.')
        seen_cursors.add(cursor)
        cursor = next_cursor
    return query, total, fetched, papers, 'partial'


def insert_papers(db, run_id, papers):
    for p in papers:
        fields = ['record_key', 'source_id', 'title', 'authors', 'journal', 'year', 'date',
                  'date_basis', 'doi', 'pmid', 'abstract', 'pub_types', 'is_open_access']
        db.execute('INSERT INTO papers(run_id,' + ','.join(fields) + ') VALUES (' +
                   ','.join('?' for _ in range(len(fields) + 1)) + ')',
                   [run_id] + [p[k] for k in fields])


def collect(path, chemical_id, start, end, max_pages=10, fetch_page=read_page):
    with connect(path) as db:
        kind = db.execute("SELECT value FROM metadata WHERE key='dataset_kind'").fetchone()
        if not kind or kind['value'] != 'live':
            raise ValueError('데모 DB에는 외부 수집을 저장하지 않습니다.')
        chem = db.execute('SELECT * FROM chemicals WHERE id=?', (chemical_id,)).fetchone()
        if not chem:
            raise ValueError('등록되지 않은 물질입니다.')
        query = query_for(json.loads(chem['terms']), start, end)
        rid = db.execute('''INSERT INTO runs(chemical_id,started_at,status,query,source,start_year,end_year)
            VALUES (?,?, 'running',?, 'Europe PMC',?,?)''',
                         (chemical_id, now(), query, start, end)).lastrowid
        from .analysis import key
        db.execute('UPDATE runs SET query_version=? WHERE id=?',('aliases-'+key(chem['terms'])[:12],rid))
    try:
        query, total, fetched, papers, status = fetch(json.loads(chem['terms']), start, end,
                                                     max_pages, fetch_page)
        with connect(path) as db:
            insert_papers(db, rid, papers)
            db.execute('''UPDATE runs SET finished_at=?,status=?,source_total=?,fetched=?,
                       unique_count=? WHERE id=?''', (now(), status, total, fetched, len(papers), rid))
    except Exception as exc:
        with connect(path) as db:
            db.execute("UPDATE runs SET finished_at=?,status='failed',error=? WHERE id=?",
                       (now(), f'{type(exc).__name__}: {exc}'[:500], rid))
        raise
    return rid


def view(path):
    from .analysis import LABELS
    current = dt.date.today().year
    with connect(path) as db:
        kind = db.execute("SELECT value FROM metadata WHERE key='dataset_kind'").fetchone()['value']
        marker = db.execute("SELECT value FROM metadata WHERE key='validation_copy'").fetchone()
        chemicals = []
        for row in db.execute('SELECT * FROM chemicals ORDER BY rowid'):
            c = dict(row)
            c['terms'] = json.loads(c['terms'])
            runs = db.execute('SELECT * FROM runs WHERE chemical_id=? ORDER BY id DESC', (c['id'],)).fetchall()
            c['latest_attempt'] = dict(runs[0]) if runs else None
            complete = next((r for r in runs if r['status'] == 'complete'), None)
            # A partial refresh never silently replaces the last complete snapshot.
            chosen = complete or next((r for r in runs if r['status'] == 'partial'), None)
            c['run'] = dict(chosen) if chosen else None
            c['papers'] = []
            c['trend'] = []
            c['comparison'] = None
            if chosen:
                c['papers'] = [dict(p) for p in db.execute(
                    'SELECT * FROM papers WHERE run_id=? ORDER BY year DESC,record_key', (chosen['id'],))]
                for p in c['papers']:
                    p['pub_types'] = json.loads(p['pub_types'])
                for year in range(chosen['start_year'], chosen['end_year'] + 1):
                    c['trend'].append(dict(year=year, count=sum(p['year'] == year for p in c['papers']),
                                          incomplete_year=year == current))
                if chosen['status'] == 'complete' and chosen['start_year'] <= current - 2 and chosen['end_year'] >= current - 1:
                    a = sum(p['year'] == current - 2 for p in c['papers'])
                    b = sum(p['year'] == current - 1 for p in c['papers'])
                    c['comparison'] = dict(from_year=current - 2, to_year=current - 1,
                                           before=a, after=b, delta=b-a,
                                           percent=round((b-a)/a*100, 1) if a else None)
            chemicals.append(c)
    return dict(dataset_kind=kind, validation_copy=bool(marker and marker['value']=='true'), labels=LABELS, generated_at=now(), current_year=current, chemicals=chemicals,
                criteria_version='descriptive-v1', criteria_status='provisional')


def seed_demo(path):
    init_db(path, 'demo')
    year = dt.date.today().year
    with connect(path) as db:
        if db.execute('SELECT COUNT(*) FROM runs').fetchone()[0]:
            return
        # Synthetic fixtures: no invented DOI, PMID, real paper title, or scientific result.
        for i, (cid, _, terms) in enumerate(WATCHLIST):
            rid = db.execute('''INSERT INTO runs(chemical_id,started_at,finished_at,status,query,source,
                start_year,end_year,source_total,fetched,unique_count)
                VALUES (?,?,?,'complete',?,'SYNTHETIC DEMO',?,?,?,?,?)''',
                (cid, now(), now(), query_for(terms, year-5, year), year-5, year,
                 sum((j+1)*(i+1) for j in range(6)), sum((j+1)*(i+1) for j in range(6)),
                 sum((j+1)*(i+1) for j in range(6)))).lastrowid
            for j in range(6):
                for n in range((j+1)*(i+1)):
                    p = normalize(dict(source='DEMO', id=f'{cid}-{j}-{n}',
                        title=f'[합성 데모] 차트 연결 확인용 레코드 {j+1}-{n+1}',
                        pubYear=year-5+j, abstractText='실제 논문이나 연구 결과가 아닙니다. 문헌 목록·연도 필터·추적 기능의 동작 확인용 합성 데이터입니다.'))
                    insert_papers(db, rid, [p])
