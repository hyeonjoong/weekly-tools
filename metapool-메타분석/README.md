# metapool — 메타분석기 (meta-analysis in one command)

연구 목록 CSV 한 장으로 **효과크기 합성(고정효과·변량효과) · 이질성(Q·I²·τ²·예측구간) · 하위군 분석 ·
leave-one-out 민감도 · Egger 출판편향 검정 · 텍스트 숲그림 · 논문에 그대로 붙일 한국어/영어 문장**까지
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

설치 없이도 됩니다: `python3 -m metapool <파일>`

## 입력 CSV 형식 (셋 중 하나)

| 형식 | 필요한 열 | 계산되는 지표 |
|---|---|---|
| 이미 계산된 효과크기 | `study, effect, se` (또는 `ci_low, ci_high`) | `generic` |
| 연속형 2군 원자료 | `study, n1, mean1, sd1, n2, mean2, sd2` | `smd`(Hedges g) 또는 `md` |
| 이분형 2군 원자료 | `study, events1, n1, events2, n2` (`a,b,c,d`도 인식) | `or`, `rr`, `rd` |

- 선택 열 `subgroup` 이 있으면 하위군 분석이 자동으로 붙습니다.
- **1군 = 처치/실험군, 2군 = 대조군.** 효과 방향은 항상 `1군 − 2군` 또는 `1군 / 2군`입니다.
- 열 이름은 흔한 변형(`ne/nc`, `mean.e`, `Author`, `연구`, `실험군n` …)을 자동 인식하고,
  안 되면 `--map 원본열=표준열` 로 알려주면 됩니다.

## 사용 예시

### 1) 연속형 원자료 → 표준화 평균차 + 하위군

```bash
metapool examples/breathing_isi_smd.csv
```

실제 출력 (일부):

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
  ⚠ 연구가 8편(<10)이라 이 검정의 검정력은 낮습니다. Cochrane은 10편 미만에서 시행을 권하지 않습니다.

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

주요 옵션: `--measure`(지표 강제) · `--model random|fixed` · `--tau2 DL|PM` · `--no-hksj` ·
`--conf`(출력 신뢰수준, ≤0.999) · `--input-conf`(입력 CI의 신뢰수준, 기본 0.95) · `--outcome` ·
`--cc`(연속성 보정값) · `--log-input` · `--no-forest/--no-sensitivity/--no-bias` ·
`--sensitivity-max` · `--sort` · `--json/--md/--out`. 전체 목록은 `metapool --help`.

> `--log-input`(effect 열이 OR/RR 같은 비(ratio) 값일 때)은 `ci_low/ci_high` 열을 함께 요구합니다.
> 비 척도의 표준오차는 로그 척도로 그대로 옮길 수 없어서, 조용히 틀리는 대신 거부합니다.

## 무엇을 계산하나 (방법)

| 항목 | 방법 |
|---|---|
| 고정효과 | 역분산 가중(inverse-variance) |
| 변량효과 | DerSimonian–Laird (기본) 또는 Paule–Mandel τ² |
| 신뢰구간 | 기본 **Hartung–Knapp** 보정(t 분포, df = k−1). `--no-hksj` 로 고전적 z 사용 |
| 이질성 | Cochran Q(편차 형태로 계산), I² = max(0, (Q−df)/Q), H² = max(1, Q/df), τ², 예측구간(t, df = k−2, 모형기반 SE) |
| SMD | Hedges g (J = 1 − 3/(4df−1)), var(g) = J²[(n1+n2)/(n1n2) + d²/(2(n1+n2))] |
| OR / RR | 로그 척도에서 합성 후 지수변환, 어느 칸이든 0이면 연속성 보정(+0.5, `--cc`) 후 경고 |
| 하위군 | 하위군별 τ² 각각 추정 → Q_between(모형기반 SE 사용), df = G−1 |
| 민감도 | leave-one-out 재합성, 유의성 결론이 뒤집히면 경고 |
| 출판편향 | Egger 회귀 비대칭 검정(절편, t, df = k−2), k<10이면 검정력 부족 경고 |
| 분포 함수 | 정규·t·카이제곱을 직접 구현 (erfc·불완전베타·불완전감마). 메타분석에서 쓰는 범위(신뢰수준 ≤0.999, df ≥ 1)에서 참값과 1e-9 이내로 일치하도록 상수 대조 시험을 둡니다 |

## 한계 / 정직한 고지

- **자동 분석이지 판단 대행이 아닙니다.** 연구 선정·비뚤림 평가(RoB)·이질성의 임상적 해석은 사람이 해야 합니다.
- 제공하지 않는 것: 메타회귀(연속형 조절변수), 네트워크 메타분석, 다변량/다수준 모형, trim-and-fill,
  REML/SJ τ² 추정, 개별환자자료(IPD) 분석, 비율/상관 단일군 메타분석, 그래픽 숲그림(PNG/PDF).
- `--tau2 DL`은 관행적 기본값이지만 연구 수가 적고 이질적일 때 τ²를 과소추정하는 경향이 있습니다 —
  이 경우 `--tau2 PM` + 기본 Hartung–Knapp 보정을 권합니다.
- I²는 "이질성의 양"이 아니라 "총 변동 중 연구간 변동의 비율"이며, 연구 수가 적으면 매우 불안정합니다.
- Egger 검정은 연구 10편 미만이면 사실상 무의미합니다(도구가 경고를 붙이고, 논문 문장에는 수치를 넣지 않습니다).
  또 **이분형(OR/RR)에서는 효과크기와 표준오차의 구조적 상관 때문에 위양성이 잦습니다** — Cochrane이 권하는
  Harbord/Peters 검정은 제공하지 않으므로 참고용으로만 보세요.
- **Hartung–Knapp 보정은 이질성이 거의 없을 때(I² ≈ 0) 오히려 고정효과 구간보다 좁은 신뢰구간을 만들 수
  있습니다**(ad hoc 절단 미적용). 이 경우 도구가 경고를 띄우니 `--no-hksj` 결과와 함께 확인하세요.
- 하위군 간 검정은 **하위군마다 τ²를 따로 추정**합니다. RevMan/metafor의 기본값(공통 τ²)보다 하위군 차이를
  크게 잡는(=덜 보수적인) 경향이 있으니, 결과를 보고할 때 이 선택을 함께 밝히세요.
- 중앙값(IQR)만 보고된 연구는 자동 변환하지 않고 제외합니다 — 평균·SD로 직접 바꿔 넣어야 합니다.
- 인터넷에 접속하지 않고, 입력 파일 밖으로 아무것도 내보내지 않습니다. 계산은 전부 로컬입니다.
  `--out`은 입력 파일 자체를 덮어쓰려 하면 거부하고, 기존 파일을 덮어쓸 때는 경고합니다.

## 테스트

```bash
python3 -m pytest -q     # 181개 통과 (분포 참값 대조, 손계산 검증, CSV 견고성, CLI 종단, 적대적 검토 회귀)
```

주요 수치는 손으로 계산한 값과 대조합니다 — 예: 고정효과 추정치 60.3/129, Cochran Q = 1.0232558,
DL τ² = 0.15, Hedges g = 148/151, log(OR) = log(4/9), Egger 절편 = 0(정확), qt(0.975, 10) = 2.2281389.
`tests/test_hardening.py`는 적대적 검토에서 실제로 발견된 결함(0셀+`--cc 0` 크래시, `--log-input`의 척도
오류, 동일 효과크기에서 폭 0의 신뢰구간, 유럽식 소수점 쉼표 오독, 제어문자 주입 등)을 회귀 시험으로 고정합니다.
자세한 내역은 `HARDENING.md`.

## 라이선스

MIT © 2026 hyeonjoong
