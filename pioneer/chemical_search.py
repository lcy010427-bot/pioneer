"""Bounded, read-only literature search for browser-local analysis subjects."""
import datetime as dt
import json
import re
import threading
import time
from . import analysis
from .core import normalize, now, read_page

_cache={}
_lock=threading.Lock()
_slots=threading.BoundedSemaphore(2)


def search(name,start,end,fetch_page=None):
    if not isinstance(name,str) or not 2<=len(name.strip())<=160 or re.search(r'[\x00-\x1f"\\]',name):
        raise ValueError('물질명은 2–160자로 입력하세요. 따옴표와 검색 연산식은 넣지 마세요.')
    name=name.strip()
    try:a,b=dt.date.fromisoformat(start),dt.date.fromisoformat(end)
    except (ValueError,TypeError):raise ValueError('올바른 시작일·종료일이 필요합니다.')
    if a>b or a.year<1900 or (b-a).days>366*5:raise ValueError('조회 기간은 1900년 이후 최대 5년입니다.')
    key=(name.casefold(),start,end)
    with _lock:
        cached=_cache.get(key)
        if cached and time.monotonic()-cached[0]<900:return cached[1]
    if not _slots.acquire(blocking=False):raise ValueError('다른 검색이 진행 중입니다. 잠시 후 다시 검색하세요.')
    try:
        query=f'TITLE_ABS:"{name}" AND FIRST_PDATE:[{start} TO {end}]'
        page=(fetch_page or read_page)(dict(query=query,format='json',resultType='core',pageSize=100,cursorMark='*'))
        total=page.get('hitCount');raw=page.get('resultList',{}).get('result')
        if type(total) is not int or total<0 or not isinstance(raw,list):raise ValueError('문헌 검색 응답이 유효하지 않습니다.')
        papers=[];seen=set()
        for item in raw:
            p=normalize(item);pid=p['doi'] or p['record_key']
            if pid in seen:continue
            seen.add(pid);p['pub_types']=json.loads(p['pub_types']);p.update(review={},document_flags=[],fulltext_available=False,fulltext_license=None)
            papers.append(p)
        cid='manual:'+analysis.key(name.casefold())
        rid=-int(analysis.key(name.casefold(),start,end)[:10],16)-100000
        stamp=now()
        run=dict(id=rid,query=query,source='Europe PMC · 직접 물질 검색',start_year=a.year,end_year=b.year,
            started_at=stamp,finished_at=stamp,status='complete' if len(raw)>=total else 'partial',
            source_total=total,fetched=len(raw),unique_count=len(papers),query_version='manual-search-v1',error=None)
        c=dict(id=cid,name=name,terms=[name],run=run,latest_attempt=run,papers=papers,comparison=None,identity=None,
            derived_from_screening=True,manual_search=True,trend=[dict(year=y,count=sum(p['year']==y for p in papers),incomplete_year=y==dt.date.today().year) for y in range(a.year,b.year+1)])
        evidence=[e for p in papers for e in analysis.extract(p,rid)]
        for e in evidence:e.update(review={},paper_review={})
        c.update(evidence=evidence,map=analysis.evidence_map(papers,evidence),signals=analysis.signals(c),questions=analysis.questions(c,evidence))
        for q in c['questions']:q.update(review={},followups=[],followup_query='')
        result=dict(profile=c,total=total,returned=len(papers),warning='입력 명칭의 제목·초록 검색 결과입니다. 동의어·구조 식별은 자동 확정하지 않습니다.')
        with _lock:
            if len(_cache)>=64:_cache.pop(next(iter(_cache)))
            _cache[key]=(time.monotonic(),result)
        return result
    finally:_slots.release()
