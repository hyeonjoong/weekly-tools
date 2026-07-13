# agreestat — 측정 방법 일치도(agreement) 분석기

두 측정 방법(A vs B)을 같은 대상에 적용한 **짝지은 CSV**를 넣으면,
**Bland–Altman**(bias·95% 일치한계 LoA·신뢰구간), **ICC(2,1)/ICC(3,1)**,
**Lin's CCC**, **반복성(within-subject CV·repeatability coefficient)**, 그리고
참고용 **Pearson r·대응 차이검정**을 한 번에 계산하고, **논문에 바로 붙일 수 있는
문장**까지 출력합니다. 외부 라이브러리 없이 **표준 라이브러리만**으로 동작합니다.

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

**Who it's for.** 임상·디바이스 연구자, 검증(validation) 논문을 쓰는 사람, "새 측정법이
기존 방법을 대체할 수 있는가?"를 판단해야 하는 누구나.

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

### 옵션

| 옵션 | 의미 |
|------|------|
| `-a`, `--method-a` | 방법 A(측정1) 열 이름 (미지정 시 자동 탐지) |
| `-b`, `--method-b` | 방법 B(측정2/기준) 열 이름 |
| `-s`, `--subject` | 피험자 ID 열 (반복측정 지표 계산) |
| `--name-a`, `--name-b` | 리포트에 표시할 이름 |
| `--percent` | 백분율 Bland–Altman |
| `--alpha 0.05` | 유의수준 / 신뢰구간 폭 (기본 0.05 → 95% CI) |
| `--json` | JSON으로 출력 |

---

## 무엇을, 왜 그렇게 계산하나 (methods)

- **Bland–Altman**: 차이 = A − B. bias는 차이의 평균, LoA = bias ± 1.96·SD.
  bias의 CI는 t분포로, 각 LoA의 CI는 `Var(LoA)=s²[1/n + 1.96²/(2(n−1))]`
  (Bland & Altman 1999)로 계산합니다. 차이를 평균에 회귀시켜 **비례 편향**(기울기≠0)을
  검정하고, 유의하면 경고합니다. `--percent`는 차이를 `100·(A−B)/mean`으로 바꿔
  비례오차 데이터에 대응합니다.
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
- **Pearson r + 대응 t검정**: 참고용입니다. **r은 일치도가 아닙니다** — 계통편향을
  감지하지 못하므로 리포트에서 명시적으로 경고합니다. (대응 t검정 = bias≠0 검정.)

## Notes / limitations

- **상관(r)은 일치도가 아닙니다.** r=0.99여도 한 방법이 다른 방법보다 항상 +5만큼
  크면 두 방법은 바꿔 쓸 수 없습니다. 판단은 Bland–Altman/ICC/CCC로 하세요.
- ICC(2,1)의 CI는 표본이 작거나 계통차가 크면 매우 넓어질 수 있습니다(정상입니다).
- 반복측정이 있으면 각 행을 독립으로 가정한 기본 LoA가 **좁게** 나올 수 있습니다.
  도구가 이를 감지해 경고하며, 필요하면 반복측정용 방법(Bland & Altman 2007)을 쓰세요.
- 한 방법의 분산이 0(모두 같은 값)이면 ICC/CCC/Pearson이 정의되지 않아 해당 값은
  `NaN`(JSON에서는 `null`)으로 나오고 경고합니다.
- 이 도구는 계산을 자동화하지만 최종 판단은 연구자의 몫입니다 — 경고를 꼭 확인하세요.

## Tests

```bash
python3 -m pytest -q    # 61개 테스트, 전부 오프라인
```

ICC는 Shrout & Fleiss(1979) 공개 예제와 1e-9, Bland–Altman·CCC는 손계산 값과 일치하며,
numpy/scipy가 있으면 Pearson·대응 t·F분위수·ICC CI·CCC를 교차검증합니다(없으면 건너뜀).

## License

MIT © 2026 hyeonjoong
