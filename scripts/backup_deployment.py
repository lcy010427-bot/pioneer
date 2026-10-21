#!/usr/bin/env python3
"""Online consistent backup from the configured Compose deployment to this host."""
import datetime
import subprocess
from pathlib import Path


def main():
    folder=Path('backups');folder.mkdir(mode=0o700,exist_ok=True)
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    name=f'pioneer-{stamp}.sqlite';destination=folder/name
    if destination.exists():raise SystemExit('동일 시각 백업이 이미 있습니다. 기존 파일을 보존합니다.')
    subprocess.run(['docker','compose','exec','-T','app','python','-m','pioneer','backup','/data/backups/'+name],check=True)
    subprocess.run(['docker','compose','cp','app:/data/backups/'+name,str(destination)],check=True)
    destination.chmod(0o600)
    print(destination)
    print('서버 외부의 별도 보관 위치에도 복사하세요. 자동 삭제는 하지 않습니다.')


if __name__=='__main__':main()
