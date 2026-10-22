import datetime as dt
import tempfile
import unittest
from pathlib import Path

from pioneer.core import collect, fetch, init_db, normalize, query_for, seed_demo, view


def record(sid, year=2024, **extra):
    return dict(source='MED', id=str(sid), title=f'Paper {sid}', pubYear=str(year), **extra)


def page(records, total=None, next_cursor=None):
    result = dict(hitCount=len(records) if total is None else total,
                  resultList={'result': records})
    if next_cursor:
        result['nextCursorMark'] = next_cursor
    return result


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name)/'live.sqlite')
        init_db(self.path,seed_examples=True)
        self.year = dt.date.today().year

    def tearDown(self):
        self.temp.cleanup()

    def save(self, records):
        return collect(self.path, '6ppd-q', self.year-5, self.year,
                       fetch_page=lambda params: page(records))

    def chemical(self):
        return view(self.path)['chemicals'][0]

    def test_query_literal_aliases_and_date_bounds(self):
        q = query_for(['6PPD-quinone', '6PPD quinone'], 2021, self.year)
        self.assertIn('TITLE_ABS:"6PPD quinone"', q)
        self.assertIn(f'FIRST_PDATE:[2021-01-01 TO {dt.datetime.now(dt.timezone.utc).date().isoformat()}]', q)
        with self.assertRaises(ValueError):
            query_for(['x'], self.year, 2020)

    def test_normalize_uses_first_publication_date(self):
        p = normalize(record(1, 2025, firstPublicationDate='2024-12-31',
                             abstractText='<p>A &amp; B</p>', doi='https://doi.org/10.1234/TEST'))
        self.assertEqual(p['year'], 2024)
        self.assertEqual(p['doi'], '10.1234/test')
        self.assertEqual(p['abstract'], 'A & B')

    def test_missing_year_remains_unknown(self):
        p = normalize({'source':'MED','id':'1'})
        self.assertIsNone(p['year'])
        self.assertEqual(p['date_basis'], 'unknown')

    def test_duplicate_doi_and_source_ids(self):
        records = [record(1, doi='10.1234/a'), record(2, doi='10.1234/A'), record(2), record(3)]
        result = fetch(['x'], 2020, self.year, fetch_page=lambda _:page(records))
        self.assertEqual((result[2], len(result[3]), result[4]), (4,2,'complete'))

    def test_pagination_counts_and_cursor(self):
        seen=[]
        def reader(p):
            seen.append(p['cursorMark'])
            return page([record(1)],2,'next') if p['cursorMark']=='*' else page([record(2)],2)
        result=fetch(['x'],2020,self.year,fetch_page=reader)
        self.assertEqual(seen,['*','next'])
        self.assertEqual(len(result[3]),2)
        self.assertEqual(result[4],'complete')

    def test_page_cap_marks_partial(self):
        result=fetch(['x'],2020,self.year,max_pages=1,fetch_page=lambda _:page([record(1)],2,'next'))
        self.assertEqual(result[4],'partial')

    def test_stalled_cursor_is_error(self):
        with self.assertRaises(ValueError):
            fetch(['x'],2020,self.year,fetch_page=lambda _:page([record(1)],2,'*'))

    def test_changing_totals_are_error(self):
        count=iter([page([record(1)],2,'next'),page([record(2)],3,'last')])
        with self.assertRaises(ValueError):
            fetch(['x'],2020,self.year,fetch_page=lambda _:next(count))

    def test_invalid_response_is_not_zero_results(self):
        with self.assertRaises(ValueError):
            fetch(['x'],2020,self.year,fetch_page=lambda _:{'error':'quota'})

    def test_repeat_collection_does_not_inflate_snapshot(self):
        self.save([record(1),record(2)])
        self.save([record(1),record(2)])
        self.assertEqual(len(self.chemical()['papers']),2)
        self.assertEqual(self.chemical()['run']['id'],2)

    def test_failed_refresh_preserves_success(self):
        self.save([record(1)])
        def fail(_):
            raise OSError('unreachable')
        with self.assertRaises(OSError):
            collect(self.path,'6ppd-q',self.year-5,self.year,fetch_page=fail)
        c=self.chemical()
        self.assertEqual(c['run']['id'],1)
        self.assertEqual(c['latest_attempt']['status'],'failed')
        self.assertEqual(len(c['papers']),1)

    def test_partial_does_not_replace_complete(self):
        self.save([record(1)])
        collect(self.path,'6ppd-q',self.year-5,self.year,max_pages=1,
                fetch_page=lambda _:page([record(2)],10,'next'))
        c=self.chemical()
        self.assertEqual(c['run']['status'],'complete')
        self.assertEqual(c['latest_attempt']['status'],'partial')
        self.assertEqual(c['papers'][0]['source_id'],'MED:1')

    def test_partial_initial_snapshot_has_no_comparison(self):
        collect(self.path,'6ppd-q',self.year-5,self.year,max_pages=1,
                fetch_page=lambda _:page([record(2)],10,'next'))
        self.assertEqual(self.chemical()['run']['status'],'partial')
        self.assertIsNone(self.chemical()['comparison'])

    def test_current_year_excluded_from_comparison_and_chart_counts_link(self):
        self.save([record(1,self.year-2),record(2,self.year-1),record(3,self.year-1),
                   record(4,self.year),record(5,self.year)])
        c=self.chemical()
        self.assertEqual(c['comparison']['delta'],1)
        self.assertEqual(c['comparison']['percent'],100)
        self.assertEqual(c['trend'][-1]['incomplete_year'],True)
        for bar in c['trend']:
            self.assertEqual(bar['count'],len([p for p in c['papers'] if p['year']==bar['year']]))

    def test_zero_denominator_is_na(self):
        self.save([record(1,self.year-1)])
        self.assertIsNone(self.chemical()['comparison']['percent'])
        self.assertEqual(self.chemical()['comparison']['delta'],1)

    def test_complete_zero_is_distinct_from_uncollected(self):
        self.save([])
        state=view(self.path)
        self.assertEqual(state['chemicals'][0]['run']['source_total'],0)
        self.assertIsNone(state['chemicals'][1]['run'])

    def test_demo_is_separate_idempotent_and_has_no_citations(self):
        demo=str(Path(self.temp.name)/'demo.sqlite')
        seed_demo(demo)
        seed_demo(demo)
        data=view(demo)
        self.assertEqual(data['dataset_kind'],'demo')
        self.assertEqual(len(data['chemicals'][0]['papers']),21)
        for c in data['chemicals']:
            for p in c['papers']:
                self.assertFalse(p['doi'])
                self.assertFalse(p['pmid'])
        with self.assertRaises(ValueError):
            seed_demo(self.path)
        with self.assertRaises(ValueError):
            collect(demo,'6ppd-q',2020,self.year)


if __name__=='__main__':
    unittest.main()
