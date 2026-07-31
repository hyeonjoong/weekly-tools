# table1 — 기저 특성표(Table 1) 생성기

임상 CSV(또는 **엑셀 .xlsx**) 한 장을 넣으면 **출판용 "표 1"(baseline characteristics table)** 을 자동으로 만들어 줍니다. 변수마다 연속형/범주형을 스스로 판별해 알맞은 요약(평균±SD 또는 중앙값[IQR], n(%))과 검정을 고르고, 두 군 비교의 **표준화 평균차(SMD)**, 선택적으로 **군간 차이(95% CI)** 와 **다중비교 보정 p값**, **IPTW/성향점수 가중표(가중 SMD·Kish ESS)**, 그리고 **결측**까지 정리해 Markdown·CSV·TSV·JSON·**HTML**로 출력합니다. 외부 의존성 0 (표준 라이브러리만).

## 목적 / Why this exists

**한국어** — 거의 모든 임상·약학 논문(그리고 BELL-001 SERENE 같은 임상시험)은 "표 1: 기저 특성"으로 시작합니다. 이 표를 손으로 만들려면 변수마다 정규성을 확인해 평균/중앙값을 고르고, 이진·다범주 변수는 카이제곱·Fisher를 따로 돌리고, 군간 균형(SMD)과 결측을 표로 옮기는 반복 작업이 필요합니다. `table1`은 CSV와 "군(group) 열" 하나만 주면 이 과정을 한 번에 끝내, 붙여넣으면 되는 표를 만들어 줍니다. 정규성·등분산을 점검해 검정을 자동 선택하므로 검정 오용을 줄이고, SMD로 무작위배정/성향점수 매칭의 균형까지 한눈에 보여 줍니다.

**English** — Almost every clinical/pharma paper (and trials like BELL-001 SERENE) opens with a "Table 1: baseline characteristics." Building it by hand means, per variable, checking normality to pick mean vs. median, running chi-square/Fisher for categoricals, and transcribing between-group balance (SMD) and missingness. `table1` does all of that from a CSV and a single grouping column, emitting a paste-ready table. It checks normality/equal-variance to select the right test (reducing test misuse) and reports SMDs so covariate balance after randomization or propensity matching is visible at a glance.

Serves a clinical/pharma researcher who routinely writes up trial and cohort data (BELL-001 sleep device: EEG/HRV/respiration/ISI; WowFit audiology). Reach for it whenever you have a tidy CSV with a group column and want the baseline table done correctly and reproducibly.

## 설치 / Install

```bash
cd ~/Downloads/02_프로젝트/깃헙/table1-기저특성표
python3 -m pip install -e .      # 'table1' 명령이 등록됩니다(PATH에 따라 전역 사용)
```

설치 없이도 폴더 안에서 `python3 -m table1.cli ...` 로 바로 실행할 수 있고, **`실행.command` 더블클릭**으로 예제를 볼 수 있습니다.

## 사용법 / Usage

```bash
# 모든 변수 자동 판별, arm(군)별로 비교
table1 examples/serene_baseline.csv --group arm

# 특정 변수만 골라서 (F/M 같은 문자열 열은 지정 없이도 범주형으로 자동 판별)
table1 examples/serene_baseline.csv --group arm --vars age,sex,isi,bmi

# 원고에 붙일 CSV로 저장
table1 examples/serene_baseline.csv --group arm --format csv -o table1.csv

# 영문 저널 제출용: 영어 라벨 + 변수 이름/단위 지정 + (CONSORT) p값 숨김
table1 examples/serene_baseline.csv --group arm --lang en --no-pvalue \
  --labels rmssd_ms="RMSSD (ms)" ahi="AHI (events/h)" isi="ISI"

# 비교(관찰) 연구용: 군간 차이(95% CI) 열 + 다중비교 보정(Holm) p값
table1 examples/serene_baseline.csv --group arm --effect --padjust holm

# Word/저널 제출 시스템에 붙여넣을 HTML 표로 저장
table1 examples/serene_baseline.csv --group arm --effect --format html -o table1.html

# 군(group) 없이 — 전체 코호트 기술통계표(단일군 시험·코호트 기술용)
table1 examples/serene_baseline.csv --vars age,sex,bmi,isi

# 성향점수(IPTW) 가중표 — 가중 요약 + 가중 SMD + 군별 Kish 유효표본수(ESS)
table1 examples/psm_weighted.csv --group cohort --weights iptw --vars age,sex,bmi,copd

# 왜곡된 변수를 분석자가 직접 지정(사전검정 대신) → 항상 중앙값[IQR] + 순위검정
table1 examples/serene_baseline.csv --group arm --nonnormal ahi,rmssd_ms
```

아래 두 줄은 **내 파일 이름으로 바꿔 쓰는 예시**입니다(저장소에 해당 파일은 없습니다).

```bash
# 엑셀(.xlsx) 파일을 바로 — CSV로 다시 저장할 필요 없음 (--sheet 로 시트 선택)
table1 내파일.xlsx --group arm --sheet 기저

# 정수 코드 열(예: NYHA 1~4, ISI 중증도 밴드)은 범주형으로 강제
#   — 기본값(--cat-max-levels 2)에서는 0/1 이진 플래그만 자동으로 범주형이 됩니다
table1 내데이터.csv --group arm --categorical nyha

### 실제 출력 예 (examples/serene_baseline.csv, 합성 데이터)

```
## 표 1. 기저 특성 (Table 1. Baseline characteristics)

| 특성 (Characteristic) | 전체 (N=48) | device (n=24) | sham (n=24) | p값 | SMD | 검정 |
|---|---|---|---|---|---|---|
| age — 평균(SD) | 53.6 (9.6) | 55.7 (8.2) | 51.4 (10.5) | 0.116 | 0.463 | Student t |
| sex — n(%) · 결측 1 (device 1) |  |  |  | 0.106 | 0.486 | Pearson χ² |
|  F | 22 (46.8) | 8 (34.8) | 14 (58.3) |  |  |  |
|  M | 25 (53.2) | 15 (65.2) | 10 (41.7) |  |  |  |
| bmi — 중앙값[IQR] · 결측 1 (device 1) | 26.1 [24.4, 27.7] | 26.2 [25.6, 27.5] | 25.6 [23.8, 28.0] | 0.431 | 0.242 | Mann-Whitney U |
| isi — 평균(SD) | 18.7 (3.2) | 19.0 (2.8) | 18.3 (3.5) | 0.431 | 0.229 | Student t |
| rmssd_ms — 평균(SD) · 결측 4 (device 2, sham 2) | 26.4 (8.9) | 25.0 (8.3) | 27.8 (9.5) | 0.302 | 0.315 | Student t |
| ahi — 평균(SD) | 6.5 (2.7) | 6.3 (3.1) | 6.7 (2.3) | 0.628 | 0.141 | Student t |
| site — n(%) |  |  |  | 0.776 | 0.207 | Pearson χ² |
|  A | 16 (33.3) | 8 (33.3) | 8 (33.3) |  |  |  |
|  B | 18 (37.5) | 10 (41.7) | 8 (33.3) |  |  |  |
|  C | 14 (29.2) | 6 (25.0) | 8 (33.3) |  |  |  |
| chronic — n(%) |  |  |  | 0.731 | 0.099 | Pearson χ² |
|  0 | 11 (22.9) | 5 (20.8) | 6 (25.0) |  |  |  |
|  1 | 37 (77.1) | 19 (79.2) | 18 (75.0) |  |  |  |
```

> ID 열(`subject_id`)은 고유값이 너무 많아 자동으로 제외됩니다(경고로 안내). 각 통계는 SciPy와 대조해 검증했습니다: 위 age의 p=0.116은 SciPy Student t와, 범주형 p·SMD는 각각 SciPy χ²/Fisher·numpy 다변량 SMD와 일치합니다.

### IPTW(성향점수 가중) 표 예 (examples/psm_weighted.csv, 합성 데이터)

관찰연구에서 `device` 군이 더 고령·고BMI로 치우친(교란된) 코호트입니다. **가중 전** age의 SMD는 0.575로 크게 불균형하지만, `--weights iptw` 로 가중하면 0.054까지 내려가 균형이 맞았음을 보여 줍니다 — 이것이 IPTW 후 "표 1"을 다시 보고해야 하는 이유입니다.

```bash
table1 examples/psm_weighted.csv --group cohort --weights iptw --vars age,sex,bmi,copd
```

```
| 특성 (Characteristic) | 전체 (N=64, ESS=56.0) | device (n=36, ESS=30.7) | control (n=28, ESS=25.4) | SMD |
|---|---|---|---|---|
| age — 평균(SD) | 56.2 (10.4) | 56.5 (10.8) | 55.9 (10.2) | 0.054 |
| sex — n(%) |  |  |  | 0.001 |
|  F | 22.2 (34.9) | 11.7 (34.9) | 10.4 (34.8) |  |
|  M | 41.5 (65.1) | 21.9 (65.1) | 19.5 (65.2) |  |
| bmi — 평균(SD) | 26.6 (3.6) | 26.4 (4.2) | 26.7 (2.8) | 0.079 |
| copd — n(%) |  |  |  | 0.038 |
|  0 | 53.9 (84.6) | 28.3 (84.0) | 25.6 (85.4) |  |
|  1 | 9.8 (15.4) | 5.4 (16.0) | 4.4 (14.6) |  |
```

비교용 — **가중 없이** 같은 자료(`--no-pvalue`)를 보면 age SMD=0.575, bmi SMD=0.202로 두 군이 교란되어 있습니다.

> 가중표에서 셀 값은 **가중 n(가중 %)** 이라 정수가 아닙니다. 머리글의 `n=` 은 실제 환자 수, `ESS=` 는 Kish 유효표본수((Σw)²/Σw²)로 "이 가중 군이 실제로 담고 있는 정보량"입니다. 가중 시 **p값·다중비교·차이(95% CI) 열은 생략**됩니다(아래 방법론 노트 참조).

### 주요 옵션

| 옵션 | 뜻 |
|------|-----|
| `--group, -g` | 군 라벨 열. **생략하면 전체 코호트 기술통계표**(단일 '전체' 열, 검정·p·SMD·차이·보정 없음) — 단일군 시험·코호트 기술·파일럿 연구용 |
| `--vars` | 요약할 변수(쉼표 구분). 미지정 시 군 열 제외 전체 |
| `--weights, -w 열` | **IPTW/성향점수/설문 가중표**. 가중 평균(가중 SD)·가중 중앙값[가중 IQR]·**가중 n(가중 %)** 요약과 **가중 SMD**(Austin & Stuart 2015), 군별 **Kish ESS**((Σw)²/Σw²)를 머리글에 표시. 가중치가 결측·0 이하·비수치인 행은 제외하고 경고. **p값·다중비교·차이(95% CI)는 생략**(설계기반 분산 필요) |
| `--nonnormal 열들` | 해당 열을 **분석자가 비정규로 지정** → Shapiro–Wilk 사전검정을 건너뛰고 항상 중앙값[IQR] + Mann–Whitney/Kruskal. `--test-cont` 보다 우선(더 구체적인 지정). `--display` 는 표기만 별도 제어 |
| `--sheet 이름\|번호` | **엑셀(.xlsx)** 입력에서 읽을 시트(이름 또는 1부터 시작하는 번호, 기본 첫 시트) |
| `--continuous` / `--categorical` | 특정 열의 형(型)을 강제 |
| `--cat-max-levels N` | 수치형이라도 서로 다른 값이 N개 이하이면 범주형(기본 2) |
| `--max-levels N` | 자동 범주형의 고유값이 N개 초과면 ID로 보고 제외(기본 20) |
| `--display auto\|mean\|median\|both` | 연속형 표기(기본 auto = 정규성 따라) |
| `--test-cont auto\|welch\|student\|nonparam` | 연속형 **검정** 선택: auto(사전검정, 기본)·welch(항상 Welch t)·student(항상 Student t)·nonparam(항상 Mann-Whitney/Kruskal). 분산 사전검정을 피하려면 `welch` 권장(Delacre 2017). **welch·student 는 2군 비교에만 적용**되며 ≥3군은 일원배치 ANOVA(비모수는 Kruskal) |
| `--pct col\|row` | 범주형 % 기준(기본 col = 그룹 내) |
| `--pct-decimals N` | 범주형 % 소수 자릿수(기본 1, 0~10) |
| `--binary-single` | 2수준(이진) 범주형을 한 줄로 축약(예: `sex = M — n(%)`) — 저널 관례. **md·csv·tsv·html 에 적용**(JSON은 구조화 데이터라 모든 수준 유지) |
| `--ref COL=수준` | 이진 축약 시 기준(참조) 수준 지정 → 표엔 반대 수준 표시(예: `--ref sex=F` → M 행) |
| `--alpha-norm A` | 정규성·등분산 판정 유의수준(기본 0.05). 검정 자동선택에 영향 |
| `--fisher` | 2×2 범주형에 항상 Fisher exact |
| `--missing-as-level` | 범주형 결측을 '(결측)' 수준으로 표시(검정 제외) |
| `--no-overall` | '전체' 열 숨김 |
| `--no-pvalue` | p값 열 숨김 — **무작위배정 임상시험(SERENE 등)** 은 CONSORT 권고상 기저 p값 대신 SMD로 균형 보고 |
| `--range` | 연속형 셀에 `(최소–최대)` 범위 추가 |
| `--effect` | **군간 차이(95% CI)** 열 추가 — 연속형은 평균차(모수 검정 시, Student는 합동·Welch는 비합동 SE로 t-기반 CI, **p값과 정합**)/**Hodges–Lehmann** 중앙값차(비모수, tie-보정 분포무관 CI), 이진 범주형은 표시된 **index 수준**(`M:`)의 **위험차(%p)** 를 **Newcombe** 점수구간으로(이 CI는 Fisher/χ²와 다른 방법이라 p와 어긋날 수 있음). 방향은 **첫 군 − 둘째 군**(양수면 첫 군이 큼). **2군 비교에만** 적용 — ≥3군이거나 가중(`--weights`) 표에서는 **열 자체가 생략**되고, 2군 표 안의 다범주형(3수준 이상) 행만 공란입니다 |
| `--padjust none\|bonferroni\|holm\|bh\|by` | 변수별 p값에 **다중비교 보정** 열 추가. bh=Benjamini–Hochberg(FDR), by=Benjamini–Yekutieli. 검정 불가 변수(p 없음)는 가족 크기에서 제외. **무작위배정 시험의 기저 p값 보정은 비권장**(CONSORT) — 비교/관찰 연구용 |
| `--lang ko\|en` | 표 라벨 **및 모든 주석·경고** 언어(기본 ko, `en` = 영문 저널 제출용) |
| `--labels COL=이름 …` | 변수 표시 이름/단위 지정(예: `--labels rmssd_ms="RMSSD (ms)"`) |
| `--decimals N` | 연속형 소수 자릿수(기본 1, 음수 불가) |
| `--delimiter D` | 입력 구분자 강제(한 글자, 미지정 시 자동 감지). 탭은 `--delimiter tab`(또는 `\t`), 세로줄은 `--delimiter '\|'`. **CSV 전용**(xlsx에 쓰면 오류) |
| 입력 `.xlsx` | **엑셀 파일을 그대로 입력**(확장자가 아니라 실제 파일 내용으로 판별). 공유문자열·서식있는 텍스트·수식 결과·불리언·**빈 셀이 생략된 희소 행**·**날짜 일련번호→`YYYY-MM-DD`**(1900/1904 기준 모두) 처리. 표준 라이브러리만 사용 |
| 입력 `-` | CSV 경로 대신 `-` 를 주면 **표준입력(stdin)** 에서 읽음(파이프라인용: `cut -d, -f2- data.csv \| table1 - -g arm`) |
| `--format md\|csv\|tsv\|json\|html` (`-f`), `-o\|--out 경로` | 출력 형식/파일. `html` = Word/저널 제출 시스템에 붙여넣을 HTML 조각(`<table>` + 범례·주석·경고, 데이터 셀 HTML 이스케이프, 표는 유효 XHTML로 파싱) |
| `--version` | 버전 출력 후 종료 |

## 방법론 노트 / Methods

- **연속형 요약·검정 선택**: 각 군(n≥3)에 Shapiro–Wilk 정규성 검정. 어느 군이라도 정규성 기각 → 중앙값[IQR] + 비모수 검정(2군 Mann–Whitney U, ≥3군 Kruskal–Wallis). 모두 정규 → 평균±SD + 모수 검정(2군은 Levene 등분산 점검 후 Student t / Welch t, ≥3군은 일원배치 ANOVA). ANOVA에서 Levene이 기각되면 해석 주의를 주석으로 표시합니다.
- **범주형 검정**: r×c 표에 Pearson χ²(연속성 보정 없음, `chi2_contingency(correction=False)` 기준). 2×2에서 기대빈도<5이거나 `--fisher` 지정 시 양측 Fisher exact.
- **SMD(두 군 전용)**: 연속형은 `|m₁−m₂| / √((s₁²+s₂²)/2)`, 이진형은 비율 기반, 다범주형은 **Yang & Dalton(2012)** 다변량 SMD. 관례상 |SMD|>0.1 을 불균형 신호로 봅니다.
- **군간 차이(95% CI, `--effect`, 두 군 전용)**: 방향은 **첫 군 − 둘째 군**(양수면 첫 군이 큼). **모수 연속형**은 보고한 t-검정과 **정합**하는 CI — Student이면 합동분산·자유도 n₁+n₂−2, Welch이면 비합동·Welch 자유도로 **p값과 같은 SE** 를 씁니다. **비모수 연속형**(Mann–Whitney)은 **Hodges–Lehmann 중앙값 이동량**(모든 쌍별 차 xᵢ−yⱼ의 중앙값 — 표시된 두 중앙값의 차와 다를 수 있음)과 tie-보정 정규근사(Moses) CI. **이진 범주형**은 표시된 **index 수준**(표에 `M:` 처럼 표기)의 **위험차(risk difference, %p)** 를 **Newcombe(1998) 점수구간**으로(영(0) 셀에서도 안정) — 단, 이 CI는 범주형 검정(Fisher/χ²)과 **다른 방법**이라 p값의 유의성과 어긋날 수 있습니다(정합은 모수 연속형에만 해당). 다범주형은 단일 스칼라 효과가 없어 공란(다변량 SMD가 균형을 요약). statsmodels(`CompareMeans.tconfint_diff`, `confint_proportions_2indep(method='newcomb')`)와 ≤1e-9 일치.
- **IPTW/성향점수 가중표(`--weights`)**: 가중치는 **신뢰도(reliability) 가중**으로 해석합니다 — 가중치 전체에 상수를 곱해도 모든 추정치가 변하지 않으며(척도 불변), 정수 가중치를 "그만큼 복제"로 보지 **않습니다**(IPTW/설문 가중의 표준 해석). 가중 분산은 Austin & Stuart(2015)가 가중 SMD에 쓰는 불편추정량 `s²_w = (Σw / ((Σw)² − Σw²)) · Σ wᵢ(xᵢ − m_w)²` 이며, 가중치가 모두 같으면 **정확히** 일반 표본분산(ddof=1)으로 환원됩니다. 가중 분위수(중앙값·IQR)는 가중치를 합이 n이 되도록 정규화한 뒤 정렬된 i번째 점을 `pᵢ = (cumsum(ŵ)ᵢ − (ŵᵢ+1)/2)/(n−1)` 위치에 두고 선형보간하는 **type-7 일반화**로, 가중치가 모두 같으면 비가중 표(numpy/R 기본 type 7)와 **정확히 일치**합니다(이 불변식은 테스트로 고정). 다만 이 분위수 정의는 특정 외부 패키지(R `Hmisc` 등)와의 일치를 보장하지 않는 **문서화된 관례**입니다. 가중 SMD는 연속형 `|m_w1 − m_w2| / √((s²_w1 + s²_w2)/2)`, 범주형은 가중 비율에 Yang–Dalton 다변량 SMD를 적용합니다(2수준이면 이진 공식으로 환원). 머리글의 **ESS**는 Kish 유효표본수 `(Σw)²/Σw²`(가중치가 같으면 n과 동일, 불균등할수록 감소)이며, '전체' 열의 ESS는 **전 행을 합쳐 계산**한 값이지 군별 ESS의 합이 아닙니다. **가중 시 p값·다중비교 보정·차이(95% CI)는 의도적으로 생략합니다**: 타당한 가중 p값/CI는 설계기반(robust·Rao–Scott) 분산이 필요해 이 도구의 범위 밖이며, 비가중 p값을 가중 요약 옆에 두면 정합이 깨집니다. 성향점수 문헌(Austin & Stuart 2015)도 균형 판단에는 p값이 아니라 **가중 SMD**를 권고합니다. 표기(평균/중앙값) 선택을 위한 정규성 검정은 **비가중 원자료**로 수행합니다. 가중치가 결측·비수치·0 이하·비유한인 행은 **모든 변수에서 일관되게** 제외하고 경고합니다.
- **엑셀(.xlsx) 입력**: 확장자가 아니라 **컨테이너 내용**으로 판별하므로 이름이 `.txt` 인 워크북도 읽고, 반대로 `.xlsx` 라고 이름만 바꾼 CSV도 CSV로 올바르게 읽습니다. 공유문자열(서식있는 텍스트의 여러 조각을 이어붙임)·인라인 문자열·수식의 캐시된 결과·불리언·오류 셀을 지원하며, **빈 셀이 통째로 생략된 희소 행**은 `r="C7"` 참조로 자리를 잡아 열이 밀리지 않게 합니다(위치 기준으로 읽으면 빈칸 뒤 값이 조용히 한 칸씩 밀려 표 전체가 오염됩니다). **날짜 서식**이 걸린 일련번호는 `YYYY-MM-DD`(시각이 있으면 ISO 일시)로 변환하며 1900/1904 기준과 엑셀의 가상 날짜 `1900-02-29`를 모두 반영합니다. 통화 서식(`#,##0"m"`)의 `m` 처럼 **따옴표·대괄호 안 문자는 날짜 토큰으로 보지 않습니다**. .xlsx는 zip이므로 압축 해제량을 실제 바이트로 세어 상한(512MB)을 넘으면 중단합니다(zip bomb 방어). 구형 `.xls`(BIFF)는 지원하지 않지만 **OLE2 시그니처로 탐지해** `.xlsx`/`CSV UTF-8`로 다시 저장하라는 정확한 안내를 냅니다(인코딩 탓으로 오인시키지 않음). 암호로 보호되었거나 지원하지 않는 압축 방식으로 저장된 워크북도 각각 전용 안내를 냅니다.
- **수치 열에 섞인 비수치 셀(검열값·단위)**: 비결측 셀의 **80% 이상이 숫자**이고 그 숫자들이 측정값처럼 보이면(정수 코드가 아니면) 그 열을 **연속형으로 처리**하고, 숫자가 아닌 셀(예: `>100`)은 요약에서 제외한 뒤 **주석과 경고로 알립니다**. 검열된 검사값 하나 때문에 연속형 지표가 "환자마다 한 수준"인 범주형으로 바뀌어 무의미한 χ²가 붙는 사고를 막기 위함입니다. 반대로 정수 코드(NYHA 1–4 + '기타')처럼 **소수 지지집합의 정수**는 승격하지 않고 범주형으로 둡니다. 모든 셀에 단위가 붙어(`72 kg`, `92%`) 숫자가 하나도 파싱되지 않으면 범주형으로 두되, **"수치처럼 보인다"는 경고**로 `--continuous` 사용을 안내합니다. 판정 결과는 어느 방향이든 항상 경고로 밝히므로 조용히 바뀌는 일은 없습니다.
- **요약(합계) 행 탐지**: `합계`·`총계`·`평균`·`Total`·`Sum` 같은 값이 든 행은 엑셀에서 붙여넣은 요약 행일 가능성이 높아 **경고**합니다(자동 삭제는 하지 않음 — 실제 자료를 조용히 지우는 편이 더 위험). 군 열이 있으면 대개 군 값이 비어 자동 제외되지만, 전체 코호트 표에서는 N과 요약을 왜곡할 수 있습니다.
- **열 이름 오타**: `--continuous`·`--categorical`·`--nonnormal`·`--labels`·`--ref` 에 자료에 없는 열을 적으면 **경고**합니다(`--vars`·`--group`·`--weights` 는 하드 오류). 오타 때문에 보고되는 통계가 조용히 바뀌지 않도록 합니다.
- **전체 코호트 기술통계(`--group` 생략)**: 군 열 없이 실행하면 모든 행을 하나의 '전체(Overall)' 열로 요약합니다(연속형 평균±SD/중앙값[IQR], 범주형 n(%), 결측). 비교가 없으므로 검정·p·SMD·차이·다중비교 열은 모두 생략됩니다 — 단일군(single-arm)·파일럿·코호트 기술에 적합.
- **다중비교 보정(`--padjust`)**: 변수별 1차 p값(수준별 아님)에 Bonferroni / Holm(단계적 하강) / Benjamini–Hochberg(FDR, 단계적 상승) / Benjamini–Yekutieli(임의 종속하 FDR)를 적용. 검정 불가 변수는 가족 크기에서 제외. `statsmodels.stats.multitest.multipletests` 와 일치. 무작위배정 시험의 기저 p값 보정은 CONSORT상 비권장이므로 기본값은 `none`.
- **결측**: 연속형은 결측·비수치 셀(및 inf/-inf 등 비유한 수치)을 제외해 요약하고 결측 수를 표기하며, 두 군 이상일 때는 **군별 결측 분포**를 함께 표시합니다(`· 결측 4 (device 2, sham 2)`) — 무작위배정 시험에서 차등 결측(differential missingness)을 한눈에 볼 수 있습니다. 범주형 %는 **비결측(non-missing) 기준**이며, 결측은 기본적으로 수준에서 제외합니다(`--missing-as-level`로 표시 가능). 군 값이 결측인 행은 전체에서 제외하고 경고합니다. 숫자로 해석되지 않는 셀(예: `>100`·`12 kg`·`45%`·유럽식 `1,5`)은 **단순 결측이 아니라 "해석 불가"로 별도 주석**해, 검열(censored)·단위 포함 값이 평균에 조용히 반영되지 않도록 알립니다. 변수의 결측이 50%를 넘거나, 대소문자만 다른 그룹 라벨(예: `Device`/`device`)이 별개 군으로 잡히면 경고합니다.
- 분포 함수(정규/ t / F / χ²)와 Shapiro–Wilk, Fisher exact 열거는 모두 표준 라이브러리로 자체 구현했고, SciPy/numpy와 대조 검증했습니다: 분포함수·Student/Welch t·ANOVA·Fisher·χ²는 대체로 ≤1e-9(꼬리 확률까지) 일치하고, Shapiro–Wilk는 ~1e-8 수준(W 통계량은 ~1e-9)으로 일치합니다. **단, Mann–Whitney U·Kruskal–Wallis는 정규/χ² 점근(asymptotic) 근사를 쓰므로 SciPy의 `method='asymptotic'` 결과와 일치하며, 소표본에서 SciPy 기본값의 정확검정(exact)과는 다를 수 있습니다.** 이 대조값들은 오프라인 테스트로 고정해 두었습니다(`tests/test_tests_stat.py`, `tests/test_normality.py`, `tests/test_special.py`, `tests/test_smd.py`, 골든 스냅샷, 하드닝 회귀·속성 테스트 `tests/test_hardening_r1.py`~`r5.py`, 신규 기능 테스트 `tests/test_effects.py`·`tests/test_multiplicity.py`·`tests/test_new_features.py`·`tests/test_weights.py`·`tests/test_xlsx.py`·`tests/test_nonnormal_cli.py` 등, 400개 이상). 효과크기·다중비교 보정은 statsmodels와 대조 검증(≤1e-9).

## 한계 / Limitations

- 검정 자동 선택은 합리적 기본값이며, 만능이 아닙니다. 짝지은(대응) 설계, 시간-사건(생존) 분석은 대상이 아닙니다(각각 전용 도구 필요). 가중(IPTW/설문) 표는 `--weights` 로 **요약·SMD까지** 지원하지만, 가중 **추론**(p값·CI)은 설계기반 분산이 필요해 제공하지 않습니다.
- `--nonnormal` 은 해당 변수를 항상 비모수로 처리합니다. 사전검정(Shapiro–Wilk) 기반 자동 선택의 대안으로 권장되지만, 어떤 변수가 왜곡되었는지는 **분석자가 자료를 보고 판단**해야 합니다(관례상 사전 프로토콜에 명시).
- 정규성 판정은 표본이 매우 작으면(각 군 n<3) 불가하며, 이때 평균±SD로 표시하고 주석을 답니다. 대표본에서는 Shapiro가 사소한 이탈도 기각할 수 있어 `--display`로 수동 지정할 수 있습니다. 표본이 5000개를 넘으면 Shapiro–Wilk의 유효 범위를 벗어나므로 5000개 부분표본으로 근사하고 주석으로 알립니다.
- 검정 자동 선택(정규성→모수/비모수, Levene→Student/Welch)은 편의적 기본값입니다. 사전검정 기반 선택을 지양하려면 `--test-cont welch`(항상 Welch t, 분산 사전검정 없음 — Delacre 2017 권고)나 `--test-cont student`/`nonparam`으로 검정을 고정하고, `--display`로 표기를 고정할 수 있습니다. 무작위배정 시험은 `--no-pvalue`로 p값을 빼고 SMD만 보고할 수 있습니다.
- 정수 코드로 저장된 순서형(예: NYHA 1–4, Likert)은 `--cat-max-levels` 기본값(2) 때문에 연속형으로 처리될 수 있어, 그럴 경우 경고를 표시합니다. 범주형으로 보려면 `--categorical` 을 쓰세요.
- 유럽식 소수 쉼표("1,5")는 천단위 구분과 구분되지 않아 숫자로 해석하지 않습니다("1,234"처럼 명확한 천단위 구분만 인식). 연속형에서는 이런 값을 "해석 불가"로 주석해 알려주니, 점(".") 소수로 변환해 입력하세요.
- ≥3군에서는 SMD를 계산하지 않습니다(SMD는 두 군 균형 지표). 또한 다범주형 SMD는 수준이 50개를 넘으면(고카디널리티) 해석 의미가 없고 계산이 무거워 생략합니다.
- 모든 계산은 로컬에서만 이루어지며 네트워크를 사용하지 않습니다. 입력 데이터는 어디로도 전송되지 않습니다.
- **주의(PII)**: 숫자로 해석되지 않는 셀이 있으면, 문제 셀을 찾을 수 있도록 그 **원문 일부**(최대 3개·각 20자)를 주석/경고에 그대로 인용합니다(예: `(예: >100)`). 해당 열에 환자 식별정보나 자유텍스트가 섞여 있다면 이 문구가 md/csv/json/html 출력에 포함되므로, 원고·제출 시스템에 붙여넣기 전에 확인하세요. 그 외에는 표에 집계값만 나가며, JSON `meta` 에도 행 단위 값(예: 개별 가중치)은 포함되지 않습니다.

## 테스트

```bash
cd ~/Downloads/02_프로젝트/깃헙/table1-기저특성표
python3 -m pytest -q      # 400개 이상, 전부 오프라인
```
