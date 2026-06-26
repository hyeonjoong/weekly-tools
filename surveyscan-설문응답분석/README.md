# surveyscan — 설문 응답 CSV 분석기

설문(설문지) 응답 CSV 한 개를 넣으면 **문항별 기술통계 · 결측 요약 · 역문항 자동 재코딩 · 하위척도 점수 · Cronbach α(신뢰도) · 수정된 문항-총점 상관 · 문항 제거 시 α** 를 한 번에 리포트로 만들어 줍니다. 표준 라이브러리만 사용 — 설치할 외부 패키지가 없습니다.

## 목적 / Why this exists

**(한국어)** 임상·제약 연구나 사용자 연구에서 설문 데이터를 받으면 매번 똑같은 일을 합니다 — 문항별 평균/표준편차 보기, 결측 확인, 역문항 뒤집기, 하위척도 점수 만들기, 신뢰도(Cronbach α) 계산. SPSS를 열어 클릭으로 반복하거나 R 코드를 매번 다시 짜는 대신, CSV와 (선택) 하위척도 설정만 주면 일관된 리포트가 나옵니다. ISI(불면), 청능재활 설문 등 우리 데이터에 바로 쓰도록 설계했습니다. 논문 Methods/Results에 넣을 신뢰도와 기술통계를 빠르고 재현 가능하게 뽑는 것이 목표입니다.

**(English)** Clinical/UX researchers repeat the same chores on every questionnaire dataset: per-item descriptives, missing-data checks, reverse-coding, subscale scoring, and Cronbach's alpha. Instead of clicking through SPSS or rewriting R each time, give this tool a CSV (and an optional subscale config) and get one consistent, reproducible report. Built for a researcher who writes manuscripts and needs trustworthy reliability + descriptive numbers for Methods/Results — fast and offline.

## 설치 / Install

```bash
cd surveyscan-설문응답분석
python3 -m pip install -e .
```

설치 없이도 실행할 수 있습니다: `python3 -m surveyscan.cli ...`

## 사용법 / Usage

입력 CSV는 **행=응답자, 열=문항**, 첫 행은 헤더(문항 이름)입니다. 빈 칸과 `NA/N/A/NaN/.` 등은 결측으로 처리합니다.

### 1) 하위척도 설정과 함께 (권장)

`config.json` 예시:

```json
{
  "scale_min": 0,
  "scale_max": 4,
  "subscales": {
    "불면증상(ISI)": ["ISI1", "ISI2", "ISI3", "ISI4", "ISI5", "ISI6", "ISI7"],
    "주간기능": ["DAY1", "DAY2", "DAY3"]
  },
  "reverse_items": ["DAY3"],
  "min_valid_ratio": 0.5
}
```

- `reverse_items`: 역방향(긍정문) 문항. `x' = (scale_min + scale_max) - x` 로 자동 재코딩합니다. 역문항이 있으면 `scale_min`/`scale_max`는 필수입니다.
- `min_valid_ratio`: 응답자가 해당 하위척도 문항을 이 비율 이상 응답해야 점수를 부여(기본 0.5).

```bash
surveyscan examples/sleep_survey.csv --config examples/sleep_config.json --id-col ID
```

### 2) 설정 없이 빠르게

ID 컬럼만 빼면 숫자 컬럼 전체를 하나의 척도(`전체`)로 보고 분석합니다.

```bash
surveyscan examples/sleep_survey.csv --id-col ID
```

### 주요 옵션

| 옵션 | 설명 |
|------|------|
| `--config, -c` | 하위척도/역문항 설정 JSON |
| `--id-col 이름` | 분석에서 제외할 ID 컬럼(여러 번 가능) |
| `--na-number 999` | 결측 코드로 쓰인 숫자(여러 번 가능) |
| `--delimiter ';'` | CSV 구분자(기본 콤마) |
| `--json` | JSON으로 출력 |
| `-o, --output 파일` | 결과를 파일로 저장 |

### 예시 출력

```
================================================================
  설문 응답 분석 리포트 (surveyscan)
================================================================
  응답자 수 : 40
  문항 수   : 10
  역문항    : DAY3 (재코딩 적용됨)
  척도 범위 : 0 ~ 4

[ 결측 요약 ]
  전체 셀 400개 중 결측 6개 (1.5%)
  완전응답자(모든 문항 응답) 35명 (87.5%)

[ 문항별 기술통계 ]
  문항              N    결측      평균     표준편차     중앙    최소    최대
  ------------------------------------------------------------
  ISI1           39 1(2.5%)    2.21     1.26    2.0   0.0   4.0
  ...

[ 하위척도별 신뢰도 · 점수 ]

  ▶ 불면증상(ISI)  (문항 7개)
     Cronbach α = 0.909  [우수]   (완전응답 37명 기준; listwise 제외 3명)
     하위척도 점수: 평균 2.16 ± 0.89  (점수산출 40명)
     문항                문항-총점 r       α(문항제거시)
     ----------------------------------------
     ISI6                0.510          0.916  ← 제거시 α↑(검토)
     ...
```

## 결과 읽는 법

- **Cronbach α** — 내적 일치도(신뢰도). 해석: `.9 우수 / .8 양호 / .7 수용가능 / .6 의심 / <.6 낮음`. 완전응답자(listwise)만으로 계산하며, 제외된 인원 수를 함께 표기합니다.
- **하위척도 점수** — 역문항 재코딩 후 가용 문항의 **평균**(합이 아님). `min_valid_ratio` 미만으로 응답한 사람은 점수에서 제외됩니다.
- **문항-총점 r** — 수정된 문항-총점 상관(해당 문항을 제외한 나머지 합과의 상관). `< .30` 이면 그 문항이 척도와 잘 안 맞을 수 있습니다.
- **α(문항제거시)** — 그 문항을 빼면 척도 α가 어떻게 변하는지. 현재 α보다 **올라가면**(`← 제거시 α↑`) 문항 적합성을 재검토하세요.

## 한계 / Notes

- 신뢰도(α)·문항-총점 상관은 **완전응답자(listwise)** N 기준이고, **하위척도 점수**는 가용문항(`min_valid_ratio` 충족) N 기준입니다 — 둘의 N이 다를 수 있으니(예: α는 37명, 점수는 40명) 논문에 옮길 때 분모를 구분하세요. 리포트에 두 N을 모두 표기합니다.
- `scale_min`/`scale_max`를 지정하면 **범위를 벗어난 값**(입력 오류 가능)을 리포트 상단에 ⚠로 표시합니다. 역코딩은 이런 값을 그대로 뒤집으므로(예: 1~5 척도의 `9` → `-3`), 경고가 뜨면 원자료를 먼저 정리하세요.
- `--config` 없이 실행하면 숫자값이 없는 컬럼(전부 결측/텍스트)은 자동 제외되며, 어떤 컬럼이 빠졌는지 경고로 알립니다. 의도와 다르면 `--config`로 문항을 명시하세요.
- 문항별 기술통계는 **원자료(raw) 값** 기준으로 보고합니다(역코딩 전). 데이터 입력 오류·범위 이탈을 그대로 보기 위함입니다. 역코딩은 신뢰도·하위척도 점수 계산에만 반영됩니다.
- Cronbach α는 일차원성(단일 구성개념)을 가정합니다. 요인구조 검증(EFA/CFA)은 이 도구의 범위 밖입니다.
- McDonald's ω, 결측 다중대치 등은 포함하지 않습니다(의도적으로 가볍게 유지).

## 라이선스

MIT © 2026 hyeonjoong
