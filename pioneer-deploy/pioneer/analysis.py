"""Conservative, deterministic mention extraction; never infer measured effects from tags."""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict

VERSION = 'context-rules-v2'
RULES = {
    'medium': {
        'water': r'\b(?:freshwater|surface water|river water|stormwater|water samples?)\b',
        'wastewater': r'\b(?:wastewater|sewage|effluent)\b',
        'sediment': r'\bsediments?\b', 'soil': r'\bsoils?\b',
        'air': r'\b(?:airborne|atmospheric|air samples?)\b',
        'dust': r'\b(?:indoor dust|road dust|house dust)\b',
        'blood': r'\b(?:blood|serum|plasma)\b', 'urine': r'\b(?:urine|urinary)\b',
    },
    'endpoint': {
        'mortality': r'\b(?:mortality|lethal|lethality|survival)\b',
        'endocrine': r'\b(?:endocrine|estrogen|androgen|thyroid|steroidogenesis|hormonal)\b',
        'neural': r'\b(?:neurotoxicity|neurodevelopment|neurological|dopaminergic|brain)\b',
        'reproduction': r'\b(?:reproductive|reproduction|fertility|spermatogenesis)\b',
        'liver': r'\b(?:hepatic|hepatotoxicity|liver)\b',
        'genotoxicity': r'\b(?:genotoxicity|DNA damage|mutagenic)\b',
    },
    'model': {
        'zebrafish': r'\b(?:zebrafish|Danio rerio)\b',
        'fish': r'\b(?:fish|salmon|trout|medaka)\b',
        'mammal': r'\b(?:mice|mouse|rats?|rodent)\b',
        'human': r'\b(?:humans?|human participants|population cohort)\b',
        'cell': r'\b(?:cell lines?|in vitro|H295R|HepG2)\b',
        'in_silico': r'\b(?:in silico|molecular docking|network toxicology|computational)\b',
    },
    'life_stage': {
        'embryo': r'\bembryo(?:s|nic)?\b', 'larva': r'\b(?:larvae|larval|larva)\b',
        'adult': r'\badults?\b', 'pregnancy': r'\b(?:pregnan\w*|prenatal)\b',
    },
    'purpose': {
        'occurrence': r'\b(?:detected|detection|occurrence|measured concentrations?|quantified)\b',
        'exposure': r'\b(?:exposure assessment|biomonitoring|human exposure)\b',
        'toxicity': r'\b(?:toxicity|toxicological|toxic effects?)\b',
        'analytical': r'\b(?:analytical method|mass spectrometry|chromatography|limit of detection)\b',
        'synthesis': r'\b(?:synthesis|synthetic route|catalyst)\b',
    },
}
LABELS = {
    'water':'수환경', 'wastewater':'폐수·하수', 'sediment':'퇴적물', 'soil':'토양',
    'air':'대기', 'dust':'먼지', 'blood':'혈액·혈청', 'urine':'소변',
    'mortality':'치사·생존', 'endocrine':'내분비', 'neural':'신경',
    'reproduction':'생식', 'liver':'간', 'genotoxicity':'유전독성',
    'zebrafish':'제브라피쉬', 'fish':'어류', 'mammal':'설치류', 'human':'사람',
    'cell':'세포·in vitro', 'in_silico':'계산·in silico', 'embryo':'배아',
    'larva':'유생', 'adult':'성체', 'pregnancy':'임신·태아',
    'occurrence':'검출·측정 언급', 'exposure':'노출평가', 'toxicity':'독성',
    'analytical':'분석법', 'synthesis':'합성·촉매',
}


def key(*parts):
    return hashlib.sha256('|'.join(str(p) for p in parts).encode()).hexdigest()[:24]


def sentences(text):
    for match in re.finditer(r'[\s\S]+?(?:[.!?](?=\s+[A-Z]|\s*$)|\n|$)', text):
        raw = match.group()
        offset = len(raw) - len(raw.lstrip())
        if raw.strip():
            yield raw.strip(), match.start() + offset


def extract(paper, run_id, fulltext=None):
    result = []
    fields = [('title', paper['title'], 'title'), ('abstract', paper['abstract'], 'abstract')]
    if fulltext:
        fields += [(f"fulltext:{p['id']}", p['text'], p['section']) for p in fulltext]
    for field, text, section in fields:
        for sentence, offset in sentences(text):
            flags = []
            if re.search(r'\b(?:no|not|without|absence|lack|failed to)\b', sentence, re.I):
                flags.append('negation_or_absence')
            if re.search(r'\b(?:may|might|could|potential|hypothesi\w*|suggest)\b', sentence, re.I):
                flags.append('tentative')
            if re.search(r'\b(?:review|reviewed|overview|summariz\w*)\b', sentence, re.I) or any(
                    'review' in t.lower() for t in paper['pub_types']):
                flags.append('review_or_background')
            if re.search(r'\b(?:aim|objective|propose|future|further research)\b', sentence, re.I):
                flags.append('aim_or_future')
            if field.startswith('fulltext') and re.search(r'introduction|background', section, re.I):
                flags.append('review_or_background')
            for dimension, rules in RULES.items():
                for label, pattern in rules.items():
                    match = re.search(pattern, sentence, re.I)
                    if match:
                        result.append(dict(id=key(VERSION,run_id,paper['record_key'],field,offset,dimension,label),
                            record_key=paper['record_key'], run_id=run_id, dimension=dimension, label=label,
                            display_label=LABELS[label], field=field, section=section,
                            start=offset+match.start(), end=offset+match.end(), matched=match.group(),
                            snippet=sentence, flags=sorted(set(flags)), rule_version=VERSION,
                            evidence_level='fulltext' if field.startswith('fulltext') else field,
                            interpretation='keyword_mention_only'))
    return result


def evidence_map(papers, evidence):
    by_record = defaultdict(lambda: defaultdict(set))
    for e in evidence:
        if e.get('review', {}).get('status') != 'rejected' and e.get('paper_review',{}).get('status')!='rejected':
            by_record[e['record_key']][e['dimension']].add(e['label'])
    cells=[]
    for medium in RULES['medium']:
        for endpoint in RULES['endpoint']:
            records=sorted(r for r,d in by_record.items() if medium in d['medium'] and endpoint in d['endpoint'])
            cells.append(dict(medium=medium,endpoint=endpoint,count=len(records),records=records,
                              status='mention_cooccurrence' if records else 'no_keyword_match_in_scope'))
    return cells


def questions(chemical, evidence):
    """Generate screening questions, not confirmed gaps or priority assignments."""
    if not chemical['run'] or chemical['run']['status']!='complete':
        return []
    by_label=defaultdict(set)
    for e in evidence:
        if e.get('review',{}).get('status')=='rejected' or e.get('paper_review',{}).get('status')=='rejected' or e['flags']:
            continue
        by_label[e['label']].add(e['record_key'])
    exposures=by_label['occurrence'] | by_label['exposure']
    if not exposures:
        return []
    output=[]
    for medium in RULES['medium']:
        exposed=exposures & by_label[medium]
        if not exposed:
            continue
        for endpoint in RULES['endpoint']:
            records=by_label[endpoint] & by_label[medium]
            if len(records)>3:
                continue
            output.append(dict(id=key('question-v2',chemical['run']['id'],medium,endpoint),chemical_id=chemical['id'],
                endpoint=endpoint,medium=medium,
                title=f"{LABELS[medium]} 맥락에서 {LABELS[endpoint]} 관련 근거가 질문에 충분한가?",
                observation=f'이 스냅샷에서 해당 매질과 검출/노출 키워드가 함께 언급된 문헌 {len(exposed)}건, 해당 매질과 endpoint 키워드가 함께 언급된 문헌 {len(records)}건.',
                rationale='검출·노출 맥락을 해석할 영향 자료의 조건·종·농도를 보완 확인할 질문입니다. 실제 측정·연구 필요성·생물학적 개연성은 아직 검증하지 않았습니다.',
                evidence_records=sorted(exposed)[:5],endpoint_records=sorted(records),
                followup_query='',query_hint=f'{chemical["name"]} AND {medium} AND {endpoint}',
                limitations=['공동 키워드 미검출은 연구 부재가 아님','제목·초록 위주의 범위 및 분류 오류',
                    '생물학적 개연성·반대 근거·기존 답의 누락 미검토',
                    '3건 이하는 검토함 크기를 제한하는 잠정 운영값이며 과학적 임계값이 아님'],
                status='automatic_question',criteria_version='question-v2'))
    return sorted(output,key=lambda q:(-len(q['evidence_records']),q['medium'],q['endpoint']))[:4]


def signals(c, min_count=5, min_percent=50, min_delta=3):
    if not c['run'] or c['run']['status']!='complete':
        return dict(status='insufficient_scope',scenarios=[],limitations=['완전 수집 필요'])
    counts={t['year']:t['count'] for t in c['trend'] if not t['incomplete_year']}
    scenarios=[]
    for width in (1,2):
        last=max(counts,default=0)
        old=range(last-2*width+1,last-width+1)
        recent=range(last-width+1,last+1)
        if not all(y in counts for y in [*old,*recent]):
            continue
        a,b=sum(counts[y] for y in old),sum(counts[y] for y in recent)
        pct=(b-a)/a*100 if a else None
        profiles=[]
        for label,minimum,percent,delta in [('선택값',min_count,min_percent,min_delta),('완화',3,25,2),('보수',10,100,5)]:
            profiles.append(dict(name=label,min_count=minimum,min_percent=percent,min_delta=delta,
                selected=bool(a and b>=minimum and b-a>=delta and pct>=percent)))
        scenarios.append(dict(window_years=width,baseline_years=list(old),recent_years=list(recent),
            baseline=a,recent=b,delta=b-a,percent=round(pct,1) if pct is not None else None,profiles=profiles))
    return dict(status='provisional_screening',scenarios=scenarios,
        limitations=['수치 기준은 미검증 운영 선택','분야 출판량 분모·색인 지연 미보정',
                     '소수 지정 후보 내 비교이며 전체 화학물질 순위가 아님','통계적 유의성·위해성·예측력 아님'])


# Only explicitly named chemical-like terms; no probabilistic confidence or automatic identity merge.
CANDIDATE_PATTERN=re.compile(r'\b(?:bisphenol[ -][A-Z][A-Z0-9-]*|[A-Z0-9]{1,8}(?:-[A-Z0-9]{1,10}){1,3}|(?:[0-9NnOoSs]+(?:,[0-9NnOoSs]+)*-)?[A-Za-z][A-Za-z0-9-]{3,}(?:phthalate|benzothiazole|quinone|melamine|guanidine))\b')
EXCLUDE={'LC-MS','LC-MS-MS','GC-MS','LC-HRMS','UPLC-MS','HPLC-MS','UHPLC-MS','RNA-SEQ','RNA-seq','PI3K-AKT','KEGG','GO','DNA','UV-VIS','Q-TOF','LC-QTOF','ROS','TNF','IL-6','COVID-19','SARS-COV-2','RT-PCR','QSAR','H295R'}


def discover(chemicals):
    known={t.lower() for c in chemicals for t in [c['name'],*c['terms']]}
    found={}
    for c in chemicals:
        for p in c['papers']:
            for field in ('title','abstract'):
                text=p[field]
                for match in CANDIDATE_PATTERN.finditer(text):
                    name=match.group().strip()
                    if name.lower() in known or name.upper() in EXCLUDE or len(name)<4:
                        continue
                    # Exclude digit-only date/range strings and obvious pathway/assay names.
                    if re.search(r'CREB|BDNF|ERK|PI3K|GSK-|MAPK',name,re.I):
                        continue
                    if not re.search('[a-zA-Z]',name) or re.search(r'^(?:IL-|CYP|GEO|GSE|IC-|EC-|LC-|GC-|HPLC|UHPLC|UPLC|AUC-|RNA-|Q-TOF)',name,re.I):
                        continue
                    k=key('candidate-v1',name.lower())
                    f=found.setdefault(k,dict(id=k,name=name,records=set(),examples=[]))
                    f['records'].add(p['record_key'])
                    if len(f['examples'])<3 and not any(e['record_key']==p['record_key'] for e in f['examples']):
                        f['examples'].append(dict(record_key=p['record_key'],run_id=c['run']['id'],field=field,
                            snippet=text[max(0,match.start()-120):match.end()+160],chemical_id=c['id']))
    return [dict(f,records=sorted(f['records'])) for f in sorted(found.values(),key=lambda f:(-len(f['records']),f['name']))[:30]]
