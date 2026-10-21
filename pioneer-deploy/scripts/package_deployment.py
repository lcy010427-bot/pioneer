#!/usr/bin/env python3
"""Package source and a consistent live-data backup, excluding credentials/test DBs."""
import argparse
import hashlib
import json
import os
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pioneer import deployment, research
from pioneer.core import connect, view


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--db',default=str(ROOT/'.data/pioneer.sqlite'))
    parser.add_argument('--output',default=str(ROOT/'.data/pioneer-deploy.tar.gz'))
    parser.add_argument('--without-data',action='store_true')
    args=parser.parse_args()
    output=Path(args.output).resolve();output.parent.mkdir(parents=True,exist_ok=True)
    paths=[ROOT/name for name in ('README.md','.gitignore','.dockerignore','.env.example','Dockerfile','compose.yaml')]
    for folder in ('pioneer','web','docs','deploy','scripts','tests','.github'):
        if (ROOT/folder).exists():
            paths.extend(p for p in (ROOT/folder).rglob('*') if p.is_file() and not p.is_symlink() and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.pyo'))
    with tempfile.TemporaryDirectory(prefix='pioneer-package-') as folder:
        folder=Path(folder);manifest=dict(created_at=datetime.now(timezone.utc).isoformat(),includes_credentials=False,includes_data=not args.without_data)
        seed=folder/'pioneer.sqlite'
        if not args.without_data:
            deployment.check_live(args.db)
            if not research.doctor(args.db)['ok']:raise SystemExit('DB 검사 실패. 이관본을 만들지 않습니다.')
            research.backup(args.db,seed);seed.chmod(0o600)
            snapshot=view(seed)
            manifest.update(seed_sha256=hashlib.sha256(seed.read_bytes()).hexdigest(),dataset_kind=snapshot['dataset_kind'],
                chemicals=[dict(id=c['id'],name=c['name'],run_id=c['run']['id'] if c['run'] else None,records=len(c['papers'])) for c in snapshot['chemicals']],
                scope='물질별 검색 일치 레코드이며 물질 간 중복·검색 사전 누락이 있을 수 있습니다.')
        manifest_file=folder/'manifest.json';manifest_file.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
        handle,temp_name=tempfile.mkstemp(prefix='.pioneer-package-',dir=output.parent);os.close(handle)
        def public_mode(info):info.mode=0o644;info.uid=info.gid=0;info.uname=info.gname='';return info
        try:
            with tarfile.open(temp_name,'w:gz') as archive:
                for p in sorted(paths):archive.add(p,arcname=str(Path('pioneer-deploy')/p.relative_to(ROOT)),recursive=False,filter=public_mode)
                archive.add(manifest_file,arcname='pioneer-deploy/manifest.json',filter=public_mode)
                if not args.without_data:
                    archive.add(seed,arcname='pioneer-deploy/seed/pioneer.sqlite',filter=lambda info: info)
            os.chmod(temp_name,0o600);os.replace(temp_name,output)
        finally:Path(temp_name).unlink(missing_ok=True)
    print(output)
    print('배포 패키지만 생성했습니다. .env·실제 비밀번호·검사 DB·Docker 이미지는 포함하지 않습니다.')


if __name__=='__main__':main()
