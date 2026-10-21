# 방법론 근거표

공개 원문 3편의 아래 절을 실제 확인했습니다. 세 논문의 DOI·PMID·제목·저자·연도를 Europe PMC 메타데이터와 XML로 대조했습니다.
검색일·접근일: 2026-10-20. 원문 XML과 검색 응답은 `.data/research/`에 보관하고, 내용 해시는 `methods.json`에 기록합니다.

이 자료를 완전한 체계적 문헌고찰로 부르지 않습니다. 최초 특정 방법론 제목 검색 2개는 결과가 없었고, 일반 bibliometric/normalization 검색은 무관한 응용 논문이어서 채택하지 않았습니다. 이후 evidence map 관련 제목과 DOI로 좁혀 아래 3편을 선택했습니다. hot 수치 기준을 정당화하는 원문은 확보하지 않았으므로 임계값은 운영값으로만 남겼습니다.

## PECO와 원문 추출·독립 검토

**Systematic Evidence Map for Over One Hundred and Fifty Per- and Polyfluoroalkyl Substances (PFAS)** (2022). Carlson Laura M, Angrish Michelle, Shirke Avanti V, Radke Elizabeth G, Schulz Brittany, Kraft Andrew, Judson Richard, Patlewicz Grace, Blain Robyn, Lin Cynthia, Vetter Nicole, Lemeris Courtney, Hartman Pamela, Hubbard Heidi, Arzuaga Xabier, Davis Allen, Dishaw Laura V, Druwe Ingrid L, Hollinger Hillary, Jones Ryan, Kaiser J Phillip, Lizarraga Lucina, Noyes Pamela D, Taylor Michele, Shapiro Andrew J, Williams Antony J, Thayer Kristina A.
DOI: [10.1289/EHP10343](https://doi.org/10.1289/EHP10343) · PMID: 35580034 · [PMC9113544 원문](https://europepmc.org/articles/PMC9113544)

- 실제 확인 위치: Methods / Populations, Exposures, Comparators, and Outcomes; Data Extraction of Study Methods and Findings
- 확인한 원문: “Data extraction was performed by one member of the evaluation team (primary extractor) and checked by a second member for completeness and accuracy (secondary extractor).”
- 적용: 모델·매질·생애단계·endpoint와 근거 위치를 분리하고 검토 이력을 저장합니다. 자동 태깅 결과는 검증된 데이터 추출로 간주하지 않습니다.
- 직접/변형: 변형 적용
- 한계: PFAS 특정 범위의 원저 방법을 데이터 구조·검토 흐름에 변형 적용. 포괄적 PECO 스크리닝과 2인 검토는 미완료이며 해당 논문의 노출기간·평가기준을 이식하지 않습니다.

## 범위 차이와 자동 분류의 검증 필요

**Invited Perspective: The Promise of Fit-for-Purpose Systematic Evidence Maps for Supporting Regulatory Health Assessment** (2022). Pelch Katherine E, Kwiatkowski Carol F.
DOI: [10.1289/EHP10743](https://doi.org/10.1289/EHP10743) · PMID: 35580037 · [PMC9113540 원문](https://europepmc.org/articles/PMC9113540)

- 실제 확인 위치: Body paragraphs 4–8
- 확인한 원문: “The scope of an SEM is determined and defined by the Population, Exposure, Comparison, and Outcome (PECO) statement.”
- 적용: 검색 범위·동의어·접근 수준을 표시하고, 원문 미확인·키워드 미검출·자료 부족을 별도 상태로 둡니다.
- 직접/변형: 변형 적용
- 한계: Invited Perspective의 논점에 근거한 설계. 논평 자체는 자동 분류 정확도나 hot 임계값의 검증 연구가 아닙니다.

## 검색 재현성과 자료원 기여 기록

**Insights from search summary tables for evidence and gap maps: a case study on peer support interventions** (2025). Bethel Alison C, Shaw Naomi, Abbot Rebecca, Rogers Morwenna, Price Anna, Anderson Rob, de Bell Sian, Coon Jo Thompson.
DOI: [10.5195/jmla.2025.1831](https://doi.org/10.5195/jmla.2025.1831) · PMID: 40342303 · [PMC12058345 원문](https://europepmc.org/articles/PMC12058345)

- 실제 확인 위치: CASE PRESENTATION / DISCUSSION
- 확인한 원문: “All records retrieved from searches including duplicates, were downloaded into EndNote X9.2 (Clarivate) and coded to indicate the source”
- 적용: 검색식·자료원·날짜·검색/수신/고유 건수와 보완 검색 결과를 남기고 기존 자료를 재사용합니다.
- 직접/변형: 변형 적용
- 한계: 동료지원 EGM의 검색 사례를 로그 구조에 변형 적용. 여러 DB의 포함 연구 기여도 분석·정답 표본이 없으므로 SST 민감도·정밀도는 산출하지 않습니다.

## 문헌에서 가져오지 않은 값

최소 5건·증가율 50%·절대 증가 3건, 완화/보수 프로필, 1년·2년 창, endpoint 공동 언급 3건 이하, 최대 4개 질문, 최대 30개 후보는 모두 운영·UI·파일럿 선택입니다. 논문에 나온 검증된 보편 기준이 아닙니다. 특정 PFAS 원저의 노출기간·종·평가 기준도 다른 물질에 자동 적용하지 않았습니다.

## 공식 기술 자료와 과학적 근거의 구별

Europe PMC REST 검색/원문 API와 PubChem PUG REST는 실제 요청·응답으로 연결을 검증했습니다. 공식 DB 명칭 조회·초록 접근·공개 이용 라이선스는 위 방법론 문헌의 과학적 검증을 대신하지 않습니다. CTD는 CONNECT 403으로 조회하지 못했으며 관련 관계 데이터를 만들지 않았습니다.

## Hot Radar의 기술적 기간 비교 · 2026-10-21

`radar-equal-halves-v1`: 사용자가 선택한 기간을 동일 일수의 전반·후반으로 비교합니다. 홀수 기간의 가운데 하루는
증가량/증가율 계산에서만 제외하고 전체 기간 그래프·목록에는 포함합니다. 기저 0건의 %는 N/A이며 초기 관찰로 표시합니다.
기본값은 후반 5건·전반 3건·증가 3건·50%이고 완화/보수 프로필을 함께 확인합니다. 이 방식과 값은 문헌 검증값이 아닌 잠정 운영 선택입니다.

현재 검색 사전과 같은 식의, 요청 기간을 덮는 complete 실행을 우선합니다. 비교에 동일 문서유형·사람의 제외 기록·맥락 필터를 적용합니다.
리뷰/preprint/정정/철회의 메타데이터·제목 표기를 제외하는 기본 옵션은 해당 유형을 완전하게 탐지했다는 보장이 아닙니다.
제목·초록에 균일한 규칙을 적용하며 공개 원문을 확보한 일부 문헌에만 필터상 이점을 주지 않습니다.
상세 날짜 부족·부분 수집·미수집 구간은 판정을 보류합니다. 원본 논문/스냅샷 조건·선정 판단과 이유를 저장해 검토할 수 있습니다.
‘선정’ 기록은 사용자의 후속 검토 대상 선택이며 검증된 hot·위해성 판정이 아닙니다.

원래 조회 대상의 문헌에서 얻은 후보와 별도로, emerging contaminants/environmental pollutants/contaminants of emerging concern의
고정 제목·초록 검색을 범위가 있는 환경 코퍼스로 기록합니다. 기본 2페이지의 편향된 표본에서 정규식 명칭을 제안하므로
전체 화학물질의 발견·검증된 NER/recall·전 분야 순위로 해석하지 않습니다. PubChem 단일 명칭 매핑도 과학적 식별 검토를 대신하지 않습니다.
