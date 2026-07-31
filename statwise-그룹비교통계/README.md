# statwise — 그룹 비교 통계 자동 선택기

두 그룹(또는 여러 그룹)의 CSV를 넣으면 **정규성·등분산을 먼저 점검**한 뒤
알맞은 검정(t-검정 / Welch / Mann-Whitney / ANOVA / **Welch-ANOVA** / Kruskal-Wallis,
그리고 같은 대상의 전/후 비교를 위한 **대응표본 paired** 검정)을 **자동으로 골라
실행**하고, 효과크기(신뢰구간 포함 — 모든 구간의 신뢰수준은 `--alpha` 를 따릅니다)와
**논문에 붙일 문장 초안**까지 출력합니다.

연속형 결과만이 아닙니다. 임상시험 프로토콜에 실제로 들어 있는 나머지 네 가지도
한 도구에서 처리합니다:

- **이진(반응자/이상반응) 결과** — `--binary`: 반응률(Wilson CI), 카이제곱/Fisher
  정확검정, **위험차(RD)·위험비(RR)·오즈비(OR)·NNT** 를 신뢰구간과 함께.
- **등가성·비열등성** — `--equivalence-margin` / `--ni-margin`: "차이가 없다"가
  아니라 **"임상적으로 같다/뒤지지 않는다"** 를 TOST로 검정.
- **여러 엔드포인트 동시 분석** — `--values isi,psqi,hrv`: 엔드포인트별로 알맞은
  검정을 돌리고 **엔드포인트 간 다중비교까지 보정**한 요약표.
- **공변량 보정(ANCOVA)** — `--covariate isi_base --adjust-factor site`: RCT의 표준
  1차 분석. 보정평균(LS mean)·보정된 군간 차이·공변량 기울기와 **기울기 동질성**
  점검까지.

결과는 사람이 읽는 텍스트뿐 아니라 **JSON**과 **정돈된 CSV 결과표**로도 뽑아
파이프라인이나 엑셀에 바로 넣을 수 있습니다(`--format`, `--output`).
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
tests. Beyond continuous outcomes it also covers the rest of a real protocol: **binary
responder / adverse-event endpoints** (`--binary`: Wilson rates, chi-square or Fisher,
risk difference with a Newcombe interval, risk ratio, odds ratio and NNT),
**equivalence and non-inferiority** (`--equivalence-margin` / `--ni-margin`: TOST, so
you can actually claim similarity instead of merely failing to reject a difference),
and **several endpoints in one pass with multiplicity control across them**
(`--values`). Reach for it whenever you compare groups — e.g. HRV/respiration outcomes
for the BELL-001 sleep device, or hearing-rehab groups in WowFit.

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
    group               n     mean       sd   median       Q1       Q3      min      max
    sham               16   33.056    4.925   32.950   28.950   36.775   25.900   41.000
    device             16   44.925    4.773   44.800   41.425   48.775   37.500   52.800

[2] 가정 점검 / Assumption checks
    정규성 Shapiro-Wilk [sham]: W=0.952, p=0.514  → 정규성 위배 근거 없음
    정규성 Shapiro-Wilk [device]: W=0.964, p=0.726  → 정규성 위배 근거 없음
    등분산 Levene(median): W=0.060, p=0.808  → 등분산 위배 근거 없음

[3] 선택된 검정 / Selected test
    → Student's t-test
      (근거: both groups ~normal (Shapiro p>0.05) and equal variance (Levene p=0.808) -> Student's t-test)
      t=-6.922, df=30, p=<0.001
      유의수준 α=0.05: 통계적으로 유의함 (p<0.05)
      평균차 mean difference (sham − device) = -11.869 [95% CI -15.370, -8.367]

[4] 효과크기 / Effect size
    Hedges' g = -2.386  [95% CI -3.292, -1.479]  (large)

[논문용 문장 / Ready-to-paste sentence]
  sham (n = 16, M = 33.06, SD = 4.92) and device (n = 16, M = 44.92, SD = 4.77)
  were compared using an independent-samples t-test; the difference was
  statistically significant (t(30) = -6.92, p < 0.001, Hedges' g = -2.39).
  The mean difference (sham − device) was -11.87 (95% CI -15.37 to -8.37).
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
statwise 내파일.csv --paired --wide --columns post,pre
```

> **부호(방향) 주의.** 차이는 항상 `(조건A − 조건B)` 로 계산되며, 출력에 방향을
> 명시합니다(`비교 방향 direction: 차이 = (post − pre)`). long 형식에서 조건 순서는
> 데이터 등장 순서로 정해지므로, 부호를 재현 가능하게 고정하려면 `--baseline 기준조건`
> 을 지정하세요(그 조건이 빼지는 기준이 됩니다). Wilcoxon일 때는 위치차의
> **Hodges-Lehmann 추정값과 분포무관 신뢰구간**도 함께 출력합니다.

```
[3] 선택된 검정 / Selected test
    → Paired t-test
      (근거: differences ~normal (Shapiro p=0.110) -> paired-samples t-test)
      t=-14.697, df=11, p=<0.001
      유의수준 α=0.05: 통계적으로 유의함 (p<0.05)
      평균차 mean difference (post − pre) = -6.000 [95% CI -6.899, -5.101]
[4] 효과크기 / Effect size
    Cohen's dz = -4.243  [95% CI -6.032, -2.453]  (large)
```

> 위 숫자는 `--baseline pre` 를 준 결과입니다 — 차이가 `(post − pre)` 이므로 치료 후
> ISI가 6점 **낮아진** 것이 음수로 나옵니다. `--baseline` 없이 돌리면 CSV 등장 순서에
> 따라 부호가 반대(`+6.000`)로 나올 수 있습니다. 그래서 기준 조건을 고정하라고
> 권하는 것입니다.

### 2) Wide 형식 — 각 열이 하나의 그룹 (3그룹 이상이면 자동으로 ANOVA/Kruskal + 사후검정)

```bash
statwise examples/isi_change_by_dose.csv --wide
```

```
[3] 선택된 검정 / Selected test
    → One-way ANOVA
      (근거: all groups ~normal and equal variance (Levene p=1.000) -> one-way ANOVA)
      F=51.652, df=(2, 33), p=<0.001
      유의수준 α=0.05: 통계적으로 유의함 (p<0.05)

[4] 효과크기 / Effect size
    eta-squared = 0.758  (large)

[5] 사후검정 / Post-hoc (Holm-Bonferroni 보정)
    comparison                        n  difference             95% CI(비보정)   p(adj)   effect sig
    low vs mid                    12/12       -3.00          [-4.22, -1.78]   <0.001    -2.00   *
    low vs high                   12/12       -6.00          [-7.22, -4.78]   <0.001    -4.01   *
    mid vs high                   12/12       -3.00          [-4.22, -1.78]   <0.001    -2.00   *
    (difference = mean difference, 첫 그룹 − 둘째 그룹 / first minus second; …)
```

사후검정 표는 p값만이 아니라 **차이값과 신뢰구간, 각 군의 n, 부호 규약**을 함께
싣습니다 — p값만 있는 표는 원고에 그대로 쓸 수 없기 때문입니다. `p(raw)` 는 JSON과
CSV에 있습니다.

(등분산 ANOVA 뒤에는 **쌍별 Student t**, Welch-ANOVA 뒤에는 쌍별 Welch t 로
omnibus와 일관되게 맞춥니다. 이는 **Tukey HSD도, Games-Howell도, Dunn 검정도
아닙니다** — "쌍별 검정 + 다중비교 보정" 방식입니다. 특히 Kruskal-Wallis 뒤의 쌍별
Mann-Whitney는 Dunn 검정과 달리 omnibus의 통합 순위를 쓰지 않으므로, 유의한 omnibus와
사후검정 결과가 서로 어긋날 수 있습니다.)

### 3) 이진(yes/no) 결과 — 반응률·RD·RR·OR·NNT

반응자(responder), 이상반응 발생 여부처럼 **결과가 예/아니오**인 엔드포인트는
평균·표준편차가 의미 없습니다. `--binary` 를 붙이면 그에 맞는 분석을 합니다.

```bash
statwise examples/responder_two_arm.csv --binary --value responder --group arm \
    --reference sham --event-is benefit
```

```
[1] 반응률 / Event rates
    group             events     n     rate       95% CI (Wilson)
    device                15    24    62.5%        [42.7%, 78.8%]
    sham                   5    24    20.8%         [9.2%, 40.5%]

[2] 선택된 검정 / Selected test
    → Chi-square test of independence
      (근거: 모든 기대빈도 ≥ 5 (최솟값 10.00) → Pearson 카이제곱 독립성 검정)
      χ²=8.571, df=1, p=0.003
      유의수준 α=0.05: 통계적으로 유의함 (p<0.05)
      (참고: Yates 연속성 보정 카이제곱 p=0.008)
      기대빈도 최솟값 min expected = 10.00

[3] 효과 크기 / Effect measures
    Risk difference (RD) = 41.7%  [95% CI 13.8%, 61.7%]
      방법: Newcombe hybrid score
    Risk ratio (RR) = 3.000  [95% CI 1.296, 6.944]
      방법: Katz log
    Odds ratio (OR) = 6.333  [95% CI 1.751, 22.912]
      방법: Woolf logit
    Number needed to treat (NNT) = 2.4  [95% CI 1.6, 7.3]
      방법: 1/RD (inverted from the RD CI)
    (기준 reference = sham: RD = p(device) − p(sham), RR/OR = device ÷ sham)

[!] 주의 / Warnings
    - 결과가 흔한 사건(발생률 10% 초과)이라 오즈비(OR)는 위험비(RR)보다 1에서 훨씬
      멀어집니다 — OR를 '몇 배 위험'으로 읽지 마세요. …
    - 이진 결과 매핑(반드시 확인하세요): 사건(event) = {YES}, 비사건(non-event) = {NO}
```

> `--event-is` 를 주지 않으면 NNT/NNH를 구분할 수 없으므로 중립적인
> `NNT/NNH (1/|위험차|)` 로 표시됩니다. 이상반응처럼 **나쁜** 사건이면
> `--event-is harm` 을 주어야 **NNH**로 올바르게 나옵니다.

- **검정 선택**: **2×2(두 군)** 에서 기대빈도가 하나라도 5 미만이면 **Fisher 정확검정**,
  아니면 **Pearson 카이제곱**(2×2면 Yates 보정 p도 함께 표시).
  **3군 이상은 Fisher를 지원하지 않으므로** 기대빈도와 무관하게 k×2 카이제곱을 쓰고,
  기대빈도가 작으면 근사가 부정확할 수 있다고 근거 줄에 경고합니다
  (`--binary-test fisher` 를 3군에 주면 조용히 다른 검정을 하지 않고 거부합니다).
  `--binary-test` 로 검정을 고정할 수 있습니다.
- **값 해석**: `1/0`, `Y/N`, `yes/no`, `true/false`, `유/무`, `성공/실패` 등을 자동
  인식하고 **어떤 값을 사건으로 봤는지 항상 출력**합니다. 자동 인식이 안 되는 라벨은
  추측하지 않고 `--event-value` 를 요구합니다(반대로 매핑된 결과를 조용히 내놓는 것보다
  낫습니다).
- **집계표 입력**: 이미 `arm,responders,total` 형태로 세어 둔 표라면
  `--events-col responders --n-col total --group arm`.
- 3그룹 이상이면 k×2 카이제곱 + **Cramér's V**, 유의하면 쌍별 2×2 사후검정(Holm/BH).

### 4) 등가성(TOST)·비열등성 — "차이가 없다"를 제대로 주장하기

p > 0.05 는 **"같다"는 증거가 아닙니다**. 새 요법이 표준요법에 뒤지지 않는다는
주장은 **마진을 정해 놓고** 검정해야 합니다.

> **생물학적동등성(BE)과는 다릅니다.** 규제용 BE는 로그변환한 AUC/Cmax에 80–125%
> 한계와 교차설계 모형을 씁니다. 여기의 TOST는 **원래 척도의 평균차**에 대한
> t-모형이므로 BE 제출에 그대로 쓸 수 없습니다.

```bash
# 등가성: 평균차가 ±20 ms 안에 있는가 (two one-sided tests)
statwise examples/hrv_two_arm.csv --value rmssd_ms --group arm --equivalence-margin 20

# 비대칭 마진도 가능
statwise examples/hrv_two_arm.csv --value rmssd_ms --group arm \
    --reference device --equivalence-margin -20,5

# 비열등성: 3점 이상 나빠지지만 않으면 된다 (ISI는 낮을수록 좋음)
statwise examples/isi_pre_post_paired.csv --paired --value isi --group time \
    --id subject --baseline pre --ni-margin 3 --ni-direction lower_is_better
```

```
[3b] 등가성 검정 / Equivalence (TOST)
     등가 마진 margin: [-20.000, 20.000]  (평균차 (sham − device) 기준)
     평균차 diff = -11.869, SE = 1.715, df = 30  [t-모형: Student's t (pooled)]
     H01 (diff ≤ low):  t = 4.742, p = <0.001
     H02 (diff ≥ high): t = -18.586, p = <0.001
     p(TOST) = max(p1, p2) = <0.001
     90% CI [-14.779, -8.959]
       ↳ 100(1−2α)% 구간입니다. 이 구간이 마진 안에 완전히 들어가는 것이 p(TOST)<α 와
         정확히 같은 판정이라서 α=0.05에서는 90%를 씁니다
         (연구의 일반적 신뢰구간인 95%가 아닙니다).
     → α=0.05: 등가(equivalence) 성립 — 차이가 마진 안에 있음
```

비열등성이면 단측 검정 하나와 단측 신뢰한계를 보고합니다:

```
[3b] 비열등성 검정 / Non-inferiority
     비열등성 마진 margin: 3.000  (평균차 (post − pre) 기준, 낮을수록 좋음 (lower is better))
     → 기각 기준: 95% 단측 신뢰상한 < 3.000  (점추정값이 아니라 신뢰한계로 판정합니다)
     평균차 diff = -6.000, SE = 0.408, df = 11  [t-모형: paired t]
     t = -22.045, p = <0.001 (단측 one-sided)
     95% 단측 상한 upper bound = -5.267
     → α=0.05: 비열등성(non-inferiority) 성립
```

- TOST는 두 개의 단측 t-검정이며 `p = max(p_low, p_high)`. 이는 **100(1−2α)% 신뢰구간이
  마진 안에 완전히 들어가는 것과 대수적으로 동일**하므로, 규제기관이 요구하는 90% CI를
  함께 출력합니다.
- 사용하는 t-모형(Student/Welch/paired)은 **위에서 선택된 주검정과 일치**시킵니다.
  주검정이 순위검정(Mann-Whitney/Wilcoxon)이면 평균차 t-모형으로 근사하며 **경고를
  표시**합니다.
- **마진은 통계가 아니라 임상적 결정입니다.** 도구는 절대 대신 정해 주지 않습니다.

### 5) 공변량 보정(ANCOVA) — RCT의 표준 1차 분석

무작위배정 임상시험에서 연속형 1차 평가변수의 **사전 지정된 1차 분석은 t-검정이
아니라 공분산분석(ANCOVA)** 인 경우가 대부분입니다: 사후 측정값을 **치료군 + 그
환자 자신의 기저값**(+ 층화인자)으로 회귀합니다. ICH E9 §5.7이 다루는 **사전 지정된
공변량 보정**이 바로 이것입니다(E9가 ANCOVA를 의무화하는 것은 아니며, 보정을 사전에
정할 것과 **보정하지 않은 분석도 함께 제시할 것**을 요구합니다). 장식이 아닙니다 —
기저값과 결과의 상관이 r이면 잔차분산의 약 r²가 제거되어 잔차분산이 커지는 일은 없고,
기저 상관이 웬만큼 있으면 사후값 비교보다도 변화량(change score) t-검정보다도 **검정력이
높습니다**(다만 자유도를 1 더 쓰므로 상관이 0에 가까운 소표본에서는 아주 약간 손해).
또 기저 불균형이 있을 때 변화량 분석과 달리 **편향되지 않습니다**(Lord's paradox).

```bash
# 기저 ISI로 보정한 3군 비교 (기준군 = placebo), 기관(site)까지 층화 보정
statwise examples/isi_ancova_baseline.csv \
    --value isi_week8 --group arm \
    --covariate isi_base --adjust-factor site --reference placebo
```

```
[1] 모형 / Model
    결과변수 outcome : isi_week8
    공변량 covariates: isi_base
    보정인자 factors : site
    기준군 reference : placebo  [기준군 대비 차이 = (다른 군 − placebo)]
      ↳ 아래 [4]에는 기준군을 포함하지 않는 쌍도 **모두** 나오며, 다중비교 보정은 그 전체 가족에 적용됩니다.
    분석 n = 88 (결측으로 제외 2행)
    잔차 표준편차 σ = 2.441, R²=0.759, 수정 R²=0.747

[2] 보정평균 / Adjusted (LS) means
    group               n   raw mean   adjusted       SE                 95% CI   isi_base
    drug_low           30     14.077     14.038    0.446       [13.151, 14.924]      17.10
    drug_high          28     12.225     12.221    0.461       [11.303, 13.139]      17.07
    placebo            30     16.463     16.506    0.446       [15.619, 17.392]      17.03
    (마지막 열들 = 그룹별 공변량 평균 — 기저 균형을 눈으로 확인하세요)

[3] 그룹 효과 / Omnibus test of the group term
    F(2, 83)=22.571, p=<0.001, 부분 η²=0.352
    유의수준 α=0.05: 통계적으로 유의함

[4] 보정된 그룹 차이 / Adjusted differences
    comparison                                   차이       SE                 95% CI        p   p(adj)
    drug_low − drug_high                      1.817    0.642         [0.541, 3.093]    0.006    0.006 *
    drug_low − placebo                       -2.468    0.630       [-3.722, -1.214]   <0.001   <0.001 *
    drug_high − placebo                      -4.285    0.642       [-5.561, -3.009]   <0.001   <0.001 *
    (* = Holm-Bonferroni (family-wise) 보정 후 α=0.05에서 유의. 신뢰구간은 **비교 1건 기준(비보정)** 이므로 별표와 결론이 다를 수 있습니다.)

[5] 공변량 효과 / Covariate & factor effects
    term                         coef       SE                 95% CI        t        p
    isi_base                    1.221    0.083         [1.056, 1.386]   14.707   <0.001
    site=seoul (vs busan)      -0.553    0.526        [-1.599, 0.493]   -1.051    0.296

[6] 가정 점검 / Assumption checks
    기울기 동질성(그룹×공변량): F(2, 81)=0.092, p=0.912  → 기울기 동질성 위배 근거 없음
    잔차 정규성 Shapiro-Wilk: p=0.160  → 정규성 위배 근거 없음
```

무엇을 계산하는가:

- **보정평균(adjusted / LS mean)** — 수치 공변량을 **전체 평균**에, 보정인자는 각
  수준에 **동일 가중치**를 주어 예측한 값(`emmeans` 기본 규약)과 그 신뢰구간. 관측된
  군평균(raw mean)과 나란히 찍히므로 보정이 무엇을 바꿨는지 바로 보입니다.
- **보정된 군간 차이**와 신뢰구간·p값. 3군 이상이면 쌍별 비교에 Holm(기본) 또는 BH
  보정을 적용합니다.
- **그룹 항의 omnibus F 검정** — 그룹 더미를 뺀 모형을 다시 적합해 비교하는 방식
  (상호작용이 없는 모형이므로 Type II = Type III)과 부분 η².
- **공변량·보정인자 계수**와 t-검정 — 보정이 실제로 값어치가 있었는지 읽을 수 있습니다.
- **기울기 동질성 검정** — 그룹 × 공변량 상호작용을 넣은 모형과의 F 검정 (수치
  공변량이 있을 때만; `--adjust-factor` 만 쓰면 검정할 기울기가 없어 생략하고 그렇게
  표시합니다). 유의하면
  "치료효과가 기저값에 따라 다르다"는 뜻이므로 하나의 보정된 차이로 요약하지 말라고
  경고합니다.
- **잔차** 정규성(Shapiro-Wilk)과 **잔차** 등분산(Levene) — 원자료가 아니라 모형이
  실제로 가정하는 대상에 대해 점검합니다.
- `--equivalence-margin` / `--ni-margin` 을 함께 주면 **보정된 평균차**에 대해 TOST /
  비열등성 검정을 수행합니다(**2군일 때만**; 3군 이상이면 경고와 함께 건너뜁니다).

반드시 알아야 할 것:

- **공변량은 무작위배정 *전에* 측정된 것이어야 합니다.** 치료 시작 후에 측정한 값
  (중간 방문 수치, 순응도, 부작용 발생 여부)을 넣으면 매개변수·충돌변수를 통해
  치료효과가 편향되고, **그 편향은 자료만으로는 절대 확인할 수 없습니다.** 도구는
  이 경고를 매번 출력합니다 — 산술로는 두 경우를 구분할 방법이 없기 때문입니다.
- ANCOVA는 결과–공변량 관계가 **직선**이라고 가정합니다. 곡선이면 보정이 불완전합니다.
- 결측이 있는 행은 **완전자료(complete-case)** 로 제외하고, 제외한 행 수를 보고합니다.
- 공변량이 그룹 안에서 상수이거나 다른 공변량과 중복(선형종속)이면 조용히 이상한 값을
  내놓지 않고 **거부**합니다. 잔차가 사실상 0이 되면(결과값 자체가 들어간 변수를 넣은
  전형적 실수) 그 사실도 경고합니다.
- 3군 이상이면 **모든 쌍**(k(k−1)/2개)의 보정된 차이가 나오고, 다중비교 보정은 그
  **전체 가족**에 적용됩니다. `--reference` 는 부호와 기준 코딩만 고정할 뿐 비교 대상을
  기준군 대비로 **줄이지 않습니다**. `--reference` 를 생략하면 기준군은 CSV에서 **마지막
  으로 처음 등장한 군**이 되므로, 부호를 재현하려면 항상 지정하세요.
- `--covariate` / `--adjust-factor` 는 long 형식 전용이라 `--wide`, `--paired`,
  `--binary`, `--columns` 와 함께 쓸 수 없고, **`--values`(다중 엔드포인트)와도 함께 쓸
  수 없습니다** — 보정이 필요한 엔드포인트는 하나씩 따로 실행한 뒤 p값을 직접 보정하세요.
  `--no-posthoc` 와 `--test` 도 이 경로에는 적용되지 않으며, 조용히 무시하지 않고
  오류로 거부합니다.
- 군이 60개를 넘으면 계산·보고 양쪽에서 의미가 없어 **거부**합니다 (`--group` 이 대상
  ID나 날짜 열을 가리키는 실수를 잡기 위한 안전장치).
- 그룹 비교만 목적이라면 `--covariate` 없이 쓰던 자동 선택 경로가 그대로 유효합니다.
  ANCOVA는 **사전 지정된 모형**이므로 정규성·등분산으로 검정을 바꾸지 않습니다.

### 6) 여러 엔드포인트 한 번에 + 엔드포인트 간 다중비교 보정

엔드포인트 8개를 α=0.05로 각각 검정하면 **적어도 하나가 우연히 유의할 확률이 약 34%**
입니다. `--values` 는 엔드포인트별로 알맞은 검정을 돌린 뒤 **엔드포인트 패밀리 전체에
대해** 보정합니다.

```bash
statwise examples/multi_endpoint_two_arm.csv \
    --values isi_change,psqi_change,rmssd_ms,ess_change \
    --group arm --reference sham --brief
```

```
[요약] 엔드포인트 4개 (분석 실패 0개) — 엔드포인트 간 보정: Holm-Bonferroni (family-wise)

    endpoint        test                    effect   p(raw)   p(adj)  sig
    isi_change      Student t              g -1.92   <0.001   <0.001    *
    psqi_change     Student t              g -0.95    0.004    0.008    *
    rmssd_ms        Student t               g 2.67   <0.001   <0.001    *
    ess_change      Student t               g 0.04    0.910    0.910     

    * = 보정 후 p < 0.05 (엔드포인트 간 Holm-Bonferroni (family-wise))
```

- `--endpoint-correction holm|bh|none` — 엔드포인트 패밀리 보정 방법. `none` 을 고르면
  **왜 위험한지 경고**를 함께 출력합니다.
- 엔드포인트 안의 사후검정 보정(`--correction`)과는 **별개의 패밀리**로 취급하며,
  출력에도 그렇게 명시합니다.
- `--brief` 는 요약표만, 기본은 요약표 + 엔드포인트별 전체 리포트.
- `--binary` 와 함께 쓰면 이진 엔드포인트 여러 개(예: 이상반응 표)를 한 번에.
- 한 엔드포인트가 분석 불가여도(전부 결측 등) **나머지는 그대로 분석**하고, 실패한
  엔드포인트와 이유를 따로 표시합니다.

### JSON 출력 — 재현·자동화용

```bash
statwise examples/hrv_two_arm.csv --value rmssd_ms --group arm --format json
```

`schema`, 기술통계, 선택된 검정, 효과크기(±CI), 사후검정, 경고, 논문 문장을 담은
안정적인 JSON을 출력합니다(NaN/Inf는 `null` 로 안전 처리). 스크립트에서 `jq` 등으로
바로 파싱할 수 있습니다. 스키마는 모드별로 `statwise/analysis/1`(연속형),
`statwise/binary/1`(이진), `statwise/multi/1`(다중 엔드포인트),
`statwise/ancova/1`(공변량 보정) 입니다.

### CSV 결과표 · 파일로 저장

```bash
# 비교 1건당 1행인 정돈된 결과표 (엑셀/R 로 바로)
statwise data.csv --value isi --group arm --format csv

# 화면 대신 파일로 (csv는 엑셀이 한글을 바로 열도록 BOM 포함)
statwise data.csv --values isi,psqi --group arm --format csv -o 결과.csv
```

열: `endpoint, kind, comparison, test, n1, n2, n_all, estimate_name, estimate,
ci_low, ci_high, ci_conf, statistic, df, pvalue, pvalue_adj, significant, verdict`.

`n_all` 은 모든 군의 n(이진이면 events/n)을 `low=12|mid=12|high=12` 형태로 담습니다
— `n1`/`n2` 만으로는 3군 이상에서 세 번째 군부터 사라지기 때문입니다. `ci_conf` 는
그 행의 신뢰구간이 몇 %인지(TOST 행은 90%, 나머지는 `1−alpha`), `verdict` 는
`significant` 가 그 행에서 무엇을 뜻하는지를 사람이 읽을 수 있게 적습니다.

`kind` 로 행의 종류를 구분합니다: `continuous` / `paired` / `binary`(주검정 1행),
`binary-effect`(같은 비교의 RR·OR·NNT — 이진 결과 하나가 여러 행이 됩니다),
`post-hoc`(사후검정), `tost` / `noninferiority`(마진 검정), 그리고 공변량 보정에서는
`ancova`(그룹 효과 1행) / `adjusted-mean`(군별 보정평균) / `adjusted-contrast`(보정된
군간 차이) / `covariate`(공변량·보정인자 계수).

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
| `--no-posthoc` | 3그룹 이상에서 사후검정 생략 (ANCOVA 경로에는 적용 불가) |
| `--reference GROUP` | (독립 비교) 기준(대조) 그룹 고정 — 차이 = (다른 그룹 − 기준) |
| `--test auto\|student\|welch\|mannwhitney` | (연속형 2그룹) 검정을 **사전 지정** (SAP용) |
| `--covariate a,b` | 공변량 보정(ANCOVA) — 기저값 등 **수치형** 공변량 열 (long 형식 전용) |
| `--adjust-factor site,stratum` | (ANCOVA) **범주형** 보정인자 열 (기관·층화인자). 단독 사용 가능 |
| `--event-is benefit\|harm` | (`--binary`) 사건이 이로운지 해로운지. 이름은 **사건의 성격과 위험차의 부호를 함께** 봐서 정합니다 — 이로운 사건인데 시험군에서 *덜* 나왔다면 NNH가 맞습니다 |
| `--overwrite` | (`--output`) 기존 파일 덮어쓰기 허용 |
| `--values a,b,c` | 결과 열 여러 개를 한 번에 (엔드포인트 간 보정 포함) |
| `--endpoint-correction holm\|bh\|none` | 엔드포인트 간 다중비교 보정 (기본 holm) |
| `--brief` | (`--values`) 요약표만 출력 |
| `--binary` | 이진(yes/no) 결과 분석 (RD/RR/OR/NNT + χ²·Fisher) |
| `--event-value VALUE` | (`--binary`) '사건'으로 볼 값 지정 |
| `--events-col`, `--n-col` | (`--binary`) 이미 집계된 표 입력 |
| `--binary-test auto\|chisq\|chisq-yates\|fisher` | (`--binary`) 검정 고정 |
| `--equivalence-margin Δ` | 등가성(TOST) 마진 — `1.5`(=±1.5) 또는 `-1.0,2.0` |
| `--ni-margin Δ` | 비열등성 마진(양수) |
| `--ni-direction higher_is_better\|lower_is_better` | 결과값의 좋은 방향 (**`--ni-margin` 사용 시 필수** — 기본값 없음) |
| `--format text\|json\|csv` | 출력 형식 (기본 text) |
| `--output PATH`, `-o` | 결과를 파일로 저장 (csv는 엑셀용 BOM 포함) |
| `--delimiter ';'` | CSV 구분자 강제 지정 (미지정 시 자동 감지: `,` `;` tab `\|`) |
| `--version` | 버전 출력 |

> `--equivalence-margin` / `--ni-margin` 은 **독립 2군 또는 대응 2조건**에서만
> 정의됩니다. 3군 이상에 주면 조용히 무시하지 않고 경고와 함께 건너뜁니다.
> `--reference` 는 독립 비교용, `--baseline` 은 `--paired` 전용입니다.

---

## 검정을 사전 지정하기 (`--test`) — 자동 선택의 대가

기본값(`auto`)은 정규성·등분산을 먼저 검정한 뒤 그 결과에 따라 검정을 고릅니다.
편리하지만 **그 자체가 자료 의존적 선택**이며, 공짜가 아닙니다:

- 소표본·불균형 설계에서 Levene 검정은 검정력이 낮아 **이분산을 놓치고 Student t를
  고르는 일이 잦습니다**. 작은 군의 분산이 더 클 때 Student t는 반보수적입니다 —
  n=(6,18), SD=(3,1)에서 귀무가설이 참인데도 1종 오류가 **약 0.11** 로 커집니다
  (항상 Welch를 쓰면 0.05 근처를 유지).
- 반대로 작은 군의 분산이 더 작으면 지나치게 보수적이 되어 검정력을 버립니다.
- 관측치 하나가 바뀌면 선택된 검정 자체가 바뀔 수 있습니다.

그래서 출력에는 **"검정을 자료에서 골랐습니다(사전 지정이 아님)"** 경고가 항상 붙고,
사전 지정이 필요하면 이렇게 고정합니다:

```bash
statwise data.csv --value isi --group arm --test welch        # 권장 기본값
statwise data.csv --value isi --group arm --test mannwhitney  # 순위검정 사전 지정
```

ICH E9를 따르는 규제 대상 분석이라면 **`--test` 로 사전 지정한 검정을 SAP에 적어
두고 그대로 실행**하세요.

> **범위 주의.** `--test` 는 **독립 2그룹 연속형 비교에만** 적용됩니다. 대응표본,
> 3그룹 이상, 이진 결과 경로는 `--test` 로 고정할 수 없고 **여전히 자료 의존적으로
> 검정을 선택**합니다(각 경로도 그 사실을 경고로 출력합니다). 그리고 이 도구는
> **검증된(validated) 통계 소프트웨어가 아닙니다** — 규제 제출용 분석은 검증된
> 도구로 재현하세요.

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
- 효과크기: t/Welch → **Hedges' g**, 대응 t → **Cohen's d_z** (둘 다 신뢰구간 포함 —
  모든 구간의 신뢰수준은 `--alpha` 를 따라갑니다),
  Mann-Whitney → **rank-biserial r + Cliff's δ**, Wilcoxon → **matched rank-biserial r**,
  ANOVA/Welch-ANOVA → **η²**, Kruskal-Wallis → **η²[H]**.
- **이진 결과 (2군)**: 기대빈도 최솟값 < 5 → **Fisher 정확검정**, 아니면 **Pearson
  카이제곱**(Yates 보정 p도 참고로 표시). **3군 이상**은 기대빈도와 무관하게 k×2
  카이제곱 + **Cramér's V**(Fisher는 2×2 전용), omnibus가 유의하면 쌍별 2×2 사후검정.
  구간 방법은 비율 = **Wilson**, 위험차 = **Newcombe hybrid score**,
  위험비 = **Katz log**, 오즈비 = **Woolf logit**.
- 비모수(Mann-Whitney / Wilcoxon) 검정에는 **위치차의 Hodges-Lehmann 추정값 +
  분포무관 신뢰구간**을 함께 보고합니다(독립표본은 쌍별 차이의 중앙값, 대응표본은
  Walsh 평균의 중앙값; 소표본은 정확 순위분포, 대표본은 정규근사).
  **동점이 없어 정확검정을 쓴 경우** CI와 p값의 판정은 정확히 일치합니다
  (CI가 0을 배제 ⟺ 정확검정이 α에서 기각). 동점이 있어 p값이 정규근사로 계산되면
  둘이 갈릴 수 있는데, 그럴 때는 **판정이 다르다는 경고를 출력**합니다.

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
- **이진 결과의 0인 칸.** 어느 칸이든 0이면 위험비·오즈비의 로그 스케일 신뢰구간이
  정의되지 않습니다. 이때 **신뢰구간에만** Haldane-Anscombe 0.5 보정을 적용하고
  (점추정값은 원자료 그대로) 그 사실을 출력에 명시합니다. 두 군 모두 전원이 사건을
  경험하면 위험비 구간은 아예 보고하지 않습니다.
- **NNT의 신뢰구간.** 위험차 신뢰구간이 0을 포함하면 NNT 구간은 유한 구간이 아니라
  `NNT_benefit ~ ∞ ~ NNT_harm` 형태입니다(Altman 1998). 그럴 때는 깔끔한 두 숫자를
  지어내지 않고 **구간을 보고하지 않으며 이유를 표시**합니다.
- **등가/비열등성 마진은 임상적 결정**입니다. 도구는 마진을 제안하지 않습니다.
  주검정이 순위검정일 때의 TOST는 평균차 t-모형 근사이며 경고를 표시합니다.
  **비율(이진 결과)에 대한 등가/비열등성 검정은 아직 미구현**이며, 시도하면 조용히
  다른 계산을 하지 않고 명시적으로 거부합니다.
- **공변량 보정(ANCOVA)** 은 `--covariate` / `--adjust-factor` 로 지원합니다. 단
  **고정효과 선형모형 하나**만 적합합니다: 그룹 × 공변량 상호작용을 포함한 모형,
  반복측정/혼합모형(random effect), 비선형 공변량 항은 범위 밖입니다. 기울기
  동질성이 기각되면 그 사실을 경고로 알릴 뿐 모형을 바꾸지 않습니다. 또 ANCOVA는
  **완전자료(complete-case)** 로 계산하며, 제외된 행 수를 보고할 뿐 대체하지 않습니다.
- **대응 이진 자료(McNemar)**, **생존분석**, **반복측정 ANOVA/혼합모형**,
  **다중대체(multiple imputation)** 는 범위 밖입니다. 결측은 분석에서 제외하고 그
  개수를 CONSORT식으로 보고할 뿐, 대체하지 않습니다.
- **다중 엔드포인트 보정**은 omnibus p값들에 대해서만 적용합니다(엔드포인트 안의
  사후검정은 별도 패밀리). 계층적 검정(gatekeeping)이나 사전 지정 순서 검정은
  지원하지 않습니다.
- 이 도구는 검정 선택을 **자동화**하지만, 최종 판단은 연구자의 몫입니다 — 근거(`reason`)를
  항상 함께 출력하니 반드시 확인하세요.

---

## 입력 파일이 실제로 어떻게 읽히는가 (알아야 놀라지 않습니다)

| 셀 내용 | 해석 |
|---|---|
| `62.5%` | **62.5** (퍼센트 기호만 떼어냄 — 0.625로 바꾸지 **않습니다**) |
| `1,234.5` | 1234.5 (명확한 미국식 천단위 구분만) |
| `1,5` | **결측** — 유럽식 소수점인지 15인지 알 수 없어 추측하지 않습니다 |
| `"12.3"`, ` 12.3 ` | 12.3 (따옴표·공백 제거) |
| 빈 칸, `NA` `N/A` `NAN` `NULL` `.` `-` `NONE` `MISSING` `#N/A` | 결측 |
| `inf`, `nan`, `1e999`, `1_000`, 전각 숫자 | **결측** (조용히 틀린 값을 만드는 것보다 낫다는 판단) |

- **인코딩**: `utf-8-sig`(BOM 포함) → `cp949`(한글 엑셀) → `latin-1` 순으로 시도하고,
  UTF-8이 아니면 어떤 인코딩으로 읽었는지 **경고에 표시**합니다.
- **구분자**: `,` `;` 탭 `|` 자동 감지, `--delimiter` 로 강제 지정.
- **결측 집계**: long 형식에서 그룹은 있는데 값이 결측인 행은 그 군의 `miss` 로 셉니다.
  이는 **입력 품질 집계이지 CONSORT 참여자 흐름도가 아닙니다** — 무작위배정·중도탈락
  사유별 인원은 도구가 알 수 없습니다.
  wide 형식에서 **빈 칸은 열 길이 차이**로 보고 결측으로 세지 않으며, `NA`·문자 등
  "뭔가 들어 있는데 못 읽는" 칸만 셉니다.
- **`--binary` 값 인식**: `1/0`, `Y/N`, `YES/NO`, `TRUE/FALSE`, `T/F`, `POSITIVE/NEGATIVE`,
  `SUCCESS/FAILURE`, `유/무`, `있음/없음`, `발생/미발생`, `성공/실패`, `반응/무반응`,
  `양성/음성`, `예/아니오` 등(대소문자 무시). 목록에 없는 라벨은 **추측하지 않고**
  `--event-value` 를 요구합니다. 어떤 값을 사건으로 봤는지는 **항상 경고 줄에 출력**되니
  반드시 확인하세요.
- **관측치가 2개 미만인 그룹**은 분석에서 제외하고 경고에 남깁니다(연속형 기준).
- 입력이 잘못되면 `입력 오류: ...` 를 stderr 로 내고 **종료 코드 2** 로 끝납니다.
- **자료 무결성 점검**: 결측 코드로 흔한 값(−9, −99, −999, 999 …)이 숫자로 들어 있거나,
  사분위 범위의 3배를 벗어난 값이 있거나, 대소문자·공백만 다른 그룹 라벨(`Active` vs
  `active`)이 별개 군으로 잡히면 **경고**합니다. 이런 것 하나가 검정 선택과 결론을
  바꿉니다.
- 따옴표 안에 줄바꿈이 있는 셀도 CSV 규칙대로 한 셀로 읽습니다(숫자로 이어붙이지 않음).
- 극단적인 크기에서는 스케일 불변으로 계산하되, **요약통계가 배정밀도 범위를 넘어가면
  (|값| ≳ 1e154) 분석을 거부**합니다. 그대로 계산하면 SD가 무한대가 되어 t=0,
  p=1.000, 효과크기 "negligible" 같은 **완전히 뒤집힌 값**이 나오는데 그 값들은 모두
  유한해서 NaN 검사로는 잡히지 않기 때문입니다. 단위를 바꿔서(원 → 백만원) 다시
  실행하세요.
- 그룹이 60개를 넘으면 사후검정을 생략합니다 — 1000그룹이면 쌍이 499,500개가 되어
  보정 후 사실상 아무것도 검출하지 못하고 보고할 표도 아닙니다(경고로 알립니다).

## Python API

CLI 없이 직접 부를 수도 있습니다:

```python
from statwise import analyze, analyze_paired, EquivalenceSpec, render_text, result_to_dict
from statwise.binary import compare_binary
from statwise.endpoints import run_endpoints
from statwise.report import render_binary_text, render_multi_text, render_csv

res = analyze([("sham", [31.2, 29.8, ...]), ("device", [44.5, 41.0, ...])],
              alpha=0.05, equivalence=EquivalenceSpec(margin=(-5.0, 5.0)))
print(render_text(res))
d = result_to_dict(res)          # JSON-safe dict (NaN/Inf -> None)

b = compare_binary([("device", (15, 24)), ("sham", (5, 24))])
print(render_binary_text(b))

# 공변량 보정 (ANCOVA): 한 행이 한 대상
from statwise import AncovaRecord, run_ancova
from statwise.report import render_ancova_text
# 한 군당 최소 2행씩 — 실제로는 전체 대상 목록이 들어갑니다
records = [AncovaRecord("drug", 11.8, (14.0,), ("busan",)),
           AncovaRecord("drug", 14.2, (17.0,), ("seoul",)),
           AncovaRecord("placebo", 16.1, (15.0,), ("seoul",)),
           AncovaRecord("placebo", 19.4, (18.0,), ("busan",))]
anc = run_ancova(records, covariate_names=["base"], factor_names=["site"],
                 outcome="isi_week8", reference="placebo")
print(render_ancova_text(anc))
```

JSON 출력에는 텍스트 리포트에 없는 항목도 들어 있습니다 — 그룹별 평균의 신뢰구간
`mean_ci`(+`mean_ci_conf`), Hodges-Lehmann의 `method`, 효과크기의 `conf`,
등가성 검정의 전체 필드(`t_low`/`p_low`/`margin_low` 등)까지 포함합니다.
모든 신뢰구간은 `--alpha` 를 따라가며, 각 구간은 자기 신뢰수준을 함께 실어 보냅니다.

## Tests

```bash
python3 -m pytest    # 609 tests, 전부 오프라인, SciPy/statsmodels 참조값과 대조
```

## License

MIT © 2026 hyeonjoong
