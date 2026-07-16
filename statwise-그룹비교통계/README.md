# statwise — 그룹 비교 통계 자동 선택기

두 그룹(또는 여러 그룹)의 CSV를 넣으면 **정규성·등분산을 먼저 점검**한 뒤
알맞은 검정(t-검정 / Welch / Mann-Whitney / ANOVA / **Welch-ANOVA** / Kruskal-Wallis,
그리고 같은 대상의 전/후 비교를 위한 **대응표본 paired** 검정)을 **자동으로 골라
실행**하고, 효과크기(95% CI 포함)와 **논문에 바로 붙일 수 있는 문장**까지 출력합니다.
결과는 사람이 읽는 텍스트뿐 아니라 **JSON**으로도 뽑아 파이프라인에 넣을 수 있습니다.
외부 라이브러리 없이 표준 라이브러리만으로 동작합니다.

---

## 목적 / Why this exists

**한국어.** 임상·제약 연구자는 "두 집단(또는 여러 집단)의 값이 통계적으로 다른가?"를
논문마다 반복해서 검정합니다. 그런데 올바르게 하려면 매번 (1) 정규성(Shapiro-Wilk),
(2) 등분산(Levene)을 확인하고, 그 결과에 따라 Student t / Welch t / Mann-Whitney /
ANOVA / Kruskal-Wallis 중 **맞는 검정**을 고른 뒤, 효과크기와 신뢰구간까지 보고해야
합니다. 손으로 하면 번거롭고, 가정 점검을 건너뛰어 **틀린 검정을 쓰는 실수**가 흔합니다.
`statwise`는 이 전 과정을 한 번에 처리하고, 어떤 근거로 그 검정을 골랐는지까지
설명하며, 논문 문장(APA 스타일)과 붙여넣기용 표를 만들어 줍니다. BELL-001 수면
디바이스의 HRV/호흡 지표 비교, WowFit 난청 재활 그룹 비교처럼 **회사 데이터의 그룹
비교**에 바로 쓸 수 있습니다.

**English.** Clinical/pharma researchers repeatedly test "do these two (or several)
groups differ?" Doing it *correctly* means first checking normality (Shapiro-Wilk)
and equal variance (Levene), then choosing the right test — independent Student's t,
Welch's t, or Mann-Whitney U; **paired t-test or Wilcoxon signed-rank** for
within-subject pre/post data; one-way ANOVA, **Welch's ANOVA**, or Kruskal-Wallis for
several groups — and finally reporting an effect size with a confidence interval.
Done by hand this is tedious, and skipping the assumption checks is a common source of
*using the wrong test*. `statwise` runs the whole pipeline, explains *why* it picked a
test, and emits a ready-to-paste APA-style sentence plus a table (and JSON). It also
reports exact small-sample p-values and a Hodges-Lehmann location CI for the rank
tests. Reach for it whenever you compare groups — e.g. HRV/respiration outcomes for the
BELL-001 sleep device, or hearing-rehab groups in WowFit.

Every p-value and the Shapiro-Wilk statistic are implemented from first principles
(regularized incomplete beta/gamma, Royston's AS R94). The t / F / χ² / Mann-Whitney /
Levene p-values match SciPy to ≤1e-9; the Shapiro-Wilk **W** matches to ~1e-9 and its
**p-value** to ≲1e-6 for large n (it uses Royston's approximation, exactly as SciPy
does). The tool itself has **zero dependencies** so it runs anywhere Python 3.9+ is
installed.

---

## Install

```bash
cd ~/Downloads/02_프로젝트/깃헙/statwise-그룹비교통계
python3 -m pip install -e .
```

설치 없이도 실행할 수 있습니다:

```bash
python3 -m statwise.cli <파일.csv> ...
```

또는 폴더 안의 **`실행.command` 더블클릭** (예제 데이터로 바로 시연).

---

## Usage

### 1) Long(tidy) 형식 — 값 열 + 그룹 열

```bash
statwise examples/hrv_two_arm.csv --value rmssd_ms --group arm
```

입력 (`examples/hrv_two_arm.csv`):

```
rmssd_ms,arm
31.2,sham
...
44.5,device
...
```

출력 (요약):

```
[1] 기술통계 / Descriptives
    group               n      mean        sd    median       Q1       Q3
    sham               16    33.056     4.925    32.950   28.950   36.775
    device             16    44.925     4.773    44.800   41.425   48.775

[2] 가정 점검 / Assumption checks
    정규성 Shapiro-Wilk [sham]:   W=0.952, p=0.514  → 정규분포로 볼 수 있음
    정규성 Shapiro-Wilk [device]: W=0.964, p=0.726  → 정규분포로 볼 수 있음
    등분산 Levene(median): W=0.060, p=0.808  → 등분산 가정 충족

[3] 선택된 검정 / Selected test
    → Student's t-test
      t=-6.922, df=30, p=<0.001   → 통계적으로 유의함
      평균차 = -11.869 [95% CI -15.370, -8.367]

[4] 효과크기 / Effect size
    Hedges' g = -2.386  [95% CI -3.292, -1.479]  (large)

[논문용 문장 / Ready-to-paste sentence]
  sham (M = 33.06, SD = 4.92) and device (M = 44.92, SD = 4.77) were compared
  using an independent-samples t-test; the difference was statistically
  significant (t(30) = -6.92, p < 0.001, Hedges' g = -2.39).
```

### 1-b) 대응 표본(paired) — 같은 대상의 전/후 비교

같은 환자를 치료 **전(pre)/후(post)** 로 측정한 경우처럼 **짝지어진** 데이터는
독립 2그룹이 아니라 **대응표본**으로 분석해야 합니다. `--paired` 를 붙이면
차이값의 정규성을 점검해 **대응 t-검정** 또는 **Wilcoxon 부호순위검정**을
자동으로 고릅니다.

```bash
# long 형식: 대상 id 열이 필요 (--id). 기준 조건을 --baseline 로 고정 권장
statwise examples/isi_pre_post_paired.csv --paired --value isi --group time --id subject --baseline pre

# wide 형식: 두 열이 각각 pre/post (행 단위로 짝지음, 차이 = 첫 열 − 둘째 열)
statwise pairs.csv --paired --wide --columns post,pre
```

> **부호(방향) 주의.** 차이는 항상 `(조건A − 조건B)` 로 계산되며, 출력에 방향을
> 명시합니다(`비교 방향 direction: 차이 = (post − pre)`). long 형식에서 조건 순서는
> 데이터 등장 순서로 정해지므로, 부호를 재현 가능하게 고정하려면 `--baseline 기준조건`
> 을 지정하세요(그 조건이 빼지는 기준이 됩니다). Wilcoxon일 때는 위치차의
> **Hodges-Lehmann 추정값과 분포무관 신뢰구간**도 함께 출력합니다.

```
[3] 선택된 검정 / Selected test
    → Paired t-test
      t=14.697, df=11, p=<0.001
      평균차 mean difference = 6.000 [95% CI 5.101, 6.899]
[4] 효과크기 / Effect size
    Cohen's dz = 4.243  [95% CI 2.453, 6.032]  (large)
```

### 2) Wide 형식 — 각 열이 하나의 그룹 (3그룹 이상이면 자동으로 ANOVA/Kruskal + 사후검정)

```bash
statwise examples/isi_change_by_dose.csv --wide
```

```
[3] 선택된 검정 / Selected test
    → One-way ANOVA
      F=51.652, df=(2, 33), p=<0.001   → 통계적으로 유의함
[4] 효과크기 / Effect size
    eta-squared = 0.758  (large)
[5] 사후검정 / Post-hoc (Holm-Bonferroni 보정)
    low vs mid    Welch's t   p(adj)<0.001  *
    low vs high   Welch's t   p(adj)<0.001  *
    mid vs high   Welch's t   p(adj)<0.001  *
```

### JSON 출력 — 재현·자동화용

```bash
statwise examples/hrv_two_arm.csv --value rmssd_ms --group arm --format json
```

`schema`, 기술통계, 선택된 검정, 효과크기(±CI), 사후검정, 경고, 논문 문장을 담은
안정적인 JSON을 출력합니다(NaN/Inf는 `null` 로 안전 처리). 스크립트에서 `jq` 등으로
바로 파싱할 수 있습니다.

### 옵션

| 옵션 | 의미 |
|------|------|
| `--value`, `--group` | long 형식의 값/그룹 열 이름 |
| `--id` | (paired long) 대상 식별자(subject id) 열 이름 |
| `--baseline` | (paired) 기준(reference) 조건 이름 — 차이 = (다른 조건 − 기준). 부호 고정 |
| `--wide` | wide 형식(각 열이 그룹) |
| `--paired` | 대응 표본(전/후 등 같은 대상) 분석 |
| `--columns a,b` | wide에서 사용할 열 선택 (paired wide는 정확히 2개) |
| `--alpha 0.05` | 유의수준 |
| `--alpha-norm 0.05` | 정규성/등분산 판정용 유의수준 |
| `--correction holm\|bh` | 사후검정 다중비교 보정 (Holm 기본 / BH=FDR) |
| `--no-posthoc` | 3그룹 이상에서 사후검정 생략 |
| `--format text\|json` | 출력 형식 (기본 text) |
| `--delimiter ';'` | CSV 구분자 강제 지정 (미지정 시 자동 감지: `,` `;` tab `\|`) |

---

## 검정 선택 규칙 (How the test is chosen)

- **2그룹 (독립)**: 두 그룹 모두 정규(Shapiro p>α) →
  등분산(Levene p>α)이면 **Student t**, 아니면 **Welch t**.
  하나라도 비정규면 **Mann-Whitney U**.
- **2조건 (대응 `--paired`)**: 차이값(a−b)이 정규면 **대응표본 t-검정**(효과크기
  Cohen's d_z), 비정규면 **Wilcoxon 부호순위검정**(matched rank-biserial r).
- **3그룹 이상**:
  - 모두 정규 + **등분산** → **one-way ANOVA** (사후검정: 쌍별 Student t)
  - 모두 정규 + **이분산** → **Welch's ANOVA** (사후검정: 쌍별 Welch t) — 정규인데
    분산만 다른 데이터를 굳이 순위검정으로 떨어뜨리지 않습니다.
  - 하나라도 비정규 → **Kruskal-Wallis H** (사후검정: 쌍별 Mann-Whitney U)
  - omnibus가 유의하면 사후검정을 자동 수행하고 **Holm-Bonferroni**(기본) 또는
    **Benjamini-Hochberg FDR**(`--correction bh`)로 보정합니다. (Tukey HSD가 아니라
    "쌍별 검정 + 다중비교 보정" 방식입니다.)
- 효과크기: t/Welch → **Hedges' g**(95% CI), 대응 t → **Cohen's d_z**(95% CI),
  Mann-Whitney → **rank-biserial r + Cliff's δ**, Wilcoxon → **matched rank-biserial r**,
  ANOVA/Welch-ANOVA → **η²**, Kruskal-Wallis → **η²[H]**.
- 비모수(Mann-Whitney / Wilcoxon) 검정에는 **위치차의 Hodges-Lehmann 추정값 +
  분포무관 신뢰구간**을 함께 보고합니다(독립표본은 쌍별 차이의 중앙값, 대응표본은
  Walsh 평균의 중앙값; 소표본은 정확 순위분포, 대표본은 정규근사). 검정 p값과
  일관되게(CI가 0을 포함하지 않으면 유의) 계산됩니다.

## Notes / limitations

- Mann-Whitney U와 Wilcoxon 부호순위검정은 **동점이 없고 표본이 작으면 정확(exact)
  순열 p값**을, 그렇지 않으면 **동점 보정된 정규 근사**(연속성 보정 포함, SciPy의
  `method='asymptotic'`와 일치)를 사용합니다 — 선택 근거를 `reason`에 표시합니다.
  Kruskal-Wallis는 카이제곱 근사입니다.
- 정규성 검정은 각 그룹 3 ≤ n ≤ 5000에서만 수행합니다. n<3이면 정규성 판정을 못 하므로
  비모수 검정으로 안전하게 처리하고, n>5000이면 Shapiro-Wilk를 건너뛰고 정규로 간주합니다
  (대표본에서는 CLT로 t-검정이 견고하기 때문). 대표본에서는 정규성 검정이 사소한 편차에도
  민감해지므로, p값뿐 아니라 기술통계와 분포 모양도 함께 보고 판단하세요.
- **Welch's ANOVA의 η²** 는 등분산을 가정하는 고전적 제곱합(pooled SS)에서 계산되므로
  Welch 모형과 정확히 일관되지는 않습니다. 이분산에서는 근사치로 해석하세요(출력에도
  경고를 함께 표시합니다).
- **소표본 정규성 검정의 한계.** n이 5~15로 작으면 Shapiro-Wilk의 검정력이 낮아 완만한
  비정규성을 놓쳐 t-검정이 선택될 수 있습니다. p값뿐 아니라 분포 모양·기술통계도 함께
  보세요. n<3이면 정규성을 **판정할 수 없어**(비정규가 아니라 미상) 보수적으로 비모수
  검정을 씁니다.
- 이 도구는 검정 선택을 **자동화**하지만, 최종 판단은 연구자의 몫입니다 — 근거(`reason`)를
  항상 함께 출력하니 반드시 확인하세요.

## Tests

```bash
python3 -m pytest    # 123 tests, 전부 오프라인, SciPy/statsmodels 참조값과 대조
```

## License

MIT © 2026 hyeonjoong
