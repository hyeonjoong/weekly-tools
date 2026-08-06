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
