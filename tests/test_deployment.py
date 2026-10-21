import hashlib
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from pioneer import deployment, research
from pioneer.core import connect, init_db, seed_demo
from pioneer.__main__ import handler_for


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'live.sqlite';init_db(self.path)

    def tearDown(self):self.temp.cleanup()

    def test_origin_validation(self):
        self.assertEqual(deployment.validate_origin('https://Pioneer.example.com/'),'https://pioneer.example.com')
        for value in ('http://example.com','https://example.com/path','https://user:password@example.com','https://example.com?x=1','https://*.example.com','https://example.com:99999','https://example.com\nX:y'):
            with self.subTest(value=value),self.assertRaises(ValueError):deployment.validate_origin(value)

    def test_public_projection_preserves_scientific_notes_but_not_operator_notes(self):
        source=dict(review_history=[{'actor':'private'}],evaluations=[{'participant':'private'}],external_log=[{'details':'private'}],report_count=5,
            nested={'review':{'status':'rejected','actor':'private','reason':'private'},'notes':'private'},notes=['scientific limitation'])
        clean=deployment.public_data(source)
        self.assertNotIn('private',json.dumps(clean));self.assertEqual(clean['notes'],['scientific limitation'])
        self.assertEqual(clean['nested']['review']['status'],'rejected');self.assertEqual(clean['report_count'],0)
        self.assertIn('actor',source['nested']['review'])

    def test_writer_lock_rejects_second_instance(self):
        with deployment.writer_lock(self.path):
            with self.assertRaises(ValueError):
                with deployment.writer_lock(self.path):pass

    def test_production_rejects_demo_and_validation_database(self):
        demo=Path(self.temp.name)/'demo.sqlite';seed_demo(demo)
        with self.assertRaises(ValueError):deployment.prepare_database(demo)
        with connect(self.path) as db:db.execute("INSERT INTO metadata VALUES ('validation_copy','true')")
        with self.assertRaises(ValueError):deployment.prepare_database(self.path)

    def test_fresh_production_database_has_no_synthetic_papers(self):
        path=Path(self.temp.name)/'new.sqlite';deployment.prepare_database(path)
        with connect(path) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM papers').fetchone()[0],0)

    def test_restart_recovers_stale_jobs_without_losing_complete_runs(self):
        from pioneer.core import collect
        collect(self.path,'6ppd-q',2024,2024,fetch_page=lambda _:dict(hitCount=0,resultList={'result':[]}))
        with connect(self.path) as db:
            db.execute("INSERT INTO refresh_jobs(created_at,status,request,progress) VALUES ('now','running','{}','[]')")
            db.execute("INSERT INTO corpus_runs(query,start_date,end_date,started_at,status) VALUES ('q','2024-01-01','2024-12-31','now','running')")
        deployment.prepare_database(self.path)
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT status FROM refresh_jobs').fetchone()[0],'interrupted')
            self.assertEqual(db.execute('SELECT status FROM corpus_runs').fetchone()[0],'failed')
            self.assertEqual(db.execute('SELECT status FROM runs').fetchone()[0],'complete')

    def test_readonly_startup_does_not_modify_db(self):
        before=hashlib.sha256(self.path.read_bytes()).digest();deployment.prepare_database(self.path,True)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).digest(),before)
        with self.assertRaises(ValueError):deployment.prepare_database(Path(self.temp.name)/'absent.sqlite',True)

    def test_migration_backs_up_before_changing_schema(self):
        with connect(self.path) as db:db.execute("UPDATE metadata SET value='3' WHERE key='schema_version'")
        deployment.prepare_database(self.path)
        backups=list((self.path.parent/'backups').glob('*.sqlite'));self.assertEqual(len(backups),1)
        with connect(backups[0]) as db:self.assertEqual(db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0],'3')
        self.assertEqual(research.doctor(self.path)['schema_version'],'4')

    def test_restore_is_checked_and_never_overwrites_existing_data(self):
        dest=Path(self.temp.name)/'restored.sqlite';deployment.restore_database(self.path,dest)
        self.assertTrue(research.doctor(dest)['ok'])
        before=dest.read_bytes()
        with self.assertRaises(ValueError):deployment.restore_database(self.path,dest)
        self.assertEqual(dest.read_bytes(),before)
        self.assertFalse(dest.with_name(dest.name+'.restore-tmp').exists())

    def test_stream_import_preserves_live_data_and_refuses_overwrite(self):
        dest=Path(self.temp.name)/'imported.sqlite'
        deployment.import_database(io.BytesIO(self.path.read_bytes()),dest)
        self.assertTrue(research.doctor(dest)['ok']);self.assertEqual(dest.stat().st_mode & 0o777,0o600)
        with self.assertRaises(ValueError):deployment.import_database(io.BytesIO(self.path.read_bytes()),dest)

    def test_backups_are_private_and_existing_files_are_preserved(self):
        dest=Path(self.temp.name)/'backup.sqlite';research.backup(self.path,dest)
        self.assertEqual(dest.stat().st_mode & 0o777,0o600)
        before=dest.read_bytes()
        with self.assertRaises(ValueError):research.backup(self.path,dest)
        self.assertEqual(dest.read_bytes(),before)


class PublicHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.path=Path(cls.temp.name)/'live.sqlite';init_db(cls.path)
        with connect(cls.path) as db:
            db.execute("INSERT INTO decisions(target_type,target_id,status,actor,reason,created_at,rule_version) VALUES ('paper','test','uncertain','PRIVATE ACTOR','PRIVATE NOTE','now','test')")
        cls.server=deployment.BoundedHTTPServer(('127.0.0.1',0),handler_for(cls.path,0))
        cls.port=cls.server.server_port
        cls.server.RequestHandlerClass=handler_for(cls.path,cls.port,'https://pioneer.example.com',True,'https://admin.pioneer.example.com')
        cls.url=f'http://127.0.0.1:{cls.port}';cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.thread.join();cls.server.server_close();cls.temp.cleanup()

    def request(self,path,data=None,headers=None):
        req=urllib.request.Request(self.url+path,data=json.dumps(data).encode() if data is not None else None,headers=headers or {})
        try:return urllib.request.urlopen(req,timeout=10)
        except urllib.error.HTTPError as exc:return exc

    def test_public_host_health_and_configuration(self):
        with self.request('/healthz',headers={'Host':'pioneer.example.com'}) as r:self.assertEqual(r.code,200);self.assertEqual(json.load(r),{'status':'ok'})
        with self.request('/api/config') as r:self.assertEqual(json.load(r),{'read_only':True,'admin_url':'https://admin.pioneer.example.com'})

    def test_public_database_is_unchanged_by_every_write_route(self):
        before=self.path.read_bytes()
        for path in ('/api/collect','/api/radar/report','/api/radar/decision','/api/radar/lookup','/api/radar/refresh','/api/radar/corpus','/api/review','/api/fulltext','/api/evaluation/start'):
            with self.subTest(path=path),self.request(path,{}, {'Content-Type':'application/json','Origin':'https://pioneer.example.com'}) as r:self.assertEqual(r.code,403)
        self.assertEqual(self.path.read_bytes(),before)

    def test_public_full_export_and_private_reports_are_blocked(self):
        for path in ('/api/export','/api/export.csv','/api/history','/api/radar/reports','/api/radar/jobs','/api/radar?report_id=anything','/api/radar.csv?report_id=anything','/api/radar/articles?report_id=anything'):
            with self.subTest(path=path),self.request(path) as r:self.assertEqual(r.code,403)

    def test_public_research_redacts_actual_review_identity_and_reason(self):
        with self.request('/api/research') as r:
            text=r.read().decode();self.assertNotIn('PRIVATE ACTOR',text);self.assertNotIn('PRIVATE NOTE',text)
            self.assertEqual(json.loads(text)['review_history'],[])

    def test_public_current_csv_and_comparison_notes_remain_available(self):
        with self.request('/api/radar.csv') as r:self.assertEqual(r.code,200);self.assertIn('chemical',r.read().decode())
        with self.request('/api/compare?a=6ppd-q&b=bps') as r:self.assertIsInstance(json.load(r)['notes'],list)

    def test_forwarded_headers_cannot_override_host_guard(self):
        with self.request('/api/state',headers={'Host':'evil.example','X-Forwarded-Host':'pioneer.example.com'}) as r:self.assertEqual(r.code,403)

    def test_connection_limit_returns_503_then_recovers(self):
        server=deployment.BoundedHTTPServer(('127.0.0.1',0),handler_for(self.path,0),max_requests=1)
        server.RequestHandlerClass=handler_for(self.path,server.server_port)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            self.assertTrue(server.slots.acquire(blocking=False))
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/healthz',timeout=5)
            self.assertEqual(caught.exception.code,503)
            server.slots.release()
            with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/healthz') as r:self.assertEqual(r.code,200)
        finally:server.shutdown();thread.join();server.server_close()
