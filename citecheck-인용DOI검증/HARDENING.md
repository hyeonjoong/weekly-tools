# Hardening log

Adversarial, multi-round hardening of `citecheck`. Each round runs an
independent panel of reviewers (correctness, edge cases, real-world usefulness,
docs honesty, tests/security), then every material finding is fixed with a
regression test added, and the suite is re-run to green.

---

## 2026-07-16 — Round 1

**Panel:** 5 independent reviewers (correctness · edge-cases/robustness ·
clinical-researcher usefulness · docs honesty · test-quality/security).
**Baseline:** 13 tests passing. **After round:** 62 tests passing, offline.

### Correctness / parsing fixes

- **DOI given as a URL or `doi:`-prefixed in a BibTeX `doi` field** produced a
  false "DOI does not resolve" *error* (the raw URL was percent-encoded whole
  into the Crossref path). Reference managers export this form by default. Fixed:
  `parse_bibtex` now routes the field through `normalize_doi_field`, which strips
  `https://doi.org/`, `dx.doi.org`, and `doi:` prefixes and extracts the DOI
  core. (`parsers.py`)
- **DOI regex dropped `)`**, truncating Elsevier/Wiley DOIs such as
  `10.1016/S0140-6736(97)11096-0`. Fixed: the regex now allows parentheses, and
  `_clean_doi` trims trailing punctuation with bracket balancing (a wrapping
  `)` from "(doi: …)" is removed, an internal balanced `)` is kept). (`parsers.py`)
- **`@string`/`@comment` macros swallowed the following real entry** because the
  cite-key regex `[^,]*` ran across brace boundaries to the next comma. Fixed:
  the key is bounded (`[^,{}\s]*`), non-reference entry types are skipped, and
  the brace-matcher's forward scan is bounded by the next entry header. (`parsers.py`)
- **Retraction detection missed genuinely retracted *articles*.** It only checked
  the retraction *notice* shape (`type == "retraction"`, `update-to`). A retracted
  article carries Crossmark `update-by`. Fixed: `_is_retracted` now checks
  `update-by`, `update-to`, and `is-retracted-by` relation keys, and the finding
  surfaces the retraction-notice DOI. Docs now state the coverage caveat honestly.
  (`core.py`, `README.md`)
- **Year comparison preferred `published-print`**, producing false "year
  mismatch" on online-ahead-of-print papers. Fixed: `_crossref_years` collects
  every date field (print/online/issued/created); a citation matches if it
  equals *any* of them. (`core.py`)
- **Subtitle dropped** → false title mismatch on trial titles. Fixed: title
  similarity is computed against both the main title and `title + subtitle`,
  taking the best. (`core.py`)
- **`parse_text` guard/split disagreed** (`"\n\n" in text` vs. the `\n\s*\n`
  split), shattering multi-line references when a blank line contained
  whitespace. Fixed: guard and split now use the same regex. (`parsers.py`)
- **Diacritics not folded** → "Müller" vs "Muller" false author mismatch. Fixed:
  `_norm` now NFKD-folds and strips combining marks; author check also matches
  against *any* listed author. (`core.py`)
- **Auto-detect misfired** on plain text containing `@` + `{` (e.g. an email
  address), routing to the BibTeX parser and losing the reference. Fixed:
  detection now requires a real `@type{` header. (`parsers.py`)

### Robustness / crash fixes

- **Non-UTF-8 input crashed** with an uncaught `UnicodeDecodeError` (the guard
  only caught `OSError`) — common for cp949/latin-1 exports. Fixed: input is read
  as bytes and decoded with a utf-8 → utf-8-sig → cp949 → latin-1 fallback chain,
  with a stderr note on non-UTF-8. (`cli.py`)
- **A single malformed Crossref record aborted the whole batch** (comparison
  helpers ran outside any guard; e.g. `update-to` as a string). Fixed: the
  comparison block is wrapped (bad record → warning, not a crash), and every
  helper type-guards its inputs (`_as_list`, dict/int checks). (`core.py`)
- **O(n²) brace scanning** on unbalanced BibTeX let a truncated file burn CPU
  (21 s for 8k bad entries). Fixed: the forward scan is bounded by the next entry
  header, so malformed entries cost O(entry), not O(file). (`parsers.py`)
- **Negative `--delay` crashed** (`time.sleep(-5)`). Fixed: `--delay` validates
  `>= 0`. (`cli.py`)
- **`title`/`author` as a bare string** (not a list) returned the first
  *character*. Fixed by `_as_list` coercion + string filtering. (`core.py`)

### Usefulness improvements

- **"Not in Crossref" is no longer a hard error when the DOI still resolves.** On
  a Crossref 404, citecheck now does a `doi.org` handle check: a resolvable DOI
  (dataset/preprint/DataCite) becomes a *warning*, only a truly dead handle is an
  *error*. (`core.py`)
- **Duplicate-DOI detection** across the manuscript (same DOI under two keys).
  (`cli.py`)
- **Per-DOI caching / memoisation** so a repeated DOI (or a re-run) hits the
  network once. (`core.py`)
- **`--strict`** promotes warnings to a non-zero exit for a stricter submission
  gate. (`cli.py`)
- **Clearer summary**: "N verified against Crossref, M could not be verified",
  separating genuine verification from unverifiable references. (`cli.py`)

### Security / PII

- **ANSI/terminal-escape injection**: crafted BibTeX titles or poisoned Crossref
  records could emit escape sequences to the terminal (e.g. fake a "✓ all
  verified"). Fixed: all externally sourced text is stripped of C0/C1 control
  chars before printing. (`cli.py`)
- Confirmed clean: URL quoting is SSRF/CRLF-safe (`quote(doi, safe="")`); no
  `eval`/`exec`/`pickle`/shell; the default User-Agent carries only a placeholder
  email (a real `--mailto` is sent only when explicitly provided). Added
  regression tests pinning the UA behaviour.

### Tests

- Added `tests/conftest.py` — an autouse fixture that blocks real sockets, making
  the offline-test guarantee an enforced invariant (accidental live calls now
  fail loudly).
- Added `tests/test_cli.py` (exit-code contract 0/1/2, `--json` shape, non-UTF-8
  handling, ANSI stripping, `--strict`, duplicate-DOI, malformed-entry reporting).
- Extended `tests/test_core.py` and `tests/test_parsers.py`: retraction shapes
  (`update-by`/`update-to`/relation), year-any-date, subtitle, diacritics,
  malformed-record parametrised no-crash, caching, DOI-URL/paren normalisation,
  `@string`/`@comment`, auto-detect, text-splitting.

### Docs honesty

- README's **fabricated example** (keys `smith2020/jones2019/doe2018` that were
  never in the sample, invented findings and totals) replaced with the tool's
  real output on the shipped sample.
- Added an explicit **"requires internet"** disclosure and offline behaviour.
- Usage now leads with `python3 -m citecheck` (the bare `citecheck` command is
  only on `PATH` after an install); removed the author's private, nonexistent
  path from `사용법.md` and `실행.command` in favour of a placeholder.
- Added an honest **retraction-detection caveat** (Crossref coverage is
  incomplete).
- Rewrote `examples/sample.bib` with distinct real DOIs so each demo finding
  (verified / broken / year-mismatch / no-DOI) is reproducible.

---

## 2026-07-16 — Round 2

**Panel:** 4 independent reviewers re-reviewed the hardened code (correctness ·
edge-cases · docs+tests · clinical usefulness). **Baseline:** 62 tests. **After
round:** 83 tests passing, offline. Round 2 caught two regressions Round 1
introduced, plus real gaps.

### Regressions introduced in Round 1 (now fixed)

- **HIGH — BibTeX splitter dropped a valid entry** whose field value contained a
  `@type{key,` pattern (a title discussing BibTeX, or a URL/note field). Round
  1's "bound the scan by the next entry header" fix mis-bounded such entries and
  fabricated a phantom entry from the inner text. Replaced with a correct
  single-pass, brace-depth scanner (`_scan_entries`): a `@…{` inside a field
  value is consumed as part of the enclosing body via depth, never mistaken for
  a top-level entry. Still O(n) (verified: 20k unterminated entries in 0.03 s),
  so the Round-1 DoS fix is preserved. (`parsers.py`)
- **Docs contradiction — offline exit code.** Round 1's docs claimed an offline
  run "does not exit 0 / never falsely passes," but the default (non-strict)
  path returned 0 on all-warnings. Fixed for real: a lookup failure now yields a
  distinct **exit code 3 (inconclusive)**, so a network outage can't read as a
  clean CI pass; docs (README + 사용법) corrected to match. (`cli.py`)

### New correctness fixes

- **URL query string / fragment not stripped** from a DOI (e.g. a browser-copied
  `…0312345?utm_source=…`) produced a false "does not resolve" error. `_clean_doi`
  now cuts at `?`/`#`. (`parsers.py`)
- **`normalize_doi_field` fallback returned junk** (`_clean_doi(value)`) for a
  field with no DOI core, so "not a doi" became a DOI. Now returns None. (`parsers.py`)
- **Retraction-notice DOI missed** when `update-by` matched on type but carried
  no DOI while `update-to` had it — `_retraction_notice` now falls through to the
  DOI-bearing field. (`core.py`)
- **Retraction relation over-matched** `is-retraction-of` (which identifies the
  *notice*, not a retracted paper); narrowed to `is-retracted-by`. (`core.py`)

### New robustness fixes

- **CRASH — `--delay nan` / `--delay inf`** passed the `>= 0` check (`nan < 0`
  and `inf < 0` are both False) and then crashed in `time.sleep`. `_nonneg_float`
  now rejects non-finite values with a clean usage error (exit 2). (`cli.py`)
- **UTF-8 BOM decoded to a phantom `﻿` reference** (the `utf-8-sig` branch
  was dead code behind `utf-8`). BOMs (UTF-8 and UTF-16 LE/BE) are now detected
  first and stripped; added a **`--encoding` override** so a user whose Western
  file was greedily mis-decoded as cp949 can force the right codec. (`cli.py`)
- **Non-UTF-8 Crossref *response* body** (`resp.read().decode("utf-8")`) could
  raise `UnicodeDecodeError` outside the retry loop's caught set; added it. (`core.py`)

### Usefulness fixes

- **Text-mode false positives suppressed.** Free-text parsing guesses author from
  the first capitalised word ("Effect of aspirin…" → "Effect") and year from the
  first year-like token ("2000 patients … 2019" → 2000), producing false
  author/year mismatches on otherwise-correct citations. References parsed from
  free text are now marked `heuristic_fields=True`, and the verifier skips the
  author/year comparisons for them (DOI, title, retraction checks still run).
  (`parsers.py`, `core.py`)
- **Clearer error wording**: "DOI does not resolve on Crossref" → "does not
  resolve anywhere (Crossref or doi.org) — check for a typo" (it fires only after
  the doi.org handle check also fails). (`core.py`)

### Tests added (62 → 83)

- Nested-`@`-in-field regression; `count_malformed_entries`; `normalize_doi_field`
  direct + query-string cases; retraction-notice fall-through and relation
  narrowing; `_crossref_years` shapes (int/str/multi/empty); fetch **and** resolve
  caching; monkeypatched real-transport tests (`_fetch_network` 404-no-retry and
  retry-then-raise; `_resolve_network` 200→True / 404→False); UTF-8-BOM/UTF-16/
  `--encoding` decoding; `--delay` non-finite rejection; exit-code-3 offline;
  `--strict` in JSON mode; text-mode heuristic suppression.

### Verified holding (no change needed)

Reviewers independently confirmed: DOI-quoting is SSRF/CRLF-safe; no
`eval`/`exec`/`pickle`/shell; the socket guard blocks all real network in tests;
the O(n) splitter and Counter-based duplicate detection scale linearly (5k refs
in ~0.04 s); `core.py` is defensive against every malformed Crossref shape tried;
the `--strict` exit matrix is correct in text and JSON; README's demo output now
matches the tool line-for-line.

### Known limitations (documented, not bugs)

- Retraction detection relies on Crossref's incomplete coverage (README caveat).
- A mid-file unterminated BibTeX entry may swallow following entries; the CLI
  warns that entries were skipped.
- Legacy Wiley SICI DOIs containing `<`/`>` are not matched (excluded to avoid
  capturing XML markup); modern DOIs are unaffected.

---

## 2026-07-16 — Round 3

**Panel:** 2 fresh reviewers (correctness+robustness combined · docs+tests+
usefulness). **Baseline:** 83 tests. **After round:** 91 tests passing, offline.
The correctness/robustness reviewer found **no bug on any valid input** and
validated every Round-2 fix; the usefulness reviewer surfaced two real issues.

### Fixes

- **HIGH — transient doi.org failure was reported as a false hard error.** When
  Crossref returned 404, `check_reference` called `resolve()`, whose
  `_resolve_network` collapsed *every* exception (including a transient timeout /
  5xx / 429) to `False` → "DOI does not resolve anywhere" ERROR → failed the CI
  gate for a legitimate reference. Now `_resolve_network` returns `False` only on
  a real doi.org **404** and re-raises transient errors; `check_reference` treats
  a resolve failure as an inconclusive **"Lookup failed"** warning (exit 3), never
  a hard error. (`core.py`)
- **MEDIUM — plain-text mode verified less than the docs implied.** `parse_text`
  never extracts a title, and author/year are suppressed as unreliable, so a
  swapped-but-valid DOI (pointing to a *different* real paper) passed clean. Added
  a conservative title-containment check: for a free-text reference, warn if the
  cited line's words don't overlap the Crossref title (a strong swapped-DOI
  signal). Skipped for bare-DOI lines (no prose) and only fires well below half
  overlap, so it does not manufacture false positives. Verified live: a correct
  citation stays `✓`; a DOI pointing to an unrelated paper is flagged. Docs
  (README + 사용법) now state exactly what text mode checks. (`core.py`, docs)

### Tests added (83 → 91)

- Text-mode: swapped-DOI caught via title, correct-title stays clean, bare-DOI
  not flagged, transient-resolve-failure → inconclusive (not error).
- CLI end-to-end: unknown `--encoding` → exit 2, resolvable-not-in-Crossref
  warning, retraction → exit 1, summary "verified / could not be verified"
  counts, unterminated-mid-file entry is counted and prior entries survive.

### Verified holding

Both reviewers confirmed: `_scan_entries` is correct and O(n) (40k entries in
0.07 s) with no valid-input misparse; DOI query/fragment stripping preserves
balanced parens; BOM/UTF-16 decoding and invalid-codec→exit-2 work; the exit-code
matrix is correct including "error dominates over inconclusive"; retraction
narrowing (`is-retracted-by` yes, `is-retraction-of` no) holds; all malformed
Crossref shapes degrade to warnings without crashing; the socket guard blocks all
real network in tests. Only LOW/informational note: an unbalanced brace inside a
quoted BibTeX value (which real BibTeX also rejects) truncates that one entry.

---

## 2026-07-16 — Round 4 (verification)

**Panel:** 1 focused regression reviewer over the Round-3 changes (resolve
tri-state, exception-cache handling, title-containment heuristic). **Baseline:**
91 tests. **After round:** 93 tests passing, offline.

**Result: clean — no regression.** The reviewer confirmed the resolve tri-state
and exit-code mapping (transient → inconclusive/exit 3, 404 → error, resolvable →
warning), that raised transport exceptions are never cached (re-raised each
call), that the title-containment heuristic stays clean on legitimate citations
(verbatim, subtitle, stopword-heavy, accented, punctuated) while catching the
swapped-DOI case, and that BibTeX refs never reach the text-mode check (no
double-warn).

**One small polish applied:** `_alpha_tokens` now extracts alphabetic sub-words
(`[a-z]+`) instead of dropping any token containing punctuation/digits, so a
hyphenated/technical title ("COVID-19", "neural-networks") no longer skews the
overlap — reducing the reviewer's noted low-severity over-warn edge. Added two
regression tests (punctuation-tolerant match stays clean; unrelated title still
flagged).

Two rounds (R3 correctness, R4) now pass clean, so hardening stops here.
