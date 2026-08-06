# medpath — 매개효과(간접효과) 분석기

**"중재군이 더 좋았다"에서 한 걸음 더 — *무엇을 거쳐서* 달라졌는가.**
(인과로 읽으려면 별도의 가정이 필요합니다 — 아래 "할 수 없는 것" 참고.)
X(독립·노출) · M(매개) · Y(결과) 열이 있는 **CSV를 넣으면**, 간접효과 `a×b`를
**부트스트랩 신뢰구간**과 함께 계산하고, 경로별 회귀표·진단·**논문에 바로 붙일
문장(한/영)**까지 출력합니다. 외부 라이브러리 없이 **표준 라이브러리만**으로 돕니다.

```bash
python3 -m medpath examples/sleep_breathing_hrv.csv \
    --x arm --m rmssd_ms --y sws_min --covariates age
```

```
── 효과 요약 ─────────────────────────────────────────────────────────
  효과                                        추정치   SE        95% CI       판정
  ------------------------------------------  ------  -----  ---------------  --------
  총효과 c (arm → sws_min)                     4.155  1.813   [0.564, 7.746]  p = .024
  직접효과 c' (arm → sws_min, 매개변수 통제)   1.193  1.774  [-2.321, 4.707]  p = .502
  간접효과 arm → rmssd_ms → sws_min            2.961  1.044   [1.170, 5.279]  0 미포함

  · 매개비율(간접/총) = 71.3%
```

---

## 목적 / Why this exists

**한국어.** 임상·제약 연구에서 "중재군이 대조군보다 좋았다"는 1차 결과만으로는
심사자를 설득하기 어렵습니다. 이어지는 질문은 항상 **"작용 기전이 뭔가"** 이고,
그 답을 자료로 말하는 표준 도구가 매개분석(mediation analysis)입니다.
그런데 실무에서 이걸 제대로 하려면 지금까지는 SPSS + PROCESS 매크로나 R을
설치해야 했고, 그 과정에서 다음 실수들이 반복됩니다.

- **Sobel 검정으로 간접효과를 검정합니다.** 곱 `a×b`의 표집분포는 정규가 아니라
  치우쳐 있어서 Sobel z는 검정력이 낮습니다. medpath는 **부트스트랩 구간을
  기본**으로 쓰고 Sobel z는 "참고용"으로만 병기합니다.
- **"완전매개 / 부분매개"로 결론을 씁니다.** 이 표현은 직접효과의 유의성에
  의존하는데 그건 대체로 검정력 문제입니다. medpath는 이 표현을 쓰지 않고
  경고문으로 안내합니다.
- **매개비율(%)을 무비판적으로 보고합니다.** 총효과의 구간이 0을 포함하거나
  간접·직접 부호가 반대면 매개비율은 138%처럼 나오거나 아예 무의미해집니다.
  medpath는 이 세 경우(총효과 CI가 0 포함 / 부호 반대 / 100% 초과)에
  **계산을 거부하거나 명시적으로 경고**합니다.
- **N수가 슬그머니 달라집니다.** 세 회귀식(M모형·Y모형·총효과모형)에서 결측
  처리가 다르면 `c = c' + Σab` 항등식이 깨집니다. medpath는 **세 식 모두
  동일한 행 집합**을 쓰고, 그 항등식을 테스트로 검증합니다.
- **임상 CSV는 지저분합니다.** BOM·CP949(한글 엑셀)·세미콜론 구분자·`N/A`·
  `미측정`·`1,024.0`(천단위 쉼표)·유럽식 소수점 `1,5`를 그대로 읽고,
  **무엇을 몇 건 버렸는지 보고서 첫머리에 밝힙니다.**

**English.** `medpath` takes a CSV with an independent variable (X), one or more
mediators (M), and an outcome (Y), and reports simple / parallel / serial
multiple mediation with covariate adjustment: OLS path coefficients, specific
indirect effects with case-resampling bootstrap intervals (percentile / BC /
BCa), contrasts between indirect paths, proportion mediated, collinearity and
heteroscedasticity diagnostics, and a ready-to-adapt results paragraph in Korean
and English. It is deliberately blunt about the things this literature most
often overclaims: an indirect effect is **not** causal evidence, "full vs.
partial mediation" is not a conclusion, and the proportion mediated is unstable.

**누구에게 쓸모 있나 / Who it's for.** 중재연구·관찰연구의 기전을 보고해야 하는
임상·제약·의료기기 연구자, PROCESS 결과를 재현·검산하려는 통계 담당자,
SPSS 라이선스 없이 매개분석이 필요한 대학원생.

---

## 할 수 있는 것 (정확히)

| 기능 | 지원 | 비고 |
|---|---|---|
| 단순매개 X → M → Y | ✅ | PROCESS model 4와 같은 모형 설정(대조 검증은 안 함 — 아래 주 참고) |
| 병렬 다중매개 (M 여러 개 동시) | ✅ | PROCESS model 4 (매개 ≥2)와 같은 설정 |
| 직렬(연쇄) 매개 X→M1→M2→Y | ✅ | `--serial`, PROCESS model 6과 같은 설정. 매개 2~3개 권장 |
| 공변량 보정 | ✅ | 연속형 그대로, 범주형은 자동 가변수화 |
| X가 이분형 범주(문자열) | ✅ | 기준군 자동 추정 + `--reference` 로 지정 |
| X가 3수준 이상 범주 | ⚠️ 부분 | `--x-levels 기준,비교` 로 **두 수준만** 골라 비교 |
| 간접효과 신뢰구간 | ✅ | 케이스 부트스트랩 — 백분위(기본) / BC / BCa |
| 경로 간 대비(어느 경로가 큰가) | ✅ | 같은 부트스트랩 표본에서 차이의 구간 |
| Sobel / 델타법 z | ✅ (참고용) | 정규 가정이므로 주 근거로 쓰지 않음 |
| 표준화 효과 | ✅ | X 이분형이면 부분표준화, 연속형이면 완전표준화 |
| 진단 (VIF, Breusch–Pagan, Cook's D) | ✅ | `--no-diagnostics` 로 생략 가능 |
| 강건 표준오차 | ✅ | `--robust hc3` (경로계수 SE에만 적용) |
| 출력 형식 | ✅ | 표 / Markdown / JSON / 파일 저장 |
| 재현성 | ✅ | `--seed` 고정, `--jobs` 를 바꿔도 **결과 동일** |

## 할 수 **없는** 것 (한계 — 먼저 읽어 주세요)

- **인과추론이 아닙니다.** 이 도구는 회귀 계수의 곱을 계산합니다. 그 숫자가
  "기전"을 뜻하려면 X→M→Y의 **시간적 선후**가 실제로 보장되고 **M–Y 사이에
  측정되지 않은 교란변수가 없어야** 합니다. 횡단자료면 "가설과 일치한다" 정도로만
  쓰세요. 보고서에도 이 경고가 항상 함께 출력됩니다.
- **조절매개(moderated mediation) 없음.** PROCESS model 7/8/14/59 류의 상호작용
  모형, 조건부 간접효과(index of moderated mediation)는 지원하지 않습니다.
- **이분형 Y에 대한 로지스틱 매개분석 없음.** Y가 0/1이어도 **선형확률모형**으로
  적합되며(경고 출력), 로지스틱·프로빗 기반이나 반사실적(counterfactual) 인과매개
  분석(자연직접/간접효과)은 없습니다.
- **결측은 목록별 제거(listwise)만.** 다중대체(MI)·FIML 없음. 몇 행이 왜 빠졌는지는
  보고하지만, 결측이 무작위가 아니면 편향됩니다.
- **다층·반복측정·잠재변수 모형 없음.** 군집(클리닉/병원) 자료, 개인 내 반복측정,
  측정오차를 반영한 SEM은 다루지 않습니다. 관측 변수 OLS 경로모형입니다.
- **X×M 상호작용을 가정에서 배제합니다.** Y 모형은 `Y ~ X + M + 공변량`으로만
  적합되며 노출–매개 상호작용 항이 없습니다. 실제로 상호작용이 있으면 `a×b`
  분해 자체가 편향되고, 그 사실은 이 도구로는 확인할 수 없습니다.
- **다중비교 보정이 없습니다.** 경로가 여러 개면 모든 간접효과와 모든 쌍별
  대비가 각각 명목 95% 수준에서 검정됩니다(직렬 매개 3개 → 경로 7개 + 대비 21개).
  보고서에 기대 오탐 수를 함께 표시하지만, 보정은 하지 않습니다.
- **미측정 교란에 대한 민감도분석이 없습니다.** E-value나 ρ-민감도 같은
  "교란이 얼마나 있어야 결론이 뒤집히는가" 계산은 제공하지 않습니다.
- **생존시간(Cox) Y 없음.**
- **그림을 그리지 않습니다.** 숫자와 문장만 출력합니다(외부 의존성 없음의 대가).
- **동봉 예제는 전부 합성 데이터**이며 실제 환자 정보가 아닙니다.

> PROCESS와 비교: 모형 정의·표준화·대비 방식은 Hayes(2022)의 관례를 따랐으므로
> **점추정치(a, b, c, c', ab)는 같은 자료·같은 모형이면 일치해야 합니다.** 다만
> 부트스트랩 구간은 난수에 의존하므로 소수점 아래에서 달라집니다. 저자가 PROCESS
> 출력과 대조 검증한 것은 아니니, 재현이 중요하면 직접 대조해 보세요.

---

## 설치 없이 실행하기

Python 3.9 이상만 있으면 됩니다(macOS·최신 Linux는 기본 포함).

```bash
cd medpath-매개효과분석
python3 -m medpath examples/sleep_breathing_hrv.csv --x arm --m rmssd_ms --y sws_min
```

macOS라면 **`실행.command` 를 더블클릭**하면 동봉 예제로 전체 기능이 한 번에
돕니다. (경고가 뜨면 우클릭 → 열기)

패키지로 설치하고 싶다면(선택):

```bash
pip install -e .      # 이후 `medpath ...` 로도 실행 가능
```

---

## 자주 쓰는 명령

```bash
# 0) 열 이름이 기억나지 않을 때
python3 -m medpath 내파일.csv --list-columns

# 1) 단순매개 + 공변량 보정
python3 -m medpath 내파일.csv --x 군 --m 순응도 --y 증상점수 --covariates 나이,성별

# 2) 병렬 다중매개 (매개 2개 동시) + 경로 간 대비
python3 -m medpath 내파일.csv --x 군 --m 순응도,자기효능감 --y 증상점수

# 3) 직렬(연쇄) 매개 — 지정한 순서대로 X→M1→M2→Y
python3 -m medpath 내파일.csv --x 군 --m 순응도,자기효능감 --y 증상점수 --serial

# 4) 기준군을 직접 지정 (계수 부호가 반대로 나올 때)
python3 -m medpath 내파일.csv --x 군 --m ... --y ... --reference 대조군

# 5) 논문 보고용 — BCa 구간 + 마크다운 표 + 파일 저장
python3 -m medpath 내파일.csv --x 군 --m ... --y ... \
    --ci bca --bootstrap 10000 --markdown --out 결과.md

# 6) 다른 도구로 넘기기
python3 -m medpath 내파일.csv --x 군 --m ... --y ... --json --out 결과.json
```

전체 옵션은 `python3 -m medpath --help`, 단계별 안내는 [`사용법.md`](사용법.md)를 보세요.

---

## 동봉 예제

| 파일 | 내용 | 쓰임 |
|---|---|---|
| `examples/sleep_breathing_hrv.csv` | 호흡·수면 중재 RCT 형태 (n=120): `arm` → `rmssd_ms` → `sws_min` → `isi_change` | 단순·병렬·직렬 매개 전부 |
| `examples/wowfit_training.csv` | 훈련량 관찰연구 (n=150): `weekly_sessions` → `adherence_pct`/`self_efficacy` → `speech_score_change` | 연속형 X, 공변량 보정 |

두 파일 모두 **알려진 인과 구조에서 고정 시드로 생성한 합성 데이터**입니다.
결측·비숫자 토큰이 일부러 섞여 있어 전처리 보고 기능을 볼 수 있습니다.
재생성: `python3 examples/make_examples.py`

---

## 결과를 어떻게 읽나

- **간접효과 `ab`의 신뢰구간이 0을 포함하지 않으면** 그 경로를 통한 매개가
  자료와 일치합니다. `a`나 `b` 각각의 p값이 아니라 **곱의 구간**이 판단 기준입니다.
- **총효과 c가 유의하지 않아도** 간접효과는 유의할 수 있습니다(억제효과·경로 상쇄).
  "c가 유의해야 매개를 볼 수 있다"는 Baron & Kenny식 관문은 더 이상 요구되지 않습니다.
- **직접효과 c'가 유의하지 않다고 "완전매개"라고 쓰지 마세요.** 검정력 문제입니다.
- **매개비율**은 총효과가 작으면 폭발합니다. 100%를 넘거나 부호가 반대면
  medpath가 경고하거나 계산하지 않습니다.
- **Cook's D 경고가 뜨면** 그 관측치를 빼고 다시 돌려 결론이 유지되는지 확인하세요.

---

## 검증 / 신뢰성

- `python3 -m pytest` — **242개 테스트**가 표준 라이브러리만으로 통과하고,
  scipy·statsmodels가 설치돼 있으면 교차검증 테스트를 포함해 **269개**가 돕니다
  (수 초). 두 패키지는 테스트 전용이며 도구 실행에는 필요 없습니다.
  - `c = c' + Σ(특정 간접효과)` 항등식을 병렬·직렬 모형 모두에서 수치로 검증
  - 알려진 계수로 생성한 자료에서 `a`, `b`, `c`, `ab` 복원 확인
  - 회귀(QR)와 부트스트랩(Cholesky) 두 계산 경로의 교차 검산
  - 부트스트랩 시드 재현성, `--jobs` 값과 무관한 동일 결과
  - 결측·비숫자·CP949·세미콜론·천단위 쉼표·유럽식 소수점 CSV, 특이행렬, 상수 열
  - CLI 종료코드·JSON 유효성(NaN 미포함)·오류 메시지·플래그가 실제 모형에 전달되는지
  - 재표본이 부족할 때 신뢰구간을 만들어내지 않는지(0폭 구간 금지)
- 개선 이력과 외부 리뷰 결과: [`HARDENING.md`](HARDENING.md)

## 참고 문헌

- Hayes, A. F. (2022). *Introduction to Mediation, Moderation, and Conditional
  Process Analysis* (3rd ed.). Guilford Press.
- Preacher, K. J., & Hayes, A. F. (2008). Asymptotic and resampling strategies
  for assessing and comparing indirect effects. *Behavior Research Methods*, 40, 879–891.
- MacKinnon, D. P., Lockwood, C. M., & Williams, J. (2004). Confidence limits for
  the indirect effect. *Multivariate Behavioral Research*, 39, 99–128.
- VanderWeele, T. J. (2016). Mediation analysis: a practitioner's guide.
  *Annual Review of Public Health*, 37, 17–32.

## 라이선스

MIT — [`LICENSE`](LICENSE)
