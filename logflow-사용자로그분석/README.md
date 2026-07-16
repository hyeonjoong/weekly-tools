# logflow — 사용자 이벤트 로그(CSV) 분석기

이벤트 로그 CSV 한 개를 넣으면 **세션화 · 이벤트/사용자별 집계 · DAU/WAU/MAU · 리텐션(신뢰구간 포함) ·
퍼널 전환(전환율 신뢰구간 + 단계별 소요시간) · 활동 시간대**를 한 번에 계산해 텍스트 또는 **JSON**으로
보여줍니다. 외부 라이브러리 없이 **파이썬 표준 라이브러리만** 사용합니다.

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

> 아래 예시의 `logflow ...` 단축 명령은 위 **Install** 의 `pip install -e .` 를 실행해야 씁니다.
> 설치하지 않았다면 `logflow` 대신 `python3 -m logflow.cli` 로 바꿔 그대로 실행하세요.

```bash
# 번들 예시로 실행 (퍼널 단계 지정)
logflow examples/app_events.csv \
    --funnel app_open,breathing_start,breathing_complete,sleep_report

# 열 이름이 다르면 매핑
logflow my_log.csv --user-col uid --event-col action --time-col ts

# 세션 간격 15분, 리텐션 day-1/3/7/30
logflow my_log.csv --gap-min 15 --retention 1,3,7,30

# 기간을 잘라 분석하고 결과를 JSON 파일로 저장 (다운스트림 분석·논문용)
logflow my_log.csv --from 2026-01-01 --to 2026-01-31 --json --out result.json

# 세미콜론/탭 구분 CSV·중복 행이 섞인 실데이터: 자동감지 + 중복 제거
logflow my_log.csv --dedup            # 구분자는 자동감지(콤마/세미콜론/탭/파이프)
```

### 예시 출력 (일부)

```
[ 개요 ]
  총 이벤트       : 41
  고유 사용자     : 6
  기간            : 2026-01-01 ~ 2026-01-09  (달력 9일, 활성 7일)
  세션 수         : 18  (비활동 기준 30분)
  사용자당 세션   : 평균 3.0
  세션당 이벤트   : 평균 2.3 · 중앙값 2
  세션 길이       : 평균 17.5분 · 중앙값 21.0분 (단일이벤트 세션 제외 n=11)

[ 리텐션 ] (코호트=첫 활성일, 정확히 day-N 재방문)
  day-1   :   50.0%  (retained 3/6, 95%CI 18.8–81.2%)
  day-7   :   40.0%  (retained 2/5, 95%CI 11.8–76.9%)

[ 퍼널 전환 ] (시간순 진행)     (한글은 전각으로 렌더되어 실제 터미널에선 열이 맞습니다)
  단계                      도달  직전대비   1단계대비    소요(중앙)
  app_open                     6    100.0%      100.0%             -
  breathing_start              5     83.3%       83.3%          60초
  breathing_complete           4     80.0%       66.7%        19.5분
  sleep_report                 3     75.0%       50.0%         2.0분
  * 소요(중앙): 직전 단계 도달자 중 이 단계 도달자의 소요시간 중앙값(조건부).
  ── 전환율 95% 신뢰구간 ──
    breathing_start: 직전대비 83.3%, 95%CI 43.6–97.0%
    ...

[ 활동 시간대 ]
  피크 시간대     : 22시 (32건, 전체의 78%)
  요일별 이벤트   : 월·0  화·0  수▃7  목█22  금▃7  토▂4  일▁1
  시간대 분포     : 0시 ▁▁▁▁▁▁▁▂▁▁▁▁▁▁▁▁▁▁▁▁▁▁█▂ 23시
```

### 옵션

| 옵션 | 설명 | 기본값 |
|------|------|--------|
| `--user-col` / `--event-col` / `--time-col` | 열 이름 매핑 | `user_id` / `event` / `timestamp` |
| `--gap-min` | 세션 분리 비활동 간격(분) | `30` |
| `--retention` | 리텐션 day-N 목록(쉼표) | `1,7` |
| `--retention-mode` | `exact`(정확히 day-N) / `rolling`(day-N 이후) | `exact` |
| `--funnel` | 퍼널 단계(이벤트 이름, 순서대로) | 없음 |
| `--top` | 상위 N 표시 | `10` |
| `--encoding` | CSV 인코딩 | `utf-8-sig` |
| `--tz-offset` | 시각에 더할 시간(시). 날짜를 현지시각 기준으로 끊을 때 (예: `9`=KST) | `0` |
| `--skip-bad-rows` | 파싱 불가한 타임스탬프 행을 오류 없이 건너뜀 | off |
| `--confidence` | 리텐션·퍼널 전환율 신뢰구간 수준 (0~1) | `0.95` |
| `--delimiter` | CSV 구분자. 미지정 시 자동감지(콤마/세미콜론/탭/파이프) | auto |
| `--dedup` | `(user, event, timestamp)` 가 완전히 같은 중복 행 제거 | off |
| `--from` / `--to` | 분석 기간을 이 날짜(YYYY-MM-DD) 구간[포함]으로 제한 (tz 보정 후 기준) | 없음 |
| `--json` | 텍스트 대신 JSON 결과 출력 (다운스트림 분석·논문용) | off |
| `--out` | 리포트를 이 파일에 저장 (미지정 시 표준출력) | 표준출력 |
| `--csv-dir` | DAU·리텐션·퍼널 등 표를 이 폴더에 CSV(엑셀 호환)로 저장 | 없음 |

## 지표 정의 (Notes / 한계)

- **세션**: 한 사용자의 이벤트를 시간순으로 보며, 직전 이벤트와의 간격이 `--gap-min`을 **초과**하면 새 세션.
- **DAU/WAU/MAU**: DAU=그날 고유 사용자, WAU=당일 포함 직전 7일, MAU=직전 28일의 고유 사용자(롤링).
- **점착도(stickiness)**: 평균 DAU / 평균 MAU. 주의 — MAU는 28일 롤링이라 데이터 기간이
  28일보다 짧으면 초반 며칠의 MAU가 작게 잡혀(워밍업) 점착도가 다소 높게 보일 수 있습니다.
  기간이 충분히 길 때 해석하세요.
- **리텐션**: 코호트는 사용자의 *첫 활성일* C. C+N이 데이터 최종일을 넘는 코호트는 관찰 기회가
  없으므로 분모에서 제외합니다(편향 방지). 두 정의를 지원합니다(`--retention-mode`):
  - `exact`(기본): C+N일에 **정확히** 다시 활성인 비율. 클래식 day-N.
  - `rolling`: C+N일 **이후(포함)** 한 번이라도 활성인 비율. 표본이 작을 때 특정일만 세는
    exact 의 요철을 완화해 더 안정적입니다(예: C+2, C+4 활성 사용자가 exact day-3 에선 이탈로
    잡히는 문제 완화).
- **CSV 표 내보내기(`--csv-dir`)**: DAU/WAU/MAU·리텐션(신뢰구간 포함)·퍼널·이벤트·사용자·활동
  표를 각각 CSV 로 저장합니다(엑셀 호환 utf-8-sig). 원고 표/추가 분석에 바로 붙여 쓰기 좋습니다.
- **신뢰구간(CI)**: 리텐션·퍼널 전환율은 **Wilson score 구간**(기본 95%)을 함께 보고합니다.
  코호트/표본이 작을 때 Wald(정규근사)보다 안정적이며, 비율이 0%/100% 근처여도 구간이
  붕괴하지 않습니다. `--confidence` 로 수준을 바꿉니다(예: `0.90`, `0.99`). *표본이 작으면
  구간이 매우 넓게 나옵니다 — 이는 도구의 결함이 아니라 데이터가 말해줄 수 있는 한계입니다.*
- **퍼널**: 단계는 시간순으로 진행해야 도달로 카운트(step_i는 step_{i-1} 시각 이후의 최초 발생).
  각 단계에는 **직전 단계→이 단계까지 걸린 시간의 중앙값**(소요)을 함께 표시합니다. 도달한
  사용자만 대상이므로 이탈자는 이 시간 계산에 포함되지 않습니다(조건부 소요시간).
- **세션 길이/이벤트 분포**: 로그는 치우쳐(skewed) 있어 평균만 보면 오해하기 쉬우므로
  **중앙값**을 함께 봅니다. JSON 출력에는 사분위수·p90 까지 포함됩니다.
- **활동 시간대**: 시(0~23)·요일(월~일)별 이벤트 분포와 피크 시간대. 시각 버킷은 tz 보정
  후 기준이며, 수면앱처럼 사용 시간대가 중요한 로그에서 유용합니다.
- 타임존: 오프셋이 있으면 UTC로 변환해 비교하며, **날짜 버킷(DAU/리텐션 등)도 UTC 기준**입니다.
  로그가 KST 등 현지시각 기준이고 자정 경계가 중요하면 `--tz-offset 9` 처럼 보정하세요.
  오프셋 없는(naive) 타임스탬프는 그대로 사용되므로, 모든 로그가 동일 타임존이면 보정이 필요 없습니다.
- 결측: 빈 칸이나 `nan/null/none/na/n/a` 토큰이 든 행은 건너뜁니다(건너뛴 수는 실행 후 안내).
  파싱 불가한 타임스탬프는 기본적으로 오류로 중단하며, `--skip-bad-rows` 로 건너뛸 수 있습니다.
- 실데이터 관용: 구분자(콤마/세미콜론/탭/파이프) 자동감지, 헤더 열 이름의 앞뒤 공백·대소문자
  차이 허용, BOM(`utf-8-sig`) 처리. 인코딩이 다르면 `--encoding cp949` 처럼 지정하세요.
  완전 중복 행은 `--dedup` 으로 제거합니다(중복 수는 실행 후 안내).
- **개인정보(PII)**: 리포트(텍스트·JSON)에는 입력의 **사용자 ID가 그대로** 담깁니다(상위 사용자,
  users 배열 등). 이는 본인 데이터를 다루는 정상 동작이지만, 리포트 파일을 외부와 공유할 때는
  민감정보로 취급하세요. `--out` 은 지정한 경로를 **말없이 덮어씁니다**(심볼릭 링크 포함).
- 네트워크 전송·외부 호출이 전혀 없습니다(오프라인, 표준 라이브러리만). 입력 CSV를 읽고
  `--out` 경로에만 씁니다.
- 이 도구는 **빠른 1차 요약**용입니다. 통계적 검정/모델링은 별도 도구(예: 같은 저장소의 `surveyscan`)를 쓰세요.

## License

MIT © 2026 hyeonjoong
