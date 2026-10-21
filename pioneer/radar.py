"""Auditable discovery views. All signals are provisional bibliographic screening, not risk scores."""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import threading
from collections import Counter
from functools import lru_cache

from . import analysis
from .core import collect, connect, now, query_for, read_page, normalize, view
from .research import lookup_identity, require_live

VERSION='radar-equal-halves-v1'
STATUS_LABELS={'growing':'문헌 관심 증가','early':'기저 0건 초기 신호','low_volume':'저빈도·해석 주의',
               'declining':'문헌 수 감소','not_flagged':'증가 조건 미충족','insufficient':'자료 부족·비교 보류'}
CORPUS_TERMS='(TITLE_ABS:"emerging contaminants" OR TITLE_ABS:"environmental pollutants" OR TITLE_ABS:"contaminants of emerging concern")'


def init_radar(path):
    with connect(path) as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS radar_reports(id TEXT PRIMARY KEY,created_at TEXT NOT NULL,data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS radar_decisions(id INTEGER PRIMARY KEY,report_id TEXT NOT NULL REFERENCES radar_reports(id),
            chemical_id TEXT NOT NULL,status TEXT NOT NULL,actor TEXT NOT NULL,reason TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS corpus_runs(id INTEGER PRIMARY KEY,query TEXT NOT NULL,start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,started_at TEXT NOT NULL,finished_at TEXT,status TEXT NOT NULL,
            source_total INTEGER,fetched INTEGER DEFAULT 0,papers TEXT,error TEXT);
        CREATE TABLE IF NOT EXISTS corpus_candidates(id TEXT PRIMARY KEY,name TEXT NOT NULL,evidence TEXT NOT NULL,
            corpus_run_id INTEGER NOT NULL REFERENCES corpus_runs(id),created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS refresh_jobs(id INTEGER PRIMARY KEY,created_at TEXT NOT NULL,finished_at TEXT,
            status TEXT NOT NULL,request TEXT NOT NULL,progress TEXT NOT NULL,error TEXT);
        CREATE TABLE IF NOT EXISTS watch_origins(chemical_id TEXT PRIMARY KEY REFERENCES chemicals(id),
            origin TEXT NOT NULL,name_entered TEXT NOT NULL,created_at TEXT NOT NULL);
        ''')


def date(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):
        raise ValueError('날짜 형식은 YYYY-MM-DD입니다.')
    return dt.date.fromisoformat(value)


def options(params):
    today=dt.datetime.now(dt.timezone.utc).date()
    end=date(params.get('end',today.isoformat()))
    start=date(params.get('start',(end-dt.timedelta(days=365)).isoformat()))
    if not dt.date(1900,1,1)<=start<end or (end-start).days>366*20:
        raise ValueError('1900년 이후, 2일 이상 20년 이내의 조회 기간을 선택하세요.')
    result=dict(start=start.isoformat(),end=end.isoformat())
    for name,default in [('min_recent',5),('min_baseline',3),('min_delta',3),('min_percent',50)]:
        raw=params.get(name,default)
        if isinstance(raw,bool) or isinstance(raw,float) and not raw.is_integer():raise ValueError('조건값은 정수여야 합니다.')
        try:v=int(raw)
        except (ValueError,TypeError):raise ValueError('조건값은 정수여야 합니다.')
        if not 0<=v<=10000:raise ValueError('조건값 범위는 0–10000입니다.')
        result[name]=v
    enums={'unit':('quarter','month','week'),'sort':('delta','percent','recent','name'),
           'status':('all',*STATUS_LABELS),'field':('all','health','occurrence','materials')}
    for name,allowed in enums.items():
        value=params.get(name,allowed[0])
        if value not in allowed:raise ValueError(f'{name} 값이 유효하지 않습니다.')
        result[name]=value
    for name in ('medium','endpoint','model'):
        value=params.get(name,'')
        if value and value not in analysis.RULES[name]:raise ValueError('맥락 필터가 유효하지 않습니다.')
        result[name]=value
    result['exclude_reviews']=str(params.get('exclude_reviews','true')).lower() not in ('0','false')
    result['search']=str(params.get('search',''))[:180].strip()
    return result


def windows(start,end):
    a,b=date(start),date(end);half=((b-a).days+1)//2
    return dict(baseline_start=a.isoformat(),baseline_end=(a+dt.timedelta(days=half-1)).isoformat(),
                recent_start=(b-dt.timedelta(days=half-1)).isoformat(),recent_end=b.isoformat(),days_per_window=half,
                middle_days_omitted=((b-a).days+1)%2)


def coverage(run):
    if not run:return None
    match=re.search(r'FIRST_PDATE:\[(\d{4}-\d{2}-\d{2}) TO (\d{4}-\d{2}-\d{2})\]',run['query'])
    start=match[1] if match else f"{run['start_year']}-01-01"
    end=match[2] if match else f"{run['end_year']}-12-31"
    if run['finished_at']:end=min(end,run['finished_at'][:10])
    return dict(start=start,end=end,complete=run['status']=='complete')


def best_snapshot(db,c,start,end):
    prefix=query_for(c['terms'],1900,1900).split(' AND FIRST_PDATE:',1)[0]
    runs=[dict(r) for r in db.execute("SELECT * FROM runs WHERE chemical_id=? AND status IN ('complete','partial')",(c['id'],))]
    runs=[r for r in runs if r['query'].split(' AND FIRST_PDATE:',1)[0]==prefix]
    if not runs:return None,[]
    def priority(r):
        cov=coverage(r);overlap=max(0,(min(date(end),date(cov['end']))-max(date(start),date(cov['start']))).days+1)
        return (cov['complete'] and cov['start']<=start and cov['end']>=end,cov['complete'],overlap,r['finished_at'] or '',r['id'])
    run=max(runs,key=priority)
    papers=[dict(p) for p in db.execute('SELECT * FROM papers WHERE run_id=? ORDER BY date,record_key',(run['id'],))]
    for p in papers:p['pub_types']=json.loads(p['pub_types'])
    return run,papers


def flags(p):
    return bool(any(re.search(r'review|preprint|retract|correct|errat',x,re.I) for x in p['pub_types']) or
                re.search(r'\b(?:retracted|retraction|correction to)\b',p['title'],re.I) or p['source_id'].startswith('PPR:'))


def mentions(p):
    # Uniform title/abstract access across candidates; availability of full text must not bias a filter.
    return _mentions_text(p['title']+'\n'+p['abstract'])


@lru_cache(maxsize=10000)
def _mentions_text(text):
    result={d:set() for d in analysis.RULES}
    for dimension,rules in analysis.RULES.items():
        for label,pattern in rules.items():
            if re.search(pattern,text,re.I):result[dimension].add(label)
    return result


def matches_context(m,o):
    if any(o[d] and o[d] not in m[d] for d in ('medium','endpoint','model')):return False
    if o['field']=='health':return bool(m['endpoint'] or m['purpose'] & {'toxicity','exposure'})
    if o['field']=='occurrence':return bool(m['medium'] and m['purpose'] & {'occurrence','exposure'})
    if o['field']=='materials':return 'synthesis' in m['purpose']
    return True


def signal(before,recent,o):
    if recent==0:return 'declining' if before else 'not_flagged'
    if before==0:return 'early'
    if before<o['min_baseline'] or recent<o['min_recent']:return 'low_volume'
    pct=(recent-before)/before*100
    if recent-before>=o['min_delta'] and pct>=o['min_percent']:return 'growing'
    return 'declining' if recent<before else 'not_flagged'


def evidence_ref(p):
    return {k:p[k] for k in ('record_key','source_id','title','doi','pmid','date','pub_types')}


def buckets(papers,run,o,unknown=0):
    start,end=date(o['start']),date(o['end']);unit=o['unit'];cursor=start
    if unit=='week':cursor-=dt.timedelta(days=cursor.weekday())
    else:cursor=cursor.replace(day=1,month=(cursor.month-1)//3*3+1 if unit=='quarter' else cursor.month)
    cov=coverage(run);out=[]
    while cursor<=end:
        if unit=='week':after=cursor+dt.timedelta(days=7)
        else:
            month=cursor.month+(3 if unit=='quarter' else 1)
            after=dt.date(cursor.year+(month-1)//12,(month-1)%12+1,1)
        a,b=max(start,cursor),min(end,after-dt.timedelta(days=1))
        overlap=cov and cov['start']<=b.isoformat() and cov['end']>=a.isoformat()
        ps=[p for p in papers if p['date'] and a.isoformat()<=p['date']<=b.isoformat()]
        out.append(dict(start=a.isoformat(),end=b.isoformat(),count=len(ps) if overlap and not unknown else None,
            records=[p['record_key'] for p in ps],partial=bool(not cov or not cov['complete'] or
            cov['start']>a.isoformat() or cov['end']<b.isoformat() or a!=cursor or b!=after-dt.timedelta(days=1))))
        cursor=after
    return out


def analyze_chemical(c,run,papers,o,reviews=None):
    reviews=reviews or {};w=windows(o['start'],o['end']);cov=coverage(run)
    valid=[];unknown=0;excluded=0;contexts={}
    for p in papers:
        if reviews.get(f"{run['id']}:{p['record_key']}")=='rejected' or (o['exclude_reviews'] and flags(p)):
            excluded+=1;continue
        m=mentions(p)
        if not matches_context(m,o):continue
        if not p['date']:
            if not p['year'] or int(o['start'][:4])<=p['year']<=int(o['end'][:4]):unknown+=1
            continue
        if o['start']<=p['date']<=o['end']:
            valid.append(p);contexts[p['record_key']]=m
    old=[p for p in valid if w['baseline_start']<=p['date']<=w['baseline_end']]
    recent=[p for p in valid if w['recent_start']<=p['date']<=w['recent_end']]
    comparable=bool(cov and cov['complete'] and cov['start']<=o['start'] and cov['end']>=o['end'] and unknown==0)
    status=signal(len(old),len(recent),o) if comparable else 'insufficient'
    pct=round((len(recent)-len(old))/len(old)*100,1) if comparable and old else None
    reasons=[dict(code='EQUAL_DAYS',text=f"전반·후반 각 {w['days_per_window']}일을 같은 검색식으로 비교합니다.")]
    if not comparable:
        reasons.append(dict(code='INCOMPLETE_SCOPE',text='미수집·부분 수집·상세 날짜 부족으로 증가 판정을 보류했습니다.'))
    elif not old and recent:
        reasons.append(dict(code='ZERO_BASELINE',text='전반 구간 0건에서 관찰된 초기 신호입니다. 증가율은 N/A이며 신물질 발견을 뜻하지 않습니다.'))
    elif status=='low_volume':
        reasons.append(dict(code='LOW_VOLUME',text='낮은 문헌 수에서의 변화이므로 증가율을 단독으로 해석하지 마세요.'))
    elif status=='growing':
        reasons.append(dict(code='VOLUME_AND_GROWTH',text='문헌 규모·절대 증가·상대 증가가 선택한 잠정 운영 조건을 함께 충족했습니다.'))
    else:reasons.append(dict(code='DESCRIPTIVE_ONLY',text='선택 조건의 기술적 집계이며 중요성·위해성을 판정하지 않습니다.'))
    old_context=Counter();new_context=Counter()
    for ps,counter in ((old,old_context),(recent,new_context)):
        for p in ps:
            for d in ('medium','endpoint','model'):
                for label in contexts[p['record_key']][d]:counter[(d,label)]+=1
    changes=[dict(dimension=d,label=label,name=analysis.LABELS[label],baseline=old_context[(d,label)],recent=n,
                  records=[p['record_key'] for p in recent if label in contexts[p['record_key']][d]])
             for (d,label),n in new_context.items() if old_context[(d,label)]==0]
    profiles=[]
    for name,overrides in [('선택값',{}),('완화',dict(min_recent=3,min_baseline=2,min_delta=2,min_percent=25)),
                           ('보수',dict(min_recent=10,min_baseline=5,min_delta=5,min_percent=100))]:
        setting={**o,**overrides};profiles.append(dict(name=name,status=signal(len(old),len(recent),setting) if comparable else 'insufficient',
            settings={k:setting[k] for k in ('min_recent','min_baseline','min_delta','min_percent')}))
    return dict(chemical_id=c['id'],name=c['name'],terms=c['terms'],run=run,coverage=cov,comparable=comparable,
        status=status,status_label=STATUS_LABELS[status],total=len(valid),baseline=len(old),recent=len(recent),
        delta=len(recent)-len(old) if comparable else None,percent=pct,unknown_dates=unknown,excluded_documents=excluded,
        windows=w,reasons=reasons,recent_evidence=[evidence_ref(p) for p in sorted(recent,key=lambda p:p['date'],reverse=True)[:6]],
        baseline_evidence=[evidence_ref(p) for p in old[:3]],records=[p['record_key'] for p in valid],
        new_contexts=sorted(changes,key=lambda x:-x['recent'])[:6] if comparable else [],sensitivity=profiles,
        unstable=len({p['status'] for p in profiles})>1,buckets=buckets(valid,run,o,unknown),
        rules_version=VERSION,identity=c.get('identity'),last_attempt=c.get('latest_attempt'))


def build(path,params):
    o=options(params);state=view(path);rows=[]
    with connect(path) as db:
        identities={r['subject']:dict(r,response=json.loads(r['response'])) for r in db.execute('SELECT * FROM identities')}
        reviews={}
        for r in db.execute("SELECT target_id,status FROM decisions WHERE target_type='paper' ORDER BY id"):reviews[r['target_id']]=r['status']
        for c in state['chemicals']:
            c['identity']=identities.get('chemical:'+c['id'])
            hay=' '.join([c['name'],*c['terms'],str(c['identity']['response'].get('properties',[])) if c['identity'] else '']).lower()
            if o['search'].lower() not in hay:continue
            run,papers=best_snapshot(db,c,o['start'],o['end'])
            rows.append(analyze_chemical(c,run,papers,o,reviews))
        corpus=db.execute('SELECT id,query,start_date,end_date,started_at,finished_at,status,source_total,fetched,error FROM corpus_runs ORDER BY id DESC LIMIT 1').fetchone()
        report_count=db.execute('SELECT COUNT(*) FROM radar_reports').fetchone()[0]
    if o['status']!='all':rows=[r for r in rows if r['status']==o['status']]
    if o['sort']=='name':rows.sort(key=lambda r:r['name'].lower())
    else:
        field=o['sort'];rows.sort(key=lambda r:(not r['comparable'],r[field] is None,-(r[field] or 0),r['name'].lower()))
    with connect(path) as db:
        marker=db.execute("SELECT value FROM metadata WHERE key='validation_copy'").fetchone()
    result=dict(options=o,windows=windows(o['start'],o['end']),rows=rows,total_monitored=len(state['chemicals']),
        eligible=sum(r['comparable'] for r in rows),growing=sum(r['status']=='growing' for r in rows),
        generated_at=now(),dataset_kind=state['dataset_kind'],validation_copy=bool(marker and marker['value']=='true'),
        rules_version=VERSION,context_rules_version=analysis.VERSION,report_count=report_count,latest_corpus=dict(corpus) if corpus else None,
        scope='현재 조회 목록과 검색 사전 안의 비교입니다. 전체 화학물질 순위가 아닙니다.',
        methodology='선택 기간의 앞·뒤를 동일 일수로 나눕니다. 홀수 기간의 가운데 하루는 증가 비교에서만 제외합니다. 문헌 수는 독립 연구 수가 아닙니다.',
        limitations=['기준값은 잠정 운영 선택이며 검증된 hot·위해성 기준이 아님','분야 전체 출판량 분모·색인 지연을 보정하지 않음',
            '맥락은 제목·초록의 키워드 언급이며 실제 측정·원문 검토와 구별','조건에 따른 후보 변동과 선택 편향이 존재'])
    fingerprint={k:v for k,v in result.items() if k not in ('generated_at','report_count')}
    result['report_id']=analysis.key(VERSION,json.dumps(fingerprint,sort_keys=True,ensure_ascii=False))
    return result


def save_report(path,params):
    result=build(path,params)
    if params.get('expected_report_id') and params['expected_report_id']!=result['report_id']:
        raise ValueError('표시 후 자료가 변경됐습니다. 결과를 새로 조회한 뒤 저장하세요.')
    with connect(path) as db:
        db.execute('INSERT OR IGNORE INTO radar_reports VALUES (?,?,?)',(result['report_id'],now(),json.dumps(result,ensure_ascii=False)))
        return json.loads(db.execute('SELECT data FROM radar_reports WHERE id=?',(result['report_id'],)).fetchone()['data'])


def saved_report(path,report_id):
    with connect(path) as db:
        row=db.execute('SELECT data FROM radar_reports WHERE id=?',(report_id,)).fetchone()
        if not row:raise ValueError('저장된 분석 결과가 없습니다.')
        result=json.loads(row['data'])
        result['decisions']=[dict(r) for r in db.execute('SELECT * FROM radar_decisions WHERE report_id=? ORDER BY id',(report_id,))]
    return result


def articles(path,params):
    try:offset=int(params.get('offset',0))
    except (ValueError,TypeError):raise ValueError('유효하지 않은 페이지입니다.')
    if offset<0:raise ValueError('유효하지 않은 페이지입니다.')
    report=saved_report(path,params['report_id']) if params.get('report_id') else build(path,params)
    if params.get('expected_report_id') and params['expected_report_id']!=report['report_id']:
        raise ValueError('표시 후 자료가 변경됐습니다. 결과를 새로 조회하세요.')
    row=next((r for r in report['rows'] if r['chemical_id']==params.get('chemical_id')),None)
    if not row:raise ValueError('조회 결과에 없는 물질입니다.')
    if not row['run']:return dict(total=0,offset=offset,articles=[])
    keys=set(row['records']);papers=[]
    with connect(path) as db:
        for p in db.execute('SELECT * FROM papers WHERE run_id=? ORDER BY date DESC,record_key',(row['run']['id'],)):
            if p['record_key'] in keys:
                p=dict(p);p['pub_types']=json.loads(p['pub_types']);papers.append(p)
    return dict(total=len(papers),offset=offset,articles=papers[offset:offset+20])


def report_decision(path,data):
    if data.get('status') not in ('selected','deferred','rejected'):raise ValueError('선정·보류·제외 중 선택하세요.')
    if not all(isinstance(data.get(k),str) and 2<=len(data[k].strip())<=2000 for k in ('actor','reason')):
        raise ValueError('기록자와 구체적인 이유를 입력하세요.')
    with connect(path) as db:
        row=db.execute('SELECT data FROM radar_reports WHERE id=?',(data.get('report_id'),)).fetchone()
        if not row or data.get('chemical_id') not in {r['chemical_id'] for r in json.loads(row['data'])['rows']}:
            raise ValueError('저장된 분석 결과에서 후보를 선택하세요.')
        return db.execute('INSERT INTO radar_decisions(report_id,chemical_id,status,actor,reason,created_at) VALUES (?,?,?,?,?,?)',
            (data['report_id'],data['chemical_id'],data['status'],data['actor'],data['reason'],now())).lastrowid


def report_csv(report):
    out=io.StringIO(newline='');writer=csv.writer(out)
    writer.writerow(['chemical','status','baseline_start','baseline_end','recent_start','recent_end','baseline','recent','delta','percent',
                     'query','run_id','rules_version','reasons','recent_dois','selection_history','report_id','criteria',
                     'dataset_kind','validation_copy','scope','context_rules_version'])
    for r in report['rows']:
        values=[r['name'],r['status_label'],*[r['windows'][x] for x in ('baseline_start','baseline_end','recent_start','recent_end')],
            r['baseline'],r['recent'],r['delta'],r['percent'],r['run']['query'] if r['run'] else '',r['run']['id'] if r['run'] else '',
            report['rules_version'],' | '.join(x['text'] for x in r['reasons']),' | '.join(p['doi'] for p in r['recent_evidence'] if p['doi']),
            json.dumps([d for d in report.get('decisions',[]) if d['chemical_id']==r['chemical_id']],ensure_ascii=False),report['report_id'],json.dumps(report['options'],ensure_ascii=False),
            report['dataset_kind'],report.get('validation_copy',False),report['scope'],report.get('context_rules_version','')]
        writer.writerow(["'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')) else v for v in values])
    return '\ufeff'+out.getvalue()


def track(path,name,origin='user_lookup'):
    require_live(path)
    if not isinstance(name,str) or not 2<=len(name.strip())<=180 or any(ch in name for ch in ('\n','\r','"','\\')):
        raise ValueError('2–180자의 단일 물질명으로 조회하세요. 검색식은 입력하지 않습니다.')
    name=name.strip();subject='lookup:'+analysis.key(name.casefold())
    identity=lookup_identity(path,subject,name)
    if identity['status']!='unique_match':
        return dict(status=identity['status'],identity=identity,chemical_id=None)
    cid=identity['response']['properties'][0]['CID']
    with connect(path) as db:
        for row in db.execute("SELECT * FROM identities WHERE subject LIKE 'chemical:%' AND status='unique_match'"):
            props=json.loads(row['response']).get('properties',[])
            if any(p.get('CID')==cid for p in props):
                return dict(status='existing',chemical_id=row['subject'].split(':',1)[1],identity=identity)
        chemical_id='cid-'+str(cid)
        db.execute('INSERT OR IGNORE INTO chemicals VALUES (?,?,?)',(chemical_id,name,json.dumps([name])))
        db.execute('INSERT OR REPLACE INTO identities VALUES (?,?,?,?,?,?)',('chemical:'+chemical_id,name,identity['status'],
            json.dumps(identity['response'],ensure_ascii=False),identity['checked_at'],identity['source']))
        db.execute('INSERT OR IGNORE INTO watch_origins VALUES (?,?,?,?)',(chemical_id,origin,name,now()))
    return dict(status='added',chemical_id=chemical_id,identity=identity,
        warning='명칭 기반 조회 대상으로 추가했습니다. 문헌의 직접 대상·염·이성질체 식별 확인은 미완료입니다.')


def scan_corpus(path,data,fetch_page=read_page):
    require_live(path)
    a,b=date(data.get('start')),date(data.get('end'))
    if a>b or (b-a).days>366*5 or a<dt.date(1900,1,1):raise ValueError('문헌 후보 탐색 범위는 최대 5년입니다.')
    max_pages=data.get('max_pages',2)
    if type(max_pages) is not int or not 1<=max_pages<=3:raise ValueError('후보 탐색은 1–3페이지로 제한합니다.')
    query=CORPUS_TERMS+f' AND FIRST_PDATE:[{a.isoformat()} TO {b.isoformat()}]'
    with connect(path) as db:
        rid=db.execute("INSERT INTO corpus_runs(query,start_date,end_date,started_at,status) VALUES (?,?,?,?,'running')",(query,a.isoformat(),b.isoformat(),now())).lastrowid
    papers=[];seen=set();seen_cursors=set();cursor='*';total=None;received=0;status='partial'
    try:
        for _ in range(max_pages):
            page=fetch_page(dict(query=query,format='json',resultType='core',pageSize=100,cursorMark=cursor))
            if type(page.get('hitCount')) is not int or page['hitCount']<0 or not isinstance(page.get('resultList',{}).get('result'),list):
                raise ValueError('자료원 응답 형식이 유효하지 않습니다.')
            if total is not None and total!=page['hitCount']:raise ValueError('검색 중 총건수가 변했습니다.')
            total=page['hitCount'];raw=page['resultList']['result'];received+=len(raw)
            for item in raw:
                p=normalize(item);key=p['doi'] or p['source_id']
                if key not in seen:
                    seen.add(key);p['pub_types']=json.loads(p['pub_types']);papers.append(p)
            if received>=total:status='complete';break
            nxt=page.get('nextCursorMark')
            if not raw or not nxt or nxt==cursor or nxt in seen_cursors:raise ValueError('후보 탐색 페이지 진행이 중단됐습니다.')
            seen_cursors.add(cursor);cursor=nxt
        discovered=analysis.discover([dict(id='corpus:'+str(rid),name='',terms=[],run={'id':rid},papers=papers)])
        with connect(path) as db:
            db.execute('UPDATE corpus_runs SET finished_at=?,status=?,source_total=?,fetched=?,papers=? WHERE id=?',
                (now(),status,total,received,json.dumps(papers,ensure_ascii=False),rid))
            for c in discovered:
                db.execute('INSERT OR REPLACE INTO corpus_candidates VALUES (?,?,?,?,?)',
                    (c['id'],c['name'],json.dumps(c,ensure_ascii=False),rid,now()))
        return dict(id=rid,status=status,source_total=total,fetched=received,candidates=len(discovered),
            warning='환경 문헌 검색의 제한된 페이지에서 제안한 명칭입니다. 전체 후보군 탐지·물질 식별·증가 분석 완료가 아닙니다.')
    except Exception as exc:
        with connect(path) as db:db.execute("UPDATE corpus_runs SET finished_at=?,status='failed',error=? WHERE id=?",(now(),str(exc)[:500],rid))
        raise


def corpus_view(path):
    with connect(path) as db:
        run=db.execute('SELECT * FROM corpus_runs ORDER BY id DESC LIMIT 1').fetchone()
        candidates=[dict(c,evidence=json.loads(c['evidence'])) for c in db.execute('SELECT * FROM corpus_candidates ORDER BY created_at DESC,name')]
    if run:
        run=dict(run);run['papers']=json.loads(run['papers'] or '[]')
    return dict(run=run,candidates=candidates,scope='고정 환경 문헌 검색식의 최대 300개 원본 레코드입니다. 검색 기본 정렬의 편향·검색어 누락이 있으며 전체 문헌이나 모든 화학물질을 대표하지 않습니다.')


def refresh_worker(path,jid,request):
    progress=[]
    try:
        with connect(path) as db:db.execute("UPDATE refresh_jobs SET status='running' WHERE id=?",(jid,))
        base=view(path)
        for cid in request['chemical_ids']:
            try:
                c=next(c for c in base['chemicals'] if c['id']==cid)
                target_end=min(f"{request['end_year']}-12-31",dt.datetime.now(dt.timezone.utc).date().isoformat())
                with connect(path) as db:
                    run,_=best_snapshot(db,c,f"{request['start_year']}-01-01",target_end)
                cov=coverage(run)
                if not request.get('force') and cov and cov['complete'] and cov['start']<=f"{request['start_year']}-01-01" and cov['end']>=target_end:
                    entry=dict(chemical_id=cid,status='reused',run_id=run['id'])
                else:
                    rid=collect(path,cid,request['start_year'],request['end_year'],request['max_pages'])
                    with connect(path) as db:r=dict(db.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone())
                    entry=dict(chemical_id=cid,status=r['status'],run_id=rid,fetched=r['fetched'],source_total=r['source_total'])
            except Exception as exc:entry=dict(chemical_id=cid,status='failed',error=str(exc)[:300])
            progress.append(entry)
            with connect(path) as db:db.execute('UPDATE refresh_jobs SET progress=? WHERE id=?',(json.dumps(progress,ensure_ascii=False),jid))
        status='complete' if all(p['status'] in ('complete','reused') for p in progress) else 'completed_with_issues'
        with connect(path) as db:db.execute('UPDATE refresh_jobs SET status=?,finished_at=? WHERE id=?',(status,now(),jid))
    except Exception as exc:
        with connect(path) as db:db.execute("UPDATE refresh_jobs SET status='failed',finished_at=?,error=? WHERE id=?",(now(),str(exc)[:300],jid))


def start_refresh(path,data,launch=True):
    import os
    require_live(path)
    ids=data.get('chemical_ids')
    if not isinstance(ids,list) or not 1<=len(ids)<=12 or any(not isinstance(c,str) for c in ids) or len(set(ids))!=len(ids):
        raise ValueError('서로 다른 조회 대상 1–12개를 선택하세요.')
    a,b,limit=data.get('start_year'),data.get('end_year'),data.get('max_pages',20)
    if any(type(v) is not int for v in (a,b,limit)) or not 1<=limit<=20:raise ValueError('연도·페이지 한도를 확인하세요. 최대 20페이지입니다.')
    query_for(['validation'],a,b)
    request=dict(chemical_ids=ids,start_year=a,end_year=b,max_pages=limit,force=bool(data.get('force')),owner_pid=os.getpid())
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        known={c['id'] for c in db.execute('SELECT id FROM chemicals')}
        if not set(ids)<=known:raise ValueError('조회 목록에 없는 물질이 있습니다.')
        for row in db.execute("SELECT * FROM refresh_jobs WHERE status IN ('queued','running')"):
            previous=json.loads(row['request']);alive=False
            try:
                pid=previous.get('owner_pid')
                if isinstance(pid,int) and pid>0:os.kill(pid,0);alive=True
            except ProcessLookupError:pass
            except PermissionError:alive=True
            if alive:raise ValueError('이미 갱신이 진행 중입니다.')
            db.execute("UPDATE refresh_jobs SET status='interrupted',finished_at=?,error='이전 작업 프로세스가 종료됐습니다.' WHERE id=?",(now(),row['id']))
        jid=db.execute("INSERT INTO refresh_jobs(created_at,status,request,progress) VALUES (?,'queued',?,'[]')",(now(),json.dumps(request))).lastrowid
    if launch:threading.Thread(target=refresh_worker,args=(path,jid,request),daemon=True,name=f'pioneer-refresh-{jid}').start()
    return dict(id=jid,status='queued',max_primary_pages=len(ids)*limit)


def jobs(path):
    with connect(path) as db:
        return [dict(r,request=json.loads(r['request']),progress=json.loads(r['progress'])) for r in db.execute('SELECT * FROM refresh_jobs ORDER BY id DESC LIMIT 10')]


def saved_reports(path):
    with connect(path) as db:
        reports=[dict(id=r['id'],created_at=r['created_at'],options=json.loads(r['data'])['options']) for r in db.execute('SELECT * FROM radar_reports ORDER BY created_at DESC LIMIT 30')]
        decisions=[dict(r) for r in db.execute('SELECT * FROM radar_decisions ORDER BY id DESC LIMIT 100')]
    return dict(reports=reports,decisions=decisions)
