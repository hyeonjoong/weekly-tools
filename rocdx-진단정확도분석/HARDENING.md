# HARDENING log — rocdx (진단정확도 분석)

Adversarial review of this tool. Each round: an independent panel of reviewer
subagents (statistical correctness / messy-CSV edge cases / docs honesty /
test quality + PII safety) attacks the tool from first principles; every
material finding is fixed, regression tests are added, and `python3 -m pytest`
is re-run to green before the round closes.

---

## Round 1 — 2026-08-06

**Baseline at the start of the round.** The folder held only two modules
(`rocdx/stats_core.py`, `rocdx/roc.py`) from an earlier run that was cut off —
no CLI, no tests, no README, so the tool was unusable and invisible to the
catalog. This round completed it: `loader.py` (messy clinical CSV reading),
`delong.py` (DeLong variance / paired AUC comparison), `analyze.py`
(cleaning, direction, bootstrap optimism), `report.py`, `cli.py`,
`pyproject.toml`, two synthetic example datasets, `README.md`, `사용법.md`,
`실행.command`, and 138 tests.

**Fixed while building (found by the tests themselves, before the panel ran):**
- `wilson_ci(0, n)` returned `2.8e-17` instead of exactly `0.0` (and
  symmetrically at `k = n`). The bounds are now pinned at the endpoints.
  (`stats_core.py`)
- `parse_label_column` counted `"1"` and `"1.0"` as two different levels, so a
  perfectly ordinary numeric outcome column spelled inconsistently by Excel was
  rejected as "3 levels". Levels are now grouped by canonical numeric form.
  (`loader.py`)
- `python3 -m pytest <path>` from another working directory failed to import
  the package; added `tests/conftest.py` so the suite runs from anywhere.

### Panel — 4 reviewers in parallel (statistics / messy CSVs / docs honesty / tests + PII)

The correctness reviewer recomputed every estimator from first principles
(brute-force O(n²), Monte-Carlo sampling distributions, scipy/statsmodels where
available) and found the AUC, mid-rank placements, DeLong variance and paired
covariance, Wilson, Simel, Haldane, the Bayes PPV/NPV, the `>=` convention, the
`direction=lower` remap and the Harrell optimism loop all **exact**. The real
defects were around the edges of those correct kernels.

#### Fixed — statistics
- **Bootstrap intervals were centred on the optimistic statistic.** The
  percentile intervals for sensitivity/specificity/J were built from each
  resample's cut-off scored *on that same resample*, so they covered the truth
  63–72% of the time at a nominal 95%, and the printed "낙관 보정 J" could fall
  below its own interval. They are now built from the out-of-sample evaluation
  (each resample's cut-off applied to the original data). (`analyze.py`)
- **`p = 1.000` for a real AUC gap.** A zero DeLong variance was reported as
  "no difference", but zero variance only means the placement *differences* are
  constant — e.g. against an all-constant comparator, a perfect marker vs a dead
  assay printed `diff 0.5, p 1.000`. The test is now reported as undefined.
  (`delong.py`)
- **SE several times too small when a group had one member.** `var_ddof1`
  returns 0 for n<2, so `S10/m + S01/n` silently dropped the unestimable half
  (m=1: reported SE 0.067 vs a true sampling SD of 0.245, CI coverage 0.72).
  The variance is now `nan` and no interval is printed. (`delong.py`)
- **`LR± = ∞` at the two 0/0 corners.** The Youden rule picks the
  "everybody positive" point whenever max J = 0, where LR− is 0/0; printing "∞"
  read as overwhelming evidence. Both corners now report undefined. (`roc.py`)
- **LR intervals vanished exactly when sensitivity or specificity hit 100%** —
  i.e. in the `--min-sens 0.99` rule-out workflow the docs recommend, leaving a
  bare "음성우도비 0.00". Simel's 0.5 correction is now applied and flagged.
  (`roc.py`)
- **Percentile index off by one / mislabelled level.** Now Efron's order
  statistics, and the interval is refused (rather than silently narrower than
  advertised) below `2/alpha - 1` draws. The optimism correction is suppressed
  below 50 usable resamples instead of being printed like a 2000-rep run.
  (`analyze.py`)
- **The comparator's direction was auto-flipped silently**, maximising its AUC
  from the outcome data and biasing the DeLong test with no warning. It now
  warns, and the report prints each comparator's direction. (`analyze.py`)

#### Fixed — messy clinical CSVs
- **A 1000x silent corruption.** In one European `;`-delimited creatinine
  column, `1,614` matched the thousands rule and `1,06` the decimal-comma rule,
  producing a fabricated perfect separation (AUC 1.000 vs a true 0.967) with no
  warning. The comma reading is now decided **per column** and stated in the
  report. (`loader.py`)
- **Percent cells were divided by 100 invisibly** — a column mixing `12%` and
  `91` turned an AUC of 1.000 into 0.500 with no note. Conversions are now
  counted, and mixed notation raises an explicit warning. (`loader.py`)
- **Four uncaught tracebacks** became clean Korean errors with exit 2:
  `--sep '\t'` (multi-character separator, now also accepted as a literal
  escape), `--encoding cp949` on a UTF-8 file, an unknown codec name, and
  `inf` in the outcome column (`OverflowError` in the label canonicaliser).
- **`없음` was both a missing token and a negative label**, so a 있음/없음
  outcome column lost its entire control group and failed with a confusing
  "one level only". `없음` is now a negative level. (`loader.py`)
- **A single stray high byte made the whole file "UTF-16"**, producing CJK
  mojibake and an error message that dumped the file back at the user. UTF-16 is
  now only accepted with a BOM, and the column list in errors is truncated.
- Whitespace inside numbers no longer rescues typos (`"3 4"` was becoming 34);
  `<=` / `>=` detection-limit markers now actually parse; `--cutoff nan` is
  rejected; and an all-missing comparator now explains *why* every row vanished.

#### Fixed — honesty of the output
- **`--alpha` was cosmetic below section 1.** Operating-point, bootstrap and
  markdown intervals were computed at the requested level but hard-labelled
  "95% CI" — `--alpha 0.01` printed 99% intervals as 95%, straight into a
  paste-ready manuscript paragraph. All labels are now derived from alpha
  (including 99.5% / 99.9%). (`report.py`)
- **Cut-offs were printed to 4 significant digits**, so `platelet <= 1.928e+05`
  did not reproduce the sensitivity quoted beside it (192821 → 95.0% vs the
  printed 97.5%). Now 12 significant digits, with a test that re-applies the
  printed rule to the data. (`report.py`)
- **The paper-sentence draft made publishable claims for useless markers.** For
  a pure-noise marker it emitted "민감도 90.0%, 특이도 23.3%"; at n=8 it emitted
  "AUC 1.000 … 양성우도비 ∞". It now refuses: when the AUC interval covers 0.5
  it says so and withholds the operating-point sentence, small groups are
  flagged inside the paragraph, and the English sentence carries the same
  caveats (it previously carried none). It also reports enrolled *and*
  analysable n. (`report.py`)
- **`auc_grade` called a perfectly reversed marker "판별력 거의 없음"** (AUC
  0.000 with p = 0.012). It now says the direction is inverted, withholds any
  band while the interval covers 0.5, and states that the bands are a
  convention, not a clinical criterion. (`report.py`)
- **`--markdown` — the output most likely to be pasted into a paper — dropped
  the optimism caveat entirely** and truncated warnings at their first `(`. It
  now tags data-chosen cut-offs, always carries the caveat, labels PPV/NPV
  columns with the assumed prevalence, and prints warnings whole. (`report.py`)
- **`--positive-label` swept every other level (판정보류, borderline) into the
  control group silently.** Counted and reported now, with the fix documented.
- Prevalence-adjusted accuracy, the `>`/`≥` upper-limit substitution, the
  stratified bootstrap's fixed prevalence, equal-misclassification-cost
  selection and the percent-to-ratio rescaling are all now in README's
  "하지 않는 것" list; pyproject no longer claims the optimism is "보정"(removed)
  when it is an estimate.

#### Fixed — tests and data safety
A mutation run (95 hand-written realistic bugs) killed 62 and left 33 alive.
The survivors were all *downstream* of the maths: PPV/NPV cell arithmetic (the
only fixture had `fp == fn`, making PPV numerically identical to sensitivity),
Bayes NPV, `--alpha` / `--ci-method` wiring, the percentile index, seed
dependence, stratification, six CLI flags that could be no-ops, and the entire
report layer — including `if an.warnings:` → `if False:`, i.e. every caveat
could vanish from the output and the suite stayed green. `tests/test_report_wiring.py`
(new, 45 tests) closes these: asymmetric 2x2 tables, prevalence-adjusted NPV and
accuracy, alpha end-to-end, exact Efron order statistics, seed-dependence,
stratification, every CLI flag changing the output, and the rendered report
containing every warning the analysis produced.

Data safety: the bundled examples are synthetic (no RRN/MRN/name/DOB/phone/
institution patterns — checked by regex sweep), and nothing is written outside
the user's own `--points-csv` path. But three paths echoed raw cells: the
report's "unparsed values" note (now truncated to 12 characters), and
`--list-columns`, which printed three complete patient records — exactly what a
confused user runs first. Value previews are now **hidden by default** behind
`--show-samples`, and masked to 8 characters when asked for. `.gitignore` was
allow-listing all of `examples/*.csv` — the one place a researcher is most
likely to drop a real export — and ignored none of `.tsv/.xlsx/.sav/.dta`; it
now ignores every data-shaped extension and allow-lists the two example files by
name. A locked-down `LC_ALL=C` terminal used to kill the report with
`UnicodeEncodeError` mid-print; output streams are now reconfigured to replace
unencodable characters.

**Closing state:** 187 tests passing (`python3 -m pytest`, and from any working
directory). Known and documented, not fixed: no clustered/repeated-measures
inference, no partial AUC, no verification-bias correction, and the bootstrap
optimism correction under-corrects for a selected cut-point (a known property of
the simple bootstrap, stated in the report and README rather than hidden).

---

## 2026-08-06 — 기능 확장 + 병렬 적대적 리뷰 1라운드 (부분 AUC·군집 보정·다중비교·비열등성·SVG/JSON)

### 새로 만든 기능 (전부 테스트·문서 포함)
- **부분 AUC (pAUC)** — `--pauc-min-spec` / `--pauc-max-spec`. 실제로 쓰는 특이도
  구간만 사다리꼴 적분하고 **McClish 표준화값**을 함께 냅니다. 구간 경계는 선형
  보간하며, `pAUC(0,t) + pAUC(t,1) = AUC` 항등식으로 검증했습니다. 신뢰구간은
  부트스트랩 백분위(해석적 공식 없음)이며 출력에 그렇게 표시됩니다.
- **군집(반복측정) 보정** — `--cluster-col` / `--cluster`. 한 환자가 여러 행을 내는
  자료에서 **군집 단위 부트스트랩**으로 AUC·pAUC 구간을 계산합니다. 플래그 없이
  열만 지정해도 중복 ID를 찾아 경고합니다.
- **Holm 다중비교 보정** — `--compare` 2개 이상일 때 자동. 검정 불가한 비교는 보정
  대상 수에서 제외하고, 보고서에 실제로 보정한 건수를 표시합니다.
- **비열등성 검정** — `--ni-margin`. 차이 신뢰구간 하한 기준으로 비열등/우월/입증실패를
  구분하고, 한계가 사전에 정해져야 한다는 경고를 항상 함께 냅니다.
- **SVG 그림 출력** — `--plot-svg`. 벡터 곡선(비교 검사·pAUC 구간·Youden 절단점 포함),
  색약 안전 팔레트, n과 "절단점은 같은 자료에서 골랐다"는 주의 문구가 그림 안에 박혀
  있습니다.
- **JSON 출력** — `--json FILE` 또는 `--json -`(표준출력 전용). NaN/∞ 는 `null`,
  경고·주의 문구가 payload 안에 포함됩니다.
- `--version`, 군집 자료 예제(`examples/lesion_multi_reader.csv`, 92병변/42명).

### 리뷰 1라운드에서 고친 것 (독립 리뷰어 4명 병렬)

**통계 서술 오류**
- **군집 자료에서 "DeLong 구간은 좁아진다"고 단정하고 있었습니다.** 몬테카를로로
  확인해 보니 방향은 설계에 따라 다릅니다: 반복측정(한 단위가 같은 결과를 공유)에서는
  95% 구간의 실제 포함률 83%(좁음), 짝지은 설계(한 단위가 질환·비질환 1건씩)에서는
  100%(넓음). 이제 자료를 보고 어느 쪽인지 판정해 문구를 바꿉니다. 군집 부트스트랩은
  두 경우 모두 94~96%로 명목값에 가깝습니다.
- **표준화 pAUC가 음수까지 내려가는데(위로만 1로 막혀 있음)** 보고서는 "0.5=우연,
  1.0=완벽"이라고만 안내하고 논문 문장 초안에 `-9.000`을 그대로 써넣었습니다. 이제
  우연 미만이면 경고하고 초안은 성능 주장 대신 "판별력이 확인되지 않았다"고 씁니다.
- **Holm 보정 건수 표시가 실제 보정 건수와 달랐습니다** (분산 추정 불가로 p가 없는
  비교는 보정 대상에서 제외되는데 "비교 3건"으로 표시). 실제 가족 크기를 셉니다.
- **부트스트랩 표준오차가 백분위 구간의 최소 반복 수 규칙을 우회**해, `--bootstrap 5`
  에서 구간은 `null`인데 SE는 5회로 계산돼 JSON에 실렸습니다. 같은 규칙을 적용합니다.
- **신뢰구간이 아예 없는 표본(군당 1명)에 "매우 우수(outstanding)" 등급**을 주면서
  같은 보고서의 논문 초안은 "판별력을 입증하지 못하였다"고 썼고, 그 *이유*도
  "신뢰구간이 0.5를 포함하므로"(구간이 없는데)라고 틀리게 말했습니다. 둘 다 수정.

**크래시·데이터 훼손**
- **`--compare b1 --compare B1` (같은 열의 두 표기) → `IndexError` 트레이스백.**
  중복 비교 열을 걸러내고 보고합니다. `--compare` 에 검사값 열을 준 경우도 같이 처리.
- **`--json -` 이 비UTF-8 로케일에서 한글을 조용히 망가뜨렸습니다**(`LC_ALL` 이
  ISO8859-1이면 열 이름이 `???`, rc=0). 사람용 보고서를 위한 `errors="replace"` 가
  기계용 스트림에도 적용된 탓입니다. JSON은 이제 UTF-8 바이트로 직접 씁니다.
- **환자ID가 `1` 과 `1.0` 로 섞여 있으면**(결측 있는 정수 ID 열을 pandas가 내보내는
  형태) 한 환자가 두 군집으로 쪼개져 군집 보정이 무력화됐습니다. 결과 열과 같은
  숫자 정규화를 적용하고 그 사실을 보고합니다. 대소문자만 다른 ID는 서로 다른 환자일
  수 있으므로 합치지 않습니다.

**정직하지 않은 출력**
- **군집 2개짜리 자료에서 폭 0인 "95% CI [0.764, 0.764]"** 를 만들어 논문 초안까지
  실어 보냈습니다. 재표본 분포가 축퇴하면 구간을 만들지 않고 그 이유를 말합니다.
  군집 20개 미만이면 불안정하다고 경고합니다.
- **`--cluster --bootstrap 10` 이 아무것도 만들지 못하고 rc=0으로 조용히 끝나면서**
  "`--bootstrap` 을 지정하세요"라고 안내했습니다(이미 지정했는데). 이제 필요한 최소
  반복 수와 실패 이유를 말합니다.
- **SVG의 주의 문구가 캔버스 밖으로 잘려 나갔습니다** — 한글 열 이름에서는 제목이
  474px, 각주가 330px 넘쳤고, 각주와 x축 제목이 겹쳐 인쇄됐습니다. 즉 "이 절단점은
  낙관적"이라는 문장이 정확히 잘려 없어지는 그림이었고, 사용법.md는 "주의 문구가 같이
  들어가서 경고가 떨어지지 않습니다"라고 적어 두었습니다. 텍스트 폭을 추정해 줄바꿈·
  말줄임·캔버스 높이를 조절하고, 회귀 테스트가 모든 `<text>` 가 뷰박스 안에 있는지
  검사합니다.
- **`--no-curve` 에서 절대 번호가 1 → 2 → 4** 로 뛰었습니다(예제 10개 중 7개가 해당).
  섹션 번호를 카운터로 바꿨습니다.
- **`--markdown` 이 군집 자료에서 92병변을 "92명"으로 셌습니다** (텍스트 보고서는
  이미 "행"으로 구분하고 있었음). 논문 초안도 "N건 / M개 단위"로 씁니다.
- **`[양성 = '악성', 음성 = '양성']`** — 조직검사 열에서 `양성`은 benign이라 헤더가
  자기모순이었습니다. `[질환군 = …, 비질환군 = …]` 으로 바꿨습니다. 두 값만 있는
  깨끗한 열에서 뜨던 불필요한 "나머지를 비질환군으로 처리" 경고도 없앴습니다.
- **관측된 위양성률이 1가지뿐인 pAUC 구간이 "표준화 1.000"** 으로 초안에 실렸습니다.
  이제 초안이 "사실상 보간값"이라며 보고를 권하지 않습니다.
- 비열등성 단측 p가 Holm 보정 대상이 아니라는 사실, pAUC 구간이 해석적 공식이 아니라
  재표본 백분위라는 사실, 군집 보정이 절단점별 Wilson 구간에는 적용되지 않는다는
  사실을 보고서·마크다운·JSON·README·사용법에 모두 명시했습니다.

**개인정보**
- **읽을 수 없는 셀의 예시를 12자까지 그대로 출력**하고 있었고, 그 문구가 `--json`
  파일에까지 실렸습니다. 12자는 방어가 되지 않습니다 — 세 글자 한글 이름은 통째로
  들어가고, 주민등록번호 앞 12자는 생년월일 + 뒷자리 5자입니다. 이제 문자 종류만
  남기는 **모양(shape)** 으로 마스킹합니다(`홍길동 010-1234-5678` →
  `가가가 999-9999-999… (17자)`). 열을 찾는 데는 그대로 쓸 수 있고 식별정보는 없습니다.
- JSON의 `input.path` 가 전체 경로(`/Users/…/환자_홍길동_export.csv`)였습니다 →
  파일명만 남깁니다.
- 군집 ID **값**은 JSON·SVG에 실리지 않습니다(열 이름과 개수만). `.gitignore` 는
  생성되는 `.svg`/`.json` 까지 무시하도록 확장했습니다.

**성능**
- 절단점이 여러 개일 때 **같은 재표본을 규칙마다 다시 뽑고 있었습니다**(`--min-spec
  --min-sens` 면 4번). 재표본을 한 번만 뽑아 공유합니다 — 숫자는 완전히 동일하고
  작업량은 3~5분의 1입니다. 큰 자료에서의 실제 소요 시간(20만 행 × 2000회 ≈ 30분)을
  README·사용법에 적었습니다.

### 테스트
리뷰어의 변이(mutation) 실험에서 **75개 중 33개가 살아남았습니다.** 살아남은 것들은
대부분 새 코드의 *검증이 아니라 배선*만 보던 테스트였습니다: 군집 부트스트랩이 아예
재표본을 하지 않아도(폭 0 구간) "행을 복제해도 구간이 안 좁아진다" 테스트가 통과했고,
"경계에서 보간한다" 테스트는 평평한 구간만 써서 보간을 전혀 검사하지 않았으며,
비열등성 주장은 구현을 그대로 다시 쓴 동어반복(`lower > -margin` 을 확인하는데
`noninferior` 가 바로 그 정의)이었고, JSON/SVG 테스트는 값이 아니라 문자열 포함만
봤습니다.

`tests/` 는 187 → **291개**. 새로 추가한 것 중 실질적인 것들:
- pAUC: 손계산 곡선, 구간 하한이 0이 아닐 때의 `chance_area`(두 가지 오답 공식을
  구분), 기울어진(동점) 구간 안에서의 보간(끝점 평균이면 정확히 2배가 되는 사례),
  방향 반전 시 지향 점수 사용, 우연 미만·보간뿐인 구간의 초안 거부.
- 군집: 부트스트랩이 실제로 재표본하는지(폭이 0이 아니고 DeLong 구간과 같은 크기),
  군집 수만큼 뽑는지, 반복측정과 짝지은 설계에서 경고 문구가 반대인지, 축퇴 구간·군집
  20개 미만·`1`/`1.0` ID·대소문자 ID.
- 비열등성: 하한이 정확히 `diff − 1.96·SE` 이고 출력된 CI 하한과 같은지, 단측 α가
  0.025인지, 비열등이지만 우월은 아닌 경우에 "우월" 문구가 안 나오는지, CLI 정상 경로.
- 출력: JSON의 `area` vs `standardized` 교차배선, 구간 범위·`ci_source`·경고 목록·군집
  필드·비열등성 불리언, 방향 반전 시 절단점 구간 부호, 마스킹된 셀 모양, 파일명만 기록,
  비UTF-8 로케일에서의 `--json -`(서브프로세스), SVG의 Youden 점 좌표·pAUC 밴드 범위·
  자동 반전된 비교 곡선이 대각선 위에 있는지·모든 텍스트가 캔버스 안인지·각주와 축
  제목이 겹치지 않는지.
- 보고서: 섹션 번호에 빈 칸이 없는지, 군집 자료에서 "명"을 쓰지 않는지, Holm 가족
  크기 표시, 중복 `--compare` 크래시 회귀, 구간 없는 표본의 등급 거부.

**최종 상태:** 291개 통과(`python3 -m pytest`, 어느 작업 디렉터리에서나).
문서화했지만 고치지 않은 것: 절단점별 민감도·특이도 구간의 군집 보정, pAUC 차이의
DeLong 검정, 검증 편향 보정, 다중 절단점·하위군 분석의 다중비교 보정.
