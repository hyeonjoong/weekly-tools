# hrvkit — 심박변이도(HRV) 분석기

스마트워치·PPG·ECG에서 얻은 **RR/IBI 간격(ms)** 또는 **순간 심박수(bpm)** CSV 한 개를
넣으면, 이상박동(ectopic/missed beat)을 자동 보정한 뒤 **시간영역 · 주파수영역 · 비선형**
HRV 지표를 계산해 사람이 읽는 리포트(또는 `--json`)로 출력합니다.
numpy/scipy 없이 **표준 라이브러리만**으로 동작합니다 — FFT(radix-2 Cooley–Tukey),
Welch PSD, 보간, 표본 엔트로피까지 전부 직접 구현했습니다.

---

## 목적 / Why this exists / Who it's for

**한국어.** BELL-001 수면 디바이스의 작용기전은 *느린 호흡 → 부교감신경 활성 ↑ →
호흡성 동성부정맥(RSA)/HRV ↑ → 서파수면 촉진* 입니다. 이 기전이 실제로 일어났는지
확인하려면 착용형 기기에서 나온 박동간격(RR) 시계열로 HRV를 **정량화**해야 합니다.
그런데 실측 RR은 (1) 놓친 박동·조기수축 같은 이상값이 섞여 있고, (2) 불균등 표본이라
주파수 분석 전에 보간이 필요하며, (3) RMSSD·SDNN·LF/HF 등 지표 정의와 정규화가
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
- **시간영역 / Time-domain** — mean RR, mean HR, SDNN, RMSSD, SDSD, pNN50, pNN20, CVNN,
  HR min–max.
- **주파수영역 / Frequency-domain** — VLF/LF/HF **절대(ms²)·정규화(n.u.)·비율(%)** 파워,
  LF/HF, total power, 대역별 피크 주파수. 불균등 RR을 4 Hz 균등 격자로 **선형 리샘플**한
  뒤, **직접 구현한 radix-2 FFT + Welch 방식**(Hann 창, 50 % 겹침, 구간별 주기도 평균)으로
  PSD를 추정합니다. 방법은 리포트에 함께 표기됩니다.
- **비선형 / Nonlinear** — Poincaré SD1, SD2, SD1/SD2, 타원 면적(π·SD1·SD2), 표본
  엔트로피(SampEn, m=2).

---

## 엔지니어링/품질 노트 / Engineering & quality notes

- **표준 라이브러리만.** FFT는 반복형 radix-2 Cooley–Tukey로 직접 구현(2의 거듭제곱
  zero-pad), PSD는 `scipy.signal.welch(scaling='density')`와 **동일한 수식**을 순수
  파이썬으로 재현했습니다. 정규화는 Parseval을 만족(Σ P·df ≈ 신호 분산)해 합성 정현파로
  절대 스케일(ms²)까지 손 검산됩니다.
- **교차검증된 테스트(총 56개, 전부 오프라인).**
  - 직접 구현 FFT ↔ 소박한 O(n²) DFT: 무작위 벡터에서 최대오차 **< 1e-9**.
  - Welch PSD ↔ `scipy.signal.welch`: **rtol 1e-6** 일치 (scipy 있을 때).
  - rfft ↔ `numpy.fft.rfft`, SampEn ↔ numpy 참조 구현: **~1e-9** 일치.
  - RMSSD/SDNN/SDSD/pNN을 손 계산한 5-박동 시리즈로 검증, SD1 = SDSD/√2 항등식 확인.
  - numpy/scipy 참조 테스트는 **가드 처리**되어 있어, 순수 표준 라이브러리 환경에서도
    (해당 3개만 skip) 전부 통과합니다.
- **적대적 입력 방어.** 빈 파일 / 1-박동 / 분산 0(전부 동일) / 2의 거듭제곱이 아닌 길이 /
  ms·s·bpm 단위 자동 감지를 모두 처리합니다.

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

```
[1] 시간영역 / Time-domain
    평균 RR mean RR    : 819.5 ms   (평균 HR 73.3 bpm)
    SDNN               : 23.47 ms
    RMSSD              : 18.77 ms
    pNN50 / pNN20      : 0.0% / 33.8%
[2] 주파수영역 / Frequency-domain
    방법 method        : 4 Hz 선형 리샘플 → Welch PSD (Hann, nperseg=256, 50% overlap, radix-2 FFT, 6 segments)
    LF  power          : 228.8 ms²  (54.1%,  66.0 n.u.)
    HF  power          : 117.8 ms²  (27.8%,  34.0 n.u.)
    LF/HF ratio        : 1.94
[3] 비선형 / Nonlinear (Poincaré + SampEn)
    SD1 : 13.30 ms   SD2 : 30.47 ms   SD1/SD2 : 0.436   SampEn : 1.810
```

### 2) 시간+값(time+value) 형식 — 값 열 자동/수동 선택

```bash
hrvkit examples/slow_breathing.csv          # time_s,rr_ms → rr_ms 열 자동 선택
hrvkit examples/slow_breathing.csv --col rr_ms --json
```

느린 호흡 기록은 안정 기록보다 **RMSSD·HF·SD1 ↑, LF/HF ↓** 로 나와,
*느린 호흡 → 부교감 활성 ↑ → RSA/HRV ↑* 라는 BELL-001 기전과 일치합니다:

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
| `--clean interpolate\|remove\|none` | 이상박동 보정 방법 (기본 interpolate) |
| `--min-rr`, `--max-rr` | 생리적 범위(ms) (기본 300 / 2000) |
| `--rel-thresh 0.2` | 국소 중앙값 대비 급변 임계값 |
| `--fs 4.0` | 주파수영역 리샘플 주파수(Hz) |
| `--no-sampen` | 표본 엔트로피 생략 |
| `--json` | JSON 출력 |

---

## Notes / limitations

- **VLF는 짧은 기록에서 신뢰도가 낮습니다.** VLF(0.003–0.04 Hz)는 최소 수 분 이상의
  기록이 있어야 의미가 있으며, 예제처럼 ~4–5분 기록에서는 참고용입니다.
- **정규화 단위(n.u.)와 LF/HF는 상대 지표입니다.** 절대 파워(ms²)와 함께 해석하세요.
- **주파수 방법을 반드시 명시.** 리샘플 주파수·창·구간 수가 값에 영향을 주므로 리포트에
  같이 출력합니다(재현성). 다른 도구와 비교할 땐 동일 설정인지 확인하세요.
- 이 도구는 지표를 **계산·요약**할 뿐, 임상적 판단을 대신하지 않습니다.

---

## Tests

```bash
python3 -m pytest -q      # 56 tests, 전부 오프라인. numpy/scipy 있으면 교차검증,
                          # 없으면 해당 3개만 skip 하고 나머지 전부 통과.
```

## License

MIT © 2026 hyeonjoong
