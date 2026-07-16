# HARDENING LOG — statwise (그룹 비교 통계 자동 선택기)

이 파일은 배포 전, 도구를 무인(無人)으로 **깊이 개선하고 하드닝**한 기록입니다.
먼저 임상·제약 연구자가 실제로 필요로 하는 기능을 새로 구현(모두 표준 라이브러리)하고,
그 다음 Agent 도구로 **독립적인 적대적 검토자 패널을 병렬로** 여러 라운드 소환해 각자
우선순위가 매겨진 구체적·재현가능한 결함 목록(파일:줄, 깨뜨리는 입력)을 받았습니다.
매 라운드마다 제작자(메인 루프)가 모든 material 결함을 고치고 `python3 -m pytest`를
초록으로 되돌린 뒤 다음 라운드를 시작했습니다.

검증 기준선: 모든 p값·통계량을 SciPy 1.17 / statsmodels / (Welch-ANOVA는 pingouin·R
`oneway.test`)와 대조 — 정확 순열분포·근사분포 모두 ≤1e-9 수준 일치.

---

## 2026-07-16 — 깊은 기능 개선 (v0.1.0 → v0.2.0)

기존 도구는 "독립 2그룹/다그룹 자동선택 + 효과크기 + 논문문장"까지 잘 만들어져 있었으나,
임상 연구에서 **가장 흔한 설계 중 하나인 대응표본(전/후)** 을 지원하지 않았고, 소표본에서
근사 p값만 제공했으며, 정규지만 이분산인 다그룹을 굳이 순위검정으로 떨어뜨렸습니다. 아래를
모두 구현하고 테스트/문서를 갱신했습니다(테스트 54 → 112).

- **대응표본(paired / repeated-measures) 분석** — `--paired`(+ long은 `--id`, wide는 2개 열).
  차이값(a−b)의 정규성으로 **대응 t-검정 vs Wilcoxon 부호순위검정**을 자동 선택.
  효과크기는 **Cohen's d_z(95% CI)** / **matched rank-biserial r**. 새 모듈 `paired.py`,
  `dataio.load_paired_long`(대상 id로 짝짓기, 미짝·중복 id 경고) / `load_paired_wide`
  (행 단위 매칭). 새 예제 `examples/isi_pre_post_paired.csv`.
- **소표본 정확(exact) p값** — 동점이 없고 표본이 작으면 Mann-Whitney U와 Wilcoxon
  부호순위의 **정확 순열분포**를 DP로 계산(가우스 이항계수 재귀 / 부분합 재귀), SciPy
  `method='exact'`와 일치. 그렇지 않으면 동점·연속성 보정 정규근사로 자동 폴백하고 근거를
  `reason`에 표기. 새 모듈 `exact.py`.
- **Welch's ANOVA + 쌍별 Welch 사후검정** — 3그룹↑에서 정규지만 이분산이면 Kruskal이 아니라
  Welch-ANOVA를 선택(순위검정으로 강등하지 않음). `tests_stat.welch_anova`.
- **다중비교 보정 선택** — `--correction holm|bh` (Holm 기본, Benjamini-Hochberg=FDR 추가).
- **JSON 출력** — `--format json`, 안정적 스키마(`statwise/analysis/1`), NaN/Inf는 `null`로
  안전 직렬화. `report.render_json` / `result_to_dict`.
- **지저분한 실제 임상 CSV 견고화** — 인코딩 자동 폴백(utf-8-sig → cp949(한글 엑셀) → latin-1,
  비-UTF8 시 경고), 구분자 자동 감지(`,` `;` tab `|`)와 `--delimiter` 강제 지정, `parse_float`가
  따옴표·`%`·명확한 천단위 쉼표를 허용.

---

## 2026-07-16 — 라운드 1 (3인 병렬 패널: 정확성 / 엣지케이스·견고성 / 문서정직성·테스트품질)

**패널 구성.** Agent 도구로 3명의 독립 검토자를 병렬 소환: (1) 정확성 — SciPy/statsmodels/
pingouin을 오라클로 모든 신규 통계를 제1원리에서 재계산(수백~수천 무작위 케이스), (2)
엣지케이스/견고성 — 약 40종의 악성/지저분 입력으로 CLI·로더를 공격, (3) 문서 정직성 +
테스트 품질 — README/사용법/실행.command의 모든 명령·수치 재현, 참조값이 실제 라이브러리
출력인지 재확인.

**패널이 확인한 정상 항목(결함 0).** 정확 MWU/부호순위 순열분포(전 크기 전수 일치), 대응
t, Welch-ANOVA(F·df2·p), Holm/BH 보정(각 2000+ 케이스), 결정 트리 분기, 효과크기 부호,
문서의 SciPy 일치 주장·옵션표·"101 tests"·"의존성 0" 주장 — 모두 사실로 확인.

**고친 결함.**
- **[HIGH] 유럽식 소수점 쉼표가 조용히 왜곡됨** (`dataio.parse_float`). 천단위 쉼표 제거
  로직이 `"1,5"`(=1.5)를 `15.0`으로, `"12,34"`를 `1234.0`으로 만들어 **경고 없이 10~100배
  틀린 값**을 그럴듯하게 출력. → `parse_float`를 엄격한 정규식(`re.ASCII`)으로 재작성:
  명확한 US 천단위 그룹(`1,234` / `1,234.56`)만 허용하고, 모호한 유럽식 쉼표·`inf`/`nan`·
  밑줄·전각 숫자는 **추정하지 않고 None으로 거부**(왜곡보다 드롭이 안전). 회귀 테스트 추가.
- **[HIGH/MED] `inf`/`-inf` 셀이 실수로 수용됨** → 분석 전체를 오염. → 비유한값을
  `parse_float`에서 거부하고, 프로그래매틱 API(`analyze`/`analyze_paired`)에도 `_finite`
  가드를 추가해 NaN/Inf 입력 시 명확한 ValueError.
- **[MED] 두 그룹이 모두 분산 0이고 n>5000일 때 `ZeroDivisionError`(raw traceback)**.
  n>5000이면 정규성 검정을 건너뛰고 정규로 가정 → Levene NaN → Welch t가 분산 0에서
  0으로 나눔. → `students_t`/`welch_t`에 SE==0 가드를 추가해 명확한 ValueError로 처리(CLI는
  종료코드 2). 회귀 테스트 추가.
- **[MED] 논문용 문장이 `--correction`과 무관하게 "Holm-corrected"로 하드코딩** (`report._sentence`).
  BH를 골라도 [5] 헤더는 "Benjamini-Hochberg"인데 복사용 문장은 "Holm-corrected"라고 적혀
  잘못된 방법 기술이 원고로 들어감. → 문장이 실제 보정법을 반영하도록 수정. 문장 단위 테스트 추가.
- **[LOW] Wilcoxon 근사에서 `W+ == W-`(완전 대칭)일 때 불필요한 연속성 보정** → z≠0, p<1로
  SciPy(p=1.0)와 불일치. → `sign(W−μ)` 기반으로 고쳐 균형 케이스는 z=0. 3000개 무작위(균형
  60건 포함) 재검증 전수 일치.
- **[LOW] 테스트 품질**: JSON NaN-safety 테스트가 사실상 항진명제(항상 통과) → `parse_constant`로
  실제 가드하도록 강화. exact→근사 크기 경계, `--correction` 문장 반영 회귀 테스트 추가.
- **[minor] README 예시의 오래된 df 표기**(`df=30.000`, `t(30.0)`) → 현재 출력(`df=30`, `t(30)`)에 맞춤.

**결과.** `python3 -m pytest` → **112 passed**. 모든 라운드-1 material 결함 수정 완료.

---

## 2026-07-16 — 라운드 2 (병렬 패널: 수정 검증·회귀 / 임상 타당성·미비 기능)

**패널 구성.** (1) 수정-검증 검토자 — 라운드-1의 5개 수정(엄격 parse_float, Wilcoxon 균형
보정, t-검정 SE=0 가드, `_finite` 가드, 문장 보정 문구)이 정상값을 잘못 거부하거나 회귀를
일으키지 않는지 SciPy로 재검증. (2) 임상 생물통계학자 — 결정 트리 기본값의 타당성과
가장 가치 있는 미비 기능 우선순위화.

**수정-검증 결론: 전부 clean(회귀 0).** parse_float는 정상 임상값(음수·지수표기·`1,234.56`·
따옴표·`%` 등)을 모두 통과시키고 모호/비유한 토큰만 거부. Wilcoxon 균형(W+==W-) 보정은
4000 케이스에서 SciPy와 최대 3.3e-16 일치. SE=0 가드는 진짜 이분산 0에서만 발동하며 CLI가
종료코드 2로 깔끔히 처리. Welch-ANOVA η²의 `ss_total==0` 분기는 도달 불가로 확인.

**고친 결함(임상 타당성 패널).**
- **[HIGH] 대응표본 방향이 CSV 행 순서로 결정되는데 문서는 "pre−post"라고 표기** →
  행 순서만 바뀌어도 효과의 **부호가 조용히 뒤집힘**(dz +4.24 ↔ −4.24). → (1) 출력에
  **비교 방향을 명시**(`비교 방향 direction: 차이 = (post − pre)`, 평균차·위치차 라벨에도
  `(A − B)`), (2) `--baseline 기준조건` 옵션 추가로 빼지는 기준을 고정(행 순서 무관하게
  부호 재현), (3) README/사용법의 "pre−post" 주장을 정정. 회귀 테스트 추가.
- **[MED] Welch's ANOVA의 η²가 등분산 가정 pooled SS에서 계산돼 모형 불일치인데 무경고** →
  출력에 **명시적 경고**를 추가하고 README "한계"에 문서화. 테스트로 경고 존재를 강제.
- **[minor] n<3을 "assumed non-normal"로 오표기**(정규성은 판정 불가일 뿐 비정규가 아님) →
  "normality unknown (defaulting to non-parametric, conservative)"로 정정. 소표본 Shapiro
  검정력 한계도 README에 명시.

**추가한 고가치 기능(임상 패널 우선순위 #1, 표준 라이브러리).**
- **비모수 검정의 Hodges-Lehmann 위치차 추정값 + 분포무관 신뢰구간** (새 모듈 `location.py`).
  지금까지 Mann-Whitney/Wilcoxon 분기는 위치차에 대한 **신뢰구간이 전혀 없었고**(순위
  효과크기만) — CONSORT/ICH-E9 보고에 필요한 "추정값+CI"를 채웁니다. 독립표본은 쌍별 차이의
  중앙값, 대응표본은 Walsh 평균의 중앙값; CI 순서통계량 인덱스는 정확 순위분포(소표본)/
  정규근사(대표본)에서 산출. 텍스트·JSON·논문 문장에 모두 통합. **검증**: 점추정값은 중앙값과
  정확 일치, CI는 **검정-역산 일관성**(CI가 0을 배제 ⟺ 정확검정이 α에서 기각)을 독립·대응
  각각 60/163 무작위 케이스에서 전수 통과.

**결과.** `python3 -m pytest` → **123 passed** (54 → 123). 라운드-2 material 결함 모두 수정,
고가치 기능 1건 추가.
