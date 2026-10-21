import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs,urlparse

from pioneer.core import init_db
from pioneer import screening


def paper(identifier,date):
    return dict(source='MED',id=str(identifier),title=f'Chemical monitoring {identifier}',abstractText='PFAS in environmental samples.',
        firstPublicationDate=date,pubYear=date[:4],pubTypeList={'pubType':['research article']})


class ScreeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'screen.sqlite';init_db(self.path)

    def tearDown(self):self.tmp.cleanup()

    def test_literature_first_counts_and_provenance(self):
        def fetch(params):
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


if __name__=='__main__':unittest.main()
