import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pioneer import radar, research
from pioneer.core import collect, connect, init_db


def record(n,date='2024-08-10',**extra):
    return {**dict(source='MED',id=str(n),title='Chemical detected in water samples',abstractText='Exposure in zebrafish caused endocrine changes.',
                firstPublicationDate=date,pubYear=date[:4] if date else '2024'),**extra}


def page(records):return dict(hitCount=len(records),resultList={'result':records})


class RadarTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=str(Path(self.temp.name)/'test.sqlite');init_db(self.path,seed_examples=True)
        self.params={'start':'2024-01-01','end':'2024-12-31'}

    def tearDown(self):self.temp.cleanup()

    def collect(self,items,chemical='6ppd-q',start=2024,end=2024):
        return collect(self.path,chemical,start,end,fetch_page=lambda _:page(items))

    def result(self,**params):return radar.build(self.path,{**self.params,**params})

    def row(self,**params):return next(r for r in self.result(**params)['rows'] if r['chemical_id']=='6ppd-q')

    def test_equal_days_leap_year_and_odd_period(self):
        a=radar.windows('2024-01-01','2024-12-31');self.assertEqual(a['days_per_window'],183);self.assertEqual(a['recent_start'],'2024-07-02')
        b=radar.windows('2025-01-01','2025-12-31');self.assertEqual(b['baseline_end'],'2025-07-01');self.assertEqual(b['recent_start'],'2025-07-03');self.assertEqual(b['middle_days_omitted'],1)

    def test_boundaries_and_signal(self):
        self.collect([record(i,'2024-01-01') for i in range(3)]+[record(i,'2024-12-31') for i in range(3,9)])
        r=self.row();self.assertEqual((r['baseline'],r['recent'],r['delta'],r['percent']),(3,6,3,100));self.assertEqual(r['status'],'growing')
        self.assertTrue(r['unstable']);self.assertEqual(sum(b['count'] or 0 for b in r['buckets']),9)

    def test_zero_baseline_and_low_volume(self):
        self.collect([record(1)]);r=self.row();self.assertEqual(r['status'],'early');self.assertIsNone(r['percent'])
        self.collect([record(2,'2024-01-01'),record(3),record(4)])
        self.assertEqual(self.row()['status'],'low_volume')

    def test_partial_and_unknown_dates_withhold_comparison(self):
        rid=self.collect([record(1)])
        with connect(self.path) as db:db.execute("UPDATE runs SET status='partial' WHERE id=?",(rid,))
        self.assertFalse(self.row()['comparable']);self.assertIsNone(self.row()['delta'])
        self.assertEqual(self.row()['new_contexts'],[])
        self.collect([record(2,'')]);r=self.row();self.assertEqual(r['unknown_dates'],1);self.assertEqual(r['status'],'insufficient')
        self.assertTrue(all(b['count'] is None for b in r['buckets']))

    def test_missing_period_not_zero(self):
        self.collect([record(1)])
        r=self.row(start='2023-01-01');self.assertEqual(r['status'],'insufficient');self.assertIsNone(r['buckets'][0]['count'])

    def test_older_wide_snapshot_beats_new_narrow(self):
        wide=self.collect([record(1)],start=2023,end=2025);self.collect([record(2)],start=2024,end=2024)
        self.assertEqual(self.row(start='2023-01-01',end='2025-12-31')['run']['id'],wide)

    def test_changed_aliases_cannot_reuse_different_query(self):
        self.collect([record(1)])
        with connect(self.path) as db:db.execute('UPDATE chemicals SET terms=? WHERE id=?',(json.dumps(['changed query']),'6ppd-q'))
        self.assertIsNone(self.row()['run'])

    def test_context_and_document_filter_same_population(self):
        self.collect([record(1),record(2,pubTypeList={'pubType':['Review']}),record(3,title='Synthesis in a polymer',abstractText='Novel materials synthesis')])
        self.assertEqual(self.row()['total'],2);self.assertEqual(self.row(exclude_reviews=False)['total'],3)
        self.assertEqual(self.row(medium='water')['total'],1);self.assertEqual(self.row(field='materials')['total'],1)
        self.assertEqual(self.row(model='zebrafish')['records'],['MED:1'])

    def test_review_rejection_excludes_exact_run_paper(self):
        rid=self.collect([record(1)])
        research.save_review(self.path,{'target_type':'paper','target_id':f'{rid}:MED:1','status':'rejected','actor':'TEST ONLY','reason':'Unrelated chemical'})
        self.assertEqual(self.row()['total'],0)

    def test_sort_places_insufficient_last(self):
        self.collect([record(1)])
        rows=self.result(sort='percent')['rows'];self.assertTrue(rows[0]['comparable']);self.assertTrue(all(not r['comparable'] for r in rows[1:]))

    def test_options_reject_bad_conditions(self):
        for extra in ({'start':'2025-02-29'},{'start':'2025-01-01'},{'min_delta':True},{'min_delta':1.5},{'medium':'fake'},{'sort':'script'}):
            with self.subTest(extra=extra),self.assertRaises(ValueError):self.result(**extra)

    def test_immutable_report_and_changed_result_guard(self):
        self.collect([record(1)]);r=self.result();s=radar.save_report(self.path,{**self.params,'expected_report_id':r['report_id']})
        self.assertEqual(s['report_id'],r['report_id']);self.collect([record(2),record(3)])
        with self.assertRaises(ValueError):radar.save_report(self.path,{**self.params,'expected_report_id':r['report_id']})
        self.assertEqual(radar.saved_report(self.path,r['report_id'])['rows'],s['rows'])

    def test_selection_requires_saved_report_and_exports_reason(self):
        self.collect([record(1)]);r=radar.save_report(self.path,self.params)
        d=dict(report_id=r['report_id'],chemical_id='6ppd-q',status='selected',actor='TEST ONLY',reason='Compare water evidence')
        radar.report_decision(self.path,d);report=radar.saved_report(self.path,r['report_id'])
        self.assertIn('Compare water evidence',radar.report_csv(report));self.assertEqual(len(report['decisions']),1)
        with self.assertRaises(ValueError):radar.report_decision(self.path,{**d,'chemical_id':'not_in_report'})
        exported=list(csv.reader(io.StringIO(radar.report_csv(report).lstrip('\ufeff'))));self.assertIn('criteria',exported[0]);self.assertIn('dataset_kind',exported[0]);self.assertIn('validation_copy',exported[0])

    def test_paged_articles_match_filters_and_saved_snapshot(self):
        self.collect([record(i) for i in range(25)])
        saved=radar.save_report(self.path,self.params)
        self.collect([record(99)])
        a=radar.articles(self.path,dict(report_id=saved['report_id'],chemical_id='6ppd-q',offset=20))
        self.assertEqual(a['total'],25);self.assertEqual(len(a['articles']),5)
        with self.assertRaises(ValueError):radar.articles(self.path,dict(chemical_id='6ppd-q',offset=-1))
        with self.assertRaises(ValueError):radar.articles(self.path,{**self.params,'chemical_id':'6ppd-q','expected_report_id':saved['report_id']})

    def test_csv_formula_escape(self):
        self.collect([record(1)])
        with connect(self.path) as db:db.execute("UPDATE chemicals SET name='=DANGEROUS()' WHERE id='6ppd-q'")
        self.assertIn("'=DANGEROUS()",radar.report_csv(self.result()))

    def test_lookup_unique_cid_reuses_without_human_review(self):
        identity=dict(status='unique_match',response={'properties':[{'CID':123}]},checked_at='2026-01-01',source='fixture')
        with patch('pioneer.radar.lookup_identity',return_value=identity):
            a=radar.track(self.path,'example chemical');b=radar.track(self.path,'another alias')
        self.assertEqual(a['chemical_id'],b['chemical_id']);self.assertEqual(b['status'],'existing')
        with connect(self.path) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM decisions').fetchone()[0],0)

    def test_ambiguous_lookup_does_not_add(self):
        with patch('pioneer.radar.lookup_identity',return_value={'status':'ambiguous','response':{}}):
            self.assertIsNone(radar.track(self.path,'mixture example')['chemical_id'])
        with self.assertRaises(ValueError):radar.track(self.path,'x" OR *')

    def test_corpus_cap_and_source_log(self):
        def response(_):return dict(hitCount=500,resultList={'result':[record(1,title='Measured 1,3-diphenylguanidine in water')]},nextCursorMark='next')
        result=radar.scan_corpus(self.path,{**self.params,'max_pages':1},fetch_page=response)
        self.assertEqual(result['status'],'partial');self.assertEqual(result['fetched'],1)
        data=radar.corpus_view(self.path);self.assertEqual(data['run']['source_total'],500);self.assertTrue(data['candidates'])
        self.assertIn(radar.CORPUS_TERMS,data['run']['query'])

    def test_corpus_total_change_and_stuck_cursor_fail(self):
        pages=iter([dict(hitCount=5,resultList={'result':[record(1)]},nextCursorMark='a'),dict(hitCount=6,resultList={'result':[record(2)]},nextCursorMark='b')])
        with self.assertRaises(ValueError):radar.scan_corpus(self.path,self.params,fetch_page=lambda _:next(pages))
        self.assertEqual(radar.corpus_view(self.path)['run']['status'],'failed')
        with self.assertRaises(ValueError):radar.scan_corpus(self.path,self.params,fetch_page=lambda _:dict(hitCount=5,resultList={'result':[record(1)]},nextCursorMark='*'))

    def test_refresh_reuses_covers_and_isolates_failure(self):
        self.collect([record(1)])
        job=radar.start_refresh(self.path,dict(chemical_ids=['6ppd-q','bps'],start_year=2024,end_year=2024,max_pages=1),launch=False)
        with patch('pioneer.radar.collect',side_effect=ValueError('Fixture network failure')):
            radar.refresh_worker(self.path,job['id'],radar.jobs(self.path)[0]['request'])
        j=radar.jobs(self.path)[0];self.assertEqual(j['status'],'completed_with_issues');self.assertEqual(j['progress'][0]['status'],'reused');self.assertEqual(j['progress'][1]['status'],'failed')

    def test_refresh_concurrent_job_and_bad_ids_rejected(self):
        data=dict(chemical_ids=['6ppd-q'],start_year=2024,end_year=2024,max_pages=1)
        radar.start_refresh(self.path,data,launch=False)
        with self.assertRaises(ValueError):radar.start_refresh(self.path,data,launch=False)
        with self.assertRaises(ValueError):radar.start_refresh(self.path,{**data,'chemical_ids':['x']},launch=False)
        with self.assertRaises(ValueError):radar.start_refresh(self.path,{**data,'chemical_ids':['6ppd-q','6ppd-q']},launch=False)
