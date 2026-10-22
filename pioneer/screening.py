"""Literature-first chemical screening without a predefined substance list."""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
import urllib.parse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from . import analysis, journals
from .core import connect, normalize, now, read_page
from .research import get_json, require_live


VERSION = 'journal-discovery-v1'
ANNOTATIONS = 'https://www.ebi.ac.uk/europepmc/annotations_api/annotationsByArticleIds'
QUERY = journals.QUERY
GENERIC = {
    'water','oxygen','nitrogen','carbon dioxide','sodium','chloride','sulfate','amino acid',
    'protein','lipid','glucose','atp','dna','rna','hydrogen','calcium','potassium','iron',
    'methanol','acetonitrile','formic acid','ethanol','acetone','isopropanol',
    'metal','carbon','phosphorus','sulfur','mineral','oxide','nitride','hydrocarbon',
    'alkali metal cation','fluoroalkane','chemical element','organic compound','inorganic compound',
    'peptide','methyl','fatty acid','polyunsaturated fatty acid',
    'environmental contaminant','emerging contaminant','pollutant','nanostructure','hydroxyl',
    'salt','electron','carbonate mineral','amino oligosaccharide','palmitoyl amino acid',
    'selenite esters','polycyclic hydrocarbon','phthalate','phthalate ester','paraben','quinone',
    'sulfonamide','fluoroquinolone antibiotic','bisphenol','polyethylene polymer','polyester polymer',
    'antibiotic','antibiotics','pesticide','pesticides','insecticide','insecticides',
    'herbicide','herbicides','fungicide','fungicides','surfactant','surfactants',
    'active substance','active substances','thyroid hormone','extracellular polymeric substance',
}
SIGNAL_PATTERNS = {
    'environment': re.compile(r'\b(?:detect(?:ed|ion|able)?|occurrence|quantif(?:ied|ication)|measured concentration|'
        r'detection frequenc\w*|monitoring|environmental sample|wastewater|effluent|sewage|surface water|river|'
        r'sediment|soil|air|dust|biota|wildlife|placenta|cord blood|remote (?:region|area|site))\b',re.I),
    'hazard': re.compile(r'\b(?:toxicity|toxicological|ecotoxic\w*|endocrine|estrogen|androgen|thyroid|developmental|'
        r'neurotox\w*|immunotox\w*|reproduct\w*|genotox\w*|carcinogen\w*|bioassay|ToxCast|Tox21)\b',re.I),
    'exposure': re.compile(r'\b(?:exposure|biomonitoring|product use|consumer product|industrial use|production|'
        r'manufactur\w*|emission|release|polymer additive|tire|tyre|electronic|battery)\b',re.I),
    'regulatory': re.compile(r'\b(?:regulat\w*|restriction|watch list|candidate list|SVHC|ECHA|REACH|EPA|'
        r'priority substance|risk management|K-REACH)\b',re.I),
    'novel_context': re.compile(r'\b(?:first report|first detect\w*|newly detect\w*|novel pollutant|replacement chemical|'
        r'transformation product|metabolite|unintentionally added substance|NIAS|suspect screening|non-target screening|'
        r'polymer additive|tire|tyre|electronic|battery)\b',re.I),
    'gap': re.compile(r'\b(?:data gap|knowledge gap|limited (?:\w+ ){0,2}(?:data|evidence|information|studies)|poorly understood|'
        r'remains? unknown|further (?:study|studies|research)|understudied|not well understood)\b',re.I),
}
GAP_DIMENSIONS = {
    '만성 영향': re.compile(r'\bchronic\b',re.I), '저농도 영향': re.compile(r'\blow[- ]dose\b|low concentration',re.I),
    '발달독성': re.compile(r'\bdevelopmental|prenatal|embryo',re.I),
    '성차': re.compile(r'\bsex[- ]specific|sex differences?|male and female',re.I),
    '혼합물': re.compile(r'\bmixture|combined exposure|co-exposure',re.I),
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
    # Equal caps per journal prevent high-volume journals from occupying all
    # of a partial sample. Totals include every selected journal, including zeroes.
    papers=[];keys=set();total=0;received=0
    per_journal=max(1,max_pages*100//len(journals.JOURNALS))
    for issn,journal_name in journals.JOURNALS:
        query=f'ISSN:{issn} AND FIRST_PDATE:[{start.isoformat()} TO {end.isoformat()}]'
        cursor='*';seen_cursors=set();journal_total=None;journal_received=0
        while journal_received<per_journal:
            size=min(100,per_journal-journal_received)
            page=fetch_page(dict(query=query,format='json',resultType='core',pageSize=size,cursorMark=cursor))
            raw=page.get('resultList',{}).get('result')
            if type(page.get('hitCount')) is not int or page['hitCount']<0 or not isinstance(raw,list):
                raise ValueError('자료원 응답 형식이 유효하지 않습니다.')
            if journal_total is not None and journal_total!=page['hitCount']:
                raise ValueError('검색 중 총 문헌 수가 변했습니다.')
            if journal_total is None:journal_total=page['hitCount'];total+=journal_total
            journal_received+=len(raw);received+=len(raw)
            for item in raw:
                p=normalize(item);p['journal']=p['journal'] or journal_name;p['journal_issn']=issn
                key=p['doi'] or p['source_id']
                if key not in keys:
                    keys.add(key);p['pub_types']=json.loads(p['pub_types']);papers.append(p)
            if journal_received>=journal_total or journal_received>=per_journal:break
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
    groups=[ids[offset:offset+10] for offset in range(0,len(ids),10)]
    with ThreadPoolExecutor(max_workers=3) as pool:
        payloads=list(pool.map(fetch_group,groups))
    for payload in payloads:
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
                    journal=paper.get('journal',''),section='title' if section.startswith('Title ') else 'abstract',
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
                    date=paper['date'],title=paper['title'],doi=paper['doi'],source_id=paper['source_id'],journal=paper.get('journal',''),section=field,
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
        group_mentions=journals.mentions(papers)
        for mention in group_mentions:
            if name_key(mention['name']) in named:mention['identifier']=named[name_key(mention['name'])]
        mentions=annotated_mentions+regex_mentions+group_mentions
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
                    (run_id,analysis.key('journal-candidate',cid),name,data['identifier'],json.dumps(data,ensure_ascii=False)))
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


def _multi_signals(papers,old_keys,recent_keys,baseline,recent,delta,percent):
    records={name:set() for name in SIGNAL_PATTERNS};examples={name:[] for name in SIGNAL_PATTERNS}
    dimensions=set();media=set()
    for paper in papers:
        text=(paper.get('title','')+' '+paper.get('abstract','')).strip();key=paper['record_key']
        for name,pattern in SIGNAL_PATTERNS.items():
            match=pattern.search(text)
            if match:
                records[name].add(key)
                if len(examples[name])<3:examples[name].append(dict(record_key=key,date=paper.get('date'),title=paper.get('title'),matched=match.group()))
        for label,pattern in GAP_DIMENSIONS.items():
            if pattern.search(text):dimensions.add(label)
        for label,pattern in analysis.RULES['medium'].items():
            if re.search(pattern,text,re.I):media.add(label)
    recent_counts={name:len(keys&recent_keys) for name,keys in records.items()}
    baseline_counts={name:len(keys&old_keys) for name,keys in records.items()}
    novelty=min(100,(65 if baseline==0 and recent>=3 else min(55,max(0,delta)*7))+
        min(25,recent_counts['novel_context']*8)+(10 if percent is not None and percent>=100 else 0))
    env=min(100,recent_counts['environment']*8+len(media)*9+
        (20 if recent_counts['environment'] and not baseline_counts['environment'] else 0))
    hazard=min(100,recent_counts['hazard']*10+sum(8 for label,pattern in analysis.RULES['endpoint'].items()
        if any(re.search(pattern,(p.get('title','')+' '+p.get('abstract','')),re.I) for p in papers if p['record_key'] in recent_keys)))
    exposure=min(100,recent_counts['exposure']*10)
    regulatory=min(100,recent_counts['regulatory']*25)
    gate=env>=30 or exposure>=30
    gap=min(100,recent_counts['gap']*20) if gate else 0
    components=dict(N=novelty,E=env,H=hazard,X=exposure,R=regulatory,G=gap)
    score=round(.20*novelty+.20*env+.20*hazard+.15*exposure+.15*regulatory+.10*gap,1)
    types=[]
    if baseline==0 and recent>=1:types.append('new_mention')
    elif recent>=5 and delta>=3 and percent is not None and percent>=50:types.append('publication_growth')
    if recent_counts['environment'] and (not baseline_counts['environment'] or recent_counts['environment']>baseline_counts['environment']):types.append('environmental_detection')
    if recent_counts['hazard']:types.append('hazard_signal')
    if recent_counts['exposure']>baseline_counts['exposure']:types.append('exposure_signal')
    if recent_counts['regulatory']>baseline_counts['regulatory']:types.append('regulatory_attention')
    missing=[name for name in GAP_DIMENSIONS if name not in dimensions]
    return dict(priority_score=score,components=components,signal_types=types,
        signal_counts={name:dict(baseline=baseline_counts[name],recent=recent_counts[name]) for name in records},
        signal_examples=examples,environmental_media=sorted(media),gap_gate=gate,
        gap_questions=missing if hazard else [],evidence_level='제목·초록 키워드 신호 · 사람 미검토',
        source_coverage={
            'literature':'Europe PMC 수집',
            'environment':'문헌 내 검출·매질 언급만 확인 · NORMAN 미연결',
            'hazard':'문헌 내 독성 endpoint 언급만 확인 · CompTox 미연결',
            'regulatory':'문헌 내 규제 키워드만 확인 · 공식 규제 목록 미연결'})


def view(path,params):
    start,end=params.get('start'),params.get('end');a,b,old_end,new_start=_window(start,end)
    unit=params.get('unit','month')
    if unit not in ('week','month','quarter'):raise ValueError('그래프 단위가 유효하지 않습니다.')
    search=str(params.get('search','')).strip().lower()[:180]
    saved_ids={value for value in str(params.get('ids','')).split(',')[:100] if 0<len(value)<=200}
    only_id=str(params.get('only_id',''))[:200]
    preview=str(params.get('preview','0'))=='1'
    with connect(path) as db:
        runs=[dict(r) for r in db.execute('SELECT * FROM screening_runs WHERE query=? ORDER BY id DESC',(QUERY,))]
        exact=next((r for r in runs if r['start_date']==start and r['end_date']==end and r['status'] in ('complete','partial')),None)
        covering=next((r for r in runs if r['start_date']<=start and r['end_date']>=end and r['status'] in ('complete','partial')),None)
        chosen=exact or covering
        latest=runs[0] if runs else None
        rows=[]
        if chosen:
            candidates=list(db.execute('SELECT * FROM screening_candidates WHERE run_id=?',(chosen['id'],)))
            documents={p['record_key']:p for p in json.loads(chosen['documents'] or '[]')}
            identified={re.sub(r'[^a-z0-9]','',r['name'].lower()) for r in candidates if r['identifier']}
            for row in candidates:
                if only_id and row['candidate_id']!=only_id:continue
                normalized=re.sub(r'[^a-z0-9]','',row['name'].lower())
                if (row['name'].lower() in GENERIC or not re.search(r'[A-Za-z]',row['name']) or
                        (not row['identifier'] and normalized in identified)):continue
                data=json.loads(row['data']);mentions=[m for m in data['mentions'] if m.get('date') and start<=m['date']<=end]
                display_name=row['name']
                search_text=display_name.lower()
                if (search and search not in search_text and
                        search not in str(row['identifier'] or '').lower() and
                        row['candidate_id'] not in saved_ids):continue
                old={m['record_key'] for m in mentions if start<=m['date']<=old_end.isoformat()}
                recent={m['record_key'] for m in mentions if new_start.isoformat()<=m['date']<=end}
                total=old|recent
                if not total:continue
                delta=len(recent)-len(old);percent=round(delta/len(old)*100,1) if old else None
                status='early' if not old and recent else ('growing' if len(recent)>=5 and delta>=3 and percent is not None and percent>=50 else 'other')
                evidence=[];seen=set()
                for m in sorted(mentions,key=lambda x:x.get('date') or '',reverse=True):
                    if m['record_key'] not in seen:seen.add(m['record_key']);evidence.append(m)
                if preview:
                    evidence=[{k:m.get(k) for k in ('date','section','exact','title','source_id','doi','journal')} for m in evidence[:1]]
                # Signal evidence is local to the candidate mention, not an unrelated
                # toxicology/regulation sentence elsewhere in the same abstract.
                contexts={}
                for m in mentions:
                    k=m['record_key']
                    contexts.setdefault(k,dict(record_key=k,title='',abstract='',date=m.get('date')))
                    contexts[k]['abstract']+=' '+m.get('snippet','')
                signal_data=_multi_signals(list(contexts.values()),old,recent,len(old),len(recent),delta,percent)
                rows.append(dict(id=row['candidate_id'],name=display_name,identifier=row['identifier'],
                    candidate_kind='물질군·명칭 후보' if not row['identifier'] else '자료원 주석 후보',score_max=100,baseline=len(old),
                    recent=len(recent),delta=delta,percent=percent,total=len(total),status=status,
                    buckets=_buckets(mentions,start,end,unit),evidence=evidence[:1] if preview else evidence[:8],**signal_data))
    rows.sort(key=lambda r:(-r['priority_score'],-r['delta'],-r['recent'],r['name'].lower()))
    visible=rows[:120 if preview else 300]
    visible_ids={row['id'] for row in visible}
    visible.extend(row for row in rows if row['id'] in saved_ids and row['id'] not in visible_ids)
    def summary(run):
        if not run:return None
        return {k:run[k] for k in ('id','query','start_date','end_date','started_at','finished_at','status',
            'baseline_total','baseline_fetched','recent_total','recent_fetched','annotated','error','rules_version')}
    return dict(run=summary(chosen),latest_run=summary(latest),rows=visible,total_candidates=len(rows),
        journals=[dict(issn=i,name=n) for i,n in journals.JOURNALS],
        journal_coverage=[dict(issn=i,name=n,records=sum(p.get('journal_issn')==i for p in documents.values()))
            for i,n in journals.JOURNALS] if chosen else [],
        publication_growth=sum(r['status']=='growing' for r in rows),new_mentions=sum(r['baseline']==0 and r['recent']>0 for r in rows),
        growing=sum(bool(r['signal_types']) for r in rows),start=start,end=end,unit=unit,
        exact_scope=bool(exact),rules_version=VERSION,
        scope='지정 저널의 제목·초록에서 물질과 물질군을 자동 추출했습니다. 신규는 수집 범위 내 첫 언급이며 세계 최초 보고를 뜻하지 않습니다.',
        limitations=['지정 저널과 수집된 제목·초록 범위의 자동 후보','명칭·물질군 추출 오류 가능',
            '신규는 수집 범위 내 첫 언급','문헌 증가는 위해성 증가가 아님','부분 수집이면 표본 내 비교'])


def candidate(path,params):
    cid=str(params.get('id',''))
    if not cid:raise ValueError('후보 ID가 필요합니다.')
    data=view(path,{**params,'only_id':cid,'preview':'0','ids':cid})
    if not data['rows']:raise ValueError('선택한 기간에서 후보 근거를 찾을 수 없습니다.')
    return data['rows'][0]


def profiles(path,params):
    ids=[x for x in str(params.get('ids','')).split(',')[:50] if x]
    if not ids:return []
    output=[]
    with connect(path) as db:
        for cid in ids:
            row=db.execute('''SELECT c.*,r.query,r.start_date,r.end_date,r.started_at,r.finished_at,r.status,
                r.baseline_total,r.baseline_fetched,r.recent_total,r.recent_fetched,r.documents,r.annotated,r.rules_version
                FROM screening_candidates c JOIN screening_runs r ON r.id=c.run_id
                WHERE c.candidate_id=? AND r.status IN ('complete','partial') ORDER BY r.id DESC LIMIT 1''',(cid,)).fetchone()
            if not row:continue
            data=json.loads(row['data']);mentions=data['mentions'];keys={m['record_key'] for m in mentions}
            papers=[]
            for p in json.loads(row['documents'] or '[]'):
                if p['record_key'] not in keys:continue
                p=dict(p);p['pub_types']=p.get('pub_types') if isinstance(p.get('pub_types'),list) else json.loads(p.get('pub_types') or '[]')
                p.update(review={},document_flags=[t for t in p['pub_types'] if re.search('review|preprint|retract|correct|errat',t,re.I)],fulltext_available=False,fulltext_license=None)
                papers.append(p)
            run_id=-int(row['run_id']);run=dict(id=run_id,query=row['query']+f" AND FIRST_PDATE:[{row['start_date']} TO {row['end_date']}]",source='Europe PMC 저널 스크리닝',
                start_year=int(row['start_date'][:4]),end_year=int(row['end_date'][:4]),started_at=row['started_at'],
                finished_at=row['finished_at'],status=row['status'],source_total=(row['baseline_total'] or 0)+(row['recent_total'] or 0),
                fetched=(row['baseline_fetched'] or 0)+(row['recent_fetched'] or 0),unique_count=len(papers),
                query_version=row['rules_version'],error=None)
            chemical=dict(id='screen:'+cid,name=row['name'],terms=[row['name']],run=run,latest_attempt=run,papers=papers,trend=[],comparison=None,
                identity=None,derived_from_screening=True,screen_candidate_id=cid)
            for year in range(run['start_year'],run['end_year']+1):
                chemical['trend'].append(dict(year=year,count=sum(p.get('year')==year for p in papers),incomplete_year=False))
            evidence=[]
            for paper in papers:evidence+=analysis.extract(paper,run_id)
            for e in evidence:e.update(review={},paper_review={})
            chemical['evidence']=evidence;chemical['map']=analysis.evidence_map(papers,evidence)
            chemical['signals']=analysis.signals(chemical);chemical['questions']=analysis.questions(chemical,evidence)
            for q in chemical['questions']:
                q.update(review={},followups=[],followup_query=f'TITLE_ABS:"{row["name"]}" AND TITLE_ABS:"{q["endpoint"]}"')
            output.append(chemical)
    return output


def meta(path):
    with connect(path) as db:
        runs=[dict(r) for r in db.execute('SELECT id,start_date,end_date,status,started_at,finished_at,error,baseline_total,baseline_fetched,recent_total,recent_fetched,annotated FROM screening_runs WHERE query=? ORDER BY id DESC LIMIT 20',(QUERY,))]
    complete=[r for r in runs if r['status'] in ('complete','partial')]
    return dict(journals=[dict(issn=i,name=n) for i,n in journals.JOURNALS],runs=runs,latest_end=max((r['end_date'] for r in complete),default=dt.date.today().isoformat()),rules_version=VERSION)
