# HARDENING & 개선 로그 — pubgap

이 문서는 pubgap 도구를 **더 유능하고 견고하게** 만들기 위해 수행한 기능 개선과
적대적(adversarial) 하드닝 라운드를 날짜별로 기록합니다.

---

## 2026-07-16 — 심층 개선 + 하드닝 라운드 1

### A. 기능 개선 / 확장 (임상·제약 연구자 관점의 실제 니즈)

**통계적 엄밀성 (핵심 강화)**
- **BH-FDR q-value**: 공백 탐색은 상위 주제쌍 수십 개를 *동시에* 검정하므로 raw p 만
  보고하면 다중검정 함정에 빠진다. Benjamini–Hochberg 절차(`benjamini_hochberg`)를
  순수 표준라이브러리로 구현하고, **검정한 모든 후보쌍**(기대≥`min_expected`)에 대해
  q-value 를 계산해(필터 이전에) 각 공백에 함께 보고. `--gap-max-q` 로 "통계적으로 유의한
  공백만" 필터 가능. 리포트/JSON/CSV 모두에 q 컬럼 추가.
- **Mann–Kendall 단조추세 검정**(`mann_kendall`/`trend_test`): 발행량이 시간에 따라
  *유의하게* 증가/감소하는지를 초기/최근 2분할 비율보다 견고하게 판정. 동률 보정
  분산, 연속성 보정 z, tau-b, 양측 p. 빠진 해를 0 으로 채운 조밀 시계열 사용.
- **CAGR**(연평균 성장률): `growth_summary` 에 추가. 양끝 해 잡음에 민감함을 문서화하고
  유의성 판단은 Mann–Kendall 에 위임.

**입력 형식 / 실세계 견고성**
- **MEDLINE/NBIB 파서**(`parse_medline_nbib`): PubMed 웹의 *Save → PubMed format* 과
  인용 관리자 내보내기 포맷을 직접 분석. MH 별표(descriptor `*Sleep`, qualifier
  `Heart Rate/*physiology`)로 대표주제 판별, OT 키워드, 6칸 이어짐, 다양한 연도 태그.
- **자동 형식 판별 + gzip + 인코딩 관대화**(`load_articles`/`decode_bytes`/`detect_format`):
  XML/NBIB 를 내용으로 판별, `.gz` 자동 해제, UTF-8→latin-1 폴백, BOM 처리.
- **PMID 중복 제거**(`dedup_articles`): efetch 배치 병합/재수출로 생기는 중복 논문 제거.

**MeSH 처리 심화**
- Article 에 `mesh_major`(대표주제), `keywords`(저자 키워드) 필드 추가(XML/NBIB 공통).
- `--major-topics-only`: 별표 대표주제만으로 분석(정밀도↑).
- `--include-keywords`: MeSH 미부여 최신 논문 대비, 저자 키워드를 대소문자 무시로 병합.

**출력 / CLI**
- `--format {md,json,csv}`(`--json` 은 구버전 별칭). CSV 는 엑셀 한글 대비 UTF-8 BOM.
- `--min-year/--max-year` 연도 범위 필터.
- 효율적 공동출현 집계(`_cooccurrence`): 한 번의 스캔으로 상위 주제쌍 관측수 계산.

### B. 하드닝 라운드 1 — 병렬 리뷰어 4종(정확성/엣지케이스/문서정직성/테스트·보안)

발견 및 **수정**한 사항:

- **[HIGH] `hypergeom_lower_tail` 오버플로**: 큰 N(수천 편)에서 `comb()` 정수가 float
  범위를 넘어 `OverflowError`(원시 트레이스백). → `math.lgamma` 기반 log-공간 합산으로
  재작성, 작은 표본(N≤60)은 정확 정수 경로 유지, 꼬리 끝은 정확히 1.0. scipy 대비
  N=8000 까지 오차 <2e-11 확인. 회귀 테스트 추가.
- **[MEDIUM] `term_trends` 성능**: 주제마다 전체 논문을 훑어 O(주제수×논문수) — 수천
  주제에서 분 단위 지연. → 역색인 단일 스캔으로 재작성(N=4000/8만 주제: 120s+ → 0.07s).
  등가성·성능 테스트 추가.
- **[LOW] XML billion-laughs**: 내부 엔티티 선언(`<!ENTITY>`) 방어 가드 추가. 정상
  efetch 의 외부 DTD DOCTYPE 은 그대로 통과함을 테스트로 보장.
- **[LOW] NBIB 탭 구분 변종**: `TAG<TAB>value` 를 데이터 손실 없이 파싱하도록 관대화.
- **[방어] `build_report`/렌더가 예외 보호 밖**: CLI 에서 분석/렌더도 try/except 로 감싸
  예기치 못한 오류 시 원시 트레이스백 대신 rc 3.
- **[일관성] `top_journals` 결정론화**: `_ranked` 기반 (편수, 이름) 정렬로 통일.
- **[문서 정직성] scipy 대조 주장**: 재현 가능한 선택적 테스트(`test_scipy_crosscheck.py`,
  scipy 없으면 skip)로 뒷받침하고 README 문구를 정확화. README 공백 예시의 누락 행 보강.
- **[테스트 보강]** Mann–Kendall 정확 p/z/tau(동률 포함), BH 비단조 손계산, NBIB
  descriptor 대표 승격, 대표주제 한정 실제 효과, 연도 필터 경계, 디코드 폴백 등.

정정: 정확성 리뷰어는 세 핵심 통계(BH·MK·초기하)가 scipy 와 수치 일치함을 재확인(무결).

테스트: 46 → **117 통과**(전부 오프라인, 네트워크/PII 누출 없음).
