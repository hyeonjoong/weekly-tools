# HARDENING log — sleepdiary (수면일기 지표)

Adversarial review of this tool. Each round: an independent panel of reviewer
subagents (statistical correctness / messy-CSV edge cases / docs honesty /
test quality + PII safety) attacks the tool from first principles; every
material finding is fixed, regression tests are added, and `python3 -m pytest`
is re-run to green before the round closes.

---

## Round 1 — 2026-08-06

**Baseline at the start of the round.** The folder held only four modules
(`timeparse.py`, `dataio.py`, `nightly.py`, `stats.py`) from an earlier run
that was cut off — no package `__init__`, no aggregation layer, no CLI, no
tests, no README, so the tool could not be run at all and was invisible to the
catalog. This round completed it: `aggregate.py` (night → subject → group
two-stage aggregation and paired period comparison), `report.py` (text and
markdown output), `cli.py`, `pyproject.toml`, two synthetic example datasets,
`README.md`, `사용법.md`, `실행.command`, `LICENSE`, `.gitignore`, and tests.

**Fixed while building (found before the panel ran):**

- **Column auto-detection missed the most common real spelling.** Diary
  exports name their columns `sleep_latency_min` and `waso_min`, but the alias
  table only matched `sleeplatency` / `waso`, so SOL and WASO were silently
  read as absent — and absent SOL/WASO default to 0, which quietly inflates
  sleep efficiency. Added a second matching pass that compares names with unit
  suffixes stripped, keeping the "two candidates → error, never guess" rule.
  (`dataio.py`)
- **A physically impossible night could pass validation.** If the final
  awakening was recorded later than the time the subject got out of bed (a
  common transcription slip), `forward_minutes` wrapped the terminal
  wakefulness to nearly 24 hours and SPT came out longer than TIB. Sleep
  efficiency then landed just under 100% for some rows and the night was
  aggregated as if it were fine. Added an explicit `SPT > TIB` guard that
  rejects the night and names the likely cause. (`nightly.py`)
- **`circular_sd` is a population SD, not a sample SD.** The `sqrt(-2 ln R)`
  definition divides by *n*, so the regularity number is systematically
  smaller than `sd()` (which uses *n*−1). This is the circular-statistics
  convention and is intentional, but it was undocumented and would have looked
  like a bug to anyone cross-checking against numpy. Documented in the
  docstring and pinned by a test. (`stats.py`)

**Panel round (4 reviewers in parallel: statistics / messy CSV / docs honesty /
tests + PII).** Every material finding below was fixed and pinned by a
regression test in `tests/test_hardening_round1.py`.

*Crashes — each reproduced on a realistic clinical file, each now impossible:*

- **CR-only line endings** (old Excel-for-Mac exports) killed the reader with
  `_csv.Error: new-line character seen in unquoted field`. Line endings are now
  normalised and `csv.Error` is translated into a `DataError` with an
  explanation. (`dataio.py`)
- **An unbalanced quote** in a free-text column of a file over 128 KB raised
  `field larger than field limit`. Now a clean error naming the likely cause.
- **`nan`/`inf` in a duration cell** propagated into every mean and SD *while
  the night stayed marked valid* — `value < 0` and `tst <= 0` are both false for
  NaN — and then crashed the renderer in `int(round(...))`. Non-finite values
  are refused at the parser, and the formatters render `—` defensively.
  (`timeparse.py`)
- **An unwritable `--json` / `--per-night-csv` path** surfaced a raw
  `FileNotFoundError`. Now a clean message and exit code 2. (`cli.py`)

*Silently wrong numbers:*

- **Duplicate column names.** A column duplicated by a copy-paste (second copy
  blank) made `csv.DictReader` keep the empty one: a real SOL of 45 min became
  0, TST rose by 45 min, SE went 84.6% → 93.3%, and the manuscript sentence read
  "입면잠복기는 0분". No warning anywhere. Duplicate headers are now rejected.
- **The "two candidates → error, never guess" promise had a hole.** It only held
  when both candidates were suffix-free: with `latency` and `sleep_latency_min`
  side by side the exact-match pass won and the other column was dropped in
  silence. Both passes now collect into the same candidate list.
- **Zero-filled SOL/WASO were reported as if measured** — "입면잠복기 평균 0분,
  95% CI [0.0, 0.0]", straight into the auto-drafted paragraph. Imputation is
  now recorded per night (`imputed` field, per-night CSV column, JSON), counted
  in the 자료 품질 section, and **excluded from that metric's own summary** while
  still entering TST as 0. A blank *cell* is distinguished from a missing
  *column*.
- **Out-of-order clock times slipped past validation.** The `SPT > TIB` guard
  added earlier is necessary but not sufficient: `bedtime 02:00 / lights-off
  01:00` and `final-awake 07:00 / out-of-bed 06:00` both produced SE = 100% with
  TWAK = 1380 min and no error at all. Replaced with a containment invariant —
  `(bed→lights-off) + SPT + TWAK` must equal TIB exactly. (`nightly.py`)
- **`밤 12시` parsed as noon, not midnight** — a 12-hour error in the most
  colloquial Korean spelling of the most common bedtime. Also `11:15p` was read
  as 11:15 AM. (`timeparse.py`)
- **Invisible characters split one subject into three.** `S01`, `S01​` and
  a mid-file BOM became separate people, silently changing n and which subjects
  paired. Zero-width characters are now stripped alongside whitespace.
- **UTF-16 files decoded as latin-1**, since latin-1 accepts every byte. Now
  BOM-sniffed, and a latin-1 fallback prints a warning in the report header.
- **`t_ppf` clamped its bisection bracket to ±1e4 silently**, so `--conf 0.99999`
  returned 10000 instead of 31831 — a confidence interval far narrower than the
  truth. The bracket now expands until it straddles p. (`stats.py`)
- **Wilcoxon's effect size appeared or vanished by accident.** `r` was only
  computed in the normal-approximation branch, so whether a study got an effect
  size depended on whether a tie happened to occur. `z`/`r` are now computed in
  both branches (exact p is still exact). Non-finite differences are dropped
  before ranking — a NaN inflated `n` without contributing a rank, quietly
  shifting p.
- **A 12-hour phase shift was reported as "no change, p = 1.0."** Clock
  differences wrap at ±720 min, so +700 and −740 cancel in a linear t-test.
  Circular comparisons with any shift over 6 hours now suppress the test and say
  why. (`aggregate.py`, `report.py`)
- **Zero-variance results claimed a zero-width confidence interval.** When every
  subject changes by exactly the same amount there is no information about the
  population mean; `paired_ttest` and `mean_ci` now return `None` bounds.
- **`circular_sd` could return `-0.0`** (a negative SD) and, for values spread
  around the clock, "28.4 hours" of spread on a 24-hour circle. It now returns
  `None` below a resultant-length floor, and the group report shows the median
  regularity alongside the mean so one erratic subject can't carry it.

*Documentation honesty:*

- The README's pasted sample output had drifted from the real numbers
  (five values). Repasted from an actual run and marked as an excerpt.
- **"SPT" was labelled a standard term under a heading claiming standard
  definitions.** Conventional SPT runs from sleep *onset*; this tool computes
  lights-off → final awakening, which includes SOL. Renamed 수면기회시간 with the
  difference stated, in the README, the module docstring, and the report footer.
- **Mid-sleep was called a chronotype measure.** It is a sleep-*timing* outcome;
  the chronotype proxy is MSFsc (free days, sleep-debt corrected). Likewise the
  regularity number is not the Sleep Regularity Index. Both corrected.
- **The Carney 2012 citation was stretched.** The Consensus Sleep Diary
  standardises diary *items* and calls itself a living document needing further
  validation; it does not adjudicate derived-variable formulas. The README now
  attributes the items to CSD and the formulas to this tool, and notes that the
  CSD item is "잠을 자려고 시도한 시각", not "lights off".
- **The column-fallback rule omitted its cost.** Substituting lights-off for
  bedtime shortens TIB and raises SE — measured on the bundled data as
  82.5% → 86.8%. Now stated with the measured figure.
- **`group`/`arm`/`군` are auto-detected as "period."** In a parallel-group
  trial that silently splits the summary by arm and leaves zero pairs for
  `--compare-periods`. Documented with the `--ignore-period` remedy and an
  explicit statement that this tool does paired comparisons only.
- Added the caveats a reviewer would ask for and that were missing everywhere:
  naps are excluded, the period comparison is complete-case (not ITT), subjects
  are equally weighted regardless of nights contributed, subject-level SE is the
  mean of nightly SEs rather than ΣTST/ΣTIB, and clock differences wrap at
  ±12 hours. These now appear in the README, the report footer, the markdown
  output, and the JSON `notes`.
- Two commands printed by `실행.command` differed from what it actually ran
  (a copied `--min-nights` example would have printed nothing). Fixed.
- Fixed the encoding-troubleshooting entry in `사용법.md`, which claimed file
  reading was "정상" when a latin-1 misdetection is exactly what silently
  garbles it.

*Test quality (mutation-tested by the reviewer: 87 mutations, survivors listed
and addressed):*

- **The circular-mean test was vacuous** — it used values (02:00 and 04:00)
  where the circular and arithmetic means are identical, so the tool's headline
  correctness property was untested at both aggregation levels. Replaced with
  wrapping values (23:50 / 00:10) at subject *and* group level, each asserting
  the arithmetic answer (noon) is *not* produced.
- Replaced a tautological column-mapping assertion, four CLI tests that only
  matched a static label (they survived mutations disabling `--min-nights`,
  `--conf` and `--date-means`), and an assertion that compared the code against
  its own threshold constant. The option tests now assert on numbers that change.
- Added coverage for the previously untested: JSON night/subject payload
  contents, `date_span`, regularity provenance, and all four "odd but possible"
  warning thresholds.

*PII / privacy:*

- **The input file path was embedded verbatim in the JSON**, which is the
  artifact most likely to be handed to a collaborator — and clinical filenames
  routinely carry a patient name. The JSON now stores the basename only.
- `sanitize_cell` inspected only the first character, so `" =1+1"` passed
  through unprefixed; it was saved only by an unrelated `.strip()` in another
  module. Now it ignores leading whitespace itself.
- The examples were confirmed synthetic; no network calls, telemetry, temp
  files, or author paths exist anywhere in the package.

**Not changed, deliberately:** the `SE > 100` check is now unreachable given the
containment invariant, but it is kept as a float-slack backstop and its comment
says so. `circular_sd` remains a population (n-divisor) statistic, matching the
circular-statistics convention rather than `sd()`.

`python3 -m pytest` — **234 passed**.
