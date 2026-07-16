# table1 — 기저 특성표(Table 1) 생성기

임상 CSV 한 장을 넣으면 **출판용 "표 1"(baseline characteristics table)** 을 자동으로 만들어 줍니다. 변수마다 연속형/범주형을 스스로 판별해 알맞은 요약(평균±SD 또는 중앙값[IQR], n(%))과 검정을 고르고, 두 군 비교의 **표준화 평균차(SMD)** 와 **결측**까지 정리해 Markdown·CSV·TSV·JSON으로 출력합니다. 외부 의존성 0 (표준 라이브러리만).

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

# 특정 변수만, 코드형 열은 범주형으로 강제
table1 examples/serene_baseline.csv --group arm --vars age,sex,isi,bmi --categorical sex

# 원고에 붙일 CSV로 저장
table1 examples/serene_baseline.csv --group arm --format csv -o table1.csv

# 영문 저널 제출용: 영어 라벨 + 변수 이름/단위 지정 + (CONSORT) p값 숨김
table1 examples/serene_baseline.csv --group arm --lang en --no-pvalue \
  --labels rmssd_ms="RMSSD (ms)" ahi="AHI (events/h)" isi="ISI"
```

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

### 주요 옵션

| 옵션 | 뜻 |
|------|-----|
| `--group, -g` | (필수) 군 라벨 열 |
| `--vars` | 요약할 변수(쉼표 구분). 미지정 시 군 열 제외 전체 |
| `--continuous` / `--categorical` | 특정 열의 형(型)을 강제 |
| `--cat-max-levels N` | 수치형이라도 서로 다른 값이 N개 이하이면 범주형(기본 2) |
| `--max-levels N` | 자동 범주형의 고유값이 N개 초과면 ID로 보고 제외(기본 20) |
| `--display auto\|mean\|median\|both` | 연속형 표기(기본 auto = 정규성 따라) |
| `--test-cont auto\|welch\|student\|nonparam` | 연속형 **검정** 선택: auto(사전검정, 기본)·welch(항상 Welch t)·student(항상 Student t)·nonparam(항상 Mann-Whitney/Kruskal). 분산 사전검정을 피하려면 `welch` 권장(Delacre 2017). **welch·student 는 2군 비교에만 적용**되며 ≥3군은 일원배치 ANOVA(비모수는 Kruskal) |
| `--pct col\|row` | 범주형 % 기준(기본 col = 그룹 내) |
| `--pct-decimals N` | 범주형 % 소수 자릿수(기본 1, 0~10) |
| `--binary-single` | 2수준(이진) 범주형을 한 줄로 축약(예: `sex = M — n(%)`) — 저널 관례. **md·csv·tsv 에만 적용**(JSON은 구조화 데이터라 모든 수준 유지) |
| `--ref COL=수준` | 이진 축약 시 기준(참조) 수준 지정 → 표엔 반대 수준 표시(예: `--ref sex=F` → M 행) |
| `--alpha-norm A` | 정규성·등분산 판정 유의수준(기본 0.05). 검정 자동선택에 영향 |
| `--fisher` | 2×2 범주형에 항상 Fisher exact |
| `--missing-as-level` | 범주형 결측을 '(결측)' 수준으로 표시(검정 제외) |
| `--no-overall` | '전체' 열 숨김 |
| `--no-pvalue` | p값 열 숨김 — **무작위배정 임상시험(SERENE 등)** 은 CONSORT 권고상 기저 p값 대신 SMD로 균형 보고 |
| `--range` | 연속형 셀에 `(최소–최대)` 범위 추가 |
| `--lang ko\|en` | 표 라벨 **및 모든 주석·경고** 언어(기본 ko, `en` = 영문 저널 제출용) |
| `--labels COL=이름 …` | 변수 표시 이름/단위 지정(예: `--labels rmssd_ms="RMSSD (ms)"`) |
| `--decimals N` | 연속형 소수 자릿수(기본 1, 음수 불가) |
| `--delimiter D` | 입력 구분자 강제(한 글자, 미지정 시 자동 감지). 탭은 `--delimiter tab`(또는 `\t`), 세로줄은 `--delimiter '\|'` |
| 입력 `-` | CSV 경로 대신 `-` 를 주면 **표준입력(stdin)** 에서 읽음(파이프라인용: `cut -d, -f2- data.csv \| table1 - -g arm`) |
| `--format md\|csv\|tsv\|json`, `-o` | 출력 형식/파일 |
| `--version` | 버전 출력 후 종료 |

## 방법론 노트 / Methods

- **연속형 요약·검정 선택**: 각 군(n≥3)에 Shapiro–Wilk 정규성 검정. 어느 군이라도 정규성 기각 → 중앙값[IQR] + 비모수 검정(2군 Mann–Whitney U, ≥3군 Kruskal–Wallis). 모두 정규 → 평균±SD + 모수 검정(2군은 Levene 등분산 점검 후 Student t / Welch t, ≥3군은 일원배치 ANOVA). ANOVA에서 Levene이 기각되면 해석 주의를 주석으로 표시합니다.
- **범주형 검정**: r×c 표에 Pearson χ²(연속성 보정 없음, `chi2_contingency(correction=False)` 기준). 2×2에서 기대빈도<5이거나 `--fisher` 지정 시 양측 Fisher exact.
- **SMD(두 군 전용)**: 연속형은 `|m₁−m₂| / √((s₁²+s₂²)/2)`, 이진형은 비율 기반, 다범주형은 **Yang & Dalton(2012)** 다변량 SMD. 관례상 |SMD|>0.1 을 불균형 신호로 봅니다.
- **결측**: 연속형은 결측·비수치 셀(및 inf/-inf 등 비유한 수치)을 제외해 요약하고 결측 수를 표기하며, 두 군 이상일 때는 **군별 결측 분포**를 함께 표시합니다(`· 결측 4 (device 2, sham 2)`) — 무작위배정 시험에서 차등 결측(differential missingness)을 한눈에 볼 수 있습니다. 범주형 %는 **비결측(non-missing) 기준**이며, 결측은 기본적으로 수준에서 제외합니다(`--missing-as-level`로 표시 가능). 군 값이 결측인 행은 전체에서 제외하고 경고합니다. 숫자로 해석되지 않는 셀(예: `>100`·`12 kg`·`45%`·유럽식 `1,5`)은 **단순 결측이 아니라 "해석 불가"로 별도 주석**해, 검열(censored)·단위 포함 값이 평균에 조용히 반영되지 않도록 알립니다. 변수의 결측이 50%를 넘거나, 대소문자만 다른 그룹 라벨(예: `Device`/`device`)이 별개 군으로 잡히면 경고합니다.
- 분포 함수(정규/ t / F / χ²)와 Shapiro–Wilk, Fisher exact 열거는 모두 표준 라이브러리로 자체 구현했고, SciPy/numpy와 대조 검증했습니다: 분포함수·Student/Welch t·ANOVA·Fisher·χ²는 대체로 ≤1e-9(꼬리 확률까지) 일치하고, Shapiro–Wilk는 ~1e-8 수준(W 통계량은 ~1e-9)으로 일치합니다. **단, Mann–Whitney U·Kruskal–Wallis는 정규/χ² 점근(asymptotic) 근사를 쓰므로 SciPy의 `method='asymptotic'` 결과와 일치하며, 소표본에서 SciPy 기본값의 정확검정(exact)과는 다를 수 있습니다.** 이 대조값들은 오프라인 테스트로 고정해 두었습니다(`tests/test_tests_stat.py`, `tests/test_normality.py`, `tests/test_special.py`, `tests/test_smd.py`, 골든 스냅샷, 하드닝 회귀·속성 테스트 `tests/test_hardening_r1.py`~`r3.py` 등, 총 207개).

## 한계 / Limitations

- 검정 자동 선택은 합리적 기본값이며, 만능이 아닙니다. 짝지은(대응) 설계, 층화/가중, 시간-사건(생존), 다중비교 보정 등은 대상이 아닙니다(각각 전용 도구 필요).
- 정규성 판정은 표본이 매우 작으면(각 군 n<3) 불가하며, 이때 평균±SD로 표시하고 주석을 답니다. 대표본에서는 Shapiro가 사소한 이탈도 기각할 수 있어 `--display`로 수동 지정할 수 있습니다. 표본이 5000개를 넘으면 Shapiro–Wilk의 유효 범위를 벗어나므로 5000개 부분표본으로 근사하고 주석으로 알립니다.
- 검정 자동 선택(정규성→모수/비모수, Levene→Student/Welch)은 편의적 기본값입니다. 사전검정 기반 선택을 지양하려면 `--test-cont welch`(항상 Welch t, 분산 사전검정 없음 — Delacre 2017 권고)나 `--test-cont student`/`nonparam`으로 검정을 고정하고, `--display`로 표기를 고정할 수 있습니다. 무작위배정 시험은 `--no-pvalue`로 p값을 빼고 SMD만 보고할 수 있습니다.
- 정수 코드로 저장된 순서형(예: NYHA 1–4, Likert)은 `--cat-max-levels` 기본값(2) 때문에 연속형으로 처리될 수 있어, 그럴 경우 경고를 표시합니다. 범주형으로 보려면 `--categorical` 을 쓰세요.
- 유럽식 소수 쉼표("1,5")는 천단위 구분과 구분되지 않아 숫자로 해석하지 않습니다("1,234"처럼 명확한 천단위 구분만 인식). 연속형에서는 이런 값을 "해석 불가"로 주석해 알려주니, 점(".") 소수로 변환해 입력하세요.
- ≥3군에서는 SMD를 계산하지 않습니다(SMD는 두 군 균형 지표). 또한 다범주형 SMD는 수준이 50개를 넘으면(고카디널리티) 해석 의미가 없고 계산이 무거워 생략합니다.
- 모든 계산은 로컬에서만 이루어지며 네트워크를 사용하지 않습니다. 입력 데이터는 어디로도 전송되지 않습니다.

## 테스트

```bash
cd ~/Downloads/02_프로젝트/깃헙/table1-기저특성표
python3 -m pytest -q      # 207개 테스트, 전부 오프라인
```
