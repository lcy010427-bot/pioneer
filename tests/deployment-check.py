"""Isolated Docker test: loopback TLS, fixture DB, no deployment/ACME/DNS changes."""
import argparse
import base64
import http.client
import socket
import json
import os
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--browser',action='store_true');args=parser.parse_args()
    project='pioneer-check-'+uuid.uuid4().hex[:8]
    checks=[]
    (ROOT/'.data').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='deploy-check-',dir=ROOT/'.data') as folder:
        folder=Path(folder)
        def run(args,**kwargs):
            result=subprocess.run(args,cwd=ROOT,capture_output=True,**kwargs)
            if result.returncode:raise RuntimeError(result.stderr.decode() if isinstance(result.stderr,bytes) else result.stderr)
            return result
        password='deployment-test-fixture-only'
        hashed=run(['docker','run','--rm','--network','none','caddy:2.10.2-alpine','caddy','hash-password','--plaintext',password]).stdout.decode().strip()
        env=folder/'test.env';env.write_text("DOMAIN=pioneer.localhost:19443\nADMIN_DOMAIN=admin.pioneer.localhost:19443\nACME_EMAIL=test@example.com\nPIONEER_USERNAME=admin\nPIONEER_PASSWORD_HASH='"+hashed+"'\nPIONEER_IMAGE_TAG=local\n")
        env.chmod(0o600)
        config=folder/'Caddyfile'
        config.write_text((ROOT/'deploy/Caddyfile').read_text().replace('{\n','{\n    local_certs\n    skip_install_trust\n    auto_https disable_redirects\n',1))
        config.chmod(0o644)
        override=folder/'override.yaml'
        override.write_text('services:\n  app:\n    healthcheck: {interval: 1s, timeout: 3s, start_period: 2s, retries: 10}\n  public:\n    healthcheck: {interval: 1s, timeout: 3s, start_period: 2s, retries: 10}\n  proxy:\n    ports: !override\n      - "127.0.0.1:19443:19443"\n    volumes: !override\n      - '+str(config)+':/etc/caddy/Caddyfile:ro\n      - caddy_data:/data\n      - caddy_config:/config\n')
        command=['docker','compose','--project-name',project,'--env-file',str(env),'-f',str(ROOT/'compose.yaml'),'-f',str(override)]
        ctx=ssl._create_unverified_context()  # Only the isolated test's private CA.
        def request(host,path,method='GET',payload=None,auth=False,origin=None):
            headers={'Host':host}
            if auth:headers['Authorization']='Basic '+base64.b64encode(('admin:'+password).encode()).decode()
            if origin:headers['Origin']=origin
            body=None
            if payload is not None:headers['Content-Type']='application/json';body=json.dumps(payload).encode()
            connection=http.client.HTTPSConnection(host.split(':')[0],19443,context=ctx,timeout=30)
            connection._create_connection=lambda address,timeout,source_address=None:socket.create_connection(('127.0.0.1',19443),timeout)
            try:
                connection.request(method,path,body=body,headers=headers)
                response=connection.getresponse()
                return response.status,response.read(),response.headers
            finally:connection.close()
        try:
            run(command+['config','--quiet']);checks.append('compose validation')
            # Validate the exact production Caddyfile before the local-CA test variant.
            run(command+['run','--rm','--no-deps','--entrypoint','caddy','-v',str(ROOT/'deploy/Caddyfile')+':/tmp/production.Caddyfile:ro','proxy','validate','--config','/tmp/production.Caddyfile','--adapter','caddyfile'])
            checks.append('production Caddy config validation')
            run(command+['up','-d','--no-build','--wait','--wait-timeout','90'])
            assert run(command+['exec','-T','app','id','-u']).stdout.decode().strip()=='10001'
            for service in ('app','public'):
                container=run(command+['ps','-q',service]).stdout.decode().strip()
                inspected=json.loads(run(['docker','inspect',container]).stdout)[0]
                assert inspected['HostConfig']['ReadonlyRootfs']
                assert not any(inspected['NetworkSettings']['Ports'].values())
                if service=='public':assert not next(m for m in inspected['Mounts'] if m['Destination']=='/data')['RW']
            checks.append('healthy non-root services, read-only public volume, no exposed backend ports')
            public='pioneer.localhost:19443';admin='admin.pioneer.localhost:19443'
            for _ in range(30):
                try:
                    if request(public,'/healthz')[0]==200:break
                except (OSError,urllib.error.URLError):pass
                time.sleep(.3)
            assert request(public,'/')[0]==200
            assert request(admin,'/')[0]==401
            assert request(admin,'/',auth=True)[0]==200
            checks.append('anonymous public and authenticated administrator TLS routing')
            assert request(public,'/api/radar/report','POST',{})[0]==403
            assert request(admin,'/api/radar/report','POST',{})[0]==401
            checks.append('public writes and unauthenticated admin writes blocked')
            assert request(admin,'/api/radar/report','POST',{},True,'https://evil.example')[0]==403
            checks.append('authenticated cross-origin writes blocked')
            status,body,headers=request(public,'/api/config');assert status==200 and json.loads(body)['read_only']
            assert headers['Strict-Transport-Security']=='max-age=31536000'
            assert 'frame-ancestors' in headers['Content-Security-Policy']
            checks.append('public mode and security headers')
            saved=json.loads(request(admin,'/api/radar/report','POST',{'start':'2024-01-01','end':'2024-12-31'},True,'https://'+admin)[1])
            assert saved.get('report_id'),saved
            checks.append('authenticated same-origin report save')
            assert request(public,'/api/radar?report_id='+saved['report_id'])[0]==403
            checks.append('private snapshot cannot be exported publicly')
            report_id=saved['report_id']
            run(command+['restart','app'])
            run(command+['up','-d','--no-build','--wait','--wait-timeout','90'])
            result=json.loads(request(admin,'/api/radar?report_id='+report_id,auth=True)[1]);assert result['report_id']==report_id
            checks.append('saved data survives container restart')
            # Online SQLite backup and restore into an absent alternate DB.
            run(command+['exec','-T','app','python','-m','pioneer','backup','/data/check-backup.sqlite'])
            run(command+['exec','-T','app','python','-m','pioneer','--db','/data/restored.sqlite','restore','/data/check-backup.sqlite'])
            doctor=json.loads(run(command+['exec','-T','app','python','-m','pioneer','--db','/data/restored.sqlite','doctor']).stdout)
            assert doctor['ok'];checks.append('online backup and checked restore')
            public_db=run(command+['exec','-T','public','python','-c',"import sqlite3; d=sqlite3.connect('/data/pioneer.sqlite'); print(d.execute('SELECT COUNT(*) FROM radar_reports').fetchone()[0])"]).stdout.decode().strip()
            assert int(public_db)>=1;checks.append('public process sees shared persisted data')
            # Fresh volume import uses an actual SQLite stream, no host file permissions workaround.
            backup_file=folder/'fixture.sqlite'
            run(command+['cp','app:/data/check-backup.sqlite',str(backup_file)])
            with backup_file.open('rb') as stream:
                run(command+['run','--rm','--no-deps','-T','-e','PIONEER_DB=/data/imported.sqlite','--entrypoint','python','app','-m','pioneer.deployment','import'],stdin=stream)
            checks.append('stdin data import')
            if args.browser:
                result=run(['node','tests/public-browser.cjs']);print(result.stdout.decode());checks.append('public and admin browser flows')
            print(json.dumps({'passed':checks,'deployment':'none; loopback-only ephemeral test','project':project},ensure_ascii=False))
        except Exception:
            diagnostic=subprocess.run(command+['logs','--tail','30'],cwd=ROOT,capture_output=True,text=True)
            print(diagnostic.stdout[-12000:])
            raise
        finally:
            run(command+['down','--volumes','--remove-orphans'])


if __name__=='__main__':main()
