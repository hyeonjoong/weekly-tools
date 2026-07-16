# citecheck

**Verify your manuscript's citations against Crossref before a reviewer does.**

`citecheck` reads a `.bib` file or a plain reference list, looks every DOI up on
[Crossref](https://www.crossref.org/), and flags the mistakes that quietly
slip into reference lists:

- 🔗 **Broken DOIs** — a DOI that doesn't resolve at all. (A DOI that resolves
  but simply isn't in Crossref — e.g. a dataset/preprint/DataCite DOI — is
  reported as a *warning*, not a hard error.)
- 🔀 **Metadata mismatches** — the title, year, or first author you cited doesn't
  match the record Crossref holds (usually a copy-paste from the wrong entry).
- ⛔ **Retractions** — the paper you're citing has been marked retracted *in
  Crossref*. See the honest caveat under [Retraction detection](#retraction-detection).
- 👯 **Duplicate DOIs** — the same DOI cited under two different keys.

It is a small, dependency-free Python package you can drop into CI to fail a
build when a citation is wrong.

> Built as a weekly automation tool. Pure standard library — no `pip install`
> of third-party packages required to run.
>
> **Requires internet access** to reach Crossref (`api.crossref.org`) and the
> DOI resolver (`doi.org`). Offline, every lookup is reported as a warning and
> the run exits **3 (inconclusive)** — never 0 — so a network outage can't be
> mistaken for a clean pass in CI.

## 목적 / Why this exists

**한글:** 논문을 제출하기 직전, 참고문헌의 DOI를 손으로 하나씩 Crossref·PubMed에 대조하는 일은 지루하고 실수가 잦습니다. 잘못 붙여넣은 DOI, 다른 논문의 메타데이터, 심지어 이미 **철회된 논문**이 그대로 제출되면 리뷰어 지적이나 게재 후 정정 사유가 됩니다. `citecheck`는 그 Crossref 대조 과정을 자동화해, 임상·약리 연구자가 원고를 제출하기 전에 인용 오류를 빠르게 잡도록 돕습니다.

**English:** Right before submission, manually checking every reference DOI against Crossref/PubMed is tedious and error-prone. A mistyped DOI, metadata from the wrong paper, or a **retracted** source can slip into a manuscript and trigger reviewer complaints or post-publication corrections. `citecheck` automates the Crossref half of that verification pass so a researcher can catch citation errors before submitting — and can even wire it into CI as a submission gate.

## Install

```bash
pip install git+https://github.com/hyeonjoong/citecheck.git
# or, from a clone:
pip install -e .
```

Or just run it without installing (this always works from a clone; the bare
`citecheck` command only works if your Python scripts dir is on `PATH`):

```bash
python3 -m citecheck references.bib
```

## Usage

```bash
# Check a BibTeX file
python3 -m citecheck references.bib

# Check a plain-text reference list (one per line or blank-line separated)
python3 -m citecheck refs.txt --format text

# Pipe from stdin
pbpaste | python3 -m citecheck -

# Machine-readable output for CI
python3 -m citecheck references.bib --json

# Fail the run on warnings too (not just errors)
python3 -m citecheck references.bib --strict

# Join Crossref's faster "polite" pool (your email is sent in the request header)
python3 -m citecheck references.bib --mailto you@example.com
```

### Example

```
$ python3 -m citecheck examples/sample.bib --verbose --no-color

✓ ioannidis2005
    Verified: Ioannidis (2005) — Why Most Published Research Findings Are False
✗ broken_doi
    DOI does not resolve anywhere (Crossref or doi.org) — check for a typo: 10.9999/does.not.exist
! wrong_year
    Year mismatch: cited 2015, Crossref says 2010.
! no_doi
    No DOI found — cannot verify against Crossref.

checked 4 references: 1 ok, 2 warnings, 1 errors
  (2 verified against Crossref, 1 could not be verified)
```

(Run without `--verbose` to hide the verified `✓` lines and see only problems.)

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | All references checked; no errors (and no warnings under `--strict`) |
| `1` | At least one error (broken DOI / retraction), or a warning under `--strict` |
| `2` | Usage problem — file unreadable, or no references found |
| `3` | Inconclusive — a lookup failed (e.g. offline); nothing was actually verified |

So it works as a CI gate:

```yaml
# .github/workflows/citations.yml
- run: pip install git+https://github.com/hyeonjoong/citecheck.git
- run: python3 -m citecheck paper/references.bib
```

## What gets checked

| Check | Severity | Meaning |
|-------|----------|---------|
| DOI does not resolve on Crossref *and* not at doi.org | error | The DOI is wrong or the record is gone |
| Retraction flag in Crossref | error | Crossref marks the work as retracted (see caveat below) |
| DOI resolves but is not in Crossref | warning | Likely a dataset/preprint/DataCite DOI; metadata not checked |
| Title similarity < 80% (vs. title, or title+subtitle) | warning | Likely wrong DOI for this citation |
| Year mismatch (vs. *any* of Crossref's dates) | warning | Cited year ≠ any Crossref publication date |
| First-author mismatch (< 85% vs. any listed author) | warning | Cited surname ≠ Crossref authors (diacritics folded) |
| Duplicate DOI | warning | Same DOI cited under two references |
| No DOI present | warning | Nothing to verify against |

Thresholds are conservative to avoid false alarms on formatting differences.
Use `--strict` to make warnings fail the run as well.

**BibTeX vs. plain-text input.** With structured BibTeX fields, all of the above
checks run. In plain-text/`--format text` mode the author and year are only
*guessed* from the line, so those two comparisons are skipped to avoid false
alarms; citecheck still verifies the DOI resolves, checks for retraction, and —
because a real citation quotes the paper's title — warns if the cited line
doesn't mention the Crossref title (a swapped DOI). For full title/year/author
verification, use a `.bib` file.

### Retraction detection

citecheck reports a retraction when Crossref's record for the cited article
exposes it — via the Crossmark `update-by`/`update-to` fields or a
`is-retracted-by` relation. **This is not exhaustive:** Crossref's retraction
coverage is incomplete (many older or smaller-publisher retractions are never
marked), so a clean result is *not* a guarantee that a paper hasn't been
retracted. Treat a retraction finding as a strong signal, but don't treat its
absence as proof. (A future version may cross-check the public Retraction Watch
database and PubMed for fuller coverage.)

## Why

When you finalize a paper you often verify references by hand against Crossref,
PubMed, or DataCite. `citecheck` automates the Crossref half of that pass so a
swapped DOI or a retracted source can't make it into submission.

## Development

```bash
pip install -e ".[dev]"
pytest
```

Tests run fully offline — the Crossref client accepts an injected transport, so
no network calls happen in CI for the unit tests.

## License

MIT © hyeonjoong
