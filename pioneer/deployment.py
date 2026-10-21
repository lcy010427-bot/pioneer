"""Single-writer deployment behind a TLS/authentication reverse proxy."""
from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import signal
import sqlite3
import sys
import tempfile
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .core import connect, init_db, now
from . import research


def public_data(value):
    """Redact operator identity, free-text notes and internal logs from public JSON."""
    private={'actor','actor_type','participant','evaluator','reason','task'}
    internal={'review_history','external_log','evaluations','decisions'}
    if isinstance(value,list):return [public_data(v) for v in value]
    if isinstance(value,dict):
        return {k:[] if k in internal else 0 if k=='report_count' else
                ('수집 오류·운영자 확인 필요' if v else v) if k=='error' else public_data(v)
                for k,v in value.items() if k not in private and not (k=='notes' and isinstance(v,str))}
    return value


def validate_origin(value):
    if not isinstance(value,str):raise ValueError('HTTPS 공개 주소를 지정하세요.')
    p=urlsplit(value)
    if (p.scheme!='https' or not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?(?::[0-9]{1,5})?',p.netloc)
        or p.path not in ('','/') or p.query or p.fragment or p.username or p.password):
        raise ValueError('공개 주소는 https://도메인[:포트] 형식이어야 합니다.')
    if p.port is not None and not 1<=p.port<=65535:raise ValueError('잘못된 공개 포트입니다.')
    return 'https://'+p.netloc.lower()


class BoundedHTTPServer(ThreadingHTTPServer):
    daemon_threads=True
    request_queue_size=32
    allow_reuse_address=True

    def __init__(self,*args,max_requests=16,**kwargs):
        self.slots=threading.BoundedSemaphore(max_requests)
        super().__init__(*args,**kwargs)

    def process_request(self,request,client_address):
        if not self.slots.acquire(blocking=False):
            try:
                request.settimeout(2)
                request.sendall(b'HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\nRetry-After: 5\r\nConnection: close\r\n\r\n')
            finally:self.shutdown_request(request)
            return
        try:super().process_request(request,client_address)
        except Exception:
            self.slots.release();raise

    def process_request_thread(self,request,client_address):
        try:super().process_request_thread(request,client_address)
        finally:self.slots.release()


@contextmanager
def writer_lock(path):
    lock_path=Path(path).with_name('.'+Path(path).name+'.writer.lock')
    lock_path.parent.mkdir(parents=True,exist_ok=True)
    with lock_path.open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('같은 DB의 운영 서버가 이미 실행 중입니다. 단일 쓰기 인스턴스를 사용하세요.')
        try:yield
        finally:fcntl.flock(lock,fcntl.LOCK_UN)


def check_live(path):
    with sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True) as db:
        if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('DB 무결성 확인 실패')
        meta=dict(db.execute('SELECT key,value FROM metadata'))
    if meta.get('dataset_kind')!='live' or meta.get('validation_copy')=='true':
        raise ValueError('운영에는 실자료 DB만 사용하세요. 데모·자동 검사 복제본은 배포하지 않습니다.')
    return meta


def prepare_database(path,read_only=False):
    path=Path(path)
    if read_only:
        if not path.is_file():raise ValueError('조회 서버 시작 전에 운영 DB를 초기화·이관하세요.')
        meta=check_live(path)
        if meta.get('schema_version')!='4':raise ValueError('운영자 서버에서 DB 마이그레이션을 먼저 수행하세요.')
        return
    if path.exists():
        meta=check_live(path)
        if meta.get('schema_version')!='4':
            stamp=now().replace(':','').replace('+','_')
            research.backup(path,path.parent/'backups'/f'before-migration-{stamp}.sqlite')
    init_db(path)
    check_live(path)
    if not research.doctor(path)['ok']:raise ValueError('운영 DB 검사 실패')
    # PIDs may repeat after a container restart. The exclusive writer lock makes
    # these unfinished operations stale, independent of their old PID values.
    with connect(path) as db:
        reason='서버 재시작으로 작업이 중단됐습니다. 기존 완전 스냅샷은 보존합니다.'
        db.execute("UPDATE refresh_jobs SET status='interrupted',finished_at=?,error=? WHERE status IN ('queued','running')",(now(),reason))
        for table in ('runs','corpus_runs','followups'):
            db.execute(f"UPDATE {table} SET status='failed',finished_at=?,error=? WHERE status='running'",(now(),reason))


def restore_database(source,destination):
    """Restore into an absent DB only. Never overwrite an existing live DB."""
    source=Path(source);destination=Path(destination)
    check_live(source)
    destination.parent.mkdir(parents=True,exist_ok=True)
    with writer_lock(destination):
        if destination.exists():raise ValueError('복구 대상 DB가 이미 있습니다. 서버를 중지하고 기존 DB를 별도로 보존한 뒤 복구하세요.')
        staging=destination.with_name(destination.name+'.restore-tmp')
        if staging.exists():raise ValueError('이전 복구 임시 파일이 있습니다. 상태를 먼저 확인하세요.')
        try:
            research.backup(source,staging)
            if not research.doctor(staging)['ok']:raise ValueError('복구 자료 검사 실패')
            staging.chmod(0o600)
            # Hard link is atomic and refuses to replace an existing destination.
            os.link(staging,destination)
        finally:staging.unlink(missing_ok=True)
    return str(destination)


def import_database(stream,destination):
    """Receive a private SQLite backup through stdin, without a host bind mount."""
    destination=Path(destination)
    if destination.exists():raise ValueError('이미 운영 DB가 있습니다. 기존 자료를 덮어쓰지 않습니다.')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.import-',dir=destination.parent) as folder:
        source=Path(folder)/'source.sqlite'
        received=0
        with source.open('wb') as target:
            while block:=stream.read(1024*1024):
                received+=len(block)
                if received>512*1024*1024:raise ValueError('이관본은 최대 512MiB입니다.')
                target.write(block)
        return restore_database(source,destination)


def main():
    from .__main__ import handler_for
    logging.basicConfig(level=logging.INFO,format='%(levelname)s %(message)s')
    os.umask(0o077)
    path=os.environ.get('PIONEER_DB','/data/pioneer.sqlite')
    if sys.argv[1:]==['import']:
        print(import_database(sys.stdin.buffer,path));return
    if sys.argv[1:]:raise ValueError('지원하지 않는 운영 명령입니다.')
    origin=validate_origin(os.environ.get('PIONEER_PUBLIC_ORIGIN',''))
    read_only=os.environ.get('PIONEER_READ_ONLY','0')=='1'
    if not read_only:
        # A private writer must be reached only through the authenticated proxy.
        if not re.fullmatch(r'\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}',os.environ.get('PIONEER_PASSWORD_HASH','')):
            raise ValueError('운영 로그인 비밀번호의 bcrypt 해시를 설정하세요.')
    def serve():
        prepare_database(path,read_only)
        admin_origin=validate_origin(os.environ['PIONEER_ADMIN_ORIGIN']) if os.environ.get('PIONEER_ADMIN_ORIGIN') else None
        server=BoundedHTTPServer(('0.0.0.0',8765),handler_for(path,8765,origin,read_only,admin_origin))
        def stop(*_):threading.Thread(target=server.shutdown,daemon=True).start()
        signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
        logging.info('PIONEER ready; read_only=%s',read_only)
        try:server.serve_forever()
        finally:server.server_close()
    if read_only:serve()
    else:
        with writer_lock(path):serve()


if __name__=='__main__':main()
