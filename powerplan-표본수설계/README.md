# powerplan — 임상연구 표본수·검정력 설계기

프로토콜/IRB/연구계획서에 그대로 붙일 수 있는 **표본수 계산**을 명령 한 줄로. 평균·ANOVA 계열은 비중심 t/F 분포로 정확히 계산하고, 탈락·군집설계·다중비교·기저값 보정(ANCOVA)까지 반영하며, 사전연구 CSV에서 효과크기를 직접 뽑아 **한국어·영어 프로토콜 문장**까지 만들어 줍니다. 외부 의존성 0.

## 목적 / Why this exists

연구를 시작할 때마다 "몇 명이 필요한가"에 답해야 합니다. IRB, 임상시험 프로토콜, 연구비 신청서에는 예외 없이 표본수 근거가 들어가고, 심사자는 α·검정력·효과크기·탈락률이 어디서 왔는지 묻습니다. 그런데 G*Power는 GUI라 재현 기록이 남지 않고, 온라인 계산기는 근거가 불투명하며, R 패키지는 설치·문법 부담이 있고 "분석 대상 n"과 "모집 n"을 나눠 보여주는 경우가 드뭅니다. 결과적으로 매번 손으로 다시 계산하고, 프로토콜 문장은 또 따로 씁니다. `powerplan`은 그 한 사이클(계산 → 검증 → 문장)을 한 번에 끝내고, 명령어 자체가 재현 가능한 기록이 되도록 만든 도구입니다. 특히 **기저값이 있으면 ANCOVA 보정으로 실제 필요한 표본수**를 계산하고(추적값만 비교하는 것보다 흔히 절반), 사전연구 데이터를 넣으면 **관측 효과크기의 신뢰구간 하한**으로 보수적 표본수를 기본값으로 제시합니다 — 소규모 파일럿의 효과크기를 그대로 믿어 본연구가 미검출로 끝나는 흔한 실패를 막기 위해서입니다.

Every study starts with "how many participants?", and every IRB submission, trial protocol and grant application must justify it. G*Power is a GUI that leaves no reproducible record, online calculators hide their assumptions, and sample-size packages rarely distinguish the **number to analyse** from the **number to enrol**. `powerplan` closes that loop in one command: it computes power exactly from the noncentral t/F distributions for means and ANOVA, applies the ANCOVA/change-score design factor when a baseline measurement exists, inflates for attrition, clustering and multiplicity, and emits ready-to-paste Korean and English protocol sentences. Given pilot data it plans on the **lower confidence bound** of the observed effect size by default — the conservative choice that prevents an underpowered main study. It is aimed at clinical/pharma researchers running insomnia-device and hearing-rehabilitation trials who need defensible numbers fast, offline, and reproducible from the command line itself.

## 설치

```bash
cd ~/Downloads/02_프로젝트/깃헙/powerplan-표본수설계
python3 -m pip install -e .
```

설치 없이도 동작합니다: `python3 -m powerplan.cli ...` (또는 `실행.command` 더블클릭)
`powerplan` 명령이 안 잡히면 `python3 -m powerplan.cli` 로 쓰세요.
(설치 자체는 setuptools를 내려받을 수 있으므로 인터넷이 필요할 수 있습니다. **실행은 완전히 오프라인**입니다.)

## 지원하는 설계

| 하위 명령 | 언제 쓰나 | 계산 방식 |
|---|---|---|
| `ttest2` | 두 독립군 평균 비교 (device vs sham) | **정확** 비중심 t · `--analysis ancova/change` 지원 |
| `paired` | 같은 사람 전후 비교 (baseline → 8주) | **정확** 비중심 t |
| `onesample` | 기준값·규준 대비 | **정확** 비중심 t |
| `prop2` | 두 군 반응률(반응자 비율) 비교 | 정규근사 z (Yates 연속성 보정 옵션) |
| `anova` | 3군 이상 평균 비교 | **정확** 비중심 F |
| `corr` | 두 변수 상관 (HRV × 수면지표) | Fisher z 근사 |
| `noninf` | "더 나쁘지 않다" (비열등성) | **정확** 비중심 t, 단측 |
| `equiv` | "같다" (동등성, TOST) | **정확** 두 단측검정 동시확률 |
| `icc` | 신뢰도 연구 (ICC 신뢰구간 폭) | Bonett 2002 근사 (정밀도 기준) |
| `loa` | Bland–Altman 일치한계 정밀도 | Bland & Altman 1999 근사 |
| `pilot` | 사전연구 CSV → 효과크기 → 표본수 | 위 계산 + d의 정확 신뢰구간 |

**옵션이 설계마다 다릅니다:**

- `ttest2` `paired` `onesample` `prop2` `corr` `pilot` → `--alpha` `--sides` `--power`(→표본수) `--n`(→검정력) `--dropout` `--cluster-size/--cluster-icc` `--comparisons/--alpha-method` `--sensitivity`
- `anova` `noninf` `equiv` → 위와 같지만 **`--sides` 없음** (검정 방향이 설계로 고정됩니다)
- `icc` `loa` → 정밀도 기준이라 `--alpha`만 받습니다 (`--power`/`--n`/`--dropout`/`--cluster-*`/`--comparisons` **없음** — 탈락은 직접 나눠서 보정하세요)
- 설계별 추가 옵션: `--ratio`(ttest2/prop2/noninf/equiv), `--analysis`/`--baseline-r`(ttest2), `--continuity`(prop2), `--bias-correct`(corr), `--lower-is-better`(noninf), `--means`(anova), `--groups`/`--filter`/`--baseline`/`--plan-on`/`--conf`/`--skip-invalid`(pilot)
- 모든 설계 공통: `--format text|md|json` `-o 파일` `--force`

전체 목록은 `powerplan <설계> --help`.

## 사용법

### 1) 표본수 구하기 (가장 흔한 용도)

```bash
$ powerplan ttest2 --d 0.5 --power 0.8 --dropout 0.15
```

```
──────────────────────────────────────────────────────────────────────────
 powerplan — 두 독립군 평균 비교  [ttest2]
──────────────────────────────────────────────────────────────────────────
 검정         : 독립표본 t 검정 (등분산 가정)
 유의수준     : α = 0.05 (양측)
 효과크기     : Cohen's d = 0.5 (중간/medium)

▶ 필요한 분석 표본수 : 군당 64명 (1군 64 + 2군 64) = 총 128명
  목표 검정력        : 80.0%  →  실제 달성 80.1%
▶ 모집 표본수        : 군당 76명 (1군 76 + 2군 76) = 총 152명  (탈락 15%)

■ 프로토콜용 문장 (그대로 붙여 쓰세요)
  [KR] 독립표본 t 검정 (등분산 가정), 유의수준 양측 α = 0.05, 목표 검정력
        80.0% 기준으로 Cohen's d = 0.5를 검출하려면 분석 대상 군당 64명
        (1군 64 + 2군 64) = 총 128명이 필요하다 (실제 검정력 80.1%).
        탈락을 고려해 군당 76명 (1군 76 + 2군 76) = 총 152명을 모집한다.
  [EN] A two-sample t-test (equal variances) with two-sided α of 0.05 and
        80% power requires 64 participants per group (128 in total) to
        detect Cohen's d = 0.5 (actual power 0.801). Allowing for 15%
        attrition, 76 participants per group (152 in total) will be
        enrolled.
...
```

효과크기를 모르면 **원래 단위로** 넣으면 됩니다 (ISI 3점 차이, SD 6점):

```bash
$ powerplan ttest2 --mean1 8 --mean2 5 --sd 6 --power 0.9 --sensitivity
```

`--sensitivity`는 "가정이 틀렸을 때"를 함께 보여줍니다:

```
■ 민감도 분석 (가정이 틀렸을 때 표본수가 어떻게 변하는가)
  목표 검정력           효과×0.8          효과×1        효과×1.2
  --------------------------------------------------------------
  70.0%                 79/총158        51/총102         36/총72
  80.0%                100/총200        64/총128         45/총90
  85.0%                114/총228        73/총146        51/총102
  90.0%                133/총266        86/총172        60/총120
  95.0%                164/총328       105/총210        74/총148
  (표기: 단위당 n / 총 N — 분석 대상 기준)
```

### 2) 기저값이 있으면 ANCOVA로 — 표본수가 절반으로

사전-사후 측정이 있는 연구(거의 전부)에서 추적값만 비교하는 것은 검정력을 버리는 일입니다. 기저값을 공변량으로 넣으면 분산이 (1 − r²)배로 줄어듭니다:

```bash
$ powerplan ttest2 --d 0.309 --power 0.8 --analysis ancova --baseline-r 0.711
```
```
 검정         : 공분산분석 ANCOVA (기저값을 공변량으로 보정)
 효과크기     : Cohen's d = 0.309 (작음/small · 설계배율 0.494 → 실질 d 0.439)

▶ 필요한 분석 표본수 : 군당 83명 (1군 83 + 2군 83) = 총 166명
```
같은 가정에서 추적값만 비교하면 군당 166명(총 332명)입니다 — **정확히 2배**. r은 사전연구에서 추정할 수 있습니다(아래 3번). `--analysis change`(변화량 분석)도 있으며, 배율 2(1−r)이라 **r > 0.5일 때만** 유리합니다.

### 3) 확보 가능한 표본수로 검정력 확인 (역방향)

```bash
$ powerplan ttest2 --d 0.5 --n 30 --power 0.8
```
```
▶ 주어진 표본수      : 군당 30명 (1군 30 + 2군 30) = 총 60명
▶ 검정력             : 47.8%
  목표 80.0% 대비 : 미달
  목표 달성에 필요   : 군당 64명 (1군 64 + 2군 64) = 총 128명
```

### 4) 사전연구 CSV → 효과크기 → 본연구 표본수

```bash
$ powerplan pilot examples/wowfit_pilot.csv --pre 훈련전_단어인지도 --post 훈련후_단어인지도 --filter 군=중재 --power 0.8
```
```
■ 사전연구에서 관측된 효과크기
  선택 조건: 군=중재 (제외된 행 11개)
  쌍 n=11  변화량 평균=10.3  변화량 SD=6.542  (불완전 쌍 1개 제외)
  Cohen's dz = 1.5743  (Hedges g = 1.4527)
  사전-사후 상관 r = 0.9395  → 두 군 비교를 계획한다면 --analysis ancova --baseline-r 0.940
  95% 신뢰구간(비중심 t 정확법): [0.6571, 2.4589]

■ 계획 기준: **신뢰구간 하한** 0.6571 (보수적, 기본값)
  참고) 관측값 1.5743을 그대로 쓰면 표본수가 6명으로 줄지만, 사전연구
  효과크기는 위로 편향되기 쉬워 위험합니다 (--plan-on observed).
```
헤드라인 표본수와 프로토콜 문장은 **보수적 기준(신뢰구간 하한)**으로 계산됩니다. `--filter 군=중재`를 빼면 중재군과 대조군의 전후 변화가 섞여 무의미한 효과크기가 나오니 주의하세요.

두 군 비교 + 기저값 상관·탈락률 추정:

```bash
$ powerplan pilot examples/serene_pilot.csv --value isi_week8 --group arm --baseline isi_baseline --power 0.8
```
```
  device               n=17  평균=11.02  SD=5.626  범위 0~19.7  결측=1
  sham                 n=15  평균=12.63  SD=4.75  범위 1.3~19.1  결측=1
  Cohen's d = -0.3086  (Hedges g = -0.3008)
  결측/탈락 2명 / 전체 34명 = 5.9% → 본연구 --dropout 참고값
  기저값('isi_baseline')-추적값 군내 상관 r = 0.7106  → ANCOVA 계획: --analysis ancova --baseline-r 0.711
  95% 신뢰구간(비중심 t 정확법): [-1.0047, 0.3926]

■ 계획 기준: 관측 효과크기 (⚠ 신뢰구간이 0을 포함)
  사전연구만으로는 효과크기를 확정할 수 없습니다. ...MCID를 직접 정해 ttest2/paired 로 다시 계산하세요.
```
(3군 이상이면 `--groups 군A,군B`로 지정)

### 5) 그 밖의 설계

```bash
powerplan prop2 --p1 0.30 --p2 0.50 --power 0.8            # 반응률 30%→50%: 군당 93명
powerplan anova --k 3 --means 8,6,5 --sd 6 --power 0.8     # 3군 비교
powerplan corr --r 0.35 --power 0.8                        # 상관 r=0.35: 62명
powerplan noninf --margin 3 --sd 8 --power 0.8 --lower-is-better   # 비열등성(ISI)
powerplan equiv --margin 5 --sd 8 --power 0.8              # 동등성(TOST)
powerplan icc --icc 0.8 --width 0.15 --raters 2            # 신뢰도 연구: 90명
powerplan loa --sd-diff 2.0 --half-width 0.5               # LoA 정밀도: 183명
powerplan ttest2 --d 0.4 --power 0.8 --cluster-size 10 --cluster-icc 0.05 --dropout 0.1
powerplan ttest2 --d 0.5 --power 0.8 --comparisons 3        # 다중비교 α 보정
powerplan ttest2 --d 0.5 --power 0.8 --format md -o 표본수.md
```

군집 무작위배정에서는 세 숫자를 구분해 보여줍니다:

```
  유효 표본수(개인배정 기준) : 군당 100명 = 총 200명
▶ 필요한 분석 표본수 : 군당 145명 = 총 290명        ← × 설계효과 1.450
▶ 모집 표본수        : 군당 170명 = 총 340명        ← ÷ (1−탈락률), 군집 단위로 올림
  군집 수            : n1: 17개, n2: 17개 (군집당 10명 — 위 모집 인원은 군집 단위로 올린 값)
```

## 계산의 정확도 — 무엇과 대조했나

- **비중심 t / 비중심 F를 직접 구현**했습니다(정규근사 아님). 비중심 t는 χ 분포에 대한 조건부 기대값을 Gauss–Legendre 복합구적으로, 비중심 F는 Poisson 가중 불완전베타 급수로 계산합니다. mpmath 40자리 기준값과 **상대오차 1e-11 이하**로 일치합니다(기준값을 테스트에 하드코딩 — scipy 없이도 검증됩니다).
- **G*Power 3.1 값과 일치**: d=0.5→군당 64명, d=0.8→26명, d=0.2→394명, 검정력 90%→86명, 대응표본 dz=0.5→34명, ANOVA k=3 f=0.25→군당 53명(총 159), 비율 .30 vs .50→군당 93명 등 16개 사례를 테스트로 고정.
- **몬테카를로 교차검증**: 비중심 t와 TOST 검정력을 6만 회 시뮬레이션 기각률과 4 표준오차 이내로 대조(고정 시드, 오프라인).
- **검정의 크기(1종오류율) 확인**: 효과크기를 0에 가깝게(1e-9) 두면 8개 설계 모두에서 기각률이 α와 2e-3 이내로 일치합니다(양측·단측 각각).
- **ANCOVA 설계배율**은 Frison & Pocock(1992) / Borm et al.(2007)의 (1−r²) 공식과 ±2명 이내로 일치.

## Notes / 한계 (정직하게)

- **"정확"이라고 쓴 것은 평균·ANOVA 계열(t·F 검정)뿐입니다.** `prop2`는 정규근사 z, `corr`는 Fisher z, `icc`·`loa`는 근사식입니다.
- `corr`의 Fisher z 근사는 **정확법보다 0~1명 크게** 나옵니다(계획 단계에서는 보수적이라 안전). 심사자의 G*Power 값과 맞추려면 `--bias-correct`를 쓰세요.
- `prop2`는 정규근사 z 검정 기준입니다. **Fisher 정확검정**을 쓸 계획이면 5~15% 여유를 두세요. 기대 사건수(n·p)가 군당 5 미만이면 정규근사가 깨집니다. `--continuity`는 검정통계량에 Yates 보정을 적용한 것이며, 표본수를 직접 부풀리는 Casagrande–Pike–Smith 공식과는 몇 명 차이가 날 수 있습니다.
- `ttest2`는 **등분산**을 가정합니다(Welch를 쓸 계획이면 약 5% 여유). 비모수 검정(Mann–Whitney)을 쓸 계획이면 10% 정도 여유를 두세요.
- `anova`의 표본수는 **전체 F 검정** 기준입니다. 특정 두 군 사후비교를 확실히 검출하려면 보정된 α로 `ttest2`를 따로 계산하세요.
- ANCOVA 배율은 **기저값 공변량 1개**를 가정합니다(자유도 1 차감). r을 낙관적으로 잡으면 표본수가 과소해집니다.
- 군집설계는 설계효과(DE = 1 + (m−1)·ICC) 보정 방식입니다. 군집 수가 적으면(군당 10개 미만) 혼합효과모형 기반 계산이 더 적절합니다.
- `icc`(Bonett)·`loa`(Bland–Altman)는 **검정력이 아니라 정밀도(신뢰구간 폭) 기준**이며, 근사식이라 정확법과 몇 명 정도 차이가 날 수 있습니다.
- **아직 없는 설계**: 이분형 결과의 비열등성·동등성, 일표본 비율, 대응 비율(McNemar), k군 비율 비교(χ²), 발생률(Poisson), 생존분석(로그순위), 반복측정 ANOVA·혼합효과모형(MMRM), 회귀 다중예측자, 진단정확도(민감도·특이도·AUC), 범주형 일치도(Cohen's kappa), 중간분석(군차별설계·α 소비함수). **센서 검증 연구에서 결과가 범주형(예: 수면단계)이면 `icc`/`loa`가 아니라 kappa 기반 계산이 필요하며 이 툴은 제공하지 않습니다.**
- 마진(비열등성·동등성)과 "임상적으로 의미있는 차이"는 통계가 아니라 **임상적 판단**입니다. 이 툴은 계산만 해 줍니다. 3상 확증시험이라면 검정력 0.80보다 0.90을 권합니다.
- 사전연구 기반 계획의 하한 신뢰수준은 `--conf`로 조절합니다. 문헌에서는 60~80% 하한 사용을 제안하는 경우가 많습니다(Browne 1995; Kieser & Wassmer 1996) — 기본 0.95는 가장 보수적입니다.
- 수치 신뢰 범위: α ≥ 1e-9, 검정력 ≤ 1−1e-10, 자유도 ≤ 1e12. 그 밖(예: `--power 0.99999999999999`)에서는 표본수가 1~3명 흔들릴 수 있습니다. 자유도 2 이하 + 극단적 임계값 조합에서는 비중심 t의 절대오차가 최대 ~1e-3까지 커질 수 있습니다(표본수 결과는 바뀌지 않음).
- **CSV/데이터 취급**: 네트워크 접속이 전혀 없습니다(코드에 소켓/HTTP 계열 import이 없음). CSV는 앞 64KB만 읽어 인코딩·구분자를 판별한 뒤 **스트리밍으로 한 줄씩** 처리하므로 파일이 커도 메모리는 수십 MB 수준입니다(100만 행 4초, 25MB). 인코딩은 utf-8·한국어 엑셀(cp949)을 자동 인식하며, 그 밖의 인코딩(예: Shift-JIS)은 오류 없이 깨진 글자로 읽힐 수 있으니 열 이름이 이상하면 UTF-8로 다시 저장하세요. 결과 저장은 `-o`로 지정한 파일에만 쓰고, 권한 0600·심볼릭 링크 미추적·기존 파일은 `--force` 없이 덮어쓰지 않습니다. 저장물에는 군 라벨·요약통계·관측 범위가 들어가며, 숫자가 아닌 원본 값은 남기지 않습니다.
- 번들 예제(`examples/*.csv`)는 **합성 데이터**입니다 — 실제 환자 자료가 아닙니다([examples/README.md](examples/README.md)).

## 개발

```bash
python3 -m pytest tests/ -q     # 313개 테스트, 완전 오프라인
```

적대적 검토 이력(무엇을 지적받아 어떻게 고쳤는지)은 [HARDENING.md](HARDENING.md)에 있습니다.

MIT License · 한글 안내: [사용법.md](사용법.md)
