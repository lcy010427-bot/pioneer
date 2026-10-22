"""Journal-scoped discovery: journals are fixed, chemical candidates are not."""
import re

JOURNALS = [
    ('0091-6765', 'Environmental Health Perspectives'),
    ('0013-936X', 'Environmental Science & Technology'),
    ('2328-8930', 'Environmental Science & Technology Letters'),
    ('0160-4120', 'Environment International'),
    ('0304-3894', 'Journal of Hazardous Materials'),
    ('0043-1354', 'Water Research'),
    ('0269-7491', 'Environmental Pollution'),
    ('0048-9697', 'Science of the Total Environment'),
    ('0045-6535', 'Chemosphere'),
    ('0003-2700', 'Analytical Chemistry'),
]
QUERY = '(' + ' OR '.join(f'ISSN:{issn}' for issn, _ in JOURNALS) + ')'

# Chemical noun-phrase grammar, rather than an allowed substance catalogue.
HEAD = r'(?:monomers?|oligomers?|polymers?|plasticizers?|phthalates?|bisphenols?|parabens?|siloxanes?|retardants?|pesticides?|insecticides?|herbicides?|fungicides?|antibiotics?|benzotriazoles?|benzothiazoles?|quinones?|surfactants?|substances?|nanoparticles?|microplastics?|nanoplastics?)'
GROUP = re.compile(r'\b(?:[A-Za-z][A-Za-z0-9-]*[ -]){0,5}' + HEAD + r'\b', re.I)
TRIM = re.compile(r'^.*\b(?:of|to|by|from|with|for|and|in|on|the|these|those|among|between|using|measured|detected|exposure|novel|emerging)\s+', re.I)
BAD = {'substance','substances','monomer','monomers','polymer','polymers','nanoparticles','pesticides','antibiotics'}


def phrases(text):
    """Yield open chemical/group phrases and abbreviations with source offsets."""
    for m in GROUP.finditer(text):
        name=TRIM.sub('',m.group()).strip()
        if len(name)<4 or name.lower() in BAD:continue
        start=m.end()-len(name)
        # Prefer a defined abbreviation, e.g. liquid crystal monomers (LCMs).
        acronym=re.match(r'\s*\(([A-Z][A-Za-z0-9-]{1,12})\)',text[m.end():])
        yield name,start,m.end(),acronym.group(1) if acronym else None


def mentions(papers):
    out=[]
    definitions={}
    for p in papers:
        for field in ('title','abstract'):
            for name,_,_,acronym in phrases(p.get(field,'')):
                if acronym:definitions.setdefault(acronym,set()).add(name.lower())
    unique={abbr:next(iter(names)) for abbr,names in definitions.items() if len(names)==1}
    acronym_pattern=re.compile(r'(?<!\w)(?:'+'|'.join(re.escape(a) for a in unique)+r')(?!\w)') if unique else None
    for p in papers:
        aliases={}
        for field in ('title','abstract'):
            text=p.get(field,'')
            for name,start,end,acronym in phrases(text):
                # Canonicalize singular/plural for groups only.
                canonical=name.lower()
                if canonical.endswith(' monomer'):canonical+='s'
                aliases[canonical]=canonical
                if acronym:aliases[acronym]=canonical
            if acronym_pattern:
                for m in acronym_pattern.finditer(text):aliases[m.group()]=unique[m.group()]
        for alias,canonical in aliases.items():
            for field in ('title','abstract'):
                text=p.get(field,'')
                for m in re.finditer(r'(?<!\w)'+re.escape(alias)+r'(?!\w)',text,re.I):
                    out.append(dict(identifier='',name=canonical,exact=m.group(),record_key=p['record_key'],
                        date=p['date'],title=p['title'],doi=p['doi'],source_id=p['source_id'],journal=p.get('journal',''),
                        section=field,snippet=text[max(0,m.start()-150):m.end()+250],candidate_kind='물질군·명칭 후보'))
    return out
