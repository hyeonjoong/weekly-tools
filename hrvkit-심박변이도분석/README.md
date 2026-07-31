# hrvkit — 심박변이도(HRV) 분석기

스마트워치·PPG·ECG에서 얻은 **RR/IBI 간격(ms)** 또는 **순간 심박수(bpm)** CSV 한 개를
넣으면, 이상박동(ectopic/missed beat)을 자동 보정한 뒤 **시간영역 · 주파수영역 · 비선형**
HRV 지표를 계산해 사람이 읽는 리포트(또는 `--json`)로 출력합니다.
numpy/scipy 없이 **표준 라이브러리만**으로 동작합니다 — FFT(radix-2 Cooley–Tukey),
Welch PSD, **Lomb–Scargle 주기도**, 보간, 표본 엔트로피까지 전부 직접 구현했습니다.

---

## 목적 / Why this exists / Who it's for

> **BELL-001 이란?** 이 저장소가 상정한 **사내 가상 수면 디바이스 프로젝트 코드명**
> 입니다 — 실재하는 승인 제품이 아니고, 문서에 인용된 임상 근거도 없습니다.
> "느린 호흡 → 부교감 활성 ↑ → RSA/HRV ↑" 라는 **연결 자체는** HRV 문헌에서
> 잘 확립된 생리(Task Force 1996; Lehrer & Gevirtz 2014 등)지만, *그것이 서파수면을
> 늘린다*는 마지막 고리는 이 도구가 검증하는 **가설**이지 기정사실이 아닙니다.
> hrvkit은 그 가설을 검정할 **지표를 계산할 뿐**, 기전을 입증하지 않습니다.

**한국어.** BELL-001 수면 디바이스의 상정 작용기전은 *느린 호흡 → 부교감신경 활성 ↑ →
호흡성 동성부정맥(RSA)/HRV ↑ → 서파수면 촉진* 입니다. 이 기전이 실제로 일어났는지
확인하려면 착용형 기기에서 나온 박동간격(RR) 시계열로 HRV를 **정량화**해야 합니다.
그런데 그런데 실측 RR은 (1) 놓친 박동·조기수축 같은 이상값이 섞여 있고, (2) 불균등 표본이라
주파수 분석 전에 보간이 필요하거나(Welch) 보간 없는 추정기(Lomb–Scargle)를 써야 하며, (3) RMSSD·SDNN·LF/HF 등 지표 정의와 정규화가
제각각이라 손으로 하면 번거롭고 실수하기 쉽습니다. `hrvkit`은 이 전 과정을 한 번에
처리하고, 각 지표를 BELL-001 기전(부교감 우세 = RMSSD·HF·SD1 ↑, LF/HF ↓)에 연결한
한 줄 해석까지 붙여 줍니다. 임상 수면 연구자·디바이스 검증 파이프라인에서 바로
쓰도록 만들었습니다.

**English.** BELL-001's mechanism is *slow breathing → parasympathetic activation →
respiratory sinus arrhythmia (RSA)/HRV ↑ → more slow-wave sleep*. To check whether that
actually happens you must **quantify HRV** from the wearable's beat-to-beat (RR) series.
Real RR data is messy: it carries ectopic/missed beats, it is unevenly sampled (so the
frequency analysis needs interpolation first), and the metric definitions/normalizations
(RMSSD, SDNN, LF/HF, normalized units…) are fiddly to get right by hand. `hrvkit` runs the
whole pipeline — artifact correction → time-domain → frequency-domain → non-linear — and
ties the numbers back to the BELL-001 mechanism (parasympathetic dominance = higher
RMSSD/HF/SD1, lower LF/HF). Built for clinical sleep researchers and device-validation
pipelines. Zero dependencies, so it runs anywhere Python 3.9+ is installed.

---

## 계산하는 지표 / What it computes

- **이상박동 처리 / Artifacts** — 생리적 범위(기본 300–2000 ms) 필터 + 국소 중앙값 대비
  급변(기본 >20 %) 필터. 극단값이 이웃 박동을 오탐하지 않도록 기준선 중앙값은 범위 밖
  이웃을 제외하고 계산. 보간/제거/표시 선택, 이상박동 비율(%) 보고.
- **시간영역 / Time-domain** — mean RR, **median RR·MAD(로버스트)**, mean HR, SDNN, RMSSD,
  SDSD, pNN50, pNN20, CVNN, HR min–max, 그리고 **기하학적 지표 HRV 삼각지수(HTI)·TINN**
  (Task Force 1996, 이상값에 강건).
- **주파수영역 / Frequency-domain** — VLF/LF/HF **절대(ms²)·정규화(n.u.)·비율(%)** 파워,
  LF/HF, **ln(HF)**, total power, 대역별 피크 주파수, **HF 피크 기반 호흡수(회/분) 추정**.
  PSD 추정기가 **두 가지**입니다(`--psd`): 기본 **Welch**(4 Hz 선형 리샘플 → 직접 구현한
  radix-2 FFT, Hann 창, 50 % 겹침, 구간별 주기도 평균)와 보간하지 않는
  **Lomb–Scargle**(`--psd lomb`, 아래 전용 절 참조). 리포트에 방법과 함께
  **주파수 해상도·구간 길이·대역별 빈 수·VLF 신뢰 여부**를 표기해, 각 대역이 실제로
  해상됐는지 눈으로 확인할 수 있습니다(아래 "Notes / limitations" 의 VLF 항목 참조).
- **비선형 / Nonlinear** — Poincaré SD1, SD2, SD1/SD2, 타원 면적(π·SD1·SD2), 표본
  엔트로피(SampEn, m=2), 그리고 **상세변동분석 DFA α1(단기)·α2(장기)** (Peng et al. 1995
  기반이되, 꼬리를 버리지 않는 **양방향 구간 분할**(Kantelhardt/MF-DFA 방식)을 사용 —
  N이 구간 크기의 배수가 아니면 전방향 방식과 α1이 1–2% 다를 수 있으니 다른 도구와
  비교할 때 유의하세요. 백색잡음 α≈0.5·적분잡음 α≈1.5로 손 검산).
- **장기 지표 / Long-term** (`--window`) — 겹치지 않는 5분 구간에서 **SDANN**(구간 평균
  NN들의 SD)과 **SDNN index**(구간 SDNN들의 평균). Task Force 1996 **공식을 그대로**
  적용합니다. 단 그 참고값(SDANN ≈ 127±35 ms, SDNN index ≈ 54±15 ms)은 **24시간 홀터**
  기준이므로, 20분 기록에서 나온 SDANN은 공식상 맞아도 발표 값과 비교할 수 없습니다 —
  기록이 6시간 미만이면 리포트가 그 사실을 같은 자리에 적습니다.

---

## 구간별 추이 / Windowed (epoch-wise) analysis (`--window`)

Task Force 표준 지표는 **정상성(stationarity)을 가정한 5분 기록**에 대해 정의돼 있습니다.
20분·야간 기록을 한 덩어리로 계산하면 각성→수면 전환, 자세 변화, 개입 순응 같은 **느린
추세가 SDNN을 부풀리고**, "개입 중 언제 효과가 붙었는가"에 답할 수 없습니다.

```bash
hrvkit examples/session_20min.csv --window                 # 5분(300초) 구간, Task Force 표준
hrvkit examples/session_20min.csv --window 120             # 2분 구간
hrvkit examples/session_20min.csv --window 300 --step 60   # 5분 창을 1분씩 미는 슬라이딩
hrvkit examples/session_20min.csv --window --format csv > epochs.csv   # 구간당 1행
```

출력은 세 블록입니다.

- **[A] 구간별 지표** — 구간마다 n·이상박동%·HR·RMSSD·SDNN·pNN50·HF n.u.·LF/HF·SD1·DFA α1.
- **[B] 요약 + 추세** — 지표별 mean±SD·CV·min–max 와 **Mann–Kendall 단조 추세 검정**
  (Kendall tau-b, 동점 없고 구간 ≤25개면 **정확 분포**, 아니면 동점 보정 정규 근사) +
  이상 구간에 강건한 **Theil–Sen 기울기(지표단위/구간)**. 지표 전체를 한 가족으로 보아
  Holm(FWER)·BH(FDR) 보정 p를 함께 냅니다.
- **[C] 장기 지표** — SDANN·SDNN index.

정직성 장치(계산만큼 중요합니다):

- 정제(이상박동 보정)는 **기록 전체에서 한 번만** 합니다. 창마다 다시 탐지하면 이미 보간된
  값 위에서 탐지가 돌아 이상박동 비율이 0 %로 잘못 보고됩니다.
- 마지막 **불완전 구간은 버리고**, 버린 초 수를 notes에 적습니다(길이가 다른 구간을 섞으면
  SDANN·추세가 구간 길이에 오염됩니다).
- 박동이 부족한 구간도 **표와 CSV에서 사라지지 않고** error 사유와 함께 남습니다.
- 창이 겹치면 구간이 서로 독립이 아니므로 **SDANN을 생략**하고, 추세 p값이 낙관적임을
  경고합니다.
- 구간이 적으면 완벽한 단조 추세(tau=±1)라도 **기각이 원천적으로 불가능**합니다. 정확검정의
  최소 양측 p는 2/n! 이므로 구간 4개면 0.083(보정 전에 이미 α 초과), 5개면 0.017이지만
  9개 지표 Holm 보정 후 0.15, 6개면 0.0028 → 보정 후 0.025로 비로소 유의해질 수 있습니다.
  즉 **보정 후 기각이 가능해지는 최소 구간 수는 6개**입니다. 리포트는 이 최소 p를 숫자로
  찍어 줍니다 — "추세 없음"이 증거의 부재인지 부재의 증거인지 구분할 수 있게.
- 창 길이가 300초가 아니면 SDANN·SDNN index를 다른 도구/논문 값과 직접 비교하지 말라고
  같은 줄에 적습니다.

---

## 보간 없는 PSD / Lomb–Scargle periodogram (`--psd lomb`)

RR 시계열은 **박동이 날 때만** 값이 생기는 불균등 표본입니다. 표준 Welch 경로는 이걸
4 Hz 균등 격자로 **선형보간**한 뒤 FFT를 거는데, 선형보간은 사실상 **저역통과 필터**라
HF(0.15–0.40 Hz) 파워를 체계적으로 깎습니다(Clifford & Tarassenko 2005; Laguna 1998).
`--clean remove` 로 이상박동을 지우면 그 구멍을 직선으로 메우면서 원래 없던 저주파
성분까지 생깁니다.

`--psd lomb` 은 **보간을 하지 않습니다.** Lomb(1976)/Scargle(1982) 주기도는 각 주파수에서
박동 시각 위에 정현파를 최소제곱 적합하므로 균등 격자가 필요 없습니다.

```bash
hrvkit examples/session_20min.csv --psd lomb  # 보간 없이 PSD (아래 수치 재현)
hrvkit examples/session_20min.csv --psd lomb --ls-oversample 8
hrvkit --paired manifest.csv --psd lomb       # 코호트 통계도 그대로
hrvkit examples/session_20min.csv --window --psd lomb   # 구간별 추이도 그대로
```

### 왜 쓰나 — 같은 파일, 두 방법 (`examples/session_20min.csv`, 20분·1465박동)

| | Welch (기본) | Lomb–Scargle | 참값 힌트 |
|---|---|---|---|
| VLF power | 2.3 ms² (`vlf_reliable=False`) | **3.0 ms² (`vlf_reliable=True`)** | — |
| HF power | 413.6 ms² | **561.6 ms²** | — |
| Total power | 513.4 ms² | **667.7 ms²** | SDNN² = **684.4 ms²** |

마지막 열이 핵심입니다. Parseval에 따라 total power(VLF+LF+HF)는 신호의 분산, 즉
**SDNN²** 에 가까워야 합니다. 이 기록에서 Welch는 684 ms² 중 513 ms² 만 회수해
**25 %를 잃는** 반면 Lomb은 668 ms²로 **2.5 %** 안에 들어옵니다(나머지는 0.003 Hz 아래·
0.40 Hz 위라 대역에 포함되지 않는 성분). 손실의 **96 %는 보간의 HF 감쇠**(154.2 ms² 중
148.1)이고, Welch의 구간 분할로 인한 저주파 손실은 이 기록에선 4 % 수준입니다.

### 정규화·비 지표도 무시할 만큼 같지는 않습니다

절대 파워보다는 차이가 작지만, "비율만 볼 거면 안 바꿔도 된다"고 말할 수는 없습니다.
동봉 예제 3개 전부에서 Welch → Lomb:

| 파일 | HF power | LF/HF | HF n.u. |
|---|---|---|---|
| session_20min | 413.6 → 561.6 (+36 %) | 0.236 → 0.183 (**−22 %**) | 80.9 → 84.5 (+4.4 %) |
| resting | 117.8 → 157.9 (+34 %) | 1.942 → 1.541 (**−21 %**) | 34.0 → 39.4 (**+15.8 %**) |
| slow_breathing | 1249 → 1523 (+22 %) | 0.098 → 0.088 (−10 %) | 91.1 → 91.9 (+0.9 %) |

**차이는 절대 파워(ms²·ln HF)에서 가장 크지만 LF/HF도 10–22 % 움직입니다.**
따라서 한 연구 안에서는 **반드시 한 방법으로 통일**하고 논문에 명시하세요. 두 방법으로
낸 숫자를 같은 표에 섞으면 안 됩니다(hrvkit은 표에 방법을 찍고, 섞이면 경고합니다).

### 언제 lomb을 고르나

- **절대 파워(ms², ln HF)를 논문/보고서에 싣을 때** — 보간 편향이 그대로 숫자에 남습니다.
- **`--clean remove` 를 쓸 때** — 제거된 구간을 4 Hz 격자 위에서 직선으로 메우지 않고
  **구멍을 건너뛰어** 적합합니다. (v0.5.0부터 `--clean remove` 는 박동만 지우고 **시각은
  보존**합니다. 이전에는 정제된 NN의 누적합으로 시간축을 다시 만들어, 박동을 지울 때마다
  그만큼 기록이 짧아지고 뒤따르는 박동이 앞으로 당겨졌습니다 — Welch 경로도 같이 고쳤습니다.)
- **긴 기록에서 VLF를 보고 싶을 때** — 기록을 쪼개지 않으므로 `--nperseg` 조정 없이
  `vlf_reliable=True` 가 될 수 있습니다. 단 아래 경고를 먼저 읽으세요.

> **⚠️ VLF에 lomb을 쓸 때의 함정 — 추세 제거를 하지 않습니다.**
> Welch는 구간마다 평균을 빼므로(detrend='constant') 느린 추세가 자동으로 걸러집니다.
> lomb은 **전체 평균만** 빼기 때문에 자세 변화·체온 조절 같은 느린 드리프트가 통째로
> VLF로 들어갑니다. 20분에 걸쳐 240 ms 표류하는 합성 신호로 측정하면 Welch VLF 8.3 ms²
> 대 **lomb VLF 288.6 ms²** — 35배입니다. VLF를 보고할 생각이면 기록이 정상성을
> 만족하는지(자세·각성 상태가 일정한지) 먼저 확인하세요.
> 또 `vlf_reliable=True` 는 "**대역이 해상됐다**"는 뜻이지 "**논문에 실어도 된다**"는
> 뜻이 아닙니다. hrvkit은 VLF 최저 주파수의 **3주기(999 s ≈ 16.7분)** 이상을 요구하지만,
> 발표된 VLF 참고값은 대부분 24시간 홀터 기준이라 20분 기록과 직접 비교할 수 없습니다.

Welch를 기본으로 남겨 둔 이유는 (1) 기존 결과·다른 HRV 도구와의 비교 가능성,
(2) 구간 평균으로 분산이 작은 매끄러운 스펙트럼을 주기 때문, (3) **속도** 입니다.
Welch는 O(N log N)이지만 Lomb은 O(격자점 × 박동수)라 순수 파이썬에서 눈에 띄게 느립니다 —
측정값: 5분 기록 0.1초, 20분 0.5초, 1시간 2.8초. 격자점은 4096으로 상한이 걸려 있어
그 뒤로는 박동 수에 **선형**입니다. Lomb은 구간 평균을 하지 않아 **단일 기록 스펙트럼의
분산이 더 큽니다**(피크 위치는 또렷하지만 값은 더 출렁입니다).

기록이 아주 길면(≈2.8시간 초과) 격자 상한 때문에 격자 간격이 해상도 1/T 보다 **성글어집니다**
(보고되는 `ls_oversample` 이 1 미만). 대역 파워는 여전히 같은 적분의 리만 합이라 편향되지
않고(HF 대역만 해도 격자점이 2000개 이상), 리포트는 요청값이 아니라 **실제 적용된** 배수를
그대로 찍습니다.

### 정직성 장치

- 리포트가 **어느 방법으로 계산했는지 항상 명시**합니다 — 단일 파일·일괄·`--window`
  뿐 아니라 논문 표로 직행하는 `--compare`·`--paired`·`--groups` 의 텍스트·CSV·JSON
  **전부**에 `psd_method` 가 들어갑니다. 서로 다른 방법으로 낸 기록이 한 표에 섞이면
  `mixed` 로 표시하고 절대 파워를 비교하지 말라고 경고합니다.
- **해상 불가는 방향이 없습니다.** 대역이 해상되지 않으면 `NaN` 을 내고, LF/HF·n.u.도
  함께 `NaN` 이 됩니다(예전에는 HF=NaN 일 때 LF/HF가 `∞` 가 되어 해석 엔진이 그것을
  "교감 우세 — 각성·스트레스 부하"로 단정했습니다). 해석문도 판단을 보류합니다.
- **해상도는 1/기록길이로 보고합니다** — `--ls-oversample` 은 격자를 촘촘하게 만들 뿐
  해상도를 늘리지 않으므로, 격자 간격을 해상도로 광고하지 않습니다(둘 다 표시).
- 격자가 상한(4096점)에 걸리면 **실제로 적용된** 과표본 배수를 다시 계산해 보고합니다.
- 평균 심박이 48 bpm 미만이면 평균 Nyquist(≈HR/2)가 HF 상단 0.40 Hz보다 낮아집니다 —
  이 경우 **앨리어싱 위험을 경고**합니다.
- 정규화는 `PSD(f) = 2·P_lomb(f)/f_eff`(`f_eff` = (N−1)/기록길이)로, 균등 표본이면
  사각창 주기도의 밀도 스케일과 정확히 일치합니다. 그래서 진폭 A 정현파의 대역 적분이
  **A²/2 = 분산**이 되고, 이것을 테스트로 고정했습니다(합성 신호 5 % 이내, 고속 격자
  구현 ↔ 교과서 정의식 참조 구현 1e-9 일치, τ의 직교성(Σcos·sin=0)을 구현과 무관하게
  직접 검정, scipy가 설치돼 있으면 `scipy.signal.lombscargle` 과도 1e-8 일치).
- **해상도보다 성긴 격자는 아예 거부합니다.** 기록이 약 2.8시간을 넘으면 격자 상한
  (4096점) 때문에 격자 간격이 해상도 1/T 보다 성글어져 좁은 피크가 격자 사이로 빠집니다
  (측정: 4시간 기록에서 LF −30 %, HF +46 %, **LF/HF 2.1배** 오차). 조용히 틀린 숫자를
  내는 대신 계산을 거부하고 `--window` 또는 `--psd welch` 를 안내합니다. 같은 이유로
  `--ls-oversample` 의 하한은 **1** 입니다.

---

## 평행군(독립 2군) 비교 / Parallel-arm comparison (`--groups`)

`--paired` 는 **같은 피험자의 pre–post**용입니다. 약물 대 위약, 디바이스 대 sham처럼
**각 피험자가 한 군에만 속하는 설계**에는 짝지은 검정을 쓸 수 없습니다(짝짓기가 임의가
되어 p값이 무의미해집니다). `--groups` 가 그 경우를 담당합니다.

```bash
hrvkit --groups arms.csv                    # 군간 비교 표
hrvkit --groups arms.csv --format csv       # 지표당 1행 (논문 표용)
hrvkit --groups arms.csv --json --alpha 0.01
```

`arms.csv` 는 **기록당 한 행**입니다(헤더 없으면 1열=파일, 2열=군).
⚠️ **매니페스트에 먼저 나온 군이 기준(대조)** 이 되고, 모든 차이·이동량·방향 화살표는
`(나중 군 − 먼저 군)` 방향입니다. 행 순서를 바꾸면 부호가 통째로 뒤집히므로,
대조군을 위에 두세요. 리포트 머리글이 어느 쪽을 기준으로 삼았는지 이름으로 찍습니다.

```
file,group,subject
C01.csv,control,C01
C02.csv,control,C02
D01.csv,device,D01
D02.csv,device,D02
```

지표마다 다음을 냅니다.

| 값 | 뜻 |
|----|----|
| mean±SD, median | 군별 기술통계 |
| **HL shift** | Hodges–Lehmann 이동량 = median(개입−대조). 평균차보다 이상값에 강건 |
| **분포무관 CI** | HL의 (1−α) 신뢰구간 — Mann–Whitney 정확검정과 **쌍대**(구간 밖 ⇔ 기각) |
| **Hedges g** | 소표본 편의를 보정한 표준화 효과크기 (Cohen's d × J) |
| **rank-biserial**, CLES | 순위 기반 효과크기 / P(개입 > 대조) |
| **p (e/a)** | Mann–Whitney 양측 p — 동점 없고 군당 ≤30이면 **정확 분포**(`e`), 아니면 동점 보정 정규 근사(`a`) |
| **p_holm / p_BH** | 지표 가족 전체에 대한 FWER / FDR 보정 |

거부하는 입력(조용히 넘어가면 n이 부풀려집니다): 같은 파일 중복, 피험자 라벨 중복,
군이 2개가 아님(3군 이상은 Kruskal–Wallis가 필요한데 이 도구는 제공하지 않습니다),
군당 기록 1개.

리포트는 **표본이 부족해 기각이 불가능한 경우를 숫자로 밝힙니다** — 예를 들어 n=5/5,
지표 11개 보정에서는 완전분리(모든 개입값 > 모든 대조값)여도 최소 p=0.0079, Holm 보정
후 0.087이라 α=0.05에서 유의할 수 없습니다. 이걸 말하지 않으면 "유의하지 않음"이
"효과 없음"으로 오독됩니다.

---

## 여러 기록 비교 & 일괄 처리 / Compare & batch

BELL-001 검증의 핵심은 **기저(안정) 대 개입(느린 호흡)** 비교입니다. 이제 한 번에 처리합니다.

```bash
# 짝지은 비교 — 델타·변화율·부교감 방향 화살표 + BELL-001 방향 해석
hrvkit baseline.csv intervention.csv --compare

# 여러 피험자 일괄 요약 (장치 검증 파이프라인) — 화면 표 또는 CSV
hrvkit subj01.csv subj02.csv subj03.csv
hrvkit data/*.csv --format csv > hrv_summary.csv
```

`--compare` 는 mean HR·RMSSD·SDNN·pNN50·HTI·HF·LF/HF·SD1·DFA α1 등에 대해 각 지표가
부교감 우세 방향(↑부교감)인지 교감 쪽으로 이동(↑교감)인지 표시하고, 몇 개가 기전과 일치하는지
요약합니다. (**n=1 비교는 방향만** 나타내며 통계적 유의성이 아님을 명시합니다.)

### 여러 피험자 짝 통계 / Paired-cohort statistics (`--paired`)

n=1 방향 비교를 넘어, **여러 피험자의 (기저, 개입) 짝**을 모아 지표별로 개입 효과의
**통계적 유의성과 효과크기**를 냅니다. 장치 검증 연구의 핵심 산출물입니다.

```bash
hrvkit --paired examples/paired/manifest.csv   # 예제 코호트(합성 10명)
hrvkit --paired manifest.csv               # 코호트 통계 표
hrvkit --paired manifest.csv --json        # 기계가 읽는 형태
hrvkit --paired manifest.csv --format csv  # 논문 표/스프레드시트용 (지표당 1행)
hrvkit --paired manifest.csv --alpha 0.01  # 99% 신뢰구간 + α=0.01 판정
```

`manifest.csv` (헤더 이름으로 기저/개입 열 자동 인식; 상대경로는 매니페스트 기준):

```
baseline,intervention,subject
S01_rest.csv,S01_slow.csv,S01
S02_rest.csv,S02_slow.csv,S02
...
```

출력은 **[A] 기술** 과 **[B] 추론** 두 블록입니다.

**[A] 기술** — 지표별 평균 base→interv, 평균 차이 ± SD, 증가한 피험자 수(↑n/n).

**[B] 추론** — 지표별로:

- **Wilcoxon 부호순위 검정 양측 p값.** |차이|에 동점이 없고 n ≤ 25 이면 **정확
  분포**(부호순위합의 정확 영분포를 부분집합-합 DP로 전개)를, 동점이 있거나 n이 크면
  동점·연속성 보정 **정규 근사**를 자동으로 씁니다(표에 `e`/`a` 로 표기).
  디바이스 검증 코호트는 n=8~20 이 대부분인데 이 영역에서 정규 근사는 실제로
  빗나갑니다 — n=8 이 전부 같은 방향이면 정확 p=0.0078 인데 정규 근사는 0.0143
  (1.8배 보수적). 두 경로 모두 scipy와 대조 검증했습니다(정확: 완전 일치, 근사: 2e-16).
- **Hodges–Lehmann 이동량과 분포무관 신뢰구간.** 평균차는 이상값에 끌리므로 위치
  추정은 Walsh 평균의 중앙값(HL)으로 냅니다. CI는 부호순위 검정과 **쌍대(duality)**
  라서 **|차이|에 동점이 없을 때** "CI가 0을 포함하지 않음" ⇔ "p < α" 가 정확히
  일치합니다 — 6만 개(동점 없는) 격자점 전수 대조로 불일치 0을 확인했고, 정확 영분포로 계산한 **해석적 피복률이 n=6~25
  전 구간에서 명목 수준 이상**임을 테스트로 고정합니다. n이 너무 작아(α=0.05 에서
  n≤5) 어떤 유한 구간도 명목 수준을 담보할 수 없으면 전체 범위를 "95% 구간"이라
  부르지 않고 **(-∞, ∞) 와 "n부족"** 을 명시합니다. `--alpha` 로 수준을 바꿉니다.
  **동점이 있으면** p는 동점 보정 정규 근사로 전환되는 반면 CI의 절단 지수는 정확
  영분포에서 나오므로, 경계에서 둘이 완전히 일치하지 않을 수 있습니다(대개 CI 쪽이
  더 보수적). 리포트가 `a` 표시와 함께 그 사실을 적습니다.
- **Cohen's dz** 효과크기.
- **다중비교 보정 p값** — 지표군 전체를 하나의 검정 가족으로 보아
  **Holm–Bonferroni**(`p_holm`, FWER 통제 — 확증적 분석용)와
  **Benjamini–Hochberg**(`p_bh`, FDR 통제 — 탐색적 스크리닝용)를 함께 냅니다
  (statsmodels 와 대조 검증). 검정되지 않은 지표는 가족 크기 m에서 제외됩니다.
  단, 가족에는 **대수적으로 중복인 지표**가 있습니다(SD1 = SDSD/√2 ≈ RMSSD/√2 라
  둘은 사실상 같은 검정이고 동일한 p를 냅니다). 즉 m이 독립 검정 수를 과대평가해
  보정이 **필요 이상으로 보수적**입니다 — 리포트도 이를 명시하며, 사전에 주 지표를
  지정해 보정 없는 p를 보고하는 편이 가장 강력합니다.

느린/공명 호흡 레짐 짝이 섞여 있으면 HF 기반 지표를 방향 집계에서 빼고 경고합니다.

### 느린/공명 호흡 레짐 자동 처리 (중요)

느린 호흡(예: 6회/분=0.1 Hz)은 RSA가 **HF가 아니라 LF 대역**에 실립니다. 이 경우
HF n.u.·LF/HF의 "부교감 방향" 해석이 **역전**됩니다. `hrvkit`은 호흡 피크가 LF에
있고 HF n.u.가 매우 낮으면 **느린/공명 호흡 레짐**으로 감지해: (1) 호흡수를 LF 피크에서
추정하고, (2) 경고를 띄우며, (3) 해석·비교·코호트 판정을 대역에 무관한 시간영역 vagal
지표(RMSSD·SD1·pNN50·HTI)에 근거하도록 전환합니다. 이는 BELL-001 같은 **느린 호흡
개입**에서 잘못된 "교감 우세" 결론을 내리지 않게 하는 핵심 안전장치입니다.

---

## 지저분한 실측 CSV 강건성 / Robust real-world CSV handling

- **인코딩 폴백** — UTF-16(BOM 판별) → UTF-8(BOM) → cp949/euc-kr → latin-1.
  한국·윈도우 환경 CSV에서 헤더가 깨져도 읽습니다.
- **구분자 자동 감지** — 쉼표 · **세미콜론(;)** · 탭 · 파이프.
- **숫자 표기는 파일 로케일로 해석** — 유럽식 파일(세미콜론/탭 구분)에서 쉼표는
  **항상 소수점**입니다: `0,803`→0.803, `1.234,5`→1234.5. 쉼표 구분 파일에서는 따옴표
  안의 `"1,010"`→1010(영미식 천단위). 토큰의 자릿수로 추측하지 않습니다 — `0,803`(=0.803)과
  `1,010`(=1.010)은 원리적으로 구분 불가능해, 자릿수 규칙은 독일식 엑셀 익스포트를
  1000배로 읽는 오답을 냅니다. `inf`/`nan`/`1e400` 같은 비유한 값은 버립니다.
- **값 열 추정이 플래그 열에 속지 않음** — 헤더를 토큰으로 쪼개 **정확 일치**로
  점수화합니다(`valid`⊃`val`, `annotation`⊃`nn`, `thr`⊃`hr` 같은 부분일치 오답 차단).
  한글 헤더(`간격`·`심박수`)도 토큰화됩니다. 예: 헤더가 `valid,rr_ms` 이고 valid가
  전부 1인 디바이스 익스포트에서 과거엔 플래그 열이 뽑혀 "SDNN 0.00, HR 60.0" 을
  경고 없이 냈습니다. 단 어떤 열도 **완전히 배제하지는 않습니다** — 배제했더니
  `time_s,Pulse rate (count/min)` 에서 "count" 때문에 진짜 값 열이 탈락하고 누적
  시간축이 값으로 뽑히는 더 나쁜 일이 생겼습니다. 점수가 같을 때만 생리적으로
  그럴듯한 열(상수 열은 값 열이 아닙니다)로 가립니다.
- **단위 감지가 열 이름을 존중** — `rr_ms`/`rr_s`/`hr_bpm` 처럼 이름에 단위가 있으면
  그것을 신뢰하고, 없을 때만 중앙값 규칙을 씁니다. (중앙값 규칙은 성인 기준이라
  신생아 RR 270 ms 를 bpm으로 오판했습니다.) 이름과 중앙값 규칙이 엇갈리면 경고합니다.
- **박동 발생시각 입력** — `--timestamps` 로 누적 발생시각 열을 차분해 RR 계산. 값이
  발생시각처럼 보이면 힌트도 띄웁니다.
- **결측 라벨 확장** — `NA/N/A/NaN/NULL/./-/--/?/NONE` 를 결측으로 처리, 무시된 셀 수 보고.
- **주석/메타 헤더** — `#` 로 시작하는 줄은 건너뜁니다 (Polar·Kubios 등이 파일 앞머리에
  붙이는 `# Device: ...` 메타 블록 대응).
- **유사반복 거부** — 매니페스트에 같은 피험자 라벨이나 동일한 (기저,개입) 짝이 중복되면
  거부합니다(중복 = 유사반복 → n·p값·CI가 부풀려짐).

---

## 엔지니어링/품질 노트 / Engineering & quality notes

- **표준 라이브러리만.** FFT는 반복형 radix-2 Cooley–Tukey로 직접 구현(2의 거듭제곱
  zero-pad), PSD는 `scipy.signal.welch(scaling='density')`와 **동일한 수식**을 순수
  파이썬으로 재현했습니다. 정규화는 Parseval을 만족(Σ P·df ≈ 신호 분산)해 합성 정현파로
  절대 스케일(ms²)까지 손 검산됩니다.
- **교차검증된 테스트(총 447개, 전부 오프라인).**
  - 직접 구현 FFT ↔ 소박한 O(n²) DFT: 무작위 벡터에서 최대오차 **< 1e-9**.
  - Welch PSD ↔ `scipy.signal.welch`: **rtol 1e-6** 일치 (scipy 있을 때).
  - Wilcoxon **정확 검정** ↔ `scipy.stats.wilcoxon(method='exact')`: 완전 일치.
    **정규 근사** 경로 ↔ scipy(approx): ~1e-16 일치.
  - **TINN**: 참값을 아는 완전 삼각형 히스토그램에서 정확히 회수(156.25 ms).
  - **VLF**: 알려진 진폭(60 ms, 0.008 Hz)의 합성 성분을 긴 구간에서 ~100% 회수.
  - **HL 신뢰구간 ↔ 정확검정 쌍대성**: 격자 탐색으로 "CI가 μ0를 배제" ⇔ "정확검정이
    H0: pseudomedian=μ0 를 기각" 을 확인.
  - rfft ↔ `numpy.fft.rfft`, SampEn ↔ numpy 참조 구현: **~1e-9** 일치.
  - **DFA ↔ 독립 numpy 참조(polyfit) 구현: rel 1e-6 일치**, 백색잡음 α≈0.5 ·
    적분잡음 α≈1.5 속성 검증.
  - **HTI = N/최빈빈, TINN, MAD 로버스트성**을 손 계산·이상값 시나리오로 검증.
  - RMSSD/SDNN/SDSD/pNN을 손 계산한 5-박동 시리즈로 검증, SD1 = SDSD/√2 항등식 확인.
  - numpy/scipy 참조 테스트는 **가드 처리**되어 있어, 순수 표준 라이브러리 환경에서도
    (해당 참조 테스트만 skip) 전부 통과합니다.
- **적대적 입력 방어.** 빈 파일 / 1-박동 / 분산 0(전부 동일) / 2의 거듭제곱이 아닌 길이 /
  ms·s·bpm 단위 자동 감지 / 비UTF-8·UTF-16 인코딩 / 세미콜론·탭 구분자 / 소수점 쉼표 ·
  천단위 구분 / 결측 라벨 / 품질 플래그 열 / 중복 열 이름 / 유사반복 매니페스트를
  모두 처리합니다. 자세한 이력은 `HARDENING.md`.
- **오류 메시지 위생.** CSV가 아닌 파일을 실수로 지정해도 그 내용을 stderr로 흘리지
  않도록 헤더/셀 인용을 잘라냅니다(CI 로그·버그 리포트로 비밀이 새는 것 방지).

---

## Install

```bash
cd ~/Downloads/02_프로젝트/깃헙/hrvkit-심박변이도분석
python3 -m pip install -e .
```

설치 없이도 실행할 수 있습니다:

```bash
python3 -m hrvkit.cli <파일.csv> ...
```

또는 폴더 안의 **`실행.command` 더블클릭** (예제 데이터로 바로 시연).

---

## Usage

### 1) 단일 열 (RR/IBI ms 또는 HR bpm)

```bash
hrvkit examples/resting.csv
```

입력 (`examples/resting.csv`) — 값 한 줄에 하나:

```
rr_ms
827.8
845.1
...
```

출력 (요약):

출력 (발췌 — 실제 출력 그대로):

```
[1] 시간영역 / Time-domain
    평균 RR mean RR    : 819.5 ms   (평균 HR 73.3 bpm)
    SDNN               : 23.47 ms   (전체 변동성)
    RMSSD              : 18.77 ms   (단기·부교감)
    pNN50 / pNN20      : 0.0% / 33.8%
    HRV triangular idx : 7.50   (TINN 117.2 ms, 기하학·이상값에 강건)

[2] 주파수영역 / Frequency-domain
    방법 method        : 4 Hz 선형 리샘플 → Welch PSD (Hann, nperseg=256, 50% overlap, radix-2 FFT, 6 segments)
    기록 길이 duration : 245.9 s (981 samples)
    해상도 resolution  : 0.0156 Hz (구간 64.0 s → VLF/LF/HF 빈 2/7/16개)
    VLF power          : 76.7 ms²  (18.1%)  ※ 구간 < VLF 주기(333 s) → 과소추정, 참고용 (--nperseg 로 구간 확대)
    LF  power          : 228.8 ms²  (54.1%,  66.0 n.u.)
    HF  power          : 117.8 ms²  (27.8%,  34.0 n.u.)
    LF/HF ratio        : 1.94   (ln HF 4.769)
    호흡수 est. resp   : 15.0 회/분 (HF 피크 0.250 Hz; 자발 호흡(HF RSA))

[3] 비선형 / Nonlinear (Poincaré + SampEn + DFA)
    SD1                : 13.30 ms   (단기·부교감)
    SD2                : 30.47 ms   (장기)
    SampEn (m=2)       : 1.810   (복잡성/규칙성)
    DFA α1 / α2        : 1.002 / 0.857   (단기/장기 분형 상관; 참고: 안정·자발호흡 성인 α1≈1.0, 느린·공명 호흡에서 낮아짐)
```

`해상도` 줄과 VLF 줄의 경고에 주목하세요 — 이 도구는 각 대역이 **실제로 해상됐는지**를
숨기지 않습니다.

### 2) 시간+값(time+value) 형식 — 값 열 자동/수동 선택

```bash
hrvkit examples/slow_breathing.csv          # time_s,rr_ms → rr_ms 열 자동 선택
hrvkit examples/slow_breathing.csv --col rr_ms --json
```

> ⚠️ **`examples/` 의 모든 CSV(안정·느린호흡·20분 세션·평행군 10개·짝지은 10명)는 합성 데이터입니다** — 실제 피험자 기록이 아니라,
> 도구의 입출력 형식을 보여주려고 각 특성(안정 호흡 / 더 느린 호흡 + 큰 RSA)을
> 갖도록 생성한 것입니다. 따라서 아래 표는 **기전의 증거가 아니라 계산 예시**입니다
> (그렇게 만든 데이터에서 그 결과가 나오는 것은 당연 — 순환논증). 도구가 실제
> 데이터에서 무엇을 보여줄지는 여러분의 기록으로 확인하세요.
>
> 참고: `slow_breathing.csv` 의 추정 호흡수는 **11.2회/분(HF 대역)** 으로, 아래에서
> 말하는 "느린/공명 호흡 레짐"(≤9회/분, LF 대역)에는 **해당하지 않습니다**. 파일
> 이름보다 리포트의 추정 호흡수를 믿으세요.

이 합성 예시에서 느린 호흡 기록은 안정 기록보다 **RMSSD·HF·SD1 ↑, LF/HF ↓** 로
계산됩니다:

| 지표 | resting | slow_breathing |
|------|--------:|---------------:|
| mean HR (bpm) | 73.3 | 64.1 |
| RMSSD (ms) | 18.8 | 40.6 |
| SDNN (ms) | 23.5 | 41.8 |
| HF (n.u.) | 34.0 | **91.1** |
| LF/HF | 1.94 | **0.10** |
| SD1 (ms) | 13.3 | 28.7 |

### 순간 HR(bpm) 입력

```bash
hrvkit my_watch_hr.csv --unit bpm      # 또는 --unit auto (기본, 중앙값으로 감지)
```

### 옵션

| 옵션 | 의미 |
|------|------|
| `--col NAME|IDX` | 값 열 이름(또는 0-based 인덱스). 미지정 시 자동 추정 |
| `--unit auto\|ms\|s\|bpm` | 입력 단위 (기본 auto: 중앙값 <10→s, <300→bpm, 그 외 ms) |
| `--timestamps` | 값 열을 누적 박동 발생시각으로 보고 차분해 RR 계산 |
| `--clean interpolate\|remove\|none` | 이상박동 보정 방법 (기본 interpolate) |
| `--min-rr`, `--max-rr` | 생리적 범위(ms) (기본 300 / 2000) |
| `--rel-thresh 0.2` | 국소 중앙값 대비 급변 임계값 |
| `--fs 4.0` | 주파수영역 리샘플 주파수(Hz) |
| `--nperseg N` | Welch 구간 길이(표본, 2의 거듭제곱으로 내림). 기본 = 기록의 약 절반(상한 256 → fs 4 Hz에서 64초). **VLF를 보려면 키우세요** (예: `--nperseg 2048` = 512초) |
| `--psd welch\|lomb` | PSD 추정 방법 (기본 welch). `lomb` = **보간 없는** Lomb–Scargle 주기도 — HF 과소추정을 피하고 기록을 쪼개지 않아 VLF가 신뢰 가능해집니다. `--fs`/`--nperseg` 는 lomb에서 무시됩니다 |
| `--ls-oversample K` | Lomb 주파수 격자 과표본 배수 (기본 4, 상한 32). 격자 간격 = 1/(K·기록길이). **해상도(1/기록길이)를 늘리지는 않습니다** — 대역 경계 적분 오차만 줄입니다 |
| `--no-sampen` | 표본 엔트로피 생략 |
| `--compare` | 정확히 2개 파일을 기저 대 개입으로 짝지어 비교 |
| `--paired MANIFEST` | 매니페스트 CSV로 여러 피험자 코호트 통계(Wilcoxon 정확검정·HL 신뢰구간·Holm/BH 보정) |
| `--groups MANIFEST` | 평행군(독립 2군) 비교 — Mann–Whitney 정확검정·HL 이동량 CI·Hedges g·Holm/BH 보정 |
| `--window [SEC]` | 파일 1개를 SEC초 구간으로 나눠 구간별 지표 + Mann–Kendall 추세 + SDANN/SDNN index (값 생략 시 300초) |
| `--step SEC` | 구간 시작 간격(초). 기본은 `--window` 와 동일(겹치지 않음). 작게 주면 슬라이딩 창 |
| `--min-window-beats N` | 구간을 분석하기 위한 최소 박동 수 (기본 20) |
| `--alpha 0.05` | 유의수준 — HL 신뢰구간 수준(1-α)과 유의 판정 기준 |
| `--format text\|json\|csv` | 출력 형식 (기본 text; 여러 파일은 표/CSV, 단일 파일도 1행 CSV, `--paired`/`--groups`는 지표당 1행 CSV, `--window`는 구간당 1행 CSV) |
| `--json` | `--format json` 의 단축 |
| `--version`, `-h` | 버전 / 도움말 |

---

## Notes / limitations

- **VLF는 기본 설정에서 신뢰할 수 없습니다 — 도구가 그렇게 말해 줍니다.**
  VLF 하한 0.003 Hz의 한 주기는 **333초**인데, 기본 Welch 구간은 64초입니다. 구간보다
  느린 성분은 구간별 평균 제거로 사라지므로 VLF는 **과소추정**됩니다(합성 0.008 Hz
  성분으로 측정: 64초 구간 → 참값의 23%, 512초 구간 → 99.8%). 그래서 hrvkit은
  ① 대역에 빈이 하나도 없으면 **`0.0` 이 아니라 `NaN`** 을 내고(0.0은 "진짜 0"과
  "추정 불가"를 구분할 수 없음), ② 리포트에 `vlf_reliable` 상태와 대역별 빈 수를
  같이 찍고, ③ 신뢰 조건(구간 ≥ 333초)이 아니면 그 줄에 경고를 붙입니다.
  `total_power` 는 정의상 VLF를 포함하므로 VLF가 NaN이면 함께 NaN이 됩니다.
  긴 기록에서 VLF가 필요하면 `--nperseg 2048` 처럼 구간을 키우거나, 기록을 아예
  쪼개지 않는 **`--psd lomb`** 을 쓰세요(20분 기록이면 그것만으로 `vlf_reliable=True`).
- **20초 미만 기록은 주파수영역을 거부합니다.** HF 하한(0.15 Hz)조차 해상되지 않아
  과거엔 전 대역 0.0 · LF/HF=inf 를 조용히 반환했습니다.
- **정규화 단위(n.u.)와 LF/HF는 상대 지표입니다.** 절대 파워(ms²)와 함께 해석하세요.
- **주파수 방법을 반드시 명시.** 리샘플 주파수·창·구간 수가 값에 영향을 주므로 리포트에
  같이 출력합니다(재현성). 다른 도구와 비교할 땐 동일 설정인지 확인하세요.
- **`--window` 구간의 VLF와 total_power는 쓰지 마세요.** 5분 창(fs 4 Hz)이면 Welch 구간이
  64초로 VLF 주기(333초)보다 훨씬 짧습니다. VLF 대역에 빈은 2개가 잡히므로 결과는
  **NaN이 아니라 심하게 과소추정된 유한 값**이고, `total_power`는 정의상 VLF를 포함하니
  같은 편향을 그대로 물려받습니다. 구간별 출력은 `vlf_reliable` 플래그(텍스트 리포트의
  주석, CSV·JSON의 열)로 이를 표시합니다 — 구간별로는 시간영역·Poincaré 지표를 보세요.
- **`--groups` 는 무작위배정을 가정하지 않습니다.** 군간 차이는 그 자체로 인과가 아닙니다.
  같은 피험자의 pre–post 자료가 있다면 `--paired` 가 검정력이 훨씬 높습니다.
- **3군 이상 비교는 제공하지 않습니다.** Kruskal–Wallis + 사후검정이 필요하며, 2군만
  골라 쓰면 다중비교가 숨습니다. `--groups` 는 군이 2개가 아니면 거부합니다.
- 이 도구는 지표를 **계산·요약**할 뿐, 임상적 판단을 대신하지 않습니다.

---

## Tests

```bash
python3 -m pytest -q      # 447 tests, 전부 오프라인. numpy/scipy 있으면 교차검증
                          # (FFT·Welch·SampEn·DFA), 없으면 해당 참조 테스트만 skip.
```

## License

MIT © 2026 hyeonjoong
