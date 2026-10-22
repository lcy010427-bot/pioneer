import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs,urlparse

from pioneer.core import init_db
from pioneer import screening, journals


def paper(identifier,date):
    return dict(source='MED',id=str(identifier),title=f'Chemical monitoring {identifier}',abstractText='PFAS in environmental samples.',
        firstPublicationDate=date,pubYear=date[:4],pubTypeList={'pubType':['research article']})


class ScreeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'screen.sqlite';init_db(self.path)

    def tearDown(self):self.tmp.cleanup()

    def test_literature_first_counts_and_provenance(self):
        def fetch(params):
            if 'ISSN:0091-6765' not in params['query']:return dict(hitCount=0,resultList=dict(result=[]))
            recent='TO 2025-12-31' in params['query']
            rows=[paper(2,'2025-08-01'),paper(3,'2025-11-01')] if recent else [paper(1,'2025-02-01')]
            return {'hitCount':len(rows),'resultList':{'result':rows}}
        def annotations(url):
            ids=parse_qs(urlparse(url).query)['articleIds'][0].split(',')
            return [{'source':'MED','extId':value.split(':')[1],'annotations':[{'section':'Abstract (x)','exact':'PFAS',
                'prefix':'measured ','postfix':' in water','tags':[{'name':'per- and polyfluoroalkyl substances',
                'uri':'http://purl.obolibrary.org/obo/CHEBI_00001'}]}]} for value in ids]
        screening.run(self.path,'2025-01-01','2025-12-31',1,fetch,annotations)
        data=screening.view(self.path,{'start':'2025-01-01','end':'2025-12-31','unit':'month'})
        row=next(r for r in data['rows'] if r['identifier']=='http://purl.obolibrary.org/obo/CHEBI_00001')
        self.assertEqual((row['baseline'],row['recent'],row['delta']),(1,2,1))
        self.assertEqual(data['run']['baseline_fetched'],1)
        self.assertNotIn('documents',data['run'])
        self.assertTrue(row['evidence'][0]['source_id'].startswith('MED:'))
        saved=screening.view(self.path,{'start':'2025-01-01','end':'2025-12-31','unit':'month',
            'search':'no matching name','ids':row['id']})
        self.assertEqual(saved['rows'][0]['id'],row['id'])
        profiles=screening.profiles(self.path,{'ids':row['id']})
        self.assertEqual(profiles[0]['id'],'screen:'+row['id'])
        self.assertTrue(profiles[0]['derived_from_screening'])

    def test_multi_signal_score_separates_detection_hazard_and_gap(self):
        documents=[dict(record_key='MED:1',title='First detection in river water',
            abstract='Measured concentrations show developmental toxicity; limited chronic data and regulatory attention.')]
        result=screening._multi_signals(documents,set(),{'MED:1'},0,3,3,None)
        self.assertIn('environmental_detection',result['signal_types'])
        self.assertIn('hazard_signal',result['signal_types'])
        self.assertTrue(result['gap_gate'])
        self.assertGreater(result['components']['E'],0)
        self.assertGreater(result['components']['H'],0)
        self.assertGreater(result['components']['G'],0)

    def test_invalid_period_is_rejected(self):
        with self.assertRaises(ValueError):screening.view(self.path,{'start':'2025-01-01','end':'2031-01-01'})

    def test_new_live_database_has_no_default_chemicals(self):
        from pioneer.core import view
        self.assertEqual(view(self.path)['chemicals'],[])

    def test_journal_scope_does_not_require_emerging_keyword(self):
        self.assertIn('ISSN:0091-6765',screening.QUERY)
        self.assertNotIn('TITLE_ABS',screening.QUERY)
        self.assertEqual(len(journals.JOURNALS),10)

    def test_unlisted_group_and_acronym_get_real_evidence_and_profile(self):
        # No catalogue entry or annotation exists for this name.
        title='Widespread Human Exposure to Liquid Crystal Monomers'
        abstract='Liquid crystal monomers (LCMs) are released from electronics. We measured 49 LCMs in urine.'
        def fetch(params):
            if 'ISSN:0091-6765' not in params['query']:return dict(hitCount=0,resultList=dict(result=[]))
            rows=[] if 'TO 2025-07-01' in params['query'] else [dict(paper(90,'2025-10-01'),title=title,
                abstractText=abstract,journalTitle='Environmental Health Perspectives',doi='10.1234/example')]
            return dict(hitCount=len(rows),resultList=dict(result=rows))
        screening.run(self.path,'2025-01-01','2025-12-31',1,fetch,lambda _:[])
        params=dict(start='2025-01-01',end='2025-12-31',unit='month',preview='1')
        data=screening.view(self.path,params)
        row=next(r for r in data['rows'] if r['name']=='liquid crystal monomers')
        self.assertEqual((row['baseline'],row['recent']),(0,1))
        self.assertIn('new_mention',row['signal_types'])
        self.assertIn('exposure_signal',row['signal_types'])
        detail=screening.candidate(self.path,{**params,'id':row['id']})
        self.assertIn('snippet',detail['evidence'][0])
        self.assertEqual(detail['evidence'][0]['journal'],'Environmental Health Perspectives')
        self.assertEqual(detail['evidence'][0]['doi'],'10.1234/example')
        profile=screening.profiles(self.path,dict(ids=row['id']))[0]
        self.assertEqual(len(profile['papers']),1)

    def test_new_chemical_family_is_not_limited_to_example_lcm(self):
        text='First detection of novel fluorinated oligomers (NFOs) in river water. NFOs exhibit toxicity.'
        p=dict(record_key='MED:1',source_id='MED:1',doi='',date='2025-10-01',title=text,abstract='')
        mentions=journals.mentions([p])
        self.assertTrue(any(m['name']=='fluorinated oligomers' for m in mentions))
        self.assertTrue(any(m['exact']=='NFOs' for m in mentions))

    def test_abbreviation_only_followup_uses_unambiguous_definition(self):
        base=dict(source_id='MED:1',doi='',date='2025-10-01',abstract='')
        papers=[dict(base,record_key='MED:1',title='Liquid crystal monomers (LCMs) in urine'),
                dict(base,record_key='MED:2',title='LCMs in river water')]
        self.assertTrue(any(m['record_key']=='MED:2' and m['name']=='liquid crystal monomers'
                            for m in journals.mentions(papers)))

    def test_each_selected_journal_gets_its_own_collection_cap(self):
        calls=[]
        def fetch(params):
            calls.append(params)
            return dict(hitCount=0,resultList=dict(result=[]))
        from datetime import date
        screening._collect(date(2025,1,1),date(2025,6,30),5,fetch)
        self.assertEqual(len(calls),10)
        self.assertTrue(all(p['pageSize']==50 for p in calls))
        self.assertEqual(len({p['query'].split()[0] for p in calls}),10)

    def test_candidate_missing_is_reported(self):
        with self.assertRaises(ValueError):
            screening.candidate(self.path,dict(id='missing',start='2025-01-01',end='2025-12-31'))


if __name__=='__main__':unittest.main()
