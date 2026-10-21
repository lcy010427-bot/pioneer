#!/usr/bin/env python3
"""Create private configuration; passwords never enter host argv or shell history."""
import getpass
import os
import re
import subprocess
from pathlib import Path


def main():
    target=Path('.env')
    if target.exists():raise SystemExit('.env가 이미 있습니다. 기존 파일은 직접 수정하세요.')
    domain=input('공개 조회 도메인 (예: pioneer.example.com): ').strip().lower()
    admin=input('운영자 도메인 (예: admin.pioneer.example.com): ').strip().lower()
    email=input('HTTPS 인증서 알림 이메일: ').strip()
    user=input('운영자 아이디 [admin]: ').strip() or 'admin'
    for value in (domain,admin):
        if not re.fullmatch(r'(?=.{1,253}$)[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?',value) or '.' not in value:
            raise SystemExit('도메인만 입력하세요. https://·경로·포트는 넣지 않습니다.')
    if domain==admin:raise SystemExit('공개/운영자 도메인을 분리하세요.')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{2,64}',user):raise SystemExit('아이디는 영문/숫자/._- 2~64자입니다.')
    if not re.fullmatch(r'[A-Za-z0-9_.+%-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',email):raise SystemExit('이메일 형식을 확인하세요.')
    password=getpass.getpass('운영자 비밀번호 (12자 이상): ')
    if len(password)<12 or '\n' in password or '\r' in password or len(password.encode())>72:
        raise SystemExit('12자 이상, UTF-8 72바이트 이내의 줄바꿈 없는 비밀번호를 사용하세요.')
    if password!=getpass.getpass('비밀번호 확인: '):raise SystemExit('비밀번호가 일치하지 않습니다.')
    result=subprocess.run(['docker','run','--rm','-i','--network','none','--entrypoint','sh',
        'caddy:2.10.2-alpine@sha256:4c6e91c6ed0e2fa03efd5b44747b625fec79bc9cd06ac5235a779726618e530d',
        '-c','IFS= read -r secret; exec caddy hash-password --plaintext "$secret"'],
        input=password+'\n',text=True,capture_output=True)
    if result.returncode:raise SystemExit('해시 생성 실패. docker pull caddy:2.10.2-alpine 후 다시 실행하세요.')
    hashed=result.stdout.strip()
    if not re.fullmatch(r'\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}',hashed):raise SystemExit('해시 형식 확인 실패')
    content=f"DOMAIN={domain}\nADMIN_DOMAIN={admin}\nACME_EMAIL={email}\nPIONEER_USERNAME={user}\nPIONEER_PASSWORD_HASH='{hashed}'\nPIONEER_IMAGE_TAG=local\n"
    fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as stream:stream.write(content)
    print('.env 준비 완료. 비밀번호는 저장하지 않았습니다. 아직 배포하지 않았습니다.')


if __name__=='__main__':main()
