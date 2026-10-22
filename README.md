# PIONEER · Journal Chemical Radar

지정한 환경·보건 저널의 논문에서 물질과 물질군을 자동 발견하는 연구용 웹사이트입니다. 물질 목록을 미리 정하지 않습니다.

- **Hot Radar**: 시작일·종료일을 설정하고, 문헌에서 발견한 후보·시간 추이·전후반 건수·근거 문장·저널·DOI/Europe PMC 링크를 확인합니다.
- **물질 분석**: 사용자가 보관함에 추가한 후보만 기존 추이·문헌·맥락·공백·비교 화면에서 검토합니다. 기본 분석 물질은 없습니다.
- 보관함은 현재 브라우저에 저장됩니다. Pretendard와 파란색 레이더 디자인을 유지합니다.

## 수집 범위

Europe PMC API에서 다음 10개 저널을 ISSN으로 조회합니다. 출판사 사이트나 유료 원문은 수집하지 않습니다.

1. Environmental Health Perspectives
2. Environmental Science & Technology
3. Environmental Science & Technology Letters
4. Environment International
5. Journal of Hazardous Materials
6. Water Research
7. Environmental Pollution
8. Science of the Total Environment
9. Chemosphere
10. Analytical Chemistry

저널 목록은 `pioneer/journals.py`에 있습니다. 검색식에 특정 물질이나 `emerging contaminant` 필수어를 넣지 않습니다. 자료원 주석과 화학 명칭·물질군 문구 및 약어를 함께 추출합니다. 예를 들어 `liquid crystal monomers (LCMs)`와 사전에 없던 `fluorinated oligomers (NFOs)`를 추출할 수 있습니다. 자동 명칭과 구조 식별자는 원문 확인이 필요합니다. 모든 물질군·화학명을 빠짐없이 인식하는 기능은 아닙니다.

기간은 같은 길이의 전반과 후반으로 비교합니다. 가운데 하루가 남으면 비교에서 제외합니다. 조회 기간 전체의 선그래프에는 그 날도 포함됩니다. 자료원 수집 한도를 넘으면 **부분 표본**이며, 건수·증감·신규 표시는 수집 범위 내 관찰입니다. 신규 표시는 세계 최초 보고를 뜻하지 않습니다. 신규 한 편도 근거와 함께 표시하며, 문헌 증가 신호는 후반 5건 이상·증가 3건 이상·증가율 50% 이상이라는 잠정값을 사용합니다. 노출·검출·독성·규제 신호는 후보 주변 근거 문장의 키워드 문맥이며 위해성 판정이나 기관의 공식 조치 확인이 아닙니다.

수집 한도는 저널마다 동일하게 배분합니다. 기본 운영 한도는 전·후반 각각 최대 1,000건이며 저널당 최대 100건입니다. 조회 데이터가 없는 날짜는 수집 결과가 없다고 표시하며 운영자가 원하는 기간을 수집할 수 있습니다. 새 수집 완료까지 작업 상태를 확인한 뒤 목록을 갱신합니다.

공개 페이지에서는 누구나 기간을 바꿔 저장된 결과를 조회하고 개인 보관함에 추가할 수 있습니다. 외부 자료 수집은 `/operator` 로그인 후 실행합니다. Crossref 직접 수집, NORMAN, CompTox, 공식 규제 자료 연계는 현재 포함하지 않습니다. 자료원에 아직 색인되지 않은 최신 논문과 초록 미제공 논문은 누락될 수 있습니다.

## 온라인 실행

동봉 시작 자료는 2026-10-22 기준 API 실수집 결과입니다. 1년 기간 895건, 최근 3개월 785건, 5년 기간 1,000건을 포함하며 모두 부분 표본입니다. 기간별 자료가 겹칠 수 있습니다. 검색 총건수와 저널별 수신 건수는 `public-data/manifest.json`과 화면에서 확인할 수 있습니다. 사용자가 예시로 제공한 DOI `10.1021/EHP.6c00619`는 수집 시점 Europe PMC에서 조회되지 않아 이 논문을 실수집 근거로 넣지 않았습니다.

GitHub 저장소 최상단에 이 프로젝트의 파일을 올리고 기존 Render 서비스에서 다시 빌드합니다. `Dockerfile.render`와 `render.yaml`을 사용합니다. 사용자 컴퓨터에 Python을 설치할 필요는 없습니다. 배포를 직접 수행하지 않은 소스 패키지입니다.

Render 무료 파일시스템에서는 재시작 시 운영자가 추가한 자료가 시작 데이터로 돌아갈 수 있습니다. 보존하려면 영구 디스크나 별도 Docker 서버가 필요합니다. 자세한 설정은 [Render 안내](docs/render.md)를 참고하세요.

## 로컬 실행 및 검사

Python 3.12+ 표준 라이브러리만 사용합니다.

```sh
python -m pioneer init
python -m pioneer screen --start 2025-10-23 --end 2026-10-22 --max-pages 10
python -m pioneer serve --port 8765
python -m unittest discover -s tests -q
node --check web/radar.js
```

단위·HTTP 검사와 실제 브라우저에서 후보 → 근거 → 저장 → 물질 분석 흐름을 확인합니다. 테스트 예시 물질은 검사 코드에서만 명시적으로 넣습니다.
