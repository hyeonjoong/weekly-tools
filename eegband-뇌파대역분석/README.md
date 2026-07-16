# eegband — 단일채널 EEG 대역파워 분석기

단일채널 EEG 시계열 CSV(값 열, 선택적으로 시간 열)와 표본화율(`--fs`)을 주면
**Welch PSD**로 표준 대역(delta/theta/alpha/beta/gamma)의 **절대·상대 파워**,
**슬로우파 활동(SWA = delta)**, **스펙트럼 에지 주파수(SEF95)**, **피크 주파수**,
**총 파워**, **대역비**를 계산합니다. 필요하면 신호를 **에폭(epoch)** 으로 잘라
에폭별 + 요약 리포트를 냅니다. **FFT(radix-2 Cooley–Tukey)까지 전부 표준
라이브러리로 자체 구현** — numpy/scipy 없이 어디서나 돕니다.

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
[1] 대역파워 / Band power  (absolute µV², relative %)
    band    range(Hz)         abs(µV²)    rel(%)
    delta   0.5–4             1072.564      98.7  ← SWA
    theta   4–8                  5.030       0.5
    alpha   8–13                 6.539       0.6
    beta    13–30                1.774       0.2
    gamma   30–45                0.628       0.1
    total   0.5–45            1086.536     100.0

[2] 슬로우파 활동 / Slow-wave activity (SWA = delta 0.5–4 Hz) — key sleep endpoint
    SWA absolute  = 1072.564 µV²   SWA relative = 98.7 %
    dominant band = delta   ← slow-wave/delta dominant

[3] 스펙트럼 요약 / Spectral summary
    peak frequency = 1.50 Hz   SEF95 = 1.89 Hz   total power (0.5–45) = 1086.536 µV²
```

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
    relative delta across epochs = 98.7 ± 0.2 % (SD, n-1),  SEM 0.1 %  (n=2)
    SWA absolute across epochs   = 1068.946 ± 132.626 µV² (SD, n-1),  SEM 93.781 µV²
```

### 4) 통계 SW로 넘기기 (CSV 내보내기)

```bash
eegband examples/delta_deep_sleep.csv --fs 128 --epoch 20 --csv > epochs.csv
```

에폭이 있으면 에폭당 한 행, 없으면 전체 한 행으로 대역별 `abs/rel`, 총파워, 피크, SEF,
우세대역을 CSV로 냅니다(맨 앞 `#` 주석 행에 버전·fs·입력파일). R/SAS/Prism에서 바로 읽어
1차 종말점 통계를 돌리기 좋습니다.

### 옵션

| 옵션 | 의미 |
|------|------|
| `--fs 128` | 표본화율 Hz (기본 128). 시간 열이 있으면 추정값 우선 |
| `--value NAME` | 값(µV) 열 이름 (미지정 시 자동 감지) |
| `--time NAME` | 시간(초) 열 이름 (fs 추정·교차검증) |
| `--epoch 30` | 에폭 길이(초) → 에폭별 + 요약 |
| `--nperseg N` | Welch 세그먼트 길이(표본). 기본 ~4초, 신호 길이로 상한 |
| `--noverlap N` | 세그먼트 겹침(표본). 기본 nperseg//2 (50%) |
| `--bands ...` | 대역 재정의, 예: `delta:0.5-4,theta:4-8,alpha:8-13,beta:13-30,gamma:30-45` |
| `--sef 95` | 스펙트럼 에지 주파수 백분위 (기본 95 → SEF95) |
| `--json` | 사람용 리포트 대신 JSON 출력 |
| `--csv` | 에폭별(없으면 전체) 대역파워 표를 CSV로 출력 (R/SAS/Prism 용) |

---

## 어떻게 계산하나 (How it's computed)

- **Welch PSD**: 신호를 길이 `nperseg` 세그먼트로 자르고(기본 50% 겹침), 세그먼트마다
  평균 제거(detrend) 후 **주기형 Hann 창**을 곱하고, 다음 2의 거듭제곱까지 0-패딩하여
  **자체 FFT**로 변환합니다. 주기도를 `|X|² / (fs·Σw²)` 로 정규화하고, 단측화(DC·
  Nyquist 제외 ×2), 세그먼트 평균 → **µV²/Hz**. 입력이 µV라고 가정합니다.
- **대역파워**: PSD를 각 대역 `[lo, hi]`에서 사다리꼴 적분(경계는 선형 보간). 상대파워 =
  대역/총파워(0.5–45 Hz).
- **SWA**: delta(0.5–4 Hz) 절대 파워를 별도 강조 — BELL-001의 슬로우파 종말점.
- **SEF95 / 피크 / 총파워 / 대역비**: 누적 파워가 95%가 되는 주파수(구간 내 PSD가
  선형이므로 누적은 2차식 → 근을 **정확히** 풀어 sub-bin 오차 없음), 분석대역 내
  최대 PSD 주파수, 대역 총적분, `theta/alpha`·`delta/beta`·느림지수
  `(delta+theta)/(alpha+beta)`.
- **에폭 + SWA density**: `--epoch`로 비겹침 에폭 분할, 에폭별 지표 + delta 우세 에폭
  비율(SWA density)을 요약합니다.

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
- **디트렌드는 세그먼트별 평균 제거(`detrend='constant'`)** 로 scipy 기본과 동일합니다.
  선형 추세(느린 드리프트, 땀·전극 이동)는 제거되지 않아 **delta로 새어들 수 있으므로**,
  강한 드리프트가 있으면 사전에 고역통과(예: 0.5 Hz) 필터를 권장합니다.
- 느린진동 이벤트 카운트는 신뢰할 만한 시간영역 필터가 필요해 **의도적으로 생략**하고,
  대신 에폭 단위 **SWA density**(delta 우세 에폭 비율)로 제공합니다.

## Tests

```bash
python3 -m pytest -q                      # 82 tests, 전부 오프라인
python3 -m unittest discover -s tests -q  # 같은 스위트를 unittest로도 실행
```

FFT는 O(n²) DFT와 ~1e-9, Welch PSD는 scipy와 ~1e-9로 대조합니다(numpy/scipy가 없으면
해당 교차검증만 skip되고 순수 표준 라이브러리 테스트는 그대로 통과).

## License

MIT © 2026 hyeonjoong
