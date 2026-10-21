"""Bridge between the two modes.

Screening (no predefined list) -> the operator picks one candidate -> the candidate is
identified through PubChem, added to the tracked list, and collected with the same
watchlist pipeline (core.collect). The detail view then reports how mentions changed
and what the papers report (keyword mentions only, never measured effects).
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from collections import Counter, defaultdict

from . import analysis, radar
from .core import connect, now
from .research import require_live

ORIGIN_PREFIX = 'screening:'
MAX_NAME_TRIES = 3


def init_tracking(path):
    with connect(path) as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS screening_tracks(
            candidate_id TEXT PRIMARY KEY, chemical_id TEXT NOT NULL REFERENCES chemicals(id),
            run_id INTEGER NOT NULL REFERENCES screening_runs(id), name_used TEXT NOT NULL,
            created_at TEXT NOT NULL);
        ''')


def links(db):
    """candidate_id -> chemical_id. Tolerates an older DB opened read-only before migration."""
    try:
        return dict(db.execute('SELECT candidate_id,chemical_id FROM screening_tracks'))
    except sqlite3.OperationalError:
        return {}


def _candidate(db, candidate_id, start=None, end=None):
    """Prefer the screening run the user was looking at; otherwise the newest one."""
    rows = db.execute('''SELECT c.*, r.start_date, r.end_date FROM screening_candidates c
        JOIN screening_runs r ON r.id=c.run_id WHERE c.candidate_id=? ORDER BY c.run_id DESC''',
        (candidate_id,)).fetchall()
    exact = [r for r in rows if r['start_date'] == start and r['end_date'] == end]
    return (exact or rows or [None])[0]


def _valid_name(name):
    return isinstance(name, str) and 2 <= len(name.strip()) <= 180 and not any(ch in name for ch in '\n\r"\\')


def names_to_try(row, override=None):
    """Canonical name first, then frequent surface forms. Short all-caps abbreviations are
    skipped: a PubChem hit on an acronym ("BPS") is not evidence of chemical identity."""
    if override is not None:
        if not _valid_name(override):
            raise ValueError('2–180자의 단일 물질명을 입력하세요.')
        return [override.strip()]
    data = json.loads(row['data'])
    forms = Counter(m.get('exact', '').strip() for m in data.get('mentions', []) if m.get('exact'))
    out, seen = [], set()
    for name in [row['name'], *(n for n, _ in forms.most_common())]:
        name = name.strip()
        if name.casefold() in seen or not _valid_name(name):
            continue
        if name != row['name'] and (len(name) < 5 or re.fullmatch(r'[A-Z0-9-]{1,6}', name)):
            continue
        seen.add(name.casefold())
        out.append(name)
        if len(out) == MAX_NAME_TRIES:
            break
    return out


def _years(data, row):
    this_year = dt.date.today().year
    screen_end = min(int(row['end_date'][:4]), this_year)
    start = data.get('start_year', screen_end - 4)
    end = data.get('end_year', screen_end)
    pages = data.get('max_pages', 10)
    if any(type(v) is not int for v in (start, end, pages)):
        raise ValueError('연도와 페이지 한도는 정수여야 합니다.')
    return start, end, pages


def from_screening(path, data, launch=True):
    """Register a screening candidate as a tracked chemical and start its collection."""
    require_live(path)
    candidate_id = data.get('candidate_id')
    if not isinstance(candidate_id, str) or not 1 <= len(candidate_id) <= 64:
        raise ValueError('스크리닝 후보를 선택하세요.')
    with connect(path) as db:
        row = _candidate(db, candidate_id, data.get('screening_start'), data.get('screening_end'))
        if not row:
            raise ValueError('스크리닝 결과에 없는 후보입니다. 스크리닝을 먼저 실행하세요.')
        linked = links(db).get(candidate_id)
        if linked and not db.execute('SELECT 1 FROM chemicals WHERE id=?', (linked,)).fetchone():
            linked = None
    start, end, pages = _years(data, row)

    tried, status, chemical_id = [], None, linked
    if linked:
        status = 'existing'
    else:
        for name in names_to_try(row, data.get('name')):
            try:
                result = radar.track(path, name, origin=ORIGIN_PREFIX + str(row['run_id']))
            except ValueError as exc:
                tried.append(dict(name=name, status='invalid_name', message=str(exc)))
                continue
            entry = dict(name=name, status=result['status'])
            if result['status'] == 'ambiguous':
                entry['cids'] = [p.get('CID') for p in result['identity']['response'].get('properties', [])][:10]
            tried.append(entry)
            if result['status'] == 'failed':
                break  # PubChem unreachable: other names would fail the same way.
            if result['chemical_id']:
                chemical_id, status = result['chemical_id'], result['status']
                with connect(path) as db:
                    db.execute('INSERT OR REPLACE INTO screening_tracks VALUES (?,?,?,?,?)',
                               (candidate_id, chemical_id, row['run_id'], name, now()))
                break
        if not chemical_id:
            if tried and tried[-1]['status'] == 'failed':
                return dict(status='lookup_failed', chemical_id=None, candidate_name=row['name'], tried=tried,
                            message='PubChem에 연결하지 못했습니다. 추적 목록은 바뀌지 않았습니다. 잠시 뒤 다시 시도하세요.')
            return dict(status='unresolved', chemical_id=None, candidate_name=row['name'], tried=tried,
                        message='PubChem에서 하나의 화합물로 확정되지 않아 추적 목록에 넣지 않았습니다. '
                                '정확한 화학명(IUPAC명·정식명)을 입력해 다시 시도하세요.')

    job, job_error = None, None
    try:
        job = radar.start_refresh(path, dict(chemical_ids=[chemical_id], start_year=start, end_year=end,
                                             max_pages=pages), launch=launch)
    except ValueError as exc:
        job_error = str(exc)
    return dict(status=status, chemical_id=chemical_id, candidate_name=row['name'], tried=tried,
                job=job, job_error=job_error,
                screening=dict(run_id=row['run_id'], start=row['start_date'], end=row['end_date']))


def _window(params, origin_scope):
    today = dt.datetime.now(dt.timezone.utc).date()
    end = params.get('end') or (origin_scope or {}).get('end') or today.isoformat()
    start = params.get('start') or (origin_scope or {}).get('start') or \
        (radar.date(end) - dt.timedelta(days=365)).isoformat()
    return start, end


def _content(papers, keys, w):
    """What the papers report: keyword mentions per context label, baseline vs recent."""
    stats = defaultdict(lambda: dict(baseline=set(), recent=set(), all=set(), examples=[]))
    for p in papers:
        if p['record_key'] not in keys:
            continue
        half = 'baseline' if p['date'] <= w['baseline_end'] else ('recent' if p['date'] >= w['recent_start'] else None)
        for e in analysis.extract(p, 0):
            s = stats[(e['dimension'], e['label'])]
            s['all'].add(p['record_key'])
            if half:
                s[half].add(p['record_key'])
            if len(s['examples']) < 3 and not e['flags'] and not any(x['record_key'] == p['record_key'] for x in s['examples']):
                s['examples'].append(dict(record_key=p['record_key'], date=p['date'], title=p['title'],
                                          doi=p['doi'], snippet=e['snippet'][:400], matched=e['matched']))
    groups = []
    names = dict(medium='매질', endpoint='영향 지표', model='시험계·대상', life_stage='생애 단계', purpose='연구 성격')
    for dimension in analysis.RULES:
        items = [dict(label=label, name=analysis.LABELS[label], total=len(s['all']), baseline=len(s['baseline']),
                      recent=len(s['recent']), new=not s['baseline'] and bool(s['recent']), examples=s['examples'])
                 for (d, label), s in stats.items() if d == dimension]
        items.sort(key=lambda x: (-x['total'], x['name']))
        groups.append(dict(dimension=dimension, name=names[dimension], items=items))
    return groups


def detail(path, params):
    chemical_id = params.get('chemical')
    if not isinstance(chemical_id, str) or not chemical_id:
        raise ValueError('추적 물질을 선택하세요.')
    with connect(path) as db:
        chem = db.execute('SELECT * FROM chemicals WHERE id=?', (chemical_id,)).fetchone()
        if not chem:
            raise ValueError('추적 목록에 없는 물질입니다.')
        identity = db.execute("SELECT * FROM identities WHERE subject=?", ('chemical:' + chemical_id,)).fetchone()
        link = None
        try:
            link = db.execute('''SELECT t.*, r.start_date, r.end_date FROM screening_tracks t
                JOIN screening_runs r ON r.id=t.run_id WHERE t.chemical_id=? ORDER BY t.created_at LIMIT 1''',
                (chemical_id,)).fetchone()
        except sqlite3.OperationalError:
            pass
        origin_scope = dict(start=link['start_date'], end=link['end_date']) if link else None
        start, end = _window(params, origin_scope)
        o = radar.options(dict(start=start, end=end, unit=params.get('unit', 'month'),
                               exclude_reviews=params.get('exclude_reviews', 'true')))
        c = dict(id=chem['id'], name=chem['name'], terms=json.loads(chem['terms']))
        run, papers = radar.best_snapshot(db, c, o['start'], o['end'])
        reviews = {r['target_id']: r['status'] for r in
                   db.execute("SELECT target_id,status FROM decisions WHERE target_type='paper' ORDER BY id")}
        last = db.execute('SELECT * FROM runs WHERE chemical_id=? ORDER BY id DESC LIMIT 1', (chemical_id,)).fetchone()
        job = None
        for r in db.execute('SELECT * FROM refresh_jobs ORDER BY id DESC LIMIT 20'):
            request = json.loads(r['request'])
            if chemical_id in request.get('chemical_ids', []):
                progress = next((p for p in json.loads(r['progress']) if p.get('chemical_id') == chemical_id), None)
                job = dict(id=r['id'], status=r['status'], created_at=r['created_at'], finished_at=r['finished_at'],
                           progress=progress, years=[request.get('start_year'), request.get('end_year')])
                break
    c['latest_attempt'] = dict(last) if last else None
    c['identity'] = dict(identity, response=json.loads(identity['response'])) if identity else None
    row = radar.analyze_chemical(c, run, papers, o, reviews)
    keys = set(row['records'])
    recent_papers = sorted((p for p in papers if p['record_key'] in keys), key=lambda p: p['date'], reverse=True)[:40]
    props = (c['identity'] or {}).get('response', {}).get('properties') or [{}]
    return dict(
        chemical=dict(id=c['id'], name=c['name'], terms=c['terms'], cid=props[0].get('CID'),
                      formula=props[0].get('MolecularFormula'), inchikey=props[0].get('InChIKey')),
        origin=dict(kind='screening', run_id=link['run_id'], name_used=link['name_used'],
                    start=link['start_date'], end=link['end_date']) if link else dict(kind='watchlist'),
        options=o, windows=row['windows'], job=job,
        snapshot=dict(run_id=run['id'], status=run['status'], source_total=run['source_total'],
                      fetched=run['fetched'], unique_count=run['unique_count'], coverage=row['coverage'],
                      finished_at=run['finished_at']) if run else None,
        trend=dict(status=row['status'], status_label=row['status_label'], comparable=row['comparable'],
                   baseline=row['baseline'], recent=row['recent'], delta=row['delta'], percent=row['percent'],
                   total=row['total'], unknown_dates=row['unknown_dates'], excluded=row['excluded_documents'],
                   reasons=row['reasons'], sensitivity=row['sensitivity'], buckets=row['buckets']),
        content=_content(papers, keys, row['windows']),
        new_contexts=row['new_contexts'],
        papers=[{k: p[k] for k in ('record_key', 'title', 'authors', 'journal', 'date', 'doi', 'pmid', 'pub_types')}
                for p in recent_papers],
        limitations=['언급 수는 같은 검색식에서 찾은 문헌 수이며 독립 연구 수가 아님',
                     '보고 내용은 제목·초록의 키워드 언급이며 측정 결과·영향 크기가 아님',
                     '스크리닝 후보명으로 등록한 검색어 하나만 사용함(약어·동의어 미포함)',
                     '위해성·사용량·환경농도 증가를 뜻하지 않음'],
        generated_at=now())


def tracked(path):
    """Tracked chemicals with their origin and latest collection status."""
    with connect(path) as db:
        link_by_chem = {}
        try:
            for r in db.execute('SELECT chemical_id,run_id FROM screening_tracks'):
                link_by_chem[r['chemical_id']] = r['run_id']
        except sqlite3.OperationalError:
            pass
        out = []
        for c in db.execute('SELECT * FROM chemicals ORDER BY rowid DESC'):
            last = db.execute('SELECT status,finished_at,unique_count FROM runs WHERE chemical_id=? ORDER BY id DESC LIMIT 1',
                              (c['id'],)).fetchone()
            out.append(dict(id=c['id'], name=c['name'], from_screening_run=link_by_chem.get(c['id']),
                            last_status=last['status'] if last else None,
                            papers=last['unique_count'] if last else 0))
    return dict(chemicals=out)
