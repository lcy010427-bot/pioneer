import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from pioneer import analysis, research
from pioneer.core import collect, connect, init_db, normalize, seed_demo, view


def item(sid='1',title='Example',abstract='No endocrine effect was detected in water samples.',**extra):
    return dict(source='MED',id=sid,title=title,pubYear='2024',abstractText=abstract,**extra)


def response(records):
    return dict(hitCount=len(records),resultList={'result':records})


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=str(Path(self.temp.name)/'live.sqlite');init_db(self.path,seed_examples=True)
        self.records=[item('1',abstract='Detected in water samples. No endocrine effect was observed. Zebrafish larvae were exposed.'),
                      item('2',abstract='We quantified 1,3-diphenylguanidine in road dust.')]
        collect(self.path,'6ppd-q',2023,2025,fetch_page=lambda _:response(self.records))

    def tearDown(self):
        self.temp.cleanup()

    def state(self):
        return research.workspace(self.path)

    def test_migration_idempotent_preserves_papers(self):
        research.init_research(self.path);init_db(self.path,seed_examples=True)
        self.assertEqual(len(view(self.path)['chemicals'][0]['papers']),2)

    def test_extraction_exact_offsets_and_no_probability(self):
        p=normalize(self.records[0]);p['pub_types']=[]
        ev=analysis.extract(p,1)
        for e in ev:
            self.assertEqual(p[e['field']][e['start']:e['end']],e['matched'])
            self.assertNotIn('confidence',e)
            self.assertEqual(e['interpretation'],'keyword_mention_only')
        endocrine=next(e for e in ev if e['label']=='endocrine')
        self.assertIn('negation_or_absence',endocrine['flags'])
        self.assertTrue(any(e['label']=='larva' for e in ev))

    def test_review_paper_does_not_become_primary_experimental_evidence(self):
        p=normalize(item(abstract='Endocrine effects may occur in zebrafish.',pubTypeList={'pubType':['Review']}));p['pub_types']=['Review']
        for e in analysis.extract(p,1):
            self.assertIn('review_or_background',e['flags'])
            self.assertIn('tentative',e['flags'])

    def test_matrix_document_counts_not_number_of_mentions(self):
        p=normalize(item(abstract='Water samples showed endocrine changes. Water samples also had endocrine changes.'));p['pub_types']=[]
        ev=analysis.extract(p,1);cells=analysis.evidence_map([p],ev)
        cell=next(c for c in cells if c['medium']=='water' and c['endpoint']=='endocrine')
        self.assertEqual(cell['count'],1)
        for e in ev:e['review']={'status':'rejected'}
        cells=analysis.evidence_map([p],ev)
        self.assertTrue(all(c['count']==0 for c in cells))

    def test_append_only_review_and_rejected_paper_excluded_from_map(self):
        state=self.state();p=state['chemicals'][0]['papers'][0]
        data=dict(target_type='paper',target_id=f"1:{p['record_key']}",actor='실제 입력자',reason='확인 필요',status='uncertain')
        research.save_review(self.path,data)
        research.save_review(self.path,dict(data,status='rejected',reason='대상 물질 아님'))
        s=self.state();self.assertEqual(len(s['review_history']),2)
        self.assertEqual(s['chemicals'][0]['papers'][0]['review']['status'],'rejected')
        self.assertEqual(len(s['chemicals'][0]['papers']),2) # raw snapshot remains intact
        cell=next(c for c in s['chemicals'][0]['map'] if c['medium']=='water' and c['endpoint']=='endocrine')
        self.assertEqual(cell['count'],0)

    def test_cannot_create_review_without_actor_reason_or_valid_target(self):
        for change in [dict(actor=''),dict(reason=''),dict(target_id='invalid')]:
            data=dict(target_type='paper',target_id='1:MED:1',actor='검토자',reason='검토 사유',status='accepted');data.update(change)
            with self.assertRaises(ValueError):research.save_review(self.path,data)

    def test_followup_complete_requires_actual_complete_search(self):
        s=self.state();q=s['chemicals'][0]['questions'][0]
        data=dict(target_type='question',target_id=q['id'],actor='검토자',reason='검색 범위 확인',status='followup_complete')
        with self.assertRaises(ValueError):research.save_review(self.path,data)
        result=research.followup(self.path,q['id'],fetch_page=lambda _:response([]))
        self.assertEqual(result['status'],'complete')
        research.save_review(self.path,data)
        self.assertEqual(len(self.state()['review_history']),1)

    def test_questions_do_not_claim_absence_or_priority(self):
        q=self.state()['chemicals'][0]['questions'][0]
        self.assertEqual(q['status'],'automatic_question')
        self.assertNotIn('priority',q)
        self.assertIn('공동 키워드 미검출은 연구 부재가 아님',q['limitations'])
        self.assertIn('TITLE_ABS:',q['followup_query'])
        self.assertNotIn(r'\b',q['followup_query'])
        self.assertFalse(q['followups'])

    def test_signal_sensitivity_zero_baseline_and_partial(self):
        c=view(self.path)['chemicals'][0]
        c['trend']=[dict(year=y,count=n,incomplete_year=False) for y,n in zip(range(2021,2026),[0,0,0,0,2])]
        sig=analysis.signals(c)
        self.assertIsNone(sig['scenarios'][0]['percent'])
        self.assertFalse(any(p['selected'] for p in sig['scenarios'][0]['profiles']))
        c['run']['status']='partial'
        self.assertEqual(analysis.signals(c)['status'],'insufficient_scope')
        self.assertEqual(analysis.questions(c,[]),[])

    def test_identity_cache_and_ambiguous_no_promotion(self):
        calls=[]
        def reader(url):
            calls.append(url);return {'PropertyTable':{'Properties':[{'CID':1},{'CID':2}]}}
        a=research.lookup_identity(self.path,'chemical:6ppd-q','6PPD-quinone',reader)
        b=research.lookup_identity(self.path,'chemical:6ppd-q','6PPD-quinone',reader)
        self.assertEqual(a['status'],'ambiguous');self.assertEqual(len(calls),1)

    def test_candidate_discovery_idempotent_from_existing_data(self):
        research.sync_candidates(self.path);research.sync_candidates(self.path)
        s=self.state()
        self.assertTrue(s['candidates'])
        names=[c['name'] for c in s['candidates']]
        self.assertEqual(len(names),len(set(names)))
        self.assertEqual(len(s['review_history']),0)
        self.assertTrue(all(c['evidence']['examples'] for c in s['candidates']))

    def test_promotion_needs_identity_and_is_idempotent(self):
        research.sync_candidates(self.path);c=self.state()['candidates'][0]
        with self.assertRaises(ValueError):research.promote(self.path,c['id'],'검토자','식별 확인')
        research.lookup_identity(self.path,'candidate:'+c['id'],c['name'],lambda _: {'PropertyTable':{'Properties':[{'CID':999,'InChIKey':'test'}]}})
        cid=research.promote(self.path,c['id'],'검토자','식별 확인')
        self.assertEqual(research.promote(self.path,c['id'],'검토자','식별 확인'),cid)
        new=next(x for x in view(self.path)['chemicals'] if x['id']==cid)
        self.assertIsNone(new['run'])

    def test_duplicate_cid_not_promoted_as_new_chemical(self):
        research.sync_candidates(self.path);c=self.state()['candidates'][0]
        reader=lambda _: {'PropertyTable':{'Properties':[{'CID':999}]}}
        research.lookup_identity(self.path,'chemical:6ppd-q','6PPD-quinone',reader)
        research.lookup_identity(self.path,'candidate:'+c['id'],c['name'],reader)
        with self.assertRaises(ValueError):research.promote(self.path,c['id'],'검토자','동일 CID')

    def test_fulltext_license_and_doi_match_and_cache(self):
        xml=b'<article><front><article-meta><article-id pub-id-type="pmcid">PMC1</article-id><article-id pub-id-type="doi">10.1234/x</article-id><permissions><license>Creative Commons Attribution 4.0</license></permissions></article-meta></front><body><sec><title>Results</title><p id="p1">Zebrafish larvae in water samples.</p></sec></body></article>'
        meta=lambda _:response([dict(self.records[0],pmcid='PMC1',isOpenAccess='Y')])
        r=research.fetch_fulltext(self.path,'6ppd-q','MED:1',request=meta,read_xml=lambda _:xml)
        self.assertEqual(r['paragraphs'],1)
        cached=research.fetch_fulltext(self.path,'6ppd-q','MED:1',request=lambda _:self.fail('cache ignored'))
        self.assertTrue(cached['cached'])
        c=self.state()['chemicals'][0]
        ev=[e for e in c['evidence'] if e['evidence_level']=='fulltext']
        self.assertTrue(ev);self.assertEqual(ev[0]['section'],'Results')

    def test_fulltext_denied_without_open_access_or_license(self):
        meta=lambda _:response([dict(self.records[0],pmcid='PMC1',isOpenAccess='N')])
        with self.assertRaises(ValueError):research.fetch_fulltext(self.path,'6ppd-q','MED:1',request=meta)
        meta=lambda _:response([dict(self.records[0],pmcid='PMC1',isOpenAccess='Y')])
        with self.assertRaises(ValueError):research.fetch_fulltext(self.path,'6ppd-q','MED:1',request=meta,read_xml=lambda _:b'<article><front><article-meta><article-id pub-id-type="pmcid">PMC1</article-id></article-meta></front><body><p>water samples</p></body></article>')

    def test_fulltext_doi_mismatch_is_not_connected(self):
        collect(self.path,'bps',2023,2025,fetch_page=lambda _:response([item('10',doi='10.1234/a')]))
        meta=lambda _:response([item('10',pmcid='PMC10',isOpenAccess='Y')])
        xml=b'<article><front><article-meta><article-id pub-id-type="pmcid">PMC10</article-id><article-id pub-id-type="doi">10.1234/b</article-id></article-meta></front></article>'
        with self.assertRaises(ValueError):research.fetch_fulltext(self.path,'bps','MED:10',request=meta,read_xml=lambda _:xml)

    def test_comparison_only_common_complete_years(self):
        collect(self.path,'bps',2024,2024,fetch_page=lambda _:response([item('3')]))
        result=research.compare(self.state(),'6ppd-q','bps')
        self.assertTrue(result['comparable']);self.assertEqual(result['common_years'],[2024])
        result=research.compare(self.state(),'6ppd-q','hfpo-da')
        self.assertFalse(result['comparable'])

    def test_same_paper_relation_not_counted_twice(self):
        paper=item('8',abstract='6PPD is oxidized to 6PPD-quinone.',doi='10.1234/r')
        for cid in ('6ppd-q','6ppd'):
            collect(self.path,cid,2023,2025,fetch_page=lambda _:response([paper]))
        edges=self.state()['relations']
        self.assertEqual(len(edges),1)
        self.assertEqual(edges[0]['type'],'transformation_mention')
        self.assertEqual(edges[0]['direction'],'parent_to_product_proposed')

    def test_common_mention_without_relation_not_an_edge(self):
        paper=item('8',abstract='6PPD and 6PPD-quinone were measured in water.')
        collect(self.path,'6ppd-q',2023,2025,fetch_page=lambda _:response([paper]))
        self.assertEqual(self.state()['relations'],[])

    def test_csv_formula_injection_protected(self):
        collect(self.path,'bps',2023,2025,fetch_page=lambda _:response([item('9',title='=HYPERLINK("bad")')]))
        output=research.csv_export(self.state())
        self.assertIn("'=HYPERLINK",output)
        self.assertTrue(output.startswith('\ufeff'))

    def test_backup_preserves_integrity_without_overwrite(self):
        destination=str(Path(self.temp.name)/'backup.sqlite')
        research.backup(self.path,destination)
        self.assertEqual(len(view(destination)['chemicals'][0]['papers']),2)
        with self.assertRaises(ValueError):research.backup(self.path,destination)

    def test_demo_blocks_external_operations(self):
        demo=str(Path(self.temp.name)/'demo.sqlite');seed_demo(demo)
        with self.assertRaises(ValueError):research.lookup_identity(demo,'test','name')
        with self.assertRaises(ValueError):research.fetch_fulltext(demo,'6ppd-q','DEMO:1')
        with self.assertRaises(ValueError):research.followup(demo,'unknown')

    def test_decimal_sentence_context_and_locant_preserved(self):
        p=normalize(item(abstract='At 0.05 mg/L no endocrine changes were detected in water samples.'))
        p['pub_types']=[]
        e=next(e for e in analysis.extract(p,1) if e['label']=='endocrine')
        self.assertIn('0.05',e['snippet'])
        self.assertIn('negation_or_absence',e['flags'])
        research.sync_candidates(self.path)
        self.assertIn('1,3-diphenylguanidine',[c['name'] for c in self.state()['candidates']])

    def test_manual_relation_requires_exact_stored_quote_and_is_proposal(self):
        data=dict(from_id='6ppd-q',to_id='bps',type='hypothesis',run_id=1,record_key='MED:1',field='abstract',
                  snippet='Invented quotation.',actor='검토자',reason='가설 검토')
        with self.assertRaises(ValueError):research.create_relation(self.path,data)
        data['snippet']='No endocrine effect was observed.'
        rid=research.create_relation(self.path,data)
        self.assertEqual(research.create_relation(self.path,data),rid)
        edge=next(r for r in self.state()['relations'] if r['id']==rid)
        self.assertEqual(edge['evidence_type'],'user_classified_literature_statement')
        self.assertIn('user_interpretation_unvalidated',edge['flags'])
        self.assertEqual(edge['conditions'],'조건 미기록')

    def test_task_evaluation_real_denominator_and_no_overwrite(self):
        rid=research.evaluation_start(self.path,dict(participant='검사 참여자',condition='pioneer',task='동일 과업 비교'))
        base=dict(id=rid,checked=2,correct=3,usefulness=3,evaluator='검사자',notes='자동 검사·효용 평가 아님')
        with self.assertRaises(ValueError):research.evaluation_finish(self.path,base)
        base.update(checked=0,correct=0)
        result=research.evaluation_finish(self.path,base)
        self.assertIsNone(result['accuracy'])
        with self.assertRaises(ValueError):research.evaluation_finish(self.path,base)
        rid2=research.evaluation_start(self.path,dict(participant='검사 참여자',condition='manual',task='동일 과업 비교'))
        row=next(e for e in self.state()['evaluations'] if e['id']==rid2)
        self.assertEqual(row['order_index'],2)

    def test_doctor_and_missing_backup_source(self):
        self.assertTrue(research.doctor(self.path)['ok'])
        with self.assertRaises(ValueError):research.backup(str(Path(self.temp.name)/'absent.sqlite'),str(Path(self.temp.name)/'new.sqlite'))
        with connect(self.path) as db:db.execute('UPDATE runs SET unique_count=99 WHERE id=1')
        self.assertFalse(research.doctor(self.path)['ok'])

    def test_failed_identity_refresh_preserves_prior_mapping(self):
        research.lookup_identity(self.path,'chemical:6ppd-q','known-name',lambda _:{'PropertyTable':{'Properties':[{'CID':1}]}})
        def fail(url):raise OSError('service unavailable')
        result=research.lookup_identity(self.path,'chemical:6ppd-q','new-name',fail)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(self.state()['chemicals'][0]['identity']['name'],'known-name')

    def test_fulltext_pmcid_mismatch_rejected(self):
        meta=lambda _:response([dict(self.records[0],pmcid='PMC1',isOpenAccess='Y')])
        xml=b'<article><front><article-meta><article-id pub-id-type="pmcid">PMC2</article-id></article-meta></front></article>'
        with self.assertRaisesRegex(ValueError,'PMCID'):
            research.fetch_fulltext(self.path,'6ppd-q','MED:1',request=meta,read_xml=lambda _:xml)


if __name__=='__main__':unittest.main()
