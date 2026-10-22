import unittest
from pioneer import chemical_search


class ChemicalSearchTests(unittest.TestCase):
    def setUp(self):chemical_search._cache.clear()

    def test_literal_search_creates_analyzable_profile_without_database_writes(self):
        queries=[]
        def fetch(params):
            queries.append(params)
            return dict(hitCount=250,resultList=dict(result=[dict(source='MED',id='123',title='Caffeine in water',
                abstractText='Caffeine exposure caused developmental toxicity.',firstPublicationDate='2025-08-01',
                pubYear='2025',doi='10.1234/a')]))
        r=chemical_search.search('caffeine','2025-01-01','2025-12-31',fetch)
        c=r['profile']
        self.assertTrue(c['manual_search']);self.assertEqual(c['run']['status'],'partial')
        self.assertEqual(r['total'],250);self.assertEqual(r['returned'],1)
        self.assertIn('TITLE_ABS:"caffeine"',queries[0]['query'])
        self.assertNotIn('ISSN',queries[0]['query'])
        self.assertTrue(c['evidence']);self.assertEqual(c['papers'][0]['doi'],'10.1234/a')
        self.assertEqual(chemical_search.search('caffeine','2025-01-01','2025-12-31',lambda _:self.fail('cache miss')),r)

    def test_no_results_are_not_fabricated(self):
        r=chemical_search.search('nonexistent fixture','2025-01-01','2025-12-31',lambda _:dict(hitCount=0,resultList=dict(result=[])))
        self.assertEqual(r['returned'],0);self.assertEqual(r['profile']['papers'],[])

    def test_invalid_input_and_bad_source_response(self):
        for name,start,end in [('x','2025-01-01','2025-12-31'),('x" OR *','2025-01-01','2025-12-31'),
                               ('caffeine','bad','2025-12-31'),('caffeine','2025-12-31','2025-01-01')]:
            with self.assertRaises(ValueError):chemical_search.search(name,start,end,lambda _:self.fail('unexpected network'))
        with self.assertRaises(ValueError):chemical_search.search('caffeine','2025-01-01','2025-12-31',lambda _:dict(error='quota'))
