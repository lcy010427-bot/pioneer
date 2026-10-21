import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from pioneer.__main__ import handler_for
from pioneer.core import seed_demo


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = str(Path(cls.temp.name)/'demo.sqlite')
        seed_demo(path)
        cls.server = ThreadingHTTPServer(('127.0.0.1',0), handler_for(path,0))
        port = cls.server.server_port
        cls.server.RequestHandlerClass = handler_for(path,port)
        cls.url = f'http://127.0.0.1:{port}'
        cls.thread = threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join()
        cls.server.server_close()
        cls.temp.cleanup()

    def request(self, path, data=None, headers=None):
        req=urllib.request.Request(self.url+path,
            data=json.dumps(data).encode() if data is not None else None,
            headers=headers or {})
        try:
            return urllib.request.urlopen(req,timeout=5)
        except urllib.error.HTTPError as exc:
            return exc

    def test_export_and_state_are_consistent(self):
        with self.request('/api/state') as response:
            data=json.load(response)
        with self.request('/api/export') as response:
            exported=json.load(response)
        self.assertEqual([c['run'] for c in data['chemicals']],[c['run'] for c in exported['chemicals']])
        self.assertEqual([[p['record_key'] for p in c['papers']] for c in data['chemicals']],
                         [[p['record_key'] for p in c['papers']] for c in exported['chemicals']])
        self.assertIn('methods',exported)
        self.assertIn('review_history',exported)
        self.assertEqual(data['dataset_kind'],'demo')

    def test_demo_cannot_trigger_external_collection(self):
        with self.request('/api/collect', {'chemical_id':'6ppd-q','start_year':2021,'end_year':2026},
                          {'Content-Type':'application/json'}) as r:
            self.assertEqual(r.code,400)
            self.assertIn('데모',json.load(r)['error'])

    def test_cross_origin_collection_is_rejected(self):
        with self.request('/api/collect', {}, {'Content-Type':'application/json',
                                             'Origin':'https://evil.example'}) as r:
            self.assertEqual(r.code,403)

    def test_dns_rebinding_host_rejected(self):
        with self.request('/api/state', headers={'Host':'evil.example'}) as r:
            self.assertEqual(r.code,403)

    def test_path_traversal_and_unknown_routes_rejected(self):
        with self.request('/../pioneer/core.py') as r:
            self.assertEqual(r.code,404)

    def test_content_type_and_body_validation(self):
        with self.request('/api/collect', {}) as r:
            self.assertEqual(r.code,415)
        with self.request('/api/collect', [], {'Content-Type':'application/json'}) as r:
            self.assertEqual(r.code,400)

    def test_serves_app_with_security_headers(self):
        with self.request('/') as r:
            self.assertEqual(r.code,200)
            self.assertIn('PIONEER',r.read().decode())
            self.assertIn("frame-ancestors 'none'",r.headers['Content-Security-Policy'])
            self.assertEqual(r.headers['Cache-Control'],'no-store')

    def test_radar_routes_keep_demo_and_scope_labels(self):
        with self.request('/api/radar?start=2024-01-01&end=2024-12-31') as r:
            report=json.load(r)
        self.assertEqual(report['dataset_kind'],'demo')
        self.assertEqual(report['windows']['days_per_window'],183)
        self.assertTrue(all(row['status']=='insufficient' for row in report['rows']))
        with self.request('/radar.js') as r:self.assertEqual(r.code,200)

    def test_radar_invalid_queries_return_json_errors(self):
        for path in ('/api/radar?min_delta=1.5','/api/radar?start=bad',
                     '/api/radar?report_id=missing','/api/radar/articles?chemical_id=missing'):
            with self.subTest(path=path),self.request(path) as r:
                self.assertEqual(r.code,400);self.assertIn('error',json.load(r))

    def test_radar_snapshot_export_is_roundtrippable(self):
        params={'start':'2024-01-01','end':'2024-12-31'}
        with self.request('/api/radar/report',params,{'Content-Type':'application/json'}) as r:
            self.assertEqual(r.code,200);report=json.load(r)
        with self.request('/api/radar?report_id='+report['report_id']) as r:
            self.assertEqual(json.load(r)['rows'],report['rows'])
        with self.request('/api/radar.csv?report_id='+report['report_id']) as r:
            self.assertIn('report_id',r.read().decode('utf-8'))

    def test_radar_external_actions_disabled_in_demo(self):
        for path in ('/api/radar/lookup','/api/radar/corpus','/api/radar/refresh'):
            with self.subTest(path=path),self.request(path,{}, {'Content-Type':'application/json'}) as r:
                self.assertEqual(r.code,400)


if __name__=='__main__':
    unittest.main()
