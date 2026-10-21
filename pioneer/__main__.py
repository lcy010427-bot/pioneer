from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .core import collect, connect, init_db, seed_demo, view
from . import research, radar, screening

WEB = Path(__file__).resolve().parent.parent / 'web'


def handler_for(db_path, port, public_origin=None, read_only=False, admin_origin=None):
    lock = threading.Lock()
    hosts={f'127.0.0.1:{port}',f'localhost:{port}'}
    origins={f'http://127.0.0.1:{port}',f'http://localhost:{port}'}
    if public_origin:
        from .deployment import validate_origin
        public_origin=validate_origin(public_origin)
        hosts.add(urlparse(public_origin).netloc.lower())
        origins.add(public_origin)

    class Handler(BaseHTTPRequestHandler):
        server_version='PIONEER'
        sys_version=''

        def setup(self):
            super().setup()
            self.connection.settimeout(20)

        def log_request(self, code='-', size='-'):
            logging.getLogger('pioneer.http').info(json.dumps(dict(method=self.command,path=urlparse(self.path).path,status=code)))

        def send(self, status, data, mime='application/json; charset=utf-8'):
            if read_only and mime.startswith('application/json'):
                from .deployment import public_data
                data=public_data(data)
            body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(body)

        def host_ok(self):
            return self.headers.get('Host','').lower() in hosts

        def do_GET(self):
            if not self.host_ok():
                return self.send(403, {'error': '허용되지 않은 Host입니다.'})
            path = urlparse(self.path).path
            try:
                if path=='/healthz':
                    # A bounded readiness check; never creates a missing database.
                    with sqlite3.connect(Path(db_path).resolve().as_uri()+'?mode=ro',uri=True,timeout=2) as db:
                        db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
                    return self.send(200,{'status':'ok'})
                if path=='/api/config':return self.send(200,{'read_only':read_only,'admin_url':admin_origin if read_only else None})
                params=parse_qs(urlparse(self.path).query)
                if read_only and (path in ('/api/export','/api/export.csv','/api/history','/api/radar/reports','/api/radar/jobs') or params.get('report_id')):
                    return self.send(403,{'error':'운영 기록은 운영자 사이트에서만 조회할 수 있습니다.'})
                if path in ('/api/radar','/api/radar.csv'):
                    flat={k:v[0] for k,v in params.items()}
                    if flat.get('report_id'):
                        report=radar.saved_report(db_path,flat['report_id'])
                    else:report=radar.build(db_path,flat)
                    if path.endswith('.csv'):
                        return self.send(200,radar.report_csv(report).encode('utf-8'),'text/csv; charset=utf-8')
                    return self.send(200,report)
                if path=='/api/screen/meta':return self.send(200,screening.meta(db_path))
                if path=='/api/screen':return self.send(200,screening.view(db_path,{k:v[0] for k,v in params.items()}))
                if path=='/api/radar/reports':return self.send(200,radar.saved_reports(db_path))
                if path=='/api/radar/articles':return self.send(200,radar.articles(db_path,{k:v[0] for k,v in params.items()}))
                if path=='/api/radar/jobs':return self.send(200,radar.jobs(db_path))
                if path=='/api/radar/corpus':return self.send(200,radar.corpus_view(db_path))
                if path == '/api/state':
                    return self.send(200, view(db_path))
                if path in ('/api/research','/api/export','/api/export.csv','/api/compare'):
                    settings={}
                    for name in ('min_count','min_percent','min_delta'):
                        if name in params:
                            value=int(params[name][0])
                            if not 0<=value<=10000:
                                raise ValueError('기준값 범위는 0–10000입니다.')
                            settings[name]=value
                    state=research.workspace(db_path,settings)
                    if path=='/api/compare':
                        return self.send(200,research.compare(state,params.get('a',[''])[0],params.get('b',[''])[0]))
                    if path=='/api/export.csv':
                        return self.send(200,research.csv_export(state).encode('utf-8'),'text/csv; charset=utf-8')
                    return self.send(200,state)
                if path=='/api/history':
                    with connect(db_path) as db:
                        rows=[dict(r) for r in db.execute('SELECT * FROM runs ORDER BY id DESC LIMIT 100')]
                    return self.send(200,rows)
            except ValueError as exc:
                return self.send(400,{'error':str(exc)})
            except Exception:
                logging.getLogger('pioneer.http').exception('Read request failed')
                return self.send(503,{'error':'자료를 일시적으로 조회할 수 없습니다. 잠시 후 다시 시도하세요.'})
            files = {'/': ('index.html', 'text/html; charset=utf-8'),
                     '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                     '/timeline.js': ('timeline.js', 'text/javascript; charset=utf-8'),
                     '/research.js': ('research.js', 'text/javascript; charset=utf-8'),
                     '/radar.js': ('radar.js', 'text/javascript; charset=utf-8'),
                     '/screen.js': ('screen.js', 'text/javascript; charset=utf-8'),
                     '/style.css': ('style.css', 'text/css; charset=utf-8'),
                     '/favicon.svg': ('favicon.svg', 'image/svg+xml')}
            if path in files:
                file, mime = files[path]
                return self.send(200, (WEB / file).read_bytes(), mime)
            return self.send(404, {'error': '찾을 수 없습니다.'})

        def do_POST(self):
            origin = self.headers.get('Origin')
            if (not self.host_ok() or
                (origin and origin not in origins) or
                self.headers.get('Sec-Fetch-Site') == 'cross-site'):
                return self.send(403, {'error': '동일 출처 요청만 허용합니다.'})
            if read_only:return self.send(403,{'error':'조회 전용 사이트입니다. 갱신·기록은 운영자 사이트에서 진행하세요.'})
            routes={'/api/collect','/api/discover','/api/identify','/api/review','/api/promote','/api/fulltext','/api/followup','/api/relation','/api/evaluation/start','/api/evaluation/finish','/api/radar/report','/api/radar/decision','/api/radar/lookup','/api/radar/refresh','/api/radar/corpus','/api/screen'}
            if self.path not in routes:
                return self.send(404, {'error':'찾을 수 없습니다.'})
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':
                return self.send(415,{'error':'JSON 요청이 필요합니다.'})
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=8192:
                    raise ValueError('유효하지 않은 요청 크기입니다.')
                data=json.loads(self.rfile.read(length))
                if not isinstance(data,dict):
                    raise ValueError('JSON 객체가 필요합니다.')
            except (ValueError,TypeError) as exc:
                return self.send(400,{'error':str(exc)})
            if not lock.acquire(blocking=False):
                return self.send(409,{'error':'다른 작업이 진행 중입니다. 완료 후 다시 시도하세요.'})
            try:
                if self.path=='/api/screen':
                    result=screening.start(db_path,data)
                elif self.path=='/api/radar/report':
                    result=radar.save_report(db_path,data)
                elif self.path=='/api/radar/decision':
                    result={'decision_id':radar.report_decision(db_path,data)}
                elif self.path=='/api/radar/lookup':
                    result=radar.track(db_path,data.get('name'))
                elif self.path=='/api/radar/refresh':
                    result=radar.start_refresh(db_path,data)
                elif self.path=='/api/radar/corpus':
                    result=radar.scan_corpus(db_path,data)
                elif self.path=='/api/collect':
                    start,end=data.get('start_year'),data.get('end_year')
                    if type(start) is not int or type(end) is not int:
                        raise ValueError('시작·종료 연도는 정수여야 합니다.')
                    result={'run_id':collect(db_path,data.get('chemical_id'),start,end)}
                    research.sync_candidates(db_path)
                elif self.path=='/api/discover':
                    result={'candidate_count':research.sync_candidates(db_path)}
                elif self.path=='/api/identify':
                    subject=data.get('subject','')
                    if not isinstance(subject,str) or ':' not in subject:
                        raise ValueError('식별 대상이 유효하지 않습니다.')
                    kind,target=subject.split(':',1)
                    with connect(db_path) as db:
                        if kind=='chemical':
                            row=db.execute('SELECT name FROM chemicals WHERE id=?',(target,)).fetchone()
                        elif kind=='candidate':
                            row=db.execute('SELECT name FROM candidates WHERE id=?',(target,)).fetchone()
                        else:
                            row=None
                    if not row:
                        raise ValueError('식별 대상이 없습니다.')
                    result=research.lookup_identity(db_path,subject,row['name'])
                elif self.path=='/api/review':
                    result={'review_id':research.save_review(db_path,data)}
                elif self.path=='/api/promote':
                    result={'chemical_id':research.promote(db_path,data.get('candidate_id'),data.get('actor'),data.get('reason'))}
                elif self.path=='/api/fulltext':
                    result=research.fetch_fulltext(db_path,data.get('chemical_id'),data.get('record_key'))
                elif self.path=='/api/relation':
                    result={'relation_id':research.create_relation(db_path,data)}
                elif self.path=='/api/evaluation/start':
                    result={'evaluation_id':research.evaluation_start(db_path,data)}
                elif self.path=='/api/evaluation/finish':
                    result=research.evaluation_finish(db_path,data)
                else:
                    result=research.followup(db_path,data.get('question_id'))
                self.send(200,result)
            except (ValueError,TypeError) as exc:
                self.send(400,{'error':str(exc)})
            except Exception:
                self.send(502,{'error':'외부 자료원 요청 또는 저장에 실패했습니다. 기존 스냅샷을 유지합니다. 로그와 네트워크 설정을 확인하세요.'})
            finally:
                lock.release()

    return Handler


def main():
    parser = argparse.ArgumentParser(description='PIONEER 문헌 모니터')
    parser.add_argument('--db', default=os.environ.get('PIONEER_DB','.data/pioneer.sqlite'))
    subs = parser.add_subparsers(dest='command', required=True)
    subs.add_parser('init')
    subs.add_parser('seed-demo')
    server = subs.add_parser('serve')
    server.add_argument('--port', type=int, default=8765)
    server.add_argument('--host',default='127.0.0.1')
    server.add_argument('--public-origin')
    server.add_argument('--read-only',action='store_true')
    restore=subs.add_parser('restore');restore.add_argument('source')
    collector = subs.add_parser('collect')
    collector.add_argument('chemical_id', type=str)
    collector.add_argument('--start', type=int, required=True)
    collector.add_argument('--end', type=int, required=True)
    collector.add_argument('--max-pages', type=int, default=10)
    screener=subs.add_parser('screen')
    screener.add_argument('--start',required=True)
    screener.add_argument('--end',required=True)
    screener.add_argument('--max-pages',type=int,default=10)
    subs.add_parser('export')
    subs.add_parser('analyze')
    subs.add_parser('discover')
    identity=subs.add_parser('identify');identity.add_argument('chemical_id')
    fulltext=subs.add_parser('fulltext');fulltext.add_argument('chemical_id');fulltext.add_argument('record_key')
    back=subs.add_parser('backup');back.add_argument('destination')
    fixture=subs.add_parser('prepare-check');fixture.add_argument('destination')
    subs.add_parser('doctor')
    args = parser.parse_args()
    if args.command == 'init':
        init_db(args.db)
    elif args.command == 'seed-demo':
        seed_demo(args.db)
    elif args.command == 'collect':
        init_db(args.db)
        rid = collect(args.db, args.chemical_id, args.start, args.end, args.max_pages)
        print(json.dumps({'run_id': rid}))
    elif args.command == 'screen':
        init_db(args.db)
        rid=screening.run(args.db,args.start,args.end,args.max_pages)
        print(json.dumps({'screening_run_id':rid},ensure_ascii=False))
    elif args.command in ('export','analyze'):
        research.init_research(args.db)
        print(json.dumps(research.workspace(args.db),ensure_ascii=False,indent=2))
    elif args.command=='discover':
        print(json.dumps({'candidates':research.sync_candidates(args.db)}))
    elif args.command=='identify':
        with connect(args.db) as db:
            chem=db.execute('SELECT name FROM chemicals WHERE id=?',(args.chemical_id,)).fetchone()
        if not chem:
            parser.error('관심 목록에 없는 물질입니다.')
        print(json.dumps(research.lookup_identity(args.db,'chemical:'+args.chemical_id,chem['name']),ensure_ascii=False))
    elif args.command=='fulltext':
        print(json.dumps(research.fetch_fulltext(args.db,args.chemical_id,args.record_key),ensure_ascii=False))
    elif args.command in ('backup','prepare-check'):
        destination=research.backup(args.db,args.destination)
        if args.command=='prepare-check':
            with connect(destination) as db:
                db.execute("INSERT OR REPLACE INTO metadata VALUES ('validation_copy','true')")
        print(destination)
    elif args.command=='doctor':
        result=research.doctor(args.db)
        print(json.dumps(result,ensure_ascii=False))
        if not result['ok']:
            raise SystemExit(1)
    elif args.command=='restore':
        from .deployment import restore_database
        print(restore_database(args.source,args.db))
    elif args.command == 'serve':
        if not Path(args.db).is_file():
            parser.error('DB가 없습니다. init 또는 별도 경로의 seed-demo를 먼저 실행하세요.')
        research.init_research(args.db)
        if args.host not in ('127.0.0.1','localhost') and not args.public_origin:
            parser.error('외부 바인딩에는 --public-origin https://도메인을 명시하세요. TLS·인증 프록시 뒤에서만 사용하세요.')
        from .deployment import BoundedHTTPServer
        httpd = BoundedHTTPServer((args.host, args.port), handler_for(args.db, args.port,args.public_origin,args.read_only))
        print(f'PIONEER listening on port {args.port}', flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.server_close()


if __name__ == '__main__':
    main()
