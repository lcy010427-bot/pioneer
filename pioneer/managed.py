"""One-container hosting: platform HTTPS, optional administrator Basic login."""
import gzip
import logging
import os
import signal
import subprocess
import tempfile
import threading
from pathlib import Path

from . import deployment
from . import screening
from .core import connect
from .__main__ import handler_for


CADDY='''{
 admin off
 auto_https off
}
:{$PORT} {
 encode gzip
 request_body {
  max_size 8KB
 }
 header {
  Strict-Transport-Security "max-age=31536000"
  -Server
 }
 @login path /operator
 handle @login {
  route {
   basic_auth {
    {$PIONEER_USERNAME} {$PIONEER_PASSWORD_HASH}
   }
   redir * / 303
  }
 }
 @operator header Authorization *
 handle @operator {
  basic_auth {
   {$PIONEER_USERNAME} {$PIONEER_PASSWORD_HASH}
  }
  reverse_proxy 127.0.0.1:8765 {
   header_up -Authorization
  }
 }
 handle {
  reverse_proxy 127.0.0.1:8766 {
   header_up -Authorization
  }
 }
}
'''


def bootstrap(path,asset):
    if Path(path).exists():return
    if not Path(asset).is_file():raise ValueError('초기 공개 문헌 자료가 없습니다.')
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=Path(path).parent) as folder:
        seed=Path(folder)/'starter.sqlite'
        with gzip.open(asset,'rb') as source,seed.open('wb') as target:
            while block:=source.read(1024*1024):target.write(block)
        deployment.restore_database(seed,path)


def install_journal_starter(path,asset):
    """Append the new public corpus on older persistent disks; preserve user data."""
    with connect(path) as db:
        if db.execute("SELECT 1 FROM screening_runs WHERE query=? AND status IN ('complete','partial') LIMIT 1",(screening.QUERY,)).fetchone():return
    with tempfile.TemporaryDirectory(dir=Path(path).parent) as folder:
        seed=Path(folder)/'journals.sqlite'
        with gzip.open(asset,'rb') as src,seed.open('wb') as dst:
            import shutil
            shutil.copyfileobj(src,dst)
        with connect(path) as db:
            db.execute('ATTACH DATABASE ? AS journal_seed',(str(seed),))
            # Recheck while holding the writer transaction; do not overwrite runs.
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM screening_runs WHERE query=? AND status IN ('complete','partial') LIMIT 1",(screening.QUERY,)).fetchone():return
            for row in db.execute("SELECT * FROM journal_seed.screening_runs WHERE query=? AND status IN ('complete','partial')",(screening.QUERY,)).fetchall():
                columns=[k for k in row.keys() if k!='id']
                rid=db.execute('INSERT INTO screening_runs ('+','.join(columns)+') VALUES ('+','.join('?' for _ in columns)+')',[row[k] for k in columns]).lastrowid
                db.execute('INSERT INTO screening_candidates SELECT ?,candidate_id,name,identifier,data FROM journal_seed.screening_candidates WHERE run_id=?',(rid,row['id']))


def main():
    # A new managed disk may be owned by root. Only the initial ownership step
    # runs privileged; both HTTP servers and Caddy run as UID/GID 10001.
    if os.geteuid()==0:
        data_dir=Path(os.environ.get('PIONEER_DB','/data/pioneer.sqlite')).parent
        data_dir.mkdir(parents=True,exist_ok=True);os.chown(data_dir,10001,10001)
        os.setgroups([]);os.setgid(10001);os.setuid(10001)
    logging.basicConfig(level=logging.INFO,format='%(levelname)s %(message)s')
    os.umask(0o077)
    host=os.environ.get('RENDER_EXTERNAL_HOSTNAME','')
    origin=deployment.validate_origin(os.environ.get('PIONEER_PUBLIC_ORIGIN','https://'+host))
    password=os.environ.get('PIONEER_ADMIN_PASSWORD','')
    if len(password)<12 or len(password.encode())>72 or any(c in password for c in '\r\n'):
        raise ValueError('관리자 비밀번호는 12자 이상, UTF-8 72바이트 이내로 설정하세요.')
    env=os.environ.copy();env.setdefault('PIONEER_USERNAME','admin');env.setdefault('PORT','10000')
    if not env['PORT'].isdigit() or not 1024<=int(env['PORT'])<=65535 or int(env['PORT']) in (8765,8766):raise ValueError('잘못된 서비스 포트입니다.')
    if not __import__('re').fullmatch(r'[A-Za-z0-9_.-]{2,64}',env['PIONEER_USERNAME']):raise ValueError('잘못된 운영자 아이디입니다.')
    hashed=subprocess.run(['caddy','hash-password','--plaintext',password],capture_output=True,text=True,check=True).stdout.strip()
    env['PIONEER_PASSWORD_HASH']=hashed;env.pop('PIONEER_ADMIN_PASSWORD',None)
    path=os.environ.get('PIONEER_DB','/data/pioneer.sqlite')
    bootstrap(path,Path(__file__).resolve().parent.parent/'public-data/starter.sqlite.gz')
    with deployment.writer_lock(path):
        deployment.prepare_database(path)
        install_journal_starter(path,Path(__file__).resolve().parent.parent/'public-data/starter.sqlite.gz')
        public=deployment.BoundedHTTPServer(('127.0.0.1',8766),handler_for(path,8766,origin,True,origin+'/operator'))
        admin=deployment.BoundedHTTPServer(('127.0.0.1',8765),handler_for(path,8765,origin,False))
        stop=threading.Event()
        signal.signal(signal.SIGTERM,lambda *_:stop.set());signal.signal(signal.SIGINT,lambda *_:stop.set())
        for server in (public,admin):threading.Thread(target=server.serve_forever,daemon=True).start()
        with tempfile.TemporaryDirectory() as folder:
            config=Path(folder)/'Caddyfile';config.write_text(CADDY)
            env['XDG_CONFIG_HOME']=str(Path(folder)/'config')
            env['XDG_DATA_HOME']=str(Path(folder)/'data')
            proxy=subprocess.Popen(['caddy','run','--config',str(config),'--adapter','caddyfile'],env=env)
            try:
                while proxy.poll() is None and not stop.wait(.5):pass
                if proxy.poll() not in (None,0):raise RuntimeError('HTTP 프록시가 종료됐습니다.')
            finally:
                proxy.terminate()
                try:proxy.wait(timeout=10)
                except subprocess.TimeoutExpired:proxy.kill();proxy.wait()
                for server in (public,admin):server.shutdown();server.server_close()


if __name__=='__main__':main()
