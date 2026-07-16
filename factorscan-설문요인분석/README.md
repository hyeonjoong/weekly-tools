# factorscan — 설문 척도 요인분석·타당도 진단기

설문 문항 CSV를 넣으면 **탐색적 요인분석(EFA)** 과 척도 타당도 진단을 한 번에 출력합니다:
요인분석 적합성(KMO·Bartlett), 몇 개의 요인으로 이루어졌는지(고유값·Kaiser·평행분석),
요인 적재량(Varimax 회전)·공통성, 그리고 수정된 문항-총점 상관까지.

## 목적 / Why this exists

**한국어** — 설문 척도로 논문을 쓸 때, 리뷰어는 거의 항상 *"이 척도가 요인분석에 적합한가(KMO·Bartlett), 몇 개
차원인가, 각 문항이 어느 요인에 얼마나 실리는가(적재량), 겉도는 문항은 없는가"* 를 묻습니다. SPSS로 매번
클릭해 뽑던 이 표들을 CSV 하나로 재현 가능하게(시드 고정) 뽑아 줍니다. 신뢰도(Cronbach α)를 다루는
`surveyscan`과 짝을 이루어, 이 도구는 **차원성·구성타당도** 쪽을 담당합니다. 임상·제약 연구자가
자체 개발 설문(예: BELL-001 수면 자가진단, 와우핏 청능재활 설문)을 검증하거나 게재 전 표를 준비할 때 씁니다.

**English** — When publishing with a survey scale, reviewers almost always ask whether the scale is
*factorable* (KMO, Bartlett), *how many dimensions* it has, *how each item loads* on the factors, and
*which items misbehave*. factorscan reproduces those exact tables — the ones you'd otherwise click through
in SPSS — from a single CSV, reproducibly (fixed seed). It is the *dimensionality / construct-validity*
companion to `surveyscan` (which covers *reliability*, Cronbach's α). Built for a clinical/pharma researcher
validating an in-house questionnaire or preparing a scale-validation table before submission.

**이 도구가 하는 것 / What it computes**
- **요인분석 적합성**: Bartlett 구형성 검정(χ², df, p), KMO 전체 + 문항별 MSA(표에 함께 표시)
- **요인(차원) 수**: 상관행렬 고유값·설명분산, Kaiser 기준(고유값>1), Horn 평행분석(정규난수·95백분위)
- **요인 적재량·회전**: 주성분 추출 + Varimax 직교회전 / Promax 사교회전(요인 간 상관행렬 Φ 함께), 공통성
- **요인별 신뢰도·적합도**: McDonald ω(요인별 합성신뢰도), RMSR·비중복잔차(재현 상관행렬 적합도), 상관행렬 행렬식(다중공선성)
- **문항 품질**: 수정된 문항-총점 상관(소속 요인/하위척도 기준), 저(低)공통성·저MSA·교차적재·저적재 자동 플래그
- **역문항 재점수화**, 결측(listwise) 처리, 인코딩 지정(`--encoding`, CP949 등), JSON 출력

> 추출 방식은 **주성분(principal component)** 으로 SPSS 요인분석의 기본 추출과 동일합니다. 축소랭크(공통요인)
> 모형이 꼭 필요하면 전용 패키지(예: `factor_analyzer`의 PAF/ML)를 함께 쓰는 것을 권합니다 — 아래 한계 참고.

## Install

```bash
cd factorscan-설문요인분석
python3 -m pip install -e .        # numpy만 설치됩니다
# 또는 설치 없이 바로:  python3 -m factorscan.cli ...
```

의존성은 `numpy` 하나입니다(카이제곱 p값 등 통계는 자체 구현, scipy 불필요).

## Usage

```bash
# 1) 설정 파일로(역문항·척도범위·문항목록 지정) — 가장 권장
factorscan examples/sleep_scale.csv --config examples/sleep_config.json

# 2) 열을 직접 지정
factorscan responses.csv --items Q1,Q2,Q3,Q4,Q5,Q6 --id-col ID

# 3) 요인 수를 직접 지정 + 회전 끄기 + JSON
factorscan responses.csv --id-col ID --n-factors 3 --rotation none --json
```

주요 옵션: `--n-factors K`(미지정 시 평행분석 기준 자동, 평행분석 끄면 Kaiser), `--rotation varimax|promax|none`,
`--reverse Q1,Q2 --scale-min 1 --scale-max 5`(역문항), `--parallel-iter N`(평행분석 반복, 0이면 생략),
`--seed`(재현), `--min-loading 0.40`(주적재/교차적재 임계값, 0~1), `--na 값`(결측 문자열 추가),
`--encoding cp949`(한국어 엑셀 CSV 등 UTF-8이 아닌 파일).

### 실제 출력 예시 (번들 예시: 수면 자가진단 8문항 · 80명)

```
==================================================================
  factorscan — 설문 척도 요인분석·타당도 진단
==================================================================
문항 수: 8    응답자: 77명 사용 (전체 80, 결측제거 3)

[ 1. 요인분석 적합성 ]
  Bartlett 구형성 검정: χ²(28) = 335.49, p = 2.046e-54  → 유의(적합)
  KMO 전체: 0.813  (우수(meritorious))

[ 2. 요인(차원) 수 진단 ]
  요인   고유값   설명분산%   누적%     평행분석
    1    3.821     47.8     47.8     1.673 ★
    2    2.098     26.2     74.0     1.419 ★
    3    0.598      7.5     81.5     1.241
   ...
  → Kaiser 기준(고유값>1): 2개 요인
  → 평행분석 기준(★): 2개 요인
  → 적용한 요인 수: 2개 (평행분석 기준)

[ 3. 요인 적재량 (Varimax 회전 후, 추출=주성분(PCA)) · 공통성 · MSA · 문항-총점 ]
  문항                F1      F2      공통성    MSA   요인총점
  Q1_잠들기어려움     0.239   0.820*   0.730   0.855     0.733   →F2
  Q2_자주깸           0.029   0.898*   0.807   0.772     0.790   →F2
  ...
  Q5_주간졸림         0.882*  0.137    0.796   0.805     0.793   →F1
  ...
  적재제곱합(SS)      2.968   2.950
  요인별 설명분산%: F1=37.1%  F2=36.9%
  요인별 신뢰도(McDonald ω): F1=0.911  F2=0.911
  모형 적합: RMSR=0.068  (|잔차|>0.05 인 비중복잔차 14/28 = 50%)
  요인 간 상관행렬 (promax 추정):
          F1      F2
    F1  1.000   0.293
    F2  0.293   1.000

[ 4. 점검이 필요한 문항 ]
  ✓ 임계값 기준으로 눈에 띄는 문제 문항이 없습니다.
```

수면의 질 문항(Q1–Q4)과 주간 기능 문항(Q5–Q8)이 서로 다른 요인으로 깔끔히 갈리는 것을 보여 줍니다.
(주성분 추출이므로 RMSR은 공통요인 모형보다 다소 큰 편이 정상입니다 — 아래 한계 참고.)

## 입력 형식 · 설정 파일

- CSV: 행=응답자, 열=문항. ID/텍스트 열은 자동 제외되거나 `--id-col`로 지정.
- 결측: 빈칸, `NA`, `N/A`, `null`, `.`, `-` 등을 자동 인식(추가는 `--na`).
- 설정 JSON(선택):
  ```json
  {
    "id_cols": ["ID"],
    "items": ["Q1", "Q2", "Q3", "Q4"],
    "reverse": ["Q1"],
    "scale_range": [1, 5]
  }
  ```

## 해석 가이드 (관례적 기준)

- **KMO** ≥ 0.6 적합, ≥ 0.8 우수 / **Bartlett** p < .05 이면 요인분석 적합.
- 요인 수: Kaiser(고유값>1)는 과대추정 경향이 있어, **기본값으로 평행분석(★) 결과를 적용**합니다
  (평행분석을 끄면 `--parallel-iter 0` Kaiser로 대체). 둘이 어긋나면 보고서가 알려 주며,
  `--n-factors K` 로 직접 지정할 수 있습니다.
- 적재량 **|.40| 이상(\*)** 을 주요 적재로 봅니다. 두 요인에 모두 .40↑이면 **교차적재**.
- **공통성 < 0.3**, **문항-총점 < 0.3**, **문항별 MSA < 0.5** 문항은 제거/수정 검토.
- **문항-총점('요인총점')** 은 각 문항이 주적재된 **요인(하위척도) 내 총점** 기준입니다(다차원 척도 권장).
  요인 배정은 |적재|최대(argmax)이므로 교차·저적재 문항은 배정이 불안정합니다 — 전체합 기준 값도
  JSON(`item_total_overall`)에 함께 제공되니 함께 확인하세요.
- **McDonald ω** 는 요인별 합성신뢰도(대략 ≥.70 권장)이나 **PCA 기반 근사(낙관적)** 입니다(아래 한계 참고).
  요인 내 문항이 2개 미만이면 정의 불가('—'). **RMSR** 은 재현 상관행렬 잔차(작을수록 적합, 대략 <.08).

## 한계 / Notes

- 추출은 **주성분(PCA)** 방식(SPSS 기본과 동일). 공통요인(PAF/ML) 모형과는 소폭 다를 수 있습니다.
- 회전은 **직교(Varimax)** 와 **사교(Promax)** 를 제공합니다. 기본은 Varimax이며, 요인 상관 추정치가
  크면(|r|≥.32) 보고서가 Promax 검토를 안내합니다. `--rotation promax` 로 사교 패턴적재와 요인 상관행렬 Φ를 얻습니다.
- 결측은 **listwise** 제거만 지원. 표본이 작을 때(문항당 5명 미만·n<문항수) 경고를 출력합니다.
- 순서형(리커트)을 연속형으로 취급하는 피어슨 상관 기반입니다(대다수 실무 관행과 동일).
- **문항-총점 상관**은 (자신 제외) 원점수 합을 총점으로 씁니다. 문항들의 척도범위가 크게 다르면
  (예: 0–100 슬라이더와 1–5 리커트 혼재) 넓은 범위 문항이 총점을 지배해 값이 왜곡될 수 있으니
  같은 척도의 문항끼리 분석하세요. (요인분석 자체는 상관행렬 기반이라 이 영향을 받지 않습니다.)
- **RMSR·ω 는 주성분(PCA) 추출 기준**이며 두 지표의 방향은 반대입니다. PCA는 공통성을 다소 크게
  추정하므로: **RMSR은 공통요인(PAF/ML)보다 다소 크게(보수적)**, **ω는 공통요인 ω·Cronbach α보다
  다소 높게(낙관적)** 나오는 경향이 있습니다. ω는 'PCA 기반 ω(근사)'로 보고하고, 정식 합성신뢰도가
  필요하면 PAF/ML 기반 ω를 함께 확인하세요. 두 지표 모두 절대 기준보다 상대 비교·경향 파악에 적합합니다.
- **요인별 ω·요인총점은 |적재|최대(argmax) 배정**에 기반하므로 교차적재·저적재 문항, 그리고 비회전
  (`--rotation none`) 결과에서는 배정이 불안정해 값이 왜곡될 수 있습니다(참고용). 요인 내 문항이 2개
  미만이면 ω는 정의되지 않습니다.
- 인코딩은 기본 UTF-8(BOM 허용). 한국어 엑셀이 저장한 **CP949/EUC-KR** 파일은 `--encoding cp949` 로 지정하세요.

## License

MIT © 2026 hyeonjoong
