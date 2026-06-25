# citecheck

**Verify your manuscript's citations against Crossref before a reviewer does.**

`citecheck` reads a `.bib` file or a plain reference list, looks every DOI up on
[Crossref](https://www.crossref.org/), and flags the three mistakes that quietly
slip into reference lists:

- 🔗 **Broken DOIs** — a DOI that doesn't resolve at all.
- 🔀 **Metadata mismatches** — the title, year, or first author you cited doesn't
  match the record Crossref holds (usually a copy-paste from the wrong entry).
- ⛔ **Retractions** — the paper you're citing has been retracted.

It is a single, dependency-free Python file set you can drop into CI to fail a
build when a citation is wrong.

> Built as a weekly automation tool. Pure standard library — no `pip install`
> of third-party packages required to run.

## 목적 / Why this exists

**한글:** 논문을 제출하기 직전, 참고문헌의 DOI를 손으로 하나씩 Crossref·PubMed에 대조하는 일은 지루하고 실수가 잦습니다. 잘못 붙여넣은 DOI, 다른 논문의 메타데이터, 심지어 이미 **철회된 논문**이 그대로 제출되면 리뷰어 지적이나 게재 후 정정 사유가 됩니다. `citecheck`는 그 Crossref 대조 과정을 자동화해, 임상·약리 연구자가 원고를 제출하기 전에 인용 오류를 빠르게 잡도록 돕습니다.

**English:** Right before submission, manually checking every reference DOI against Crossref/PubMed is tedious and error-prone. A mistyped DOI, metadata from the wrong paper, or a **retracted** source can slip into a manuscript and trigger reviewer complaints or post-publication corrections. `citecheck` automates the Crossref half of that verification pass so a researcher can catch citation errors before submitting — and can even wire it into CI as a submission gate.

## Install

```bash
pip install git+https://github.com/hyeonjoong/citecheck.git
# or, from a clone:
pip install -e .
```

Or just run it without installing:

```bash
python -m citecheck references.bib
```

## Usage

```bash
# Check a BibTeX file
citecheck references.bib

# Check a plain-text reference list (one per line or blank-line separated)
citecheck refs.txt --format text

# Pipe from stdin
pbpaste | citecheck -

# Machine-readable output for CI
citecheck references.bib --json

# Join Crossref's faster "polite" pool with your email
citecheck references.bib --mailto you@example.com
```

### Example

```
$ citecheck examples/sample.bib

✗ smith2020
    DOI does not resolve on Crossref: 10.9999/does.not.exist
! jones2019
    Title mismatch (41% similar):
    cited:    A study of widgets
    crossref: An analysis of gadgets in industrial settings
✗ doe2018
    Reference appears to be RETRACTED according to Crossref.

checked 4 references: 1 ok, 1 warnings, 2 errors
```

`citecheck` exits with status **1** if any reference has an error (broken DOI or
retraction), and **0** otherwise — so it works as a CI gate:

```yaml
# .github/workflows/citations.yml
- run: pip install git+https://github.com/hyeonjoong/citecheck.git
- run: citecheck paper/references.bib
```

## What gets checked

| Check | Severity | Meaning |
|-------|----------|---------|
| DOI resolves on Crossref | error | The DOI is wrong or the record is gone |
| Retraction flag | error | Crossref marks the work as retracted |
| Title similarity < 80% | warning | Likely wrong DOI for this citation |
| Year mismatch | warning | Cited year ≠ Crossref publication year |
| First-author mismatch | warning | Cited surname ≠ Crossref first author |
| No DOI present | warning | Nothing to verify against |

Thresholds are conservative to avoid false alarms on formatting differences.

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
