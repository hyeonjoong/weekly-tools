# longistat — 반복측정 추이 분석기 (longitudinal outcome analyser)

같은 사람을 여러 번 측정한 CSV 하나로 **반복측정/혼합 ANOVA · 구형성 보정 · 사후비교 ·
기저 대비 변화량 · 기저값 보정(ANCOVA) · MCID 반응자 분석 · 신뢰변화지수(RCI) ·
논문용 한/영 문장**까지 한 번에. 외부 의존성 0 (Python 3.9+ 표준 라이브러리만).

---

## 목적 / Why this exists

**한국어.** 임상시험 결과를 정리할 때 가장 자주 하는 작업은 "같은 환자를 기저·4주·8주에
측정한 점수가 시간에 따라 달라졌는가, 그리고 그 변화가 군에 따라 다른가"를 보는 것입니다.
그런데 이걸 제대로 하려면 구형성(Mauchly) 점검과 Greenhouse–Geisser/Huynh–Feldt 보정,
결측·탈락 처리, 다중비교 보정, 효과크기, 그리고 리뷰어가 반드시 요구하는
**기저 대비 변화량의 군간 차이와 95% CI**, **MCID 이상 좋아진 환자 비율**까지
따로따로 챙겨야 해서, SPSS 화면을 여러 번 오가며 손으로 옮겨 적게 됩니다.
longistat은 이 과정을 CSV 한 개 → 리포트 한 장으로 줄입니다.
BELL-001(수면/불면 디바이스, ISI·수면일기)과 002 와우핏(난청 재활 훈련 결과)처럼
**전·후 반복측정이 기본인 우리 데이터**를 그대로 넣도록 만들었습니다.

**English.** The workhorse analysis for a repeated-measures trial — "did the score
change over visits, and did it change differently between arms?" — is tedious to do
correctly. It needs a sphericity check with the right ε correction, an explicit
account of dropout, multiplicity control, effect sizes, and the two numbers
reviewers always ask for: the **between-arm difference in change from baseline with
its confidence interval**, and the **proportion of patients improving by at least the
MCID**. longistat produces all of that from one CSV, in one report, with no external
dependencies, so a clinical researcher can go from an export to a paste-ready
results paragraph without moving numbers by hand between SPSS windows.

**누구에게 / who it's for.** 임상·제약 연구자, 특히 전-후 또는 다시점 설계를 다루는 사람.
**언제 쓰나 / when to reach for it.** 방문(visit)이 2회 이상인 결과지표를 정리할 때.

### 이 도구가 맞나요? (자매 도구와의 경계)

| 내 자료 / 하고 싶은 것 | 쓸 도구 |
|---|---|
| 시점이 **1개**, 군만 비교 | [`statwise-그룹비교통계`](../statwise-그룹비교통계) |
| 시점이 **2개**인데 전/후 검정만 필요 | `statwise --paired` (더 간단) |
| 시점이 **2개**이고 반응자·RCI·군×시점까지 | **longistat** |
| 시점이 **3개 이상** | **longistat** |
| **여러 결과지표**를 한 번에 (지표 간 다중비교 보정 포함) | `statwise --values a,b,c` — longistat은 한 번에 지표 1개 |
| 설계 단계의 **표본수 계산** | [`powerplan-표본수설계`](../powerplan-표본수설계) |
| 출판용 **기저 특성표(Table 1)** | [`table1-기저특성표`](../table1-기저특성표) |

---

## 설치

```bash
cd ~/Downloads/02_프로젝트/깃헙/longistat-반복측정추이분석
python3 -m pip install -e .
```

설치가 어려우면 폴더 안에서 `python3 -m longistat.cli ...` 로 바로 실행해도 됩니다.
가장 쉬운 방법은 **`실행.command` 더블클릭** — 번들된 예제로 리포트를 보여줍니다.

---

## 사용법

### 긴(long) 형식 — 한 행 = 한 대상 × 한 시점

```bash
longistat examples/isi_serene_예시.csv \
  --id 대상 --time 방문 --value ISI --group 군 \
  --time-order 기저,4주,8주 \
  --mcid 6 --direction lower \
  --reliability 0.9 --recovery-cutoff 7
```

### 넓은(wide) 형식 — 한 행 = 한 대상, 시점마다 열

```bash
longistat examples/와우핏_단어인지도_wide예시.csv \
  --wide --id 환자 --columns 기저,4주,8주,12주 \
  --mcid 10 --direction higher
```

### 실제 출력 (아래 명령을 그대로 실행한 결과에서 일부 구획만 발췌 — 편집하지 않았습니다)

```bash
longistat examples/isi_serene_예시.csv \
  --id 대상 --time 방문 --value ISI --group 군 \
  --time-order 기저,4주,8주 --primary-time 8주 \
  --mcid 6 --direction lower --brief
```

```
대상 수 N = 48   시점 3개: 기저 → 4주 → 8주   기준시점: 기저
그룹: 능동자극 (n=24), 가짜자극 (n=24)
측정변수: ISI   유의수준 α = 0.05
사전 지정 주요 시점: 8주 (다중비교 보정 제외)

[1] 결측 · 탈락 (CONSORT 흐름에 넣을 숫자)
  완전자료(모든 시점 관측) 38/48명 (79%) · 결측 패턴 비단조(중간 누락 포함)
  범위      배정 n  기저  4주  8주  완전자료
  ────────  ──────  ────  ───  ───  ────────
  전체          48    48   47   39  38 (79%)
  능동자극      24    24   23   19  18 (75%)

[3] 가정 점검
  · 구형성 (Mauchly): W = 0.895, χ²(2) = 3.89, p = .143  → 위배 근거 없음
    ε: Greenhouse–Geisser .905, Huynh–Feldt .976, 하한 .500  → 적용: 보정 없음
  · 권장 분석: 모수(ANOVA / t-검정) — Shapiro–Wilk(Holm 보정)에서 정규성 위배 근거가 없습니다.

[4] 주 분석 (omnibus)
  ※ 아래 ANOVA와 변화량은 모든 시점이 관측된 대상만 쓰는 완전사례(completer) 분석입니다 — ITT 주분석이 아닙니다.
  반복측정/혼합 ANOVA (완전자료 N = 38, 시점 내 보정: 보정 없음)
    효과             SS     df      F          p   ηp²   η²G
    ───────────  ──────  ─────  ─────  ─────────  ────  ────
    그룹(집단)    98.44  1, 36   2.05       .161  .054  .043
    시점(시간)   627.56  2, 72  46.76  <.001 ***  .565  .221
    그룹 × 시점  193.25  2, 72  14.40  <.001 ***  .286  .080
    · 시점 주효과는 Type III(그룹 비가중 평균) 기준입니다.
    · 그룹 주효과는 기저 시점까지 평균한 값이라 무작위배정 시험에서는 해석 가치가 낮습니다 — 상호작용을 보세요.

[5] 기준시점(기저) 대비 변화량 — 모수(ANOVA / t-검정)
  군간 변화량 차이 (임상시험의 통상적 주요 추정치)
    시점        대비                     n  변화A  변화B   차이          95% CI     보정 p     효과크기 [95% CI]
    ──────────  ───────────────────  ─────  ─────  ─────  ─────  ──────────────  ─────────  ────────────────────
    4주         능동자극 − 가짜자극  23/24  -5.48  -3.33  -2.14  [-4.18, -0.11]     .039 *  -0.61 [-1.19, -0.02]
    8주 (주요)  능동자극 − 가짜자극  19/20  -8.42  -2.20  -6.22  [-8.85, -3.59]  <.001 ***  -1.51 [-2.22, -0.79]


  기저값 보정 (ANCOVA) — 기저 불균형·평균회귀에 강건하고 대개 검정력이 더 높습니다
    시점        대비                     n  조정평균차          95% CI     보정 p  비보정 차이  기저 기울기
    ──────────  ───────────────────  ─────  ──────────  ──────────────  ─────────  ───────────  ───────────
    4주         능동자극 − 가짜자극  23/24       -1.48   [-3.27, 0.31]       .103        -2.14         0.61
    8주 (주요)  능동자극 − 가짜자극  19/20       -5.82  [-8.21, -3.44]  <.001 ***        -6.22         0.62

[8] 반응자 분석 (MCID 6.00점(ISI) 이상, 낮을수록 호전 · 관측 완료자 기준)
  그룹      시점  반응자/n  반응률          95% CI
  ────────  ────  ────────  ──────  ──────────────
  전체      4주      15/47   31.9%  [20.4%, 46.2%]
  전체      8주      16/39   41.0%  [27.1%, 56.6%]
  능동자극  4주       9/23   39.1%  [22.2%, 59.2%]
  능동자극  8주      13/19   68.4%  [46.0%, 84.6%]
  가짜자극  4주       6/24   25.0%  [12.0%, 44.9%]
  가짜자극  8주       3/20   15.0%   [5.2%, 36.0%]

[10] 논문용 문장 (그대로 붙여쓰고 숫자만 확인하세요)
  [KO] 그룹(집단) 효과는 유의하지 않았다, F(1, 36) = 2.05, p = .161, ηp² = .054.
  [EN] There was no significant effect of group, F(1, 36) = 2.05, p = .161, ηp² = .054.
```

(예제 데이터는 실제 임상자료가 아니라 형식을 보여주는 합성 자료입니다.)

### JSON / CSV 로 내보내기

```bash
# 논문/Word 에 붙여넣을 표
longistat data.csv --id id --time visit --value score --format md   -o result.md
# 엑셀에서 열기 (모수·비모수 두 트랙 모두 track 열로 구분해 담김)
longistat data.csv --id id --time visit --value score --format csv  -o result.csv
# 다른 스크립트에서 재사용
longistat data.csv --id id --time visit --value score --format json -o result.json
```

같은 이름의 파일이 이미 있으면 안전을 위해 저장하지 않습니다 — 덮어쓰려면
`--overwrite` 를 붙이세요.

---

## 무엇을 계산하나

| 구획 | 내용 |
|------|------|
| 결측·탈락 | **군별 × 시점별** 관측 수(CONSORT 흐름 숫자), 완전자료 비율, 결측 패턴이 단조(탈락)인지 비단조인지, 차등 탈락 경고 |
| 기술통계 | 그룹 × 시점별 n / 평균 / SD / 95% CI / 중앙값 [IQR] |
| 가정 점검 | Shapiro–Wilk(군내 중심화 잔차 + 변화량 잔차, Holm 보정), Mauchly 구형성(2차항 포함), GG·HF ε |
| omnibus | 반복측정 ANOVA (1군) 또는 혼합 split-plot ANOVA (군 지정 시): 그룹·시점·상호작용, ηp²·일반화 η² |
| 순위 기반 | Friedman + Kendall's W (전체 및 군별) — 항상 교차확인으로 함께 계산 |
| 사후비교 | 시점 간(대상 내) 대응 t / Wilcoxon, 시점별 군간 Welch t / Mann–Whitney (권장 트랙을 따름), Holm 또는 BH 보정. 기저 시점은 참고값으로만 표시하고 다중비교에서 제외 |
| 변화량 | 군별 기저 대비 변화량 + 95% CI, **군간 변화량 차이 + 95% CI**, 효과크기의 95% CI |
| **ANCOVA** | 기저값을 공변량으로 넣은 **조정평균차 + 95% CI** — 기저 불균형·평균회귀에 강건하고 대개 검정력이 더 높습니다 |
| 주요 시점 | `--primary-time` 으로 사전 지정한 시점은 **보정 없이**, 나머지는 자기들끼리 보정 |
| 반응자 | MCID(절대 또는 %) 기준 반응률 + Wilson CI, RD(Newcombe)·RR·OR·NNT, Fisher/χ² |
| RCI | Jacobson–Truax 신뢰변화지수: 신뢰적 호전/무변화/악화 비율, 회복(절단점 통과) 비율 |
| 문장 | 모든 결과에 대해 한국어·영어 APA 형식 문장 (`--labels-en` 으로 영문 라벨 지정) |
| 출력 형식 | `text`(기본) · `md`(논문/Word 붙여넣기용 표) · `json` · `csv`(모수·비모수 두 트랙 모두) |

### 방법론 메모 (숨기지 않는 부분)

- **계산 방식.** 모든 반복측정 효과는 **직교정규 Helmert 대비 점수**에서 계산합니다.
  1군 설계에서는 교과서 공식과 정확히 일치하고, **군 크기가 다를 때** 시점 주효과는
  SPSS GLM 기본값과 같은 **Type III**(그룹 비가중 평균 기준)로 계산됩니다.
  (균형 설계에서는 두 방식이 동일합니다.)
- **검증.** 반복측정 ANOVA는 statsmodels `AnovaRM`, 혼합설계 Type III는 statsmodels
  `anova_lm(typ=3)`, Friedman·Wilcoxon·Mann–Whitney·Fisher·χ²는 SciPy와 대조하여
  일치를 테스트로 고정해 두었습니다 (`tests/test_crosscheck_scipy.py`, SciPy가 없으면 자동 skip).
- **결측.** omnibus ANOVA는 **완전사례**만 사용합니다(대비 점수를 만들 수 없으므로).
  기술통계·사후비교·변화량·반응자·RCI는 **가용사례**를 쓰므로 각 표에 실제 n을 함께
  표시합니다. 완전자료 비율이 80% **이하**면 경고하고, 군별 완전자료 비율이 10%포인트
  이상 벌어지면 차등 탈락 경고를 따로 냅니다. longistat은 MMRM·다중대체를 제공하지
  않습니다 — 리포트도 그 사실을 [4] 바로 위에 명시합니다.
- **모수 vs 비모수.** 검정 통계량을 정규성 결과로 몰래 바꾸지는 않습니다: **두 결과를
  모두 계산**해 JSON/CSV 에 담고, 텍스트 리포트의 [4]~[7] 에는 "권장" 트랙만 표시하되
  표 제목에 어느 트랙인지 항상 적습니다. 둘의 결론이 다르면 그 자체가 보고할 정보이므로
  `--format json` 으로 확인하세요.
- **구형성.** Mauchly p는 R `stats::mauchly.test` 와 같은 2차항 전개를 씁니다(1차항만
  쓰면 작은 n에서 기각이 과합니다). 공분산이 특이해 W를 못 구해도 ε 보정은 그대로
  적용합니다 — 그 경우가 오히려 구형성 위배가 가장 심한 경우이기 때문입니다.
  혼합설계의 Huynh–Feldt ε는 SPSS/SAS 형태이며 Lecoutre(1991) 수정형보다 약간
  관대합니다(리포트에 표기).

---

## 한계 / 주의

- **혼합효과모형(MMRM)·다중대체는 제공하지 않습니다.** 탈락이 많은 확증적 분석은
  R `nlme`/`lme4` 또는 SAS PROC MIXED를 쓰세요. ANCOVA는 **기저값 1개**만 공변량으로
  넣습니다 (나이·성별 등 추가 공변량은 지원하지 않음).
- 요인은 **시점 1개 + 군 1개**까지입니다 (2요인 이상 within 설계, 예: 시점 × SNR × 귀
  는 지원하지 않음). 결과지표도 한 번에 하나입니다.
- 로그·arcsine 등 **변환 옵션이 없습니다.** HRV처럼 심하게 치우친 지표는 미리 변환해서
  넣으세요.
- **반응자·RCI 분석도 기본은 완전사례**(해당 시점 관측자)입니다. ITT 기준 반응률이
  필요하면 `--responder-denominator randomized` (무응답 대체, NRI)를 쓰세요.
- Mauchly의 χ² 근사는 오차 자유도가 추정 모수 d(d+1)/2 에 비해 넉넉할 때만 믿을 수
  있습니다 — 그렇지 않으면 리포트가 "과신하지 마세요" 라고 적습니다.
- Hedges g·dz의 신뢰구간은 정규근사입니다 (비중심 t 기반 정확 구간이 아님).
- MCID·신뢰도(r)·회복 절단점은 **문헌에서 가져와 지정해야 하는 값**입니다. 도구가
  대신 정해주지 않으며, `--direction` 을 반드시 지정하게 한 것도 방향을 잘못 잡으면
  반응률이 그대로 뒤집히기 때문입니다.
- 자료는 **로컬에서만** 처리합니다 — 네트워크 접근이 전혀 없고, 지정한 `--output`
  외에는 아무 파일도 쓰지 않습니다. `--output` 이 입력 CSV 와 같으면 거부하고, 저장은
  임시 파일 → `os.replace` 로 원자적으로 합니다. CSV 내보내기에는 수식 인젝션 방지,
  JSON 에는 HTML 인라인 안전 이스케이프가 적용됩니다. 오류 메시지는 대상 ID 나 셀
  내용을 그대로 노출하지 않습니다(행 번호와 열 이름만).
- 입력 파일은 200 MB, 시점은 60개까지입니다. 시점이 그보다 많으면 `--time` 이 방문일
  (날짜) 열을 가리키는 실수일 가능성이 높아 오류로 알려 줍니다.

---

## 테스트

```bash
cd longistat-반복측정추이분석
python3 -m pytest -q
```

적대적 하드닝 이력은 [`HARDENING.md`](HARDENING.md) 를 보세요.

## 라이선스

MIT © 2026 hyeonjoong
