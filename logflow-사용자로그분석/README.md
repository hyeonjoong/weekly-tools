# logflow — 사용자 이벤트 로그(CSV) 분석기

이벤트 로그 CSV 한 개를 넣으면 **세션화 · 이벤트/사용자별 집계 · DAU/WAU/MAU · 리텐션 · 퍼널 전환**을
한 번에 계산해 텍스트 리포트로 보여줍니다. 외부 라이브러리 없이 **파이썬 표준 라이브러리만** 사용합니다.

## 목적 / Why this exists

**한국어** — 앱·디바이스 사용자 로그(누가·무엇을·언제)는 모이지만, "어제 몇 명이 들어왔지?(DAU)",
"첫날 쓴 사람 중 다음날 다시 온 비율은?(리텐션)", "호흡운동 시작→완료까지 몇 %가 떨어지나?(퍼널)"
같은 질문에 매번 스프레드시트를 손으로 돌리는 건 번거롭고 실수도 납니다. logflow는 BELL이 자주 다루는
**유저테스트/앱 사용 로그**를 받아 이런 지표를 결정적(deterministic)으로 계산해, 사용 행동 분석이나
**논문/리포트용 사용성 지표** 초안을 즉시 만들어 줍니다.

**English** — Product/clinical apps accumulate raw event logs (who did what, when), but answering
"how many were active yesterday (DAU)?", "what fraction returned the next day (retention)?", or
"where do users drop off in the onboarding funnel?" usually means hand-rolling spreadsheets each time.
logflow takes one event-log CSV and computes sessionization, active-user curves, classic day-N
retention, and ordered funnel conversion in one pass — useful for a researcher who analyzes user-test
logs and needs reproducible usage metrics for analysis or a manuscript.

**언제 쓰나 / When** — 사용자 로그 CSV가 손에 있고, 빠르게 활성도·잔존율·퍼널을 보고 싶을 때.
SPSS/R로 본격 분석하기 전 **빠른 1차 요약**으로 적합합니다.

## Install

```bash
cd logflow-사용자로그분석
python3 -m pip install -e .
# 또는 설치 없이 바로:  python3 -m logflow.cli <csv>
```

요구사항: Python 3.11+ (의존성 없음). *(3.11+ 의 관대한 `datetime.fromisoformat` 에 의존 —
소수점 초·다양한 오프셋 표기까지 파싱하기 위함.)*

## Usage

입력 CSV는 최소 3개 열이 필요합니다 — 사용자 ID, 이벤트 이름, 타임스탬프
(기본 열 이름: `user_id`, `event`, `timestamp`). 타임스탬프는 ISO-8601 또는 epoch(초/밀리초)을 받습니다.

```bash
# 번들 예시로 실행 (퍼널 단계 지정)
logflow examples/app_events.csv \
    --funnel app_open,breathing_start,breathing_complete,sleep_report

# 열 이름이 다르면 매핑
logflow my_log.csv --user-col uid --event-col action --time-col ts

# 세션 간격 15분, 리텐션 day-1/3/7/30
logflow my_log.csv --gap-min 15 --retention 1,3,7,30
```

### 예시 출력 (일부)

```
[ 개요 ]
  총 이벤트       : 41
  고유 사용자     : 6
  고유 이벤트종류 : 4
  기간            : 2026-01-01 ~ 2026-01-09  (달력 9일, 활성 7일)
  세션 수         : 18  (비활동 기준 30분)
  세션당 이벤트   : 평균 2.3
  세션 길이       : 평균 17.5분 (단일이벤트 세션 제외 n=11)

[ 리텐션 ] (코호트=첫 활성일, 정확히 day-N 재방문)
  day-1   :   50.0%  (retained 3/6)
  day-7   :   40.0%  (retained 2/5)

[ 퍼널 전환 ] (시간순 진행)
  단계                          도달      직전대비       1단계대비
  app_open                     6    100.0%      100.0%
  breathing_start              5     83.3%       83.3%
  breathing_complete           4     80.0%       66.7%
  sleep_report                 3     75.0%       50.0%
```

### 옵션

| 옵션 | 설명 | 기본값 |
|------|------|--------|
| `--user-col` / `--event-col` / `--time-col` | 열 이름 매핑 | `user_id` / `event` / `timestamp` |
| `--gap-min` | 세션 분리 비활동 간격(분) | `30` |
| `--retention` | 리텐션 day-N 목록(쉼표) | `1,7` |
| `--funnel` | 퍼널 단계(이벤트 이름, 순서대로) | 없음 |
| `--top` | 상위 N 표시 | `10` |
| `--encoding` | CSV 인코딩 | `utf-8-sig` |
| `--tz-offset` | 시각에 더할 시간(시). 날짜를 현지시각 기준으로 끊을 때 (예: `9`=KST) | `0` |
| `--skip-bad-rows` | 파싱 불가한 타임스탬프 행을 오류 없이 건너뜀 | off |

## 지표 정의 (Notes / 한계)

- **세션**: 한 사용자의 이벤트를 시간순으로 보며, 직전 이벤트와의 간격이 `--gap-min`을 **초과**하면 새 세션.
- **DAU/WAU/MAU**: DAU=그날 고유 사용자, WAU=당일 포함 직전 7일, MAU=직전 28일의 고유 사용자(롤링).
- **점착도(stickiness)**: 평균 DAU / 평균 MAU. 주의 — MAU는 28일 롤링이라 데이터 기간이
  28일보다 짧으면 초반 며칠의 MAU가 작게 잡혀(워밍업) 점착도가 다소 높게 보일 수 있습니다.
  기간이 충분히 길 때 해석하세요.
- **리텐션**: 코호트는 사용자의 *첫 활성일* C. day-N 리텐션 = C+N일에 **정확히** 다시 활성인 비율.
  C+N이 데이터 최종일을 넘는 코호트는 관찰 기회가 없으므로 분모에서 제외합니다(편향 방지).
- **퍼널**: 단계는 시간순으로 진행해야 도달로 카운트(step_i는 step_{i-1} 시각 이후의 최초 발생).
- 타임존: 오프셋이 있으면 UTC로 변환해 비교하며, **날짜 버킷(DAU/리텐션 등)도 UTC 기준**입니다.
  로그가 KST 등 현지시각 기준이고 자정 경계가 중요하면 `--tz-offset 9` 처럼 보정하세요.
  오프셋 없는(naive) 타임스탬프는 그대로 사용되므로, 모든 로그가 동일 타임존이면 보정이 필요 없습니다.
- 결측: 빈 칸이나 `nan/null/none/na/n/a` 토큰이 든 행은 건너뜁니다(건너뛴 수는 실행 후 안내).
  파싱 불가한 타임스탬프는 기본적으로 오류로 중단하며, `--skip-bad-rows` 로 건너뛸 수 있습니다.
- 이 도구는 **빠른 1차 요약**용입니다. 통계적 검정/모델링은 별도 도구(예: 같은 저장소의 `surveyscan`)를 쓰세요.

## License

MIT © 2026 hyeonjoong
