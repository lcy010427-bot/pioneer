# PIONEER 배포 안내

이 저장소는 **누구나 조회, 운영자만 갱신·기록**하는 단일 서버 배포를 지원합니다.
아래는 배포자가 실행할 명령이며 이번 작업에서는 실제 도메인 연결·게시·배포를 하지 않았습니다.

## 구성

| 구성 요소 | 역할 | 접근 |
| --- | --- | --- |
| Caddy | HTTPS 발급/갱신, 운영자 HTTP Basic 로그인, 프록시·압축·보안 헤더 | 호스트의 80/443만 공개 |
| public | 기존 자료 조회·기간/필터·근거·비교·현재 결과 CSV | 공개, 모든 쓰기 차단, 데이터 볼륨도 읽기 전용 |
| app | 문헌 수집·갱신·검토 기록·분석 저장 | 운영자 도메인의 로그인 뒤에서만 접근 |
| pioneer_data | SQLite·수집 자료·백업 | Docker 영구 볼륨, 이미지/공개 정적 파일과 분리 |

공개 예시 `https://pioneer.example.com`, 운영자 예시 `https://admin.pioneer.example.com`.
운영자는 브라우저의 로그인 창에서 인증합니다. Basic 인증은 HTTPS 뒤에서만 사용하며 평문 비밀번호를 저장하지 않습니다.
이는 단일 운영 계정 구성입니다. 사용자별 계정·세분화된 권한·SSO·수평 확장을 구현했다고 주장하지 않습니다.

공개 API는 검토자/평가자 이름·자유 입력 사유·평가 내용·내부 작업/검토 이력을 내보내지 않습니다.
저장 분석·전체 내부 export는 운영자 전용이고 공개 CSV는 현재 조회 조건의 문헌 집계만 내보냅니다.
공개 화면의 수집·저장·검토 버튼은 비활성화되며 운영자 로그인 링크를 제공합니다.

## 필요한 환경

- Linux 서버, Docker Engine + Compose v2.24 이상. 권장 2 vCPU·4GB RAM·여유 SSD.
- 같은 서버 IP를 가리키는 공개/운영자 도메인 A 레코드 2개. IPv6를 쓰면 AAAA도 실제 서버에 맞춥니다.
- 인바운드 TCP 80/443, 선택 UDP 443(HTTP/3). 백엔드 8765는 호스트에 열지 않습니다.
- 운영자 컨테이너의 아웃바운드 HTTPS: Europe PMC `www.ebi.ac.uk`, PubChem `pubchem.ncbi.nlm.nih.gov`.
  공개 컨테이너는 내부 네트워크만 사용합니다. CTD의 기존 접근 실패를 해결했다고 간주하지 않습니다.
- Python 3.12+는 설정/패키지 스크립트를 실행하는 호스트에서 사용합니다. 앱에 별도 pip 설치·AI 키는 필요 없습니다.

## 1. 소스와 실자료 준비

생성된 `pioneer-deploy.tar.gz`를 서버의 비공개 작업 디렉터리로 옮겨 풉니다.

```sh
tar -xzf pioneer-deploy.tar.gz
cd pioneer-deploy
```

패키지에는 소스·설정 예시·실자료 SQLite 이관본 `seed/pioneer.sqlite`·체크섬 `manifest.json`이 있습니다.
`.env`·실제 로그인 비밀번호·검사용 DB·Docker 이미지는 없습니다. DB 이관본을 포함하므로 패키지를 웹 디렉터리에 두지 마세요.
기존 일반 소스 ZIP에는 DB가 없습니다. 데이터 없이 패키징한 경우 아래 이관 단계를 건너뛰면 빈 실자료 DB로 시작합니다.

체크섬 확인:

```sh
python3 -c 'import hashlib,json,pathlib; m=json.loads(pathlib.Path("manifest.json").read_text()); assert hashlib.sha256(pathlib.Path("seed/pioneer.sqlite").read_bytes()).hexdigest()==m["seed_sha256"]; print("seed OK")'
```

## 2. 도메인·운영 계정 설정

```sh
docker pull caddy:2.10.2-alpine
python3 scripts/configure_deployment.py
```

스크립트가 두 도메인·인증서 알림 이메일·운영자 아이디·비밀번호를 입력받습니다.
비밀번호는 숨김 입력하며 bcrypt 해시만 `.env`에 0600 권한으로 저장합니다. 12자 이상, UTF-8 72바이트 이내입니다.
기존 `.env`를 덮어쓰지 않습니다. 실제 비밀번호를 명령행·Git·이미지에 넣지 마세요.

수동 설정은 `.env.example`을 참고합니다. bcrypt의 `$` 보존을 위해 `.env`의 해시는 **작은따옴표**로 감쌉니다.
공개 도메인과 운영자 도메인을 같게 설정하지 않습니다. `deploy/Caddyfile`은 비밀값 없는 템플릿으로 읽기 권한 0644를 유지합니다.

## 3. 빌드·자료 이관

```sh
docker compose config --quiet
docker compose build app
# 기존 자료가 있는 최초 배포에서, 서버를 시작하기 전에 한 번만:
docker compose run --rm --no-deps -T --entrypoint python app -m pioneer.deployment import < seed/pioneer.sqlite
```

이관은 stdin으로 읽으므로 호스트 파일의 소유권을 컨테이너 UID로 바꾸지 않아도 됩니다.
512MiB 이내의 SQLite 백업을 검사하고 새 DB로 원자적으로 복구합니다. 이미 운영 DB가 있으면 덮어쓰지 않습니다.
데모·자동 검사 복제 DB는 운영 이관/기동에서 거부합니다. 이미 빈 DB를 시작한 경우도 임의로 덮어쓰지 말고 새 프로젝트/볼륨에 이관하세요.

## 4. 실제 배포 시 실행

DNS·방화벽과 위 설정을 마친 배포자가 실행합니다.

```sh
docker compose up -d --no-build --wait
docker compose ps
docker compose logs --tail 50 app public proxy
```

Caddy가 실제 DNS가 가리키는 서버에서 공인 HTTPS 인증서를 발급·갱신합니다.
**공인 인증서 발급은 실제 도메인 연결 후에 확인할 사항**입니다. 로컬 테스트의 자체 CA 인증서와 구분합니다.

확인 항목: 공개 사이트 로그인 없이 조회 / 운영자 주소 미인증 401 / 로그인 후 갱신·저장 / 공개 쓰기 403 /
새로고침 후 같은 저장 자료 / 두 앱의 healthy 상태. 인증 정보 없이 `/healthz`는 공개 사이트에서 최소 준비 상태만 반환합니다.
서버는 동시 처리 상한·소켓 제한 시간·요청 크기 제한을 두고, HTTPS/연결 처리는 Caddy 뒤에서 수행합니다.

## 백업·복구

운영 중 일관된 SQLite 백업을 호스트에도 가져옵니다.

```sh
python3 scripts/backup_deployment.py
```

`backups/`와 서버 볼륨의 `/data/backups/`에 저장하며 기존 백업을 덮어쓰거나 자동 삭제하지 않습니다.
호스트 백업을 서버와 다른 보관 위치에도 복사하고 필요에 맞춰 스케줄링합니다. 자동 원격 백업 설정은 포함하지 않습니다.

복구는 기존 볼륨을 보존하고 **새 Compose 프로젝트/볼륨**에 수행할 수 있습니다.

```sh
# 기존 프로젝트를 중지하여 공개 포트를 반환. 볼륨은 보존합니다.
docker compose stop
# 복구할 백업으로 새 프로젝트의 새 DB를 만듭니다.
docker compose -p pioneer-recovery run --rm --no-deps -T --entrypoint python app -m pioneer.deployment import < backups/복구할백업.sqlite
docker compose -p pioneer-recovery up -d --no-build --wait
docker compose -p pioneer-recovery exec -T app python -m pioneer doctor
```

복구 프로젝트는 이후 명령에도 `-p pioneer-recovery`를 사용합니다. 필요 시 새 프로젝트를 중지하고 이전 프로젝트를 다시 시작해 되돌립니다.
원본 볼륨·인증서 저장소를 지우지 않습니다. 소스/이미지도 함께 이전 버전으로 되돌릴 수 있도록 태그와 패키지를 보관하세요.
단일 SQLite 쓰기 인스턴스는 파일 잠금으로 보호하며, 컨테이너 재시작 시 끝나지 않은 작업은 중단/실패로 표시하고 기존 완전 스냅샷을 보존합니다.

## 업데이트

업데이트 전에 백업하고 새 `PIONEER_IMAGE_TAG`로 빌드한 후 재기동합니다. 공개 읽기 서버보다 운영자 서버가 먼저 마이그레이션합니다.
스키마 변경 시 마이그레이션 전 백업을 남깁니다. `docker compose down`은 볼륨을 보존하지만 볼륨 삭제 옵션은 사용하지 마세요.
Caddy 설정 변경은 `docker compose restart proxy`, 계정 해시 변경은 환경 변경을 반영하는 `docker compose up -d`로 적용합니다.
운영자 Basic 로그인은 브라우저가 캐시하므로 공용 컴퓨터 사용 후 인증 세션/브라우저를 종료하세요.

## 로컬 검증과 CI

```sh
python3 -m unittest discover -s tests -v
node tests/timeline-check.cjs
docker build -t pioneer:local .
docker pull caddy:2.10.2-alpine
python3 tests/deployment-check.py
# 기존 Playwright/Chromium 검사 환경에서 추가:
python3 tests/deployment-check.py --browser
```

Docker 검사는 임시 프로젝트·가짜 운영 입력·빈 실자료 DB·로컬 CA·127.0.0.1:19443만 사용하고 종료 시 해당 검사 볼륨을 정리합니다.
실제 DB·공인 DNS·인증서·공개 80/443 포트에 변경을 가하지 않습니다. GitHub Actions는 검사와 이미지 빌드만 수행하고 게시/push/배포는 하지 않습니다.
검증된 상세 범위는 `docs/deployment-validation.md`에 기록합니다.

이 구성은 연구용 단일 서버 배포를 위한 것입니다. Hot 판정 임계값·직접 근거 여부·전문가 평가의 과학적 미확정 상태는 배포 준비와 별개입니다.
