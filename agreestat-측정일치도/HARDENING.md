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

## Status

Three clean adversarial rounds. The statistics were verified correct from first
principles against scipy/pingouin/bootstrap in every round; rounds 2–3 added the
genuinely-useful features a device-validation researcher needs (repeated-measures
LoA, regression LoA, interchangeability verdict, precision/required-n,
markdown/SVG export) and hardened every messy-input path (encoding, non-finite,
huge-finite, malformed CSV, output errors). The final usefulness review judged
the tool **complete for its 2-method agreement scope**. Stopping here.
