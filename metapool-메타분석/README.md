# metapool — 메타분석기 (meta-analysis in one command)

연구 목록 CSV 한 장으로 **효과크기 합성(고정효과·변량효과, τ² 4종) · 이질성(Q·I²·τ²와 그 신뢰구간·예측구간) ·
하위군 분석 · leave-one-out 민감도와 영향력 진단 · 출판편향(Egger·Begg·trim-and-fill·깔때기그림) ·
NNT/절대위험차 · 텍스트 숲그림 · 결과 CSV 내보내기 · 논문에 그대로 붙일 한국어/영어 문장**까지
한 번에 만들어 주는 명령줄 도구입니다. **외부 의존성 0** (표준 라이브러리만).

## 목적 / Why this exists

**한국어.** 체계적 문헌고찰을 하다 보면 결국 "추출표(엑셀) → 통합 효과크기 → 이질성 → 숲그림 → 결과 문단"을
매번 다시 만들게 됩니다. RevMan은 설치·프로젝트 관리가 무겁고, R의 `metafor`/`meta`는 좋지만 코드를 다시 찾아
쓰게 되며, 그때마다 τ² 추정법·Hartung–Knapp 보정·예측구간 같은 결정을 매번 손으로 챙겨야 합니다. metapool은
그 반복을 명령 한 줄로 끝내고, **어떤 모형을 썼는지·연구 몇 편인지·검정력이 부족한 검정은 무엇인지까지 결과에
같이 적어 주는** 것을 목표로 합니다. 임상시험 근거를 요약해야 하는 연구자(예: 수면 중재 RCT 통합, 난청 재활
훈련 효과 통합)가 초안을 30초 만에 얻고, 그 뒤 해석에 시간을 쓰게 하려고 만들었습니다.

**English.** Every systematic review ends up repeating the same chain: extraction spreadsheet → pooled effect →
heterogeneity → forest plot → a paragraph of Results. RevMan is heavy, and `metafor`/`meta` in R are excellent but
mean re-deriving the same choices (τ² estimator, Hartung–Knapp correction, prediction interval) each time.
metapool collapses that into one command and — importantly — **reports the choices and the limits alongside the
numbers** (it tells you when Egger's test is underpowered, when a single study flips the conclusion, when a zero
cell forced a continuity correction). It is meant for a clinical researcher who needs a defensible first draft of a
meta-analysis in seconds, so the time goes into interpretation instead of plumbing.

**언제 쓰나 / When to reach for it.** 체계적 문헌고찰·메타분석 원고를 쓸 때, 학회 초록용으로 빠르게 통합 추정치가
필요할 때, 남이 보고한 메타분석 결과를 재현·검증하고 싶을 때, 또는 연구계획서에 "예상되는 통합 효과크기"를
넣어야 할 때. — *Reach for it when you are writing a systematic review or meta-analysis, need a pooled estimate
quickly for a conference abstract, want to reproduce and check someone else's published meta-analysis, or need a
plausible pooled effect size for a grant or protocol.*

## 설치

```bash
cd metapool-메타분석
python3 -m pip install -e .
```

설치 없이도 됩니다: `python3 -m metapool <파일>`.
**아래 예시의 `metapool …` 은 설치한 경우이고, 설치하지 않았다면 그대로
`python3 -m metapool …` 으로 바꿔 읽으세요.**

## 입력 CSV 형식 (다섯 중 하나)

| 형식 | 필요한 열 | 계산되는 지표 |
|---|---|---|
| 이미 계산된 효과크기 | `study, effect, se` (또는 `ci_low, ci_high`) | `generic` |
| 연속형 2군 원자료 | `study, n1, mean1, sd1, n2, mean2, sd2` | `smd`(Hedges g) 또는 `md` |
| 이분형 2군 원자료 | `study, events1, n1, events2, n2` (`a,b,c,d`도 인식) | `or`, `rr`, `rd` |
| 상관계수 | `study, r, n` | `cor` (Fisher z에서 합성, r로 보고) |
| 단일군 비율(유병률·반응률) | `study, events, n` | `prop` (logit에서 합성, 비율로 보고) |

- 선택 열 `subgroup` 이 있으면 하위군 분석이 자동으로 붙습니다.
- **1군 = 처치/실험군, 2군 = 대조군.** 효과 방향은 항상 `1군 − 2군` 또는 `1군 / 2군`입니다.
- 열 이름은 흔한 변형(`ne/nc`, `mean.e`, `Author`, `연구`, `실험군n` …)을 자동 인식하고,
  안 되면 `--map 원본열=표준열` 로 알려주면 됩니다.

## 사용 예시

### 1) 연속형 원자료 → 표준화 평균차 + 하위군

```bash
metapool examples/breathing_isi_smd.csv --outcome "ISI 총점"
```

실제 출력 (일부. v1.1.0 은 여기에 I²의 신뢰구간·Begg·trim-and-fill·깔때기그림·표준화 잔차가 더 붙습니다):

```
==============================================================================
메타분석 결과 — Hedges g(표준화 평균차)
==============================================================================
입력 파일   : breathing_isi_smd.csv
연구 수 (k) : 8   총 표본 N = 691
주 모형     : 변량효과 random-effects (tau² 추정: DL, Hartung–Knapp 보정 CI)

── 개별 연구와 통합 효과 ────────────────────────────────────────
연구           효과 [95% CI]            가중치  숲그림 (│ 무효과선, ■ 연구, ◆ 통합, ◇ 예측구간)
-----------------------------------------------------------------------------------------
Kim 2021       -0.720 [-1.224, -0.217]  10.2%     ───────────■────────────    │          
Lee 2022       -0.738 [-1.164, -0.312]  12.9%      ──────────■──────────      │          
Park 2022      -0.195 [-0.717, 0.328]   9.7%                 ────────────■────┼───────   
Choi 2023      -0.843 [-1.217, -0.469]  15.0%     ────────■─────────          │          
Jung 2023      -0.092 [-0.643, 0.460]   9.0%                   ─────────────■─┼─────────►
Han 2024       -0.496 [-0.885, -0.107]  14.4%            ─────────■─────────  │          
Seo 2024       -0.178 [-0.626, 0.271]   12.0%                  ───────────■───┼──────    
Yoon 2025      -0.623 [-0.961, -0.284]  16.8%           ───────■────────      │          
-----------------------------------------------------------------------------------------
통합(변량효과) -0.520 [-0.754, -0.286]  100%                ──────◆─────      │          
통합(고정효과) -0.537 [-0.688, -0.386]  100%                  ───◆────        │          
95% 예측구간   [-0.984, -0.056]                        ───────────◇────────── │          
                                                -1.30                                0.45

── 이질성 ─────────────────────────────────────────────────────
  Q(7) = 10.84, p = .146
  I² = 35.4% (중간 이하)   H² = 1.55   τ² = 0.026 (τ = 0.163, DL)

── 하위군 분석 ────────────────────────────────────────────────
  app                  (k=3) : -0.159  [-0.288, -0.030]   I² = 0.0%
  device               (k=5) : -0.678  [-0.846, -0.510]   I² = 0.0%
  하위군 간 차이: Q_between(1) = 8.97, p = .003 → 하위군 간 효과 차이가 유의함

── 출판편향 / 소규모연구 효과 ──────────────────────────────────
  Egger 회귀 절편 = 4.36 (SE 2.37), t(6) = 1.84, p = .116
  → 비대칭의 뚜렷한 근거는 없습니다.
  ⚠ 연구가 8편(<10)이라 이 검정들의 검정력은 낮습니다. Cochrane은 10편 미만에서 시행을 권하지 않습니다.

── 논문에 붙일 문장 ────────────────────────────────────────────
  [KO] 변량효과 모형으로 8편의 연구(총 691명)를 통합한 결과, ISI 총점에 대한 표준화 평균차는
       -0.520 (95% CI -0.754 ~ -0.286, t(7) = -5.25, p = .001)로 통계적으로 유의하였다. …
       하위군 분석에서 app -0.159, device -0.678로, 하위군 간 차이는 유의하였다
       (Q_between(1) = 8.97, p = .003). 연구를 하나씩 제외한 민감도 분석에서 통합 추정치는
       -0.571 ~ -0.467 범위였으며, 어느 한 편을 제외해도 결론은 유지되었다.
       연구가 8편(<10)이어서 깔때기그림 비대칭은 형식적으로 평가하지 않았다.
  [EN] A random-effects meta-analysis of 8 studies (N = 691) yielded, for ISI 총점, a pooled
       Hedges' g of -0.520 (95% CI -0.754 to -0.286, t(7) = -5.25, p = .001), which was
       statistically significant. …
```

### 2) 이분형 2×2 → 오즈비 (자동 판별)

```bash
metapool examples/adherence_or.csv
# 통합(변량효과) OR = 1.583 [1.329, 1.885], 로그척도에서 합성 후 지수변환
```

### 3) 이미 계산된 효과크기 + 옵션

```bash
metapool examples/published_effects.csv --tau2 PM --sort effect     # Paule–Mandel τ²
metapool 데이터.csv --measure rr --subgroup 국가                     # 위험비 + 하위군
metapool 데이터.csv --json -o 결과.json                              # 기계가 읽을 형식
metapool 데이터.csv --md  -o 결과.md                                 # 원고용 마크다운
metapool 데이터.csv --map 실험군수=n1 --map 대조군수=n2               # 열 이름이 다를 때
```

```bash
metapool 데이터.csv --outcome "ISI 총점"          # 논문 문장에 결과변수 이름을 넣어 줌
metapool 데이터.csv --input-conf 0.90            # 입력 파일의 CI가 90% 구간일 때
```

### 4) 상관계수 · 단일군 비율 · 임상 해석(NNT) · 결과 표

```bash
metapool examples/adherence_correlation.csv                    # r, n 열 → Fisher z 합성, r로 보고
metapool examples/response_rate_prop.csv                       # events, n 열 → logit 합성, 비율로 보고
metapool examples/adherence_or.csv --baseline-risk 0.20        # 기저위험 20% 기준 NNT·절대위험차
metapool examples/adherence_or.csv --tau2 REML                 # metafor 기본값과 같은 REML τ²
metapool examples/adherence_or.csv --csv -o 결과표.csv          # 연구별+통합+민감도 tidy CSV
```

주요 옵션: `--measure`(지표 강제) · `--model random|fixed` · `--tau2 DL|PM|REML|SJ` · `--no-hksj` ·
`--conf`(출력 신뢰수준, ≤0.999) · `--input-conf`(입력 CI의 신뢰수준, 기본 0.95) · `--outcome` ·
`--cc`(연속성 보정값) · `--log-input` · `--baseline-risk`(NNT 기준 대조군 위험) ·
`--trimfill-estimator L0|R0` · `--no-forest/--no-funnel/--no-sensitivity/--no-bias/--no-trimfill` ·
`--sensitivity-max` · `--sort` · `--json/--md/--csv/--out`. 전체 목록은 `metapool --help`.

> `--log-input`(effect 열이 OR/RR 같은 비(ratio) 값일 때)은 `ci_low/ci_high` 열을 함께 요구합니다.
> 비 척도의 표준오차는 로그 척도로 그대로 옮길 수 없어서, 조용히 틀리는 대신 거부합니다.

## 무엇을 계산하나 (방법)

| 항목 | 방법 |
|---|---|
| 고정효과 | 역분산 가중(inverse-variance) |
| 변량효과 | τ² 추정 4종: DerSimonian–Laird(기본) · Paule–Mandel · **REML**(metafor 기본값, 점수함수 이분법) · **Sidik–Jonkman** |
| 신뢰구간 | 기본 **Hartung–Knapp** 보정(t 분포, df = k−1). `--no-hksj` 로 고전적 z 사용 |
| 이질성 | Cochran Q(편차 형태로 계산), I² = max(0, (Q−df)/Q), H² = max(1, Q/df), τ², 예측구간(t, df = k−2, 모형기반 SE) |
| I² / H² | **선택한 τ² 추정법 기준**으로 I² = τ²/(τ²+s²), H² = (τ²+s²)/s² (s² = Higgins–Thompson 전형적 연구내 분산). DL에서는 고전적 (Q−df)/Q, Q/df 와 대수적으로 동일 |
| τ²·I² 신뢰구간 | **Q-profile**(Viechtbauer 2007): 일반화 Q(τ²) = χ²_{1−α/2}, χ²_{α/2} 를 풀어 얻음 |
| 상관계수 | Fisher z = atanh(r), var = 1/(n−3) 로 합성 후 tanh 로 되돌림 |
| 단일군 비율 | logit(p) = log(e/(n−e)), var = 1/e + 1/(n−e). 0%·100% 연구만 `--cc` 보정 |
| SMD | Hedges g (J = 1 − 3/(4df−1)), var(g) = J²[(n1+n2)/(n1n2) + d²/(2(n1+n2))] |
| OR / RR | 로그 척도에서 합성 후 지수변환, 어느 칸이든 0이면 연속성 보정(+0.5, `--cc`) 후 경고 |
| 하위군 | 하위군별 τ² 각각 추정 → Q_between(모형기반 SE 사용), df = G−1 |
| 민감도 | leave-one-out 재합성 + 잔여 I² + 표준화 잔차(|값|>2면 이상치 후보), 유의성 결론이 뒤집히면 경고 |
| 출판편향 | Egger 회귀(절편, t, df = k−2) · **Begg–Mazumdar 순위상관**(동점이 없고 k ≤ 20이면 순열 **정확검정**, 아니면 정규근사) · **Duval–Tweedie trim-and-fill**(L0/R0, 방향 자동 판정) · 텍스트 깔때기그림. k<10이면 검정력 부족 경고 |
| 임상 해석 | 가정 대조군 위험에서 실험군 위험·절대위험차·1000명당 사건수·**NNT/NNH**(OR·RR·RD 한정). 기본 기저위험은 포함 연구의 대조군 사건률, `--baseline-risk` 로 변경 |
| 내보내기 | 텍스트 · 마크다운 · JSON · **tidy CSV** |

`--csv` 표의 열: `row_type`(study/pooled/subgroup/leave_one_out/prediction/trim_and_fill) ·
`label` · `subgroup` · `k` · `effect`/`ci_low`/`ci_high`(**보고 척도**) ·
`effect_analysis_scale`/`se_analysis_scale`(분석 척도) · `weight_fixed_pct`/`weight_random_pct` ·
`statistic`/`p_value`(그 행 통합값의 z 또는 t 와 p — 지표에 무효과값이 없으면 비움) ·
`tau2` · `I2_percent` · `std_residual`(leave-one-out 전용) · `n_total`.
열의 뜻은 행 종류에 따라 바뀌지 않습니다. 연구명이 `=`·`+`·`@` 로 시작하면 엑셀이 수식으로
실행하지 않도록 앞에 작은따옴표를 붙입니다.
| 분포 함수 | 정규·t·카이제곱을 직접 구현 (erfc·불완전베타·불완전감마). 메타분석에서 쓰는 범위(신뢰수준 ≤0.999, df ≥ 1)에서 참값과 1e-9 이내로 일치하도록 상수 대조 시험을 둡니다 |

## 한계 / 정직한 고지

- **논문 문장은 한국어만 "그대로" 붙일 수 있습니다.** 영어 문장은 문법은 맞지만 하위군 이름 등이
  한국어 그대로 들어가므로, 영문 투고 전에 손을 보세요.
- **자동 분석이지 판단 대행이 아닙니다.** 연구 선정·비뚤림 평가(RoB)·이질성의 임상적 해석은 사람이 해야 합니다.
- 제공하지 않는 것: 메타회귀(연속형 조절변수), 네트워크 메타분석, 다변량/다수준 모형, 개별환자자료(IPD) 분석,
  Harbord/Peters 비대칭 검정, Freeman–Tukey 이중아크사인 비율 변환, 그래픽 숲그림(PNG/PDF).
- **trim-and-fill 은 참값 추정이 아니라 "출판편향이 있었다면 이 정도"라는 민감도 분석**입니다. 이질성이 크면
  누락 연구 수(k0)를 과대추정하는 것이 잘 알려져 있으므로, 보정 추정치를 주 결과로 쓰지 마세요.
- **τ²·I² 의 Q-profile 구간은 연구 수가 적으면 거의 (0%, 100%)에 가깝게 나옵니다.** 이는 오류가 아니라
  "그 자료로는 이질성의 크기를 사실상 모른다"는 정직한 답이며, 오히려 I² 점추정만 믿는 것을 막아 줍니다.
- **NNT 는 가정한 대조군 위험에 전적으로 의존합니다.** 기본값은 포함 연구들의 대조군 사건률이지만, 실제 진료
  집단의 기저 위험이 다르면 NNT 도 달라집니다 — 보고할 때 어떤 기저 위험을 썼는지 반드시 함께 밝히세요.
  또 도구는 "사건"이 좋은 결과인지 나쁜 결과인지 알 수 없어, 사건이 늘면 기계적으로 NNH 라고 이름 붙입니다.
- **단일군 비율(`prop`)에는 무효과선이 없습니다** — logit 0은 50%일 뿐입니다. 그래서 숲그림에 세로선을 긋지
  않고, 논문 문장에도 유의성 검정을 싣지 않습니다. 극단적으로 낮은 비율(0에 가까운)에서는 logit 변환의
  분산 근사가 나빠지므로 연구 수와 사건 수를 함께 보고하세요.
- **상관계수(`cor`)는 각 연구의 표본이 독립이라고 가정**합니다. 같은 코호트에서 여러 상관을 뽑아 넣으면
  가중치가 부풀려집니다(다변량 모형이 필요하지만 제공하지 않습니다).
- **τ² 추정법마다 답이 다릅니다.** REML(metafor 기본값)은 점수함수의 근을 이분법으로 찾으므로
  수렴이 보장되지만, 그래도 수렴하지 못하면 **경고를 띄웁니다**. **Sidik–Jonkman은 구조상 τ² > 0** 이라 이질성이 없을 때 τ²를
  과대추정하고 신뢰구간을 지나치게 넓힙니다(모든 효과크기가 같으면 임의의 대체값을 씁니다).
- **Begg 검정의 p값은 동점이 없고 k ≤ 20이면 순열 정확검정**입니다. 정규근사는 연구 수가 적을 때
  심하게 비보수적이어서(k = 4·완전 일치에서 정확 p = .083인데 근사는 .042) 나올 수 없는 유의성을
  만듭니다. 동점이 있으면 정규근사로 돌아가고 그 사실을 결과 옆에 적습니다 — 이때 p값은 실제보다
  작게 나올 수 있습니다.
- **τ²·I²의 Q-profile 구간은 τ² 추정법과 무관하게 계산됩니다**(metafor `confint`와 동일). Q-profile은
  Paule–Mandel 추정방정식을 뒤집은 것이라 **PM 점추정만 항상 구간 안에** 들어가고, DL·SJ에서는
  점추정이 구간 밖에 놓일 수 있습니다. 이상해 보이면 `--tau2 PM` 으로 한 번 더 돌려 보세요.
- `--tau2 DL`은 관행적 기본값이지만 연구 수가 적고 이질적일 때 τ²를 과소추정하는 경향이 있습니다 —
  이 경우 `--tau2 PM` + 기본 Hartung–Knapp 보정을 권합니다.
- I²는 "이질성의 양"이 아니라 "총 변동 중 연구간 변동의 비율"이며, 연구 수가 적으면 매우 불안정합니다.
- Egger·Begg·trim-and-fill 은 연구 10편 미만이면 사실상 무의미합니다(도구가 경고를 붙이고,
  **세 가지 모두 논문 문장에는 수치를 넣지 않습니다**).
  또 **이분형(OR/RR)에서는 효과크기와 표준오차의 구조적 상관 때문에 위양성이 잦습니다** — Cochrane이 권하는
  Harbord/Peters 검정은 제공하지 않으므로 참고용으로만 보세요.
- **단일군 비율(`prop`)에서는 깔때기그림 비대칭 검정을 해석하지 마세요.** 분산 1/e + 1/(n−e) 가
  효과크기의 함수라 비대칭이 구조적으로 보장됩니다 — 깨끗한 합성 자료에서도 Egger가 p < .001을
  냅니다. 도구가 이 사실을 결과 옆에 적고 비대칭 판정 문구를 내보내지 않습니다.
  (상관계수 `cor` 은 var = 1/(n−3) 이 r 과 무관해 이 문제가 없습니다.)
- **Hartung–Knapp 보정은 이질성이 거의 없을 때(I² ≈ 0) 오히려 고정효과 구간보다 좁은 신뢰구간을 만들 수
  있습니다**(ad hoc 절단 미적용). 이 경우 도구가 경고를 띄우니 `--no-hksj` 결과와 함께 확인하세요.
- 하위군 간 검정은 **하위군마다 τ²를 따로 추정**합니다. RevMan/metafor의 기본값(공통 τ²)보다 하위군 차이를
  크게 잡는(=덜 보수적인) 경향이 있으니, 결과를 보고할 때 이 선택을 함께 밝히세요.
- 중앙값(IQR)만 보고된 연구는 자동 변환하지 않고 제외합니다 — 평균·SD로 직접 바꿔 넣어야 합니다.
- 인터넷에 접속하지 않고, 입력 파일 밖으로 아무것도 내보내지 않습니다. 계산은 전부 로컬입니다.
  `--out`은 입력 파일 자체를 덮어쓰려 하면 거부하고, 기존 파일을 덮어쓸 때는 경고합니다.

## 테스트

```bash
python3 -m pytest -q     # 342개 통과 (분포 참값 대조, 손계산 검증, CSV 견고성, CLI 종단, 적대적 검토 회귀)
```

주요 수치는 손으로 계산한 값과 대조합니다 — 예: 고정효과 추정치 60.3/129, Cochran Q = 1.0232558,
DL τ² = 0.15, Hedges g = 148/151, log(OR) = log(4/9), Egger 절편 = 0(정확), qt(0.975, 10) = 2.2281389,
χ²(0.95, 5) = 11.0704977. REML τ²는 수렴한 값이 추정방정식의 고정점인지, Q-profile 상·하한은 일반화 Q가
정확히 χ² 분위수와 같아지는지 되풀어 확인하고, trim-and-fill 은 자료의 부호를 뒤집으면 답도 뒤집히는지
(대칭성) 검사합니다.
`tests/test_hardening.py`는 적대적 검토에서 실제로 발견된 결함(0셀+`--cc 0` 크래시, `--log-input`의 척도
오류, 동일 효과크기에서 폭 0의 신뢰구간, 유럽식 소수점 쉼표 오독, 제어문자 주입 등)을 회귀 시험으로 고정합니다.
자세한 내역은 `HARDENING.md`.

## 라이선스

MIT © 2026 hyeonjoong
