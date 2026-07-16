"""Reference parsing: BibTeX, RIS, CSL-JSON, plain DOI lists, and free text.

The goal is to extract, for each reference, whatever the author *claimed* —
DOI, title, first-author surname, year, journal, and PMID — so the verifier can
compare those claims against Crossref's authoritative record. RIS (EndNote /
Zotero / Mendeley export) and CSL-JSON (Zotero / Better BibTeX) are handled
alongside BibTeX because clinical/pharma reference managers export those far
more often than raw ``.bib``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional


# A DOI is "10." followed by a registrant code and a suffix. The suffix is
# deliberately permissive (DOIs may contain balanced parentheses, e.g.
# Elsevier's ``10.1016/S0140-6736(97)11096-0``); we only exclude whitespace,
# angle brackets, and quote characters, then trim trailing punctuation with
# bracket balancing in ``_clean_doi``. The registrant code keeps a >=4-digit
# lower bound so clinical dosing text like "10.55/kg" is not misread as a DOI.
_DOI_RE = re.compile(r"10\.\d{4,}/[^\s<>\"']+", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(1[6-9]\d{2}|20\d{2})\b")

# Common human-facing prefixes wrapped around a DOI (reference managers export
# DOIs as full URLs or with a "doi:" label).
_DOI_PREFIX_RE = re.compile(
    r"^\s*(?:https?://(?:dx\.)?doi\.org/|https?://|doi:\s*)",
    re.IGNORECASE,
)
_DOI_TRAIL = ".,;:'\"”’`)]}>"

# A PubMed identifier is a bare integer, but we only trust it when it is
# explicitly labelled "PMID" (or given as a PubMed URL), so we never mistake a
# page number, sample size, or accession for one. Up to 9 digits (PubMed's
# current ceiling is ~8), and we forbid a following digit so a longer run isn't
# truncated into a bogus PMID.
# NOTE: the label uses a single ``[\s:]*`` class rather than ``\s*:?\s*``. Two
# adjacent ``\s*`` quantifiers can partition a whitespace run many ways, which
# is catastrophic O(N^2) backtracking on a crafted "pmid<many spaces>" input
# (a ReDoS reachable from any untrusted .bib/.ris/.txt). One character class is
# linear.
_PMID_RE = re.compile(
    r"(?:pmid[\s:]*|pubmed(?:\.ncbi\.nlm\.nih\.gov)?/)(\d{1,9})(?!\d)",
    re.IGNORECASE,
)


@dataclass
class Reference:
    """A single citation as the author wrote it."""

    raw: str
    doi: Optional[str] = None
    title: Optional[str] = None
    author: Optional[str] = None  # first-author surname, best effort
    year: Optional[int] = None
    journal: Optional[str] = None  # container / journal name, if given
    pmid: Optional[str] = None  # PubMed ID, if explicitly labelled
    key: Optional[str] = None  # BibTeX cite key, if available
    fields: dict = field(default_factory=dict)
    # True when author/year were *guessed* from free text (unstructured), so the
    # verifier should not raise noisy author/year mismatches on them.
    heuristic_fields: bool = False

    def label(self) -> str:
        """A short human-readable identifier for reports."""
        if self.key:
            return self.key
        if self.doi:
            return self.doi
        if self.author and self.year:
            return f"{self.author} ({self.year})"
        snippet = self.raw.strip().replace("\n", " ")
        return (snippet[:50] + "…") if len(snippet) > 50 else snippet


def _clean_doi(doi: str) -> Optional[str]:
    """Normalise a raw DOI string: strip URL/``doi:`` prefix and trailing junk.

    Trailing punctuation is stripped, but a closing bracket that has a matching
    opener inside the DOI is preserved (so ``(97)11096-0`` survives while a
    wrapping ``)`` from "(doi: 10.x)" is removed).
    """
    if not doi:
        return None
    doi = _DOI_PREFIX_RE.sub("", doi.strip()).strip()
    # Drop a URL query string / fragment (e.g. "?utm_source=…" from a
    # browser-copied link); real DOIs do not contain '?' or '#'.
    doi = re.split(r"[?#]", doi, maxsplit=1)[0]
    doi = doi.lower()
    while doi and doi[-1] in _DOI_TRAIL:
        if doi[-1] == ")" and doi.count("(") >= doi.count(")"):
            break
        if doi[-1] == "]" and doi.count("[") >= doi.count("]"):
            break
        doi = doi[:-1]
    return doi or None


def find_doi(text: str) -> Optional[str]:
    """Return the first DOI found in *text*, normalised, or None.

    Handles DOIs embedded in URLs or ``doi:`` labels by matching the ``10.…``
    core directly. Uses a >=4-digit registrant lower bound so clinical dosing
    text like "10.55/kg" is not misread as a DOI.
    """
    m = _DOI_RE.search(text)
    return _clean_doi(m.group(0)) if m else None


# For an *explicit* ``doi={...}`` field there is no free-text ambiguity, so the
# registrant code may have any number of digits.
_DOI_FIELD_RE = re.compile(r"10\.\d+/[^\s<>\"']+", re.IGNORECASE)


def normalize_doi_field(value: str) -> Optional[str]:
    """Normalise the value of an explicit ``doi`` field (BibTeX/CSL).

    Strips a URL/``doi:`` prefix and trailing junk. More lenient than
    ``find_doi`` because the field is known to hold a DOI.
    """
    if not value:
        return None
    stripped = _DOI_PREFIX_RE.sub("", value.strip()).strip()
    m = _DOI_FIELD_RE.search(stripped)
    if m:
        return _clean_doi(m.group(0))
    # No DOI core present — not a usable DOI.
    return None


def find_year(text: str) -> Optional[int]:
    m = _YEAR_RE.search(text)
    return int(m.group(0)) if m else None


def find_pmid(text: str) -> Optional[str]:
    """Return the first explicitly-labelled PubMed ID in *text*, or None.

    Only matches when preceded by a ``PMID`` label or a PubMed URL, so a page
    number or sample size is never mistaken for a PMID. Leading zeros are
    stripped (``PMID: 0123`` → ``123``) so the same paper matches regardless of
    zero-padding.
    """
    m = _PMID_RE.search(text)
    if not m:
        return None
    return _clean_pmid(m.group(1))


def _clean_pmid(digits: str) -> Optional[str]:
    """Normalise a digit string to a canonical PMID, or None if not valid.

    Strips leading zeros; rejects a zero/empty value (0 is not a real PMID).
    """
    digits = re.sub(r"\D", "", digits or "")
    if not digits:
        return None
    value = int(digits)
    return str(value) if value > 0 else None


# --- BibTeX -----------------------------------------------------------------

# Entry header: ``@type{key,``.  The key is bounded to exclude commas, braces,
# and whitespace so it can never run past its own entry.
_ENTRY_RE = re.compile(r"@(\w+)\s*\{\s*([^,{}\s]*)\s*,", re.IGNORECASE)
# BibTeX "entry" types that are not references and must be skipped.
_NON_REFERENCE_TYPES = {"string", "comment", "preamble"}


def _scan_entries(text: str) -> list[tuple[str, str, Optional[str]]]:
    """Scan top-level ``@type{key, …}`` entries in a single left-to-right pass.

    Returns (entry_type, key, body) tuples; ``body`` is None when the entry's
    braces never balanced (unterminated/malformed). Because the pointer only
    ever moves forward and each character is visited once, this is O(n) — no
    quadratic rescanning — and, crucially, a ``@type{…}``-looking string *inside*
    a field value (e.g. a title that discusses BibTeX, or a URL) is consumed as
    part of the enclosing entry's body via brace depth, never mistaken for a new
    top-level entry.
    """
    results: list[tuple[str, str, Optional[str]]] = []
    i, n = 0, len(text)
    while i < n:
        m = _ENTRY_RE.search(text, i)
        if not m:
            break
        entry_type, key = m.group(1), m.group(2).strip()
        start = text.index("{", m.start())
        depth = 0
        end: Optional[int] = None
        for j in range(start, n):
            c = text[j]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end = j
                    break
        if end is None:
            # Unterminated: the remaining text cannot be reliably delimited.
            results.append((entry_type, key, None))
            break
        results.append((entry_type, key, text[start + 1 : end]))
        i = end + 1
    return results


def _split_bibtex_entries(text: str) -> list[tuple[str, str, str]]:
    """Return (entry_type, key, body) for each well-formed reference entry."""
    return [
        (t, k, body)
        for (t, k, body) in _scan_entries(text)
        if body is not None and t.lower() not in _NON_REFERENCE_TYPES
    ]


def count_malformed_entries(text: str) -> int:
    """Number of reference entries whose braces never balanced (skipped)."""
    return sum(
        1
        for (t, _k, body) in _scan_entries(text)
        if body is None and t.lower() not in _NON_REFERENCE_TYPES
    )


_FIELD_RE = re.compile(r"(\w+)\s*=\s*", re.IGNORECASE)


def _parse_bibtex_fields(body: str) -> dict:
    """Parse `field = {value}` / `field = "value"` / `field = value` pairs."""
    fields: dict = {}
    i = 0
    n = len(body)
    while i < n:
        m = _FIELD_RE.search(body, i)
        if not m:
            break
        name = m.group(1).lower()
        j = m.end()
        if j >= n:
            break
        if body[j] == "{":
            depth = 0
            for k in range(j, n):
                if body[k] == "{":
                    depth += 1
                elif body[k] == "}":
                    depth -= 1
                    if depth == 0:
                        fields[name] = body[j + 1 : k]
                        i = k + 1
                        break
            else:
                break
        elif body[j] == '"':
            for k in range(j + 1, n):
                if body[k] == '"':
                    fields[name] = body[j + 1 : k]
                    i = k + 1
                    break
            else:
                break
        else:
            end = body.find(",", j)
            if end == -1:
                end = n
            fields[name] = body[j:end].strip()
            i = end + 1
    return fields


def _clean_bibtex_value(value: str) -> str:
    value = value.replace("\n", " ")
    value = re.sub(r"[{}]", "", value)
    return re.sub(r"\s+", " ", value).strip()


def _first_author_surname(author_field: str) -> Optional[str]:
    """Best-effort first-author surname from a BibTeX `author` field."""
    if not author_field:
        return None
    first = re.split(r"\band\b", author_field, maxsplit=1)[0].strip()
    first = _clean_bibtex_value(first)
    if "," in first:  # "Surname, Given"
        return first.split(",")[0].strip() or None
    parts = first.split()
    return parts[-1] if parts else None


def parse_bibtex(text: str) -> list[Reference]:
    refs = []
    for entry_type, key, body in _split_bibtex_entries(text):
        fields = _parse_bibtex_fields(body)
        title = _clean_bibtex_value(fields.get("title", "")) or None
        author = _first_author_surname(fields.get("author", ""))
        # Year: `year` (BibTeX) or the year inside `date` (biblatex, e.g.
        # "2020-03-14"). Better BibTeX / biblatex exports use `date`, so reading
        # only `year` would silently skip the year check for those files.
        year = None
        for key_name in ("year", "date"):
            if fields.get(key_name):
                year = find_year(fields[key_name])
                if year:
                    break
        # The `doi` field may be a bare DOI, a full URL, or a `doi:`-prefixed
        # string — extract the DOI core in every case.
        doi = normalize_doi_field(fields["doi"]) if fields.get("doi") else None
        # Journal / container: `journal` (BibTeX), `journaltitle` (biblatex), or
        # `booktitle` for chapters.
        journal = (
            _clean_bibtex_value(
                fields.get("journal")
                or fields.get("journaltitle")
                or fields.get("booktitle")
                or ""
            )
            or None
        )
        pmid = _bibtex_pmid(fields)
        refs.append(
            Reference(
                raw=body.strip(),
                doi=doi,
                title=title,
                author=author,
                year=year,
                journal=journal,
                pmid=pmid,
                key=key or None,
                fields={"type": entry_type, **fields},
            )
        )
    return refs


def _bibtex_pmid(fields: dict) -> Optional[str]:
    """Extract a PMID from a BibTeX entry (explicit field or note/eprint)."""
    for name in ("pmid", "pubmedid", "eprint"):
        val = fields.get(name)
        if val:
            # `eprint` is only a PMID when eprinttype says so; otherwise skip it.
            if name == "eprint" and "pubmed" not in str(fields.get("eprinttype", "")).lower():
                continue
            cleaned = _clean_pmid(val)
            if cleaned:
                return cleaned
    for name in ("note", "annote", "url", "howpublished"):
        val = fields.get(name)
        if val:
            found = find_pmid(val)
            if found:
                return found
    return None


# --- Plain text / DOI lists -------------------------------------------------


def parse_text(text: str) -> list[Reference]:
    """Parse a newline- or blank-line-separated list of references.

    Each non-empty line (or paragraph) becomes one reference. A bare DOI line
    is treated as a DOI-only reference.
    """
    # Split on blank lines if any are present (allowing whitespace-only blank
    # lines); otherwise split on single newlines. The guard and the split use
    # the same pattern so they can never disagree.
    if re.search(r"\n\s*\n", text):
        blocks = re.split(r"\n\s*\n", text.strip())
    else:
        blocks = text.strip().splitlines()
    refs = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        doi = find_doi(block)
        year = find_year(block)
        author = _guess_text_author(block)
        pmid = find_pmid(block)
        refs.append(
            Reference(
                raw=block, doi=doi, year=year, author=author, pmid=pmid,
                heuristic_fields=True,
            )
        )
    return refs


def _guess_text_author(block: str) -> Optional[str]:
    """Grab a leading surname from a reference string like 'Kim H, Lee S. ...'.

    Callers always pass a ``.strip()``-ed block, so there is no leading
    whitespace to consume — the pattern deliberately omits a leading ``\\s*`` that
    would otherwise create two whitespace-matching groups straddling the same run
    (quadratic backtracking on a pathological all-space input).
    """
    m = re.match(r"\[?\d*\]?\.?\s*([A-Z][A-Za-z'\-]+)", block.lstrip())
    return m.group(1) if m else None


# --- RIS (EndNote / Zotero / Mendeley export) -------------------------------

# A RIS tag line: two-to-four uppercase letters/digits, spaces, a hyphen, then
# the value. The canonical form is ``XX  - value`` (two spaces); we accept one
# or more spaces on each side of the hyphen for the sloppier exports in the wild.
_RIS_TAG_RE = re.compile(r"^([A-Z][A-Z0-9]{1,3})\s{1,}-\s?(.*)$")
# Tags that can carry the title, in preference order.
_RIS_TITLE_TAGS = ("TI", "T1", "BT", "CT")
# Tags that can carry the journal / container name, in preference order.
_RIS_JOURNAL_TAGS = ("JF", "JO", "JA", "T2", "J1", "J2")


def looks_like_ris(text: str) -> bool:
    """True if *text* looks like RIS.

    Requires a ``TY  - `` record header AND enough other RIS tag lines (or an
    ``ER`` terminator) that a lone ``TY  - …`` line inside plain-text prose can't
    misroute a whole reference list into a single RIS record (silent data loss).
    """
    has_ty = has_er = False
    tag_lines = 0
    for line in text.splitlines():
        m = _RIS_TAG_RE.match(line)
        if not m:
            continue
        tag_lines += 1
        if m.group(1) == "TY":
            has_ty = True
        elif m.group(1) == "ER":
            has_er = True
    return has_ty and (has_er or tag_lines >= 3)


def _ris_records(text: str) -> list[list[tuple[str, str]]]:
    """Split RIS *text* into records, each a list of (tag, value) pairs.

    A record runs from a ``TY`` tag to its ``ER`` tag. Continuation lines (no
    tag) are appended to the previous field's value so a wrapped title survives.
    Content before the first ``TY`` is ignored.
    """
    records: list[list[tuple[str, str]]] = []
    current: Optional[list[list]] = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        m = _RIS_TAG_RE.match(line)
        if m:
            tag, value = m.group(1), m.group(2).strip()
            if tag == "TY":
                if current is not None:
                    records.append([(t, v) for t, v in current])
                current = [["TY", value]]
            elif tag == "ER":
                if current is not None:
                    records.append([(t, v) for t, v in current])
                    current = None
            elif current is not None:
                current.append([tag, value])
        elif current is not None and current and line.strip():
            # Continuation of the previous field's value.
            current[-1][1] = (current[-1][1] + " " + line.strip()).strip()
    if current is not None:  # a final record with no explicit ER
        records.append([(t, v) for t, v in current])
    return records


def _ris_first(joined: dict, tags: tuple) -> Optional[str]:
    for tag in tags:
        if joined.get(tag):
            return joined[tag]
    return None


def parse_ris(text: str) -> list[Reference]:
    """Parse an RIS reference list (EndNote / Zotero / Mendeley export)."""
    refs: list[Reference] = []
    for record in _ris_records(text):
        # First value per tag (title/journal/year), plus all authors.
        first: dict = {}
        authors: list[str] = []
        for tag, value in record:
            if not value:
                continue
            if tag in ("AU", "A1"):
                authors.append(value)
            first.setdefault(tag, value)
        raw = "\n".join(f"{t}  - {v}" for t, v in record)

        # DOI: prefer the DO/DOI tag, else scan the whole record.
        doi = None
        for tag in ("DO", "DOI"):
            if first.get(tag):
                doi = normalize_doi_field(first[tag]) or find_doi(first[tag])
                if doi:
                    break
        if not doi:
            doi = find_doi(raw)

        title = None
        for tag in _RIS_TITLE_TAGS:
            if first.get(tag):
                title = _clean_bibtex_value(first[tag])
                break
        journal = _ris_first(first, _RIS_JOURNAL_TAGS)
        journal = _clean_bibtex_value(journal) if journal else None
        year = None
        for tag in ("PY", "Y1", "DA"):
            if first.get(tag):
                year = find_year(first[tag])
                if year:
                    break
        author = _first_author_surname(authors[0]) if authors else None
        pmid = _ris_pmid(first, raw)
        refs.append(
            Reference(
                raw=raw.strip(),
                doi=doi,
                title=title or None,
                author=author,
                year=year,
                journal=journal,
                pmid=pmid,
                fields={"type": "ris:" + (first.get("TY", "") or "?")},
            )
        )
    return refs


def _ris_pmid(first: dict, raw: str) -> Optional[str]:
    """Extract a PMID from an RIS record.

    ``AN`` (accession) is only trusted as a PMID when the data-provider tags
    (``DB``/``DP``) mention PubMed/MEDLINE; otherwise a bare ``AN`` could be any
    database's accession. Also scans free-text notes for a labelled PMID.
    """
    provider = " ".join(
        str(first.get(t, "")) for t in ("DB", "DP", "DptDp", "T3")
    ).lower()
    if first.get("AN") and ("pubmed" in provider or "medline" in provider):
        cleaned = _clean_pmid(first["AN"])
        if cleaned:
            return cleaned
    return find_pmid(raw)


# --- CSL-JSON (Zotero / Better BibTeX / pandoc) -----------------------------


def looks_like_csl_json(text: str) -> bool:
    """True if *text* parses as a CSL-JSON array (or single item object)."""
    stripped = text.lstrip()
    if not stripped or stripped[0] not in "[{":
        return False
    try:
        data = json.loads(text)
    except (ValueError, TypeError, RecursionError):
        # RecursionError: pathologically deeply-nested JSON overflows the decoder
        # (it is a RuntimeError, not a ValueError) — treat as "not CSL-JSON".
        return False
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return False
    # Must look like citation items, not some other JSON document.
    return any(
        isinstance(item, dict)
        and any(k in item for k in ("DOI", "title", "author", "issued", "id", "type"))
        for item in data
    )


def _csl_first_str(value) -> Optional[str]:
    """A CSL field that may be a str or a list-of-str → the first string."""
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        for v in value:
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def _csl_year(item: dict) -> Optional[int]:
    for key in ("issued", "published", "published-print", "published-online"):
        date = item.get(key)
        if isinstance(date, dict):
            parts = date.get("date-parts")
            if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
                try:
                    return int(parts[0][0])
                except (TypeError, ValueError):
                    pass
            raw = date.get("raw") or date.get("literal")
            if isinstance(raw, str):
                y = find_year(raw)
                if y:
                    return y
    return None


def _csl_first_author(item: dict) -> Optional[str]:
    authors = item.get("author")
    if not isinstance(authors, list):
        return None
    for a in authors:
        if isinstance(a, dict):
            fam = a.get("family")
            if isinstance(fam, str) and fam.strip():
                return fam.strip()
            literal = a.get("literal")
            if isinstance(literal, str) and literal.strip():
                return _first_author_surname(literal)
    return None


def parse_csl_json(text: str) -> list[Reference]:
    """Parse a CSL-JSON reference list (Zotero / Better BibTeX / pandoc)."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError, RecursionError):
        return []
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    refs: list[Reference] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        doi_raw = item.get("DOI") or item.get("doi")
        doi = normalize_doi_field(doi_raw) if isinstance(doi_raw, str) else None
        title = _csl_first_str(item.get("title"))
        journal = _csl_first_str(item.get("container-title")) or _csl_first_str(
            item.get("collection-title")
        )
        pmid_raw = item.get("PMID") or item.get("pmid")
        if pmid_raw is not None and not isinstance(pmid_raw, bool):
            pmid = _clean_pmid(str(pmid_raw))
        else:
            pmid = find_pmid(str(item.get("note", "")))
        key = item.get("id")
        refs.append(
            Reference(
                raw=json.dumps(item, ensure_ascii=False, sort_keys=True),
                doi=doi,
                title=title,
                author=_csl_first_author(item),
                year=_csl_year(item),
                journal=journal,
                pmid=pmid,
                key=str(key) if key is not None else None,
                fields={"type": "csl:" + str(item.get("type", "?"))},
            )
        )
    return refs


def detect_format(text: str) -> str:
    """Auto-detect the reference format of *text*.

    Order matters: CSL-JSON (a JSON document) is unambiguous, RIS needs a
    ``TY  - `` header, BibTeX needs a real ``@type{key,`` entry, everything else
    is free text. A stray ``@`` (e.g. an email address) must not route to BibTeX.
    """
    if looks_like_csl_json(text):
        return "csljson"
    if looks_like_ris(text):
        return "ris"
    if _ENTRY_RE.search(text):
        return "bibtex"
    return "text"


_PARSERS = {
    "bibtex": parse_bibtex,
    "ris": parse_ris,
    "csljson": parse_csl_json,
    "text": parse_text,
}


def parse_references(text: str, fmt: str = "auto") -> list[Reference]:
    """Parse *text* into references.

    fmt: "bibtex", "ris", "csljson", "text", or "auto" (detect from content).
    """
    if fmt == "auto":
        fmt = detect_format(text)
    parser = _PARSERS.get(fmt, parse_text)
    return parser(text)
