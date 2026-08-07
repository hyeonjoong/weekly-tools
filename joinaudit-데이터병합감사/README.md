# joinaudit — 데이터 병합 감사기

출처가 다른 여러 CSV/TSV/XLSX를 **피험자 × 시점** 기준으로 한 장의 분석용 표로 병합하고,
**누가 왜 빠졌고 N이 왜 이 숫자가 되었는지를 증거로 남기는** 오프라인 CLI입니다.

---

## 목적 / Why this exists

**한국어.** 장비 엔지니어가 워치 export CSV를 주고, 호흡밴드 로그는 따로 오고, 수면일기는
엑셀이고, ISI는 설문 플랫폼에서 받은 또 다른 CSV입니다. 분석을 시작하려면 이걸 먼저 **한 장의
표**로 만들어야 하는데, 지금은 엑셀 VLOOKUP을 하거나 pandas `merge`를 그때그때 세 줄 짜서
돌립니다. **둘 다 조용히 틀립니다** — 피험자 ID가 한 파일은 `S01`, 다른 파일은 `S1`, 또 다른
파일은 `BELL-001-01`이라 pandas는 **아무 말 없이 행을 버리고**, 같은 피험자·같은 날짜가 재측정으로
두 번 들어 있으면 `merge`는 경고 없이 **행을 곱합니다**. 수면 데이터는 자정을 넘겨서, 03:20의
HRV 값이 어느 밤에 속하는지를 파일마다 다르게 정하면 표는 완성되지만 하루씩 어긋납니다.
**이 실수들의 공통점은 티가 안 난다는 것입니다.** 병합은 성공한 것처럼 보이고, 표가 나오고,
통계도 돌아갑니다. 틀린 건 N뿐입니다. joinaudit은 이 자리를 결정론적으로 막고, `merged.csv`와
함께 **"입력 662행 → 최종 248행, 차이의 내역"** 을 논문 Methods에 그대로 넣을 수 있는 형태로
남깁니다. BELL-001(EEG·HRV·호흡·ISI·수면일기·UT 로그) 연구자를 위해 만들었습니다.

**English.** Clinical multimodal studies arrive as a pile of mismatched exports — a watch CSV
from an engineer, a respiration log, a sleep diary in Excel, a questionnaire CSV from a survey
platform. Getting them into one analysable table is where most of the time (and most of the
silent error) goes. Excel VLOOKUP collapses the moment subject IDs are written `S01` / `S1` /
`BELL-001-01`; pandas `merge` drops non-matching rows without a word and *multiplies* rows on
duplicate keys without a warning; and post-midnight sleep timestamps end up attributed to the
wrong night. All three failures look like success. joinaudit blocks them deterministically —
no fuzzy matching, no implicit cartesian join, no silent row loss — and, more importantly,
emits the **audit trail** a merge normally never leaves: an N-flow from input rows to final
rows with a reason for every dropped row, a subject × file coverage matrix, and a drafted
Methods paragraph. Use it before `statwise` / `table1` / `longistat`, not instead of them.

> **이 툴이 이기는 지점은 병합이 아니라 감사입니다.** `merged.csv`는 pandas도 만듭니다.
> pandas가 절대 주지 않는 것은 *"ID 미매칭 31, 중복 키 제거 18, 시점 윈도우 이탈 15"* 라는
> 문장과 그 근거가 되는 행 단위 목록입니다.

---

## 설치

```bash
cd ~/Downloads/02_프로젝트/깃헙/joinaudit-데이터병합감사
python3 -m pip install -e .
```

외부 의존성 **0개**(표준 라이브러리만), 네트워크 호출 **0회**. `.xlsx`는 `zipfile` +
`xml.etree`로 직접 읽으므로 pandas나 openpyxl이 필요 없습니다. Python 3.9+.

설치하지 않고 쓰려면 폴더 안에서 `python3 -m joinaudit.cli ...` 로 실행하면 됩니다.
또는 **`실행.command` 더블클릭**.

---

## 써 보기 — 번들 예제 (전부 합성 데이터)

### 1) 먼저 무엇을 키로 잡는지 확인 (`--inspect`)

자동 탐지가 엉뚱한 열을 키로 잡으면 병합은 "성공"하고 표는 틀립니다. 그래서 첫 실행은
항상 이것부터 하세요.

```bash
joinaudit examples/clean/watch_hrv.csv examples/clean/diary.xlsx examples/clean/isi.csv --inspect
```

```
● watch_hrv.csv
    행 160 · 열 5 · 인코딩 utf-8 · 구분자 ','
    헤더 행: 원본 1행
    피험자 키: subject_id — 열 이름 'subject_id' 으로 판단
      고유 ID 16개, 예: S01, S02, S03, S04, S05, S06 ...
    날짜 열: measured_at — 어느 해석으로 읽어도 같은 날짜입니다(ymd 로 확정)
      파싱 성공 160행 / 실패 0행 / 시각 포함 160행
    열 목록: subject_id, measured_at, rmssd_ms, sdnn_ms, mean_hr_bpm

● diary.xlsx
    행 160 · 열 6 · 인코딩 xlsx · 구분자 '-'
    피험자 키: 피험자번호 — 열 이름 '피험자번호' 으로 판단
    날짜 열: 날짜 — 어느 해석으로 읽어도 같은 날짜입니다(ymd 로 확정)

... (isi.csv 블록 생략)
```

*(위는 발췌입니다. 실제로는 파일마다 헤더 행 위치·고유 ID 예시·파싱 성공/실패 행수·전체
열 목록까지 나오고, 마지막에 `--inspect` 만 빼고 그대로 실행할 명령이 찍힙니다.)*

### 2) 깨끗한 자료 — 조용해야 정상입니다

```bash
joinaudit examples/clean/watch_hrv.csv examples/clean/diary.xlsx examples/clean/isi.csv \
  --align night --out-dir 결과_clean
```

```
[입력]
  watch_hrv.csv             160행  피험자 16명  키: subject_id (자동 탐지)  날짜열: measured_at (자동 탐지)
  diary.xlsx                160행  피험자 16명  키: 피험자번호 (자동 탐지)  날짜열: 날짜 (자동 탐지)
  isi.csv                    16행  피험자 16명  키: subject_id (자동 탐지)  시점열 없음 → 피험자 단위

[키 정규화] 제로패딩 정규화 189건 → 서로 다른 표기가 한 사람으로 합쳐진 경우는 없었습니다
           (위 건수는 내부 표준화 계산이며 병합 결과를 바꾸지 않았습니다)
[야간 귀속] 12:00 기준. 그 이전 시각의 타임스탬프는 전날 밤에 귀속했습니다(날짜 파일 2개).

[N 흐름]
  입력 행 합계...............................    336
  ---------------------------------------------
  병합에 기여한 입력 행..........................    336
  최종 표.................................. 피험자 16명 / 160행

[정보] 피험자단위파일 — 종료코드에 영향 없음

... (결과 파일 4개 목록 생략)

종료코드 0 (문제 없음).
```

`--align night` 가 없었다면 워치의 `2026-03-04 04:36` 은 3월 4일로 붙어 일기의 3월 3일과
어긋나고, 160행 중 상당수가 조용히 반쪽짜리가 됩니다. **깨끗한 자료에서 경고가 하나라도 뜨면
그건 툴의 잘못입니다** — 그 성질을 테스트가 지킵니다.

### 3) 결함을 심은 자료 — 여기서 값어치가 나옵니다

```bash
joinaudit examples/flawed/watch_hrv.csv examples/flawed/diary.xlsx examples/flawed/isi.csv \
  --align night --spec examples/flawed/spec.json --out-dir 결과_flawed
```

```
[키 정규화] 제로패딩 정규화 169건, 공백 제거 10건, 전각→반각 5건, 접두어 제거 16건(BELL-001-)
           → 서로 다른 표기가 한 사람으로 합쳐진 경우 **3건** (S11/s11, S03/S3, S05/Ｓ０５)

[!] 중복키 4건, 키겹침없음 2건, 중복키요약 1건, 범위이탈 1건, 키정규화충돌 1건
      심각: 두 파일에 공통으로 존재하는 피험자가 **한 명도 없습니다** (diary.xlsx: 17명 예 S1, S10, … …
      심각: watch_hrv.csv 의 2개 행이 같은 키를 가집니다(행 24, 25)
      심각: watch_hrv.csv 의 2개 행이 같은 키를 가집니다(행 28, 29)
      ... 전체 목록은 문제목록.csv
```

`문제목록.csv` 의 권고 칸이 다음에 뭘 할지까지 알려 줍니다:

> 두 파일의 ID 머리말이 각각 `'S'` / `''` 로 파일 안에서는 상수입니다. `--unify-id-heads` 를
> 붙이면 16명이 맞물립니다 — 다만 두 머리말이 서로 다른 코호트를 뜻하는 것은 아닌지 먼저
> 확인하세요.

시키는 대로 `--alias examples/flawed/alias.csv --unify-id-heads` 를 붙여 다시 돌리면
피험자가 **34명 → 17명**, 즉 세 파일이 실제로 맞물립니다. **툴은 스스로 붙이지 않았습니다** —
`S01..S16` 과 `C01..C16` 이 다른 코호트일 수도 있고, 그 판단은 사람 몫이기 때문입니다.

### 4) 타임존이 섞이면 멈춥니다 (종료코드 3)

```bash
joinaudit examples/flawed/watch_hrv.csv examples/flawed/respiration_tz.csv --align night
```

```
[!] 병합할 수 없습니다 — 추측해서 붙이지 않고 멈춥니다.

  · respiration_tz.csv: 'measured_at' 열에 서로 다른 시간대 표기가 섞여 있습니다(오프셋: +09:00, 오프셋 없는 행 24건)
    → 이 툴은 타임존을 변환하지 않습니다. 원본을 한 시간대로 통일한 뒤 다시 실행하세요.

먼저 `joinaudit <파일들> --inspect` 로 각 파일의 구조를 확인하세요.
```

파일**끼리** 시간대가 다른 경우(`+09:00` 파일과 `+00:00` 파일)도 같은 이유로 멈춥니다 —
각 파일은 일관돼 보이지만 같은 순간이 서로 다른 날짜로 귀속되기 때문입니다.

---

## 산출물 4종

| 파일 | 내용 |
|---|---|
| `merged.csv` | 분석용 표. 1행 = 피험자 × 시점. 열 이름은 `파일접두어_원본열`. `--long` 도 지원 |
| `병합감사.md` | N-흐름, 파일별 행 처분, 파일 간 교집합/차집합, 커버리지, 적용 규칙, **Methods 초안(한/영)** |
| `문제목록.csv` | `파일,행번호,키,심각도,유형,설명,권고` — 실제로 조치할 수 있는 형태 |
| `키매칭표.csv` | 피험자 × 파일 커버리지(1/0)와 빠진 파일 이름 |

`merged.csv` 는 만든 직후 스스로 다시 읽어 **하류 툴 투입 조건**(헤더 1줄, 열 이름 중복 없음,
모든 행의 열 수 동일, 결측은 빈 칸)을 검사합니다.

`--align night`/`date` 로 만든 표는 **1행 = 피험자 × 시점**입니다. 반복측정을 반복측정으로
다루는 툴에 바로 넣을 수 있습니다:

```bash
longistat 결과/merged.csv --id subject_id --time timepoint --value watch_hrv_rmssd_ms
```

> **주의 — 유사반복(pseudoreplication).** `statwise` 와 `table1` 은 **1행 = 1피험자**를
> 전제합니다. 위 시점별 표(16명 × 10밤 = 160행)를 그대로 넣으면 같은 사람의 10개 행이
> 서로 독립인 관측으로 취급되어 N이 80/80으로 부풀고 p값이 실제보다 작아집니다.
> **먼저 피험자당 한 행으로 요약한 뒤에** 넣으세요:
> ```bash
> # 예: 피험자당 평균을 낸 표를 만든 다음
> statwise 피험자별요약.csv --value 평균_총수면시간 --group 배정군
> table1   피험자별요약.csv --group 배정군
> ```
> joinaudit 은 이 요약을 대신 해 주지 않습니다 — 기저값이 맞는지, 평균이 맞는지,
> 변화량이 맞는지는 연구 질문에 달려 있기 때문입니다. `병합감사.md` §8 이 이 경고를
> 실행마다 다시 계산해 보여 줍니다.

---

## 주요 옵션

| 옵션 | 뜻 |
|---|---|
| `--align night\|visit\|date` | 시점 정렬 방식. **기본 `date`**. 워치·수면 자료처럼 타임스탬프가 자정을 넘기면 반드시 `night` 를 쓰세요(`--night-cutoff 12:00`) — `date` 로 두면 한 밤이 두 날짜로 갈라져 정상 자료가 중복 키로 잡힙니다(툴이 이 경우를 감지해 `--align night` 를 권합니다) |
| `--visit-label 파일=라벨` | 시점 열이 없고 **파일 하나가 곧 한 시점**인 자료(`설문_기저.csv` / `설문_4주.csv`)에 라벨을 붙입니다. `--align visit` 과 함께 |
| `--out-dir 폴더` | 결과 폴더. **기본 `결과`** — 지정하지 않으면 현재 폴더 아래 `결과/` 를 만듭니다 |
| `--dup-policy error\|first\|last\|mean` | 중복 키 처리. **기본 `error`** — 중복된 키를 통째로 제외하고 보고합니다(툴이 어느 행을 남길지 고르지 않습니다). 남기려면 정책을 명시하세요 |
| `--how outer\|inner\|left` · `--base 파일` | 조인 방식과 기준 파일 |
| `--tolerance-days N` | 기준 파일 시점과 ±N일까지 맞춤. 동률이면 **붙이지 않고 보고** |
| `--key/--date/--visit` | 자동 탐지 대신 열 이름 지정. `--key diary.xlsx=피험자번호` 처럼 파일별 지정 가능 |
| `--alias alias.csv` | 사람이 명시하는 ID 대응표 (`파일,원본ID,표준ID`) |
| `--spec spec.json` | 연구별 규칙 — `id_prefixes` / `visit_aliases` / `ranges` |
| `--unify-id-heads` | 파일 안에서 상수인 ID 머리말 제거(`S07`/`BELL-001-07`/`07` → `7`). **기본 꺼짐** |
| `--inspect` | 병합하지 않고 탐지 결과만 출력 |
| `--long` | EAV 형식 출력(`subject_id,timepoint,variable,value`). 사람이 훑어볼 때용이며 **`longistat` 에는 기본(wide) 출력을 쓰세요** |

**종료코드**: `0` 문제 없음 · `1` 실패 · `2` 경고 있음(병합은 됨) · `3` **병합 불가**.

---

## 한계 (솔직하게)

- **통계를 하지 않습니다.** 평균도 비교도 없습니다. 그건 `statwise`/`table1`/`longistat` 의
  몫이고, 이 툴의 출력이 그들의 입력입니다.
- **결측 대체(imputation)를 하지 않습니다.** 비어 있으면 비운 채로 넘깁니다.
- **퍼지 매칭을 하지 않습니다.** `S01` 과 `S02` 는 어떤 경우에도 붙지 않습니다. 규칙으로
  못 붙이는 ID는 `--alias` 로 사람이 적어야 합니다. 이건 기능 부족이 아니라 설계입니다 —
  임상 데이터에서 편집거리로 ID를 붙이는 건 조용히 틀리는 최악의 방법입니다.
- **`--unify-id-heads` 는 위험한 옵션입니다.** `S01..S16` 과 `C01..C16` 이 서로 다른 코호트인데
  이 옵션을 켜면 두 사람이 한 사람이 됩니다. 툴은 "이걸 켜면 몇 명이 맞물린다"까지만 알려 주고
  **켜지는 않습니다.** 켜기 전에 두 파일의 ID 체계가 정말 같은 사람을 가리키는지 확인하세요.
- **타임존을 변환하지 않습니다.** 단일 타임존 가정이고, 오프셋이 섞이면 보고하고 멈춥니다.
- **자동 단위 변환을 하지 않습니다.** 분↔시간, ms↔s 의심은 **정보로만** 알립니다.
- **신호 파일을 읽지 않습니다.** EDF·원시 EEG·PPG 파형은 대상 밖입니다(`eegband`/`hrvkit` 이
  요약한 CSV를 받습니다).
- **자동 탐지는 좁게 설계돼 있습니다.** 후보가 둘 이상이면 고르지 않고 종료코드 3으로 멈춥니다.
  귀찮게 느껴질 수 있지만, 틀린 표를 자신 있게 내놓는 것보다 낫습니다.
- **커버리지 매트릭스의 1/0 은 "그 파일에 그 피험자 행이 존재하는가"** 이며, 그 행이 실제로
  병합에 쓰였는지는 아닙니다(중복으로 빠졌을 수도 있습니다). 행 단위 처분은 `병합감사.md` 의
  "파일별 행 처분" 표를 보세요.
- **여러 시트 워크북은 첫 시트만** 읽습니다(`--sheet` 로 지정 가능). 구형 `.xls`는 읽지 않고
  재저장을 안내합니다. 암호가 걸린 워크북도 읽지 않습니다.
- **`--align` 기본값은 `date` 입니다.** 수면 타임스탬프를 그대로 넣으면 한 밤이 두 날짜로
  갈라져 정상 자료가 중복 키로 잡힙니다. 툴이 이 상황을 감지해 `--align night` 를 권하지만,
  **애초에 수면 자료라면 처음부터 `--align night` 를 쓰세요.**
- **피험자당 한 행으로 요약해 주지 않습니다.** 출력은 항상 피험자 × 시점 단위이므로,
  Table 1이나 군간 비교를 하려면 요약 단계를 직접 거쳐야 합니다(위 유사반복 주의 참고).
- **같은 스키마의 파일을 세로로 잇는(concat/stack) 모드가 없습니다.** 피험자별로 쪼개진
  워치 export 여러 개를 한 파일로 합치는 일은 이 툴의 범위 밖입니다 — 이 툴은 **서로 다른
  모달리티를 가로로 붙이는** 도구입니다. (다만 그런 입력을 넣으면 `키겹침없음` 으로
  잡아내기는 합니다.)
- **`--long` 출력은 EAV 형식**이며 `longistat` 에 그대로 들어가지 않습니다. `longistat` 에는
  기본(wide) 출력을 쓰세요.
- **엑셀 날짜 시리얼의 자정은 '시각 없음'과 구분되지 않습니다.** 엑셀이 정수 시리얼로 저장한
  날짜에는 시각 정보가 아예 없기 때문이며, 그런 행은 야간 귀속에서 날짜를 그대로 씁니다.
- **번들 예제는 전부 난수 기반 합성 데이터입니다.** 실제 환자 자료가 아닙니다.

## 테스트

```bash
python3 -m pytest -q      # 331개, 완전 오프라인
```

가장 중요한 두 테스트는 `tests/test_merge.py` 에 있습니다 —
**중복 키에서 카테시안 조인이 일어나지 않는다**(무작위 중복 폭탄 100회 포함)와
**병합에 기여한 행과 실제 근거 행이 정확히 일치한다**(`Ledger.verify`). 이 둘이 이 툴의
존재 이유입니다. `tests/test_hardening.py` 는 적대적 리뷰가 실제로 툴을 조용히 틀리게
만들었던 입력들의 회귀 테스트이고, `tests/test_flags.py` 는 옵션 하나하나가 정말로
결과를 바꾸는지 확인합니다.

**적대적 검증 기록은 `HARDENING.md`** 에 있습니다 — 독립 서브에이전트 4명이 약 2,000회
실행하며 찾은 결함 26건과 그 대응, 그리고 알면서 남겨 둔 것들입니다.

## 라이선스

MIT © 2026 hyeonjoong
