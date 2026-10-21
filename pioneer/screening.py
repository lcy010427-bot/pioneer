"""Literature-first chemical screening without a predefined substance list."""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
import urllib.parse
from collections import defaultdict

from . import analysis
from .core import connect, normalize, now, read_page
from .research import get_json, require_live


VERSION = 'literature-screen-v1'
ANNOTATIONS = 'https://www.ebi.ac.uk/europepmc/annotations_api/annotationsByArticleIds'
QUERY = ('(TITLE_ABS:"emerging contaminants" OR TITLE_ABS:"environmental pollutants" '
         'OR TITLE_ABS:"contaminants of emerging concern")')
GENERIC = {
    'water','oxygen','nitrogen','carbon dioxide','sodium','chloride','sulfate','amino acid',
    'protein','lipid','glucose','atp','dna','rna','hydrogen','calcium','potassium','iron',
    'methanol','acetonitrile','formic acid','ethanol','acetone','isopropanol',
    'metal','carbon','phosphorus','sulfur','mineral','oxide','nitride','hydrocarbon',
    'alkali metal cation','fluoroalkane','chemical element','organic compound','inorganic compound',
    'peptide','methyl','fatty acid','polyunsaturated fatty acid',
}


def init_screening(path):
    with connect(path) as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS screening_runs(
            id INTEGER PRIMARY KEY, query TEXT NOT NULL, start_date TEXT NOT NULL,
            end_date TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
            status TEXT NOT NULL, baseline_total INTEGER, baseline_fetched INTEGER DEFAULT 0,
            recent_total INTEGER, recent_fetched INTEGER DEFAULT 0, documents TEXT,
            annotated INTEGER DEFAULT 0, error TEXT, rules_version TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS screening_candidates(
            run_id INTEGER NOT NULL REFERENCES screening_runs(id), candidate_id TEXT NOT NULL,
            name TEXT NOT NULL, identifier TEXT, data TEXT NOT NULL,
            PRIMARY KEY(run_id,candidate_id));
        ''')


def _date(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):
        raise ValueError('날짜 형식은 YYYY-MM-DD입니다.')
    return dt.date.fromisoformat(value)


def _window(start,end):
    a,b=_date(start),_date(end)
    if not dt.date(1900,1,1)<=a<b or (b-a).days>366*5:
        raise ValueError('스크리닝 기간은 2일 이상 5년 이내여야 합니다.')
    half=((b-a).days+1)//2
    return a,b,a+dt.timedelta(days=half-1),b-dt.timedelta(days=half-1)


def _collect(start,end,max_pages,fetch_page):
    query=QUERY+f' AND FIRST_PDATE:[{start.isoformat()} TO {end.isoformat()}]'
    cursor='*';seen_cursors=set();papers=[];keys=set();total=None;received=0
    for _ in range(max_pages):
        page=fetch_page(dict(query=query,format='json',resultType='core',pageSize=100,cursorMark=cursor))
        raw=page.get('resultList',{}).get('result')
        if type(page.get('hitCount')) is not int or page['hitCount']<0 or not isinstance(raw,list):
            raise ValueError('자료원 응답 형식이 유효하지 않습니다.')
        if total is not None and total!=page['hitCount']:
            raise ValueError('검색 중 총 문헌 수가 변했습니다.')
        total=page['hitCount'];received+=len(raw)
        for item in raw:
            p=normalize(item);key=p['doi'] or p['source_id']
            if key not in keys:
                keys.add(key);p['pub_types']=json.loads(p['pub_types']);papers.append(p)
        if received>=total:break
        nxt=page.get('nextCursorMark')
        if not raw or not nxt or nxt==cursor or nxt in seen_cursors:
            raise ValueError('문헌 페이지 진행이 중단됐습니다.')
        seen_cursors.add(cursor);cursor=nxt
    return total,received,papers


def _annotation_batches(papers,request):
    by_source={p['source_id']:p for p in papers if p['source_id'].split(':',1)[0] in ('MED','PMC')};output=[];annotated=set()
    ids=list(by_source)
    def fetch_group(group):
        url=ANNOTATIONS+'?'+urllib.parse.urlencode({'articleIds':','.join(group),'type':'Chemicals','format':'JSON'})
        try:return request(url)
        except Exception:
            if len(group)==1:return []
            middle=len(group)//2
            return [*fetch_group(group[:middle]),*fetch_group(group[middle:])]
    for offset in range(0,len(ids),10):
        group=ids[offset:offset+10]
        payload=fetch_group(group)
        if not isinstance(payload,list):raise ValueError('화학물질 주석 응답 형식이 유효하지 않습니다.')
        for article in payload:
            key=f"{article.get('source')}:{article.get('extId')}"
            paper=by_source.get(key)
            if not paper:continue
            annotated.add(key)
            for item in article.get('annotations') or []:
                section=str(item.get('section') or '')
                if not (section.startswith('Title ') or section.startswith('Abstract ')):continue
                tags=item.get('tags') or []
                tag=next((t for t in tags if isinstance(t,dict) and t.get('name') and t.get('uri')),None)
                if not tag:continue
                name=str(tag['name']).strip();exact=str(item.get('exact') or '').strip()
                if len(name)<4 or name.lower() in GENERIC or not re.search('[A-Za-z]',name):continue
                output.append(dict(identifier=str(tag['uri']),name=name,exact=exact,record_key=key,
                    date=paper['date'],title=paper['title'],doi=paper['doi'],source_id=paper['source_id'],
                    section='title' if section.startswith('Title ') else 'abstract',
                    snippet=(str(item.get('prefix') or '')[-100:]+exact+str(item.get('postfix') or '')[:140]).strip()))
    return output,len(annotated)


def _regex_mentions(papers):
    output=[]
    for paper in papers:
        for field in ('title','abstract'):
            text=paper[field]
            for match in analysis.CANDIDATE_PATTERN.finditer(text):
                name=match.group().strip()
                if len(name)<4 or name.upper() in analysis.EXCLUDE:continue
                output.append(dict(identifier='',name=name,exact=name,record_key=paper['record_key'],
                    date=paper['date'],title=paper['title'],doi=paper['doi'],source_id=paper['source_id'],section=field,
                    snippet=text[max(0,match.start()-100):match.end()+140]))
    return output


def run(path,start,end,max_pages=10,fetch_page=read_page,annotation_request=get_json,run_id=None):
    require_live(path);a,b,old_end,new_start=_window(start,end)
    if type(max_pages) is not int or not 1<=max_pages<=25:
        raise ValueError('스크리닝 문헌 한도는 구간별 1–25페이지입니다.')
    if run_id is None:
        with connect(path) as db:
            run_id=db.execute('''INSERT INTO screening_runs(query,start_date,end_date,started_at,status,rules_version)
                VALUES (?,?,?,?,'running',?)''',(QUERY,a.isoformat(),b.isoformat(),now(),VERSION)).lastrowid
    else:
        with connect(path) as db:db.execute("UPDATE screening_runs SET status='running' WHERE id=?",(run_id,))
    try:
        bt,bf,bp=_collect(a,old_end,max_pages,fetch_page)
        rt,rf,rp=_collect(new_start,b,max_pages,fetch_page)
        papers=[];seen=set()
        for p in [*bp,*rp]:
            key=p['doi'] or p['source_id']
            if key not in seen:seen.add(key);papers.append(p)
        annotated_mentions,annotated_count=_annotation_batches(papers,annotation_request)
        def name_key(value):return re.sub(r'[^a-z0-9]','',value.lower())
        named={name_key(m['name']):m['identifier'] for m in annotated_mentions}
        regex_mentions=_regex_mentions(papers)
        for mention in regex_mentions:
            if name_key(mention['name']) in named:mention['identifier']=named[name_key(mention['name'])]
        mentions=annotated_mentions+regex_mentions
        grouped=defaultdict(lambda:dict(names=defaultdict(int),mentions={}))
        for m in mentions:
            cid=m['identifier'] or 'text:'+re.sub(r'[^a-z0-9]+','-',m['name'].lower()).strip('-')
            g=grouped[cid];g['names'][m['name']]+=1;g['mentions'][(m['record_key'],m['section'],m['exact'])]=m
        with connect(path) as db:
            db.execute('DELETE FROM screening_candidates WHERE run_id=?',(run_id,))
            for cid,g in grouped.items():
                records=list(g['mentions'].values());name=max(g['names'],key=lambda n:(g['names'][n],-len(n),n))
                data=dict(identifier=cid if not cid.startswith('text:') else None,mentions=records)
                db.execute('INSERT INTO screening_candidates VALUES (?,?,?,?,?)',
                    (run_id,analysis.key(VERSION,cid),name,data['identifier'],json.dumps(data,ensure_ascii=False)))
            db.execute('''UPDATE screening_runs SET finished_at=?,status=?,baseline_total=?,baseline_fetched=?,
                recent_total=?,recent_fetched=?,documents=?,annotated=?,error=NULL WHERE id=?''',
                (now(),'complete' if bf>=bt and rf>=rt else 'partial',bt,bf,rt,rf,
                 json.dumps(papers,ensure_ascii=False),annotated_count,run_id))
        return run_id
    except Exception as exc:
        with connect(path) as db:db.execute("UPDATE screening_runs SET finished_at=?,status='failed',error=? WHERE id=?",
            (now(),f'{type(exc).__name__}: {exc}'[:500],run_id))
        raise


def start(path,data):
    require_live(path);a,b,_,_=_window(data.get('start'),data.get('end'))
    pages=data.get('max_pages',10)
    if type(pages) is not int or not 1<=pages<=25:raise ValueError('스크리닝 문헌 한도가 유효하지 않습니다.')
    with connect(path) as db:
        active=db.execute("SELECT id FROM screening_runs WHERE status IN ('queued','running') LIMIT 1").fetchone()
        if active:return dict(id=active['id'],status='already_running')
        rid=db.execute('''INSERT INTO screening_runs(query,start_date,end_date,started_at,status,rules_version)
            VALUES (?,?,?,?,'queued',?)''',(QUERY,a.isoformat(),b.isoformat(),now(),VERSION)).lastrowid
    threading.Thread(target=_worker,args=(path,rid,a.isoformat(),b.isoformat(),pages),daemon=True).start()
    return dict(id=rid,status='queued')


def _worker(path,rid,start_date,end_date,pages):
    try:run(path,start_date,end_date,pages,run_id=rid)
    except Exception:pass


def _buckets(mentions,start,end,unit):
    a,b=_date(start),_date(end);cursor=a
    if unit=='week':cursor-=dt.timedelta(days=cursor.weekday())
    else:cursor=cursor.replace(day=1,month=(cursor.month-1)//3*3+1 if unit=='quarter' else cursor.month)
    out=[]
    while cursor<=b:
        if unit=='week':after=cursor+dt.timedelta(days=7)
        else:
            month=cursor.month+(3 if unit=='quarter' else 1)
            after=dt.date(cursor.year+(month-1)//12,(month-1)%12+1,1)
        x,y=max(a,cursor),min(b,after-dt.timedelta(days=1))
        keys={m['record_key'] for m in mentions if m.get('date') and x.isoformat()<=m['date']<=y.isoformat()}
        out.append(dict(start=x.isoformat(),end=y.isoformat(),count=len(keys)))
        cursor=after
    return out


def view(path,params):
    start,end=params.get('start'),params.get('end');a,b,old_end,new_start=_window(start,end)
    unit=params.get('unit','month')
    if unit not in ('week','month','quarter'):raise ValueError('그래프 단위가 유효하지 않습니다.')
    search=str(params.get('search','')).strip().lower()[:180]
    saved_ids={value for value in str(params.get('ids','')).split(',')[:100] if 0<len(value)<=200}
    with connect(path) as db:
        runs=[dict(r) for r in db.execute('SELECT * FROM screening_runs ORDER BY id DESC')]
        exact=next((r for r in runs if r['start_date']==start and r['end_date']==end and r['status'] in ('complete','partial')),None)
        covering=next((r for r in runs if r['start_date']<=start and r['end_date']>=end and r['status'] in ('complete','partial')),None)
        chosen=exact or covering
        latest=runs[0] if runs else None
        rows=[]
        if chosen:
            candidates=list(db.execute('SELECT * FROM screening_candidates WHERE run_id=?',(chosen['id'],)))
            identified={re.sub(r'[^a-z0-9]','',r['name'].lower()) for r in candidates if r['identifier']}
            for row in candidates:
                normalized=re.sub(r'[^a-z0-9]','',row['name'].lower())
                if row['name'].lower() in GENERIC or (not row['identifier'] and normalized in identified):continue
                data=json.loads(row['data']);mentions=[m for m in data['mentions'] if m.get('date') and start<=m['date']<=end]
                if (search and search not in row['name'].lower() and
                        search not in str(row['identifier'] or '').lower() and
                        row['candidate_id'] not in saved_ids):continue
                old={m['record_key'] for m in mentions if start<=m['date']<=old_end.isoformat()}
                recent={m['record_key'] for m in mentions if new_start.isoformat()<=m['date']<=end}
                total=old|recent
                if not total:continue
                delta=len(recent)-len(old);percent=round(delta/len(old)*100,1) if old else None
                status='early' if not old and len(recent)>=3 else ('growing' if len(recent)>=5 and delta>=3 and percent is not None and percent>=50 else 'other')
                evidence=[];seen=set()
                for m in sorted(mentions,key=lambda x:x.get('date') or '',reverse=True):
                    if m['record_key'] not in seen:seen.add(m['record_key']);evidence.append(m)
                rows.append(dict(id=row['candidate_id'],name=row['name'],identifier=row['identifier'],baseline=len(old),
                    recent=len(recent),delta=delta,percent=percent,total=len(total),status=status,
                    buckets=_buckets(mentions,start,end,unit),evidence=evidence[:8]))
    rows.sort(key=lambda r:(-r['delta'],-r['recent'],r['name'].lower()))
    visible=rows[:300]
    visible_ids={row['id'] for row in visible}
    visible.extend(row for row in rows if row['id'] in saved_ids and row['id'] not in visible_ids)
    def summary(run):
        if not run:return None
        return {k:run[k] for k in ('id','query','start_date','end_date','started_at','finished_at','status',
            'baseline_total','baseline_fetched','recent_total','recent_fetched','annotated','error','rules_version')}
    return dict(run=summary(chosen),latest_run=summary(latest),rows=visible,total_candidates=len(rows),
        growing=sum(r['status'] in ('growing','early') for r in rows),start=start,end=end,unit=unit,
        exact_scope=bool(exact),rules_version=VERSION,
        scope='선택 기간의 환경·신흥오염물질 문헌에서 Europe PMC 화학물질 주석과 명칭 패턴으로 찾은 후보입니다.',
        limitations=['미리 정한 물질 목록을 사용하지 않음','검색식 밖 문헌·주석이 없는 물질은 누락 가능',
            '문헌 언급 증가는 위해성·사용량·환경농도 증가가 아님','부분 수집이면 동일 크기 표본의 기술적 비교'])


def meta(path):
    with connect(path) as db:
        runs=[dict(r) for r in db.execute('SELECT id,start_date,end_date,status,started_at,finished_at,error,baseline_total,baseline_fetched,recent_total,recent_fetched,annotated FROM screening_runs ORDER BY id DESC LIMIT 20')]
    complete=[r for r in runs if r['status'] in ('complete','partial')]
    return dict(runs=runs,latest_end=max((r['end_date'] for r in complete),default=dt.date.today().isoformat()),rules_version=VERSION)
