# logflow — 사용자 이벤트 로그 분석기

이벤트 로그 파일 한 개(CSV/JSONL, `.gz` 가능)를 넣으면 **세션화 · 이벤트/사용자별 집계 ·
DAU/WAU/MAU · 리텐션(신뢰구간 포함) · 퍼널 전환(전환율 신뢰구간 + 단계별 소요시간) · 활동 시간대**를
한 번에 계산해 텍스트 또는 **JSON**으로 보여줍니다. 로그에 **군(arm) 열**이 있으면
`--group-col` 하나로 **중재군 vs 대조군 비교**(리텐션 차이 + Newcombe 신뢰구간 · Fisher 정확검정 ·
Mann-Whitney · 이탈 Kaplan-Meier/log-rank · Holm 다중비교 보정)까지 함께 냅니다.
외부 라이브러리 없이 **파이썬 표준 라이브러리만** 사용합니다.

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

**군 비교 / Arm comparison** — 무작위 배정한 유저테스트나 파일럿 임상에서 진짜 궁금한 것은
"전체 리텐션이 몇 %인가"가 아니라 **"중재군이 대조군보다 더 오래·더 자주 썼는가, 그 차이가
얼마나 불확실한가"** 입니다. `--group-col arm` 을 주면 군별 기술통계에 더해 리텐션·퍼널 완주율의
**위험차와 신뢰구간**, 참여도의 **순위검정과 효과크기**, 이탈까지의 시간에 대한 **생존분석**을
한 번에 계산하고, 여러 검정을 한 family 로 묶어 **Holm 보정**한 p 값을 함께 보고합니다.

**언제 쓰나 / When** — 사용자 로그가 손에 있고, 빠르게 활성도·잔존율·퍼널을 보고 싶을 때.
군 비교까지 필요하면 `--group-col` 로 1차 효과 추정치(점추정 + 구간)를 바로 얻을 수 있어,
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

입력 파일은 최소 3개 열이 필요합니다 — 사용자 ID, 이벤트 이름, 타임스탬프
(기본 열 이름: `user_id`, `event`, `timestamp`). 타임스탬프는 ISO-8601 또는 epoch(초/밀리초)을 받습니다.
형식은 **CSV**(구분자 자동감지)와 **JSONL/NDJSON**(한 줄에 JSON 객체 하나)을 지원하며, 확장자로
자동 판별합니다(`--format` 으로 강제 가능). 어느 쪽이든 **gzip 압축(`.gz`)** 이면 그냥 풀어서 읽습니다.
군 비교를 하려면 군 라벨 열(예: `arm`)을 하나 더 두고 `--group-col arm` 을 주세요.

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

# 군 비교 (중재군 vs 대조군) — 번들 2군 예시 24명·3주
logflow examples/trial_events.csv --group-col arm --ref-group control \
    --funnel app_open,breathing_start,breathing_complete,sleep_report --retention 1,7

# 이탈 기준을 14일로 바꾸고, 군 비교 표까지 엑셀용 CSV 로 저장
logflow examples/trial_events.csv --group-col arm --churn-days 14 --csv-dir 표출력

# 군이 3개 이상일 때 비교할 두 군만 골라서
logflow my_log.csv --group-col arm --only-groups 중재군,대조군

# JSONL / gzip 로그도 그대로 (확장자로 자동 판별, --format 으로 강제 가능)
logflow events.jsonl.gz --group-col arm
logflow events.log --format jsonl
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

군 비교(`--group-col arm`, 번들 예시 `examples/trial_events.csv` — 2군 24명·3주):

```
[ 군 비교 ] (열: arm, 기준군: control)
  군              사용자  이벤트    세션   이벤트/명   세션/명   사용시간/명   활성일/명
  control             12      84      44         7.0       3.0        15.0분         3.0
  intervention        12     239      80        18.0       6.5        66.5분         6.5
  * 군별 값은 모두 사용자당 중앙값입니다 (한 사용자가 여러 번 세어지지 않도록).
  * 사용시간 = 각 세션의 (첫→마지막 이벤트) 시간의 합. 이벤트가 하나뿐인 세션은
    0분으로 잡히며, 세션과 세션 사이의 시간은 포함하지 않습니다.

  ── 비율 비교 (intervention − control, 위험차 95%CI: Newcombe / p: Fisher exact) ──
  지표                      intervention     control     차이  95%CI                    p   p(Holm)
  day-1 리텐션                      8/12        8/12   +0.0%p  [-33.8, +33.8]%p     1.000     1.000
  day-7 리텐션                      3/12        1/12  +16.7%p  [-14.8, +45.7]%p     0.590     1.000
  퍼널 완주(sleep_report)          12/12        5/12  +58.3%p  [+22.5, +80.7]%p     0.005     0.027

  ── 분포 비교 (사용자당 값, Mann-Whitney U · 효과크기 rank-biserial) ──
  지표                      intervention     control  효과크기       p   p(Holm)
  사용자당 이벤트 수(건)            18.0         7.0     +0.78   0.001     0.008
  사용자당 세션 수(회)               6.5         3.0     +0.49   0.042     0.208
  사용자당 총 사용시간(분)          66.5        15.0     +0.87  <0.001     0.003
  사용자당 활성 일수(일)             6.5         3.0     +0.49   0.042     0.208
  * 표시값은 중앙값. 효과크기 +는 첫 군이 큼, −는 작음 (0=차이 없음).

  ── 이탈까지의 시간 (마지막 활동 후 7일 이상 무활동 = 이탈) ──
    intervention: n=12, 이탈 관찰 9명 (절단 3명), 생존중앙값 4일
    control: n=12, 이탈 관찰 11명 (절단 1명), 생존중앙값 3일
    log-rank: chi2=2.47, p=0.116, p(Holm)=0.347

  * 다중비교: 이 절의 검정 8개를 하나의 family 로 보고
    Holm–Bonferroni 로 보정했습니다 — 판단은 p(Holm) 으로 하세요.
    (검정 개수는 --retention/--funnel 에 따라 달라지므로, 보정된 p 값도
     함께 바뀝니다. 비교 설계를 먼저 정하고 돌리세요.)
  * 이 비교는 사후(post-hoc) 관찰 분석입니다. 사전에 정한 주요 평가변수가
    아니라면 확증이 아닌 탐색적 근거로 다루세요.
```
*(위 예시 수치는 `examples/trial_events.csv` 로 실제 실행한 결과입니다. 예시 데이터는
군 차이가 드러나도록 만든 합성 로그이며, 실제 임상 결과가 아닙니다.)*

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
| `--csv-dir` | DAU·리텐션·퍼널·군 비교 등 표를 이 폴더에 CSV(엑셀 호환)로 저장 | 없음 |
| `--group-col` | 군(arm) 라벨 열 이름. 지정하면 군 간 비교 분석을 함께 수행 | 없음 |
| `--ref-group` | 기준군(대조군) 라벨. 비율 차이는 (비교군 − 기준군) | 사전순 첫 군 |
| `--churn-days` | 마지막 활동 후 이 일수 이상 무활동이면 이탈로 간주(생존분석) | `7` |
| `--format` | 입력 형식 `auto`/`csv`/`jsonl`. `.gz` 는 항상 자동 해제 | `auto` |
| `--only-groups` | 이 군들만 남겨 분석(쉼표 구분). 3군 이상에서 비교할 두 군을 고를 때 | 없음 |
| `--max-rows` | 읽어들일 최대 이벤트 수(0=제한 없음). 압축 로그의 메모리 폭주 방지 | `0` |

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
- **CSV 표 내보내기(`--csv-dir`)**: DAU/WAU/MAU·리텐션(신뢰구간 포함)·이벤트·사용자·활동
  표를 각각 CSV 로 저장합니다(엑셀 호환 utf-8-sig). `funnel.csv` 는 `--funnel` 을 준 경우에만,
  `group_summary`·`group_tests`(검정 결과 한 표)·`group_survival_km`(KM 곡선점) 은
  `--group-col` 을 준 경우에만 만들어집니다. 표 이름은 고정이라 폴더에 같은 이름의 파일이
  있으면 **말없이 덮어씁니다**(덮어쓴 개수는 실행 후 안내). 입력 로그와 같은 파일을
  덮어쓰려 하면 아무것도 쓰지 않고 오류로 멈춥니다.
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
  users 배열 등). `--group-col` 을 쓰면 여기에 더해 **군 라벨, 군별 인원·중앙값, KM 곡선의
  시점별 인원(`group_survival_km.csv`)** 이 출력됩니다. 사용자↔군 대응표 자체는 출력하지
  않지만, **인원이 적은 군에서는 중앙값이나 KM 시점이 곧 개인의 값**이 되어 같은 폴더의
  `users.csv` 와 맞추면 누가 어느 군인지 좁혀질 수 있습니다(그래서 5명 미만 군에는 경고를
  표시합니다). 군 라벨 자체가 민감할 수도 있습니다(예: `조기중단군`). 리포트 파일을 외부와
  공유할 때는 민감정보로 취급하세요. `--out` 은 지정한 경로를 **말없이 덮어씁니다**
  (심볼릭 링크 포함).
- 네트워크 전송·외부 호출이 전혀 없습니다(오프라인, 표준 라이브러리만). 입력 CSV를 읽고
  `--out` 경로에만 씁니다.
### 군 비교 (`--group-col`)

- **분석 단위는 사용자**입니다. 세션·이벤트를 독립 관측치로 세면 한 사람이 여러 번 기여해
  표준오차가 과소평가되므로, 모든 검정은 사용자당 값 하나(또는 0/1 결과 하나)만 씁니다.
- **군 배정**: 한 사용자에게 서로 다른 라벨이 붙어 있으면 **가장 이른 이벤트의 라벨**로 확정하고
  충돌 건수를 리포트에 경고로 남깁니다. 라벨이 하나도 없는 사용자는 군 비교에서 제외합니다
  (전체 지표에는 그대로 포함됩니다).
- **비율 비교** (day-N 리텐션, 퍼널 완주율): 위험차(risk difference)와 **Newcombe hybrid-score
  신뢰구간**, p 값은 **Fisher 정확검정**(양측). 군당 수십 명 규모에서 카이제곱 근사보다 적절합니다.
  리텐션의 관찰 지평(eligible 판정)은 **전체 데이터의 마지막 날**로 통일해 군 간 분모를 공정하게 맞춥니다.
- **분포 비교** (사용자당 이벤트/세션/총 사용시간/활성일수): **Mann-Whitney U**(동점 보정 +
  연속성 보정)와 효과크기 **rank-biserial**(−1~+1). 로그는 치우쳐 있어 t-검정보다 적절합니다.
  한 군의 n<8 이면 정규근사 p 값이 대략적이라는 경고를 함께 표시합니다.
- **이탈까지의 시간**: 관찰 종료일(전체 데이터의 마지막 날) 기준으로, 마지막 활동 후
  `--churn-days`(기본 7) 이상 기록이 없으면 **이탈로 관찰**(시간 = 첫 활동→마지막 활동 일수),
  그렇지 않으면 **우측 절단**(시간 = 첫 활동→관찰 종료 일수)으로 두고 **Kaplan-Meier**
  (log-log 변환 CI)와 **log-rank 검정**을 냅니다. log-rank는 비례위험을 가정하므로 곡선이
  교차하면 검정력이 떨어집니다 — p 값만 보지 말고 KM 곡선(`group_survival_km.csv`)을 함께 보세요.
- **사용시간의 정의**: 군 표의 `사용시간/명`(과 `사용자당 총 사용시간`)은 **각 세션의
  (첫 이벤트 → 마지막 이벤트) 시간을 사용자별로 합한 값**입니다. 따라서 이벤트가 하나뿐인
  세션은 0분으로 잡히고, 세션과 세션 사이의 시간은 포함되지 않습니다. 개요의 `세션 길이`가
  단일 이벤트 세션을 *제외*하는 것과 달리, 여기서는 **0분으로 포함**합니다 — "앱을 켠 시간"이
  아니라 "기록된 활동이 이어진 시간의 합"으로 읽으세요.
- **다중비교**: 위 검정들을 **하나의 family** 로 묶어 **Holm–Bonferroni** 보정한 `p(Holm)` 을
  함께 냅니다. 판단은 보정된 값으로 하세요. 주의 — **family 크기는 `--retention` 개수와
  `--funnel` 지정 여부에 따라 달라지므로, 같은 데이터라도 옵션을 바꾸면 `p(Holm)` 이
  바뀝니다.** 어떤 비교를 볼지 먼저 정한 뒤 한 번만 돌리세요(중복된 day-N 은 자동으로
  한 번만 셉니다).
- **비교가 불가능한 경우**: 한 군의 관찰 기회(eligible)나 퍼널 1단계 도달자가 0명이면 그
  비교는 계산할 수 없습니다. 이때 행을 조용히 빼지 않고 `!` 안내로 이유를 표시합니다.
- **소수 인원 군**: 어떤 군의 인원이 5명 미만이면 중앙값·생존곡선이 사실상 개인의 값이라
  재식별될 수 있어 경고를 표시합니다. 해석에도 쓰지 마세요.
- **`--dedup` 과의 관계**: 중복 제거 키에 군 라벨이 포함됩니다. 즉 군 라벨만 다른 두 행은
  중복이 아니라 *배정 모순*으로 보아 남기고, 군 비교에서 충돌로 경고합니다.
- **군이 3개 이상이면 기술통계만** 냅니다. 모든 쌍을 자동으로 검정해 다중비교를 부풀리지 않기
  위한 선택입니다. 비교할 두 군을 고르려면 **`--only-groups 중재군,대조군`** 을 쓰세요
  (`--from/--to` 는 날짜 필터라 군을 고를 수 없습니다). `--only-groups` 를 주면 전체 지표
  (DAU·리텐션·퍼널 등)도 남은 군만으로 다시 계산됩니다.
- **한계 — 이 비교는 사후(post-hoc) 관찰 분석입니다.** 무작위 배정이었더라도 여기서 쓰는 지표는
  사전에 정한 주요 평가변수가 아니며, 공변량 보정·중도탈락 모형·군집 구조를 다루지 않습니다.
  확증적 결론이 아니라 **효과크기와 불확실성의 1차 추정치**로만 쓰세요.

- 이 도구는 **빠른 1차 요약**용입니다. 회귀·혼합모형 등 본격 모델링은 별도 도구(예: 같은 저장소의
  `surveyscan`)나 R/SPSS 를 쓰세요.

## JSON 출력 구조 (`--json`)

최상위 키: `meta`, `overview`, `events`, `users`, `active_users`, `stickiness`,
`retention`, `funnel`, `activity`, `groups`. `groups` 는 `--group-col` 을 준 경우에만
채워지고(아니면 `null`), 아래 키를 가집니다.

| 키 | 내용 |
|----|------|
| `group_col`, `groups`, `reference` | 군 열 이름, 발견된 군 목록(사전순), 기준군 |
| `compare_a`, `compare_b` | 검정에서 쓴 비교군/기준군. 모든 차이는 (a − b) |
| `n_tests` | Holm 보정 family 크기 |
| `ungrouped_users`, `conflicting_users` | 군 미상 / 라벨 충돌 사용자 수 |
| `notes` | 리포트의 `!` 안내와 같은 문자열 목록 (비교 불가 사유 등) |
| `arms[]` | 군별 `n_users`·`n_events`·`n_sessions`·각종 `median_*`·`retention`·`funnel_completion` |
| `proportion_tests[]` | `label`, `a`/`b`(`successes`,`n`,`rate`), `diff`, `diff_ci`, `p_fisher`, `p_holm` |
| `distribution_tests[]` | `label`, `n_a`/`n_b`, `median_a`/`median_b`, `u`, `z`(부호=방향), `rank_biserial`, `p_mann_whitney`, `p_holm` |
| `survival` | `churn_days`, `n_churned`, 군별 `curves`(`points[]`: `day`,`n_risk`,`n_event`,`survival`,`ci`), `logrank`(`chi2`,`p`,`p_holm`,`observed*`,`expected*`) |

`survival.logrank` 의 `observed`/`expected` 는 **두 군을 합쳐 위험집합이 2명 이상인 시각**만
누적한 값입니다(한 군만 남은 시점은 분산이 정의되지 않아 양쪽에서 똑같이 제외). 그래서
`observed1 + observed2` 가 전체 이탈자 수보다 작을 수 있고, 대신 `(O1−E1) + (O2−E2) = 0` 이
정확히 성립합니다.

## License

MIT © 2026 hyeonjoong
