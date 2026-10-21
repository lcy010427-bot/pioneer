"""Research workspace: explicit provenance, append-only review, bounded external requests."""
from __future__ import annotations

import csv
import io
import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from . import analysis
from .core import API, collect, connect, normalize, now, read_page, view

ROOT = Path(__file__).resolve().parent.parent
RELATION_TYPES={'transformation_mention':'변환 관계 언급','transformation':'변환 관계 제안','substitution':'대체 관계 제안','shared_use':'공통 용도','co_mention':'공동 언급','biological_relation':'생물학적 관계 제안','hypothesis':'검토 가설'}
ENDPOINT_TERMS={
    'mortality':['mortality','lethality','survival'],
    'endocrine':['endocrine','estrogen','androgen','thyroid','steroidogenesis'],
    'neural':['neurotoxicity','neurodevelopment','brain'],
    'reproduction':['reproduction','reproductive','fertility'],
    'liver':['liver','hepatic','hepatotoxicity'],
    'genotoxicity':['genotoxicity','DNA damage','mutagenic'],
}


def init_research(path):
    with connect(path) as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS identities(
            subject TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL,
            response TEXT NOT NULL, checked_at TEXT NOT NULL, source TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS decisions(
            id INTEGER PRIMARY KEY, target_type TEXT NOT NULL, target_id TEXT NOT NULL,
            status TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL,
            created_at TEXT NOT NULL, rule_version TEXT NOT NULL,
            actor_type TEXT NOT NULL DEFAULT 'user_entered');
        CREATE TABLE IF NOT EXISTS candidates(
            id TEXT PRIMARY KEY, name TEXT NOT NULL, evidence TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, promoted_id TEXT);
        CREATE TABLE IF NOT EXISTS fulltexts(
            source_id TEXT PRIMARY KEY, pmcid TEXT NOT NULL, doi TEXT,
            license TEXT NOT NULL, paragraphs TEXT NOT NULL, retrieved_at TEXT NOT NULL,
            content_sha256 TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS followups(
            id INTEGER PRIMARY KEY, question_id TEXT NOT NULL, query TEXT NOT NULL,
            started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
            source_total INTEGER, fetched INTEGER DEFAULT 0, papers TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS manual_relations(id TEXT PRIMARY KEY,data TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS evaluations(
            id INTEGER PRIMARY KEY,participant TEXT NOT NULL,condition TEXT NOT NULL,
            task TEXT NOT NULL,order_index INTEGER NOT NULL,started_at TEXT NOT NULL,
            finished_at TEXT,elapsed_seconds REAL,checked INTEGER,correct INTEGER,
            usefulness INTEGER,notes TEXT,evaluator TEXT);
        CREATE TABLE IF NOT EXISTS external_log(
            id INTEGER PRIMARY KEY, operation TEXT NOT NULL, subject TEXT NOT NULL,
            started_at TEXT NOT NULL, status TEXT NOT NULL, requests INTEGER DEFAULT 0,
            details TEXT);
        CREATE INDEX IF NOT EXISTS decisions_target ON decisions(target_type,target_id,id);
        CREATE INDEX IF NOT EXISTS papers_run ON papers(run_id);
        ''')
        if 'active' not in {r['name'] for r in db.execute('PRAGMA table_info(candidates)')}:
            db.execute('ALTER TABLE candidates ADD COLUMN active INTEGER NOT NULL DEFAULT 1')
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('schema_version','4')")
    from .radar import init_radar
    init_radar(path)
    from .screening import init_screening
    init_screening(path)
    from .tracking import init_tracking
    init_tracking(path)


def require_live(path):
    with connect(path) as db:
        row=db.execute("SELECT value FROM metadata WHERE key='dataset_kind'").fetchone()
    if not row or row['value']!='live':
        raise ValueError('합성 데모에서는 외부 수집·실자료 편입을 수행하지 않습니다.')


def get_json(url, retries=2):
    """Retry only transient service errors; leave TLS and proxy authentication intact."""
    for attempt in range(retries+1):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':'PIONEER/0.2 research monitor','Accept':'application/json'})
            with urllib.request.urlopen(req,timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code not in (429,500,502,503,504) or attempt==retries:
                raise
            # Do not turn an excessive Retry-After into an early retry.
            retry=e.headers.get('Retry-After','')
            if retry.isdigit() and int(retry)>10:
                raise
            time.sleep(int(retry) if retry.isdigit() else 2**attempt)


def lookup_identity(path, subject, name, request=get_json):
    require_live(path)
    if not isinstance(name,str) or not 2<=len(name)<=180:
        raise ValueError('물질명 길이가 유효하지 않습니다.')
    with connect(path) as db:
        cached=db.execute('SELECT * FROM identities WHERE subject=?',(subject,)).fetchone()
        if cached and cached['name']==name and cached['status']!='failed':
            return dict(cached, response=json.loads(cached['response']))
        log=db.execute("INSERT INTO external_log(operation,subject,started_at,status) VALUES ('pubchem',?,?,'running')",(subject,now())).lastrowid
    url='https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/'+urllib.parse.quote(name,safe='')+'/property/InChIKey,MolecularFormula/JSON'
    status='failed'; payload={}
    try:
        data=request(url)
        props=data.get('PropertyTable',{}).get('Properties',[])
        if not isinstance(props,list) or not props or any(type(p.get('CID')) is not int or p['CID']<=0 for p in props):
            raise ValueError('PubChem 식별 결과가 없습니다.')
        payload={'properties':props,'url':url,'warning':'DB 명칭 매핑 후보입니다. 원문 물질·염·이성질체 확인을 대체하지 않습니다.'}
        status='unique_match' if len(props)==1 else 'ambiguous'
    except urllib.error.HTTPError as e:
        if e.code==404:
            status='not_found';payload={'message':'PubChem에서 명칭에 일치하는 compound를 찾지 못했습니다.'}
        else:
            payload={'error':f'HTTP {e.code}'}
    except Exception as e:
        payload={'error':str(e)[:300]}
    with connect(path) as db:
        if not (status=='failed' and cached and cached['status']!='failed'):
            db.execute('INSERT OR REPLACE INTO identities VALUES (?,?,?,?,?,?)',
                       (subject,name,status,json.dumps(payload,ensure_ascii=False),now(),'PubChem PUG REST'))
        db.execute('UPDATE external_log SET status=?,requests=1,details=? WHERE id=?',(status,json.dumps(payload,ensure_ascii=False),log))
    return dict(subject=subject,name=name,status=status,response=payload,checked_at=now(),source='PubChem PUG REST')


def sync_candidates(path, state=None):
    state=state or view(path)
    discovered=analysis.discover(state['chemicals'])
    with connect(path) as db:
        db.execute('UPDATE candidates SET active=0')
        for c in discovered:
            db.execute('''INSERT INTO candidates(id,name,evidence,created_at,updated_at) VALUES (?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET evidence=excluded.evidence,updated_at=excluded.updated_at,active=1''',
                (c['id'],c['name'],json.dumps(c,ensure_ascii=False),now(),now()))
    return len(discovered)


def latest_reviews(db):
    rows=[dict(r) for r in db.execute('SELECT * FROM decisions ORDER BY id')]
    return {(r['target_type'],r['target_id']):r for r in rows},rows


def relation_edges(state, reviews):
    edges=[]
    parent=next((c for c in state['chemicals'] if c['id']=='6ppd'),None)
    product=next((c for c in state['chemicals'] if c['id']=='6ppd-q'),None)
    if not parent or not product:
        return []
    for c in (parent,product):
        if not c['run']:
            continue
        for p in c['papers']:
            for field in ('title','abstract'):
                for sentence,offset in analysis.sentences(p[field]):
                    if (re.search(r'\b6PPD(?![- ]?(?:quinone|Q)\b)\b',sentence,re.I) and
                        re.search(r'\b6PPD[- ]?(?:quinone|Q)\b',sentence,re.I) and
                        re.search(r'transformation product|oxidation product|transformed into|oxidized to|ozonat',sentence,re.I)):
                        eid=analysis.key('relation-v1',c['run']['id'],p['record_key'],field,offset)
                        edges.append(dict(id=eid,from_id='6ppd',to_id='6ppd-q',type='transformation_mention',
                            evidence_type='literature_statement',direction='parent_to_product_proposed',
                            record_key=p['record_key'],run_id=c['run']['id'],field=field,snippet=sentence,year=p['year'],doi=p['doi'],
                            flags=['negation_or_uncertainty'] if re.search(r'\b(?:not|no|without|may|might|could)\b',sentence,re.I) else [],
                            review=reviews.get(('relation',eid),{}),paper_review=reviews.get(('paper',f"{c['run']['id']}:{p['record_key']}"),{}),
                            limitations=['문헌 문장에 나타난 변환 관계 언급 후보',
                                         '변환 조건·실험 수행 여부·원문 근거 미확인',
                                         '공동 독성·인과관계·근거 전이를 뜻하지 않음']))
                        break
    # Do not count a paper present in both searches as independent supporting evidence.
    unique={}
    for e in edges:
        unique.setdefault(e['doi'] or e['record_key'],e)
    return list(unique.values())


def workspace(path, settings=None):
    state=view(path)
    settings=settings or {}
    with connect(path) as db:
        reviews,history=latest_reviews(db)
        validation=db.execute("SELECT value FROM metadata WHERE key='validation_copy'").fetchone()
        state['validation_copy']=bool(validation and validation['value']=='true')
        identities={r['subject']:dict(r,response=json.loads(r['response'])) for r in db.execute('SELECT * FROM identities')}
        texts={r['source_id']:dict(r,paragraphs=json.loads(r['paragraphs'])) for r in db.execute('SELECT * FROM fulltexts')}
        candidates=[]
        for r in db.execute('SELECT * FROM candidates WHERE active=1 OR promoted_id IS NOT NULL ORDER BY created_at,id'):
            c=dict(r); c['evidence']=json.loads(c['evidence']);c['review']=reviews.get(('candidate',c['id']),{})
            c['identity']=identities.get('candidate:'+c['id']);candidates.append(c)
        followups=[dict(r,papers=json.loads(r['papers'] or '[]')) for r in db.execute('SELECT * FROM followups ORDER BY id DESC')]
        manual_edges=[json.loads(r['data']) for r in db.execute('SELECT data FROM manual_relations ORDER BY created_at')]
        evaluations=[dict(r) for r in db.execute('SELECT * FROM evaluations ORDER BY id DESC')]
        usage=[dict(r) for r in db.execute('SELECT id,operation,subject,started_at,status,requests FROM external_log ORDER BY id DESC LIMIT 30')]
    for c in state['chemicals']:
        ev=[]
        for p in c['papers']:
            text=texts.get(p['source_id'])
            p['fulltext_available']=bool(text)
            p['fulltext_license']=text['license'] if text else None
            p['review']=reviews.get(('paper',f"{c['run']['id']}:{p['record_key']}"),{})
            p['document_flags']=[t for t in p['pub_types'] if re.search('review|preprint|retract|correct|errat',t,re.I)]
            ev+=analysis.extract(p,c['run']['id'],text['paragraphs'] if text else None)
        for e in ev:
            e['review']=reviews.get(('evidence',e['id']),{})
            e['paper_review']=reviews.get(('paper',f"{e['run_id']}:{e['record_key']}"),{})
        c['identity']=identities.get('chemical:'+c['id'])
        c['evidence']=ev
        c['map']=analysis.evidence_map(c['papers'],ev)
        c['signals']=analysis.signals(c,**settings)
        c['questions']=analysis.questions(c,ev)
        for q in c['questions']:
            # Store actual API syntax, never pass extraction regexes to a search engine.
            terms=' OR '.join('TITLE_ABS:"'+t.replace('"','')+'"' for t in c['terms'])
            endpoints=' OR '.join('TITLE_ABS:"'+t+'"' for t in ENDPOINT_TERMS[q['endpoint']])
            q['followup_query']=f'({terms}) AND ({endpoints})'
            q['review']=reviews.get(('question',q['id']),{})
            q['followups']=[f for f in followups if f['question_id']==q['id']]
    for edge in manual_edges:
        edge['review']=reviews.get(('relation',edge['id']),{})
        edge['paper_review']=reviews.get(('paper',f"{edge['run_id']}:{edge['record_key']}"),{})
    state.update(candidates=candidates,relations=relation_edges(state,reviews)+manual_edges,relation_types=RELATION_TYPES,evaluations=evaluations,review_history=history,
                 methods=json.loads((ROOT/'docs/methods.json').read_text()) if (ROOT/'docs/methods.json').exists() else [],
                 labels=analysis.LABELS,external_log=usage,extraction_version=analysis.VERSION,
                 review_warning='검토자 이름·판정은 사용자 입력이며 독립적인 신원·전문성 검증이 아닙니다.')
    return state


def save_review(path,data):
    kind=data.get('target_type'); target=data.get('target_id');status=data.get('status')
    actor=data.get('actor');reason=data.get('reason')
    if kind not in ('paper','evidence','question','relation','candidate') or status not in ('accepted','rejected','uncertain','followup_complete'):
        raise ValueError('검토 대상·상태가 유효하지 않습니다.')
    if not all(isinstance(s,str) and 2<=len(s)<=2000 for s in (actor,reason)):
        raise ValueError('검토자와 구체적인 사유를 2자 이상 입력하세요.')
    state=workspace(path)
    targets={'paper':set(),'evidence':set(),'question':set(),'relation':{r['id'] for r in state['relations']},
             'candidate':{c['id'] for c in state['candidates']}}
    for c in state['chemicals']:
        targets['paper'].update(f"{c['run']['id']}:{p['record_key']}" for p in c['papers'])
        targets['evidence'].update(e['id'] for e in c['evidence'])
        targets['question'].update(q['id'] for q in c['questions'])
    if target not in targets[kind]:
        raise ValueError('현재 스냅샷에 없는 대상입니다. 화면을 갱신하세요.')
    if status=='followup_complete':
        if kind!='question':
            raise ValueError('보완 검색 완료 상태는 질문에만 적용합니다.')
        with connect(path) as db:
            done=db.execute("SELECT 1 FROM followups WHERE question_id=? AND status='complete'",(target,)).fetchone()
        if not done:
            raise ValueError('완전 수집된 실제 보완 검색 기록이 먼저 필요합니다.')
    with connect(path) as db:
        rid=db.execute('INSERT INTO decisions(target_type,target_id,status,actor,reason,created_at,rule_version) VALUES (?,?,?,?,?,?,?)',
            (kind,target,status,actor.strip(),reason.strip(),now(),analysis.VERSION)).lastrowid
    return rid


def promote(path,candidate_id,actor,reason):
    require_live(path)
    if not all(isinstance(x,str) and len(x.strip())>=2 for x in (actor,reason)):
        raise ValueError('편입에는 검토자와 식별 검토 사유가 필요합니다.')
    with connect(path) as db:
        c=db.execute('SELECT * FROM candidates WHERE id=?',(candidate_id,)).fetchone()
        identity=db.execute('SELECT * FROM identities WHERE subject=?',('candidate:'+str(candidate_id),)).fetchone()
        if not c or not identity or identity['status']!='unique_match':
            raise ValueError('PubChem 단일 명칭 매핑 후보가 확인되어야 합니다. 모호한 후보는 자동 편입하지 않습니다.')
        if c['promoted_id']:
            return c['promoted_id']
        candidate_props=json.loads(identity['response']).get('properties',[])
        candidate_cid=candidate_props[0].get('CID') if candidate_props else None
        for other in db.execute("SELECT response FROM identities WHERE subject LIKE 'chemical:%' AND status='unique_match'"):
            props=json.loads(other['response']).get('properties',[])
            if any(p.get('CID')==candidate_cid for p in props):
                raise ValueError('이미 관심 목록에 같은 PubChem CID가 있습니다. 동의어 후보를 별도 물질로 편입하지 않습니다.')
        # Preserve the original name; PubChem synonyms are not blindly added to search queries.
        cid='candidate-'+candidate_id[:12]
        db.execute('INSERT INTO chemicals VALUES (?,?,?)',(cid,c['name'],json.dumps([c['name']])))
        db.execute('UPDATE candidates SET promoted_id=? WHERE id=?',(cid,candidate_id))
        db.execute('INSERT INTO identities VALUES (?,?,?,?,?,?)',('chemical:'+cid,identity['name'],identity['status'],identity['response'],identity['checked_at'],identity['source']))
        db.execute('INSERT INTO decisions(target_type,target_id,status,actor,reason,created_at,rule_version) VALUES (?,?,?,?,?,?,?)',
            ('candidate',candidate_id,'accepted',actor,reason,now(),analysis.VERSION))
    return cid


def fetch_fulltext(path,chemical_id,record_key,request=get_json,read_xml=None):
    require_live(path)
    state=view(path)
    chemical=next((c for c in state['chemicals'] if c['id']==chemical_id),None)
    paper=next((p for p in chemical['papers'] if p['record_key']==record_key),None) if chemical else None
    if not paper:
        raise ValueError('현재 스냅샷의 문헌을 선택하세요.')
    with connect(path) as db:
        cached=db.execute('SELECT * FROM fulltexts WHERE source_id=?',(paper['source_id'],)).fetchone()
        if cached:
            return dict(source_id=paper['source_id'],cached=True,license=cached['license'])
    src,sid=paper['source_id'].split(':',1)
    if not re.fullmatch('[A-Z]+',src) or not re.fullmatch('[A-Za-z0-9]+',sid):
        raise ValueError('지원하지 않는 자료원 식별자입니다.')
    query=f'EXT_ID:{sid} AND SRC:{src}'
    meta=request(API+'?'+urllib.parse.urlencode(dict(query=query,format='json',resultType='core',pageSize=1)))
    records=meta.get('resultList',{}).get('result',[])
    if not records or str(records[0].get('id'))!=sid or records[0].get('source')!=src:
        raise ValueError('원문 메타데이터 식별자가 일치하지 않습니다.')
    record=records[0]; pmcid=record.get('pmcid','')
    if record.get('isOpenAccess')!='Y' or not re.fullmatch(r'PMC\d+',pmcid):
        raise ValueError('API에서 제공하는 공개 원문이 없습니다. 초록 수준으로 유지합니다.')
    url=f'https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML'
    if read_xml:
        raw=read_xml(url)
    else:
        with urllib.request.urlopen(url,timeout=30) as r:
            raw=r.read(12_000_001)
    if len(raw)>12_000_000:
        raise ValueError('로컬 분석의 원문 크기 한도(12 MB)를 초과했습니다.')
    root=ET.fromstring(raw)
    ids={n.get('pub-id-type'):(n.text or '') for n in root.findall('.//article-meta/article-id')}
    xml_pmcid=ids.get('pmcid') or ('PMC'+ids.get('pmc','') if ids.get('pmc') else '')
    if xml_pmcid!=pmcid:
        raise ValueError('원문 PMCID가 조회 결과와 일치하지 않습니다.')
    doi=ids.get('doi','').lower()
    if paper['doi'] and not doi:
        raise ValueError('원문의 DOI를 확인하지 못했습니다.')
    if paper['doi'] and doi and paper['doi']!=doi:
        raise ValueError('원문 DOI가 저장된 문헌과 다릅니다. 자동 연결하지 않습니다.')
    license_text=' '.join(' '.join(n.itertext()) for n in root.findall('.//permissions/license')).strip()
    # The full-text API alone is not sufficient proof of permission for further processing.
    if not re.search('creative commons|public domain|CC[- ]BY',license_text,re.I):
        raise ValueError('공개 이용 라이선스를 확인하지 못했습니다. 원문 저장·분석을 보류합니다.')
    parents={child:parent for parent in root.iter() for child in parent}
    paragraphs=[]
    for i,p in enumerate(root.findall('./body//p')):
        current=p; headings=[]
        while current in parents:
            current=parents[current]
            if current.tag=='sec' and current.find('title') is not None:
                headings.append(' '.join(current.find('title').itertext()))
        text=' '.join(''.join(p.itertext()).split())
        if text:
            paragraphs.append(dict(id=p.get('id') or f'p{i}',section=' / '.join(reversed(headings)) or 'body',text=text))
    if not paragraphs:
        raise ValueError('분석 가능한 원문 본문 문단이 없습니다.')
    with connect(path) as db:
        db.execute('INSERT INTO fulltexts VALUES (?,?,?,?,?,?,?)',(paper['source_id'],pmcid,doi,license_text,
                   json.dumps(paragraphs,ensure_ascii=False),now(),__import__('hashlib').sha256(raw).hexdigest()))
        db.execute("INSERT INTO external_log(operation,subject,started_at,status,requests,details) VALUES ('fulltext',?,?,'complete',2,?)",(paper['source_id'],now(),pmcid))
    return dict(source_id=paper['source_id'],pmcid=pmcid,paragraphs=len(paragraphs),license=license_text,cached=False)


def followup(path,question_id,fetch_page=read_page):
    require_live(path)
    state=workspace(path)
    q=next((q for c in state['chemicals'] for q in c['questions'] if q['id']==question_id),None)
    if not q:
        raise ValueError('현재 스냅샷에 없는 질문입니다.')
    with connect(path) as db:
        fid=db.execute("INSERT INTO followups(question_id,query,started_at,status) VALUES (?,?,?,'running')",(question_id,q['followup_query'],now())).lastrowid
    papers=[]; seen=set(); cursor='*'; total=None; status='partial';received=0
    try:
        for _ in range(3):
            p=fetch_page(dict(query=q['followup_query'],format='json',resultType='core',pageSize=100,cursorMark=cursor))
            if type(p.get('hitCount')) is not int or p['hitCount']<0 or not isinstance(p.get('resultList',{}).get('result'),list):
                raise ValueError('비정상 보완 검색 응답입니다.')
            if total is not None and total!=p['hitCount']:
                raise ValueError('검색 중 총건수가 변했습니다.')
            total=p['hitCount'];raw=p['resultList']['result'];received+=len(raw)
            for item in raw:
                n=normalize(item)
                k=n['doi'] or n['source_id']
                if k not in seen:
                    seen.add(k);papers.append(n)
            if received>=total:
                status='complete';break
            nxt=p.get('nextCursorMark')
            if not nxt or nxt==cursor or not raw:
                raise ValueError('보완 검색 페이지가 중단됐습니다.')
            cursor=nxt
        with connect(path) as db:
            db.execute('UPDATE followups SET finished_at=?,status=?,source_total=?,fetched=?,papers=? WHERE id=?',
                       (now(),status,total,received,json.dumps(papers,ensure_ascii=False),fid))
    except Exception as e:
        with connect(path) as db:
            db.execute("UPDATE followups SET finished_at=?,status='failed',error=? WHERE id=?",(now(),str(e)[:500],fid))
        raise
    return dict(id=fid,status=status,total=total,fetched=received)


def compare(state,first,second):
    a=next((c for c in state['chemicals'] if c['id']==first),None)
    b=next((c for c in state['chemicals'] if c['id']==second),None)
    if not a or not b or a['id']==b['id']:
        raise ValueError('서로 다른 물질 두 개를 선택하세요.')
    complete=all(c['run'] and c['run']['status']=='complete' for c in (a,b))
    overlap=sorted({t['year'] for t in a['trend'] if not t['incomplete_year']} & {t['year'] for t in b['trend'] if not t['incomplete_year']})
    comparable=complete and bool(overlap)
    rows=[]
    for label in analysis.RULES['endpoint']:
        row=dict(endpoint=label,label=analysis.LABELS[label])
        for side,c in [('a',a),('b',b)]:
            records={p['record_key'] for p in c['papers'] if p['year'] in overlap}
            row[side]=sorted({e['record_key'] for e in c['evidence'] if e['label']==label and
                              e['record_key'] in records and e.get('review',{}).get('status')!='rejected'})
        rows.append(row)
    relation=[r for r in state['relations'] if {r['from_id'],r['to_id']}=={first,second} and (not overlap or r['year'] in overlap) and r.get('review',{}).get('status')!='rejected' and r.get('paper_review',{}).get('status')!='rejected']
    common_dois={p['doi'] for p in a['papers'] if p['doi']} & {p['doi'] for p in b['papers'] if p['doi']}
    return dict(a=a['id'],b=b['id'],comparable=comparable,common_years=overlap,rows=rows,relations=relation,
                shared_doi_count=len(common_dois),relation_document_count=len({r.get('doi') or r['record_key'] for r in relation}),
                notes=['공통 완결 연도의 키워드 언급을 비교',
                       '검색식·동의어·자료원 커버리지·수집 시점 차이는 여전히 존재',
                       'A의 근거를 B로 전이하거나 독성·인과관계를 예측하지 않음',
                       '0건은 현재 범위의 키워드 미확인. 생물학적 개연성·조건 차이·반대 근거 검토 필요'])


def csv_export(state):
    out=io.StringIO(newline='');writer=csv.writer(out)
    writer.writerow(['chemical_id','run_id','source_id','doi','year','title','review_status','dataset_kind'])
    for c in state['chemicals']:
        for p in c['papers']:
            values=[c['id'],c['run']['id'],p['source_id'],p['doi'],p['year'],p['title'],p.get('review',{}).get('status','unreviewed'),state['dataset_kind']]
            # Prevent spreadsheet formula execution when exporting external titles or user input.
            writer.writerow(["'"+str(v) if str(v).lstrip().startswith(('=','+','-','@')) else v for v in values])
    return '\ufeff'+out.getvalue()


def backup(path,destination):
    if not Path(path).is_file():
        raise ValueError('백업할 DB 파일이 없습니다.')
    dest=Path(destination)
    if dest.exists():
        raise ValueError('기존 백업을 덮어쓰지 않습니다. 새 경로를 지정하세요.')
    dest.parent.mkdir(parents=True,exist_ok=True)
    # Exclusive creation also closes the exists/connect race and keeps private
    # operator records unreadable to other host users from the first byte.
    fd=os.open(dest,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
    try:
        with sqlite3.connect(path) as src, sqlite3.connect(dest) as target:
            src.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise ValueError('백업 무결성 검사에 실패했습니다.')
    except Exception:
        dest.unlink(missing_ok=True);raise
    return str(dest)


def create_relation(path,data):
    """Persist a user's interpretation only when its quotation exists in cached evidence."""
    state=workspace(path)
    from_id,to_id=data.get('from_id'),data.get('to_id')
    ids={c['id'] for c in state['chemicals']}
    if from_id not in ids or to_id not in ids or from_id==to_id:
        raise ValueError('서로 다른 등록 물질 두 개가 필요합니다.')
    kind=data.get('type')
    if kind not in RELATION_TYPES or kind=='transformation_mention':
        raise ValueError('지원하지 않는 수동 관계 유형입니다.')
    actor,reason,quote=data.get('actor'),data.get('reason'),data.get('snippet')
    if not all(isinstance(v,str) and 2<=len(v)<=2000 for v in (actor,reason,quote)):
        raise ValueError('검토자·사유·실제 근거 문장을 입력하세요.')
    run_id=data.get('run_id');record=data.get('record_key');field=data.get('field')
    if type(run_id) is not int or not isinstance(field,str):
        raise ValueError('실행 ID와 근거 필드가 필요합니다.')
    with connect(path) as db:
        p=db.execute('SELECT * FROM papers WHERE run_id=? AND record_key=?',(run_id,record)).fetchone()
        if not p:
            raise ValueError('저장된 문헌을 찾지 못했습니다.')
        if field in ('title','abstract'):
            text=p[field]
        elif field.startswith('fulltext:'):
            row=db.execute('SELECT paragraphs FROM fulltexts WHERE source_id=?',(p['source_id'],)).fetchone()
            text=next((p['text'] for p in json.loads(row['paragraphs']) if p['id']==field.split(':',1)[1]),'') if row else ''
        else:
            raise ValueError('지원하지 않는 근거 필드입니다.')
        if quote not in text:
            raise ValueError('근거 문장이 저장된 해당 문헌 필드에 정확히 일치하지 않습니다.')
        eid=analysis.key('manual-relation-v1',from_id,to_id,kind,run_id,record,field,quote)
        edge=dict(id=eid,from_id=from_id,to_id=to_id,type=kind,evidence_type='user_classified_literature_statement',
                  direction='undirected' if kind in ('shared_use','co_mention') else 'user_proposed',
                  record_key=record,run_id=run_id,field=field,snippet=quote,year=p['year'],doi=p['doi'],
                  flags=['user_interpretation_unvalidated'],actor=actor,reason=reason,
                  conditions=str(data.get('conditions') or '조건 미기록')[:1000],
                  limitations=['저장 문장 일치만 확인한 사용자 분류·미검증 관계','효과·인과·안전성·다른 물질로의 전이 근거 아님'])
        db.execute('INSERT OR IGNORE INTO manual_relations VALUES (?,?,?)',(eid,json.dumps(edge,ensure_ascii=False),now()))
    return eid


def evaluation_start(path,data):
    participant,task,condition=data.get('participant'),data.get('task'),data.get('condition')
    if not all(isinstance(s,str) and 2<=len(s)<=1000 for s in (participant,task)):
        raise ValueError('참여자 구분명과 과업을 입력하세요. 개인 민감정보는 입력하지 마세요.')
    if condition not in ('manual','chatgpt','pioneer'):
        raise ValueError('평가 조건이 유효하지 않습니다.')
    with connect(path) as db:
        order=db.execute('SELECT COUNT(*) FROM evaluations WHERE participant=?',(participant,)).fetchone()[0]+1
        rid=db.execute('INSERT INTO evaluations(participant,condition,task,order_index,started_at) VALUES (?,?,?,?,?)',
                       (participant,condition,task,order,now())).lastrowid
    return rid


def evaluation_finish(path,data):
    import datetime as dt
    rid,checked,correct,score=data.get('id'),data.get('checked'),data.get('correct'),data.get('usefulness')
    notes,evaluator=data.get('notes'),data.get('evaluator')
    if type(rid) is not int or type(checked) is not int or type(correct) is not int or not 0<=correct<=checked or checked>10000:
        raise ValueError('확인 건수·정확 건수의 실제 분모와 정수를 입력하세요.')
    if type(score) is not int or not 1<=score<=5 or not all(isinstance(s,str) and 2<=len(s)<=3000 for s in (notes,evaluator)):
        raise ValueError('효용 평점 1–5, 평가자와 검증·수정 내역을 입력하세요.')
    with connect(path) as db:
        row=db.execute('SELECT * FROM evaluations WHERE id=?',(rid,)).fetchone()
        if not row or row['finished_at']:
            raise ValueError('진행 중인 평가가 없습니다. 완료된 결과는 덮어쓰지 않습니다.')
        end=now();elapsed=(dt.datetime.fromisoformat(end)-dt.datetime.fromisoformat(row['started_at'])).total_seconds()
        db.execute('UPDATE evaluations SET finished_at=?,elapsed_seconds=?,checked=?,correct=?,usefulness=?,notes=?,evaluator=? WHERE id=?',
                   (end,elapsed,checked,correct,score,notes,evaluator,rid))
    return dict(id=rid,elapsed_seconds=elapsed,accuracy=correct/checked if checked else None)


def doctor(path):
    if not Path(path).is_file():
        raise ValueError('DB 파일이 없습니다.')
    with connect(path) as db:
        integrity=db.execute('PRAGMA integrity_check').fetchone()[0]
        counts=[dict(r) for r in db.execute('''SELECT r.id,r.unique_count,COUNT(p.record_key) actual
            FROM runs r LEFT JOIN papers p ON r.id=p.run_id WHERE r.status IN ('complete','partial')
            GROUP BY r.id HAVING actual != r.unique_count''')]
        foreign_keys=[tuple(r) for r in db.execute('PRAGMA foreign_key_check')]
        version=db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
    return dict(ok=integrity=='ok' and not counts and not foreign_keys,
                integrity=integrity,snapshot_mismatches=counts,foreign_key_errors=foreign_keys,
                schema_version=version['value'] if version else None)
