# agreestat — 측정 방법 일치도(agreement) 분석기

두 측정 방법(A vs B)을 같은 대상에 적용한 **짝지은 CSV**를 넣으면, 측정 척도에 맞는
일치도 분석을 한 번에 계산하고 **논문에 바로 붙일 수 있는 문장**까지 출력합니다.
외부 라이브러리 없이 **표준 라이브러리만**으로 동작합니다.

| 자료 유형 | 명령 | 계산 내용 |
|---|---|---|
| **연속형** (호흡수, RMSSD, 농도…) | `agreestat data.csv -a A -b B` | **Bland–Altman**(bias·95% LoA·CI), **ICC(2,1)/ICC(3,1)**, **Lin's CCC**, **반복성**, **방법비교 회귀(Deming·Passing–Bablok, CLSI EP09)**, 참고용 Pearson r·대응 t |
| **범주형/순서형** (수면단계, 등급, 양성/음성…) | `agreestat data.csv --categorical -a A -b B` | **Cohen's kappa**·**가중 kappa**, **Gwet's AC1/AC2**, **Scott's pi**, **Krippendorff's alpha**, **범주별 일치도(PPA/NPA)**, **kappa 역설 진단**, **주변 동질성 검정** |

> "두 방법이 같은 **숫자**를 주는가?"와 "두 평가자가 같은 **범주**를 매기는가?"는
> 서로 다른 통계가 필요합니다. `agreestat`는 둘 다 다룹니다.

---

## 목적 / Why this exists

**한국어.** 새 센서를 기준(reference) 방법에 대해 검증할 때 — 예: 비접촉 호흡 신호 vs
호흡밴드/PSG, 워치-HRV vs ECG-HRV — "두 방법이 얼마나 일치하는가?"를 제대로
보고하려면 상관계수(r) 하나로는 부족합니다. **r이 높아도 계통편향(bias)이 크면 두
방법은 서로 바꿔 쓸 수 없습니다.** 올바른 검증에는 (1) Bland–Altman으로 편향과 95%
일치한계(LoA)를, (2) ICC로 신뢰도를, (3) Lin's CCC로 정밀도×정확도를, (4) 반복측정이
있으면 반복성까지 함께 보고해야 합니다. 손으로 하면 번거롭고, ICC는 모델(절대일치 vs
일관성)을 잘못 고르기 쉽습니다. `agreestat`는 이 전 과정을 한 번에 처리하고, 비례
편향·반복측정·분산 0 같은 함정을 자동으로 경고하며, 논문용 문장을 만들어 줍니다.
BELL 수면 디바이스의 호흡/HRV 센서 검증에 바로 쓸 수 있습니다.

**English.** When validating a new sensor against a reference — contactless
respiration vs a chest band/PSG, watch-HRV vs ECG-HRV — a correlation coefficient
is *not* enough: a high `r` with a large systematic bias still means the two methods
are not interchangeable. Proper validation reports (1) Bland–Altman bias and 95%
limits of agreement (LoA), (2) an intraclass correlation for reliability, (3) Lin's
concordance correlation for precision × accuracy, and (4) repeatability when
replicate measurements exist. `agreestat` runs that whole pipeline, warns about the
usual traps (proportional bias, repeated measures, zero variance), and emits a
ready-to-paste Korean validation sentence. It targets the BELL sleep-device sensor
validation work directly.

**그런데 측정값이 '숫자'가 아니라 '범주'라면?** 같은 수면 디바이스라도 호흡수·HRV는
숫자지만 **수면단계(W/N1/N2/N3/REM)는 범주**입니다. 병변 등급, 판독 결과, 양성/음성
판정도 마찬가지입니다. 범주 자료에는 Bland–Altman도 ICC도 쓸 수 없고 **kappa 계열**이
필요한데, 여기에는 숫자 자료에는 없는 고유한 함정이 있습니다:
- **kappa 역설** — 한 범주가 대부분이면 일치도가 90%여도 kappa는 0에 가깝습니다.
  (도구가 자동 감지해 Gwet's AC1·PABAK을 함께 보고합니다.)
- **전체 kappa는 어느 범주가 문제인지 숨깁니다** — 예제에서 kappa=0.694는 "상당함"
  이지만, 범주별로 보면 **N1만 0.301**이고 나머지는 0.84 수준입니다.
- **주변 편향** — 한쪽이 특정 범주를 체계적으로 더 자주 쓰는지는 kappa로 안 보입니다
  (McNemar/Stuart–Maxwell로 검정).
- **군집(피험자 내 반복)** — 한 사람에게서 나온 수백 epoch를 독립으로 세면 신뢰구간이
  몇 배나 좁아집니다. `-s`로 피험자 열을 주면 **군집 부트스트랩 CI**로 바로잡습니다.

**English (categorical).** Sleep stages, lesion grades and positive/negative calls
are *classes*, not numbers: Bland–Altman and ICC do not apply, and the kappa family
brings its own traps — the kappa paradox, a headline kappa that hides which single
category is failing, marginal bias, and epoch-level rows that are not independent.
`agreestat --categorical` covers all four, and (given `-s`) computes cluster-robust
CIs by resampling subjects.

**Who it's for.** 임상·디바이스 연구자, 검증(validation) 논문을 쓰는 사람, "새 측정법이
기존 방법을 대체할 수 있는가?"를 판단해야 하는 누구나 — 측정값이 **숫자든 범주든**.

모든 p값·분위수(정규·t·F·χ²)와 불완전 베타/감마 함수는 **밑바닥부터 표준
라이브러리로** 구현했고, F/정규 분위수는 SciPy와 ≤1e-6 수준으로 일치합니다. ICC 점추정은
Shrout & Fleiss(1979)의 공개된 예제와 **정확히** 일치하며(ICC(2,1)=184/635,
ICC(3,1)=920/1287), CI는 R `psych::ICC`와 일치합니다. 도구 자체는 **의존성이 전혀
없어** Python 3.9+만 있으면 어디서든 동작합니다.

---

## Install

```bash
cd ~/Downloads/02_프로젝트/깃헙/agreestat-측정일치도
python3 -m pip install -e .
```

설치 없이도 실행할 수 있습니다:

```bash
python3 -m agreestat.cli <파일.csv> -a 방법A열 -b 방법B열
```

또는 폴더 안의 **`실행.command` 더블클릭** (예제 데이터로 바로 시연).

---

## Usage

입력은 같은 대상에 두 방법을 적용한 **짝지은 수치 두 열**입니다. (선택) 피험자 ID 열을
주면 반복측정 지표를 계산합니다.

```
subject,contactless_brpm,band_brpm
S01,14.2,14.0
S01,15.1,14.8
S02,11.9,12.3
...
```

### 1) 기본 (열 이름 지정, 또는 두 수치열만 있으면 자동 탐지)

```bash
agreestat examples/resp_rate_good.csv -a contactless_brpm -b band_brpm
```

출력 (요약):

```
[2] Bland–Altman 분석 (절대 / absolute)
    bias (평균차) = -0.012  [95% CI -0.174, 0.151]
    SD of differences = 0.629
    95% 일치한계 LoA = [-1.244, 1.221]
       lower LoA -1.244  [95% CI -1.524, -0.965]
       upper LoA 1.221  [95% CI 0.942, 1.500]
    비례 편향 검정 (diff ~ mean): slope=0.0394, p=0.083  → 비례 편향 없음

[3] ICC (급내상관계수 / intraclass correlation, 단일 측정)
    ICC(2,1) ... = 0.985  [95% CI 0.975, 0.991]  (excellent / 매우 좋음)  ← 보고 권장
    ICC(3,1) ... = 0.985  [95% CI 0.975, 0.991]  (excellent / 매우 좋음)

[4] Lin's CCC
    CCC = 0.985  [95% CI 0.975, 0.991]  (substantial / 상당함)
```

### 2) 비례 편향 + 반복측정 (경고가 발동하는 예제)

```bash
agreestat examples/hrv_rmssd_proportional.csv -a watch_rmssd_ms -b ecg_rmssd_ms -s subject
```

```
    비례 편향 검정 (diff ~ mean): slope=0.1227, p=<0.001  → 비례 편향 있음 ⚠
[5] 반복측정 지표 / Repeatability (within-subject)
    within-subject CV: watch_rmssd_ms=8.73%, ecg_rmssd_ms=7.29%
    반복성 계수(repeatability coeff, 2.77·s_w): watch_rmssd_ms=14.153, ...
[!] 주의 / Warnings
    - 비례 편향(proportional bias)이 감지되었습니다: ...
    - 반복측정 데이터가 감지되었습니다: ...
```

### 3) 백분율 Bland–Altman (비례오차 데이터) · JSON 출력

```bash
agreestat data.csv -a watch -b ecg --percent        # diff% = 100·(A−B)/mean
agreestat data.csv -a watch -b ecg --json           # 기계 판독용 JSON
```

### 4) 범주형 일치도 — kappa (`--categorical`)

평가자·기기가 **숫자가 아니라 범주**를 매길 때 (수면단계 vs PSG, 병변 등급, 양성/음성
판정) 씁니다. 입력은 똑같이 짝지은 두 열입니다.

```
epoch,psg_stage,device_stage
E0001,N2,N2
E0002,W,N1
...
```

```bash
# 명목형(순서 없음) — 수면단계 5단계, 기기 vs PSG
agreestat examples/sleep_stage_device_vs_psg.csv --categorical \
    -a psg_stage -b device_stage --categories "W,N1,N2,N3,REM" --name-a PSG --name-b 기기
```

```
[2] 교차표 / Confusion matrix (행=A, 열=B; 대각선=일치)
    PSG\기기 |  W N1  N2 N3 REM | 합계
    ---------+------------------+-----
    W        | 81  9   3  0   1 |   94
    N1       | 10 11   9  0   4 |   34
    N2       |  6 15 172 10   6 |  209
    N3       |  0  0  19 42   0 |   61
    REM      |  2  4   8  0  68 |   82
    관찰 일치도 po = 0.779  (374/480)

[3] 일치도 계수 / Agreement coefficients
    Cohen's kappa                = 0.694  [95% CI 0.643, 0.744]  (substantial)  ← 보고 권장
    Gwet's AC1                   = 0.731  [95% CI 0.685, 0.776]  (substantial)

[5] 범주별 일치도 / Per-category agreement
    범주 A사용 B사용 둘다 특이적일치도     [95% CI] one-vs-rest κ
    N1      34    39   11        0.301 [0.18, 0.46]         0.244   ← N1만 일치도가 낮음
```

전체 kappa 하나로는 보이지 않는 **"어느 범주에서 틀리는가"**가 드러납니다.

```bash
# 순서형(경증<중등도<중증) — 가중 kappa(기본 quadratic) + 사전 기준 판정
agreestat 등급.csv --categorical --ordinal --categories "0,1,2,3" --min-kappa 0.6
```

`--ordinal`을 주면 **가중 kappa**를 계산합니다: 인접 범주 오분류(1↔2)를 먼 오분류(0↔3)보다
가볍게 취급합니다. `--min-kappa 0.6`은 **점추정이 아니라 신뢰구간 하한**으로 기준 충족을
판정합니다(작은 표본에서 점추정만 보면 과대주장이 되기 때문).

### 옵션

| 옵션 | 의미 |
|------|------|
| `-a`, `--method-a` | 방법 A(측정1) 열 이름 (미지정 시 자동 탐지) |
| `-b`, `--method-b` | 방법 B(측정2/기준) 열 이름 |
| `-s`, `--subject` | 피험자 ID 열 (반복측정 지표 계산) |
| `--name-a`, `--name-b` | 리포트에 표시할 이름 |
| `--percent` | 백분율 Bland–Altman |
| `--alpha 0.05` | 유의수준 / 신뢰구간 폭 (기본 0.05 → 95% CI) |
| `--accept DELTA` | 사전 설정한 임상 허용한계 ±DELTA. 95% LoA가 그 안이면 **교환가능(interchangeable)** 판정을 출력 (`--accept-lower/--accept-upper`로 비대칭 지정 가능) |
| `--target-loa-hw H` | 목표 LoA CI 반너비 H. 그 정밀도 달성에 필요한 표본수 n을 계산 |
| `--deming-lambda L` | Deming 회귀의 오차분산비 λ=Var(err_기준B)/Var(err_검증A) (기본 1.0=직교회귀) |
| `--at XC` | 의학적 결정수준 XC(기준법 값)에서의 예측 계통편향 `bias(XC)=절편+(기울기−1)·XC`를 회귀로 추정(Deming CI 포함). `--accept`와 함께 쓰면 그 지점 편향이 허용한계 안인지 판정 |
| `--encoding` | 입력 CSV 인코딩 (기본: 자동 감지 — UTF-8/UTF-16/CP949/EUC-KR) |
| `--json` | JSON으로 출력 |
| `--markdown [PATH]` | 결과를 논문 부록용 마크다운 표로 출력(경로 생략 시 화면) |
| `--plot-data PATH` | Bland–Altman 플롯 데이터(mean, diff)를 CSV로 저장(R/Excel/Python에서 바로 작도) |
| `--svg PATH` | Bland–Altman 플롯을 SVG 파일로 저장(외부 라이브러리 없이) |

**범주형 전용 옵션** (`--categorical`과 함께 사용; 연속형 전용 옵션과는 함께 쓸 수 없고
섞어 쓰면 오류로 알려 줍니다)

| 옵션 | 의미 |
|------|------|
| `--categorical` | 범주형 일치도(kappa) 분석 수행 (Bland–Altman/ICC 대신) |
| `--ordinal` | 범주가 순서형임을 표시 → **가중 kappa**(기본 quadratic)를 계산해 headline |
| `--weights linear\|quadratic\|unweighted` | 가중치 방식 지정 (지정 시 `--ordinal` 자동 적용) |
| `--categories "W,N1,N2,N3,REM"` | 범주의 **순서**를 직접 지정. 순서형에서 중요하며, 자료에 없는 범주도 표에 포함됩니다 |
| `--min-kappa K` | 사전 설정한 최소 허용 kappa. **신뢰구간 하한**이 K 이상이면 "기준 충족" 판정 |
| `-s`, `--subject` | 피험자 ID 열. 피험자당 여러 행(수면 epoch 등)이면 **군집 부트스트랩 CI**를 계산해 권장 지표로 headline하고, `--min-kappa` 판정도 이 CI로 합니다 |
| `--bootstrap B` | 군집 부트스트랩 재표본 수 (기본 2000) |
| `--seed S` | 부트스트랩 시드 (기본 20260716) — 같은 시드는 항상 같은 CI (재현성) |
| `--na "NA,모름"` | 결측으로 처리할 라벨. **기본은 빈 칸과 `#N/A`만 결측** — 범주형에서 `None`·`-`·`.`·`NA`는 실제 등급일 수 있어 임의로 버리지 않습니다 |

#### 군집 자료(피험자당 여러 행)는 반드시 `-s`를 주세요

수면 epoch처럼 **한 피험자가 수백 행**을 내는 자료에서 각 행을 독립으로 세면 신뢰구간이
심하게 좁아집니다. `-s subject`를 주면 피험자를 재표집하는 군집 부트스트랩으로 바로잡고,
설계효과·유효표본수·피험자별 kappa 분포까지 보고합니다:

```bash
agreestat examples/sleep_stage_clustered.csv --categorical \
    -a psg_stage -b device_stage -s subject --min-kappa 0.70
```

```
[3b] 군집 보정 신뢰구간 / Cluster-robust CI (피험자 재표집 부트스트랩) — 권장
    피험자 20명, 총 1800행 (반복 있는 피험자 20명), 재표본 2000회, seed=20260716
    Cohen's kappa = 0.738  군집 CI [0.680, 0.789]  (SE 0.0275)
       naive CI [0.713, 0.762]  (SE 0.0125) — 각 행을 독립으로 가정, 너무 좁음
    설계효과 design effect = 4.87  →  유효 표본수 ≈ 370 (실제 1800행)
    피험자별 Cohen's kappa 분포 (n=20명): 중앙값 0.770, IQR 0.637–0.837, 범위 0.486–0.907
[!] 주의 / Warnings
    - ⚠ 각 행을 독립으로 가정한 naive CI 하한(0.713)만 보면 기준을 '충족'하는 것처럼
      보이지만, 군집(피험자 내 반복)을 보정하면 기준에 미치지 못합니다.
```

위 예에서 naive CI는 기준 0.70을 통과하지만 **군집 보정 CI는 통과하지 못합니다** —
`--min-kappa` 판정은 군집 CI로 하므로 이런 거짓 통과가 생기지 않습니다.

> **임상 허용한계 예시:** `agreestat data.csv -a watch -b ecg --accept 5` 는 "두 방법의 차이가
> ±5 이내면 임상적으로 교환 가능"이라는 사전 기준에 대해, 95% LoA가 그 한계 안에 드는지
> 판정하고 논문용 문장에 결론까지 넣어 줍니다. 숫자만 내는 게 아니라 **연구자가 실제로 내려야
> 하는 결정**(교환 가능 여부)을 직접 지원합니다.

---

## 무엇을, 왜 그렇게 계산하나 (methods)

- **Bland–Altman**: 차이 = A − B. bias는 차이의 평균, LoA = bias ± 1.96·SD.
  bias의 CI는 t분포로, 각 LoA의 CI는 `Var(LoA)=s²[1/n + 1.96²/(2(n−1))]`
  (Bland & Altman 1999)로 계산합니다. 차이를 평균에 회귀시켜 **비례 편향**(기울기≠0)을
  검정하고, 유의하면 경고합니다. `--percent`는 차이를 `100·(A−B)/mean`으로 바꿔
  비례오차 데이터에 대응합니다. LoA 밖 관측치 비율(정규성 하 ~5% 기대)과 LoA 추정
  정밀도(각 LoA CI의 반너비)도 함께 보고합니다.
- **비례 편향이 감지되면** 회귀 기반 LoA(Bland & Altman 1999 §3)를 자동 계산합니다:
  차이를 평균에 회귀하고 잔차의 SD를 평균의 함수로 모형화(×√(π/2)=1.253)해
  `LoA(mean) = D(mean) ± 1.96·s(mean)`을 산출합니다.
- **반복측정(개인당 여러 쌍)이 감지되면** 반복측정 보정 LoA(Bland & Altman 2007)를
  계산해 **권장 지표로 headline**합니다. 분산성분(σ_b²+σ_w²)으로 within-subject 상관을
  반영하므로, 각 행을 독립으로 가정한 naive LoA의 지나치게 좁은 CI 문제를 해결합니다
  (수면 디바이스처럼 개인당 여러 epoch가 있는 데이터의 올바른 방법).
- **임상 허용한계(`--accept`)**를 주면 95% LoA가 그 안에 드는지로 **교환가능 여부**를
  판정하고, **필요 표본수(`--target-loa-hw`)**는 목표 LoA-CI 정밀도로부터 역산합니다.
- **ICC**: 피험자 × 방법(2열) 이원배치 ANOVA의 평균제곱에서
  **ICC(2,1)**(이원 랜덤, 절대일치, 단일측정)과 **ICC(3,1)**(이원 혼합, 일관성)을
  계산합니다. CI는 F 기반 정확법(Shrout & Fleiss 1979; McGraw & Wong 1996).
  기본으로 **ICC(2,1)**(절대일치)을 보고 권장합니다 — 두 방법을 서로 바꿔 쓰려면
  일관성이 아니라 절대일치가 필요하기 때문입니다. 해석은 Koo & Li(2016):
  <0.5 낮음 / 0.5–0.75 보통 / 0.75–0.9 좋음 / >0.9 매우 좋음.
- **Lin's CCC**: `2·s_ab/(s_a²+s_b²+(ā−b̄)²)` (Lin 1989, 모집단 모멘트). CI는
  z변환 근사(Lin 1989, 2000). 정확도 `Cb = CCC/r`도 함께 보고합니다.
- **반복성**: 피험자당 반복측정이 있으면 일원배치 잔차에서 within-subject SD(s_w)를
  구해 CV(%)와 반복성 계수(2.77·s_w = 1.96·√2·s_w)를 계산합니다(Bland & Altman 1996).
- **방법비교 회귀(CLSI EP09)**: 검증법(A)을 기준법(B)에 회귀합니다
  (`A = 절편 + 기울기·B`). **기울기 CI가 1을 제외하면 비례 편향**, **절편 CI가 0을
  제외하면 상수(계통) 편향**입니다. 단순 최소제곱(OLS)은 기준법에 오차가 없다고
  가정해 기울기를 0쪽으로 편향(regression dilution)시키므로, 두 방법 모두에 오차를
  허용하는 두 추정량을 씁니다: **Deming 회귀**(Linnet 1990; 오차분산비 λ 가정,
  λ=1이면 직교회귀, CI는 leave-one-out 잭나이프 t(n−2))와 **Passing–Bablok 회귀**
  (1983; 분포무관·이상치에 강건, 기울기=전체 쌍별 기울기의 이동중앙값, rank 기반 CI).
  Bland–Altman의 단일 bias 요약이 놓치는 크기-의존/상수 편향을 잡아 줍니다.
  (Deming λ=1 기울기는 scipy의 직교거리회귀 `scipy.odr`와 ≤1e-4로 교차검증됩니다.)
- **결정수준 예측 편향(`--at XC`)**: 임상적으로 중요한 값 XC(기준법 척도)에서 두 방법의
  예측 계통편향 `bias(XC)=절편+(기울기−1)·XC`를 회귀로 추정합니다(Passing–Bablok 점추정 +
  Deming 잭나이프 CI). "이 수준에서 새 기기가 기준보다 얼마나 치우치는가"라는, 임상가가
  실제로 쓰는 숫자입니다. `--accept`와 함께 주면 그 지점의 편향 CI가 허용한계 안인지까지
  판정합니다. 권장 회귀는 기본 Passing–Bablok(강건), 오차가 정규·등분산이면 Deming입니다.
- **Pearson r + 대응 t검정**: 참고용입니다. **r은 일치도가 아닙니다** — 계통편향을
  감지하지 못하므로 리포트에서 명시적으로 경고합니다. (대응 t검정 = bias≠0 검정.)

### 범주형(`--categorical`)

- **Cohen's kappa**: `κ=(po−pe)/(1−pe)`, `pe`는 두 평가자의 주변확률 곱의 합.
  CI는 Fleiss, Cohen & Everitt(1969)의 점근분산으로, H0: κ=0 검정은 **다른**
  (H0 전용) 분산으로 계산합니다 — 두 분산을 혼동하는 구현이 흔합니다.
  (sklearn `cohen_kappa_score`, statsmodels `cohens_kappa`와 1e-12로 교차검증.)
- **가중 kappa**(`--ordinal`): 불일치에 거리 가중치를 줍니다(linear=|i−j|,
  quadratic=(i−j)²; 범주 라벨이 숫자면 **라벨의 실제 값 간격**을 씁니다 — 0,1,2,4처럼
  불균등한 척도를 순위로 뭉개지 않습니다). 분산은 같은 Fleiss 계열 공식이며,
  가중치가 항등행렬이면 비가중 kappa 분산으로 **정확히** 환원됩니다.
  quadratic 가중 kappa는 점수에 대한 ICC(2,1)과 점근적으로 일치합니다(테스트로 확인).
- **kappa 역설과 Gwet's AC1/AC2**: 한 범주가 대부분을 차지하면 `pe`가 부풀려져
  **관찰 일치도가 높은데 kappa는 0에 가까운** 현상이 생깁니다(Feinstein & Cicchetti
  1990). 도구는 이를 자동 감지해 경고하고, Byrt(1993)의 **유병률 지수(PI)·편향 지수(BI)·
  PABAK**, 그리고 유병률에 강건한 **Gwet's AC1/AC2**(Gwet 2008; `pe`를
  `(1/(K−1))·Σπ_k(1−π_k)`로 대체, 분산은 Gwet의 선형화 추정량)를 함께 보고합니다.
- **이 주변분포에서 가능한 최대 kappa**(Cohen 1960): 두 평가자의 범주 사용 빈도가
  다르면 완전일치 자체가 구조적으로 불가능합니다. 최대 kappa가 1보다 한참 작으면
  낮은 kappa의 원인이 '불일치'가 아니라 '주변분포 차이'일 수 있습니다.
- **범주별 일치도**: 특이적 일치도 `2·n_ii/(r_i+c_i)`(Cicchetti & Feinstein 1990) —
  2×2에서는 FDA의 **PPA/NPA**와 동일합니다 — 와 one-vs-rest kappa를 범주마다 냅니다.
  전체 kappa가 숨기는 **"특정 범주에서만 나쁜 일치"**를 드러냅니다.
- **주변 동질성**: 2×2는 **McNemar**(불일치 셀이 적으면 정확 이항검정, 많으면 연속성
  보정 χ²), k×k는 **Stuart–Maxwell**(χ², df=K−1). "한 평가자가 특정 범주를 체계적으로
  더 많이 쓰는가?"라는, kappa 하나로는 드러나지 않는 **계통 편향**을 검정합니다.
  (statsmodels와 교차검증.)
- **Scott's pi / Krippendorff's alpha**: 참고용으로 함께 보고합니다. alpha는 일치행렬
  (coincidence matrix)로 계산하며 nominal/ordinal 차이함수를 지원합니다.

## Notes / limitations

- **상관(r)은 일치도가 아닙니다.** r=0.99여도 한 방법이 다른 방법보다 항상 +5만큼
  크면 두 방법은 바꿔 쓸 수 없습니다. 판단은 Bland–Altman/ICC/CCC로 하세요.
- ICC(2,1)의 CI는 표본이 작거나 계통차가 크면 매우 넓어질 수 있습니다(정상입니다).
  **점추정이 아니라 신뢰구간 하한으로 등급을 판단하세요(Koo & Li 2016).** 도구도
  점추정 등급이 CI 하한 등급보다 높으면 경고하고, 논문용 문장은 CI 하한 기준으로 등급을 씁니다.
- 반복측정이 있으면 각 행을 독립으로 가정한 기본 LoA가 **좁게** 나올 수 있습니다.
  도구가 이를 감지해 경고하며, 필요하면 반복측정용 방법(Bland & Altman 2007)을 쓰세요.
- 한 방법의 분산이 0(모두 같은 값)이면 **Pearson r과 그 신뢰구간, CCC의 신뢰구간**이
  정의되지 않아 `NaN`(JSON에서는 `null`)으로 나옵니다. ICC/CCC 점추정은 퇴화(degenerate)값
  (예: CCC=0, ICC≈0)이 나올 수 있어 신뢰할 수 없으므로, 도구가 경고하고 **논문용 문장에서는
  ICC/CCC를 제외**합니다. (Bland–Altman의 bias·LoA와 그 신뢰구간은 여전히 계산됩니다.)
- 입력에 `inf`·`-inf`·`1e999`(→무한대) 같은 비정상 수치가 있으면 해당 쌍을 제외하고
  개수를 경고합니다(한 셀의 무한대가 전체 통계를 오염시키지 못하도록).
- 한국어 Excel에서 저장한 CSV는 보통 CP949/EUC-KR 인코딩입니다 — 자동 감지하며,
  안 되면 `--encoding cp949`로 지정하세요.
- **방법비교 회귀**는 검증법(A)을 기준법(B)에 회귀합니다(`-b`가 기준). Passing–Bablok은
  두 방법이 **양의 관계**(둘 다 같은 양을 측정)라고 가정하므로 강한 음의 관계에는
  적합하지 않습니다. Deming의 기본 λ=1(직교회귀)은 두 방법의 측정오차가 비슷할 때
  적절하며, 기준법(B)이 훨씬 정밀하면(오차가 작으면) λ를 **작게**, 검증법(A)이 훨씬
  정밀하면 λ를 **크게** 주세요(λ=Var(err_기준B)/Var(err_검증A)). n이 작으면(<10) rank 기반
  Passing–Bablok CI가 계산되지 않을 수 있고(그때 CI 생략), 이는 정상입니다.
- 이 도구는 계산을 자동화하지만 최종 판단은 연구자의 몫입니다 — 경고를 꼭 확인하세요.

### 범주형 분석의 한계

- **범주형은 2명(2열) 완전자료만** 지원합니다. 평가자가 3명 이상인 설계(Fleiss' kappa)나
  결측이 있는 설계는 아직 지원하지 않습니다 — 결측이 있는 행은 짝 단위로 제외됩니다.
- **군집(피험자 내 반복) 자료에서 `-s`를 주지 않으면 신뢰구간이 너무 좁습니다.** 각 행을
  독립으로 가정하기 때문입니다(위 예에서 설계효과 ≈ 4.9배). 피험자당 행이 여러 개면
  **반드시 `-s`로 피험자 열을 지정**하세요. 도구는 피험자 열이 있을 때만 군집을 감지할 수
  있으며, 열이 없으면 군집 여부를 알 방법이 없습니다.
- 군집 부트스트랩은 **피험자 수가 적으면(<10명) 불안정**합니다(경고합니다). 백분위
  부트스트랩이라 극단적으로 치우친 분포에서는 BCa보다 보수적이지 않을 수 있습니다.
- **결측 처리는 기본이 '빈 칸만'입니다.** `NA`·`None`·`-`·`.`는 실제 범주로 취급하며,
  결측이면 `--na`로 직접 지정해야 합니다 (연속형 로더와 규칙이 다릅니다). 이는 의도된
  차이입니다 — `None/Mild/Severe`, `+/-` 같은 실제 척도를 조용히 지우지 않기 위해서입니다.
- **범주가 200종을 넘으면 거부**합니다 — 연속형 자료를 `--categorical`로 잘못 분석하는
  경우를 막기 위해서입니다(k×k 행렬이 메모리를 폭발시킵니다).
- **라벨은 문자 그대로 다룹니다.** `pos`와 `Pos`, `2`와 `2.0`은 **다른 범주**입니다
  (연구자의 범주 정의를 도구가 임의로 합치지 않기 위한 의도적 선택). 원자료 표기를
  먼저 통일하세요. 자료에 없는 범주도 `--categories`로 지정하면 표에 포함됩니다.
- **순서형 범주의 순서는 도구가 알 수 없습니다.** 라벨이 숫자면 숫자 크기순으로,
  아니면 **알파벳순**으로 가정하고 경고합니다 — `mild/moderate/severe`는 우연히 맞지만
  `경증/중증/중등도`는 틀립니다. 순서형이면 `--categories`로 순서를 **직접 지정**하세요.
- **가중 kappa는 가중치 선택에 민감합니다.** quadratic은 linear보다 대체로 높게 나오므로
  어떤 가중치를 썼는지 반드시 밝혀야 합니다(도구는 리포트·JSON에 명시합니다).
- **kappa·AC1·PABAK은 서로 다른 값이며, 유리한 것만 골라 쓰면 안 됩니다.** 주 지표는
  **분석 전에** 정하세요. 도구는 역설이 감지될 때만 AC1/PABAK을 강조하며, 세 값을 모두
  보고할 것을 권장합니다. Landis & Koch 등급표를 AC1/AC2에도 (관례상) 적용하지만,
  AC1은 chance 보정 방식이 달라 같은 등급 경계가 엄밀히 타당하다는 근거는 약합니다.
- **Landis & Koch(1977) 등급은 관례일 뿐 임상적 근거가 아닙니다.** 등급보다 CI와
  `--min-kappa`로 사전 설정한 기준이 낫습니다. 도구는 등급을 **점추정이 아니라 CI 하한**
  으로 판단하도록 안내합니다.
- **특이적 일치도의 CI는 근사**입니다: 두 평가자의 그 범주 사용을 독립 시행으로 본
  Wilson 구간이라 실제보다 좁을 수 있어 참고용으로만 보고합니다(점추정 자체는 정확).
- kappa의 정규근사 CI는 **n이 작거나(<30) 희소 범주가 있으면 부정확**합니다 —
  도구가 경고합니다.

## Tests

```bash
python3 -m pytest -q    # 383개 테스트, 전부 오프라인 (네트워크 불필요)
```

이 중 **344개는 순수 표준 라이브러리만으로 실행**되고, 나머지 39개는 교차검증
테스트라 numpy/scipy/sklearn/statsmodels가 있을 때만 수집됩니다(없으면 자동 skip).
따라서 의존성이 없는 환경에서는 `344 passed, 1 skipped`가 정상입니다.

**연속형:** ICC는 Shrout & Fleiss(1979) 공개 예제와 1e-9, Bland–Altman·CCC는 손계산 값과
일치하며, numpy/scipy가 있으면 Pearson·대응 t·F분위수·ICC CI·CCC·Deming(scipy.odr)을
교차검증합니다. **범주형:** kappa·가중 kappa는 **sklearn**과, kappa의 SE·CI·z·p와
McNemar·Stuart–Maxwell은 **statsmodels**와 1e-12~1e-8 수준으로 교차검증하고, 참조 구현이
없는 Gwet's AC1의 선형화 분산은 **부트스트랩 SE**와 대조합니다. AC1·kappa 역설 값은
Gwet(2008)의 공개 예제([[118,5],[2,0]] → po=0.944, κ=−0.02, AC1=0.94)와 일치합니다.
교차검증 테스트는 해당 패키지가 없으면 자동으로 건너뛰므로 **순수 표준 라이브러리
환경에서도 전체 통과**합니다.

## License

MIT © 2026 hyeonjoong
