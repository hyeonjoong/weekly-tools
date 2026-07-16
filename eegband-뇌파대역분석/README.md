# eegband — 단일채널 EEG 대역파워 분석기

단일채널 EEG 시계열 CSV(값 열, 선택적으로 시간 열)와 표본화율(`--fs`)을 주면
**Welch PSD**로 표준 대역(delta/theta/alpha/beta/gamma)의 **절대·상대 파워**,
**슬로우파 활동(SWA = delta)**, **스펙트럼 에지 주파수(SEF95)**, **피크 주파수**,
**대역별 피크(알파 피크 = IAF)**, **스펙트럼 엔트로피**, **총 파워**, **대역비**,
그리고 **신호 품질/아티팩트 지표**(클리핑·평탄·보간 비율, RMS)를 계산합니다.
필요하면 신호를 **에폭(epoch)** 으로 잘라 에폭별 + 요약(mean±SD, SEM, 95% CI,
중앙값·IQR) 리포트를 냅니다. 세그먼트 **디트렌드**(constant/linear/none)와 **평균**
(mean/median, 아티팩트에 강건)도 선택할 수 있습니다. **FFT(radix-2 Cooley–Tukey)까지
전부 표준 라이브러리로 자체 구현** — numpy/scipy 없이 어디서나 돕니다.

---

## 목적 / Why this exists

**한국어.** 수면·뇌파 연구에서 "이 구간이 얼마나 느린파(느린 진동)가 우세한가?"는
반복적으로 계산해야 하는 핵심 지표입니다. 특히 **BELL-001**처럼 슬로우파 수면(SWS)을
1차 종말점으로 삼는 경우, delta(0.5–4 Hz) 대역파워 = **SWA**를 정확한 PSD 정규화로
구해야 합니다. 손으로 하려면 (1) 창(window) 적용, (2) 세그먼트 평균(Welch), (3)
단측 스펙트럼의 µV²/Hz 정규화, (4) 대역 적분, (5) SEF/피크/비율 계산을 매번 맞춰야
하고 실수가 잦습니다. `eegband`는 이 전 과정을 한 번에 처리하고, 알파(각성) vs
델타(깊은수면) 우세가 어떻게 뒤집히는지 바로 보여줍니다. 환자 데이터는 로컬에서만
처리되며 외부 전송이 없습니다.

**English.** In sleep/EEG work you repeatedly need "how slow-wave-dominant is this
segment?" For endpoints like **BELL-001** (slow-wave sleep), the delta (0.5–4 Hz)
band power — the **SWA** — must be computed with a correctly normalized PSD. Done by
hand that means getting the window, Welch segment averaging, one-sided µV²/Hz
scaling, band integration, and the SEF/peak/ratio math right every time. `eegband`
runs the whole pipeline and makes the alpha-vs-delta dominance flip obvious. Every
number — including the FFT — is computed from first principles in the standard
library, so it runs anywhere Python 3.9+ is installed with **zero dependencies**.

품질/검증: 자체 FFT는 순수 파이썬 O(n²) DFT(및 numpy.fft)와 **~1e-9**까지 일치하고,
Welch PSD는 `scipy.signal.welch`(density)와 **~1e-9**까지 일치합니다. 알려진 진폭·
주파수의 정현파를 넣으면 총 파워가 **Parseval(분산 = A²/2)** 과 일치하고 해당 대역에
정확히 떨어집니다 — 모두 테스트로 고정되어 있습니다.

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
    amplitude: min = -81.510  max = 76.997  ptp = 158.506  mean = -0.000  RMS = 33.673 µV
    interpolated = 0/5120 (0.0%),  clipped(rail) = 0 (0.0%),  flat-run = 0 (0.0%)

[1] 대역파워 / Band power  (absolute µV², relative %, prominent in-band peak Hz)
    band    range(Hz)         abs(µV²)    rel(%)  peak(Hz)
    delta   0.5–4             1072.564      98.7      1.50  ← SWA
    theta   4–8                  5.030       0.5       n/a
    alpha   8–13                 6.539       0.6     11.00
    beta    13–30                1.774       0.2       n/a
    gamma   30–45                0.628       0.1       n/a
    total   0.5–45            1086.536     100.0

[2] 슬로우파 활동 / Slow-wave activity (SWA = delta 0.5–4 Hz) — key sleep endpoint
    SWA absolute  = 1072.564 µV²   SWA relative = 98.7 %
    dominant band = delta   ← slow-wave/delta dominant

[3] 스펙트럼 요약 / Spectral summary
    peak frequency = 1.50 Hz   SEF95 = 1.89 Hz
    spectral entropy (norm) = 0.313   alpha peak (IAF) = 11.00 Hz
    total power (0.5–45) = 1086.536 µV²
```

`peak(Hz)` 는 **뚜렷한(prominent) 대역 내 피크**만 보고합니다 — 1/f 기울기 위 잡음의
argmax는 `n/a`로 억제됩니다(위 delta 예제에서 이 데이터에 실재하는 11 Hz 스핀들만 알파 피크로 표시).

### 2) 값 열 + 시간 열 (fs 자동 추정·교차검증)

```bash
eegband examples/alpha_wake.csv --time time_s --value eeg_uv
```

시간 열이 있으면 fs를 추정하고, `--fs`와 1% 넘게 다르면 경고 후 **추정값**을 씁니다.
이 각성 트레이스는 **alpha 우세**(≈88%, 피크 10 Hz)로 나와, 위 델타 예제와 **뒤집힙니다**.

### 3) 에폭별 분석

```bash
eegband examples/delta_deep_sleep.csv --fs 128 --epoch 20
```

```
[5] 에폭별 / Per-epoch  (epoch = 20 s, n_epochs = 2)
     ep   t0(s)   t1(s)   delta   theta   alpha    beta   gamma    peak     SEF  dominant
      0     0.0    20.0   98.6%    0.5%    0.6%    0.2%    0.1%    1.50    1.90  delta
      1    20.0    40.0   98.8%    0.4%    0.6%    0.1%    0.1%    1.50    1.90  delta
    SWA density (delta-dominant epochs) = 2/2  (100 %)
    relative delta across epochs = 98.7 ± 0.2 % (SD, n-1),  SEM 0.1 %,  95% CI [97.3, 100.1] %  (n=2)
      median 98.7 %, IQR [98.7, 98.8] %, range [98.6, 98.8] %
    SWA absolute across epochs   = 1068.946 ± 132.626 µV² (SD, n-1),  SEM 93.781 µV²,  95% CI [-122.637, 2260.529] µV²
      median 1068.946, IQR [1022.056, 1115.837], range [975.165, 1162.727] µV²
      (에폭은 자기상관 — 기록 내 분포이며 피험자간 추론 CI 아님 / within-recording spread, not a between-subject CI)
```

### 4) 아티팩트 에폭 제거 (SWA 종말점 보호)

```bash
eegband examples/delta_deep_sleep.csv --fs 128 --epoch 20 --max-amp 150
```

`--max-amp T` 를 주면 최대 |진폭|이 `T` µV를 넘는 에폭을 **아티팩트로 표시(`✗REJ`)하고
SWA 요약 통계(mean/SD/CI/density)에서 제외**합니다. 움직임·근전위 스파이크가 섞인 한 에폭이
평균 SWA를 부풀리는 것을 막아 1차 종말점을 보호합니다. 에폭 표에는 각 에폭의 `|amp|` 와
제외 여부가 함께 표시되고, `kept X/Y` 요약이 나옵니다. (신호 품질 [0] 지표를 실제 결과에
반영하는 경로입니다.)

### 5) 통계 SW로 넘기기 (CSV 내보내기)

```bash
eegband examples/delta_deep_sleep.csv --fs 128 --epoch 20 --csv > epochs.csv
# base-R read.csv / SAS PROC IMPORT 용 깔끔한 사각형(주석 없음):
eegband examples/delta_deep_sleep.csv --fs 128 --epoch 20 --csv --no-comment > epochs.csv
```

에폭이 있으면 에폭당 한 행, 없으면 전체 한 행으로 대역별 `abs/rel/peak`, 총파워, 피크, SEF,
**스펙트럼 엔트로피**, **대역비 3종**(theta_alpha/delta_beta/slowing), 우세대역을 CSV로 냅니다.
`--max-amp` 를 쓰면 `peak_amp_uv`·`rejected` 열이 추가됩니다. 맨 앞 `#` 주석 행에 전체 분석
파라미터를 담아 자기재현이 가능하며, `--no-comment` 로 이 행을 끄면 base-R/SAS가 옵션 없이
바로 읽는 깔끔한 사각형이 됩니다. R/SAS/Prism에서 1차 종말점 통계를 돌리기 좋습니다.

### 옵션

| 옵션 | 의미 |
|------|------|
| `--fs 128` | 표본화율 Hz (기본 128). 시간 열이 있으면 추정값 우선 |
| `--value NAME` | 값(µV) 열 이름 (미지정 시 자동 감지) |
| `--time NAME` | 시간(초) 열 이름 (fs 추정·교차검증) |
| `--epoch 30` | 에폭 길이(초) → 에폭별 + 요약 |
| `--max-amp 150` | 아티팩트 제거: 최대 |진폭|이 150 µV를 넘는 에폭을 SWA 요약에서 제외 (표에는 `✗REJ` 표시) |
| `--nperseg N` | Welch 세그먼트 길이(표본). 기본 ~4초, 신호 길이로 상한 |
| `--noverlap N` | 세그먼트 겹침(표본). 기본 nperseg//2 (50%) |
| `--detrend {constant,linear,none}` | 세그먼트 디트렌드. `linear`는 느린 드리프트를 제거해 delta 누설을 막음 (기본 constant) |
| `--average {mean,median}` | Welch 세그먼트 평균. `median`은 일시적 아티팩트에 강건(편향보정 포함, 기본 mean) |
| `--bands ...` | 대역 재정의, 예: `delta:0.5-4,theta:4-8,alpha:8-13,beta:13-30,gamma:30-45` |
| `--sef 95` | 스펙트럼 에지 주파수 백분위 (기본 95 → SEF95) |
| `--json` | 사람용 리포트 대신 JSON 출력 |
| `--csv` | 에폭별(없으면 전체) 대역파워 표를 CSV로 출력 (R/SAS/Prism 용) |
| `--no-comment` | `--csv` 출력에서 맨 앞 `#` 주석 행 생략 (base-R `read.csv`/SAS `PROC IMPORT` 호환) |

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
- **SEF95 / 피크 / 스펙트럼 엔트로피 / 총파워 / 대역비**: 누적 파워가 95%가 되는 주파수
  (구간 내 PSD가 선형이므로 누적은 2차식 → 근을 **정확히** 풀어 sub-bin 오차 없음),
  분석대역 내 최대 PSD 주파수, 대역 내 PSD 빈(총 빈 수로 정규화)을 확률분포로 본 **정규 섀넌
  엔트로피**(1=백색/평탄, 0=단일 리듬 — 스펙트럼 평탄도/복잡도 지표이며 수면단계 마커는 아님),
  대역 총적분, `theta/alpha`·`delta/beta`·느림지수 `(delta+theta)/(alpha+beta)`.
- **신호 품질**: 진폭 min/max/ptp/mean/RMS와 함께 **클리핑(레일 고정)**, **평탄 구간(연속
  동일값 ≥3)**, **보간 비율**을 계산해 흔한 기록 문제(ADC 포화·리드 탈락·과도한 결측)를
  표면화합니다. 분석 자체는 절대 바꾸지 않는 순수 기술 지표입니다.
- **에폭 + SWA density + 아티팩트 제거**: `--epoch`로 비겹침 에폭 분할, 에폭별 지표 + delta
  우세 에폭 비율(SWA density), 그리고 종말점(상대 delta·절대 SWA)의 **mean±SD, SEM, t-기반
  95% CI, 중앙값·IQR·범위**를 요약합니다. `--max-amp T`를 주면 최대 |진폭|이 `T` µV를 넘는
  에폭을 제외하고 요약합니다(품질 지표를 결과에 반영).

## Notes / limitations

- PSD는 `scipy.signal.welch(scaling='density')`와 동일한 관례(주기형 Hann,
  detrend='constant', 단측 ×2)를 따릅니다. 대역파워는 정현파에서 Parseval(분산=A²/2)과
  일치하도록 정규화되어 손 검산이 가능합니다.
- **주파수 분해능**은 `df = fs/nfft`로 정해집니다. 사용자가 `--fs 128`을 직접 주면 기본
  4초 세그먼트에서 nfft=512, df=0.25 Hz가 되어 모든 표준 대역 경계(0.5/4/8/13/30/45)가
  정확히 빈에 떨어집니다. 시간 열에서 fs를 추정한 경우(예: alpha 예제의 ≈127.9999 Hz)에는
  df가 미세하게 달라 경계가 빈에서 ~1e-8 만큼 벗어날 수 있으나, 경계는 선형 보간으로
  적분하므로 결과에 영향이 없습니다. (참고: 경계가 빈에 정렬된다는 것이 0.5 Hz 성분을
  분리 해상한다는 뜻은 아닙니다 — 주기형 Hann 주엽 폭은 ≈4·df ≈ 1 Hz 입니다.)
- 비유한(NaN/inf)·빈 셀은 **선형 보간**으로 메우고 그 개수를 리포트합니다. 상수(분산 0)
  신호는 총파워 0, 상대파워 0, 피크·SEF·우세대역은 `n/a`로 안전하게 처리합니다.
- 시간 열이 **불규칙**하면 경고합니다(Welch는 균등 표본을 가정). fs가 `--fs`와 다르면
  경고 후 추정값을 사용합니다. 시간 열은 **초(second)** 단위로 가정하며, 열 이름이
  `time_ms`/`ms` 이면 밀리초로 보고 자동 변환합니다. `sample`/`index` 같은 **표본 카운터**는
  시간 열로 자동 인식하지 않습니다(초가 아니므로 fs를 오염시킴). 필요하면 `--time`으로 명시하세요.
- **인코딩**: UTF-8이 아니면 cp949(한국 Excel)·latin-1 순으로 자동 시도하고 어떤 인코딩으로
  읽었는지 경고합니다.
- **커스텀 `--bands`** 가 서로 겹치거나 빈 구간을 남기면 상대파워 합이 100%가 아니게 되며,
  총(total) 행에 실제 합계를 표시하고 경고합니다(기본 대역은 항상 100%).
- **우세 대역**이 근소차(상위 두 대역이 1% 이내)면 `⚠ near-tie`로 표시합니다.
- **대역 피크(IAF 포함)**는 **뚜렷한 피크**일 때만 보고합니다: 대역 내부의 국소 최대이면서
  양쪽 경계보다 크고 대역 중앙값의 3배 이상일 때. 1/f 기울기 위 잡음의 argmax는 `n/a`로
  억제되어, 실재하지 않는 알파 리듬을 IAF로 오보하지 않습니다.
- **에폭 요약의 95% CI**는 한 기록 내 에폭들의 분포입니다. 에폭은 서로 **자기상관**이 크므로
  이 CI는 **피험자간(집단) 추론 CI가 아니며**, 실제 불확실성을 과소평가합니다(리포트에도 명시).
  피험자간 추론은 기록/피험자 단위 요약값으로 별도 분석하세요.
- **스펙트럼 엔트로피**는 대역 내 PSD 빈의 평탄도(복잡도) 지표입니다. 1/f 배경을 포함해
  계산하므로 수면단계·마취심도 마커로 곧바로 해석하지 마세요.
- **개인정보(PII)**: 도구는 완전 오프라인이며 어떤 데이터도 외부로 보내지 않습니다. 다만
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

## Tests

```bash
python3 -m pytest -q                      # 145 tests, 전부 오프라인
python3 -m unittest discover -s tests -q  # 같은 스위트를 unittest로도 실행
```

FFT는 O(n²) DFT와 ~1e-9, Welch PSD는 scipy와 ~1e-9로 대조합니다(numpy/scipy가 없으면
해당 교차검증만 skip되고 순수 표준 라이브러리 테스트는 그대로 통과).

## License

MIT © 2026 hyeonjoong
