# eegband — EEG 대역파워 분석기 (단일/다채널, CSV·EDF)

EEG 시계열을 주면 **Welch PSD**로 표준 대역(delta/theta/alpha/beta/gamma)의
**절대·상대 파워**, **슬로우파 활동(SWA = delta)**, **스펙트럼 에지 주파수(SEF95)**,
**피크 주파수**, **대역별 피크(알파 피크 = IAF)**, **스펙트럼 엔트로피**, **총 파워**,
**대역비**, 그리고 **1/f 비주기(aperiodic) 배경**(지수·오프셋·R²와 **배경을 제거한
진동성 대역파워**)을 계산합니다. **신호 품질/아티팩트 지표**(클리핑·평탄·보간 비율, RMS)와
**전원(50/60 Hz) 잡음 진단**(배경 대비 비율·µV² 초과분·오염 대역, 에일리어싱 포함)도
함께 내고, `--notch` 로 그 잡음을 스펙트럼에서 제거한 뒤 계산할 수 있습니다.

필요하면 신호를 **에폭(epoch)** 으로 잘라 에폭별 표 + 요약(mean±SD, SEM, 95% CI,
**자기상관 보정 CI**, 중앙값·IQR)과 **시간 추세 검정**(Mann–Kendall + Theil–Sen 기울기)을
냅니다. `--baseline` 을 주면 투약 전 구간을 그 피험자 자신의 기저로 삼아 **지표별 기저 대비
변화**(Δ·Δ%·95% CI·Hedges' g·Welch t·**BH-FDR 보정 q**)를 냅니다 — 약력학 분석의 표준 형식. 입력은 **CSV/TSV**(구분자 자동 감지, 유럽식 소수점 쉼표 허용)와
**EDF/EDF+/BDF**(임상 수면검사 표준 포맷)를 지원하고, **다채널·여러 파일 일괄 분석**과
채널/파일 **비교표**를 냅니다. **FFT(radix-2 Cooley–Tukey)와 EDF 파서까지 전부 표준
라이브러리로 자체 구현** — numpy/scipy 없이 어디서나 돕니다.

---

## 목적 / Why this exists

**한국어.** 수면·뇌파 연구에서 "이 구간이 얼마나 느린파(느린 진동)가 우세한가?"는
반복적으로 계산해야 하는 핵심 지표입니다. 특히 **BELL-001**처럼 슬로우파 수면(SWS)을
1차 종말점으로 삼는 경우, delta(0.5–4 Hz) 대역파워 = **SWA**를 정확한 PSD 정규화로
구해야 합니다. 손으로 하려면 (1) 창(window) 적용, (2) 세그먼트 평균(Welch), (3)
단측 스펙트럼의 µV²/Hz 정규화, (4) 대역 적분, (5) SEF/피크/비율 계산을 매번 맞춰야
하고 실수가 잦습니다. 게다가 대역파워는 **1/f 배경**과 **진짜 진동**이 섞인 값이라,
배경만 변하는 약물 효과를 "델타 증가"로 오해할 수 있습니다. `eegband`는 이 전 과정을
한 번에 처리하고, 배경(1/f 지수)과 진동 성분을 분리해 보여주며, 알파(각성) vs
델타(깊은수면) 우세가 어떻게 뒤집히는지 바로 보여줍니다. 환자 데이터는 로컬에서만
처리되며 외부 전송이 없습니다.

**English.** In sleep/EEG work you repeatedly need "how slow-wave-dominant is this
segment?" For endpoints like **BELL-001** (slow-wave sleep), the delta (0.5–4 Hz)
band power — the **SWA** — must be computed with a correctly normalized PSD. Done by
hand that means getting the window, Welch segment averaging, one-sided µV²/Hz
scaling, band integration, and the SEF/peak/ratio math right every time. And a raw
band power mixes a broadband **aperiodic 1/f background** with genuine oscillations,
so a drug that only steepens the background looks like "more delta". `eegband` runs
the whole pipeline, separates those two components, and makes the alpha-vs-delta
dominance flip obvious. Every number — including the FFT and the EDF parser — is
computed from first principles in the standard library, so it runs anywhere Python
3.9+ is installed with **zero dependencies**.

품질/검증: 자체 FFT는 순수 파이썬 O(n²) DFT(및 numpy.fft)와 **~1e-9**까지 일치하고,
Welch PSD는 `scipy.signal.welch`(density)와 **~1e-9**까지 일치합니다. 알려진 진폭·
주파수의 정현파를 넣으면 총 파워가 **Parseval(분산 = A²/2)** 과 일치하고 해당 대역에
정확히 떨어집니다. 1/f 지수는 **실제 지수를 아는 합성 스펙트럼**에서 무잡음일 때
기계정밀도로, 실측 Welch PSD에서 ±0.1 이내로 복원되고, Mann–Kendall·Theil–Sen·
Student-t 분위는 `scipy.stats`와 일치합니다 — 모두 테스트로 고정되어 있습니다.

---

## Install

```bash
cd ~/Downloads/02_프로젝트/깃헙/eegband-뇌파대역분석
python3 -m pip install -e .
```

설치 없이도 실행할 수 있습니다:

```bash
python3 -m eegband.cli <파일.csv> --fs 128
```

또는 폴더 안의 **`실행.command` 더블클릭** (예제 데이터로 바로 시연).

---

## Usage

### 1) 값 열만 (표본화율은 `--fs` 로)

```bash
eegband examples/delta_deep_sleep.csv --fs 128
```

입력 (`examples/delta_deep_sleep.csv`):

```
eeg_uv
13.279070
17.625371
...
```

출력 (요약):

```
[0] 신호 품질 / Signal quality
    amplitude: min = -81.510  max = 76.997  ptp = 158.506  mean = -2.734e-09  RMS = 33.673 µV
    interpolated = 0/5120 (0.0%),  clipped(rail) = 0 (0.0%),  flat-run(≥13) = 0 (0.0%, longest 1)
    quantisation step = 0.0007 µV (227739 levels across the range)
    전원잡음 / mains line noise: none detected at 60 Hz (auto-detected); peak/background ≤ 1.4× (threshold 3×)

[1] 대역파워 / Band power  (absolute µV², relative %, prominent in-band peak Hz)
    band    range(Hz)         abs(µV²)    rel(%)  peak(Hz)
    delta   0.5–4             1072.564      98.7      1.50  ← SWA
    theta   4–8                  5.030       0.5       n/a
    alpha   8–13                 6.539       0.6     11.00
    beta    13–30                1.774       0.2       n/a
    gamma   30–45                0.628       0.1       n/a
    total   0.5–45            1086.536     100.0

[2] 슬로우파 활동 / Slow-wave activity (SWA = 0.5–4 Hz) — key sleep endpoint
    SWA absolute  = 1072.564 µV²
    SWA relative  = 98.7 %
    dominant band = delta   ← slow-wave/delta dominant

[3] 스펙트럼 요약 / Spectral summary
    peak frequency          = 1.50 Hz
    SEF95 (spectral edge freq) = 1.89 Hz
    spectral entropy (norm) = 0.313   (1=flat/white, 0=single rhythm)
    alpha peak (IAF)        = 11.00 Hz
    total power (0.5–45 Hz) = 1086.536 µV²

[4] 비주기(1/f) 배경 + 진동성 파워 / Aperiodic background & oscillatory power
    fit = 0.50–45.00 Hz, mode = robust, bins used = 164/179
    model: PSD(f) = 10^offset · f^(−exponent)   (exponent > 0 = falling with f)
    exponent (χ) = 1.854 ± 0.024 (SE, 낙관적 하한 / optimistic: neighbouring Welch bins are not independent)
    offset (log10 µV²/Hz @1 Hz) = 1.458
    fit R² = 0.973 (적합에 쓴 빈 / fitted bins), 0.865 (전체 빈, 진동 포함 / all bins)
    slope by half-range: 2.65 (lower half) vs 1.85 (upper half)   ⚠ knee/bend — a single exponent is an average
    band          osc(µV²)  osc(%osc)  peak(Hz)   height
    delta         1020.311       99.4      1.50     2.21
    theta            0.759        0.1       n/a      n/a
    alpha            4.702        0.5     11.00     1.55
    beta             0.098   9.57e-03       n/a      n/a
    ...
```

`peak(Hz)` 는 **뚜렷한(prominent) 대역 내 피크**만 보고합니다 — 1/f 기울기 위 잡음의
argmax는 `n/a`로 억제됩니다(위 delta 예제에서 이 데이터에 실재하는 11 Hz 스핀들만 알파 피크로 표시).

### 2) 값 열 + 시간 열 (fs 자동 추정·교차검증)

```bash
eegband examples/alpha_wake.csv --time time_s --value eeg_uv
```

시간 열이 있으면 fs를 추정하고, `--fs`를 **명시**했는데 1% 넘게 다르면 경고 후 **추정값**을
씁니다. 이 각성 트레이스는 **alpha 우세**(≈88%, 피크 10 Hz)로 나와, 위 델타 예제와 **뒤집힙니다**.

### 3) 다채널(와이드) CSV — 채널별 분석 + 비교표

```bash
eegband examples/multichannel_wide.csv --channels all
eegband study.csv --channels Fp1,Cz --fs 256        # 특정 채널만
eegband study.csv --list-channels                   # 어떤 열이 있는지 먼저 확인
```

```
================================================================================================================================
  [비교 / Series comparison]  n = 3
================================================================================================================================
    series        fs    fs_src   dur(s)   total(µV²)  delta%  theta%  alpha%   beta%  gamma%  dominant     SEF   expo    R²
    Fp1          128  inferred     20.0      104.044   33.4%    6.2%   52.7%    5.5%    2.2%     alpha   18.86   1.31  0.98
    Cz           128  inferred     20.0      857.355   98.7%    0.6%    0.3%    0.3%    0.1%     delta    1.87   1.72  0.99
    O1           128  inferred     20.0      215.916   12.3%    2.9%   81.1%    2.6%    1.1%     alpha   10.94   1.20  0.98
```

이 예제는 **실제 1/f 지수를 1.3 / 1.7 / 1.2 로 합성**한 데이터입니다 — 표의 `expo`
(1.31 / 1.72 / 1.20)가 그 값을 복원하는지 확인할 수 있습니다(`examples/generate_examples.py` 참고).

### 4) EDF / EDF+ / BDF 기록 (임상 표준 포맷)

```bash
eegband night.edf --list-channels                       # 채널·fs·단위 확인
eegband night.edf --channels all --epoch 30 --csv > bands.csv
eegband night.edf --channels "EEG Fpz-Cz" --start 3600 --duration 600
```

- **fs는 EDF 헤더에서** 가져옵니다(채널마다 다른 표본화율도 지원). `--fs`를 명시했는데
  헤더와 1% 넘게 다르면 경고하고 헤더 값을 씁니다.
- 물리 단위를 읽어 **µV로 변환**합니다(mV/V/nV 자동 환산, 알 수 없는 단위는 경고 후 그대로).
- `EDF Annotations` 채널은 목록에 표시하되 분석에서 제외합니다. **EDF+D**(불연속)는
  경고와 함께 연속으로 취급합니다. 잘린 파일·레코드 수 미기재(-1)도 처리합니다.
- **개인정보**: EDF 헤더의 환자/기록 식별 필드와 **기록 시작 날짜·시각은 아예 읽지
  않습니다** — 리포트·JSON·CSV로 새어 나갈 경로가 없습니다.
- `--start` / `--duration` 을 주면 **필요한 레코드만 읽습니다**(8시간 기록에서 10분만
  볼 때 10분만큼의 I/O).

### 5) 여러 파일 일괄 분석 (코호트)

```bash
eegband subj*.csv --fs 128 --epoch 30 --csv > cohort.csv
```

파일당(또는 파일×채널당) 한 계열로 분석하고, CSV에는 `series`·`source_file` 열이
붙어 R/SAS에서 바로 그룹별 분석을 돌릴 수 있습니다. 일부 파일이 실패하면 그 파일만
stderr에 보고하고 **나머지는 정상 처리**하며 종료코드 1(전부 실패는 2)을 반환합니다.

### 6) 에폭별 분석 + 시간 추세

```bash
eegband examples/dose_session.csv --epoch 20
```

```
[6] 에폭별 / Per-epoch  (epoch = 20 s, n_epochs = 12)
     ep   t0(s)   t1(s)   delta   theta   alpha    beta   gamma    peak     SEF   expo  dominant
      0     0.0    20.0   86.0%    1.2%   10.8%    1.3%    0.7%    1.56   10.13   1.09  delta
      1    20.0    40.0   85.7%    1.4%   10.8%    1.3%    0.8%    1.56   10.13   1.14  delta
      2    40.0    60.0   86.9%    1.4%    9.7%    1.4%    0.7%    1.56   10.11   1.13  delta
      3    60.0    80.0   86.8%    1.4%    9.8%    1.3%    0.7%    1.56   10.11   1.12  delta
      ...
    delta-dominant epochs = 12/12  (100 %)   (a.k.a. 'SWA density'; NOT slow-wave events per minute)
    relative SWA across epochs = 91.1 ± 5.0 % (SD, n-1),  SEM 1.4 %,  95% CI [88.0, 94.3] %  (n=12)
      자기상관 보정 / autocorr-adjusted: ρ₁ = 0.74, n_eff = 2.0, 95% CI [46.5, 135.8] %
      자기상관 보정 / autocorr-adjusted: ρ₁ = 0.75, n_eff = 2.0, 95% CI [-5108.859, 7455.835] µV²
      자기상관 보정 / autocorr-adjusted: ρ₁ = 0.76, n_eff = 2.0, 95% CI [0.350, 5.619] log10 µV²
      자기상관 보정 / autocorr-adjusted: ρ₁ = 0.75, n_eff = 2.0, 95% CI [-5026.442, 7532.422] µV²
      자기상관 보정 / autocorr-adjusted: ρ₁ = -0.13, n_eff = 12.0, 95% CI [1.085, 1.127]
    시간 추세 / Trend across epochs (Mann–Kendall 검정 + Theil–Sen 기울기, per min)
      endpoint               slope/min        95% CI (slope/min)     tau         p
      relative SWA               3.121           [-0.017, 4.558]   0.424    0.0641   %/min
```

- **자기상관 보정 CI**: 연속 에폭은 서로 강하게 상관되어 있어 순진한 CI는 지나치게 좁습니다.
  lag-1 자기상관 ρ₁로 **유효표본수 n_eff = n(1−ρ)/(1+ρ)** 를 구해 넓힌 CI를 함께 보고합니다.
- **시간 추세**: 순위 기반 **Mann–Kendall**(동순위·연속성 보정 정규근사) + **Theil–Sen**
  기울기와 Sen 95% CI. 밤새 SWA가 감소하는 항상성 감쇠나 약물 시간경과를 정규성 가정
  없이 정량화합니다. 표시 단위는 기록 길이에 맞춰 초/분/시로 자동 선택하고, JSON은 항상
  **초당 기울기**로 냅니다.
- 요약 통계는 **채택(비아티팩트) 에폭만** 사용합니다.

### 7) 아티팩트 에폭 제거 (SWA 종말점 보호)

```bash
eegband night.edf --channels "EEG Fpz-Cz" --epoch 30 --max-amp 150 --max-grad 40
```

- `--max-amp T` : 최대 |진폭| > T µV 인 에폭을 제외(움직임·전극 팝).
- `--max-grad G` : 인접 표본 간 최대 |변화량| > G µV 인 에폭을 제외 — **진폭 한계 안에
  숨은 급격한 스파이크/디지털 글리치**를 잡습니다(진폭 기준만으로는 놓칩니다).
- 제외된 에폭은 표에 `✗REJ`로 표시되고 SWA 요약(mean/SD/CI/density/추세)에서 빠집니다.

### 8) 통계 SW로 넘기기 (CSV 내보내기)

```bash
eegband examples/delta_deep_sleep.csv --fs 128 --epoch 20 --csv > epochs.csv
# base-R read.csv / SAS PROC IMPORT 용 깔끔한 사각형(주석 없음):
eegband examples/delta_deep_sleep.csv --fs 128 --epoch 20 --csv --no-comment > epochs.csv
```

에폭이 있으면 에폭당 한 행, 없으면 계열당 한 행으로 대역별 `abs/rel/peak`,
**배경보정 `osc`·`adj_peak`**, 총파워, 피크, SEF, **엔트로피**, **대역비 3종**,
**1/f `ap_exponent`·`ap_offset`·`ap_r2`·`osc_total`**, 우세대역을 CSV로 냅니다.
`--max-amp`/`--max-grad`를 쓰면 `peak_amp_uv`·`max_grad_uv`·`rejected` 열이, 다계열
분석에서는 `series`·`source_file` 열이 추가됩니다. 맨 앞 `#` 주석 행에 전체 분석
파라미터를 담아 자기재현이 가능하며, `--no-comment` 로 이 행을 끄면 base-R/SAS가
옵션 없이 바로 읽는 깔끔한 사각형이 됩니다.

### 9) 전원(50/60 Hz) 잡음 진단·제거

동봉된 예제로 바로 확인할 수 있습니다 (`examples/dose_session.csv` — 합성 기록,
60 Hz 전원잡음 포함):

```bash
eegband examples/dose_session.csv --bands 'delta:0.5-4,theta:4-8,alpha:8-13,beta:13-30,gamma:30-90'
eegband examples/dose_session.csv --notch --bands 'delta:0.5-4,theta:4-8,alpha:8-13,beta:13-30,gamma:30-90'   # gamma 56.0 → 14.2 µV²
```

내 데이터에 쓸 때:

```bash
eegband rec.csv --fs 256                  # 기본: 50/60 자동 판별 후 "보고만"
eegband rec.csv --fs 256 --notch          # 검출된 기본파+고조파를 스펙트럼에서 제거
eegband rec.csv --fs 256 --line-freq 60   # 자동 판별 대신 직접 지정
eegband rec.csv --fs 256 --line-bw 2      # 창 반폭 ±2 Hz (기본 1.0)
eegband rec.csv --fs 256 --line-freq off  # 검사 자체를 끄기
```

`[0] 신호 품질` 절에 이런 표가 붙습니다:

```
    전원잡음 / mains line noise @ 60 Hz (auto-detected) — 검출됨 (제거 안 함) / detected, NOT removed — add --notch
      harmonic      freq(Hz)   peak/bg   excess(µV²)   bands affected
      ×1                  60     473.7        41.858   ← gamma
      ×2 (aliased)        80       1.1         0.000   -
      ×3 (aliased)        20       1.2         0.000   -
      전기잡음 비율 / share that is electrical: gamma 74.7%
```

- **검출**: 각 고조파의 ±`--line-bw` 창 안 최고 PSD를 **주변 배경의 중앙값**과 비교해
  `RATIO_THRESHOLD`(3×) 이상이면 전원잡음으로 판정합니다. `excess(µV²)`는 그 창에서
  배경을 뺀 초과 파워 — 진폭 A의 정현파면 정확히 **A²/2** 입니다(손 검산 가능).
- **제거(`--notch`)**: 해당 빈들을 양옆 빈 사이의 **log-선형 보간**으로 대체(스펙트럼
  보간)한 뒤 대역파워·SEF·엔트로피·1/f 적합을 계산합니다. PSD 위에서 처리하므로
  시간영역 노치 필터의 **링잉·위상 왜곡·경계 효과가 없습니다**.
- **에일리어싱**: `fs/2 < f₀` 이면 전원 성분은 사라지지 않고 `|f₀ − k·fs|` 로 접혀
  들어옵니다. fs=100 Hz에서 60 Hz는 **40 Hz** — gamma 한가운데 — 에 나타납니다.
  이 경우에도 리포트는 침묵하지 않고 접힌 위치를 찾아 다음처럼 알려줍니다:

  ```
      전원잡음 / mains line noise: none detected at 60 Hz (auto-detected); 이 fs 에서는 대역 내 기본파가 없습니다 / no in-band fundamental at fs=100 Hz
        ⓘ 에일리어싱 의심 / suspected mains alias at 40 Hz (150×, 60 Hz 접힘) — 자동 판정에서 제외했습니다(그 자리에서는 전원 접힘과 진짜 리듬이 같은 측정값). 제거하려면 --line-freq 60 를 명시하세요.
  ```
- **접힌 성분은 절대 자동 제거하지 않습니다.** 접혀 들어온 위치에는 그것이 전원인지 진짜
  리듬인지 구별할 정보가 **없습니다**(fs=80 Hz에서 50 Hz의 3고조파는 정확히 **10 Hz —
  알파 한가운데** 에 떨어집니다). 그래서 `auto` 는 접힌 고조파를 `ⓘ 에일리어싱 의심` 으로
  **보고만** 하고 `--notch` 대상에서 제외합니다(알파가 지워지지 않습니다). 정말 제거하려면
  `--line-freq 50` 처럼 전원 주파수를 **명시**해야 하며, 그 경우 그 자리의 진짜 활동도 함께
  지워진다는 것을 리포트가 다시 경고합니다.
- 기본파(`auto`)는 **전체 기록 스펙트럼에서 한 번만** 결정하고 모든 에폭에 같은 값을
  씁니다 — 조용한 에폭이 몰래 50↔60을 바꿔 다른 주파수를 제거하는 일이 없습니다.

### 10) 기저 대비 변화 (`--baseline`) — 약력학 종말점

```bash
eegband examples/dose_session.csv --epoch 30 --baseline 120 --notch
```

(예제는 0–120초가 기저, 이후 슬로우파 진폭이 2배 = 파워 4배가 되도록 합성했습니다.)

처음 120초(투약 전)를 기저로 잡고, 이후 에폭을 지표별로 대조합니다:

```
[7] 기저 대비 변화 / Change from baseline  (baseline = 0–120 s)
    baseline epochs = 4,  post epochs = 4,  BH-FDR family m = 15
      endpoint                baseline        post           Δ       Δ%                95% CI (Δ)        g       n_eff     df           p      q(FDR)
      relative SWA              86.774      95.863       9.089     10.5            [8.471, 9.706]    25.38     4.0/4.0    3.9    2.70e-06    1.02e-05*  %
      SWA absolute             513.798    1823.618    1309.820    254.9      [1203.779, 1415.860]    22.27     4.0/4.0    3.5    1.17e-05    2.52e-05*  µV²
      SWA absolute (log10)       2.711       3.261       0.550     20.3            [0.521, 0.580]    28.17     4.0/4.0    6.0    7.76e-09    9.78e-08*  log10 µV²
      ...
```

- 대상 지표는 에폭 요약과 **같은 목록**(SWA 상대/절대/log10, 총파워, 1/f 지수, SEF,
  엔트로피, 그리고 `--bands` 로 정의된 **모든 대역의 절대·상대 파워**)입니다.
- **Welch 부등분산 t-검정**을 쓰되, 각 군의 분산을 **AR(1) 유효표본수**
  `n_eff = n(1−ρ̂)/(1+ρ̂)` 로 나눕니다 — 즉 **SE·95% CI·자유도가 모두** 보정됩니다.
  연속 에폭은 독립 관측이 아니기 때문입니다. 다만 보정 폭은 ρ̂ 와 에폭 수에 달려 있어,
  ρ̂ ≤ 0 이거나 창이 짧으면 **거의 0** 일 수 있습니다(`n_eff` 는 2 미만으로 내려가지
  않습니다). 표의 `n_eff` 열이 `n` 과 같으면 보정이 실제로는 일어나지 않은 것입니다.
- **Hedges' g** 는 원자료 합동 SD에 소표본 보정 `1 − 3/(4·df−1)` 를 적용합니다(효과크기는
  분포의 성질이므로 자기상관 보정을 하지 않습니다). ⚠ 이 g는 **한 기록의 에폭간 변동**으로
  표준화한 값이라 문헌의 **피험자간 g와 직접 비교할 수 없습니다** — 에폭 변동이 작으면
  g가 수십까지 나옵니다.
- 한 번에 여러 지표를 검정하므로 **Benjamini–Hochberg FDR** q를 함께 냅니다(`*` = q<0.05).
  기본 대역에서는 SWA가 곧 delta 대역이라 `swa_*` 와 `delta_*` 는 **같은 검정**입니다.
  이런 구조적 중복은 FDR 가족(m)에서 **한 번만** 세고 서로 q를 공유합니다(표의
  `BH-FDR family m` 이 실제 m). 대역파워끼리는 강하게 상관되므로 BH는 근사임을 유념하세요.
- 아티팩트로 제외된 에폭(`--max-amp`/`--max-grad`)은 양쪽 창 어디에도 들어가지 않습니다.
- 한쪽 창의 에폭이 2개 미만이면 계산하지 않고 **경고**합니다(조용히 0을 내지 않습니다).

> **한계**: 이것은 **한 기록 안의 전·후 비교**입니다. 위약 대조가 아니며, 약효와 시간에
> 따른 자연 변화(각성도 저하·수면 진행)를 구분하지 못합니다.
>
> **집단 결론을 내는 방법**: 피험자별로 한 번씩 돌려 `--csv-summary` 를 모은 뒤,
> `swa_absolute_uv2_base_pct_change`(또는 `..._base_delta`) 열을 피험자 단위 관측치로
> 삼아 군간 비교를 별도 도구에서 하세요. 각 지표마다
> `<endpoint>_base_mean / _base_post_mean / _base_delta / _base_pct_change /
> _base_ci_lo / _base_ci_hi / _base_hedges_g / _base_p / _base_q_fdr` 열이 나옵니다.

### 옵션

| 옵션 | 의미 |
|------|------|
| `INPUT ...` | CSV/TSV 또는 EDF/EDF+/BDF 파일. 여러 개 지정 가능(일괄 분석) |
| `--fs 128` | 표본화율 Hz (미지정 시 128 가정하고 경고). 시간 열/EDF 헤더가 있으면 그 값 우선 |
| `--value NAME` | 값(µV) 열 이름 (미지정 시 자동 감지) |
| `--time NAME` | 시간(초) 열 이름 (fs 추정·교차검증) |
| `--channels all\|A,B` | 다채널 분석: 모든 열/채널 또는 지정한 것들 |
| `--list-channels` | 입력의 채널(열)·fs·단위 목록만 출력하고 종료 |
| `--start 3600` `--duration 600` | 분석 구간(초) 잘라내기. EDF는 필요한 레코드만 읽음 |
| `--epoch 30` | 에폭 길이(초) → 에폭별 표 + 요약 + 추세 |
| `--max-amp 150` | 최대 \|진폭\| 초과 에폭을 요약에서 제외 (표에 `✗REJ`) |
| `--max-grad 40` | 인접 표본 간 최대 \|변화량\| 초과 에폭을 제외 |
| `--nperseg N` | Welch 세그먼트 길이(표본). 기본 ~4초, 신호 길이로 상한 |
| `--noverlap N` | 세그먼트 겹침(표본). 기본 nperseg//2 (50%) |
| `--detrend {constant,linear,none}` | 세그먼트 디트렌드. `linear`는 드리프트의 delta 누설을 막음 |
| `--average {mean,median}` | Welch 세그먼트 평균. `median`은 일시적 아티팩트에 강건 |
| `--line-freq auto\|off\|HZ` | 전원잡음 주파수. 기본 `auto`(50/60 자동 판별 후 보고만) |
| `--notch` | 검출된 전원잡음과 고조파를 스펙트럼 보간으로 제거한 뒤 계산 |
| `--line-bw 1.0` | 전원잡음 창의 반폭(Hz). 기본 1.0 |
| `--baseline 600` | 처음 600초를 기저로 삼아 이후 에폭과 지표별 대조 (`--epoch` 필요) |
| `--aperiodic {robust,ols,off}` | 1/f 배경 적합 방식 (기본 robust) |
| `--fit-range 2-45` | 1/f 적합 주파수 범위 (기본: 분석 대역 전체) |
| `--bands ...` | 대역 재정의, 예: `delta:0.5-4,theta:4-8,alpha:8-13,beta:13-30,gamma:30-45` |
| `--sef 95` | 스펙트럼 에지 주파수 백분위 (기본 95 → SEF95) |
| `--swa-band 0.5-4` | SWA 대역을 명시 (커스텀 `--bands` 에 `delta` 가 없을 때) |
| `--json` | JSON 출력 (다계열이면 `series` 배열) |
| `--csv` | 대역파워 표를 CSV로 출력 (R/SAS/Prism 용) |
| `--csv-summary` | 계열당 한 줄 요약표 (전원잡음 열 + `--baseline` 사용 시 대조 열) |
| `--psd-csv` | 스펙트럼 자체를 CSV로 (`--notch` 적용분 반영) |
| `--no-comment` | `--csv` 출력에서 맨 앞 `#` 주석 행 생략 |

---

## 어떻게 계산하나 (How it's computed)

- **Welch PSD**: 신호를 길이 `nperseg` 세그먼트로 자르고(기본 50% 겹침), 세그먼트마다
  디트렌드(기본 평균 제거; `--detrend linear`면 최소제곱 직선 제거) 후 **주기형 Hann
  창**을 곱하고, 다음 2의 거듭제곱까지 0-패딩하여 **자체 FFT**로 변환합니다. 주기도를
  `|X|² / (fs·Σw²)` 로 정규화하고, 단측화(DC·Nyquist 제외 ×2), 세그먼트 평균(mean; 또는
  `--average median` — 중앙값 + 편향보정) → **µV²/Hz**. 입력이 µV라고 가정합니다.
- **대역파워 + 대역별 피크**: PSD를 각 대역 `[lo, hi]`에서 사다리꼴 적분(경계는 선형 보간).
  상대파워 = 대역/총파워(0.5–45 Hz). 각 대역의 **최대 PSD 주파수**도 보고하며, 알파 대역의
  피크는 **개인 알파 주파수(IAF)** 로 요약에 별도 표기합니다.
- **SWA**: delta(0.5–4 Hz) 절대 파워를 별도 강조 — BELL-001의 슬로우파 종말점.
- **1/f 비주기 배경**: 로그-로그 공간에서 `log10 PSD ≈ offset − exponent·log10 f` 를
  최소제곱 적합합니다. `robust`(기본)는 잔차가 **MAD 기반 강건 SD의 2배**를 넘는 빈
  (= 진동 피크)을 반복적으로 제외하고 재적합하여 배경만 남깁니다(MAD는 절단해도 수축하지
  않아 발산하지 않음). 여기서
  **지수(χ)**·**오프셋**·**기울기 SE**·**R²**(적합 빈 / 전체 빈)를 보고하고,
  **진동성(배경보정) 대역파워** = `∫ max(PSD − 배경, 0)`, 그리고 **평탄화(flattened)
  스펙트럼**(log10 PSD − log10 배경)에서 대역별 피크를 찾습니다.
- **SEF95 / 피크 / 스펙트럼 엔트로피 / 총파워 / 대역비**: 누적 파워가 95%가 되는 주파수
  (구간 내 PSD가 선형이므로 누적은 2차식 → 근을 **정확히** 풀어 sub-bin 오차 없음),
  분석대역 내 최대 PSD 주파수, 대역 내 PSD 빈(총 빈 수로 정규화)을 확률분포로 본 **정규 섀넌
  엔트로피**(1=백색/평탄, 0=단일 리듬 — 스펙트럼 평탄도/복잡도 지표이며 수면단계 마커는 아님),
  대역 총적분, `theta/alpha`·`delta/beta`·느림지수 `(delta+theta)/(alpha+beta)`.
- **신호 품질**: 진폭 min/max/ptp/mean/RMS와 함께 **클리핑(레일 고정)**, **평탄 구간(연속
  동일값 ≥3)**, **보간 비율**을 계산해 흔한 기록 문제(ADC 포화·리드 탈락·과도한 결측)를
  표면화합니다. 분석 자체는 절대 바꾸지 않는 순수 기술 지표입니다.
- **에폭 + SWA density + 아티팩트 제거 + 추세**: `--epoch`로 비겹침 에폭 분할, 에폭별 지표 +
  delta 우세 에폭 비율(SWA density), 종말점(상대 delta·절대 SWA·총파워·1/f 지수)의
  **mean±SD, SEM, t-기반 95% CI, 자기상관 보정 CI, 중앙값·IQR·범위**, 그리고 **Mann–Kendall
  추세검정 + Theil–Sen 기울기(Sen 95% CI)** 를 요약합니다. `--max-amp`/`--max-grad`로
  아티팩트 에폭을 요약에서 제외합니다.
- **입력 처리**: 구분자(`,` `;` 탭 `|`)를 헤더 기준으로 자동 감지하고, 쉼표가 구분자가 아닐 때
  **소수점 쉼표**(`12,3`)를 감지해 변환합니다(자릿수 구분 `1,234,567`은 변환하지 않고 결측
  처리). 인코딩은 UTF-8(BOM 포함) → cp949 → latin-1 순으로 시도합니다. EDF/BDF는 헤더를
  직접 파싱해 digital→physical 보정을 적용합니다.

## Notes / limitations

- PSD는 `scipy.signal.welch(scaling='density')`와 동일한 관례(주기형 Hann,
  detrend='constant', 단측 ×2)를 따릅니다. 대역파워는 정현파에서 Parseval(분산=A²/2)과
  일치하도록 정규화되어 손 검산이 가능합니다.
- **주파수 분해능**은 `df = fs/nfft`로 정해집니다. 사용자가 `--fs 128`을 직접 주면 기본
  4초 세그먼트에서 nfft=512, df=0.25 Hz가 되어 모든 표준 대역 경계(0.5/4/8/13/30/45)가
  정확히 빈에 떨어집니다. 시간 열/EDF 헤더에서 fs가 128의 배수가 아니면(예: 100 Hz →
  df≈0.195 Hz) 경계가 빈과 어긋나지만 경계는 선형 보간으로 적분하므로 결과에 영향이
  없습니다. (참고: 경계가 빈에 정렬된다는 것이 0.5 Hz 성분을 분리 해상한다는 뜻은
  아닙니다 — 주기형 Hann 주엽 폭은 ≈4·df 입니다.)
- 비유한(NaN/inf)·빈 셀은 **선형 보간**으로 메우고 그 개수를 리포트합니다. 상수(분산 0)
  신호는 총파워 0, 상대파워 0, 피크·SEF·우세대역·1/f 적합은 `n/a`로 안전하게 처리합니다.
- 시간 열이 **불규칙**하면 경고합니다(Welch는 균등 표본을 가정). 명시한 `--fs`와 다르면
  경고 후 추정값을 사용합니다. 시간 열은 **초(second)** 단위로 가정하며, 열 이름이
  `time_ms`/`ms` 이면 밀리초로 보고 자동 변환합니다. `sample`/`index` 같은 **표본 카운터**는
  시간 열로 자동 인식하지 않습니다(초가 아니므로 fs를 오염시킴). 필요하면 `--time`으로 명시하세요.
- **1/f 지수의 해석**: 적합 범위 안에 강한 진동이 있으면(특히 범위의 **낮은 두 옥타브**)
  지수가 부풀 수 있습니다. 그런 경우를 자동으로 감지해 경고하고 `--fit-range 2-45`
  (또는 문헌에서 쓰는 `30-45`) 같은 재적합을 권합니다. **knee(꺾임)는 모델링하지 않습니다** —
  넓은 범위에서 꺾임이 있으면 단일 지수는 평균 기울기이며, 보고되는 R²가 그 부적합을
  드러냅니다(R² < 0.8이면 경고). 배경보정 파워는 적합 범위와 겹치는 부분에서만 계산되며,
  범위 밖 대역은 `n/a`입니다.
- **비주기 지수의 SE**는 인접 Welch 빈이 독립이 아니므로(창 주엽·겹친 세그먼트) **하한**에
  가깝습니다. 피험자간 비교에는 기록 단위 지수를 모아 별도 분석하세요.
- **인코딩/구분자**: UTF-8이 아니면 cp949(한국 Excel)·latin-1 순으로 자동 시도하고 어떤
  인코딩·구분자로 읽었는지 경고합니다. 소수점 쉼표는 구분자가 쉼표가 아닐 때만 적용합니다.
- **커스텀 `--bands`** 가 서로 겹치거나 빈 구간을 남기면 상대파워 합이 100%가 아니게 되며,
  총(total) 행에 실제 합계를 표시하고 경고합니다(기본 대역은 항상 100%).
- **우세 대역**이 근소차(상위 두 대역이 1% 이내)면 `⚠ near-tie`로 표시합니다.
- **대역 피크(IAF 포함)**는 **뚜렷한 피크**일 때만 보고합니다. 원 스펙트럼에서는 대역 내부의
  국소 최대이면서 양쪽 경계보다 크고 대역 중앙값의 3배 이상일 때, 배경보정(평탄화)
  스펙트럼에서는 추가로 **강건 SD의 3배 이상 + 폭(양옆 빈이 함께 상승)** 조건을 만족할 때만
  보고합니다. 합성 1/f 스펙트럼에서 측정한 대역당 거짓양성률은 ≈0.4%이며, 배경 대비
  뚜렷하지 않은 약한 리듬은 보수적으로 `n/a`가 됩니다(있는 리듬을 놓칠 수 있음).
- **에폭 요약의 95% CI**는 한 기록 내 에폭들의 분포입니다. 에폭은 서로 **자기상관**이 크므로
  이 CI는 **피험자간(집단) 추론 CI가 아닙니다**. 자기상관 보정(n_eff) CI를 함께 제공하지만
  이것도 AR(1) 1차 근사이며, 피험자간 추론은 기록/피험자 단위 요약값으로 별도 분석하세요.
- **추세 검정**은 O(n²)이라 에폭 수가 1500을 넘으면 계산하지 않고 그 사실을 경고합니다
  (30초 에폭 하룻밤 ≈ 1000개). 기술통계는 영향받지 않습니다.
- **EDF/BDF**: 연속(EDF/EDF+C)·불연속(EDF+D, 경고 후 연속 취급)·16비트 EDF·24비트 BDF를
  읽습니다. 채널마다 다른 표본화율을 지원하며, 요청한 채널만 읽습니다. 압축(EDF.gz)이나
  GDF/BrainVision/FIF 등 다른 포맷은 지원하지 않습니다.
- **스펙트럼 엔트로피**는 대역 내 PSD 빈의 평탄도(복잡도) 지표입니다. 1/f 배경을 포함해
  계산하므로 수면단계·마취심도 마커로 곧바로 해석하지 마세요.
- **개인정보(PII)**: 도구는 완전 오프라인이며 어떤 데이터도 외부로 보내지 않습니다.
  EDF 헤더의 **환자/기록 식별 필드와 기록 시작 날짜·시각은 읽지 않습니다**. 다만
  **입력 파일 경로(파일명)** 는 리포트·JSON·CSV 프로버넌스에 그대로 기록되므로, 파일명에
  환자 식별정보(이름·MRN 등)를 넣지 마세요. (파일 내용은 출력에 포함되지 않습니다.)
- **디트렌드**: 기본은 세그먼트별 평균 제거(`--detrend constant`, scipy 기본과 동일).
  선형 추세(느린 드리프트, 땀·전극 이동)는 이 모드로는 제거되지 않아 **delta로 새어들 수
  있으므로**, 강한 드리프트가 있으면 **`--detrend linear`**(세그먼트별 최소제곱 직선 제거)를
  쓰거나 사전에 고역통과(예: 0.5 Hz) 필터를 적용하세요. `--detrend none`은 디트렌드를 끕니다.
- **강건 평균**: 일시적 아티팩트(근전위·움직임 스파이크)가 섞인 임상 기록에서는
  **`--average median`**(세그먼트 주기도의 중앙값 + 편향보정)이 평균보다 견고합니다.
- 느린진동 이벤트 카운트는 신뢰할 만한 시간영역 필터가 필요해 **의도적으로 생략**하고,
  대신 에폭 단위 **SWA density**(delta 우세 에폭 비율)로 제공합니다.
- **성능**: 순수 파이썬이라 대용량 기록은 느립니다(대략 50만 표본 ≈ 수 초). 긴 EDF는
  `--start`/`--duration`으로 필요한 구간만 보거나 채널을 골라 분석하세요.

## Tests

```bash
python3 -m pytest -q                      # 506 tests, 전부 오프라인
python3 -m unittest discover -s tests -q  # 같은 스위트를 unittest로도 실행
```

FFT는 O(n²) DFT와 ~1e-9, Welch PSD는 scipy와 ~1e-9로 대조하고, 1/f 지수·Mann–Kendall·
Theil–Sen·Student-t 분위·**Welch t/BH-FDR**은 numpy/scipy와 대조합니다(numpy/scipy가 없으면 해당 교차검증만
skip되고 순수 표준 라이브러리 테스트는 그대로 통과). EDF 리더는 테스트 안에서 **독립
구현된 EDF/BDF 작성기**(`tests/edf_fixtures.py`)가 만든 파일로 검증하므로 리더·작성기가
같은 실수를 공유할 수 없습니다.

## License

MIT © 2026 hyeonjoong
