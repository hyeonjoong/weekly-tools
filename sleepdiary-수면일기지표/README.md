# sleepdiary — 수면일기 지표 계산기

손으로 쓴 **수면일기 CSV를 넣으면**, Consensus Sleep Diary 표준 정의대로
TST·수면효율(SE)·입면잠복기(SOL)·중도각성(WASO)·수면중앙시각을 계산하고,
**대상자별 → 집단** 2단계로 집계해 시기 간 변화를 대응표본으로 검정합니다.
외부 라이브러리 없이 **표준 라이브러리만**으로 동작합니다.

```bash
python3 -m sleepdiary.cli examples/sleep_diary_trial.csv \
    --compare-periods baseline followup
```

```
  지표                       n            평균         SD        중앙값  95% CI (평균)
  총수면시간 TST            12  355분 (5h 55m)       23.7  355분 (5h 55m)  [340.5, 370.5]
  수면효율 SE               12           74.6%        5.9         75.0%  [70.9, 78.4]
  입면잠복기 SOL            12   58분 (0h 58m)       22.7  55분 (0h 55m)  [43.2, 72.0]
  …
  수면중앙시각              12           03:37       50분    (원형평균)

  ● 수면효율 SE  (짝 n=12명)
      baseline: 74.6%   →   followup: 81.9%
      변화량 : +7.2%p   95% CI [5.3, 9.1]
      대응 t검정 : t(11) = 8.428, p < 0.0001, Cohen's dz = 2.433
      Wilcoxon   : W = 0.0, p = 0.0005  [정확분포, n=12, 0인 차이 0건 제외]
```

---

## 목적 / Why this exists

**한국어.** 불면증 임상시험·수면 관련 관찰연구·CBT-I 효과 검증에서 수면일기는
가장 흔한 1차 결과변수입니다. 그런데 일기를 엑셀로 옮긴 뒤 지표를 계산하는
단계에서 **거의 항상 같은 실수들이 반복됩니다.**

- **자정을 넘는 시각.** `07:00 − 23:00 = −16시간`. 엑셀 수식으로 계산하다
  음수가 나와 절댓값을 씌우거나, 24를 더하는 보정을 일부 행에서만 빠뜨립니다.
  이 도구는 모든 구간을 시계 방향 경과시간으로 계산합니다.
- **취침 시각의 평균.** 23:50과 00:10의 평균은 12:00이 아닙니다. 취침·기상·
  수면중앙시각은 **원형(circular) 평균**으로 계산해야 하며, 산술평균을 쓰면
  야간형 대상자가 섞인 집단에서 결과가 통째로 뒤집힙니다.
- **분석 단위.** 12명이 각 7박을 적으면 84행이지만 통계의 n은 **12**입니다.
  84를 n으로 쓰면 표준오차가 √7배 작아지고 p값이 가짜로 유의해집니다. 이
  도구는 **항상 사람별 평균을 먼저 구한 뒤 사람 사이에서** 요약합니다.
- **말이 안 되는 밤을 조용히 통과시키는 것.** SPT가 TIB보다 길거나, SE가
  100%를 넘거나, 입면잠복기가 음수인 행은 계산하지 않고 **몇 행에서 왜
  제외했는지 보고서 첫머리에 적습니다.** 이상하지만 가능한 값(예: SOL 250분)은
  제외하지 않고 **경고만 붙여 집계에 포함**합니다 — 이상치를 조용히 지우는 것도
  똑같이 나쁜 관행이기 때문입니다.

**English.** `sleepdiary` turns a hand-kept sleep-diary CSV into the standard
Consensus Sleep Diary metrics (TIB, SPT, TST, SOL, WASO, terminal wakefulness,
sleep efficiency, sleep-onset time, mid-sleep time), aggregates them
**night → subject → group** so the analysis unit is the person rather than the
night, uses **circular statistics** for clock-valued metrics that cross
midnight, and compares two periods with a paired t-test plus Wilcoxon
signed-rank. It refuses to compute impossible nights and tells you exactly which
rows it dropped and why.

**누구에게 쓸모 있나 / Who it's for.** 불면증·수면 임상시험을 굴리는 연구자,
CBT-I·약물 전후 비교를 보고해야 하는 대학원생, 수면일기 데이터를 정리해야 하는
CRA/데이터매니저. Clinical and pharma researchers who must clean, compute, and
report sleep-diary outcomes.

---

## 무엇을 계산하나 (정의)

| 지표 | 정의 | 비고 |
|---|---|---|
| **TIB** (Time in Bed) | 잠자리에 든 시각 → 잠자리에서 나온 시각 | 분모 |
| **SPT** (수면기회시간) | 소등 시각 → 최종 기상 시각 | 관례적 SPT(입면→최종기상)와 **다릅니다** — 이 값은 SOL을 포함합니다 |
| **TST** (Total Sleep Time) | SPT − SOL − WASO | |
| **SOL** (Sleep Onset Latency) | 일기에 적힌 입면잠복기 | 결측이면 0으로 취급 |
| **WASO** | 일기에 적힌 중도각성 시간 | 결측이면 0으로 취급 |
| **TWAK** (Terminal Wakefulness) | 최종 기상 → 잠자리에서 나옴 | |
| **SE** (Sleep Efficiency) | TST / TIB × 100 | |
| **입면시각** | 소등 시각 + SOL | 원형 |
| **수면중앙시각** (mid-sleep) | 입면시각과 최종기상 시각의 중점 | 원형, **수면 타이밍** 지표 (크로노타입 지표 MSFsc가 아닙니다 — 그것은 자유일 한정·수면부채 보정이 필요합니다) |
| **규칙성** | 그 사람의 수면중앙시각 **원형 표준편차(분)** | 작을수록 규칙적. Sleep Regularity Index(SRI)가 **아니며**, 밤 수가 적으면 매우 불안정합니다 |

**문항** 정의는 Consensus Sleep Diary(Carney CE et al., *Sleep* 2012;35(2):287–302)를
따랐고, **파생지표 산식은 이 도구의 운용적 정의**입니다. CSD는 일기 문항을
표준화한 합의 문서이며 원 논문 스스로 "추가 검증이 필요한 living document"라고
밝히고 있습니다 — 파생변수 계산법을 정해 주는 문서가 아닙니다. SE의 분모로
TIB를 쓰는지 SPT를 쓰는지는 문헌마다 갈리는데, 이 도구는 **TIB**를 씁니다.
CSD의 해당 문항은 엄밀히는 "소등"이 아니라 **"잠을 자려고 시도한 시각"**입니다.

---

## 정직한 한계 (읽고 쓰세요)

- **자기보고 자료입니다.** 수면일기의 TST/SOL은 수면다원검사(PSG)나
  액티그래피와 체계적으로 다릅니다(불면증군은 대개 TST를 과소, SOL을 과대
  보고). 이 도구는 그 차이를 **보정하지 않으며 보정할 수도 없습니다.**
- **결측 SOL/WASO를 0으로 채웁니다.** 이는 "적지 않았다 = 없었다"는 가정이고,
  **TST와 수면효율을 낙관적으로** 만듭니다. 채운 밤은 숨기지 않습니다: 보고서
  "자료 품질"에 몇 박을 채웠는지 적히고, 밤별 CSV의 `imputed` 열과 JSON의
  `nights[].imputed`에 밤 단위로 표시되며, **SOL/WASO 자체의 평균·CI에서는 그
  밤들을 뺍니다**(측정한 적 없는 값에 "평균 0분, CI [0,0]"을 붙이지 않기 위해).
  다만 TST/SE에는 0으로 들어갑니다.
- **낮잠은 포함되지 않습니다.** 야간 수면만 계산합니다(CSD-Expanded의 낮잠
  문항은 지원하지 않습니다). 24시간 총수면시간을 보고해야 한다면 따로 처리하세요.
- **완전자료(complete-case) 분석입니다.** `--compare-periods`는 두 시기를 모두
  기록한 대상자만 씁니다. ITT가 아니며, `--min-nights` 제외도 사후 배제입니다.
- **집계는 대상자마다 같은 가중치**입니다. 3박 쓴 사람과 14박 쓴 사람이 집단
  평균에 똑같이 기여합니다(그래서 `--min-nights`가 필요합니다).
- **대상자별 SE는 밤별 SE의 산술평균**이며 ΣTST/ΣTIB가 아닙니다. 밤마다 TIB가
  크게 다르면 두 값이 달라집니다.
- **시각 지표의 차이는 ±12시간에서 감깁니다.** 6시간을 넘는 위상 이동이 있으면
  부호를 신뢰할 수 없어 검정을 생략하고 경고합니다. 또한 그 차이에 대한 t검정과
  CI는 선형 통계로 계산합니다(원형 자료의 근사입니다).
- **다중비교 보정이 없습니다.** `--compare-periods`는 12개 지표를 한꺼번에
  검정합니다. 주 지표를 사전에 정하거나 보정된 p를 별도로 계산하세요.
- **시기가 3개 이상이면 두 개씩만 비교합니다.** 반복측정 ANOVA나 혼합효과
  모형은 제공하지 않습니다(그런 분석은 `longistat` 같은 도구나 R/SAS를 쓰세요).
- **결측 밤을 대체(imputation)하지 않습니다.** 순응도가 낮은 대상자는
  `--min-nights`로 걸러내되, 걸러냈다는 사실을 논문에 적어야 합니다.
- **MM/DD/YYYY 형식 날짜를 해석하지 않습니다.** DD/MM과 구별할 수 없기
  때문에 일부러 비워 둡니다(날짜는 요약에만 쓰이고 지표 계산에는 쓰이지
  않으므로 결과에는 영향이 없습니다). ISO 형식(`2026-03-04`)을 쓰세요.
- **통계량은 기술적입니다.** 인과 해석, 결측 메커니즘 진단, 순응도 모형은
  이 도구의 범위 밖입니다. 임상적 판정(예: "SE 85% 미만이면 불면증")도 하지
  않습니다 — 진단은 임상의의 몫입니다.
- **독립 2군 비교는 하지 않습니다.** 대응표본(같은 사람의 전후)만 다룹니다.
- **예제 CSV는 전부 합성 데이터**이며 실제 환자 정보가 아닙니다.

---

## 설치 없이 실행하기

Python 3.9 이상이면 됩니다. 설치·의존성 없음.

```bash
cd sleepdiary-수면일기지표
python3 -m sleepdiary.cli examples/sleep_diary_trial.csv
```

macOS에서는 **`실행.command`를 더블클릭**하면 예제 8가지가 순서대로 돌아갑니다.

패키지로 설치하려면 (선택):

```bash
pip install -e .        # 이후 `sleepdiary` 명령으로 실행
```

---

## 사용법

```bash
# 0) 내 파일의 열이 어떻게 인식되는지 먼저 확인
python3 -m sleepdiary.cli 내일기.csv --list-columns

# 1) 기본 분석
python3 -m sleepdiary.cli 내일기.csv

# 2) 시기 비교 (차이 = 나중 − 먼저)
python3 -m sleepdiary.cli 내일기.csv --compare-periods baseline followup

# 3) 열 자동인식이 틀렸을 때 직접 지정
python3 -m sleepdiary.cli 내일기.csv \
    --subject 환자번호 --lights-off 소등 --sol 잠들기까지 \
    --waso 깬시간 --final-awake 기상 --out-of-bed 침대밖

# 4) 순응도가 낮은 대상자 제외 + 산출물 저장
python3 -m sleepdiary.cli 내일기.csv --min-nights 5 \
    --per-night-csv 밤별.csv --per-subject-csv 대상자별.csv --json 결과.json

# 5) 논문·발표용 마크다운 표
python3 -m sleepdiary.cli 내일기.csv --markdown
```

전체 옵션은 `python3 -m sleepdiary.cli --help`, 단계별 안내는
[사용법.md](사용법.md)를 보세요.

---

## 입력 CSV 형식

한 행 = 한 밤. 열 이름은 한글/영문 모두 자동 인식하며, 단위 꼬리표
(`sleep_latency_min`, `waso_min`)도 인식합니다.

```csv
subject_id,period,diary_date,bedtime,lights_off,sleep_latency_min,waso_min,n_awakenings,final_awake,out_of_bed
S01,baseline,2026-03-03,21:58,22:08,73,106,3,06:46,06:51
```

- **필수**: 소등(또는 취침) 시각, 그리고 최종기상(또는 잠자리에서 나온) 시각.
- **열 대체와 그 대가**: 취침 시각이 없으면 소등 시각으로, 잠자리에서 나온 시각이
  없으면 최종기상 시각으로 대신합니다. **그러면 TIB가 짧아져 수면효율(SE)이
  체계적으로 높게 나옵니다** (예제 자료 실측: 82.5% → 86.8%). 보고서의 열 매핑 표에서
  두 항목이 같은 열을 가리키는지 확인하세요.
- **선택**: 대상자, 날짜, 시기, SOL, WASO, 각성횟수.
- **⚠ 시기(period) 자동인식 주의**: `period`, `visit`, `차수` 외에 `group`,
  `arm`, `condition`, `군`도 "시기"로 인식합니다. **병렬군 시험**에서 `group`
  열이 있으면 집단 요약이 군별로 쪼개지고 "시기"로 표시되며, 같은 사람이 양쪽에
  없으므로 `--compare-periods`의 짝이 0이 됩니다. 그럴 땐 `--ignore-period`를
  쓰세요. 이 도구는 **대응표본(전후 비교)** 전용이며 독립 2군 비교는 하지 않습니다.
- **시각 표기**: `23:15`, `11:15 PM`, `오후 11시 15분`, `2315`, `2026-03-01 23:15`
  모두 읽습니다.
- **소요시간 표기**: `45`, `45분`, `1:05`, `1h20m`, `1.5시간`.
- **인코딩/구분자**: UTF-8(BOM 포함)·CP949·EUC-KR·UTF-16, `,` `;` `\t` `|` 자동 판별.
  판별에 실패하면 마지막 수단으로 latin-1을 쓰는데, 이 경우 보고서 머리에
  경고가 붙습니다 — 한글이 깨져 보이면 'CSV UTF-8'로 다시 저장하세요.
- **날짜의 의미**: 기본은 "기상한 아침"(`--date-means morning`). 저녁 기준으로
  적었다면 `--date-means evening`.

같은 지표에 후보 열이 둘 이상이면 **추측하지 않고 오류를 냅니다.** 잘못
매핑된 열로 계산된 수면효율이 조용히 논문에 들어가는 것보다 낫기 때문입니다.

---

## 출력

- **텍스트 보고서**(기본): 열 매핑 → 자료 품질(제외·경고) → 대상자별 표 →
  집단 요약 → 시기 비교 → 논문용 문장 초안.
- `--markdown`: 표만 마크다운으로.
- `--json 파일`: 밤별·대상자별·집단·비교 결과 전체 + 한계 문구.
- `--per-night-csv 파일`: 밤별 계산 결과(제외된 밤과 사유 포함).
- `--per-subject-csv 파일`: 대상자별 요약.

CSV로 쓸 때 `=`, `+`, `-`, `@`, 탭, CR로 시작하는(앞 공백은 무시) 문자열 값 앞에는
`'`를 붙여 엑셀 수식 주입을 막습니다. 진짜 음수(`-15`)는 그대로 둡니다.

---

## 테스트

```bash
python3 -m pytest -q      # 234개 테스트
```

t분포·Wilcoxon(정확분포·동점 보정 정규근사)·분위수 값은 scipy/numpy에서 뽑은
참조값과 대조합니다(참조값은 하드코딩되어 있어 실행에는 scipy가 필요 없습니다).

---

## 라이선스

MIT.
