# HARDENING log — agreestat (측정 방법 일치도)

Adversarial, multi-round hardening of this tool. Each round: an independent panel
of reviewer subagents (correctness / edge cases / usefulness / docs-honesty /
tests+security) attacks the tool from first principles; every material finding is
fixed, regression/property tests are added, and `python3 -m pytest` is re-run to
green before the round closes.

---

## Round 1 — 2026-07-16

**Baseline:** 61 tests passing. Pure-stdlib method-comparison analyzer
(Bland–Altman, ICC(2,1)/ICC(3,1), Lin's CCC, repeatability, Pearson/paired-t,
from-scratch distribution functions).

**Panel verdict:** The statistics engine is unusually solid — the correctness
reviewer recomputed every estimate and CI from first principles against
scipy / pingouin / bootstrap and found **no wrong result on any in-domain
input**. The real weaknesses were one honesty defect, a cluster of robustness
gaps against messy real-world CSVs, and missing high-value clinical features.

### Fixed — correctness / honesty
- **ICC/CCC grade now respects Koo & Li (2016).** The report and the
  auto-generated "ready-to-paste" sentence previously graded reliability off the
  **point estimate**, so the HRV example printed `ICC(2,1)=0.924 (95% CI
  0.009–0.983) → "excellent"` — an overclaim the cited guideline explicitly warns
  against (grade from the CI lower bound). The sentence now grades from the CI
  lower bound (HRV → "낮음/poor"), the report adds a Koo & Li conservative-grade
  line, and a warning fires when the point grade overstates the CI-lower grade.
  (`analyze.py`, `report.py`)
- **`t_ppf` bracket now expands.** The Student-t quantile bisected on a fixed
  `[-1e6, 1e6]` bracket and silently clamped for extreme quantiles / small
  fractional df (e.g. `t_ppf(1e-7, 1)` returned `-1e6` vs true `-3.18e6`). It now
  expands the bracket outward like `f_ppf`; verified to machine precision vs
  scipy across extreme tails and fractional df. (`special.py`)
- **ICC(2,1) CI for non-positive estimates.** The exact McGraw & Wong CI was
  bailed out (`NaN`) whenever the point estimate was ≤0; the formula is valid
  there too. Relaxed the guard to `v2 < 1`; verified the CI matches pingouin
  exactly (`[-0.51, 0.47]` for a near-random sample). (`agreement.py`)
- **Percent-mode CI unit.** CIs in `--percent` mode dropped the `%` suffix while
  the point values carried it. Threaded the unit through `_ci`. (`report.py`)
- **Confidence-level label** no longer degenerates to `0%`/`100%` at extreme
  `--alpha`. (`report.py`)

### Fixed — robustness / edge cases
- **Non-finite cells (`inf`, `-inf`, `1e999`, `Infinity`) are dropped and
  counted+warned** instead of silently poisoning every downstream statistic
  (they parse as valid floats). (`dataio.py`, `analyze.py`)
- **Encoding auto-detection.** Korean Excel CSVs are usually CP949/EUC-KR and
  previously failed with a raw `UnicodeDecodeError`. Now auto-detects
  UTF-8/UTF-16(BOM)/CP949/EUC-KR with a Latin-1 last resort, plus an
  `--encoding` override. (`dataio.py`, `cli.py`)
- **`IsADirectoryError`/`PermissionError`/malformed-CSV** no longer escape as
  tracebacks — CLI catches `OSError`, `_read_rows` catches `csv.Error`, and the
  csv field-size limit is raised (with a ceiling) so an unterminated quote can't
  OOM. (`cli.py`, `dataio.py`)
- **Auto-detect no longer silently picks a sequential id column** as a
  measurement, and warns when >2 numeric columns force a guess; NA labels no
  longer count against a sparse column's numeric-ness. (`dataio.py`)
- **Percent mode warns** on near-zero-mean pairs (exploding %) and sign-mixed
  means. (`analyze.py`)

### Added — clinical usefulness
- **`--accept DELTA` (and `--accept-lower/--accept-upper`)** — compares the 95%
  LoA against a pre-specified clinically acceptable difference and prints an
  **interchangeable / not-interchangeable verdict**, in the report, the JSON, and
  the paste-ready sentence. Turns the tool's numbers into the decision a
  validation researcher actually has to make. (`cli.py`, `analyze.py`, `report.py`)
- **% (and count) of points outside the 95% LoA** — a standard Bland–Altman
  sanity check (~5% expected under normality). (`agreement.py`, `report.py`)
- **CCC/ICC scale note** clarifying the two use different (McBride vs Koo & Li)
  grading scales. (`report.py`)

### Added — tests (61 → 118)
- New `tests/test_cli.py`: end-to-end `main()` — exit codes, stderr messages,
  `--json`, `--accept`, `--encoding`, `--version`, directory/permission errors,
  and `_resolve_accept` units. (CLI was entirely untested before.)
- dataio: non-finite drop+count, CP949/UTF-16/explicit-encoding, sequential-id
  skip, sparse-NA auto-detect, malformed-CSV.
- agreement: outside-LoA count, negative-ICC finite CI (vs pingouin),
  perfect-fit/all-constant/both-constant degenerate branches, and property tests
  (CCC∈[−1,1] & |CCC|≤|r|, ICC≤1, LoA ordering & CI nesting, bias=paired
  mean-diff, bias antisymmetry, percent scale-invariance). Removed the weak
  percent-mode escape-hatch assertion and pinned the denominator.
- analyze: `--accept` verdicts, Koo & Li CI-grade warning, non-finite/percent
  warnings, extra-warnings passthrough, **PII test that subject ids never appear
  in report/JSON**, exact-number report assertions.
- special: `t_ppf` extreme-tail/fractional-df vs scipy, `norm_ppf`/`t_ppf`
  domain guards.

### Security / PII
Confirmed clean: no network / eval / exec / pickle / subprocess; the only file
I/O is reading the user's CSV. Subject ids are never echoed into output (now
regression-tested). Added a `.gitignore` rule (`*.csv` with an `examples/`
allow-list) so a real patient CSV dropped into the repo isn't committed by
accident.

**Result:** 118 tests passing. README/사용법.md/실행.command updated (new flags,
corrected zero-variance behavior claim, Koo & Li guidance, test count) and
re-verified against actual output.

---

## Round 2 — 2026-07-16

**Panel verdict:** A fresh 5-reviewer panel re-checked the Round-1 code and dug
deeper. The correctness reviewer recomputed every new/changed statistic against
scipy/pingouin/bootstrap and found **no WRONG result** — only fragile edges.
The remaining findings were a real crash on absurd-but-finite input, input
validation gaps on the new flags, a couple of doc/test defects (including a
vacuous test I wrote in Round 1), and a set of high-value features.

### Fixed — bugs
- **HIGH crash: `OverflowError` on huge finite values.** Round 1 made
  `parse_float` keep finite values, so `1e308` survived — then `(v-mean)**2` in
  `variance()` overflowed with an uncaught traceback. `parse_float` now also
  rejects `|v| > 1e150` (folded into the abnormal-value drop+count), and the CLI
  catches `OverflowError` as a backstop. (`dataio.py`, `cli.py`)
- **`--accept nan` / `--accept inf` produced bogus clinical verdicts.** A NaN
  limit silently read "not interchangeable"; an infinite limit trivially read
  "interchangeable". `_resolve_accept` now requires finite limits. (`cli.py`)
- **ICC(2,1) CI collapse on strongly-negative estimates.** When the
  Satterthwaite df shrank to ~1e-8, the "CI" pinched to a point that excluded
  the estimate. Now guarded: require `v > 1e-6` and that the interval brackets
  the point estimate, else return NaN (pingouin declines here too). (`agreement.py`)
- **Auto-detect now names the id column it dropped** ("'dose'를 ID로 보고
  제외…") instead of only a generic note, so a monotonic *measurement* wrongly
  skipped is visible. (`dataio.py`)
- **Encoding: latin-1 last-resort and BOM-less UTF-16 now warn.** A silent
  mojibake decode previously passed unnoticed; the loader now flags it and
  suggests `--encoding`. (`dataio.py`)
- **Doc fix:** my Round-1 README edit overstated the zero-variance case ("모든
  신뢰구간이 NaN"). Corrected — only Pearson r/CI and the CCC CI go NaN; ICC/LoA
  CIs stay finite. (`README.md`)

### Added — features (value-ranked, per an independent spec reviewer)
- **Repeated-measures Bland–Altman (Bland & Altman 2007)** — the tool's primary
  data shape (sleep-device epochs). Computes variance-components LoA
  (`√(σ_b²+σ_w²)`, unbalanced `m0` divisor) when a subject column has replicates,
  **headlines it as the recommended LoA** (report block [2c], JSON, and the
  paste sentence), and keeps the naive LoA labelled for transparency. Variance
  components verified against an independent recompute. (`agreement.py`,
  `analyze.py`, `report.py`)
- **Regression-based (proportional) LoA (Bland & Altman 1999 §3)** — auto-computed
  when proportional bias is flagged: `LoA(mean)=D(mean)±1.96·1.253·(c0+c1·mean)`
  (block [2b], JSON). Closes the gap the tool's own warning advertised.
- **LoA-CI precision + required-n** — reports each LoA's CI half-width always,
  and with `--target-loa-hw H` solves for the n needed to reach that precision
  (exact t-based search; verified `n=115` boundary). (`analyze.py`)
- **`--markdown`** paste-ready supplementary results table (metric | estimate |
  95% CI | grade), including RM-LoA / interchangeability / repeatability rows.
- **`--plot-data`** CSV (mean, diff, outside_loa) and **`--svg`** standalone
  Bland–Altman plot (pure-stdlib string templating, xml-escaped, acceptance band
  + bias/LoA lines) — the one required figure the tool previously couldn't emit.

### Tests (118 → 144)
- **Replaced a vacuous Round-1 test.** `test_ci_lower_grade_warning_when_overstated`
  used data that graded poor/poor, so its assertion never ran — the flagship
  Koo & Li fix could silently regress. Now a real regression pins the HRV example
  sentence to '낮음' (CI-lower grade), asserts '매우 좋음' is absent, and that the
  warning fires.
- New: RM-BA variance components (independent recompute) + not-available +
  negative-between clamp; regression-LoA presence/absence + factor; se_loa /
  half-width; precision required-n + JSON; markdown/plot-data/SVG renderers
  (SVG parsed as XML); percent sign-mixed warning; percent-mode acceptance;
  `ci_lower_grade` JSON (present + null); huge-finite drop; descending-id skip;
  latin-1 fallback note; ICC strongly-negative CI guard; `--accept nan/inf`,
  `--target-loa-hw` (+non-positive), `--markdown`/`--plot-data`/`--svg`/output-error
  CLI paths; tightened loose exit-code-only assertions.

**Result:** 144 tests passing. All new formulas verified numerically (RM
variance components, regression-LoA `√(π/2)`, min-n boundary). Docs updated for
every new flag and feature; README example numbers re-confirmed unchanged.

---

## Round 3 — 2026-07-16

**Panel verdict:** A fresh 3-reviewer panel verified the Round-2 features. The
correctness reviewer independently recomputed every new statistic (RM variance
components on balanced *and* unbalanced designs, regression-LoA via Monte-Carlo,
min-n boundary) and found **all feature math correct** — no WRONG defects. The
remaining findings were one consistency bug, one input-robustness gap, one
false-pin test, and coverage holes on already-shipped branches.

### Fixed — material
- **Interchangeability verdict now tracks the headlined LoA.** The `--accept`
  verdict was always computed on the *naive* LoA even when the report and the
  paste sentence headline the *repeated-measures* LoA — so on replicate data the
  headline ("report the RM LoA") could contradict the verdict ("interchangeable"
  on the naive LoA). The verdict, its warning, and the sentence now use the RM
  LoA whenever replicates exist; regression-tested with a fixture where the two
  LoA straddle the acceptance limit. (`analyze.py`, `report.py`)
- **`--target-loa-hw` robustness.** A tiny target (e.g. `1e-9`) needed n≳1e16,
  where `t_ppf` breaks down; the search ran its full 1,000,000-iteration cap
  (~3 minutes) and returned a non-converged, wrong n. Now: non-finite/≤0 targets
  are rejected up front; the search is capped at n=10,000,000 (returns "target
  too tight" instantly beyond that) and **steps down to the exact minimum** so
  the reported n is truly minimal at all in-domain sizes. (`cli.py`, `analyze.py`)
- **Thin-replicate caution.** When only one subject carries the replicates, the
  RM within-subject variance rests on that subject; the tool now records
  `n_replicated_subjects` and warns when it is <2. (`agreement.py`, `analyze.py`)
- **Markdown name safety.** Method names are neutralised for `|`/newlines in the
  markdown header. (`report.py`)

### Tests (144 → 160)
- **Replaced a false-pin test.** `test_icc_strongly_negative_ci_not_collapsed`
  used a fixture that produced a *valid* tight CI, so it never exercised the
  collapse guard. Split into `test_icc_negative_valid_ci_still_brackets` (valid
  path) and `test_icc_ci_collapse_returns_nan` (a fixture that genuinely makes
  the McGraw–Wong interval exclude the estimate → NaN, with finite mse/value).
- New coverage: RM verdict-uses-RM-LoA; RM percent mode + zero-mean guard;
  `n_replicated_subjects`; regression-LoA `sd_negative_warning` (render + flag);
  markdown RM rows; RM-JSON not-available shape; precision already-met branch;
  precision target-too-tight (text + JSON `required_n=None`); required-n exact
  minimum boundary (`hw(n)≤H<hw(n-1)`); BOM-less UTF-16 note; SVG acceptance-band
  + xml-escape on hostile names; `--target-loa-hw nan/inf` rejected; tiny-H fast.

**Result:** 160 tests passing, deterministic (~0.9s). All three round-3
reviewers confirmed the feature math correct and (after these fixes) no default
output that misleads. README example numbers still exact; docs match behavior.

---

## Session 2 (2026-07-16) — Method-comparison regression (Deming & Passing–Bablok) + 3 adversarial rounds

**Motivation.** The tool measured *how far apart* two methods are (Bland–Altman)
but had only a crude OLS `diff ~ mean` check for the two decisions a
method-comparison study (CLSI EP09) actually makes: is there **constant bias**
(intercept ≠ 0) or **proportional bias** (slope ≠ 1)? OLS is the wrong tool —
it assumes the reference is error-free and biases the slope by regression
dilution. Added the two standard error-in-both-variables regressions, the single
most-requested missing capability for a sensor-vs-reference validation.

### Added — real new capability (v0.1.0 → v0.2.0)
- **Deming regression** (`agreestat/regression.py`, Linnet 1990): closed-form ML
  slope for a known error-variance ratio λ = Var(err_x)/Var(err_y) (x=reference,
  y=test; λ=1 = orthogonal), with **leave-one-out jackknife CIs** (t(n−2)) for
  slope, intercept, and the decision-point bias. Verified: Deming λ=1 slope
  matches `scipy.odr` to ≤1e-4 and an exact ML minimizer to 2.4e-9 (positive &
  negative correlation); jackknife SE matches a 20 000-rep bootstrap.
- **Passing–Bablok regression** (1983): distribution-free, robust to outliers;
  slope = shifted median of all pairwise slopes (offset K = #{slopes < −1},
  slope = −1 excluded), rank-based CI `Cγ = z·√(n(n−1)(2n+5)/18)`. Matched an
  independent reimplementation to **0 difference over 700+ datasets** (incl.
  x-ties, negative corr) and a hand-computed 4-point example.
- **`--at Xc` — predicted systematic bias at a medical decision level** (the most
  actionable EP09 number): `bias(Xc) = intercept + (slope−1)·Xc` in absolute
  units, with a Deming jackknife CI that correctly captures slope/intercept
  covariance (a naïve combination of the two marginal CIs is ~9× too wide and
  would wrongly include 0). With `--accept` it prints a within/outside-limits
  verdict at that level (absolute mode only).
- **`--deming-lambda L`** to set the error-variance ratio.
- Every block wired into the text report ([7] + a "which regression to prefer"
  guideline), JSON (`regression.*`, `bias_at_decision_point`), markdown table,
  the paste-ready sentence (Passing–Bablok clause), and an automatic warning when
  the regression flags a bias the single-number Bland–Altman summary would miss.

### Fixed — findings from three fresh adversarial panels
- **HIGH / correctness — Deming λ was the reciprocal of its documented meaning.**
  Two independent reviewers proved `deming(...,lam=L)` computed the fit for error
  ratio 1/L (the closed form's parameter is δ = Var(err_y)/Var(err_x)). The docs,
  CLI help, and prose disagreed among themselves. Fixed the code to the standard
  convention (`delta = 1/lam`, so λ = Var(err_x)/Var(err_y)); λ=1 unchanged. Now
  the options table, CLI help, docstrings, and prose all agree and match an exact
  ML fit. (`regression.py`, README, 사용법.md, cli.py)
- **MED — `--at` with a huge Xc overflowed** the jackknife variance (`**2` raises
  `OverflowError`) and discarded the entire report with a raw errno. Guarded: skip
  only the CI, keep the point estimate and the rest of the report; `analyze`/CLI
  also catch `OverflowError`. (`regression.py`, `analyze.py`)
- **MED — Deming emitted `inf`/`nan` as `available=True`** on sub-cap magnitudes
  (`t*t` overflows silently). Added an output-finiteness guard (mirrors the
  Passing–Bablok guard). Both estimators now also reject non-finite inputs.
- **MED — Passing–Bablok had no size cap** (O(n²) memory) — added `_MAX_PB_N=3000`
  with a clear "use Deming / subsample" note (Deming keeps its n>5000 jackknife cap).
- **MED — decision-point bias mislabeled with the `%` unit in `--percent` mode**
  and would have compared an absolute bias against a percentage `--accept` limit.
  The regression is always fit on raw values, so bias(Xc) is absolute; the unit is
  now dropped and the `--accept` comparison is suppressed (with a note) in percent
  mode. (`report.py`)
- **LOW — misleading Passing–Bablok note** ("all points identical/tied" for the
  all-slopes-−1 anti-correlation case), stale README test count, and
  `analyze`'s ICC/CCC catch widened to `OverflowError` for absurd magnitudes.

### Tests (160 → 209)
New `tests/test_regression.py` (Deming closed-form & orthogonal identity, λ→0/∞
limits, Passing–Bablok hand example + property tests, bias flags, finiteness
guards, size cap, decision-point CI); `scipy.odr` cross-checks for Deming (λ=1,
positive & negative); integration in `test_analyze.py`/`test_cli.py` (regression
blocks, `--deming-lambda`, `--at` text/JSON/markdown, percent-mode unit safety,
`--accept` verdict, overflow-resilience). Deterministic, offline, ~1.3 s.

**Round verdicts.** R1 correctness: no wrong result except the λ reciprocal
(fixed). R1 edge: finiteness/cap gaps (fixed). R2 correctness: **no defects** —
λ, decision-point jackknife (covariance-aware, vs bootstrap), and `--accept`
logic all verified correct. R2 edge: `--at` overflow + markdown parity (fixed).
R3 verification: **CLEAN**. R3 docs: only a stale test count (fixed).

---
---

## Round 7 — 2026-07-31  (multi-rater feature + 1 bounded review round)

**Baseline:** 419 tests. **After:** 561 tests, all offline, ~11 s.

### Added — agreement among 3+ raters, and long/tidy input
The tool only handled "method A vs method B". Clinical work just as often has
*k* readers grading the same images, and exports arrive one-row-per-measurement.

- **`--raters "A,B,C"` (continuous)** — the full ICC family ICC(1,1)/(1,k),
  ICC(2,1)/(2,k), ICC(3,1)/(3,k) with CIs, a between-rater systematic-difference
  F test, SEM (absolute-agreement **and** consistency), MDC95, within-subject SD
  and the reproducibility coefficient, plus a pairwise bias/LoA/CCC table.
- **`--raters "A,B,C" --categorical`** — Fleiss' kappa (overall + per category,
  with the Fleiss 1971 H0 z test), Gwet's AC1, Krippendorff's alpha
  (nominal/ordinal, tolerant of partially-rated units), pairwise Cohen's kappa
  and Light's kappa. CIs come from a subject-level percentile bootstrap.
- **`--long --id-col --method-col --value-col`** — tidy input pivoted to wide;
  2 levels route to the ordinary A-vs-B report, 3+ to the multi-rater one.
  A repeated `(id, method)` pair is a hard error, never a silent average.
- New modules `multirater.py` / `multireport.py`, new loaders in `dataio.py`,
  two example datasets, README/사용법.md/실행.command updated (examples 7-8).
- The bootstrap groups subjects by distinct rating pattern, so a resample costs
  O(n) draws instead of O(n·q²) — 20 000 subjects × 3 raters went 10 s → 3 s
  with *all three* CIs, and the fast path is pinned to the naive one by test.

### Fixed — correctness (panel: 4 parallel reviewers, 1 round)
- **Fleiss' kappa H0 standard error was wrong.** The implementation used an
  m-dependent expression; Fleiss (1971) / R `irr::kappam.fleiss` use
  `[S² − Σp_jq_j(q_j−p_j)]/S²` with `S = 1−P̄e`, which has no m term. On skewed
  marginals (0.9/0.1, N=200, m=4) the SE was 0.0986 against a Monte-Carlo truth
  of 0.0287 — a 3.4× inflation that could turn a significant kappa
  non-significant. Now matches simulation and the published z ≈ 17.7 example.
- **SEM/MDC95 used √MSE (consistency) while sitting next to ICC(2,1)
  (absolute agreement).** For three raters offset by a constant 10 units the
  report printed `MDC95 = 0` — "any change is detectable". SEM is now √MSW
  (absolute agreement, `MSW = MSE + (MSC−MSE)/n`); the consistency SEM is shown
  separately and labelled.
- **ICC(2,k) CI could print reversed and out of range.** Spearman–Brown is only
  monotone while `1+(k−1)r > 0`, and the ICC(2,1) Satterthwaite bound has no
  such floor: one 4×3 table printed `ICC(2,3) −2.661 [95% CI 8.134, 0.852]`
  (18% of random small tables were affected). The transform now returns NaN past
  the pole and the interval is dropped unless it brackets the point estimate.
- **The bootstrap discarded unanimous resamples**, truncating every CI at
  exactly the perfect-agreement end (12% of resamples in one case). `P̄e = 1`
  implies `P̄a = 1`, so kappa is 1 by continuity — now recorded, not dropped.
  Conversely, data with only **one observed category** is refused outright
  instead of emitting three mutually contradictory coefficients.
- **Krippendorff's alpha CI bootstrapped a different set of units than its point
  estimate** (complete cases vs every unit with ≥2 ratings) — 0.370 reported
  with a CI centred on 0.259. The two now resample the same universe.
- **Gwet's π_k averaged over units with ≥2 ratings**; Gwet defines it over every
  rated unit. Fixed (reachable through the public function).
- `--min-kappa` reported "미충족" when the CI was undefined — now a third state,
  "판정 불가". n<4 tables and zero-within-subject-variance tables are flagged.

### Fixed — data integrity, robustness, DoS/PII
- **`csv.reader(text.splitlines())` tore records apart.** `str.splitlines()`
  also breaks on `\x0b \x0c \x1c-\x1e \x85 \u2028 \u2029`, so a vertical tab or a
  NEL byte inside a cell dropped one subject and invented a phantom one (exit 0,
  only a vague "제외 1행"); quoted multi-line labels silently lost their newline
  and merged with a distinct category. Now parsed via `io.StringIO`.
  **This affected every input path, not just the new ones.**
- **Two quadratic scans.** `reshape_long` did `sid not in order` on a list
  (60 000-subject long CSV: 53 s → 0.65 s) and `_require_unique_header` did
  `header.count()` per column (60 000 columns: 43 s → 0.08 s).
- **Uncapped pairwise kappa** — 100 raters × 200 categories is 4 950 pairs of
  200×200 matrices, minutes of CPU from a 30 KB file. Now budgeted: the table is
  skipped with an explicit warning. The wide path also honours the 100-rater cap
  the long path already had, and labels over 64 chars are refused.
- **PII in error messages.** The duplicate-`(id, method)` error echoed the raw
  identifier (`'홍길동-8801011234567'`) — the message users hit first and paste
  into bug reports; it now reports **row numbers**. `--categories` mismatches
  dumped up to 200 raw cells and a `--raters` typo dumped the whole header
  (an MRN and a resident-registration number in one repro). All truncated now,
  and category labels are truncated in the JSON/Markdown files too.
- `--raters ""` silently disabled multi-rater routing *and* the mutual-exclusion
  checks (an unset shell variable would do it); a blank first line is reported
  rather than silently promoting a data row to header.

### Fixed — docs honesty
- **"정확(exact) CI" was an overclaim.** Only ICC(1,1)/ICC(3,1) have exact
  F-based intervals; ICC(2,1) is McGraw & Wong's Satterthwaite approximation and
  ICC(·,k) is a Spearman–Brown transform. Corrected in README, 사용법.md and the
  `agreement.py` docstring.
- The multi-rater reports lost the CI-lower-bound grading rule the two-rater
  reports enforce — the paste-ready sentence graded a `[0.375, 0.980]` interval
  as "excellent". Both reports now carry the Koo & Li / Landis & Koch legends,
  flag when the point grade overstates the CI-lower grade, and grade the
  sentence from the lower bound. NaN CIs render as "신뢰구간 계산 불가", never
  as `NaN–NaN`.
- **`--accept` judged interchangeability on the LoA point estimates** while
  printing an LoA CI that crossed the limit four lines above. The verdict now
  carries an explicit caveat and the paper sentence says "확정할 수 없었다".
- `--ordinal` leaves the headline Fleiss kappa **unweighted** (weighted kappa is
  not defined for Fleiss) — now labelled, explained, and stated in the
  `--min-kappa` verdict. The sentence names the alpha metric, the weighting and
  the category order.
- Stale README output block regenerated; RC relabelled as a **reproducibility**
  (between-rater) coefficient, not Bland–Altman repeatability; pairwise LoA
  header carries the confidence level; small-n warning raised to n<20; example
  echo lines in 실행.command match the commands actually run.

### Tests (419 → 561)
`tests/test_multirater.py`, `tests/test_multi_cli_io.py`,
`tests/test_hardening_multirater.py` (one test per finding above), and
`tests/test_multirater_reference.py` — which pins **only** independently-sourced
numbers: the Fleiss (1971) 10×14×5 table (κ=0.210, P̄=0.378, P̄e=0.213), the
Shrout & Fleiss (1979) published ICC CIs (.019–.761, .342–.946), and the m=2
collapse of Fleiss/AC1/alpha onto this repo's *separate* two-rater
implementations. Cross-checks added against `pingouin` (all six ICCs and CIs),
`statsmodels` (Fleiss), the `krippendorff` package (nominal + ordinal), and a
Monte-Carlo simulation of the Fleiss H0 SE. Tautological "by hand" tests that
re-derived the module's own formula were replaced with structural invariants.
Guardrail tests cover the PII redactions, the DoS caps, the line-break
lookalikes, and wall-clock budgets for the two de-quadratified scans.

**Round verdict.** One review round, four parallel reviewers (correctness /
edge cases / docs honesty / tests+security). Every material finding above was
fixed and pinned by a regression test. Not fixed (recorded deliberately):
output files are opened without `O_NOFOLLOW` and overwrite without `--force`
(user-supplied path, no privilege boundary); `--json --markdown` together
silently prefers markdown (pre-existing, consistent across all paths); the
whole input file is read into memory with no size ceiling (pre-existing).


## Status

Three sessions, seven adversarial rounds total. Session 1 verified the core
agreement statistics (Bland–Altman, ICC, CCC, repeatability, repeated-measures &
regression LoA, interchangeability, precision/required-n) correct from first
principles and hardened every messy-input path. Session 2 added the CLSI EP09
method-comparison regressions (Deming + Passing–Bablok) and the decision-point
predicted-bias number a clinician actually uses, fixed a genuine λ-convention
correctness bug plus a cluster of overflow/robustness edges, and re-verified the
new math against scipy.odr, an exact ML minimizer, an independent Passing–Bablok
reimplementation, and a bootstrap. Session 3 added agreement among **three or more** raters (the full ICC family
with SEM/MDC95, and Fleiss' kappa / Gwet AC1 / Krippendorff alpha with bootstrap
CIs) plus long/tidy CSV input, then ran one bounded four-reviewer round that
caught a wrong Fleiss standard-error formula, a SEM built from the wrong mean
square, a reversible ICC(2,k) interval, a bootstrap truncated at the
perfect-agreement end, a CSV record-splitting bug affecting **every** input
path, two quadratic scans, and several PII leaks in error messages — all fixed
and pinned. 561 offline tests. The tool now covers "can method A replace
method B?" for paired continuous and categorical data, and "do these k readers
agree?" for both scales.
